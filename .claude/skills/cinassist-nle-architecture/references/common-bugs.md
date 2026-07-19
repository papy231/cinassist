# Catalogue des bugs récurrents

À lire **en premier** face à un bug de lecture ou de synchro. Presque tous les
symptômes de CinAssist remontent à une poignée de causes racines. Corriger la
cause, pas le symptôme.

Format : **Symptôme → Cause racine → Correction**.

---

## Playhead saccadé / qui avance par à-coups
**Cause :** le playhead est déplacé depuis `video.timeupdate` (≈ 4 Hz) ou depuis
un état React mis à jour trop rarement.
**Correction :** piloter le playhead depuis la `MasterClock` en
`requestAnimationFrame` (60 fps). Le playhead lit `currentFrame`, jamais
`video.currentTime`. Pour la position en pixels : `(currentFrame / fps) *
pixelsParSeconde`.

## Timeline et player désynchronisés
**Cause :** deux sources de vérité concurrentes — le temps de la `<video>` **et**
le temps de la timeline se disputent qui commande.
**Correction :** une seule horloge maîtresse. La `<video>` suit `t` (et se
corrige par drift) ; elle ne dicte jamais `t`. Supprimer tout code où
`video.currentTime` met à jour l'état de la timeline.

## Trous noirs aux changements de clip
**Cause :** un seul `<video>` dont on change `src` à la coupe ; le chargement +
seek asynchrones laissent une frame noire.
**Correction :** pool de 2 `<video>` + préchargement du clip suivant dans le
standby (~1 s à l'avance), puis bascule instantanée. Voir
`playback-engine.md` §3.

## Lecture qui hache / micro-freezes constants
**Cause :** on force `video.currentTime = …` à chaque frame en lecture → re-seek
permanent.
**Correction :** en lecture, laisser la `<video>` jouer seule ; ne recaler que si
`|video.currentTime − sourceVoulu| > seuil` (~0.15 s). Voir §4 du moteur.

## Trou noir après un seek/scrub (mais pas aux coupes)
**Cause :** le clip cible n'était pas préchauffé ; chargement direct dans
l'`active`.
**Correction :** comportement en partie inévitable après un saut arbitraire.
Atténuer en affichant la dernière frame connue jusqu'à ce que la nouvelle soit
prête (`readyState ≥ 2`), plutôt que du noir. Pour un scrub, n'afficher la frame
exacte qu'au relâchement.

## Le playhead « saute » ou l'image est en avance/retard d'une frame
**Cause :** temps stocké en secondes flottantes → drift ; ou confusion
frame/seconde dans une conversion.
**Correction :** stocker tout le temps en frames entières ; convertir en secondes
seulement au contact de la `<video>` et de l'affichage. Vérifier les `round()`.

## Après un split/trim, la mauvaise portion de vidéo s'affiche
**Cause :** `sourceIn` non recalculé lors du découpage. La partie droite d'un
split doit décaler son `sourceIn`.
**Correction :** appliquer le mapping — pour la partie droite d'un split à `t` :
`sourceIn = clip.sourceIn + (t − clip.timelineStart)`. Voir `timeline-model.md`.

## L'audio dérive de la vidéo sur les longs clips
**Cause :** audio et vidéo pilotés par deux horloges indépendantes, ou seuil de
drift trop laxiste pour l'audio.
**Correction :** aligner l'audio sur la `MasterClock` avec un seuil de recalage
plus fin que la vidéo ; planifier l'audio sur l'horloge du contexte audio.

## Le clip se remet à zéro / rejoue depuis le début à la frontière
**Cause :** à la bascule, le nouveau `<video>` n'a pas été seeké à `sourceIn` (ou
le seek a échoué car `readyState` trop bas au moment du préchargement).
**Correction :** au préchargement, seeker sur `loadedmetadata` si les métadonnées
ne sont pas encore prêtes ; revérifier la position juste après la bascule.

## L'onglet en arrière-plan désynchronise tout
**Cause :** rAF est throttlé/suspendu quand l'onglet est masqué, mais la `<video>`
peut continuer différemment.
**Correction :** l'horloge basée sur `performance.now()` se recale correctement au
retour (elle mesure le temps réel écoulé). Mettre la lecture en pause sur
`visibilitychange` si un comportement strict est souhaité.

---

## Démarche générale de diagnostic

1. **Quelle couche ?** (modèle / horloge / compositeur). Un bug de « mauvaise
   image » est souvent modèle (mapping) ; un bug de « fluidité » est horloge ;
   un bug de « noir/chargement » est compositeur.
2. **La règle d'or est-elle respectée ?** Chercher tout endroit où
   `video.currentTime` influence l'état de la timeline → c'est presque toujours
   là qu'est le bug.
3. **Frames ou secondes ?** Traquer les conversions et les `float` qui traînent
   là où il devrait y avoir des frames entières.
