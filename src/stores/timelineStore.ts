"use client";

/**
 * CinAssist — Timeline Store (Zustand)
 *
 * Zentraler State für Timeline-Clips, Playhead, Zoom, Spuren.
 */

import { create } from "zustand";

// ─── Typen ──────────────────────────────────────────────

export interface TimelineClip {
  id: string;
  clipId: string;
  track: "V1" | "V2" | "A1" | "Musik";
  startPx: number;
  widthPx: number;
  startZeit: number;  // Sekunden
  endZeit: number;    // Sekunden
  source: "A" | "B";
  label: string;
  farbe: string;
  aiGenerated: boolean;
  transition?: "cut" | "dissolve" | "fade" | "wipe";
  colorGrade?: string;
}

export interface TimelineState {
  // Clips auf der Timeline
  clips: TimelineClip[];
  
  // Ausgewählter Clip
  selectedClipId: string | null;
  
  // Playhead (Pixel-Position)
  playheadPx: number;
  
  // Wiedergabe
  isPlaying: boolean;
  currentTime: number; // Sekunden
  
  // Zoom: px pro Sekunde (Standard: 20)
  pxPerSecond: number;
  
  // Gesamtdauer
  totalDuration: number;
  
  // Scroll-Offset
  scrollLeft: number;

  // KI-Banner
  aiBanner: {
    visible: boolean;
    stil: string;
    segmente: number;
    message: string;
  } | null;

  // Aktionen
  setClips: (clips: TimelineClip[]) => void;
  addClip: (clip: TimelineClip) => void;
  removeClip: (id: string) => void;
  updateClip: (id: string, updates: Partial<TimelineClip>) => void;
  moveClip: (id: string, newStartPx: number) => void;
  resizeClip: (id: string, newWidthPx: number, fromLeft?: boolean) => void;
  selectClip: (id: string | null) => void;
  setPlayheadPx: (px: number) => void;
  setIsPlaying: (playing: boolean) => void;
  setCurrentTime: (time: number) => void;
  setPxPerSecond: (pps: number) => void;
  setScrollLeft: (scroll: number) => void;
  setAiBanner: (banner: TimelineState["aiBanner"]) => void;
  splitClipAt: (id: string, splitPx: number) => void;
}

// ─── Farben pro Spur ────────────────────────────────────

const SPUR_FARBEN: Record<string, { a: string; b: string }> = {
  V1:    { a: "rgba(37,99,235,0.5)",  b: "rgba(59,130,246,0.5)"  },
  V2:    { a: "rgba(37,99,235,0.35)", b: "rgba(59,130,246,0.35)" },
  A1:    { a: "rgba(34,197,94,0.45)",  b: "rgba(34,197,94,0.35)"  },
  Musik: { a: "rgba(168,85,247,0.45)", b: "rgba(168,85,247,0.35)" },
};

export function getClipFarbe(track: string, source: "A" | "B"): string {
  const farben = SPUR_FARBEN[track] || SPUR_FARBEN.V1;
  return source === "A" ? farben.a : farben.b;
}

// ─── Store ──────────────────────────────────────────────

export const useTimelineStore = create<TimelineState>((set, get) => ({
  clips: [],
  selectedClipId: null,
  playheadPx: 0,
  isPlaying: false,
  currentTime: 0,
  pxPerSecond: 20,
  totalDuration: 60,
  scrollLeft: 0,
  aiBanner: null,

  setClips: (clips) => set({ clips }),

  addClip: (clip) =>
    set((s) => ({ clips: [...s.clips, clip] })),

  removeClip: (id) =>
    set((s) => ({
      clips: s.clips.filter((c) => c.id !== id),
      selectedClipId: s.selectedClipId === id ? null : s.selectedClipId,
    })),

  updateClip: (id, updates) =>
    set((s) => ({
      clips: s.clips.map((c) => (c.id === id ? { ...c, ...updates } : c)),
    })),

  moveClip: (id, newStartPx) =>
    set((s) => ({
      clips: s.clips.map((c) =>
        c.id === id ? { ...c, startPx: Math.max(0, newStartPx) } : c
      ),
    })),

  resizeClip: (id, newWidthPx, fromLeft = false) =>
    set((s) => ({
      clips: s.clips.map((c) => {
        if (c.id !== id) return c;
        const minWidth = 10;
        const width = Math.max(minWidth, newWidthPx);
        if (fromLeft) {
          const diff = c.widthPx - width;
          return { ...c, startPx: c.startPx + diff, widthPx: width };
        }
        return { ...c, widthPx: width };
      }),
    })),

  selectClip: (id) => set({ selectedClipId: id }),

  setPlayheadPx: (px) => {
    const { pxPerSecond } = get();
    set({ playheadPx: Math.max(0, px), currentTime: px / pxPerSecond });
  },

  setIsPlaying: (playing) => set({ isPlaying: playing }),

  setCurrentTime: (time) => {
    const { pxPerSecond } = get();
    set({ currentTime: time, playheadPx: time * pxPerSecond });
  },

  setPxPerSecond: (pps) => {
    const { currentTime } = get();
    set({ pxPerSecond: pps, playheadPx: currentTime * pps });
  },

  setScrollLeft: (scroll) => set({ scrollLeft: scroll }),

  setAiBanner: (banner) => set({ aiBanner: banner }),

  splitClipAt: (id, splitPx) =>
    set((s) => {
      const clip = s.clips.find((c) => c.id === id);
      if (!clip) return s;
      const relPx = splitPx - clip.startPx;
      if (relPx <= 5 || relPx >= clip.widthPx - 5) return s;

      const linksTeil: TimelineClip = {
        ...clip,
        widthPx: relPx,
        endZeit: clip.startZeit + (relPx / s.pxPerSecond),
      };
      const rechtsTeil: TimelineClip = {
        ...clip,
        id: `${clip.id}_split`,
        startPx: clip.startPx + relPx,
        widthPx: clip.widthPx - relPx,
        startZeit: clip.startZeit + (relPx / s.pxPerSecond),
        label: `${clip.label} (2)`,
      };

      return {
        clips: s.clips
          .filter((c) => c.id !== id)
          .concat([linksTeil, rechtsTeil]),
      };
    }),
}));
