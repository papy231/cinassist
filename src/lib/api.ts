/**
 * CinAssist — API Client (Frontend → FastAPI Backend)
 * Alle Aufrufe gehen an localhost:8001 (100% lokal).
 */

const API = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8001";
const WS_API = process.env.NEXT_PUBLIC_WS_URL || "ws://localhost:8001";

// ─── Typen ──────────────────────────────────────────────

export interface ClipDTO {
  id: string;
  dateiname: string;
  quelle: "A" | "B";
  dauer: number | null;
  aufloesung: string | null;
  bildrate: number | null;
  dateigroesse_mb: number | null;
  status: string;
  erstellt_am: string | null;
  video_url: string | null;
  proxy_url: string | null;     // low-res proxy for browser preview (960p H.264)
  waveform_url: string | null;  // pre-rendered PNG waveform (1920×80, transparent bg)
  strip_url: string | null;     // pre-rendered JPG thumbnail strip (24 tiles, 1920×45)
  dateipfad: string | null;     // absolute path on disk, used for NLE export
}

export interface SzeneDTO {
  szenen_nr: number;
  start_zeit: number;
  end_zeit: number;
  dauer: number;
  thumbnail_pfad: string | null;
  beschreibung: string | null;
  transkription: string | null;
  hat_embedding: boolean;
}

export interface AnalyseDTO {
  clip: {
    id: string;
    dateiname: string;
    quelle: string;
    dauer: number;
    aufloesung: string;
    status: string;
  };
  szenen_anzahl: number;
  szenen: SzeneDTO[];
}

export interface UploadResult {
  clip_id: string;
  job_id: string;
  dateiname: string;
  quelle: string;
  groesse_mb: number;
  nachricht: string;
}

export interface JobUpdate {
  status: string;
  progress: number;
  message: string;
  result?: Record<string, unknown> | null;
  // Pipeline-Schritt-Tracking (für PipelineSteps-Komponente)
  schritt?: string | null;
  schritt_daten?: Record<string, unknown> | null;
}

export interface WortZeitstempel {
  wort: string;
  start: number | null;
  end: number | null;
}

export interface SzeneDetail {
  id: string;
  szenen_nr: number;
  start_zeit: number;
  end_zeit: number;
  dauer: number;
  thumbnail_url: string | null;
  transkription: string | null;
  transkription_segmente: unknown[] | null;
  woerter_zeitstempel: WortZeitstempel[];
  beschreibung: string | null;
  analyse_visuelle: Record<string, unknown> | null;
  embedding_vorhanden: boolean;
  embedding_dimension: number | null;
  embedding_norm: number | null;
}

export interface PipelineBericht {
  clip_id: string;
  dateiname: string;
  schritt_history: Record<string, Record<string, unknown>>;
  szenen_detail: SzeneDetail[];
}

export interface TimelineDTO {
  id: string;
  name: string;
  stil: string | null;
  prompt: string | null;
  daten: TimelineDaten;
  gesamtdauer: number | null;
  erstellt_am: string | null;
}

export interface TimelineSegment {
  id: string;
  clip_id: string;
  szene_nr?: number;
  label: string;
  track: string;
  start: number;   // Sekunden in der Timeline
  dauer: number;    // Sekunden
  quelle: "A" | "B" | "audio" | "music";
  ai?: boolean;
}

export interface TimelineDaten {
  segmente: TimelineSegment[];
  gesamtdauer: number;
  stil?: string;
}

// ─── Clips ──────────────────────────────────────────────

export async function fetchClips(): Promise<ClipDTO[]> {
  const res = await fetch(`${API}/api/clips`);
  if (!res.ok) throw new Error(`Clips laden fehlgeschlagen: ${res.status}`);
  return res.json();
}

export async function fetchClipDetails(clipId: string): Promise<ClipDTO> {
  const res = await fetch(`${API}/api/clips/${encodeURIComponent(clipId)}`);
  if (!res.ok) throw new Error(`Clip-Details fehlgeschlagen: ${res.status}`);
  return res.json();
}

export async function fetchClipPipeline(clipId: string): Promise<PipelineBericht> {
  const res = await fetch(`${API}/api/clips/${encodeURIComponent(clipId)}/pipeline`);
  if (!res.ok) throw new Error(`Pipeline-Bericht fehlgeschlagen: ${res.status}`);
  return res.json();
}

export async function fetchAnalyse(clipId: string): Promise<AnalyseDTO> {
  const res = await fetch(`${API}/api/clips/${encodeURIComponent(clipId)}/analyse`);
  if (!res.ok) throw new Error(`Analyse laden fehlgeschlagen: ${res.status}`);
  return res.json();
}

export async function uploadClip(file: File, quelle: "A" | "B"): Promise<UploadResult> {
  const form = new FormData();
  form.append("datei", file);
  form.append("quelle", quelle);

  const res = await fetch(`${API}/api/clips/upload`, { method: "POST", body: form });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail || `Upload fehlgeschlagen: ${res.status}`);
  }
  return res.json();
}

export async function deleteClip(clipId: string): Promise<void> {
  const res = await fetch(`${API}/api/clips/${encodeURIComponent(clipId)}`, { method: "DELETE" });
  if (!res.ok) throw new Error(`Löschen fehlgeschlagen: ${res.status}`);
}

// ─── Timeline ───────────────────────────────────────────

export async function fetchTimelines(): Promise<TimelineDTO[]> {
  const res = await fetch(`${API}/api/timelines`);
  if (!res.ok) throw new Error(`Timelines laden fehlgeschlagen: ${res.status}`);
  return res.json();
}

export async function saveTimeline(data: {
  name?: string;
  stil?: string;
  prompt?: string;
  daten: TimelineDaten;
}): Promise<TimelineDTO> {
  const res = await fetch(`${API}/api/timelines`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
  if (!res.ok) throw new Error(`Timeline speichern fehlgeschlagen: ${res.status}`);
  return res.json();
}

export async function updateTimeline(id: string, data: {
  name?: string;
  daten?: TimelineDaten;
}): Promise<TimelineDTO> {
  const res = await fetch(`${API}/api/timelines/${encodeURIComponent(id)}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
  if (!res.ok) throw new Error(`Timeline aktualisieren fehlgeschlagen: ${res.status}`);
  return res.json();
}

// ─── KI-Schnitt ─────────────────────────────────────────

export type LLMProvider = "auto" | "ollama" | "claude" | "openai" | "gemini";

export interface ProvidersResult {
  verfuegbar: Record<LLMProvider, boolean>;
  standard: LLMProvider;
  modelle: Record<LLMProvider, string>;
}

export interface CutMetrics {
  diversitaet: number;     // mean CLIP cosine distance between adjacent scenes (0..1)
  wechselrate: number;     // share of cuts that switch source clip (0..1)
  dialog_treue: number;    // share of cuts that do NOT land mid-word (0..1)
  szenen_anzahl: number;
  uebergaenge: number;
  prompt_relevance?: number;  // mean CLIP similarity to user prompt (0..1) — only if prompt given
}

export interface AiCutResult {
  timeline_id: string;
  segmente_anzahl: number;
  gesamtdauer: number;
  llm_provider: LLMProvider | null;
  metriken?: CutMetrics;
  scoring_methode?: string;
  daten: TimelineDaten;
}

export async function fetchProviders(): Promise<ProvidersResult> {
  const res = await fetch(`${API}/api/ai/providers`);
  if (!res.ok) throw new Error(`Providers laden fehlgeschlagen: ${res.status}`);
  return res.json();
}

export interface ReorganizeResult {
  segmente: Array<{
    id: string;
    clip_id: string | null;
    szene_nr: number | null;
    track: string;
    start: number;
    dauer: number;
    mediaStart: number;
    groupId: string | null;
    label: string | null;
    rolle?: string | null;
  }>;
  anzahl: number;
  gesamtdauer: number;
  arc_rollen: Record<string, number>;
  methodik: string;
}

export async function reorganizeTimeline(segmente: Array<{
  id: string;
  clip_id: string | null;
  szene_nr?: number | null;
  dauer: number;
  mediaStart: number;
  track: string;
  groupId?: string | null;
  label?: string | null;
}>): Promise<ReorganizeResult> {
  const res = await fetch(`${API}/api/ai/reorganize`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ segmente }),
  });
  if (!res.ok) throw new Error(`Reorganize fehlgeschlagen: ${res.status}`);
  return res.json();
}

export async function requestAiCut(params: {
  stil: string;
  prompt?: string;
  clip_ids: string[];
  provider?: LLMProvider;
  llm_modell?: string;
  llm_aktiviert?: boolean;
  max_szenen?: number;
  qualitaet_schwelle?: number;
  beat_sync?: boolean;
  beat_pro_segment?: number;
}): Promise<AiCutResult> {
  const res = await fetch(`${API}/api/ai/cut`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(params),
  });
  if (!res.ok) throw new Error(`KI-Schnitt fehlgeschlagen: ${res.status}`);
  return res.json();
}

// ─── Export ────────────────────────────────────────────

export interface TransitionDef {
  type: string;   // "dissolve" | "fade" | "wipeleft" | ...
  dauer: number;  // Sekunden
}

export interface ExportSegment {
  id: string;
  clip_id: string;
  track: string;
  start: number;
  dauer: number;
  mediaStart: number;
  transition?: TransitionDef;
}

export interface ExportResult {
  job_id: string;
  nachricht: string;
}

export async function exportTimeline(params: {
  segments: ExportSegment[];
  resolution?: string;
  name?: string;
}): Promise<ExportResult> {
  const res = await fetch(`${API}/api/export`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      segments: params.segments,
      resolution: params.resolution ?? "1920x1080",
      name: params.name ?? "Export",
    }),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail || `Export fehlgeschlagen: ${res.status}`);
  }
  return res.json();
}

// ─── WebSocket (Job-Status) ─────────────────────────────

export function connectJobWs(
  jobId: string,
  onMessage: (data: JobUpdate) => void,
  onClose?: () => void,
): WebSocket {
  const ws = new WebSocket(`${WS_API}/ws/jobs/${encodeURIComponent(jobId)}`);
  ws.onmessage = (e) => {
    try {
      onMessage(JSON.parse(e.data));
    } catch { /* ignore bad json */ }
  };
  ws.onclose = () => onClose?.();
  ws.onerror = () => onClose?.();
  return ws;
}

// ─── Health ─────────────────────────────────────────────

export async function checkHealth(): Promise<boolean> {
  try {
    const res = await fetch(`${API}/health`, { signal: AbortSignal.timeout(3000) });
    return res.ok;
  } catch {
    return false;
  }
}
