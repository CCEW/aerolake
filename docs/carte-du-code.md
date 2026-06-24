# Carte du code AeroLake — le strict nécessaire

> But de ce document : **reprendre AeroLake en une lecture.** Il n'y a que ~17
> fichiers qui comptent (le « cœur »). Le reste est optionnel — on le repère plus
> bas. Si tu ne lis qu'une page, lis celle-ci.

---

## 1. Ce que fait AeroLake, en une phrase
**Enregistrer un signal radio → l'écrire au format SigMF → le ranger dans MinIO
avec un maximum de métadonnées.** C'est tout. Le reste (streaming, replay,
collections) est du bonus, pas le cœur de la mission.

Une capture rangée = 3 objets côte à côte dans MinIO :
```
{type_signal}/{date}/{session}/capture.sigmf-data   ← les échantillons IQ bruts
                              /capture.sigmf-meta    ← le JSON SigMF (description)
                              /capture-preview.png   ← l'aperçu spectre (auto)
```

---

## 2. Le chemin d'une capture (le « happy path »)

Quand tu lances `aerolake-capture --config ma_capture.json`, voici **dans l'ordre**
les fichiers traversés. C'est LA séquence à comprendre :

```
  ma_capture.json
        │
        ▼
 ┌─────────────────────────────────────────────────────────────────┐
 │ 1. scripts/capture.py        Point d'entrée CLI.                  │
 │                              Lit les arguments, orchestre le flux.│
 └─────────────────────────────────────────────────────────────────┘
        │ charge le JSON
        ▼
 ┌─────────────────────────────────────────────────────────────────┐
 │ 2. producer/config_loader.py + capture_config.py                 │
 │    JSON  ──►  objet CaptureConfig validé (pydantic).             │
 │    (fréquence, sample rate, durée, source, antenne, géoloc…)     │
 └─────────────────────────────────────────────────────────────────┘
        │ + géoloc éventuelle
        ▼
 ┌─────────────────────────────────────────────────────────────────┐
 │ 3. producer/gps.py        (optionnel) lit UNE position gpsd      │
 │                           → core:geolocation SigMF, sinon rien.  │
 └─────────────────────────────────────────────────────────────────┘
        │
        ▼
 ┌─────────────────────────────────────────────────────────────────┐
 │ 4. producer/orchestrator.py  ──  prepare_capture()               │
 │    LE chef d'orchestre. Il enchaîne :                            │
 │      a) génère/capte les échantillons IQ via la SOURCE :         │
 │           • synthetic.py    → signal de test (pas de matériel)   │
 │           • soapy_source.py → vrai SDR (RTL-SDR / BladeRF)       │
 │           • ingest.py       → fichiers RFSoC déjà enregistrés    │
 │      b) sigmf_writer.py  ──  encode()                            │
 │           échantillons + métadonnées  ►  bytes .sigmf-data/-meta │
 │           (+ sha512, num_channels, version SigMF…)               │
 └─────────────────────────────────────────────────────────────────┘
        │  "prepared" = bytes + clés + tags prêts
        ▼
 ┌─────────────────────────────────────────────────────────────────┐
 │ 5. (l'utilisateur confirme) orchestrator.py ── push_capture()    │
 │      a) preview.py  → rend l'aperçu PNG du spectre               │
 │      b) tout part dans MinIO via la SEULE porte de sortie :      │
 └─────────────────────────────────────────────────────────────────┘
        │
        ▼
 ┌─────────────────────────────────────────────────────────────────┐
 │ 6. common/storage.py  ──  StorageClient                          │
 │    LE point d'accès unique à MinIO/S3. TOUTE lecture/écriture    │
 │    passe ici (upload_multipart, download_range, tags…).          │
 │    Ordre d'upload : .sigmf-meta AVANT .sigmf-data (anti-orphelin)│
 └─────────────────────────────────────────────────────────────────┘
        │
        ▼
     MinIO (sur ton PC en dev, sur le NAS du labo en prod)
```

**Si tu retiens 3 fichiers :** `orchestrator.py` (l'enchaînement),
`sigmf_writer.py` (le format), `storage.py` (l'accès MinIO). Tout le reste gravite
autour.

---

## 3. Pour relire / parcourir le lakehouse
- `consumer/reader.py` — lister, inspecter, lire une capture (ou juste une fenêtre
  temporelle via HTTP Range).
- `scripts/catalog.py` (`aerolake-list`) — le catalogue : lister/filtrer par tag,
  **sans télécharger les octets** (requêtes HEAD seulement).

---

## 4. L'infra partagée (2 petits fichiers, lus partout)
- `common/config.py` — tous les réglages viennent d'ici (variables `AEROLAKE_*`
  + `.env`). **Pour pointer vers le NAS : on ne change QUE le `.env`**, aucune ligne
  de code.
- `common/logging.py` — logs structurés vers stderr (stdout reste propre pour les
  résultats).

---

## 5. Les 4 commandes du quotidien
| Commande | Fichier | Rôle |
|---|---|---|
| `aerolake-healthcheck` | scripts/healthcheck.py | Vérifie .env + MinIO joignable + bucket OK |
| `aerolake-capture --config x.json` | scripts/capture.py | **La capture** (synthétique ou SDR) → MinIO |
| `aerolake-ingest fichier --signal-type … ` | scripts/ingest.py | Ingérer un fichier IQ **réel** déjà existant |
| `aerolake-list --signal-type …` | scripts/catalog.py | Parcourir/filtrer le catalogue |

---

## 6. Ce que tu peux IGNORER pour faire tourner le lakehouse (périphérie)
Ces fichiers sont du **bonus** (utiles un jour, pas requis pour la mission
« record → SigMF → MinIO »). Si tu reprends le projet, tu peux les laisser de côté
au premier abord :

- `consumer/player.py` + `scripts/play.py` — rejouer une capture à sa cadence (ADR-007).
- `consumer/stream.py` + `scripts/stream.py` + `scripts/subscribe.py` — diffuser
  les trames sur un bus ZeroMQ Pub/Sub (ADR-008).
- `consumer/collection.py` + `scripts/collection.py` — regrouper plusieurs captures
  en une `.sigmf-collection` (ADR-014).

> Ils ont leurs tests et leur ADR ; rien n'est cassé. Ils ne sont juste pas sur le
> chemin critique de la mission.

---

## 7. Où aller ensuite
- **Le « pourquoi » de chaque choix** : `docs/adr/` (ADR-001 → 018).
- **Le cours détaillé du code** : `docs/cours-code-aerolake.docx`.
- **Reprendre / déployer / migrer vers le NAS** : `HANDOFF.md`.
- **Faire une vraie acquisition de bout en bout** : `./acquire.sh examples/<config>.json`.
```
                 Tu maîtrises 6 fichiers → tu maîtrises AeroLake.
        config.py · capture_config.py · orchestrator.py
        sigmf_writer.py · storage.py · reader.py
```
