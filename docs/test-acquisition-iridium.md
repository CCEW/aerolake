# Test d'acquisition Iridium (RFSoC) — matériel & protocole

> Plan de test pour la captation Iridium avec le **RFSoC**, **intégrée à
> AeroLake** (ingestion / qualité / visualisation).
>
> **Note de cadrage — liberté de conception.** Le *Technical Report* de Lucien
> Millet (§2-1.2 & 4-3.3) est utilisé ici uniquement comme **référence pour
> comprendre la démarche** et fournir une **baseline éprouvée** (paramètres,
> matériel). **Je reste libre de mes choix** : fréquence, canal, sample rate,
> matériel et protocole peuvent être adaptés à mes objectifs — les valeurs
> ci-dessous sont un point de départ, pas une contrainte.

## 1. Objectif

Capter le signal **Iridium** sur un canal simplex fixe, l'enregistrer en IQ,
puis le **faire entrer dans le lakehouse AeroLake** (ingestion → MinIO →
validation qualité → visualisation), pour valider la chaîne sur de la **vraie
donnée RF**.

## 2. Rappel — structure du signal Iridium

- Constellation Iridium NEXT : 66 satellites, orbites polaires ~780 km.
- Lien utilisateur : **1616 – 1626.5 MHz**
  - Duplex 1616–1626 MHz (240 canaux)
  - **Simplex 1626–1626.5 MHz** (12 canaux, *broadcast* — c'est ce qu'on vise)
- Canaux continus utiles (transmis en permanence, idéaux pour l'analyse) :
  - Ch 3 — Quaternary Messaging : **1626.104 MHz**
  - **Ch 7 — Ring Alert : 1626.271 MHz** ← canal retenu par Lucien
  - Ch 11 — Primary Messaging : **1626.438 MHz**

## 3. Paramètres d'acquisition — baseline (d'après Lucien, à ajuster librement)

| Paramètre | Valeur | Pourquoi |
|---|---|---|
| Fréquence centrale | **1626.271 MHz** (Ch 7 Ring Alert) | canal simplex stable et prévisible |
| Sample rate | **400 kHz** | couvre le canal Iridium (31,5 kHz) + marge Doppler/offset |
| Bande passante | **= 400 kHz** | filtrage SDR calé sur la fenêtre → anti-aliasing |
| Réf. horloge | **10 MHz externe** | l'horloge interne RFSoC dérive ~20 kHz → Doppler inexploitable |
| Warm-up | **≥ 15 min** | stabilisation thermique (résiduel ~100 Hz même après) |
| Durée d'enregistrement | à définir (ex. 10–30 min) | assez long pour plusieurs passages satellites |

> ⚠️ **Le point le plus critique du rapport** : sans **référence 10 MHz
> externe**, la dérive du RFSoC rend l'analyse Doppler fausse. Lucien a obtenu
> ~50 Hz d'erreur Doppler avec un générateur 10 MHz ; le plan était un OCXO
> **CTI OSC5A2B02**.

## 4. Liste de matériel (Bill of Materials)

| # | Élément | Détail / modèle (réf. labo) | Indispensable ? |
|---|---|---|---|
| 1 | **SDR — RFSoC** | carte RFSoC + logiciel d'acquisition de **Mohamed Same** | ✅ |
| 2 | **Antenne Iridium** | active L-band — **Iridium-AT1621-12** (+ embase magnétique *Caan 33-27210-00-5000*) | ✅ |
| 3 | **Référence 10 MHz externe** | générateur de signal 10 MHz **ou** OCXO **CTI OSC5A2B02** | ✅ (qualité Doppler) |
| 4 | **Câbles coaxiaux** | SMA, faible perte (antenne → SDR ; réf. 10 MHz → SDR) | ✅ |
| 5 | **Ordinateur hôte** | PC du labo (acquisition RFSoC) ; Raspberry Pi 5 utilisé en dynamique | ✅ |
| 6 | **Accès toit + support antenne** | vue ciel dégagée ; supports 3D imprimés (Lucien) | ✅ |
| 7 | **Oscilloscope** | vérifier le 10 MHz (comparer générateur vs OCXO) | ⭐ recommandé |
| 8 | LNA / atténuateurs | si niveau trop faible / trop fort (à évaluer) | ⚪ selon besoin |
| 9 | (Dynamique) **BladeRF 2.0** + antenne + VN100 (IMU) + GPS ublox | alternative mobile (méthode Wissem, large bande 10 MS/s @ 1622 MHz) | ⚪ autre régime |

## 5. Protocole de test (étapes)

1. **Installation** : monter l'antenne Iridium sur le toit (ciel dégagé), relier
   antenne → (LNA ?) → entrée RX du RFSoC en coaxial.
2. **Horloge** : connecter la **référence 10 MHz externe** à l'entrée ref du
   RFSoC. (Vérifier le signal à l'oscilloscope.)
3. **Mise sous tension + warm-up** : allumer et **attendre ≥ 15 min**.
4. **Configurer l'acquisition** (logiciel RFSoC de Mohamed Same) :
   `center = 1626.271 MHz`, `sample_rate = 400 kHz`, `bandwidth = 400 kHz`.
5. **Enregistrer** l'IQ pendant la durée choisie (ex. 10–30 min).
6. **Récupérer le fichier IQ** produit par le logiciel d'acquisition.
7. **Ingestion AeroLake** (voir §6) → MinIO → validation qualité → visualisation.
8. **Post-traitement** (optionnel, hors AeroLake) : GR-Iridium Toolkit / outils
   Doppler de Lucien pour SNR & dérive Doppler.

## 6. Intégration AeroLake (là où NOTRE travail entre)

Le RFSoC capte avec **son propre logiciel** (pas notre GNU Radio). AeroLake
prend le relais **dès que le fichier IQ existe** :

```bash
# 1. Ingestion : fichier IQ -> SigMF -> MinIO (multipart) avec tags
uv run aerolake-ingest <fichier.iq> \
    --signal-type iridium --sample-rate 400e3 --center-freq 1626.271e6 \
    --hardware rfsoc --datatype <cf32|cs16|cu8>     # selon la sortie du logiciel

# 2. Validation qualité + promotion du tag
uv run aerolake-validate --prefix iridium/ --expected-duration <durée_s>

# 3. Visualisation du spectre/spectrogramme/constellation
uv run --group gui aerolake-gui
```

> ❓ **À confirmer** : le **format de sortie** du logiciel d'acquisition RFSoC
> (cf32 ? cs16 ? cu8 ?) — ça détermine le `--datatype` de l'ingestion. Notre
> ingest convertit tout en cf32 normalisé.

## 7. Questions à clarifier avant le test

1. **Régime** : statique rooftop (RFSoC, bande étroite Ch 7 @ 400 kHz, ce doc)
   ou dynamique (BladeRF, large bande 10 MS/s @ 1622 MHz) ?
2. **Format de sortie** du logiciel d'acquisition RFSoC (pour `--datatype`).
3. **Référence 10 MHz** disponible (générateur ou OCXO CTI) ?
4. **Antenne** Iridium-AT1621-12 disponible + accès toit ?
5. Qui pilote le logiciel d'acquisition RFSoC (Mohamed Same) — dispo le jour J ?

## Références

- *Technical Report* — Lucien Millet, §2-1 (Data Acquisition), §2-1.2.1 (RFSoC
  wrapper / horloge), §4-3.3 (Iridium Analysis).
- Attributs HDF5 Wissem (`Test Setup Materials`, `Recording Materials`).
