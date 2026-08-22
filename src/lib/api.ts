/**
 * CinAssist — API Client (Frontend → FastAPI Backend)
 *
 * Das Ziel steht in NEXT_PUBLIC_API_URL. Die Start-Skripte setzen es je Projekt,
 * damit die Oberfläche mit dem Backend spricht, das zu diesem Projekt gehört.
 * Ohne die Angabe bleibt es bei der Voreinstellung, dem ersten Projekt auf 8001.
 */

const API = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8001";
function resolveWsBase(): string {
  if (process.env.NEXT_PUBLIC_WS_URL) return process.env.NEXT_PUBLIC_WS_URL;
  if (typeof window !== "undefined" && location.hostname !== "localhost") {
    return `${location.protocol === "https:" ? "wss:" : "ws:"}//${location.host}`;
  }
  return "ws://localhost:8001";
}

// ─── Typen ──────────────────────────────────────────────

export interface ClipDTO {
  id: string;
  dateiname: string;
  quelle: "A" | "B";
  dauer: number | null;
  aufloesung: string | null;
  bildrate: number | null;
  codec: string | null;
  dateigroesse_mb: number | null;
  status: string;
  erstellt_am: string | null;
  video_url: string | null;
  proxy_url: string | null;     // low-res proxy for browser preview (960p H.264)
  waveform_url: string | null;  // pre-rendered PNG waveform (1920×80, transparent bg)
  strip_url: string | null;     // pre-rendered JPG thumbnail strip (24 tiles, 1920×45)
  dateipfad: string | null;     // absolute path on disk, used for NLE export
  ordner_id?: string | null;    // Medien-Ordner (Bin), null = Wurzel
  take_id?: string | null;      // Sync-Take (Clip per Referenz), null = klassischer Upload
  medientyp?: "video" | "audio"; // reine Audiodatei (mp3/wav …) → Audio-only-Clip auf A-Spur
  medienart?: "video" | "audio" | "av"; // Etikett aus der Ingestion: nur Bild | nur Ton | Bild+Ton
  hat_bild?: boolean | null;
  hat_ton?: boolean | null;
  sync?: { take_id: string; status: string; multicam_gruppe: string | null; szene: number | null; plan: number | null; prise: number | null;
           tc_start: string | null; tc_start_s: number | null;
           ton: { dateiname: string; offset_s: number; methode: string; konfidenz: number } | null } | null;
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

export type NleApp = "davinci" | "premiere" | "fcp" | "avid";

export interface SendToAppResult {
  status: "importiert" | "geöffnet" | "download";
  app: string;
  datei: string;
  groesse_bytes: number;
  nachricht: string;
  download_url?: string;
}

export async function sendToApp(params: {
  app: NleApp;
  segments: ExportSegment[];
  name?: string;
  fps?: number;
  mode?: "timeline" | "projekt";
}): Promise<SendToAppResult> {
  const res = await fetch(`/api/export/open-in`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      app: params.app,
      segments: params.segments,
      name: params.name ?? "CinAssist_Timeline",
      fps: params.fps ?? 30.0,
      mode: params.mode ?? "timeline",
    }),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail || `Senden fehlgeschlagen: ${res.status}`);
  }
  return res.json();
}

// ─── WebSocket (Job-Status) ─────────────────────────────

export function connectJobWs(
  jobId: string,
  onMessage: (data: JobUpdate) => void,
  onClose?: () => void,
): WebSocket {
  const ws = new WebSocket(`${resolveWsBase()}/ws/jobs/${encodeURIComponent(jobId)}`);
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

// ─── Synchronisation (Take-Modell: Ordner-Import per Referenz, Audio↔Video-Zuordnung) ───

export interface MediaAssetDTO {
  id: string;
  typ: "video" | "audio";
  pfad: string;
  dateiname: string;
  dauer_s: number | null;
  sample_rate: number | null;
  kanaele: number | null;
  fps: number | null;
  codec: string | null;
  dateigroesse: number | null;
  tc_start: string | null;
  tc_start_s: number | null;
  tc_quelle: "bwf" | "ixml" | "ltc" | "container" | "keine";
  tc_rate: string | null;
  tc_flag: string | null;
  container_tc: string | null;
  ltc_kanal: number | null;
  scratch_kanal: number | null;
  record_kanal: number;
  szene: number | null;
  plan: number | null;
  prise: number | null;
  unbekannte_markierung: string | null;
  datum: string | null;
  warnungen: string[];
  ixml: Record<string, unknown> | null;
  ordner_import_id: string | null;
  vorhanden: boolean;
  volume_gemountet: boolean;
}

export type TakeStatus = "sicher" | "plausibel" | "unklar" | "verwaist" | "manuell_bestaetigt" | "manuell_abgelehnt";
export type LinkMethode = "timecode" | "waveform" | "klappe" | "dateiname" | "manuell" | "verwaist";

export interface TakeAudioLinkDTO {
  id: string;
  take_id: string;
  audio_asset_id: string;
  audio: MediaAssetDTO | null;
  offset_s: number;
  methode: LinkMethode;
  konfidenz: number;
  begruendung: string;
  kanal_fuer_transkription: number;
  warnungen: string[];
  bestaetigt: boolean;
  vorschau_audio_url: string | null;
}

export interface TakeKandidatDTO {
  audio_asset_id: string;
  video_asset_id: string;
  audio_dateiname: string | null;
  video_dateiname: string | null;
  offset_s: number | null;
  ueberlappung_s: number;
  ueberlappung_ratio: number;
  begruendung: string;
}

export interface TakeDTO {
  id: string;
  video_asset_id: string | null;
  video: MediaAssetDTO | null;
  szene: number | null;
  plan: number | null;
  prise: number | null;
  status: TakeStatus;
  automatisch: boolean;
  multicam_gruppe?: string | null;   // gleicher Ton auf parallel laufenden Kameras
  warnungen: string[];
  kandidaten: TakeKandidatDTO[];
  links: TakeAudioLinkDTO[];
  clip_id: string | null;
  clip_status: string | null;
  vorschau_video_url: string | null;
  erstellt_am: string | null;
}

export interface TakesAntwort {
  takes: TakeDTO[];
  anzahl: number;
  unklar: number;
  analyse_blockiert: boolean;
  status_zaehler: Record<TakeStatus, number>;
}

export interface OrdnerImportDTO {
  id: string;
  pfad: string;
  typ: "video" | "audio";
  status: string;
  gescannt_am: string | null;
  anzahl_dateien: number;
  anzahl_ignoriert: number;
  volume_uuid: string | null;
  volume_root: string | null;
  volume_gemountet: boolean;
  fehler: string | null;
  job_id: string | null;
}

async function jsonOrThrow<T>(res: Response, was: string): Promise<T> {
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    const d = err?.detail;
    const msg = typeof d === "string" ? d : d?.nachricht || JSON.stringify(d ?? err);
    const e = new Error(`${was}: ${msg}`) as Error & { detail?: unknown; status?: number };
    e.detail = d;
    e.status = res.status;
    throw e;
  }
  return res.json();
}

export async function importOrdner(pfad: string, typ: "video" | "audio"): Promise<{ import_id: string; job_id: string }> {
  const res = await fetch(`${API}/api/import/ordner`, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ pfad, typ }),
  });
  return jsonOrThrow(res, "Import fehlgeschlagen");
}

export async function fetchImporte(): Promise<OrdnerImportDTO[]> {
  return jsonOrThrow(await fetch(`${API}/api/import/ordner`), "Importe laden fehlgeschlagen");
}

export async function deleteImport(importId: string): Promise<void> {
  await jsonOrThrow(await fetch(`${API}/api/import/ordner/${encodeURIComponent(importId)}`, { method: "DELETE" }), "Import löschen fehlgeschlagen");
}

export async function runSync(importIds?: string[]): Promise<{ job_id: string }> {
  const res = await fetch(`${API}/api/sync/run`, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ import_ids: importIds ?? null }),
  });
  return jsonOrThrow(res, "Matching starten fehlgeschlagen");
}

export async function fetchTakes(): Promise<TakesAntwort> {
  return jsonOrThrow(await fetch(`${API}/api/sync/takes`), "Takes laden fehlgeschlagen");
}

export async function fetchAssets(): Promise<MediaAssetDTO[]> {
  return jsonOrThrow(await fetch(`${API}/api/sync/assets`), "Assets laden fehlgeschlagen");
}

export async function takeBestaetigen(takeId: string): Promise<TakeDTO> {
  return jsonOrThrow(await fetch(`${API}/api/sync/takes/${encodeURIComponent(takeId)}/bestaetigen`, { method: "POST" }), "Bestätigen fehlgeschlagen");
}

export async function takeOhneAudioBestaetigen(takeId: string): Promise<TakeDTO> {
  return jsonOrThrow(await fetch(`${API}/api/sync/takes/${encodeURIComponent(takeId)}/verwaist-bestaetigen`, { method: "POST" }), "Freigabe ohne Ton fehlgeschlagen");
}

export async function takeAblehnen(takeId: string): Promise<TakeDTO> {
  return jsonOrThrow(await fetch(`${API}/api/sync/takes/${encodeURIComponent(takeId)}/ablehnen`, { method: "POST" }), "Ablehnen fehlgeschlagen");
}

export async function linkAnlegen(takeId: string, audioAssetId: string, offsetS?: number | null): Promise<TakeDTO> {
  const res = await fetch(`${API}/api/sync/takes/${encodeURIComponent(takeId)}/links`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ audio_asset_id: audioAssetId, offset_s: offsetS ?? null }),
  });
  return jsonOrThrow(res, "Anhängen fehlgeschlagen");
}

export async function linkLoeschen(linkId: string): Promise<TakeDTO | { geloescht: boolean }> {
  return jsonOrThrow(await fetch(`${API}/api/sync/links/${encodeURIComponent(linkId)}`, { method: "DELETE" }), "Abhängen fehlgeschlagen");
}

export async function linkOffsetSetzen(linkId: string, offsetS: number): Promise<TakeDTO> {
  const res = await fetch(`${API}/api/sync/links/${encodeURIComponent(linkId)}`, {
    method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ offset_s: offsetS }),
  });
  return jsonOrThrow(res, "Offset speichern fehlgeschlagen");
}

export async function vorschauAnfordern(takeId: string): Promise<{ fertig: boolean; job_id: string | null; take: TakeDTO }> {
  return jsonOrThrow(await fetch(`${API}/api/sync/takes/${encodeURIComponent(takeId)}/vorschau`, { method: "POST" }), "Vorschau fehlgeschlagen");
}

export interface InMedienOptionen {
  ordnung: "szene" | "chronologisch" | "flach";
  ton_separat: boolean;
  waisen_video: boolean;
  waisen_audio: boolean;
  analyse: boolean;
}
export interface AnalyseStartAntwort {
  medien: { take_id: string; clip_id: string; dateiname: string; mit_ton: boolean; nur_ton?: boolean }[];
  gestartet: { take_id?: string; clip_id: string; job_id: string; dateiname: string; mit_ton?: boolean }[];
  uebersprungen: { take_id: string; clip_id?: string; grund: string }[];
  ordnung: string;
}

/** Synchronisierte Takes ins Medien-Panel übernehmen (ein Medium = Video + zugeordneter, synchroner Ton). */
export async function inMedienUebernehmen(opts: Partial<InMedienOptionen> = {}, takeIds?: string[], unklarBestaetigen = false): Promise<AnalyseStartAntwort> {
  const res = await fetch(`${API}/api/sync/in-medien`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ take_ids: takeIds ?? null, unklar_bestaetigen: unklarBestaetigen,
      ordnung: "szene", ton_separat: false, waisen_video: true, waisen_audio: false, analyse: true, ...opts }),
  });
  return jsonOrThrow(res, "In Medien übernehmen fehlgeschlagen");
}
/** @deprecated → inMedienUebernehmen */
export const analyseStarten = (takeIds?: string[], unklarBestaetigen = false) => inMedienUebernehmen({}, takeIds, unklarBestaetigen);

/** Absolute URL für Medien-Derivate (/proxies/…) bzw. Referenz-Medien (/api/sync/media/…). */
export function mediaUrl(pfad: string | null | undefined): string | null {
  if (!pfad) return null;
  return pfad.startsWith("http") ? pfad : `${API}${pfad}`;
}

export interface OrdnerEintrag { name: string; pfad: string; videos: number; audios: number; unterordner: boolean }
export interface OrdnerListe { pfad: string | null; eltern: string | null; videos?: number; audios?: number; eintraege: OrdnerEintrag[] }

/** Ordner-Browser (lokale App): ohne pfad = Einstiegspunkte (/Volumes, Home …). */
export async function durchsucheOrdner(pfad?: string | null): Promise<OrdnerListe> {
  const q = pfad ? `?pfad=${encodeURIComponent(pfad)}` : "";
  return jsonOrThrow(await fetch(`${API}/api/import/durchsuchen${q}`), "Ordner lesen fehlgeschlagen");
}

export async function syncZuruecksetzen(): Promise<{ geloescht: Record<string, number> }> {
  const res = await fetch(`${API}/api/sync/zuruecksetzen`, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ clips_loeschen: true, vorschau_loeschen: true }),
  });
  return jsonOrThrow(res, "Zurücksetzen fehlgeschlagen");
}

// ─── Medien-Ordner (Bins) ───────────────────────────────

export interface OrdnerDTO { id: string | null; name: string; eltern_id: string | null; quelle_pfad: string | null; anzahl_clips: number; erstellt_am: string | null }

export async function fetchOrdner(): Promise<OrdnerDTO[]> {
  return jsonOrThrow(await fetch(`${API}/api/ordner`), "Ordner laden fehlgeschlagen");
}
export async function createOrdner(name: string, elternId: string | null): Promise<OrdnerDTO> {
  const res = await fetch(`${API}/api/ordner`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name, eltern_id: elternId }) });
  return jsonOrThrow(res, "Ordner anlegen fehlgeschlagen");
}
export async function renameOrdner(id: string, name: string): Promise<OrdnerDTO> {
  const res = await fetch(`${API}/api/ordner/${encodeURIComponent(id)}`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name }) });
  return jsonOrThrow(res, "Umbenennen fehlgeschlagen");
}
export async function moveOrdner(id: string, elternId: string | null): Promise<OrdnerDTO> {
  const res = await fetch(`${API}/api/ordner/${encodeURIComponent(id)}`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify(elternId ? { eltern_id: elternId } : { eltern_loesen: true }) });
  return jsonOrThrow(res, "Verschieben fehlgeschlagen");
}
export async function deleteOrdner(id: string): Promise<void> {
  await jsonOrThrow(await fetch(`${API}/api/ordner/${encodeURIComponent(id)}`, { method: "DELETE" }), "Ordner löschen fehlgeschlagen");
}
export async function moveClipsToOrdner(clipIds: string[], ordnerId: string | null): Promise<{ verschoben: number }> {
  const res = await fetch(`${API}/api/ordner/verschieben`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ clip_ids: clipIds, ordner_id: ordnerId }) });
  return jsonOrThrow(res, "Clips verschieben fehlgeschlagen");
}
export async function importOrdnerInMedien(pfad: string, elternId: string | null, analyseStarten = true): Promise<{ ordner: OrdnerDTO; import_id: string; job_id: string }> {
  const res = await fetch(`${API}/api/ordner/importieren`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ pfad, eltern_id: elternId, analyse_starten: analyseStarten }) });
  return jsonOrThrow(res, "Ordner-Import fehlgeschlagen");
}

export interface OrdnerKandidat { pfad: string; quote: number; medien: number }
/** Gedroppten Finder-Ordner per Name + Dateinamen auf dem Rechner wiederfinden (Browser kennt keine Pfade). */
export async function findeOrdner(name: string | null, dateien: string[], typ: "video" | "audio"): Promise<{ kandidaten: OrdnerKandidat[]; durchsucht: number }> {
  const res = await fetch(`${API}/api/import/finden`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name, dateien, typ }) });
  return jsonOrThrow(res, "Ordner suchen fehlgeschlagen");
}

// ─── Transkriptions-Einstellungen (Whisper) ─────────────
export interface TranskriptionEinstellungen { sprache: string; glossar: string[]; modell: "turbo" | "qualitaet"; kanal: "sprachreichster" | "record" }
export async function fetchTranskriptionEinstellungen(): Promise<TranskriptionEinstellungen> {
  return jsonOrThrow(await fetch(`${API}/api/system/transkription`), "Einstellungen laden fehlgeschlagen");
}
export async function saveTranskriptionEinstellungen(e: Partial<TranskriptionEinstellungen>): Promise<TranskriptionEinstellungen> {
  const res = await fetch(`${API}/api/system/transkription`, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(e) });
  return jsonOrThrow(res, "Einstellungen speichern fehlgeschlagen");
}
// ─── Projekt-Kontext (für den Clip-Bericht / Synthese) ────
export interface ProjektEinstellungen { kontext: string; max_sprecher?: number | null }
export async function fetchProjektEinstellungen(): Promise<ProjektEinstellungen> {
  return jsonOrThrow(await fetch(`${API}/api/system/projekt`), "Projekt-Einstellungen laden fehlgeschlagen");
}
export async function saveProjektEinstellungen(e: ProjektEinstellungen): Promise<ProjektEinstellungen> {
  const res = await fetch(`${API}/api/system/projekt`, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(e) });
  return jsonOrThrow(res, "Projekt-Einstellungen speichern fehlgeschlagen");
}
export async function clipNeuAnalysieren(clipId: string): Promise<{ job_id: string }> {
  return jsonOrThrow(await fetch(`${API}/api/clips/${encodeURIComponent(clipId)}/neu-analysieren`, { method: "POST" }), "Neu analysieren fehlgeschlagen");
}
export async function clipNeuTranskribieren(clipId: string): Promise<{ job_id: string }> {
  return jsonOrThrow(await fetch(`${API}/api/clips/${encodeURIComponent(clipId)}/transkribieren`, { method: "POST" }), "Neu transkribieren fehlgeschlagen");
}

// ─── Drehbuch / Kontext-Schicht / Schnittplan ────────────
export interface SkriptZeileDTO { id: string; nr: number; art: "dialog" | "aktion" | "uebergang"; figur: string | null; regie: string | null; text: string; text_ziel: string | null; text_ziel_quelle: string | null }
export interface SzenenKontextDTO {
  zusammenfassung: string | null;
  beats: Array<{ nr: number; text: string; skript_zeilen?: string[]; typ?: string; gedreht?: boolean }> | null;
  figuren: Array<{ skript: string; film: string | null; rolle?: string; beleg?: string }> | null;
  coverage: Record<string, Record<string, number | null>> | null;
  take_ranking: Array<{ clip_id: string; dateiname: string | null; einstellung: string; take: number | null; score: number; gruende: string[]; abdeckung: number | null; spiel: [number | null, number | null]; ng: { abbruch?: boolean; kurz?: boolean; gruende?: string[] } | null }> | null;
  belege: string[] | null; unsicher: string[] | null; manuell_geprueft: boolean; aktualisiert_am: string | null;
  aktions_coverage?: Record<string, { text: string; status: "gedreht" | "unsicher" | "fehlt"; takes: Array<{ clip_id: string; dateiname: string | null; einstellung: string | null; spans: number[][] | null; ja: number | null; frames: number | null; clip_sim_rel: number | null }> }> | null;
}
export interface SkriptSzeneDTO { id: string; nummer: string; reihenfolge: number; ueberschrift: string | null; innen_aussen: string | null; ort: string | null; tageszeit: string | null; figuren: string[]; zeilen: SkriptZeileDTO[]; takes: number; kontext: SzenenKontextDTO | null }
export interface SkriptDTO { id: string; name: string; titel: string | null; sprache: string | null; ziel_sprache: string | null; status: string; erstellt_am: string | null; szenen: SkriptSzeneDTO[] }
export interface StoryKontextDTO { zusammenfassung: string | null; figuren: Array<{ skript: string; film: string | null; stimmen?: number }> | null; szenenfolge: string[] | null; arc: Array<{ szene: string; wendepunkt: string }> | null; motive: string[] | null; unsicher: string[] | null; aktualisiert_am: string | null }
export interface TakeKontextDTO { clip_id: string; dateiname: string | null; dauer: number | null; skript_szene_id: string | null; slate_szene: string | null; slate_take: number | null; slate_quelle: string | null; slate_konflikt: boolean; einstellung: string | null; spiel_start_s: number | null; spiel_ende_s: number | null; ng: { abbruch?: boolean; kurz?: boolean; gruende?: string[] } | null; abdeckung: number | null; bewertung: string | null; notiz: string | null; aktionen?: Record<string, { spans: number[][]; ja: number; frames: number; schritt?: number; clip_sim_rel: number | null; clip_sim_t?: number | null; label?: string | null }> | null; gesichter?: Record<string, { frames: number; anteil: number; spans: number[][] }> | null; zeilen?: Array<{ start: number; end: number; sprecher: string | null; text: string; art: string; skript_zeile_id: string | null; skript_zeile_nr: number | null; score: number | null }>; bildverlauf?: Array<{ t: number; beschreibung: string; personen: number | null }> }
export interface SchnittplanEintragDTO { nr: number; szene: string; clip_id: string; dateiname: string; einstellung: string | null; take: number | null; in_s: number; out_s: number; dauer: number; zeilen: number[]; art: "dialog" | "stumm" | "insert" | "cutaway" | "audio" | "alternative"; grund: string; beleg: string[]; video_only?: boolean; audio_only?: boolean; fade_in?: number; fade_out?: number; tl_start?: number | null; spur?: number; clip: { id: string; dateiname: string; dauer: number | null; video_url: string | null; proxy_url: string | null; waveform_url: string | null; strip_url: string | null; medientyp?: "video" | "audio"; medienart?: "video" | "audio" | "av"; hat_bild?: boolean | null; hat_ton?: boolean | null } | null }
export interface SchnittplanDTO { id: string; name: string; statistik: { eintraege: number; dauer_s: number; luecken: Array<Record<string, unknown>>; szenen: number; szenen_mit_material: number } | null; parameter: Record<string, unknown> | null; eintraege: SchnittplanEintragDTO[] }
export interface SkriptJobDTO { id: string; typ: string; status: string; fortschritt: number; nachricht: string | null; ergebnis: Record<string, unknown> | null }

export async function fetchSkript(): Promise<{ skript: SkriptDTO | null; story: StoryKontextDTO | null }> {
  return jsonOrThrow(await fetch(`${API}/api/skript`), "Drehbuch laden fehlgeschlagen");
}
export async function uploadSkript(file: File, zielSprache = "de"): Promise<{ job_id: string; transkription_sprache?: string; transkription_angepasst?: boolean; transkription_vorher?: string | null }> {
  const fd = new FormData(); fd.append("datei", file); fd.append("ziel_sprache", zielSprache);
  return jsonOrThrow(await fetch(`${API}/api/skript/upload`, { method: "POST", body: fd }), "Drehbuch-Upload fehlgeschlagen");
}
export async function fetchSkriptJob(jobId: string): Promise<SkriptJobDTO> {
  return jsonOrThrow(await fetch(`${API}/api/skript/job/${encodeURIComponent(jobId)}`), "Job laden fehlgeschlagen");
}
export async function aktionenPruefen(szenen?: string, neuFragen = false): Promise<{ job_id: string }> {
  const q = new URLSearchParams(); if (szenen) q.set("szenen", szenen); if (neuFragen) q.set("neu_fragen", "true");
  return jsonOrThrow(await fetch(`${API}/api/skript/kontext/aktionen?${q.toString()}`, { method: "POST" }), "Bildprüfung starten fehlgeschlagen");
}
export async function ordnerNachSkriptSortieren(): Promise<{ verschoben: number; ordner: string[] }> {
  return jsonOrThrow(await fetch(`${API}/api/skript/kontext/ordner-sortieren`, { method: "POST" }), "Ordner sortieren fehlgeschlagen");
}
export interface GesichtDTO { id: string; idx: number; anzahl: number; takes: number; name_skript: string | null; name_film: string | null; score: number | null; manuell: boolean; thumb_url: string | null; szenen_anteil: Record<string, number> | null }
export async function gesichterErkennen(): Promise<{ job_id: string }> {
  return jsonOrThrow(await fetch(`${API}/api/skript/gesichter/erkennen`, { method: "POST" }), "Gesichtserkennung starten fehlgeschlagen");
}
export async function fetchGesichter(): Promise<GesichtDTO[]> {
  return jsonOrThrow(await fetch(`${API}/api/skript/gesichter`), "Personen laden fehlgeschlagen");
}
export async function gesichtBenennen(id: string, body: { name_skript?: string; name_film?: string }): Promise<{ ok: boolean }> {
  return jsonOrThrow(await fetch(`${API}/api/skript/gesichter/${encodeURIComponent(id)}`, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }), "Benennen fehlgeschlagen");
}
export async function kontextAufbauen(mitLlm = true): Promise<{ job_id: string }> {
  return jsonOrThrow(await fetch(`${API}/api/skript/kontext/aufbauen?mit_llm=${mitLlm}`, { method: "POST" }), "Kontext-Aufbau fehlgeschlagen");
}
export async function fetchKontextTakes(): Promise<{ takes: TakeKontextDTO[] }> {
  return jsonOrThrow(await fetch(`${API}/api/skript/kontext`), "Kontext laden fehlgeschlagen");
}
export async function fetchTakeKontext(clipId: string): Promise<TakeKontextDTO> {
  return jsonOrThrow(await fetch(`${API}/api/skript/kontext/take/${encodeURIComponent(clipId)}`), "Take-Kontext laden fehlgeschlagen");
}
export async function setTakeKontext(clipId: string, body: { slate_szene?: string; slate_take?: number; bewertung?: string | null; notiz?: string }): Promise<{ ok: boolean; hinweis?: string }> {
  return jsonOrThrow(await fetch(`${API}/api/skript/kontext/take/${encodeURIComponent(clipId)}`, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }), "Take-Kontext speichern fehlgeschlagen");
}
export async function updateSkriptZeile(zeileId: string, body: { text_ziel?: string; text?: string; figur?: string }): Promise<{ ok: boolean }> {
  return jsonOrThrow(await fetch(`${API}/api/skript/zeile/${encodeURIComponent(zeileId)}`, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }), "Zeile speichern fehlgeschlagen");
}
export async function schnittplanErzeugen(body: { name?: string; modus?: "rohschnitt" | "feinschnitt"; coverage_wechsel?: boolean; stumm_max_s?: number; insert_dauer_s?: number }): Promise<{ job_id: string }> {
  return jsonOrThrow(await fetch(`${API}/api/skript/schnittplan`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }), "Rohschnitt fehlgeschlagen");
}
export async function fetchSchnittplaene(): Promise<Array<{ id: string; name: string; erstellt_am: string | null; statistik: SchnittplanDTO["statistik"] }>> {
  return jsonOrThrow(await fetch(`${API}/api/skript/schnittplan`), "Schnittpläne laden fehlgeschlagen");
}
export async function fetchSchnittplan(id: string): Promise<SchnittplanDTO> {
  return jsonOrThrow(await fetch(`${API}/api/skript/schnittplan/${encodeURIComponent(id)}`), "Schnittplan laden fehlgeschlagen");
}
