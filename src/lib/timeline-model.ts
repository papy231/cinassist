/**
 * CinAssist timeline model — Layer 1: the source of truth.
 *
 * All internal time is in INTEGER FRAMES. Floating-point seconds drift and
 * break sync; convert to seconds only at the system boundaries
 * (`video.currentTime`, display). See skill: cinassist-nle-architecture.
 *
 * Framework-agnostic: no React, no DOM.
 */

/** Time in integer frames. */
export type Frames = number;

/** Multi-track: a track is either a video track (composited) or an audio
 *  track. The compositor only walks video tracks; audio tracks exist for the
 *  model's completeness and future multi-audio mixing. */
export type TrackKind = "video" | "audio";

export interface EngineClip {
  id: string;
  /** Source media URL. */
  src: string;
  /** Entry point INTO the source media (frames). */
  sourceIn: Frames;
  /** Position ON the timeline (frames). */
  timelineStart: Frames;
  /** Duration on the timeline (frames). No sourceOut: it is derived. */
  duration: Frames;
  /** Fade-in ramp length (frames). Applied to opacity + volume linearly from 0 to 1. */
  fadeInFrames?: Frames;
  /** Fade-out ramp length (frames). Applied to opacity + volume linearly from 1 to 0. */
  fadeOutFrames?: Frames;
  /** Fade-in curve factor in [-1, 1]. 0 = linear, +1 = ease-in (slow start), -1 = ease-out. */
  fadeInCurve?: number;
  /** Fade-out curve factor in [-1, 1]. Same convention as fadeInCurve. */
  fadeOutCurve?: number;
  /** Constant clip gain in decibels. 0 dB = unity. Multiplies volume on top of master + fades. */
  gainDb?: number;
}

/** Clips sorted by timelineStart, no overlap. Enforced by normalize(). */
export interface EngineTrack {
  id: string;
  /** "video" (composited) or "audio". */
  kind: TrackKind;
  clips: EngineClip[];
}

export interface EngineTimeline {
  fps: number;
  tracks: EngineTrack[];
}

// ─── Conversions (only at system boundaries) ───────────────────────

export function secondsToFrames(sec: number, fps: number): Frames {
  return Math.round(sec * fps);
}

export function framesToSeconds(f: Frames, fps: number): number {
  return f / fps;
}

/** Display timecode `HH:MM:SS:FF`. */
export function framesToTimecode(f: Frames, fps: number): string {
  const fpsInt = Math.max(1, Math.round(fps));
  const total = Math.max(0, Math.floor(f));
  const ff = total % fpsInt;
  const ss = Math.floor(total / fpsInt) % 60;
  const mm = Math.floor(total / (fpsInt * 60)) % 60;
  const hh = Math.floor(total / (fpsInt * 3600));
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${pad(hh)}:${pad(mm)}:${pad(ss)}:${pad(ff)}`;
}

// ─── Adapter from the existing seconds-based Editor model ──────────

/**
 * Mirror of the TLClip shape in `src/components/Editor.tsx` (seconds-based).
 * Re-defined here so lib/ never reaches up into components/. Field names are
 * identical, so the Editor's TLClip is structurally assignable to this type.
 */
export type TLClip = {
  tlId: string;
  clipId: string;
  start: number;      // seconds on timeline
  mediaStart: number; // seconds into source
  duration: number;   // seconds
  name?: string;
  proxyUrl?: string | null;
  videoUrl?: string | null;
  sourceDuration?: number;
  /** Whether the clip carries an audio stream (drives the audio track split). */
  hasAudio?: boolean;
  /** Which video track this clip lives on (0 = V1 = top priority). Default 0. */
  videoTrackIndex?: number;
  /** Which audio track this clip's audio lives on. Defaults to videoTrackIndex. */
  audioTrackIndex?: number;
  /** Fade-in ramp length (seconds). Applied to opacity + volume. */
  fadeIn?: number;
  /** Fade-out ramp length (seconds). Applied to opacity + volume. */
  fadeOut?: number;
  /** Fade-in curve shape in [-1, 1]. 0 = linear. */
  fadeInCurve?: number;
  /** Fade-out curve shape in [-1, 1]. 0 = linear. */
  fadeOutCurve?: number;
  /** Clip-level gain in dB (rubber band). 0 dB = unity. */
  gainDb?: number;
};

/**
 * Converts the existing TLClip[] (seconds) into frame-based engine tracks,
 * split across the requested number of video + audio tracks.
 *
 * Returns `[...videoTracks, ...audioTracks]` with stable, position-based ids
 * (`v0`, `v1`, … then `a0`, `a1`, …) — these match the UI track ids so
 * per-track UI state (hidden/solo/mute) keys straight onto engine tracks.
 *
 * Track convention (documented for the compositor): index 0 = V1 = the
 * TOP-PRIORITY video track. The compositor walks video tracks 0→N and the
 * FIRST that has a clip at `t` wins (Premiere/simple convention).
 *
 * Video track `i` gets clips whose `videoTrackIndex === i`. Audio track `i`
 * gets clips that have audio (`hasAudio`) whose `audioTrackIndex === i`
 * (audioTrackIndex defaults to videoTrackIndex, so for now audio mirrors
 * video). Clips without a playable src are skipped.
 *
 * `opts.brokenProxies`: proxy URLs detected as defective at runtime (0-byte →
 * 416 → DEMUXER_ERROR). For those clips we fall back to the original
 * `videoUrl`, mirroring the old inline fallback in Editor.tsx.
 *
 * Duration is derived as (round(end) − round(start)) rather than round(dur):
 * rounding start and duration independently can leave a 1-frame overlap between
 * two contiguous clips (e.g. after a split), which normalize() rejects. Tiling
 * off the shared rounded boundary keeps adjacent clips exactly flush.
 */
export function tlClipsToEngineTracks(
  tlClips: TLClip[],
  fps: number,
  opts?: {
    brokenProxies?: Set<string>;
    numVideoTracks?: number;
    numAudioTracks?: number;
  },
): EngineTrack[] {
  const brokenProxies = opts?.brokenProxies;
  const numVideoTracks = Math.max(1, Math.floor(opts?.numVideoTracks ?? 1));
  const numAudioTracks = Math.max(1, Math.floor(opts?.numAudioTracks ?? 1));
  const clampIdx = (i: number, n: number) =>
    Math.max(0, Math.min(n - 1, Math.floor(i) || 0));

  type Built = { clip: EngineClip; vIdx: number; aIdx: number; hasAudio: boolean };
  const built: Built[] = [];
  for (const c of tlClips) {
    const proxyOk = c.proxyUrl && !(brokenProxies?.has(c.proxyUrl));
    const src = (proxyOk ? c.proxyUrl : c.videoUrl ?? c.proxyUrl) ?? "";
    if (!src) continue;
    const startF = secondsToFrames(c.start, fps);
    const endF = secondsToFrames(c.start + c.duration, fps);
    const vIdx = clampIdx(c.videoTrackIndex ?? 0, numVideoTracks);
    const aIdx = clampIdx(c.audioTrackIndex ?? c.videoTrackIndex ?? 0, numAudioTracks);
    const clipDurF = endF - startF;
    const fadeInF = c.fadeIn && c.fadeIn > 0 ? Math.min(clipDurF, secondsToFrames(c.fadeIn, fps)) : undefined;
    const fadeOutF = c.fadeOut && c.fadeOut > 0 ? Math.min(clipDurF, secondsToFrames(c.fadeOut, fps)) : undefined;
    const clampCurve = (v: number) => Math.max(-1, Math.min(1, v));
    const fadeInCurve = c.fadeInCurve != null ? clampCurve(c.fadeInCurve) : undefined;
    const fadeOutCurve = c.fadeOutCurve != null ? clampCurve(c.fadeOutCurve) : undefined;
    const gainDb = c.gainDb != null ? Math.max(-48, Math.min(24, c.gainDb)) : undefined;
    built.push({
      clip: {
        id: c.tlId,
        src,
        sourceIn: secondsToFrames(c.mediaStart, fps),
        timelineStart: startF,
        duration: clipDurF,
        ...(fadeInF ? { fadeInFrames: fadeInF } : {}),
        ...(fadeOutF ? { fadeOutFrames: fadeOutF } : {}),
        ...(fadeInCurve ? { fadeInCurve } : {}),
        ...(fadeOutCurve ? { fadeOutCurve } : {}),
        ...(gainDb ? { gainDb } : {}),
      },
      vIdx,
      aIdx,
      hasAudio: !!c.hasAudio,
    });
  }

  const videoTracks: EngineTrack[] = [];
  for (let i = 0; i < numVideoTracks; i++) {
    videoTracks.push(
      normalize({
        id: `v${i}`,
        kind: "video",
        clips: built.filter((b) => b.vIdx === i).map((b) => b.clip),
      }),
    );
  }
  const audioTracks: EngineTrack[] = [];
  for (let i = 0; i < numAudioTracks; i++) {
    audioTracks.push(
      normalize({
        id: `a${i}`,
        kind: "audio",
        clips: built.filter((b) => b.hasAudio && b.aIdx === i).map((b) => b.clip),
      }),
    );
  }
  return [...videoTracks, ...audioTracks];
}

// ─── Invariants ────────────────────────────────────────────────────

/**
 * Enforces the track invariants: clips sorted by timelineStart, no
 * zero/negative durations, sourceIn ≥ 0. Overlaps are NOT fixed silently —
 * they indicate a broken edit operation upstream, so we throw.
 */
export function normalize(track: EngineTrack): EngineTrack {
  const clips = track.clips
    .filter((c) => c.duration > 0)
    .map((c) => (c.sourceIn < 0 ? { ...c, sourceIn: 0 } : c))
    .sort((a, b) => a.timelineStart - b.timelineStart);

  for (let i = 1; i < clips.length; i++) {
    const prev = clips[i - 1];
    if (clips[i].timelineStart < prev.timelineStart + prev.duration) {
      throw new Error(
        `Track "${track.id}": clip "${clips[i].id}" overlaps "${prev.id}" — fix the edit operation, overlaps are never auto-resolved`,
      );
    }
  }
  return { id: track.id, kind: track.kind, clips };
}
