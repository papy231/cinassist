# Phase 1 — L'hôtel cinq étoiles

> Phase 1 (l'upload synchrone) racontée comme une histoire,
> avec une analogie unifiée — l'hôtel — pour comprendre **qui appartient à qui**.

Ce document est pensé pour **ma compréhension personnelle** (étudiant en première
prise de contact avec le backend). Il complète la version technique formelle
[`PHASE_1_UPLOAD.md`](../PHASE_1_UPLOAD.md) (en allemand, pour le professeur).

---

## L'histoire générale

Imagine que ton backend est un **grand hôtel cinq étoiles**. Quand tu cliques
« Upload » dans ton navigateur, c'est comme arriver à la réception avec une
valise importante (ton fichier MP4) et dire :

> « Je voudrais déposer ceci pour analyse. »

**Dix employés** travaillent ensemble pour t'accueillir. Certains sont
**patrons**, d'autres sont leurs **assistants**, d'autres encore sont des
**services indépendants** qu'on appelle au besoin.

Voici l'organigramme :

```
   uvicorn ─── LE BÂTIMENT (fournit la porte d'entrée, l'électricité)
       │
       └── FastAPI ─── PIERRE, LE MAÎTRE D'HÔTEL
              │
              ├── Pydantic ──────── le videur (assistant de Pierre)
              ├── python-multipart ─ le bagagiste (assistant de Pierre)
              ├── websockets ────── le radio-opérateur (assistant de Pierre)
              │
              ├── SQLAlchemy ─── MARIE, LA SECRÉTAIRE-ARCHIVISTE
              │       │
              │       ├── asyncpg ─── son téléphone async vers les archives
              │       └── psycopg2 ── son téléphone sync (pour Celery)
              │              │
              │              └── PostgreSQL ─── LA SALLE DES ARCHIVES (service externe)
              │
              └── Celery ─── MARC, LE CUISINIER D'ARRIÈRE-SALLE
                     │
                     └── Redis + redis-py ─── LE PASSE-PLAT
                                               + LE TABLEAU D'AFFICHAGE
```

Maintenant on plonge bibliothèque par bibliothèque. Pour chacune :

1. **L'histoire** — l'analogie
2. **Sa famille** — les éléments qui lui appartiennent
3. **De qui elle dépend** — ce qu'elle utilise
4. **Qui l'utilise** — qui s'appuie sur elle

---

## 1. uvicorn — Le bâtiment lui-même

### Histoire
Avant qu'un employé puisse travailler, il faut un **bâtiment physique** avec
une porte ouverte sur la rue. uvicorn, c'est le bâtiment. Il a une porte
d'entrée (le port `8001`) où les visiteurs peuvent toquer.

uvicorn ne sait rien faire d'autre. Il ne reçoit pas, n'archive pas, ne cuisine
pas. Il **fournit l'espace physique** dans lequel les autres travaillent.

### Sa famille
- La commande CLI : `uvicorn backend.main:app --port 8001 --reload`
- Le mode `--reload` (auto-redémarrage en dev)
- Le support TCP/HTTP bas-niveau (il sait recevoir des bytes du réseau)

### De qui il dépend
- **Rien** (c'est le fondement)

### Qui l'utilise
- **FastAPI tourne DANS uvicorn.** uvicorn héberge FastAPI.

### Mot-clé
**HÉBERGEUR / SERVEUR**

---

## 2. FastAPI — Pierre le maître d'hôtel

### Histoire
Pierre est le maître d'hôtel. Dès qu'un client toque (= une requête HTTP),
Pierre va à la porte, salue, écoute la demande, et **oriente** vers le bon
employé interne :
- *« Vous voulez uploader ? »* → il appelle le videur (Pydantic) et le
  bagagiste (multipart)
- *« Vous voulez la liste de vos clips ? »* → il appelle la secrétaire
  (SQLAlchemy)
- *« Vous voulez suivre votre dossier en direct ? »* → il branche le
  radio-opérateur (websockets)

Pierre lui-même **ne fait pas** le travail technique. Il **coordonne**.

### Sa famille
- `APIRouter`, `@router.post(...)`, `@router.get(...)` — les routes
- `HTTPException(400, "...")` — les refus
- `Depends(get_db)` — attribue un assistant
- `UploadFile`, `File(...)`, `Form(...)` — déclare les types d'entrée (le vrai
  boulot d'extraction est fait par le bagagiste python-multipart)
- `WebSocket` (wrapper) — branche le radio-opérateur
- `CORSMiddleware`, `StaticFiles` — règles d'accueil globales

### De qui il dépend
- **Pydantic** (son videur)
- **python-multipart** (son bagagiste)
- **websockets** (son radio-opérateur)
- **uvicorn** (le bâtiment)

### Qui l'utilise
- **Toi**, dans tes fichiers `backend/api/*.py`

### Mot-clé
**COORDINATEUR / ROUTEUR**

---

## 3. Pydantic — Le videur de Pierre

### Histoire
Quand un client donne des informations à Pierre (*« je suis dans la catégorie
A »*), Pierre se tourne vers son **videur**, qui jette un œil rapide :
- *« Catégorie A ? OK, c'est dans la liste autorisée. »*
- *« Catégorie XYZ ? Non, refusé. »*

Le videur a une **fiche** que Pierre lui a donnée avec les règles. Cette
fiche, c'est ta classe `AiCutRequest(BaseModel)` ou l'annotation
`quelle: str = Form(...)`.

### ⚠️ La question importante : « Pydantic appartient-il à FastAPI ? »

**NON.** Pydantic est une bibliothèque **standalone**. Elle existe
indépendamment, depuis 2017, et tu peux l'utiliser **sans FastAPI** (par
exemple pour valider des fichiers de configuration ou parser des YAML).

Mais FastAPI s'en sert tellement intensivement qu'on a l'impression qu'elle
fait partie de FastAPI. C'est comme si Pierre avait un **contrat exclusif**
avec une agence de sécurité externe : on voit toujours le même videur, mais
lui ne travaille pas pour Pierre, il travaille pour son agence (Pydantic).

### Sa famille
- `BaseModel` — la classe-mère des « fiches de règles »
- `Field(...)` avec contraintes (`ge=0.0`, `le=1.0`, etc.)
- `Literal["A", "B", "C"]` — choix fermés
- Le retour automatique de `HTTP 422` quand quelque chose ne respecte pas les
  règles
- La conversion auto Python → JSON dans les réponses

### De qui il dépend
- **Rien** (pure Python)

### Qui l'utilise
- **FastAPI** (massivement, presque à chaque endpoint)
- **Toi** quand tu déclares `AiCutRequest(BaseModel)` dans
  [backend/api/ai.py:124](../../backend/api/ai.py#L124)

### Mot-clé
**VALIDATEUR**

---

## 4. python-multipart — Le bagagiste de Pierre

### Histoire
Quand un client arrive avec une grosse valise (= un fichier vidéo dans une
requête `multipart/form-data`), Pierre ne s'en occupe pas lui-même. Il appelle
**le bagagiste**.

Le bagagiste sait **ouvrir le contenant** :
- *« Dans ce paquet, je vois un champ texte `quelle = 'A'`. »*
- *« Et un blob binaire `datei` de 4 MB, c'est un MP4. »*

Il pose les deux éléments séparément sur le comptoir, et Pierre peut les
manipuler.

### « python-multipart appartient à FastAPI ? »

**NON, STANDALONE.** Mais FastAPI l'utilise **automatiquement** dès que tu
déclares `UploadFile = File(...)` ou `Form(...)`.

Si tu n'installes pas python-multipart, FastAPI te crachera au démarrage :

```
RuntimeError: Form data requires "python-multipart" to be installed.
```

Tu ne vois jamais le bagagiste dans ton code, mais il est là, **derrière
Pierre**, faisant le travail dès qu'on parle d'upload.

### Sa famille
- L'implémentation du protocole RFC 7578 (`multipart/form-data`)
- Le parseur de bytes binaires en champs nommés

### De qui il dépend
- Rien

### Qui l'utilise
- FastAPI (déclenché par `UploadFile`, `File()`, `Form()`)

### Mot-clé
**EXTRACTEUR DE FICHIERS HTTP**

---

## 5. SQLAlchemy — Marie la secrétaire-archiviste

### Histoire
Maintenant Pierre doit enregistrer l'arrivée du client. Il va voir **Marie**.

Marie sait parler le langage administratif des archives (= **SQL**), mais
Pierre, lui, parle Python. Donc Marie fait le **traducteur** :
- Pierre dit : *« Marie, crée-moi un nouveau Clip avec ces caractéristiques. »*
- Marie traduit en SQL : `INSERT INTO clips (id, dateiname, ...) VALUES (...);`
- Et elle décroche son téléphone (asyncpg) pour envoyer la commande à la
  salle des archives.

Marie a un **gros classeur** qui décrit la structure des registres (à quoi
ressemble un Clip, une Szene, etc.) → c'est ton fichier
[`backend/core/database.py`](../../backend/core/database.py).

### Sa famille
- `DeclarativeBase` — la classe-mère des modèles
- Tes 4 modèles : `Clip`, `Szene`, `Job`, `Timeline`
- Types de colonnes : `String`, `Integer`, `JSON`, `ARRAY(Float)`, `UUID`,
  etc.
- `db.add(obj)`, `await db.commit()` — « écris ça »
- `select(Clip).where(...)` — « trouve-moi ça »
- `relationship(...)` avec `cascade="all, delete-orphan"` — les liens
  parent-enfant

### De qui elle dépend
- **asyncpg** (téléphone async vers les archives)
- **psycopg2-binary** (téléphone sync, utilisé par le cuisinier Celery)

### Qui l'utilise
- Pierre (FastAPI) via `Depends(get_db)`
- Marc le cuisinier (Celery worker) directement

### Mot-clé
**TRADUCTEUR PYTHON ↔ SQL** (= ORM)

---

## 6. asyncpg + psycopg2-binary — Les deux téléphones

### Histoire
Marie ne se déplace pas physiquement jusqu'à la salle des archives — elle
décroche un **téléphone**. Il y en a deux dans son bureau :

- **asyncpg** : téléphone moderne **sans fil, async**. Marie peut continuer
  de travailler pendant qu'elle attend la réponse (= ne bloque pas le thread).
- **psycopg2-binary** : téléphone classique **à fil, sync**. Quand Marc le
  cuisinier (Celery) appelle, il utilise celui-ci, parce que son métier est
  de toute façon séquentiel.

### Sa famille (chacun)
- L'implémentation du **protocole « wire » PostgreSQL** (la façon dont
  Python parle au serveur Postgres en bytes binaires)

### De qui ils dépendent
- Rien (parlent directement au protocole Postgres)

### Qui les utilise
- **SQLAlchemy** uniquement. Tu ne les appelles jamais directement.

### Mot-clé
**DRIVER DB** (un async + un sync)

---

## 7. PostgreSQL — La salle des archives elle-même

### Histoire
Derrière une porte blindée, **la salle des archives** : étagères, classeurs,
dossiers, à l'abri. C'est un **service externe** qui tourne **en dehors** de
ton code Python, sur le port `5432`.

L'archiviste (= le processus `postgres`) reçoit les commandes par téléphone
(asyncpg/psycopg2), les exécute physiquement (écriture sur disque), et
confirme.

### Sa famille
- Tes 4 tables : `clips`, `szenen`, `jobs`, `timelines`
- Les types de colonnes spéciaux : `UUID`, `JSON`, `JSONB`, `ARRAY[Float]`
- Les contraintes : `PRIMARY KEY`, `FOREIGN KEY ... ON DELETE CASCADE`
- Les transactions ACID (garantit l'atomicité de `INSERT clip + INSERT job`)

### De qui il dépend
- Rien (c'est un service externe écrit en C, indépendant de Python)

### Qui l'utilise
- SQLAlchemy via asyncpg ou psycopg2

### Mot-clé
**STOCKAGE PERSISTANT**

---

## 8. Celery — Marc le cuisinier d'arrière-salle

### Histoire
Quand un client demande un plat très long (= analyse vidéo de plusieurs
minutes), Pierre **n'envoie PAS le client en cuisine** et **ne cuisine pas
lui-même**. Il :

1. Écrit un **ticket de commande** sur un papier.
2. Pose le papier dans le **passe-plat** (= Redis).
3. Continue à servir d'autres clients.

**Marc**, le cuisinier en arrière-salle (= un autre processus Python, lancé
séparément), surveille le passe-plat. Dès qu'un ticket apparaît, il le prend,
va cuisiner pendant 3 heures, et quand c'est prêt, il sonne la cloche.

### Sa famille
- La classe `Celery(...)` dans
  [`backend/core/celery_app.py`](../../backend/core/celery_app.py)
- Le décorateur `@celery_app.task(name="cinassist.ingest")` qui transforme
  une fonction Python en **recette exécutable**
- La méthode `.delay(*args)` = « écris ce ticket et pose-le dans le
  passe-plat »
- Le **worker** (le processus qui tourne en parallèle, lancé par
  `celery -A backend.core.celery_app worker --pool=solo`)
- `billiard` (une lib interne à Celery qui gère les processus — c'est lui qui
  posait problème avec PyTorch + Metal, d'où le `--pool=solo`)

### De qui il dépend
- **Redis** (le passe-plat / broker)
- **billiard** (sa lib interne de gestion de processus)

### Qui l'utilise
- Pierre (FastAPI) avec `ingestion_pipeline.delay(...)`
- Le worker lui-même quand il exécute la recette

### Mot-clé
**FILE DE TÂCHES / EXÉCUTEUR D'ARRIÈRE-PLAN**

---

## 9. Redis + redis-py — Le passe-plat et le tableau d'affichage

### Histoire
**Redis**, c'est une **mémoire ultra-rapide** dans le couloir entre la
réception et la cuisine. Physiquement c'est UN seul service, mais il joue
**deux rôles** :

**Rôle A — Le passe-plat (broker)**
Pierre écrit un ticket « tâche X à faire », le pose dedans. Marc le prend.

**Rôle B — Le tableau d'affichage temps réel (Pub/Sub)**
Pendant que Marc cuisine, il écrit sur un grand **tableau d'affichage** :
*« Étape 30 % faite. Whisper a transcrit 17 mots. »* Le radio-opérateur
(websockets) LIT ce tableau et **relaie** au client en temps réel.

### ⚠️ Question importante : « redis-py appartient à Redis ? »

**Redis** est un **logiciel serveur écrit en C**, externe à Python. Il tourne
sur le port `6379`.

**redis-py** est le **client Python** pour parler à ce serveur. C'est comme
un téléphone qui appelle un service de stockage en ligne.

- **Redis** = le service distant (le passe-plat physique)
- **redis-py** = le bras de Python pour interagir avec lui

### Sa famille (Redis lui-même)
- Les structures de données (listes, sets, hashes, channels pub/sub)
- Les commandes basiques : `LPUSH`, `BRPOP`, `PUBLISH`, `SUBSCRIBE`

### Sa famille (redis-py)
- `redis.from_url("redis://localhost:6379/0")` — se connecter
- `.publish(channel, json_payload)` — publier sur un canal
- `.subscribe(channel)` — s'abonner à un canal

### De qui il dépend
- Rien (Redis = service externe, redis-py = pure Python)

### Qui l'utilise
- **Celery** (utilise Redis comme broker — rôle A)
- Ton code dans `_update_job(...)` à
  [`backend/workers/ingest.py:59-66`](../../backend/workers/ingest.py#L59-L66)
  (rôle B, côté publish)
- [`backend/api/websocket.py`](../../backend/api/websocket.py) (rôle B, côté
  subscribe)

### Mot-clé
**MÉMOIRE PARTAGÉE RAPIDE** (broker + pub/sub)

---

## 10. websockets — Le radio-opérateur

### Histoire
HTTP, c'est comme un téléphone fixe : tu appelles, on te répond, tu raccroches.
Le serveur ne peut **pas** te rappeler de lui-même.

**WebSocket**, c'est comme une **radio bidirectionnelle qui reste toujours
allumée** entre Pierre et le client. Pierre peut dire au client *« Ah, on est
à 50 % »* à tout moment, sans que le client ait à demander.

C'est le rôle du **radio-opérateur** de l'hôtel : il maintient le canal
ouvert et fait le relais.

### « websockets appartient à FastAPI ? »

**NON, STANDALONE.** Mais FastAPI fournit un **wrapper sympa** par-dessus :
la classe `WebSocket` et le décorateur `@app.websocket("/...")`.

C'est comme si FastAPI avait son **propre interphone** intégré qui parle au
radio-opérateur — tu n'as pas besoin de t'adresser directement à l'opérateur
radio, tu parles à l'interphone et il transmet.

### Sa famille (websockets brute)
- L'implémentation du protocole RFC 6455
- Le handshake HTTP-Upgrade

### Sa famille (wrapper FastAPI)
- L'objet `WebSocket` (à ne pas confondre avec la lib)
- `@app.websocket("/ws/...")` (route)
- `.accept()`, `.send_json(...)`, `.receive_text()`

### De qui il dépend
- uvicorn (qui fournit le support TCP bas-niveau pour maintenir la connexion
  ouverte)

### Qui l'utilise
- FastAPI via [`backend/api/websocket.py`](../../backend/api/websocket.py)

### Mot-clé
**CANAL BIDIRECTIONNEL PERMANENT**

---

## Le tableau de correspondance final

| Bibliothèque | Standalone ? | Famille (ce qui lui appartient) | Dépend de | Utilisée par |
|---|:-:|---|---|---|
| **uvicorn** | ✓ | `uvicorn` CLI, `--reload`, support TCP | rien | FastAPI tourne dedans |
| **FastAPI** | ✓ | `APIRouter`, `Depends`, `HTTPException`, `UploadFile`, `WebSocket` wrapper | Pydantic, multipart, websockets, uvicorn | Toi |
| **Pydantic** | ✓ | `BaseModel`, `Field`, `Literal`, validation 422 | rien | FastAPI |
| **python-multipart** | ✓ | parser `multipart/form-data` | rien | FastAPI (uploads) |
| **SQLAlchemy** | ✓ | `DeclarativeBase`, modèles `Clip/Szene/Job`, `select()`, `relationship()` | asyncpg + psycopg2 | FastAPI + Celery |
| **asyncpg** | ✓ | protocole wire Postgres (async) | rien | SQLAlchemy |
| **psycopg2-binary** | ✓ | protocole wire Postgres (sync) | rien | SQLAlchemy côté Celery |
| **PostgreSQL** | ✓ (externe) | tables, JSON, ARRAY, transactions | rien | SQLAlchemy via les drivers |
| **Celery** | ✓ | `@task`, `.delay()`, worker, billiard | Redis, billiard | FastAPI + worker |
| **Redis** | ✓ (externe) | listes, channels pub/sub | rien | Celery, redis-py |
| **redis-py** | ✓ | `from_url`, `.publish()`, `.subscribe()` | Redis (le service) | Celery, ton code, WebSocket |
| **websockets** | ✓ | protocole RFC 6455, handshake | uvicorn | FastAPI (via wrapper) |

## La règle d'or

**Toutes ces bibliothèques sont STANDALONE et indépendantes.** Aucune n'est
sous-module d'une autre. Mais elles s'utilisent entre elles **comme des
collègues d'une même entreprise** : chacune a son métier, et elles se passent
les balles selon le besoin.

C'est pour ça que tu peux les remplacer une à une (par exemple : passer de
Pydantic à autre chose, ou de FastAPI à Flask) sans changer les autres.

---

## Mini-glossaire des termes techniques

| Terme | Traduction simple |
|---|---|
| **Thread** | Un fil d'exécution, un employé virtuel qui fait les tâches une par une |
| **Thread principal** | L'employé central par lequel toutes les requêtes HTTP passent |
| **Async / await** | Le mécanisme qui permet à l'employé de continuer à travailler pendant qu'il attend une réponse externe (DB, réseau) |
| **Broker** | Un intermédiaire qui stocke les messages entre deux processus qui ne se parlent pas directement |
| **Pub/Sub** | « Publish / Subscribe » : un système où plusieurs personnes peuvent écouter le même canal d'annonces |
| **ORM** | « Object-Relational Mapper » : un traducteur entre objets Python et tables SQL |
| **ASGI** | Le standard async qui remplace le vieux WSGI (synchrone) pour les serveurs Python |
| **Multipart** | Un format HTTP qui permet d'envoyer plusieurs morceaux (texte + fichier binaire) dans la même requête |
| **WebSocket** | Une connexion ouverte en permanence dans les deux sens, contrairement à HTTP qui ferme après chaque échange |

---

*Document de compréhension personnelle pour la Bachelorarbeit CinAssist.*
*Mise à jour : 2026-05-21.*
