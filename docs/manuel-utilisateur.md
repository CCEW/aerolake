# AeroLake — Manuel utilisateur

> **Pour qui ?** Toute personne du LASSENA qui veut **enregistrer, retrouver ou
> rejouer** des signaux RF — sans être développeur. Aucune ligne de commande
> n'est nécessaire pour l'usage quotidien.

---

## 1. AeroLake en 30 secondes

AeroLake est le **lakehouse RF** du labo : chaque acquisition est enregistrée au
format standard **SigMF**, rangée dans le stockage partagé **MinIO** (serveur
FAST), décrite par des **métadonnées et tags cherchables**, avec un **aperçu du
spectre** généré automatiquement.

Une capture rangée = 3 objets côte à côte :

```
{type_signal}/{date}/{session}/capture.sigmf-data    ← le signal (échantillons IQ bruts)
                               capture.sigmf-meta    ← sa description (JSON SigMF)
                               capture-preview.png   ← l'aperçu spectre + waterfall
```

Le **signal lui-même** est conservé, échantillon par échantillon (intégrité
sha512) : ce qu'on rejoue est **exactement** ce qui a été reçu.

## 2. Les adresses à connaître

| Quoi | Où |
|---|---|
| **Interface AeroLake** (capture + playback) | `http://<poste-acquisition>:8501` |
| **Console MinIO de FAST** (parcourir le lakehouse) | https://minio.fast.etsmtl.ca/browser |
| Portail FAST | https://fast.etsmtl.ca |
| Code source + documentation | dépôt `aerolake` (GitLab LASSENA) |

## 3. Faire une capture (interface web — recommandé)

**Étape 0 — ouvrir l'interface.** Sur le poste d'acquisition : double-cliquer
le raccourci **« AeroLake GUI »** (le navigateur s'ouvre tout seul). Depuis un
autre PC du réseau : ouvrir `http://<poste>:8501`.

**Étape 1 — déposer une config.** Glisser un fichier **`.toml`** (recommandé)
ou `.json` décrivant la capture. Des **modèles commentés** sont dans le dossier
`examples/` du dépôt — copier un modèle, ajuster 3-4 valeurs, c'est prêt :

```toml
signal_type = "gnss_l1"          # catégorie → rangement + tag de recherche
center_freq = 1_575_420_000      # fréquence centrale en Hz
sample_rate = 2_000_000          # échantillonnage en Hz (= bande captée)
duration_s  = 10                 # durée en secondes

[source]
type   = "soapy"                 # "soapy" = vrai SDR ; "synthetic" = signal de test
driver = "rtlsdr"                # rtlsdr, bladerf, …
```

> 📄 `examples/capture.example.toml` = modèle minimal commenté ;
> `examples/capture.full.toml` = **tous** les champs possibles, marqués
> *(obligatoire)* / *(optionnel)* — garder ce qui sert, supprimer le reste.

**Étape 2 — (option) pointer l'antenne sur la carte.** Ouvrir le volet
« 📍 Régler la position », cliquer l'endroit exact de l'antenne → la position
part dans les métadonnées SigMF. (Sinon la position du fichier de config est
utilisée.)

**Étape 3 — Démarrer.** Un clic. L'appli valide la config **avant** de toucher
au matériel, capture, puis affiche : nombre d'échantillons, taille, durée, et
le **spectre**.

**Étape 4 — décider.**
- **⬆ Pousser dans MinIO** → la capture rejoint le lakehouse partagé ;
- **💾 Garder en local** → écrite sur le disque du poste (dossier `captures/`) ;
- **🗑 Jeter** → rien n'est stocké.

## 4. Retrouver et regarder une capture

**Sans rien installer** : ouvrir la **console MinIO**
(https://minio.fast.etsmtl.ca/browser) → bucket `aerolake-captures` → naviguer
par type de signal puis par date → cliquer le **`capture-preview.png`** pour
voir le spectre immédiatement.

**Dans l'interface AeroLake** : onglet **▶ Playback** → choisir une capture
dans la liste → métadonnées + aperçu s'affichent → avec les curseurs
*Début / Fenêtre*, visualiser le **spectre de n'importe quel instant** (seule
la fenêtre demandée est téléchargée, même sur une capture de plusieurs Go).

## 5. Rejouer une capture

Trois modes, du plus simple au plus complet :

1. **Visualiser** un instant : onglet Playback (ci-dessus).
2. **Diffuser en direct sur le réseau (ZeroMQ)** : l'onglet Playback affiche la
   commande prête à copier —
   ```bash
   uv run aerolake-stream --key <capture> --bind tcp://*:5555      # côté émetteur
   uv run aerolake-subscribe --address tcp://<poste>:5555          # côté récepteur
   ```
3. **Ré-émettre en RF** (GNU Radio + SDR émetteur, ex. BladeRF) : bouton
   **« Exporter pour GNU Radio »** dans l'onglet Playback → charger le
   `.sigmf-data` dans le flowgraph `gnuradio/playback.grc`. *(Volet RF du
   projet — voir ADR-019 ; référente : Camila.)*

## 6. Pour les utilisateurs avancés : la ligne de commande

Toutes les fonctions existent aussi en CLI (depuis le dépôt) :

```bash
uv run aerolake-healthcheck                       # la config et MinIO répondent ?
uv run aerolake-capture --config ma_capture.toml  # capture pilotée par fichier
uv run aerolake-list --signal-type gnss_l1        # catalogue : lister/filtrer par tag
uv run aerolake-ingest fichier.bin --signal-type X --sample-rate 2e6 --center-freq 1575.42e6
                                                  # ingérer un IQ déjà enregistré (ex. GNU Radio, RFSoC)
uv run aerolake-play --prefix gnss_l1/ --start 200 --duration 10   # rejouer une fenêtre, cadencé
uv run aerolake-collection --prefix gnss_l1/2026-07-01/ --name "campagne"  # grouper en .sigmf-collection
./acquire.sh ma_capture.toml                      # tout-en-un : USB + SoapySDR + healthcheck + capture
```

## 7. Monter un nouveau poste d'acquisition

C'est l'affaire d'un référent technique, une seule fois par poste — la
procédure complète (prérequis, `.env`, USB/WSL, raccourci de lancement,
auto-démarrage) est dans **`HANDOFF.md`** à la racine du dépôt.

L'essentiel : cloner le dépôt, `uv sync --extra gui`, `bash setup-soapy.sh`,
copier `.env.example` → `.env` et y mettre l'endpoint FAST
(`https://minio-api.fast.etsmtl.ca`) + les clés d'accès du compte de service,
puis créer le raccourci vers `launch-gui.vbs`.

## 8. Dépannage express

| Symptôme | Cause probable | Remède |
|---|---|---|
| « Could not connect to the endpoint URL » au Push | MinIO injoignable | FAST : vérifier réseau/VPN et le `.env`. Local : démarrer Docker (`cd docker && docker compose up -d`) |
| « No SDR found for driver=… » | SDR pas vu par WSL | Rebrancher, puis `./acquire.sh` (fait l'`usbipd attach`), ou vérifier `usbipd list` côté Windows |
| Spectre « écrasé » / plat | Gain trop fort (clipping) | Baisser la puissance d'entrée ou laisser `agc = true` |
| Le GUI ne s'ouvre pas | Appli pas lancée | Double-clic « AeroLake GUI » sur le poste ; sinon voir HANDOFF §5 |
| `aerolake-healthcheck` échoue | `.env` incomplet/faux | Vérifier endpoint, clés, nom du bucket |
| Import SoapySDR échoue après `uv sync` | Pont venv cassé | Relancer `bash setup-soapy.sh` |

## 9. Où trouver plus

- **`docs/carte-du-code.md`** — la carte du code en une page (par où commencer).
- **`docs/documentation-code.md`** — la référence complète du code.
- **`docs/adr/`** — le *pourquoi* de chaque décision (ADR-001 → 020).
- **`HANDOFF.md`** — reprise du projet, installation d'un poste, migration.
