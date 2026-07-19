# Modèle de timeline et opérations d'édition

Détail de la couche **Modèle** — la source de vérité. Principe directeur : chaque
opération de montage est une **transformation pure du modèle**. Elle ne touche
jamais la `<video>` ni l'horloge. Une fois le modèle modifié, le compositeur
affiche automatiquement le bon résultat au prochain tick.

## Sommaire
1. Structure de données
2. Le temps en frames entières
3. Les opérations d'édition
4. Invariants à préserver

---

## 1. Structure de données

```ts
type Frames = number; // temps en frames ENTIÈRES

interface Clip {
  id: string;
  src: string;            // média source
  sourceIn: Frames;       // point d'entrée DANS le source
  timelineStart: Frames;  // position SUR la timeline
  duration: Frames;       // durée sur la timeline
}

interface Track { id: string; clips: Clip[]; } // triés, sans chevauchement
interface Timeline { fps: number; tracks: Track[]; }
```

Notes :
- Il n'y a **pas** de champ `sourceOut` : il se déduit (`sourceIn + duration`).
  Un seul champ à maintenir = un invariant de moins à casser.
- `src` peut pointer plusieurs fois vers le même fichier (un même média découpé
  en plusieurs clips) : c'est normal et voulu.

## 2. Le temps en frames entières

Tout le temps interne est en frames entières. Raisons :
- Les `float` en secondes accumulent du drift → erreurs d'un demi-frame qui
  cassent la synchro et rendent les coupes imprécises.
- Les frames sont l'unité naturelle d'un NLE : une coupe tombe *sur* une frame.

Conversion, uniquement aux frontières du système :
```
secondes = frames / fps            // vers <video>.currentTime et affichage
frames   = round(secondes * fps)   // depuis une entrée en secondes
```

Timecode d'affichage `HH:MM:SS:FF` :
```
FF = frames % fps
SS = floor(frames / fps) % 60
MM = floor(frames / (fps*60)) % 60
HH = floor(frames / (fps*3600))
```

## 3. Les opérations d'édition

Chacune prend le modèle (ou une piste) et renvoie un nouveau modèle. Écrire ces
opérations **immuables** (retourner de nouveaux objets) facilite l'undo/redo :
l'historique n'est qu'une pile d'états (ou de patches) du modèle.

### Split (couper au playhead)
À la frame `t`, sur le clip actif : le remplacer par deux clips.
```
gauche  = { ...clip, duration: t − clip.timelineStart }
droite  = { ...clip,
            sourceIn:      clip.sourceIn + (t − clip.timelineStart),
            timelineStart: t,
            duration:      clip.duration − (t − clip.timelineStart) }
```
Noter le décalage de `sourceIn` sur la partie droite — c'est le §mapping
fondamental appliqué au découpage.

### Trim (rogner un bord)
Déplacer le bord d'entrée ou de sortie d'un clip **sans** décaler les voisins
(laisse un trou ou un chevauchement selon le mode).
- Trim d'entrée de `Δ` frames : `sourceIn += Δ`, `timelineStart += Δ`,
  `duration −= Δ`.
- Trim de sortie de `Δ` : `duration += Δ` (borné par le média disponible).

### Ripple (rogner + refermer)
Un trim d'entrée/sortie suivi d'un **décalage de tous les clips suivants** de la
même piste pour combler/absorber le trou. C'est le trim « qui pousse » : la durée
totale de la séquence change.

### Roll (déplacer une coupe)
Sur deux clips adjacents A|B : allonger A de `Δ` et raccourcir B de `Δ` (ou
l'inverse). La frontière bouge, la durée totale ne change pas. C'est un trim de
sortie sur A + un trim d'entrée sur B, liés.

### Slip (glisser le contenu)
Changer *ce qu'on voit* d'un clip sans changer sa position ni sa durée sur la
timeline : `sourceIn += Δ` seulement (borné par le média). La fenêtre source
glisse, la fente timeline reste identique.

### Slide (glisser la position)
Déplacer un clip le long de la timeline en ajustant les deux voisins : le clip
garde son contenu et sa durée, `timelineStart += Δ`, le voisin gauche gagne `Δ`,
le voisin droit perd `Δ` (ou l'inverse).

### Move / drag-and-drop
Changer `timelineStart` (et éventuellement de piste). Décider la politique de
collision : écraser, insérer (ripple), ou refuser.

## 4. Invariants à préserver

Après **toute** opération, vérifier (idéalement via une fonction `normalize()`
appelée en fin d'opération) :

- **Clips triés** par `timelineStart` sur chaque piste.
- **Pas de chevauchement** sur une même piste (sauf zones de transition, gérées à
  part).
- **Bornes du média** : `sourceIn ≥ 0` et `sourceIn + duration ≤` durée du média
  source. On ne peut pas montrer des frames qui n'existent pas.
- **Durée > 0** : supprimer les clips de durée nulle produits par un trim/split.
- **`fps` cohérent** : si un média a un fps différent de la séquence, décider tôt
  la politique (conformer, ou convertir les frames à la volée). Ne pas laisser
  deux référentiels de frames se mélanger silencieusement.

Tant que ces invariants tiennent, le compositeur affiche toujours quelque chose
de correct — parce qu'il ne fait que lire le modèle.
