"use client";

/**
 * /lab/playback — Phase 1 proof of concept for the new playback engine.
 *
 * Only job: show that the engine plays two clips seamlessly with NO black
 * frame at the boundary. Pure engineering test page, no product UI.
 */

import { useEffect, useRef, useState } from "react";
import { fetchClips } from "@/lib/api";
import {
  type EngineTimeline,
  type TLClip,
  framesToTimecode,
  tlClipsToEngineTracks,
} from "@/lib/timeline-model";
import { PlaybackEngine, resolveClipAt } from "@/lib/playback-engine";

const FPS = 25; // lab constant; proxies are conformed — real fps policy is Phase 2

export default function PlaybackLabPage() {
  const containerRef = useRef<HTMLDivElement>(null);
  const engineRef = useRef<PlaybackEngine | null>(null);

  const [timeline, setTimeline] = useState<EngineTimeline | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [mediaError, setMediaError] = useState<string | null>(null);
  const [frame, setFrame] = useState(0);
  const [driftMs, setDriftMs] = useState<number | null>(null);
  const [playing, setPlaying] = useState(false);

  // Build a 2-clip back-to-back timeline from the first two usable clips.
  useEffect(() => {
    let cancelled = false;
    fetchClips()
      .then((clips) => {
        if (cancelled) return;
        const usable = clips.filter((c) => (c.proxy_url ?? c.video_url) && c.dauer);
        if (usable.length < 2) {
          setError(`Need at least 2 processed clips with media, found ${usable.length}.`);
          return;
        }
        const [a, b] = usable;
        const tlClips: TLClip[] = [
          { tlId: `lab-${a.id}`, clipId: a.id, name: a.dateiname, start: 0, mediaStart: 0, duration: a.dauer!, proxyUrl: a.proxy_url, videoUrl: a.video_url },
          { tlId: `lab-${b.id}`, clipId: b.id, name: b.dateiname, start: a.dauer!, mediaStart: 0, duration: b.dauer!, proxyUrl: b.proxy_url, videoUrl: b.video_url },
        ];
        setTimeline({ fps: FPS, tracks: tlClipsToEngineTracks(tlClips, FPS) });
      })
      .catch((e) => setError(e instanceof Error ? e.message : String(e)));
    return () => {
      cancelled = true;
    };
  }, []);

  // Engine lifecycle.
  useEffect(() => {
    if (!timeline || !containerRef.current) return;
    const engine = new PlaybackEngine(timeline, containerRef.current);
    engine.onFrame = (f) => {
      setFrame(f);
      setDriftMs(engine.driftMs);
    };
    engine.onError = (err) => setMediaError(`Media error on ${err.src}`);
    engineRef.current = engine;
    engine.seek(0); // render the first frame immediately
    return () => {
      engine.destroy();
      engineRef.current = null;
    };
  }, [timeline]);

  const track = timeline?.tracks[0] ?? null;
  const totalFrames = track?.clips.length
    ? track.clips[track.clips.length - 1].timelineStart + track.clips[track.clips.length - 1].duration
    : 0;
  const active = track ? resolveClipAt(track, Math.floor(frame)) : null;

  return (
    <main className="min-h-screen bg-neutral-950 p-4 font-mono text-sm text-neutral-200">
      <h1 className="mb-2 text-base">Playback Engine Lab — Phase 1</h1>

      {error && <p className="mb-2 text-red-400">Error: {error}</p>}
      {mediaError && <p className="mb-2 text-amber-400">{mediaError}</p>}

      {/* Video container: the engine appends its 2 <video> elements here. */}
      <div ref={containerRef} className="relative h-[60vh] w-full bg-black" />

      {timeline && (
        <>
          <div className="mt-3 flex items-center gap-3">
            <button
              className="rounded bg-neutral-700 px-4 py-1 hover:bg-neutral-600"
              onClick={() => {
                engineRef.current?.play();
                setPlaying(true);
              }}
            >
              Play
            </button>
            <button
              className="rounded bg-neutral-700 px-4 py-1 hover:bg-neutral-600"
              onClick={() => {
                engineRef.current?.pause();
                setPlaying(false);
              }}
            >
              Pause
            </button>
            <input
              type="range"
              min={0}
              max={Math.max(0, totalFrames - 1)}
              value={Math.floor(frame)}
              onChange={(e) => engineRef.current?.seek(Number(e.target.value))}
              className="flex-1"
            />
            <span className="tabular-nums">{framesToTimecode(frame, FPS)}</span>
          </div>

          <div className="mt-3 rounded bg-neutral-900 p-3">
            <div>playing: {String(playing)}</div>
            <div>currentFrame: {Math.floor(frame)} / {totalFrames}</div>
            <div>activeClip: {active ? active.clip.id : "— (gap)"}</div>
            <div>sourceFrame: {active ? Math.floor(active.sourceFrame) : "—"}</div>
            <div>driftMs: {driftMs === null ? "—" : driftMs.toFixed(1)}</div>
          </div>
        </>
      )}

      {!timeline && !error && <p className="mt-3">Loading clips…</p>}
    </main>
  );
}
