# Runbook — Test banc : générateur de signal → SDR → lakehouse

Objectif : capturer un signal connu (générateur CW) avec un **RTL-SDR** ou un
**BladeRF**, via un **fichier de config**, et le ranger dans le lakehouse MinIO.

---

## 0. Prérequis logiciels (à faire une fois, avant)

```bash
cd ~/code/lassena/aerolake

# 1) Pont SoapySDR dans le venv (À RELANCER après chaque `uv sync` !)
bash setup-soapy.sh                       # doit afficher "SoapySDR ... OK"

# 2) Démarrer MinIO (le lakehouse)
cd docker && docker compose up -d && cd ..

# 3) Vérifier que tout est joignable
uv run aerolake-healthcheck               # doit être vert
```

---

## 1. Montage matériel

1. **Câble coaxial** : sortie du générateur → entrée **RX** du SDR (SMA).
   - RTL-SDR : le port antenne.
   - BladeRF : port **RX1** (ou **RX**).
2. ⚠️ **PUISSANCE — le point critique pour ne rien griller** :
   - Commence **TRÈS BAS** sur le générateur : **−40 dBm** (voire moins).
   - **Ne dépasse jamais ~−10 dBm** sur un RTL-SDR (tu risques de détruire le front-end).
   - Tu **monteras** le niveau ensuite, petit à petit, jusqu'à voir la tonalité proprement.
3. **Fréquence** (le SDR doit pouvoir la recevoir) :
   - RTL-SDR : 24 MHz → 1.7 GHz (ex. générateur **100.25 MHz**).
   - BladeRF : 47 MHz → 6 GHz (ex. générateur **1000.25 MHz**).
4. ⚠️ **Décalage (offset) — sinon tu ne verras rien** : règle le générateur
   **~250 kHz AU-DESSUS** du `center_freq` de la config. Le SDR a un gros pic
   parasite pile au centre (« DC spike ») ; en décalant, ta tonalité apparaît à
   **+250 kHz**, bien visible à côté.

---

## 2. Régler le fichier de config

Deux configs prêtes : `examples/test-rtlsdr.json` et `examples/test-bladerf.json`.
La **seule chose à ajuster** : `center_freq` = (fréquence du générateur − 250 000).

Exemple RTL-SDR : générateur à **100.25 MHz** → `center_freq` = **100000000**.

```json
{
  "signal_type": "test_banc",
  "center_freq": 100000000,      // ← générateur − 250 kHz
  "sample_rate": 2000000,        // 2 MS/s (OK RTL-SDR et BladeRF)
  "duration_s": 2.0,             // court pour un premier test
  "source": { "type": "soapy", "driver": "rtlsdr", "agc": true },
  "operator": "schmitt",
  "location": { "name": "labo LASSENA", "mobile": false }
}
```

(Le JSON ne supporte pas les commentaires `//` — c'est juste pour l'explication.)

---

## 3. Lancer la capture

```bash
uv run aerolake-capture --config examples/test-rtlsdr.json
```

- Un **résumé** s'affiche (fréquence, durée, source).
- La capture se fait, puis : **« Push this capture to MinIO? »** → réponds **y**
  pour l'envoyer dans le lakehouse. (Si tu réponds non, tu peux la garder en
  local sous `captures/`.)
- Les warnings `Avahi` / `RtApi` au démarrage = bruit SoapySDR, **à ignorer**.

---

## 4. Vérifier dans le lakehouse

```bash
uv run aerolake-list --signal-type test_banc      # ta capture doit apparaître
```

---

## 5. Vérifier que la tonalité est là (FFT)

Lit la dernière capture et affiche la fréquence du pic (≈ +250 kHz attendu) et
le niveau RMS (pour voir si c'est trop fort/trop faible) :

```bash
uv run python - <<'PY'
import numpy as np
from aerolake.consumer.reader import CaptureReader
r = CaptureReader()
key = r.list_captures(prefix="test_banc/")[-1]      # la plus récente
c = r.read(key)
x = c.samples
sr = float(c.sigmf_meta["global"]["core:sample_rate"])
n = min(len(x), 65536)
spec = np.fft.fftshift(np.abs(np.fft.fft(x[:n])))
freqs = np.fft.fftshift(np.fft.fftfreq(n, 1 / sr))
peak = freqs[np.argmax(spec)]
rms = 20 * np.log10(np.sqrt(np.mean(np.abs(x) ** 2)) + 1e-12)
print(f"capture : {key}")
print(f"pic     : {peak/1e3:+.1f} kHz du centre   (attendu ~ +250 kHz)")
print(f"niveau  : {rms:.1f} dBFS   ({len(x)} samples)")
PY
```

Lecture du résultat :
- **pic ≈ +250 kHz** → ✅ ta tonalité est bien capturée et géolocalisée en fréquence.
- **niveau entre −30 et −10 dBFS** → bon niveau.
- **niveau ~0 dBFS** → trop fort, **baisse** le générateur (ça écrête).
- **niveau < −50 dBFS / pas de pic net** → trop faible, **monte** le générateur.

---

## 6. Dépannage

| Symptôme | Cause / solution |
|---|---|
| `No SDR found for driver=...` | Appareil pas branché, mauvais `driver`, ou droits USB. Vérifie `SoapySDRUtil --find`. Au besoin, règles udev ou teste avec `sudo`. |
| `ModuleNotFoundError: SoapySDR` | Relance `bash setup-soapy.sh` (le pont saute après un `uv sync`). |
| Un seul pic pile au centre (0 kHz) | C'est le DC spike ; ta tonalité est cachée → décale le générateur de +250 kHz. |
| Niveau ~0 dBFS / signal écrêté | Baisse le niveau du générateur. |
| Pas de pic / niveau très bas | Monte le générateur ; vérifie câble + fréquence + offset. |
| MinIO injoignable | `cd docker && docker compose up -d` puis `uv run aerolake-healthcheck`. |
| BladeRF : erreur sur l'antenne | Enlève le champ `"antenna"` de la config (laisse le défaut). |

---

## Limite connue (gain)

La config n'expose pas le **gain** : c'est l'AGC si `"agc": true`, sinon un gain
fixe de 40 dB. Pour un test au générateur, **le niveau du générateur est ton vrai
bouton de réglage**. Si tu veux un gain explicite dans la config, c'est un ajout
de ~5 min — demande-le.
