# AeroLake — le pitch : pourquoi cette architecture ?

Document de vulgarisation. Il explique **le problème** qu'on résout et **pourquoi**
on a choisi ces trois briques techniques. À lire avant le code ; il ne suppose
aucune connaissance préalable de SigMF, MinIO ou des data lakehouses.

---

## 1. Le problème

Un laboratoire comme le LASSENA capte énormément de **signaux radiofréquences
(RF)** : GNSS (GPS), Iridium, Starlink… Chaque enregistrement, c'est un flot
d'**échantillons IQ** — des millions de nombres complexes par seconde. Une
minute de capture à 2 MHz, c'est déjà ~1 Go.

Très vite, sans organisation, c'est le chaos :

- **Des fichiers binaires muets.** `capture_03_final_v2.bin` : quelle fréquence ?
  quel taux d'échantillonnage ? quel récepteur ? quand ? où ? Personne ne sait.
- **Des formats maison.** Chacun invente sa convention ; six mois plus tard plus
  personne ne sait relire les captures d'un collègue parti.
- **Pas de recherche.** « Donne-moi toutes les captures GPS L1 validées prises
  sur le toit » devient une fouille manuelle de dossiers.
- **Le volume.** On ne peut pas charger un fichier de plusieurs Go en RAM juste
  pour en relire 10 secondes.

**L'objectif d'AeroLake** : que n'importe quel membre du labo puisse
**retrouver, relire et rejouer** n'importe quelle capture, grâce à des
**métadonnées standardisées**. Capturer → stocker → indexer → rejouer, sans
chaos.

Le pipeline, en une ligne :

```
Producer (capture → SigMF)  →  MinIO (lakehouse)  →  Consumer (extraction → ZeroMQ)
```

---

## 2. Le triptyque technique

Trois choix structurants. Chacun répond à un morceau précis du problème.

### 🅰 SigMF — pour standardiser les métadonnées

[SigMF](https://github.com/sigmf/SigMF) (*Signal Metadata Format*) est un
**standard ouvert** pour décrire un enregistrement RF. Une capture = deux
fichiers :

- `…​.sigmf-data` : les octets bruts des échantillons IQ.
- `…​.sigmf-meta` : un JSON lisible qui décrit tout — fréquence centrale, taux
  d'échantillonnage, type de données, date, position, antenne, annotations…

**Pourquoi SigMF plutôt qu'un format maison ?**

- **Auto-descriptif** : la capture porte sa propre notice. Plus de fichier muet.
- **Interopérable** : GNU Radio, les outils de la communauté SDR, et n'importe
  quel labo lisent SigMF nativement. On ne s'enferme pas dans notre tambouille.
- **Pérenne** : un standard documenté survit au départ de celui qui l'a écrit.
- **Validable** : on vérifie la conformité d'une capture *avant* de la stocker —
  une erreur de structure est attrapée tout de suite, pas six mois plus tard.

> C'est le sens du *« piège GPSD »* (voir ADR-016) : même la position GPS, on la
> traduit dans le champ standard `core:geolocation` de SigMF, au lieu de
> recopier le format brut du démon GPS.

### 🅱 MinIO — pour le stockage objet

[MinIO](https://min.io/) est un **stockage objet compatible S3**, open-source,
qu'on fait tourner **en local** (ou sur un serveur du labo, ici
`fast.etsmtl.ca`).

**Pourquoi MinIO ?**

- **Compatible S3** : on parle le même langage qu'Amazon S3 — l'API standard de
  l'industrie. Le code marche pareil en local et dans le cloud (un simple
  changement d'URL). On n'est lié à aucun fournisseur.
- **Scalable et performant** : conçu pour de gros volumes binaires, exactement
  notre cas (des Go d'IQ).
- **Métadonnées et tags natifs** : chaque objet porte des en-têtes
  (`x-amz-meta-*`) et des **tags** indexables, qu'on peut lire *sans télécharger
  le fichier*. C'est la clé de la recherche rapide.
- **Open-source et local** : pas de coût cloud, données maîtrisées, idéal pour un
  labo.

### 🅲 Data Lakehouse — pour indexer et requêter intelligemment

Petite mise au point de vocabulaire, parce que c'est tout l'intérêt :

| | Description | Limite |
|---|---|---|
| **Data Lake** | On déverse tout en vrac : « le lac de données ». Stockage brut, pas cher, flexible. | Sans index, retrouver quelque chose = fouiller à la main. |
| **Data Warehouse** | Données nettoyées, structurées, requêtables en SQL. | Rigide, coûteux, mal adapté au binaire brut. |
| **Data Lakehouse** | **Le meilleur des deux** : le stockage brut d'un lac **+** une couche de catalogage intelligente pour requêter (idéalement en SQL). | — |

**Pourquoi viser un Lakehouse et pas juste un Lake ?**
Parce qu'on veut les deux : garder les octets bruts (SigMF sur MinIO, pas cher et
flexible) **ET** pouvoir poser des questions par-dessus — « toutes les captures
Iridium validées de juin », « celles prises en mouvement » — sans rapatrier un
seul octet d'échantillon.

---

## 3. Où en est AeroLake, honnêtement

C'est le point à **ne pas survendre** en présentation. Aujourd'hui, AeroLake est
un **Data Lake avec une couche de catalogage**, pas (encore) un Lakehouse SQL
complet :

- ✅ **Stockage brut** : SigMF sur MinIO, avec une convention de clés claire
  (`{type}/{date}/{session}/…`).
- ✅ **Couche de catalogage** : les **tags S3** (`signal-type`, `quality`,
  `hardware`, `recorder`…) et les métadonnées d'objet rendent les captures
  **filtrables sans télécharger** (commande `aerolake-list`, voir ADR-003). On
  promeut aussi un tag qualité `raw → validated/rejected`.
- ✅ **Regroupement** : les *Collections* SigMF lient plusieurs captures d'une
  même campagne.
- ✅ **Extraction ciblée** : lecture partielle par *HTTP Range* (relire t=200s
  sans charger tout le fichier) puis diffusion sur un bus **ZeroMQ Pub/Sub**.
- 🔜 **Couche SQL / analytique** (Parquet, Apache Iceberg) : c'est *la* brique
  qui ferait passer de « Lake catalogué » à « vrai Lakehouse requêtable en
  SQL ». Elle est **identifiée comme évolution future** (elle avait été explorée
  puis mise de côté, ADR-013, pour recentrer sur le pipeline RX du mandat).

**Formulation honnête pour le pitch :** « AeroLake pose les fondations d'un
lakehouse RF — stockage objet standardisé + catalogage par tags et métadonnées.
La couche de requêtage SQL est la prochaine étape naturelle. »

---

## 4. Le fil rouge

Tout tient ensemble grâce à **une idée** : la capture n'est jamais un fichier
muet. Elle porte ses métadonnées (SigMF), exposées de façon indexable (tags
MinIO), donc retrouvable et rejouable par tout le labo (extraction ciblée +
ZeroMQ). SigMF répond au *quoi*, MinIO au *où*, le lakehouse au *comment on s'y
retrouve*.

---

### Pour aller plus loin

- Les décisions de conception détaillées : `docs/adr/` (chaque choix a son ADR).
- Le recadrage sur le mandat (priorité au pipeline RX) : ADR-013.
- Le contexte projet et l'historique : `docs/context/`.
