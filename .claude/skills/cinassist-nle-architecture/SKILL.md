---
name: cinassist-nle-architecture
description: >-
  Architecture de référence pour CinAssist — le montage vidéo non-linéaire (NLE)
  de type Final Cut / DaVinci que construit Pascal en Next.js avec la balise
  video HTML5.
  UTILISER DÈS QU'il est question de l'éditeur vidéo, du Schnittassistenzsystem,
  de la timeline, du playhead, de la synchro player/timeline, de clips, de scrub,
  de lecture (playback), de trous noirs, de saccades, ou d'ajouter/corriger une
  fonctionnalité de montage (split, trim, ripple, roll, slip, slide, transitions).
  Utiliser aussi quand les mots-clés apparaissent en allemand (Schnitt, Timeline,
  Wiedergabe, Abspielkopf) ou en anglais (NLE, timeline, playhead, playback engine,
  compositor, non-linear editor). Toujours consulter ce skill AVANT d'écrire du
  code lié à la lecture ou à la timeline, même si la demande semble simple : la
  plupart des bugs de CinAssist viennent d'un mauvais modèle mental corrigé ici.
---

# CinAssist — Architecture NLE

CinAssist est un système d'assistance au montage vidéo (Schnittassistenzsystem)
construit en **Next.js + `<video>` HTML5** dans le navigateur. Ce skill encode le
modèle mental correct d'un montage non-linéaire, pour que chaque intervention sur
l'éditeur reste cohérente et n'introduise pas les bugs classiques (playhead
saccadé, timeline désynchronisée, trous noirs aux frontières de clips).

## La règle d'or (à ne jamais violer)

**La timeline est la SEULE source de vérité. Une seule horloge produit le temps
`t`. Tout le reste suit `t` — jamais l'inverse.**

Le lecteur ne « joue » pas une vidéo. Il affiche *la frame calculée pour `t`*.
L'élément `<video>` n'est qu'une sortie parmi d'autres (playhead, waveforms,
audio) qui rattrape `t`. Dès qu'on inverse ce rapport — c'est-à-dire dès qu'on
pilote la timeline à partir du temps de la `<video>` — on obtient de la
désynchronisation et des saccades. C'est l'erreur d'architecture n°1.

## Le modèle en trois couches

Toute fonctionnalité de CinAssist appartient à **exactement une** de ces couches.
Avant d'écrire du code, identifier la couche : ça évite 90 % des régressions.

1. **Modèle (source de vérité)** — la structure de données de la séquence :
   pistes, clips, chaque clip = `{ src, sourceIn, timelineStart, duration }`.
   Le temps y est en **frames entières**, jamais en secondes flottantes.
   → Les opérations d'édition (split, trim, ripple…) sont de **pures
   transformations de ce modèle**. Elles ne touchent JAMAIS à la `<video>`.

2. **Horloge (le temps `t`)** — une `MasterClock` unique, pilotée par
   `requestAnimationFrame` (60 fps). C'est elle qui fait avancer `t` pendant la
   lecture et qui saute lors d'un scrub/seek.
   → Le playhead lit `t` ici. Il ne lit JAMAIS `video.currentTime`.

3. **Compositeur (le rendu)** — à chaque frame, pour le `t` courant : résoudre
   quel(s) clip(s) sont actifs, calculer le temps source voulu, positionner la
   bonne `<video>`, compositer, afficher.
   → C'est ici que vivent le pool de `<video>`, le préchargement, la correction
   de drift, et plus tard les effets/transitions.

```
   Modèle  ──lu par──►  Compositeur  ──produit──►  frame affichée
      ▲                      ▲
      │                      │ lit t
   édité par           Horloge (t) ──lu par──► Playhead UI
   les outils
```

## Le mapping fondamental (toute la synchro tient là-dessus)

Pour un clip actif au temps timeline `t` :

```
sourceFrame = clip.sourceIn + (t − clip.timelineStart)
```

`sourceIn` = point d'entrée dans le fichier source. `timelineStart` = position du
clip sur la timeline. Ce sont deux temps distincts ; les confondre casse tout.

## Workflow quand on te demande de corriger ou d'ajouter quelque chose

1. **Identifier la couche** (modèle / horloge / compositeur). Si la demande
   mélange plusieurs couches, la découper.
2. **Vérifier la règle d'or** : est-ce que la solution envisagée fait piloter la
   timeline par la `<video>` ? Si oui, c'est faux — repartir de l'horloge.
3. **Rester en frames entières** dans toute la logique ; convertir en secondes
   seulement au contact de la `<video>` (`video.currentTime`) et de l'affichage.
4. **Pour un bug de lecture/synchro**, consulter d'abord
   `references/common-bugs.md` : le catalogue relie chaque symptôme à sa cause
   racine et à sa correction. Ne pas rustiner le symptôme.
5. **Pour une opération d'édition** (split, trim, ripple, roll, slip, slide),
   consulter `references/timeline-model.md` : chacune y est décrite comme une
   transformation pure du modèle.
6. **Pour le moteur de lecture** (horloge, résolveur, pool `<video>`, drift,
   préchargement), consulter `references/playback-engine.md`. Une implémentation
   TypeScript prête à adapter est fournie dans `assets/playback-engine.ts`.

## Pièges spécifiques au web (`<video>` HTML5)

- **Ne jamais** piloter le playhead depuis l'événement `timeupdate` : il ne tire
  que ~4 fois/seconde → saccade. Piloter depuis `requestAnimationFrame`.
- **Ne jamais** faire `video.currentTime = …` à chaque frame en lecture : chaque
  écriture provoque un re-seek qui hache la lecture. En lecture, laisser la
  `<video>` jouer seule et ne corriger que si le drift dépasse un seuil (~0.15 s).
- **Un seul `<video>` = trous noirs** aux changements de clip (chargement + seek
  asynchrones). Utiliser un **pool** de 2 `<video>` et **précharger** le clip
  suivant à l'avance. Un trou noir sur un *vrai* trou de timeline est correct.
- **Drift des float** : le temps en secondes flottantes dérive. Stocker le temps
  en frames entières et ne convertir qu'au dernier moment.

## Fichiers de référence

- `references/playback-engine.md` — horloge maîtresse, résolveur, pool `<video>`,
  correction de drift, préchargement. Lire pour tout travail sur la lecture.
- `references/timeline-model.md` — modèle de données, temps en frames, et les
  opérations d'édition (split, trim, ripple, roll, slip, slide) comme
  transformations pures. Lire pour tout outil de montage.
- `references/common-bugs.md` — catalogue symptôme → cause racine → correction.
  Lire en premier face à un bug de lecture ou de synchro.
- `assets/playback-engine.ts` — implémentation de référence du moteur, à adapter
  dans le projet Next.js.
