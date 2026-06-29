"""Streamlit GUI for AeroLake — launch a capture from a browser (MVP).

Why this exists
---------------
The CLI (``aerolake-capture``) needs a terminal — lab colleagues won't use one.
This page is a **thin front-end over the exact same engine**: upload a config →
review it → capture → look at the spectrum → push to MinIO / keep local /
discard. It contains **no capture logic of its own**: it reuses
``load_capture_config`` → ``prepare_capture`` → ``push_capture`` /
``save_capture_locally`` just like the CLI, so everything we already have (SigMF,
metadata, tags, preview, TOML/JSON) works here for free. Because it talks to
whatever ``AEROLAKE_S3_ENDPOINT`` points at, the same GUI works against the local
MinIO today and the lab's remote MinIO (FAST) tomorrow — no code change.

How Streamlit works (in one paragraph)
--------------------------------------
Streamlit re-runs this whole script top-to-bottom on every interaction (every
click, every upload). Anything that must survive between runs (here: the
prepared capture, so the "push/keep/discard" buttons act on what we just
recorded) is stashed in ``st.session_state``.

Run it
------
    uv run streamlit run src/aerolake/gui/app.py
On the acquisition station, add ``--server.address 0.0.0.0`` so colleagues can
open it from their own browser. Or use the convenience entry point: ``aerolake-gui``.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import streamlit as st
import streamlit.components.v1 as components

from aerolake.common.storage import StorageClient, StorageError
from aerolake.consumer.reader import CaptureReader
from aerolake.producer.capture_config import CaptureConfig
from aerolake.producer.config_loader import ConfigError, load_capture_config
from aerolake.producer.orchestrator import (
    PreparedCapture,
    prepare_capture,
    push_capture,
    save_capture_locally,
)
from aerolake.producer.soapy_source import SoapyParams

# Reuse the CLI's metadata helpers so there is a single source of truth for how
# a config becomes SigMF metadata (geolocation resolution + rich-metadata build).
from aerolake.scripts.capture import _build_rich_metadata, _resolve_geolocation

# A dark, "reactbits-inspired" look done with injected CSS: ambient green glow,
# glass cards, a gradient title and glowing buttons. Streamlit isn't React, so
# this captures the *vibe* (not the animated components) and keeps everything in
# plain CSS so the page stays fast and the smoke test still runs.
_CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap');

/* Dark base; the ColorBends WebGL shader (an iframe pinned below) is the
   real background. */
.stApp {
  background: #06080c !important;
  font-family: 'Inter', system-ui, sans-serif;
}
/* Containers transparent so the WebGL background shows through the page
   (Streamlit's containers are opaque by default and would hide it). */
[data-testid="stAppViewContainer"], [data-testid="stMain"],
section.main, .block-container { background: transparent !important; }

/* Pin the ColorBends component iframe as a fixed, full-viewport background.
   components.html renders via srcdoc, so iframe[srcdoc] targets it precisely. */
iframe[srcdoc] {
  position: fixed !important; inset: 0 !important;
  width: 100vw !important; height: 100vh !important;
  border: none !important; z-index: 0; pointer-events: none;
}
/* Its wrapper must not reserve space once the iframe is taken out of flow. */
[data-testid="stElementContainer"]:has(iframe[srcdoc]) { height: 0 !important; margin: 0 !important; }

/* Keep the actual content above the background. */
.block-container { position: relative; z-index: 1; }
header[data-testid="stHeader"] { background: transparent; }
.block-container { padding-top: 2.2rem; max-width: 880px; }

/* hide Streamlit's "Deploy" button + chrome we don't need */
.stDeployButton, [data-testid="stToolbar"] { display: none; }
#MainMenu { visibility: hidden; }
footer { visibility: hidden; }

/* hero */
.al-hero { margin: .2rem 0 1.5rem; }
.al-kicker {
  font-size: .78rem; font-weight: 600; letter-spacing: .22em;
  text-transform: uppercase; color: #7fd07a; margin-bottom: .4rem;
}
.al-title {
  font-size: 3.1rem; font-weight: 800; line-height: 1; margin: 0;
  letter-spacing: -.02em;
  background: linear-gradient(92deg, #eafff0 0%, #3fd64a 52%, #158C01 100%);
  -webkit-background-clip: text; background-clip: text; color: transparent;
}
.al-sub { color: #9aa4b2; font-size: 1.02rem; margin: .55rem 0 0; }

/* glass metric cards */
[data-testid="stMetric"] {
  background: rgba(255,255,255,.035);
  border: 1px solid rgba(255,255,255,.08);
  border-radius: 14px; padding: 14px 16px;
  backdrop-filter: blur(6px);
}
[data-testid="stMetricValue"] { color: #eafff0; }

/* file uploader as a glowing glass dropzone */
[data-testid="stFileUploaderDropzone"] {
  background: rgba(21,140,1,.06);
  border: 1.5px dashed rgba(63,214,74,.45);
  border-radius: 16px;
}

/* buttons: glow + hover lift; primary gets the green gradient */
.stButton > button {
  border-radius: 12px; font-weight: 600;
  border: 1px solid rgba(255,255,255,.10);
  transition: transform .12s ease, box-shadow .2s ease, filter .2s ease;
}
.stButton > button:hover { transform: translateY(-1px); }
.stButton > button[kind="primary"] {
  background: linear-gradient(90deg, #158C01, #3fd64a);
  border: none; color: #04150a;
  box-shadow: 0 6px 20px rgba(21,140,1,.45);
}
.stButton > button[kind="primary"]:hover {
  filter: brightness(1.06); box-shadow: 0 8px 26px rgba(21,140,1,.55);
}

/* rounded, bordered spectrum image + subtle dividers */
[data-testid="stImage"] img { border-radius: 14px; border: 1px solid rgba(255,255,255,.08); }
hr { border-color: rgba(255,255,255,.08); }
</style>
"""


def _inject_css() -> None:
    """Inject the dark/glass/glow theme (once per run)."""
    st.markdown(_CSS, unsafe_allow_html=True)


# reactbits "ColorBends" — a WebGL/three.js fragment shader: warped, animated
# colour bands with grain (real depth + texture). Streamlit can't run three.js
# in st.markdown, so we run it inside an iframe via components.html and CSS-pin
# that iframe as a fixed full-screen background (see the iframe[srcdoc] rule in
# the stylesheet). The GLSL is the upstream ColorBends shader; only the props
# and palette are ours (LASSENA green + teal). Needs the browser online to
# fetch three.js from a CDN.
_COLORBENDS_HTML = """
<!doctype html><html><head><meta charset="utf-8">
<style>html,body{margin:0;height:100%;overflow:hidden;background:#06080c}
canvas{display:block;width:100%;height:100%}</style>
<script src="https://unpkg.com/three@0.158.0/build/three.min.js"></script>
</head><body><script>
const MAX_COLORS = 8;
const COLORS = ["#063a1e", "#0f7a12", "#36e07a", "#0bd1c2"];  // LASSENA green + teal
const vert = `
varying vec2 vUv;
void main(){ vUv = uv; gl_Position = vec4(position, 1.0); }
`;
const frag = `
#define MAX_COLORS ${MAX_COLORS}
uniform vec2 uCanvas; uniform float uTime; uniform float uSpeed; uniform vec2 uRot;
uniform int uColorCount; uniform vec3 uColors[MAX_COLORS]; uniform int uTransparent;
uniform float uScale; uniform float uFrequency; uniform float uWarpStrength;
uniform vec2 uPointer; uniform float uMouseInfluence; uniform float uParallax;
uniform float uNoise; uniform int uIterations; uniform float uIntensity; uniform float uBandWidth;
varying vec2 vUv;
void main(){
  float t = uTime * uSpeed;
  vec2 p = vUv * 2.0 - 1.0;
  p += uPointer * uParallax * 0.1;
  vec2 rp = vec2(p.x * uRot.x - p.y * uRot.y, p.x * uRot.y + p.y * uRot.x);
  vec2 q = vec2(rp.x * (uCanvas.x / uCanvas.y), rp.y);
  q /= max(uScale, 0.0001);
  q /= 0.5 + 0.2 * dot(q, q);
  q += 0.2 * cos(t) - 7.56;
  vec2 toward = (uPointer - rp);
  q += toward * uMouseInfluence * 0.2;
  for (int j = 0; j < 5; j++){
    if (j >= uIterations - 1) break;
    vec2 rr = sin(1.5 * (q.yx * uFrequency) + 2.0 * cos(q * uFrequency));
    q += (rr - q) * 0.15;
  }
  vec3 col = vec3(0.0); float a = 1.0;
  if (uColorCount > 0){
    vec2 s = q; vec3 sumCol = vec3(0.0); float cover = 0.0;
    for (int i = 0; i < MAX_COLORS; ++i){
      if (i >= uColorCount) break;
      s -= 0.01;
      vec2 r = sin(1.5 * (s.yx * uFrequency) + 2.0 * cos(s * uFrequency));
      float m0 = length(r + sin(5.0 * r.y * uFrequency - 3.0 * t + float(i)) / 4.0);
      float kBelow = clamp(uWarpStrength, 0.0, 1.0);
      float kMix = pow(kBelow, 0.3);
      float gain = 1.0 + max(uWarpStrength - 1.0, 0.0);
      vec2 disp = (r - s) * kBelow;
      vec2 warped = s + disp * gain;
      float m1 = length(warped + sin(5.0 * warped.y * uFrequency - 3.0 * t + float(i)) / 4.0);
      float m = mix(m0, m1, kMix);
      float w = 1.0 - exp(-uBandWidth / exp(uBandWidth * m));
      sumCol += uColors[i] * w; cover = max(cover, w);
    }
    col = clamp(sumCol, 0.0, 1.0);
    a = uTransparent > 0 ? cover : 1.0;
  }
  col *= uIntensity;
  if (uNoise > 0.0001){
    float n = fract(sin(dot(gl_FragCoord.xy + vec2(uTime), vec2(12.9898, 78.233))) * 43758.5453123);
    col += (n - 0.5) * uNoise; col = clamp(col, 0.0, 1.0);
  }
  vec3 rgb = (uTransparent > 0) ? col * a : col;
  gl_FragColor = vec4(rgb, a);
}
`;
const scene = new THREE.Scene();
const camera = new THREE.OrthographicCamera(-1, 1, 1, -1, 0, 1);
const geo = new THREE.PlaneGeometry(2, 2);
const uColors = Array.from({length: MAX_COLORS}, () => new THREE.Vector3(0, 0, 0));
function toVec3(hex){ const h = hex.replace('#',''); return new THREE.Vector3(
  parseInt(h.slice(0,2),16)/255, parseInt(h.slice(2,4),16)/255, parseInt(h.slice(4,6),16)/255); }
const arr = COLORS.map(toVec3);
for (let i = 0; i < arr.length; i++) uColors[i].copy(arr[i]);
const mat = new THREE.ShaderMaterial({ vertexShader: vert, fragmentShader: frag, uniforms: {
  uCanvas:{value:new THREE.Vector2(1,1)}, uTime:{value:0}, uSpeed:{value:0.28},
  uRot:{value:new THREE.Vector2(1,0)}, uColorCount:{value:arr.length}, uColors:{value:uColors},
  uTransparent:{value:0}, uScale:{value:1.0}, uFrequency:{value:1.0}, uWarpStrength:{value:1.3},
  uPointer:{value:new THREE.Vector2(0,0)}, uMouseInfluence:{value:0.0}, uParallax:{value:0.4},
  uNoise:{value:0.10}, uIterations:{value:3}, uIntensity:{value:1.4}, uBandWidth:{value:6.0}
}, transparent:false });
scene.add(new THREE.Mesh(geo, mat));
const renderer = new THREE.WebGLRenderer({antialias:false, powerPreference:'high-performance'});
renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
renderer.setClearColor(0x06080c, 1);
document.body.appendChild(renderer.domElement);
function resize(){ const w = window.innerWidth||1, h = window.innerHeight||1;
  renderer.setSize(w, h, false); mat.uniforms.uCanvas.value.set(w, h); }
resize(); window.addEventListener('resize', resize);
new ResizeObserver(resize).observe(document.body);
const rotation = 90, autoRotate = 8; const clock = new THREE.Clock();
function loop(){ const e = clock.getElapsedTime(); mat.uniforms.uTime.value = e;
  const deg = rotation + autoRotate * e; const r = deg * Math.PI / 180;
  mat.uniforms.uRot.value.set(Math.cos(r), Math.sin(r));
  renderer.render(scene, camera); requestAnimationFrame(loop); }
requestAnimationFrame(loop);
</script></body></html>
"""


def _render_background() -> None:
    """Render the ColorBends WebGL shader as a fixed full-screen background."""
    components.html(_COLORBENDS_HTML, height=1)


def _render_header() -> None:
    """Gradient 'LASSENA Aerolake' hero."""
    st.markdown(
        """
        <div class="al-hero">
          <div class="al-kicker">● LASSENA · RF Lakehouse</div>
          <h1 class="al-title">Aerolake</h1>
          <p class="al-sub">Capture RF → SigMF → MinIO, en un clic.</p>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _config_summary(config: CaptureConfig) -> list[tuple[str, str]]:
    """Human-readable recap of a config, shown before launching the capture."""
    signal = config.signal_type
    if config.signal_type_detail:
        signal += f" ({config.signal_type_detail})"

    params = config.source_params()
    source = (
        f"Real SDR — {params.driver} (AGC={params.agc})"
        if isinstance(params, SoapyParams)
        else "Synthetic"
    )

    rows = [
        ("Signal", signal),
        ("Frequency", f"{config.center_freq / 1e6:.3f} MHz"),
        ("Sample rate", f"{config.sample_rate / 1e6:.1f} MS/s"),
        ("Duration", f"{config.duration_s} s"),
        ("Source", source),
    ]
    if config.location is not None:
        rows.append(("Location", config.location.name))
        rows.append(("Motion", "dynamic" if config.location.mobile else "static"))
        if config.location.gps:
            rows.append(("Position", "live GPS fix (gpsd)"))
        elif config.location.geolocation is not None:
            g = config.location.geolocation
            rows.append(("Position", f"{g.latitude:.4f}, {g.longitude:.4f}"))
    else:
        rows.append(("Location", "(not specified)"))
    return rows


def _spectrum_png(prepared: PreparedCapture) -> bytes | None:
    """Render the spectrum PNG from the prepared bytes (same as the stored one).

    The ``.sigmf-data`` payload is raw ``cf32_le``, so we decode it back to
    complex samples and reuse ``render_spectrum_png`` — identical to what
    ``push_capture(with_preview=True)`` stores next to the capture. Best-effort:
    returns ``None`` if rendering fails (a preview is a convenience, never fatal).
    """
    import numpy as np

    from aerolake.producer.preview import render_spectrum_png

    try:
        samples = np.frombuffer(prepared.data_bytes, dtype="<c8")
        sample_rate = float(prepared.data_metadata.get("sample-rate") or 0)
        center_freq = float(prepared.data_metadata.get("center-freq") or 0)
        return render_spectrum_png(samples, sample_rate, center_freq)
    except Exception:
        return None


def _load_uploaded(uploaded: st.runtime.uploaded_file_manager.UploadedFile) -> CaptureConfig:
    """Validate an uploaded config by reusing the on-disk loader.

    ``load_capture_config`` works on a path and picks TOML vs JSON from the
    extension, so we write the uploaded bytes to a temp file with the original
    suffix and hand it over — keeping a single parsing/validation code path.
    """
    suffix = Path(uploaded.name).suffix or ".json"
    tmp_path: str | None = None
    try:
        with tempfile.NamedTemporaryFile("wb", suffix=suffix, delete=False) as tmp:
            tmp.write(uploaded.getvalue())
            tmp_path = tmp.name
        return load_capture_config(tmp_path)
    finally:
        if tmp_path is not None:
            Path(tmp_path).unlink(missing_ok=True)


def _do_capture(config: CaptureConfig) -> None:
    """Run prepare_capture (acquire + encode) and stash the result in session.

    Mirrors the CLI: resolve geolocation, flatten metadata, prepare. Nothing is
    stored yet — the user decides afterwards (push / keep / discard).
    """
    try:
        geolocation = _resolve_geolocation(config)
    except RuntimeError as exc:  # gpsd requested but unreachable
        st.error(f"Erreur GPS : {exc}")
        return

    location_name = config.location.name if config.location is not None else None
    mobile = config.location.mobile if config.location is not None else False

    try:
        with st.spinner("Capture en cours…"):
            prepared = prepare_capture(
                signal_type=config.signal_type,
                signal_type_detail=config.signal_type_detail,
                duration_s=config.duration_s,
                sample_rate=config.sample_rate,
                center_freq=config.center_freq,
                source=config.source_params(),
                operator=config.operator,
                location=location_name,
                mobile=mobile,
                rich=_build_rich_metadata(config, geolocation),
            )
    except Exception as exc:  # acquisition / encoding failure
        st.error(f"Erreur de capture : {exc}")
        return

    st.session_state.prepared = prepared
    st.session_state.preview = _spectrum_png(prepared)


def _render_result(prepared: PreparedCapture) -> None:
    """Post-capture recap + spectrum + the push / keep / discard actions."""
    st.subheader("Capture terminée")

    sample_rate = float(prepared.data_metadata.get("sample-rate") or 0)
    duration = prepared.sample_count / sample_rate if sample_rate else 0.0
    c1, c2, c3 = st.columns(3)
    c1.metric("Échantillons", f"{prepared.sample_count:,}")
    c2.metric("Taille", f"{prepared.size_bytes / 1e6:.1f} MB")
    c3.metric("Durée", f"{duration:.2f} s")
    if prepared.overflow_count:
        st.warning(f"⚠ {prepared.overflow_count} overflow(s) pendant la capture.")

    preview = st.session_state.get("preview")
    if preview is not None:
        st.image(preview, caption="Spectre + waterfall", use_container_width=True)
    else:
        st.info("Aperçu indisponible pour cette capture.")

    st.markdown("**Que faire de cette capture ?**")
    a, b, c = st.columns(3)

    if a.button("⬆  Pousser dans MinIO", type="primary", use_container_width=True):
        try:
            with st.spinner("Upload vers MinIO…"):
                result = push_capture(prepared, with_preview=True)
            st.session_state.prepared = None
            st.success(f"✓ Poussé dans MinIO — session {result.session_id}")
            st.caption(result.data_key)
        except StorageError as exc:
            st.error(f"Erreur de stockage : {exc}")
        except Exception as exc:
            st.error(f"Erreur inattendue : {exc}")

    if b.button("💾  Garder en local", use_container_width=True):
        try:
            out_dir = save_capture_locally(prepared)
            st.session_state.prepared = None
            st.success(f"✓ Gardé en local : {out_dir}")
        except OSError as exc:
            st.error(f"Échec de la sauvegarde : {exc}")

    if c.button("🗑  Jeter", use_container_width=True):
        st.session_state.prepared = None
        st.info("Capture jetée — rien n'a été stocké.")


def _render_capture() -> None:
    """The 📡 Capture tab: upload a config, run a capture, push/keep/discard."""
    # Step 1 — the user uploads a capture config (TOML recommended, JSON ok).
    uploaded = st.file_uploader(
        "Fichier de configuration de capture",
        type=["toml", "json"],
        help="Dépose un .toml (recommandé) ou .json décrivant la capture.",
    )
    if uploaded is None:
        st.info("⬆️ Dépose un fichier de configuration pour commencer.")
        return

    # Step 2 — parse + validate, then show a recap before doing anything.
    try:
        config = _load_uploaded(uploaded)
    except ConfigError as exc:
        st.error(f"Configuration invalide : {exc}")
        return

    st.subheader("Aperçu de la capture")
    for label, value in _config_summary(config):
        col_label, col_value = st.columns([1, 2])
        col_label.markdown(f"**{label}**")
        col_value.write(value)

    # Step 3 — launch. The button only returns True on the run where it's clicked.
    if st.button("▶  Démarrer la capture", type="primary", use_container_width=True):
        _do_capture(config)

    # Step 4/5 — if a capture is prepared (this run or a previous one), show the
    # recap, the spectrum, and the push / keep / discard actions.
    prepared = st.session_state.get("prepared")
    if prepared is not None:
        st.divider()
        _render_result(prepared)


def _render_playback() -> None:
    """The ▶ Playback tab: browse MinIO captures, scrub the spectrum, export.

    Software/visual playback only (ADR-019): list captures, look at any time
    window's spectrum (HTTP Range), show the ready ZeroMQ command, and export the
    SigMF files for GNU Radio (the bridge to real RF re-emission). No RF here.
    """
    from aerolake.producer.preview import render_spectrum_png

    reader = CaptureReader()
    storage = StorageClient()

    try:
        keys = reader.list_captures()
    except Exception as exc:  # StorageError, or a raw connection error if MinIO is down
        st.error(f"MinIO injoignable : {exc}")
        st.caption("Vérifie que MinIO tourne et que le .env pointe au bon endroit.")
        return
    if not keys:
        st.info("Aucune capture dans MinIO. Fais d'abord une capture (onglet 📡 Capture).")
        return

    def _label(key: str) -> str:
        parts = key.split("/")
        signal = parts[0] if parts else key
        folder = parts[2] if len(parts) > 2 else key
        return f"{signal} · {folder}"

    data_key = st.selectbox("Capture à rejouer", keys, format_func=_label)
    if not data_key:
        return

    info = reader.inspect(data_key)
    sr = float(info.metadata.get("sample-rate") or 0.0)
    cf = float(info.metadata.get("center-freq") or 0.0)
    n_samples = int(info.metadata.get("sample-count") or 0)
    total_s = n_samples / sr if sr else 0.0

    c1, c2, c3 = st.columns(3)
    c1.metric("Fréquence", f"{cf / 1e6:.3f} MHz")
    c2.metric("Sample rate", f"{sr / 1e6:.2f} MS/s")
    c3.metric("Durée", f"{total_s:.1f} s")

    # Preview PNG stored next to the capture (cheap; no full download).
    preview_key = data_key[: -len(".sigmf-data")] + "-preview.png"
    try:
        if storage.object_exists(preview_key):
            st.image(
                storage.download_bytes(preview_key),
                caption="Aperçu (au moment de l'enregistrement)",
                use_container_width=True,
            )
    except StorageError:
        pass

    # --- Scrub: render the spectrum of any time window (HTTP Range, ADR-009) --
    st.markdown("**Visualiser un instant de la capture**")
    if total_s <= 0 or sr <= 0:
        st.caption("Pas de sample rate exploitable pour cette capture.")
    else:
        col_a, col_b = st.columns(2)
        start = col_a.slider("Début (s)", 0.0, round(max(0.0, total_s - 0.1), 2), 0.0, 0.1)
        max_win = round(min(2.0, total_s), 2)
        dur = col_b.slider("Fenêtre (s)", 0.05, max_win, min(0.5, max_win), 0.05)
        if st.button("🔎 Afficher le spectre de la fenêtre", use_container_width=True):
            try:
                with st.spinner("Lecture de la fenêtre (HTTP Range)…"):
                    content = reader.read_segment(data_key, start_s=start, duration_s=dur)
            except StorageError as exc:
                st.error(f"Lecture impossible : {exc}")
            else:
                if len(content.samples) == 0:
                    st.warning("Fenêtre vide (au-delà de la fin ?).")
                else:
                    png = render_spectrum_png(content.samples, sr, cf)
                    st.image(
                        png,
                        caption=f"Spectre — t={start:.2f}s · {dur:.2f}s",
                        use_container_width=True,
                    )

    # --- Live ZeroMQ stream: show the ready-to-run command -------------------
    st.markdown("**Diffuser en direct (ZeroMQ)**")
    st.caption(
        "Sur le poste d'acquisition, lance la diffusion ; depuis n'importe quel poste, abonne-toi :"
    )
    st.code(
        f"uv run aerolake-stream --key {data_key} --bind tcp://*:5555\n"
        "uv run aerolake-subscribe --address tcp://<ip-du-poste>:5555",
        language="bash",
    )

    # --- Export for GNU Radio (the bridge to RF re-emission, mode 2) ---------
    st.markdown("**Exporter pour GNU Radio** (ré-émission RF — mode 2)")
    meta_key = data_key[: -len(".sigmf-data")] + ".sigmf-meta"
    try:
        st.download_button(
            "⬇ .sigmf-meta",
            storage.download_bytes(meta_key),
            file_name="capture.sigmf-meta",
            mime="application/json",
        )
        # The .sigmf-data can be huge — only fetch it into memory if the user
        # opts in (a download_button needs the bytes upfront, on every rerun).
        if st.checkbox("Préparer le téléchargement du .sigmf-data"):
            size = storage.object_size(data_key)
            if size <= 100_000_000:
                st.download_button(
                    "⬇ .sigmf-data",
                    storage.download_bytes(data_key),
                    file_name="capture.sigmf-data",
                    mime="application/octet-stream",
                )
            else:
                st.caption(
                    f"Fichier volumineux ({size / 1e6:.0f} Mo) — récupère-le plutôt "
                    f"via la console MinIO : {data_key}"
                )
    except StorageError as exc:
        st.error(f"Export impossible : {exc}")


def main() -> None:
    st.set_page_config(page_title="AeroLake", page_icon="📡", layout="centered")
    _inject_css()
    _render_background()
    _render_header()

    tab_capture, tab_playback = st.tabs(["📡 Capture", "▶ Playback"])
    with tab_capture:
        _render_capture()
    with tab_playback:
        _render_playback()


def run() -> None:
    """Console-script launcher (``aerolake-gui``): start Streamlit on this app."""
    import sys

    from streamlit.web import cli as stcli

    app_path = str(Path(__file__).resolve())
    sys.argv = ["streamlit", "run", app_path, "--server.address", "0.0.0.0"]
    sys.exit(stcli.main())


if __name__ == "__main__":
    main()
