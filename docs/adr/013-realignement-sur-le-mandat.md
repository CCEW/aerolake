# ADR-013 — Réalignement sur le périmètre du mandat (recadrage)

- **Status:** Accepted
- **Date:** 2026-06-08
- **Author:** Théo Schmitt
- **Supersedes:** réoriente la priorisation de l'ADR-004 (ne supprime pas l'ADR-004 ; en corrige la portée)

## Contexte

Le mandat du projet (`docs/LASSENA-Project_AeroLake.pdf`) définit un périmètre
clair, organisé en quatre sprints : un pipeline RX de bout en bout — capture SDR
→ stockage MinIO au format SigMF (avec métadonnées et tags) → extraction par
HTTP Range Requests → bus de streaming ZeroMQ — capable de soutenir 25 MHz, et
**prêt** (mais pas tenu) à accueillir une émission TX dans une phase future.

Deux écarts se sont accumulés par rapport à ce périmètre :

1. **Une réorientation de priorité (ADR-004).** À la suite d'un échange avec mon
   superviseur, la priorité avait été déplacée vers la *qualité des données* et
   la constitution d'un *dataset curé*, ce qui a conduit à **différer le chemin
   streaming** (multipart upload, HTTP Range, ZeroMQ) — pourtant au cœur du
   mandat. La couche qualité elle-même était légitime ; c'est sa mise en *tête
   de priorité*, au point de repousser le cœur du mandat, qui a fait diverger le
   projet de l'ordre attendu.

2. **Des explorations non demandées.** Au-delà de cette réorientation, le dépôt
   s'est enrichi de composants absents du mandat : une interface graphique de
   visualisation (Streamlit/Plotly, ADR-006), un module d'analyse de données
   décodées (Doppler/IMU/GPS sur fichiers `.h5`, ADR-011), et un chemin
   d'émission TX (flowgraph BladeRF + pont MinIO→fichier, ADR-012). Le TX est
   explicitement désigné « future phase » par le mandat ; la GUI et l'analyse
   n'y figurent pas du tout.

Un point récent avec mon superviseur a confirmé ce constat : le projet s'était
éloigné de ce qui était demandé. Le présent ADR acte le recadrage.

## Décision

**Nous réalignons `main` sur le périmètre du mandat. Le pipeline RX → MinIO →
extraction → ZeroMQ redevient la priorité ; le reste est conservé mais sorti du
chemin principal.**

1. **Le chemin streaming reprend sa place de priorité.** L'extraction par HTTP
   Range Requests et la publication ZeroMQ Pub/Sub ne sont plus différés : ils
   constituent le cœur livrable du mandat (Sprints 2–3). La priorisation inverse
   de l'ADR-004 (qualité d'abord, streaming plus tard) est annulée.

2. **La couche qualité est conservée comme outil de support, pas comme axe
   central.** `aerolake.quality` (métriques + checker) et
   `CaptureReader.validate()` restent dans le projet : ils servent les critères
   de validation des sprints du mandat (waterfall intact, `Samples In == Samples
   Out`, rapport de profilage sans perte). Ce qui change, c'est leur *statut* :
   un moyen de valider les captures, non la finalité du projet.

3. **Les explorations hors-périmètre sont archivées, non supprimées.** La GUI
   (ADR-006), l'analyse `.h5` (ADR-011) et le TX (ADR-012) sont retirés de `main`
   et préservés intégralement sur la branche `archive/explorations-v1`. Elles
   restent récupérables à tout moment et pourront redevenir pertinentes dans une
   phase ultérieure.

4. **Les ADR concernés ne sont pas effacés.** ADR-006, ADR-011 et ADR-012
   restent dans `docs/adr/`, marqués « archivé — hors-périmètre phase 1 (voir
   ADR-013) ». La trace des décisions est conservée ; on ne réécrit pas
   l'historique.

## Justification

- **Le mandat fait foi.** Le périmètre attendu est écrit noir sur blanc dans le
  PDF du projet ; en cas de divergence entre une initiative locale et le mandat,
  le mandat tranche.
- **Rien n'est perdu.** L'archivage par branche Git garantit que le travail
  réalisé (de qualité, mais prématuré) reste disponible. Le recadrage est
  entièrement réversible.
- **Le cœur est sain.** Après retrait des composants hors-périmètre, la suite de
  tests passe (124 passés, 1 skippé — le test d'intégration nécessitant un MinIO
  réel), `ruff` et `mypy` ne signalent rien : aucun élément du cœur ne dépendait
  du surplus retiré.
- **La qualité reste utile sans être centrale.** La conserver comme support, et
  non comme finalité, satisfait à la fois la consigne passée (mesurer la qualité
  des captures) et le mandat (livrer le pipeline RX→ZMQ).

## Conséquences

### Positives

- `main` reflète désormais le mandat : un lecteur (encadrement, nouvel arrivant)
  comprend immédiatement le cœur du projet sans être noyé sous des composants
  périphériques.
- Le projet est repositionné pour reprendre l'ordre des sprints du mandat
  (extraction + ZeroMQ, puis tests de débit GNSS/Iridium/Starlink 25 MHz).
- L'empreinte de dépendances est réduite (retrait de streamlit, plotly, h5py,
  skyfield et de leurs transitives).

### Négatives

- Les fonctionnalités archivées ne sont plus accessibles depuis `main` ; les
  réactiver demandera un travail de réintégration (et un nouvel ADR le moment
  venu).

### Neutres

- Les conventions de stockage, métadonnées et tags (ADR-001, ADR-003) sont
  inchangées.
- L'ADR-004 demeure consultable comme trace de la réorientation passée ; le
  présent ADR en corrige seulement la priorité, sans nier l'échange qui l'avait
  motivée.

## Plan de sprints réaligné

- **Sprint 1 — fait.** Infrastructure d'ingestion : stack MinIO, `StorageClient`,
  producteur synthétique, encodage SigMF, `CaptureReader`, convention
  métadonnées + tags, CLI healthcheck. (Capture SDR réelle via SoapySDR : encore
  à venir — le producteur génère aujourd'hui du synthétique.)
- **Sprint 2 — priorité courante.** Extraction par HTTP Range Requests +
  publication ZeroMQ Pub/Sub, avec preuve `Samples In == Samples Out`. La couche
  qualité sert ici d'outil de validation.
- **Sprint 3 — à suivre.** Tests de débit : GNSS (position lock via GNSS-SDR),
  Iridium (continuité sur 1 h), Starlink 25 MHz (200 Mo/s soutenus).
- **Sprint 4 — à suivre.** Externalisation `.env`/CLI, documentation Confluence,
  guide de migration on-prem.
- **Phases futures (archivées).** Visualisation (ADR-006), analyse `.h5`
  (ADR-011), émission TX (ADR-012), évolution analytique Parquet/Iceberg.

## Références

- `docs/LASSENA-Project_AeroLake.pdf` — mandat du projet (périmètre faisant foi)
- ADR-004 — réorientation qualité dont le présent ADR corrige la priorité
- ADR-002 — report batch/streaming (le streaming reprend ici sa priorité)
- ADR-006, ADR-011, ADR-012 — décisions des composants archivés
- Branche `archive/explorations-v1` — état complet préservé avant recadrage
