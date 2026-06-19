# Phase 2 — La grande cuisine de Marc

> Phase 2 (l'ingestion asynchrone) racontée comme une histoire.
> Marc le cuisinier — le worker Celery — prend le ticket déposé dans le
> passe-plat en fin de Phase 1, et part visiter ses **9 stations**.

Suite de [`PHASE_1_HOTEL_FR.md`](PHASE_1_HOTEL_FR.md).
Document pour ma compréhension personnelle.

---

## Le réveil de Marc

14h27. Marc surveille le passe-plat depuis la cuisine. Un papier tombe :

```
TICKET #b0e8f24c
Commande : ingestion_pipeline
Args     : ("8c1a6add-...", "b0e8f24c-...")
```

Marc l'attache à son tablier et entre dans la cuisine.

La cuisine n'est pas comme la salle d'accueil de Pierre. **Ici tout est en ligne
droite, séquentiel** : chaque tâche doit être terminée avant la suivante. Marc
travaille seul, à son rythme, **sans pression de timing client**, mais avec une
**discipline militaire**.

Au mur, son **plan de travail** : 9 étapes numérotées. Chaque étape utilise une
**station** différente, avec son spécialiste.

```
┌──────────────────────────────────────────────────────────────────┐
│                    LA CUISINE DE MARC                            │
│                                                                  │
│  [1] Bureau d'identification ─── ffprobe (contrôleur métadata)   │
│         ↓                                                        │
│  [2] Atelier transcodage ─────── FFmpeg (couteau suisse)         │
│         ↓                                                        │
│  [3] Extraction audio ────────── FFmpeg encore                   │
│         ↓                                                        │
│  [4] Studio d'écoute ─────────── mlx-whisper (sténographe)       │
│         ↓                                                        │
│  [5] L'œil détecteur ─────────── PySceneDetect (veilleur)        │
│         ↓     + OpenCV (lunettes binoculaires)                   │
│         ↓     + NumPy (calculateur)                              │
│         ↓                                                        │
│  [6] Studio photo ────────────── PIL (loupe-photomètre)          │
│         ↓                                                        │
│  [7] Scanner sémantique ──────── open-clip + PyTorch             │
│         ↓                          (critique d'art)              │
│         ↓                                                        │
│  [8] Bureau du narrateur ─────── LLaMA3 via Ollama               │
│         ↓     + httpx (téléphone)                                │
│         ↓                                                        │
│  [9] Service archivage final ─── SQLAlchemy + psycopg2           │
│                                    (Marie en mode sync)          │
│                                                                  │
│  En permanence : Tableau d'affichage Redis Pub/Sub               │
│    → "Étape X% — Whisper a transcrit 17 mots"                    │
│    → relayé au browser par websockets                            │
└──────────────────────────────────────────────────────────────────┘
```

Pendant TOUT son travail, Marc griffonne sur un **tableau d'affichage**
(Redis Pub/Sub). Le radio-opérateur (websockets) lit ce tableau et **relaie**
au client dans son navigateur, en temps réel. C'est ce qui fait avancer la
barre de progression.

C'est parti.

---

## Station 1 — ffprobe : le contrôleur des bagages

### Histoire
Avant de commencer, Marc doit savoir ce qu'il a entre les mains. Il pose le
fichier MP4 sur le **bureau d'identification** et appelle **ffprobe**.

ffprobe ouvre le carton (sans déballer), inspecte les étiquettes, et lui dit :
- *« Durée : 17 secondes. »*
- *« Résolution : 1920×1080. »*
- *« Codec : h264. »*
- *« Bildrate : 24 fps. »*

ffprobe **ne touche pas au contenu** du fichier. Il lit juste les **métadonnées**
dans l'en-tête.

Marc note tout sur un papier et écrit sur le tableau : *« Étape 3 % —
Metadaten gelesen »*.

### Sa famille
- L'exécutable `ffprobe` (installé sur la machine, **hors Python**)
- Les flags `-show_format -show_streams -of json`
- Le retour : du JSON, parsé par Python

### De qui il dépend
- Le binaire FFmpeg installé sur la machine (via Homebrew sur macOS)

### Qui l'utilise
- Marc l'appelle via `subprocess.run([FFPROBE_BIN, "-v", "quiet", ...])` à
  [`ingest.py:73-79`](../../backend/workers/ingest.py#L73-L79)

### Mot-clé
**LECTEUR DE MÉTADONNÉES**

---

## Station 2 — FFmpeg : le couteau suisse de la cuisine

### Histoire
Marc passe à l'**atelier transcodage**. FFmpeg est son **couteau suisse
universel** — il va l'utiliser plusieurs fois dans la journée :

1. **Créer un proxy** (version basse résolution pour le navigateur)
2. **Extraire l'audio** (pour Whisper plus tard)
3. **Extraire des images** (vignettes et frames pour CLIP)
4. Plus tard en Phase 4 : **export final** avec transitions

FFmpeg sait tout faire : lire, transcoder, redimensionner, mixer, encoder.
C'est l'**outil le plus polyvalent** de toute la cuisine.

**Pour le proxy** : Marc lui dit *« redimensionne à 960 px de large, encode
en H.264 rapide, audio AAC 128 kbps »*. FFmpeg sort un fichier de 0.68 MB qui
passe dans le navigateur sans charger 4K à chaque clic.

Marc écrit : *« Étape 4 % — Proxy bereit »*.

### Sa famille
- L'exécutable `ffmpeg`
- Les filtres : `-vf scale=960:-2`, `-vf scale=64:64`
- Les codecs : `libx264`, `aac`, `pcm_s16le`
- Les presets : `-preset fast -crf 26`
- L'extraction de frame : `-ss N -frames:v 1`
- Plus tard les transitions : `xfade=dissolve`, `xfade=fadeblack`, etc.

### ⚠️ Question : « FFmpeg est-il une bibliothèque Python ? »

**NON.** FFmpeg est un **programme externe écrit en C**, comme Postgres.
Python l'appelle via `subprocess.run([...])`, c'est-à-dire **comme si tu
l'appelais depuis le terminal**.

Il existe des wrappers Python comme `ffmpeg-python` ou `moviepy` qui cachent
le subprocess, mais CinAssist a choisi de **l'appeler directement** pour avoir
le contrôle total des flags. Plus transparent, moins de magie.

### De qui il dépend
- Le binaire FFmpeg installé sur la machine

### Qui l'utilise
- Marc via `subprocess.run([FFMPEG_BIN, ...])` à plusieurs endroits de
  [`ingest.py`](../../backend/workers/ingest.py)

---

### 🔬 Deep dive : combien d'images sont extraites au total ?

Excellente question. FFmpeg ne se contente pas de faire le proxy — il est
appelé **plusieurs fois** dans la pipeline pour extraire des images uniques
(« frame grabs »).

**Pour CHAQUE scène détectée**, voici les extractions FFmpeg :

| # | Quand | Combien | Taille | Pour quoi |
|:-:|---|:-:|---|---|
| 1 | Pendant Station 5 (PySceneDetect) | **1 par scène** | scale=320:-1 (320px de large, hauteur auto) | Vignette UI (`thumbnail.jpg`) |
| 2-4 | Pendant Station 6 (PIL) | **3 par scène** | 64×64 (au 50 %) + 32×32 (au 25 %) + 32×32 (au 75 %) | Analyse pixel : luminosité, contraste, mouvement, netteté |
| 5 | Pendant Station 7 (CLIP) | **1 par scène** | résolution originale (puis CLIP downscale à 224×224 lui-même) | Embedding sémantique |

**Total = 5 frames extraites par scène.**

Pour Sintel C (1 scène) → **5 frames**.
Pour un clip avec 10 scènes → **50 frames**.

### Comment se passe l'extraction technique d'UNE frame ?

C'est la commande FFmpeg utilisée à [`ingest.py:382-383`](../../backend/workers/ingest.py#L382-L383) et ailleurs :

```bash
ffmpeg -y -ss 8.5 -i video.mp4 -frames:v 1 -q:v 3 -vf "scale=64:64" out.jpg
```

Décortiquage flag par flag :

| Flag | Rôle |
|---|---|
| `-y` | Écrase le fichier de sortie sans demander (oui par défaut) |
| `-ss 8.5` | **Seek** à 8.5 secondes **AVANT** `-i` (= input seek, rapide mais saute au keyframe le plus proche) |
| `-i video.mp4` | Fichier d'entrée |
| `-frames:v 1` | Extrait **exactement 1** frame vidéo, puis arrête |
| `-q:v 3` | Qualité JPEG (1 = meilleure, 31 = pire ; 2-3 = très bon) |
| `-vf "scale=64:64"` | Filtre vidéo : redimensionne à 64×64 px |
| `out.jpg` | Fichier de sortie (le format JPEG est déduit de l'extension) |

### Pourquoi `-ss AVANT -i` et pas après ?

C'est un détail technique mais **crucial** pour la performance :

- `-ss N -i fichier` (**avant**) : FFmpeg **saute** directement au timestamp N dans le fichier sans décoder ce qui précède. **Très rapide** (~50 ms par frame), mais imprécis (saute au keyframe le plus proche, peut être ±1 seconde de décalage).
- `-i fichier -ss N` (**après**) : FFmpeg **décode tout le début** du fichier jusqu'au timestamp N. **Précis à la milliseconde**, mais lent (peut prendre 5-10 secondes sur une longue vidéo).

CinAssist utilise **`-ss avant -i`** parce que :
1. On extrait beaucoup de frames (5 par scène × N scènes)
2. Une précision à ±1 seconde est largement suffisante pour l'analyse visuelle
3. La rapidité l'emporte sur la précision absolue

Sur un clip de 17 s avec 1 scène, les 5 extractions FFmpeg prennent environ **300 ms** au total.

### Mot-clé
**LE COUTEAU SUISSE VIDÉO**

---

## Station 3 — Extraction audio (FFmpeg encore)

### Histoire
Avant de transcrire, Marc doit **séparer la piste audio** de la vidéo. Il
retourne au couteau suisse FFmpeg avec une instruction très précise :

> *« Extrais juste l'audio, en format WAV 16 kHz Mono. »*

**Pourquoi 16 kHz et mono ?** Parce que **Whisper attend exactement ça**. Pas
plus, pas moins.
- 16 kHz : suffisant pour la voix humaine (la parole va jusqu'à ~8 kHz, donc
  16 kHz double avec marge selon le **théorème de Nyquist-Shannon**)
- Mono : la voix n'est pas stéréo, inutile de doubler

FFmpeg pose le fichier `audio.wav` (530 KB pour Sintel C) dans le coin
**Temp** de la cuisine.

Marc écrit : *« Étape 10 % — Audio extrahiert »*.

### Sa famille (de cette opération spécifique)
- Le flag `-vn` (no video)
- Le codec `-acodec pcm_s16le` (PCM 16-bit non compressé)
- La fréquence `-ar 16000`
- Mono `-ac 1`

### De qui elle dépend
- FFmpeg (déjà introduit)

### Qui l'utilise
- La fonction `schritt_audio_extrahieren()` à
  [`ingest.py:109-138`](../../backend/workers/ingest.py#L109-L138)

### Mot-clé
**EXTRACTEUR AUDIO POUR WHISPER**

---

## Station 4 — mlx-whisper : la sténographe

### Histoire
Marc prend le WAV et l'apporte au **studio d'écoute**. Là, **Whisper** est
assise : une **sténographe extraordinaire** qui peut écouter n'importe quelle
langue et taper la transcription mot par mot, avec le **timestamp précis de
chaque mot**.

C'est l'élève préférée d'OpenAI. Mais ici, ce n'est pas la Whisper standard —
c'est **mlx-whisper**, la version qui sait utiliser le **Neural Engine du
Mac** (la puce dédiée IA des Apple Silicon M1/M2/M3). Elle travaille **3× plus
vite** que sa cousine PyTorch normale.

Whisper se met au travail. Pour le clip Sintel 17 s, elle écoute, et écrit :

```
Segment 1 [00:01.40 → 00:02.80] : "A dangerous quest..."
   Word: "A"         start=1.40s  end=1.96s
   Word: "dangerous" start=1.96s  end=2.34s
   Word: "quest"     start=2.34s  end=2.80s
   ...
Segment 2 [00:05.20 → 00:11.60] : "I've been alone for as long as I can remember..."
   ...
```

**2 segments, 17 mots, timestamps à la milliseconde.**

Marc note ça et écrit : *« Étape 30 % — Transkription fertig (17 Wörter) »*.

### Sa famille
- `mlx_whisper.transcribe(audio_path, path_or_hf_repo="mlx-community/whisper-large-v3-turbo", language="de", word_timestamps=True)`
- Le modèle : 800 MB de poids, téléchargés au premier usage
- La structure de retour : `result["segments"]` avec `start`, `end`, `text`, `words` (chaque mot avec son timestamp)

### De qui il dépend
- **MLX** (le framework ML d'Apple, équivalent local de PyTorch optimisé pour
  Apple Silicon)
- Le **Neural Engine** d'Apple Silicon (matériel)

### Qui l'utilise
- La fonction `schritt_transkription()` à
  [`ingest.py:145-191`](../../backend/workers/ingest.py#L145-L191)

### Mot-clé
**SPEECH-TO-TEXT AVEC TIMESTAMPS MOT-PAR-MOT**

---

## Station 5 — PySceneDetect : le veilleur visuel

### Histoire
Marc revient au **fichier vidéo brut** (le MP4 original, pas le proxy). Il va
à **l'œil détecteur** : PySceneDetect.

PySceneDetect est un **veilleur** qui regarde la vidéo image par image, et dès
qu'il voit que **l'image change radicalement** (= nouvelle scène), il sonne
la cloche :

> *« Là, coupe ! Nouvelle scène commence. »*

Comment fait-il pour décider que ça a *« changé radicalement »* ? L'algorithme
`ContentDetector` :
1. Convertit chaque frame du RGB vers **HSV** (Hue-Saturation-Value).
2. Calcule la **différence moyenne** entre la frame courante et la précédente.
3. Si la différence dépasse **27** (le threshold), c'est une coupure.

**Pourquoi HSV et pas RGB ?** Parce que HSV sépare la **couleur** (H) de la
**luminosité** (V). Si la lumière vacille pendant un plan (un nuage qui passe
devant le soleil), en RGB tout change brutalement — faux positif. En HSV, le
Hue (teinte) reste stable. C'est **plus robuste**.

Pour Sintel C, le veilleur trouve : **1 scène** (les 17 s sont un seul plan
continu).

Marc écrit : *« Étape 50 % — 1 Szene erkannt »*.

### Sa famille
- `open_video(video_pfad)` : ouvre le fichier
- `SceneManager()` : l'orchestrateur
- `ContentDetector(threshold=27.0)` : l'algorithme HSV
- `detect_scenes(video)` : lance l'analyse
- `get_scene_list()` : retourne `[(start_time, end_time), ...]`

### De qui il dépend
- **OpenCV** (pour décoder les frames vidéo, indirect)
- **NumPy** (pour les calculs sur arrays, indirect)

### Qui l'utilise
- La fonction `schritt_szenen_erkennen()` à
  [`ingest.py:198-256`](../../backend/workers/ingest.py#L198-L256)

---

### 🔬 Deep dive — Comment PySceneDetect fonctionne vraiment

#### 1. D'où viennent les frames ?

**Pas du tout de la Station 2.** PySceneDetect **rouvre le fichier MP4
original lui-même** et le décode frame par frame en interne.

Concrètement :
```python
from scenedetect import open_video, SceneManager
from scenedetect.detectors import ContentDetector

video = open_video(video_pfad)               # ouvre le MP4 (via OpenCV)
scene_manager = SceneManager()
scene_manager.add_detector(ContentDetector(threshold=SCENE_THRESHOLD))
scene_manager.detect_scenes(video)            # ← itère sur TOUTES les frames
```

PySceneDetect appelle **OpenCV** (`cv2.VideoCapture`) qui décode chaque frame
du MP4 en mémoire, une par une. Pour Sintel C (17 s × 24 fps = **408 frames**),
PySceneDetect va donc inspecter 408 frames.

**Pourquoi PySceneDetect rouvre le fichier au lieu d'utiliser les images
déjà extraites par FFmpeg ?**

Parce qu'il a besoin **d'ABSOLUMENT TOUTES les frames consécutives** pour
comparer chaque frame avec la précédente. FFmpeg n'a extrait que 5 frames
isolées par scène — c'est suffisant pour analyser le contenu d'une scène une
fois qu'elle est connue, mais **pas pour la détecter**.

Donc Station 2 et Station 5 sont **indépendantes** : elles lisent toutes
deux le même MP4, mais avec des objectifs différents.

#### 2. L'algorithme étape par étape

PySceneDetect utilise le `ContentDetector`. Voici exactement ce qu'il fait
pour chaque paire de frames consécutives (frame `t` et frame `t-1`) :

**Étape A — Conversion RGB → HSV**

Chaque frame est convertie du format **RGB** (Rouge-Vert-Bleu) vers **HSV**
(Teinte-Saturation-Valeur). En OpenCV, les composantes sont stockées comme
des entiers 8-bit :

| Composante | Plage en OpenCV | Signification |
|---|---|---|
| **H** (Hue / Teinte) | 0 à 179 | La couleur dominante (rouge=0, vert=60, bleu=120…) |
| **S** (Saturation) | 0 à 255 | L'intensité de la couleur (0 = gris, 255 = vif) |
| **V** (Value / Luminosité) | 0 à 255 | La brillance (0 = noir, 255 = blanc) |

**Étape B — Différence pixel par pixel, séparément par canal**

Pour chaque pixel `(x, y)` de l'image :

```
ΔH(x,y) = |H_t(x,y) − H_{t-1}(x,y)|
ΔS(x,y) = |S_t(x,y) − S_{t-1}(x,y)|
ΔV(x,y) = |V_t(x,y) − V_{t-1}(x,y)|
```

**Étape C — Moyenne sur tous les pixels**

```
ΔH_moyen = (1/N) Σ ΔH(x,y)    où N = nombre de pixels
ΔS_moyen = (1/N) Σ ΔS(x,y)
ΔV_moyen = (1/N) Σ ΔV(x,y)
```

**Étape D — Score combiné (les fameux poids)**

PySceneDetect combine les trois deltas avec des **poids configurables**
(par défaut tous à 1.0) :

```
score = (w_H · ΔH_moyen + w_S · ΔS_moyen + w_V · ΔV_moyen + w_edges · Δedges)
        ─────────────────────────────────────────────────────────────────────
                          w_H + w_S + w_V + w_edges
```

Avec les poids par défaut de PySceneDetect 0.6.4 :
- `delta_hue = 1.0`
- `delta_sat = 1.0`
- `delta_lum = 1.0` (le V du HSV est appelé "lum" / luminance dans PySD)
- `delta_edges = 0.0` (détection de contours optionnelle, désactivée)

Donc la formule effective dans CinAssist est :

```
score = (1.0·ΔH + 1.0·ΔS + 1.0·ΔV) / 3
```

C'est simplement la **moyenne des trois différences moyennes**.

**Étape E — Décision**

```
if score > threshold (= 27.0):
    → marque une coupure entre frame t et frame t-1
```

#### 3. Pourquoi le seuil 27 ? Est-ce normé ? Pourquoi pas 0–1 ?

**Le seuil 27.0 est le DÉFAUT empirique** des auteurs de PySceneDetect,
validé sur un large corpus de contenus (films, publicités, documentaires,
youtube, etc.). Il n'est **pas normé** entre 0 et 1.

**Quelle est sa plage théorique ?**

Le score est une **moyenne de différences absolues sur des canaux 8-bit**.
Chaque canal peut varier de 0 à 255 (sauf H qui va de 0 à 179 en OpenCV).
Donc en théorie, le score maximum possible serait :

```
score_max ≈ (179 + 255 + 255) / 3 ≈ 229
```

Mais dans la pratique, sur du contenu réel :
- Frame quasi identique (intérieur d'un plan) : score 0–5
- Petit changement (mouvement de caméra) : score 5–15
- Changement notable (zoom, transition lente) : score 15–25
- **Coupure dure (cut sec)** : score 30–80

Le seuil 27 sépare donc la **zone de mouvement intra-plan** (< 25) de la
**zone de coupure** (> 30) avec une petite marge.

**Pourquoi pas normaliser à [0, 1] ?**

Deux raisons :

1. **Historique** : la littérature sur la Shot Boundary Detection
   (Vasconcelos & Lippman 1999, Lienhart 2001) a toujours utilisé des
   échelles arbitraires basées sur les pixels 8-bit. PySceneDetect respecte
   cette convention.

2. **Pratique** : la valeur "27" reste **interprétable** quand on regarde
   les stats de l'algorithme. Si tu fais `scenedetect --stats stats.csv
   detect-content`, tu obtiens un CSV avec le score frame par frame, et tu
   peux **visualiser la courbe** pour ajuster le seuil. Avec des valeurs
   0–1, ça serait plus abstrait.

**Comment ajuster pour ta vidéo ?**

| Type de contenu | Seuil recommandé |
|---|:-:|
| Vlog statique, talking head | 25–30 |
| **Standard (films, mix)** | **27** (défaut) |
| Documentaire lent | 30–35 |
| Action / clip music rapide | 15–25 |

Pour la défense devant ton prof, tu peux dire :
> *« Le seuil 27 est le défaut empirique des auteurs de PySceneDetect,
>   validé sur un large corpus. Pour mon démonstrateur, je l'ai gardé tel
>   quel parce que les contenus testés (clips cinématographiques) tombent
>   dans la catégorie standard. Une adaptation par type de contenu serait
>   une amélioration future, par exemple via un calcul adaptatif du seuil
>   sur la moyenne des scores. »*

#### 4. Pourquoi HSV au lieu de RGB ?

Voici **le cœur** de la question. HSV est **plus robuste** que RGB pour la
détection de coupures de plan parce qu'il **sépare la couleur de la
luminosité**.

**Scénario 1 — Un nuage passe devant le soleil pendant un plan continu**

| Espace | Ce qui se passe | Conséquence |
|---|---|---|
| RGB | R, G, B descendent tous ensemble | Grosse différence → **fausse coupure détectée** |
| HSV | V (luminosité) descend, mais H (teinte) reste stable | Score modéré, threshold non franchi → **plan correctement préservé** |

**Scénario 2 — Vraie coupure d'une forêt verte à une plage jaune**

| Espace | Ce qui se passe | Conséquence |
|---|---|---|
| RGB | R, G, B changent radicalement | Grosse différence → coupure détectée ✓ |
| HSV | H change radicalement (vert → jaune), S et V changent aussi | Score élevé → coupure détectée ✓ |

**Scénario 3 — Flash d'appareil photo dans un plan continu**

| Espace | Ce qui se passe | Conséquence |
|---|---|---|
| RGB | R, G, B montent en flèche un seul frame | **Fausse coupure** |
| HSV | V monte, H reste stable | Filtré, pas de coupure ✓ |

**Conclusion** : HSV permet de distinguer ce qui est **un changement
d'éclairage** (intra-plan, ne doit pas déclencher de coupure) de ce qui est
**un changement de contenu** (inter-plan, doit déclencher une coupure).

#### 5. Bonus : la règle des 15 frames minimum

Tu peux le voir dans la signature : `min_scene_len: int = 15`. PySceneDetect
ignore les coupures qui sépareraient deux scènes de moins de **15 frames**
(≈ 0.6 seconde à 24 fps). C'est pour éviter de découper en micro-segments
quand il y a une rafale de flashs ou un transition très rapide.

### Mot-clé
**DÉTECTEUR DE CHANGEMENTS DE PLAN (SHOT BOUNDARY DETECTION)**

---

## Stations annexes — OpenCV + NumPy

### Histoire
Marc ne les utilise **jamais directement**, mais ils sont là, **derrière**
PySceneDetect.

- **OpenCV** = la **paire de lunettes binoculaires** de PySceneDetect. C'est
  lui qui sait **décoder les frames** du fichier MP4. Sans lui, PySceneDetect
  ne pourrait pas voir.
- **NumPy** = le **calculateur de poche** universel pour les opérations sur
  les tableaux de pixels. Utilisé partout en ML/CV, surtout en arrière-plan.

### Pourquoi `opencv-python-headless` et pas `opencv-python` ?

Parce que la version `-headless` est **sans interface graphique** (pas de GTK,
pas de Qt). On est dans un serveur — on n'a pas besoin d'afficher de fenêtres.
C'est plus léger et évite les conflits.

### De qui ils dépendent
- Rien d'externe à Python

### Qui les utilise
- **Indirectement** : PySceneDetect, PIL (un peu), PyTorch, mlx-whisper —
  tout l'écosystème ML

### Mot-clé
**INFRASTRUCTURE ML DE BAS-NIVEAU**

---

## 🎬 Récapitulatif chronologique : qui extrait quoi, quand ?

À ce stade, c'est utile de remettre les choses dans l'ordre, parce que les
**extractions de frames** se font à plusieurs moments différents — pas tous
au même endroit.

### Le piège à éviter

Beaucoup pensent que FFmpeg fait toutes les extractions en début de pipeline.
**FAUX**. Marc revient à FFmpeg **3 fois différentes** dans la pipeline,
chacune avec un objectif distinct, **après** que PySceneDetect ait découpé
les scènes.

### Le diagramme chronologique

```
TEMPS →

Station 1 (ffprobe)
   └─ 0 frame extraite (lit juste les métadonnées)

Station 2 (FFmpeg proxy)
   └─ pas une frame isolée : ré-encode TOUT le fichier en 960p
                              (pour la prévisualisation navigateur)

Station 3 (FFmpeg audio)
   └─ 0 frame vidéo (juste l'audio en WAV)

Station 4 (Whisper)
   └─ 0 frame (lit le WAV, pas la vidéo)

Station 5 (PySceneDetect)
   │
   ├─ Phase A : OpenCV décode les 408 frames du MP4 en mémoire,
   │            les compare entre elles, trouve les coupures
   │            → AUCUNE FRAME N'EST SAUVEGARDÉE SUR DISQUE
   │
   └─ Phase B : POUR CHAQUE scène détectée,
                FFmpeg est appelé → 1 thumbnail.jpg (320 px) sauvegardé
                                    ─── 1 frame par scène ───

Station 6 (PIL)
   └─ POUR CHAQUE scène,
      FFmpeg est appelé 3 fois → 1 frame à 25 % (32×32 jpg)
                               → 1 frame à 50 % (64×64 jpg)
                               → 1 frame à 75 % (32×32 jpg)
                                    ─── 3 frames par scène ───

Station 7 (CLIP)
   └─ POUR CHAQUE scène,
      FFmpeg est appelé → 1 frame au milieu (résolution originale)
                                    ─── 1 frame par scène ───

Station 8 (LLaMA3)
   └─ 0 frame (lit la transcription)

Station 9 (Save)
   └─ 0 frame (écrit en DB)
```

### Le point qui prête à confusion

**PySceneDetect n'utilise PAS FFmpeg pour lire le fichier vidéo.**

Il y a **deux mécanismes différents** pour lire les frames d'un MP4 :

| Mécanisme | Outil utilisé | Quand | Où vont les frames ? |
|---|---|---|---|
| **Streaming en mémoire** | OpenCV (`cv2.VideoCapture`) | Station 5 Phase A | en RAM, jetées immédiatement après comparaison |
| **Extraction sur disque** | FFmpeg (`subprocess.run`) | Stations 5B, 6, 7 | sauvegardées dans `temp/` comme `.jpg` |

Pourquoi cette dualité ? Parce que **PySceneDetect a besoin d'inspecter
TOUTES les frames consécutives** (les 408 frames du clip) pour détecter les
coupures — il ne peut pas se contenter de 5 frames sauvegardées. Donc il les
lit toutes en streaming via OpenCV, et **les jette** après usage.

FFmpeg, lui, est utilisé **seulement quand on a besoin de SAUVEGARDER une
frame précise sur disque** (pour qu'elle soit ensuite lue par PIL, par CLIP,
ou affichée comme vignette dans le navigateur).

### Bilan global

Pour un clip avec **N scènes**, voici le total des frames sauvegardées sur
disque :

| Station | Frames par scène | Total pour N scènes |
|---|:-:|:-:|
| 5B (thumbnails) | 1 | N |
| 6 (PIL) | 3 | 3N |
| 7 (CLIP) | 1 | N |
| **TOTAL** | **5** | **5N** |

Pour le clip Sintel C (1 scène) : **5 frames** sauvegardées dans `temp/`.
Pour un clip avec 10 scènes : **50 frames**.

Toutes ces frames temporaires sont nettoyées après usage (ou restent dans
`temp/` selon le code, à vérifier).

---

## Station 6 — PIL : la loupe-photomètre

### Histoire
Maintenant, **pour chaque scène détectée**, Marc va au **studio photo** avec
**PIL** (Pillow).

PIL est un **photographe-analyste**. Marc lui demande :

> *« Pour cette scène, extrais-moi 3 photos (à 25 %, 50 %, 75 % de la durée),
>   et mesure-moi : luminosité, contraste, température, mouvement, netteté,
>   et l'énergie globale. »*

PIL ouvre les 3 frames (déjà extraites par FFmpeg), et calcule :

| Métrique | Comment | Sintel C |
|---|---|---|
| **Luminosité** | moyenne RGB / (3 × 255) | 0.42 |
| **Température** | ratio R/B (warm / neutral / kalt) | "neutral" |
| **Contraste** | écart-type de la luminance pondérée (formule ITU-R BT.601) | 0.71 |
| **Mouvement** | différence moyenne de pixels entre les 3 frames | 0.38 |
| **Netteté** | variance du Laplacien (proxy du flou) | 0.59 |
| **Énergie** | 0.40·contraste + 0.35·mouvement + 0.15·luminosité + 0.10·netteté | **0.331** |

L'**énergie** sera utilisée plus tard par l'algorithme de coupe IA pour
décider quelles scènes mettre où dans l'arc narratif.

Marc écrit : *« Étape 54 % — 1 Szene visuell analysiert »*.

### Pourquoi PIL et pas OpenCV pour ces métriques ?

Bonne question. Tu pourrais faire pareil avec OpenCV (`cv2.Laplacian`, etc.)
— c'est même un peu plus rapide. Mais le choix de PIL est **délibéré** :

1. PIL est plus **léger** (pas besoin de la grosse lib OpenCV pour des
   opérations basiques)
2. Le code est plus **lisible et auto-documenté** (chaque étape de la formule
   est visible)
3. Plus facile à **expliquer dans la thèse** (formules transparentes)

### Sa famille
- `Image.open(path).convert("RGB")` : ouvre une image en RGB
- `Image.resize((64, 64))` : downscale rapide
- `Image.getdata()` : lit les pixels comme liste de tuples (R, G, B)
- Implémentation maison de la **variance laplacienne** à
  [`ingest.py:332-359`](../../backend/workers/ingest.py#L332-L359)
- Implémentation maison de la **mean pixel diff** à
  [`ingest.py:321-329`](../../backend/workers/ingest.py#L321-L329)

### De qui il dépend
- Rien d'externe (libs C compilées dans Pillow)

### Qui l'utilise
- La fonction `schritt_analyse_visuelle()` à
  [`ingest.py:362-481`](../../backend/workers/ingest.py#L362-L481)

---

### 🔬 Deep dive — Comment PIL calcule-t-il VRAIMENT tout ça ?

C'est la station la plus dense mathématiquement. Décortiquons.

#### 1. Quelle frame sert à quoi ? (les 3 rôles)

Pour **chaque scène**, FFmpeg sauvegarde 3 frames :

| Frame | Position | Taille | Utilisée pour |
|---|---|---|---|
| **f50** | 50 % de la durée (le milieu) | **64×64 px** | Luminosité, Température, Contraste, Netteté, Qualité |
| **f25** | 25 % de la durée | 32×32 px | Mouvement uniquement |
| **f75** | 75 % de la durée | 32×32 px | Mouvement uniquement |

**Pourquoi cette répartition ?**

- Les métriques **statiques** (luminosité, contraste, etc.) regardent **une
  seule image** représentative de la scène → on prend celle du milieu (f50)
  comme « image type ».
- Les métriques **dynamiques** (mouvement = différence entre frames
  consécutives) ont besoin d'**au moins deux instants** dans le temps. On
  compare f25↔f50 et f50↔f75.
- f50 est en **64×64** (plus grande) parce qu'elle sert à plus de calculs.
  f25 et f75 sont en **32×32** (plus petites) parce qu'elles servent juste
  à la comparaison avec f50.

**Ce N'EST PAS** une moyenne des 3 frames. Chaque métrique a son propre mode
de calcul.

#### 2. Les 7 métriques — formule par formule

##### 2.1 Luminosité (`luminosite`)

**Intuition** : à quel point la scène est claire ou sombre.

**Formule exacte** ([ingest.py:415](../../backend/workers/ingest.py#L415)) :

```
luminosite = (Σ R + Σ G + Σ B) / (3 × N × 255)
```

où :
- Σ R = somme des valeurs Rouge de tous les pixels
- N = nombre total de pixels (= 64 × 64 = 4096 pour f50)
- 255 = valeur max d'un canal RGB 8-bit

**Plage** : 0 (noir total) → 1 (blanc total)

**D'où vient le 0.42 que tu as vu ?**

Sur Sintel C frame 50% (paysage sombre dramatique) :
- Moyenne RGB sur les 4096 pixels ≈ 107 (sur 255)
- 107 / 255 = **0.42** ✓

C'est simplement la **brillance moyenne** de l'image, normalisée à [0, 1].

##### 2.2 Température de couleur (`temperature`)

**Intuition** : la scène est-elle warm (rouge/jaune) ou kalt (bleue) ?
Important au cinéma — warm = nostalgique, romantique ; kalt = action,
mystère.

**Formule** ([ingest.py:419-422](../../backend/workers/ingest.py#L419-L422)) :

```
ratio = moyenne(R) / (moyenne(B) + 1.0)

if ratio > 1.25 : "warm"
if ratio < 0.80 : "kalt"
sinon            : "neutral"
```

**Pourquoi `+ 1.0` au dénominateur ?** Pour éviter une division par zéro si
la frame est totalement sans bleu.

**Plage** : chaîne `"warm" | "neutral" | "kalt"` (pas un nombre).

##### 2.3 Contraste (`kontrast`)

**Intuition** : à quel point l'image est riche en variations de
luminosité — un plan plat (ciel uni) a peu de contraste, un clair-obscur en
a beaucoup.

**Formule** ([ingest.py:425-428](../../backend/workers/ingest.py#L425-L428)) :

```
# Étape 1 : luminance pondérée par pixel (norme ITU-R BT.601)
lum_pixel = 0.299 × R + 0.587 × G + 0.114 × B

# Étape 2 : écart-type de cette luminance
std_dev = sqrt( Σ(lum_pixel - moyenne_lum)² / N )

# Étape 3 : normalisation à [0, 1]
kontrast = min(1.0, std_dev / 80.0)
```

**Pourquoi 0.299/0.587/0.114 ?** Ce sont les coefficients **standards
ITU-R BT.601** pour convertir RGB en luminance perçue par l'œil humain. L'œil
est **plus sensible au vert** (d'où le 0.587), moins au rouge, et peu au
bleu. Ce n'est pas arbitraire — c'est une norme internationale.

**Pourquoi `/ 80.0` ?** Pour normaliser. Empiriquement, un écart-type de
luminance de 80 (sur 255 max) correspond à un contraste « très élevé » dans
la pratique. Au-delà, on plafonne à 1.0.

**Exemple** : Sintel C frame 50% → écart-type ≈ 57 → 57/80 = **0.71** ✓

##### 2.4 Netteté (`schaerfe`)

**Intuition** : à quel point l'image est nette (focus net) ou floue (out of
focus, motion blur).

**Méthode** : **variance du Laplacien** — une technique classique de mesure
de la netteté en computer vision (Pech-Pacheco et al., 2000).

**Formule** ([ingest.py:347-359](../../backend/workers/ingest.py#L347-L359)) :

```
# Étape 1 : convertir l'image en niveaux de gris 32×32
gray[i] = luminance(pixel_i)

# Étape 2 : pour chaque pixel intérieur, appliquer l'opérateur Laplacien
laplace[x,y] = -4·gray[x,y] + gray[x-1,y] + gray[x+1,y]
                            + gray[x,y-1] + gray[x,y+1]

# Étape 3 : variance des valeurs Laplaciennes
variance = Σ(laplace[i] - moyenne)² / N

# Étape 4 : normalisation à [0, 1]
schaerfe = min(1.0, variance / 600.0)
```

**Pourquoi le Laplacien ?** C'est un opérateur qui **détecte les contours**
(les variations brutales de luminosité entre pixels voisins). Une image
nette a des contours bien définis → grande variance. Une image floue a des
transitions douces → petite variance.

**Pourquoi `/ 600.0` ?** Comme pour le contraste, c'est une **normalisation
empirique** — les images typiques bien nettes ont une variance Laplacienne
entre 200 et 800.

**Pourquoi 32×32 et pas 64×64 ?** Pour la performance — la variance
Laplacienne sur 32×32 est ~4× plus rapide qu'en 64×64, et la précision reste
suffisante.

##### 2.5 Qualité (`qualitaet`)

**Intuition** : un score combiné pour filtrer les frames de mauvaise qualité
(floues, sur-exposées ou sous-exposées).

**Formule** ([ingest.py:435-436](../../backend/workers/ingest.py#L435-L436)) :

```
# Pénalité d'exposition
expo_penalty = max(0, lum - 0.80) × 3   # sur-exposition
             + max(0, 0.15 - lum) × 2   # sous-exposition

qualitaet = min(1.0, schaerfe × (1 - expo_penalty))
```

**Idée** : la luminosité idéale est entre 0.25 et 0.75. En dehors, on
**pénalise** la netteté. Si une frame est très nette mais cramée → qualité
basse. Si elle est moyenne netteté mais bien exposée → qualité moyenne.

**Pourquoi `× 3` pour la sur-exposition et `× 2` pour la sous-exposition ?**
La sur-exposition (image cramée) détruit plus d'information que la
sous-exposition (image sombre récupérable). C'est une pondération **basée
sur l'intuition photographique**.

##### 2.6 Mouvement (`mouvement`)

**Intuition** : à quel point ça bouge entre le début, le milieu et la fin de
la scène.

**Formule** ([ingest.py:441-460](../../backend/workers/ingest.py#L441-L460)) :

```
# Étape 1 : différence de pixels entre f25 et f50, puis entre f50 et f75
diff_1 = _pixel_diff(f25, f50)   # 0 → 1
diff_2 = _pixel_diff(f50, f75)   # 0 → 1

# Étape 2 : moyenne pondérée (la 2ème moitié compte un peu plus)
raw = 0.45 × diff_1 + 0.55 × diff_2

# Étape 3 : amplification ×2.5 (parce qu'un downscale 32×32 atténue les diffs)
mouvement = min(1.0, raw × 2.5)
```

et `_pixel_diff(a, b)` ([ingest.py:321-329](../../backend/workers/ingest.py#L321-L329)) =

```
diff(a, b) = moyenne sur tous les pixels de Σ|a[c] - b[c]| / (3 × 255)
```

**Limitation à reconnaître devant le prof** :
> *« Cette mesure est une approximation par différence de pixels en 32×32,
>   pas un vrai optical flow (Farnebäck, Lucas-Kanade). Sur un montage à
>   plan fixe avec acteur immobile, le mouvement réel est faible et bien
>   mesuré. Sur un travelling rapide ou un zoom, c'est moins précis. C'est
>   une limitation documentée dans la thèse. »*

##### 2.7 Énergie (`energie`) — LE point clé pour le prof

**Intuition** : un score unique qui résume la « force visuelle » d'une scène
pour décider de sa place dans l'arc narratif (action, climax, transition,
etc.).

**Formule exacte** ([ingest.py:464-469](../../backend/workers/ingest.py#L464-L469)) :

```
energie = 0.40 × kontrast
        + 0.35 × mouvement
        + 0.15 × luminosite
        + 0.10 × schaerfe
```

**Plage** : [0, 1]. Les 4 coefficients **somment à 1.0** (c'est une moyenne
pondérée).

**Exemple Sintel C** :
- contraste = 0.71
- mouvement = 0.38
- luminosité = 0.42
- netteté = 0.59

```
energie = 0.40 × 0.71 + 0.35 × 0.38 + 0.15 × 0.42 + 0.10 × 0.59
        = 0.284 + 0.133 + 0.063 + 0.059
        = 0.331  ✓ (la valeur que tu as vue dans le modal)
```

#### 3. D'où viennent les coefficients 0.40 / 0.35 / 0.15 / 0.10 ?

C'est LA question que ton prof va creuser. Voici la réponse honnête.

**Ces coefficients sont des hyperparamètres HEURISTIQUES**, choisis par
intuition (et par toi-même via Copilot 😉), **PAS** validés expérimentalement
sur un dataset. C'est une **limitation** que ta thèse doit reconnaître.

Cela dit, les choix sont **défendables** par la théorie du montage :

| Coef | Métrique | Pourquoi ce poids ? |
|:-:|---|---|
| **0.40** | contraste | Le **contraste** est documenté comme le facteur visuel le plus dominant pour l'attention humaine. Un plan plat ennuie ; un plan contrasté capte l'œil. C'est la base de la « visual literacy » de Walter Murch. |
| **0.35** | mouvement | Le **mouvement** est le 2ème facteur d'attention. C'est ce qui crée l'« énergie cinématographique » par opposition à la « stase ». |
| **0.15** | luminosité | Secondaire : une image trop sombre ou trop claire est moins engageante, mais entre 0.25 et 0.75 ça ne différencie pas beaucoup. |
| **0.10** | netteté | Le moins important : c'est surtout un **filtre qualité** (élimine les frames floues), pas un signal narratif. |

#### 4. Comment défendre ces coefficients devant le prof ?

**Phrase prête (français pour comprendre)** :

> *« Les coefficients 0.40, 0.35, 0.15 et 0.10 sont des hyperparamètres
>   heuristiques. Ils s'inspirent de la théorie du montage de Walter Murch
>   ("In the Blink of an Eye", 2001), qui identifie le contraste et le
>   mouvement comme les principaux signaux d'attention visuelle. Le contraste
>   est documenté comme dominant, suivi du mouvement. La luminosité et la
>   netteté sont des facteurs secondaires. Une validation empirique par
>   user study reste à faire — c'est une limitation que je discute dans le
>   chapitre Évaluation. La formule actuelle est paramétrable : un futur
>   travail pourrait apprendre ces poids depuis un dataset annoté de
>   montages humains. »*

**Phrase à mémoriser en allemand pour la défense** :

> *„Die Koeffizienten 0,40 / 0,35 / 0,15 / 0,10 sind heuristisch gewählte
>   Hyperparameter, inspiriert von Walter Murch's Filmmontage-Theorie
>   ("In the Blink of an Eye", 2001). Murch nennt **Kontrast und Bewegung**
>   als die dominantesten visuellen Aufmerksamkeitsfaktoren. Luminanz und
>   Schärfe sind sekundäre Qualitätsfaktoren. **Eine formale Validierung
>   durch eine Nutzerstudie ist als Limitation in meiner Arbeit dokumentiert**
>   und für eine Erweiterung vorgesehen — etwa durch das Erlernen der
>   Gewichte aus einem annotierten Datensatz von menschlichen Schnitten."*

#### 5. Récapitulatif visuel

```
3 frames extraites par FFmpeg
        │
        ├── f50 (64×64) ──┬─→ Luminosité  → 0.42
        │                  ├─→ Température → "neutral"
        │                  ├─→ Contraste   → 0.71
        │                  ├─→ Netteté     → 0.59
        │                  └─→ Qualité     → schaerfe × (1 - expo_penalty)
        │
        ├── f25 (32×32) ──┐
        ├── f50 (32×32) ──┼─→ Mouvement → 0.38
        └── f75 (32×32) ──┘  (diff f25↔f50 + diff f50↔f75)
                              ↓
                       ┌──────────────────────────────────────┐
                       │  Énergie =                            │
                       │    0.40 × 0.71   (contraste)  = 0.284 │
                       │  + 0.35 × 0.38   (mouvement)  = 0.133 │
                       │  + 0.15 × 0.42   (luminosité) = 0.063 │
                       │  + 0.10 × 0.59   (netteté)    = 0.059 │
                       │  ─────────────────────────────        │
                       │  Énergie totale               = 0.331 │
                       └──────────────────────────────────────┘
```

### Mot-clé
**ANALYSE PIXEL-PAR-PIXEL**

---

## Station 7 — open-clip + PyTorch : le critique d'art

### Histoire
Marc passe au **scanner sémantique**. Il prend la **frame du milieu** de
chaque scène et la pose devant **CLIP**.

CLIP est un **critique d'art** très spécial : il regarde une image, et au
lieu d'écrire une description en français, il **résume l'image en 512
chiffres** (un vecteur dans un espace à 512 dimensions). Ces chiffres
encodent la **sémantique** de l'image — pas juste les couleurs, mais ce que
**représente** l'image.

L'astuce magique : si deux images représentent **des choses similaires** (par
exemple deux paysages), leurs 512 chiffres seront **proches** (similarité
cosinus haute). Si elles représentent des choses très différentes (un visage
vs. une voiture), les chiffres seront **éloignés**.

C'est exactement ce que Marc va utiliser **plus tard** (en Phase 3,
l'algorithme de coupe IA) pour mesurer la **diversité visuelle** entre
scènes.

CLIP travaille sur **PyTorch** (le framework de deep learning de Meta), et
sur Mac il utilise **Metal Performance Shaders (MPS)** — c'est-à-dire le GPU
intégré du M3 — pour aller plus vite.

Pour Sintel C : **1 embedding 512-dim, norme L2 = 11.5** (par exemple).

Marc écrit : *« Étape 75 % — 1 Embedding erstellt »*.

### Pourquoi ViT-B/32 et pas ViT-L/14 ?

| Modèle | Paramètres | Taille | Précision (ImageNet zero-shot) |
|---|---|---|---|
| ViT-B/32 | 87 M | 151 MB | 63.2 % |
| ViT-L/14 | 304 M | 890 MB | 75.5 % |

ViT-L/14 est plus précis mais ~6× plus lourd. Pour CinAssist, ViT-B/32 est le
**bon compromis taille/qualité** sur Apple Silicon sans GPU dédié.

### Sa famille (open-clip)
- `open_clip.create_model_and_transforms("ViT-B/32", pretrained="openai")`
  : charge le modèle (151 MB)
- `preprocess(image)` : normalise l'image (224×224, normalisation OpenAI)
- `model.encode_image(image)` : produit le vecteur 512-dim
- **L2-normalisation** du vecteur (essentiel pour la similarité cosinus)

### Sa famille (PyTorch)
- `torch.no_grad()` : context manager pour l'inférence (pas de gradient
  nécessaire — économise la mémoire)
- `torch.backends.mps.is_available()` : détecte le GPU Mac
- `.to("mps")` : envoie sur le GPU
- `tensor.norm(dim=-1, keepdim=True)` : calcule la norme L2

### De qui il dépend
- **PyTorch** (le moteur de calcul)
- **torchvision** (utilitaires images)
- **NumPy** (en interne pour les conversions)

### Qui l'utilise
- La fonction `schritt_clip_embeddings()` à
  [`ingest.py:487-537`](../../backend/workers/ingest.py#L487-L537)

---

### 🔬 Deep dive — Que fait CLIP exactement avec cette frame ?

C'est la station la plus « magique » de la pipeline. Démystifions.

#### 1. Combien de frames, et laquelle ?

**UNE SEULE frame par scène, prise exactement au milieu** :

```python
mitte = (szene["start_zeit"] + szene["end_zeit"]) / 2
```

Pour une scène allant de 0 s à 17 s → frame à **8.5 s**.

**Pourquoi seulement une, pas 3 comme pour PIL ?**

Parce que CLIP n'analyse pas le **mouvement** (PIL le fait déjà), il analyse
le **contenu sémantique** d'une image. Or pour la sémantique d'une scène,
**la frame du milieu suffit** : elle représente le sujet principal de la
scène mieux que le début (souvent encore en fondu) ou la fin (souvent en
transition).

Un éditeur professionnel choisirait probablement le même critère pour le
« plan-clé » d'une scène.

#### 2. La frame est extraite à résolution complète

Contrairement aux extractions pour PIL (downsizées à 32×32 ou 64×64), pour
CLIP la frame est extraite **à la résolution originale** (1920×1080 pour
Sintel). Pourquoi ? Parce que CLIP a besoin de **détails visuels riches**
pour comprendre ce qui est dans l'image.

Mais ensuite, CLIP la redimensionne **lui-même** à 224×224 via son
préprocesseur (étape 4 ci-dessous). Donc la résolution 1920×1080 n'est
qu'un intermédiaire — c'est juste pour avoir une frame de **bonne qualité**
avant le downscale.

#### 3. Qu'est-ce qu'un « embedding » ?

Imagine que tu veuilles décrire une image avec des chiffres. Tu pourrais
dire :
- « Cette image contient un visage » → 1 chiffre booléen (oui/non)
- « Cette image est lumineuse à 0.42 » → 1 chiffre continu

Mais comment encoder *« cette image montre un chasseur solitaire dans une
montagne enneigée au coucher du soleil »* avec des chiffres ?

**CLIP le fait avec 512 chiffres** (un vecteur dans un espace à
512 dimensions). Chaque dimension capture **un aspect abstrait** appris à
partir de **400 millions d'images** annotées par OpenAI. On ne sait pas
**explicitement** ce que représente chaque dimension (c'est appris
automatiquement), mais on sait que :

- **Images similaires → embeddings proches** (en distance cosinus)
- **Images différentes → embeddings éloignés**

C'est l'**hypothèse de continuité sémantique** sur laquelle CLIP est bâti.

#### 4. Le préprocessing CLIP (5 étapes invisibles)

Quand on appelle `preprocess(image)` dans le code, CLIP fait **5
transformations** automatiques sur la frame d'entrée
([open_clip](https://github.com/mlfoundations/open_clip) standard) :

| # | Étape | Détail |
|:-:|---|---|
| 1 | **Resize** | redimensionne à 224×224 (en gardant le ratio + crop) |
| 2 | **CenterCrop** | recadre au centre si non-carré |
| 3 | **ToTensor** | convertit RGB 0-255 → tenseur PyTorch [0, 1] |
| 4 | **Normalize** | applique la normalisation OpenAI : `(pixel - mean) / std` avec mean=(0.481, 0.458, 0.408) et std=(0.269, 0.261, 0.276) |
| 5 | **Unsqueeze + .to(device)** | ajoute une dimension batch et envoie sur le GPU (MPS ou CPU) |

**Pourquoi 224×224 ?** C'est la résolution d'entrée standard du **Vision
Transformer ViT-B/32** d'OpenAI. Trois raisons :
1. 224 est divisible par 32 (la taille des patches, voir ci-dessous)
2. 224 × 224 = 50 176 pixels — assez pour les détails, mais pas trop pour
   être lent
3. C'est devenu le standard de facto en vision (depuis ResNet de 2015)

**Pourquoi la normalisation avec mean/std OpenAI ?** Parce que CLIP a été
**entraîné** sur des images normalisées avec ces statistiques précises.
Pour que l'inférence donne les mêmes résultats que pendant l'entraînement,
on doit appliquer exactement les mêmes transformations.

#### 5. À l'intérieur du Vision Transformer (architecture ViT-B/32)

C'est ici que la magie se passe. Très brièvement, sans entrer dans les
maths du transformer :

```
Image 224×224×3
       │
       ▼
[Patch Embedding]  → découpe en 7×7 = 49 patches de 32×32 pixels
                     chaque patch est aplati en vecteur 768-dim
       │
       ▼
[+ Token CLS]      → on ajoute un 50ème token spécial « CLS »
                     qui « résumera » l'image entière
       │
       ▼
[12 couches Transformer Encoder]
                   → multi-head self-attention (12 têtes)
                     chaque patch « regarde » tous les autres
                     12 couches empilées
       │
       ▼
[Projection finale du token CLS]
                   → vecteur 512-dim
       │
       ▼
   embedding (512 floats)
```

**Le concept clé** : le token CLS, à travers les 12 couches de
self-attention, **collecte des informations de tous les patches**, et finit
par contenir une **représentation globale** de l'image dans son vecteur
final de 512 dimensions.

**Référence** : Dosovitskiy et al. (2021), *« An Image is Worth 16×16
Words: Transformers for Image Recognition at Scale »*, ICLR — c'est le
paper qui a introduit le ViT.

#### 6. Pourquoi normaliser le vecteur en L2 ?

Après que CLIP a produit le vecteur 512-dim, le code fait ça
([ingest.py:521](../../backend/workers/ingest.py#L521)) :

```python
embedding = embedding / embedding.norm(dim=-1, keepdim=True)
```

C'est une **normalisation L2** : on divise le vecteur par sa norme
euclidienne, pour qu'il devienne de **norme 1** (un vecteur unitaire).

**Pourquoi ?** Parce que ça **simplifie radicalement** le calcul de
similarité cosinus plus tard. La similarité cosinus entre deux vecteurs
`a` et `b` est :

```
cos(a, b) = (a · b) / (‖a‖ × ‖b‖)
```

Mais si `‖a‖ = ‖b‖ = 1` (normalisés), alors :

```
cos(a, b) = a · b       (simple produit scalaire)
```

Donc en normalisant **une fois** au moment du stockage en base, on évite de
recalculer les normes à chaque comparaison entre scènes. Plus rapide quand
on compare beaucoup de scènes (ce qu'on fait dans l'algorithme de coupe).

#### 7. Comment CinAssist utilise ces embeddings ?

Tu ne les utilises pas **pendant** l'ingestion. Ils sont juste **stockés en
base** :

```python
szene.clip_embedding = [0.024, -0.118, 0.087, ..., 0.041]  # 512 floats
```

C'est en **Phase 3 (KI-Schnitt)** que ces embeddings sont utilisés. L'algo
de coupe :

1. Pour chaque **paire de scènes adjacentes** dans le montage candidat,
   calcule la **similarité cosinus** entre leurs embeddings.
2. Utilise cette similarité comme une mesure de **diversité visuelle** :
   - Similarité haute (≈ 1.0) → scènes visuellement similaires → **mauvais
     enchaînement** (le spectateur ne perçoit pas le changement)
   - Similarité basse (≈ -0.5 à 0.5) → scènes visuellement diverses
     → **bon enchaînement** cinématographique
3. L'algorithme **maximise la diversité visuelle** entre scènes
   consécutives, comme une fonction de score.

C'est **précisément ici** que CLIP justifie son intégration dans la
pipeline. Sans CLIP, on ne pourrait pas mesurer la diversité visuelle
sémantiquement.

#### 8. Exemple chiffré : 2 scènes Sintel

Supposons deux scènes :
- Scène A (paysage de montagne) → embedding `e_A` (512 floats)
- Scène B (visage du protagoniste) → embedding `e_B` (512 floats)

Comme `‖e_A‖ = ‖e_B‖ = 1` (normalisés), la similarité cosinus est juste :

```
similarité(A, B) = e_A · e_B = Σ_{i=1}^{512} e_A[i] × e_B[i]
```

Cette valeur sera :
- ≈ 0.85 si les deux images sont du même type (paysage avec paysage)
- ≈ 0.3 si elles sont très différentes (paysage vs visage)
- ≈ -0.1 si elles sont totalement opposées

CinAssist utilise `diversité = 1 - similarité` comme score (plus c'est
haut, plus la transition est intéressante).

#### 9. Le rôle de PyTorch et Metal (MPS)

PyTorch est le **moteur de calcul** sous CLIP. Il fait toutes les opérations
matricielles du Vision Transformer (multiplications, attention, etc.).

Sur Apple Silicon (M1/M2/M3), PyTorch utilise **Metal Performance Shaders
(MPS)** — l'API graphique d'Apple — pour faire ces calculs sur le **GPU
intégré du Mac** au lieu du CPU. Résultat : ~3-5× plus rapide qu'en CPU
pur.

```python
device = "mps" if torch.backends.mps.is_available() else "cpu"
model = open_clip.create_model_and_transforms("ViT-B/32", pretrained="openai", device=device)
```

Cette ligne est ce qui rend l'analyse de 10 scènes possible en 30 secondes
au lieu de 2-3 minutes.

#### 10. Pourquoi 512 dimensions et pas plus ou moins ?

**512 n'est PAS un choix de CinAssist.** C'est une **propriété fixe du modèle
ViT-B/32** d'OpenAI. Si on changeait de modèle, on aurait une autre dimension.

##### La famille des modèles CLIP

| Modèle | Patches | Profondeur | Sortie embedding | Taille |
|---|---|---|:-:|---|
| **ViT-B/32** (CinAssist) | 32×32 | 12 couches | **512** | 151 MB |
| ViT-B/16 | 16×16 | 12 couches | 512 | 332 MB |
| ViT-L/14 | 14×14 | 24 couches | 768 | 890 MB |
| ViT-H/14 | 14×14 | 32 couches | 1024 | 1.8 GB |
| ViT-bigG/14 | 14×14 | 48 couches | 1280 | 4.9 GB |

##### Pourquoi OpenAI a choisi 512 pour le modèle Base ?

**Raison 1 — Compromis expressivité / efficacité :**

- Trop peu (ex : 64-dim) → ne peut pas capturer toute la diversité visuelle.
  L'espace est trop petit pour bien séparer les concepts.
- Trop (ex : 4096-dim) → **redondance** des informations + gaspillage mémoire
  + risque du « curse of dimensionality » (voir Raison 4).

512 est le **sweet spot empirique** trouvé par les chercheurs d'OpenAI
pendant l'entraînement.

**Raison 2 — Compatibilité avec l'encodeur texte :**

CLIP a deux encodeurs :
- **Encodeur d'image** : ViT-B/32 → 512-dim
- **Encodeur de texte** : Transformer texte → **également 512-dim**

Les deux partagent **le même espace vectoriel à 512 dimensions**. C'est ce qui
permet de comparer une image avec un texte (« le mot 'chien' est-il proche de
cette image ? ») via similarité cosinus.

Si OpenAI avait choisi des dimensions différentes pour les deux encodeurs,
toute la beauté de CLIP (la « contrastive language-image pre-training »)
disparaîtrait.

**Raison 3 — Stockage en pratique :**

- 512 floats × 4 octets = **2 048 octets ≈ 2 KB par embedding**
- Pour 1 000 scènes en base : 2 MB total
- Pour 10 000 scènes : 20 MB
- Très gérable pour PostgreSQL avec son type `ARRAY[Float]`

À l'inverse, ViT-bigG/14 produit des embeddings 1280-dim = 5 KB chacun.
Pour des bases avec millions d'images, ça commence à compter.

**Raison 4 — Le « curse of dimensionality » :**

Un phénomène statistique surprenant : en **très haute dimension** (1000+),
les vecteurs aléatoires tendent à devenir **tous équidistants**. La
similarité cosinus perd son pouvoir discriminant. À l'inverse, en **très
basse dimension** (16-64), il n'y a pas assez d'« axes » pour séparer les
concepts.

512 dimensions = assez riche pour distinguer les concepts visuels, pas assez
pour souffrir du fléau de la dimensionnalité.

**Raison 5 — Capacité théorique largement suffisante :**

Dans un espace à 512 dimensions, on peut représenter environ **10^150
directions distinguables**. À titre de comparaison :
- Nombre d'atomes dans l'univers observable : ~10^80
- Nombre de concepts visuels humains : ~10^6 à 10^9
- Capacité de 512-dim : 10^150

C'est largement plus que nécessaire pour représenter tous les types de scènes
de cinéma jamais filmées.

##### Phrase à dire au prof

> *« Die Dimension 512 ist eine feste Eigenschaft des Modells ViT-B/32 von
>   OpenAI, kein freier Parameter meines Systems. Sie wurde von den Autoren
>   gewählt als Kompromiss zwischen Ausdrucksstärke und Speicherbedarf, und
>   um den gemeinsamen Vektorraum mit dem Text-Encoder zu teilen. Bei 512
>   Dimensionen beträgt der Speicherbedarf 2 KB pro Embedding, was für
>   meine Größenordnung (einige tausend Szenen) unproblematisch ist. »*

---

#### 11. Le Mehrwert de CLIP — qu'apporte-t-il vraiment ?

Question piège du prof : *« Tu mesures déjà luminosité, contraste et mouvement
avec PIL. Pourquoi rajouter CLIP ? »*

Réponse courte : **CLIP mesure le CONTENU (le « quoi »), PIL mesure la SURFACE
(le « combien »)**. Ce sont deux dimensions différentes et complémentaires.

##### Le scénario qui le démontre

Imagine **deux scènes** :

- **Scène A** : un visage en gros plan, dans un intérieur tamisé
- **Scène B** : une montagne enneigée au lever du soleil

Mesurées par **PIL seul** :

| Métrique | Scène A | Scène B | Verdict PIL |
|---|---|---|---|
| Luminosité | 0.55 | 0.58 | quasi identiques |
| Contraste | 0.62 | 0.65 | quasi identiques |
| Mouvement | 0.15 | 0.18 | quasi identiques |
| Énergie | 0.42 | 0.44 | **« scènes similaires »** |

**Conclusion PIL** : *« Ces deux scènes sont similaires, pas intéressant de les
enchaîner — pas de contraste visuel. »*

Sauf qu'en regardant les images, c'est **un visage** et **une montagne** !
Visuellement **totalement différents**. Mettre l'un après l'autre serait un
contraste cinématographique fort, le genre d'enchaînement qui marque le
spectateur.

PIL ne le voit pas, parce qu'il mesure **des statistiques de pixels**, pas
**ce que représentent ces pixels**.

**Mesurées par CLIP :**

```
cos(embedding_A, embedding_B) = e_A · e_B ≈ 0.12
diversité(A, B) = 1 - 0.12 = 0.88   ← très haut
```

CLIP comprend que l'une montre un visage, l'autre une montagne. Il sait,
parce qu'il a vu 400 millions d'images avec leurs légendes pendant son
entraînement.

##### Tableau comparatif détaillé

| Aspect | PIL seul | + CLIP |
|---|:-:|:-:|
| Mesure le niveau de luminosité | ✓ | ✓ |
| Mesure le contraste de pixels | ✓ | ✓ |
| Mesure le mouvement (différence de pixels) | ✓ | ✓ |
| Distingue 2 plans de même luminance | ✗ | **✓** |
| Distingue un visage d'un paysage | ✗ | **✓** |
| Distingue intérieur d'extérieur | ✗ | **✓** |
| Distingue jour de nuit (même luminance moyenne) | ✗ | **✓** |
| Robuste aux flickers de lumière | partiel | **✓** (l'éclairage ne change pas la sémantique) |
| Pré-entraîné sur 400 M d'images | ✗ | **✓** |
| Permet la recherche par prompt texte | ✗ | **✓** (extension future) |

##### Le vrai Mehrwert en une phrase

| | Type de diversité optimisée |
|---|---|
| **Sans CLIP** | Diversité **numérique** des métriques bas niveau (luminance, contraste, mouvement). On peut se tromper et croire que deux scènes sont similaires alors qu'elles montrent des choses radicalement différentes. |
| **Avec CLIP** | Diversité **sémantique** : ce qui est *montré* dans la scène. C'est exactement le critère qu'un éditeur professionnel évalue intuitivement. |

##### Mehrwert × 2 : l'extensibilité texte-image

C'est le bonus qui justifie le choix de CLIP **par rapport à des
alternatives** comme DINOv2 (Meta, 2023, plus précis pour la similarité
image-image).

CLIP a aussi un **encodeur de texte** qui sort dans le **même espace 512-dim**.
CinAssist ne l'utilise pas encore, mais cela rend possible des extensions
futures **sans réentraîner** :

- *« Trouve-moi toutes les scènes avec un visage »* → encode `"face"` en
  512-dim, compare avec tous les embeddings d'images en base
- *« Cherche les scènes nocturnes »* → encode `"night scene"`
- *« Trouve les plans d'action »* → encode `"action shot"`
- *« Cherche les paysages enneigés »* → encode `"snowy landscape"`

Avec **DINOv2** (l'alternative envisagée), cette extension serait
**impossible** — DINOv2 ne connaît que les images, pas le texte.

C'est un **choix d'extensibilité délibéré** : on accepte une précision
légèrement moindre pour la similarité image-image, en échange d'un univers
d'extensions futures.

##### Phrases à dire au prof

**En français (pour comprendre)** :

> *« La valeur ajoutée de CLIP face aux métriques PIL est la comparabilité
>   **sémantique** plutôt que **pixel-statistique**. Deux scènes peuvent
>   avoir luminance, contraste et mouvement identiques, mais être
>   sémantiquement totalement différentes — par exemple un gros plan de
>   visage vs. un paysage de montagne. PIL ne détecte pas cette différence ;
>   CLIP si, parce qu'il a été pré-entraîné sur 400 millions de paires
>   image-texte. En plus, l'espace vectoriel commun texte-image de CLIP
>   permet une extension future — la recherche de scènes par prompt
>   textuel — sans nouvel entraînement. »*

**En allemand (pour la défense)** :

> *„Der wahre Mehrwert von CLIP gegenüber den PIL-Metriken ist die
>   **semantische** statt nur **pixelbasierte** Bildvergleichbarkeit. Zwei
>   Szenen können identische Luminanz, Kontrast und Bewegung haben, aber
>   semantisch völlig verschieden sein — zum Beispiel ein Gesicht in
>   Großaufnahme vs. eine Berglandschaft. PIL erkennt diesen Unterschied
>   nicht; CLIP schon, weil es auf 400 Millionen annotierten Bild-Text-Paaren
>   vortrainiert wurde. Hinzu kommt: durch den gemeinsamen
>   Text-Bild-Vektorraum von CLIP ist eine **zukünftige Erweiterung**
>   möglich — etwa die Suche nach Szenen per Textprompt — ohne erneutes
>   Training."*

---

#### 12. Phrases à dire au prof

**En français (pour comprendre)** :

> *« CLIP prend une seule frame par scène, au milieu, à résolution
>   originale. Il la redimensionne à 224×224, la normalise avec les
>   statistiques OpenAI, puis la passe à travers un Vision Transformer
>   ViT-B/32 — 12 couches de self-attention sur 49 patches de 32×32 plus
>   un token CLS. La sortie est un vecteur 512 dimensions, normalisé en L2
>   pour faciliter le calcul de similarité cosinus. Ce vecteur est stocké
>   en base et utilisé en Phase 3 pour mesurer la diversité visuelle
>   entre scènes consécutives — le critère central de l'algorithme de
>   construction du montage. »*

**En allemand (à mémoriser pour la défense)** :

> *„CLIP nimmt **eine einzige Frame pro Szene**, in der Mitte der Szene,
>   in Originalauflösung. Es skaliert sie auf 224×224, normalisiert mit
>   den OpenAI-Statistiken, und übergibt sie an einen **Vision Transformer
>   ViT-B/32** — zwölf Self-Attention-Schichten über 49 Patches von 32×32
>   plus einen CLS-Token. Die Ausgabe ist ein **512-dimensionaler Vektor**,
>   L2-normalisiert für effiziente Kosinus-Ähnlichkeitsberechnung. Dieser
>   Vektor wird in der Datenbank gespeichert und in Phase 3 zur Messung der
>   **visuellen Diversität zwischen aufeinanderfolgenden Szenen** verwendet
>   — das zentrale Kriterium meines Beam-Search-Schnittalgorithmus. CLIP
>   wurde gewählt, weil es ein etablierter Standard ist (Radford et al.,
>   ICML 2021, OpenAI) und weil ViT-B/32 das optimale Verhältnis von
>   Genauigkeit zu Laufzeit auf Apple Silicon ohne dedizierte GPU bietet."*

#### 13. Limites à reconnaître

| Limitation | Réponse défensive |
|---|---|
| « CLIP n'analyse qu'une seule frame, pas la dynamique de la scène. » | Vrai, mais la dynamique est déjà capturée par le « mouvement » de PIL. Les deux signaux sont complémentaires : PIL = dynamique pixel, CLIP = sémantique contenu. |
| « DINOv2 (Meta 2023) est plus précis que CLIP pour la similarité bild-bild. » | Vrai, mais DINOv2 n'a pas de tour de texte. CLIP a un **espace commun image-texte**, ce qui permet une future extension : « cherche-moi toutes les scènes contenant un visage » (text-to-image retrieval). C'est un choix d'**extensibilité**. |
| « ViT-L/14 serait plus précis. » | ViT-B/32 = 87M paramètres, 151 MB. ViT-L/14 = 304M, 890 MB. ViT-L/14 ne tient pas en RAM confortable sur un Mac sans GPU dédié. Trade-off documenté. |

#### 14. Récapitulatif visuel

```
Scène (start=0s, end=17s)
        │
        ▼
Marc demande à FFmpeg : "extrait UNE frame au milieu (8.5s)"
        │
        ▼
frame.jpg (1920×1080, full res)
        │
        ▼
CLIP preprocess() :
   Resize 224 → CenterCrop → ToTensor [0,1] → Normalize(OpenAI mean/std)
        │
        ▼
Vision Transformer ViT-B/32 (sur GPU MPS) :
   49 patches 32×32 + 1 CLS → 12 couches d'attention → 512-dim
        │
        ▼
L2-normalisation : embedding ÷ ‖embedding‖
        │
        ▼
Stocké dans szene.clip_embedding (ARRAY[Float] 512-dim en PostgreSQL)
        │
        │
        ▼  (utilisé seulement en Phase 3 KI-Schnitt)
        │
Pour deux scènes A et B :
   diversité(A, B) = 1 - (e_A · e_B)
        │
        ▼
Beam Search maximise la diversité moyenne sur la séquence finale
```

### Mot-clé
**EMBEDDING SÉMANTIQUE D'IMAGE 512-DIM**

---

## 🔗 Question d'architecture : comment les stations communiquent-elles ?

C'est LA question d'architecture la plus importante de Phase 2. Et la réponse
est contre-intuitive : **elles ne communiquent PAS directement.**

### La réponse courte

Aucune station ne « parle » à une autre. Marc passe d'une station à l'autre,
chacune fait son boulot, et **chacune écrit ses résultats au même endroit** :
la ligne de la scène dans la table `szenen` de PostgreSQL.

C'est un principe d'architecture qu'on appelle **« decoupling via
persistence »** (découplage par la persistance) — chaque composant est
indépendant, ils ne se connaissent même pas l'un l'autre.

### Le schéma vrai du flux de données

```
                        UNE SCÈNE en base
                     (1 ligne dans la table szenen)
                              │
                              │ chaque station remplit
                              │ une (ou plusieurs) colonne(s)
                              │
   ┌──────────────────────────┴──────────────────────────────┐
   │                                                          │
   │  Station 5  (PySceneDetect)                              │
   │    └─ écrit : szenen_nr, start_zeit, end_zeit, dauer,    │
   │              thumbnail_pfad                              │
   │                                                          │
   │  Station 4  (Whisper)                                    │
   │    └─ écrit : transkription, transkription_json          │
   │              (les timestamps mot-par-mot)                │
   │                                                          │
   │  Station 6  (PIL)                                        │
   │    └─ écrit : analyse_visuelle (JSON avec luminosité,    │
   │              kontrast, mouvement, schärfe, energie)      │
   │                                                          │
   │  Station 7  (CLIP)                                       │
   │    └─ écrit : clip_embedding (ARRAY[Float] 512-dim)      │
   │                                                          │
   │  Station 8  (LLaMA3)                                     │
   │    └─ écrit : beschreibung (1 phrase en allemand)        │
   │                                                          │
   └──────────────────────────────────────────────────────────┘

  Les stations sont en silos ; elles ne se passent rien entre elles.
  Le seul lieu de rencontre, c'est la ligne de la scène en base.
```

### Une ligne de scène après Phase 2 (exemple Sintel C)

Voici exactement ce qu'on retrouve dans la table `szenen` après que Marc a
fini son travail :

```
id                  : 7f8e1c2a-d3b4-...
clip_id             : 3f66eaab-89ad-44b1-...
szenen_nr           : 1
start_zeit          : 0.0           ← écrit par Station 5 (PySceneDetect)
end_zeit            : 17.0          ← écrit par Station 5
dauer               : 17.0          ← écrit par Station 5
thumbnail_pfad      : "temp/thumbs_3f66eaab.../szene_000.jpg"
                                    ← écrit par Station 5 (via FFmpeg)

transkription       : "A dangerous quest for a lone hunter..."
                                    ← écrit par Station 4 (Whisper)
transkription_json  : [{"start":1.4,"end":2.8,"text":"A dangerous quest",
                         "woerter":[{"wort":"A","start":1.40,"end":1.96},...]}]
                                    ← écrit par Station 4

analyse_visuelle    : {"luminosite":0.42, "temperature":"neutral",
                       "kontrast":0.71, "mouvement":0.38,
                       "schaerfe":0.59, "qualitaet":0.55,
                       "energie":0.331}
                                    ← écrit par Station 6 (PIL)

clip_embedding      : [0.024, -0.118, 0.087, 0.054, ..., 0.041]
                       (512 floats au total)
                                    ← écrit par Station 7 (CLIP)

beschreibung        : "Die Szene eröffnet mit einer langen, stummen
                       Einstellung auf eine einsame Landschaft, während der
                       Protagonist, ein isolierter Jäger, seine Stimme hört..."
                                    ← écrit par Station 8 (LLaMA3)
```

**Cinq « familles » de données écrites par cinq stations différentes**, sur
la même ligne. PIL ne savait pas que CLIP était en train d'écrire. CLIP ne
sait pas que LLaMA3 a déjà écrit.

### Quand est-ce que ça communique vraiment ?

**En Phase 3** (KI-Schnitt), quand l'algorithme de coupe IA décide quelle
scène va où dans l'arc narratif. À ce moment-là, l'algo fait :

```python
# pseudo-code simplifié de Phase 3
for scene in all_scenes:
    # Lit À LA FOIS les données PIL et les données CLIP
    energie    = scene.analyse_visuelle["energie"]      # vient de PIL
    mouvement  = scene.analyse_visuelle["mouvement"]    # vient de PIL
    embedding  = scene.clip_embedding                    # vient de CLIP
    has_dialog = bool(scene.transkription)              # vient de Whisper

    # Et combine tout ça pour décider du rôle de la scène
    role = classify_role(energie, mouvement, has_dialog, ...)

# Plus tard, calcul de la diversité visuelle entre 2 scènes consécutives
diversity = 1 - cosine_similarity(scene_A.clip_embedding, scene_B.clip_embedding)
```

**C'est ici** que les données de PIL et de CLIP se rencontrent — pas pendant
leur calcul, mais pendant leur **lecture commune** par l'algo de coupe.

### Pourquoi ce design ? Les 4 avantages

1. **Modularité**
   Tu peux remplacer Station 7 (CLIP) par DINOv2 sans changer Station 6
   (PIL). Tu peux ajouter une Station 7bis (par exemple un classifieur de
   scènes) sans toucher aux autres.

2. **Testabilité**
   Chaque station peut être testée **isolément** avec des données factices.
   Pas besoin de simuler les autres stations.

3. **Robustesse en cas de panne**
   Si LLaMA3 plante (Ollama down), Station 8 échoue mais les données de
   PIL et CLIP sont déjà en base. La scène est utilisable, juste sans
   description textuelle.

4. **Pas de couplage en mémoire**
   Marc passe d'une station à l'autre **sans porter avec lui un gros sac**
   de données intermédiaires. Chaque station prend dans la cuisine ce
   qu'elle a besoin, fait son truc, et range. Mémoire RAM minimale.

### L'alternative qu'on aurait pu faire (et pourquoi c'est moins bien)

**Design alternatif** : pipeline « streaming » où PIL passe directement ses
résultats à CLIP via un objet Python en mémoire.

```python
pil_results = analyse_visuelle(scene)        # PIL calcule
clip_results = compute_clip(scene, pil_results)  # CLIP utilise les résultats PIL
```

Problèmes :
- **Couplage fort** : CLIP dépend de la sortie exacte de PIL. Si on change
  une métrique de PIL, on doit modifier CLIP aussi.
- **Pas de persistance intermédiaire** : si CLIP plante, les calculs PIL
  sont perdus, il faut tout recommencer.
- **Pas réutilisable** : impossible de faire une analyse uniquement PIL ou
  uniquement CLIP plus tard.
- **Plus lourd en mémoire** : tout doit être gardé en RAM tant que la
  chaîne n'est pas finie.

Le design actuel de CinAssist (decoupling par DB) **évite tous ces
problèmes** en payant le prix d'un I/O DB entre les stations. C'est le bon
trade-off pour une pipeline de quelques minutes.

### L'analogie pour bien retenir

Imagine un grand cabinet médical avec **différents spécialistes** dans la
même clinique :
- L'ophtalmo examine tes yeux et écrit son rapport dans **ton dossier**
- Le cardiologue écoute ton cœur et écrit son rapport dans **le même dossier**
- Le radiologue scanne ta poitrine et écrit son rapport dans **le même dossier**

**Aucun des trois spécialistes ne se parle pendant l'examen.** Ils
travaillent indépendamment. Mais à la fin, **le médecin traitant** (= l'algo
de coupe en Phase 3) ouvre le dossier complet et **lit tous les rapports
ensemble** pour faire son diagnostic.

C'est exactement ce qui se passe ici : les stations Phase 2 sont les
spécialistes, la table `szenen` est le dossier patient, l'algo Phase 3 est
le médecin traitant.

### Phrase à dire au prof

**En français (pour comprendre)** :

> *« Les stations de la pipeline d'ingestion sont totalement découplées.
>   Aucune ne connaît l'existence des autres. Elles écrivent toutes dans
>   la même ligne de la table `szenen`, mais dans des colonnes différentes.
>   C'est un design intentionnel pour la modularité, la testabilité et la
>   robustesse : si LLaMA3 plante, le reste fonctionne. La vraie
>   intégration des signaux PIL et CLIP se fait en Phase 3, dans
>   l'algorithme de coupe, qui lit les deux colonnes ensemble pour
>   construire l'arc narratif. »*

**En allemand (pour la défense)** :

> *„Die Stationen der Ingestion-Pipeline sind **vollständig entkoppelt**.
>   Keine kennt die andere. Sie schreiben alle in dieselbe Zeile der
>   `szenen`-Tabelle, aber in **unterschiedliche Spalten**. Das ist ein
>   bewusstes Architektur-Design für Modularität, Testbarkeit und
>   Robustheit: wenn LLaMA3 ausfällt, funktioniert der Rest weiter. Die
>   eigentliche **Integration der PIL- und CLIP-Signale** geschieht erst
>   in Phase 3, im KI-Schnitt-Algorithmus, der beide Spalten zusammen
>   liest, um den narrativen Bogen zu konstruieren."*

### Mot-clé
**DECOUPLING PAR PERSISTANCE** (les stations communiquent via la DB, pas en mémoire)

---

## Station 8 — LLaMA3 via Ollama + httpx : le narrateur

### Histoire
Avant-dernière étape. Marc va voir **LLaMA3**, le narrateur local.

LLaMA3 est un **modèle de langage** de Meta (8 milliards de paramètres), qui
**tourne sur la machine de Marc** (pas dans le cloud). Pour parler à LLaMA3,
Marc passe par **Ollama**, un runtime local qui héberge le modèle et expose
une API HTTP sur le port `11434`.

Marc prend un téléphone HTTP — **httpx** — et appelle Ollama :

```http
POST http://localhost:11434/api/generate
{
  "model": "llama3",
  "prompt": "Du bist ein Filmanalyst. Beschreibe diese Szene in EINEM
    kurzen Satz auf Deutsch.\n\nSzene 1: 0.0s – 17.0s (Dauer: 17.0s)\n
    Dialog/Kommentar: \"A dangerous quest for a lone hunter...\"\n\n
    Beschreibung (1 Satz):",
  "options": { "temperature": 0.3, "num_predict": 80 }
}
```

LLaMA3 réfléchit (3-10 secondes) et répond :

> *« Die Szene eröffnet mit einer langen, stummen Einstellung auf eine
>   einsame Landschaft, während der Protagonist, ein isolierter Jäger,
>   seine Stimme hört, die von seiner Einsamkeit und dem Risiko seines
>   bevorstehenden Unternehmens kündigt. »*

Une phrase narrative en allemand qui décrit la scène, basée sur le **dialogue
transcrit + la durée** — pas sur l'image elle-même (LLaMA3 ne voit pas, il ne
lit que du texte).

Marc note cette description **par scène** et écrit : *« Étape 95 % — 1 Szene
beschrieben »*.

### ⚠️ Pourquoi httpx et pas requests ?

Les deux marchent. Mais :
- **httpx** a une API moderne (sync **et** async dans la même lib)
- Dans `backend/api/ai.py`, on fait des appels async vers Claude/OpenAI/Gemini
  → on utilise `httpx.AsyncClient()`
- Ici dans le worker (sync), on utilise `httpx.post(...)` (sync)
- **Une seule lib** pour les deux cas → plus simple à maintenir

### Sa famille (Ollama)
- Le serveur Ollama (binaire local sur :11434)
- L'API REST `POST /api/generate`
- Le modèle `llama3:latest` (4.7 GB téléchargé localement)
- Les options : `temperature`, `num_predict`

### Sa famille (httpx)
- `httpx.post(url, json=..., timeout=60.0)` — l'appel synchrone
- Pas de session, juste une requête one-shot

### De qui il dépend
- **Ollama** (serveur local séparé sur `:11434`)
- **httpx** (client HTTP Python)

### Qui l'utilise
- La fonction `schritt_szenen_beschreiben()` à
  [`ingest.py:543-611`](../../backend/workers/ingest.py#L543-L611)

---

### 🔬 Deep dive — Comment LLaMA3 écrit-il une description de scène ?

#### 1. La distinction Ollama / LLaMA3 : deux choses différentes

C'est un point qui prête à confusion. Décortiquons :

- **LLaMA3** = un **modèle** de langage. C'est un fichier de **4.7 GB** de
  poids neuronaux. Il a été entraîné par Meta sur des milliards de tokens
  de texte. Il existe en plusieurs tailles : 8B (8 milliards de
  paramètres), 70B, 405B. CinAssist utilise la version 8B par défaut.
- **Ollama** = un **runtime** (= environnement d'exécution) qui sait
  charger un modèle (LLaMA3, Mistral, Phi, etc.) et l'exposer via une **API
  HTTP**. C'est l'équivalent d'un « Docker pour LLM ». Sans Ollama,
  on devrait implémenter à la main le chargement du modèle, l'inférence,
  le tokenizing, etc.

Analogie :
- LLaMA3 = un **livre épais** rangé sur l'étagère
- Ollama = le **bibliothécaire** qui sait ouvrir le livre, le lire, et
  répondre aux questions des visiteurs

Quand Marc veut parler à LLaMA3, il **ne le contacte pas directement**. Il
appelle le bibliothécaire (Ollama) via HTTP, et Ollama consulte le livre
(LLaMA3) pour produire la réponse.

#### 2. Le rôle de httpx — le téléphone

Pour appeler Ollama, Marc utilise **httpx**, une bibliothèque Python qui
gère les requêtes HTTP. Le code exact :

```python
import httpx

response = httpx.post(
    f"{OLLAMA_BASE_URL}/api/generate",       # http://localhost:11434/api/generate
    json={
        "model": OLLAMA_MODEL,                # "llama3"
        "prompt": prompt,                     # le texte de demande
        "stream": False,                      # on attend la réponse complète
        "options": {
            "temperature": 0.3,
            "num_predict": 80,
        },
    },
    timeout=60.0,                             # max 60s par scène
)
```

**Pourquoi `stream=False` ?** Ollama peut envoyer la réponse mot par mot (en
streaming) ou d'un coup quand c'est fini. Pour notre cas (description courte
de 1-2 phrases), on attend qu'il finisse. Le streaming serait utile si on
affichait la réponse en direct au browser, mais ici on veut juste l'écrire
en base à la fin.

**Pourquoi `timeout=60.0` ?** LLaMA3 8B sur Mac M3 produit ~30 tokens/s, donc
~80 tokens en 3 secondes. 60s est une marge généreuse contre les blocages.

#### 3. Le prompt construit par CinAssist

Voici exactement ce que Marc envoie à LLaMA3 pour Sintel C
([ingest.py:566-573](../../backend/workers/ingest.py#L566-L573)) :

```
Du bist ein Filmanalyst. Beschreibe diese Szene in EINEM kurzen Satz auf Deutsch.

Szene 1: 0.0s – 17.0s (Dauer: 17.0s)
Dialog/Kommentar: "A dangerous quest for a lone hunter. I've been alone for as long as I can remember."

Beschreibung (1 Satz):
```

Décortiquons :

**Ligne 1 — Role prompting** : *« Du bist ein Filmanalyst »*. C'est une
technique connue de prompting qui **oriente** le modèle vers un style
spécifique. Plutôt que dire « décris cette scène », on dit « tu es un
analyste de film, décris cette scène ». LLaMA3 produit alors des phrases
plus professionnelles, avec du vocabulaire cinématographique.

**Ligne 2 — La consigne précise** : *« in EINEM kurzen Satz auf Deutsch »*.
Précise la langue (allemand) et la taille (une phrase). EINEM est en
majuscules pour insister.

**Ligne 3 — Les métadonnées factuelles** : numéro de scène, durée. Ça donne
un cadre numérique à LLaMA3.

**Ligne 4 — Le dialogue transcrit par Whisper** : c'est l'**information
clé**. Sans ça, LLaMA3 inventerait totalement.

**Ligne 5 — L'amorce** : *« Beschreibung (1 Satz): »*. C'est l'amorce qui
force LLaMA3 à enchaîner directement avec une description, sans
préambule (« voici une description... »).

#### 4. ⚠️ Point CRUCIAL : ce que LLaMA3 voit et ne voit PAS

**LLaMA3 NE VOIT PAS l'image de la scène.** Il ne reçoit que du **texte** :
le numéro, la durée, et le dialogue.

C'est une **limitation importante** à reconnaître. LLaMA3 « imagine » la
scène en se basant sur :
- Les mots du dialogue (« lone hunter », « alone », « quest »)
- Sa connaissance générale acquise à l'entraînement (« lone hunter » →
  paysage solitaire, chasseur, atmosphère sombre)
- Le contexte de durée (17s = scène d'introduction)

C'est pour ça que la description générée pour Sintel C ressemble à une
**interprétation littéraire** plutôt qu'à une description visuelle :

> *« Die Szene eröffnet mit einer langen, stummen Einstellung auf eine
>   einsame Landschaft, während der Protagonist, ein isolierter Jäger,
>   seine Stimme hört, die von seiner Einsamkeit und dem Risiko seines
>   bevorstehenden Unternehmens kündigt. »*

LLaMA3 invente *« lange, stumme Einstellung »* (« long plan silencieux ») —
il ne SAIT pas que c'est silencieux, il **suppose** parce que le dialogue
est un voice-over, donc on imagine du silence à l'écran. C'est plausible,
mais pas vérifié.

**Alternative qui verrait vraiment l'image** : un modèle **multimodal** comme
**LLaVA** (Liu et al., 2023) ou **BLIP-2** (Salesforce, 2023). Ces modèles
acceptent une image + un prompt. CinAssist ne les utilise pas pour deux
raisons :
1. **Simplicité d'intégration** : LLaMA3 via Ollama est ultra-simple
2. **Modèle plus petit, plus rapide** : LLaVA fait 13B (vs 8B pour LLaMA3)
   et nécessite plus de mémoire

C'est documenté comme limitation.

#### 5. Les options techniques : temperature et num_predict

Deux paramètres dans la requête Ollama qui méritent une explication :

##### Temperature = 0.3

La temperature contrôle le **degré de hasard** dans le choix des mots.

- temperature = 0.0 → **déterministe**, le modèle choisit toujours le
  token le plus probable. Très répétitif si on relance.
- temperature = 0.3 → **légèrement créatif**, mais reste focalisé. Bonne
  cohérence d'une scène à l'autre.
- temperature = 1.0 → **équilibré**, créatif et varié. Usage typique pour
  des conversations.
- temperature = 2.0+ → **chaotique**, le modèle prend des risques étranges.

**Pour CinAssist**, on veut des descriptions **factuellement stables** et
**peu fantaisistes** → 0.3 est le bon choix. On accepte une petite variété
pour ne pas avoir 50 fois le même style de phrase, mais on ne veut pas
d'invention farfelue.

##### num_predict = 80

C'est le **nombre maximum de tokens** que LLaMA3 produira pour la réponse.

Rappel : un token ≈ 0.75 mot en anglais, ~1 mot en allemand. 80 tokens ≈
60-80 mots en allemand, soit **1-2 phrases**. Largement assez pour notre
description de 1 phrase, sans risquer une réponse trop longue.

#### 6. Pourquoi local au lieu de cloud (Claude / GPT-4) ?

Très bonne question — surtout que CinAssist supporte Claude/OpenAI/Gemini
**en Phase 3** (KI-Schnitt). Alors pourquoi LLaMA3 en local pour Phase 2 ?

| Critère | LLaMA3 local | Cloud (Claude/GPT-4) |
|---|:-:|:-:|
| Coût par scène | gratuit | ~0.001 € à 0.01 € |
| Latence (1 scène) | 2-5 s | 1-3 s |
| Vie privée | aucune fuite | upload vers serveur tiers |
| Disponibilité | tant que Ollama tourne | dépend du service en ligne |
| Qualité de la prose | **moyenne** (8B) | **excellente** (Claude=175B+) |
| Nécessite une clé API | non | oui |

**Pour Phase 2** (description par scène), on traite **toutes les scènes de
toutes les vidéos**. Coût cumulé en cloud non négligeable, et données
sensibles (contenus de cinéma) qu'on ne veut peut-être pas envoyer ailleurs.
→ **Local fait sens**.

**Pour Phase 3** (KI-Schnitt = 1 décision par session utilisateur), on peut
se permettre Claude/GPT-4 si l'utilisateur a une clé. → **Cloud optionnel**.

C'est un **trade-off pertinent** à expliquer au prof : *« On utilise le
local pour les opérations massives et le cloud uniquement pour les
opérations critiques où la qualité prime sur le coût. »*

#### 7. La privacité comme argument cinéma

Le monde du cinéma a une **culture forte du secret** :
- Scripts non publiés
- Rushes non montés
- Acteurs sous NDA
- Productions confidentielles

Envoyer des contenus à OpenAI ou Anthropic peut être **inacceptable
légalement** pour certains studios. **Ollama + LLaMA3 en local** est un
argument **commercial fort** que tu peux mettre en avant dans ta thèse :

> *« CinAssist ist standardmäßig **100 % lokal**: keine Videodaten und
>   keine Transkriptionen verlassen die Maschine des Nutzers. Cloud-LLMs
>   sind optional und vom Nutzer aktiv zu konfigurieren. Dies adressiert
>   die Vertraulichkeitsanforderungen der Filmindustrie. »*

#### 8. Comment la description est utilisée plus tard (Phase 3)

La description écrite en base sert **deux usages** en Phase 3 :

**Usage 1 — Comme étiquette dans la timeline UI**
Quand l'algo de coupe produit un montage, chaque segment dans la timeline
affiche une mini-description (ex: *« Szene mit Landschaft und Jäger »*).
L'utilisateur peut survoler pour lire la description complète. C'est
l'**information éditoriale** pour reconnaître les scènes visuellement.

**Usage 2 — Pour le LLM de raffinement narratif**
L'algo de coupe utilise un LLM (Claude/GPT/Ollama) en bout de chaîne pour
**réordonner** les scènes selon la cohérence narrative. Ce LLM reçoit la
**liste des descriptions** comme entrée :

```
Scène 0: "Die Szene eröffnet mit einer langen, stummen Einstellung..."
Scène 1: "Ein dramatischer Kampf in einer eisigen Höhle..."
Scène 2: "Der Jäger reflektiert in einer stillen Nacht..."
...
```

Et il propose un ordre cohérent (par exemple Scène 0 en ouverture, Scène 1
en climax, Scène 2 en cloture). **Sans les descriptions LLaMA3, ce LLM de
raffinement n'aurait pas de signal narratif.**

C'est ici que LLaMA3 prend tout son sens : il **traduit le contenu visuel
en texte**, pour qu'un LLM puisse raisonner dessus.

#### 9. Limitations à reconnaître pour la défense

| Limitation | Réponse défensive |
|---|---|
| LLaMA3 ne voit pas l'image, il invente à partir du dialogue | Documenté ; pour une v2, utiliser BLIP-2 ou LLaVA (multimodal). |
| 8B paramètres c'est petit (vs GPT-4 ≈ 1.7T) | Trade-off délibéré pour la performance locale ; les descriptions servent juste de signal narratif, pas de chef-d'œuvre littéraire. |
| Risque d'hallucinations (invente des détails) | Acceptable car les descriptions sont une **aide à la décision**, pas une vérité absolue. Le contrôle final reste à l'éditeur humain. |
| Modèle figé (snapshot d'avril 2024) | Pas un problème pour notre cas d'usage. |

#### 10. Phrases à dire au prof

**En français (pour comprendre)** :

> *« Pour générer une description de scène, j'envoie via httpx un prompt
>   structuré à Ollama, qui exécute LLaMA3 localement sur la machine. Le
>   prompt utilise la technique du « role prompting » (« Du bist ein
>   Filmanalyst ») pour orienter le modèle vers un style cinématographique,
>   et inclut le dialogue transcrit par Whisper. **Point important** :
>   LLaMA3 ne voit pas l'image, il infère la description à partir du
>   dialogue et des métadonnées. Pour une version multimodale, BLIP-2 ou
>   LLaVA seraient des choix naturels. J'utilise temperature=0.3 pour
>   limiter le hasard et num_predict=80 pour limiter la longueur. Le choix
>   du local est délibéré pour la confidentialité — un argument pertinent
>   dans le contexte cinéma. »*

**En allemand (pour la défense)** :

> *„Für die Szenenbeschreibung sende ich via httpx einen strukturierten
>   Prompt an Ollama, das LLaMA3 **lokal auf der Maschine** ausführt. Der
>   Prompt nutzt **Role-Prompting** (« Du bist ein Filmanalyst »), um das
>   Modell zu einem filmischen Stil zu lenken, und enthält den von Whisper
>   transkribierten Dialog. **Wichtig**: LLaMA3 sieht das Bild nicht, es
>   leitet die Beschreibung aus dem Dialog und den Metadaten ab. Für eine
>   multimodale Version wären BLIP-2 oder LLaVA naheliegende Wahlmöglich-
>   keiten — eine dokumentierte Limitation. Die Wahl des **lokalen
>   Modells** ist bewusst getroffen für **Vertraulichkeit** — ein
>   relevantes Argument im Filmkontext."*

#### 11. Récapitulatif visuel

```
Scène (start=0s, end=17s)
        │
        ▼
Marc construit un prompt en allemand :
   "Du bist ein Filmanalyst. Beschreibe diese Szene in EINEM Satz...
    Dialog: 'A dangerous quest for a lone hunter...'
    Beschreibung (1 Satz):"
        │
        ▼
httpx.post(
   "http://localhost:11434/api/generate",
   json={ model: "llama3", prompt: ..., options: {temperature: 0.3} }
)
        │
        ▼
Ollama charge LLaMA3 (s'il n'est pas déjà en RAM)
        │
        ▼
LLaMA3 produit du texte (token par token, ~30 t/s sur M3)
        │
        ▼
Réponse JSON : {"response": "Die Szene eröffnet mit einer langen..."}
        │
        ▼
Marc extrait la chaîne et la sauvegarde dans szene.beschreibung
        │
        ▼  (utilisé plus tard en Phase 3)
        │
LLM de raffinement narratif (Claude/GPT/Ollama) reçoit la liste de
toutes les beschreibungen et propose un ordre cohérent pour le montage
```

### Mot-clé
**LLM LOCAL POUR DESCRIPTION DE SCÈNE**

---

## Station 9 — SQLAlchemy + psycopg2 : Marie en mode sync

### Histoire
Marc a fini ses analyses. Il a maintenant **pour chaque scène** :

- Numéro de scène, start, end, dauer
- Path du thumbnail
- Embedding CLIP 512-dim
- Description LLaMA3
- Transcription text + JSON (mots avec timestamps)
- Analyse visuelle (luminance, contraste, mouvement, schärfe, énergie)

Il va voir **Marie l'archiviste** (qu'on connaît déjà depuis Phase 1). Mais
cette fois, **Marie est en mode sync** : elle utilise son téléphone à fil
(**psycopg2-binary**) au lieu d'asyncpg, parce que Marc est dans un **autre
processus que Pierre** (Celery worker, pas FastAPI).

Marie crée une ligne `Szene` par scène, l'insère dans la table `szenen` avec
la clé étrangère vers le `clip_id`. Pour Sintel C : **1 ligne dans szenen**.

Marc fait aussi deux UPDATE finaux :
- `clip.status = "analysiert"` (avant : `"hochgeladen"`)
- `job.status = "fertig"`, `job.fortschritt = 100`

Marc écrit : *« Étape 100 % — Analyse abgeschlossen »*. 🎉

Côté browser, le client voit la modal se fermer et le clip apparaître avec
le badge **KI ✓**.

### Sa famille (Phase 2 spécifique)
- `Szene(clip_id=..., szenen_nr=..., start_zeit=..., ...,
  clip_embedding=[512 floats], analyse_visuelle={...JSON...},
  beschreibung="...", transkription="...", transkription_json=[...])`
- `db.add(szene)` × N (une fois par scène)
- `db.commit()` : commit final
- Update sync : `clip.status = "analysiert"`, `job.status = "fertig"`

### De qui elle dépend
- **psycopg2-binary** (driver sync)
- **SQLAlchemy** (déjà introduit en Phase 1)

### Qui l'utilise
- La boucle de sauvegarde finale dans `ingestion_pipeline()` à
  [`ingest.py:692-720`](../../backend/workers/ingest.py#L692-L720)

### Mot-clé
**PERSISTANCE FINALE (mode sync)**

---

## Le ballet du tableau d'affichage (Redis Pub/Sub)

Pendant **toutes** ces 9 étapes, Marc griffonne sur le tableau d'affichage
Redis. Chaque ligne est lue par websockets et **poussée au browser** en temps
réel :

```
Étape  2 %  Metadaten werden gelesen...
Étape  3 %  ✓ Metadaten gelesen          (17s, 1920×1080, 24fps, h264)
Étape  4 %  ✓ Proxy bereit                (0.68 MB)
Étape  5 %  Audio wird extrahiert...
Étape 10 %  ✓ Audio extrahiert            (530 KB, 16 kHz mono)
Étape 15 %  Transkription läuft...
Étape 30 %  ✓ Transkription fertig        (17 Wörter, 2 Segmente)
Étape 35 %  Szenenerkennung läuft...
Étape 50 %  ✓ 1 Szene erkannt             (threshold 27, algo HSV)
Étape 52 %  Visuelle Analyse läuft...
Étape 54 %  ✓ 1 Szene analysiert          (energie 0.331)
Étape 55 %  Visuelle Embeddings...
Étape 75 %  ✓ 1 Embedding erstellt        (512-dim, ViT-B/32)
Étape 80 %  Szenen werden beschrieben...
Étape 95 %  ✓ 1 Szene beschrieben         (modèle: llama3)
Étape 97 %  Ergebnisse werden gespeichert
Étape 99 %  ✓ 1 Szene in PostgreSQL       (clips UPDATE + szenen INSERT)
Étape 100 % ✓ Analyse abgeschlossen
```

C'est ce qui fait la **barre de progression** et les **9 étapes vertes** dans
ton modal `KI-Analyse läuft`.

---

## La règle d'or de Phase 2

Marc travaille **séquentiellement** (étape par étape, pas en parallèle),
parce que :

1. Chaque étape **dépend des données** de l'étape précédente
2. **PySceneDetect** doit produire la liste des scènes **avant** que PIL,
   CLIP, LLaMA3 puissent analyser scène par scène
3. **L'audio** doit être extrait avant que Whisper puisse le transcrire
4. Le tout doit être **traçable** : on sait exactement où on en est à tout
   moment

**Marc bosse seul, lentement mais sûrement.** Pendant qu'il bosse, Pierre
continue d'accueillir les autres clients à la réception — Marc n'est pas
dans le chemin critique de l'accueil.

---

## Tableau de correspondance final

| Étape | Bibliothèque(s)           | Type de spécialiste            | Output                              |
|:-----:|----------------------------|--------------------------------|-------------------------------------|
| 1     | ffprobe (FFmpeg)           | Contrôleur métadata            | dauer, aufloesung, codec, bildrate  |
| 2     | FFmpeg                     | Couteau suisse vidéo           | proxy.mp4 (960p)                    |
| 3     | FFmpeg                     | Extracteur audio               | audio.wav (16 kHz mono)             |
| 4     | mlx-whisper                | Sténographe                    | transcription + timestamps mot      |
| 5     | PySceneDetect              | Veilleur visuel                | liste (start, end) par scène        |
| 5b    | OpenCV + NumPy             | Lunettes + calculateur         | (indirect, support de PySceneDetect)|
| 6     | PIL                        | Loupe-photomètre               | metrics par scène                   |
| 7     | open-clip + PyTorch        | Critique d'art                 | embedding 512-dim par scène         |
| 8     | LLaMA3 (Ollama) + httpx    | Narrateur                      | description en 1 phrase par scène   |
| 9     | SQLAlchemy + psycopg2      | Archiviste sync                | INSERT szenen + UPDATE clip/job     |

---

## Une dernière subtilité technique

### Pourquoi `--pool=solo` pour le worker Celery ?

Quand on lance le worker normalement (`--pool=prefork`), Celery **fait un
fork** du processus pour chaque tâche. Sur Mac avec PyTorch + Metal, le fork
**casse** parce que Metal ne supporte pas le partage de contexte entre
processus parents et enfants. **SIGABRT, le worker meurt.**

Solution : `--pool=solo` = Marc travaille **dans le même processus** Celery,
sans fork. C'est moins parallèle (une tâche à la fois), mais ça marche.

### Phrase à dire au prof (allemand)

> *„Apple Metal kann nicht über `fork()` hinaus geteilt werden, deshalb läuft
> der Celery-Worker im `solo`-Modus. Das bedeutet, alle Tasks werden
> sequentiell im Hauptprozess ausgeführt — eine bewusste Trade-off-Entscheidung
> zugunsten der GPU-Stabilität."*

### Traduction française

> *« Apple Metal ne peut pas être partagé au-delà d'un `fork()`, c'est
> pourquoi le worker Celery tourne en mode `solo`. Cela signifie que toutes
> les tâches s'exécutent séquentiellement dans le processus principal — un
> compromis délibéré au profit de la stabilité du GPU. »*

---

## Et après Phase 2 ?

Le clip est maintenant **analysé**. Tu peux le voir avec son badge **KI ✓**
dans la sidebar. Toutes les scènes sont en base avec leurs métadonnées
complètes.

**La prochaine étape** (Phase 3) : quand l'utilisateur clique « KI-Schnitt »,
l'algorithme va prendre **toutes les scènes de tous les clips sélectionnés**
et **construire un montage** selon un arc narratif (pyramide de Freytag) en
utilisant Beam Search + similarité CLIP + classification A-Roll/B-Roll +
LLM optionnel.

C'est là que la "magie" arrive. Mais sans Phase 2 (toute la data extraite
ici), Phase 3 n'aurait **rien à manipuler**.

---

*Document de compréhension personnelle pour la Bachelorarbeit CinAssist.*
*Mise à jour : 2026-05-21.*
