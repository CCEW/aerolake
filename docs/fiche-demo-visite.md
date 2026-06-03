# 🎫 Fiche démo AeroLake — visite labo (1 page)

> But : montrer le pipeline **de la vraie capture Iridium → visualisation → curation
> → diffusion réseau → ouverture RF**. Tout depuis WSL, dossier `~/code/lassena/aerolake`.
> Capture vedette : `iridium/2026-06-02/1198dcdf/capture.sigmf-data` (≈14,8 min, 355 M échant.).

## 0. Avant qu'ils arrivent (2 min)
```bash
cd ~/code/lassena/aerolake/docker && docker compose up -d   # MinIO
cd ~/code/lassena/aerolake && uv run aerolake-healthcheck    # doit dire ✓ passed
uv run --group gui aerolake-gui                              # → http://localhost:8501
```
Ouvre aussi la console MinIO **http://localhost:9001** dans un onglet (preuve du stockage).

## 1. « On stocke de vraies données RF » (1 min)
- IHM → **Prefix filter** = `iridium/` → choisis la capture.
- Pointe : **Total 887,6 s · 355 060 000 échantillons** (vraie acquisition RFSoC, 2,84 Go).
- Console MinIO : montre l'objet `.sigmf-data` + `.sigmf-meta` + les **tags** (signal-type, hardware, quality).

## 2. « On la visualise, sans tout télécharger » (1 min)
- Onglet **Spectre / Spectrogramme** → la **porteuse Iridium à ~1626,15 MHz, ~56 dB** au-dessus du bruit.
- Coche **🔭 Whole-capture overview** → spectrogramme de **toute la durée** (lectures Range, quelques Mo).
- Active **▶ Animated playback** → la fenêtre **défile toute seule** (tête de lecture).

## 3. « On curate la qualité » (30 s)
- Volet **🎛️ Actions** → **✅ Validate** → le tag passe `raw → validated`.
- (Preuve indépendante au terminal) `uv run aerolake-list --signal-type iridium`.

## 4. « On la diffuse sur le réseau » (1 min) — 2 terminaux
```bash
# Terminal ABONNÉ (lance-le EN PREMIER) :
uv run aerolake-subscribe --frames 8
# Terminal ÉMETTEUR (ou bouton ▶ Start stream de l'IHM) :
uv run aerolake-stream --prefix iridium/ --duration 5
```
→ l'abonné affiche les frames reçues (index, samples, **RMS dBFS**). *« Un autre appareil =
`--address tcp://<mon-IP>:5555` »*.

## 5. Ouverture : « Et la semaine prochaine : ré-émission RF réelle » (30 s)
```bash
uv run aerolake-fetch --key iridium/2026-06-02/1198dcdf/capture.sigmf-data \
    --out /tmp/capture.sigmf-data --duration 30      # pont MinIO → fichier
grcc -o /tmp gnuradio/transmit_sdr.grc               # flowgraph TX validé
```
→ « avec une **BladeRF** + câble blindé/atténuateur, on rejoue le signal **physiquement** vers un récepteur ».

---
### 🆘 Plan B (si ça coince)
- **IHM injoignable** → relance `uv run --group gui aerolake-gui` ; vérifie le port 8501.
- **MinIO KO** → `cd docker && docker compose up -d` puis `aerolake-healthcheck`.
- **Stream : abonné ne reçoit rien** → relance l'abonné **avant** l'émetteur (ZeroMQ « slow joiner »).
- **Démo de secours sans Iridium** → tout marche aussi avec `--prefix gnss_l1/` (capture synthétique).

### 🗣️ Phrases-clés à dire
- « Un **data-lakehouse RF** : on ingère de vraies captures, on les stocke en **SigMF** avec métadonnées + qualité, on les retrouve, valide, rejoue et diffuse — **de n'importe où**. »
- « **Lecture partielle** (HTTP Range) : on ouvre une capture de plusieurs Go **instantanément** et on navigue dedans. »
- « Optimisations du mandat livrées : **multipart upload**, **Range**, **ZeroMQ**, **playback à la cadence**. »
- « Étape suivante : **ré-émission RF** sur matériel (BladeRF), en environnement câblé/blindé. »
