# Moteur de lecture (playback engine)

Détail de la couche **Horloge** + **Compositeur**. Implémentation prête à adapter
dans `assets/playback-engine.ts` — ce document explique le *pourquoi* de chaque
choix pour pouvoir l'adapter sans casser les invariants.

## Sommaire
1. L'horloge maîtresse
2. Le résolveur de clips
3. Le pool de `<video>` et le préchargement
4. La correction de drift
5. Ordre des opérations dans une frame de rendu
6. Scrub / seek
7. Extensions (audio, multi-pistes, effets)

---

## 1. L'horloge maîtresse

Une seule instance dans toute l'application. Elle avance en temps réel pendant la
lecture, en se basant sur `performance.now()` (wall clock), et non sur un
compteur incrémenté frame par frame (qui accumulerait l'erreur).

Principe :

```
play():
  wallStart  = performance.now()
  frameStart = currentFrame
  boucle rAF:
    elapsedSec   = (now − wallStart) / 1000
    currentFrame = frameStart + elapsedSec * fps
    onTick(currentFrame)
```

Pourquoi rAF et pas `setInterval` : rAF est synchronisé avec le rafraîchissement
écran (~60 Hz) et se met en pause quand l'onglet est masqué. Pourquoi pas
`video.timeupdate` : il ne se déclenche que ~4 fois/seconde → playhead saccadé.

**Invariant** : le playhead de l'UI lit `currentFrame`. Il ne lit jamais
`video.currentTime`.

## 2. Le résolveur de clips

Fonction pure : `(track, t) → { clip, sourceFrame } | null`.

```
pour chaque clip de la piste:
  si clip.timelineStart ≤ t < clip.timelineStart + clip.duration:
    sourceFrame = clip.sourceIn + (t − clip.timelineStart)
    return { clip, sourceFrame }
return null   // trou → noir volontaire
```

Hypothèses : clips triés par `timelineStart`, sans chevauchement sur une même
piste. Si un jour on autorise le chevauchement (transitions), le résolveur
renvoie une liste de clips à compositer, pas un seul.

`null` n'est pas un bug : c'est un vrai trou dans la timeline → on affiche du
noir. Les trous noirs *indésirables* apparaissent aux **frontières** entre deux
clips adjacents ; ils viennent du chargement, pas du résolveur (voir §3).

## 3. Le pool de `<video>` et le préchargement

Le problème : changer `video.src` puis `video.currentTime` est asynchrone. Le
temps que le nouveau média se charge et se positionne, l'élément affiche du noir.
Aux frontières de clips, ça se voit à chaque coupe.

La solution : **deux** éléments `<video>`, `active` (visible) et `standby`
(masqué, préchauffé).

- **Préchargement** : à chaque frame, on regarde `t + lookahead` (≈ 1 s à
  l'avance). Si le clip qui sera actif à ce moment a un `src` différent du clip
  courant, on charge ce `src` dans `standby` et on le `seek` à son point
  d'entrée. Le standby est ainsi prêt bien avant la coupe.
- **Bascule** : quand `t` franchit la frontière, le `src` voulu est déjà dans le
  standby → on échange `active` ↔ `standby` (juste un swap d'opacité + de
  référence). Instantané, aucune frame noire.
- **Cas non préchauffé** (ex. juste après un seek brutal) : on charge le clip
  directement dans `active`. Un court noir est alors possible — inévitable après
  un saut arbitraire, mais absent des coupes normales.

Deux éléments suffisent pour une piste. Pour des transitions (deux clips visibles
en même temps), prévoir un pool un peu plus grand.

## 4. La correction de drift

La subtilité que presque tout le monde rate.

En lecture, la `<video>` a son propre horloge de décodage. Elle et la
`MasterClock` dérivent légèrement l'une par rapport à l'autre. Tentation : forcer
`video.currentTime = sourceSeconds` à chaque frame pour « garder synchro ».
**C'est le piège** : chaque écriture de `currentTime` déclenche un re-seek, qui
interrompt la lecture fluide → saccade permanente.

La bonne approche :

```
drift = |video.currentTime − sourceSecondsVoulu|
si (en pause) OU (drift > seuil):   // seuil ≈ 0.15 s
  video.currentTime = sourceSecondsVoulu
sinon:
  ne rien faire — laisser la vidéo jouer seule
```

- En **pause / scrub** : on recale toujours (on veut la frame exacte sous le
  playhead).
- En **lecture** : on laisse courir et on ne recale que si ça dérive trop. Régler
  le seuil : trop petit → re-seeks fréquents (saccade) ; trop grand → décalage
  audible/visible. ~0.15 s est un bon point de départ, à affiner.

## 5. Ordre des opérations dans une frame de rendu

À chaque tick de l'horloge, dans cet ordre :

1. Résoudre le clip actif à `t` (§2).
2. Si `null` → masquer l'`active` (noir). Sinon :
   a. Si le `src` voulu ≠ `src` de l'`active` → basculer (swap si le standby est
      préchauffé, sinon chargement direct).
   b. Correction de drift sur l'`active` (§4).
   c. Précharger le clip suivant dans le standby (§3).
3. Notifier l'UI pour déplacer le playhead à `t`.

## 6. Scrub / seek

Le scrub, c'est simplement `clock.seek(frame)` :

```
seek(frame):
  currentFrame = frame
  frameStart   = frame            // réancrer l'horloge
  wallStart    = performance.now()
  onTick(frame)                   // rendu immédiat, même à l'arrêt
```

Pendant un scrub à l'arrêt, on veut la frame exacte → on passe par la branche
« recalage systématique » de la correction de drift (§4). Pour un scrub très
rapide, on peut débrancher le préchargement et n'afficher que des images clés
pour rester réactif, puis afficher la frame exacte au relâchement.

## 7. Extensions

- **Audio** : même principe. Un `AudioContext` / des `AudioBufferSourceNode` (ou
  les pistes audio des `<video>`) suivent `t`. L'audio est plus sensible au drift
  que la vidéo → seuil de recalage plus fin, et de préférence planifier l'audio
  sur l'horloge du contexte audio, elle-même alignée sur la `MasterClock`.
- **Multi-pistes vidéo** : le résolveur renvoie un clip actif *par piste* ; le
  compositeur empile de la piste du bas vers celle du haut (priorité au dessus),
  ou applique des modes de fusion.
- **Effets / transitions** : se branchent dans le compositeur (étape 2 du rendu),
  après résolution et avant affichage. Ils lisent le modèle, ne le modifient pas.
