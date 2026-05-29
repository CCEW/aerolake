# Historique projet — contexte issu des discussions de cadrage

> Synthèse des deux discussions de conception menées avec Claude (app desktop) entre
> le 21 et le 29 mai 2026, avant la mise en place de Claude Code. Les transcripts bruts
> sont archivés en local sous `docs/context/transcripts/` (non versionnés).
> Ce document capture le **pourquoi** et le **contexte humain** que le code et les ADR ne
> disent pas. Les décisions d'architecture formelles vivent dans `docs/adr/`.

## Le projet en une phrase

Déploiement et validation d'un **système de captation et de relecture RF
multi-constellations** : enregistrer des signaux radiofréquences bruts, les stocker
proprement dans un data lakehouse, et pouvoir les **rejouer** physiquement sur de vrais
récepteurs du laboratoire pour valider leur fonctionnement.

- **Objectif final (formulé par le responsable)** : *« construire notre propre dataset
  curé (curated dataset) »*. La valeur est dans la **qualité garantie** de chaque capture
  qui entre dans le lac, pas dans la politique de rétention.
- **Objectif personnel de Théo** : faire **la meilleure infrastructure possible** — un
  projet le plus complet et propre possible.
- Le matériel de référence côté labo est le **Safran RF Record and Playback**.

## Les 3 démos / livrables attendus

Trois bancs d'essai « enregistrement → relecture » sur de vrais récepteurs :

| Démo | Bande / fréquence | Bande passante | Récepteur cible | Référent |
|------|-------------------|----------------|-----------------|----------|
| **GNSS** | L1 — **1575.42 MHz** | 2 MHz | Ublox 9FP | — |
| **Iridium** | 1.626 GHz | 2 MHz | récepteur de Wissem | Wissem |
| **Starlink** | centré 1.4 GHz | **25 MHz** (~200 Mo/s) | récepteur d'Ahmad | Ahmad |

> ⚠️ Dans le brief initial la fréquence GNSS L1 a été notée « 1545.42 MHz » — c'est une
> coquille. La valeur correcte (et celle codée dans les presets) est **1575.42 MHz**.

L'ordre de démarrage réel a été ajusté par le responsable : on **ne commence pas par le
GNSS** mais par **Iridium** (données de Wissem disponibles). La fréquence/format/datatype
sont considérés comme de simples **inputs utilisateur** — ce que Théo doit maîtriser et
sur quoi se concentrer, c'est la **structure des données**.

## Les personnes

- **Abdu** — responsable / chef de projet. C'est lui qui donne les orientations (appel du
  2026-05-29 qui a reprioritisé vers la qualité, cf. ADR-004). *Note : l'ADR-004 le
  mentionne par erreur sous « Malek » par endroits — le bon nom est Abdu.*
- **Malek** — tuteur ; a relu et validé la présentation hebdomadaire (a demandé d'ajouter
  un diagramme d'architecture).
- **René** — réunion amont ayant déclenché le recadrage de la mission.
- **Wissem** — collègue ; possède le récepteur Iridium et a fourni des **données de test
  réelles** (« 07 - Raw_data »).
- **Ahmad** — possède le récepteur Starlink.
- **Pierre Galopin** & **Lucien Millet** — prédécesseurs (legacy **NeSIVA**,
  `BitGrabber.ipynb`, août 2025). Leur code utilisait **minio-py** + **s3fs** (≠ notre
  choix boto3, cf. ADR-001). Documentation/données récupérées via SharePoint puis Google
  Drive (« AeroLake Legacy - Project »).
- Équipe : **NESIVA** (contexte du weekly meeting).

## Décisions et orientations clés (le « pourquoi »)

- **Stockage : MinIO validé**, avec une **brique de catalogage** à ajouter par-dessus pour
  structurer (le challenge du tuteur « pourquoi MinIO ? » a été tranché en faveur de MinIO).
- **Priorité qualité > temps réel** (appel Abdu, 2026-05-29 — formalisé en ADR-004) :
  - Pas de besoin temps réel immédiat. Le consommateur doit respecter la **cadence
    temporelle** des données, pas la latence absolue (ex. donné : un processus lent type
    variation journalière de température).
  - La vraie question est le **débit** (« le lac tient-il la cadence, oui/non ? »), pas la
    latence — le réseau est en WiFi, hors de notre contrôle.
  - **Archivage manuel**, pas de politique de rétention automatique. *MAIS* Théo a choisi
    de **garder quand même la brique d'archivage** dans le code (plus complet, retirable au
    besoin).
- **Deux bases de code attendues** : une en **Python** et une en **GNU Radio**. Le
  flowgraph GNU Radio demandé est de type **« Record / Playback »** (capture + relecture).
  GNU Radio **n'est pas encore installé** sur la machine.
- **Style de code** : Théo veut des **commentaires abondants et pédagogiques** dans le code
  pour bien assimiler (demandé à plusieurs reprises). C'est cohérent avec la densité de
  commentaires déjà présente dans `src/`.
- Le bug du générateur synthétique **full-scale (clipping)** a été détecté *par* la couche
  qualité, puis corrigé (`tone_amplitude` par défaut → -20 dBFS). Cf. ADR-004.

## Roadmap voulue par Théo (ordre explicite)

1. **Finir l'infrastructure** proprement (priorité actuelle).
2. Puis une **IHM / interface de visualisation** paramétrable et esthétique.
3. **À la fin seulement**, tester avec de **vraies données** (SDR + données Wissem).

### IHM envisagée (pas encore commencée)

Visualiseur de captures paramétrable, pensé pour que **n'importe quel utilisateur**
puisse s'en servir facilement :
- Vues sélectionnables : **FFT**, **spectrogramme**, **constellation**, etc.
- Affichage du **rapport qualité**.
- Esthétique soignée, **thème aérospatial**.
- Réserve sur une liste déroulante de captures si un jour il y en a ~10 000 (penser à un
  autre mode de sélection à grande échelle).
- Un **serveur X** est disponible côté Théo. L'esthétique vient **après** une infra solide.

## Matériel & environnement disponibles

- **SDR** : **BladeRF** et **RTL-SDR** disponibles. Antennes disponibles.
- **Données réelles** : jeu de données de Wissem (Iridium).
- **MinIO distant** : accès reçu → **https://fast.etsmtl.ca/** (ÉTS Montréal). Théo veut
  d'abord finir en local avant de basculer dessus.
- **Poste de dev** : Windows 11 + **WSL2 Ubuntu** (hôte `FRAISE68`), Docker, `uv`. Dépôt
  **GitHub privé**, tuteur invité.

## Lien avec l'état du code

Au moment de cette synthèse (fin Sprint 2) : infrastructure batch + couche qualité en
place et testée sur données synthétiques. Restent à venir, par ordre : finalisation infra,
IHM de visualisation, flowgraph GNU Radio Record/Playback, streaming (multipart + HTTP
Range + ZeroMQ, différé par ADR-004), puis intégration SDR réelle (BladeRF / RTL-SDR) et
bascule sur le MinIO distant ÉTS.
