/**
 * CinAssist smart drag-drop placement — collision detection & track escalation.
 *
 * Pure model helpers (no React, no DOM). The Editor uses these to decide WHERE a
 * dropped clip lands: honour the target time, avoid overlapping an occupied slot,
 * and escalate to the next track (creating one if allowed) when the slot is full.
 *
 * All times are in seconds (the Editor's TLClip unit). The engine layer converts
 * to integer frames at its boundary; keeping the same seconds here means the
 * overlap test matches what the timeline actually renders.
 *
 * NOTE: `src/lib/timeline-model.ts#normalize` THROWS on any same-track overlap —
 * it is never auto-resolved. So every placement produced here MUST be collision
 * free on its target video track AND (for clips with audio) its audio track.
 */

import type { TLClip } from "./timeline-model";

export type PlacementKind = "video" | "audio";

/** Track index a clip occupies for the given kind. Mirrors the engine convention:
 *  audio defaults to the clip's video track when no explicit audioTrackIndex. */
function trackIndexOf(c: TLClip, kind: PlacementKind): number {
  return kind === "video"
    ? c.videoTrackIndex ?? 0
    : c.audioTrackIndex ?? c.videoTrackIndex ?? 0;
}

/**
 * True if placing [start, start+duration) on `trackIndex` (of the given kind)
 * would overlap any existing clip already on that track.
 *
 * `ignoreTlId` excludes a clip from the test — required when MOVING an existing
 * clip so it does not collide with its own current position.
 */
export function hasCollision(
  clips: TLClip[],
  trackIndex: number,
  kind: PlacementKind,
  start: number,
  duration: number,
  ignoreTlId?: string,
): boolean {
  // Tolérance floating point : ~1 frame (33ms). Les edges qui se touchent à
  // ±33ms sont considérés adjacents (pas de collision). Évite les fausses
  // collisions causées par le cumul d'imprécisions px→sec→frame→sec.
  const EPS = 0.034;
  const end = start + duration;
  return clips.some((c) => {
    if (ignoreTlId && c.tlId === ignoreTlId) return false;
    if (kind === "audio" && !c.hasAudio) return false;
    if (trackIndexOf(c, kind) !== trackIndex) return false;
    // Half-open interval overlap test with EPS tolerance on touching edges.
    return start + EPS < c.start + c.duration && c.start + EPS < end;
  });
}

/**
 * Find a free track for [start, start+duration) of the given kind.
 * - Returns an existing track index (0..currentCount-1) whose slot is free.
 * - Returns "add" when all existing tracks are occupied but we may still create
 *   a new one (currentCount < maxTracks) — caller bumps the track count and
 *   places on index === currentCount.
 * - Returns null when every track is full and we have hit maxTracks.
 *
 * `preferred` (optional) is tried first so escalation stays close to the user's
 * intended track before scanning from 0.
 */
export function findFreeTrack(
  clips: TLClip[],
  kind: PlacementKind,
  start: number,
  duration: number,
  maxTracks: number,
  currentCount: number,
  ignoreTlId?: string,
  preferred?: number,
): number | "add" | null {
  if (
    preferred != null &&
    preferred >= 0 &&
    preferred < currentCount &&
    !hasCollision(clips, preferred, kind, start, duration, ignoreTlId)
  ) {
    return preferred;
  }
  for (let i = 0; i < currentCount; i++) {
    if (!hasCollision(clips, i, kind, start, duration, ignoreTlId)) return i;
  }
  if (currentCount < maxTracks) return "add";
  return null;
}

/** End (seconds) of the last clip on a video track — used to append at track tail. */
export function videoTrackEnd(clips: TLClip[], vIdx: number): number {
  return clips.reduce(
    (m, c) => ((c.videoTrackIndex ?? 0) === vIdx ? Math.max(m, c.start + c.duration) : m),
    0,
  );
}

/** End (seconds) of the last audio clip on an audio track. */
export function audioTrackEnd(clips: TLClip[], aIdx: number): number {
  return clips.reduce(
    (m, c) =>
      c.hasAudio && (c.audioTrackIndex ?? c.videoTrackIndex ?? 0) === aIdx
        ? Math.max(m, c.start + c.duration)
        : m,
    0,
  );
}

export interface PlacementPlan {
  ok: boolean;
  /** Why placement failed (for the toast). */
  reason?: "video-full" | "audio-full";
  /** Clamped, frame-aligned start time actually used. */
  start: number;
  /** Resolved video track index. */
  videoTrackIndex: number;
  /** Resolved audio track index (only meaningful when hasAudio). */
  audioTrackIndex: number;
  /** A new V-track must be created (index === videoTrackIndex). */
  addVideoTrack: boolean;
  /** A new A-track must be created (index === audioTrackIndex). */
  addAudioTrack: boolean;
}

/**
 * Compute the full placement decision for dropping/moving a clip at `dropTime`
 * on the intended video track. Video and audio escalate INDEPENDENTLY:
 * the video finds its slot first, then the audio (defaulting to the resolved
 * video track) finds its own — so a clip whose video fits on V1 can still have
 * its audio pushed to A2 if A1 is busy.
 *
 * Does not mutate anything — the caller applies the plan (bump track counts,
 * set clip fields) inside its state update + snapshot.
 */
export function planPlacement(params: {
  clips: TLClip[];
  intendedVideoTrack: number;
  dropTime: number;
  duration: number;
  hasAudio: boolean;
  numVideoTracks: number;
  numAudioTracks: number;
  maxTracks: number;
  ignoreTlId?: string;
}): PlacementPlan {
  const {
    clips,
    intendedVideoTrack,
    dropTime,
    duration,
    hasAudio,
    numVideoTracks,
    numAudioTracks,
    maxTracks,
    ignoreTlId,
  } = params;

  const start = Math.max(0, dropTime);

  const base: PlacementPlan = {
    ok: false,
    start,
    videoTrackIndex: intendedVideoTrack,
    audioTrackIndex: intendedVideoTrack,
    addVideoTrack: false,
    addAudioTrack: false,
  };

  // ── Video ─────────────────────────────────────────────────────────
  let videoTrackIndex: number;
  let addVideoTrack = false;
  if (!hasCollision(clips, intendedVideoTrack, "video", start, duration, ignoreTlId)) {
    videoTrackIndex = intendedVideoTrack;
  } else {
    const free = findFreeTrack(
      clips, "video", start, duration, maxTracks, numVideoTracks, ignoreTlId,
    );
    if (free === null) return { ...base, reason: "video-full" };
    if (free === "add") {
      videoTrackIndex = numVideoTracks;
      addVideoTrack = true;
    } else {
      videoTrackIndex = free;
    }
  }

  // ── Audio (independent) ───────────────────────────────────────────
  let audioTrackIndex = videoTrackIndex;
  let addAudioTrack = false;
  if (hasAudio) {
    const preferred = videoTrackIndex; // mirror the resolved video track by default
    const inRange = preferred < numAudioTracks;
    if (inRange && !hasCollision(clips, preferred, "audio", start, duration, ignoreTlId)) {
      audioTrackIndex = preferred;
    } else {
      const free = findFreeTrack(
        clips, "audio", start, duration, maxTracks, numAudioTracks, ignoreTlId, preferred,
      );
      if (free === null) return { ...base, videoTrackIndex, addVideoTrack, reason: "audio-full" };
      if (free === "add") {
        audioTrackIndex = numAudioTracks;
        addAudioTrack = true;
      } else {
        audioTrackIndex = free;
      }
    }
  }

  return {
    ok: true,
    start,
    videoTrackIndex,
    audioTrackIndex,
    addVideoTrack,
    addAudioTrack,
  };
}
