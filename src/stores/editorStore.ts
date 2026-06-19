/**
 * CinAssist — Editor Store (Zustand)
 * Zentraler State für den Editor — Clips, Timeline, Jobs.
 */

import { create } from "zustand";
import {
  fetchClips,
  uploadClip,
  deleteClip as apiDeleteClip,
  connectJobWs,
  checkHealth,
  type ClipDTO,
  type JobUpdate,
  type TimelineSegment,
} from "@/lib/api";

// ─── Timeline-Clip (UI-Darstellung) ────────────────────

export interface TLClip {
  id: string;
  clipId?: string;        // Referenz auf ClipDTO.id
  szeneNr?: number;
  label: string;
  track: string;          // Dynamische Track-ID (z.B. "v1", "v2", "a1", "a2", "music")
  start: number;          // Sekunden (Timeline-Position)
  dauer: number;          // Sekunden
  mediaStart: number;     // Sekunden (Offset in Quelldatei, default 0)
  color: "orange" | "blue" | "green" | "purple";
  ai?: boolean;
  groupId?: string;       // AV-Gruppierung: gleiche groupId = verknüpft
  transition?: { type: string; dauer: number }; // Überblende am Anfang dieses Clips
  // ─── Selektions-Provenienz (für "Pourquoi ce cut?" Tooltip) ───
  beschreibung?: string;        // LLaVA-Beschreibung der Szene
  transkription?: string;       // Whisper-Transkript der Szene
  rolle?: string | null;        // Erzählerische Rolle (z.B. ouverture/climax)
  promptRelevance?: number | null;  // cosine sim zum Prompt (0..1)
  energie?: number | null;
  interessantheit?: number | null;
}

// ─── Track-Definition ──────────────────────────────────

export interface Track {
  id: string;
  name: string;
  type: "video" | "audio";
  muted: boolean;
  locked: boolean;
  visible: boolean;
  // Höhe der Spur in Pixel (per-Track-Resize wie in DaVinci/Premiere).
  // Optional — Fallback auf 52 wenn nicht gesetzt, für Abwärtskompatibilität.
  height?: number;
}

const DEFAULT_TRACK_HEIGHT = 52;

const DEFAULT_TRACKS: Track[] = [
  { id: "v1", name: "V1", type: "video", muted: false, locked: false, visible: true, height: DEFAULT_TRACK_HEIGHT },
  { id: "a1", name: "A1", type: "audio", muted: false, locked: false, visible: true, height: DEFAULT_TRACK_HEIGHT },
];

// ─── Job-Tracking ──────────────────────────────────────

export interface ActiveJob {
  jobId: string;
  clipId: string;
  status: string;
  fortschritt: number;
  nachricht: string;
  // Pipeline-Schritt-Tracking
  aktuellerSchritt?: string;                              // gerade laufender Schritt
  schrittHistory: Record<string, Record<string, unknown>>; // abgeschlossene Schritte + ihre Belege
}

// ─── Store ─────────────────────────────────────────────

interface EditorState {
  // Clips aus der DB
  clips: ClipDTO[];
  clipsLoading: boolean;

  // Timeline
  tlClips: TLClip[];
  tracks: Track[];
  gesamtdauer: number;

  // Undo/Redo
  undoStack: TLClip[][];
  redoStack: TLClip[][];

  // Jobs
  activeJobs: ActiveJob[];

  // Backend-Status
  backendOnline: boolean;

  // Actions
  loadClips: () => Promise<void>;
  doUpload: (file: File, quelle: "A" | "B") => Promise<void>;
  removeClip: (clipId: string) => Promise<void>;
  checkBackend: () => Promise<void>;

  // Timeline mutations
  addTLClip: (clip: TLClip) => void;
  updateTLClip: (id: string, patch: Partial<TLClip>) => void;
  removeTLClip: (id: string) => void;
  setTLClips: (clips: TLClip[]) => void;
  recalcDauer: () => void;

  // Track mutations
  addTrack: (type: "video" | "audio") => void;
  removeTrack: (trackId: string) => void;
  updateTrack: (trackId: string, patch: Partial<Track>) => void;
  moveTrack: (trackId: string, direction: "up" | "down") => void;

  // Undo/Redo
  pushUndo: () => void;
  undo: () => void;
  redo: () => void;

  // Job helpers
  updateJob: (jobId: string, patch: Partial<ActiveJob>) => void;
  removeJob: (jobId: string) => void;
}

function pxToSec(px: number, pxPerSec: number) {
  return px / pxPerSec;
}

export const useEditorStore = create<EditorState>((set, get) => ({
  clips: [],
  clipsLoading: false,
  tlClips: [],
  tracks: [...DEFAULT_TRACKS],
  gesamtdauer: 0,
  undoStack: [],
  redoStack: [],
  activeJobs: [],
  backendOnline: false,

  // ─── Clips von der API laden ─────────────────────────
  loadClips: async () => {
    set({ clipsLoading: true });
    try {
      const clips = await fetchClips();
      set({ clips, clipsLoading: false });
    } catch {
      set({ clipsLoading: false });
    }
  },

  // ─── Video hochladen ─────────────────────────────────
  doUpload: async (file, quelle) => {
    try {
      const result = await uploadClip(file, quelle);

      // Job tracken
      const job: ActiveJob = {
        jobId: result.job_id,
        clipId: result.clip_id,
        status: "wartend",
        fortschritt: 0,
        nachricht: result.nachricht,
        schrittHistory: {},
      };
      set(s => ({ activeJobs: [...s.activeJobs, job] }));

      // WebSocket für Echtzeit-Updates
      connectJobWs(
        result.job_id,
        (data: JobUpdate) => {
          const patch: Partial<ActiveJob> = {
            status: data.status,
            fortschritt: data.progress,
            nachricht: data.message,
          };
          if (data.schritt) {
            patch.aktuellerSchritt = data.schritt;
            // Wenn schritt_daten gesetzt: Schritt ist abgeschlossen → in History speichern
            if (data.schritt_daten) {
              const currentJob = get().activeJobs.find(j => j.jobId === result.job_id);
              patch.schrittHistory = {
                ...(currentJob?.schrittHistory || {}),
                [data.schritt]: data.schritt_daten,
              };
            }
          }
          get().updateJob(result.job_id, patch);

          // Wenn fertig → Clips neu laden
          if (data.status === "fertig") {
            setTimeout(() => {
              get().loadClips();
              get().removeJob(result.job_id);
            }, 1500);
          }
          if (data.status === "fehler") {
            setTimeout(() => get().removeJob(result.job_id), 5000);
          }
        },
        () => {
          // Bei WebSocket-Fehler trotzdem Clips nachladen
          setTimeout(() => get().loadClips(), 2000);
        },
      );

      // Clips direkt neu laden (zeigt sofort den "hochgeladen" Status)
      await get().loadClips();
    } catch (err) {
      console.error("Upload fehlgeschlagen:", err);
      throw err;
    }
  },

  // ─── Clip löschen ────────────────────────────────────
  removeClip: async (clipId) => {
    // Sofort aus der UI entfernen
    set(s => ({
      clips: s.clips.filter(c => c.id !== clipId),
      tlClips: s.tlClips.filter(c => c.clipId !== clipId),
    }));
    get().recalcDauer();
    // Dann vom Backend löschen
    try {
      await apiDeleteClip(clipId);
    } catch (err) {
      console.error("Backend-Löschung fehlgeschlagen:", err);
    }
  },

  // ─── Backend Health Check ────────────────────────────
  checkBackend: async () => {
    const ok = await checkHealth();
    set({ backendOnline: ok });
  },

  // ─── Timeline-Clip hinzufügen ────────────────────────
  addTLClip: (clip) => {
    get().pushUndo();
    set(s => ({ tlClips: [...s.tlClips, clip], redoStack: [] }));
    get().recalcDauer();
  },

  // ─── Timeline-Clip aktualisieren ─────────────────────
  updateTLClip: (id, patch) => {
    set(s => ({
      tlClips: s.tlClips.map(c => c.id === id ? { ...c, ...patch } : c),
    }));
    get().recalcDauer();
  },

  // ─── Timeline-Clip entfernen ─────────────────────────
  removeTLClip: (id) => {
    get().pushUndo();
    set(s => ({ tlClips: s.tlClips.filter(c => c.id !== id), redoStack: [] }));
    get().recalcDauer();
  },

  // ─── Alle Timeline-Clips setzen ──────────────────────
  setTLClips: (clips) => {
    get().pushUndo();
    set({ tlClips: clips, redoStack: [] });
    get().recalcDauer();
  },

  // ─── Undo / Redo ────────────────────────────────────
  pushUndo: () => {
    const snap = get().tlClips.map(c => ({ ...c }));
    set(s => ({ undoStack: [...s.undoStack.slice(-49), snap] }));
  },

  undo: () => {
    const { undoStack, tlClips } = get();
    if (undoStack.length === 0) return;
    const prev = undoStack[undoStack.length - 1];
    set(s => ({
      tlClips: prev,
      undoStack: s.undoStack.slice(0, -1),
      redoStack: [...s.redoStack, tlClips.map(c => ({ ...c }))],
    }));
    get().recalcDauer();
  },

  redo: () => {
    const { redoStack, tlClips } = get();
    if (redoStack.length === 0) return;
    const next = redoStack[redoStack.length - 1];
    set(s => ({
      tlClips: next,
      redoStack: s.redoStack.slice(0, -1),
      undoStack: [...s.undoStack, tlClips.map(c => ({ ...c }))],
    }));
    get().recalcDauer();
  },

  // ─── Gesamtdauer berechnen ───────────────────────────
  recalcDauer: () => {
    const clips = get().tlClips;
    if (clips.length === 0) {
      set({ gesamtdauer: 0 });
      return;
    }
    const maxEnd = Math.max(...clips.map(c => c.start + c.dauer));
    set({ gesamtdauer: maxEnd });
  },

  // ─── Track-Verwaltung ────────────────────────────────
  addTrack: (type) => {
    const tracks = get().tracks;
    const existing = tracks.filter(t => t.type === type);
    const prefix = type === "video" ? "V" : "A";
    const num = existing.length + 1;
    const id = `${prefix.toLowerCase()}${num}`;
    // Avoid duplicate IDs
    const finalId = tracks.find(t => t.id === id) ? `${id}-${Date.now()}` : id;
    const newTrack: Track = {
      id: finalId,
      name: `${prefix}${num}`,
      type,
      muted: false,
      locked: false,
      visible: true,
      height: DEFAULT_TRACK_HEIGHT,
    };
    // Video tracks go at the top, audio at the bottom
    if (type === "video") {
      const lastVideoIdx = tracks.reduce((acc, t, i) => t.type === "video" ? i : acc, -1);
      const newTracks = [...tracks];
      newTracks.splice(lastVideoIdx + 1, 0, newTrack);
      set({ tracks: newTracks });
    } else {
      set(s => ({ tracks: [...s.tracks, newTrack] }));
    }
  },

  removeTrack: (trackId) => {
    set(s => ({
      tracks: s.tracks.filter(t => t.id !== trackId),
      tlClips: s.tlClips.filter(c => c.track !== trackId),
    }));
    get().recalcDauer();
  },

  updateTrack: (trackId, patch) => {
    set(s => ({
      tracks: s.tracks.map(t => t.id === trackId ? { ...t, ...patch } : t),
    }));
  },

  moveTrack: (trackId, direction) => {
    const tracks = [...get().tracks];
    const idx = tracks.findIndex(t => t.id === trackId);
    if (idx < 0) return;
    const swapIdx = direction === "up" ? idx - 1 : idx + 1;
    if (swapIdx < 0 || swapIdx >= tracks.length) return;
    [tracks[idx], tracks[swapIdx]] = [tracks[swapIdx], tracks[idx]];
    set({ tracks });
  },

  // ─── Job-Helfer ──────────────────────────────────────
  updateJob: (jobId, patch) => {
    set(s => ({
      activeJobs: s.activeJobs.map(j => j.jobId === jobId ? { ...j, ...patch } : j),
    }));
  },

  removeJob: (jobId) => {
    set(s => ({ activeJobs: s.activeJobs.filter(j => j.jobId !== jobId) }));
  },
}));
