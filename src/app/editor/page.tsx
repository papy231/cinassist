"use client";

import { useState, useRef, useEffect, useCallback } from "react";
import { useEditorStore, type TLClip, type Track } from "@/stores/editorStore";
import {
  requestAiCut,
  saveTimeline,
  exportTimeline,
  connectJobWs,
  fetchProviders,
  fetchClipPipeline,
  type ClipDTO,
  type AiCutResult,
  type ExportSegment,
  type JobUpdate,
  type LLMProvider,
  type ProvidersResult,
  type PipelineBericht,
} from "@/lib/api";
import { PipelineSteps } from "@/components/PipelineSteps";
import { SceneDetail } from "@/components/SceneDetail";
import { ChatPanel } from "@/components/ChatPanel";
import { MaterialAtlas } from "@/components/MaterialAtlas";
import { MaterialRelations } from "@/components/MaterialRelations";

/* ═══════════════════════════════════════════════════════════
   CinAssist — Funktionaler Editor
   100% lokal — kein Cloud.
═══════════════════════════════════════════════════════════ */

type Tool = "select" | "blade" | "slip";
type MediaTab = "clips" | "chat" | "audio" | "assets";
type PageTab = "schnitt" | "farbe" | "effekte";
type AIStyle = "kinematisch" | "werbespot" | "kurzfilm" | "social_media" | "dokumentar";

interface CtxMenu { x: number; y: number; clipId: string }

// STYLES-Array entfernt — die Stil-Chips wurden durch den Chat-Assistenten
// ersetzt. Falls man später UI-Auswahl wieder aufnimmt, hier reaktivieren.

const TRANSITION_TYPES: { id: string; label: string }[] = [
  { id: "dissolve",   label: "Überblende" },
  { id: "fade",       label: "Einblenden" },
  { id: "fadeblack",  label: "Schwarz" },
  { id: "fadewhite",  label: "Weiß" },
  { id: "wipeleft",   label: "Wischen →" },
  { id: "wiperight",  label: "Wischen ←" },
  { id: "slideleft",  label: "Schieben →" },
  { id: "slideright", label: "Schieben ←" },
  { id: "pixelize",   label: "Pixel" },
  { id: "radial",     label: "Radial" },
];

// ─── Colorimetry ─────────────────────────────────────────
const NEUTRAL_CG = {
  exposure: 0, contrast: 0, saturation: 0,
  temperature: 0, tint: 0, shadows: 0, highlights: 0, hue: 0,
  liftX: 0, liftY: 0, gammaX: 0, gammaY: 0, gainX: 0, gainY: 0,
};
type CG = typeof NEUTRAL_CG;

// Auto-colorimetry presets applied when KI-Schnitt runs
const CG_PRESETS: Record<string, CG> = {
  // Cinematic teal-orange: cool shadows, lifted contrast, slight desaturation
  kinematisch:  { ...NEUTRAL_CG, contrast: 0.35, saturation: -0.12, temperature: -0.18, shadows: -0.08, highlights: 0.06, gainX: -0.06, gainY: 0.04 },
  // Documentary: natural, slightly warm, minimal processing
  dokumentar:   { ...NEUTRAL_CG, contrast: 0.10, temperature: 0.12, saturation: -0.05 },
  dokumentarisch:{ ...NEUTRAL_CG, contrast: 0.10, temperature: 0.12, saturation: -0.05 },
  // Ad spot: punchy, warm, high contrast
  werbespot:    { ...NEUTRAL_CG, contrast: 0.45, saturation: 0.28, temperature: 0.22, highlights: 0.12 },
  schnell:      { ...NEUTRAL_CG, contrast: 0.45, saturation: 0.28, temperature: 0.22, highlights: 0.12 },
  // Short film: warm, gentle contrast, cinematic
  kurzfilm:     { ...NEUTRAL_CG, contrast: 0.20, saturation: 0.08, temperature: 0.10, shadows: -0.05 },
  // Social media: vivid, high saturation, bright
  social_media: { ...NEUTRAL_CG, contrast: 0.25, saturation: 0.40, highlights: 0.18, exposure: 0.05 },
};

const TC_COLORS: Record<string, { bg: string; border: string; text: string }> = {
  orange: { bg: "rgba(249,115,22,0.22)",  border: "rgba(249,115,22,0.55)",  text: "#fdba74" },  // clip 1
  blue:   { bg: "rgba(37,99,235,0.22)",   border: "rgba(37,99,235,0.55)",   text: "#93c5fd" },  // clip 2
  purple: { bg: "rgba(168,85,247,0.22)",  border: "rgba(168,85,247,0.55)",  text: "#d8b4fe" },  // clip 3
  green:  { bg: "rgba(34,197,94,0.15)",   border: "rgba(34,197,94,0.4)",    text: "#86efac" },  // audio
};

// Assign a stable color to each video source clip by its index
const VIDEO_CLIP_COLORS: TLClip["color"][] = ["orange", "blue", "purple"];
function videoColorForClip(clipId: string, allClips: ClipDTO[]): TLClip["color"] {
  const idx = allClips.findIndex(c => c.id === clipId);
  return VIDEO_CLIP_COLORS[Math.max(0, idx) % VIDEO_CLIP_COLORS.length];
}

const PX_PER_SEC = 20;

function fmtDauer(sec: number | null | undefined): string {
  if (!sec) return "0:00";
  const m = Math.floor(sec / 60);
  const s = Math.floor(sec % 60);
  return `${m}:${String(s).padStart(2, "0")}`;
}

// ─── Custom cursor SVGs (data URIs) ────────────────────
const CURSOR_BLADE = `url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='24' height='24' viewBox='0 0 24 24' fill='none' stroke='%232563eb' stroke-width='2'%3E%3Cline x1='12' y1='2' x2='12' y2='22'/%3E%3Cpath d='M8 6l4-4 4 4'/%3E%3Cpath d='M8 18l4 4 4-4'/%3E%3C/svg%3E") 12 12, crosshair`;
const CURSOR_SLIP = `url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='24' height='24' viewBox='0 0 24 24' fill='none' stroke='%232563eb' stroke-width='2'%3E%3Cpath d='M5 12h14'/%3E%3Cpath d='M8 9l-3 3 3 3'/%3E%3Cpath d='M16 9l3 3-3 3'/%3E%3C/svg%3E") 12 12, ew-resize`;
const CURSOR_SELECT = "default";

function getToolCursor(tool: Tool): string {
  switch (tool) {
    case "blade": return CURSOR_BLADE;
    case "slip": return CURSOR_SLIP;
    default: return CURSOR_SELECT;
  }
}

function getClipCursor(tool: Tool, locked: boolean): string {
  if (locked) return "not-allowed";
  switch (tool) {
    case "blade": return CURSOR_BLADE;
    case "slip": return CURSOR_SLIP;
    default: return "grab";
  }
}

// ─── EDL / FCPXML helpers ────────────────────────────────
function tcFmt(sec: number, fps = 25): string {
  const h = Math.floor(sec / 3600);
  const m = Math.floor((sec % 3600) / 60);
  const s = Math.floor(sec % 60);
  const f = Math.round((sec % 1) * fps) % fps;
  return `${String(h).padStart(2,"0")}:${String(m).padStart(2,"0")}:${String(s).padStart(2,"0")}:${String(f).padStart(2,"0")}`;
}

function buildEdl(segs: TLClip[], fps = 25): string {
  const vSegs = segs.filter(s => s.track.toLowerCase().startsWith("v")).sort((a,b) => a.start - b.start);
  let edl = `TITLE: CinAssist Export\nFCM: NON-DROP FRAME\n\n`;
  vSegs.forEach((seg, idx) => {
    const reel = (seg.label || "AX").replace(/[^A-Za-z0-9_]/g,"_").substring(0,32).padEnd(8," ");
    const srcIn  = seg.mediaStart ?? 0;
    const srcOut = srcIn + seg.dauer;
    const recIn  = seg.start;
    const recOut = recIn + seg.dauer;
    let trans = "C      ";
    if (seg.transition && seg.transition.dauer > 0) {
      const frames = String(Math.round(seg.transition.dauer * fps)).padStart(3,"0");
      trans = `D ${frames}  `;
    }
    edl += `${String(idx+1).padStart(3,"0")}  ${reel}   V     ${trans}  ${tcFmt(srcIn,fps)} ${tcFmt(srcOut,fps)} ${tcFmt(recIn,fps)} ${tcFmt(recOut,fps)}\n`;
  });
  return edl;
}

function buildFcpxml(segs: TLClip[], allClips: import("@/lib/api").ClipDTO[], projectName = "HAW CineAssist"): string {
  const vSegs = segs.filter(s => s.track.toLowerCase().startsWith("v")).sort((a,b) => a.start - b.start);

  // FCPXML-Frame-Tick: (Zähler-pro-Frame, Nenner). Alle Zeitwerte werden als
  // ganzzahlige Vielfache dieses Ticks ausgedrückt — exakt das Format, das
  // Final Cut Pro selbst schreibt und das DaVinci Resolve zuverlässig liest.
  function frameTick(fps: number): [number, number] {
    if (Math.abs(fps - 23.976) < 0.05) return [1001, 24000];
    if (Math.abs(fps - 24)     < 0.05) return [100, 2400];
    if (Math.abs(fps - 25)     < 0.05) return [100, 2500];
    if (Math.abs(fps - 29.97)  < 0.05) return [1001, 30000];
    if (Math.abs(fps - 30)     < 0.05) return [100, 3000];
    if (Math.abs(fps - 47.952) < 0.05) return [1001, 48000];
    if (Math.abs(fps - 48)     < 0.05) return [100, 4800];
    if (Math.abs(fps - 50)     < 0.05) return [100, 5000];
    if (Math.abs(fps - 59.94)  < 0.05) return [1001, 60000];
    if (Math.abs(fps - 60)     < 0.05) return [100, 6000];
    return [100, Math.round(fps) * 100];
  }

  // frameDuration-Attribut für <format>
  function fpsToDuration(fps: number): string {
    const [num, den] = frameTick(fps);
    return `${num}/${den}s`;
  }

  // Konvertiert Sekunden → frame-aligned FCPXML-Zeitwert. Der Wert ist IMMER
  // ein ganzzahliges Vielfaches des frameTick (gleicher Nenner wie
  // frameDuration) — so kann DaVinci jeden Schnittpunkt exakt auf einen
  // Frame setzen, ohne "Unable to read FCPXML"-Fehler oder Lücken.
  function secToFrac(seconds: number, fps: number): string {
    const [num, den] = frameTick(fps);
    // Anzahl Frames = Sekunden / (num/den) = Sekunden * den / num
    const frames = Math.round((seconds * den) / num);
    return `${frames * num}/${den}s`;
  }

  // Parse "1920x1080" → [1920, 1080]
  function parseRes(res: string | null): [number, number] {
    const m = (res ?? "").match(/(\d+)\s*[xX×]\s*(\d+)/);
    return m ? [parseInt(m[1]), parseInt(m[2])] : [1920, 1080];
  }

  // Build FCP format name from fps + height
  function fmtName(fps: number, h: number): string {
    const p = h <= 720 ? "720" : h <= 1080 ? "1080" : h <= 1440 ? "1440" : "2160";
    const f = Math.abs(fps - 23.976) < 0.01 ? "2398" :
              Math.abs(fps - 29.97)  < 0.01 ? "2997" :
              Math.abs(fps - 59.94)  < 0.01 ? "5994" :
              String(Math.round(fps));
    return `FFVideoFormat${p}p${f}`;
  }

  // Encode source path as a proper file:// URI (3 slashes, percent-encode spaces)
  function fileUri(absPath: string): string {
    // absPath ist absolut (z.B. /Users/.../uploads/foo.mp4)
    // RFC 3986: file:///Users/...
    const encoded = absPath.split("/").map(s => encodeURIComponent(s)).join("/");
    return `file://${encoded}`;
  }

  // Collect unique format specs from clip metadata
  let rid = 1;
  const fmtMap = new Map<string, { id: string; fps: number; w: number; h: number }>();

  const clipFmt: Record<string, { id: string; fps: number; w: number; h: number }> = {};
  vSegs.forEach(s => {
    if (!s.clipId || clipFmt[s.clipId]) return;
    const dto = allClips.find(c => c.id === s.clipId);
    const fps = dto?.bildrate ?? 25;
    const [w, h] = parseRes(dto?.aufloesung ?? null);
    const key = `${fps}_${w}_${h}`;
    if (!fmtMap.has(key)) fmtMap.set(key, { id: `r${rid++}`, fps, w, h });
    clipFmt[s.clipId] = fmtMap.get(key)!;
  });
  if (fmtMap.size === 0) { fmtMap.set("25_1920_1080", { id: `r${rid++}`, fps: 25, w: 1920, h: 1080 }); }

  // Sequence uses the dominant format (first encountered)
  const seqFmt = fmtMap.values().next().value!;

  const assetIds: Record<string, string> = {};
  vSegs.forEach(s => { if (s.clipId && !assetIds[s.clipId]) assetIds[s.clipId] = `r${rid++}`; });

  // Sequenz-Gesamtdauer = letztes Segment-Ende
  const seqGesamtdauer = vSegs.reduce((max, s) => Math.max(max, s.start + s.dauer), 0);

  const formatXml = Array.from(fmtMap.values()).map(f =>
    `    <format id="${f.id}" name="${fmtName(f.fps, f.h)}" frameDuration="${fpsToDuration(f.fps)}" width="${f.w}" height="${f.h}" colorSpace="1-1-1 (Rec. 709)"/>`
  ).join("\n");

  const assets = Object.entries(assetIds).map(([clipId, id]) => {
    const seg = vSegs.find(s => s.clipId === clipId)!;
    const dto = allClips.find(c => c.id === clipId);
    const fmt = clipFmt[clipId] ?? seqFmt;
    const fps = fmt.fps;
    const name = seg.label.replace(/&/g,'&amp;').replace(/</g,'&lt;').substring(0, 40);
    const src = dto?.dateipfad
      ? fileUri(dto.dateipfad)
      : `file:///tmp/RECONNECT_${clipId}.mp4`;
    // Total duration of source file in native fps frames
    const totalDur = dto?.dauer ? secToFrac(dto.dauer, fps) : "3600s";
    // DaVinci verlangt audio-related Attribute auf dem Asset selbst
    return `    <asset id="${id}" name="${name}" uid="${clipId}" start="0s" duration="${totalDur}" hasVideo="1" hasAudio="1" format="${fmt.id}" videoSources="1" audioSources="1" audioChannels="2" audioRate="48000">\n      <media-rep kind="original-media" src="${src}"/>\n    </asset>`;
  }).join("\n");

  // asset-clip-Elemente. WICHTIG für DaVinci-Kompatibilität:
  //  • KEIN tcFormat-Attribut (gehört nur auf <sequence>)
  //  • selbstschließendes Tag <asset-clip .../>
  //  • KUMULATIVE offsets: jeder offset = Summe der vorherigen Dauern in
  //    Sequenz-Frames. Würde man jeden offset unabhängig runden, entstünden
  //    1-Frame-Lücken (schwarze Spalten zwischen den Clips in DaVinci).
  const [seqNum, seqDen] = frameTick(seqFmt.fps);
  let cursorFrames = 0;  // Position auf der Timeline, in Sequenz-Frames
  const clipXml = vSegs.map(seg => {
    const fmt = clipFmt[seg.clipId ?? ""] ?? seqFmt;
    const ref = assetIds[seg.clipId ?? ""] ?? `r${rid}`;
    // Dauer in Sequenz-Frames (ganzzahlig)
    const durFrames = Math.max(1, Math.round((seg.dauer * seqDen) / seqNum));
    // offset = kumulativer Cursor → garantiert lückenlose Aneinanderreihung
    const off = `${cursorFrames * seqNum}/${seqDen}s`;
    const dur = `${durFrames * seqNum}/${seqDen}s`;
    cursorFrames += durFrames;  // Cursor exakt um die gerundete Dauer weiterschieben
    // in/out-Punkt (start) im NATIVEN fps des Quellclips
    const ms  = secToFrac(seg.mediaStart ?? 0, fmt.fps);
    const name = seg.label.replace(/&/g,'&amp;').replace(/</g,'&lt;').substring(0, 40);
    return `          <asset-clip name="${name}" ref="${ref}" offset="${off}" duration="${dur}" start="${ms}"/>`;
  }).join("\n");

  // Sequenz-Gesamtdauer = exakt der Cursor-Endwert (keine Rundungsdifferenz)
  const seqDuration = `${cursorFrames * seqNum}/${seqDen}s`;

  return `<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE fcpxml>
<fcpxml version="1.10">
  <resources>
${formatXml}
${assets}
  </resources>
  <library>
    <event name="${projectName.replace(/&/g, '&amp;')}">
      <project name="${projectName.replace(/&/g, '&amp;')}">
        <sequence format="${seqFmt.id}" duration="${seqDuration}" tcStart="0s" tcFormat="NDF" audioLayout="stereo" audioRate="48k">
          <spine>
${clipXml}
          </spine>
        </sequence>
      </project>
    </event>
  </library>
</fcpxml>`;
}

function downloadText(content: string, filename: string, mime: string) {
  const blob = new Blob([content], { type: mime });
  const url  = URL.createObjectURL(blob);
  const a    = document.createElement("a");
  a.href = url; a.download = filename; a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

// ═══════════════════════════════════════════════════════════
// HAUPTKOMPONENTE
// ═══════════════════════════════════════════════════════════

export default function EditorPage() {
  const {
    clips, clipsLoading, loadClips, doUpload, removeClip,
    tlClips, gesamtdauer, addTLClip, updateTLClip, removeTLClip, setTLClips,
    tracks, addTrack, removeTrack, updateTrack,
    activeJobs, backendOnline, checkBackend,
    undo, redo, pushUndo,
  } = useEditorStore();

  // ─── Local UI State ───────────────────────────────────
  const [tool, setTool] = useState<Tool>("select");
  const [mediaTab, setMediaTab] = useState<MediaTab>("clips");
  const [pageTab, setPageTab] = useState<PageTab>("schnitt");
  // Stil ist auf "kinematisch" gepinnt — die Stil-Auswahl-Chips wurden
  // entfernt, weil der Chat-Assistent jetzt die Intent-Erfassung übernimmt.
  // Bei Bedarf könnte hier eine Auswahl wiedereingeführt werden.
  // aiStyle ist jetzt STATE statt const: der Chat kann ihn dynamisch setzen,
  // je nach editorialer Richtung (energetisch/ausgewogen/ruhig), die der
  // User in der Konversation wählt. Default = kinematisch (ausgewogen).
  const [aiStyle, setAiStyle] = useState<AIStyle>("kinematisch");
  const [aiPrompt, setAiPrompt] = useState("");
  // Provider auf Ollama gepinnt (100 % lokal). Cloud-Provider sind absichtlich
  // nicht im UI, um die Vertraulichkeitszusage zu wahren.
  const aiProvider: LLMProvider = "ollama";
  const [providers, setProviders] = useState<ProvidersResult | null>(null);
  const [showProviderMenu, setShowProviderMenu] = useState(false);
  const [selectedClip, setSelectedClip] = useState<string | null>(null);
  const [ctxMenu, setCtxMenu] = useState<CtxMenu | null>(null);
  const [pipelineBericht, setPipelineBericht] = useState<PipelineBericht | null>(null);
  const [pipelineBerichtLoading, setPipelineBerichtLoading] = useState(false);
  // ─── Layout — drag-resize wie DaVinci/Premiere ─────────
  const [sidebarWidth, setSidebarWidth] = useState(280);   // min 200, max 500
  const [viewerHeight, setViewerHeight] = useState(280);   // min 180, max 560
  const [uploadingA, setUploadingA] = useState(false);
  const [uploadingB, setUploadingB] = useState(false);
  const [aiLoading, setAiLoading] = useState(false);

  // Banner
  const [bannerVisible, setBannerVisible] = useState(false);
  const [bannerText, setBannerText] = useState("");
  const [pendingTLClips, setPendingTLClips] = useState<TLClip[] | null>(null);

  // Playback
  const [playing, setPlaying] = useState(false);
  const [pct, setPct] = useState(0);
  const rafRef = useRef<number>(0);

  // Zoom
  const [zoomLevel, setZoomLevel] = useState(100);

  // Drag
  const dragRef = useRef<{ clipId: string; startX: number; startVal: number } | null>(null);
  const resizeRef = useRef<{ clipId: string; startX: number; startDauer: number; startStart: number; isLeft: boolean } | null>(null);
  const slipRef = useRef<{ clipId: string; startX: number; origStart: number; origDauer: number; trackId: string; neighbors: { id: string; start: number; dauer: number }[] } | null>(null);
  const playheadDragRef = useRef<boolean>(false);
  const scrollContainerRef = useRef<HTMLDivElement>(null);

  // File input refs
  const fileRefA = useRef<HTMLInputElement>(null);
  const fileRefB = useRef<HTMLInputElement>(null);

  // Video preview refs
  const videoRefA = useRef<HTMLVideoElement>(null);
  const videoRefB = useRef<HTMLVideoElement>(null);
  // Bridging refs: prevent black frame when transition zone ends
  const prevTransRef  = useRef<{ clipA: TLClip; clipB: TLClip; progress: number; type: string; dauer: number } | null>(null);
  const bridgingUntil = useRef<number>(0);
  const lastClipBRef  = useRef<TLClip | null>(null);
  // Sync ref: drives pct from video.currentTime (avoids clock drift / stutter)
  const tlSyncRef = useRef<{ clip: TLClip; total: number } | null>(null);

  // Audio
  const [volume, setVolume] = useState(0.8);
  const [muted, setMuted] = useState(false);

  // Timeline toolbar
  const [snapping, setSnapping] = useState(true);
  const [linkedAV, setLinkedAV] = useState(true);

  // Transitions
  const [transitionPicker, setTransitionPicker] = useState<{ clipId: string; x: number; y: number } | null>(null);

  // Export
  const [exporting, setExporting] = useState(false);
  const [exportJobId, setExportJobId] = useState<string | null>(null);

  // Methodik-Panel: dokumentiert die wissenschaftliche Pipeline,
  // damit jederzeit nachvollziehbar ist, welche Modelle/Algorithmen
  // tatsächlich beteiligt sind — und mit welchen Referenzen.
  const [showMethodik, setShowMethodik] = useState(false);
  // Material-Atlas: visualisiert den CLIP-Embedding-Raum als 2D-Scatterplot
  const [showAtlas, setShowAtlas] = useState(false);
  // Material-Beziehungen: paarweise Korrelation (Multicam-Detektion)
  const [showRelations, setShowRelations] = useState(false);
  // Beat-Sync (librosa): rhythmus-bewusste Schnittgrenzen
  const [beatSync, setBeatSync] = useState(false);
  const [beatProSegment, setBeatProSegment] = useState(4);
  // KI-Pipeline-Overlay: visualisiert die tatsächlichen Schritte
  // (Embeddings laden → CLIP-Encoding → Top-K → Bogen → Beats → Timeline → Metriken)
  // damit der User SIEHT, was das System gerade rechnet.
  // pipelineSteps: Index des aktuellen Schritts; -1 = inaktiv; steps.length = fertig
  const [pipelineStepIdx, setPipelineStepIdx] = useState<number>(-1);
  // Letzte Cut-Metriken bleiben sichtbar, damit das Ergebnis
  // jederzeit quantitativ belegt ist (nicht nur subjektiv beurteilbar).
  const [lastMetrics, setLastMetrics] = useState<{
    stil: string;
    segmente: number;
    diversitaet: number;
    wechselrate: number;
    dialog_treue: number;
    prompt_relevance?: number;
  } | null>(null);

  // Resizable track heights — pro Track individuell speicherbar
  // (siehe Track.height im editorStore). 52 ist der Fallback.
  const trackResizeRef = useRef<{ startY: number; startH: number; trackId: string } | null>(null);

  // Clipboard
  const [copiedClip, setCopiedClip] = useState<TLClip | null>(null);

  // Colorimetry
  const [cg, setCG] = useState<CG>({ ...NEUTRAL_CG });
  const scopeRef = useRef<HTMLCanvasElement>(null);
  const scopeRaf = useRef(0);
  const wheelDragRef = useRef<{ wheel: "lift"|"gamma"|"gain"; el: HTMLDivElement } | null>(null);
  const liftWheelRef = useRef<HTMLDivElement>(null);
  const gammaWheelRef = useRef<HTMLDivElement>(null);
  const gainWheelRef = useRef<HTMLDivElement>(null);

  // ─── Colorimetry: computed SVG filter values ─────────
  const cgExpSlope = Math.max(0.05, 1 + cg.exposure * 0.8);
  const cgContSlope = Math.max(0.1, 1 + cg.contrast * 0.5);
  const cgContOff = cgExpSlope * (1 - cgContSlope) * 0.5;
  const cgLinR = (cgExpSlope * cgContSlope * (1 + cg.temperature * 0.2 + cg.gainX * 0.06)).toFixed(5);
  const cgLinG = (cgExpSlope * cgContSlope * (1 - cg.tint * 0.1 - cg.gainY * 0.04)).toFixed(5);
  const cgLinB = (cgExpSlope * cgContSlope * (1 - cg.temperature * 0.2 - cg.gainX * 0.06)).toFixed(5);
  const cgOffR = (cgContOff + cg.liftX * 0.07 + cg.liftY * 0.04 + cg.shadows * 0.08).toFixed(5);
  const cgOffG = (cgContOff - cg.liftY * 0.06 + cg.shadows * 0.08).toFixed(5);
  const cgOffB = (cgContOff - cg.liftX * 0.07 + cg.liftY * 0.04 + cg.shadows * 0.08).toFixed(5);
  const cgGamR = Math.max(0.3, 1 - cg.gammaX * 0.4 + cg.highlights * 0.5).toFixed(5);
  const cgGamG = Math.max(0.3, 1 + cg.highlights * 0.5 + cg.gammaY * 0.3).toFixed(5);
  const cgGamB = Math.max(0.3, 1 + cg.gammaX * 0.4 + cg.highlights * 0.5).toFixed(5);
  const cgSat   = Math.max(0, 1 + cg.saturation * 0.8).toFixed(5);
  const cgIsNeutral = !Object.values(cg).some(Boolean);
  const cgVideoFilter = cgIsNeutral ? undefined : `url(#cg-filter)${cg.hue !== 0 ? ` hue-rotate(${cg.hue}deg)` : ""}`;

  // ─── Init ─────────────────────────────────────────────
  useEffect(() => {
    checkBackend();
    loadClips();
    const iv = setInterval(() => checkBackend(), 15000);
    // Fetch verfügbare LLM-Provider (nur für die Modell-Anzeige; Provider
    // ist fest auf "ollama" gepinnt — 100 % lokal, keine Cloud).
    fetchProviders()
      .then(p => { setProviders(p); })
      .catch(() => { /* backend offline — kein Problem */ });
    return () => clearInterval(iv);
  }, []);

  // ─── Playback ─────────────────────────────────────────
  const totalSec = gesamtdauer || 1;
  useEffect(() => {
    if (!playing) return;
    let last = performance.now();
    let stopped = false;
    const tick = (now: number) => {
      if (stopped) return;
      const vid = videoRefA.current;
      const sync = tlSyncRef.current;
      // Primary: derive pct from video's actual currentTime → no clock drift, no stutter
      //
      // Guard rail: beim Wechsel zwischen Segmenten resettet das <video>-Element
      // kurzzeitig currentTime auf 0, BEVOR der seek auf mediaStart abgeschlossen
      // ist. Wenn wir das nicht abfangen, berechnet sich tlPos negativ und der
      // Playhead springt zurück auf 0 — und beim nächsten Frame nochmal usw.,
      // was zu einer Endlosschleife der ersten Sekunden führt.
      const ms = sync?.clip.mediaStart ?? 0;
      const dauer = sync?.clip.dauer ?? 0;
      const inRange = vid && sync
        ? vid.currentTime >= ms - 0.15 && vid.currentTime <= ms + dauer + 0.15
        : false;

      if (vid && sync && !vid.paused && !vid.ended && !vid.seeking && vid.readyState >= 3 && inRange) {
        const tlPos = sync.clip.start + (vid.currentTime - ms);
        const newPct = Math.max(0, Math.min(1, tlPos / sync.total));
        if (newPct >= 0.9999) {
          stopped = true;
          setPlaying(false);
          setPct(1);
          return;
        }
        setPct(newPct);
      } else {
        // Fallback: wall clock when video is loading / seeking / OOR
        const dt = (now - last) / 1000;
        const ts = sync?.total ?? totalSec;
        setPct(p => {
          const next = p + dt / ts;
          if (next >= 1) { stopped = true; setPlaying(false); return 1; }
          return next;
        });
      }
      last = now;
      if (!stopped) rafRef.current = requestAnimationFrame(tick);
    };
    rafRef.current = requestAnimationFrame(tick);
    return () => { stopped = true; cancelAnimationFrame(rafRef.current); };
  }, [playing, totalSec]);

  const curSec = pct * totalSec;
  const curMin = Math.floor(curSec / 60);
  const curS = Math.floor(curSec % 60);
  const curF = Math.floor((curSec % 1) * 24);
  const timecode = `00:${String(curMin).padStart(2, "0")}:${String(curS).padStart(2, "0")}:${String(curF).padStart(2, "0")}`;

  // ─── Active clips at playhead ─────────────────────────
  const API_BASE = "http://localhost:8001";

  // Prefer proxy (960p) over original 4K for browser preview — no stutter
  const previewUrl = useCallback((clip: ClipDTO | null): string | null => {
    if (!clip) return null;
    return clip.proxy_url ?? clip.video_url ?? null;
  }, []);

  const activeAtPlayhead = useCallback((trackFilter: string) => {
    return tlClips.find(c => c.track === trackFilter && curSec >= c.start && curSec < c.start + c.dauer);
  }, [tlClips, curSec]);

  // Dynamic: pick the first two video tracks for the viewer
  const vTracks = tracks.filter(t => t.type === "video");
  const activeTLClipV1 = vTracks[0] ? activeAtPlayhead(vTracks[0].id) : undefined;
  const activeTLClipV2 = vTracks[1] ? activeAtPlayhead(vTracks[1].id) : undefined;

  const clipForV1 = activeTLClipV1?.clipId ? clips.find(c => c.id === activeTLClipV1.clipId) ?? null : null;
  const clipForV2 = activeTLClipV2?.clipId ? clips.find(c => c.id === activeTLClipV2.clipId) ?? null : null;

  // ─── CSS Crossfade: transition zone detection ─────────
  // When the right clip has a `transition`, we detect a zone of ±dauer/2 around the cut point
  // and crossfade: videoRefA (outgoing) fades out, videoRefB (incoming) fades in.
  const transitionInfo = (() => {
    if (!vTracks[0]) return null;
    const vClips = tlClips
      .filter(c => c.track === vTracks[0].id)
      .sort((a, b) => a.start - b.start);
    for (let i = 0; i < vClips.length - 1; i++) {
      const a = vClips[i];
      const b = vClips[i + 1];
      if (!b.transition || b.transition.dauer <= 0) continue;
      const td = b.transition.dauer;
      const zoneStart = b.start;       // transition begins when B starts
      const zoneEnd   = b.start + td;  // fully complete after td seconds
      if (curSec >= zoneStart && curSec < zoneEnd) {
        const progress = Math.max(0, Math.min(1, (curSec - zoneStart) / td));
        return { clipA: a, clipB: b, progress, type: b.transition.type, dauer: td };
      }
    }
    return null;
  })();

  // ─── Bridging: keep vidB visible 0.45s after zone so vidA can load silently ─
  if (transitionInfo) lastClipBRef.current = transitionInfo.clipB;
  const _prevTrans = prevTransRef.current;
  prevTransRef.current = transitionInfo;
  if (_prevTrans && !transitionInfo) bridgingUntil.current = curSec + 0.45;
  const isBridging = !transitionInfo && curSec < bridgingUntil.current;

  const opacityV1 = transitionInfo ? 1 - transitionInfo.progress : (isBridging ? 0 : 1);

  // Effective clips: during transition/bridging use clipA/clipB
  const effectiveTLClipV1 = transitionInfo ? transitionInfo.clipA : activeTLClipV1;
  // Keep sync ref updated so the RAF loop can read clip info without React state
  tlSyncRef.current = effectiveTLClipV1 ? { clip: effectiveTLClipV1, total: totalSec } : null;
  const effectiveTLClipV2 = transitionInfo
    ? transitionInfo.clipB
    : (isBridging && lastClipBRef.current ? lastClipBRef.current : activeTLClipV2);
  const effectiveClipForV1 = effectiveTLClipV1?.clipId ? clips.find(c => c.id === effectiveTLClipV1.clipId) ?? null : null;
  const effectiveClipForV2 = effectiveTLClipV2?.clipId ? clips.find(c => c.id === effectiveTLClipV2.clipId) ?? null : null;

  // Next upcoming transition (to preload clipB in advance)
  const upcomingTransition = (() => {
    if (transitionInfo || isBridging || !vTracks[0]) return null;
    const vClips = tlClips.filter(c => c.track === vTracks[0].id).sort((a, b) => a.start - b.start);
    for (let i = 0; i < vClips.length - 1; i++) {
      const b = vClips[i + 1];
      if (!b.transition?.dauer) continue;
      const zoneStart = b.start;   // zone starts when B begins on the timeline
      if (curSec >= zoneStart - 3.0 && curSec < zoneStart) return { clip: b, medClip: clips.find(c => c.id === b.clipId) ?? null };
    }
    return null;
  })();

  // opacityV2: bridging=1 (cover vidA reload), preload=0.001, else normal
  const opacityV2 = transitionInfo
    ? transitionInfo.progress
    : isBridging
      ? 1
      : upcomingTransition
        ? 0.001
        : (clipForV2 ? 1 : 0);

  // ─── Persistent video src tracking (refs — never trigger re-render) ───────
  const prevSrcA   = useRef<string>("");
  const prevSrcB   = useRef<string>("");
  const bReadyRef  = useRef<boolean>(false);
  // Welches Timeline-Segment war zuletzt aktiv? Wird gebraucht, um beim
  // Wechsel der Schnittgrenze IMMER neu zu seeken — auch wenn das Quell-
  // video dasselbe ist (mehrere Segmente aus demselben Clip).
  const prevSegIdA = useRef<string>("");

  // ─── Single sync effect ──────────────────────────────────────────────────
  useEffect(() => {
    const vidA = videoRefA.current;
    const vidB = videoRefB.current;
    if (!vidA) return;
    const API = API_BASE;

    // ── Utility: load a clip into a video element, resolve when seeked ───────
    function loadInto(
      vid: HTMLVideoElement,
      url: string,
      seekTo: number,
      autoPlay: boolean,
      onReady?: () => void,
    ) {
      vid.pause();
      vid.src = url;
      vid.muted = true;
      vid.load();
      vid.addEventListener("canplay", () => {
        vid.currentTime = seekTo;
        // Wait for seek to complete before declaring ready
        const onSeeked = () => {
          vid.removeEventListener("seeked", onSeeked);
          onReady?.();
          if (autoPlay) vid.play().catch(() => {});
        };
        vid.addEventListener("seeked", onSeeked);
      }, { once: true });
    }

    // ── A: primary clip ────────────────────────────────────────────────────
    if (previewUrl(effectiveClipForV1) && effectiveTLClipV1) {
      const url    = `${API}${previewUrl(effectiveClipForV1)}`;
      const msStart = effectiveTLClipV1.mediaStart || 0;
      const msEnd   = msStart + effectiveTLClipV1.dauer;
      const localT  = Math.max(msStart, Math.min(msEnd - 0.01, (curSec - effectiveTLClipV1.start) + msStart));
      // Hat sich das aktive Segment geändert (Schnittgrenze überschritten)?
      // KRITISCH: wenn mehrere Segmente aus DEMSELBEN Quellclip stammen,
      // bleibt die URL gleich — ohne diese Erkennung würde das Video einfach
      // linear weiterlaufen statt zum mediaStart des neuen Segments zu springen.
      const segChanged = prevSegIdA.current !== effectiveTLClipV1.id;
      prevSegIdA.current = effectiveTLClipV1.id;
      vidA.volume = muted ? 0 : volume;
      vidA.muted  = muted;
      // During transition, mute A so only B's audio plays
      if (transitionInfo) { vidA.volume = 0; vidA.muted = true; }
      if (prevSrcA.current !== url) {
        // ── Post-transition seamless swap ──────────────────────────────
        // If vidB already has the new clip loaded and is at approx the right
        // position (happens right after a dissolve/fade ends), copy its
        // currentTime to avoid a visible reload/restart.
        const vidB = videoRefB.current;
        if (
          vidB &&
          prevSrcB.current === url &&
          bReadyRef.current &&
          Math.abs(vidB.currentTime - localT) < 1.5
        ) {
          prevSrcA.current = url;
          vidA.src = url;
          vidA.currentTime = vidB.currentTime;
          vidA.muted  = muted;
          vidA.volume = muted ? 0 : volume;
          if (playing) vidA.play().catch(() => {});
        } else {
          prevSrcA.current = url;
          loadInto(vidA, url, localT, playing, () => {
            vidA.muted  = muted || !!transitionInfo;
            vidA.volume = (muted || !!transitionInfo) ? 0 : volume;
            if (playing) vidA.play().catch(() => {});
          });
        }
      } else {
        if (!playing) {
          if (!vidA.seeking && Math.abs(vidA.currentTime - localT) > 0.042) vidA.currentTime = localT;
          if (!vidA.paused) vidA.pause();
        } else {
          // Re-seek bei: (a) Segmentwechsel — die Schnittgrenze wurde
          // überschritten, das Video MUSS zum neuen mediaStart springen,
          // auch wenn es derselbe Quellclip ist; (b) Ende des Videos;
          // (c) starker Desync (>1s, Sicherheitsnetz).
          if (!vidA.seeking && (segChanged || vidA.ended || Math.abs(vidA.currentTime - localT) > 1.0)) {
            vidA.currentTime = localT;
          }
          if ((vidA.paused || vidA.ended) && !vidA.seeking) vidA.play().catch(() => {});
        }
      }
    } else {
      if (!vidA.paused) vidA.pause();
      if (prevSrcA.current) { vidA.removeAttribute("src"); vidA.load(); prevSrcA.current = ""; }
    }

    // ── B: preloaded next clip — NEVER assigned a new src during a transition ─
    if (!vidB) return;

    if (transitionInfo) {
      // Transition in progress — B should already be loaded (from preload phase).
      // Only do timing corrections, never change src.
      if (bReadyRef.current && previewUrl(effectiveClipForV2) && effectiveTLClipV2) {
        const msStart = effectiveTLClipV2.mediaStart || 0;
        const msEnd   = msStart + effectiveTLClipV2.dauer;
        const localT  = Math.max(msStart, Math.min(msEnd - 0.01, (curSec - effectiveTLClipV2.start) + msStart));
        vidB.muted  = muted;
        vidB.volume = muted ? 0 : volume;
        if (!playing) {
          if (!vidB.seeking && Math.abs(vidB.currentTime - localT) > 0.042) vidB.currentTime = localT;
          if (!vidB.paused) vidB.pause();
        } else {
          if (!vidB.seeking && Math.abs(vidB.currentTime - localT) > 0.5) vidB.currentTime = localT;
          if (vidB.paused) vidB.play().catch(() => {});
        }
      }
    } else if (upcomingTransition?.medClip && previewUrl(upcomingTransition.medClip) && upcomingTransition.clip) {
      // Pre-transition phase: silently load and park B at mediaStart
      const url = `${API}${previewUrl(upcomingTransition.medClip)}`;
      const ms  = upcomingTransition.clip.mediaStart || 0;
      if (prevSrcB.current !== url) {
        prevSrcB.current = url;
        bReadyRef.current = false;
        loadInto(vidB, url, ms, false, () => {
          bReadyRef.current = true;
          vidB.pause();
        });
      } else {
        // Already loaded — keep parked at ms (let it drift max 0.3s)
        if (bReadyRef.current && Math.abs(vidB.currentTime - ms) > 0.3) vidB.currentTime = ms;
        if (!vidB.paused) vidB.pause();
      }
    } else {
      // Normal: B shows V2 track or nothing
      if (previewUrl(effectiveClipForV2) && effectiveTLClipV2) {
        const url    = `${API}${previewUrl(effectiveClipForV2)}`;
        const msStart = effectiveTLClipV2.mediaStart || 0;
        const msEnd   = msStart + effectiveTLClipV2.dauer;
        const localT  = Math.max(msStart, Math.min(msEnd - 0.01, (curSec - effectiveTLClipV2.start) + msStart));
        vidB.volume = muted ? 0 : volume;
        vidB.muted  = muted;
        if (prevSrcB.current !== url) {
          prevSrcB.current = url;
          bReadyRef.current = false;
          loadInto(vidB, url, localT, playing, () => {
            bReadyRef.current = true;
            vidB.muted  = muted;
            vidB.volume = muted ? 0 : volume;
            if (playing) vidB.play().catch(() => {});
          });
        } else {
          if (!playing) {
            if (!vidB.seeking && Math.abs(vidB.currentTime - localT) > 0.042) vidB.currentTime = localT;
            if (!vidB.paused) vidB.pause();
          } else {
            if (!vidB.seeking && Math.abs(vidB.currentTime - localT) > 0.5) vidB.currentTime = localT;
            if (vidB.paused) vidB.play().catch(() => {});
          }
        }
      } else {
        if (!vidB.paused) vidB.pause();
        if (prevSrcB.current && !upcomingTransition) {
          vidB.removeAttribute("src"); vidB.load(); prevSrcB.current = ""; bReadyRef.current = false;
        }
      }
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [curSec, playing, effectiveClipForV1, effectiveTLClipV1, effectiveClipForV2, effectiveTLClipV2, upcomingTransition, transitionInfo, volume, muted]);

  // ─── Zoom ─────────────────────────────────────────────
  const scale = zoomLevel / 100;
  const tlWidth = Math.max(800, (gesamtdauer || 60) * PX_PER_SEC * scale);

  // ─── Ruler ────────────────────────────────────────────
  const secPx = PX_PER_SEC * scale;
  const rulerTicks: { x: number; label: string; major: boolean }[] = [];
  for (let i = 0; i <= Math.ceil(tlWidth / secPx); i++) {
    const x = i * secPx;
    if (x > tlWidth) break;
    rulerTicks.push({
      x,
      label: i % 5 === 0 && i > 0 ? `${Math.floor(i / 60)}:${String(i % 60).padStart(2, "0")}` : "",
      major: i % 5 === 0,
    });
  }

  // ─── Upload Handler ───────────────────────────────────
  const handleUpload = useCallback(async (files: FileList | null) => {
    if (!files || files.length === 0) return;
    setUploadingA(true);
    try {
      for (let i = 0; i < files.length; i++) {
        await doUpload(files[i], "A");
      }
    } catch (err) {
      console.error(err);
    } finally {
      setUploadingA(false);
    }
  }, [doUpload]);

  const handleDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    handleUpload(e.dataTransfer.files);
  }, [handleUpload]);

  // ─── Add clip to timeline by button ──────────────────
  const handleClipDragToTimeline = useCallback((clip: ClipDTO, track: string) => {
    const existing = tlClips.filter(c => c.track === track);
    const lastEnd = existing.reduce((max, c) => Math.max(max, c.start + c.dauer), 0);
    const ts = Date.now();
    const trackDef = tracks.find(t => t.id === track);
    const isVideo = trackDef?.type === "video";
    const firstAudioTrack = tracks.find(t => t.type === "audio");
    const gId = linkedAV && isVideo && firstAudioTrack ? `grp-${clip.id}-${ts}` : undefined;
    addTLClip({
      id: `tl-${clip.id}-${ts}`,
      clipId: clip.id,
      label: `${clip.dateiname} · ${clip.quelle}`,
      track,
      start: lastEnd,
      dauer: clip.dauer || 10,
      mediaStart: 0,
      color: isVideo ? videoColorForClip(clip.id, clips) : "green",
      groupId: gId,
    });
    // Linked audio track
    if (linkedAV && isVideo && firstAudioTrack) {
      const existingA = tlClips.filter(c => c.track === firstAudioTrack.id);
      addTLClip({
        id: `tl-${clip.id}-a1-${ts}`,
        clipId: clip.id,
        label: `♪ ${clip.dateiname}`,
        track: firstAudioTrack.id,
        start: lastEnd,
        dauer: clip.dauer || 10,
        mediaStart: 0,
        color: "green",
        groupId: gId,
      });
    }
  }, [tlClips, addTLClip, linkedAV, tracks]);

  // ─── Clipboard handlers ───────────────────────────────
  const handleCut = useCallback(() => {
    if (!selectedClip) return;
    const clip = tlClips.find(c => c.id === selectedClip);
    if (clip) {
      setCopiedClip(clip);
      // Remove grouped clips too
      if (clip.groupId) {
        tlClips.filter(c => c.groupId === clip.groupId).forEach(c => removeTLClip(c.id));
      } else {
        removeTLClip(clip.id);
      }
      setSelectedClip(null);
    }
  }, [selectedClip, tlClips, removeTLClip]);

  const handleCopy = useCallback(() => {
    if (!selectedClip) return;
    const clip = tlClips.find(c => c.id === selectedClip);
    if (clip) setCopiedClip(clip);
  }, [selectedClip, tlClips]);

  const handlePaste = useCallback(() => {
    if (!copiedClip) return;
    addTLClip({ ...copiedClip, id: `${copiedClip.id}-paste-${Date.now()}`, start: pct * totalSec });
  }, [copiedClip, addTLClip, pct, totalSec]);

  // ─── Blade cut at click position ──────────────────────
  const handleBladeCut = useCallback((clip: TLClip, e: React.MouseEvent) => {
    const trackEl = (e.currentTarget as HTMLElement).parentElement;
    if (!trackEl) return;
    const rect = trackEl.getBoundingClientRect();
    const clickSec = (e.clientX - rect.left) / (PX_PER_SEC * scale);
    if (clickSec > clip.start && clickSec < clip.start + clip.dauer) {
      pushUndo();
      const leftDauer = clickSec - clip.start;
      const rightDauer = clip.dauer - leftDauer;
      const origMediaStart = clip.mediaStart || 0;
      // Cut this clip
      updateTLClip(clip.id, { dauer: leftDauer });
      addTLClip({ ...clip, id: `${clip.id}-cut-${Date.now()}`, start: clickSec, dauer: rightDauer, mediaStart: origMediaStart + leftDauer, groupId: clip.groupId ? `${clip.groupId}-r` : undefined });
      // Also cut grouped clips at the same time position
      if (clip.groupId) {
        const grouped = tlClips.filter(c => c.groupId === clip.groupId && c.id !== clip.id);
        grouped.forEach(gc => {
          if (clickSec > gc.start && clickSec < gc.start + gc.dauer) {
            const gcLeft = clickSec - gc.start;
            const gcRight = gc.dauer - gcLeft;
            const gcMediaStart = gc.mediaStart || 0;
            updateTLClip(gc.id, { dauer: gcLeft });
            addTLClip({ ...gc, id: `${gc.id}-cut-${Date.now()}`, start: clickSec, dauer: gcRight, mediaStart: gcMediaStart + gcLeft, groupId: `${clip.groupId}-r` });
          }
        });
      }
    }
  }, [scale, updateTLClip, addTLClip, tlClips, pushUndo]);

  // ─── Keyboard Shortcuts ───────────────────────────────
  useEffect(() => {
    const FRAME = 1 / 24;
    const onKey = (e: KeyboardEvent) => {
      if ((e.target as HTMLElement).tagName === "INPUT" || (e.target as HTMLElement).tagName === "TEXTAREA") return;
      const ctrl = e.ctrlKey || e.metaKey;
      // Clipboard
      if (ctrl && e.key === "z" && e.shiftKey) { e.preventDefault(); redo(); }
      else if (ctrl && e.key === "z") { e.preventDefault(); undo(); }
      else if (ctrl && e.key === "x") { e.preventDefault(); handleCut(); }
      else if (ctrl && e.key === "c") { e.preventDefault(); handleCopy(); }
      else if (ctrl && e.key === "v") { e.preventDefault(); handlePaste(); }
      // Undo/Redo already handled above
      // Tools
      else if (e.key === "a" || e.key === "A") setTool("select");
      else if (e.key === "b" || e.key === "B") setTool("blade");
      else if (e.key === "t" || e.key === "T") setTool("slip");
      else if (e.key === "n" || e.key === "N") setSnapping(s => !s);
      // Delete
      else if (e.key === "Delete" || e.key === "Backspace") {
        if (selectedClip) {
          e.preventDefault();
          const clip = tlClips.find(c => c.id === selectedClip);
          if (clip?.groupId) {
            tlClips.filter(c => c.groupId === clip.groupId).forEach(c => removeTLClip(c.id));
          } else {
            removeTLClip(selectedClip);
          }
          setSelectedClip(null);
        }
      }
      // Playback
      else if (e.key === " ") { e.preventDefault(); setPlaying(p => !p); }
      // Frame navigation — Left/Right arrow
      else if (e.key === "ArrowLeft") { e.preventDefault(); setPct(p => Math.max(0, p - FRAME / totalSec)); setPlaying(false); }
      else if (e.key === "ArrowRight") { e.preventDefault(); setPct(p => Math.min(1, p + FRAME / totalSec)); setPlaying(false); }
      // 5-second jump — Shift+Arrow
      else if (e.key === "ArrowLeft" && e.shiftKey) { e.preventDefault(); setPct(p => Math.max(0, p - 5 / totalSec)); }
      else if (e.key === "ArrowRight" && e.shiftKey) { e.preventDefault(); setPct(p => Math.min(1, p + 5 / totalSec)); }
      // Home / End
      else if (e.key === "Home") { e.preventDefault(); setPct(0); setPlaying(false); }
      else if (e.key === "End") { e.preventDefault(); setPct(1); setPlaying(false); }
      // J / K / L — DaVinci transport: J=back, K=stop, L=forward
      else if (e.key === "j" || e.key === "J") { setPct(p => Math.max(0, p - 1 / totalSec)); }
      else if (e.key === "k" || e.key === "K") { setPlaying(false); }
      else if (e.key === "l" || e.key === "L") { setPct(p => Math.min(1, p + 1 / totalSec)); }
      // Up/Down — select prev/next clip
      else if (e.key === "ArrowUp" || e.key === "ArrowDown") {
        e.preventDefault();
        if (tlClips.length === 0) return;
        const idx = selectedClip ? tlClips.findIndex(c => c.id === selectedClip) : -1;
        const next = e.key === "ArrowDown" ? (idx + 1) % tlClips.length : (idx - 1 + tlClips.length) % tlClips.length;
        setSelectedClip(tlClips[next].id);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [handleCut, handleCopy, handlePaste, selectedClip, removeTLClip, totalSec, tlClips, undo, redo]);

  // ─── Track height resize (per-Track) ──────────────────
  useEffect(() => {
    const onMove = (e: MouseEvent) => {
      if (!trackResizeRef.current) return;
      const dy = e.clientY - trackResizeRef.current.startY;
      const newH = Math.max(28, Math.min(160, trackResizeRef.current.startH + dy));
      updateTrack(trackResizeRef.current.trackId, { height: newH });
    };
    const onUp = () => { trackResizeRef.current = null; document.body.style.cursor = ""; };
    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
    return () => { window.removeEventListener("mousemove", onMove); window.removeEventListener("mouseup", onUp); };
  }, []);

  // ─── Drag / Resize / Slip on timeline ──────────────────
  useEffect(() => {
    const onMove = (e: MouseEvent) => {
      if (dragRef.current) {
        const { clipId, startX, startVal } = dragRef.current;
        const dx = e.clientX - startX;
        const dSec = dx / (PX_PER_SEC * scale);
        const newStart = Math.max(0, startVal + dSec);
        updateTLClip(clipId, { start: newStart });
        // Move grouped clips by the same delta
        const clip = tlClips.find(c => c.id === clipId);
        if (clip?.groupId) {
          const delta = newStart - clip.start;
          tlClips.filter(c => c.groupId === clip.groupId && c.id !== clipId).forEach(c => {
            updateTLClip(c.id, { start: Math.max(0, c.start + delta) });
          });
        }
      }
      if (resizeRef.current) {
        const { clipId, startX, startDauer, startStart, isLeft } = resizeRef.current;
        const dx = e.clientX - startX;
        const dSec = dx / (PX_PER_SEC * scale);
        if (isLeft) {
          const newDauer = Math.max(0.5, startDauer - dSec);
          const newStart = startStart + (startDauer - newDauer);
          updateTLClip(clipId, { start: Math.max(0, newStart), dauer: newDauer });
        } else {
          updateTLClip(clipId, { dauer: Math.max(0.5, startDauer + dSec) });
        }
      }
      if (slipRef.current) {
        const { clipId, startX, origStart, origDauer, trackId, neighbors } = slipRef.current;
        const dx = e.clientX - startX;
        const dSec = dx / (PX_PER_SEC * scale);
        // Ripple edit: move this clip's right edge and adjust next clip's left edge
        const newDauer = Math.max(0.5, origDauer + dSec);
        updateTLClip(clipId, { dauer: newDauer });
        // Find the direct right neighbor and adjust it
        const rightNeighbor = neighbors.find(n => Math.abs(n.start - (origStart + origDauer)) < 0.05);
        if (rightNeighbor) {
          const delta = newDauer - origDauer;
          const rnNewStart = rightNeighbor.start + delta;
          const rnNewDauer = rightNeighbor.dauer - delta;
          if (rnNewDauer > 0.5) {
            updateTLClip(rightNeighbor.id, { start: rnNewStart, dauer: rnNewDauer });
          }
        }
      }
      if (playheadDragRef.current && scrollContainerRef.current) {
        const container = scrollContainerRef.current;
        const rect = container.getBoundingClientRect();
        const absX = (e.clientX - rect.left) + container.scrollLeft;
        const newSec = Math.max(0, Math.min(totalSec, absX / (PX_PER_SEC * scale)));
        setPct(totalSec > 0 ? newSec / totalSec : 0);
      }
      // Color wheel drag
      if (wheelDragRef.current) {
        const { wheel, el } = wheelDragRef.current;
        const R = 40;
        const rect = el.getBoundingClientRect();
        let nx = ((e.clientX - rect.left) - R) / (R - 6);
        let ny = ((e.clientY - rect.top) - R) / (R - 6);
        const mag = Math.sqrt(nx * nx + ny * ny);
        if (mag > 1) { nx /= mag; ny /= mag; }
        const wx = parseFloat(nx.toFixed(3)), wy = parseFloat(ny.toFixed(3));
        if (wheel === "lift")  setCG(p => ({ ...p, liftX: wx, liftY: wy }));
        else if (wheel === "gamma") setCG(p => ({ ...p, gammaX: wx, gammaY: wy }));
        else setCG(p => ({ ...p, gainX: wx, gainY: wy }));
      }
    };
    const onUp = () => {
      // Push undo snapshot when drag/resize/slip ends
      if (dragRef.current || resizeRef.current || slipRef.current) pushUndo();
      dragRef.current = null; resizeRef.current = null; slipRef.current = null;
      playheadDragRef.current = false;
      wheelDragRef.current = null;
    };
    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
    return () => { window.removeEventListener("mousemove", onMove); window.removeEventListener("mouseup", onUp); };
  }, [scale, totalSec, updateTLClip, tlClips, pushUndo]);

  // ─── Context Menu Close ───────────────────────────────
  useEffect(() => {
    const close = () => { setCtxMenu(null); setTransitionPicker(null); };
    window.addEventListener("click", close);
    return () => window.removeEventListener("click", close);
  }, []);

  // ─── Histogram Scope ──────────────────────────────────
  useEffect(() => {
    if (pageTab !== "farbe") { cancelAnimationFrame(scopeRaf.current); return; }
    const draw = () => {
      const canvas = scopeRef.current;
      const vid = videoRefA.current;
      if (!canvas || !vid || vid.readyState < 2) { scopeRaf.current = requestAnimationFrame(draw); return; }
      const ctx = canvas.getContext("2d"); if (!ctx) return;
      const tmp = document.createElement("canvas"); tmp.width = 128; tmp.height = 72;
      const tc = tmp.getContext("2d"); if (!tc) return;
      try {
        tc.drawImage(vid, 0, 0, 128, 72);
        const px = tc.getImageData(0, 0, 128, 72).data;
        const rH = new Uint32Array(256), gH = new Uint32Array(256), bH = new Uint32Array(256);
        for (let i = 0; i < px.length; i += 4) { rH[px[i]]++; gH[px[i+1]]++; bH[px[i+2]]++; }
        const mx = Math.max(Math.max(...rH), Math.max(...gH), Math.max(...bH)) || 1;
        const w = canvas.width, h = canvas.height, bw = w / 256;
        ctx.fillStyle = "#080a0a"; ctx.fillRect(0, 0, w, h);
        for (let x = 0; x < 256; x++) {
          ctx.globalAlpha = 0.65;
          ctx.fillStyle = "#ef4444"; ctx.fillRect(x*bw, h-(rH[x]/mx)*h, bw+0.5, (rH[x]/mx)*h);
          ctx.fillStyle = "#22c55e"; ctx.fillRect(x*bw, h-(gH[x]/mx)*h, bw+0.5, (gH[x]/mx)*h);
          ctx.fillStyle = "#3b82f6"; ctx.fillRect(x*bw, h-(bH[x]/mx)*h, bw+0.5, (bH[x]/mx)*h);
        }
        ctx.globalAlpha = 1;
      } catch { ctx.fillStyle = "#111"; ctx.fillRect(0, 0, canvas.width, canvas.height); }
      scopeRaf.current = requestAnimationFrame(draw);
    };
    scopeRaf.current = requestAnimationFrame(draw);
    return () => cancelAnimationFrame(scopeRaf.current);
  }, [pageTab]);

  // ─── Timeline Seek ───────────────────────────────────
  const handleSeek = (e: React.MouseEvent<HTMLDivElement>) => {
    const rect = e.currentTarget.getBoundingClientRect();
    setPct(Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width)));
  };

  // ─── KI-Schnitt ──────────────────────────────────────
  // Akzeptiert optionale Overrides für Stil und Prompt. Grund: wenn der Chat
  // einen Cut auslöst, sind setAiStyle/setAiPrompt noch nicht geflusht (React
  // State ist async). Der Chat übergibt die Werte daher DIREKT — der Closure
  // würde sonst die alten State-Werte verwenden.
  const handleAiCut = useCallback(async (overrideStyle?: AIStyle, overridePrompt?: string) => {
    const effectiveStyle: AIStyle = overrideStyle ?? aiStyle;
    const effectivePrompt: string = overridePrompt ?? aiPrompt;
    const clipIds = clips.filter(c => c.status === "analysiert" || c.status === "hochgeladen").map(c => c.id);
    if (clipIds.length === 0) {
      setBannerVisible(true);
      setBannerText("⚠ Keine Clips vorhanden — bitte zuerst Videos hochladen.");
      setTimeout(() => setBannerVisible(false), 3000);
      return;
    }
    setAiLoading(true);
    setBannerVisible(true);
    setBannerText(`✦ Strukturierter Schnitt läuft… Stil: ${effectiveStyle}`);
    // Pipeline-Overlay: zeigt SCHRITT FÜR SCHRITT, was das System rechnet.
    // Wir machen 10 Schritte sichtbar, mit gemütlichem Tempo (≈ 800ms pro
    // Schritt). Das System ist nicht unter Zeitdruck — der User soll
    // wahrnehmen, dass mehrere Phasen real ablaufen.
    setPipelineStepIdx(0);
    let stepCancelled = false;
    // 10 Schritte, jeweils ~700-1100ms. Insgesamt ~8-10s.
    // Mit Beat-Sync zusätzlich: librosa läuft real im Backend (+2-3s),
    // also der entsprechende Schritt zeigt länger.
    const stepDelays = [
      700,                          // 1. Embeddings laden
      900,                          // 2. Prompt-Encoder
      800,                          // 3. Cosine-Similarity
      900,                          // 4. Pool-Vorauswahl (Top-3K)
      1100,                         // 5. Multicam-Dedup
      950,                          // 6. MMR Re-Ranking (Diversität)
      beatSync ? 2400 : 700,        // 7. Beat-Sync / Rollen
      800,                          // 8. Timeline-Segmente bauen
      700,                          // 9. Metriken berechnen
      500,                          // 10. Abschluss
    ];
    const animateSteps = async () => {
      for (let i = 0; i < stepDelays.length; i++) {
        if (stepCancelled) return;
        setPipelineStepIdx(i);
        await new Promise(r => setTimeout(r, stepDelays[i]));
      }
    };
    const animationPromise = animateSteps();
    try {
      const result = await requestAiCut({
        stil: effectiveStyle,
        prompt: effectivePrompt || undefined,
        clip_ids: clipIds,
        provider: aiProvider,
        beat_sync: beatSync,
        beat_pro_segment: beatProSegment,
      });
      // Erst zu Ende animieren bevor das Resultat gezeigt wird
      await animationPromise;
      setPipelineStepIdx(stepDelays.length); // "fertig"-Zustand
      // Kurz "✓ Fertig" anzeigen, dann ausblenden
      setTimeout(() => setPipelineStepIdx(-1), 600);
      const newClips: TLClip[] = (result.daten?.segmente || []).map((seg: any) => ({
        id: seg.id,
        clipId: seg.clip_id,
        szeneNr: seg.szene_nr,
        label: seg.label,
        track: seg.track as TLClip["track"],
        start: seg.start,
        dauer: seg.dauer,
        mediaStart: seg.mediaStart ?? seg.media_start ?? 0,
        color: (seg.track === "a1" ? "green" : videoColorForClip(seg.clip_id, clips)) as TLClip["color"],
        ai: true,
        groupId: seg.groupId ?? seg.group_id,
        transition: seg.transition ?? undefined,
        // Provenienz für "Warum dieses Cut?"-Popover
        beschreibung: seg.beschreibung ?? undefined,
        transkription: seg.transkription ?? undefined,
        rolle: seg.rolle ?? undefined,
        promptRelevance: seg.prompt_relevance ?? null,
        energie: seg.energie ?? null,
        interessantheit: seg.interessantheit ?? null,
      }));
      setPendingTLClips(newClips);
      // Auto-apply cinematic colour grade for this style
      setCG({ ...(CG_PRESETS[effectiveStyle] ?? NEUTRAL_CG) });

      // Quantitative Metriken in den Banner aufnehmen — das ist das Herzstück
      // der wissenschaftlichen Selbstbewertung.
      const m = result.metriken;
      let metricsLine = "";
      if (m) {
        metricsLine = ` · Diversität ${m.diversitaet.toFixed(2)} · Wechselrate ${(m.wechselrate * 100).toFixed(0)}% · Dialog-Treue ${(m.dialog_treue * 100).toFixed(0)}%`;
        // Prompt-Relevanz erscheint nur, wenn der User einen Prompt eingegeben hat
        if (m.prompt_relevance !== undefined) {
          metricsLine += ` · Prompt-Relevanz ${(m.prompt_relevance * 100).toFixed(0)}%`;
        }
        setLastMetrics({
          stil: effectiveStyle,
          segmente: result.segmente_anzahl,
          diversitaet: m.diversitaet,
          wechselrate: m.wechselrate,
          dialog_treue: m.dialog_treue,
          prompt_relevance: m.prompt_relevance,
        });
      }
      setBannerText(
        `✦ ${effectiveStyle} · ${result.segmente_anzahl} Segmente · ${fmtDauer(result.gesamtdauer)}${metricsLine}`
      );
    } catch (err) {
      stepCancelled = true;
      setPipelineStepIdx(-1);
      setBannerText(`✗ Schnitt fehlgeschlagen: ${err instanceof Error ? err.message : "Unbekannter Fehler"}`);
      setTimeout(() => setBannerVisible(false), 4000);
    } finally {
      setAiLoading(false);
    }
  }, [clips, aiStyle, aiPrompt, aiProvider, beatSync, beatProSegment]);

  // ─── Timeline narrativ neu ordnen ────────────────────
  // Nimmt die bereits platzierten Plans und ordnet sie nach Aristotelischer
  // Bogenform: ruhige Eröffnung → Aufbau → Höhepunkt → optionaler Ausklang.
  // Inhalt bleibt 100 % derselbe — nur die Reihenfolge ändert sich.
  const handleReorganize = useCallback(async () => {
    if (tlClips.length === 0) {
      setBannerVisible(true);
      setBannerText("⚠ Keine Segmente auf der Timeline zum Umordnen.");
      setTimeout(() => setBannerVisible(false), 3000);
      return;
    }
    setBannerVisible(true);
    setBannerText("✦ Narrativer Bogen wird berechnet…");
    try {
      const { reorganizeTimeline } = await import("@/lib/api");
      const result = await reorganizeTimeline(
        tlClips.map(c => ({
          id: c.id,
          clip_id: c.clipId ?? null,
          szene_nr: c.szeneNr ?? null,
          dauer: c.dauer,
          mediaStart: c.mediaStart,
          track: c.track,
          groupId: c.groupId ?? null,
          label: c.label ?? null,
        }))
      );
      // Map result back to TLClip, preserving the fields wir hatten
      const byId = new Map(tlClips.map(c => [c.id, c]));
      const neueClips: TLClip[] = result.segmente.map(seg => {
        const alt = byId.get(seg.id);
        return {
          id: seg.id,
          clipId: seg.clip_id ?? undefined,
          szeneNr: seg.szene_nr ?? undefined,
          label: seg.label ?? alt?.label ?? "",
          track: seg.track,
          start: seg.start,
          dauer: seg.dauer,
          mediaStart: seg.mediaStart,
          color: alt?.color ?? "blue",
          ai: alt?.ai,
          groupId: seg.groupId ?? alt?.groupId,
          transition: alt?.transition,
          beschreibung: alt?.beschreibung,
          transkription: alt?.transkription,
          rolle: seg.rolle ?? alt?.rolle,
          promptRelevance: alt?.promptRelevance,
          energie: alt?.energie,
          interessantheit: alt?.interessantheit,
        };
      });
      setTLClips(neueClips);
      const rollen = Object.entries(result.arc_rollen)
        .filter(([, n]) => (n as number) > 0)
        .map(([r, n]) => `${r}:${n}`)
        .join(" · ");
      setBannerText(
        `✦ Timeline neu geordnet · ${result.anzahl} Segmente · ${fmtDauer(result.gesamtdauer)}` +
        (rollen ? ` · ${rollen}` : "")
      );
      setTimeout(() => setBannerVisible(false), 4000);
    } catch (err) {
      setBannerText(`✗ Re-Organisation fehlgeschlagen: ${err instanceof Error ? err.message : "Fehler"}`);
      setTimeout(() => setBannerVisible(false), 4000);
    }
  }, [tlClips, setTLClips]);

  const handleApplyAi = useCallback(() => {
    if (pendingTLClips) {
      // Ensure all needed tracks exist
      const neededTracks = new Set(pendingTLClips.map(c => c.track));
      const existingIds = new Set(tracks.map(t => t.id));
      for (const tid of neededTracks) {
        if (!existingIds.has(tid)) {
          const isVideo = tid.startsWith("v");
          addTrack(isVideo ? "video" : "audio");
        }
      }
      setTLClips(pendingTLClips);
      setPendingTLClips(null);
    }
    setBannerText("✅ Timeline angewendet — jedes Segment ist bearbeitbar");
    setTimeout(() => setBannerVisible(false), 2000);
  }, [pendingTLClips, setTLClips, tracks, addTrack]);

  // ─── Save Timeline ───────────────────────────────────
  const handleSave = useCallback(async () => {
    try {
      await saveTimeline({
        name: "Editor-Timeline",
        stil: aiStyle,
        daten: {
          segmente: tlClips.map(c => ({
            id: c.id, clip_id: c.clipId || "", szene_nr: c.szeneNr,
            label: c.label, track: c.track, start: c.start, dauer: c.dauer,
            quelle: c.color === "orange" ? "A" : c.color === "blue" ? "B" : c.color === "green" ? "audio" : "music",
          })),
          gesamtdauer,
        },
      });
      setBannerVisible(true);
      setBannerText("✅ Timeline gespeichert");
      setTimeout(() => setBannerVisible(false), 2000);
    } catch (err) {
      console.error("Speichern fehlgeschlagen:", err);
    }
  }, [tlClips, gesamtdauer, aiStyle]);

  // ─── Export (FFmpeg xfade) ───────────────────────────
  const handleExport = useCallback(async () => {
    const vSegs = tlClips.filter(c => c.track.startsWith("v") && c.clipId);
    if (vSegs.length === 0) {
      setBannerVisible(true);
      setBannerText("⚠ Keine Videosegmente in der Timeline zum Exportieren.");
      setTimeout(() => setBannerVisible(false), 3000);
      return;
    }
    setExporting(true);
    setBannerVisible(true);
    setBannerText("🎬 Export wird gestartet…");
    try {
      const segs: ExportSegment[] = tlClips.map(c => ({
        id: c.id,
        clip_id: c.clipId || "",
        track: c.track,
        start: c.start,
        dauer: c.dauer,
        mediaStart: c.mediaStart ?? 0,
        transition: c.transition,
      }));
      const res = await exportTimeline({ segments: segs, resolution: "1920x1080" });
      setExportJobId(res.job_id);
      setBannerText(`${res.nachricht}`);
      // Track export job via WebSocket
      connectJobWs(res.job_id, (data: JobUpdate) => {
        if (data.status === "fertig") {
          const url = (data.result as Record<string, string>)?.output_url;
          setBannerText(`✅ Export abgeschlossen! ${url ? `→ /outputs/${(data.result as Record<string, string>).output_filename}` : ""}`);
          setExporting(false);
          setExportJobId(null);
          if (url) window.open(`http://localhost:8001${url}`, "_blank");
        } else if (data.status === "fehler") {
          setBannerText(`✗ Export fehlgeschlagen: ${data.message}`);
          setExporting(false);
          setExportJobId(null);
          setTimeout(() => setBannerVisible(false), 5000);
        } else {
          setBannerText(`Export läuft… ${data.progress}% — ${data.message}`);
        }
      });
    } catch (err) {
      setBannerText(`✗ Export fehlgeschlagen: ${err instanceof Error ? err.message : "Unbekannter Fehler"}`);
      setExporting(false);
      setTimeout(() => setBannerVisible(false), 4000);
    }
  }, [tlClips]);

  // ─── Track groups ─────────────────────────────────────
  const videoTracks = tracks.filter(t => t.type === "video");
  const audioTracks = tracks.filter(t => t.type === "audio");
  const trackCount = tracks.length;

  // ═══════════════════════════════════════════════════════
  // RENDER
  // ═══════════════════════════════════════════════════════
  return (
    <div style={{ display: "grid", gridTemplateRows: "44px 1fr 42px", height: "100vh" }}>

      {/* ═══ ANALYSE-OVERLAY (Loader während Ingestion) ═══ */}
      {activeJobs.length > 0 && (
        <div style={{
          position: "fixed", inset: 0, zIndex: 9999,
          background: "rgba(8,9,9,.82)", backdropFilter: "blur(8px)",
          display: "flex", alignItems: "center", justifyContent: "center",
          pointerEvents: "auto",
          padding: 24,
          overflowY: "auto",
        }}>
          <div style={{
            background: "var(--bg1)", border: "1px solid var(--border)",
            borderRadius: 16, padding: "28px 32px",
            width: "100%", maxWidth: 640, maxHeight: "calc(100vh - 48px)",
            overflowY: "auto",
            boxShadow: "0 20px 60px rgba(0,0,0,.5)",
          }}>
            {/* Spinner */}
            <div style={{ display: "flex", justifyContent: "center", marginBottom: 16 }}>
              <div style={{
                width: 44, height: 44, borderRadius: "50%",
                border: "3px solid var(--bg4)",
                borderTopColor: "var(--orange)",
                animation: "spin 1s linear infinite",
              }} />
            </div>

            <h3 style={{ textAlign: "center", fontSize: 16, fontWeight: 700, color: "var(--text)", margin: "0 0 4px" }}>
              KI-Analyse läuft
            </h3>
            <p style={{ textAlign: "center", fontSize: 11, color: "var(--text3)", margin: "0 0 20px" }}>
              Das Video wird lokal auf deinem Mac analysiert — kein Cloud-Upload.
            </p>

            {activeJobs.map((job, jobIdx) => (
              <div
                key={job.jobId}
                style={{
                  marginBottom: 16,
                  paddingTop: jobIdx > 0 ? 16 : 0,
                  borderTop: jobIdx > 0 ? "1px solid var(--border)" : "none",
                }}
              >
                {/* Dateiname + Gesamt-Fortschritt */}
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 6 }}>
                  <span style={{ fontSize: 12, fontWeight: 600, color: "var(--text)" }}>
                    {clips.find(c => c.id === job.clipId)?.dateiname || "Video"}
                  </span>
                  <span style={{ fontFamily: "var(--mono)", fontSize: 12, fontWeight: 700, color: "var(--orange)" }}>
                    {job.fortschritt}%
                  </span>
                </div>

                {/* Progress bar */}
                <div style={{ height: 6, background: "var(--bg4)", borderRadius: 3, overflow: "hidden", marginBottom: 6 }}>
                  <div style={{
                    height: "100%", width: `${job.fortschritt}%`,
                    background: "linear-gradient(90deg, var(--orange), #3b82f6)",
                    borderRadius: 3, transition: "width .4s ease",
                  }} />
                </div>

                {/* Aktuelle Nachricht */}
                <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
                  <div style={{
                    width: 6, height: 6, borderRadius: "50%", flexShrink: 0,
                    background: job.status === "fehler" ? "var(--red)" : "var(--orange)",
                    animation: job.status === "fehler" ? "none" : "pulse 1.5s ease infinite",
                  }} />
                  <span style={{ fontSize: 10, color: "var(--text3)" }}>
                    {job.nachricht || "Wird vorbereitet…"}
                  </span>
                </div>

                {/* Pipeline-Schritte mit Belegen */}
                <PipelineSteps
                  aktuellerSchritt={job.aktuellerSchritt}
                  schrittHistory={job.schrittHistory}
                />
              </div>
            ))}
          </div>
        </div>
      )}

      <style>{`@keyframes spin { to { transform: rotate(360deg) } }`}</style>

      {/* ═══ PIPELINE-BERICHT MODAL (post-mortem, via Rechtsklick) ═══ */}
      {pipelineBericht && (
        <div
          onClick={() => setPipelineBericht(null)}
          style={{
            position: "fixed", inset: 0, zIndex: 9998,
            background: "rgba(8,9,9,.82)", backdropFilter: "blur(8px)",
            display: "flex", alignItems: "center", justifyContent: "center",
            padding: 24, overflowY: "auto",
          }}
        >
          <div
            onClick={e => e.stopPropagation()}
            style={{
              background: "var(--bg1)", border: "1px solid var(--border)",
              borderRadius: 16, padding: "24px 28px",
              width: "100%", maxWidth: 640, maxHeight: "calc(100vh - 48px)",
              overflowY: "auto",
              boxShadow: "0 20px 60px rgba(0,0,0,.5)",
            }}
          >
            {/* Header */}
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", marginBottom: 16, paddingBottom: 12, borderBottom: "1px solid var(--border)" }}>
              <div>
                <div style={{ fontSize: 10, fontWeight: 700, color: "var(--text3)", letterSpacing: ".05em", textTransform: "uppercase", marginBottom: 2 }}>
                  Analyse-Bericht
                </div>
                <h3 style={{ fontSize: 14, fontWeight: 700, color: "var(--text)", margin: 0, fontFamily: "var(--mono)" }}>
                  {pipelineBericht.dateiname}
                </h3>
              </div>
              <button
                onClick={() => setPipelineBericht(null)}
                style={{
                  background: "none", border: "1px solid var(--border)",
                  color: "var(--text3)", cursor: "pointer",
                  padding: "4px 10px", borderRadius: 5,
                  fontSize: 11, fontWeight: 600,
                }}
                onMouseEnter={e => { e.currentTarget.style.color = "var(--text)"; e.currentTarget.style.borderColor = "var(--text3)"; }}
                onMouseLeave={e => { e.currentTarget.style.color = "var(--text3)"; e.currentTarget.style.borderColor = "var(--border)"; }}
              >
                Schließen
              </button>
            </div>

            {pipelineBerichtLoading && Object.keys(pipelineBericht.schritt_history).length === 0 ? (
              <div style={{ padding: "40px 0", textAlign: "center", fontSize: 11, color: "var(--text3)" }}>
                Lade Pipeline-Bericht…
              </div>
            ) : (
              <>
                <PipelineSteps schrittHistory={pipelineBericht.schritt_history} />

                {pipelineBericht.szenen_detail && pipelineBericht.szenen_detail.length > 0 && (
                  <div style={{ marginTop: 20, paddingTop: 14, borderTop: "1px solid var(--border)" }}>
                    <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", marginBottom: 10 }}>
                      <span style={{ fontSize: 10, fontWeight: 700, color: "var(--text3)", letterSpacing: ".05em", textTransform: "uppercase" }}>
                        Pro Szene — Rohdaten
                      </span>
                      <span style={{ fontFamily: "var(--mono)", fontSize: 10, color: "var(--text3)" }}>
                        {pipelineBericht.szenen_detail.length} Szene{pipelineBericht.szenen_detail.length !== 1 ? "n" : ""}
                      </span>
                    </div>
                    {pipelineBericht.szenen_detail.map(s => (
                      <SceneDetail key={s.id} szene={s} />
                    ))}
                  </div>
                )}
              </>
            )}
          </div>
        </div>
      )}

      {/* ═══ SVG COLORIMETRY FILTER ═══ */}
      <svg style={{ position: "absolute", width: 0, height: 0, overflow: "visible" }} aria-hidden="true">
        <defs>
          <filter id="cg-filter" x="0%" y="0%" width="100%" height="100%" colorInterpolationFilters="sRGB">
            <feColorMatrix type="saturate" values={cgSat} result="sat" />
            <feComponentTransfer in="sat" result="corr">
              <feFuncR type="linear" slope={parseFloat(cgLinR)} offset={parseFloat(cgOffR)} />
              <feFuncG type="linear" slope={parseFloat(cgLinG)} offset={parseFloat(cgOffG)} />
              <feFuncB type="linear" slope={parseFloat(cgLinB)} offset={parseFloat(cgOffB)} />
            </feComponentTransfer>
            <feComponentTransfer in="corr">
              <feFuncR type="gamma" amplitude={1} exponent={parseFloat(cgGamR)} offset={0} />
              <feFuncG type="gamma" amplitude={1} exponent={parseFloat(cgGamG)} offset={0} />
              <feFuncB type="gamma" amplitude={1} exponent={parseFloat(cgGamB)} offset={0} />
            </feComponentTransfer>
          </filter>
        </defs>
      </svg>

      {/* ═══ TOPBAR ═══ */}
      <header style={{ display: "flex", alignItems: "center", gap: 0, background: "var(--bg1)", borderBottom: "1px solid var(--border)", padding: "0 14px" }}>
        <div style={{ display: "flex", alignItems: "center", gap: 7, marginRight: 20 }}>
          <div style={{ width: 26, height: 26, background: "var(--orange)", borderRadius: 6, display: "flex", alignItems: "center", justifyContent: "center" }}>
            <svg width={14} height={14} viewBox="0 0 24 24" fill="white"><path d="M5 3l14 9-14 9V3z" /></svg>
          </div>
          <span style={{ fontFamily: "var(--mono)", fontSize: 13, fontWeight: 500, letterSpacing: -0.3 }}><span style={{ color: "var(--text2)", fontWeight: 400 }}>HAW </span>Cine<span style={{ color: "var(--orange)" }}>Assist</span></span>
        </div>
        <Sep />
        <div style={{ display: "flex", alignItems: "center", gap: 2 }}>
          <TbBtn active={tool === "select"} onClick={() => setTool("select")} icon={<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2}><path d="m3 3 7.07 16.97 2.51-7.39 7.39-2.51L3 3z" /></svg>} label="Auswahl" />
          <TbBtn active={tool === "blade"} onClick={() => setTool("blade")} icon={<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2}><circle cx={6} cy={6} r={3}/><circle cx={6} cy={18} r={3}/><line x1={20} y1={4} x2={8.12} y2={15.88}/><line x1={14.47} y1={14.48} x2={20} y2={20}/><line x1={8.12} y1={8.12} x2={12} y2={12}/></svg>} label="Schneiden" />
          <TbBtn active={tool === "slip"} onClick={() => setTool("slip")} icon={<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2}><path d="M5 12h14M12 5l7 7-7 7" /></svg>} label="Ripple-Edit" />
        </div>
        <Sep />
        <div style={{ display: "flex", alignItems: "center", gap: 2 }}>
          <TbBtn icon={<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2}><polyline points="9 14 4 9 9 4"/><path d="M20 20v-7a4 4 0 0 0-4-4H4"/></svg>} label="Rückgängig" onClick={undo} />
          <TbBtn icon={<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2}><polyline points="15 14 20 9 15 4"/><path d="M4 20v-7a4 4 0 0 1 4-4h12"/></svg>} label="Wiederholen" onClick={redo} />
        </div>
        <Sep />
        <TbBtn
          icon={<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2}><path d="M7 20l-3-3 3-3"/><path d="M4 17h13a4 4 0 0 0 4-4"/><path d="M17 4l3 3-3 3"/><path d="M20 7H7a4 4 0 0 0-4 4"/></svg>}
          label="Re-Organisieren"
          onClick={handleReorganize}
        />
        <div style={{ marginLeft: "auto", display: "flex", alignItems: "center", gap: 8 }}>
          <div style={{ fontFamily: "var(--mono)", fontSize: 12, fontWeight: 500, color: "var(--text)", background: "var(--bg3)", border: "1px solid var(--border2)", borderRadius: 5, padding: "3px 10px", letterSpacing: ".04em" }}>
            <span style={{ color: "var(--orange)" }}>{timecode}</span>
          </div>
          <button onClick={handleExport} disabled={exporting} style={{ display: "flex", alignItems: "center", gap: 5, padding: "6px 14px", background: exporting ? "var(--bg4)" : "var(--orange)", color: "white", border: "none", borderRadius: 6, fontSize: 11, fontWeight: 600, cursor: exporting ? "wait" : "pointer", fontFamily: "var(--font)", letterSpacing: ".03em", opacity: exporting ? 0.7 : 1 }}>
            <svg width={12} height={12} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2.5}><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4M7 10l5 5 5-5M12 15V3"/></svg>
            {exporting ? "Exportiert…" : "Exportieren 1080p"}
          </button>
          <SendToNleButton tlClips={tlClips} clips={clips} />
          <button
            title="FCPXML als Datei herunterladen — falls du die Software manuell öffnen möchtest"
            onClick={() => downloadText(buildFcpxml(tlClips, clips), "cinassist_export.fcpxml", "application/xml")}
            style={{ display: "flex", alignItems: "center", gap: 4, padding: "6px 10px", background: "var(--bg3)", color: "var(--text3)", border: "1px solid var(--border2)", borderRadius: 6, fontSize: 9.5, fontWeight: 600, cursor: "pointer", fontFamily: "var(--font)" }}
          >
            <svg width={10} height={10} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2}><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4M7 10l5 5 5-5M12 15V3"/></svg>
            Download FCPXML
          </button>
        </div>
      </header>

      {/* ═══ WORKSPACE ═══ */}
      <div style={{
        display: "grid",
        gridTemplateColumns: pageTab === "farbe"
          ? `${sidebarWidth}px 5px 1fr 260px`
          : `${sidebarWidth}px 5px 1fr`,
        overflow: "hidden",
      }}>
        {/* ─── LEFT: MEDIA PANEL ─── */}
        <aside style={{ background: "var(--bg1)", borderRight: "1px solid var(--border)", display: "flex", flexDirection: "column", overflow: "hidden" }}>
          <div style={{ display: "flex", alignItems: "center", padding: "8px 12px", borderBottom: "1px solid var(--border)", gap: 6 }}>
            <span style={{ fontSize: 10, fontWeight: 600, textTransform: "uppercase", letterSpacing: ".08em", color: "var(--text3)" }}>Medien</span>
            <span style={{ marginLeft: "auto", fontSize: 9, color: "var(--text3)" }}>{clips.length} Clip{clips.length !== 1 ? "s" : ""}</span>
          </div>
          <div style={{ display: "flex", padding: "0 10px", gap: 0, borderBottom: "1px solid var(--border)" }}>
            {(["clips", "chat", "audio", "assets"] as MediaTab[]).map(tab => (
              <button key={tab} onClick={() => setMediaTab(tab)} style={{ padding: "5px 10px", fontSize: 10, fontWeight: 600, color: mediaTab === tab ? "var(--orange)" : "var(--text3)", cursor: "pointer", borderTop: "none", borderLeft: "none", borderRight: "none", borderBottom: `2px solid ${mediaTab === tab ? "var(--orange)" : "transparent"}`, marginBottom: -1, background: "none", fontFamily: "var(--font)", textTransform: "capitalize" }}>{tab === "clips" ? "Clips" : tab === "chat" ? "Assistent" : tab === "audio" ? "Audio" : "Assets"}</button>
            ))}
          </div>

          <input ref={fileRefA} type="file" accept="video/*" multiple hidden onChange={e => handleUpload(e.target.files)} />

          {/* CHAT-TAB — Konversation mit dem Schnittassistenten */}
          {mediaTab === "chat" && (
            <ChatPanel clips={clips} onProposedPrompt={(prompt, stil) => {
              setAiPrompt(prompt);
              // Chat-Stil (energetisch/ausgewogen/ruhig) → STIL_CONFIG-Name.
              // DAS ist die Verbindung, die vorher fehlte: die editoriale
              // Wahl des Users steuert jetzt Tempo, Schnittlänge und Bogen.
              const stilMap: Record<string, AIStyle> = {
                energetisch: "werbespot",   // schnell, kurze Schnitte
                ausgewogen:  "kinematisch", // mittel, dramatischer Bogen
                ruhig:       "dokumentar",  // langsam, lange Takes, chronologisch
              };
              const mapped: AIStyle = (stil && stilMap[stil]) ? stilMap[stil] : "kinematisch";
              setAiStyle(mapped);  // für UI-Anzeige (Banner, CG-Preset)
              setMediaTab("clips");
              // handleAiCut bekommt Stil + Prompt DIREKT übergeben — nicht
              // über den State-Closure (der wäre noch nicht geflusht).
              setTimeout(() => handleAiCut(mapped, prompt), 120);
            }} />
          )}

          {/* PLATZHALTER für Audio/Assets — kommen später */}
          {(mediaTab === "audio" || mediaTab === "assets") && (
            <div style={{ flex: 1, display: "flex", alignItems: "center", justifyContent: "center", padding: 20, fontSize: 11, color: "var(--text3)", textAlign: "center" }}>
              {mediaTab === "audio" ? "Audio-Bibliothek folgt." : "Asset-Bibliothek folgt."}
            </div>
          )}

          {/* CLIPS-TAB — Upload + Liste */}
          {mediaTab === "clips" && <>
          <UploadZone label="Videos" badge="Videos ablegen" badgeStyle={{}} loading={uploadingA} onClick={() => fileRefA.current?.click()} onDrop={e => handleDrop(e)} />

          <div style={{ flex: 1, overflowY: "auto", padding: 6 }}>
            {clipsLoading && clips.length === 0 && <div style={{ padding: 16, textAlign: "center", fontSize: 11, color: "var(--text3)" }}>Lade Clips…</div>}
            {!clipsLoading && clips.length === 0 && <div style={{ padding: 16, textAlign: "center", fontSize: 11, color: "var(--text3)" }}>Noch keine Clips.<br/>Videos oben ablegen um zu beginnen.</div>}
            {clips.map((clip, clipIdx) => (
              <ClipCard key={clip.id} clip={clip} job={activeJobs.find(j => j.clipId === clip.id)}
                clipColor={VIDEO_CLIP_COLORS[clipIdx % VIDEO_CLIP_COLORS.length]}
                onAddToTimeline={() => {
                  const firstVideoTrack = tracks.find(t => t.type === "video");
                  handleClipDragToTimeline(clip, firstVideoTrack?.id || "v1");
                }}
                onDelete={async () => { try { await removeClip(clip.id); } catch (err) { console.error(err); } }}
                onShowPipeline={async () => {
                  if (clip.status !== "analysiert") return;
                  setPipelineBerichtLoading(true);
                  setPipelineBericht({ clip_id: clip.id, dateiname: clip.dateiname, schritt_history: {}, szenen_detail: [] });
                  try {
                    const bericht = await fetchClipPipeline(clip.id);
                    setPipelineBericht(bericht);
                  } catch (err) {
                    console.error("Pipeline-Bericht laden fehlgeschlagen:", err);
                  } finally {
                    setPipelineBerichtLoading(false);
                  }
                }} />
            ))}
          </div>

          </>}
        </aside>

        {/* ─── Resize handle: sidebar ↔ editor ─── */}
        <ResizeHandle
          direction="horizontal"
          onResize={d => setSidebarWidth(w => Math.max(200, Math.min(500, w + d)))}
        />

        {/* ─── RIGHT: EDITOR AREA ─── */}
        <div style={{ display: "flex", flexDirection: "column", overflow: "hidden", background: "var(--bg0)" }}>

          {/* ═══ VIDEO PREVIEW (Single Viewer — DaVinci-style) ═══ */}
          <div style={{ flexShrink: 0, background: "#000", borderBottom: "1px solid var(--border)", position: "relative" }}>
            {/* Volume control */}
            <div style={{ position: "absolute", top: 8, left: 10, zIndex: 10, display: "flex", alignItems: "center", gap: 4, background: "rgba(0,0,0,.6)", borderRadius: 5, padding: "2px 6px" }}>
              <button onClick={() => setMuted(!muted)} style={{ background: "none", border: "none", cursor: "pointer", padding: 2, display: "flex", alignItems: "center" }}>
                {muted || volume === 0
                  ? <svg width={14} height={14} viewBox="0 0 24 24" fill="none" stroke="var(--text3)" strokeWidth={2}><polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5"/><line x1={23} y1={9} x2={17} y2={15}/><line x1={17} y1={9} x2={23} y2={15}/></svg>
                  : <svg width={14} height={14} viewBox="0 0 24 24" fill="none" stroke="var(--text3)" strokeWidth={2}><polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5"/><path d="M19.07 4.93a10 10 0 0 1 0 14.14M15.54 8.46a5 5 0 0 1 0 7.07"/></svg>}
              </button>
              <input type="range" min={0} max={1} step={0.05} value={muted ? 0 : volume} onChange={e => { setVolume(parseFloat(e.target.value)); if (muted) setMuted(false); }} style={{ width: 60, height: 3, accentColor: "var(--orange)", cursor: "pointer" }} />
            </div>
            {/* Active track badge */}
            {(clipForV2 || clipForV1) && (
              <div style={{ position: "absolute", top: 8, right: 10, zIndex: 10, display: "flex", gap: 4, background: "rgba(0,0,0,.6)", borderRadius: 5, padding: "3px 8px", alignItems: "center" }}>
                {clipForV2 && <span style={{ fontSize: 9, fontWeight: 700, padding: "1px 6px", borderRadius: 3, background: "rgba(59,130,246,.25)", color: "var(--blue)" }}>V2</span>}
                {clipForV1 && <span style={{ fontSize: 9, fontWeight: 700, padding: "1px 6px", borderRadius: 3, background: "rgba(37,99,235,.25)", color: "var(--orange)" }}>V1</span>}
              </div>
            )}

            <div style={{ position: "relative", height: viewerHeight, display: "flex", alignItems: "center", justifyContent: "center", background: "#0a0a0a" }}>
              {/* V1: outgoing (fades out during transition) */}
              <video ref={videoRefA} playsInline crossOrigin="anonymous" style={{ position: "absolute", inset: 0, width: "100%", height: "100%", objectFit: "contain", display: previewUrl(effectiveClipForV1) ? "block" : "none", opacity: opacityV1, filter: cgVideoFilter }} />
              {/* V2: ALWAYS display:block so the GPU compositor pipeline never gets suspended.
                   Opacity 0 = invisible but decoder stays warm → no flash when transition starts.
                   For wipe/slide transitions, clipPath/transform handle reveal while opacity stays 1. */}
              <video ref={videoRefB} playsInline crossOrigin="anonymous" style={{
                position: "absolute", inset: 0, width: "100%", height: "100%", objectFit: "contain", display: "block", zIndex: 2,
                filter: opacityV2 > 0.01 ? cgVideoFilter : undefined,
                opacity: transitionInfo && ["wipeleft","wiperight","slideleft","slideright"].includes(transitionInfo.type) ? 1 : opacityV2,
                clipPath: (() => {
                  if (!transitionInfo) return undefined;
                  const p = transitionInfo.progress;
                  if (transitionInfo.type === "wipeleft")  return `inset(0 ${((1-p)*100).toFixed(2)}% 0 0)`;
                  if (transitionInfo.type === "wiperight") return `inset(0 0 0 ${((1-p)*100).toFixed(2)}%)`;
                  return undefined;
                })(),
                transform: (() => {
                  if (!transitionInfo) return undefined;
                  const p = transitionInfo.progress;
                  if (transitionInfo.type === "slideleft")  return `translateX(${((1-p)*100).toFixed(2)}%)`;
                  if (transitionInfo.type === "slideright") return `translateX(-${((1-p)*100).toFixed(2)}%)`;
                  return undefined;
                })(),
              }} />
              {/* Empty state */}
              {!previewUrl(clipForV1) && !previewUrl(clipForV2) && (
                <div style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 6, color: "var(--text3)", zIndex: 1 }}>
                  <svg width={36} height={36} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.2} style={{ opacity: 0.3 }}><rect x={2} y={2} width={20} height={20} rx={2.18}/><line x1={7} y1={2} x2={7} y2={22}/><line x1={17} y1={2} x2={17} y2={22}/><line x1={2} y1={12} x2={22} y2={12}/><line x1={2} y1={7} x2={7} y2={7}/><line x1={2} y1={17} x2={7} y2={17}/><line x1={17} y1={17} x2={22} y2={17}/><line x1={17} y1={7} x2={22} y2={7}/></svg>
                  <span style={{ fontSize: 10, opacity: 0.4 }}>Kein Clip am Playhead</span>
                </div>
              )}
              {/* Clip info overlay */}
              {(clipForV2 || clipForV1) && (() => {
                const activeClip = activeTLClipV2 || activeTLClipV1;
                const c = activeClip ? TC_COLORS[activeClip.color] : TC_COLORS.orange;
                return (
                <span style={{ position: "absolute", bottom: 6, left: 8, fontSize: 9, fontWeight: 700, padding: "2px 6px", borderRadius: 3, zIndex: 10, background: c.bg, color: c.text }}>
                  {clipForV2 ? `V2 · ${clipForV2.dateiname}` : `V1 · ${clipForV1!.dateiname}`}
                </span>
                );
              })()}
            </div>
          </div>

          {/* ─── Resize handle: viewer ↔ timeline ─── */}
          <ResizeHandle
            direction="vertical"
            onResize={d => setViewerHeight(h => Math.max(180, Math.min(560, h + d)))}
          />

          {/* Preview Bar */}
          <div style={{ display: "flex", alignItems: "center", gap: 8, padding: "5px 12px", background: "var(--bg1)", borderBottom: "1px solid var(--border)", flexShrink: 0 }}>
            <PbBtn icon={<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2}><polygon points="19 20 9 12 19 4 19 20"/><line x1={5} y1={19} x2={5} y2={5}/></svg>} onClick={() => setPct(0)} />
            <PbBtn icon={<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2}><polygon points="19 20 9 12 19 4 19 20"/></svg>} onClick={() => setPct(p => Math.max(0, p - 1/totalSec))} />
            <button onClick={() => setPlaying(!playing)} style={{ width: 28, height: 28, background: "var(--bg4)", border: "1px solid var(--border2)", borderRadius: "50%", display: "flex", alignItems: "center", justifyContent: "center", cursor: "pointer" }}>
              {playing
                ? <svg width={10} height={10} viewBox="0 0 24 24" fill="var(--text)" stroke="none"><rect x={6} y={4} width={4} height={16}/><rect x={14} y={4} width={4} height={16}/></svg>
                : <svg width={10} height={10} viewBox="0 0 24 24" fill="var(--text)" stroke="none"><polygon points="6 3 20 12 6 21 6 3"/></svg>}
            </button>
            <PbBtn icon={<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2}><polygon points="5 4 15 12 5 20 5 4"/><line x1={19} y1={5} x2={19} y2={19}/></svg>} onClick={() => setPct(1)} />
            <div style={{ flex: 1, position: "relative", cursor: "pointer" }} onClick={handleSeek}>
              <div style={{ height: 3, background: "var(--bg4)", borderRadius: 2, position: "relative" }}>
                <div style={{ height: "100%", width: `${pct * 100}%`, background: "var(--orange)", borderRadius: 2 }} />
                <div style={{ width: 11, height: 11, borderRadius: "50%", background: "white", position: "absolute", top: "50%", transform: "translate(-50%,-50%)", left: `${pct * 100}%`, border: "2px solid var(--orange)", cursor: "grab" }} />
              </div>
            </div>
            <span style={{ fontFamily: "var(--mono)", fontSize: 10, color: "var(--text3)" }}>{fmtDauer(curSec)} / {fmtDauer(gesamtdauer)}</span>
            <div style={{ display: "flex", alignItems: "center", gap: 4, marginLeft: 4 }}>
              <ZoomBtn label="−" onClick={() => setZoomLevel(z => Math.max(50, z - 25))} />
              <span style={{ fontFamily: "var(--mono)", fontSize: 9, color: "var(--text3)", minWidth: 28, textAlign: "center" }}>{zoomLevel}%</span>
              <ZoomBtn label="+" onClick={() => setZoomLevel(z => Math.min(400, z + 25))} />
            </div>
          </div>

          {/* AI Banner */}
          {bannerVisible && (
            <div style={{ display: "flex", alignItems: "center", gap: 8, padding: "5px 12px", background: "rgba(34,197,94,.08)", borderBottom: "1px solid rgba(34,197,94,.2)", flexShrink: 0 }}>
              <div style={{ width: 6, height: 6, borderRadius: "50%", background: "var(--green)", animation: "pulse 2s infinite", flexShrink: 0 }} />
              <span style={{ fontSize: 11, color: "#86efac", flex: 1 }}>{bannerText}</span>
              {pendingTLClips && <>
                <button onClick={() => { setPendingTLClips(null); setBannerVisible(false); }} style={{ padding: "4px 10px", background: "transparent", border: "1px solid rgba(34,197,94,.4)", borderRadius: 5, fontSize: 10, fontWeight: 700, color: "var(--green)", cursor: "pointer", fontFamily: "var(--font)" }}>Ablehnen</button>
                <button onClick={handleApplyAi} style={{ padding: "4px 10px", background: "var(--green)", border: "none", borderRadius: 5, fontSize: 10, fontWeight: 700, color: "white", cursor: "pointer", fontFamily: "var(--font)" }}>Anwenden</button>
              </>}
            </div>
          )}

          {/* Active Jobs */}
          {activeJobs.length > 0 && (
            <div style={{ padding: "4px 12px", background: "var(--bg1)", borderBottom: "1px solid var(--border)", flexShrink: 0 }}>
              {activeJobs.map(job => (
                <div key={job.jobId} style={{ display: "flex", alignItems: "center", gap: 8, padding: "2px 0" }}>
                  <div style={{ flex: 1 }}>
                    <div style={{ height: 3, background: "var(--bg4)", borderRadius: 2 }}>
                      <div style={{ height: "100%", width: `${job.fortschritt}%`, background: "var(--orange)", borderRadius: 2, transition: "width .3s" }} />
                    </div>
                  </div>
                  <span style={{ fontSize: 9, color: "var(--text3)", minWidth: 30, textAlign: "right" }}>{job.fortschritt}%</span>
                  <span style={{ fontSize: 9, color: "var(--text3)", maxWidth: 200, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{job.nachricht}</span>
                </div>
              ))}
            </div>
          )}

          {/* ═══ TIMELINE TOOLBAR (DaVinci-Style) ═══ */}
          <div style={{ display: "flex", alignItems: "center", gap: 0, padding: "0 8px", background: "var(--bg2)", borderBottom: "1px solid var(--border)", flexShrink: 0, height: 32 }}>
            {/* Selection tools */}
            <TlTool active={tool === "select"} onClick={() => setTool("select")} tooltip="Auswahl (A)">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2}><path d="m3 3 7.07 16.97 2.51-7.39 7.39-2.51L3 3z"/></svg>
            </TlTool>
            <TlTool active={tool === "blade"} onClick={() => setTool("blade")} tooltip="Schneiden (B)">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2}><circle cx={6} cy={6} r={3}/><circle cx={6} cy={18} r={3}/><line x1={20} y1={4} x2={8.12} y2={15.88}/><line x1={14.47} y1={14.48} x2={20} y2={20}/><line x1={8.12} y1={8.12} x2={12} y2={12}/></svg>
            </TlTool>
            <TlTool active={tool === "slip"} onClick={() => setTool("slip")} tooltip="Ripple-Edit (T)">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2}><path d="M5 12h14"/><polyline points="12 5 19 12 12 19"/></svg>
            </TlTool>

            <TlSep />

            {/* Clipboard */}
            <TlTool tooltip="Ausschneiden (Ctrl+X)" onClick={handleCut}>
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2}><line x1={5} y1={12} x2={19} y2={12}/><polyline points="12 5 19 12 12 19"/></svg>
            </TlTool>
            <TlTool tooltip="Kopieren (Ctrl+C)" onClick={handleCopy}>
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2}><rect x={9} y={9} width={13} height={13} rx={2}/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>
            </TlTool>
            <TlTool tooltip="Einfügen (Ctrl+V)" onClick={handlePaste}>
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2}><path d="M16 4h2a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h2"/><rect x={8} y={2} width={8} height={4} rx={1}/></svg>
            </TlTool>

            <TlSep />

            {/* Snapping / Link */}
            <TlTool active={snapping} onClick={() => setSnapping(!snapping)} tooltip="Einrasten (N)">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2}><path d="M21 2l-2 2m-7.61 7.61a5.5 5.5 0 1 1-7.778 7.778 5.5 5.5 0 0 1 7.777-7.777zm0 0L15.5 7.5m0 0l3 3L22 7l-3-3m-3.5 3.5L19 4"/></svg>
            </TlTool>
            <TlTool active={linkedAV} onClick={() => setLinkedAV(!linkedAV)} tooltip="Audio/Video verknüpfen">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2}><path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/></svg>
            </TlTool>

            <TlSep />

            {/* Markers */}
            <TlTool tooltip="Marker setzen (M)">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2}><path d="M19 21l-7-5-7 5V5a2 2 0 0 1 2-2h10a2 2 0 0 1 2 2z"/></svg>
            </TlTool>
            <TlTool tooltip="In-Punkt (I)">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2}><path d="M7 4v16"/><path d="M7 12h10l-4-4"/><path d="M13 16l4-4"/></svg>
            </TlTool>
            <TlTool tooltip="Out-Punkt (O)">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2}><path d="M17 4v16"/><path d="M17 12H7l4-4"/><path d="M11 16l-4-4"/></svg>
            </TlTool>

            <TlSep />

            {/* Zoom */}
            <TlTool onClick={() => setZoomLevel(z => Math.max(50, z - 25))} tooltip="Herauszoomen">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2}><circle cx={11} cy={11} r={8}/><line x1={21} y1={21} x2={16.65} y2={16.65}/><line x1={8} y1={11} x2={14} y2={11}/></svg>
            </TlTool>
            <TlTool onClick={() => setZoomLevel(z => Math.min(400, z + 25))} tooltip="Hineinzoomen">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2}><circle cx={11} cy={11} r={8}/><line x1={21} y1={21} x2={16.65} y2={16.65}/><line x1={11} y1={8} x2={11} y2={14}/><line x1={8} y1={11} x2={14} y2={11}/></svg>
            </TlTool>
            <TlTool onClick={() => setZoomLevel(100)} tooltip="Zoom zurücksetzen">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2}><circle cx={11} cy={11} r={8}/><line x1={21} y1={21} x2={16.65} y2={16.65}/><path d="M11 8v6M8 11h6"/></svg>
            </TlTool>

            <div style={{ flex: 1 }} />

            {/* Zoom slider */}
            <div style={{ display: "flex", alignItems: "center", gap: 4 }}>
              <span style={{ fontFamily: "var(--mono)", fontSize: 9, color: "var(--text3)" }}>−</span>
              <input type="range" min={50} max={400} step={25} value={zoomLevel} onChange={e => setZoomLevel(parseInt(e.target.value))} style={{ width: 80, height: 3, accentColor: "var(--orange)", cursor: "pointer" }} />
              <span style={{ fontFamily: "var(--mono)", fontSize: 9, color: "var(--text3)" }}>+</span>
            </div>
          </div>

          {/* ─── TIMELINE ─── */}
          <div style={{ flex: 1, display: "flex", overflow: "hidden", position: "relative" }}>
            <div style={{ width: 80, flexShrink: 0, background: "var(--bg2)", borderRight: "1px solid var(--border)", display: "flex", flexDirection: "column" }}>
              <div style={{ height: 28, borderBottom: "1px solid var(--border)", flexShrink: 0 }} />
              {tracks.map((trk) => {
                const trH = trk.height ?? 52;
                return (
                <div key={trk.id} style={{ position: "relative", flexShrink: 0 }}>
                  <div style={{ height: trH, display: "flex", alignItems: "center", gap: 4, paddingLeft: 4, paddingRight: 4, fontSize: 9, fontWeight: 700, color: trk.muted ? "var(--text3)" : trk.type === "video" ? "var(--orange)" : "var(--green)", borderBottom: "1px solid var(--border)", textTransform: "uppercase", letterSpacing: ".06em", opacity: trk.muted ? 0.5 : 1 }}>
                    <span style={{ flex: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{trk.name}</span>
                    <button onClick={() => updateTrack(trk.id, { muted: !trk.muted })} title={trk.muted ? "Stummschaltung aufheben" : "Stummschalten"} style={{ background: "none", border: "none", cursor: "pointer", padding: 0, display: "flex", alignItems: "center", color: trk.muted ? "var(--red)" : "var(--text3)", fontSize: 10 }}>
                      {trk.muted ? "M" : <svg width={10} height={10} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2}><polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5"/><path d="M19.07 4.93a10 10 0 0 1 0 14.14M15.54 8.46a5 5 0 0 1 0 7.07"/></svg>}
                    </button>
                    <button onClick={() => updateTrack(trk.id, { locked: !trk.locked })} title={trk.locked ? "Entsperren" : "Sperren"} style={{ background: "none", border: "none", cursor: "pointer", padding: 0, display: "flex", alignItems: "center", color: trk.locked ? "var(--orange)" : "var(--text3)", fontSize: 10 }}>
                      {trk.locked ? <svg width={10} height={10} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2}><rect x={3} y={11} width={18} height={11} rx={2}/><path d="M7 11V7a5 5 0 0 1 10 0v4"/></svg> : <svg width={10} height={10} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2}><rect x={3} y={11} width={18} height={11} rx={2}/><path d="M7 11V7a5 5 0 0 1 9.9-1"/></svg>}
                    </button>
                  </div>
                  {/* Drag handle on EVERY track (DaVinci/Premiere-style per-track resize) */}
                  <div
                    title={`${trk.name} Höhe ändern`}
                    style={{ position: "absolute", bottom: -3, left: 0, right: 0, height: 6, cursor: "ns-resize", zIndex: 5 }}
                    onMouseDown={e => { e.preventDefault(); trackResizeRef.current = { startY: e.clientY, startH: trH, trackId: trk.id }; document.body.style.cursor = "ns-resize"; }}
                    onMouseEnter={e => (e.currentTarget.style.background = "rgba(249,115,22,.4)")}
                    onMouseLeave={e => (e.currentTarget.style.background = "transparent")}
                  />
                </div>
                );
              })}
              {/* Add track buttons */}
              <div style={{ display: "flex", flexDirection: "column", gap: 2, padding: "4px 3px" }}>
                <button onClick={() => addTrack("video")} style={{ background: "none", border: "1px solid var(--border)", borderRadius: 4, color: "var(--text3)", fontSize: 8, fontWeight: 700, cursor: "pointer", padding: "2px 0", fontFamily: "var(--font)", letterSpacing: ".04em" }}>+ V</button>
                <button onClick={() => addTrack("audio")} style={{ background: "none", border: "1px solid var(--border)", borderRadius: 4, color: "var(--text3)", fontSize: 8, fontWeight: 700, cursor: "pointer", padding: "2px 0", fontFamily: "var(--font)", letterSpacing: ".04em" }}>+ A</button>
              </div>
            </div>
            <div ref={scrollContainerRef} style={{ flex: 1, overflow: "auto", position: "relative", cursor: getToolCursor(tool) }}>
              <div style={{ minWidth: tlWidth, position: "relative" }}>
                {/* Ruler */}
                <div style={{ height: 28, background: "var(--bg2)", borderBottom: "1px solid var(--border)", position: "sticky", top: 0, zIndex: 20, cursor: "pointer" }} onMouseDown={e => { const container = scrollContainerRef.current; if (!container) return; const rect = container.getBoundingClientRect(); const absX = (e.clientX - rect.left) + container.scrollLeft; const newSec = Math.max(0, Math.min(totalSec, absX / (PX_PER_SEC * scale))); setPct(totalSec > 0 ? newSec / totalSec : 0); setPlaying(false); playheadDragRef.current = true; }}>
                  {rulerTicks.map((tick, i) => (
                    <div key={i} style={{ position: "absolute", bottom: 0, left: tick.x, display: "flex", flexDirection: "column", alignItems: "center" }}>
                      <div style={{ width: 1, height: tick.major ? 10 : 6, background: tick.major ? "var(--border3)" : "var(--border2)" }} />
                      {tick.label && <div style={{ position: "absolute", bottom: 12, transform: "translateX(-50%)", fontFamily: "var(--mono)", fontSize: 8, color: "var(--text3)", whiteSpace: "nowrap" }}>{tick.label}</div>}
                    </div>
                  ))}
                </div>

                {/* Tracks */}
                {tracks.map((trk, ti) => {
                  const trackClips = tlClips.filter(c => c.track === trk.id);
                  const isVideo = trk.type === "video";
                  const trH = trk.height ?? 52;
                  return (
                  <div key={trk.id} style={{ height: trH, borderBottom: "1px solid var(--border)", position: "relative", background: ti % 2 === 0 ? "var(--bg1)" : "var(--bg0)", opacity: trk.muted ? 0.4 : 1 }}
                    onDragOver={e => { if (!trk.locked) e.preventDefault(); }}
                    onDrop={e => {
                      e.preventDefault();
                      if (trk.locked) return;
                      const clipId = e.dataTransfer.getData("text/clip-id");
                      const clip = clips.find(c => c.id === clipId);
                      if (!clip) return;
                      const rect = e.currentTarget.getBoundingClientRect();
                      const startSec = Math.max(0, (e.clientX - rect.left) / (PX_PER_SEC * scale));
                      const ts = Date.now();
                      const firstVideoTrack = tracks.find(t => t.type === "video");
                      const firstAudioTrack = tracks.find(t => t.type === "audio");
                      // Ein Video-Clip wird IMMER als A/V-Paar platziert: Bild
                      // auf die Videospur, Ton auf die Audiospur — egal auf
                      // welche Spur fallen gelassen wurde. Verhindert, dass ein
                      // Video versehentlich nur auf der Audiospur landet
                      // (Segment ohne Bild, kein Playback im Vorschaufenster).
                      if (linkedAV && firstVideoTrack && firstAudioTrack) {
                        const gId = `grp-${clipId}-${ts}`;
                        addTLClip({ id: `tl-${clipId}-${ts}`, clipId, label: `${clip.dateiname} · ${clip.quelle}`, track: firstVideoTrack.id, start: startSec, dauer: clip.dauer || 10, mediaStart: 0, color: videoColorForClip(clipId, clips), groupId: gId });
                        addTLClip({ id: `tl-${clipId}-a1-${ts}`, clipId, label: `♪ ${clip.dateiname}`, track: firstAudioTrack.id, start: startSec, dauer: clip.dauer || 10, mediaStart: 0, color: "green", groupId: gId });
                      } else {
                        addTLClip({ id: `tl-${clipId}-${ts}`, clipId, label: `${clip.dateiname} · ${clip.quelle}`, track: trk.id, start: startSec, dauer: clip.dauer || 10, mediaStart: 0, color: isVideo ? videoColorForClip(clipId, clips) : "green", groupId: undefined });
                      }
                    }}
                  >
                    {/* ── Transition indicators (diamond between clips) ── */}
                    {isVideo && trackClips.map(clip => {
                      if (!clip.transition || clip.transition.dauer <= 0) return null;
                      const x = clip.start * PX_PER_SEC * scale;
                      return (
                        <div key={`tr-${clip.id}`}
                          title={`${clip.transition.type} · ${clip.transition.dauer}s — klicken zum Ändern`}
                          onClick={e => { e.stopPropagation(); setTransitionPicker({ clipId: clip.id, x: e.clientX, y: e.clientY }); }}
                          style={{ position: "absolute", left: x - 9, top: "50%", transform: "translateY(-50%) rotate(45deg)", width: 14, height: 14, background: "rgba(168,85,247,0.9)", border: "2px solid rgba(255,255,255,0.8)", borderRadius: 2, cursor: "pointer", zIndex: 15, boxShadow: "0 0 6px rgba(168,85,247,0.7)" }}
                        />
                      );
                    })}

                    {trackClips.map(clip => {
                      const c = TC_COLORS[clip.color];
                      const isSelected = selectedClip === clip.id;
                      // Audio: waveform overlay. Video: thumbnail strip overlay.
                      // Beide werden positioniert, sodass nur das Fenster
                      // [mediaStart, mediaStart + dauer] des Quellclips sichtbar ist.
                      const srcClip = clip.clipId
                        ? clips.find(cl => cl.id === clip.clipId)
                        : null;
                      const overlayUrl = srcClip
                        ? (isVideo ? srcClip.strip_url : srcClip.waveform_url)
                        : null;
                      const overlayBg: React.CSSProperties = overlayUrl && srcClip?.dauer
                        ? (() => {
                            const fullWidth = (srcClip.dauer * PX_PER_SEC * scale);
                            const offset = -(clip.mediaStart * PX_PER_SEC * scale);
                            return {
                              backgroundImage: `url(http://localhost:8001${overlayUrl})`,
                              backgroundSize: `${fullWidth}px 100%`,
                              backgroundPosition: `${offset}px center`,
                              backgroundRepeat: "no-repeat",
                            };
                          })()
                        : {};
                      return (
                        <div key={clip.id} style={{ position: "absolute", top: 5, height: trH - 10, left: clip.start * PX_PER_SEC * scale, width: clip.dauer * PX_PER_SEC * scale, borderRadius: 5, display: "flex", alignItems: "center", overflow: "hidden", cursor: getClipCursor(tool, trk.locked), background: c.bg, border: `1px solid ${c.border}`, color: c.text, outline: isSelected ? "2px solid white" : "none", outlineOffset: isSelected ? 1 : 0, zIndex: isSelected ? 5 : 1, ...overlayBg }}
                          onClick={e => {
                            e.stopPropagation();
                            if (trk.locked) return;
                            if (tool === "blade") { handleBladeCut(clip, e); }
                            else { setSelectedClip(clip.id); }
                          }}
                          onContextMenu={e => { e.preventDefault(); e.stopPropagation(); setCtxMenu({ x: e.clientX, y: e.clientY, clipId: clip.id }); setSelectedClip(clip.id); }}
                          onMouseDown={e => {
                            if (trk.locked) return;
                            if ((e.target as HTMLElement).dataset.handle) return;
                            if (tool === "blade") return; // blade uses onClick only
                            if (tool === "slip") {
                              // Ripple edit: store neighbors on same track
                              const neighbors = trackClips.filter(tc => tc.id !== clip.id).map(tc => ({ id: tc.id, start: tc.start, dauer: tc.dauer }));
                              slipRef.current = { clipId: clip.id, startX: e.clientX, origStart: clip.start, origDauer: clip.dauer, trackId: trk.id, neighbors };
                            } else {
                              dragRef.current = { clipId: clip.id, startX: e.clientX, startVal: clip.start };
                            }
                          }}
                        >
                          <div data-handle="1" style={{ position: "absolute", top: 0, bottom: 0, left: 0, width: 6, background: "rgba(255,255,255,.2)", cursor: trk.locked ? "not-allowed" : "col-resize", borderRadius: "5px 0 0 5px", zIndex: 2 }} onMouseDown={e => { if (trk.locked) return; e.stopPropagation(); resizeRef.current = { clipId: clip.id, startX: e.clientX, startDauer: clip.dauer, startStart: clip.start, isLeft: true }; }} />
                          {/* Label als kompakter Chip oben links — funktioniert
                              für Video (Thumbnail-Strip) UND Audio (Waveform),
                              ohne den Overlay-Inhalt zu verdecken. */}
                          <span style={{
                            position: "absolute",
                            top: 3,
                            left: 10,
                            zIndex: 3,
                            padding: "1px 6px",
                            fontSize: 9,
                            fontWeight: 600,
                            background: "rgba(0,0,0,0.6)",
                            color: "#fff",
                            borderRadius: 3,
                            pointerEvents: "none",
                            maxWidth: "calc(100% - 30px)",
                            whiteSpace: "nowrap",
                            overflow: "hidden",
                            textOverflow: "ellipsis",
                          }}>{clip.label}</span>
                          <div data-handle="1" style={{ position: "absolute", top: 0, bottom: 0, right: 0, width: 6, background: "rgba(255,255,255,.2)", cursor: trk.locked ? "not-allowed" : "col-resize", borderRadius: "0 5px 5px 0", zIndex: 2 }} onMouseDown={e => { if (trk.locked) return; e.stopPropagation(); resizeRef.current = { clipId: clip.id, startX: e.clientX, startDauer: clip.dauer, startStart: clip.start, isLeft: false }; }} />
                        </div>
                      );
                    })}
                  </div>
                  );
                })}

                {/* Playhead */}
                <div style={{ position: "absolute", top: 0, left: `${curSec * PX_PER_SEC * scale}px`, width: 1.5, height: 28 + tracks.reduce((s, t) => s + (t.height ?? 52), 0), background: "white", pointerEvents: "none", zIndex: 30 }}>
                  <div
                    style={{ position: "absolute", top: 0, left: "50%", transform: "translateX(-50%)", width: 14, height: 14, background: "white", clipPath: "polygon(0 0, 100% 0, 50% 100%)", cursor: "ew-resize", pointerEvents: "auto" }}
                    onMouseDown={e => { e.preventDefault(); e.stopPropagation(); playheadDragRef.current = true; setPlaying(false); }}
                  />
                </div>
              </div>
            </div>
          </div>
        </div>

        {/* ─── FARBKORREKTUR PANEL ─── */}
        {pageTab === "farbe" && (
          <div style={{ background: "var(--bg2)", borderLeft: "1px solid var(--border)", display: "flex", flexDirection: "column", overflow: "hidden" }}>
            {/* Header */}
            <div style={{ display: "flex", alignItems: "center", padding: "7px 10px", borderBottom: "1px solid var(--border)", flexShrink: 0 }}>
              <svg width={11} height={11} viewBox="0 0 24 24" fill="none" stroke="var(--orange)" strokeWidth={2}><circle cx={12} cy={12} r={5}/><path d="M12 1v2M12 21v2M4.22 4.22l1.42 1.42M18.36 18.36l1.42 1.42M1 12h2M21 12h2M4.22 19.78l1.42-1.42M18.36 5.64l1.42-1.42"/></svg>
              <span style={{ fontSize: 10, fontWeight: 700, color: "var(--text)", marginLeft: 5, flex: 1 }}>Farbkorrektur</span>
              <button onClick={() => setCG({ ...NEUTRAL_CG })} style={{ background: "none", border: "1px solid var(--border)", borderRadius: 4, fontSize: 9, color: "var(--text3)", padding: "2px 7px", cursor: "pointer", fontFamily: "var(--font)" }}>↺ Reset</button>
            </div>

            <div style={{ flex: 1, overflowY: "auto", padding: "8px 10px" }}>

              {/* Color wheels: Lift / Gamma / Gain */}
              <div style={{ display: "flex", justifyContent: "space-around", marginBottom: 10 }}>
                {(["lift", "gamma", "gain"] as const).map(wheel => {
                  const x = wheel === "lift" ? cg.liftX : wheel === "gamma" ? cg.gammaX : cg.gainX;
                  const y = wheel === "lift" ? cg.liftY : wheel === "gamma" ? cg.gammaY : cg.gainY;
                  const ref = wheel === "lift" ? liftWheelRef : wheel === "gamma" ? gammaWheelRef : gainWheelRef;
                  const label = wheel === "lift" ? "Lift" : wheel === "gamma" ? "Gamma" : "Gain";
                  const R = 34;
                  const dotX = R + x * (R - 5);
                  const dotY = R + y * (R - 5);
                  return (
                    <div key={wheel} style={{ textAlign: "center" }}>
                      <div
                        ref={ref}
                        style={{ width: R*2, height: R*2, borderRadius: "50%", position: "relative", cursor: "crosshair",
                          background: `radial-gradient(circle, rgba(255,255,255,0.92) 0%, rgba(255,255,255,0) 52%), conic-gradient(from 270deg, hsl(0,90%,50%),hsl(60,90%,50%),hsl(120,90%,50%),hsl(180,90%,50%),hsl(240,90%,50%),hsl(300,90%,50%),hsl(360,90%,50%))`,
                          boxShadow: "inset 0 0 0 1px rgba(255,255,255,.12), 0 2px 8px rgba(0,0,0,.4)",
                        }}
                        onMouseDown={e => {
                          e.preventDefault();
                          wheelDragRef.current = { wheel, el: e.currentTarget as HTMLDivElement };
                          const rect = e.currentTarget.getBoundingClientRect();
                          let nx = ((e.clientX - rect.left) - R) / (R - 5);
                          let ny = ((e.clientY - rect.top) - R) / (R - 5);
                          const mag = Math.sqrt(nx*nx + ny*ny);
                          if (mag > 1) { nx /= mag; ny /= mag; }
                          const wx = parseFloat(nx.toFixed(3)), wy = parseFloat(ny.toFixed(3));
                          if (wheel === "lift") setCG(p => ({ ...p, liftX: wx, liftY: wy }));
                          else if (wheel === "gamma") setCG(p => ({ ...p, gammaX: wx, gammaY: wy }));
                          else setCG(p => ({ ...p, gainX: wx, gainY: wy }));
                        }}
                        onDoubleClick={() => {
                          if (wheel === "lift") setCG(p => ({ ...p, liftX: 0, liftY: 0 }));
                          else if (wheel === "gamma") setCG(p => ({ ...p, gammaX: 0, gammaY: 0 }));
                          else setCG(p => ({ ...p, gainX: 0, gainY: 0 }));
                        }}
                      >
                        <div style={{ position: "absolute", left: R-0.5, top: 3, width: 1, height: R*2-6, background: "rgba(0,0,0,.18)", pointerEvents: "none" }} />
                        <div style={{ position: "absolute", top: R-0.5, left: 3, height: 1, width: R*2-6, background: "rgba(0,0,0,.18)", pointerEvents: "none" }} />
                        <div style={{ position: "absolute", width: 7, height: 7, borderRadius: "50%", background: "white", border: "2px solid rgba(0,0,0,.7)", left: dotX-3.5, top: dotY-3.5, pointerEvents: "none", boxShadow: "0 0 4px rgba(0,0,0,.6)" }} />
                      </div>
                      <div style={{ fontSize: 8, color: "var(--text3)", marginTop: 2, fontWeight: 700, textTransform: "uppercase", letterSpacing: ".06em" }}>{label}</div>
                      {(Math.abs(x) > 0.01 || Math.abs(y) > 0.01) && <div style={{ fontFamily: "var(--mono)", fontSize: 7, color: "var(--orange)" }}>{x > 0 ? `+${x.toFixed(2)}` : x.toFixed(2)}</div>}
                    </div>
                  );
                })}
              </div>

              {/* Tone sliders */}
              <div style={{ fontSize: 8, fontWeight: 700, color: "var(--text3)", textTransform: "uppercase", letterSpacing: ".07em", paddingBottom: 4, marginBottom: 5, borderBottom: "1px solid var(--border)" }}>Belichtung</div>
              {[
                { k: "exposure",  label: "Belichtung", color: "#fb923c" },
                { k: "contrast",  label: "Kontrast",   color: "#fb923c" },
                { k: "highlights",label: "Lichter",    color: "#fcd34d" },
                { k: "shadows",   label: "Schatten",   color: "#94a3b8" },
              ].map(({ k, label, color }) => {
                const val: number = (cg as Record<string, number>)[k];
                return (
                  <div key={k} style={{ display: "flex", alignItems: "center", gap: 5, marginBottom: 4 }}>
                    <span style={{ fontSize: 9, color: "var(--text3)", width: 56, flexShrink: 0 }}>{label}</span>
                    <input type="range" min={-1} max={1} step={0.01} value={val}
                      onChange={e => setCG(p => ({ ...p, [k]: parseFloat(e.target.value) }))}
                      style={{ flex: 1, height: 3, accentColor: color, cursor: "pointer" }}
                    />
                    <span
                      style={{ fontFamily: "var(--mono)", fontSize: 8, color: val !== 0 ? color : "var(--text3)", minWidth: 30, textAlign: "right", cursor: "pointer" }}
                      onDoubleClick={() => setCG(p => ({ ...p, [k]: 0 }))}
                    >{val > 0 ? `+${val.toFixed(2)}` : val.toFixed(2)}</span>
                  </div>
                );
              })}

              {/* Color sliders */}
              <div style={{ fontSize: 8, fontWeight: 700, color: "var(--text3)", textTransform: "uppercase", letterSpacing: ".07em", paddingBottom: 4, marginBottom: 5, borderBottom: "1px solid var(--border)", marginTop: 8 }}>Farbe</div>
              {[
                { k: "saturation",  label: "Sättigung",  min: -1,   max: 1,   step: 0.01, color: "#a78bfa" },
                { k: "temperature", label: "Temperatur", min: -1,   max: 1,   step: 0.01, color: "#fb923c" },
                { k: "tint",        label: "Farbton",    min: -1,   max: 1,   step: 0.01, color: "#f472b6" },
                { k: "hue",         label: "Hue-Shift",  min: -180, max: 180, step: 1,    color: "#34d399" },
              ].map(({ k, label, min, max, step, color }) => {
                const val: number = (cg as Record<string, number>)[k];
                return (
                  <div key={k} style={{ display: "flex", alignItems: "center", gap: 5, marginBottom: 4 }}>
                    <span style={{ fontSize: 9, color: "var(--text3)", width: 56, flexShrink: 0 }}>{label}</span>
                    <input type="range" min={min} max={max} step={step} value={val}
                      onChange={e => setCG(p => ({ ...p, [k]: parseFloat(e.target.value) }))}
                      style={{ flex: 1, height: 3, accentColor: color, cursor: "pointer" }}
                    />
                    <span
                      style={{ fontFamily: "var(--mono)", fontSize: 8, color: val !== 0 ? color : "var(--text3)", minWidth: 30, textAlign: "right", cursor: "pointer" }}
                      onDoubleClick={() => setCG(p => ({ ...p, [k]: 0 }))}
                    >{val > 0 ? `+${val.toFixed(2)}` : val.toFixed(2)}</span>
                  </div>
                );
              })}

              {/* Histogram scope */}
              <div style={{ fontSize: 8, fontWeight: 700, color: "var(--text3)", textTransform: "uppercase", letterSpacing: ".07em", paddingBottom: 4, marginBottom: 5, borderBottom: "1px solid var(--border)", marginTop: 8 }}>Scope</div>
              <canvas ref={scopeRef} width={230} height={64}
                style={{ width: "100%", height: 64, borderRadius: 3, border: "1px solid var(--border)", background: "#080a0a", display: "block" }}
              />
              <div style={{ display: "flex", justifyContent: "space-between", marginTop: 3 }}>
                <span style={{ fontSize: 7, color: "#ef4444", fontWeight: 700 }}>R</span>
                <span style={{ fontSize: 7, color: "#22c55e", fontWeight: 700 }}>G</span>
                <span style={{ fontSize: 7, color: "#3b82f6", fontWeight: 700 }}>B</span>
                <span style={{ fontSize: 7, color: "var(--text3)" }}>Histogram</span>
              </div>

            </div>
          </div>
        )}
      </div>

      {/* ═══ BOTTOM BAR ═══ */}
      <div style={{ background: "var(--bg1)", borderTop: "1px solid var(--border)", display: "flex", alignItems: "center", padding: "0 12px", gap: 6 }}>
        {([{ id: "schnitt" as PageTab, label: "Schnitt", icon: <svg width={12} height={12} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2}><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/></svg> }, { id: "farbe" as PageTab, label: "Farbkorrektur", icon: <svg width={12} height={12} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2}><circle cx={12} cy={12} r={5}/><path d="M12 1v2M12 21v2M4.22 4.22l1.42 1.42M18.36 18.36l1.42 1.42"/></svg> }, { id: "effekte" as PageTab, label: "Effekte", icon: <svg width={12} height={12} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2}><polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2"/></svg> }]).map(p => (
          <button key={p.id} onClick={() => setPageTab(p.id)} style={{ display: "flex", alignItems: "center", gap: 5, padding: "0 10px", cursor: "pointer", fontSize: 10, fontWeight: 700, textTransform: "uppercase", letterSpacing: ".06em", color: pageTab === p.id ? "var(--orange)" : "var(--text3)", borderBottom: `2px solid ${pageTab === p.id ? "var(--orange)" : "transparent"}`, height: "100%", background: "none", border: "none", borderTop: "none", borderLeft: "none", borderRight: "none", fontFamily: "var(--font)" }}>{p.icon}{p.label}</button>
        ))}
        <div style={{ marginLeft: "auto", display: "flex", alignItems: "center", gap: 8 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 4, fontSize: 10, color: backendOnline ? "var(--green)" : "var(--red)" }}>
            <div style={{ width: 5, height: 5, borderRadius: "50%", background: backendOnline ? "var(--green)" : "var(--red)", animation: backendOnline ? "pulse 2s infinite" : "none" }} />
            {backendOnline ? "Backend verbunden" : "Backend offline"}
          </div>
          <span style={{ fontFamily: "var(--mono)", fontSize: 9, color: "var(--text3)" }}>{trackCount} Spuren · {fmtDauer(gesamtdauer)} · {(() => {
            // Bildrate des ersten Clips auf der Timeline (sonst erster
            // hochgeladener Clip, sonst "—"). Die Anzeige spiegelt die
            // Source-Bildrate wider, nicht eine fixe Editor-Bildrate.
            for (const tlc of tlClips) {
              if (!tlc.clipId) continue;
              const c = clips.find(x => x.id === tlc.clipId);
              if (c?.bildrate) return `${Math.round(c.bildrate)} fps`;
            }
            for (const c of clips) {
              if (c.bildrate) return `${Math.round(c.bildrate)} fps`;
            }
            return "— fps";
          })()}</span>
          <span style={{ fontFamily: "var(--mono)", fontSize: 9, color: "var(--text3)" }}>{clips.length} Clips · {tlClips.length} Segmente</span>
          {lastMetrics && (
            <span
              title={`Letzter Cut · Stil: ${lastMetrics.stil}\nDiversität: ${lastMetrics.diversitaet.toFixed(2)}\nWechselrate: ${(lastMetrics.wechselrate * 100).toFixed(0)} %\nDialog-Treue: ${(lastMetrics.dialog_treue * 100).toFixed(0)} %${lastMetrics.prompt_relevance !== undefined ? `\nPrompt-Relevanz: ${(lastMetrics.prompt_relevance * 100).toFixed(0)} %` : ""}`}
              style={{
                fontFamily: "var(--mono)", fontSize: 9, color: "var(--green)",
                padding: "2px 6px", borderRadius: 3, background: "rgba(34,197,94,.08)",
                border: "1px solid rgba(34,197,94,.25)",
                cursor: "help",
              }}
            >
              Div {lastMetrics.diversitaet.toFixed(2)} · WR {(lastMetrics.wechselrate * 100).toFixed(0)}%{lastMetrics.prompt_relevance !== undefined ? ` · Rel ${(lastMetrics.prompt_relevance * 100).toFixed(0)}%` : ""}
            </span>
          )}
          <button
            onClick={() => setBeatSync(v => !v)}
            title={beatSync ? `Beat-Sync aktiv (${beatProSegment} Beats/Segment) — Klick zum Deaktivieren` : "Beat-Sync aktivieren: Schnitte folgen dem musikalischen Beat (librosa)"}
            style={{
              fontFamily: "var(--mono)", fontSize: 9, fontWeight: 600,
              color: beatSync ? "var(--green)" : "var(--text2)",
              padding: "2px 8px", borderRadius: 3,
              background: beatSync ? "rgba(34,197,94,.12)" : "var(--bg3)",
              border: `1px solid ${beatSync ? "rgba(34,197,94,.4)" : "var(--border)"}`,
              cursor: "pointer",
            }}
          >♪ Beat-Sync{beatSync ? ` ${beatProSegment}×` : ""}</button>
          <button
            onClick={() => setShowAtlas(true)}
            title="Material-Atlas: 2D-Projektion des CLIP-Embedding-Raums"
            style={{
              fontFamily: "var(--mono)", fontSize: 9, fontWeight: 600,
              color: "var(--text2)", padding: "2px 8px", borderRadius: 3,
              background: "var(--bg3)", border: "1px solid var(--border)",
              cursor: "pointer",
            }}
          >◉ Atlas</button>
          <button
            onClick={() => setShowRelations(true)}
            title="Material-Beziehungen: paarweise Korrelation, Multicam-Detektion"
            style={{
              fontFamily: "var(--mono)", fontSize: 9, fontWeight: 600,
              color: "var(--text2)", padding: "2px 8px", borderRadius: 3,
              background: "var(--bg3)", border: "1px solid var(--border)",
              cursor: "pointer",
            }}
          >⇄ Beziehungen</button>
          <button
            onClick={() => setShowMethodik(true)}
            title="Wissenschaftliche Pipeline & Referenzen anzeigen"
            style={{
              fontFamily: "var(--mono)", fontSize: 9, fontWeight: 600,
              color: "var(--text2)", padding: "2px 8px", borderRadius: 3,
              background: "var(--bg3)", border: "1px solid var(--border)",
              cursor: "pointer",
            }}
          >▣ Methodik</button>
        </div>
      </div>

      {/* ═══ CONTEXT MENU ═══ */}
      {ctxMenu && (() => {
        const ctxClip = tlClips.find(c => c.id === ctxMenu.clipId);
        const isGrouped = !!ctxClip?.groupId;
        return (
        <div onClick={e => e.stopPropagation()} style={{ position: "fixed", left: ctxMenu.x, top: ctxMenu.y, background: "var(--bg3)", border: "1px solid var(--border2)", borderRadius: 7, padding: 4, zIndex: 100, boxShadow: "0 8px 24px rgba(0,0,0,.5)", minWidth: 180 }}>
          <CtxItem icon={<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2}><rect x={9} y={9} width={13} height={13} rx={2}/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>} label="Duplizieren" onClick={() => { if (ctxClip) addTLClip({ ...ctxClip, id: `${ctxClip.id}-dup-${Date.now()}`, start: ctxClip.start + ctxClip.dauer, groupId: undefined }); setCtxMenu(null); }} />
          <CtxItem icon={<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2}><circle cx={6} cy={6} r={3}/><circle cx={6} cy={18} r={3}/><line x1={20} y1={4} x2={8.12} y2={15.88}/></svg>} label="Hier schneiden" onClick={() => { if (ctxClip) { const cutAt = pct * totalSec; if (cutAt > ctxClip.start && cutAt < ctxClip.start + ctxClip.dauer) { const leftDauer = cutAt - ctxClip.start; const origMs = ctxClip.mediaStart || 0; pushUndo(); updateTLClip(ctxClip.id, { dauer: leftDauer }); addTLClip({ ...ctxClip, id: `${ctxClip.id}-cut-${Date.now()}`, start: cutAt, dauer: ctxClip.dauer - leftDauer, mediaStart: origMs + leftDauer }); } } setCtxMenu(null); }} />
          <div style={{ height: 1, background: "var(--border)", margin: "3px 0" }} />
          {isGrouped ? (
            <CtxItem icon={<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2}><path d="M18 6L6 18M6 6l12 12"/></svg>} label="Gruppierung aufheben" onClick={() => {
              tlClips.filter(c => c.groupId === ctxClip!.groupId).forEach(c => updateTLClip(c.id, { groupId: undefined }));
              setCtxMenu(null);
            }} />
          ) : (
            <CtxItem icon={<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2}><path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/></svg>} label="Mit Audio gruppieren" onClick={() => {
              if (ctxClip && ctxClip.clipId) {
                const partner = tlClips.find(c => c.clipId === ctxClip.clipId && c.id !== ctxClip.id && !c.groupId);
                if (partner) {
                  const gId = `grp-${Date.now()}`;
                  updateTLClip(ctxClip.id, { groupId: gId });
                  updateTLClip(partner.id, { groupId: gId });
                }
              }
              setCtxMenu(null);
            }} />
          )}
          <div style={{ height: 1, background: "var(--border)", margin: "3px 0" }} />
          <CtxItem icon={<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2}><path d="M8 3H5a2 2 0 0 0-2 2v3m18 0V5a2 2 0 0 0-2-2h-3m0 18h3a2 2 0 0 0 2-2v-3M3 16v3a2 2 0 0 0 2 2h3"/></svg>}
            label={ctxClip?.transition ? `Überblende: ${ctxClip.transition.type} (${ctxClip.transition.dauer}s)` : "Überblende hinzufügen"}
            onClick={() => {
              if (ctxClip) {
                if (!ctxClip.transition) {
                  updateTLClip(ctxClip.id, { transition: { type: "dissolve", dauer: 0.5 } });
                }
                setTransitionPicker({ clipId: ctxClip.id, x: ctxMenu.x, y: ctxMenu.y - 80 });
              }
              setCtxMenu(null);
            }}
          />
          {ctxClip?.transition && (
            <CtxItem icon={<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2}><line x1={18} y1={6} x2={6} y2={18}/><line x1={6} y1={6} x2={18} y2={18}/></svg>}
              label="Überblende entfernen"
              danger
              onClick={() => { if (ctxClip) updateTLClip(ctxClip.id, { transition: undefined }); setCtxMenu(null); }}
            />
          )}
          <div style={{ height: 1, background: "var(--border)", margin: "3px 0" }} />
          <CtxItem icon={<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2}><polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14H6L5 6M10 11v6M14 11v6M9 6V4h6v2"/></svg>} label="Löschen" danger onClick={() => {
            if (ctxClip?.groupId) {
              tlClips.filter(c => c.groupId === ctxClip.groupId).forEach(c => removeTLClip(c.id));
            } else {
              removeTLClip(ctxMenu.clipId);
            }
            setCtxMenu(null);
          }} />
        </div>
        );
      })()}

      {/* ═══ TRANSITION PICKER ═══ */}
      {transitionPicker && (() => {
        const tClip = tlClips.find(c => c.id === transitionPicker.clipId);
        if (!tClip) return null;
        const cur = tClip.transition ?? { type: "dissolve", dauer: 0.5 };
        return (
          <div
            onClick={e => e.stopPropagation()}
            style={{ position: "fixed", left: transitionPicker.x, top: transitionPicker.y, background: "var(--bg3)", border: "1px solid var(--border2)", borderRadius: 8, padding: 10, zIndex: 200, boxShadow: "0 8px 28px rgba(0,0,0,.6)", minWidth: 200 }}
          >
            <div style={{ display: "flex", alignItems: "center", gap: 6, marginBottom: 8 }}>
              <div style={{ width: 8, height: 8, background: "rgba(168,85,247,0.9)", borderRadius: 2, transform: "rotate(45deg)" }} />
              <span style={{ fontSize: 11, fontWeight: 700, color: "var(--text)" }}>Überblende</span>
              <button onClick={() => setTransitionPicker(null)} style={{ marginLeft: "auto", background: "none", border: "none", cursor: "pointer", color: "var(--text3)", padding: 1 }}>✕</button>
            </div>
            <div style={{ display: "flex", flexWrap: "wrap", gap: 3, marginBottom: 8 }}>
              {TRANSITION_TYPES.map(t => (
                <button key={t.id} onClick={() => updateTLClip(tClip.id, { transition: { ...cur, type: t.id } })}
                  style={{ padding: "3px 7px", borderRadius: 4, fontSize: 9, fontWeight: 600, cursor: "pointer", border: `1px solid ${cur.type === t.id ? "rgba(168,85,247,.6)" : "var(--border)"}`, color: cur.type === t.id ? "rgba(168,85,247,1)" : "var(--text3)", background: cur.type === t.id ? "rgba(168,85,247,.15)" : "transparent", fontFamily: "var(--font)" }}>
                  {t.label}
                </button>
              ))}
            </div>
            <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
              <span style={{ fontSize: 10, color: "var(--text3)", flex: 1 }}>Dauer</span>
              <input type="range" min={0.1} max={2} step={0.1} value={cur.dauer}
                onChange={e => updateTLClip(tClip.id, { transition: { ...cur, dauer: parseFloat(e.target.value) } })}
                style={{ width: 80, accentColor: "rgba(168,85,247,0.9)", cursor: "pointer" }}
              />
              <span style={{ fontFamily: "var(--mono)", fontSize: 10, color: "var(--text)", minWidth: 28 }}>{cur.dauer.toFixed(1)}s</span>
            </div>
            <button onClick={() => { updateTLClip(tClip.id, { transition: undefined }); setTransitionPicker(null); }}
              style={{ marginTop: 8, width: "100%", padding: "4px 0", background: "rgba(239,68,68,.12)", border: "1px solid rgba(239,68,68,.3)", borderRadius: 5, fontSize: 10, color: "rgba(239,68,68,0.9)", cursor: "pointer", fontFamily: "var(--font)" }}>
              Überblende entfernen
            </button>
          </div>
        );
      })()}

      {/* ═══ METHODIK-MODAL ═══
          Dokumentiert die wissenschaftliche Pipeline mit Referenzen,
          quantitativen Metriken und Reproduzierbarkeits-Anker. */}
      {showMethodik && <MethodikModal onClose={() => setShowMethodik(false)} />}
      {showAtlas && (
        <MaterialAtlas
          onClose={() => setShowAtlas(false)}
          clipIds={clips.filter(c => c.status === "analysiert").map(c => c.id)}
        />
      )}
      {showRelations && (
        <MaterialRelations
          onClose={() => setShowRelations(false)}
          clipIds={clips.filter(c => c.status === "analysiert").map(c => c.id)}
        />
      )}

      {/* ═══ KI-PIPELINE-OVERLAY ═══
          Macht die Schritte der Selektions-Pipeline sichtbar. Die einzelnen
          Vektor-Operationen sind in Millisekunden fertig — aber das System
          SOLL transparent zeigen, was es tut. Die kurzen Sichtbarkeits-Pausen
          sind nicht künstliche Verlangsamung der Rechnung, sondern lediglich
          Lesbarkeit der Anzeige. */}
      {pipelineStepIdx >= 0 && (
        <AiPipelineOverlay
          currentStep={pipelineStepIdx}
          prompt={aiPrompt || null}
          beatSync={beatSync}
          beatProSegment={beatProSegment}
          szenenAnzahl={clips.filter(c => c.status === "analysiert").length}
        />
      )}

      {/* ═══ "WARUM DIESES SEGMENT?" PANEL ═══
          Erscheint, wenn ein vom System ausgewähltes Segment selektiert ist.
          Zeigt die Provenienz: LLaVA-Beschreibung, Whisper-Transkript,
          CLIP-Relevanz, Rolle in der Erzählung. Jedes Cut wird damit
          nachvollziehbar — kein Black-Box-Resultat. */}
      {(() => {
        const sel = selectedClip ? tlClips.find(c => c.id === selectedClip) : null;
        if (!sel || !sel.ai) return null;
        const hasProvenance = !!(sel.beschreibung || sel.transkription || sel.promptRelevance !== null || sel.energie !== null);
        if (!hasProvenance) return null;
        const sourceClip = sel.clipId ? clips.find(c => c.id === sel.clipId) : null;
        return (
          <div
            style={{
              position: "fixed", right: 12, bottom: 44, zIndex: 90,
              width: 320, maxHeight: "55vh", overflowY: "auto",
              background: "var(--bg2)", border: "1px solid var(--border2)",
              borderRadius: 8, boxShadow: "0 12px 36px rgba(0,0,0,.55)",
              fontFamily: "var(--font)",
            }}
          >
            <div style={{
              display: "flex", alignItems: "center", gap: 6,
              padding: "8px 11px", borderBottom: "1px solid var(--border)",
              background: "var(--bg3)",
            }}>
              <div style={{ width: 6, height: 6, borderRadius: "50%", background: "var(--green)" }} />
              <span style={{ fontSize: 10, fontWeight: 700, color: "var(--text)", letterSpacing: ".04em", textTransform: "uppercase" }}>
                Warum dieses Segment?
              </span>
              <button
                onClick={() => setSelectedClip(null)}
                style={{
                  marginLeft: "auto", background: "none", border: "none",
                  color: "var(--text3)", cursor: "pointer", fontSize: 13, padding: 0, lineHeight: 1,
                }}
              >✕</button>
            </div>

            <div style={{ padding: "10px 12px", fontSize: 11, color: "var(--text2)", lineHeight: 1.5 }}>
              {/* Identität */}
              <div style={{ marginBottom: 8 }}>
                <div style={{ fontSize: 10, color: "var(--text3)" }}>Quelle</div>
                <div style={{ fontSize: 11, color: "var(--text)", fontWeight: 600 }}>
                  {sourceClip?.dateiname ?? "—"}{sel.szeneNr !== undefined ? ` · Szene ${sel.szeneNr}` : ""}
                </div>
                <div style={{ fontSize: 10, color: "var(--text3)", fontFamily: "var(--mono)", marginTop: 2 }}>
                  {sel.mediaStart.toFixed(2)}s → {(sel.mediaStart + sel.dauer).toFixed(2)}s · Dauer {sel.dauer.toFixed(2)}s
                </div>
              </div>

              {/* CLIP-Relevanz — die wichtigste Zahl bei prompt-getriebener Auswahl */}
              {sel.promptRelevance !== null && sel.promptRelevance !== undefined && (
                <div style={{ marginBottom: 10, padding: "7px 9px", background: "rgba(34,197,94,.08)", border: "1px solid rgba(34,197,94,.25)", borderRadius: 5 }}>
                  <div style={{ fontSize: 9.5, fontWeight: 700, color: "var(--green)", letterSpacing: ".05em", textTransform: "uppercase", marginBottom: 3 }}>
                    CLIP-Prompt-Relevanz
                  </div>
                  <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                    <div style={{ flex: 1, height: 5, background: "rgba(255,255,255,.08)", borderRadius: 2, overflow: "hidden" }}>
                      <div style={{ width: `${(sel.promptRelevance * 100).toFixed(0)}%`, height: "100%", background: "var(--green)" }} />
                    </div>
                    <span style={{ fontFamily: "var(--mono)", fontSize: 11, color: "var(--text)", fontWeight: 600 }}>
                      {(sel.promptRelevance * 100).toFixed(0)}%
                    </span>
                  </div>
                  <div style={{ fontSize: 9.5, color: "var(--text3)", marginTop: 3, fontStyle: "italic" }}>
                    cosine sim(prompt, scene) in CLIP-512-dim Raum
                  </div>
                </div>
              )}

              {/* Sekundär-Scores */}
              {(sel.energie !== null || sel.interessantheit !== null) && (
                <div style={{ display: "flex", gap: 8, marginBottom: 10 }}>
                  {sel.energie !== null && sel.energie !== undefined && (
                    <Mini label="Energie" value={`${(sel.energie * 100).toFixed(0)}%`} />
                  )}
                  {sel.interessantheit !== null && sel.interessantheit !== undefined && (
                    <Mini label="Interessant" value={`${(sel.interessantheit * 100).toFixed(0)}%`} />
                  )}
                  {sel.rolle && <Mini label="Rolle" value={sel.rolle} />}
                </div>
              )}

              {/* LLaVA-Visualbeschreibung */}
              {sel.beschreibung && (
                <div style={{ marginBottom: 9 }}>
                  <div style={{ fontSize: 9.5, fontWeight: 700, color: "var(--orange)", letterSpacing: ".05em", textTransform: "uppercase", marginBottom: 3 }}>
                    LLaVA · Visualbeschreibung
                  </div>
                  <div style={{ fontSize: 10.5, color: "var(--text)", lineHeight: 1.45, fontStyle: "italic" }}>
                    {sel.beschreibung}
                  </div>
                </div>
              )}

              {/* Whisper-Transkript */}
              {sel.transkription && sel.transkription.trim().length > 0 && (
                <div>
                  <div style={{ fontSize: 9.5, fontWeight: 700, color: "var(--blue)", letterSpacing: ".05em", textTransform: "uppercase", marginBottom: 3 }}>
                    Whisper · Transkript
                  </div>
                  <div style={{
                    fontSize: 10, color: "var(--text2)", lineHeight: 1.45,
                    fontFamily: "var(--mono)",
                    padding: "5px 7px",
                    background: "rgba(255,255,255,.025)",
                    border: "1px solid var(--border)", borderRadius: 4,
                    maxHeight: 80, overflowY: "auto",
                  }}>
                    {sel.transkription}
                  </div>
                </div>
              )}
            </div>
          </div>
        );
      })()}
    </div>
  );
}

// ═══════════════════════════════════════════════════════════
// AI-Pipeline-Overlay — zeigt SCHRITT FÜR SCHRITT, was passiert
// ═══════════════════════════════════════════════════════════
// Wozu?
//   Auch wenn die Vektor-Operationen schnell sind (cosine sim auf 512-dim
//   ist in Millisekunden fertig), muss der User SEHEN, was das System tut.
//   Ein instantes Resultat wirkt unseriös ("Wo ist die Rechnung?"). Diese
//   Overlay macht die Pipeline-Phasen lesbar — die kleinen Anzeige-Pausen
//   verlangsamen nicht die Rechnung, sondern die Darstellung, damit der
//   Mensch die Schritte überhaupt wahrnehmen kann.
function AiPipelineOverlay({
  currentStep,
  prompt,
  beatSync,
  beatProSegment,
  szenenAnzahl,
}: {
  currentStep: number;
  prompt: string | null;
  beatSync: boolean;
  beatProSegment: number;
  szenenAnzahl: number;
}) {
  const steps: { label: string; detail: string }[] = [
    {
      label: "Embeddings laden",
      detail: `${szenenAnzahl > 0 ? szenenAnzahl + " analysierte Clips · " : ""}512-dim CLIP-Vektoren aus PostgreSQL`,
    },
    {
      label: prompt ? "Prompt via CLIP-Text-Encoder" : "Energie- / Interessantheits-Scoring",
      detail: prompt
        ? `„${prompt.length > 38 ? prompt.slice(0, 36) + "…" : prompt}" → 512-dim L2-normalisiert`
        : "Action vs. Calm Prompts (zero-shot)",
    },
    {
      label: prompt ? "Cosine-Similarity gegen alle Szenen" : "Szenen-Scoring",
      detail: prompt
        ? "sim(t, s) = (E_t · E_s) / (‖E_t‖ · ‖E_s‖) für jede Szene"
        : "narrativer Bogen: ouverture → climax → cloture",
    },
    {
      label: "Top-Kandidaten-Pool (3 ×K)",
      detail: "Vorauswahl der relevantesten Szenen für die Re-Ranking-Phase",
    },
    {
      label: "Multicam-Dedup",
      detail: "Zeit-Buckets (6s) · Kamera-Variation für natürlichen Multicam-Schnitt",
    },
    {
      label: "MMR Re-Ranking (Diversität)",
      detail: "Carbonell & Goldstein 1998 · λ·rel - (1-λ)·max_sim · λ=0.7",
    },
    {
      label: beatSync ? "Beat-Tracking via librosa" : "Narrative Rollen zuweisen",
      detail: beatSync
        ? `librosa.beat.beat_track (Ellis 2007) · ${beatProSegment} Beats pro Segment`
        : "ouverture · action · transition · climax · cloture",
    },
    {
      label: "Timeline-Segmente bauen",
      detail: "V1 + A1 · chronologisch sortiert · AV-gruppiert",
    },
    {
      label: "Quantitative Metriken",
      detail: "Diversität · Wechselrate · Dialog-Treue · Prompt-Relevanz",
    },
    {
      label: "Persistenz & Übergabe",
      detail: "Speichern in PostgreSQL · Übergabe ans Frontend",
    },
  ];

  return (
    <div
      style={{
        position: "fixed", inset: 0, zIndex: 600,
        background: "rgba(8,9,10,.85)", backdropFilter: "blur(10px)",
        display: "flex", alignItems: "center", justifyContent: "center",
        padding: 20,
      }}
    >
      <div
        style={{
          background: "var(--bg1)", border: "1px solid var(--border2)",
          borderRadius: 12, width: "100%", maxWidth: 520,
          boxShadow: "0 30px 80px rgba(0,0,0,.7)",
          fontFamily: "var(--font)",
        }}
      >
        {/* Header */}
        <div style={{
          padding: "14px 20px", borderBottom: "1px solid var(--border)",
          display: "flex", alignItems: "center", gap: 10,
        }}>
          <div style={{
            width: 9, height: 9, borderRadius: "50%",
            background: "var(--orange)",
            boxShadow: "0 0 12px rgba(249,115,22,.7)",
            animation: "pulse 1.4s ease-in-out infinite",
          }} />
          <div style={{ fontSize: 13, fontWeight: 700, color: "var(--text)", letterSpacing: ".02em" }}>
            KI-Schnitt-Pipeline läuft
          </div>
          <div style={{ marginLeft: "auto", fontFamily: "var(--mono)", fontSize: 10, color: "var(--text3)" }}>
            {Math.min(currentStep + 1, steps.length)} / {steps.length}
          </div>
        </div>

        {/* Steps */}
        <div style={{ padding: "14px 20px", display: "flex", flexDirection: "column", gap: 6 }}>
          {steps.map((s, i) => {
            const status: "done" | "active" | "pending" =
              i < currentStep ? "done" : i === currentStep ? "active" : "pending";
            const color = status === "done" ? "var(--green)" : status === "active" ? "var(--orange)" : "var(--text3)";
            return (
              <div key={i} style={{
                display: "flex", alignItems: "flex-start", gap: 10,
                padding: "7px 9px",
                background: status === "active" ? "rgba(249,115,22,.07)" : "transparent",
                border: `1px solid ${status === "active" ? "rgba(249,115,22,.25)" : "transparent"}`,
                borderRadius: 6,
                opacity: status === "pending" ? 0.45 : 1,
                transition: "all .2s",
              }}>
                <div style={{
                  width: 16, height: 16, borderRadius: "50%",
                  flexShrink: 0, marginTop: 1,
                  display: "flex", alignItems: "center", justifyContent: "center",
                  border: `1.5px solid ${color}`,
                  background: status === "done" ? "var(--green)" : "transparent",
                }}>
                  {status === "done" && (
                    <svg width={9} height={9} viewBox="0 0 24 24" fill="none" stroke="white" strokeWidth={4}>
                      <polyline points="20 6 9 17 4 12"/>
                    </svg>
                  )}
                  {status === "active" && (
                    <div style={{
                      width: 6, height: 6, borderRadius: "50%",
                      background: "var(--orange)",
                      animation: "pulse 0.9s ease-in-out infinite",
                    }} />
                  )}
                </div>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ fontSize: 11.5, fontWeight: 600, color }}>
                    {s.label}
                  </div>
                  <div style={{ fontSize: 10, color: "var(--text3)", fontFamily: "var(--mono)", marginTop: 1 }}>
                    {s.detail}
                  </div>
                </div>
              </div>
            );
          })}
        </div>

        {/* Footer */}
        <div style={{
          padding: "10px 20px", borderTop: "1px solid var(--border)",
          fontSize: 10, color: "var(--text3)", lineHeight: 1.5,
          background: "var(--bg2)", borderRadius: "0 0 12px 12px",
        }}>
          Alle Rechnungen laufen lokal · keine Cloud-Calls · deterministisch
        </div>
      </div>
    </div>
  );
}

// ═══════════════════════════════════════════════════════════
// SendToNleButton — "Senden an..." NLE-Dropdown
// ═══════════════════════════════════════════════════════════
// Schreibt die Timeline als FCPXML in ~/Documents/CinAssist_Exports/
// und öffnet sie direkt mit DaVinci Resolve / Premiere Pro / Final Cut Pro.
// Macht den Übergang vom CinAssist-Prototyp ins professionelle NLE-Tool
// ein-Klick — der User braucht keine Datei manuell zu importieren.
function SendToNleButton({ tlClips, clips }: { tlClips: TLClip[]; clips: import("@/lib/api").ClipDTO[] }) {
  const [open, setOpen] = useState(false);
  const [sending, setSending] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);
  const ref = useRef<HTMLDivElement>(null);

  // Außerhalb klicken schließt das Menu
  useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, [open]);

  const send = useCallback(async (app: "davinci" | "premiere" | "fcp", label: string) => {
    setSending(label);
    setError(null);
    setSuccess(null);
    try {
      const fcpxml = buildFcpxml(tlClips, clips, "HAW CineAssist");
      const r = await fetch("http://localhost:8001/api/export/open-in", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ app, fcpxml, name: "CinAssist_Timeline" }),
      });
      if (!r.ok) {
        const j = await r.json().catch(() => ({}));
        throw new Error(j.detail || `HTTP ${r.status}`);
      }
      const j = await r.json();
      setSuccess(j.nachricht || `${j.app} gestartet, Datei im Finder sichtbar.`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Fehler");
    } finally {
      setSending(null);
    }
  }, [tlClips, clips]);

  const disabled = tlClips.length === 0;

  return (
    <div ref={ref} style={{ position: "relative" }}>
      <button
        onClick={() => !disabled && setOpen(v => !v)}
        disabled={disabled}
        title={disabled ? "Keine Segmente in der Timeline" : "Timeline an ein professionelles Schnittprogramm senden"}
        style={{
          display: "flex", alignItems: "center", gap: 5,
          padding: "6px 12px", background: "var(--bg3)",
          color: disabled ? "var(--text3)" : "var(--text)",
          border: "1px solid var(--border2)", borderRadius: 6,
          fontSize: 11, fontWeight: 600,
          cursor: disabled ? "not-allowed" : "pointer",
          fontFamily: "var(--font)", opacity: disabled ? 0.55 : 1,
        }}
      >
        <svg width={12} height={12} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2.5}>
          <line x1={22} y1={2} x2={11} y2={13}/>
          <polygon points="22 2 15 22 11 13 2 9 22 2"/>
        </svg>
        Senden an…
        <svg width={9} height={9} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2.5}>
          <polyline points="6 9 12 15 18 9"/>
        </svg>
      </button>
      {open && (
        <div style={{
          position: "absolute", top: "calc(100% + 4px)", right: 0,
          background: "var(--bg2)", border: "1px solid var(--border2)",
          borderRadius: 6, boxShadow: "0 10px 30px rgba(0,0,0,.5)",
          minWidth: 230, zIndex: 100, padding: 4,
        }}>
          <NleItem
            label="DaVinci Resolve"
            sub="Timeline wird direkt importiert"
            iconSrc="/nle-davinci.png"
            disabled={sending !== null}
            sending={sending === "DaVinci"}
            onClick={() => send("davinci", "DaVinci")}
          />
          <NleItem
            label="Adobe Premiere Pro"
            sub="öffnet mit FCPXML-Import"
            iconSrc="/nle-premiere.png"
            disabled={sending !== null}
            sending={sending === "Premiere"}
            onClick={() => send("premiere", "Premiere")}
          />
          <NleItem
            label="Final Cut Pro"
            sub="natives FCPXML-Format"
            iconSrc="/nle-fcp.png"
            disabled={sending !== null}
            sending={sending === "FCP"}
            onClick={() => send("fcp", "FCP")}
          />
          {error && (
            <div style={{
              padding: "6px 9px", margin: "4px 2px 2px",
              fontSize: 10, color: "var(--red)", lineHeight: 1.4,
              background: "rgba(239,68,68,.07)", border: "1px solid rgba(239,68,68,.25)",
              borderRadius: 4,
            }}>
              {error}
            </div>
          )}
          {success && (
            <div style={{
              padding: "7px 10px", margin: "4px 2px 2px",
              fontSize: 10.5, color: "var(--green)", lineHeight: 1.45,
              background: "rgba(34,197,94,.08)", border: "1px solid rgba(34,197,94,.3)",
              borderRadius: 4,
            }}>
              ✓ {success}
            </div>
          )}
          <div style={{ padding: "5px 9px 2px", fontSize: 9, color: "var(--text3)", lineHeight: 1.45, fontStyle: "italic" }}>
            FCPXML wird in ~/Documents/CinAssist_Exports/ gespeichert.<br/>
            Drag aus dem Finder in die NLE = Timeline importiert.
          </div>
        </div>
      )}
    </div>
  );
}

function NleItem({ label, sub, iconSrc, disabled, sending, onClick }: {
  label: string; sub: string; iconSrc: string;
  disabled: boolean; sending: boolean; onClick: () => void;
}) {
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      style={{
        display: "flex", alignItems: "center", gap: 9,
        width: "100%", padding: "7px 9px",
        background: "none", border: "none", borderRadius: 4,
        cursor: disabled ? "wait" : "pointer", textAlign: "left",
        opacity: disabled && !sending ? 0.5 : 1,
        fontFamily: "var(--font)",
      }}
      onMouseEnter={e => { if (!disabled) e.currentTarget.style.background = "var(--bg3)"; }}
      onMouseLeave={e => { e.currentTarget.style.background = "none"; }}
    >
      <img
        src={iconSrc}
        alt={label}
        style={{
          width: 24, height: 24, borderRadius: 5, flexShrink: 0,
          objectFit: "contain",
        }}
      />
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ fontSize: 11, fontWeight: 600, color: "var(--text)" }}>{label}</div>
        <div style={{ fontSize: 9, color: "var(--text3)", marginTop: 1 }}>{sub}</div>
      </div>
      {sending && (
        <div style={{
          width: 12, height: 12, borderRadius: "50%",
          border: "2px solid var(--border2)", borderTopColor: "var(--orange)",
          animation: "spin 0.7s linear infinite",
        }} />
      )}
    </button>
  );
}

function Mini({ label, value }: { label: string; value: string }) {
  return (
    <div style={{
      flex: 1, padding: "5px 7px",
      background: "rgba(255,255,255,.025)",
      border: "1px solid var(--border)", borderRadius: 4,
    }}>
      <div style={{ fontSize: 9, color: "var(--text3)", textTransform: "uppercase", letterSpacing: ".04em" }}>{label}</div>
      <div style={{ fontSize: 11, color: "var(--text)", fontWeight: 600, fontFamily: "var(--mono)" }}>{value}</div>
    </div>
  );
}

// ═══════════════════════════════════════════════════════════
// Methodik-Modal — dokumentiert Pipeline & Referenzen
// ═══════════════════════════════════════════════════════════
function MethodikModal({ onClose }: { onClose: () => void }) {
  return (
    <div
      onClick={onClose}
      style={{
        position: "fixed", inset: 0, background: "rgba(0,0,0,.7)",
        display: "flex", alignItems: "center", justifyContent: "center",
        zIndex: 500, padding: 20,
      }}
    >
      <div
        onClick={e => e.stopPropagation()}
        style={{
          background: "var(--bg1)", border: "1px solid var(--border2)",
          borderRadius: 10, maxWidth: 720, width: "100%",
          maxHeight: "85vh", overflowY: "auto",
          boxShadow: "0 20px 60px rgba(0,0,0,.6)",
        }}
      >
        <div style={{
          padding: "14px 18px", borderBottom: "1px solid var(--border)",
          display: "flex", alignItems: "center", gap: 10,
          background: "var(--bg2)", position: "sticky", top: 0,
        }}>
          <span style={{ fontSize: 13, fontWeight: 700, color: "var(--text)" }}>
            Methodik · CinAssist Pipeline
          </span>
          <span style={{ fontSize: 10, color: "var(--text3)", marginLeft: 4 }}>
            100 % lokal · reproduzierbar · zitierbar
          </span>
          <button
            onClick={onClose}
            style={{
              marginLeft: "auto", background: "none", border: "none",
              color: "var(--text2)", cursor: "pointer", fontSize: 16,
              padding: "0 4px",
            }}
          >✕</button>
        </div>

        <div style={{ padding: "16px 20px", fontSize: 12, color: "var(--text2)", lineHeight: 1.65 }}>

          <Section title="Phase 1 — Ingestion">
            <Item label="ffprobe" desc="Metadaten-Extraktion (Auflösung, Bildrate, Codec, Audiokanäle)." />
            <Item label="Proxy (H.264, -g 12)" desc="Vorschau-Stream mit frequenten Keyframes für präzises Seek." />
            <Item label="Waveform-PNG (showwavespic)" desc="Visuelle Audio-Vorschau in der Timeline." />
            <Item label="Thumbnail-Strip" desc="Frame-Streifen für Clip-Navigation." />
          </Section>

          <Section title="Phase 2 — Multimodale Analyse">
            <Item
              label="PySceneDetect · ContentDetector"
              desc="Szenengrenzen via HSV-Differenz (Threshold 27, empirisch von den Autoren)."
              ref="Castellano, B. 2014–2024 · github.com/Breakthrough/PySceneDetect"
            />
            <Item
              label="Whisper large-v3"
              desc="Lokale Sprache-zu-Text Transkription, Sprache automatisch erkannt."
              ref="Radford et al., 2022 · OpenAI"
            />
            <Item
              label="LLaVA-7B (über Ollama)"
              desc="Vision-Language-Modell beschreibt FAKTISCH, was im Thumbnail sichtbar ist. Ersetzt LLaMA3-Halluzinationen bei dialogarmem Material."
              ref="Liu et al., NeurIPS 2023 · llava-vl.github.io"
            />
            <Item
              label="CLIP ViT-B/32"
              desc="512-dimensionale Embeddings pro Szenen-Thumbnail. Ermöglicht zero-shot Text-zu-Bild Retrieval."
              ref="Radford et al., ICML 2021 · openai.com/research/clip"
            />
          </Section>

          <Section title="Phase 3 — Prompt-getriebene Selektion">
            <Item
              label="Cosine-Similarity"
              desc={"sim(t, s) = (E_t · E_s) / (‖E_t‖ · ‖E_s‖)  —  Prompt-Embedding gegen jedes Szenen-Embedding."}
            />
            <Item
              label="Top-K Auswahl"
              desc="Szenen nach Relevanz sortiert, dann zeitlich auf der Timeline geordnet. Keine LLM-Halluzination, deterministisch."
            />
            <Item
              label="PCA-Atlas (◉)"
              desc="Lineare 2D-Projektion des 512-dim CLIP-Raums via SVD. Macht den semantischen Raum sichtbar und überprüfbar — der Prompt wird in denselben Raum projiziert, räumliche Nähe = Selektions-Relevanz."
            />
          </Section>

          <Section title="Phase 4 — Rhythmus-Synchronisation (optional, ♪)">
            <Item
              label="librosa.beat.beat_track"
              desc="Onset-strength + dynamic programming auf der Audio-Spur des Master-Clips. Liefert Tempo (BPM) und Beat-Zeitpunkte."
              ref="Ellis, JNMR 2007 · librosa.org"
            />
            <Item
              label="Beat-Snapping"
              desc="Schnittgrenzen werden auf den nächsten Beat ≥ Zielposition gesnappt; Segment-Mindestlänge = N Beats (Default 4). Macht Musik-Cuts rhythmisch tight statt visuell-aber-rhythmisch-zufällig."
            />
          </Section>

          <Section title="Quantitative Metriken">
            <Formula k="Diversität" v="|einzigartige Clip-Quellen| / Anzahl Segmente" />
            <Formula k="Wechselrate" v="Schnitte / Dauer (Schnitte pro Sekunde)" />
            <Formula k="Dialog-Treue" v="Σ Worte in Auswahl / Σ Worte gesamt" />
            <Formula k="Prompt-Relevanz" v="mean(cosine sim) der gewählten Szenen" />
          </Section>

          <Section title="Reproduzierbarkeit">
            <Item label="Lokale Modelle" desc="Kein Cloud-Call. Whisper, LLaVA, LLaMA3 laufen über Ollama. CLIP über PyTorch + Metal." />
            <Item label="Deterministische Selektion" desc="Top-K nach cosine similarity ist stabil. LLM-Refinement abgeschaltet, Temperatur fixiert (T=0.2)." />
            <Item label="Versionierter State" desc="Jede Szene speichert: Bildgrenzen, Embeddings, Transkript, Beschreibung in PostgreSQL." />
          </Section>

          <Section title="Bekannte Grenzen">
            <Item label="Multicam-Sync" desc="Kein automatischer Sync via Audiokorrelation — aktuell werden Multicam-Winkel als separate Clips behandelt; Audio-Drift wird über den Master-Clip umgangen." />
            <Item label="Semantischer Höhepunkt-Detektor" desc="Top-K reiht relevante Szenen, schneidet aber nicht explizit auf semantische Höhepunkte (Refrain, Punchline). Eine Erweiterung wäre prosodie-basierte (Whisper-Wort-Energie) Peak-Detection." />
            <Item label="Beats vs. Visual Cuts" desc="Wenn Beat-Sync aktiv ist und das Material kein klares Beat-Pattern hat (z.B. Sprach-Dokumentation), kann die rhythmische Snap-Regel ungewollte Verzerrungen erzeugen. Toggle deaktivieren in solchen Fällen." />
          </Section>

        </div>
      </div>
    </div>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div style={{ marginBottom: 16 }}>
      <div style={{
        fontSize: 10, fontWeight: 700, letterSpacing: ".08em",
        textTransform: "uppercase", color: "var(--orange)",
        marginBottom: 6, paddingBottom: 4, borderBottom: "1px solid var(--border)",
      }}>{title}</div>
      <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>{children}</div>
    </div>
  );
}

function Item({ label, desc, ref }: { label: string; desc: string; ref?: string }) {
  return (
    <div style={{ padding: "5px 0" }}>
      <div style={{ fontSize: 11.5, fontWeight: 600, color: "var(--text)" }}>{label}</div>
      <div style={{ fontSize: 11, color: "var(--text2)", marginTop: 2 }}>{desc}</div>
      {ref && <div style={{ fontSize: 10, color: "var(--text3)", fontStyle: "italic", marginTop: 2 }}>{ref}</div>}
    </div>
  );
}

function Formula({ k, v }: { k: string; v: string }) {
  return (
    <div style={{ display: "flex", gap: 10, alignItems: "baseline", padding: "3px 0" }}>
      <span style={{ fontSize: 11.5, fontWeight: 600, color: "var(--text)", minWidth: 120 }}>{k}</span>
      <span style={{ fontFamily: "var(--mono)", fontSize: 10.5, color: "var(--text2)" }}>{v}</span>
    </div>
  );
}

// ═══════════════════════════════════════════════════════════
// Sub-Komponenten
// ═══════════════════════════════════════════════════════════

function Sep() { return <div style={{ width: 1, height: 20, background: "var(--border)", margin: "0 8px" }} />; }

function TbBtn({ icon, label, active, onClick }: { icon: React.ReactNode; label: string; active?: boolean; onClick?: () => void }) {
  return (
    <button onClick={onClick} style={{ display: "flex", alignItems: "center", gap: 5, padding: "5px 8px", borderRadius: 5, color: active ? "var(--text)" : "var(--text3)", fontSize: 11, fontWeight: 500, background: active ? "var(--bg3)" : "none", border: "none", cursor: "pointer", fontFamily: "var(--font)" }}
      onMouseEnter={e => { if (!active) { e.currentTarget.style.background = "var(--bg3)"; e.currentTarget.style.color = "var(--text2)"; } }}
      onMouseLeave={e => { if (!active) { e.currentTarget.style.background = "none"; e.currentTarget.style.color = "var(--text3)"; } }}
    >
      <span style={{ width: 13, height: 13, display: "flex" }}>{icon}</span>{label}
    </button>
  );
}

function PbBtn({ icon, onClick }: { icon: React.ReactNode; onClick?: () => void }) {
  return (
    <button onClick={onClick} style={{ background: "none", border: "none", color: "var(--text3)", cursor: "pointer", padding: 3, borderRadius: 4, display: "flex", alignItems: "center" }}
      onMouseEnter={e => (e.currentTarget.style.color = "var(--text2)")}
      onMouseLeave={e => (e.currentTarget.style.color = "var(--text3)")}
    >
      <span style={{ width: 13, height: 13, display: "flex" }}>{icon}</span>
    </button>
  );
}

function ZoomBtn({ label, onClick }: { label: string; onClick: () => void }) {
  return (
    <button onClick={onClick} style={{ background: "var(--bg3)", border: "1px solid var(--border)", color: "var(--text3)", fontSize: 12, width: 20, height: 20, borderRadius: 4, display: "flex", alignItems: "center", justifyContent: "center", cursor: "pointer", lineHeight: 1 }}
      onMouseEnter={e => { e.currentTarget.style.background = "var(--bg4)"; e.currentTarget.style.color = "var(--text2)"; }}
      onMouseLeave={e => { e.currentTarget.style.background = "var(--bg3)"; e.currentTarget.style.color = "var(--text3)"; }}
    >{label}</button>
  );
}

function TlTool({ children, active, onClick, tooltip }: { children: React.ReactNode; active?: boolean; onClick?: () => void; tooltip?: string }) {
  return (
    <button onClick={onClick} title={tooltip} style={{ width: 28, height: 28, display: "flex", alignItems: "center", justifyContent: "center", background: active ? "var(--bg4)" : "none", border: "none", borderRadius: 4, cursor: "pointer", color: active ? "var(--text)" : "var(--text3)", flexShrink: 0, position: "relative" }}
      onMouseEnter={e => { if (!active) { e.currentTarget.style.background = "var(--bg3)"; e.currentTarget.style.color = "var(--text2)"; } }}
      onMouseLeave={e => { if (!active) { e.currentTarget.style.background = "none"; e.currentTarget.style.color = "var(--text3)"; } }}
    >
      <span style={{ width: 15, height: 15, display: "flex" }}>{children}</span>
      {active && <div style={{ position: "absolute", bottom: 1, left: "50%", transform: "translateX(-50%)", width: 10, height: 2, borderRadius: 1, background: "var(--orange)" }} />}
    </button>
  );
}

function TlSep() {
  return <div style={{ width: 1, height: 18, background: "var(--border)", margin: "0 4px", flexShrink: 0 }} />;
}

function UploadZone({ label, badge, badgeStyle, loading, onClick, onDrop }: { label: string; badge: string; badgeStyle: React.CSSProperties; loading?: boolean; onClick: () => void; onDrop: (e: React.DragEvent) => void }) {
  return (
    <div style={{ margin: "10px", border: "1.5px dashed var(--border2)", borderRadius: 8, padding: 12, display: "flex", flexDirection: "column", alignItems: "center", gap: 5, cursor: loading ? "wait" : "pointer", transition: "all .15s", opacity: loading ? 0.6 : 1 }}
      onClick={onClick}
      onMouseEnter={e => { e.currentTarget.style.borderColor = "var(--orange)"; e.currentTarget.style.background = "var(--orange-soft)"; }}
      onMouseLeave={e => { e.currentTarget.style.borderColor = "var(--border2)"; e.currentTarget.style.background = ""; }}
      onDragOver={e => { e.preventDefault(); e.currentTarget.style.borderColor = "var(--orange)"; }}
      onDragLeave={e => { e.currentTarget.style.borderColor = "var(--border2)"; }}
      onDrop={e => { e.currentTarget.style.borderColor = "var(--border2)"; onDrop(e); }}
    >
      {loading
        ? <div style={{ fontSize: 11, color: "var(--orange)", fontWeight: 600 }}>Wird hochgeladen…</div>
        : <>
            <svg width={18} height={18} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.5} style={{ color: "var(--text3)" }}><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4M17 8l-5-5-5 5M12 3v12"/></svg>
            <p style={{ fontSize: 11, color: "var(--text3)", textAlign: "center", lineHeight: 1.4 }}>Videos hier ablegen<br/><span style={{ fontSize: 9 }}>MP4 · MOV · AVI · mehrere Dateien</span></p>
          </>}
    </div>
  );
}

function ClipCard({ clip, job, clipColor, onAddToTimeline, onDelete, onShowPipeline }: { clip: ClipDTO; job?: { fortschritt: number; nachricht: string; status: string }; clipColor?: TLClip["color"]; onAddToTimeline: () => void; onDelete: () => void; onShowPipeline?: () => void }) {
  const isAnalysing = job && job.status !== "fertig" && job.status !== "fehler";
  const cc = TC_COLORS[clipColor ?? "orange"];
  const isAnalysiert = clip.status === "analysiert";
  return (
    <div draggable onDragStart={e => e.dataTransfer.setData("text/clip-id", clip.id)}
      onContextMenu={e => {
        if (isAnalysiert && onShowPipeline) {
          e.preventDefault();
          e.stopPropagation();
          onShowPipeline();
        }
      }}
      title={isAnalysiert ? "Rechtsklick: Analyse-Bericht anzeigen" : undefined}
      style={{ display: "flex", alignItems: "center", gap: 8, padding: "6px 8px", borderRadius: 6, cursor: "grab", border: "1px solid transparent", marginBottom: 2, transition: "all .12s", borderLeft: `3px solid ${cc.border}` }}
      onMouseEnter={e => { e.currentTarget.style.background = "var(--bg3)"; e.currentTarget.style.borderColor = cc.border; e.currentTarget.style.borderLeftColor = cc.border; }}
      onMouseLeave={e => { e.currentTarget.style.background = ""; e.currentTarget.style.borderColor = "transparent"; e.currentTarget.style.borderLeftColor = cc.border; }}
    >
      <div style={{ width: 52, height: 32, borderRadius: 4, background: "var(--bg4)", border: "1px solid var(--border)", flexShrink: 0, overflow: "hidden", position: "relative", display: "flex", alignItems: "center", justifyContent: "center" }}>
        {clip.strip_url ? (
          // Echtes Thumbnail: erste Kachel des 24-Tile JPG-Strips.
          // Strip = 1920×45 (24 Frames à 80×45). Wir skalieren so, dass
          // genau Frame 1 die 52×32 Box füllt: background-size 1248×32.
          <div style={{
            position: "absolute", inset: 0,
            backgroundImage: `url(http://localhost:8001${clip.strip_url})`,
            backgroundSize: "1248px 100%",
            backgroundPosition: "0 0",
            backgroundRepeat: "no-repeat",
          }} />
        ) : (
          <>
            <div style={{ position: "absolute", inset: 0, opacity: 0.9, background: clip.quelle === "A" ? "linear-gradient(135deg,#1a1040,#0c2040)" : "linear-gradient(135deg,#1a0c30,#2a1040)" }} />
            <div style={{ width: 16, height: 16, background: "rgba(0,0,0,.5)", borderRadius: "50%", display: "flex", alignItems: "center", justifyContent: "center", zIndex: 1 }}>
              <svg width={7} height={7} viewBox="0 0 24 24" fill="white"><path d="M5 3l14 9-14 9V3z"/></svg>
            </div>
          </>
        )}
        <span style={{ position: "absolute", bottom: 2, right: 3, fontFamily: "var(--mono)", fontSize: 8, color: "rgba(255,255,255,.85)", background: "rgba(0,0,0,.65)", padding: "0 3px", borderRadius: 2, zIndex: 2 }}>{fmtDauer(clip.dauer)}</span>
      </div>
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ fontSize: 11, fontWeight: 500, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{clip.dateiname}</div>
        <div style={{ display: "flex", gap: 6, marginTop: 2, alignItems: "center" }}>
          {isAnalysing
            ? <span style={{ fontSize: 9, color: "var(--orange)" }}>Analyse… {job?.fortschritt}%</span>
            : clip.status === "analysiert"
              ? <span title="Pipeline abgeschlossen: PySceneDetect · Whisper · LLaVA · CLIP" style={{ fontSize: 9, fontWeight: 600, padding: "1px 5px", borderRadius: 3, background: "var(--green-soft)", color: "var(--green)" }}>Analyse ✓</span>
              : <span style={{ fontSize: 9, color: "var(--text3)" }}>{clip.status}</span>}
          {clip.dateigroesse_mb && <span style={{ fontSize: 9, color: "var(--text3)" }}>{clip.dateigroesse_mb} MB</span>}
        </div>
        {isAnalysing && <div style={{ marginTop: 3, height: 2, background: "var(--bg4)", borderRadius: 1 }}><div style={{ height: "100%", width: `${job?.fortschritt || 0}%`, background: "var(--orange)", borderRadius: 1, transition: "width .3s" }} /></div>}
      </div>
      <div style={{ display: "flex", flexDirection: "column", gap: 3, flexShrink: 0, alignItems: "center" }}>
        <button onClick={e => { e.stopPropagation(); onAddToTimeline(); }} title="Zur Timeline hinzufügen" style={{ background: "none", border: "1px solid var(--border)", color: "var(--text3)", cursor: "pointer", padding: 3, borderRadius: 4, display: "flex", alignItems: "center", justifyContent: "center", width: 22, height: 22 }}
          onMouseEnter={e => { e.currentTarget.style.color = "var(--green)"; e.currentTarget.style.borderColor = "var(--green)"; }} onMouseLeave={e => { e.currentTarget.style.color = "var(--text3)"; e.currentTarget.style.borderColor = "var(--border)"; }}>
          <svg width={12} height={12} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2.5}><line x1={12} y1={5} x2={12} y2={19}/><line x1={5} y1={12} x2={19} y2={12}/></svg>
        </button>
        <button onClick={e => { e.stopPropagation(); if (confirm(`"${clip.dateiname}" löschen?`)) onDelete(); }} title="Clip löschen" style={{ background: "none", border: "1px solid var(--border)", color: "var(--text3)", cursor: "pointer", padding: 3, borderRadius: 4, display: "flex", alignItems: "center", justifyContent: "center", width: 22, height: 22 }}
          onMouseEnter={e => { e.currentTarget.style.color = "var(--red)"; e.currentTarget.style.borderColor = "var(--red)"; }} onMouseLeave={e => { e.currentTarget.style.color = "var(--text3)"; e.currentTarget.style.borderColor = "var(--border)"; }}>
          <svg width={12} height={12} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2.5}><polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14H6L5 6M10 11v6M14 11v6M9 6V4h6v2"/></svg>
        </button>
      </div>
    </div>
  );
}

function CtxItem({ icon, label, danger, onClick }: { icon: React.ReactNode; label: string; danger?: boolean; onClick: () => void }) {
  return (
    <div onClick={onClick} style={{ padding: "6px 10px", borderRadius: 5, fontSize: 11, cursor: "pointer", display: "flex", alignItems: "center", gap: 8, color: danger ? "var(--red)" : "var(--text2)" }}
      onMouseEnter={e => (e.currentTarget.style.background = danger ? "rgba(239,68,68,.1)" : "var(--bg4)")}
      onMouseLeave={e => (e.currentTarget.style.background = "")}
    >
      <span style={{ width: 12, height: 12, display: "flex", color: "var(--text3)" }}>{icon}</span>{label}
    </div>
  );
}

// ─── ResizeHandle — DaVinci/Premiere-style drag-resize between panels ─────
// Beim Hover orange leuchten; während dem Drag bleibt der globale Cursor
// aktiv (ew-resize/ns-resize) und die Text-Selektion ist deaktiviert.
function ResizeHandle({
  direction,
  onResize,
}: {
  direction: "horizontal" | "vertical";
  onResize: (delta: number) => void;
}) {
  const startPos = useRef(0);

  const handleMouseDown = (e: React.MouseEvent) => {
    e.preventDefault();
    startPos.current = direction === "horizontal" ? e.clientX : e.clientY;

    const handleMove = (ev: MouseEvent) => {
      const pos = direction === "horizontal" ? ev.clientX : ev.clientY;
      const delta = pos - startPos.current;
      if (delta !== 0) {
        onResize(delta);
        startPos.current = pos;
      }
    };

    const handleUp = () => {
      window.removeEventListener("mousemove", handleMove);
      window.removeEventListener("mouseup", handleUp);
      document.body.style.cursor = "";
      document.body.style.userSelect = "";
    };

    document.body.style.cursor = direction === "horizontal" ? "ew-resize" : "ns-resize";
    document.body.style.userSelect = "none";
    window.addEventListener("mousemove", handleMove);
    window.addEventListener("mouseup", handleUp);
  };

  const isHorizontal = direction === "horizontal";
  return (
    <div
      onMouseDown={handleMouseDown}
      style={{
        cursor: isHorizontal ? "ew-resize" : "ns-resize",
        background: "var(--border)",
        transition: "background .15s",
        flexShrink: 0,
        ...(isHorizontal
          ? { width: 5, height: "100%" }
          : { height: 5, width: "100%" }),
      }}
      onMouseEnter={e => (e.currentTarget.style.background = "var(--orange)")}
      onMouseLeave={e => (e.currentTarget.style.background = "var(--border)")}
    />
  );
}
