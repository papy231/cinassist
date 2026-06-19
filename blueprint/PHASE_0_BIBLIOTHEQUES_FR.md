# Phase 0 — Inventaire des bibliothèques utilisées

> Vue d'ensemble de **toutes** les bibliothèques open source utilisées dans
> CinAssist, avec leur justification, les éléments concrètement employés, et
> les raisons du choix.

Ce document est la **base** des phases spécifiques (1 — Upload, 2 — Ingestion,
3 — Coupe IA, 4 — Export). Chaque phase fait référence à des bibliothèques qui
sont définies ici avec leur rôle et leur licence.

> **Version française** pour ma compréhension personnelle.
> La version pour le professeur est dans [PHASE_0_BIBLIOTHEKEN.md](PHASE_0_BIBLIOTHEKEN.md) (en allemand).

---

## Sommaire

- A — Infrastructure backend (Web, base de données, tâches)
- B — Médias & IA du backend (vidéo, audio, vision, langage)
- C — Frontend (UI dans le navigateur)
- D — Tableau récapitulatif

---

## A — Infrastructure backend

### A.1 FastAPI (0.115) · MIT

**Définition.** Framework Python moderne pour construire des APIs REST, async-natif,
basé sur les annotations de type.

**Pourquoi CinAssist l'utilise.** Il combine trois qualités dont le projet a
besoin : traitement asynchrone (pour que les appels longs à Whisper et CLIP ne
bloquent pas le thread principal), validation automatique des entrées via
Pydantic, et un système WebSocket intégré pour l'affichage du progrès en temps
réel.

**Éléments concrètement utilisés.**
- `APIRouter`, décorateurs `@router.post(...)`, `@router.get(...)`
- `UploadFile`, `File(...)`, `Form(...)` pour les uploads multipart
- `Depends(get_db)` pour la gestion des sessions de base de données
- `WebSocket` pour la route de progrès des jobs
- `HTTPException` pour les réponses d'erreur
- `CORSMiddleware`, `StaticFiles` (dans `backend/main.py`)

**Sans elle**, il faudrait implémenter manuellement le routage, le parsing du
corps des requêtes et la documentation OpenAPI.

---

### A.2 uvicorn (0.30) · BSD-3

**Définition.** Serveur ASGI haute performance pour Python. ASGI = le standard
async qui remplace WSGI.

**Pourquoi CinAssist l'utilise.** FastAPI n'est qu'une bibliothèque applicative ;
elle a besoin d'un serveur qui réceptionne les bytes HTTP depuis le réseau.
uvicorn est le standard dans l'écosystème async Python.

**Éléments concrètement utilisés.**
- La commande CLI `uvicorn backend.main:app --port 8001 --reload`
- Le mode `--reload` pour le rechargement automatique en développement
- Le support WebSocket intégré (sinon il faudrait ajouter `wsproto` séparément)

**Sans elle**, FastAPI ne pourrait pas écouter sur un port réseau.

---

### A.3 Pydantic (2.x) · MIT

**Définition.** Bibliothèque de validation de données via les annotations de
type Python.

**Pourquoi CinAssist l'utilise.** Elle est intégrée à FastAPI et garantit que
toutes les entrées du navigateur sont typées et validées **dès l'étape de
routage** — sans code de vérification manuel.

**Éléments concrètement utilisés.**
- Classes `BaseModel` pour les corps de requête complexes (ex. `AiCutRequest`
  dans `backend/api/ai.py:124`)
- `Field(...)` avec contraintes (`ge=0.0`, `le=1.0`) pour la validation
- Sérialisation automatique des dictionnaires de réponse en JSON

**Sans elle**, chaque vérification de type (`isinstance(...)`, intervalle de
valeurs) devrait être écrite à la main.

---

### A.4 python-multipart (0.0.9) · Apache 2

**Définition.** Implémentation de l'encodage HTTP `multipart/form-data` pour
l'upload de fichiers.

**Pourquoi CinAssist l'utilise.** Nécessaire pour que l'endpoint d'upload
(`POST /api/clips/upload`) puisse extraire un vrai fichier depuis le corps HTTP.
Utilisée implicitement par FastAPI dès qu'on déclare `UploadFile = File(...)`.

**Éléments concrètement utilisés.**
- Implicitement via l'annotation FastAPI `datei: UploadFile = File(...)`
- Lecture en streaming du contenu de la datei via `await datei.read(1024 * 1024)`

**Sans elle**, les uploads de fichiers seraient impossibles.

---

### A.5 SQLAlchemy (2.0) · MIT

**Définition.** L'ORM (Object-Relational Mapper) standard de facto pour Python.

**Pourquoi CinAssist l'utilise.** Les données de CinAssist sont fortement
relationnelles (Clip ↔ Szenen ↔ Jobs), ce qui impose une base de données
relationnelle. SQLAlchemy traduit les classes Python en tables SQL et protège
contre les injections SQL.

**Éléments concrètement utilisés.**
- `DeclarativeBase` comme classe de base des modèles
- Modèles `Clip`, `Szene`, `Job`, `Timeline` (voir `backend/core/database.py`)
- Types de colonnes : `String`, `Integer`, `Float`, `Text`, `DateTime`,
  `Boolean`, `JSON`, `ARRAY(Float)`, `UUID`
- Relations : `relationship(...)` avec `cascade="all, delete-orphan"`
- Session async via `AsyncSession`, `async_sessionmaker`, `select(...)`,
  `await db.commit()`
- Session sync via `sessionmaker(bind=sync_engine)` pour le worker Celery

**Sans elle**, il faudrait construire du SQL en chaînes de caractères — sujet
aux erreurs et insécurisé.

---

### A.6 asyncpg (0.29) · Apache 2 — et — psycopg2-binary (2.9) · LGPL

**Définition.** Deux drivers PostgreSQL. **asyncpg** est nativement asynchrone
et très rapide ; **psycopg2** est le driver classique synchrone.

**Pourquoi CinAssist utilise les deux.** FastAPI a besoin d'un driver async
pour que les requêtes DB ne bloquent pas l'event-loop. Le worker Celery, lui,
travaille **en mode synchrone**, parce qu'il dispose de sa propre abstraction
de processus. Les deux sont utilisés en parallèle.

**Éléments concrètement utilisés.**
- asyncpg : implicitement via `DATABASE_URL = "postgresql+asyncpg://..."`
- psycopg2-binary : implicitement via `DATABASE_URL_SYNC = "postgresql://..."`
- Deux engines séparés dans `backend/core/database.py:17-22`

**Sans eux**, aucune communication avec Postgres ne serait possible.

---

### A.7 PostgreSQL (16) · Licence PostgreSQL

**Définition.** Système de gestion de base de données relationnelle mature,
open source, avec garanties ACID.

**Pourquoi CinAssist l'utilise.** Trois exigences spécifiques qui ne sont pas
remplies par SQLite ou d'autres bases :
1. **Colonnes JSON natives** — pour `szenen.analyse_visuelle` et
   `szenen.transkription_json`
2. **Colonnes ARRAY natives** — pour `szenen.clip_embedding` (vecteur Float
   512-dim)
3. **CASCADE DELETE robuste** — quand on supprime un clip, ses scènes
   disparaissent automatiquement

**Éléments concrètement utilisés.**
- 4 tables : `clips`, `szenen`, `jobs`, `timelines`
- Type JSON dans `szenen.analyse_visuelle`, `szenen.transkription_json`
- Type ARRAY dans `szenen.clip_embedding`
- Clé étrangère `clips.id → szenen.clip_id` avec `ondelete="CASCADE"`

**Sans elle**, les embeddings devraient être stockés en chaîne ou en fichier
externe, ce qui complique les requêtes et les jointures.

---

### A.8 Celery (5.4) · BSD-3

**Définition.** Le framework standard pour les tâches en arrière-plan
distribuées en Python.

**Pourquoi CinAssist l'utilise.** Une analyse vidéo complète prend plusieurs
minutes — beaucoup trop long pour une requête HTTP. Celery permet d'externaliser
la tâche dans un autre processus tout en gardant un `task_id` pour la suivre.

**Éléments concrètement utilisés.**
- Instance `Celery(...)` dans `backend/core/celery_app.py`
- Décorateur `@celery_app.task(bind=True, name="cinassist.ingest")` sur
  `ingestion_pipeline` (`backend/workers/ingest.py:617`) et
  `export_video_task` (`backend/workers/export.py`)
- `.delay(*args)` pour envoyer une tâche sans attendre le résultat
- `--pool=solo` comme mode d'exécution (important : pas de fork, car PyTorch
  + Metal sur macOS ne survivent pas au fork)

**Sans elle**, il faudrait implémenter une file de tâches maison, ou faire
l'analyse de manière synchrone dans la réponse HTTP — ce qui provoquerait des
timeouts.

---

### A.9 Redis (7) + redis-py (5.0) · BSD-3 / MIT

**Définition.** Redis est une base de données en mémoire. redis-py est le
client Python.

**Pourquoi CinAssist l'utilise.** Double rôle :
1. **Broker pour Celery** — la file d'attente où FastAPI dépose les tâches et
   où le worker les récupère.
2. **Canal Pub/Sub** — le worker publie des messages de progrès sur un canal
   par job ; la route WebSocket s'y abonne et forwarde au navigateur.

**Éléments concrètement utilisés.**
- Serveur Redis sur `localhost:6379`
- Client redis-py via `redis.from_url(...)`, `.publish(channel, json_payload)`
  (`backend/workers/ingest.py:59-66`)
- Abonné WebSocket dans `backend/api/websocket.py`

**Sans lui**, Celery n'aurait pas de broker ; et il n'y aurait pas de canal
Pub/Sub rapide pour les mises à jour live.

---

### A.10 websockets (12.0) · BSD-3

**Définition.** Bibliothèque Python qui implémente le protocole WebSocket
(RFC 6455).

**Pourquoi CinAssist l'utilise.** HTTP est requête/réponse — le serveur ne
peut pas envoyer un message de lui-même. Pour le suivi en direct durant les
minutes d'analyse, il faut une connexion bidirectionnelle ouverte.

**Éléments concrètement utilisés.**
- Implicitement via le `WebSocket` de FastAPI dans `backend/api/websocket.py`
- Pings/Heartbeat via `WS_PING_INTERVAL = 20` dans `backend/core/config.py:52`

**Sans elle**, le navigateur devrait poller l'état du job toutes les quelques
secondes — plus de latence et plus de trafic réseau.

---

## B — Médias & IA du backend

### B.1 FFmpeg + ffprobe · LGPL / GPL

**Définition.** L'outil open source universel pour le traitement audio et
vidéo. ffprobe est son équivalent pour l'inspection.

**Pourquoi CinAssist l'utilise.** C'est le gold standard pour tout ce qui
concerne les pixels et les samples PCM : lire les métadonnées, extraire l'audio,
créer les vignettes, encoder les proxies, exporter les vidéos finales. Aucune
alternative ne couvre cette largeur.

**Éléments concrètement utilisés** (tous via `subprocess.run(...)`) :
- `ffprobe` avec `-show_format -show_streams` — métadonnées en JSON
  (`backend/workers/ingest.py:73-79`)
- `ffmpeg -i ... -vn -acodec pcm_s16le -ar 16000 -ac 1` — extraction audio
  en WAV 16 kHz Mono (`ingest.py:115-122`)
- `ffmpeg -ss ... -frames:v 1 -q:v 3 -vf scale=...` — extraction de frame
  unique pour les vignettes (`ingest.py:223-232`) et les frames CLIP
  (`ingest.py:507-515`)
- `ffmpeg ... -c:v libx264 -preset fast -crf 26 -c:a aac` — création du
  proxy en 960p H.264 (`ingest.py:656-663`)
- `ffmpeg ... -filter_complex xfade=...` — transitions pour l'export final
  (`backend/workers/export.py`)

**Sans lui**, aucune opération vidéo ne serait possible.

---

### B.2 mlx-whisper (0.4) · MIT

**Définition.** Variante optimisée Apple-MLX de OpenAI Whisper, la
reconnaissance vocale state-of-the-art.

**Pourquoi CinAssist l'utilise.** Whisper fournit des transcriptions avec
**timestamps mot-par-mot**, ce qui est essentiel pour le montage audio-aware
(voir Phase Coupe IA : les points de coupe sont alignés sur les pauses de
parole). La variante MLX exploite le Neural Engine des Apple Silicon (M1/M2/M3)
et est environ **3× plus rapide** que la variante PyTorch standard.

**Éléments concrètement utilisés.**
- `mlx_whisper.transcribe(audio_path, path_or_hf_repo=..., language="de",
  word_timestamps=True)` (`ingest.py:152-157`)
- Modèle : `mlx-community/whisper-large-v3-turbo`
- Exploitation : `result["segments"]` avec `start`, `end`, `text`, `words`
  (chaque mot avec ses timestamps)

**Sans lui**, aucune transcription possible ; la classification A-Roll/B-Roll
ne fonctionnerait pas.

---

### B.3 PySceneDetect (0.6) · MIT

**Définition.** Bibliothèque Python pour la détection de changements de scène
dans les fichiers vidéo.

**Pourquoi CinAssist l'utilise.** Toute l'analyse fonctionne **par scène** —
les frames sont extraites par scène, les embeddings CLIP sont calculés par
scène, LLaMA3 décrit chaque scène. La détection des scènes est donc la base
de toute la pipeline.

**Éléments concrètement utilisés.**
- `open_video(video_pfad)` pour ouvrir
- `SceneManager()` comme orchestrateur
- `ContentDetector(threshold=27.0)` comme algorithme concret (basé sur la
  différence HSV)
- `detect_scenes(video)` pour lancer
- `get_scene_list()` pour récupérer la liste des coupures

**Sans lui**, il faudrait implémenter la détection de scènes à la main, ou
traiter toute la vidéo comme une scène unique.

---

### B.4 OpenCV — opencv-python-headless (4.10) · Apache 2

**Définition.** La bibliothèque standard de Computer Vision pour Python et C++.
La variante `headless` ne dépend pas du GUI (pas de GTK, pas de Qt), idéale en
contexte serveur.

**Pourquoi CinAssist l'utilise.** Utilisée en interne par PySceneDetect pour
décoder les frames vidéo. CinAssist lui-même n'utilise pas directement OpenCV
parce que les opérations PIL plus simples suffisent.

**Éléments concrètement utilisés.** Uniquement indirectement via PySceneDetect.

**Sans elle**, PySceneDetect ne fonctionnerait pas.

---

### B.5 NumPy (1.26) · BSD-3

**Définition.** La bibliothèque fondamentale pour les arrays n-dimensionnels
et les opérations mathématiques en Python.

**Pourquoi CinAssist l'utilise.** Utilisée en interne par OpenCV, PySceneDetect
et PyTorch. Utilisation directe dans CinAssist minimale — les opérations sur
listes suffisent généralement.

**Éléments concrètement utilisés.** Surtout comme dépendance d'OpenCV, PIL,
PyTorch.

**Sans elle**, la plupart des autres bibliothèques ML ne fonctionneraient pas.

---

### B.6 PIL / Pillow (10.4) · HPND

**Définition.** Bibliothèque Python standard pour la manipulation d'images.
Pillow est le fork activement maintenu du PIL original.

**Pourquoi CinAssist l'utilise.** Choisi délibérément **à la place d'OpenCV**
pour l'analyse visuelle, parce que les opérations (luminance, contraste,
mouvement, netteté) se réduisent à de simples statistiques de pixels. PIL est
plus léger et n'a pas besoin de bibliothèques natives supplémentaires.

**Éléments concrètement utilisés.**
- `Image.open(path).convert("RGB")` pour charger une frame extraite
- `Image.resize((64, 64))` et `Image.resize((32, 32))` pour le downsampling
- `Image.getdata()` pour lire les tuples RGB
- Implémentation maison de la variance Laplacienne pour la métrique de
  netteté (`ingest.py:332-359`)
- Implémentation maison de la différence moyenne de pixels pour la métrique
  de mouvement (`ingest.py:321-329`)

**Sans elle**, il faudrait intégrer OpenCV avec plus de complexité.

---

### B.7 open-clip-torch (2.26) · MIT

**Définition.** Une réimplémentation open source du modèle CLIP d'OpenAI
(Contrastive Language-Image Pre-training) pour PyTorch.

**Pourquoi CinAssist l'utilise.** Les embeddings CLIP fournissent une
représentation sémantique 512-dim de chaque frame. Ils permettent de mesurer
la **diversité visuelle** entre scènes via similarité cosinus — une entrée
centrale de l'algorithme de coupe par Beam Search.

**Éléments concrètement utilisés.**
- `open_clip.create_model_and_transforms("ViT-B/32", pretrained="openai")`
  (`ingest.py:497`)
- `preprocess(image)` pour la normalisation de la frame d'entrée
- `model.encode_image(image)` pour générer le vecteur 512-dim
- Normalisation L2 du vecteur pour les comparaisons cosinus

**Sans elle**, la comparaison sémantique entre scènes serait perdue.

---

### B.8 PyTorch (2.4) + torchvision (0.19) · BSD-3

**Définition.** Le framework de deep learning développé par Meta. torchvision
fournit les extensions spécifiques à la vision.

**Pourquoi CinAssist l'utilise.** Nécessaire comme backend pour open-clip-torch.
De plus, l'accélération Apple MPS (Metal Performance Shaders) est exploitée,
donc l'inférence CLIP tourne sur le GPU d'un Mac Apple Silicon.

**Éléments concrètement utilisés.**
- `torch.backends.mps.is_available()` pour le choix du périphérique
  (`ingest.py:496`)
- `torch.no_grad()` comme context manager pour l'inférence
- Opérations sur tenseurs `embedding / embedding.norm(dim=-1, keepdim=True)`

**Sans lui**, CLIP ne fonctionnerait pas.

---

### B.9 httpx (0.27) · BSD-3

**Définition.** Un client HTTP Python moderne avec API sync et async
(successeur de `requests` avec support async).

**Pourquoi CinAssist l'utilise.** Nécessaire pour parler à Ollama (LLM local)
et optionnellement à Claude/OpenAI/Gemini (LLMs cloud). L'API async s'intègre
au stack FastAPI.

**Éléments concrètement utilisés.**
- `httpx.post(...)` (synchrone) dans le worker Celery pour Ollama
  (`ingest.py:576-588`)
- `httpx.AsyncClient()` (async) dans `backend/api/ai.py` pour tous les
  providers cloud (Claude, OpenAI, Gemini)

**Sans elle**, il faudrait combiner `requests` pour le sync et `aiohttp`
pour l'async.

---

### B.10 anthropic SDK (≥0.40) · MIT

**Définition.** Le SDK Python officiel pour la famille Claude d'Anthropic.

**Pourquoi CinAssist l'utilise.** Optionnellement comme provider haut de gamme
pour la verfeinement de l'algorithme de coupe IA. Si une `CLAUDE_API_KEY` est
définie, le système passe la séquence Beam Search à Claude pour optimisation
narrative.

**Éléments concrètement utilisés.** En réalité, Claude est appelé **directement
via httpx** plutôt que via le SDK (`backend/api/ai.py:953-984`), pour garder
la dépendance petite et avoir le contrôle complet de la couche HTTP. Le SDK
est seulement présent comme import optionnel.

**Sans elle**, l'option Claude disparaît ; Ollama reste comme fallback.

---

### B.11 openai SDK (≥1.50) · Apache 2

**Définition.** Le SDK Python officiel pour l'API OpenAI (GPT-4o, o1-preview).

**Pourquoi CinAssist l'utilise.** Même idée qu'Anthropic : GPT-4o comme
provider optionnel de verfeinement.

**Éléments concrètement utilisés.** Comme pour Anthropic : l'appel se fait
directement via httpx (`backend/api/ai.py:987-1026`). Le SDK est seulement
installé en option.

**Sans elle**, l'option OpenAI disparaît.

---

### B.12 Ollama + LLaMA3 · MIT (Ollama) / Llama Community License

**Définition.** Ollama est un runtime LLM local qui exécute des modèles comme
LLaMA3, Mistral, Phi sur la machine. LLaMA3 est le modèle à 8 milliards de
paramètres publié par Meta.

**Pourquoi CinAssist l'utilise.** Exécution locale sans coûts d'API et sans
que le contenu ne quitte le système. Utilisé pour deux choses :
1. **Descriptions de scènes** dans la pipeline d'ingestion (une phrase
   allemande par scène).
2. **Provider de fallback** pour la coupe IA, quand aucune clé d'API cloud
   n'est configurée.

**Éléments concrètement utilisés.**
- API HTTP Ollama sur `localhost:11434`
- Endpoint `POST /api/generate` avec payload JSON `{model: "llama3", prompt,
  stream: false, options: {temperature, num_predict}}`
- Modèle `llama3:latest` (4.7 GB local)

**Sans lui**, aucune pipeline 100 % locale ne serait possible ; il faudrait
dépendre des LLMs cloud.

---

## C — Frontend

### C.1 Next.js (16.2.3) + React (19.2.4) · MIT

**Définition.** Next.js est un framework React avec rendu côté serveur,
App-Router et Turbopack comme système de build. React est la bibliothèque UI
sous-jacente.

**Pourquoi CinAssist l'utilise.** Stack standard pour les UIs web modernes ;
s'adapte au style async du backend FastAPI et supporte l'organisation
orientée-composants du code (chaque élément UI est un fichier).

**Éléments concrètement utilisés.**
- App-Router (`src/app/editor/page.tsx`, `src/app/project/[id]/page.tsx`)
- Client-Components avec la directive `"use client"`
- Hooks : `useState`, `useEffect`, `useRef`, `useCallback`
- Redirections côté serveur via `next.config.ts`

---

### C.2 TypeScript (5.x) · Apache 2

**Définition.** JavaScript typé statiquement.

**Pourquoi CinAssist l'utilise.** Détection précoce des erreurs lors des
appels au backend, types clairs pour les messages WebSocket, auto-complétion
en IDE.

**Éléments concrètement utilisés.**
- Définitions de types pour la couche API (`src/lib/api.ts`)
- Interfaces pour `ClipDTO`, `JobUpdate`, `PipelineBericht`, `SzeneDetail`
- Generics dans les stores Zustand

---

### C.3 Zustand (5.0) · MIT

**Définition.** Bibliothèque légère de state-management pour React (une
alternative simplifiée à Redux).

**Pourquoi CinAssist l'utilise.** L'interface éditeur partage un état global
entre de nombreux composants (liste des clips, jobs actifs, segments de
timeline, pile undo/redo). Zustand le fait sans wrapper Provider.

**Éléments concrètement utilisés.**
- `useEditorStore` (`src/stores/editorStore.ts`)
- `useTimelineStore` (`src/stores/timelineStore.ts`)
- Sélecteurs, pattern `set`/`get`

---

### C.4 Tailwind CSS (4) · MIT

**Définition.** Framework CSS Utility-First — des classes comme `flex`,
`gap-4`, `bg-blue-500` sont utilisées directement dans le JSX.

**Pourquoi CinAssist l'utilise.** Itération rapide du design sans fichiers CSS
séparés. Cohérence via les design-tokens (`var(--bg1)`, `var(--text)`).

**Éléments concrètement utilisés.** Les inline-styles dominent dans CinAssist ;
Tailwind est surtout utilisé pour les variables CSS globales
(`src/app/globals.css`).

---

### C.5 Framer Motion (12) · MIT

**Définition.** Bibliothèque d'animations pour React.

**Pourquoi CinAssist l'utilise.** Pour des transitions fluides à l'ouverture
de panneaux, à l'affichage de modaux, lors du drag-and-drop.

**Éléments concrètement utilisés.** Classes d'animation et composants
`motion` dans l'UI éditeur.

---

### C.6 Lucide React (1.8) · ISC

**Définition.** Bibliothèque d'icônes SVG avec ~1 000 symboles cohérents.

**Pourquoi CinAssist l'utilise.** Icônes cohérentes dans l'UI sans avoir à
créer ses propres SVG.

**Éléments concrètement utilisés.** Icônes comme Upload, Trash, Plus,
Play/Pause sont importées et utilisées dans les composants éditeur.

---

### C.7 clsx (2.1) · MIT

**Définition.** Petit utilitaire pour composer conditionnellement des classes
CSS.

**Pourquoi CinAssist l'utilise.** Simplifie un code comme
`className={clsx("base", isActive && "active", isDisabled && "disabled")}`.

**Éléments concrètement utilisés.** Ponctuellement dans les composants pour
composer les noms de classe.

---

## D — Tableau récapitulatif

| Phase / Couche       | Bibliothèque        | Rôle principal                                      |
| -------------------- | ------------------- | --------------------------------------------------- |
| Phase 1 — Upload     | FastAPI             | Routage HTTP                                        |
| Phase 1 — Upload     | uvicorn             | Serveur ASGI                                        |
| Phase 1 — Upload     | Pydantic            | Validation des entrées                              |
| Phase 1 — Upload     | python-multipart    | Parse le corps multipart                            |
| Phase 1 — Upload     | SQLAlchemy          | INSERT du Clip + Job                                |
| Phase 1 — Upload     | asyncpg             | Driver DB async                                     |
| Phase 1 — Upload     | PostgreSQL          | Persistance                                         |
| Phase 1 — Upload     | Celery              | Met la tâche en file                                |
| Phase 1 — Upload     | Redis + redis-py    | Broker                                              |
| Phase 1 — Upload     | websockets          | Ouvre le canal WS pour le progrès                   |
| Phase 2 — Ingestion  | psycopg2-binary     | Driver DB sync dans le worker                       |
| Phase 2 — Ingestion  | FFmpeg + ffprobe    | Métadonnées, audio, vignettes, proxy                |
| Phase 2 — Ingestion  | mlx-whisper         | Transcription avec timestamps mot-par-mot           |
| Phase 2 — Ingestion  | PySceneDetect       | Détection de scènes                                 |
| Phase 2 — Ingestion  | OpenCV (headless)   | Décodage de frames (indirect)                       |
| Phase 2 — Ingestion  | NumPy               | Opérations sur arrays (indirect)                    |
| Phase 2 — Ingestion  | PIL / Pillow        | Analyse pixel (luminance, contraste, etc.)          |
| Phase 2 — Ingestion  | open-clip-torch     | Embedding CLIP 512-dim par scène                    |
| Phase 2 — Ingestion  | PyTorch + torchvision | Backend pour CLIP                                 |
| Phase 2 — Ingestion  | httpx               | Client HTTP vers Ollama                             |
| Phase 2 — Ingestion  | Ollama + LLaMA3     | Descriptions de scènes                              |
| Phase 3 — Coupe IA   | httpx               | Client HTTP vers Claude/OpenAI/Gemini               |
| Phase 3 — Coupe IA   | anthropic SDK       | (optionnel) verfeinement Claude                     |
| Phase 3 — Coupe IA   | openai SDK          | (optionnel) verfeinement GPT-4o                     |
| Phase 4 — Export     | FFmpeg              | Transitions xfade, mix audio, encodage H.264        |
| Frontend             | Next.js + React     | Framework UI                                        |
| Frontend             | TypeScript          | Sécurité de types                                   |
| Frontend             | Zustand             | State-management                                    |
| Frontend             | Tailwind CSS        | CSS utility                                         |
| Frontend             | Framer Motion       | Animations                                          |
| Frontend             | Lucide React        | Icônes                                              |
| Frontend             | clsx                | className conditionnels                             |

---

## Annexe — Comment présenter ce choix comme délibéré

Si le prof demande : *« Pourquoi autant de bibliothèques ? »*, la réponse est :

> En allemand (à dire au prof) :
> *„Jede Bibliothek erfüllt eine **klar abgegrenzte Verantwortung**. Es gibt
> keine Duplikate. Wenn ich eine Bibliothek austausche, bleibt der Rest des
> Systems intakt. Diese Modularität ist das Resultat der Schichtentrennung —
> Web-Schicht, Datenschicht, Aufgaben-Schicht, Medien-Schicht, KI-Schicht.
> **Jede Schicht hat ihre eigenen Standardwerkzeuge**, die ich aus dem
> etablierten Python- und JS-Ökosystem ausgewählt habe."*

**Traduction française** (pour ma compréhension) :
> *« Chaque bibliothèque a une **responsabilité clairement définie**. Il n'y a
> aucun doublon. Si j'en remplace une, le reste du système reste intact. Cette
> modularité est le résultat de la séparation par couches — couche web, couche
> de données, couche de tâches, couche médias, couche IA. **Chaque couche a
> ses outils standards**, choisis dans les écosystèmes Python et JS établis. »*

Cette affirmation est défendable parce qu'elle correspond à la structure
réelle du code.

---

*Mise à jour : 2026-05-21. Partie de la Bachelorarbeit CinAssist — documentation
de défense.*
