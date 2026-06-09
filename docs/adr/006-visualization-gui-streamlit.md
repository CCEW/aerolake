# ADR-006 — Visualization GUI: Streamlit + Plotly web app

> **Archivé — hors-périmètre phase 1 (voir ADR-013).** Ce composant a été retiré de `main` et préservé sur la branche `archive/explorations-v1`. Cet ADR est conservé comme trace de décision.

- **Status:** Accepted
- **Date:** 2026-06-01
- **Author:** Théo Schmitt
- **Supersedes:** N/A

## Context

With the software infrastructure complete (Phase A: producer, quality layer,
`aerolake-validate`/`aerolake-list` CLIs, CI), the next deliverable is a
**visualization GUI** so a human can *see* a capture — its spectrum,
spectrogram, IQ constellation — and its quality verdict, rather than reading
raw metrics in a terminal.

Requirements gathered from the project lead and Théo (see
`docs/context/historique-discussions.md`):

- **Usable by anyone, easily** — minimal friction, no per-user install ideally.
- A **server (X)** is available to host it; remote MinIO at `fast.etsmtl.ca`.
- **Parametrable views**: choose FFT / spectrogram / constellation, see the
  quality report.
- An **aerospace aesthetic** is wanted, but *after* the infrastructure is solid
  ("finish the infra first, polish later").
- Scaling concern flagged: a flat dropdown of captures won't scale to ~10 000.

The choice of GUI technology is a real commitment (it will be maintained,
demoed, and deployed), so it warrants an ADR.

## Decision

**Build the GUI as a Streamlit web app, with Plotly for the charts.** The code
lives in a new `aerolake.gui` package and is launched via the `aerolake-gui`
console script (`uv run --group gui aerolake-gui`).

Structure (mirrors the project's existing "pure logic vs. glue" split):

- **`gui/plots.py`** — *pure functions* (no Streamlit, no I/O): `compute_*`
  return numpy arrays (Welch spectrum, STFT spectrogram), `*_figure` wrap them
  in themed Plotly figures. Unit-tested like `quality/metrics.py`.
- **`gui/theme.py`** — the aerospace dark palette as a Plotly template + a CSS
  snippet for the Streamlit chrome. Single source of truth for styling.
- **`gui/app.py`** — thin Streamlit glue: sidebar (select/filter capture, toggle
  views, set params) + main panel (info, quality report, figures). Reads go
  through `CaptureReader`/`StorageClient` (never S3 directly) and are cached.
- **`gui/launch.py`** — wrapper exposing `aerolake-gui`.

Dependencies live in an optional `gui` dependency-group (`streamlit` + `plotly`)
so the core pipeline/CLIs stay lean. `plotly` is *also* in the `dev` group so
the pure plot functions are unit-tested in CI without installing Streamlit.

## Rationale

- **Fastest path to a working, shareable tool.** Streamlit is pure Python, no
  HTML/JS/CSS scaffolding, and serves a browser UI out of the box — directly
  satisfying "usable by anyone, no install" (open a URL) and "host on the
  server".
- **Plotly is genuinely good-looking and interactive** (zoom, hover, pan) and
  themeable to the aerospace dark look now, with room to polish later — matching
  "infra first, aesthetics after" without sacrificing all aesthetics.
- **Pedagogical & maintainable.** Python-only, readable, heavily commented; the
  DSP is isolated in pure, tested functions. Easy for Théo (learning) and for a
  successor to pick up.
- **Reuses the existing stack.** The GUI sits on top of `CaptureReader` — no new
  data path, no bypass of the storage chokepoint, consistent with ADR-001/003.

## Consequences

### Positive

- A browser-accessible explorer with zero client install; deployable on the lab
  server and pointable at local or remote MinIO via the same `.env`.
- The DSP (spectrum/spectrogram) is unit-tested; only the thin Streamlit glue is
  not, which is the standard trade-off for UI code.
- Styling is centralised; restyling later touches one file.

### Negative

- Streamlit's re-run-on-every-interaction model requires explicit caching
  (`cache_resource`/`cache_data`) to avoid re-downloading captures — handled,
  but a foot-gun to remember.
- Less pixel-level layout control than a hand-built front-end; a very bespoke
  aerospace look may later justify Dash or a custom front (revisit if needed).
- The capture selector is a dropdown today; it does **not** yet address the
  ~10 000-capture scaling concern (see Future work).

### Neutral

- `streamlit` is a heavy dependency, but isolated in the optional `gui` group;
  the core install and CI runtime are unaffected.

## Alternatives considered

- **Dash (Plotly):** more layout/aesthetic control, but more code (callbacks)
  for the same MVP. Reasonable future migration if the bespoke look demands it.
- **Desktop app (PyQt/Tkinter):** native, but needs a per-user install and
  doesn't fit "open a URL on the server, usable by anyone".
- **Custom Flask + Plotly.js front-end:** maximum control and aesthetics, far
  more work (HTML/CSS/JS) — premature before the infra is validated on real
  data.

## Future work

- Replace the flat capture dropdown with a tag-based filter/search (lean on
  `aerolake-list`'s filtering) to scale past thousands of captures.
- Deploy on the lab server; point at the remote `fast.etsmtl.ca` MinIO.
- Add time-domain and PSD-in-dBFS views; aesthetic polish pass.

## References

- `docs/context/historique-discussions.md` (GUI requirements, aesthetic, server)
- `src/aerolake/gui/` (implementation), `tests/gui/test_plots.py`
- ADR-001 (boto3 client), ADR-003 (metadata/tags discovery used by the catalog)
