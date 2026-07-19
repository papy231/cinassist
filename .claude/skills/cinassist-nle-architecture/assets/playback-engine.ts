/**
 * playback-engine.ts
 * ------------------------------------------------------------------
 * Moteur de lecture pour un NLE (style Final Cut / DaVinci), pensé pour
 * le web (HTML5 <video>). C'est la BASE : horloge maîtresse, résolution
 * de clips, pool de <video> avec préchargement, correction de drift.
 *
 * Idée centrale (à ne jamais perdre de vue) :
 *   - La TIMELINE est la seule source de vérité.
 *   - L'HORLOGE produit un temps `t` (en frames entières).
 *   - Tout le reste (playhead, <video>, audio) SUIT `t`. Jamais l'inverse.
 *
 * Ce que ce fichier NE fait pas (volontairement, pour rester lisible) :
 *   - audio / mixage       -> même principe : un AudioContext suit l'horloge
 *   - effets / transitions -> se branchent dans le compositeur (render())
 *   - multi-pistes vidéo    -> ici 1 piste vidéo ; la logique s'étend
 * ------------------------------------------------------------------
 */

// ============================================================
// 1. MODÈLE DE DONNÉES  (la source de vérité)
// ============================================================

/** Temps exprimé en FRAMES entières. Jamais en secondes flottantes :
 *  les float accumulent du drift et cassent la synchro. */
export type Frames = number;

export interface Clip {
  id: string;
  src: string; // URL du média source (fichier .mp4, blob, etc.)
  /** Point d'entrée DANS LE MÉDIA SOURCE (frames). */
  sourceIn: Frames;
  /** Position du clip SUR LA TIMELINE (frames). */
  timelineStart: Frames;
  /** Durée du clip sur la timeline (frames). */
  duration: Frames;
}

export interface Track {
  id: string;
  clips: Clip[]; // triés par timelineStart, sans chevauchement sur une piste
}

export interface Timeline {
  fps: number;
  tracks: Track[]; // tracks[0] = piste du dessus (priorité au compositing)
}

/** Résultat de la résolution : quel clip est actif à un temps `t`. */
interface ActiveClip {
  clip: Clip;
  /** Temps voulu DANS LE MÉDIA SOURCE (frames). */
  sourceFrame: Frames;
}

/**
 * LE CŒUR DE LA SYNCHRO.
 * Pour une piste et un temps timeline `t`, renvoie le clip actif et le
 * temps source correspondant, ou null (= trou -> noir volontaire).
 */
function resolveClipAt(track: Track, t: Frames): ActiveClip | null {
  for (const clip of track.clips) {
    const start = clip.timelineStart;
    const end = clip.timelineStart + clip.duration;
    if (t >= start && t < end) {
      // mapping timeline -> source : LA formule fondamentale
      const sourceFrame = clip.sourceIn + (t - start);
      return { clip, sourceFrame };
    }
  }
  return null; // aucun clip ici -> le compositeur affichera du noir
}

// ============================================================
// 2. HORLOGE MAÎTRESSE  (pilote TOUT, 60 fps via rAF)
// ============================================================

/**
 * Une seule horloge dans toute l'app. Elle avance en temps réel (wall clock)
 * pendant la lecture. Le playhead et les <video> lisent `currentFrame`.
 *
 * Pourquoi rAF et pas `video.timeupdate` : timeupdate ne tire que ~4x/s
 * (playhead saccadé). rAF tire à 60 fps -> playhead fluide.
 */
export class MasterClock {
  currentFrame: Frames = 0;
  playing = false;

  private raf = 0;
  private wallStart = 0; // performance.now() au lancement de la lecture
  private frameStart = 0; // currentFrame au lancement
  private fps: number;

  /** Appelé à CHAQUE frame rAF avec le temps courant. */
  onTick: (frame: Frames) => void = () => {};

  constructor(fps: number) {
    this.fps = fps;
  }

  play() {
    if (this.playing) return;
    this.playing = true;
    this.wallStart = performance.now();
    this.frameStart = this.currentFrame;

    const loop = (now: number) => {
      if (!this.playing) return;
      const elapsedSec = (now - this.wallStart) / 1000;
      this.currentFrame = this.frameStart + elapsedSec * this.fps;
      this.onTick(this.currentFrame);
      this.raf = requestAnimationFrame(loop);
    };
    this.raf = requestAnimationFrame(loop);
  }

  pause() {
    this.playing = false;
    cancelAnimationFrame(this.raf);
  }

  /** Scrub / clic sur la timeline : on déplace `t`, tout suit. */
  seek(frame: Frames) {
    this.currentFrame = frame;
    this.frameStart = frame;
    this.wallStart = performance.now();
    this.onTick(frame); // rafraîchit playhead + rendu même à l'arrêt
  }
}

// ============================================================
// 3. POOL DE <video>  (tue les trous noirs par préchargement)
// ============================================================

/**
 * Deux éléments <video> : `active` (visible) et `standby` (préchauffé).
 * Quand on approche d'une frontière de clip, on précharge le clip suivant
 * dans `standby` et on le "seek" à son point d'entrée. Au moment de la
 * bascule, l'échange est instantané -> aucune frame noire.
 */
class VideoPool {
  private a: HTMLVideoElement;
  private b: HTMLVideoElement;
  active: HTMLVideoElement;
  private standby: HTMLVideoElement;

  /** Quel src est chargé dans quel élément (pour éviter les rechargements). */
  private srcOf = new WeakMap<HTMLVideoElement, string>();

  constructor(container: HTMLElement) {
    this.a = this.makeVideo();
    this.b = this.makeVideo();
    container.append(this.a, this.b);
    this.active = this.a;
    this.standby = this.b;
    this.active.style.opacity = '1';
    this.standby.style.opacity = '0';
  }

  private makeVideo(): HTMLVideoElement {
    const v = document.createElement('video');
    v.muted = true; // l'audio se gère séparément (AudioContext)
    v.playsInline = true;
    v.preload = 'auto';
    Object.assign(v.style, {
      position: 'absolute',
      inset: '0',
      width: '100%',
      height: '100%',
      objectFit: 'contain',
      transition: 'opacity 0ms', // bascule dure, pas de fondu par défaut
    });
    return v;
  }

  /** Charge un src dans un élément seulement s'il n'y est pas déjà. */
  private ensureSrc(v: HTMLVideoElement, src: string) {
    if (this.srcOf.get(v) === src) return;
    v.src = src;
    v.load();
    this.srcOf.set(v, src);
  }

  srcOfActive(): string | undefined {
    return this.srcOf.get(this.active);
  }

  /** Précharge un clip dans le standby et le positionne à son entrée. */
  preload(src: string, sourceSeconds: number) {
    if (this.srcOf.get(this.standby) === src) return;
    this.ensureSrc(this.standby, src);
    const doSeek = () => {
      try {
        this.standby.currentTime = sourceSeconds;
      } catch {
        /* seek trop tôt : réessayé au prochain preload */
      }
    };
    if (this.standby.readyState >= 1) doSeek();
    else this.standby.addEventListener('loadedmetadata', doSeek, { once: true });
  }

  /** Bascule active <-> standby (le standby est déjà préchauffé). */
  swap() {
    const old = this.active;
    this.active = this.standby;
    this.standby = old;
    this.active.style.opacity = '1';
    this.standby.style.opacity = '0';
    this.standby.pause();
  }

  /** Si le clip voulu n'est pas préchauffé (ex : après un seek brutal),
   *  on le charge directement dans l'active. Peut causer un court noir :
   *  inévitable après un saut, mais pas aux frontières normales. */
  forceActive(src: string, sourceSeconds: number) {
    this.ensureSrc(this.active, src);
    const doSeek = () => {
      try {
        this.active.currentTime = sourceSeconds;
      } catch {}
    };
    if (this.active.readyState >= 1) doSeek();
    else this.active.addEventListener('loadedmetadata', doSeek, { once: true });
  }
}

// ============================================================
// 4. LE MOTEUR  (assemble horloge + résolveur + pool)
// ============================================================

export class PlaybackEngine {
  private timeline: Timeline;
  private clock: MasterClock;
  private pool: VideoPool;

  /** Seuil de drift au-delà duquel on recale la <video> (en secondes).
   *  Trop petit = on re-seek en boucle (saccade). ~0.15s est un bon départ. */
  private driftThreshold = 0.15;

  /** Combien de temps à l'avance on précharge le clip suivant (secondes). */
  private preloadLookahead = 1.0;

  /** Callback pour bouger le playhead dans ton UI (en frames). */
  onFrame: (frame: Frames) => void = () => {};

  constructor(timeline: Timeline, container: HTMLElement) {
    this.timeline = timeline;
    this.clock = new MasterClock(timeline.fps);
    this.pool = new VideoPool(container);
    this.clock.onTick = (frame) => this.render(frame);
  }

  get fps() {
    return this.timeline.fps;
  }
  private toSeconds(f: Frames) {
    return f / this.fps;
  }

  play() {
    this.clock.play();
    this.pool.active.play().catch(() => {});
  }
  pause() {
    this.clock.pause();
    this.pool.active.pause();
  }
  seek(frame: Frames) {
    this.clock.seek(frame);
  }

  /**
   * LE COMPOSITEUR. Appelé à chaque frame par l'horloge.
   * 1) résout le clip actif à `t`
   * 2) recale la bonne <video> sur le bon temps source (si nécessaire)
   * 3) précharge le clip suivant
   * 4) déplace le playhead
   */
  private render(t: Frames) {
    const track = this.timeline.tracks[0]; // v1 : une seule piste vidéo
    const active = resolveClipAt(track, t);

    if (!active) {
      // trou volontaire -> noir (on masque simplement)
      this.pool.active.style.opacity = '0';
    } else {
      const wantSrc = active.clip.src;
      const wantSourceSec = this.toSeconds(active.sourceFrame);
      this.pool.active.style.opacity = '1';

      if (this.pool.srcOfActive() !== wantSrc) {
        // frontière de clip : le standby a normalement déjà été préchauffé
        // avec ce src -> bascule instantanée.
        // @ts-ignore accès interne volontaire pour la démo
        if (this.pool['srcOf'].get(this.pool['standby']) === wantSrc) {
          this.pool.swap();
          if (this.clock.playing) this.pool.active.play().catch(() => {});
        } else {
          // pas préchauffé (ex : juste après un seek) -> charge en direct
          this.pool.forceActive(wantSrc, wantSourceSec);
          if (this.clock.playing) this.pool.active.play().catch(() => {});
        }
      }

      // Correction de DRIFT — la subtilité clé :
      // en lecture, on LAISSE la <video> jouer toute seule, on ne re-seek
      // QUE si elle s'écarte trop. Re-seeker à chaque frame = saccade.
      const v = this.pool.active;
      if (v.readyState >= 2) {
        const drift = Math.abs(v.currentTime - wantSourceSec);
        if (!this.clock.playing || drift > this.driftThreshold) {
          v.currentTime = wantSourceSec;
        }
      }

      // Préchargement du clip suivant
      const lookaheadFrame = t + this.preloadLookahead * this.fps;
      const next = resolveClipAt(track, lookaheadFrame);
      if (next && next.clip.src !== wantSrc) {
        this.pool.preload(next.clip.src, this.toSeconds(next.clip.sourceIn));
      }
    }

    this.onFrame(t); // -> ton UI place le playhead à (t / fps) * pixelsParSeconde
  }
}

/* ------------------------------------------------------------------
 * INTÉGRATION (exemple)
 * ------------------------------------------------------------------
 * const timeline: Timeline = {
 *   fps: 25,
 *   tracks: [{
 *     id: 'v1',
 *     clips: [
 *       { id:'c1', src:'/media/snowden.mp4', sourceIn:0, timelineStart:0,   duration:4500 },
 *       { id:'c2', src:'/media/mlk_1min.mp4', sourceIn:0, timelineStart:4500, duration:1500 },
 *     ],
 *   }],
 * };
 *
 * const engine = new PlaybackEngine(timeline, document.getElementById('player')!);
 * engine.onFrame = (f) => {
 *   playhead.style.left = (f / engine.fps) * PIXELS_PER_SECOND + 'px';
 *   timecodeLabel.textContent = framesToTimecode(f, engine.fps);
 * };
 *
 * playButton.onclick = () => engine.play();
 * timelineRuler.onclick = (e) => engine.seek(pixelToFrame(e.offsetX));
 * ------------------------------------------------------------------ */
