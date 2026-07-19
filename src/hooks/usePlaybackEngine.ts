"use client";

/**
 * usePlaybackEngine — React binding for the framework-agnostic PlaybackEngine.
 *
 * Golden rule: the timeline is the sole source of truth; the engine owns the
 * one MasterClock that produces `t` in integer frames. `currentFrame` flows OUT
 * of the engine into React — React never writes a <video>.currentTime to drive
 * playback state. See skill: cinassist-nle-architecture.
 *
 * Lifecycle: the engine is instantiated ONCE (on mount, once the container is
 * in the DOM) and destroyed on unmount. Timeline edits are pushed via
 * `engine.setTimeline(...)` — never a re-instantiation, which would tear down
 * the <video> pool and reintroduce black frames.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import type { Frames, EngineTimeline } from "@/lib/timeline-model";
import { PlaybackEngine } from "@/lib/playback-engine";

export function usePlaybackEngine(opts: {
  timeline: EngineTimeline | null;
  containerRef: React.RefObject<HTMLDivElement | null>;
}): {
  engine: PlaybackEngine | null;
  currentFrame: Frames;
  isPlaying: boolean;
  /** Set of clip.src currently loading (added by timeline, not yet canplay).
   *  Consumers can render a spinner on clips whose src is in this set. */
  loadingSrcs: ReadonlySet<string>;
  play(): void;
  pause(): void;
  seek(frame: Frames): void;
} {
  const { timeline, containerRef } = opts;
  const engineRef = useRef<PlaybackEngine | null>(null);
  const [engine, setEngine] = useState<PlaybackEngine | null>(null);
  const [currentFrame, setCurrentFrame] = useState<Frames>(0);
  const [isPlaying, setIsPlaying] = useState(false);
  const [loadingSrcs, setLoadingSrcs] = useState<ReadonlySet<string>>(new Set());
  const readySrcsRef = useRef<Set<string>>(new Set());

  // Instantiate once, on mount, after the container div exists. The clock fps
  // is fixed at construction, so we seed it from the current timeline (or a
  // sane default); later timeline swaps go through setTimeline.
  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;
    const initial: EngineTimeline = timeline ?? { fps: 30, tracks: [] };
    const eng = new PlaybackEngine(initial, container);
    eng.onFrame = (f) => setCurrentFrame(f);
    eng.onSlotReady = (src) => {
      readySrcsRef.current.add(src);
      setLoadingSrcs((prev) => {
        if (!prev.has(src)) return prev;
        const next = new Set(prev);
        next.delete(src);
        return next;
      });
    };
    engineRef.current = eng;
    setEngine(eng);
    eng.seek(0); // render the first frame immediately, even while paused
    return () => {
      eng.destroy();
      engineRef.current = null;
      setEngine(null);
      readySrcsRef.current.clear();
      setLoadingSrcs(new Set());
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Push timeline changes into the running engine (no re-instantiation).
  // Also track which clip srcs are added-but-not-yet-ready → "loading".
  useEffect(() => {
    if (!engine || !timeline) return;
    engine.setTimeline(timeline);
    const wanted = new Set<string>();
    for (const track of timeline.tracks) {
      for (const clip of track.clips) if (clip.src) wanted.add(clip.src);
    }
    setLoadingSrcs((prev) => {
      const next = new Set<string>();
      for (const src of wanted) {
        if (!readySrcsRef.current.has(src)) next.add(src);
      }
      // no-op if the set is unchanged
      if (next.size === prev.size && [...next].every((s) => prev.has(s))) return prev;
      return next;
    });
  }, [engine, timeline]);

  const play = useCallback(() => {
    engineRef.current?.play();
    setIsPlaying(true);
  }, []);

  const pause = useCallback(() => {
    engineRef.current?.pause();
    setIsPlaying(false);
  }, []);

  const seek = useCallback((frame: Frames) => {
    engineRef.current?.seek(frame);
  }, []);

  return { engine, currentFrame, isPlaying, loadingSrcs, play, pause, seek };
}
