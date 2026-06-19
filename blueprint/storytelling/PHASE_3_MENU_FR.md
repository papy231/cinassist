# Phase 3 — Le menu dégustation du Chef

> Phase 3 (le KI-Schnitt — la coupe par IA) racontée comme une histoire.
> Marc a préparé tous les ingrédients en cuisine (Phase 2). Maintenant **le
> Chef** entre en scène : à partir de ces ingrédients, il doit **composer un
> menu** — choisir les plats, les goûter, les classer, et surtout les
> **ordonner** pour créer une progression qui a du sens.

Suite de [`PHASE_2_CUISINE_FR.md`](PHASE_2_CUISINE_FR.md).
Ce document est pensé pour **ma compréhension personnelle**. Il complète la
version technique formelle [`PHASE_3_KI_SCHNITT.md`](../PHASE_3_KI_SCHNITT.md)
(en allemand, pour le professeur).

C'est **le cœur de la thèse** — la partie que le professeur attaquera le
plus. Prends le temps de bien comprendre chaque étape.

---

## L'histoire générale

En Phase 2, Marc a transformé chaque vidéo brute en une série de **scènes
préparées**. Chaque scène est rangée dans le garde-manger (la base de
données PostgreSQL) avec sa **fiche d'identité complète** :

- un vecteur de 512 chiffres (l'embedding CLIP) — sa « signature visuelle »
- une description en une phrase (LLaVA)
- la transcription du dialogue avec l'horodatage de chaque mot
- les métriques pixel (luminosité, contraste, mouvement, énergie…)

Le **client** (toi, l'utilisateur) passe commande à la réception : il choisit
un **style** (« kinematisch », « werbespot »…) et, optionnellement, écrit une
**demande spéciale** (un prompt texte, par ex. *« montre les passages
calmes »*).

Le **Chef** reçoit la commande. Son travail n'est PAS de cuisiner — Marc l'a
déjà fait. Son travail, c'est la **composition** : décider quels plats
entrent dans le menu, et **dans quel ordre** les servir.

> ⚠️ **Le point le plus important de toute la thèse :**
> La Phase 3 ne produit **aucune image**. Elle produit un **plan de
> montage** — une liste de la forme *« prends dans le Clip X les secondes
> a→b et place-les en position p »*. Les vraies images ne seront lues qu'en
> Phase 4 (l'export), directement depuis les fichiers originaux. Le Chef
> écrit le menu ; il ne cuisine pas le plat final.

Voici le plan de travail du Chef :

```
┌────────────────────────────────────────────────────────────────────┐
│                  LA COMPOSITION DU MENU (Phase 3)                   │
│              backend/api/ai.py · ai_schnitt()                       │
│                                                                     │
│  [1]  Sortir les ingrédients du garde-manger ──── SELECT en DB      │
│         ↓                                                           │
│  [2]  Goûter chaque ingrédient ─────────────────── CLIP zero-shot   │
│         ↓        (intense ou doux ?)                                │
│  [3]  Lire la demande spéciale du client ───────── CLIP text-encoder│
│         ↓        (optionnel)                                        │
│  [4]  Écarter le fade & les cartes pub ─────────── filtres qualité  │
│         ↓                                                           │
│  [5]  Découper les gros morceaux ───────────────── subdivision audio│
│         ↓        (aux pauses de parole)                             │
│  [6]  Étiqueter chaque plat ────────────────────── A-roll / B-roll  │
│         ↓        (résistance / garniture / amuse-bouche)            │
│  [7]  Ne pas servir 2× le même plat ────────────── dédup multicam   │
│         ↓                                                           │
│  [8]  COMPOSER LE MENU ◀══ LE CŒUR ─────────────── Beam Search      │
│         ↓        (ou MMR si demande spéciale)                       │
│  [9]  L'avis du critique gastronomique ─────────── LLM (optionnel)  │
│         ↓                                                           │
│  [10] Dresser les assiettes ────────────────────── segments V1+A1   │
│         ↓                                                           │
│  [11] La fiche de dégustation ──────────────────── 3 métriques      │
│         ↓                                                           │
│  [12] Imprimer le menu ─────────────────────────── INSERT timeline  │
└────────────────────────────────────────────────────────────────────┘
```

On plonge étape par étape.

---

## Le carnet de commandes — les 5 styles

Avant tout, le Chef regarde **quel menu** le client a commandé. Il a 5 cartes
préétablies (`STIL_CONFIG` dans
[`ai.py:221`](../../backend/api/ai.py#L221)) :

| Style          | durée min/max | tempo | poids dialogue | arc narratif | esprit                              |
|----------------|---------------|-------|----------------|--------------|-------------------------------------|
| `kinematisch`  | 1,5 – 12 s    | 0,60  | 0,30           | ✔ oui        | menu gastronomique, vraie dramaturgie|
| `dokumentar`   | 3 – 40 s      | 0,25  | 0,85           | ✘ non        | chronologique, axé dialogue, calme  |
| `werbespot`    | 0,8 – 5 s     | 0,90  | 0,10           | ✘ non        | tapas rapides, impact visuel        |
| `kurzfilm`     | 2 – 20 s      | 0,45  | 0,55           | ✔ oui        | menu équilibré, focus narratif      |
| `social_media` | 0,5 – 3,5 s   | 0,95  | 0,05           | ✘ non        | bouchées ultra-courtes, énergie max |

- **tempo** (0 = lent, 1 = rapide) : décide si les plats trop longs sont
  raccourcis.
- **poids dialogue** : à quel point le Chef privilégie les scènes parlées.
- **arc narratif** : si activé, le Chef construit une vraie **courbe
  dramatique** (étape 8, voie B). Sinon, un simple tri.

---

## Étape 1 — Sortir les ingrédients du garde-manger

### Histoire
Le Chef ouvre le garde-manger et sort **toutes les scènes** des clips que le
client a sélectionnés. Pour chaque scène, il récupère la fiche complète que
Marc a remplie en Phase 2.

Concrètement, c'est une requête SQL
([`ai.py:1934`](../../backend/api/ai.py#L1934)) : pour chaque `clip_id`, on
charge le `Clip` et ses `szenen`, et on les traduit en dictionnaires Python.
Chaque dictionnaire-scène porte : `embedding`, `beschreibung`,
`transkription`, `transkription_json` (avec l'horodatage des mots),
`analyse_visuelle`, et la **position relative** `_pos_pct` dans le clip
source (0 = début du clip, 1 = fin).

> 💡 La Phase 3 ne lit **que la base de données**. Elle ne touche jamais au
> fichier vidéo. Tout le travail visuel a été fait en Phase 2 et « congelé »
> dans la DB. Le Chef travaille à partir des fiches, pas des légumes.

### Mot-clé
**CHARGEMENT DES SCÈNES ANALYSÉES**

---

## Étape 2 — Goûter chaque ingrédient : intense ou doux ?

### Histoire
Le Chef doit savoir, pour chaque scène, si elle est **énergique** (de
l'action, du mouvement, du contraste) ou **calme** (statique, posée). Il lui
attribue un score `_energie` entre 0 et 1.

Mais **comment** mesure-t-il ça ? C'est ici que se joue **la** question du
prof. Il y a deux méthodes, et CinAssist est passé de l'ancienne à la
nouvelle.

### 🔬 Deep dive — L'ancienne méthode (la « recette à coefficients »)

Avant, l'énergie venait de la formule de Phase 2 :

```
energie = contraste·0.40 + mouvement·0.35 + luminosité·0.15 + netteté·0.10
```

**Le problème** : d'où viennent les chiffres 0.40, 0.35, 0.15, 0.10 ? De
l'intuition. Ce sont des **hyperparamètres heuristiques**, choisis « à la
main ». Un prof dira immédiatement : *« Pourquoi 0.40 et pas 0.35 ? Tu as
validé ces poids sur un jeu de données ? »* — et la réponse honnête serait
« non ». C'est exactement le genre de **magic number** indéfendable.

### 🔬 Deep dive — La nouvelle méthode : CLIP Zero-Shot

CinAssist calcule maintenant le score **autrement**, dans
`_szene_energie()` ([`ai.py:337`](../../backend/api/ai.py#L337)).

**L'analogie de la cuisine.** Imagine que le Chef ait deux **bocaux de
référence** sur son plan de travail :
- un bocal étiqueté **« saveur intense »** (qui contient l'idée abstraite de
  l'action, de la vitesse)
- un bocal étiqueté **« saveur douce »** (l'idée du calme, de la lenteur)

Pour goûter un ingrédient, le Chef ne suit pas une recette à coefficients.
Il compare directement : *« cet ingrédient ressemble-t-il davantage au bocal
"intense" ou au bocal "doux" ? »*

**Techniquement.** Les deux « bocaux » sont des **prompts texte**
prédéfinis — par ex. *« a fast-paced action shot »* et *« a calm static
scene »*. On les transforme une fois pour toutes en vecteurs CLIP (le fichier
`backend/data/prompt_embeddings.json`). Pour une scène donnée :

```
avg_action = moyenne des cos(embedding_scène, prompts "action")
avg_calm   = moyenne des cos(embedding_scène, prompts "calm")
score = 0.5 + 2.0 · (avg_action − avg_calm)        → ramené dans [0, 1]
```

`cos(...)` est la **similarité cosinus** : l'angle entre deux vecteurs dans
l'espace à 512 dimensions de CLIP. C'est une **classification zero-shot**
(Radford et al., ICML 2021) : on classe une image sans avoir entraîné de
classifieur, juste en la comparant à des descriptions textuelles.

**Pourquoi c'est défendable :**
- Plus aucun coefficient arbitraire. Tous les chiffres viennent de l'espace
  vectoriel commun image-texte de CLIP.
- Les prompts sont **explicites et auditables** — on peut les lire, les
  discuter, les modifier.
- Le résultat est **déterministe et reproductible**.

L'ancienne formule reste comme **filet de sécurité** (« fallback ») : si les
prompt-embeddings manquent ou qu'une scène n'a pas d'embedding image.

### Phrase à mémoriser pour la défense (allemand)

> *„Der Szenen-Score basiert auf einer CLIP-Zero-Shot-Klassifikation: Ich
> vergleiche das Bild-Embedding jeder Szene mit vordefinierten Text-Prompts
> ('action' vs. 'calm') über die Kosinus-Ähnlichkeit. Es gibt **keine
> willkürlichen Koeffizienten** mehr — alle Werte stammen aus dem
> gemeinsamen Bild-Text-Vektorraum von CLIP (Radford et al., 2021), die
> Prompts sind explizit auditierbar. Die alte heuristische Formel dient nur
> noch als Fallback."*

### Mot-clé
**SCORE PAR CLASSIFICATION ZERO-SHOT (PAS DE MAGIC NUMBERS)**

---

## Étape 3 — Lire la demande spéciale du client

### Histoire
Si le client a écrit une **demande spéciale** (un prompt texte), le Chef doit
la comprendre. Par exemple : *« je veux les passages calmes et lumineux »*.

### 🔬 Deep dive — L'encodeur de texte CLIP

C'est **la vraie magie de CLIP**. CLIP place les **images ET les textes dans
le même espace** à 512 dimensions. Donc le Chef peut prendre la phrase du
client, la transformer en vecteur (`_encode_prompt`,
[`ai.py:141`](../../backend/api/ai.py#L141)), et la **comparer directement
aux images** des scènes — sans avoir entraîné quoi que ce soit.

```
prompt utilisateur → CLIP text-encoder → vecteur 512-dim
pour chaque scène : _prompt_relevance = cos(embedding_scène, vecteur_prompt)
```

Chaque scène reçoit un score `_prompt_relevance` : à quel point elle
correspond à l'intention du client. Si un prompt est présent, le Chef bascule
sur la **voie « prompt »** à l'étape 8 (le MMR).

> 💡 C'est pour ça que CLIP est si puissant : une phrase libre comme *« un
> chasseur seul dans la neige »* peut être comparée à des images, sans
> dictionnaire, sans modèle dédié. Texte et image parlent la même langue
> vectorielle.

### Mot-clé
**INTENTION DU CLIENT → VECTEUR TEXTE**

---

## Étape 4 — Écarter le fade et les cartes publicitaires

### Histoire
Le Chef fait un premier tri. Deux choses partent à la poubelle :

1. **Les ingrédients fades** (seuil de qualité). Si le client a réglé un
   `qualitaet_schwelle > 0`, les scènes dont le score `_energie` est trop bas
   sont écartées.

2. **Les cartes publicitaires** (filtre « filler »). C'est `_ist_filler_szene`
   ([`ai.py:1610`](../../backend/api/ai.py#L1610)). Si la description LLaVA ou
   la transcription contient des mots comme `subscribe`, `follow us`,
   `abspann`, `end card` — c'est un carton de fin YouTube ou un générique,
   **pas du contenu**. Le Chef l'écarte pour qu'un « ABONNEZ-VOUS » ne se
   retrouve pas au milieu du film.

> C'est ici que la description LLaVA de Phase 2 est utile : comme LLaVA
> **voit** vraiment l'image, si elle écrit « a thumbs-up icon with the word
> SUBSCRIBE », le Chef sait sans ambiguïté que c'est un carton de fin.

### Mot-clé
**FILTRAGE QUALITÉ + ANTI-CARTONS**

---

## Étape 5 — Découper les gros morceaux aux jointures naturelles

### Histoire
Certaines scènes sont **trop longues** pour le style choisi (une prise de 40
secondes alors que le style « kinematisch » vise ~4 s par plan). Le Chef
découpe ces gros morceaux en sous-scènes — c'est `_subdivise_scenes`
([`ai.py:590`](../../backend/api/ai.py#L590)).

### 🔬 Deep dive — Couper aux pauses de parole, pas en plein mot

Si on coupait une longue scène en morceaux **réguliers**, la coupe tomberait
presque toujours **au milieu d'un mot prononcé** — désagréable à entendre.

Le Chef est plus malin. La fonction `_find_natural_cut_points`
([`ai.py:542`](../../backend/api/ai.py#L542)) utilise les **horodatages de
mots** que Whisper a produits en Phase 2 :

1. Elle repère toutes les **pauses de parole > 300 ms** (les silences entre
   les segments de Whisper).
2. Pour chaque point de coupe idéal, elle le **déplace vers la pause la plus
   proche** (si celle-ci est à moins de 1,5 s).
3. S'il n'y a pas de transcription, on garde la coupe régulière.

**Analogie** : on découpe le rôti **à la jointure naturelle** entre deux
morceaux, pas en plein milieu de la viande.

En bonus, les sous-scènes reçoivent un **profil d'énergie sinusoïdal**
(calme au début et à la fin, énergique au milieu) — une mini-dramaturgie
dans chaque morceau découpé.

### Mot-clé
**SUBDIVISION AUDIO-CONSCIENTE**

---

## Étape 6 — Étiqueter chaque plat

### Histoire
Le Chef colle **deux étiquettes** sur chaque scène.

**Étiquette A — le type narratif** (`_detecte_role_narratif`,
[`ai.py:497`](../../backend/api/ai.py#L497)). Vocabulaire emprunté au montage
de film :

| Type           | C'est quoi                          | Dans la cuisine     | Signaux détectés                       |
|----------------|-------------------------------------|---------------------|-----------------------------------------|
| `a_roll`       | prise principale (interview, parole)| le **plat de résistance** | transcription présente + caméra peu mobile |
| `b_roll`       | plan de coupe (décor, action)       | la **garniture**    | pas de dialogue + mouvement moyen/fort  |
| `establishing` | plan d'introduction (situe le lieu) | l'**amuse-bouche**  | lumineux + large + calme + long         |

**Étiquette B — le rôle cinématographique** (`_rolle_kinematisch`,
[`ai.py:665`](../../backend/api/ai.py#L665)) : `ouverture`, `action`,
`transition`, `climax` ou `cloture`. Elle se déduit de l'énergie, du
mouvement, du contraste, de la température de couleur, de la durée, de la
présence de dialogue, de la position dans le clip **et** du type narratif
(une scène A-roll devient plutôt une `transition`, un establishing devient
plutôt une `ouverture`).

Ensuite, les scènes plus courtes que la durée minimale du style sont
écartées de la sélection (`kandidaten`).

> ⚠️ **Honnêteté pour la défense** : les seuils de cette classification
> (« mouvement < 0.65 » etc.) sont **empiriques**. C'est une limitation
> assumée et documentée.

### Mot-clé
**CLASSIFICATION A-ROLL / B-ROLL + RÔLE DRAMATIQUE**

---

## Étape 7 — Ne pas servir deux fois le même plat

### Histoire
Imagine que le client ait filmé **le même concert depuis 3 caméras**. Sans
précaution, le menu contiendrait 3 fois le même moment. Le **dédup
multicam** l'évite (`_get_multicam_groups` /
`_dedupe_multicam_candidates`, [`ai.py:2497`](../../backend/api/ai.py#L2497)).

### 🔬 Deep dive — Reconnaître deux caméras du même moment

Deux clips appartiennent au même groupe multicam s'ils se ressemblent **à la
fois à l'oreille et à l'œil** :

- **L'oreille** : `_audio_chroma_correlation` compare les **chroma-features**
  (la répartition des 12 hauteurs de notes) des deux bandes-son via librosa.
  Le chroma est robuste : il reconnaît la même musique même filmée par des
  micros différents.
- **L'œil** : `_visual_clip_similarity` compare les embeddings CLIP des
  scènes des deux clips.

Si les deux ressemblances dépassent leurs seuils
(`visuel ≥ 0.85 et audio ≥ 0.65`), les clips sont fusionnés en un groupe via
**Union-Find** (une structure de données « ensembles disjoints »). Ensuite,
par fenêtre de 6 secondes, le Chef ne garde **qu'une seule caméra** — et il
choisit volontairement celle qui a été **la moins utilisée** récemment. Le
résultat : une alternance naturelle entre les angles, exactement ce que ferait
un monteur multicam humain.

### Mot-clé
**DÉDUP MULTICAM (UNION-FIND, AUDIO + IMAGE)**

---

## Étape 8 — COMPOSER LE MENU ◀══ LE CŒUR DE LA THÈSE

### Histoire
C'est ici que naît l'**ordre** des plats. Selon qu'il y a un prompt et selon
le style, le Chef suit l'une de **trois voies**.

---

### Voie A — Menu sur demande spéciale (s'il y a un prompt)

Le client a écrit une demande. Le Chef choisit les scènes les plus
**pertinentes** — mais avec une subtilité.

### 🔬 Deep dive — Le MMR (Maximal Marginal Relevance)

Un tri naïf « prends les K scènes les plus proches du prompt » a un défaut :
les scènes les plus proches du prompt se ressemblent souvent **entre elles**.
Le menu deviendrait monotone — 8 plats au même goût.

Le **MMR** (Carbonell & Goldstein, 1998) équilibre **pertinence** et
**diversité** ([`ai.py:2100`](../../backend/api/ai.py#L2100)) :

```
MMR(scène) = λ · pertinence(scène, prompt)
           − (1−λ) · max_similarité(scène, déjà_choisies)
```

Avec `λ = 0.7` : 70 % de pertinence au prompt, 30 % de pénalité si la scène
ressemble trop à une scène déjà choisie. Résultat : chaque plat choisi est
pertinent **ET** apporte de la variété.

**Analogie** : le client demande « du chocolat ». Sans MMR, le Chef sert 8
desserts au chocolat identiques. Avec MMR : un fondant, une mousse, un
sorbet, une tarte — tous au chocolat (pertinents), mais tous différents
(divers).

Ensuite, selon le style, soit on impose un arc dramatique
(`_zwinge_narrativen_bogen`), soit on trie chronologiquement.

---

### Voie B — Le menu gastronomique : l'arc dramatique (défaut)

C'est le cas par défaut pour `kinematisch` et `kurzfilm`. Le Chef compose un
**vrai menu dégustation** avec une **progression dramatique** — la structure
aristotelienne (`_baue_kinematischen_bogen`,
[`ai.py:971`](../../backend/api/ai.py#L971)).

L'arc, c'est la **succession des services** d'un menu :

```
 [Ouverture]  →  [Action ≈25%]  →  [Respiration/Dialogue ≈20%]
              →  [Montée d'énergie ≈25%]  →  [Climax 1-2]  →  [Cloture]

   amuse-      tapas              le plat calme,           le plat de       le
   bouche      énergiques         le dialogue              résistance       dessert
```

Le Chef remplit d'abord chaque « service » avec les scènes du bon rôle. Mais
il reste toujours des scènes qui ne rentrent pas proprement dans ce schéma.
Pour les **insérer à la meilleure place**, il utilise le **Beam Search**.

### 🔬 Deep dive — Le Beam Search (largeur 3) — L'ALGORITHME

C'est **l'algorithme central de ta thèse**. Apprends-le par cœur.

**Le problème.** Ordonner *n* scènes « parfaitement », ce serait essayer
toutes les permutations : *n!*. Pour 15 scènes, c'est **1 307 milliards** de
possibilités. Impossible.

**La solution gourmande naïve** serait : « à chaque étape, prends la
meilleure scène suivante ». Rapide, mais myope — un bon choix immédiat peut
mener à une mauvaise suite.

**Le Beam Search** (`_beam_fill`, [`ai.py:784`](../../backend/api/ai.py#L784))
est un compromis intelligent :

1. Le Chef ne prépare pas **un** brouillon de menu, mais **3 brouillons en
   parallèle** (c'est ça, la « largeur 3 » / *beam width*).
2. Pour chaque brouillon, il essaie d'ajouter chaque scène restante, et note
   cet ajout avec un **score local rapide** (diversité visuelle avec le plat
   précédent, bonus si on change de type A/B-roll, **gros bonus si on change
   de clip source**, petit bonus d'énergie).
3. Parmi **tous** les prolongements de **tous** les brouillons, il garde les
   **3 meilleurs** — jugés cette fois par un **score global** (`_sequence_score`).
4. Il recommence jusqu'à ce que toutes les scènes soient placées. Le meilleur
   des 3 brouillons finaux gagne.

**Complexité** : O(largeur × n²) au lieu de O(n!). Avec largeur 3, c'est
praticable jusqu'à ~30 scènes, et le résultat est **bien meilleur** qu'un
choix gourmand, sans le coût d'une recherche exhaustive.

**Analogie** : le Chef hésite. Plutôt que de figer son menu plat par plat
(gourmand) ou d'essayer tous les menus possibles (impossible), il garde **3
versions du menu** ouvertes sur sa table, les fait évoluer en parallèle, et
ne tranche qu'à la fin pour la meilleure.

### 🔬 Deep dive — Comment on note un menu entier (`_sequence_score`)

Le score global ([`ai.py:732`](../../backend/api/ai.py#L732)) juge une
séquence complète sur **4 critères pondérés** :

```
0.20 · énergie moyenne
0.30 · diversité visuelle moyenne entre plats voisins
0.20 · taux d'alternance A-roll / B-roll
0.30 · taux de changement de clip source
```

> ⚠️ Ces 4 poids (0.20, 0.30, 0.20, 0.30) **sont** des hyperparamètres
> heuristiques — sois honnête là-dessus en défense. Mais le poids fort du
> **changement de clip** (0.30) est justifié : la mission même de CinAssist
> est de **mélanger plusieurs sources**. Trois plats du même clip d'affilée,
> ce serait un mauvais montage.

Après le Beam Search, le Chef passe encore plusieurs **corrections de
rythme** : mettre la cloture à la fin, jamais 3 plats longs d'affilée,
casser deux scènes A-roll consécutives avec une B-roll entre les deux,
alternance stricte des clips (ABAB… pour 2 clips).

---

### Voie C — Le menu simple (werbespot, social_media, dokumentar)

Pas d'arc (`_baue_einfachen_schnitt`,
[`ai.py:1161`](../../backend/api/ai.py#L1161)). Si le poids dialogue est fort
(documentaire), on trie **chronologiquement**. Sinon, on trie par **énergie
décroissante**. Puis on favorise quand même le changement de clip.

### Mot-clé
**BEAM SEARCH (LARGEUR 3) — ORDONNANCEMENT DE LA SÉQUENCE**

---

## Étape 9 — L'avis du critique gastronomique (optionnel)

### Histoire
Le Chef peut, s'il le souhaite, soumettre son menu à un **critique
gastronomique** — un grand modèle de langage (LLM) — pour qu'il réordonne
les plats (`_llm_verfeinern`, [`ai.py:1555`](../../backend/api/ai.py#L1555)).

### 🔬 Deep dive — Une abstraction pour 4 critiques

`_llm_call_async` ([`ai.py:1210`](../../backend/api/ai.py#L1210)) est une
interface unique devant **quatre** modèles : **Claude** (Anthropic),
**GPT-4o** (OpenAI), **Gemini** (Google) et **Ollama/LLaMA3** (local). En
mode `auto`, le premier disponible est choisi (selon les clés API).

On envoie au modèle toutes les métadonnées des scènes ; il répond **seulement
par un tableau JSON d'indices**, par ex. `[2, 0, 5, 3, 1]`.
`_parse_llm_response` extrait ce tableau de façon robuste et le **vérifie** :
si le LLM renvoie n'importe quoi, son résultat est **rejeté** et l'ordre
algorithmique est conservé. Le critique peut donc seulement **améliorer**,
jamais casser.

### ⚠️ Point crucial pour la défense — désactivé par défaut

`llm_aktiviert = False` **par défaut**. Pourquoi ? La **reproductibilité**.
Si un LLM externe réordonne le menu, le résultat dépend du modèle et de son
échantillonnage aléatoire. L'algorithme cœur (étape 8), lui, est
**déterministe** : même entrée → même menu. C'est un **argument fort** :
ton système n'est pas une boîte noire dépendante d'un service externe.

### Mot-clé
**RAFFINEMENT LLM MULTI-PROVIDER (OPTIONNEL, DÉSACTIVÉ PAR DÉFAUT)**

---

## Étape 10 — Dresser les assiettes

### Histoire
Le menu est ordonné. Le Chef le transforme en **assiettes concrètes** —
les segments de la timeline.

D'abord, `_merge_kontinuierliche_szenen`
([`ai.py:1628`](../../backend/api/ai.py#L1628)) fusionne les **coupes
fantômes** : si deux scènes voisines viennent du même clip et s'enchaînent
sans trou (< 0,2 s), la coupe entre elles est invisible — on les fusionne.

Ensuite, `_baue_timeline` ([`ai.py:1659`](../../backend/api/ai.py#L1659))
crée, pour chaque scène, **deux segments** : un sur la piste vidéo `v1`, un
sur la piste audio `a1` (le « miroir » audio). Chaque segment porte :
`start` (sa position dans la timeline), `dauer`, `mediaStart` (l'instant de
départ dans le clip source), `clip_id`, `rolle`, `energie`, une couleur (une
par clip) et un `groupId` qui lie le segment vidéo et son segment audio.

> Si l'option **beat-sync** est active, la durée de chaque segment est
> « aimantée » sur le prochain temps musical. La piste du premier clip est
> analysée par `librosa.beat.beat_track` (Ellis, 2007 — suivi de tempo par
> programmation dynamique). Un montage rythmiquement aligné « tombe juste »
> sur la musique.

### Mot-clé
**FUSION DES COUPES FANTÔMES + SEGMENTS V1/A1**

---

## Étape 11 — La fiche de dégustation : les 3 notes

### Histoire
Comment savoir si le menu est **bon** ? Le Chef calcule **3 notes
objectives** (`_berechne_metriken`,
[`ai.py:1788`](../../backend/api/ai.py#L1788)) :

| Métrique        | Ce qu'elle mesure                                              |
|-----------------|----------------------------------------------------------------|
| `diversitaet`   | distance cosinus CLIP moyenne entre plats voisins (variété)    |
| `wechselrate`   | proportion de transitions où le clip source change             |
| `dialog_treue`  | proportion de coupes qui **ne tombent pas** en plein mot       |

### 🔬 Deep dive — Pourquoi ces métriques sont importantes

La thèse ne peut pas (faute de temps) mener une **étude utilisateur**
formelle. Ces 3 métriques sont le **substitut quantitatif** : elles rendent
la qualité du montage **mesurable**, au lieu de juste l'affirmer.

`dialog_treue` est particulièrement parlante : elle vérifie concrètement
qu'un instant de coupe ne tombe pas strictement à l'intérieur de
l'intervalle `(début, fin)` d'un mot transcrit par Whisper. C'est une mesure
**directe et objective** de la propreté audio du montage.

C'est ton **contre-argument** à la critique « pas d'évaluation ».

### Phrase à mémoriser pour la défense (allemand)

> *„Da eine formale Nutzerstudie aus Zeitgründen nicht möglich war, berechne
> ich drei objektive Metriken auf jeder erzeugten Sequenz: visuelle
> Diversität (CLIP-Kosinus-Abstand), Clip-Wechselrate und Dialog-Treue. Sie
> ersetzen die Nutzerstudie nicht vollständig, liefern aber eine messbare,
> nachvollziehbare Selbstbewertung der Schnittqualität."*

### Mot-clé
**3 MÉTRIQUES D'ÉVALUATION OBJECTIVES**

---

## Étape 12 — Imprimer le menu

### Histoire
Dernière étape. Le Chef enregistre le menu fini : une ligne dans la table
`timelines` de PostgreSQL ([`ai.py:2247`](../../backend/api/ai.py#L2247)).
Le champ `daten` (JSON) contient tous les segments, la répartition des rôles,
les 3 métriques, la méthode de scoring et les infos beat-sync.

La réponse renvoyée au navigateur contient `timeline_id`, le nombre de
segments, la durée totale et les métriques. L'éditeur charge alors la
timeline — et le client peut la voir, la modifier, puis l'exporter
(Phase 4).

### Mot-clé
**SAUVEGARDE DE LA TIMELINE**

---

## 🎬 Récapitulatif — l'état des données après la Phase 3

- **PostgreSQL · table `timelines`** : une nouvelle ligne avec le plan de
  montage complet.
- **Tables `clips` et `szenen`** : **inchangées** — la Phase 3 lit seulement,
  elle n'écrit rien sur les scènes.
- **Disque dur** : **inchangé** — aucune vidéo n'a encore été produite.

La timeline est un **plan**. Seule la Phase 4 en fera une vraie vidéo.

---

## Les limites honnêtes (à reconnaître devant le prof)

Reconnaître ses limites est une **force** en soutenance, pas une faiblesse :

- Les **proportions de l'arc** (25 % / 20 % / 25 % / 1-2 climax / cloture) et
  les **poids de `_sequence_score`** sont heuristiques, non validés sur un
  jeu de données.
- Les **seuils de classification** A-roll / B-roll / establishing sont
  empiriques.
- Les 3 métriques sont une **auto-évaluation** — il manque une comparaison à
  un montage humain et une étude utilisateur.
- Le `beat_sync` suppose un matériau musical **homogène** (une même
  performance). Pour du matériau mixte, il faudrait des beats par clip.

Tous ces points sont prévus comme **extensions** dans le chapitre *Ausblick*
(perspectives) de la thèse.

---

## Mini-glossaire des termes techniques

| Terme | Traduction simple |
|---|---|
| **Embedding** | Un vecteur de 512 chiffres qui résume une image — sa « signature » |
| **Similarité cosinus** | L'angle entre deux vecteurs ; mesure à quel point ils « pointent dans la même direction » (0 = différents, 1 = identiques) |
| **Zero-Shot** | Classer quelque chose sans avoir entraîné de classifieur, juste en comparant à des descriptions textuelles |
| **Hyperparamètre** | Un réglage choisi à la main avant le calcul (par ex. un poids 0.40). « Heuristique » = choisi par intuition |
| **Beam Search** | Algorithme de recherche qui garde *k* solutions partielles en parallèle au lieu d'une seule |
| **Beam width** | Le nombre *k* de solutions gardées en parallèle (ici : 3) |
| **Algorithme gourmand** | Qui prend toujours le meilleur choix immédiat, sans anticiper la suite |
| **MMR** | « Maximal Marginal Relevance » : sélection qui équilibre pertinence et diversité |
| **A-Roll / B-Roll** | A-Roll = prise principale (interview, parole) ; B-Roll = plan de coupe (décor, action) |
| **Arc narratif** | La courbe dramatique : ouverture → montée → climax → résolution |
| **Union-Find** | Structure de données qui regroupe des éléments en « ensembles disjoints » |
| **Chroma-features** | Représentation audio des 12 hauteurs de notes — robuste pour comparer de la musique |
| **Déterministe** | Même entrée → toujours le même résultat (l'opposé d'aléatoire) |
| **Timeline** | La ligne de temps du montage : la suite ordonnée des segments vidéo/audio |

---

## Résumé en une phrase

> La Phase 3 charge les scènes analysées depuis la base, note chacune par
> classification CLIP zero-shot, découpe les scènes trop longues aux pauses
> de parole, les classe par rôle narratif et dramatique, les ordonne par
> Beam Search (ou MMR si demande spéciale) en une séquence dramaturgiquement
> cohérente, en construit des segments vidéo et audio, mesure la qualité avec
> 3 métriques objectives, et sauvegarde le tout comme timeline dans
> PostgreSQL — le tout sans jamais produire une seule image.

**→ Suite avec [`PHASE_4_EXPORT.md`](../PHASE_4_EXPORT.md)** (version
formelle allemande ; le storytelling Phase 4 reste à écrire).

---

*Document de compréhension personnelle pour la Bachelorarbeit CinAssist.*
*Reconstruit directement depuis le code source. Mise à jour : 2026-05-22.*
