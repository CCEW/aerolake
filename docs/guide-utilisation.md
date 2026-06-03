# Guide d'utilisation — de la capture RFSoC à la visualisation (sans aide)

> Tout ce qu'il faut taper, dans l'ordre, pour : **ingérer** une mesure RFSoC →
> **vérifier** qu'elle est dans MinIO → la **visualiser** dans l'IHM → la
> **rejouer**. Toutes les commandes se lancent depuis le terminal **Ubuntu (WSL)**,
> dans le dossier du projet :
>
> ```bash
> cd ~/code/lassena/aerolake
> ```

## 0. Démarrer le stockage MinIO (une fois)

```bash
cd ~/code/lassena/aerolake/docker && docker compose up -d   # démarre MinIO
cd ~/code/lassena/aerolake
uv run aerolake-healthcheck                                 # doit afficher "✓ passed"
```
- Console web MinIO : **http://localhost:9001** (user `aerolake_admin`, mdp dans `.env`).

## 1. Ingérer ta mesure RFSoC

Tu as fait une mesure sur le toit → tu as un **dossier de paquets** `RX0_pkt_*.bin`
(ou un seul fichier IQ). Une seule commande :

```bash
uv run aerolake-ingest <CHEMIN> \
    --glob 'RX0_pkt_*.bin' \
    --signal-type iridium \
    --sample-rate 400e3 \
    --center-freq 1626.271e6 \
    --datatype cs32 \
    --hardware rfsoc
```

- `<CHEMIN>` = le **dossier** des paquets (ils sont concaténés dans l'ordre
  numérique) **ou** un fichier unique.
- **`--datatype`** : `cs32` (RFSoC int32), ou `cf32`/`cs16`/`cu8` selon la source.
- **`--sample-rate` / `--center-freq`** : tes vrais paramètres d'acquisition (Hz).
- **`--signal-type` / `--hardware`** : deviennent des **tags** (source, type).
- Gros volume = pas de souci : c'est **streamé en multipart** (RAM bornée).

→ Affiche la `Data key` (ex. `iridium/2026-06-02/<id>/capture.sigmf-data`).

## 2. Vérifier qu'elle est dans le lac

```bash
uv run aerolake-list --signal-type iridium     # liste + filtre par tag (rapide, sans télécharger)
uv run aerolake-validate --prefix iridium/ --expected-duration <durée_s>   # verdict qualité
```
*(Astuce durée : `durée_s = nb_échantillons / sample_rate`.)*

## 3. Visualiser dans l'IHM

```bash
uv run --group gui aerolake-gui      # ouvre http://localhost:8501
```
Dans le navigateur :
1. **Prefix filter** → `iridium/` puis choisis ta capture.
2. **Time window** : `Window` = durée affichée, `Start` = position → tu navigues
   dans toute la durée (lecture partielle, rien n'est rechargé inutilement).
3. **🔭 Whole-capture overview** : coche-le pour voir le **spectrogramme de toute
   la durée** d'un coup (lectures réparties, quelques Mo).
4. Onglets : **Spectre**, **Spectrogramme**, **Constellation**.

> Pour arrêter le serveur IHM : `Ctrl+C` dans le terminal où il tourne.

### 3 bis. Tout piloter depuis l'IHM (sans revenir au terminal)

La barre latérale expose désormais **tout le workflow** — pratique pour une démo :

- **📥 Ingest a capture** : un formulaire (chemin du fichier/dossier, glob, type,
  sample-rate, center-freq, datatype, hardware) + bouton → lance `ingest_files`
  (multipart). La nouvelle capture apparaît aussitôt dans la liste.
- **🎛️ Actions on this capture** :
  - **📡 ZeroMQ stream** : `▶ Start stream` lance `aerolake-stream` en tâche de
    fond (fenêtre bornée par *Stream duration* → lecture partielle), `⏹ Stop` l'arrête.
  - **🏷️ Quality** : `✅ Validate` / `❌ Reject` / `↩ Raw` change le **tag qualité**
    (lire-fusionner-écrire, sans écraser les autres tags).
  - **🗑️ Danger zone** : coche la confirmation puis `🗑️ Delete capture` (supprime
    data + meta + rapport qualité).
- **▶ Animated playback** (section *Time window*) : fait **défiler automatiquement**
  la fenêtre dans toute la durée — le spectre/spectrogramme avance comme une tête de
  lecture (chaque pas = une lecture Range mise en cache).

## 4. Rejouer la capture (playback)

```bash
uv run aerolake-play --prefix iridium/                       # toute la durée, à la cadence réelle
uv run aerolake-play --prefix iridium/ --start 200 --duration 30   # à partir de t=200s, 30s
uv run aerolake-stream --prefix iridium/                     # diffuse sur ZeroMQ (émetteur)
uv run aerolake-subscribe --address tcp://localhost:5555     # reçoit le flux (abonné)
```
*(L'abonné peut tourner sur **un autre appareil** : `--address tcp://<IP-du-PC>:5555`.
Lance l'abonné **avant/pendant** l'émetteur — ZeroMQ « slow joiner ».)*

## 5. Ré-émettre la capture en VRAIE RF (BladeRF, ADR-012)

C'est la couche matérielle : renvoyer le signal **physiquement** par un SDR pour
qu'un vrai récepteur le capte. Il faut une **BladeRF** (la RTL-SDR est réception
seule) et **GNU Radio système** (`sudo apt install gnuradio`).

> ⚠️ **Légal & sécurité** : émettre sur GNSS (1575.42 MHz) ou Iridium (~1626 MHz)
> **en l'air est illégal et brouille des récepteurs critiques**. Branche toujours
> par **câble blindé + atténuateur**, **charge fictive** ou **cage de Faraday**.

```bash
# 1) Récupérer la capture (ou une fenêtre) de MinIO vers un fichier local :
uv run aerolake-fetch --key iridium/2026-06-02/<id>/capture.sigmf-data \
    --out /tmp/capture.sigmf-data --start 100 --duration 30
#    → imprime samp_rate (ex. 400000) et freq (ex. 1626271000) à recopier.

# 2) Valider le flowgraph d'émission (sans matériel) :
grcc -o /tmp gnuradio/transmit_sdr.grc

# 3) L'ouvrir, régler capture_file / samp_rate / freq / tx_gain, puis lancer :
gnuradio-companion gnuradio/transmit_sdr.grc
```
Dans `transmit_sdr.grc` : `sdr_driver="bladerf"`, `tx_amplitude` (0..1, défaut 0.8)
= marge numérique avant le DAC, `tx_gain` = gain analogique (**commence bas**),
`repeat` = boucler ou émettre une fois. Un afficheur de spectre montre ce que tu émets.

## Aide-mémoire (toutes les commandes)

| Action | Commande |
|---|---|
| Démarrer MinIO | `cd docker && docker compose up -d` |
| Santé du stockage | `uv run aerolake-healthcheck` |
| **Ingérer** un dossier RFSoC | `uv run aerolake-ingest <dir> --glob 'RX0_pkt_*.bin' --signal-type iridium --sample-rate 400e3 --center-freq 1626.271e6 --datatype cs32 --hardware rfsoc` |
| Lister / filtrer | `uv run aerolake-list --signal-type iridium` |
| Valider la qualité | `uv run aerolake-validate --prefix iridium/ --expected-duration <s>` |
| **IHM** (visualiser) | `uv run --group gui aerolake-gui` → http://localhost:8501 |
| Playback (cadence réelle) | `uv run aerolake-play --prefix iridium/` |
| Streaming ZeroMQ (émettre) | `uv run aerolake-stream --prefix iridium/` |
| **S'abonner** au flux ZeroMQ (recevoir) | `uv run aerolake-subscribe --address tcp://<IP>:5555 [--topic iridium --frames 10]` |
| **Exporter** vers un fichier (pont GNU Radio) | `uv run aerolake-fetch --key <key> --out /tmp/capture.sigmf-data [--start 100 --duration 30]` |
| **Ré-émettre en RF** (BladeRF) | `grcc -o /tmp gnuradio/transmit_sdr.grc` puis `gnuradio-companion gnuradio/transmit_sdr.grc` |
| Viewer .h5 (GPS/IMU/Iridium décodé) | `uv run --group gui aerolake-analysis` |

> Toutes les CLI ont une aide : ajoute `--help` (ex. `uv run aerolake-ingest --help`).
