/**
 * CinAssist playback engine — Layer 2 (MasterClock) + Layer 3 (compositor).
 *
 * Golden rule: the timeline is the ONLY source of truth. The MasterClock
 * produces `t` (frames). Playhead, <video> elements, everything follows `t`
 * — never the reverse. The <video> never drives timeline state.
 *
 * Adapted from the skill reference:
 * .claude/skills/cinassist-nle-architecture/assets/playback-engine.ts
 *
 * Framework-agnostic: no React in here. Hooks come in Phase 2.
 */

import {
  type Frames,
  type EngineClip,
  type EngineTrack,
  type EngineTimeline,
  framesToSeconds,
} from "./timeline-model";

// ─── Clip resolver (pure — THE fundamental mapping) ────────────────

export interface ActiveClip {
  clip: EngineClip;
  /** Wanted time IN THE SOURCE MEDIA (frames). */
  sourceFrame: Frames;
}

/**
 * For a track and timeline time `t`: the active clip and its source frame,
 * or null (= real gap in the timeline → intentional black).
 */
export function resolveClipAt(track: EngineTrack, t: Frames): ActiveClip | null {
  for (const clip of track.clips) {
    const start = clip.timelineStart;
    const end = clip.timelineStart + clip.duration;
    if (t >= start && t < end) {
      return { clip, sourceFrame: clip.sourceIn + (t - start) };
    }
  }
  return null;
}

// ─── Master clock (drives EVERYTHING, via rAF) ─────────────────────

/**
 * One clock for the whole app. Advances on wall time (`performance.now()`)
 * during playback — not a per-frame counter, which would accumulate error.
 * The UI playhead reads `currentFrame`; it NEVER reads `video.currentTime`.
 */
export class MasterClock {
  currentFrame: Frames = 0;
  playing = false;

  private raf = 0;
  private wallStart = 0;
  private frameStart = 0;
  private fps: number;

  /** Called every rAF tick with the current time. */
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

  /** Scrub / timeline click: move `t`, everything follows. */
  seek(frame: Frames) {
    this.currentFrame = frame;
    this.frameStart = frame;
    this.wallStart = performance.now();
    this.onTick(frame); // immediate render, even while paused
  }
}

// ─── Video pool (kills black frames via preloading) ────────────────

export interface EngineMediaError {
  /** src of the media that failed. */
  src: string;
  mediaError: MediaError | null;
}

/**
 * Multi-slot video pool: ONE `<video>` element per unique clip.src in the
 * timeline. All slots are preloaded + seeked to their entry point when the
 * timeline is warmed → arbitrary seek across clips = swap opacity, ZERO
 * load latency. Trade-off: memory scales with the number of unique clips
 * (~5–20 MB per element, well within browser limits up to ~30 concurrent
 * <video>s in Chrome).
 *
 * If a clip is asked for that hasn't been warmed yet (edge case), it's
 * created on the fly — the first frame may be black briefly.
 */
export class VideoPool {
  private container: HTMLElement;
  /** One video per unique src. */
  private slots = new Map<string, HTMLVideoElement>();
  /** Currently visible video (null if none picked yet). */
  active: HTMLVideoElement | null = null;

  // ── Single-source audio state (multi-track, this phase) ────────────
  // Exactly ONE slot is unmuted at a time: the winning video track's clip.
  // All other slots stay muted. `null` mutes everything. Master volume/mute
  // gate the whole pool. Applied to a slot on creation and on every setAudio().
  private unmutedSrc: string | null = null;
  private masterVolume = 1;
  private masterMuted = false;

  /** Media error hook (set by PlaybackEngine). */
  onError: (err: EngineMediaError) => void = () => {};

  /** Fired once per slot when it becomes playable (readyState ≥ 3, canplay). */
  onSlotReady: (src: string) => void = () => {};

  constructor(container: HTMLElement) {
    this.container = container;
  }

  private makeVideo(src: string): HTMLVideoElement {
    const v = document.createElement("video");
    v.muted = true; // audio is handled separately (AudioContext, Phase 3)
    v.playsInline = true;
    v.preload = "auto";
    Object.assign(v.style, {
      position: "absolute",
      inset: "0",
      width: "100%",
      height: "100%",
      objectFit: "contain",
      transition: "opacity 0ms", // hard cut, no crossfade by default
      opacity: "0",
    });
    v.addEventListener("error", () => {
      this.onError({ src, mediaError: v.error });
    });
    v.src = src;
    v.load();
    return v;
  }

  /** Get or create a slot for this src. Also seeks it to sourceSeconds if fresh. */
  private ensureSlot(src: string, sourceSeconds: number): HTMLVideoElement {
    let v = this.slots.get(src);
    if (!v) {
      v = this.makeVideo(src);
      this.container.append(v);
      this.slots.set(src, v);
      this.applyAudioTo(src, v); // respect current single-source audio state
      const doSeek = () => {
        try { v!.currentTime = sourceSeconds; } catch { /* too early */ }
      };
      if (v.readyState >= 1) doSeek();
      else v.addEventListener("loadedmetadata", doSeek, { once: true });

      // "Ready" logic. Trois voies (whichever fires first) :
      //   A) `canplaythrough` — le browser garantit lecture sans pause.
      //   B) full buffer (`end >= duration - 0.25s`) via `progress`.
      //   C) timeout de sécurité (8s) si `readyState >= 2` (HAVE_CURRENT_DATA).
      // Motivation : les browsers throttlent `preload=auto` sur les gros
      // fichiers → le buffer plafonne et la voie B ne se déclenche jamais.
      // `canplay` fire trop tôt (lag au 1er scrub), mais `canplaythrough` est
      // le vrai signal "prêt" et évite le lag comme le "Lädt" éternel.
      let fired = false;
      const emit = () => {
        if (fired) return;
        fired = true;
        v!.removeEventListener("progress", onProgress);
        v!.removeEventListener("canplaythrough", emit);
        v!.removeEventListener("loadeddata", onProgress);
        clearTimeout(timeoutId);
        this.onSlotReady(src);
      };
      const isFullyBuffered = () => {
        const el = v!;
        if (!el.duration || !isFinite(el.duration)) return false;
        if (el.buffered.length === 0) return false;
        return el.buffered.end(el.buffered.length - 1) >= el.duration - 0.25;
      };
      const onProgress = () => { if (isFullyBuffered()) emit(); };
      v.addEventListener("progress", onProgress);
      v.addEventListener("canplaythrough", emit);
      v.addEventListener("loadeddata", onProgress);
      // Safety-net : après 8s, si le browser a au moins des données courantes,
      // on considère le clip prêt (auto-preload throttlé par le navigateur).
      const timeoutId = setTimeout(() => {
        if (v!.readyState >= 2) emit();
      }, 8000);
      // Check immédiat (cache hit).
      onProgress();
    }
    return v;
  }

  /**
   * Warm the pool: create + preload a slot per unique clip. Idempotent.
   * Removes slots for srcs no longer in the timeline (drop obsolete clips).
   */
  warm(clips: Array<{ src: string; sourceInSec: number }>) {
    const wanted = new Set<string>();
    for (const c of clips) {
      if (!c.src) continue;
      wanted.add(c.src);
      this.ensureSlot(c.src, c.sourceInSec);
    }
    // Drop slots no longer in the timeline (avoid unbounded memory growth).
    for (const [src, v] of this.slots) {
      if (!wanted.has(src)) {
        if (v === this.active) this.active = null;
        v.pause();
        v.remove();
        this.slots.delete(src);
      }
    }
  }

  /**
   * Show the slot for this src. Creates it on the fly if not warmed yet
   * (fallback for edge cases). Returns true if the slot has decoded pixels
   * ready to display (readyState ≥ 2).
   *
   * Side effect: hides the previous active (opacity 0, pauses it).
   */
  setActive(src: string, sourceSeconds: number): boolean {
    const v = this.ensureSlot(src, sourceSeconds);
    if (this.active && this.active !== v) {
      this.active.style.opacity = "0";
      this.active.pause();
    }
    v.style.opacity = "1";
    this.active = v;
    return v.readyState >= 2;
  }

  /**
   * Single-source audio: unmute ONLY the slot for `unmutedSrc`, mute all
   * others. `null` mutes everything (gap, or the winning track is UI-muted).
   * Master volume/mute gate the whole pool.
   */
  setAudio(unmutedSrc: string | null, opts?: { volume?: number; muted?: boolean }) {
    if (opts?.volume != null) this.masterVolume = opts.volume;
    if (opts?.muted != null) this.masterMuted = opts.muted;
    this.unmutedSrc = unmutedSrc;
    for (const [src, v] of this.slots) this.applyAudioTo(src, v);
  }

  private applyAudioTo(src: string, v: HTMLVideoElement) {
    const on = src === this.unmutedSrc && !this.masterMuted;
    v.muted = !on;
    v.volume = this.masterVolume;
  }

  /** Hide the active (used when the timeline has a real gap → intentional black). */
  hideActive() {
    if (this.active) {
      this.active.style.opacity = "0";
      this.active.pause();
    }
  }

  /** Src of the currently-visible video, or undefined. */
  srcOfActive(): string | undefined {
    if (!this.active) return undefined;
    for (const [src, v] of this.slots) if (v === this.active) return src;
    return undefined;
  }

  /** The <video> element currently emitting audio (winning track), or null. */
  getUnmutedElement(): HTMLVideoElement | null {
    if (!this.unmutedSrc || this.masterMuted) return null;
    return this.slots.get(this.unmutedSrc) ?? null;
  }

  destroy() {
    for (const v of this.slots.values()) {
      v.pause();
      v.remove();
    }
    this.slots.clear();
    this.active = null;
  }
}

// ─── The engine (clock + resolver + pool) ──────────────────────────

/**
 * AudioPool — one detached <audio> element per audio-only source (music, voice
 * files). Same doctrine as the video pool: the element FOLLOWS the master clock
 * (`t` → currentTime), it never drives it. While playing we let the element run
 * and only re-seek past a drift threshold; while paused we pin currentTime.
 * Elements that are not active at `t` are paused.
 */
class AudioPool {
  private elems = new Map<string, HTMLAudioElement>();
  private driftThreshold: number;

  constructor(driftThreshold: number) {
    this.driftThreshold = driftThreshold;
  }

  private get(src: string): HTMLAudioElement {
    let a = this.elems.get(src);
    if (!a) {
      a = document.createElement("audio");
      a.preload = "auto";
      a.src = src;
      a.crossOrigin = "anonymous";
      this.elems.set(src, a);
    }
    return a;
  }

  /** Preload sources (called from warmPool). */
  warm(srcs: string[]) {
    for (const s of srcs) this.get(s);
    for (const [src, a] of this.elems) if (!srcs.includes(src)) { a.pause(); a.removeAttribute("src"); a.load(); this.elems.delete(src); }
  }

  /**
   * Drive one source for this tick. `wantSec` = wanted source time; `playing` =
   * master clock state; `volume` = master × fade × gain (0..1); `muted` = track/master mute.
   */
  drive(src: string, wantSec: number, playing: boolean, volume: number, muted: boolean) {
    const a = this.get(src);
    a.muted = muted;
    a.volume = Math.max(0, Math.min(1, volume));
    if (!playing) {
      if (!a.paused) a.pause();
      if (a.readyState >= 1 && Math.abs(a.currentTime - wantSec) > 0.03) a.currentTime = wantSec;
      return;
    }
    if (a.paused) {
      a.currentTime = wantSec;
      a.play().catch(() => {});
      return;
    }
    if (a.readyState >= 2 && Math.abs(a.currentTime - wantSec) > this.driftThreshold) a.currentTime = wantSec;
  }

  /** Pause every element not in `keep` (sources active at this tick). */
  pauseOthers(keep: Set<string>) {
    for (const [src, a] of this.elems) if (!keep.has(src) && !a.paused) a.pause();
  }

  pauseAll() {
    for (const a of this.elems.values()) if (!a.paused) a.pause();
  }

  destroy() {
    for (const a of this.elems.values()) { a.pause(); a.removeAttribute("src"); a.load(); }
    this.elems.clear();
  }
}


export class PlaybackEngine {
  private timeline: EngineTimeline;
  private clock: MasterClock;
  private pool: VideoPool;
  private audioPool: AudioPool;

  /** Drift threshold (seconds) beyond which the <video> is re-seeked.
   *  Too small = constant re-seeks (stutter). ~0.15s is a good start. */
  private driftThreshold = 0.15;

  /** How far ahead the next clip is preloaded (seconds). */
  private preloadLookahead = 1.0;

  /** Last measured drift (ms) on the active video, null if not measurable. */
  private lastDriftMs: number | null = null;

  /**
   * Per-track UI state, keyed by EngineTrack.id (`v0`, `v1`, …). Lives OUTSIDE
   * the timeline model on purpose: the golden rule keeps the timeline as the
   * sole source of truth for TIME; track visibility/solo/mute is presentation
   * metadata pushed in from the UI via setTrackStates(). The compositor reads
   * it but never lets it drive the clock.
   */
  private trackStates = new Map<
    string,
    { hidden?: boolean; solo?: boolean; mute?: boolean }
  >();
  private masterVolume = 1;
  private masterMuted = false;

  /** Moves the playhead in the UI (frames). */
  onFrame: (frame: Frames) => void = () => {};

  /** Media error hook — consumers can e.g. fall back to the original
   *  videoUrl (NOT implemented here; the model decides, not the engine). */
  onError: (err: EngineMediaError) => void = () => {};

  /** Fired once per src when its slot becomes playable (canplay). Use this
   *  to hide a "loading" indicator on the clip in the UI. */
  onSlotReady: (src: string) => void = () => {};

  constructor(timeline: EngineTimeline, container: HTMLElement) {
    this.timeline = timeline;
    this.clock = new MasterClock(timeline.fps);
    this.pool = new VideoPool(container);
    this.audioPool = new AudioPool(this.driftThreshold);
    this.pool.onError = (err) => this.onError(err);
    this.pool.onSlotReady = (src) => this.onSlotReady(src);
    this.clock.onTick = (frame) => this.render(frame);
    this.warmPool();
  }

  /** Warm the pool with every unique clip.src in the timeline, seeked to
   *  its source-in. Eager preload = zero seek latency at playback time. */
  private warmPool() {
    const seen = new Set<string>();
    const clips: Array<{ src: string; sourceInSec: number }> = [];
    const audioSrcs: string[] = [];
    for (const track of this.timeline.tracks) {
      for (const clip of track.clips) {
        if (!clip.src || seen.has(clip.src)) continue;
        seen.add(clip.src);
        if (clip.audioOnly) { audioSrcs.push(clip.src); continue; }   // → AudioPool, kein <video>-Slot
        clips.push({
          src: clip.src,
          sourceInSec: framesToSeconds(clip.sourceIn, this.fps),
        });
      }
    }
    this.pool.warm(clips);
    this.audioPool.warm(audioSrcs);
  }

  get fps() {
    return this.timeline.fps;
  }
  get currentFrame(): Frames {
    return this.clock.currentFrame;
  }
  get isPlaying() {
    return this.clock.playing;
  }
  get driftMs(): number | null {
    return this.lastDriftMs;
  }

  play() {
    this.clock.play();
    this.pool.active?.play().catch(() => {});
  }

  pause() {
    this.clock.pause();
    this.pool.active?.pause();
    this.audioPool.pauseAll();
  }

  seek(frame: Frames) {
    this.clock.seek(frame);
  }

  private scrubTimer: number | null = null;

  /**
   * Scrub audio kick — briefly plays the active <video> for `ms` milliseconds
   * without touching the master clock. Called from the UI's mousemove during a
   * playhead drag to let the user hear the audio at the scrubbed position.
   * Safe to call rapidly: subsequent kicks reset the auto-pause timer.
   */
  scrubAudioKick(ms: number = 80) {
    const v = this.pool.active;
    if (!v || v.readyState < 2) return;
    if (this.masterMuted) return;
    // Unmute + master volume (no fade/gain multipliers during scrub — safest).
    v.muted = false;
    v.volume = this.masterVolume;
    v.play().catch(() => {});
    if (this.scrubTimer !== null) window.clearTimeout(this.scrubTimer);
    this.scrubTimer = window.setTimeout(() => {
      if (this.pool.active === v && !this.clock.playing) v.pause();
      this.scrubTimer = null;
    }, ms);
  }

  /**
   * Push per-track UI state (hidden/solo/mute), keyed by video-track id. The
   * map is stored wholesale (consumers build a fresh Map each change) and the
   * current frame is re-rendered so a visibility/mute toggle takes effect
   * immediately, even while paused.
   */
  setTrackStates(
    map: Map<string, { hidden?: boolean; solo?: boolean; mute?: boolean }>,
  ) {
    this.trackStates = map;
    this.render(this.clock.currentFrame);
  }

  /** Master playback volume + mute (from the UI transport / Ton tab). */
  setMasterAudio(volume: number, muted: boolean) {
    this.masterVolume = volume;
    this.masterMuted = muted;
    this.render(this.clock.currentFrame);
  }

  /** The <video> element currently emitting audio (used by VU meter). */
  getUnmutedElement(): HTMLVideoElement | null {
    return this.pool.getUnmutedElement();
  }

  /**
   * Swap the timeline in place (an edit happened). The clock keeps running:
   * we re-render the current frame so the compositor reflects the new model
   * immediately. NEVER re-instantiate the engine for this — that would rebuild
   * the <video> pool and reintroduce black frames.
   *
   * The clock fps is fixed at construction; if the new timeline disagrees we
   * warn and keep the original fps rather than silently mixing frame rates.
   */
  setTimeline(next: EngineTimeline) {
    if (next.fps !== this.timeline.fps) {
      console.warn(
        `[PlaybackEngine] new timeline fps ${next.fps} differs from clock fps ${this.timeline.fps}; keeping ${this.timeline.fps}`,
      );
    }
    this.timeline = next;
    this.warmPool();
    this.render(this.clock.currentFrame);
  }

  destroy() {
    this.clock.pause();
    this.pool.destroy();
    this.audioPool.destroy();
  }

  /**
   * THE COMPOSITOR. Called every clock tick. With the multi-slot pool, every
   * unique clip.src has its own <video> preloaded at its entry point → any
   * seek is a pure opacity swap on an already-decoded element. Zero load
   * latency (subject to browser memory limits).
   *
   * Multi-track: index 0 = V1 (unterste Spur). Gewinner ist die OBERSTE sichtbare
   * Spur mit einem Clip bei `t` (V2 über V1 — wie jede NLE, wie die UI zeichnet). Hidden tracks are skipped;
   * if ANY track is soloed, only soloed tracks are considered (solo overrides
   * hide). Audio: only the winning track's <video> is unmuted (single source).
   *
   * 1) resolve the winning clip across video tracks at `t`
   * 2) switch to the right slot (opacity swap) + set single-source audio
   * 3) drift-correct the visible video
   * 4) move the playhead
   */
  private render(t: Frames) {
    const videoTracks = this.timeline.tracks.filter((tr) => tr.kind === "video");
    const anySoloed = videoTracks.some((tr) => this.trackStates.get(tr.id)?.solo);

    let active: ActiveClip | null = null;
    let winningTrackId: string | null = null;
    // NLE-Konvention (wie in der UI gezeichnet): die OBERE Spur gewinnt — V2 liegt über V1. Die Spuren sind
    // index 0 = V1 (unten) … n = oberste; wir gehen daher von oben (höchster Index) nach unten.
    for (let k = videoTracks.length - 1; k >= 0; k--) {
      const track = videoTracks[k];
      const st = this.trackStates.get(track.id);
      if (st?.hidden) continue; // hidden track → skip
      if (anySoloed && !st?.solo) continue; // solo overrides everything else
      const clip = resolveClipAt(track, t);
      if (clip) {
        active = clip;
        winningTrackId = track.id;
        break; // oberste Spur mit Clip gewinnt
      }
    }

    if (!active) {
      // real timeline gap (or all tracks hidden) → intentional black + silence
      // (audio-only tracks — music, voice files — keep playing through the AudioPool)
      this.pool.hideActive();
      this.pool.setAudio(null, { volume: this.masterVolume, muted: this.masterMuted });
      this.lastDriftMs = null;
      this.audioFallthrough = null;
      this.mixAudioOnly(t);
      this.onFrame(t);
      return;
    }

    const wantSrc = active.clip.src;
    const wantSourceSec = framesToSeconds(active.sourceFrame, this.fps);
    const srcChanged = this.pool.srcOfActive() !== wantSrc;
    const ready = this.pool.setActive(wantSrc, wantSourceSec);

    // Single-source audio: unmute only the winning track's clip. If that track
    // is muted from the UI, silence it too (setAudio(null)).
    const trackMuted = winningTrackId
      ? !!this.trackStates.get(winningTrackId)?.mute
      : false;
    // Video-only clip (cutaway): its embedded audio stays muted; the sound comes from an audio track (AudioPool).
    const videoOnly = !!active.clip.videoOnly;
    this.pool.setAudio(trackMuted || videoOnly ? null : wantSrc, {
      volume: this.masterVolume,
      muted: this.masterMuted,
    });
    // ── Audio-Fallthrough: ein stummer Overlay (Alternative/Cutaway auf V2+) gewinnt nur das BILD.
    // Der TON kommt weiter von der obersten darunterliegenden sichtbaren Spur mit Ton — sonst macht ein
    // Alternativen-Stapel die ganze Timeline stumm (Nutzer-Befund 20.08.). Getrieben über den AudioPool
    // (eigenes <audio>-Element pro src, positions-synchron), NICHT über den Video-Slot-Pool.
    this.audioFallthrough = null;
    if ((trackMuted || videoOnly) && winningTrackId) {
      for (let k = videoTracks.length - 1; k >= 0; k--) {
        const track = videoTracks[k];
        if (track.id === winningTrackId) continue;
        const st = this.trackStates.get(track.id);
        if (st?.hidden || st?.mute) continue;
        if (anySoloed && !st?.solo) continue;
        const c = resolveClipAt(track, t);
        if (!c || c.clip.videoOnly || !c.clip.src) continue;
        const fadeMult = computeFadeMultiplier(t, c.clip);
        const gainMult = c.clip.gainDb != null && c.clip.gainDb !== 0 ? Math.pow(10, c.clip.gainDb / 20) : 1;
        this.audioFallthrough = { src: c.clip.src, sec: framesToSeconds(c.sourceFrame, this.fps),
                                  vol: this.masterVolume * fadeMult * gainMult };
        break;
      }
    }

    if (srcChanged && this.clock.playing) {
      this.pool.active?.play().catch(() => {});
    }

    // Drift correction — while playing, LET the <video> run on its own;
    // only re-seek if it drifts too far. Re-seeking every frame = stutter.
    const v = this.pool.active;
    if (v && v.readyState >= 2) {
      const drift = Math.abs(v.currentTime - wantSourceSec);
      this.lastDriftMs = drift * 1000;
      // On a fresh slot switch, snap immediately regardless of the threshold
      // (the slot may be positioned at its sourceIn, not our current wantSourceSec).
      if (!this.clock.playing || srcChanged || drift > this.driftThreshold) {
        v.currentTime = wantSourceSec;
      }
    } else {
      this.lastDriftMs = null;
    }

    // preloadLookahead is retained for future use (e.g. LRU-cache growth on
    // dynamic timelines) but the multi-slot pool already keeps every clip
    // warmed up-front — no per-tick preload needed for the common case.
    void this.preloadLookahead;

    // Audio mix (audio-only) : fade ramp × clip gain (dB → multiplier).
    // Opacity is not touched — video fades are a separate feature.
    if (this.pool.active && !trackMuted && !this.masterMuted) {
      const fadeMult = computeFadeMultiplier(t, active.clip);
      const gainMult = active.clip.gainDb != null && active.clip.gainDb !== 0
        ? Math.pow(10, active.clip.gainDb / 20)
        : 1;
      const mult = fadeMult * gainMult;
      if (mult !== 1) {
        this.pool.active.volume = Math.max(0, Math.min(1, this.masterVolume * mult));
      }
    }

    this.mixAudioOnly(t);
    this.onFrame(t);
  }

  /**
   * Audio-only clips (music/voice files) on the audio tracks: for every audio
   * track resolve the clip at `t`; if it is audio-only, drive its <audio> from
   * the clock (volume = master × fade × gain, mute = track/master). Everything
   * else in the AudioPool is paused. Video-borne audio stays single-source via
   * the VideoPool — this only adds what no <video> slot can play.
   */
  /** Fallthrough-Tonquelle des aktuellen Frames (Master unter einem stummen Overlay) — von render() gesetzt,
   *  von mixAudioOnly() mitgefahren, damit pauseOthers() sie nicht abwürgt. */
  private audioFallthrough: { src: string; sec: number; vol: number } | null = null;

  private mixAudioOnly(t: Frames) {
    const keep = new Set<string>();
    if (this.audioFallthrough) {
      keep.add(this.audioFallthrough.src);
      this.audioPool.drive(this.audioFallthrough.src, this.audioFallthrough.sec, this.clock.playing,
        this.audioFallthrough.vol, this.masterMuted);
    }
    for (const track of this.timeline.tracks) {
      if (track.kind !== "audio") continue;
      const active = resolveClipAt(track, t);
      if (!active || !active.clip.audioOnly || !active.clip.src) continue;
      const st = this.trackStates.get(track.id);
      const wantSec = framesToSeconds(active.sourceFrame, this.fps);
      const fadeMult = computeFadeMultiplier(t, active.clip);
      const gainMult = active.clip.gainDb != null && active.clip.gainDb !== 0
        ? Math.pow(10, active.clip.gainDb / 20)
        : 1;
      keep.add(active.clip.src);
      this.audioPool.drive(active.clip.src, wantSec, this.clock.playing,
        this.masterVolume * fadeMult * gainMult, this.masterMuted || !!st?.mute);
    }
    this.audioPool.pauseOthers(keep);
  }
}

/**
 * Linear fade multiplier [0,1] for a clip at timeline frame `t`. Uses fadeIn /
 * fadeOut declared on the EngineClip (if any). Returns 1 when the playhead is
 * outside both ramps.
 */
function computeFadeMultiplier(t: Frames, clip: EngineClip): number {
  // Power curve: x^(2^curve). curve=0 → linear, curve>0 → ease-in (slow start),
  // curve<0 → ease-out (fast start).
  const shape = (x: number, curve?: number) => {
    if (!curve) return x;
    const p = Math.pow(2, Math.max(-1, Math.min(1, curve)));
    return Math.pow(x, p);
  };
  let mult = 1;
  if (clip.fadeInFrames && clip.fadeInFrames > 0) {
    const pos = t - clip.timelineStart;
    if (pos < clip.fadeInFrames) {
      const x = Math.max(0, pos / clip.fadeInFrames);
      mult = shape(x, clip.fadeInCurve);
    }
  }
  if (clip.fadeOutFrames && clip.fadeOutFrames > 0) {
    const posFromEnd = clip.timelineStart + clip.duration - t;
    if (posFromEnd < clip.fadeOutFrames) {
      const x = Math.max(0, posFromEnd / clip.fadeOutFrames);
      mult = Math.min(mult, shape(x, clip.fadeOutCurve));
    }
  }
  return mult;
}
