"use client";
import { useEffect, useMemo, useRef, useState, CSSProperties } from "react";
import { Panel, PanelGroup, PanelResizeHandle, ImperativePanelHandle } from "react-resizable-panels";
import { usePlaybackEngine } from "@/hooks/usePlaybackEngine";
import {
  type EngineTimeline,
  framesToSeconds,
  secondsToFrames,
  tlClipsToEngineTracks,
} from "@/lib/timeline-model";
import ClipWaveform from "@/components/ClipWaveform";
import { planPlacement, hasCollision, findFreeTrack } from "@/lib/timeline-placement";
import type { TimelineCmd, TimelineCommandExecutor } from "@/lib/timeline-commands";
import { useProposalStore } from "@/lib/proposals";
import { ProposalSplitsLayer, ProposalDeletesInRow } from "@/components/ProposalGhostLayers";
import ChatPanel from "@/components/ChatPanel";
import { useChatStore } from "@/lib/chat-store";

// Sequence frame rate. The engine keeps time in integer frames; the existing
// seconds-based edit model is converted to/from frames only at this edge.
const PROJECT_FPS = 30;

// DaVinci-style snap guides: how close (in screen pixels) a dragged clip edge
// must come to a snap target before it "magnets" onto it and a guide line shows.
const SNAP_TOLERANCE_PX = 10;

/* ═══════════════════════════════════════════════════════════
   API types (mirrors backend @/lib/api.ts, kept inline pour
   éviter les import cycles dans ce composant unique)
   ═══════════════════════════════════════════════════════════ */
type ClipDTO = {
  id: string;
  dateiname: string;
  quelle: "A" | "B";
  dauer: number | null;
  aufloesung: string | null;
  bildrate: number | null;
  status: string;
  video_url: string | null;
  proxy_url: string | null;
  waveform_url: string | null;
  strip_url: string | null;
};
type TimelineDTO = { id: string; name: string; erstellt_am: string | null };
type ScheneMatch = { scene_id: string; clip_name: string; description: string; similarity: number };

// Same-origin par défaut : Next.js rewrite /api/* → localhost:8001 côté serveur.
// Cela évite les problèmes de CORS + rend le frontend accessible depuis n'importe
// quel appareil du tailnet sans exposer :8001 séparément. Override avec
// NEXT_PUBLIC_API_URL uniquement si besoin d'un backend cross-origin.
const API = process.env.NEXT_PUBLIC_API_URL ?? "";
const abs = (u: string | null | undefined) =>
  u ? (u.startsWith("http") ? u : `${API}${u}`) : null;

/* ═══════════════════════════════════════════════════════════
   Palette + kleine Stil-Bausteine
   ═══════════════════════════════════════════════════════════ */
const chip: CSSProperties = { display: "flex", alignItems: "center", gap: 7, background: "#1a1a1c", borderRadius: 9, height: 36, padding: "0 14px", fontSize: 13, color: "#dcdcdc" };
const sqBtn: CSSProperties = { width: 34, height: 32, borderRadius: 8, background: "#242426", display: "flex", alignItems: "center", justifyContent: "center" };

/* Waveform : ligne de barres verticales — utilisée sur les audioclips */
const wf = (seed: number, n: number) =>
  Array.from({ length: n }, (_, i) => {
    const h = 2 + Math.abs(Math.sin(seed + i * 1.7)) * 14;
    return <rect key={i} x={4 + i * 5} y={10 - h / 2} width={2} height={h} fill="#7fd4c4" />;
  });

/* ═══════════════════════════════════════════════════════════
   Icons (SVG inline, keine externen deps)
   ═══════════════════════════════════════════════════════════ */
type SP = { w?: number; c?: string; sw?: number; children: React.ReactNode; style?: CSSProperties };
const S = (p: SP) => <svg width={p.w || 16} height={p.w || 16} viewBox="0 0 24 24" fill="none" stroke={p.c || "#c9c9c9"} strokeWidth={p.sw || 1.8} strokeLinecap="round" strokeLinejoin="round" style={p.style}>{p.children}</svg>;
const FilmIcon = ({ c = "#9a9a9a" }: { c?: string }) => <S w={11} c={c} sw={1.6}><rect x="3" y="5" width="18" height="14" rx="2" /><path d="M3 9h18M7 5v14" /></S>;
const MusicIcon = ({ w = 10, c = "#cdeee7" }: { w?: number; c?: string }) => <svg width={w} height={w} viewBox="0 0 24 24" fill={c}><path d="M9 18V6l10-2v12" /><circle cx="7" cy="18" r="2.4" /><circle cx="17" cy="16" r="2.4" /></svg>;
// Ketten-Marker auf der Audio-Zelle: intakt = mit Video verknüpft, gebrochen = getrennt.
const ChainIcon = ({ w = 11, c = "#9fd8c8" }: { w?: number; c?: string }) => <S w={w} c={c} sw={2}><path d="M9 17H7A5 5 0 0 1 7 7h2" /><path d="M15 7h2a5 5 0 0 1 0 10h-2" /><path d="M8 12h8" /></S>;
const BrokenChainIcon = ({ w = 11, c = "#e6b168" }: { w?: number; c?: string }) => <S w={w} c={c} sw={2}><path d="M9 17H7A5 5 0 0 1 7 7h1" /><path d="M15 7h2a5 5 0 0 1 0 10h-1" /><path d="M5 5l14 14" /></S>;
const Sparkle = () => <svg width={12} height={12} viewBox="0 0 24 24" fill="#fff"><path d="M12 2l1.8 6.2L20 10l-6.2 1.8L12 18l-1.8-6.2L4 10l6.2-1.8z" /></svg>;
const LinkIcon = () => <S w={9} c="#fff" sw={2}><path d="M9 15l6-6M8 12l-2 2a3 3 0 0 0 4 4l2-2M16 12l2-2a3 3 0 0 0-4-4l-2 2" /></S>;
/* Multi-track header icons */
const EyeIcon = ({ c = "#9a9a9a", off = false }: { c?: string; off?: boolean }) => (
  <S w={14} c={c} sw={1.6}>
    {off
      ? <><path d="M17.9 17.9A10.4 10.4 0 0 1 12 19.5C5 19.5 1 12 1 12a18 18 0 0 1 4.2-5.1M9.9 4.7A10.4 10.4 0 0 1 12 4.5c7 0 11 7.5 11 7.5a18 18 0 0 1-2.2 3.1M9.5 9.5a3 3 0 0 0 4.2 4.2M1 1l22 22" /></>
      : <><path d="M1 12s4-7.5 11-7.5S23 12 23 12s-4 7.5-11 7.5S1 12 1 12z" /><circle cx="12" cy="12" r="3" /></>}
  </S>
);
const LockMini = ({ c = "#9a9a9a" }: { c?: string }) => <S w={13} c={c} sw={1.7}><rect x="4" y="11" width="16" height="10" rx="2" /><path d="M8 11V7a4 4 0 0 1 8 0v4" /></S>;

/* ═══════════════════════════════════════════════════════════
   Pro-Toolbar (Wave 1) — Icon-Buttons 28×28, DaVinci-Stil
   ═══════════════════════════════════════════════════════════ */
const clamp = (v: number, lo: number, hi: number) => Math.max(lo, Math.min(hi, v));
/* Icône outil : SVG 18×18, stroke 1.75, bouts arrondis. */
const TI = ({ c = "#cfcfcf", children }: { c?: string; children: React.ReactNode }) => (
  <svg width={18} height={18} viewBox="0 0 24 24" fill="none" stroke={c} strokeWidth={1.75} strokeLinecap="round" strokeLinejoin="round">{children}</svg>
);
function ToolBtn({ title, onClick, active, disabled, children }: { title: string; onClick?: () => void; active?: boolean; disabled?: boolean; children: React.ReactNode }) {
  return (
    <button
      title={title}
      onClick={onClick}
      disabled={disabled}
      onMouseEnter={(e) => { if (!disabled) e.currentTarget.style.background = "rgba(255,255,255,0.06)"; }}
      onMouseLeave={(e) => { e.currentTarget.style.background = active ? "rgba(229,193,0,0.14)" : "transparent"; }}
      style={{ width: 28, height: 28, borderRadius: 6, display: "flex", alignItems: "center", justifyContent: "center", background: active ? "rgba(229,193,0,0.14)" : "transparent", border: "none", cursor: disabled ? "not-allowed" : "pointer", opacity: disabled ? 0.4 : 1, flex: "none", padding: 0 }}
    >
      {children}
    </button>
  );
}
const ToolDivider = () => <div style={{ width: 1, height: 22, background: "rgba(255,255,255,0.09)", margin: "0 5px", flex: "none" }} />;

/* ═══════════════════════════════════════════════════════════
   Statische Fallbacks (falls Backend offline)
   ═══════════════════════════════════════════════════════════ */
const TABS = [
  { id: "cut", label: "Schnitt" },
  { id: "edit", label: "Bearbeiten" },
  { id: "color", label: "Farbe" },
  { id: "sound", label: "Ton" },
];
const FIT_MODES = ["An Bildschirm anpassen", "50 %", "100 %", "200 %"];

const FALLBACK_GRADS = [
  "linear-gradient(155deg,#c2b291,#6e5f45)", "linear-gradient(155deg,#8fb4c9,#3f5a52)",
  "linear-gradient(155deg,#9fb0a0,#5a4a3a)", "linear-gradient(155deg,#a9c4d0,#4a6b5f)",
  "linear-gradient(155deg,#b06bd6,#3a1c4a)", "linear-gradient(155deg,#7fa0b8,#2e3d33)",
];

/* Format helpers */
const fmtSec = (s: number | null | undefined) => {
  if (s == null || s < 0) return "0:00";
  const m = Math.floor(s / 60);
  const r = Math.floor(s % 60);
  return `${m}:${r.toString().padStart(2, "0")}`;
};
const fmtTC = (s: number | null | undefined, fps = 30) => {
  if (s == null || s < 0) return "00:00:00:00";
  const hh = Math.floor(s / 3600);
  const mm = Math.floor((s % 3600) / 60);
  const ss = Math.floor(s % 60);
  const ff = Math.floor((s - Math.floor(s)) * fps);
  return `${String(hh).padStart(2, "0")}:${String(mm).padStart(2, "0")}:${String(ss).padStart(2, "0")}:${String(ff).padStart(2, "0")}`;
};
// MM:SS.FF — format compact utilisé par l'Inspector (position/durée d'un clip).
const fmtMSF = (s: number | null | undefined, fps = 30): string => {
  if (s == null || s < 0) return "00:00.00";
  let ff = Math.round((s - Math.floor(s)) * fps);
  let total = Math.floor(s);
  if (ff >= fps) { ff -= fps; total += 1; }
  const mm = Math.floor(total / 60);
  const ss = total % 60;
  return `${String(mm).padStart(2, "0")}:${String(ss).padStart(2, "0")}.${String(ff).padStart(2, "0")}`;
};
// Parse "MM:SS.FF" (aussi "SS.FF", "MM:SS", "SS") → secondes, ou null si invalide.
const parseMSF = (raw: string, fps = 30): number | null => {
  const s = (raw ?? "").trim();
  if (!s) return null;
  const m = s.match(/^(?:(\d+):)?(\d{1,2})(?:\.(\d{1,2}))?$/);
  if (!m) return null;
  const mins = m[1] ? parseInt(m[1], 10) : 0;
  const secs = parseInt(m[2], 10);
  const frames = m[3] ? parseInt(m[3].padEnd(2, "0"), 10) : 0;
  if (secs >= 60 || frames >= fps) return null;
  return mins * 60 + secs + frames / fps;
};

/* ═══════════════════════════════════════════════════════════
   Editor Komponente (full-screen, connecté au backend CinAssist)
   ═══════════════════════════════════════════════════════════ */
type TLClip = {
  tlId: string;
  clipId: string;
  name: string;
  start: number;         // Position auf der Timeline (Sekunden)
  duration: number;      // Länge des Segments (Sekunden)
  mediaStart: number;    // Offset im Quellclip (Sekunden) — für Trim/Split korrekt
  sourceDuration: number; // Volle Länge des Quellclips (Sekunden), read-only
  stripUrl: string | null;
  waveformUrl: string | null;
  proxyUrl: string | null;
  videoUrl: string | null;   // Original-Upload — Fallback wenn der Proxy kaputt ist (0-Byte → 416)
  hasAudio: boolean;
  videoTrackIndex?: number;  // Multi-track : auf welcher V-Spur (0 = V1 = oben). Default 0.
  audioTrackIndex?: number;  // Multi-track : auf welcher A-Spur. Default = videoTrackIndex.
  // ── A/V-Verknüpfung ────────────────────────────────────────────────────────
  // Bei hasAudio ist ein Clip standardmäßig verknüpft: `avLinked !== false` = an
  // Video gekoppelt (Audio folgt `start`). `avLinked === false` = getrennt, dann
  // wird die Audio-Seite unabhängig über `audioStart` positioniert.
  avLinked?: boolean;
  linkGroupId?: string;      // gemeinsame ID beider Seiten (für Ketten-Marker)
  audioStart?: number;       // eigene Audio-Position (Sek.) wenn getrennt; sonst = start
  // ── Fades ──────────────────────────────────────────────────────────────────
  fadeIn?: number;           // Länge des Fade-in (Sekunden). Rampe [0→1] auf Opacity + Volume.
  fadeOut?: number;          // Länge des Fade-out (Sekunden). Rampe [1→0].
  fadeInCurve?: number;      // Kurvenfaktor [-1, 1]. 0 = linear, >0 = ease-in, <0 = ease-out.
  fadeOutCurve?: number;     // Kurvenfaktor für Fade-out, gleiche Konvention.
  gainDb?: number;           // Rubber-band-Gain in dB. 0 dB = unity. Range [-24, +12] typisch.
};

/** Per-track UI state (multi-track). Lives outside the engine timeline (golden
 *  rule): visibility/solo/mute/lock are metadata, never on the clock. */
type TrackState = { hidden: boolean; solo: boolean; mute: boolean; locked: boolean; height?: number };
const DEFAULT_TRACK_STATE: TrackState = { hidden: false, solo: false, mute: false, locked: false };
const MAX_TRACKS = 5;
const HEADER_W_DEFAULT = 104;
const HEADER_W_MIN = 68;
const HEADER_W_MAX = 260;

// Per-track resize (remplace les hauteurs group-wide de Wave 3). La hauteur vit
// désormais dans TrackState.height (persistée avec les pistes + miroir versionné
// `cinassist-track-heights-v2`).
const VIDEO_H_DEFAULT = 56, AUDIO_H_DEFAULT = 72;
const VIDEO_H_MIN = 32, VIDEO_H_MAX = 120;
const AUDIO_H_MIN = 24, AUDIO_H_MAX = 120;

/** Élément d'un menu contextuel (clic droit). */
type MenuItem = {
  label?: string; kbd?: string; onClick?: () => void; separator?: boolean; disabled?: boolean;
};

/** Champ texte à validation différée pour l'Inspector : édite un brouillon local,
 *  commit au blur/Enter, Escape annule. `onCommit` valide et applique ; s'il ne
 *  change pas la valeur source (edit invalide), l'affichage revient à `value`. */
function CommitInput({ value, onCommit, style, title, disabled }: {
  value: string;
  onCommit: (raw: string) => void;
  style?: React.CSSProperties;
  title?: string;
  disabled?: boolean;
}) {
  const [draft, setDraft] = useState(value);
  const [editing, setEditing] = useState(false);
  useEffect(() => { if (!editing) setDraft(value); }, [value, editing]);
  return (
    <input
      value={editing ? draft : value}
      disabled={disabled}
      title={title}
      spellCheck={false}
      onFocus={() => { setEditing(true); setDraft(value); }}
      onChange={(e) => setDraft(e.target.value)}
      onBlur={() => { setEditing(false); onCommit(draft); }}
      onKeyDown={(e) => {
        if (e.key === "Enter") { e.currentTarget.blur(); }
        else if (e.key === "Escape") { setDraft(value); setEditing(false); e.currentTarget.blur(); }
      }}
      style={style}
    />
  );
}

export default function Editor() {
  const [clips, setClips] = useState<ClipDTO[]>([]);
  const [tlClips, setTlClips] = useState<TLClip[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  // KI-Agent Chat Panel — piloté par le bouton "KI-Agent" du footer.
  const chatPanelOpen = useChatStore((s) => s.isOpen);
  const toggleChatPanel = useChatStore((s) => s.toggle);
  // `playing` and `globalTime` are no longer React state: they are derived from
  // the playback engine below (isPlaying + currentFrame). The engine's
  // MasterClock is the single source of truth for time (golden rule).
  const [selectedTlIds, setSelectedTlIds] = useState<Set<string>>(new Set());
  const selectedTlId = selectedTlIds.size === 1 ? Array.from(selectedTlIds)[0] : null;
  const [selectedMedia, setSelectedMedia] = useState<Set<string>>(new Set());
  const [tab, setTab] = useState("cut");
  const [fitOpen, setFitOpen] = useState(false);
  const [fitMode, setFitMode] = useState(FIT_MODES[0]);
  const [histOpen, setHistOpen] = useState(false);
  const [timelines, setTimelines] = useState<TimelineDTO[]>([]);
  const [projectName, setProjectName] = useState("Meine Reise 2026");
  const [search, setSearch] = useState("");
  const [searchResults, setSearchResults] = useState<ScheneMatch[]>([]);
  const [showSearch, setShowSearch] = useState(false);
  const [aiOpen, setAiOpen] = useState(false);
  const [aiPrompt, setAiPrompt] = useState("");
  const [aiHistory, setAiHistory] = useState<{ role: "user" | "agent"; content: string }[]>([]);
  const [aiBusy, setAiBusy] = useState(false);
  const [saveStatus, setSaveStatus] = useState<"idle" | "saving" | "saved" | "error">("idle");
  const inspectorPanelRef = useRef<ImperativePanelHandle>(null);
  const [inspectorCollapsed, setInspectorCollapsed] = useState(false);
  // VU meter : Web Audio tap sur le <video> gagnant (celui qui est unmuted).
  // Setup lazily au premier play — AudioContext requires a user gesture.
  const [vu, setVu] = useState<[number, number]>([0, 0]);
  const audioCtxRef = useRef<AudioContext | null>(null);
  const analyserRef = useRef<AnalyserNode | null>(null);
  const audioSourcesRef = useRef(new WeakMap<HTMLVideoElement, MediaElementAudioSourceNode>());
  const [toasts, setToasts] = useState<{ id: number; kind: "ok" | "warn" | "err" | "info"; msg: string }[]>([]);
  const toastIdRef = useRef(0);
  // HUD flottant pendant un edge/roll trim : delta signé + nouvelle durée près du curseur.
  const [trimHud, setTrimHud] = useState<{ x: number; y: number; delta: number; newDuration: number; label?: string } | null>(null);
  // Zoom timeline : 1 = fit to width (default). 2 = 2× zoom = 200%. Range 0.25 – 8.
  const [zoom, setZoom] = useState(1);
  const MIN_ZOOM = 0.25;
  const MAX_ZOOM = 8;

  // Sprint 4 : dropdowns + Werkzeuge + panels
  const [openMenu, setOpenMenu] = useState<string | null>(null);
  const [volume, setVolume] = useState(1);
  const [muted, setMuted] = useState(false);
  const [markers, setMarkers] = useState<{ id: string; time: number; label: string }[]>([]);
  const [inPoint, setInPoint] = useState<number | null>(null);
  const [outPoint, setOutPoint] = useState<number | null>(null);
  const [mediaFilter, setMediaFilter] = useState<"all" | "video" | "audio">("all");
  const [mediaSort, setMediaSort] = useState<"default" | "name" | "duration" | "recent">("default");
  const [mediaView, setMediaView] = useState<"grid" | "list">("grid");
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [clipboard, setClipboard] = useState<TLClip[]>([]);
  // Wave 1 : outils pro-toolbar
  const [tool, setTool] = useState<"select" | "blade">("select"); // curseur actif
  const [snapEnabled, setSnapEnabled] = useState(true);           // aimant ON par défaut (persist localStorage)
  const [lockedTlIds, setLockedTlIds] = useState<Set<string>>(new Set()); // clips verrouillés
  // Menu contextuel DaVinci-style (clic droit sur clip / en-tête / vide)
  const [contextMenu, setContextMenu] = useState<{ x: number; y: number; items: MenuItem[] } | null>(null);
  // Multi-track (Option C) : nombre de pistes V/A + état par piste (persist localStorage `cinassist-tracks`)
  const [numVideoTracks, setNumVideoTracks] = useState(1);
  const [numAudioTracks, setNumAudioTracks] = useState(1);
  const [trackStates, setTrackStates] = useState<Map<string, TrackState>>(new Map());
  // Largeur (px) de la colonne des en-têtes V1/A1... (drag l'edge droit pour resize).
  const [headerW, setHeaderW] = useState<number>(HEADER_W_DEFAULT);
  // Sprint B : Isolation drag ↔ preview (scrub-preview strip pendant le drag)
  const [isDragging, setIsDragging] = useState(false);
  // Refs miroirs lus depuis les closures (scrub, clavier, engine.onError).
  const totalDurationRef = useRef(0);
  const tlClipsRef = useRef<TLClip[]>([]);

  // Proxys défectueux détectés au runtime (0-byte → 416 → DEMUXER_ERROR). On les
  // blackliste et on rebâtit la timeline moteur sur l'original (videoUrl). Ref =
  // pas de re-render ; `brokenTick` force le rebuild du useMemo timeline moteur.
  const brokenProxiesRef = useRef<Set<string>>(new Set());
  const [brokenTick, setBrokenTick] = useState(0);

  const previewContainerRef = useRef<HTMLDivElement | null>(null);
  // Conteneur où le PlaybackEngine monte son pool de <video> (Phase 2).
  const playerContainerRef = useRef<HTMLDivElement | null>(null);
  const timelineRef = useRef<HTMLDivElement | null>(null);
  const vaSeparatorRef = useRef<HTMLDivElement | null>(null);
  const bladeCursorRef = useRef<HTMLDivElement | null>(null);
  const bladeLabelRef = useRef<HTMLDivElement | null>(null);
  // Rubber-band selection : drag dans le vide → sélectionne les clips overlappés
  const [rubberBand, setRubberBand] = useState<{ x1: number; y1: number; x2: number; y2: number } | null>(null);
  // Media drag-and-drop preview : quand l'utilisateur glisse un clip depuis
  // Medien vers la timeline, on affiche un ghost à la position du drop
  // potentiel (largeur = durée du clip, positionné sur la row cible).
  const mediaDragRef = useRef<{ id: string; duration: number; name: string; stripUrl: string | null } | null>(null);
  const [dropPreview, setDropPreview] = useState<{ leftPct: number; widthPct: number; trackIdx: number; name: string; stripUrl: string | null; snapPct: number | null } | null>(null);
  // Wave 2 : conteneur qui reçoit le wheel (Cmd+scroll → zoom). Listener attaché
  // manuellement en { passive: false } car React marque `wheel` comme passif au
  // niveau racine → preventDefault y est ignoré.
  const timelineWheelRef = useRef<HTMLDivElement | null>(null);
  // Multi-track : la colonne d'en-têtes (gauche) suit le scroll vertical de la
  // timeline via un translateY appliqué directement au DOM (pas de state → pas
  // de re-render à chaque tick de scroll).
  const trackHeaderInnerRef = useRef<HTMLDivElement | null>(null);
  const historyRef = useRef<TLClip[][]>([]);
  const redoRef = useRef<TLClip[][]>([]);
  const aiListRef = useRef<HTMLDivElement | null>(null);
  const aiInputRef = useRef<HTMLInputElement | null>(null);

  const toast = (msg: string, kind: "ok" | "warn" | "err" | "info" = "info", ms = 3000) => {
    const id = ++toastIdRef.current;
    setToasts((t) => [...t, { id, kind, msg }]);
    setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), ms);
  };

  // Click-outside handler pour fermer les dropdowns
  useEffect(() => {
    if (!openMenu) return;
    const handler = (e: MouseEvent) => {
      const t = e.target as HTMLElement;
      if (!t.closest("[data-menu]")) setOpenMenu(null);
    };
    // Petit délai pour éviter que le click d'ouverture déclenche la fermeture
    const id = setTimeout(() => window.addEventListener("mousedown", handler), 0);
    return () => { clearTimeout(id); window.removeEventListener("mousedown", handler); };
  }, [openMenu]);

  // Menu contextuel : fermeture au clic extérieur, Escape ou scroll.
  useEffect(() => {
    if (!contextMenu) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") setContextMenu(null); };
    const onDown = (e: MouseEvent) => { if (!(e.target as HTMLElement).closest("[data-context-menu]")) setContextMenu(null); };
    const onScroll = () => setContextMenu(null);
    window.addEventListener("keydown", onKey);
    window.addEventListener("scroll", onScroll, true);
    const id = setTimeout(() => window.addEventListener("mousedown", onDown), 0);
    return () => { window.removeEventListener("keydown", onKey); window.removeEventListener("scroll", onScroll, true); clearTimeout(id); window.removeEventListener("mousedown", onDown); };
  }, [contextMenu]);

  // Scroll-to-bottom + autofocus au drawer KI-Agent
  useEffect(() => {
    if (!aiOpen) return;
    // scroll list to bottom quand un nouveau message arrive
    if (aiListRef.current) aiListRef.current.scrollTop = aiListRef.current.scrollHeight;
    // autofocus input à l'ouverture
    aiInputRef.current?.focus();
  }, [aiOpen, aiHistory, aiBusy]);

  /* ─── Blade mode : flag sur <body> pour override cursor sur tous les
     enfants du timeline (clips inclus) via CSS. */
  useEffect(() => {
    if (tool === "blade") document.body.setAttribute("data-blade", "true");
    else document.body.removeAttribute("data-blade");
    return () => document.body.removeAttribute("data-blade");
  }, [tool]);

  /* ─── Auto-center timeline sur V1/A1 au premier rendu ───
     Le séparateur video/audio doit être centré verticalement dans le viewport.
     On attend un tick pour que le layout soit stable (rows video/audio rendues). */
  useEffect(() => {
    const timer = setTimeout(() => {
      const sep = vaSeparatorRef.current;
      const container = timelineRef.current;
      if (!sep || !container) return;
      const sepTop = sep.getBoundingClientRect().top;
      const containerRect = container.getBoundingClientRect();
      const containerCenterY = containerRect.top + containerRect.height / 2;
      const scrollDelta = sepTop - containerCenterY;
      container.scrollTop = container.scrollTop + scrollDelta;
    }, 150);
    return () => clearTimeout(timer);
  }, []);

  /* ─── Load clips + timelines ─── */
  useEffect(() => {
    let cancelled = false;
    fetch(`${API}/api/clips`)
      .then((r) => r.json())
      .then((data: ClipDTO[]) => {
        if (cancelled) return;
        const usable = data.filter((c) => c.status === "analysiert" || c.status === "hochgeladen");
        setClips(usable);
        let cursor = 0;
        const initial: TLClip[] = usable.map((c, i) => {
          const dur = c.dauer || 0;
          const seg: TLClip = {
            tlId: `${c.id}-${i}`,
            clipId: c.id,
            name: c.dateiname.replace(/\.[^/.]+$/, ""),
            start: cursor,
            duration: dur,
            mediaStart: 0,
            sourceDuration: dur,
            stripUrl: abs(c.strip_url),
            waveformUrl: abs(c.waveform_url),
            proxyUrl: abs(c.proxy_url || c.video_url),
            videoUrl: abs(c.video_url),
            hasAudio: !!c.waveform_url,
          };
          cursor += dur;
          return seg;
        });
        setTlClips(initial);
      })
      .catch((e) => setError((e as Error).message))
      .finally(() => setLoading(false));
    return () => { cancelled = true; };
  }, []);

  useEffect(() => {
    fetch(`${API}/api/timelines`)
      .then((r) => r.json())
      .then((data: TimelineDTO[]) => setTimelines(data || []))
      .catch(() => {});
  }, []);

  // Wave 1 + 3 : lire l'état persisté au montage (localStorage — client only,
  // donc dans un effet pour éviter tout mismatch d'hydratation SSR).
  useEffect(() => {
    try {
      const s = localStorage.getItem("cinassist-snap-v2");
      if (s != null) setSnapEnabled(s === "1");
      const l = localStorage.getItem("cinassist-locked");
      if (l) setLockedTlIds(new Set(JSON.parse(l) as string[]));
      // Multi-track : nombre de pistes + état par piste (dont hauteur par piste)
      const tr = localStorage.getItem("cinassist-tracks");
      let nV = 1, nA = 1;
      let states = new Map<string, TrackState>();
      if (tr) {
        const o = JSON.parse(tr) as { numVideoTracks?: number; numAudioTracks?: number; states?: [string, TrackState][] };
        if (typeof o.numVideoTracks === "number") { nV = clamp(Math.floor(o.numVideoTracks), 1, MAX_TRACKS); setNumVideoTracks(nV); }
        if (typeof o.numAudioTracks === "number") { nA = clamp(Math.floor(o.numAudioTracks), 1, MAX_TRACKS); setNumAudioTracks(nA); }
        if (Array.isArray(o.states)) states = new Map(o.states.map(([k, v]) => [k, { ...DEFAULT_TRACK_STATE, ...v }]));
      }
      // Hauteurs par piste (v2). Sinon migration depuis les clés group-wide Wave 3.
      const setH = (id: string, h: number) => {
        const kind = id[0] === "a" ? "a" : "v";
        const s = { ...(states.get(id) ?? DEFAULT_TRACK_STATE) };
        s.height = clamp(h, kind === "a" ? AUDIO_H_MIN : VIDEO_H_MIN, kind === "a" ? AUDIO_H_MAX : VIDEO_H_MAX);
        states.set(id, s);
      };
      const h2 = localStorage.getItem("cinassist-track-heights-v2");
      if (h2) {
        const hm = JSON.parse(h2) as Record<string, number>;
        for (const [id, h] of Object.entries(hm)) if (typeof h === "number") setH(id, h);
      } else {
        const old = localStorage.getItem("cinassist-track-heights"); // Wave 3 group-wide {video,audio}
        if (old) {
          const o = JSON.parse(old) as { video?: number; audio?: number };
          if (typeof o.video === "number") for (let i = 0; i < nV; i++) setH(`v${i}`, o.video);
          if (typeof o.audio === "number") for (let i = 0; i < nA; i++) setH(`a${i}`, o.audio);
        }
      }
      if (states.size > 0) setTrackStates(states);
      const hw = localStorage.getItem("cinassist-header-w");
      if (hw) {
        const v = parseInt(hw, 10);
        if (isFinite(v)) setHeaderW(clamp(v, HEADER_W_MIN, HEADER_W_MAX));
      }
    } catch { /* localStorage indisponible → valeurs par défaut */ }
  }, []);
  useEffect(() => { try { localStorage.setItem("cinassist-header-w", String(headerW)); } catch {} }, [headerW]);
  useEffect(() => { try { localStorage.setItem("cinassist-snap-v2", snapEnabled ? "1" : "0"); } catch {} }, [snapEnabled]);
  useEffect(() => { try { localStorage.setItem("cinassist-locked", JSON.stringify([...lockedTlIds])); } catch {} }, [lockedTlIds]);
  useEffect(() => { try { const hm: Record<string, number> = {}; for (const [id, s] of trackStates) if (typeof s.height === "number") hm[id] = s.height; localStorage.setItem("cinassist-track-heights-v2", JSON.stringify(hm)); } catch {} }, [trackStates]);
  useEffect(() => { try { localStorage.setItem("cinassist-tracks", JSON.stringify({ numVideoTracks, numAudioTracks, states: [...trackStates] })); } catch {} }, [numVideoTracks, numAudioTracks, trackStates]);

  // Multi-track : la longueur de la séquence est la fin la plus tardive de
  // TOUTES les pistes (max start+duration), pas la somme des durées — sinon les
  // clips répartis sur plusieurs pistes fausseraient le mapping horizontal.
  // Pour une piste unique tuilée bout-à-bout, max(end) == somme des durées.
  const totalDuration = useMemo(() => tlClips.reduce((m, c) => Math.max(m, c.start + c.duration), 0), [tlClips]);

  // ── Playback engine (Phase 2) ─────────────────────────────────────
  // The frame-based timeline (source of truth) is derived from the seconds-based
  // edit model. `brokenTick` forces a rebuild when a defective proxy is
  // blacklisted at runtime so the engine reloads that clip's original.
  const engineTimeline = useMemo<EngineTimeline>(
    () => ({
      fps: PROJECT_FPS,
      tracks: tlClipsToEngineTracks(tlClips, PROJECT_FPS, {
        brokenProxies: brokenProxiesRef.current,
        numVideoTracks,
        numAudioTracks,
      }),
    }),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [tlClips, brokenTick, numVideoTracks, numAudioTracks],
  );
  const { engine, currentFrame, isPlaying, loadingSrcs, play, pause, seek: seekFrame } =
    usePlaybackEngine({ timeline: engineTimeline, containerRef: playerContainerRef });

  // Compat aliases: the existing UI is seconds-based and reads `playing` /
  // `globalTime`. Both now derive from the engine (the single clock). Nothing
  // writes them directly — writes go through play/pause and seekSeconds, which
  // command the engine. Golden rule: <video> follows `t`, never the reverse.
  const playing = isPlaying;
  const togglePlay = () => (isPlaying ? pause() : play());
  const globalTime = framesToSeconds(currentFrame, PROJECT_FPS);
  const seekSeconds = (t: number) => {
    const clamped = Math.max(0, Math.min(totalDurationRef.current, t));
    seekFrame(secondsToFrames(clamped, PROJECT_FPS));
  };

  const activeTlClip = useMemo(
    () => tlClips.find((c) => globalTime >= c.start && globalTime < c.start + c.duration) || null,
    [tlClips, globalTime]
  );

  // Phase 2 : Scrub-Preview — pendant le drag du playhead, on affiche la tuile du
  // strip (_strip.jpg, 24 tuiles de 80×45 sur 1920×45) au lieu de seeker la vidéo.
  // Dérivé de globalTime, qui est DÉJÀ mis à jour à chaque mousemove par beginScrub
  // → aucun state ni re-render supplémentaire (option C). La vidéo ne seek qu'au mouseup.
  const STRIP_TILES = 24;
  const scrubPreview = useMemo(() => {
    if (!isDragging || !activeTlClip?.stripUrl) return null;
    const mediaDur = activeTlClip.sourceDuration > 0 ? activeTlClip.sourceDuration : activeTlClip.duration;
    if (!(mediaDur > 0)) return null;
    const sourcePos = activeTlClip.mediaStart + Math.max(0, globalTime - activeTlClip.start);
    const tile = Math.min(STRIP_TILES - 1, Math.max(0, Math.floor((sourcePos / mediaDur) * STRIP_TILES)));
    // Positionnement CSS en % : 0% = tuile 0, 100% = tuile 23 (stable quel que soit le viewport)
    return { stripUrl: activeTlClip.stripUrl, pctX: (tile / (STRIP_TILES - 1)) * 100 };
  }, [isDragging, activeTlClip, globalTime]);

  // Miroirs refs lus depuis les closures (scrub, clavier, engine.onError).
  useEffect(() => { tlClipsRef.current = tlClips; }, [tlClips]);
  useEffect(() => { totalDurationRef.current = totalDuration; }, [totalDuration]);

  // Broken-proxy fallback, wired through the engine. The pool reports a media
  // error (0-byte proxy → 416 → DEMUXER_ERROR); we blacklist that proxy and
  // bump `brokenTick` so the engine timeline rebuilds with the original
  // videoUrl for that clip. The engine reloads + re-seeks it automatically.
  useEffect(() => {
    if (!engine) return;
    engine.onError = ({ src }) => {
      const clip = tlClipsRef.current.find(
        (c) =>
          (c.proxyUrl && (c.proxyUrl === src || src.endsWith(c.proxyUrl))) ||
          (c.videoUrl && (c.videoUrl === src || src.endsWith(c.videoUrl))),
      );
      const proxy = clip?.proxyUrl ?? null;
      const canFallBack =
        !!clip && !!proxy && (proxy === src || src.endsWith(proxy)) &&
        !!clip.videoUrl && clip.videoUrl !== proxy && !brokenProxiesRef.current.has(proxy);
      if (canFallBack && proxy) {
        brokenProxiesRef.current.add(proxy);
        setBrokenTick((t) => t + 1);
        toast(`Proxy von „${clip!.name}“ defekt — Original wird geladen.`, "warn");
      } else {
        // No usable fallback (or the fallback itself failed) → stop cleanly.
        pause();
        toast(`Video${clip ? ` „${clip.name}“` : ""} kann nicht geladen werden.`, "err");
      }
    };
  // pause is stable (useCallback); toast/brokenProxiesRef read fresh via ref.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [engine]);

  // Stop at the end of the sequence. The MasterClock keeps advancing past the
  // last clip (showing black); we clamp playback to the timeline length,
  // mirroring the old RAF end-of-timeline guard.
  const totalFrames = useMemo(() => secondsToFrames(totalDuration, PROJECT_FPS), [totalDuration]);
  useEffect(() => {
    if (isPlaying && totalFrames > 0 && currentFrame >= totalFrames) {
      pause();
      seekFrame(totalFrames);
    }
  }, [currentFrame, isPlaying, totalFrames, pause, seekFrame]);

  // Multi-track : pousser l'état des pistes dans le moteur. Le compositeur lit
  // hidden/solo sur les pistes VIDEO ; le mute effectif d'une piste vidéo `vi`
  // vient de son homologue audio `ai` (bouton M sur la piste audio). Le solo
  // peut venir de l'une ou l'autre. On reconstruit une Map fraîche à chaque
  // changement → le moteur re-render la frame courante immédiatement.
  useEffect(() => {
    if (!engine) return;
    const map = new Map<string, { hidden?: boolean; solo?: boolean; mute?: boolean }>();
    for (let i = 0; i < numVideoTracks; i++) {
      const v = trackStates.get(`v${i}`);
      const a = trackStates.get(`a${i}`); // piste audio homologue
      map.set(`v${i}`, {
        hidden: !!v?.hidden,
        solo: !!(v?.solo || a?.solo),
        mute: !!a?.mute,
      });
    }
    engine.setTrackStates(map);
  }, [engine, trackStates, numVideoTracks]);

  // Master volume/mute (transport + onglet Ton) → moteur. L'audio provient
  // maintenant de la piste vidéo gagnante ; ce réglage la coupe globalement.
  useEffect(() => {
    engine?.setMasterAudio(volume, muted);
  }, [engine, volume, muted]);

  // VU meter : tap le <video> unmuted via AudioContext + AnalyserNode. Setup
  // lazily au premier play (AudioContext exige un user gesture). RMS sur time-
  // domain → level 0-1 → setVu. Cache 1 MediaElementSource par HTMLVideoElement
  // (createMediaElementSource ne peut être appelé qu'une fois par élément).
  useEffect(() => {
    if (!playing) { setVu([0, 0]); return; }
    const AC = window.AudioContext || (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
    if (!AC) return;
    if (!audioCtxRef.current) {
      audioCtxRef.current = new AC();
      const an = audioCtxRef.current.createAnalyser();
      an.fftSize = 1024;
      an.smoothingTimeConstant = 0.15;
      analyserRef.current = an;
    }
    const ctx = audioCtxRef.current;
    const analyser = analyserRef.current!;
    if (ctx.state === "suspended") ctx.resume().catch(() => {});
    const buf = new Uint8Array(analyser.fftSize);
    let rafId = 0;
    let lastEmit = 0;
    const tick = (ts: number) => {
      const v = engine?.getUnmutedElement?.() ?? null;
      if (v && !audioSourcesRef.current.has(v)) {
        try {
          const src = ctx.createMediaElementSource(v);
          src.connect(analyser);
          analyser.connect(ctx.destination);
          audioSourcesRef.current.set(v, src);
        } catch { /* déjà tapped ou node cross-context */ }
      }
      // Throttle à ~30 fps pour limiter les re-renders React.
      if (ts - lastEmit > 16) {
        lastEmit = ts;
        if (v) {
          analyser.getByteTimeDomainData(buf);
          let sum = 0;
          for (let i = 0; i < buf.length; i++) {
            const s = (buf[i] - 128) / 128;
            sum += s * s;
          }
          const rms = Math.sqrt(sum / buf.length);
          const level = Math.max(0, Math.min(1, rms * 6));
          setVu([level, level]);
        } else {
          setVu([0, 0]);
        }
      }
      rafId = requestAnimationFrame(tick);
    };
    rafId = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(rafId);
  }, [playing, engine]);

  // Auto-scroll : suivre le playhead pendant la lecture
  useEffect(() => {
    if (!playing || !timelineRef.current || totalDuration === 0) return;
    const container = timelineRef.current;
    const inner = container.firstElementChild as HTMLDivElement | null;
    if (!inner) return;
    const padding = 16;
    const playheadX = padding + (globalTime / totalDuration) * inner.offsetWidth;
    const visW = container.clientWidth;
    const visLeft = container.scrollLeft;
    const visRight = visLeft + visW;
    const margin = 60;
    if (playheadX < visLeft + margin) {
      container.scrollLeft = Math.max(0, playheadX - margin);
    } else if (playheadX > visRight - margin) {
      container.scrollLeft = playheadX - visW + margin;
    }
  }, [globalTime, playing, totalDuration]);

  // Wave 2 : Cmd/Ctrl + molette → zoom timeline. Attaché en { passive: false }
  // pour que preventDefault bloque le zoom natif de la page. Sans Cmd/Ctrl, on
  // laisse le scroll normal (retour anticipé, pas de preventDefault).
  useEffect(() => {
    const el = timelineWheelRef.current;
    if (!el) return;
    const onWheel = (e: WheelEvent) => {
      if (!(e.metaKey || e.ctrlKey)) return;
      e.preventDefault();
      const factor = e.deltaY > 0 ? 0.9 : 1.1;
      setZoom((z) => clamp(z * factor, MIN_ZOOM, MAX_ZOOM));
    };
    el.addEventListener("wheel", onWheel, { passive: false });
    return () => el.removeEventListener("wheel", onWheel);
  }, []);

  const timePctFromEvent = (clientX: number) => {
    if (!timelineRef.current || totalDuration === 0) return 0;
    const container = timelineRef.current;
    const inner = container.firstElementChild as HTMLDivElement | null;
    if (!inner) return 0;
    const r = container.getBoundingClientRect();
    const innerX = e_clientToInner(clientX, r.left, container.scrollLeft, 16);
    return Math.min(1, Math.max(0, innerX / inner.offsetWidth));
  };
  const e_clientToInner = (clientX: number, containerLeft: number, scrollLeft: number, padding: number) =>
    (clientX - containerLeft - padding) + scrollLeft;

  const seek = (e: React.MouseEvent) => {
    if (justScrubbedRef.current) return;
    const t = timePctFromEvent(e.clientX) * totalDuration;
    seekSeconds(t);
  };

  // "Just-scrubbed" flag pour empêcher l'onClick container d'écraser la position finale du drag
  const justScrubbedRef = useRef(false);

  // Scrub unifié : mousedown sur playhead OU règle → seek immédiat + drag continu
  // + auto-scroll horizontal quand la souris approche les bords.
  // isDragging=true → on affiche la scrub-preview (strip) au lieu de la vidéo ;
  // chaque position pilote engine.seek(). L'engine (correction de drift) recale
  // le <video> — on n'écrit JAMAIS video.currentTime ici (règle d'or).
  const beginScrub = (e: React.MouseEvent, seekOnDown = true) => {
    e.stopPropagation();
    e.preventDefault();
    const wasPlaying = playing;
    if (wasPlaying) pause();
    setIsDragging(true);
    // lastT = position courante du drag (secondes), source de vérité locale de la
    // closure — le state React peut être en retard d'un render.
    let lastT = seekOnDown ? timePctFromEvent(e.clientX) * totalDurationRef.current : globalTime;
    if (seekOnDown) seekSeconds(lastT);

    let edgeDir = 0; // -1 = scroll left, 0 = none, +1 = scroll right
    let stopped = false;             // ← empêche le leak RAF : garde en fin de tick
    let rafId: number | null = null;
    const autoScrollTick = () => {
      if (stopped) return;
      if (edgeDir !== 0 && timelineRef.current) {
        timelineRef.current.scrollLeft += edgeDir * 12;
        const inner = timelineRef.current.firstElementChild as HTMLDivElement | null;
        const total = totalDurationRef.current;
        if (inner && inner.offsetWidth > 0 && total > 0) {
          lastT = Math.max(0, Math.min(total, lastT + edgeDir * 12 / inner.offsetWidth * total));
          seekSeconds(lastT);
        }
      }
      rafId = requestAnimationFrame(autoScrollTick);
    };
    rafId = requestAnimationFrame(autoScrollTick);

    const onMove = (ev: MouseEvent) => {
      // Fix leak : si aucun bouton n'est enfoncé, le mouseup a été raté (relâché
      // hors fenêtre — dock/menu bar/autre app). On termine le drag proprement au
      // lieu de continuer à scruber en silence à chaque mouvement de souris.
      if (ev.buttons === 0) { onUp(); return; }
      lastT = timePctFromEvent(ev.clientX) * totalDurationRef.current;
      seekSeconds(lastT);
      // Scrub audio : kick une lecture de 80 ms sur l'active <video> après le
      // seek. Sans ce kick le playback est en pause pendant le drag → aucun son.
      engine?.scrubAudioKick(80);
      if (timelineRef.current) {
        const r = timelineRef.current.getBoundingClientRect();
        const edge = 40;
        if (ev.clientX > r.right - edge) edgeDir = 1;
        else if (ev.clientX < r.left + edge) edgeDir = -1;
        else edgeDir = 0;
      }
    };
    const onUp = () => {
      if (stopped) return; // idempotent : appelable via mouseup, blur ou garde ev.buttons===0
      stopped = true;
      if (rafId != null) cancelAnimationFrame(rafId);
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("mouseup", onUp);
      window.removeEventListener("blur", onUp);
      // Seek final à la position relâchée. L'engine (correction de drift, branche
      // « recalage systématique » à l'arrêt) positionne le <video> sur la frame
      // exacte. Puis reprise de la lecture si on jouait avant le drag.
      seekSeconds(Math.max(0, Math.min(totalDurationRef.current, lastT)));
      setIsDragging(false);
      // Fix #7 : empêche le click du container de fire seek() à la position mouseup et d'écraser le drag
      justScrubbedRef.current = true;
      setTimeout(() => { justScrubbedRef.current = false; }, 0);
      if (wasPlaying) play();
    };
    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
    // Fix leak : si la fenêtre perd le focus pendant le drag (Cmd+Tab, clic sur
    // une autre app), on force la fin du drag — sinon les listeners fuitent.
    window.addEventListener("blur", onUp);
  };
  // Alias pour compat avec startPlayheadDrag existant
  const startPlayheadDrag = (e: React.MouseEvent) => beginScrub(e, false);

  // Drag d'un bracket In ou Out sur la ruler — repositionne le mark en suivant
  // la souris. `stopPropagation` pour ne pas déclencher le scrub de la ruler.
  const beginBracketDrag = (e: React.MouseEvent, kind: "in" | "out") => {
    e.stopPropagation();
    e.preventDefault();
    const otherIn = inPoint;
    const otherOut = outPoint;
    const onMove = (ev: MouseEvent) => {
      const total = totalDurationRef.current;
      if (total <= 0) return;
      const t = Math.max(0, Math.min(total, timePctFromEvent(ev.clientX) * total));
      if (kind === "in") {
        setInPoint(otherOut !== null && t >= otherOut ? Math.max(0, otherOut - 0.1) : t);
      } else {
        setOutPoint(otherIn !== null && t <= otherIn ? Math.min(total, otherIn + 0.1) : t);
      }
    };
    const onUp = () => {
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("mouseup", onUp);
      window.removeEventListener("blur", onUp);
    };
    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
    window.addEventListener("blur", onUp);
  };

  // Mousedown sur la ruler : gère 3 cas.
  //   1. Pas de range set OU click dans le range shaded → scrub normal.
  //   2. Click HORS range shaded, range set, sans mouvement → clear range.
  //   3. Click HORS range shaded, range set, avec mouvement → bascule vers scrub.
  const beginRulerMouseDown = (e: React.MouseEvent) => {
    const hasRange = inPoint !== null && outPoint !== null;
    if (!hasRange || totalDuration <= 0) { beginScrub(e, true); return; }
    const t = timePctFromEvent(e.clientX) * totalDuration;
    const insideRange = t >= inPoint! && t <= outPoint!;
    if (insideRange) { beginScrub(e, true); return; }
    // Hors range shaded → on attend : clic simple = clear, drag = scrub.
    e.preventDefault();
    const startX = e.clientX;
    const startY = e.clientY;
    const originalEvent = e;
    let handled = false;
    const onMove = (ev: MouseEvent) => {
      if (handled) return;
      if (Math.abs(ev.clientX - startX) > 3 || Math.abs(ev.clientY - startY) > 3) {
        handled = true;
        window.removeEventListener("mousemove", onMove);
        window.removeEventListener("mouseup", onUp);
        beginScrub(originalEvent, true);
      }
    };
    const onUp = () => {
      if (handled) return;
      handled = true;
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("mouseup", onUp);
      setInPoint(null);
      setOutPoint(null);
      toast("In/Out gelöscht.", "ok", 1000);
    };
    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
  };

  // Fix #8 : listener clavier attaché UNE SEULE FOIS. Toutes les fonctions mutantes sont lues via ref
  // pour éviter de ré-attacher le listener 60×/sec pendant lecture (globalTime dans les deps).
  const kbActionsRef = useRef<{
    undo: () => void; redo: () => void; removeSelected: () => void;
    removeSelectedRipple: () => void;
    splitAtGlobalTime: () => void; addMarkerAtPlayhead: () => void;
    toggleFullscreen: () => void; zoomIn: () => void; zoomOut: () => void; zoomFit: () => void;
    togglePlay: () => void; seekBy: (delta: number) => void; seekTo: (t: number) => void;
    fillGapAt: (trackIdx: number, clickTime: number) => boolean;
    fillGapAtPlayhead: () => void;
    setInAtPlayhead: () => void; setOutAtPlayhead: () => void;
    clearInPoint: () => void; clearOutPoint: () => void;
    splitAtInOut: () => void; zoomToRange: () => void;
  }>({
    undo: () => {}, redo: () => {}, removeSelected: () => {},
    removeSelectedRipple: () => {},
    splitAtGlobalTime: () => {}, addMarkerAtPlayhead: () => {},
    toggleFullscreen: () => {}, zoomIn: () => {}, zoomOut: () => {}, zoomFit: () => {},
    togglePlay: () => {}, seekBy: () => {}, seekTo: () => {},
    fillGapAt: () => false,
    fillGapAtPlayhead: () => {},
    setInAtPlayhead: () => {}, setOutAtPlayhead: () => {},
    clearInPoint: () => {}, clearOutPoint: () => {},
    splitAtInOut: () => {}, zoomToRange: () => {},
  });
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const target = e.target as HTMLElement;
      if (target && (target.tagName === "INPUT" || target.tagName === "TEXTAREA" || target.isContentEditable)) return;
      const A = kbActionsRef.current;
      const total = totalDurationRef.current;
      if (e.code === "Space") { e.preventDefault(); A.togglePlay(); }
      else if (e.code === "ArrowLeft") { e.preventDefault(); A.seekBy(-(e.shiftKey ? 5 : 1)); }
      else if (e.code === "ArrowRight") { e.preventDefault(); A.seekBy(e.shiftKey ? 5 : 1); }
      else if (e.code === "Home") { e.preventDefault(); A.seekTo(0); }
      else if (e.code === "End") { e.preventDefault(); A.seekTo(total); }
      else if ((e.metaKey || e.ctrlKey) && e.shiftKey && (e.key === "z" || e.key === "Z")) { e.preventDefault(); A.redo(); }
      else if ((e.metaKey || e.ctrlKey) && (e.key === "z" || e.key === "Z")) { e.preventDefault(); A.undo(); }
      else if ((e.metaKey || e.ctrlKey) && (e.key === "y" || e.key === "Y")) { e.preventDefault(); A.redo(); }
      else if ((e.metaKey || e.ctrlKey) && (e.code === "Equal" || e.code === "NumpadAdd")) { e.preventDefault(); A.zoomIn(); }
      else if ((e.metaKey || e.ctrlKey) && (e.code === "Minus" || e.code === "NumpadSubtract")) { e.preventDefault(); A.zoomOut(); }
      else if ((e.metaKey || e.ctrlKey) && e.code === "Digit0") { e.preventDefault(); A.zoomFit(); }
      else if ((e.code === "Delete" || e.code === "Backspace") && e.shiftKey) { e.preventDefault(); A.removeSelectedRipple(); }
      else if (e.code === "Delete" || e.code === "Backspace") { e.preventDefault(); A.removeSelected(); }
      else if (e.code === "KeyC" && e.shiftKey) { e.preventDefault(); A.splitAtInOut(); }
      else if ((e.code === "KeyC" || e.code === "KeyS") && !e.shiftKey) { e.preventDefault(); A.splitAtGlobalTime(); }
      else if (e.code === "KeyM") { e.preventDefault(); A.addMarkerAtPlayhead(); }
      else if (e.code === "KeyF") { e.preventDefault(); A.toggleFullscreen(); }
      else if (e.code === "KeyI" && e.shiftKey) { e.preventDefault(); A.clearInPoint(); }
      else if (e.code === "KeyO" && e.shiftKey) { e.preventDefault(); A.clearOutPoint(); }
      else if (e.code === "KeyI" && !e.metaKey && !e.ctrlKey && !e.altKey) { e.preventDefault(); A.setInAtPlayhead(); }
      else if (e.code === "KeyO" && !e.metaKey && !e.ctrlKey && !e.altKey) { e.preventDefault(); A.setOutAtPlayhead(); }
      else if (e.code === "Slash" && e.shiftKey) { e.preventDefault(); A.zoomToRange(); }
      else if (e.code === "Escape" && tool === "blade") { e.preventDefault(); setTool("select"); }
      else if ((e.metaKey || e.ctrlKey) && !e.shiftKey && (e.key === "g" || e.key === "G")) {
        e.preventDefault();
        A.fillGapAtPlayhead();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  // Batch mode : quand vrai, snapshot() est un no-op → le batch entier occupe
  // UN seul undo step (posé par executeBatch au début). Utilisé par l'agent IA
  // et par tout consommateur du TimelineCommandExecutor.
  const batchModeRef = useRef(false);
  const snapshot = () => {
    if (batchModeRef.current) return;
    historyRef.current.push(tlClips);
    if (historyRef.current.length > 30) historyRef.current.shift();
    // Nouvelle action = redo buffer invalidé
    redoRef.current = [];
  };
  const undo = () => {
    const prev = historyRef.current.pop();
    if (prev) {
      redoRef.current.push(tlClips);
      if (redoRef.current.length > 30) redoRef.current.shift();
      setTlClips(prev);
    }
  };
  const redo = () => {
    const next = redoRef.current.pop();
    if (next) {
      historyRef.current.push(tlClips);
      if (historyRef.current.length > 30) historyRef.current.shift();
      setTlClips(next);
    }
  };

  // Wave 1 : verrouillage. Un clip verrouillé ne peut être ni coupé, ni trimmé,
  // ni supprimé, ni dupliqué, ni déplacé (glissé). Les opérations filtrent les
  // ids présents dans `lockedTlIds`.
  const isLocked = (id: string) => lockedTlIds.has(id);

  // ── Multi-track helpers ────────────────────────────────────────────
  // Smart-drop era: positions are EXPLICIT (each clip owns its `start`). The old
  // forced sequential tiling is gone — it fought positional drops and re-flowed
  // the whole timeline on every edit. `reflow` is now a pass-through so existing
  // callers (delete/trim/move) keep clip starts intact instead of re-tiling.
  const reflow = (arr: TLClip[]): TLClip[] => arr;

  // Append NEW clips at the tail of their own video track, preserving every
  // existing clip's position. Guarantees no same-track overlap (normalize would
  // THROW otherwise) for flows that have no drop position: double-click / import
  // / paste / duplicate.
  const appendTails = (existing: TLClip[], incoming: TLClip[]): TLClip[] => {
    const tail: Record<number, number> = {};
    for (const c of existing) {
      const ti = c.videoTrackIndex ?? 0;
      tail[ti] = Math.max(tail[ti] ?? 0, c.start + c.duration);
    }
    const placed = incoming.map((c) => {
      const ti = c.videoTrackIndex ?? 0;
      const start = tail[ti] ?? 0;
      tail[ti] = start + c.duration;
      return { ...c, start };
    });
    return [...existing, ...placed];
  };
  const trackState = (id: string): TrackState => trackStates.get(id) ?? DEFAULT_TRACK_STATE;
  // Mini toggle button style used inside the left track-header column.
  const hdrBtnStyle = (active: boolean, activeColor = "#e5c100"): CSSProperties => ({
    width: 20, height: 18, borderRadius: 4, display: "flex", alignItems: "center", justifyContent: "center",
    border: "none", background: active ? "rgba(229,193,0,0.18)" : "transparent",
    color: active ? activeColor : "#8a8a8a", cursor: "pointer", padding: 0, flex: "none", fontSize: 10, fontWeight: 700,
  });
  const setTrackFlag = (id: string, key: keyof TrackState, val: boolean) =>
    setTrackStates((prev) => {
      const n = new Map(prev);
      const s = { ...(n.get(id) ?? DEFAULT_TRACK_STATE) };
      (s as Record<string, unknown>)[key] = val;
      n.set(id, s);
      return n;
    });
  const toggleTrackFlag = (id: string, key: "hidden" | "solo" | "mute" | "locked") =>
    setTrackFlag(id, key, !trackState(id)[key]);
  // A clip is effectively locked if its own tlId is locked OR its video track is.
  const clipLocked = (c: TLClip) => isLocked(c.tlId) || trackState(`v${c.videoTrackIndex ?? 0}`).locked;

  // ── Per-track resize (#2) ──────────────────────────────────────────
  // Hauteur effective d'une piste (défaut + bornes selon le type v/a).
  const trackH = (id: string, kind: "v" | "a") => clamp(
    trackState(id).height ?? (kind === "v" ? VIDEO_H_DEFAULT : AUDIO_H_DEFAULT),
    kind === "v" ? VIDEO_H_MIN : AUDIO_H_MIN,
    kind === "v" ? VIDEO_H_MAX : AUDIO_H_MAX,
  );
  const setTrackHeight = (id: string, h: number) => setTrackStates((prev) => {
    const n = new Map(prev);
    const s = { ...(n.get(id) ?? DEFAULT_TRACK_STATE) };
    s.height = h;
    n.set(id, s);
    return n;
  });
  // Drag du bord inférieur d'une piste → met à jour la hauteur de CETTE piste.
  const beginTrackResize = (e: React.MouseEvent, id: string, kind: "v" | "a") => {
    e.preventDefault(); e.stopPropagation();
    const startY = e.clientY;
    const startH = trackH(id, kind);
    const min = kind === "v" ? VIDEO_H_MIN : AUDIO_H_MIN;
    const max = kind === "v" ? VIDEO_H_MAX : AUDIO_H_MAX;
    const onMove = (ev: MouseEvent) => setTrackHeight(id, clamp(startH + (ev.clientY - startY), min, max));
    const onUp = () => { window.removeEventListener("mousemove", onMove); window.removeEventListener("mouseup", onUp); };
    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
  };
  // Poignée absolue au bas d'une rangée (n'altère pas la hauteur de flux → aligne
  // avec la colonne d'en-têtes qui utilise la même hauteur par piste).
  const trackResizeHandle = (id: string, kind: "v" | "a") => (
    <div
      title="Spurhöhe ziehen"
      onMouseDown={(e) => beginTrackResize(e, id, kind)}
      onClick={(e) => e.stopPropagation()}
      onMouseEnter={(e) => (e.currentTarget.style.background = "#444")}
      onMouseLeave={(e) => (e.currentTarget.style.background = "transparent")}
      style={{ position: "absolute", left: 0, right: 0, bottom: -2, height: 5, cursor: "row-resize", background: "transparent", zIndex: 8 }}
    />
  );

  const addVideoTrack = () => {
    if (numVideoTracks >= MAX_TRACKS) { toast(`Maximal ${MAX_TRACKS} Videospuren.`, "warn", 1500); return; }
    setNumVideoTracks((n) => n + 1);
    toast(`Videospur V${numVideoTracks + 1} hinzugefügt.`, "ok", 1400);
  };
  const addAudioTrack = () => {
    if (numAudioTracks >= MAX_TRACKS) { toast(`Maximal ${MAX_TRACKS} Audiospuren.`, "warn", 1500); return; }
    setNumAudioTracks((n) => n + 1);
    toast(`Audiospur A${numAudioTracks + 1} hinzugefügt.`, "ok", 1400);
  };
  // Remove an EMPTY track by index and reindex higher tracks + their states
  // down by one. Removing an empty track can never introduce clip overlap.
  const shiftStatesDown = (prev: Map<string, TrackState>, prefix: "v" | "a", removed: number, count: number) => {
    const n = new Map(prev);
    n.delete(`${prefix}${removed}`);
    for (let k = removed + 1; k < count; k++) {
      const s = n.get(`${prefix}${k}`);
      n.delete(`${prefix}${k}`);
      if (s) n.set(`${prefix}${k - 1}`, s);
    }
    return n;
  };
  const removeVideoTrack = (i: number) => {
    if (numVideoTracks <= 1) { toast("Mindestens eine Videospur nötig.", "warn", 1500); return; }
    if (tlClips.some((c) => (c.videoTrackIndex ?? 0) === i)) { toast("Spur nicht leer — Clips zuerst verschieben.", "warn", 2000); return; }
    snapshot();
    setTlClips((cur) => reflow(cur.map((c) => {
      const v = c.videoTrackIndex ?? 0;
      const a = c.audioTrackIndex ?? v;
      return { ...c, videoTrackIndex: v > i ? v - 1 : v, audioTrackIndex: a > i ? a - 1 : a };
    })));
    setTrackStates((prev) => shiftStatesDown(prev, "v", i, numVideoTracks));
    setNumVideoTracks((n) => n - 1);
    toast(`Videospur V${i + 1} entfernt.`, "ok", 1400);
  };
  const removeAudioTrack = (i: number) => {
    if (numAudioTracks <= 1) { toast("Mindestens eine Audiospur nötig.", "warn", 1500); return; }
    if (tlClips.some((c) => c.hasAudio && (c.audioTrackIndex ?? c.videoTrackIndex ?? 0) === i)) { toast("Spur nicht leer — Clips zuerst verschieben.", "warn", 2000); return; }
    snapshot();
    setTlClips((cur) => cur.map((c) => {
      const a = c.audioTrackIndex ?? c.videoTrackIndex ?? 0;
      return a > i ? { ...c, audioTrackIndex: a - 1 } : c;
    }));
    setTrackStates((prev) => shiftStatesDown(prev, "a", i, numAudioTracks));
    setNumAudioTracks((n) => n - 1);
    toast(`Audiospur A${i + 1} entfernt.`, "ok", 1400);
  };
  // Reorder a clip among its OWN track's clips by drop position (seconds).
  const reorderWithinTrack = (tlId: string, ti: number, dropTime: number) => {
    const clip = tlClips.find((c) => c.tlId === tlId);
    if (!clip || clipLocked(clip)) { toast("Clip ist gesperrt.", "warn", 1500); return; }
    snapshot();
    setTlClips((cur) => {
      const mates = cur.filter((c) => (c.videoTrackIndex ?? 0) === ti);
      const others = cur.filter((c) => (c.videoTrackIndex ?? 0) !== ti);
      const idx = mates.findIndex((c) => c.tlId === tlId);
      if (idx === -1) return cur;
      const [moved] = mates.splice(idx, 1);
      let target = mates.findIndex((c) => dropTime < c.start + c.duration / 2);
      if (target === -1) target = mates.length;
      mates.splice(target, 0, moved);
      // Global array order only matters within a track (reflow tiles per track).
      return reflow([...others, ...mates]);
    });
  };
  // Drop an existing timeline clip onto video track `vIdx` (keeps audio mirrored).
  const moveClipToTrack = (tlId: string, vIdx: number) => {
    if (trackState(`v${vIdx}`).locked) { toast(`V${vIdx + 1} ist gesperrt.`, "warn", 1500); return; }
    const clip = tlClips.find((c) => c.tlId === tlId);
    if (!clip || clipLocked(clip)) { toast("Clip ist gesperrt.", "warn", 1500); return; }
    if ((clip.videoTrackIndex ?? 0) === vIdx) return;
    snapshot();
    setTlClips((cur) => reflow(cur.map((c) => c.tlId === tlId ? { ...c, videoTrackIndex: vIdx, audioTrackIndex: vIdx } : c)));
  };

  // ── Smart drop (collision-aware placement + track escalation) ──────────
  // Live refs to each video row DOM node → the outer/global drop handler can find
  // the nearest row by Y when a drop lands between rows (ruler, gaps, below).
  const videoRowRefs = useRef<Map<number, HTMLDivElement>>(new Map());
  const audioRowRefs = useRef<Map<number, HTMLDivElement>>(new Map());
  const nearestVideoTrack = (clientY: number): number => {
    let best = 0;
    let bestDist = Infinity;
    videoRowRefs.current.forEach((el, ti) => {
      const r = el.getBoundingClientRect();
      const dist = clientY < r.top ? r.top - clientY : clientY > r.bottom ? clientY - r.bottom : 0;
      if (dist < bestDist) { bestDist = dist; best = ti; }
    });
    return best;
  };
  const nearestAudioTrack = (clientY: number): number => {
    let best = 0;
    let bestDist = Infinity;
    audioRowRefs.current.forEach((el, ti) => {
      const r = el.getBoundingClientRect();
      const dist = clientY < r.top ? r.top - clientY : clientY > r.bottom ? clientY - r.bottom : 0;
      if (dist < bestDist) { bestDist = dist; best = ti; }
    });
    return best;
  };

  // THE ONLY place drop state churns. Fires ONCE on onDrop — NEVER call from
  // onDragOver (60 fps → re-render storm → the browser crash we are avoiding).
  // Places a NEW media clip or MOVES an existing TL clip at `dropTime` on
  // `intendedVideoTrack`; escalates to the next free V-track (adding one within
  // MAX_TRACKS) on collision; routes audio INDEPENDENTLY. `insertNew: "above"`
  // (directional, new clips only) inserts a fresh V-track above the row when it
  // is occupied at that time; otherwise placement is normal.
  const smartDrop = (opts: {
    media?: string;
    tlId?: string;
    intendedVideoTrack: number;
    dropTime: number;
    insertNew?: "above" | "below";
    // A/V-link move mode (mouse-drag only):
    //   "sync" → audioStart follows the new start (linked drag).
    //   "keep" → audio time + track preserved, only the video moves (unlinked V).
    audioFollow?: "sync" | "keep";
  }) => {
    const { intendedVideoTrack, dropTime, insertNew } = opts;
    const start = Math.max(0, dropTime);
    const ignoreTlId = opts.tlId;

    // Resolve what is being placed.
    let duration: number;
    let hasAudio: boolean;
    let buildNewClip: ((s: number, vIdx: number, aIdx: number) => TLClip) | null = null;

    if (opts.tlId) {
      const moving = tlClips.find((c) => c.tlId === opts.tlId);
      if (!moving) return;
      if (clipLocked(moving)) { toast("Clip ist gesperrt.", "warn", 1500); return; }
      duration = moving.duration;
      // "keep": only the video moves → don't route/collide the audio side.
      hasAudio = opts.audioFollow === "keep" ? false : moving.hasAudio;
    } else if (opts.media) {
      const src = clips.find((c) => c.id === opts.media);
      if (!src) return;
      const dur = src.dauer || 0;
      duration = dur;
      hasAudio = !!src.waveform_url;
      buildNewClip = (s, vIdx, aIdx) => ({
        tlId: `${opts.media}-${Date.now()}`,
        clipId: opts.media!,
        name: src.dateiname.replace(/\.[^/.]+$/, ""),
        start: s,
        duration: dur,
        mediaStart: 0,
        sourceDuration: dur,
        stripUrl: abs(src.strip_url),
        waveformUrl: abs(src.waveform_url),
        proxyUrl: abs(src.proxy_url || src.video_url),
        videoUrl: abs(src.video_url),
        hasAudio: !!src.waveform_url,
        videoTrackIndex: vIdx,
        audioTrackIndex: aIdx,
      });
    } else {
      return;
    }

    // ── Task 3: directional insert-ABOVE (new media clips only). Only kicks in
    // when the intended row is actually occupied at this time — otherwise a plain
    // placement is less surprising. ─────────────────────────────────────────
    if (insertNew === "above" && buildNewClip) {
      const collides = hasCollision(tlClips, intendedVideoTrack, "video", start, duration, ignoreTlId);
      if (collides) {
        if (numVideoTracks >= MAX_TRACKS) { toast("Kein Platz — bitte Zeit ändern.", "warn", 2500); return; }
        // Audio routes on the CURRENT (unshifted) audio layout.
        let aIdx = intendedVideoTrack;
        let addA = false;
        if (hasAudio) {
          const free = findFreeTrack(tlClips, "audio", start, duration, MAX_TRACKS, numAudioTracks, ignoreTlId, intendedVideoTrack);
          if (free === null) { toast("Kein Platz — bitte Zeit ändern.", "warn", 2500); return; }
          if (free === "add") { aIdx = numAudioTracks; addA = true; } else { aIdx = free; }
        }
        const newV = intendedVideoTrack;
        snapshot();
        setNumVideoTracks((n) => Math.min(MAX_TRACKS, n + 1));
        if (addA) setNumAudioTracks((n) => Math.min(MAX_TRACKS, n + 1));
        setTlClips((cur) => {
          // Shift clips at/below newV down one V-track; PIN their audio to its old
          // effective index so inserting a video track never moves audio rows.
          const shifted = cur.map((c) => {
            const cv = c.videoTrackIndex ?? 0;
            return cv >= newV
              ? { ...c, videoTrackIndex: cv + 1, audioTrackIndex: c.audioTrackIndex ?? cv }
              : c;
          });
          return [...shifted, buildNewClip!(start, newV, aIdx)];
        });
        toast(`Neue Videospur V${newV + 1} eingefügt.`, "ok", 1400);
        return;
      }
      // no collision → fall through to normal placement on the intended track
    }

    // ── Normal placement / escalation (Task 2) ───────────────────────────────
    // Si intendedVideoTrack >= numVideoTracks → création explicite d'une nouvelle
    // piste (drag sous la dernière row). Sinon, si la piste souhaitée est
    // libre → on l'utilise. Si elle a une collision, on préfère créer une
    // nouvelle piste au-dessus plutôt que de descendre sur une piste libre en
    // dessous (comportement DaVinci — le user attend son clip sur SA piste ou
    // au pire sur une nouvelle, pas sur une piste où il ne l'a pas mis).
    const outOfRange = intendedVideoTrack >= numVideoTracks && numVideoTracks < MAX_TRACKS;
    const wantedCollides = !outOfRange && intendedVideoTrack < numVideoTracks &&
      hasCollision(tlClips, intendedVideoTrack, "video", start, duration, ignoreTlId);
    const requestNewTrack = (outOfRange || wantedCollides) && numVideoTracks < MAX_TRACKS;
    const effectiveIntendedTrack = requestNewTrack ? numVideoTracks : intendedVideoTrack;
    let plan = planPlacement({
      clips: tlClips,
      intendedVideoTrack: effectiveIntendedTrack,
      dropTime: start,
      duration,
      hasAudio,
      numVideoTracks,
      numAudioTracks,
      maxTracks: MAX_TRACKS,
      ignoreTlId,
    });
    if (requestNewTrack && plan.ok) {
      plan = { ...plan, videoTrackIndex: numVideoTracks, addVideoTrack: true };
    }
    // Fallback : si l'audio empêche le drop (aucune piste audio libre pour
    // suivre le vidéo), on tente le drop en gardant l'audio à sa place actuelle
    // — le vidéo bouge quand même, l'audio se dé-lie automatiquement. Prévient
    // l'utilisateur pour qu'il puisse repositionner l'audio manuellement.
    let audioDetached = false;
    if (!plan.ok && hasAudio && opts.tlId) {
      plan = planPlacement({
        clips: tlClips,
        intendedVideoTrack,
        dropTime: start,
        duration,
        hasAudio: false,          // ignore la contrainte audio
        numVideoTracks,
        numAudioTracks,
        maxTracks: MAX_TRACKS,
        ignoreTlId,
      });
      if (plan.ok) {
        audioDetached = true;
      }
    }
    if (!plan.ok) {
      toast(`Kein Platz — ${plan.reason ?? "Platz belegt"}. Alt-Taste beim Drag deaktiviert das Snap.`, "warn", 3500);
      return;
    }
    if (trackState(`v${plan.videoTrackIndex}`).locked) { toast(`V${plan.videoTrackIndex + 1} ist gesperrt.`, "warn", 1500); return; }

    snapshot();
    if (plan.addVideoTrack) setNumVideoTracks((n) => Math.min(MAX_TRACKS, n + 1));
    if (plan.addAudioTrack) setNumAudioTracks((n) => Math.min(MAX_TRACKS, n + 1));
    setTlClips((cur) => {
      if (opts.tlId) {
        return cur.map((c) => {
          if (c.tlId !== opts.tlId) return c;
          if (opts.audioFollow === "keep" || audioDetached) {
            // Video repositions, audio (time + track) untouched. Si audio détaché
            // par fallback, on flag le clip comme unlinked pour cohérence.
            const patch: Partial<TLClip> = { start: plan.start, videoTrackIndex: plan.videoTrackIndex };
            if (audioDetached) {
              patch.avLinked = false;
              patch.audioStart = c.audioStart ?? c.start; // fige l'audio à sa position actuelle
            }
            return { ...c, ...patch };
          }
          const next = { ...c, start: plan.start, videoTrackIndex: plan.videoTrackIndex, audioTrackIndex: plan.audioTrackIndex };
          if (opts.audioFollow === "sync") next.audioStart = plan.start; // linked: audio tracks start
          return next;
        });
      }
      return [...cur, buildNewClip!(plan.start, plan.videoTrackIndex, plan.audioTrackIndex)];
    });
    if (audioDetached) toast("Audio-Spur konnte nicht folgen — Audio bleibt an alter Position (jetzt entkoppelt).", "warn", 3500);
  };

  // ── Fluid clip drag (custom mouse-based, replaces HTML5 draggable) ────────
  // Goal: DaVinci/Premiere-feel horizontal glide. During the drag we move the
  // clip element with a CSS `transform: translateX(px)` ONLY — ZERO setState per
  // mousemove, so there is no per-frame React re-render. mousemove is throttled
  // to one RAF tick. A SINGLE setState happens on mouseup (via smartDrop), which
  // reuses the exact same collision/escalation/reject logic as HTML5 drops.
  const clipDragRef = useRef<{
    tlId: string;
    el: HTMLDivElement;
    kind: "v" | "a";          // which row is being grabbed (video or audio side)
    linked: boolean;          // A/V linked at grab time → the sibling follows
    siblingEl: HTMLDivElement | null; // paired cell (audio when kind="v", else video) to glide together
    startClipStart: number;   // grabbed side's position at grab time (seconds)
    startTrackIdx: number;    // video track at grab time
    duration: number;
    grabDx: number;           // px from clip left edge to cursor at grab
    grabDy: number;           // px from clip top edge to cursor at grab
    startX: number; startY: number; // pointer at mousedown (click-vs-drag test)
    lastT: number;            // latest snapped start (seconds)
    hoverTrack: number;       // video row currently under the cursor
    hoverAudioTrack: number;  // audio row currently under the cursor (kind="a" only)
    engaged: boolean;         // crossed the 4px threshold → it is a real drag
    // Snap targets computed ONCE at drag start, already in priority order
    // (playhead → markers → clip left edges → clip right edges → t=0).
    snapCandidates: { time: number; label: string }[];
  } | null>(null);
  const clipRafRef = useRef<number | null>(null);
  // Auto-scroll (edge-driven) RAF id — active only during a clip drag.
  const edgeScrollRafRef = useRef<number | null>(null);
  const clipPendingEvt = useRef<{ clientX: number; clientY: number; alt: boolean } | null>(null);
  // Single DOM element for the yellow snap guide line + its tooltip label.
  // Driven ONLY via ref + inline style during drag (zero React re-renders).
  const snapGuideRef = useRef<HTMLDivElement | null>(null);
  const snapGuideLabelRef = useRef<HTMLDivElement | null>(null);
  // HUD "distance to snap" (DaVinci-style) : badge "-00:03" positionné à mi-chemin
  // entre l'edge draggée et le candidat le plus proche. Rayon plus large que le
  // snap magnétique lui-même (SNAP_HINT_PX > SNAP_TOLERANCE_PX).
  const snapDistanceRef = useRef<HTMLDivElement | null>(null);
  // Suppresses the click that browsers fire right after a mouseup so a drag does
  // not also toggle selection / seek.
  const justDraggedClipRef = useRef(false);

  const snapFrame = (t: number) => Math.round(t * PROJECT_FPS) / PROJECT_FPS;

  // Format DaVinci-like : "±SS:FF" — secondes:frames, signé.
  const fmtDeltaFrames = (sec: number): string => {
    const frames = Math.round(Math.abs(sec) * PROJECT_FPS);
    const s = Math.floor(frames / PROJECT_FPS);
    const f = frames % PROJECT_FPS;
    const sign = sec < -1e-6 ? "-" : sec > 1e-6 ? "+" : "";
    return `${sign}${String(s).padStart(2, "0")}:${String(f).padStart(2, "0")}`;
  };
  // Rayon en px screen dans lequel on affiche le HUD "distance to snap" (plus
  // large que SNAP_TOLERANCE_PX qui déclenche le magnetism réel).
  const SNAP_HINT_PX = 200;

  const clearClipDropHints = () => {
    videoRowRefs.current.forEach((el) => el.classList.remove("drop-above", "drop-below", "drop-target"));
  };

  // Runs once per RAF tick — reads the latest pointer position, moves the DOM via
  // transform, and updates the directional hint. NO React state touched here.
  const flushClipMove = () => {
    clipRafRef.current = null;
    const d = clipDragRef.current;
    const p = clipPendingEvt.current;
    if (!d || !p) return;
    const row = d.el.parentElement as HTMLDivElement | null;
    const rowRect = row ? row.getBoundingClientRect() : null;
    const total = totalDurationRef.current;
    if (!rowRect || rowRect.width <= 0 || total <= 0) return;

    const rawT = ((p.clientX - rowRect.left - d.grabDx) / rowRect.width) * total;
    let t = Math.max(0, snapFrame(rawT));

    // Vertical track change only when grabbing the VIDEO row. Audio-row drags are
    // horizontal-only (time): the video never changes track from an audio grab.
    // On utilise le CENTRE VISUEL du clip (pas le curseur brut) pour matcher la
    // piste. Avec le smooth translateY, l'utilisateur suit le clip des yeux —
    // il attend que le drop soit sur la piste où le CLIP est visuellement, pas
    // sur celle où son curseur est.
    // Position visuelle réelle du clip (avec transform translateY appliqué).
    const clipRect = d.el.getBoundingClientRect();
    // Sensibilité maximale : utilise l'EDGE du clip dans la direction de drag.
    // Drag up → clipTop pur (dès que le haut du clip touche la row du dessus,
    // on switch). Drag down → clipBottom. Ainsi le moindre mouvement Y qui
    // fait déborder le clip d'une piste voisine déclenche le switch.
    const dyFromStart = p.clientY - d.startY;
    const probeY = dyFromStart < -2
      ? clipRect.top                    // drag up
      : dyFromStart > 2
        ? clipRect.bottom               // drag down
        : clipRect.top + clipRect.height / 2;
    // Détecte si l'utilisateur drag sous la DERNIÈRE piste vidéo → signale la
    // création d'une nouvelle piste (index = numVideoTracks) au smartDrop.
    let hoverTi = d.kind === "v" ? nearestVideoTrack(probeY) : d.startTrackIdx;
    if (d.kind === "a") {
      // Audio row grab → track vertical la row AUDIO (indépendant du vidéo)
      d.hoverAudioTrack = nearestAudioTrack(probeY);
    }
    if (d.kind === "v" && numVideoTracks < MAX_TRACKS) {
      // Ordre visuel DaVinci : V(n) en HAUT, V1 en bas. La piste la plus haute
      // est ti = numVideoTracks-1. Drag au-DESSUS d'elle = créer V(n+1) au top.
      const topmostRow = videoRowRefs.current.get(numVideoTracks - 1);
      if (topmostRow) {
        const topEdge = topmostRow.getBoundingClientRect().top;
        const clipH = d.el.offsetHeight;
        if (probeY < topEdge - clipH * 0.5) {
          hoverTi = numVideoTracks;
        }
      }
    }

    // ── DaVinci-style snap guides ─────────────────────────────────────────
    // Deux zones :
    //   • MAGNET (≤ SNAP_TOLERANCE_PX, 10px) : le clip s'aimante ET la guide
    //     line devient VERTE épaisse avec glow → "OK, ça va coller".
    //   • APPROCHE (≤ SNAP_HINT_PX, 80px) : guide line JAUNE fine + badge
    //     chiffré → "tu es proche, encore X frames".
    // Alt/Option → tout désactivé.
    const guide = snapGuideRef.current;
    const pxPerSec = rowRect.width / total;
    let activeGuidePx: number | null = null;
    let activeLabel = "";
    let magnetActive = false;
    if (snapEnabled && !p.alt) {
      const leftPx = t * pxPerSec;                 // dragged clip LEFT edge, px
      const rightPx = (t + d.duration) * pxPerSec; // dragged clip RIGHT edge, px
      let bestMagnet = SNAP_TOLERANCE_PX + 0.001;  // magnet actif (aimant réel)
      let bestHint = SNAP_HINT_PX + 0.001;         // zone d'approche (feedback seul)
      let snapped = t;
      let hintCand: { time: number; label: string } | null = null;
      // Candidates are already ordered by priority; strict "<" keeps the first
      // (highest-priority) candidate on an exact-distance tie.
      for (const cand of d.snapCandidates) {
        const candPx = cand.time * pxPerSec;
        const dLeft = Math.abs(candPx - leftPx);
        const dRight = Math.abs(candPx - rightPx);
        // Magnet (10px) : applique le snap
        if (dLeft < bestMagnet) { bestMagnet = dLeft; snapped = cand.time; activeGuidePx = candPx; activeLabel = cand.label; magnetActive = true; }
        if (dRight < bestMagnet) { bestMagnet = dRight; snapped = cand.time - d.duration; activeGuidePx = candPx; activeLabel = cand.label; magnetActive = true; }
        // Hint (80px) : juste feedback visuel — priorité au plus proche
        const minEdge = Math.min(dLeft, dRight);
        if (minEdge < bestHint) { bestHint = minEdge; hintCand = cand; }
      }
      // Quand un magnet a snappé exactement à un candidat, on GARDE la valeur
      // exacte du candidat (pas de snapFrame qui réarrondirait à une frame,
      // créant un décalage de ~10-20ms qui provoque de fausses collisions).
      t = magnetActive ? Math.max(0, snapped) : Math.max(0, snapFrame(snapped));
      // Si pas de magnet mais on est en zone d'approche → montre le hint candidate
      if (!magnetActive && hintCand) {
        activeGuidePx = hintCand.time * pxPerSec;
        activeLabel = hintCand.label;
      }
    }

    // Update the guide line purely via the DOM — no setState in mousemove.
    // Style DaVinci : ligne fine BLANCHE PÂLE — subtile mais toujours visible.
    // Quand snap actif (magnet) : la ligne devient plus opaque + glow léger.
    if (guide) {
      if (activeGuidePx != null) {
        guide.style.transform = `translateX(${activeGuidePx}px)`;
        guide.style.display = "block";
        if (magnetActive) {
          guide.style.width = "1px";
          guide.style.background = "rgba(255,255,255,0.95)";
          guide.style.boxShadow = "0 0 6px rgba(255,255,255,0.9)";
        } else {
          guide.style.width = "1px";
          guide.style.background = "rgba(255,255,255,0.55)";
          guide.style.boxShadow = "none";
        }
        if (snapGuideLabelRef.current) {
          snapGuideLabelRef.current.textContent = activeLabel;
          snapGuideLabelRef.current.style.background = magnetActive ? "#ffffff" : "rgba(255,255,255,0.75)";
          snapGuideLabelRef.current.style.color = "#111";
        }
      } else {
        guide.style.display = "none";
      }
    }

    // ── HUD "distance to snap" (DaVinci-style) ────────────────────────────
    // Calcule la distance signée entre l'edge draggée et le candidat le plus
    // proche dans SNAP_HINT_PX (plus large que le magnet). Positionne le badge
    // à mi-chemin entre les deux edges, affiche "±SS:FF". Alt désactive.
    const hint = snapDistanceRef.current;
    if (hint) {
      let hintDeltaSec: number | null = null;
      let hintMidPx: number | null = null;
      if (!p.alt) {
        const leftPxHint = t * pxPerSec;
        const rightPxHint = (t + d.duration) * pxPerSec;
        let bestHintPx = SNAP_HINT_PX + 0.001;
        for (const cand of d.snapCandidates) {
          const candPx = cand.time * pxPerSec;
          const dLeft = candPx - leftPxHint;
          if (Math.abs(dLeft) < bestHintPx) { bestHintPx = Math.abs(dLeft); hintDeltaSec = dLeft / pxPerSec; hintMidPx = (candPx + leftPxHint) / 2; }
          const dRight = candPx - rightPxHint;
          if (Math.abs(dRight) < bestHintPx) { bestHintPx = Math.abs(dRight); hintDeltaSec = dRight / pxPerSec; hintMidPx = (candPx + rightPxHint) / 2; }
        }
      }
      if (hintDeltaSec != null && hintMidPx != null) {
        hint.style.transform = `translateX(${hintMidPx}px)`;
        hint.style.display = "block";
        hint.textContent = fmtDeltaFrames(hintDeltaSec);
        // Vert quand snap actif (delta ≈ 0), gris sinon
        const snapped = activeGuidePx != null && Math.abs(hintDeltaSec) < 1 / PROJECT_FPS;
        hint.style.background = snapped ? "#6ad04a" : "#1a1a1a";
        hint.style.color = snapped ? "#0a1a05" : "#e0e0e0";
      } else {
        hint.style.display = "none";
      }
    }

    d.lastT = t;
    const dxPx = ((t - d.startClipStart) / total) * rowRect.width;
    // ── Smooth translation X+Y (DaVinci-style) : le clip suit le curseur en
    // horizontal (X snappé au frame) ET en vertical (dy brut depuis start).
    // Le layout DOM n'est pas modifié — le clip reste dans sa row d'origine,
    // on triche juste le rendu via transform. Au mouseup, smartDrop pose le
    // clip sur la piste finale (calculée depuis hoverTi = nearestVideoTrack).
    // Le clip suit verticalement le curseur pour v ET a (audio peut aussi
    // changer de piste). Le sibling ne suit que horizontalement (l'autre côté
    // du couple A/V reste sur sa row d'origine).
    const dyPx = p.clientY - d.startY;
    d.el.style.transform = `translate(${dxPx}px, ${dyPx}px)`;
    if (d.siblingEl) d.siblingEl.style.transform = `translateX(${dxPx}px)`;
    // Multi-clip drag : applique la même translation à tous les clips sélectionnés
    // (sauf celui déjà dragué). Le rendu suit visuellement l'ensemble du groupe.
    if (d.kind === "v" && selectedTlIds.has(d.tlId) && selectedTlIds.size > 1) {
      selectedTlIds.forEach((tlId) => {
        if (tlId === d.tlId) return;
        document.querySelectorAll(`[data-avtlid="${tlId}"]`).forEach((node) => {
          (node as HTMLElement).style.transform = `translate(${dxPx}px, ${dyPx}px)`;
        });
      });
    }

    // Directional hint (yellow line) only when hovering a DIFFERENT video track.
    // Audio-row drags never re-track, so skip the vertical hint entirely.
    if (d.kind === "v") {
      if (hoverTi !== d.hoverTrack) { clearClipDropHints(); d.hoverTrack = hoverTi; }
      // Piste cible → highlight complet (2 lignes horizontales + bg léger).
      // Toujours affiché même si c'est la piste d'origine → feedback constant.
      const targetRow = videoRowRefs.current.get(hoverTi);
      if (targetRow) targetRow.classList.add("drop-target");
      if (hoverTi !== d.startTrackIdx) {
        const hrow = videoRowRefs.current.get(hoverTi);
        if (hrow) {
          const rr = hrow.getBoundingClientRect();
          const above = (p.clientY - rr.top) < rr.height / 2;
          hrow.classList.toggle("drop-above", above);
          hrow.classList.toggle("drop-below", !above);
        }
      }
    }
  };

  // Shared teardown: detach listeners, cancel RAF, undo the visual glide. Returns
  // the drag record (or null) so callers decide whether to commit or cancel.
  const endClipDrag = () => {
    const d = clipDragRef.current;
    clipDragRef.current = null;
    window.removeEventListener("mousemove", onClipDragMove);
    window.removeEventListener("mouseup", onClipDragUp);
    window.removeEventListener("blur", onClipDragCancel);
    if (clipRafRef.current != null) { cancelAnimationFrame(clipRafRef.current); clipRafRef.current = null; }
    if (edgeScrollRafRef.current != null) { cancelAnimationFrame(edgeScrollRafRef.current); edgeScrollRafRef.current = null; }
    clipPendingEvt.current = null;
    document.body.style.cursor = "";
    clearClipDropHints();
    if (snapGuideRef.current) snapGuideRef.current.style.display = "none"; // hide guide line
    if (snapDistanceRef.current) snapDistanceRef.current.style.display = "none"; // hide distance HUD
    if (d) {
      d.el.style.transform = ""; d.el.style.zIndex = "";
      if (d.siblingEl) { d.siblingEl.style.transform = ""; d.siblingEl.style.zIndex = ""; }
      // Clean transforms sur tous les clips du groupe (multi-drag)
      if (d.kind === "v" && selectedTlIds.has(d.tlId) && selectedTlIds.size > 1) {
        selectedTlIds.forEach((tlId) => {
          if (tlId === d.tlId) return;
          document.querySelectorAll(`[data-avtlid="${tlId}"]`).forEach((node) => {
            const el = node as HTMLElement;
            el.style.transform = ""; el.style.zIndex = "";
          });
        });
      }
    }
    return d;
  };

  const onClipDragUp = () => {
    const d = endClipDrag();
    if (!d || !d.engaged) return; // no drag / never crossed threshold → let onClick run
    // Suppress the trailing click so the drag does not also re-select / seek.
    justDraggedClipRef.current = true;
    setTimeout(() => { justDraggedClipRef.current = false; }, 0);
    if (d.kind === "a" && !d.linked) {
      // Unlinked audio drag → bouge audioStart ET la piste audio (Y).
      snapshot();
      setTlClips((cur) => cur.map((c) => c.tlId === d.tlId
        ? { ...c, audioStart: d.lastT, audioTrackIndex: d.hoverAudioTrack }
        : c));
      return;
    }
    // Multi-clip group drag : si le clip dragué fait partie d'une sélection
    // multiple, on applique le même delta (temps + piste) à tous les clips
    // sélectionnés. Vérifie les collisions AVANT commit — si le groupe overlappe
    // un clip externe, on rollback et affiche un toast.
    if (d.kind === "v" && selectedTlIds.has(d.tlId) && selectedTlIds.size > 1) {
      const deltaTime = d.lastT - d.startClipStart;
      const deltaTrack = d.hoverTrack - d.startTrackIdx;
      const groupClips = tlClips.filter((c) => selectedTlIds.has(c.tlId));
      // Calcule les nouvelles positions du groupe
      const newPositions = groupClips.map((c) => ({
        tlId: c.tlId,
        newStart: Math.max(0, c.start + deltaTime),
        newVTrack: Math.max(0, Math.min(MAX_TRACKS - 1, (c.videoTrackIndex ?? 0) + deltaTrack)),
        duration: c.duration,
        hasAudio: c.hasAudio,
        audioTrackIndex: c.audioTrackIndex,
      }));
      // Vérifie collisions vs clips EXTERNES (pas dans le groupe)
      const EPS = 0.034;
      const externals = tlClips.filter((c) => !selectedTlIds.has(c.tlId));
      const collides = newPositions.some((np) => externals.some((ec) => {
        if ((ec.videoTrackIndex ?? 0) !== np.newVTrack) return false;
        return np.newStart + EPS < ec.start + ec.duration && ec.start + EPS < np.newStart + np.duration;
      }));
      if (collides) {
        toast("Kollision — der Gruppe konnte nicht platziert werden. Alt-Taste deaktiviert das Snap.", "warn", 3500);
        return;
      }
      snapshot();
      const maxNewTrack = Math.max(...newPositions.map((np) => np.newVTrack));
      if (maxNewTrack >= numVideoTracks && maxNewTrack < MAX_TRACKS) {
        setNumVideoTracks(maxNewTrack + 1);
      }
      const posMap = new Map(newPositions.map((np) => [np.tlId, np]));
      setTlClips((cur) => cur.map((c) => {
        const np = posMap.get(c.tlId);
        if (!np) return c;
        return {
          ...c,
          start: np.newStart,
          videoTrackIndex: np.newVTrack,
          audioStart: c.avLinked !== false ? np.newStart : (c.audioStart ?? c.start),
        };
      }));
      return;
    }
    // Single-clip video drag OR linked audio drag → move the whole clip via smartDrop.
    const audioFollow: "sync" | "keep" = d.linked ? "sync" : "keep";
    const intendedVideoTrack = d.kind === "a" ? d.startTrackIdx : d.hoverTrack;
    smartDrop({ tlId: d.tlId, intendedVideoTrack, dropTime: d.lastT, audioFollow });
  };

  // Edge auto-scroll: while a clip drag is active and the cursor sits within
  // EDGE_ZONE_PX of the scroll container's left/right edge, scroll that way at a
  // speed proportional to how deep the cursor is in the zone. Runs on its own RAF
  // loop for the whole drag; reads the live cursor X via clipPendingEvt (ref). No
  // setState here — after each scroll we re-run flushClipMove so the dragged clip
  // (which is positioned from the freshly-read row rect) stays under the cursor.
  const EDGE_ZONE_PX = 40;
  const EDGE_MAX_SPEED = 24; // px per frame
  const startEdgeAutoScroll = (container: HTMLDivElement) => {
    if (edgeScrollRafRef.current != null) return;                 // already running
    if (container.scrollWidth <= container.clientWidth) return;   // nothing to scroll
    const step = () => {
      const d = clipDragRef.current;
      const p = clipPendingEvt.current;
      if (!d || !p) { edgeScrollRafRef.current = null; return; }  // drag ended
      const rect = container.getBoundingClientRect();
      const distLeft = p.clientX - rect.left;
      const distRight = rect.right - p.clientX;
      let dir = 0, intrusion = 0;
      if (distLeft < EDGE_ZONE_PX) { dir = -1; intrusion = EDGE_ZONE_PX - distLeft; }
      else if (distRight < EDGE_ZONE_PX) { dir = 1; intrusion = EDGE_ZONE_PX - distRight; }
      if (dir !== 0) {
        const speed = Math.min(EDGE_MAX_SPEED, Math.max(1, (intrusion / EDGE_ZONE_PX) * EDGE_MAX_SPEED));
        const before = container.scrollLeft;
        container.scrollLeft = before + dir * speed;
        // Only recompute if the scroll actually changed (clamped at either end).
        if (container.scrollLeft !== before) flushClipMove();
      }
      edgeScrollRafRef.current = requestAnimationFrame(step);
    };
    edgeScrollRafRef.current = requestAnimationFrame(step);
  };

  // Window blur mid-drag → cancel and revert: teardown already restores the clip
  // to its original left% (we never mutated state during the glide).
  const onClipDragCancel = () => { endClipDrag(); };

  const onClipDragMove = (ev: MouseEvent) => {
    const d = clipDragRef.current;
    if (!d) return;
    if (ev.buttons === 0) { onClipDragUp(); return; } // missed mouseup (released off-window)
    if (!d.engaged) {
      if (Math.hypot(ev.clientX - d.startX, ev.clientY - d.startY) < 4) return; // still a click
      d.engaged = true;
      document.body.style.cursor = "grabbing";
      d.el.style.zIndex = "20";
      if (d.siblingEl) d.siblingEl.style.zIndex = "20";
    }
    clipPendingEvt.current = { clientX: ev.clientX, clientY: ev.clientY, alt: ev.altKey };
    if (clipRafRef.current == null) clipRafRef.current = requestAnimationFrame(flushClipMove);
  };

  const beginClipDrag = (e: React.MouseEvent<HTMLDivElement>, clip: TLClip, kind: "v" | "a" = "v") => {
    if (e.button !== 0) return;        // left button only (right = context menu)
    if (clipLocked(clip)) return;      // locked clips never drag (selection click still works)
    const el = e.currentTarget;
    const clipRect = el.getBoundingClientRect();
    const linked = clip.hasAudio && clip.avLinked !== false;
    // The grabbed side's current position: audio uses audioStart when unlinked.
    const grabStart = kind === "a" ? audioStartOf(clip) : clip.start;
    // Linked → glide the paired cell too (audio when grabbing video, vice versa).
    const sibRow = kind === "v" ? "a" : "v";
    const siblingEl = linked
      ? (document.querySelector(`[data-avtlid="${clip.tlId}"][data-avrow="${sibRow}"]`) as HTMLDivElement | null)
      : null;
    // Snap targets are computed ONCE, here at drag start: nothing on the
    // timeline moves (from state's perspective) until mouseup, so the target
    // set is fixed for the whole drag. Order = priority (earlier wins ties).
    const snapCandidates: { time: number; label: string }[] = [];
    snapCandidates.push({ time: globalTime, label: `Abspielkopf · ${fmtTC(globalTime)}` });         // 1 playhead
    for (const m of markers) snapCandidates.push({ time: m.time, label: `Marker: ${m.label} · ${fmtTC(m.time)}` }); // 2 markers
    for (const c of tlClips) {                                                                        // 3 other clips' LEFT edges
      if (c.tlId === clip.tlId) continue;
      snapCandidates.push({ time: c.start, label: `→ ${c.name || "Clip"} (Start) · ${fmtTC(c.start)}` });
    }
    for (const c of tlClips) {                                                                        // 4 other clips' RIGHT edges
      if (c.tlId === clip.tlId) continue;
      snapCandidates.push({ time: c.start + c.duration, label: `${c.name || "Clip"} (Ende) ← · ${fmtTC(c.start + c.duration)}` });
    }
    snapCandidates.push({ time: 0, label: `Start · ${fmtTC(0)}` });                                   // 5 timeline start
    // 6 : grille 5s (0:05, 0:10, 0:15…) — permet des cuts alignés aux 5 secondes
    for (let t = 5; t < totalDuration; t += 5) snapCandidates.push({ time: t, label: `${fmtTC(t)}` });
    clipDragRef.current = {
      tlId: clip.tlId,
      el,
      kind,
      linked,
      siblingEl,
      startClipStart: grabStart,
      startTrackIdx: clip.videoTrackIndex ?? 0,
      duration: clip.duration,
      grabDx: e.clientX - clipRect.left,
      grabDy: e.clientY - clipRect.top,
      startX: e.clientX,
      startY: e.clientY,
      lastT: grabStart,
      hoverTrack: clip.videoTrackIndex ?? 0,
      hoverAudioTrack: clip.audioTrackIndex ?? clip.videoTrackIndex ?? 0,
      engaged: false,
      snapCandidates,
    };
    if (timelineRef.current) startEdgeAutoScroll(timelineRef.current);
    window.addEventListener("mousemove", onClipDragMove);
    window.addEventListener("mouseup", onClipDragUp);
    window.addEventListener("blur", onClipDragCancel); // window focus loss → cancel (revert)
  };

  const toggleLockSelected = () => {
    if (selectedTlIds.size === 0) { toast("Kein Clip ausgewählt.", "warn"); return; }
    let locked = 0, unlocked = 0;
    setLockedTlIds((cur) => {
      const n = new Set(cur);
      selectedTlIds.forEach((id) => { if (n.has(id)) { n.delete(id); unlocked++; } else { n.add(id); locked++; } });
      return n;
    });
    toast(locked && !unlocked ? `${locked} Clip(s) gesperrt.` : unlocked && !locked ? `${unlocked} Clip(s) entsperrt.` : "Sperre umgeschaltet.", "ok", 1600);
  };

  // A/V trennen: die Audio-Seite wird von der Video-Seite gelöst und kann danach
  // unabhängig gezogen werden (eigenes audioStart, geteilte linkGroupId).
  const unlinkAV = (tlId: string) => {
    const c = tlClips.find((x) => x.tlId === tlId);
    if (!c || !c.hasAudio || c.avLinked === false) return;
    if (clipLocked(c)) { toast("Clip ist gesperrt.", "warn", 1500); return; }
    snapshot();
    setTlClips((cur) => cur.map((x) => x.tlId === tlId
      ? { ...x, avLinked: false, linkGroupId: x.linkGroupId ?? genLinkId(), audioStart: x.audioStart ?? x.start }
      : x));
    toast("Audio von Video getrennt.", "ok", 1600);
  };
  // A/V verknüpfen: Audio richtet sich wieder nach start aus (audioStart entfällt).
  const linkAV = (tlId: string) => {
    const c = tlClips.find((x) => x.tlId === tlId);
    if (!c || c.avLinked !== false) return;
    if (clipLocked(c)) { toast("Clip ist gesperrt.", "warn", 1500); return; }
    snapshot();
    setTlClips((cur) => cur.map((x) => x.tlId === tlId
      ? { ...x, avLinked: true, audioStart: undefined }
      : x));
    toast("Audio mit Video verknüpft.", "ok", 1600);
  };

  // ── Inspector : édition de propriétés d'un clip (commit + snapshot/undo) ──────
  // Patch d'un clip unique. `snapshot()` avant mutation → l'undo/redo fonctionne.
  const patchClip = (tlId: string, patch: Partial<TLClip>) => {
    snapshot();
    setTlClips((cur) => cur.map((c) => (c.tlId === tlId ? { ...c, ...patch } : c)));
  };
  // A/V verknüpfen / trennen pour toute la sélection (uniquement clips hasAudio).
  const setAvLinkedSelected = (linked: boolean) => {
    const targets = tlClips.filter(
      (c) => selectedTlIds.has(c.tlId) && c.hasAudio && !clipLocked(c) && (c.avLinked !== false) !== linked
    );
    if (targets.length === 0) { toast("Nichts zu ändern.", "warn", 1400); return; }
    const ids = new Set(targets.map((c) => c.tlId));
    snapshot();
    setTlClips((cur) => cur.map((c) => {
      if (!ids.has(c.tlId)) return c;
      return linked
        ? { ...c, avLinked: true, audioStart: undefined }
        : { ...c, avLinked: false, linkGroupId: c.linkGroupId ?? genLinkId(), audioStart: c.audioStart ?? c.start };
    }));
    toast(linked ? `${targets.length} Clip(s) verknüpft.` : `${targets.length} Clip(s) getrennt.`, "ok", 1500);
  };

  const removeSelected = () => {
    if (selectedTlIds.size === 0) { toast("Kein Clip ausgewählt.", "warn"); return; }
    const removable = new Set([...selectedTlIds].filter((id) => !isLocked(id)));
    if (removable.size === 0) { toast("Ausgewählte Clips sind gesperrt.", "warn"); return; }
    const n = removable.size;
    snapshot();
    setTlClips((cur) => reflow(cur.filter((c) => !removable.has(c.tlId))));
    setSelectedTlIds(new Set());
    toast(`${n} Clip${n > 1 ? "s" : ""} entfernt.`, "ok", 1800);
  };

  // Ripple delete : enlève les clips sélectionnés ET décale à gauche tous les
  // clips restants qui viennent APRÈS chaque suppression, sur la même piste
  // (vidéo ET audio si le clip a de l'audio). Chaîne les shifts si plusieurs
  // clips sont supprimés dans l'ordre chronologique.
  const removeSelectedRipple = () => {
    if (selectedTlIds.size === 0) { toast("Kein Clip ausgewählt.", "warn"); return; }
    const removable = new Set([...selectedTlIds].filter((id) => !isLocked(id)));
    if (removable.size === 0) { toast("Ausgewählte Clips sind gesperrt.", "warn"); return; }
    const n = removable.size;
    snapshot();
    setTlClips((cur) => {
      const removed = cur.filter((c) => removable.has(c.tlId)).sort((a, b) => a.start - b.start);
      let working = cur.filter((c) => !removable.has(c.tlId));
      for (const r of removed) {
        const rvt = r.videoTrackIndex ?? 0;
        const rat = r.hasAudio ? (r.audioTrackIndex ?? rvt) : null;
        const cutEnd = r.start + r.duration;
        working = working.map((c) => {
          const cvt = c.videoTrackIndex ?? 0;
          const cat = c.hasAudio ? (c.audioTrackIndex ?? cvt) : null;
          const sameV = cvt === rvt;
          const sameA = rat !== null && cat !== null && cat === rat;
          if ((sameV || sameA) && c.start >= cutEnd - 0.001) {
            return { ...c, start: Math.max(0, c.start - r.duration) };
          }
          return c;
        });
      }
      return working;
    });
    setSelectedTlIds(new Set());
    toast(`${n} Clip${n > 1 ? "s" : ""} rippled entfernt.`, "ok", 1800);
  };

  const clickTlClip = (tlId: string, e: React.MouseEvent, seekStart: number) => {
    e.stopPropagation();
    // A drag just ended: the browser fires a trailing click — ignore it so the
    // drag does not also toggle selection / seek.
    if (justDraggedClipRef.current) return;
    const additive = e.metaKey || e.ctrlKey || e.shiftKey;
    setSelectedTlIds((cur) => {
      const n = new Set(additive ? cur : []);
      if (additive && n.has(tlId)) n.delete(tlId);
      else n.add(tlId);
      return n;
    });
    if (!additive) seekSeconds(seekStart);
  };

  const reorderClip = (tlId: string, targetIdx: number) => {
    if (isLocked(tlId)) { toast("Clip ist gesperrt.", "warn", 1500); return; }
    snapshot();
    setTlClips((cur) => {
      const idx = cur.findIndex((c) => c.tlId === tlId);
      if (idx === -1 || idx === targetIdx) return cur;
      const copy = [...cur];
      const [moved] = copy.splice(idx, 1);
      const adjusted = targetIdx > idx ? targetIdx - 1 : targetIdx;
      copy.splice(Math.max(0, Math.min(copy.length, adjusted)), 0, moved);
      return reflow(copy);
    });
  };

  // Coupe tous les clips qui overlappent un time donné (peut être appelé depuis
  // le blade mode : clic n'importe où sur la timeline → coupe à cet endroit).
  const splitAtTime = (t: number) => {
    const idx = tlClips.findIndex((c) => t > c.start && t < c.start + c.duration);
    if (idx === -1) { toast("Kein Clip an dieser Position.", "warn", 1200); return; }
    const clip = tlClips[idx];
    if (isLocked(clip.tlId)) { toast("Clip ist gesperrt.", "warn", 1200); return; }
    const cutLocal = t - clip.start;
    if (cutLocal < 0.05 || cutLocal > clip.duration - 0.05) {
      toast("Zu nah am Clip-Rand zum Schneiden.", "warn", 1200);
      return;
    }
    snapshot();
    setTlClips((cur) => {
      const copy = [...cur];
      const linked = clip.avLinked !== false;
      const before: TLClip = {
        ...clip, duration: cutLocal,
        avLinked: linked ? true : false,
        audioStart: linked ? undefined : clip.start,
      };
      const afterStart = clip.start + cutLocal;
      const after: TLClip = {
        ...clip,
        tlId: `${clip.clipId}-${Date.now()}-b`,
        start: afterStart,
        mediaStart: clip.mediaStart + cutLocal,
        duration: clip.duration - cutLocal,
        avLinked: linked ? true : false,
        audioStart: linked ? undefined : afterStart,
      };
      copy.splice(idx, 1, before, after);
      return copy;
    });
  };

  const splitAtGlobalTime = () => {
    const idx = tlClips.findIndex((c) => globalTime > c.start && globalTime < c.start + c.duration);
    if (idx === -1) { toast("Kein Clip am Abspielkopf.", "warn"); return; }
    const clip = tlClips[idx];
    if (isLocked(clip.tlId)) { toast("Clip ist gesperrt.", "warn", 1500); return; }
    const cutLocal = globalTime - clip.start;
    if (cutLocal < 0.05 || cutLocal > clip.duration - 0.05) {
      toast("Zu nah am Clip-Rand zum Schneiden.", "warn");
      return;
    }
    snapshot();
    setTlClips((cur) => {
      const copy = [...cur];
      const linked = clip.avLinked !== false;
      // Split keeps the A/V state on both halves. Linked → both stay linked
      // (audioStart follows start, so clear it). Unlinked → both stay unlinked
      // and their audioStart is recomputed to their OWN new start (the split
      // point re-references the audio to the video at each half).
      const before: TLClip = {
        ...clip, duration: cutLocal,
        avLinked: linked ? true : false,
        audioStart: linked ? undefined : clip.start,
      };
      const afterStart = clip.start + cutLocal;
      const after: TLClip = {
        ...clip,
        tlId: `${clip.clipId}-${Date.now()}-b`,
        start: afterStart,
        duration: clip.duration - cutLocal,
        mediaStart: clip.mediaStart + cutLocal,   // ← lit à partir du bon endroit du média
        avLinked: linked ? true : false,
        audioStart: linked ? undefined : afterStart,
      };
      copy.splice(idx, 1, before, after);
      return copy;
    });
    toast(`Clip bei ${fmtTC(globalTime)} geteilt.`, "ok", 1800);
  };

  const trimSelected = (side: "left" | "right" | "both", amountSec = 0.5) => {
    if (selectedTlIds.size === 0) return;
    const trimmable = [...selectedTlIds].filter((id) => !isLocked(id));
    if (trimmable.length === 0) { toast("Ausgewählte Clips sind gesperrt.", "warn", 1500); return; }
    snapshot();
    setTlClips((cur) => {
      const trimmed = cur.map((c) => {
        if (!selectedTlIds.has(c.tlId) || isLocked(c.tlId)) return c;
        // Left trim : avance mediaStart + réduit duration (le clip commence plus tard dans la source)
        // Right trim : réduit uniquement duration (le clip finit plus tôt)
        // Both : les deux
        let mediaStart = c.mediaStart;
        let dur = c.duration;
        if (side === "left" || side === "both") {
          const amt = Math.min(amountSec, dur - 0.2);
          mediaStart += amt;
          dur -= amt;
        }
        if (side === "right" || side === "both") {
          dur -= amountSec;
        }
        return { ...c, mediaStart, duration: Math.max(0.2, dur) };
      });
      return reflow(trimmed);
    });
  };

  // ── Edge Trim par drag (DaVinci-style) ─────────────────────────────────────
  // Poignées invisibles (6 px) sur les bords gauche/droit de chaque clip vidéo.
  // Gauche : mediaStart+delta / duration−delta (delta>0 = trim, <0 = extend).
  // Droite : duration+delta, borné par la longueur source. `pxPerSec` est figé
  // au mouseDown (rect de la rangée / durée totale) — le delta reste stable
  // même si la timeline se redistribue en % pendant le drag.
  const beginEdgeTrim = (e: React.MouseEvent<HTMLDivElement>, tlId: string, side: "left" | "right") => {
    if (e.button !== 0) return; // clic gauche uniquement
    e.stopPropagation();        // ne PAS déclencher beginClipDrag / clickTlClip
    e.preventDefault();
    const clip = tlClips.find((c) => c.tlId === tlId);
    if (!clip) return;
    if (clipLocked(clip)) { toast("Clip ist gesperrt.", "warn", 1500); return; }
    const row = (e.currentTarget.parentElement?.parentElement ?? null) as HTMLElement | null; // handle → clip → rangée
    const rowRect = row?.getBoundingClientRect();
    const total = totalDurationRef.current;
    if (!rowRect || rowRect.width <= 0 || total <= 0) return;
    const pxPerSec = rowRect.width / total;
    const startClientX = e.clientX;
    const orig = { ...clip };
    snapshot(); // un seul undo-step pour tout le drag

    // Snap candidates : playhead, markers, autres clips (start & end), t=0.
    // Construit UNE fois au début (le set reste fixe pendant le drag).
    const snapCandidates: { time: number; label: string }[] = [];
    snapCandidates.push({ time: globalTime, label: `Abspielkopf · ${fmtTC(globalTime)}` });
    for (const m of markers) snapCandidates.push({ time: m.time, label: `Marker: ${m.label} · ${fmtTC(m.time)}` });
    for (const c of tlClips) {
      if (c.tlId === tlId) continue;
      snapCandidates.push({ time: c.start, label: `→ ${c.name || "Clip"} (Start) · ${fmtTC(c.start)}` });
      snapCandidates.push({ time: c.start + c.duration, label: `${c.name || "Clip"} (Ende) ← · ${fmtTC(c.start + c.duration)}` });
    }
    snapCandidates.push({ time: 0, label: `Start · ${fmtTC(0)}` });
    // Grille 5s (0:05, 0:10, 0:15…) — aide à l'alignement rapide
    for (let t = 5; t < total; t += 5) snapCandidates.push({ time: t, label: `${fmtTC(t)}` });
    const guide = snapGuideRef.current;

    let lastDuration = orig.duration;
    const onMove = (ev: MouseEvent) => {
      const raw = (ev.clientX - startClientX) / pxPerSec;
      // Clamp d aux bornes selon le côté
      let d: number;
      if (side === "left") {
        d = Math.min(Math.max(raw, -orig.mediaStart), orig.duration - 0.2);
      } else {
        d = Math.min(Math.max(raw, -(orig.duration - 0.2)), orig.sourceDuration - orig.mediaStart - orig.duration);
      }
      // ── Snap magnétique (comme DaVinci) : compare l'edge draggée aux candidats ──
      let activeGuidePx: number | null = null;
      let activeLabel = "";
      if (snapEnabled && !ev.altKey) {
        const edgeTime = side === "left" ? orig.start + d : orig.start + orig.duration + d;
        const edgePx = edgeTime * pxPerSec;
        let best = SNAP_TOLERANCE_PX + 0.001;
        let snappedTime = edgeTime;
        for (const cand of snapCandidates) {
          const candPx = cand.time * pxPerSec;
          const diff = Math.abs(candPx - edgePx);
          if (diff < best) { best = diff; snappedTime = cand.time; activeGuidePx = candPx; activeLabel = cand.label; }
        }
        if (activeGuidePx != null) {
          // Recalcule d à partir de snappedTime + reclamp (garantit bornes valides)
          const dSnap = side === "left" ? snappedTime - orig.start : snappedTime - orig.start - orig.duration;
          d = side === "left"
            ? Math.min(Math.max(dSnap, -orig.mediaStart), orig.duration - 0.2)
            : Math.min(Math.max(dSnap, -(orig.duration - 0.2)), orig.sourceDuration - orig.mediaStart - orig.duration);
        }
      }
      // Update DOM guide (zero React re-render)
      if (guide) {
        if (activeGuidePx != null) {
          guide.style.transform = `translateX(${activeGuidePx}px)`;
          guide.style.display = "block";
          if (snapGuideLabelRef.current) snapGuideLabelRef.current.textContent = activeLabel;
        } else {
          guide.style.display = "none";
        }
      }
      // Trim LEFT : `start` bouge aussi → c'est le début du clip qui se déplace,
      // pas la fin. Trim RIGHT : `start` reste, seul `duration` change (la fin
      // bouge). Comportement DaVinci/Premiere standard.
      const start = side === "left" ? orig.start + d : orig.start;
      const mediaStart = side === "left" ? orig.mediaStart + d : orig.mediaStart;
      const duration = side === "left" ? orig.duration - d : orig.duration + d;
      // Si le clip est linked A/V, l'audioStart suit le start (l'audio reste
      // aligné). Si unlinked, on ne touche pas à audioStart (audio indépendant).
      const audioStartPatch = side === "left"
        ? (orig.avLinked === false
            ? { audioStart: orig.audioStart }
            : { audioStart: start })
        : {};
      lastDuration = duration;
      setTlClips((cur) => reflow(cur.map((c) => (c.tlId === tlId ? { ...c, start, mediaStart, duration, ...audioStartPatch } : c))));
      setTrimHud({ x: ev.clientX, y: ev.clientY, delta: duration - orig.duration, newDuration: duration });
    };
    const onUp = () => {
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("mouseup", onUp);
      window.removeEventListener("blur", onUp);
      document.body.style.cursor = "";
      setTrimHud(null);
      if (guide) guide.style.display = "none";
      toast(`Getrimmt: ${fmtSec(lastDuration)}`, "ok", 1400);
    };
    document.body.style.cursor = "url(\"data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' width='24' height='24' viewBox='0 0 24 24'><rect x='11' y='4' width='2' height='16' fill='%236ad04a' stroke='%23000' stroke-width='0.5'/><path d='M11 9 L5 12 L11 15 Z' fill='%236ad04a' stroke='%23000' stroke-width='0.5'/><path d='M13 9 L19 12 L13 15 Z' fill='%236ad04a' stroke='%23000' stroke-width='0.5'/></svg>\") 12 12, ew-resize";
    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
    window.addEventListener("blur", onUp); // perte de focus = fin du trim (état déjà appliqué)
  };

  // ── Fade-Handle Drag (Fade-in / Fade-out) ──────────────────────────────────
  // Drag horizontal d'une poignée dans un coin haut du clip → ajuste `fadeIn`
  // ou `fadeOut` (secondes). Le multiplicateur est ensuite appliqué à opacité +
  // volume par le PlaybackEngine (via EngineClip.fadeInFrames / fadeOutFrames).
  const beginFadeDrag = (e: React.MouseEvent<HTMLDivElement>, tlId: string, side: "in" | "out") => {
    if (e.button !== 0) return;
    e.stopPropagation();
    e.preventDefault();
    const clip = tlClips.find((c) => c.tlId === tlId);
    if (!clip) return;
    if (clipLocked(clip)) { toast("Clip ist gesperrt.", "warn", 1500); return; }
    const row = (e.currentTarget.parentElement?.parentElement ?? null) as HTMLElement | null;
    const rowRect = row?.getBoundingClientRect();
    const total = totalDurationRef.current;
    if (!rowRect || rowRect.width <= 0 || total <= 0) return;
    const pxPerSec = rowRect.width / total;
    const startClientX = e.clientX;
    const startFade = side === "in" ? (clip.fadeIn ?? 0) : (clip.fadeOut ?? 0);
    const maxFade = Math.max(0, clip.duration - 0.05);
    let snapshotDone = false;
    const onMove = (ev: MouseEvent) => {
      if (ev.buttons === 0) { onUp(); return; }
      const dx = ev.clientX - startClientX;
      const delta = side === "in" ? (dx / pxPerSec) : (-dx / pxPerSec);
      const newFade = Math.max(0, Math.min(maxFade, startFade + delta));
      if (!snapshotDone) { snapshot(); snapshotDone = true; }
      setTlClips((cur) => cur.map((c) => {
        if (c.tlId !== tlId) return c;
        const eps = 0.02;
        if (side === "in") return { ...c, fadeIn: newFade > eps ? newFade : undefined };
        return { ...c, fadeOut: newFade > eps ? newFade : undefined };
      }));
    };
    const onUp = () => {
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("mouseup", onUp);
      window.removeEventListener("blur", onUp);
    };
    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
    window.addEventListener("blur", onUp);
  };

  // ── Fade-Curve Drag (vertical) — DaVinci-style ─────────────────────────────
  // Rond central sur la rampe de fade. Drag vertical = ajuste la courbure
  // `fadeInCurve` ou `fadeOutCurve` (∈ [-1, 1]). Mis à jour côté engine via
  // computeFadeMultiplier (power curve x^(2^curve)).
  const beginFadeCurveDrag = (e: React.MouseEvent<HTMLDivElement>, tlId: string, side: "in" | "out") => {
    if (e.button !== 0) return;
    e.stopPropagation();
    e.preventDefault();
    const clip = tlClips.find((c) => c.tlId === tlId);
    if (!clip) return;
    if (clipLocked(clip)) { toast("Clip ist gesperrt.", "warn", 1500); return; }
    // Le parent = clip container. On mesure sa hauteur pour convertir dy px → % clip.
    const clipEl = (e.currentTarget.parentElement ?? null) as HTMLElement | null;
    const rect = clipEl?.getBoundingClientRect();
    if (!rect || rect.height <= 0) return;
    const startClientY = e.clientY;
    const startCurve = side === "in" ? (clip.fadeInCurve ?? 0) : (clip.fadeOutCurve ?? 0);
    const startMidY = 50 + startCurve * 40;
    let snapshotDone = false;
    const onMove = (ev: MouseEvent) => {
      if (ev.buttons === 0) { onUp(); return; }
      const dyPx = ev.clientY - startClientY;
      const dyPct = (dyPx / rect.height) * 100;
      const newMidY = Math.max(10, Math.min(90, startMidY + dyPct));
      const newCurve = (newMidY - 50) / 40; // clamped in [-1, 1]
      if (!snapshotDone) { snapshot(); snapshotDone = true; }
      setTlClips((cur) => cur.map((c) => {
        if (c.tlId !== tlId) return c;
        const eps = 0.03;
        if (side === "in") return { ...c, fadeInCurve: Math.abs(newCurve) < eps ? undefined : newCurve };
        return { ...c, fadeOutCurve: Math.abs(newCurve) < eps ? undefined : newCurve };
      }));
    };
    const onUp = () => {
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("mouseup", onUp);
      window.removeEventListener("blur", onUp);
    };
    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
    window.addEventListener("blur", onUp);
  };

  // ── Rubber-Band Gain Drag ──────────────────────────────────────────────────
  // Ligne horizontale sur le clip audio. Drag vertical = ajuste `gainDb`
  // (constant sur tout le clip pour cette itération, keyframes plus tard).
  // Mapping vertical : hauteur du clip couvre ±18 dB. Range clamped [-24, +12].
  const beginGainDrag = (e: React.MouseEvent<HTMLDivElement>, tlId: string) => {
    if (e.button !== 0) return;
    e.stopPropagation();
    e.preventDefault();
    const clip = tlClips.find((c) => c.tlId === tlId);
    if (!clip) return;
    if (clipLocked(clip)) { toast("Clip ist gesperrt.", "warn", 1500); return; }
    const clipEl = (e.currentTarget.parentElement ?? null) as HTMLElement | null;
    const rect = clipEl?.getBoundingClientRect();
    if (!rect || rect.height <= 0) return;
    const startClientY = e.clientY;
    const startDb = clip.gainDb ?? 0;
    let snapshotDone = false;
    const onMove = (ev: MouseEvent) => {
      if (ev.buttons === 0) { onUp(); return; }
      const dyPx = ev.clientY - startClientY;
      // 100% de la hauteur du clip = 36 dB de course (± 18 dB autour du start).
      const dyDb = (-dyPx / rect.height) * 36;
      const newDb = Math.max(-24, Math.min(12, startDb + dyDb));
      if (!snapshotDone) { snapshot(); snapshotDone = true; }
      setTlClips((cur) => cur.map((c) => c.tlId === tlId ? { ...c, gainDb: Math.abs(newDb) < 0.05 ? undefined : newDb } : c));
    };
    const onUp = () => {
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("mouseup", onUp);
      window.removeEventListener("blur", onUp);
    };
    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
    window.addEventListener("blur", onUp);
  };

  // ── Roll Trim entre deux clips adjacents ───────────────────────────────────
  // Déplace le point de coupe : A gagne Δ, B perd Δ (start/mediaStart de B
  // reculent d'autant) — la durée totale de la timeline reste inchangée, donc
  // AUCUN reflow. Δ est clampé pour garder les DEUX clips valides (durée ≥ 0.2,
  // bornes source respectées) ; les bornes se chevauchent toujours car
  // L ≤ 0 ≤ U par construction.
  const beginRollTrim = (e: React.MouseEvent<HTMLDivElement>, aId: string, bId: string) => {
    if (e.button !== 0) return;
    e.stopPropagation();
    e.preventDefault();
    const a = tlClips.find((c) => c.tlId === aId);
    const b = tlClips.find((c) => c.tlId === bId);
    if (!a || !b) return;
    if (clipLocked(a) || clipLocked(b)) { toast("Clip ist gesperrt.", "warn", 1500); return; }
    const row = (e.currentTarget.parentElement ?? null) as HTMLElement | null; // handle → rangée
    const rowRect = row?.getBoundingClientRect();
    const total = totalDurationRef.current;
    if (!rowRect || rowRect.width <= 0 || total <= 0) return;
    const pxPerSec = rowRect.width / total;
    const startClientX = e.clientX;
    const origA = { ...a };
    const origB = { ...b };
    const cutOrig = origA.start + origA.duration; // position du point de coupe avant drag
    snapshot();

    // Snap candidates : playhead + markers uniquement (les edges des OTHER clips
    // ne sont pas pertinents pour un roll, car A et B restent contigus par
    // construction). t=0 exclu (le cut ne peut jamais être en 0).
    const snapCandidates: { time: number; label: string }[] = [];
    snapCandidates.push({ time: globalTime, label: `Abspielkopf · ${fmtTC(globalTime)}` });
    for (const m of markers) snapCandidates.push({ time: m.time, label: `Marker: ${m.label} · ${fmtTC(m.time)}` });
    // Grille 5s
    for (let t = 5; t < totalDuration; t += 5) snapCandidates.push({ time: t, label: `${fmtTC(t)}` });
    const guide = snapGuideRef.current;

    // Bornes valides du delta (celles-ci se chevauchent car L ≤ 0 ≤ U par construction)
    const dMax = Math.min(origA.sourceDuration - origA.mediaStart - origA.duration, origB.duration - 0.2);
    const dMin = Math.max(0.2 - origA.duration, -origB.mediaStart);

    let lastDelta = 0;
    const onMove = (ev: MouseEvent) => {
      let d = (ev.clientX - startClientX) / pxPerSec;
      d = Math.max(dMin, Math.min(dMax, d));

      // Snap magnétique sur le cut point
      let activeGuidePx: number | null = null;
      let activeLabel = "";
      if (snapEnabled && !ev.altKey) {
        const cutTime = cutOrig + d;
        const cutPx = cutTime * pxPerSec;
        let best = SNAP_TOLERANCE_PX + 0.001;
        let snappedCut = cutTime;
        for (const cand of snapCandidates) {
          const candPx = cand.time * pxPerSec;
          const diff = Math.abs(candPx - cutPx);
          if (diff < best) { best = diff; snappedCut = cand.time; activeGuidePx = candPx; activeLabel = cand.label; }
        }
        if (activeGuidePx != null) {
          d = Math.max(dMin, Math.min(dMax, snappedCut - cutOrig));
        }
      }
      if (guide) {
        if (activeGuidePx != null) {
          guide.style.transform = `translateX(${activeGuidePx}px)`;
          guide.style.display = "block";
          if (snapGuideLabelRef.current) snapGuideLabelRef.current.textContent = activeLabel;
        } else {
          guide.style.display = "none";
        }
      }
      lastDelta = d;
      setTlClips((cur) => cur.map((c) => {
        if (c.tlId === aId) return { ...c, duration: origA.duration + d };
        // B.start suit A.end (= origB.start + d) et la source recale d'autant.
        if (c.tlId === bId) return { ...c, start: origB.start + d, mediaStart: origB.mediaStart + d, duration: origB.duration - d };
        return c;
      }));
      setTrimHud({ x: ev.clientX, y: ev.clientY, delta: d, newDuration: origA.duration + d, label: "Roll" });
    };
    const onUp = () => {
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("mouseup", onUp);
      window.removeEventListener("blur", onUp);
      document.body.style.cursor = "";
      setTrimHud(null);
      if (guide) guide.style.display = "none";
      toast(`Schnittpunkt verschoben: ${lastDelta >= 0 ? "+" : "−"}${fmtSec(Math.abs(lastDelta))}`, "ok", 1400);
    };
    document.body.style.cursor = "url(\"data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' width='24' height='24' viewBox='0 0 24 24'><rect x='11' y='4' width='2' height='16' fill='%236ad04a' stroke='%23000' stroke-width='0.5'/><path d='M11 9 L5 12 L11 15 Z' fill='%236ad04a' stroke='%23000' stroke-width='0.5'/><path d='M13 9 L19 12 L13 15 Z' fill='%236ad04a' stroke='%23000' stroke-width='0.5'/></svg>\") 12 12, ew-resize";
    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
    window.addEventListener("blur", onUp);
  };

  const appendClip = (clipId: string, videoTrackIndex = 0) => {
    const src = clips.find((c) => c.id === clipId);
    if (!src) return;
    if (trackState(`v${videoTrackIndex}`).locked) { toast(`V${videoTrackIndex + 1} ist gesperrt.`, "warn", 1500); return; }
    snapshot();
    setTlClips((cur) => {
      const dur = src.dauer || 0;
      const seg: TLClip = {
        tlId: `${clipId}-${Date.now()}`,
        clipId,
        name: src.dateiname.replace(/\.[^/.]+$/, ""),
        start: 0, // set by appendTails (tail of this video track)
        duration: dur,
        mediaStart: 0,
        sourceDuration: dur,
        stripUrl: abs(src.strip_url),
        waveformUrl: abs(src.waveform_url),
        proxyUrl: abs(src.proxy_url || src.video_url),
        videoUrl: abs(src.video_url),
        hasAudio: !!src.waveform_url,
        videoTrackIndex,
        audioTrackIndex: videoTrackIndex,
      };
      return appendTails(cur, [seg]);
    });
  };

  const toggleMedia = (id: string) =>
    setSelectedMedia((s) => {
      const n = new Set(s);
      if (n.has(id)) n.delete(id); else n.add(id);
      return n;
    });

  const loadTimeline = async (timelineId: string) => {
    try {
      const r = await fetch(`${API}/api/timelines/${timelineId}`);
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const t = await r.json();
      const segs = t?.daten?.segmente || [];
      if (!Array.isArray(segs) || segs.length === 0) {
        setSaveStatus("error");
        setTimeout(() => setSaveStatus("idle"), 2500);
        return;
      }
      snapshot();
      const loaded: TLClip[] = [];
      let cursor = 0;
      for (const seg of segs) {
        const clipId: string = seg.clip_id || seg.clipId || "";
        const src = clips.find((c) => c.id === clipId);
        const dur = Number(seg.dauer ?? seg.duration ?? 0);
        const mediaStart = Number(seg.media_start ?? seg.mediaStart ?? 0);
        if (dur <= 0) continue;
        loaded.push({
          tlId: String(seg.id ?? `${clipId}-${Date.now()}-${cursor}`),
          clipId,
          name: String(seg.label ?? src?.dateiname?.replace(/\.[^/.]+$/, "") ?? "Clip"),
          start: cursor,
          duration: dur,
          mediaStart,
          sourceDuration: Number(seg.source_duration ?? src?.dauer ?? dur),
          stripUrl: abs(src?.strip_url),
          waveformUrl: abs(src?.waveform_url),
          proxyUrl: abs(src?.proxy_url || src?.video_url),
          videoUrl: abs(src?.video_url),
          hasAudio: !!src?.waveform_url,
        });
        cursor += dur;
      }
      setTlClips(loaded);
      setSelectedTlIds(new Set());
      seekSeconds(0);
      pause();
      setProjectName(t.name || projectName);
      setHistOpen(false);
      setSaveStatus("saved");
      setTimeout(() => setSaveStatus("idle"), 1500);
      toast(`Timeline "${t.name}" geladen (${loaded.length} Clips).`, "ok");
    } catch (e) {
      console.warn(e);
      setSaveStatus("error");
      setTimeout(() => setSaveStatus("idle"), 3000);
      toast(`Laden fehlgeschlagen: ${(e as Error).message}`, "err", 5000);
    }
  };

  const saveNow = async () => {
    if (tlClips.length === 0) return;
    setSaveStatus("saving");
    try {
      const total = tlClips.reduce((s, c) => s + c.duration, 0);
      const r = await fetch(`${API}/api/timelines`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          name: projectName || "Unbenannt",
          stil: "manuell",
          daten: {
            segmente: tlClips.map((c) => ({
              id: c.tlId, clip_id: c.clipId, label: c.name, track: "v1",
              start: c.start, dauer: c.duration, quelle: "A",
              media_start: c.mediaStart,
              source_duration: c.sourceDuration,
            })),
            gesamtdauer: total,
          },
        }),
      });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const saved: TimelineDTO = await r.json();
      setTimelines((cur) => [saved, ...cur]);
      setSaveStatus("saved");
      setTimeout(() => setSaveStatus("idle"), 2000);
      toast(`Timeline "${projectName}" gespeichert.`, "ok");
    } catch (e) {
      setSaveStatus("error");
      setTimeout(() => setSaveStatus("idle"), 3000);
      toast(`Speichern fehlgeschlagen: ${(e as Error).message}`, "err", 5000);
    }
  };

  const runSearch = async () => {
    if (!search.trim()) { setSearchResults([]); return; }
    try {
      const r = await fetch(`${API}/api/scenes/search`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ query: search, limit: 8 }),
      });
      const data = await r.json();
      setSearchResults(data.results || []);
    } catch { setSearchResults([]); }
  };

  const sendAi = async (msg?: string) => {
    const text = (msg ?? aiPrompt).trim();
    if (!text || aiBusy) return;
    setAiPrompt("");
    setAiHistory((h) => [...h, { role: "user", content: text }]);
    setAiBusy(true);
    try {
      const r = await fetch(`${API}/api/agent/run_sync`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ prompt: text }),
      });
      const data = await r.json();
      const answer = data.final_answer || "(keine Antwort)";
      setAiHistory((h) => [...h, { role: "agent", content: answer }]);

      // Auto-detect : si l'agent a créé un stash ou une nouvelle timeline,
      // rafraîchit la liste des timelines et propose de charger.
      const trace = data.trace || [];
      const producedSegments = trace.some((e: { type: string; content?: { stash_id?: string; segments_preview?: unknown } }) =>
        e.type === "observation" &&
        typeof e.content === "object" &&
        e.content !== null &&
        (e.content.stash_id === "last" || Array.isArray(e.content.segments_preview))
      );
      if (producedSegments) {
        // Fetch timelines fraîches
        const tr = await fetch(`${API}/api/timelines`).then((x) => x.json()).catch(() => []);
        setTimelines(tr || []);
        // Prend le stash le plus récent (backend crée toujours "_stash:last", mais garde
        // aussi les anciens ; on trie par erstellt_am desc pour être safe).
        const stash = (tr || [])
          .filter((t: { name: string }) => t.name === "_stash:last")
          .sort((a: { erstellt_am?: string }, b: { erstellt_am?: string }) =>
            String(b.erstellt_am ?? "").localeCompare(String(a.erstellt_am ?? "")))
          [0];
        if (stash) {
          const toolCount = trace.filter((e: { type: string }) => e.type === "action").length;
          toast(`Agent hat ${toolCount} Tools genutzt — Rohschnitt wird geladen (Cmd+Z zum Rückgängig).`, "info", 4000);
          await loadTimeline(stash.id);
        }
      }
    } catch (e) {
      const msg = `Fehler: ${(e as Error).message}`;
      setAiHistory((h) => [...h, { role: "agent", content: msg }]);
      toast(msg, "err", 5000);
    } finally {
      setAiBusy(false);
    }
  };

  const clipToPct = (start: number) => (totalDuration > 0 ? (start / totalDuration) * 100 : 0);
  const clipWidthPct = (dur: number) => (totalDuration > 0 ? (dur / totalDuration) * 100 : 0);
  // Effektive Audio-Position: getrennt → eigenes `audioStart`, sonst folgt `start`.
  const audioStartOf = (c: TLClip) => (c.avLinked === false && c.audioStart != null ? c.audioStart : c.start);
  const genLinkId = () => {
    try { return crypto.randomUUID(); } catch { return `lg-${Date.now()}-${Math.round(performance.now())}`; }
  };
  const playheadPct = totalDuration > 0 ? (globalTime / totalDuration) * 100 : 0;

  // Timeline zoomable : multiplie la largeur interne mais garde les positions en %
  const timelineInnerWidth = `${100 * zoom}%`;
  const zoomIn = () => setZoom((z) => Math.min(MAX_ZOOM, z * 1.5));
  const zoomOut = () => setZoom((z) => Math.max(MIN_ZOOM, z / 1.5));
  const zoomFit = () => setZoom(1);

  const gridMedia = useMemo(() => {
    let list = clips;
    if (mediaFilter === "video") list = list.filter((c) => !c.waveform_url);
    else if (mediaFilter === "audio") list = list.filter((c) => !!c.waveform_url);
    if (search.trim()) {
      const q = search.toLowerCase();
      list = list.filter((c) => c.dateiname.toLowerCase().includes(q));
    }
    if (mediaSort === "name") list = [...list].sort((a, b) => a.dateiname.localeCompare(b.dateiname));
    else if (mediaSort === "duration") list = [...list].sort((a, b) => (b.dauer || 0) - (a.dauer || 0));
    else if (mediaSort === "recent") list = [...list].reverse();
    return list;
  }, [clips, search, mediaFilter, mediaSort]);

  /* ─── Sprint 4 Action helpers ─── */
  const goHome = () => {
    pause();
    seekSeconds(0);
    setSelectedTlIds(new Set());
    setHistOpen(false);
    setAiOpen(false);
    setSettingsOpen(false);
    toast("Zurück zur Übersicht.", "info", 1500);
  };

  const newTimeline = () => {
    if (tlClips.length > 0 && !confirm("Aktuelle Timeline verwerfen und neu anfangen?")) return;
    snapshot();
    setTlClips([]);
    setSelectedTlIds(new Set());
    seekSeconds(0);
    pause();
    setProjectName("Neues Projekt");
    setMarkers([]);
    toast("Neue Timeline.", "ok", 1500);
  };

  const importFromMedia = () => {
    if (selectedMedia.size === 0) { toast("Wähle zuerst Clips im Medien-Panel.", "warn"); return; }
    snapshot();
    Array.from(selectedMedia).forEach((id) => appendClip(id));
    setSelectedMedia(new Set());
    toast(`${selectedMedia.size} Clip(s) importiert.`, "ok");
  };

  const cutSelected = () => {
    if (selectedTlIds.size === 0) { toast("Kein Clip ausgewählt.", "warn"); return; }
    const cut = tlClips.filter((c) => selectedTlIds.has(c.tlId) && !isLocked(c.tlId));
    if (cut.length === 0) { toast("Ausgewählte Clips sind gesperrt.", "warn", 1500); return; }
    setClipboard(cut);
    removeSelected();
    toast(`${cut.length} Clip(s) ausgeschnitten.`, "ok", 1500);
  };

  const copySelected = () => {
    if (selectedTlIds.size === 0) { toast("Kein Clip ausgewählt.", "warn"); return; }
    const copy = tlClips.filter((c) => selectedTlIds.has(c.tlId));
    setClipboard(copy);
    toast(`${copy.length} Clip(s) kopiert.`, "ok", 1500);
  };

  const paste = () => {
    if (clipboard.length === 0) { toast("Zwischenablage leer.", "warn"); return; }
    snapshot();
    setTlClips((cur) => {
      const pasted = clipboard.map((c) => ({ ...c, tlId: `${c.clipId}-${Date.now()}-${Math.random().toString(36).slice(2, 6)}` }));
      return appendTails(cur, pasted);
    });
    toast(`${clipboard.length} Clip(s) eingefügt.`, "ok", 1500);
  };

  const duplicateSelected = () => {
    if (selectedTlIds.size === 0) { toast("Kein Clip ausgewählt.", "warn"); return; }
    const sel = tlClips.filter((c) => selectedTlIds.has(c.tlId) && !isLocked(c.tlId));
    if (sel.length === 0) { toast("Ausgewählte Clips sind gesperrt.", "warn", 1500); return; }
    setClipboard(sel);
    snapshot();
    setTlClips((cur) => {
      const dupes = sel.map((c) => ({ ...c, tlId: `${c.clipId}-${Date.now()}-${Math.random().toString(36).slice(2, 6)}` }));
      return appendTails(cur, dupes);
    });
    toast(`${sel.length} Clip(s) dupliziert.`, "ok", 1500);
  };

  const addMarkerAtPlayhead = () => {
    if (totalDuration === 0) { toast("Keine Timeline.", "warn"); return; }
    const label = prompt("Marker-Bezeichnung:", `Marker ${markers.length + 1}`);
    if (label == null) return;
    const m = { id: `m-${Date.now()}`, time: globalTime, label: label || `Marker ${markers.length + 1}` };
    setMarkers((cur) => [...cur, m].sort((a, b) => a.time - b.time));
    toast(`Marker "${m.label}" bei ${fmtTC(globalTime)}.`, "ok", 1800);
  };

  // Marker à une position temporelle donnée (menu contextuel « ici »).
  const addMarkerAt = (time: number) => {
    if (totalDuration === 0) { toast("Keine Timeline.", "warn"); return; }
    const t = clamp(time, 0, totalDuration);
    const label = prompt("Marker-Bezeichnung:", `Marker ${markers.length + 1}`);
    if (label == null) return;
    const m = { id: `m-${Date.now()}`, time: t, label: label || `Marker ${markers.length + 1}` };
    setMarkers((cur) => [...cur, m].sort((a, b) => a.time - b.time));
    toast(`Marker "${m.label}" bei ${fmtTC(t)}.`, "ok", 1800);
  };

  /* ─── Timeline Command Executor ────────────────────────────────────────────
     Contract API entre les 2 sources d'édition (humain via UI, agent IA via
     Proposal). Fonctions pures pour les mutations tlClips → 1 seul setTlClips
     par batch = 1 seul undo step. Enregistré dans le store Proposal. */
  const applyCmdToClips = (clips: TLClip[], cmd: TimelineCmd): TLClip[] => {
    switch (cmd.type) {
      case "split": {
        const targets = clips.filter((c) => {
          if (cmd.clipTlIds && !cmd.clipTlIds.includes(c.tlId)) return false;
          return cmd.at > c.start + 0.05 && cmd.at < c.start + c.duration - 0.05 && !isLocked(c.tlId);
        });
        let out = [...clips];
        for (const clip of targets) {
          const idx = out.findIndex((x) => x.tlId === clip.tlId);
          if (idx === -1) continue;
          const c = out[idx];
          const cutLocal = cmd.at - c.start;
          const linked = c.avLinked !== false;
          const before: TLClip = { ...c, duration: cutLocal, avLinked: linked ? true : false, audioStart: linked ? undefined : c.start };
          const afterStart = c.start + cutLocal;
          const after: TLClip = {
            ...c,
            tlId: `${c.clipId}-${Date.now()}-${idx}-b`,
            start: afterStart,
            mediaStart: c.mediaStart + cutLocal,
            duration: c.duration - cutLocal,
            avLinked: linked ? true : false,
            audioStart: linked ? undefined : afterStart,
          };
          out.splice(idx, 1, before, after);
        }
        return out;
      }
      case "delete": {
        const removable = new Set(cmd.tlIds);
        if (!cmd.ripple) return clips.filter((c) => !removable.has(c.tlId));
        // Ripple : décale à gauche les clips suivant les supprimés, par piste V et A.
        const removed = clips.filter((c) => removable.has(c.tlId)).sort((a, b) => a.start - b.start);
        let working = clips.filter((c) => !removable.has(c.tlId));
        for (const r of removed) {
          const rvt = r.videoTrackIndex ?? 0;
          const rat = r.hasAudio ? (r.audioTrackIndex ?? rvt) : null;
          const cutEnd = r.start + r.duration;
          working = working.map((c) => {
            const cvt = c.videoTrackIndex ?? 0;
            const cat = c.hasAudio ? (c.audioTrackIndex ?? cvt) : null;
            const sameV = cvt === rvt;
            const sameA = rat !== null && cat !== null && cat === rat;
            if ((sameV || sameA) && c.start >= cutEnd - 0.001) return { ...c, start: Math.max(0, c.start - r.duration) };
            return c;
          });
        }
        return working;
      }
      case "deleteRange": {
        // Étapes : (a) split at from + split at to sur tous les clips qui traversent
        // [from, to] (et matchent tlIds si fourni), (b) collecte les clips-milieu
        // résultants, (c) delete + optional ripple.
        const from = Math.min(cmd.from, cmd.to);
        const to = Math.max(cmd.from, cmd.to);
        if (to - from < 0.02) return clips;
        const filterTarget = (c: TLClip) => (!cmd.tlIds || cmd.tlIds.includes(c.tlId)) && !isLocked(c.tlId);
        // (a) split at `from`
        let out: TLClip[] = [...clips];
        const applySplit = (arr: TLClip[], at: number): TLClip[] => {
          const targets = arr.filter((c) => filterTarget(c) && at > c.start + 0.05 && at < c.start + c.duration - 0.05);
          let acc = arr;
          for (const clip of targets) {
            const idx = acc.findIndex((x) => x.tlId === clip.tlId);
            if (idx === -1) continue;
            const c = acc[idx];
            const cutLocal = at - c.start;
            const linked = c.avLinked !== false;
            const before: TLClip = { ...c, duration: cutLocal, avLinked: linked ? true : false, audioStart: linked ? undefined : c.start };
            const afterStart = c.start + cutLocal;
            const after: TLClip = {
              ...c,
              tlId: `${c.clipId}-${Date.now()}-${idx}-${Math.round(at * 1000)}-b`,
              start: afterStart,
              mediaStart: c.mediaStart + cutLocal,
              duration: c.duration - cutLocal,
              avLinked: linked ? true : false,
              audioStart: linked ? undefined : afterStart,
            };
            acc = acc.slice();
            acc.splice(idx, 1, before, after);
          }
          return acc;
        };
        out = applySplit(out, from);
        out = applySplit(out, to);
        // (b) collecte les clips-milieu (start >= from-eps ET end <= to+eps ET matchTarget)
        const toRemove = new Set<string>();
        for (const c of out) {
          if (!filterTarget(c)) continue;
          const cEnd = c.start + c.duration;
          if (c.start >= from - 0.02 && cEnd <= to + 0.02) toRemove.add(c.tlId);
        }
        if (toRemove.size === 0) return out;
        // (c) delete + ripple
        return applyCmdToClips(out, { type: "delete", tlIds: [...toRemove], ripple: cmd.ripple });
      }
      case "setFade":
        return clips.map((c) => c.tlId !== cmd.tlId ? c : {
          ...c,
          [cmd.side === "in" ? "fadeIn" : "fadeOut"]: cmd.duration > 0.02 ? cmd.duration : undefined,
          [cmd.side === "in" ? "fadeInCurve" : "fadeOutCurve"]: cmd.curve && Math.abs(cmd.curve) > 0.03 ? cmd.curve : undefined,
        });
      case "setGain":
        return clips.map((c) => c.tlId !== cmd.tlId ? c : { ...c, gainDb: Math.abs(cmd.gainDb) < 0.05 ? undefined : cmd.gainDb });
      case "move":
      case "trim":
      case "insert":
        // TODO ticket futur (nécessite une refonte de placement + reflow)
        console.warn("[executor] cmd non encore implémentée :", cmd.type);
        return clips;
      default:
        return clips;
    }
  };

  const applyNonClipCmd = (cmd: TimelineCmd) => {
    switch (cmd.type) {
      case "addMarker":
        setMarkers((cur) => [...cur, { id: `m-${Date.now()}`, time: cmd.at, label: cmd.label }].sort((a, b) => a.time - b.time));
        break;
      case "setRange":
        setInPoint(cmd.inPoint);
        setOutPoint(cmd.outPoint);
        break;
    }
  };

  const executor: TimelineCommandExecutor = {
    execute: (cmd) => {
      snapshot();
      if (cmd.type === "addMarker" || cmd.type === "setRange") applyNonClipCmd(cmd);
      else setTlClips((cur) => applyCmdToClips(cur, cmd));
    },
    executeBatch: (batch, label) => {
      if (batch.length === 0) return;
      snapshot();
      batchModeRef.current = true;
      try {
        const clipCmds = batch.filter((c) => c.type !== "addMarker" && c.type !== "setRange");
        const nonClipCmds = batch.filter((c) => c.type === "addMarker" || c.type === "setRange");
        if (clipCmds.length > 0) {
          setTlClips((cur) => clipCmds.reduce((acc, cmd) => applyCmdToClips(acc, cmd), cur));
        }
        for (const cmd of nonClipCmds) applyNonClipCmd(cmd);
      } finally {
        batchModeRef.current = false;
      }
      toast(`${label}: ${batch.length} Aktion${batch.length > 1 ? "en" : ""}`, "ok", 2000);
    },
    canExecute: (cmd) => {
      switch (cmd.type) {
        case "split":
          if (cmd.at < 0 || cmd.at > totalDuration) return { ok: false, reason: "Position hors timeline" };
          return { ok: true };
        case "delete":
          if (cmd.tlIds.length === 0) return { ok: false, reason: "Aucun clip à supprimer" };
          for (const id of cmd.tlIds) if (!tlClips.find((c) => c.tlId === id)) return { ok: false, reason: `Clip ${id} introuvable` };
          return { ok: true };
        case "deleteRange":
          if (cmd.to - cmd.from < 0.02) return { ok: false, reason: "Bereich zu klein" };
          if (cmd.from < 0 || cmd.to > totalDuration) return { ok: false, reason: "Bereich hors timeline" };
          return { ok: true };
        case "setFade":
        case "setGain":
        case "trim":
        case "move":
          if (!tlClips.find((c) => c.tlId === cmd.tlId)) return { ok: false, reason: `Clip ${cmd.tlId} introuvable` };
          return { ok: true };
        default:
          return { ok: true };
      }
    },
    getSnapshot: () => ({
      totalDuration,
      fps: PROJECT_FPS,
      numVideoTracks,
      numAudioTracks,
      playheadTime: globalTime,
      selectedTlIds: [...selectedTlIds],
      clips: tlClips.map((c) => ({
        tlId: c.tlId,
        clipId: c.clipId,
        name: c.name,
        start: c.start,
        duration: c.duration,
        mediaStart: c.mediaStart,
        videoTrackIndex: c.videoTrackIndex,
        audioTrackIndex: c.audioTrackIndex,
        hasAudio: c.hasAudio,
      })),
    }),
  };

  // Enregistre l'executor dans le store Proposal — l'agent (via addProposal +
  // acceptProposal) pourra pousser des batches par ce canal. Un handle global
  // est aussi exposé pour tester depuis la console : `__cinassistExecutor`.
  useEffect(() => {
    useProposalStore.getState().registerExecutor(executor);
    if (typeof window !== "undefined") {
      const w = window as unknown as { __cinassistExecutor: TimelineCommandExecutor; __cinassistProposalStore: typeof useProposalStore };
      w.__cinassistExecutor = executor;
      w.__cinassistProposalStore = useProposalStore;
    }
    return () => {
      useProposalStore.getState().registerExecutor(null);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  });

  /* ─── Menu contextuel (#3) ─── */
  const openMenuAt = (x: number, y: number, items: MenuItem[]) => {
    const W = 210;
    const H = items.reduce((a, it) => a + (it.separator ? 9 : 30), 8);
    const vw = typeof window !== "undefined" ? window.innerWidth : x + W + 8;
    const vh = typeof window !== "undefined" ? window.innerHeight : y + H + 8;
    setContextMenu({ x: Math.max(4, Math.min(x, vw - W - 8)), y: Math.max(4, Math.min(y, vh - H - 8)), items });
  };
  // Clic droit sur un clip (piste vidéo ou audio).
  const openClipMenu = (e: React.MouseEvent, clip: TLClip) => {
    e.preventDefault(); e.stopPropagation();
    if (!selectedTlIds.has(clip.tlId)) setSelectedTlIds(new Set([clip.tlId]));
    const withinClip = globalTime > clip.start && globalTime < clip.start + clip.duration;
    const locked = isLocked(clip.tlId);
    openMenuAt(e.clientX, e.clientY, [
      { label: "Ausschneiden", kbd: "Cmd X", onClick: cutSelected },
      { label: "Kopieren", kbd: "Cmd C", onClick: copySelected },
      { label: "Einfügen", kbd: "Cmd V", onClick: paste, disabled: clipboard.length === 0 },
      { label: "Duplizieren", kbd: "Cmd D", onClick: duplicateSelected },
      { separator: true },
      { label: "Schneiden", kbd: "C", onClick: splitAtGlobalTime, disabled: !withinClip },
      { label: "Links trimmen (−0,5 s)", onClick: () => trimSelected("left") },
      { label: "Rechts trimmen (−0,5 s)", onClick: () => trimSelected("right") },
      { label: "Beide trimmen (−1 s)", onClick: () => trimSelected("both") },
      { separator: true },
      { label: locked ? "Entsperren" : "Sperren", onClick: toggleLockSelected },
      (clip.hasAudio
        ? (clip.avLinked === false
            ? { label: "A/V verknüpfen", onClick: () => linkAV(clip.tlId) }
            : { label: "A/V trennen", onClick: () => unlinkAV(clip.tlId) })
        : { label: "A/V trennen", disabled: true }),
      { separator: true },
      { label: "Löschen", kbd: "⌫", onClick: removeSelected },
    ]);
  };
  // Clic droit sur un en-tête de piste (V1, A1…).
  const openHeaderMenu = (e: React.MouseEvent, kind: "v" | "a", index: number) => {
    e.preventDefault(); e.stopPropagation();
    const id = `${kind}${index}`;
    const st = trackState(id);
    const count = kind === "v" ? numVideoTracks : numAudioTracks;
    const hasClips = kind === "v"
      ? tlClips.some((c) => (c.videoTrackIndex ?? 0) === index)
      : tlClips.some((c) => c.hasAudio && (c.audioTrackIndex ?? c.videoTrackIndex ?? 0) === index);
    openMenuAt(e.clientX, e.clientY, [
      { label: "Neue Video-Spur", onClick: addVideoTrack, disabled: numVideoTracks >= MAX_TRACKS },
      { label: "Neue Audio-Spur", onClick: addAudioTrack, disabled: numAudioTracks >= MAX_TRACKS },
      { separator: true },
      { label: st.hidden ? "Spur einblenden" : "Spur ausblenden", onClick: () => toggleTrackFlag(id, "hidden") },
      { label: st.solo ? "Solo aus" : "Solo", onClick: () => toggleTrackFlag(id, "solo") },
      { label: st.mute ? "Ton an" : "Stumm", onClick: () => toggleTrackFlag(id, "mute") },
      { label: st.locked ? "Spur entsperren" : "Spur sperren", onClick: () => toggleTrackFlag(id, "locked") },
      { separator: true },
      { label: "Spur löschen", disabled: hasClips || count <= 1, onClick: () => (kind === "v" ? removeVideoTrack(index) : removeAudioTrack(index)) },
    ]);
  };
  // Clic droit sur une zone vide de la timeline.
  // Ferme le gap sous le clic : trouve le prochain clip à droite, calcule la
  // chaîne adjacente à partir de lui, et fait glisser tout ce bloc vers la
  // gauche pour combler le gap (avec le clip précédent ou t=0). Audio linked
  // suit (avLinked !== false → audioStart déplacé du même delta).
  const fillGapAt = (trackIdx: number, clickTime: number, silent = false): boolean => {
    const rowClips = tlClips.filter((c) => (c.videoTrackIndex ?? 0) === trackIdx).sort((a, b) => a.start - b.start);
    const nextIdx = rowClips.findIndex((c) => c.start > clickTime);
    if (nextIdx < 0) { if (!silent) toast("Kein Clip nach dieser Position.", "warn", 1500); return false; }
    const nextClip = rowClips[nextIdx];
    const prevEnd = nextIdx > 0 ? rowClips[nextIdx - 1].start + rowClips[nextIdx - 1].duration : 0;
    const gapDelta = nextClip.start - prevEnd;
    if (gapDelta <= 0.05) { if (!silent) toast("Keine Lücke an dieser Position.", "warn", 1500); return false; }
    const chainIds = new Set<string>([nextClip.tlId]);
    let lastEnd = nextClip.start + nextClip.duration;
    for (let i = nextIdx + 1; i < rowClips.length; i++) {
      const c = rowClips[i];
      if (Math.abs(c.start - lastEnd) < 0.05) {
        chainIds.add(c.tlId);
        lastEnd = c.start + c.duration;
      } else break;
    }
    snapshot();
    setTlClips((cur) => cur.map((c) => {
      if (!chainIds.has(c.tlId)) return c;
      const newStart = c.start - gapDelta;
      return {
        ...c,
        start: newStart,
        audioStart: c.avLinked !== false ? newStart : (c.audioStart ?? c.start),
      };
    }));
    toast(`Lücke geschlossen (${chainIds.size} Clip${chainIds.size > 1 ? "s" : ""} verschoben)`, "ok", 1500);
    return true;
  };

  const openEmptyMenu = (e: React.MouseEvent) => {
    e.preventDefault(); e.stopPropagation();
    const at = timePctFromEvent(e.clientX) * totalDuration;
    const trackIdx = nearestVideoTrack(e.clientY);
    openMenuAt(e.clientX, e.clientY, [
      { label: "Einfügen", kbd: "Cmd V", onClick: paste, disabled: clipboard.length === 0 },
      { separator: true },
      { label: "Lücke schließen", kbd: "Cmd G", onClick: () => fillGapAt(trackIdx, at) },
      { separator: true },
      { label: "Neue Video-Spur", onClick: addVideoTrack, disabled: numVideoTracks >= MAX_TRACKS },
      { label: "Neue Audio-Spur", onClick: addAudioTrack, disabled: numAudioTracks >= MAX_TRACKS },
      { label: "Marker hier hinzufügen", onClick: () => addMarkerAt(at) },
    ]);
  };

  const jumpMarker = (dir: "next" | "prev") => {
    if (markers.length === 0) { toast("Keine Marker gesetzt.", "warn"); return; }
    const sorted = [...markers].sort((a, b) => a.time - b.time);
    const target = dir === "next"
      ? sorted.find((m) => m.time > globalTime + 0.01)
      : [...sorted].reverse().find((m) => m.time < globalTime - 0.01);
    if (target) { seekSeconds(target.time); toast(target.label, "info", 1200); }
    else toast(dir === "next" ? "Letzter Marker erreicht." : "Erster Marker erreicht.", "warn", 1200);
  };

  const clearMarkers = () => {
    if (markers.length === 0) return;
    if (!confirm(`Alle ${markers.length} Marker löschen?`)) return;
    setMarkers([]);
    toast("Marker gelöscht.", "ok", 1500);
  };

  const toggleFullscreen = async () => {
    const el = previewContainerRef.current;
    if (!el) return;
    try {
      if (document.fullscreenElement) {
        await document.exitFullscreen();
      } else {
        await el.requestFullscreen();
      }
    } catch (e) {
      toast(`Vollbild nicht verfügbar: ${(e as Error).message}`, "warn", 3000);
    }
  };

  // Fix #8 : mise à jour continue du ref d'actions pour que le listener clavier reste attaché une fois
  // (sinon on ré-attache 60×/s pendant lecture et on rate des touches).
  kbActionsRef.current = {
    undo, redo, removeSelected, removeSelectedRipple, splitAtGlobalTime, addMarkerAtPlayhead,
    toggleFullscreen, zoomIn, zoomOut, zoomFit,
    togglePlay,
    seekBy: (delta: number) => seekSeconds(globalTime + delta),
    seekTo: (t: number) => seekSeconds(t),
    fillGapAt,
    fillGapAtPlayhead: () => {
      let filled = 0;
      for (let ti = 0; ti < numVideoTracks; ti++) {
        if (fillGapAt(ti, globalTime, true)) filled++;
      }
      if (filled === 0) toast("Keine Lücke am Abspielkopf.", "warn", 1500);
    },
    setInAtPlayhead: () => {
      const t = globalTime;
      setInPoint(t);
      // Si outPoint existe et que in >= out, on invalide out.
      setOutPoint((cur) => (cur !== null && cur <= t ? null : cur));
      toast(`In: ${fmtTC(t)}`, "ok", 1200);
    },
    setOutAtPlayhead: () => {
      const t = globalTime;
      setOutPoint(t);
      setInPoint((cur) => (cur !== null && cur >= t ? null : cur));
      toast(`Out: ${fmtTC(t)}`, "ok", 1200);
    },
    clearInPoint: () => { setInPoint(null); toast("In gelöscht.", "ok", 1000); },
    clearOutPoint: () => { setOutPoint(null); toast("Out gelöscht.", "ok", 1000); },
    splitAtInOut: () => {
      if (inPoint === null && outPoint === null) { toast("Kein In/Out gesetzt.", "warn", 1500); return; }
      const points = [inPoint, outPoint].filter((x): x is number => x !== null);
      let totalCuts = 0;
      snapshot();
      setTlClips((cur) => {
        let out = [...cur];
        for (const t of points) {
          const targets = out.filter((c) => t > c.start + 0.05 && t < c.start + c.duration - 0.05 && !isLocked(c.tlId));
          for (const clip of targets) {
            const idx = out.findIndex((x) => x.tlId === clip.tlId);
            if (idx === -1) continue;
            const c = out[idx];
            const cutLocal = t - c.start;
            const linked = c.avLinked !== false;
            const before: TLClip = { ...c, duration: cutLocal, avLinked: linked ? true : false, audioStart: linked ? undefined : c.start };
            const afterStart = c.start + cutLocal;
            const after: TLClip = {
              ...c,
              tlId: `${c.clipId}-${Date.now()}-${idx}-${Math.round(t * 1000)}-b`,
              start: afterStart,
              mediaStart: c.mediaStart + cutLocal,
              duration: c.duration - cutLocal,
              avLinked: linked ? true : false,
              audioStart: linked ? undefined : afterStart,
            };
            out.splice(idx, 1, before, after);
            totalCuts++;
          }
        }
        return out;
      });
      if (totalCuts === 0) toast("Kein Clip an In/Out.", "warn", 1500);
      else toast(`${totalCuts} Cut${totalCuts > 1 ? "s" : ""} an In/Out.`, "ok", 1500);
    },
    zoomToRange: () => {
      if (inPoint === null || outPoint === null) { toast("Kein Range gesetzt.", "warn", 1500); return; }
      const rangeDur = outPoint - inPoint;
      if (rangeDur <= 0 || totalDuration <= 0) return;
      const ratio = totalDuration / rangeDur;
      setZoom(clamp(ratio, MIN_ZOOM, MAX_ZOOM));
      // Scroll pour centrer sur inPoint après le paint suivant.
      requestAnimationFrame(() => {
        requestAnimationFrame(() => {
          const container = timelineRef.current;
          if (!container) return;
          const inner = container.firstElementChild as HTMLDivElement | null;
          if (!inner) return;
          const targetX = 16 + (inPoint / totalDuration) * inner.offsetWidth;
          container.scrollLeft = Math.max(0, targetX - 40);
        });
      });
    },
  };

  // Loop playback : si un range [in, out] est actif pendant la lecture, on
  // saute automatiquement à `in` dès que le playhead franchit `out`.
  useEffect(() => {
    if (!playing || inPoint === null || outPoint === null) return;
    if (globalTime >= outPoint) seekSeconds(inPoint);
  }, [globalTime, playing, inPoint, outPoint]);

  /* ─── Inspector (propriétés du clip sélectionné) ─────────────────────────────
     Panneau gauche façon DaVinci/Premiere. Lit `selectedTlIds` → 0/1/N clips. */
  const inspSecHeader: CSSProperties = { fontSize: 10, letterSpacing: 0.8, textTransform: "uppercase", color: "#6a6a6a", fontWeight: 600, margin: "0 0 6px" };
  const inspLabel: CSSProperties = { fontSize: 11, color: "#8a8a8a", flex: "none", width: 84 };
  const inspInput: CSSProperties = { background: "#242426", borderRadius: 4, padding: "4px 8px", border: "1px solid transparent", color: "#e6e6e6", fontSize: 12, fontFamily: "ui-monospace, monospace", width: "100%", minWidth: 0, outline: "none" };
  const inspRO: CSSProperties = { fontSize: 12, color: "#c0c0c0", fontFamily: "ui-monospace, monospace", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" };
  const inspRow: CSSProperties = { display: "flex", alignItems: "center", gap: 8 };
  const inspSection: CSSProperties = { display: "flex", flexDirection: "column", gap: 5, padding: "12px 14px", borderBottom: "1px solid #1c1c1e" };
  const inspSelect: CSSProperties = { ...inspInput, fontFamily: "inherit", cursor: "pointer" };

  const renderInspector = () => {
    const sel = tlClips.filter((c) => selectedTlIds.has(c.tlId));

    if (sel.length === 0) {
      return (
        <div style={{ flex: 1, display: "flex", alignItems: "center", justifyContent: "center", padding: 20, color: "#5a5a5a", fontSize: 12, textAlign: "center" }}>
          Kein Clip ausgewählt
        </div>
      );
    }

    if (sel.length > 1) {
      const aggDur = sel.reduce((a, c) => a + c.duration, 0);
      const v0 = sel[0].videoTrackIndex ?? 0;
      const sameV = sel.every((c) => (c.videoTrackIndex ?? 0) === v0);
      const audioClips = sel.filter((c) => c.hasAudio);
      const canLink = audioClips.length > 0 && audioClips.some((c) => c.avLinked === false) && !audioClips.every((c) => clipLocked(c));
      const canUnlink = audioClips.length > 0 && audioClips.some((c) => c.avLinked !== false) && !audioClips.every((c) => clipLocked(c));
      return (
        <div style={{ flex: 1, overflowY: "auto" }}>
          <div style={inspSection}>
            <div style={inspSecHeader}>Auswahl</div>
            <div style={{ fontSize: 13, color: "#e6e6e6", fontWeight: 600 }}>{sel.length} Clips ausgewählt</div>
            <div style={inspRow}><span style={inspLabel}>Dauer Σ</span><span style={inspRO}>{fmtMSF(aggDur)}</span></div>
            <div style={inspRow}>
              <span style={inspLabel}>Spur</span>
              <span style={{ ...inspRO, color: sameV ? "#c0c0c0" : "#8a8a8a" }}>
                {sameV ? <span style={{ background: "#242426", borderRadius: 4, padding: "2px 8px", fontSize: 11 }}>V{v0 + 1}</span> : "gemischt"}
              </span>
            </div>
          </div>
          <div style={inspSection}>
            <div style={inspSecHeader}>Aktionen</div>
            <button onClick={duplicateSelected} style={{ ...inspSelect, textAlign: "left" }}>Duplizieren</button>
            <button onClick={removeSelected} style={{ ...inspSelect, textAlign: "left", color: "#e08a8a" }}>Löschen</button>
            {canUnlink && <button onClick={() => setAvLinkedSelected(false)} style={{ ...inspSelect, textAlign: "left" }}>A/V trennen</button>}
            {canLink && <button onClick={() => setAvLinkedSelected(true)} style={{ ...inspSelect, textAlign: "left" }}>A/V verknüpfen</button>}
          </div>
        </div>
      );
    }

    const clip = sel[0];
    const locked = clipLocked(clip);
    const vTrack = clip.videoTrackIndex ?? 0;
    const aTrack = clip.audioTrackIndex ?? vTrack;
    const trackHidden = trackState(`v${vTrack}`).hidden;
    const trackMuted = clip.hasAudio && trackState(`a${aTrack}`).mute;

    return (
      <div style={{ flex: 1, overflowY: "auto" }}>
        {/* Info */}
        <div style={inspSection}>
          <div style={inspSecHeader}>Info</div>
          <div style={inspRow}>
            <span style={inspLabel}>Name</span>
            <CommitInput
              value={clip.name}
              style={{ ...inspInput, fontFamily: "inherit" }}
              onCommit={(raw) => {
                const name = raw.trim();
                if (!name) { toast("Name darf nicht leer sein.", "warn", 1800); return; }
                if (name === clip.name) return;
                patchClip(clip.tlId, { name });
              }}
            />
          </div>
          <div style={inspRow}><span style={inspLabel}>Quelle</span><span style={inspRO} title={clip.name || clip.clipId}>{clip.name || clip.clipId}</span></div>
          <div style={inspRow}><span style={inspLabel}>Dauer</span><span style={inspRO}>{fmtMSF(clip.duration)}</span></div>
        </div>

        {/* Position */}
        <div style={inspSection}>
          <div style={inspSecHeader}>Position</div>
          <div style={inspRow}>
            <span style={inspLabel}>Start</span>
            <CommitInput
              value={fmtMSF(clip.start)}
              disabled={locked}
              title="MM:SS.FF"
              style={inspInput}
              onCommit={(raw) => {
                const t = parseMSF(raw);
                if (t == null || t < 0) { toast("Ungültige Zeit (MM:SS.FF).", "warn", 1800); return; }
                if (Math.abs(t - clip.start) < 1e-4) return;
                patchClip(clip.tlId, clip.avLinked === false ? { start: t } : { start: t, audioStart: undefined });
              }}
            />
          </div>
          <div style={inspRow}>
            <span style={inspLabel}>Spur V</span>
            <select
              value={vTrack}
              disabled={locked}
              onChange={(e) => {
                const idx = parseInt(e.target.value, 10);
                if (idx < 0 || idx >= numVideoTracks) { toast("Spur außerhalb des Bereichs.", "warn", 1600); return; }
                patchClip(clip.tlId, { videoTrackIndex: idx });
              }}
              style={inspSelect}
            >
              {Array.from({ length: numVideoTracks }, (_, i) => <option key={i} value={i}>V{i + 1}</option>)}
            </select>
          </div>
          {clip.hasAudio && (
            <div style={inspRow}>
              <span style={inspLabel}>Spur A</span>
              <select
                value={aTrack}
                disabled={locked}
                onChange={(e) => {
                  const idx = parseInt(e.target.value, 10);
                  if (idx < 0 || idx >= numAudioTracks) { toast("Spur außerhalb des Bereichs.", "warn", 1600); return; }
                  patchClip(clip.tlId, { audioTrackIndex: idx });
                }}
                style={inspSelect}
              >
                {Array.from({ length: numAudioTracks }, (_, i) => <option key={i} value={i}>A{i + 1}</option>)}
              </select>
            </div>
          )}
        </div>

        {/* Source */}
        <div style={inspSection}>
          <div style={inspSecHeader}>Quelle</div>
          <div style={inspRow}>
            <span style={inspLabel}>Media-Start</span>
            <CommitInput
              value={fmtMSF(clip.mediaStart)}
              disabled={locked}
              title="MM:SS.FF"
              style={inspInput}
              onCommit={(raw) => {
                const t = parseMSF(raw);
                if (t == null || t < 0) { toast("Ungültige Zeit (MM:SS.FF).", "warn", 1800); return; }
                if (clip.sourceDuration > 0 && t + clip.duration > clip.sourceDuration + 1e-3) { toast("Media-Start überläuft die Quelle.", "warn", 2000); return; }
                if (Math.abs(t - clip.mediaStart) < 1e-4) return;
                patchClip(clip.tlId, { mediaStart: t });
              }}
            />
          </div>
          <div style={inspRow}><span style={inspLabel}>Quelldauer</span><span style={inspRO}>{fmtMSF(clip.sourceDuration)}</span></div>
        </div>

        {/* Audio */}
        {clip.hasAudio && (
          <div style={inspSection}>
            <div style={inspSecHeader}>Audio</div>
            <div style={inspRow}>
              <span style={inspLabel}>A/V verknüpft</span>
              <button
                onClick={() => { if (locked) { toast("Clip ist gesperrt.", "warn", 1500); return; } clip.avLinked === false ? linkAV(clip.tlId) : unlinkAV(clip.tlId); }}
                title={clip.avLinked === false ? "Getrennt — klicken zum Verknüpfen" : "Verknüpft — klicken zum Trennen"}
                style={{ display: "flex", alignItems: "center", gap: 6, background: "#242426", borderRadius: 4, padding: "4px 8px", border: "none", cursor: locked ? "not-allowed" : "pointer", color: clip.avLinked === false ? "#8a8a8a" : "#b9d94a", fontSize: 12 }}
              >
                <S w={14} sw={2} c={clip.avLinked === false ? "#8a8a8a" : "#b9d94a"}><path d="M10 13a5 5 0 0 0 7 0l1-1a5 5 0 0 0-7-7l-1 1M14 11a5 5 0 0 0-7 0l-1 1a5 5 0 0 0 7 7l1-1" /></S>
                {clip.avLinked === false ? "Getrennt" : "Verknüpft"}
              </button>
            </div>
            {clip.avLinked === false && (
              <div style={inspRow}>
                <span style={inspLabel}>Audio-Start</span>
                <CommitInput
                  value={fmtMSF(clip.audioStart ?? clip.start)}
                  disabled={locked}
                  title="MM:SS.FF"
                  style={inspInput}
                  onCommit={(raw) => {
                    const t = parseMSF(raw);
                    if (t == null || t < 0) { toast("Ungültige Zeit (MM:SS.FF).", "warn", 1800); return; }
                    if (Math.abs(t - (clip.audioStart ?? clip.start)) < 1e-4) return;
                    patchClip(clip.tlId, { audioStart: t });
                  }}
                />
              </div>
            )}
          </div>
        )}

        {/* State */}
        <div style={inspSection}>
          <div style={inspSecHeader}>Status</div>
          <label style={{ ...inspRow, cursor: "pointer" }}>
            <input
              type="checkbox"
              checked={isLocked(clip.tlId)}
              onChange={() => {
                // lockedTlIds vit hors de l'historique tlClips (comme toggleLockSelected) → pas de snapshot.
                setLockedTlIds((cur) => { const n = new Set(cur); n.has(clip.tlId) ? n.delete(clip.tlId) : n.add(clip.tlId); return n; });
              }}
              style={{ accentColor: "#b9d94a" }}
            />
            <span style={{ fontSize: 12, color: "#c0c0c0" }}>Gesperrt</span>
          </label>
          {(trackHidden || trackMuted) && (
            <div style={{ fontSize: 11, color: "#8a8a8a", lineHeight: 1.4 }}>
              {trackHidden && <div>⚠ Videospur ausgeblendet</div>}
              {trackMuted && <div>⚠ Audiospur stummgeschaltet</div>}
            </div>
          )}
        </div>
      </div>
    );
  };

  return (
    <div style={{ width: "100%", height: "100%", background: "#0c0c0d", overflow: "hidden", display: "flex", flexDirection: "column", color: "#e9e9e9" }}>

      {/* ─── Obere Leiste ─── */}
      <div style={{ height: 44, flex: "none", display: "flex", alignItems: "center", padding: "0 18px", background: "#161617", borderBottom: "1px solid #000", fontSize: 13 }}>
        <button onClick={goHome} title="Zurück zur Übersicht"><S w={17} sw={2} c="#cfcfcf"><path d="M19 12H5M12 19l-7-7 7-7" /></S></button>
        <input
          value={projectName}
          onChange={(e) => setProjectName(e.target.value)}
          style={{ marginLeft: 12, fontWeight: 600, color: "#f0f0f0", background: "none", border: "none", outline: "none", fontFamily: "inherit", fontSize: 13, letterSpacing: -0.2, minWidth: 200 }}
        />
        <nav style={{ flex: 1, display: "flex", justifyContent: "center", gap: 32, color: "#c4c4c4", position: "relative" }}>
          {(() => {
            const menuBtn: CSSProperties = { color: "#c4c4c4", position: "relative" };
            const dropdown: CSSProperties = { position: "absolute", top: 34, left: 0, background: "#242426", borderRadius: 8, padding: 4, zIndex: 50, boxShadow: "0 8px 24px rgba(0,0,0,.5)", minWidth: 180 };
            const item: CSSProperties = { display: "block", width: "100%", textAlign: "left", padding: "7px 12px", borderRadius: 6, fontSize: 13, color: "#cfcfcf", background: "transparent", border: "none", cursor: "pointer", fontFamily: "inherit", whiteSpace: "nowrap" };
            const sep: CSSProperties = { height: 1, background: "#1c1c1e", margin: "4px 0" };
            const kbd = (s: string) => <span style={{ marginLeft: 12, fontSize: 10, color: "#7a7a7a", fontFamily: "ui-monospace, monospace" }}>{s}</span>;
            const menuAction = (fn: () => void) => () => { fn(); setOpenMenu(null); };
            const menus: { key: string; label: string; items: React.ReactNode }[] = [
              { key: "datei", label: "Datei", items: <>
                <button style={item} onClick={menuAction(newTimeline)}>Neu {kbd("Cmd N")}</button>
                <button style={item} onClick={menuAction(() => setHistOpen(true))}>Öffnen… {kbd("Cmd O")}</button>
                <button style={item} onClick={menuAction(importFromMedia)}>Aus Medien importieren</button>
                <div style={sep} />
                <button style={item} onClick={menuAction(saveNow)}>Speichern {kbd("Cmd S")}</button>
              </>},
              { key: "bearb", label: "Bearbeiten", items: <>
                <button style={item} onClick={menuAction(undo)}>Rückgängig {kbd("Cmd Z")}</button>
                <button style={item} onClick={menuAction(redo)}>Wiederholen {kbd("Cmd ⇧Z")}</button>
                <div style={sep} />
                <button style={item} onClick={menuAction(cutSelected)}>Ausschneiden {kbd("Cmd X")}</button>
                <button style={item} onClick={menuAction(copySelected)}>Kopieren {kbd("Cmd C")}</button>
                <button style={item} onClick={menuAction(paste)}>Einfügen {kbd("Cmd V")}</button>
                <div style={sep} />
                <button style={item} onClick={menuAction(duplicateSelected)}>Duplizieren {kbd("Cmd D")}</button>
                <button style={item} onClick={menuAction(removeSelected)}>Löschen {kbd("⌫")}</button>
              </>},
              { key: "trim", label: "Trimmen", items: <>
                <button style={item} onClick={menuAction(() => trimSelected("left"))}>Links trimmen (−0,5 s)</button>
                <button style={item} onClick={menuAction(() => trimSelected("right"))}>Rechts trimmen (−0,5 s)</button>
                <button style={item} onClick={menuAction(() => trimSelected("both"))}>Beide Seiten (−1 s)</button>
                <div style={sep} />
                <button style={item} onClick={menuAction(() => trimSelected("left", 1))}>Links −1 s</button>
                <button style={item} onClick={menuAction(() => trimSelected("right", 1))}>Rechts −1 s</button>
              </>},
              { key: "clip", label: "Clip", items: <>
                <button style={item} onClick={menuAction(splitAtGlobalTime)}>Schneiden {kbd("C")}</button>
                <button style={item} onClick={menuAction(duplicateSelected)}>Duplizieren {kbd("Cmd D")}</button>
                <div style={sep} />
                <button style={item} onClick={menuAction(removeSelected)}>Löschen {kbd("⌫")}</button>
                <button style={item} onClick={menuAction(() => setSelectedTlIds(new Set(tlClips.map((c) => c.tlId))))}>Alle auswählen {kbd("Cmd A")}</button>
              </>},
              { key: "marke", label: "Marke", items: <>
                <button style={item} onClick={menuAction(addMarkerAtPlayhead)}>Marker setzen {kbd("M")}</button>
                <button style={item} onClick={menuAction(() => jumpMarker("prev"))}>Voriger Marker</button>
                <button style={item} onClick={menuAction(() => jumpMarker("next"))}>Nächster Marker</button>
                <div style={sep} />
                <div style={{ padding: "4px 12px", fontSize: 10, color: "#7a7a7a" }}>{markers.length} gesetzt</div>
                <button style={item} onClick={menuAction(clearMarkers)}>Alle löschen</button>
              </>},
              { key: "ansicht", label: "Ansicht", items: <>
                <button style={item} onClick={menuAction(zoomIn)}>Einzoomen {kbd("Cmd +")}</button>
                <button style={item} onClick={menuAction(zoomOut)}>Auszoomen {kbd("Cmd −")}</button>
                <button style={item} onClick={menuAction(zoomFit)}>Anpassen {kbd("Cmd 0")}</button>
                <div style={sep} />
                <button style={item} onClick={menuAction(toggleFullscreen)}>Vollbild {kbd("F")}</button>
                <button style={item} onClick={menuAction(toggleChatPanel)}>KI-Panel umschalten</button>
                <button style={item} onClick={menuAction(() => setHistOpen((o) => !o))}>Verlauf umschalten</button>
              </>},
            ];
            return menus.map((m) => (
              <div key={m.key} data-menu style={{ position: "relative" }}>
                <button
                  onClick={() => setOpenMenu((cur) => (cur === m.key ? null : m.key))}
                  onMouseEnter={(e) => (e.currentTarget.style.color = "#fff")}
                  onMouseLeave={(e) => (e.currentTarget.style.color = openMenu === m.key ? "#fff" : "#c4c4c4")}
                  style={{ ...menuBtn, color: openMenu === m.key ? "#fff" : "#c4c4c4" }}
                >
                  {m.label}
                </button>
                {openMenu === m.key && <div style={dropdown}>{m.items}</div>}
              </div>
            ));
          })()}
        </nav>
        <button onClick={saveNow} disabled={saveStatus === "saving" || tlClips.length === 0}
          style={{ display: "flex", alignItems: "center", gap: 6, color: saveStatus === "saved" ? "#6ad04a" : saveStatus === "error" ? "#e07a7a" : "#c4c4c4", fontSize: 13, marginRight: 16, opacity: tlClips.length === 0 ? 0.5 : 1 }}>
          <S w={16} c="currentColor"><path d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2z" /><path d="M17 21v-8H7v8M7 3v5h8" /></S>
          {saveStatus === "saving" ? "Speichern…" : saveStatus === "saved" ? "Gespeichert" : saveStatus === "error" ? "Fehler" : "Speichern"}
        </button>
        <button
          onClick={() => toast("Cloud-Sync noch nicht verfügbar — Timelines liegen lokal in Postgres (siehe Verlauf).", "info", 4000)}
          style={{ display: "flex", alignItems: "center", gap: 6, color: "#c4c4c4", fontSize: 13 }}>
          <S w={17} c="#c4c4c4"><path d="M18 10h-1.26A8 8 0 1 0 9 20h9a5 5 0 0 0 0-10z" /></S>Cloud
        </button>
        <div data-menu style={{ position: "relative", marginLeft: 14 }}>
          <button onClick={() => setOpenMenu((c) => c === "more" ? null : "more")}
            style={{ width: 28, height: 28, borderRadius: "50%", background: openMenu === "more" ? "#3a3a3e" : "#242426", display: "flex", alignItems: "center", justifyContent: "center" }} title="Mehr">
            <svg width={16} height={16} viewBox="0 0 24 24" fill="#c4c4c4"><circle cx="5" cy="12" r="1.6" /><circle cx="12" cy="12" r="1.6" /><circle cx="19" cy="12" r="1.6" /></svg>
          </button>
          {openMenu === "more" && (
            <div style={{ position: "absolute", top: 34, right: 0, background: "#242426", borderRadius: 8, padding: 4, zIndex: 50, boxShadow: "0 8px 24px rgba(0,0,0,.5)", minWidth: 200 }}>
              <button style={{ display: "block", width: "100%", textAlign: "left", padding: "7px 12px", borderRadius: 6, fontSize: 13, color: "#cfcfcf", background: "transparent", border: "none", cursor: "pointer", fontFamily: "inherit" }} onClick={() => { setSettingsOpen(true); setOpenMenu(null); }}>Einstellungen…</button>
              <button style={{ display: "block", width: "100%", textAlign: "left", padding: "7px 12px", borderRadius: 6, fontSize: 13, color: "#cfcfcf", background: "transparent", border: "none", cursor: "pointer", fontFamily: "inherit" }} onClick={() => { toggleFullscreen(); setOpenMenu(null); }}>Vollbild</button>
              <button style={{ display: "block", width: "100%", textAlign: "left", padding: "7px 12px", borderRadius: 6, fontSize: 13, color: "#cfcfcf", background: "transparent", border: "none", cursor: "pointer", fontFamily: "inherit" }} onClick={() => { toast(`${tlClips.length} Clips · ${fmtSec(totalDuration)} · Zoom ${Math.round(zoom * 100)}%`, "info", 4000); setOpenMenu(null); }}>Statistiken</button>
            </div>
          )}
        </div>
      </div>

      {/* ─── Mittelbereich + Timeline : redimensionnables (Wave 3) ───
          PanelGroup vertical (haut/bas) → à l'intérieur du haut, PanelGroup
          horizontal (viewer | médias). autoSaveId persiste les tailles en
          localStorage. */}
      <PanelGroup direction="vertical" autoSaveId="cinassist-main-layout" style={{ flex: 1, minHeight: 0 }}>
        <Panel defaultSize={62} minSize={30} style={{ minHeight: 0 }}>
          <PanelGroup direction="horizontal" autoSaveId="cinassist-top-layout-v2" style={{ height: "100%" }}>
            {/* Inspector-Panel (GAUCHE) — propriétés du clip sélectionné */}
            <Panel
              ref={inspectorPanelRef}
              defaultSize={22}
              minSize={15}
              collapsible
              collapsedSize={0}
              onCollapse={() => setInspectorCollapsed(true)}
              onExpand={() => setInspectorCollapsed(false)}
              style={{ minWidth: 0 }}
            >
              <div style={{ height: "100%", width: "100%", display: "flex", flexDirection: "column", background: "#161617", borderRight: "1px solid #000" }}>
                <div style={{ height: 56, flex: "none", display: "flex", alignItems: "center", padding: "0 16px", gap: 8, borderBottom: "1px solid #1c1c1e" }}>
                  <span style={{ fontSize: 13, fontWeight: 600, color: "#c4c4c4" }}>Inspektor</span>
                  {selectedTlIds.size > 0 && (
                    <span style={{ fontSize: 10, color: "#6a6a6a", fontFamily: "ui-monospace, monospace", marginLeft: "auto" }}>{selectedTlIds.size} ausgewählt</span>
                  )}
                  <button
                    onClick={() => inspectorPanelRef.current?.collapse()}
                    title="Inspektor schließen"
                    style={{ marginLeft: selectedTlIds.size > 0 ? 8 : "auto", width: 22, height: 22, borderRadius: 4, background: "transparent", border: "none", color: "#8a8a8a", cursor: "pointer", display: "flex", alignItems: "center", justifyContent: "center", padding: 0 }}
                    onMouseEnter={(e) => { e.currentTarget.style.background = "#2a2a2e"; e.currentTarget.style.color = "#fff"; }}
                    onMouseLeave={(e) => { e.currentTarget.style.background = "transparent"; e.currentTarget.style.color = "#8a8a8a"; }}
                  >
                    <svg width={14} height={14} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round"><path d="M18 6 6 18M6 6l12 12" /></svg>
                  </button>
                </div>
                {renderInspector()}
              </div>
            </Panel>
            {inspectorCollapsed && (
              <button
                onClick={() => inspectorPanelRef.current?.expand()}
                title="Inspektor öffnen"
                style={{ position: "absolute", left: 0, top: 78, width: 22, height: 60, borderRadius: "0 6px 6px 0", background: "#242426", border: "1px solid #2a2a2e", borderLeft: "none", color: "#c4c4c4", cursor: "pointer", display: "flex", alignItems: "center", justifyContent: "center", zIndex: 20, padding: 0 }}
                onMouseEnter={(e) => { e.currentTarget.style.background = "#2a2a2e"; }}
                onMouseLeave={(e) => { e.currentTarget.style.background = "#242426"; }}
              >
                <svg width={14} height={14} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round"><path d="m9 18 6-6-6-6" /></svg>
              </button>
            )}

            <PanelResizeHandle style={{ width: 6, cursor: "col-resize" }}>
              <div onMouseEnter={(e) => (e.currentTarget.style.background = "#2a2a2e")} onMouseLeave={(e) => (e.currentTarget.style.background = "transparent")}
                style={{ width: "100%", height: "100%", display: "flex", justifyContent: "center" }}>
                <div style={{ width: 1, height: "100%", background: "#000" }} />
              </div>
            </PanelResizeHandle>

            <Panel defaultSize={50} minSize={30} style={{ minWidth: 0 }}>
              <div style={{ height: "100%", display: "flex", flexDirection: "column", minWidth: 0 }}>
          <div style={{ height: 56, flex: "none", display: "flex", alignItems: "center", padding: "0 16px", gap: 12 }}>
            <button
              onClick={toggleFullscreen}
              style={{ width: 36, height: 36, borderRadius: 9, background: "#1c1c1e", display: "flex", alignItems: "center", justifyContent: "center" }} title="Vollbild-Vorschau">
              <S w={17} sw={2}><path d="M3 8V5a2 2 0 0 1 2-2h3M16 3h3a2 2 0 0 1 2 2v3M21 16v3a2 2 0 0 1-2 2h-3M8 21H5a2 2 0 0 1-2-2v-3" /></S>
            </button>
            <div style={{ flex: 1, display: "flex", alignItems: "center", justifyContent: "center", gap: 8, fontSize: 14 }}>
              <span style={{ fontWeight: 600 }}>{projectName || "Ohne Titel"}</span>
              <span style={{ color: "#7a7a7a", fontSize: 12 }}>{clips[0]?.aufloesung || "—"}</span>
              <span style={{ color: "#7a7a7a", fontSize: 12 }}>‖ {Math.round(clips[0]?.bildrate || 30)} fps</span>
              <span style={{ color: "#7a7a7a", fontSize: 12 }}>‖ {tlClips.length} Clips</span>
            </div>
            <div style={{ display: "flex", gap: 8 }}>
              <button onClick={addMarkerAtPlayhead} title={`Marker setzen (${markers.length} vorhanden)`}
                style={{ width: 38, height: 36, borderRadius: 9, background: markers.length > 0 ? "#3a2d0d" : "#1c1c1e", display: "flex", alignItems: "center", justifyContent: "center" }}>
                <S c={markers.length > 0 ? "#e0b84a" : "#c9c9c9"}><path d="M15 4l5 5M17 6L7.5 15.5 4 20l4.5-.5L18 10" /></S>
              </button>
              <button onClick={toggleChatPanel} title="KI-Agent"
                style={{ width: 38, height: 36, borderRadius: 9, background: chatPanelOpen ? "#e5c100" : "#1c1c1e", display: "flex", alignItems: "center", justifyContent: "center", boxShadow: chatPanelOpen ? "0 0 0 1px rgba(0,0,0,0.4), 0 2px 8px rgba(229,193,0,0.4)" : "none" }}>
                <S c={chatPanelOpen ? "#000" : "#c9c9c9"}><path d="M5 3v4M3 5h4M6 17v4M4 19h4M13 3l2.5 6.5L22 12l-6.5 2.5L13 21l-2.5-6.5L4 12l6.5-2.5z" /></S>
              </button>
              <button onClick={() => { setTab("color"); toast("Farbe-Modus — Farbrad-Tools folgen.", "info", 2500); }} title="Farbrad"
                style={{ width: 38, height: 36, borderRadius: 9, background: tab === "color" ? "#1a3a3e" : "#1c1c1e", display: "flex", alignItems: "center", justifyContent: "center" }}>
                <div style={{ width: 18, height: 18, borderRadius: "50%", background: "conic-gradient(#ff4d4d,#ffd24d,#5dff8f,#4dd2ff,#a04dff,#ff4d9d,#ff4d4d)" }} />
              </button>
            </div>
          </div>

          {/* Vorschau */}
          <div ref={previewContainerRef} style={{ flex: 1, margin: "0 16px", borderRadius: 12, overflow: "hidden", position: "relative", background: "#000", minHeight: 0 }}>
            {/* Player-Pool : le PlaybackEngine monte ici ses 2 <video> (active +
                standby préchauffé). Il gère src/seek/drift ; on n'y touche pas.
                Pendant un scrub, on masque le pool (le strip-overlay prend le
                relais) sans démonter les éléments (buffer conservé). */}
            <div
              ref={playerContainerRef}
              style={{ position: "absolute", inset: 0, background: "#000" }}
            />
            {!activeTlClip && (
              <div style={{ position: "absolute", inset: 0, background: "#000" }}>
                <div style={{ position: "absolute", top: "40%", left: 0, right: 0, textAlign: "center", color: "rgba(255,255,255,0.85)", fontSize: 14, fontWeight: 500 }}>
                  {loading ? "Clips werden geladen…" : error ? `Backend nicht erreichbar: ${error}` : "Keine Vorschau — wähle einen Clip"}
                </div>
              </div>
            )}
            {/* Scrub-Preview overlay désactivé : le pool de <video> préchargés
                du PlaybackEngine gère le seek en temps réel avec la vraie résolution
                de la source. Les tuiles _strip.jpg (80×45) étirées au player étaient
                très pixelisées — la vidéo native offre une bien meilleure qualité. */}
            {activeTlClip && (
              <button onClick={togglePlay} title={playing ? "Pause" : "Abspielen"}
                style={{ position: "absolute", top: "50%", left: "50%", transform: "translate(-50%,-50%)", zIndex: 10, width: 82, height: 82, borderRadius: "50%", background: "rgba(210,210,210,.55)", backdropFilter: "blur(2px)", display: "flex", alignItems: "center", justifyContent: "center", opacity: playing ? 0 : 1, transition: "opacity 0.2s", pointerEvents: playing ? "none" : "auto" }}>
                {playing
                  ? <svg width={26} height={26} viewBox="0 0 24 24" fill="#f2f2f2"><rect x="7" y="5" width="4" height="14" rx="1" /><rect x="13" y="5" width="4" height="14" rx="1" /></svg>
                  : <svg width={28} height={28} viewBox="0 0 24 24" fill="#f2f2f2"><path d="M8 5v14l11-7z" /></svg>}
              </button>
            )}
          </div>

          {/* Transport */}
          <div style={{ height: 68, flex: "none", display: "flex", alignItems: "center", padding: "0 16px", gap: 12 }}>
            <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
              <button onClick={() => seekSeconds(globalTime - 1)} title="1 s zurück"><S w={22} sw={2}><path d="M11 7l-6 5 6 5M18 7l-6 5 6 5" /></S></button>
              <span style={{ fontSize: 14, fontVariantNumeric: "tabular-nums", letterSpacing: 0.5 }}>{fmtTC(globalTime)}</span>
              <button onClick={() => seekSeconds(globalTime + 1)} title="1 s vor"><S w={22} sw={2}><path d="M13 7l6 5-6 5M6 7l6 5-6 5" /></S></button>
            </div>
            <div style={{ flex: 1, display: "flex", alignItems: "center", justifyContent: "center", gap: 22 }}>
              <button onClick={() => seekSeconds(0)} title="Zum Anfang"><svg width={20} height={20} viewBox="0 0 24 24" fill="#cfcfcf"><path d="M6 6h2v12H6zM20 6L9 12l11 6z" /></svg></button>
              <button onClick={pause} title="Stopp"><svg width={18} height={18} viewBox="0 0 24 24" fill="#cfcfcf"><rect x="6" y="6" width="12" height="12" rx="1.5" /></svg></button>
              <button onClick={togglePlay} title={playing ? "Pause" : "Abspielen"}
                style={{ width: 46, height: 46, borderRadius: "50%", background: playing ? "#3a3a3e" : "#242426", display: "flex", alignItems: "center", justifyContent: "center" }}>
                {playing
                  ? <svg width={20} height={20} viewBox="0 0 24 24" fill="#f0f0f0"><rect x="7" y="5" width="4" height="14" rx="1" /><rect x="13" y="5" width="4" height="14" rx="1" /></svg>
                  : <svg width={22} height={22} viewBox="0 0 24 24" fill="#f0f0f0"><path d="M8 5v14l11-7z" /></svg>}
              </button>
              <button onClick={() => seekSeconds(totalDuration)} title="Zum Ende"><svg width={20} height={20} viewBox="0 0 24 24" fill="#cfcfcf"><path d="M18 6h-2v12h2zM4 6l11 6L4 18z" /></svg></button>
            </div>
            <div style={{ display: "flex", alignItems: "center", gap: 10, position: "relative" }}>
              {/* Lautstärke (Wave 1 : déplacé de la barre d'outils vers le transport) */}
              <div data-menu style={{ position: "relative" }}>
                <button onClick={() => setOpenMenu((c) => c === "vol" ? null : "vol")} title={`Lautstärke ${Math.round(volume * 100)}%${muted ? " (stumm)" : ""}`}
                  style={{ display: "flex", alignItems: "center", gap: 6, background: "#1c1c1e", borderRadius: 8, height: 36, padding: "0 12px", fontSize: 12, color: muted ? "#e07a7a" : "#cfcfcf" }}>
                  {muted
                    ? <S w={16} sw={1.7}><path d="M11 5L6 9H2v6h4l5 4V5zM23 9l-6 6M17 9l6 6" /></S>
                    : <S w={16} sw={1.7}><path d="M11 5L6 9H2v6h4l5 4V5z" />{volume > 0.33 && <path d="M15.5 8.5a5 5 0 0 1 0 7" />}{volume > 0.66 && <path d="M19 5a10 10 0 0 1 0 14" />}</S>}
                  <span style={{ fontSize: 10, color: "#8a8a8a", fontFamily: "ui-monospace, monospace" }}>{Math.round(volume * 100)}%</span>
                </button>
                {openMenu === "vol" && (
                  <div style={{ position: "absolute", bottom: 44, right: 0, background: "#242426", borderRadius: 8, padding: 12, zIndex: 50, boxShadow: "0 8px 24px rgba(0,0,0,.5)", minWidth: 200, display: "flex", flexDirection: "column", gap: 8 }}>
                    <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", fontSize: 11, color: "#cfcfcf" }}>
                      <span>Lautstärke</span>
                      <span style={{ fontFamily: "ui-monospace, monospace" }}>{Math.round(volume * 100)}%</span>
                    </div>
                    <input type="range" min={0} max={1} step={0.01} value={volume}
                      onChange={(e) => { setVolume(parseFloat(e.target.value)); if (muted) setMuted(false); }}
                      style={{ width: "100%", accentColor: "#b9d94a" }} />
                    <button onClick={() => setMuted((m) => !m)}
                      style={{ padding: "6px 10px", borderRadius: 6, fontSize: 11, background: muted ? "#3a0d0d" : "#1a1a1c", color: muted ? "#e07a7a" : "#cfcfcf", border: `1px solid ${muted ? "#e07a7a" : "#1c1c1e"}`, cursor: "pointer" }}>
                      {muted ? "Ton wieder an" : "Stummschalten"}
                    </button>
                  </div>
                )}
              </div>
              <button onClick={() => setFitOpen((o) => !o)} style={{ display: "flex", alignItems: "center", gap: 8, background: "#1c1c1e", borderRadius: 8, height: 36, padding: "0 14px", fontSize: 13, color: "#cfcfcf" }}>
                {fitMode}<S w={11} c="#8a8a8a" sw={2.4}><path d="M6 9l6 6 6-6" /></S>
              </button>
              {fitOpen && (
                <div style={{ position: "absolute", bottom: 44, right: 46, background: "#242426", borderRadius: 8, padding: 4, zIndex: 10, boxShadow: "0 8px 24px rgba(0,0,0,.5)" }}>
                  {FIT_MODES.map((m) => (
                    <button key={m} onClick={() => { setFitMode(m); setFitOpen(false); }}
                      style={{ display: "block", width: "100%", textAlign: "left", padding: "7px 12px", borderRadius: 6, fontSize: 13, color: m === fitMode ? "#b9d94a" : "#cfcfcf", whiteSpace: "nowrap" }}>{m}</button>
                  ))}
                </div>
              )}
              <button onClick={toggleFullscreen} style={{ width: 38, height: 36, borderRadius: 8, background: "#1c1c1e", display: "flex", alignItems: "center", justifyContent: "center" }} title="Vollbild">
                <S sw={2}><path d="M15 3h6v6M9 21H3v-6M21 3l-7 7M3 21l7-7" /></S>
              </button>
            </div>
          </div>
              </div>
            </Panel>

            <PanelResizeHandle style={{ width: 6, cursor: "col-resize" }}>
              <div onMouseEnter={(e) => (e.currentTarget.style.background = "#2a2a2e")} onMouseLeave={(e) => (e.currentTarget.style.background = "transparent")}
                style={{ width: "100%", height: "100%", display: "flex", justifyContent: "center" }}>
                <div style={{ width: 1, height: "100%", background: "#000" }} />
              </div>
            </PanelResizeHandle>

            {/* Medien-Panel */}
            <Panel defaultSize={28} minSize={15} style={{ minWidth: 0 }}>
              <div style={{ height: "100%", width: "100%", display: "flex", flexDirection: "column", borderLeft: "1px solid #000", background: "#0f0f10" }}>
          <div style={{ height: 56, flex: "none", display: "flex", alignItems: "center", padding: "0 16px", gap: 10, borderBottom: "1px solid #1c1c1e", position: "relative" }}>
            <div data-menu style={{ position: "relative" }}>
              <button onClick={() => setOpenMenu((c) => c === "filter" ? null : "filter")} style={{ display: "flex", alignItems: "center", gap: 6, color: mediaFilter !== "all" ? "#e0b84a" : "#c4c4c4", fontSize: 13, fontWeight: 600 }}>
                <S c={mediaFilter !== "all" ? "#e0b84a" : "#c4c4c4"}><path d="M4 6h10M4 12h7M4 18h12" /><circle cx="18" cy="6" r="2" /><circle cx="15" cy="12" r="2" /><circle cx="20" cy="18" r="2" /></S>Filter
              </button>
              {openMenu === "filter" && (
                <div style={{ position: "absolute", top: 32, left: 0, background: "#242426", borderRadius: 8, padding: 4, zIndex: 50, boxShadow: "0 8px 24px rgba(0,0,0,.5)", minWidth: 140 }}>
                  {(["all", "video", "audio"] as const).map((f) => (
                    <button key={f} onClick={() => { setMediaFilter(f); setOpenMenu(null); }}
                      style={{ display: "block", width: "100%", textAlign: "left", padding: "7px 12px", borderRadius: 6, fontSize: 12, color: mediaFilter === f ? "#e0b84a" : "#cfcfcf", background: "transparent", border: "none", cursor: "pointer", fontFamily: "inherit" }}>
                      {f === "all" ? "Alle" : f === "video" ? "Nur Video" : "Nur Audio"}
                    </button>
                  ))}
                </div>
              )}
            </div>
            <span style={{ flex: 1, textAlign: "center", fontSize: 15, fontWeight: 600 }}>Medien</span>
            <div data-menu style={{ position: "relative" }}>
              <button onClick={() => setOpenMenu((c) => c === "sort" ? null : "sort")} title="Sortieren">
                <S w={17} c={mediaSort !== "default" ? "#e0b84a" : "#9a9a9a"}><path d="M3 6h13M3 12h9M3 18h5M18 8v10M18 18l3-3M18 18l-3-3" /></S>
              </button>
              {openMenu === "sort" && (
                <div style={{ position: "absolute", top: 30, right: 0, background: "#242426", borderRadius: 8, padding: 4, zIndex: 50, boxShadow: "0 8px 24px rgba(0,0,0,.5)", minWidth: 160 }}>
                  {([
                    ["default", "Standard"],
                    ["name", "Name (A→Z)"],
                    ["duration", "Dauer (lang→kurz)"],
                    ["recent", "Zuletzt zuerst"],
                  ] as const).map(([k, l]) => (
                    <button key={k} onClick={() => { setMediaSort(k); setOpenMenu(null); }}
                      style={{ display: "block", width: "100%", textAlign: "left", padding: "7px 12px", borderRadius: 6, fontSize: 12, color: mediaSort === k ? "#e0b84a" : "#cfcfcf", background: "transparent", border: "none", cursor: "pointer", fontFamily: "inherit" }}>
                      {l}
                    </button>
                  ))}
                </div>
              )}
            </div>
            <button onClick={() => setMediaView((v) => v === "grid" ? "list" : "grid")} title={mediaView === "grid" ? "Zur Listen-Ansicht" : "Zur Raster-Ansicht"}>
              {mediaView === "grid"
                ? <S w={17} c="#9a9a9a"><path d="M3 6h18M3 12h18M3 18h18" /></S>
                : <S w={17} c="#9a9a9a"><rect x="3" y="3" width="7" height="7" rx="1.5" /><rect x="14" y="3" width="7" height="7" rx="1.5" /><rect x="3" y="14" width="7" height="7" rx="1.5" /><rect x="14" y="14" width="7" height="7" rx="1.5" /></S>}
            </button>
            <button title="CLIP-Suche" onClick={() => setShowSearch((v) => !v)}>
              <S w={17} c={showSearch ? "#e0b84a" : "#9a9a9a"}><circle cx="11" cy="11" r="7" /><path d="M21 21l-4-4" /></S>
            </button>
          </div>

          {showSearch && (
            <div style={{ padding: "10px 16px 4px" }}>
              <form onSubmit={(e) => { e.preventDefault(); runSearch(); }} style={{ display: "flex", gap: 6 }}>
                <input value={search} onChange={(e) => setSearch(e.target.value)} placeholder="z. B. 'sunset drone shot'…"
                  style={{ flex: 1, height: 32, padding: "0 10px", background: "#1a1a1c", border: "1px solid #232326", borderRadius: 8, color: "#e0e0e0", fontSize: 12, fontFamily: "inherit", outline: "none" }} />
                <button type="submit" style={{ padding: "0 12px", height: 32, borderRadius: 8, background: "#3a2a5a", color: "#c9a4ff", fontSize: 12, fontWeight: 600, border: "1px solid #4a3a6a" }}>Suchen</button>
              </form>
              {searchResults.length > 0 && (
                <div style={{ marginTop: 8, maxHeight: 120, overflowY: "auto", background: "#151517", borderRadius: 8, padding: 4 }}>
                  {searchResults.map((r) => (
                    <button
                      key={r.scene_id}
                      onClick={() => {
                        // Trouve un clip sur la timeline avec le même clip_name → seek à son start
                        const tl = tlClips.find((c) => c.name === r.clip_name.replace(/\.[^/.]+$/, ""));
                        if (tl) {
                          seekSeconds(tl.start);
                          setSelectedTlIds(new Set([tl.tlId]));
                          toast(`Sprung zu ${r.clip_name}.`, "ok", 1500);
                        } else {
                          // Sinon on ajoute le clip source à la fin de la timeline
                          const src = clips.find((c) => c.dateiname === r.clip_name);
                          if (src) {
                            appendClip(src.id);
                            toast(`${r.clip_name} zur Timeline hinzugefügt.`, "ok");
                          } else {
                            toast(`Clip nicht in Medien gefunden.`, "warn");
                          }
                        }
                      }}
                      style={{
                        padding: "5px 8px", borderRadius: 4, fontSize: 11, color: "#cfcfcf",
                        display: "flex", justifyContent: "space-between", gap: 8,
                        width: "100%", textAlign: "left", background: "transparent",
                        border: "none", cursor: "pointer", fontFamily: "inherit",
                      }}
                      onMouseEnter={(e) => (e.currentTarget.style.background = "rgba(255,255,255,.04)")}
                      onMouseLeave={(e) => (e.currentTarget.style.background = "transparent")}
                    >
                      <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", flex: 1 }}>{r.clip_name}: {r.description.slice(0, 30)}…</span>
                      <span style={{ color: "#e0b84a", fontFamily: "ui-monospace, monospace" }}>{(r.similarity * 100).toFixed(0)}%</span>
                    </button>
                  ))}
                </div>
              )}
            </div>
          )}

          <div style={{ flex: 1, overflow: "auto", padding: "12px 16px", minHeight: 0 }}>
            {loading && <div style={{ textAlign: "center", color: "#7a7a7a", padding: 20, fontSize: 12 }}>Clips werden geladen…</div>}
            {!loading && gridMedia.length === 0 && !error && <div style={{ textAlign: "center", color: "#7a7a7a", padding: 20, fontSize: 12 }}>Keine Clips gefunden.</div>}
            {error && !loading && <div style={{ textAlign: "center", color: "#e07a7a", padding: 20, fontSize: 11 }}>Backend offline: {error}</div>}
            {mediaView === "grid" ? (
              <div style={{ display: "grid", gridTemplateColumns: "repeat(3,1fr)", gap: 10 }}>
                {gridMedia.map((c, i) => {
                  const sel = selectedMedia.has(c.id);
                  const strip = abs(c.strip_url);
                  const isMusic = !!c.waveform_url;
                  return (
                    <button key={c.id} onClick={() => toggleMedia(c.id)} onDoubleClick={() => appendClip(c.id)}
                      draggable
                      onDragStart={(e) => {
                        e.dataTransfer.setData("text/plain", c.id);
                        e.dataTransfer.setData("application/x-cinassist-media", c.id);
                        e.dataTransfer.effectAllowed = "copy";
                        mediaDragRef.current = { id: c.id, duration: c.dauer || 5, name: c.dateiname.replace(/\.[^/.]+$/, ""), stripUrl: abs(c.strip_url) };
                      }}
                      onDragEnd={() => { mediaDragRef.current = null; setDropPreview(null); }}
                      title={`${c.dateiname} — Doppelklick oder Ziehen auf Timeline`}
                      style={{ borderRadius: 7, overflow: "hidden", background: "#1a1a1c", textAlign: "left", outline: sel ? "2px solid #e5c100" : "none", cursor: "grab" }}>
                      <div style={{ position: "relative", height: 68, background: strip ? `url(${strip}) center/cover no-repeat` : FALLBACK_GRADS[i % FALLBACK_GRADS.length] }}>
                        <div style={{ position: "absolute", left: 0, right: 0, bottom: 0, height: 20, background: "linear-gradient(rgba(0,0,0,0),rgba(0,0,0,.75))", display: "flex", alignItems: "flex-end", justifyContent: "space-between", padding: "0 4px 3px" }}>
                          {isMusic && <MusicIcon w={9} c="#dcdcdc" />}
                          <span style={{ fontSize: 10, color: "#f0f0f0", fontWeight: 600, marginLeft: "auto" }}>{fmtSec(c.dauer)}</span>
                        </div>
                        <div style={{ position: "absolute", left: 0, right: 0, bottom: 0, height: 2, background: c.status === "analysiert" ? "#4ec06a" : "#e0a020" }} />
                      </div>
                      <div style={{ fontSize: 10, color: "#9a9a9a", padding: "5px 6px 6px", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{c.dateiname}</div>
                    </button>
                  );
                })}
              </div>
            ) : (
              <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
                {gridMedia.map((c) => {
                  const sel = selectedMedia.has(c.id);
                  const isMusic = !!c.waveform_url;
                  return (
                    <button key={c.id} onClick={() => toggleMedia(c.id)} onDoubleClick={() => appendClip(c.id)}
                      draggable
                      onDragStart={(e) => {
                        e.dataTransfer.setData("text/plain", c.id);
                        e.dataTransfer.setData("application/x-cinassist-media", c.id);
                        e.dataTransfer.effectAllowed = "copy";
                        mediaDragRef.current = { id: c.id, duration: c.dauer || 5, name: c.dateiname.replace(/\.[^/.]+$/, ""), stripUrl: abs(c.strip_url) };
                      }}
                      onDragEnd={() => { mediaDragRef.current = null; setDropPreview(null); }}
                      title={`${c.dateiname} — Doppelklick oder Ziehen auf Timeline`}
                      style={{ display: "flex", alignItems: "center", gap: 8, padding: "6px 8px", borderRadius: 6, background: sel ? "#1a3a3a" : "#1a1a1c", textAlign: "left", cursor: "grab", border: sel ? "1px solid #e5c100" : "1px solid transparent" }}>
                      {isMusic ? <MusicIcon w={12} c="#7fd4c4" /> : <FilmIcon c="#9a9a9a" />}
                      <span style={{ flex: 1, fontSize: 11, color: "#d4d4d4", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{c.dateiname}</span>
                      <span style={{ fontSize: 10, color: "#8a8a8a", fontFamily: "ui-monospace, monospace" }}>{fmtSec(c.dauer)}</span>
                      <span style={{ width: 6, height: 6, borderRadius: "50%", background: c.status === "analysiert" ? "#4ec06a" : "#e0a020" }} />
                    </button>
                  );
                })}
              </div>
            )}
          </div>
          <div style={{ height: 48, flex: "none", display: "flex", alignItems: "center", padding: "0 16px", borderTop: "1px solid #1c1c1e", fontSize: 12, color: "#b5b5b5" }}>
            <S w={15} c="#b5b5b5"><circle cx="12" cy="12" r="9" /><path d="M12 8v4l2.5 2.5" /></S>
            <span style={{ marginLeft: 6 }}>Marker</span>
            <span style={{ flex: 1, textAlign: "center", color: "#8a8a8a" }}>
              {selectedMedia.size > 0 ? `${selectedMedia.size} ausgewählt` : `${clips.length} Elemente ‖ ${fmtSec(clips.reduce((s, c) => s + (c.dauer || 0), 0))}`}
            </span>
            <S w={15} c="#b5b5b5"><circle cx="12" cy="12" r="9" /><path d="M8.5 12l2.5 2.5L15.5 9.5" /></S>
            <button style={{ marginLeft: 6, color: "#b5b5b5", fontSize: 12 }} onClick={() => setSelectedMedia(selectedMedia.size ? new Set() : new Set(clips.map((c) => c.id)))}>Auswählen</button>
          </div>
              </div>
            </Panel>
          </PanelGroup>
        </Panel>

        <PanelResizeHandle style={{ height: 6, cursor: "row-resize", flex: "none" }}>
          <div onMouseEnter={(e) => (e.currentTarget.style.background = "#2a2a2e")} onMouseLeave={(e) => (e.currentTarget.style.background = "transparent")}
            style={{ width: "100%", height: "100%", display: "flex", alignItems: "center" }}>
            <div style={{ height: 1, width: "100%", background: "#000" }} />
          </div>
        </PanelResizeHandle>

        <Panel defaultSize={38} minSize={20} style={{ minHeight: 0, display: "flex", flexDirection: "column" }}>
      {/* ─── Werkzeugleiste (Wave 1 : Pro-Toolbar) ─── */}
      <div style={{ height: 56, flex: "none", display: "flex", alignItems: "center", padding: "0 14px", gap: 3, borderTop: "1px solid #1a1a1c" }}>
        {/* Gruppe 1 — Auswahl */}
        <ToolBtn title="Auswahl-Werkzeug" active={tool === "select"} onClick={() => setTool("select")}>
          <TI c={tool === "select" ? "#e5c100" : "#cfcfcf"}><path d="M4 4l7.07 17 2.51-7.39L21 11.07z" /></TI>
        </ToolBtn>
        <ToolBtn title="Klinge — Cursor-Modus, überall klicken zum Schneiden (Esc → beenden)" active={tool === "blade"} onClick={() => setTool(tool === "blade" ? "select" : "blade")}>
          <TI c={tool === "blade" ? "#e5c100" : "#cfcfcf"}><circle cx="6" cy="6" r="3" /><circle cx="6" cy="18" r="3" /><path d="M20 4L8.5 15.5M20 20L8.5 8.5M8.1 8.1L12 12" /></TI>
        </ToolBtn>

        <ToolDivider />

        {/* Gruppe 2 — Range In/Out (Mark In · Mark Out) */}
        <ToolBtn title={`Mark In (I)${inPoint !== null ? ` · ${fmtTC(inPoint)}` : ""}`} active={inPoint !== null} onClick={() => kbActionsRef.current.setInAtPlayhead()}>
          <TI>
            {/* Bracket solide gauche */}
            <path d="M7 4v16" strokeWidth={2} />
            {/* Chevron pointant vers l'intérieur (droite) */}
            <path d="M10 9l3 3-3 3" />
            {/* Ligne pointillée côté range */}
            <path d="M18 5v14" strokeWidth={1.5} strokeDasharray="2 2.5" />
          </TI>
        </ToolBtn>
        <ToolBtn title={`Mark Out (O)${outPoint !== null ? ` · ${fmtTC(outPoint)}` : ""}`} active={outPoint !== null} onClick={() => kbActionsRef.current.setOutAtPlayhead()}>
          <TI>
            {/* Ligne pointillée côté range */}
            <path d="M6 5v14" strokeWidth={1.5} strokeDasharray="2 2.5" />
            {/* Chevron pointant vers l'intérieur (gauche) */}
            <path d="M14 9l-3 3 3 3" />
            {/* Bracket solide droite */}
            <path d="M17 4v16" strokeWidth={2} />
          </TI>
        </ToolBtn>

        <ToolDivider />

        {/* Gruppe 3 — Interaktions-Umschalter */}
        <ToolBtn title={`Magnetisches Einrasten ${snapEnabled ? "AN" : "AUS"}`} active={snapEnabled} onClick={() => setSnapEnabled((s) => !s)}>
          {/* TODO: wire snap to drag handlers (le drag timeline est actuellement
              basé sur l'index/reorder, pas sur un positionnement libre en px) */}
          <TI c={snapEnabled ? "#e5c100" : "#cfcfcf"}><path d="M6 3v7a6 6 0 0 0 12 0V3" /><path d="M6 3H3M21 3h-3M6 10H3M21 10h-3" /></TI>
        </ToolBtn>
        <ToolBtn title="Video+Audio verknüpfen — Coming soon" onClick={() => toast("Link/Unlink von Video+Audio-Paar — Coming soon.", "info", 2500)}>
          <TI><path d="M9 15l6-6M8 12l-2 2a3 3 0 0 0 4 4l2-2M16 12l2-2a3 3 0 0 0-4-4l-2 2" /></TI>
        </ToolBtn>
        <ToolBtn
          title={`Sperren / Entsperren${lockedTlIds.size ? ` — ${lockedTlIds.size} gesperrt` : ""}`}
          disabled={selectedTlIds.size === 0}
          active={selectedTlId != null && lockedTlIds.has(selectedTlId)}
          onClick={toggleLockSelected}
        >
          <TI c={selectedTlId != null && lockedTlIds.has(selectedTlId) ? "#e5c100" : "#cfcfcf"}><rect x="4" y="11" width="16" height="10" rx="2" /><path d="M8 11V7a4 4 0 0 1 8 0v4" /></TI>
        </ToolBtn>

        <ToolDivider />

        {/* Gruppe 4 — Marker */}
        <ToolBtn title="Marker am Playhead setzen (M)" onClick={addMarkerAtPlayhead}>
          <TI c={markers.length > 0 ? "#e0b84a" : "#cfcfcf"}><path d="M6 3v18l6-4 6 4V3z" /></TI>
        </ToolBtn>

        <ToolDivider />

        {/* Gruppe 5 — Rückgängig / Wiederholen / Löschen */}
        <ToolBtn title="Rückgängig (Cmd Z)" onClick={undo}>
          <TI><path d="M9 14L4 9l5-5M4 9h11a5 5 0 0 1 0 10h-3" /></TI>
        </ToolBtn>
        <ToolBtn title="Wiederholen (Cmd ⇧Z)" onClick={redo}>
          <TI><path d="M15 14l5-5-5-5M20 9H9a5 5 0 0 0 0 10h3" /></TI>
        </ToolBtn>
        <ToolBtn title="Löschen (⌫)" disabled={selectedTlIds.size === 0} onClick={removeSelected}>
          <TI c={selectedTlIds.size > 0 ? "#e07a7a" : "#cfcfcf"}><path d="M3 6h18M8 6V4h8v2M6 6l1 14h10l1-14" /></TI>
        </ToolBtn>

        <ToolDivider />

        {/* Multicam — echte KI-Funktion, als Chip erhalten */}
        <button style={{ ...chip, gap: 6, height: 32 }} title="Multicam-Sync (KI)"
          onClick={() => sendAi(`Synchronisiere alle Clips per sync_multicam. Nutze das Werkzeug direkt.`)}>
          <S sw={1.7}><rect x="3" y="6" width="11" height="8" rx="1.5" /><rect x="9" y="10" width="12" height="8" rx="1.5" /></S>Multicam
        </button>

        <span style={{ fontSize: 11, color: "#7a7a7a", marginLeft: 8, whiteSpace: "nowrap" }}>
          {selectedTlIds.size > 0
            ? `${selectedTlIds.size} ausgewählt${lockedTlIds.size ? ` · ${lockedTlIds.size} gesperrt` : ""}`
            : lockedTlIds.size ? `${lockedTlIds.size} gesperrt` : "—"}
        </span>

        {/* Gruppe 6 — Zoom + Spuren (rechtsbündig) */}
        <div style={{ flex: 1, display: "flex", alignItems: "center", justifyContent: "flex-end", gap: 2 }}>
          {/* Multi-track : Spuren hinzufügen (max 5 je Typ) */}
          <button
            title={`Videospur hinzufügen (${numVideoTracks}/${MAX_TRACKS})`}
            onClick={addVideoTrack}
            disabled={numVideoTracks >= MAX_TRACKS}
            style={{ height: 26, padding: "0 8px", borderRadius: 6, display: "flex", alignItems: "center", gap: 3, background: "transparent", border: "1px solid rgba(255,255,255,0.12)", color: numVideoTracks >= MAX_TRACKS ? "#5a5a5a" : "#5fe0c0", cursor: numVideoTracks >= MAX_TRACKS ? "not-allowed" : "pointer", fontSize: 11, fontWeight: 700, flex: "none" }}
          >
            <span style={{ fontSize: 13 }}>+</span>V
          </button>
          <button
            title={`Audiospur hinzufügen (${numAudioTracks}/${MAX_TRACKS})`}
            onClick={addAudioTrack}
            disabled={numAudioTracks >= MAX_TRACKS}
            style={{ height: 26, padding: "0 8px", borderRadius: 6, display: "flex", alignItems: "center", gap: 3, background: "transparent", border: "1px solid rgba(255,255,255,0.12)", color: numAudioTracks >= MAX_TRACKS ? "#5a5a5a" : "#7fd4c4", cursor: numAudioTracks >= MAX_TRACKS ? "not-allowed" : "pointer", fontSize: 11, fontWeight: 700, flex: "none" }}
          >
            <span style={{ fontSize: 13 }}>+</span>A
          </button>
          <ToolDivider />
          <ToolBtn title="Auszoomen (Cmd −)" onClick={zoomOut}>
            <TI><circle cx="11" cy="11" r="7" /><path d="M21 21l-4.3-4.3M8 11h6" /></TI>
          </ToolBtn>
          <ToolBtn title="Zoom anpassen (Cmd 0)" onClick={zoomFit}>
            <TI><path d="M3 8V5a2 2 0 0 1 2-2h3M16 3h3a2 2 0 0 1 2 2v3M21 16v3a2 2 0 0 1-2 2h-3M8 21H5a2 2 0 0 1-2-2v-3" /></TI>
          </ToolBtn>
          <ToolBtn title="Einzoomen (Cmd +)" onClick={zoomIn}>
            <TI><circle cx="11" cy="11" r="7" /><path d="M21 21l-4.3-4.3M8 11h6M11 8v6" /></TI>
          </ToolBtn>
          <span onClick={zoomFit} title="Anpassen" style={{ minWidth: 42, textAlign: "center", cursor: "pointer", fontFamily: "ui-monospace, monospace", fontSize: 11, color: "#8a8a8a" }}>{Math.round(zoom * 100)}%</span>
        </div>
      </div>

      {/* ─── Timeline + VU (Wave 3 : remplit le Panel bas, hauteur flexible) ─── */}
      <div style={{ flex: 1, minHeight: 0, display: "flex" }}>
        {/* Cmd/Ctrl+molette = zoom (listener non-passif attaché via timelineWheelRef) */}
        <div
          ref={timelineWheelRef}
          style={{ flex: 1, position: "relative", padding: "8px 0 10px 0", minWidth: 0, display: "flex" }}
        >
        {/* ─── Multi-track : colonne d'en-têtes de piste (gauche, fixe) ───
            Suit le scroll vertical de la timeline via translateY (onScroll du
            conteneur de droite). DaVinci-Stil : badge contour + badge plein
            (record-enable décoratif) + verrou + Trim + œil / S·M. */}
        <div style={{ width: headerW, flex: "none", overflow: "hidden", position: "relative", borderRight: "1px solid #1a1a1c" }}>
          {/* Poignée de resize horizontale sur le bord droit — drag pour élargir/rétrécir la colonne d'en-têtes. */}
          <div
            onMouseDown={(e) => {
              e.preventDefault();
              const startX = e.clientX;
              const startW = headerW;
              const onMove = (ev: MouseEvent) => {
                setHeaderW(Math.max(HEADER_W_MIN, Math.min(HEADER_W_MAX, startW + (ev.clientX - startX))));
              };
              const onUp = () => {
                window.removeEventListener("mousemove", onMove);
                window.removeEventListener("mouseup", onUp);
              };
              window.addEventListener("mousemove", onMove);
              window.addEventListener("mouseup", onUp);
            }}
            title="Colonne d'en-têtes redimensionner"
            style={{ position: "absolute", top: 0, right: -3, width: 6, height: "100%", cursor: "col-resize", zIndex: 20, background: "transparent" }}
            onMouseEnter={(e) => (e.currentTarget.style.background = "rgba(255,255,255,0.08)")}
            onMouseLeave={(e) => (e.currentTarget.style.background = "transparent")}
          />
          <div ref={trackHeaderInnerRef} style={{ position: "absolute", top: 0, left: 0, right: 0, willChange: "transform" }}>
            {/* Spacer alignant les en-têtes avec la 1re piste (règle + marqueurs + bande groupe) */}
            {/* Spacer : ruler (20 + borderBottom 1) + marker (marginTop 6 + height 12)
                + spacer constant (marginTop 8 + height 6 = 14).
                Toujours 53px maintenant que la Groupe-Zeile est retirée. */}
            <div style={{ height: 20 + 1 + 6 + 12 + 8 + 6 }} />
            {Array.from({ length: numVideoTracks }, (_, k) => numVideoTracks - 1 - k).map((i) => {
              const id = `v${i}`; const st = trackState(id);
              return (
                <div key={id}
                  onContextMenu={(e) => openHeaderMenu(e, "v", i)}
                  title="Rechtsklick: Spur-Menü"
                  style={{ height: trackH(id, "v"), marginTop: 6, boxSizing: "border-box", background: "#161618", border: `1px solid ${st.locked ? "#5a4a12" : "#1f1f22"}`, borderRadius: 6, padding: "3px 6px", display: "flex", flexWrap: "wrap", alignContent: "center", alignItems: "center", gap: 3, overflow: "hidden", opacity: st.hidden ? 0.55 : 1, position: "relative" }}>
                  <div style={{ width: 26, height: 17, borderRadius: 3, border: "1.5px solid #2fbf9e", color: "#5fe0c0", fontSize: 9.5, fontWeight: 700, display: "flex", alignItems: "center", justifyContent: "center", flex: "none" }}>V{i + 1}</div>
                  <div style={{ width: 26, height: 17, borderRadius: 3, background: "#c23a3a", color: "#fff", fontSize: 9.5, fontWeight: 700, display: "flex", alignItems: "center", justifyContent: "center", flex: "none" }} title="Aufnahme aktivieren (dekorativ)">V{i + 1}</div>
                  <button onClick={() => toggleTrackFlag(id, "locked")} title={st.locked ? "Spur entsperren" : "Spur sperren"} style={hdrBtnStyle(st.locked)}><LockMini c={st.locked ? "#e5c100" : "#8a8a8a"} /></button>
                  <span title="Trim-Modus (dekorativ)" style={{ ...hdrBtnStyle(false), cursor: "default", border: "1px solid rgba(255,255,255,0.12)", fontSize: 9 }}>T</span>
                  <button onClick={() => toggleTrackFlag(id, "hidden")} title={st.hidden ? "Spur einblenden" : "Spur ausblenden"} style={hdrBtnStyle(st.hidden)}><EyeIcon c={st.hidden ? "#e5c100" : "#8a8a8a"} off={st.hidden} /></button>
                  {trackResizeHandle(id, "v")}
                </div>
              );
            })}
            {/* Trenner : aligné avec la poignée de redimensionnement vidéo (droite) */}
            <div style={{ height: 2, marginTop: 2 }} />
            {Array.from({ length: numAudioTracks }, (_, i) => {
              const id = `a${i}`; const st = trackState(id);
              return (
                <div key={id}
                  onContextMenu={(e) => openHeaderMenu(e, "a", i)}
                  title="Rechtsklick: Spur-Menü"
                  style={{ height: trackH(id, "a"), marginTop: 4, boxSizing: "border-box", background: "#13201d", border: `1px solid ${st.locked ? "#5a4a12" : "#1c2a26"}`, borderRadius: 6, padding: "3px 6px", display: "flex", flexWrap: "wrap", alignContent: "center", alignItems: "center", gap: 3, overflow: "hidden", position: "relative" }}>
                  <div style={{ width: 26, height: 17, borderRadius: 3, border: "1.5px solid #2fbf9e", color: "#7fd4c4", fontSize: 9.5, fontWeight: 700, display: "flex", alignItems: "center", justifyContent: "center", flex: "none" }}>A{i + 1}</div>
                  <div style={{ width: 26, height: 17, borderRadius: 3, background: "#c23a3a", color: "#fff", fontSize: 9.5, fontWeight: 700, display: "flex", alignItems: "center", justifyContent: "center", flex: "none" }} title="Aufnahme aktivieren (dekorativ)">A{i + 1}</div>
                  <button onClick={() => toggleTrackFlag(id, "locked")} title={st.locked ? "Spur entsperren" : "Spur sperren"} style={hdrBtnStyle(st.locked)}><LockMini c={st.locked ? "#e5c100" : "#8a8a8a"} /></button>
                  <button onClick={() => toggleTrackFlag(id, "solo")} title="Solo" style={hdrBtnStyle(st.solo, "#e5c100")}>S</button>
                  <button onClick={() => toggleTrackFlag(id, "mute")} title="Stumm" style={hdrBtnStyle(st.mute, "#e07a7a")}>M</button>
                  <span style={{ fontSize: 9, color: "#6a8a82", marginLeft: 1, flex: "none" }}>2.0</span>
                  {trackResizeHandle(id, "a")}
                </div>
              );
            })}
          </div>
        </div>
        <div
          ref={timelineRef}
          onClick={(e) => {
            if (tool === "blade") return; // le cut est géré par onClickCapture
            seek(e);
          }}
          onMouseMove={(e) => {
            // Guide vertical en blade mode : suit le curseur avec snap magnétique.
            if (tool !== "blade") return;
            const bar = bladeCursorRef.current;
            const label = bladeLabelRef.current;
            if (!bar) return;
            const container = e.currentTarget as HTMLDivElement;
            const inner = container.firstElementChild as HTMLDivElement;
            const innerW = inner ? inner.offsetWidth : container.clientWidth;
            const scrollLeft = container.scrollLeft;
            let relX = e.clientX - container.getBoundingClientRect().left + scrollLeft - 16;
            let t = totalDuration > 0 ? Math.max(0, (relX / innerW) * totalDuration) : 0;
            // Snap magnétique : edges de clips + playhead + markers + t=0
            if (snapEnabled && !e.altKey && totalDuration > 0) {
              const pxPerSec = innerW / totalDuration;
              const candidates: number[] = [globalTime, 0, ...markers.map(m => m.time)];
              for (const c of tlClips) { candidates.push(c.start); candidates.push(c.start + c.duration); }
              for (let gt = 5; gt < totalDuration; gt += 5) candidates.push(gt);
              let best = SNAP_TOLERANCE_PX + 0.001;
              for (const cand of candidates) {
                const dPx = Math.abs(cand * pxPerSec - t * pxPerSec);
                if (dPx < best) { best = dPx; t = cand; }
              }
              relX = t * pxPerSec;
            }
            bar.style.transform = `translateX(${relX}px)`;
            bar.style.display = "block";
            if (label) label.textContent = fmtTC(t);
          }}
          onClickCapture={(e) => {
            // Utilise la position snappée (relX du dernier onMouseMove) pour le cut.
            // Skip si on vient de terminer un scrub (drag playhead) ou un clip drag.
            if (tool !== "blade") return;
            if (justScrubbedRef.current || justDraggedClipRef.current) return;
            const container = e.currentTarget as HTMLDivElement;
            const inner = container.firstElementChild as HTMLDivElement;
            const innerW = inner ? inner.offsetWidth : container.clientWidth;
            const scrollLeft = container.scrollLeft;
            let relX = e.clientX - container.getBoundingClientRect().left + scrollLeft - 16;
            let t = totalDuration > 0 ? Math.max(0, (relX / innerW) * totalDuration) : 0;
            if (snapEnabled && !e.altKey && totalDuration > 0) {
              const pxPerSec = innerW / totalDuration;
              const candidates: number[] = [globalTime, 0, ...markers.map(m => m.time)];
              for (const c of tlClips) { candidates.push(c.start); candidates.push(c.start + c.duration); }
              for (let gt = 5; gt < totalDuration; gt += 5) candidates.push(gt);
              let best = SNAP_TOLERANCE_PX + 0.001;
              for (const cand of candidates) {
                const dPx = Math.abs(cand * pxPerSec - t * pxPerSec);
                if (dPx < best) { best = dPx; t = cand; }
              }
              relX = t * pxPerSec;
            }
            e.stopPropagation();
            e.preventDefault();
            splitAtTime(t);
          }}
          onMouseLeave={() => { if (bladeCursorRef.current) bladeCursorRef.current.style.display = "none"; }}
          onContextMenu={openEmptyMenu}
          onScroll={(e) => { if (trackHeaderInnerRef.current) trackHeaderInnerRef.current.style.transform = `translateY(${-e.currentTarget.scrollTop}px)`; }}
          onDragOver={(e) => {
            if (e.dataTransfer.types.includes("application/x-cinassist-media") || e.dataTransfer.types.includes("application/x-cinassist-tl")) {
              e.preventDefault();
              e.dataTransfer.dropEffect = e.dataTransfer.types.includes("application/x-cinassist-tl") ? "move" : "copy";
              // Preview du drop : ghost sur la timeline montrant la position et
              // la durée du clip en cours de drag depuis Medien.
              const media = mediaDragRef.current;
              if (media && totalDuration > 0) {
                const container = e.currentTarget as HTMLDivElement;
                const inner = container.firstElementChild as HTMLDivElement;
                const innerW = inner ? inner.offsetWidth : container.clientWidth;
                const scrollLeft = container.scrollLeft;
                const relX = e.clientX - container.getBoundingClientRect().left + scrollLeft - 16;
                let startTime = Math.max(0, (relX / innerW) * totalDuration);
                const pxPerSec = innerW / totalDuration;
                // Snap magnétique (edges d'autres clips, playhead, markers, t=0)
                let snapPct: number | null = null;
                if (snapEnabled && !e.altKey) {
                  const candidates: number[] = [globalTime, 0, ...markers.map(m => m.time)];
                  for (const c of tlClips) {
                    candidates.push(c.start);
                    candidates.push(c.start + c.duration);
                  }
                  for (let gt = 5; gt < totalDuration; gt += 5) candidates.push(gt);
                  const leftPx = startTime * pxPerSec;
                  const rightPx = (startTime + media.duration) * pxPerSec;
                  let best = SNAP_TOLERANCE_PX + 0.001;
                  for (const cand of candidates) {
                    const candPx = cand * pxPerSec;
                    const dL = Math.abs(candPx - leftPx);
                    if (dL < best) { best = dL; startTime = cand; snapPct = (cand / totalDuration) * 100; }
                    const dR = Math.abs(candPx - rightPx);
                    if (dR < best) { best = dR; startTime = Math.max(0, cand - media.duration); snapPct = (cand / totalDuration) * 100; }
                  }
                }
                const leftPct = (startTime / totalDuration) * 100;
                const widthPct = Math.min(100 - leftPct, (media.duration / totalDuration) * 100);
                const trackIdx = nearestVideoTrack(e.clientY);
                setDropPreview({ leftPct, widthPct, trackIdx, name: media.name, stripUrl: media.stripUrl, snapPct });
              }
            }
          }}
          onDragLeave={(e) => {
            // Ne clear que si on quitte vraiment le container (pas un enfant)
            if (e.currentTarget === e.target) setDropPreview(null);
          }}
          onDrop={(e) => {
            setDropPreview(null);
            // Global fallback: fires only when the drop misses every track row
            // (ruler, gaps, area below tracks). Smart-drop at the X position on
            // the NEAREST video row by Y (default V1 if none). No re-tiling.
            const r = (e.currentTarget as HTMLDivElement).getBoundingClientRect();
            const inner = (e.currentTarget as HTMLDivElement).firstElementChild as HTMLDivElement;
            const innerW = inner ? inner.offsetWidth : r.width;
            const scrollLeft = (e.currentTarget as HTMLDivElement).scrollLeft || 0;
            const relX = e.clientX - r.left + scrollLeft - 16;
            const dropTime = totalDuration > 0 && innerW > 0 ? Math.max(0, (relX / innerW) * totalDuration) : 0;
            const intendedVideoTrack = nearestVideoTrack(e.clientY);
            const tlId = e.dataTransfer.getData("application/x-cinassist-tl");
            if (tlId) {
              smartDrop({ tlId, intendedVideoTrack, dropTime });
              e.preventDefault();
              return;
            }
            const clipId = e.dataTransfer.getData("application/x-cinassist-media") || e.dataTransfer.getData("text/plain");
            if (clipId) {
              smartDrop({ media: clipId, intendedVideoTrack, dropTime });
              e.preventDefault();
            }
          }}
          style={{ flex: 1, position: "relative", padding: "0 16px 0 16px", minWidth: 0, cursor: tool === "blade" ? "url(\"data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' width='28' height='28' viewBox='0 0 24 24' fill='none' stroke='%23e5c100' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'><circle cx='6' cy='6' r='3'/><circle cx='6' cy='18' r='3'/><path d='M20 4L8.5 15.5M20 20L8.5 8.5M8.1 8.1L12 12'/></svg>\") 4 4, crosshair" : "text", overflowX: "auto", overflowY: "auto" }}
        >
        <div
          onMouseDown={(e) => {
            // Rubber-band selection : drag dans l'espace vide → sélection multiple.
            // Skip si mousedown sur un clip, edge handle, playhead, ruler ou row header.
            if (tool === "blade") return;
            if (e.button !== 0) return;
            const target = e.target as HTMLElement;
            if (target.closest("[data-avtlid]") || target.closest("[title='Abspielkopf ziehen']")) return;
            if (target.closest(".cin-track-row") && target !== e.currentTarget) {
              // On accepte le drag sur une row si le target EST la row elle-même
              // (background vide), pas un enfant clip.
              if (!(target as HTMLElement).classList.contains("cin-track-row")) return;
            }
            const container = timelineRef.current;
            if (!container) return;
            const scrollLeft = container.scrollLeft;
            const scrollTop = container.scrollTop;
            const containerRect = container.getBoundingClientRect();
            const x0 = e.clientX - containerRect.left + scrollLeft;
            const y0 = e.clientY - containerRect.top + scrollTop;
            setRubberBand({ x1: x0, y1: y0, x2: x0, y2: y0 });
            // Helper : calcule les clips qui touchent le rectangle rubber-band
            // Coordonnées en INTERNE du timelineRef (compensées du scroll).
            const computeIntersecting = (rx1: number, ry1: number, rx2: number, ry2: number): Set<string> => {
              const rbLeft = Math.min(rx1, rx2);
              const rbRight = Math.max(rx1, rx2);
              const rbTop = Math.min(ry1, ry2);
              const rbBottom = Math.max(ry1, ry2);
              const found = new Set<string>();
              document.querySelectorAll("[data-avtlid]").forEach((el) => {
                const cel = el as HTMLElement;
                const cr = cel.getBoundingClientRect();
                const cLeft = cr.left - containerRect.left + container.scrollLeft;
                const cRight = cr.right - containerRect.left + container.scrollLeft;
                const cTop = cr.top - containerRect.top + container.scrollTop;
                const cBottom = cr.bottom - containerRect.top + container.scrollTop;
                if (cLeft < rbRight && cRight > rbLeft && cTop < rbBottom && cBottom > rbTop) {
                  const id = cel.getAttribute("data-avtlid");
                  if (id) found.add(id);
                }
              });
              return found;
            };
            const onMove = (ev: MouseEvent) => {
              const x = ev.clientX - containerRect.left + container.scrollLeft;
              const y = ev.clientY - containerRect.top + container.scrollTop;
              setRubberBand({ x1: x0, y1: y0, x2: x, y2: y });
              // Live selection : montre en jaune les clips actuellement dans la zone
              if (Math.abs(x - x0) >= 3 || Math.abs(y - y0) >= 3) {
                setSelectedTlIds(computeIntersecting(x0, y0, x, y));
              }
            };
            const onUp = (ev: MouseEvent) => {
              window.removeEventListener("mousemove", onMove);
              window.removeEventListener("mouseup", onUp);
              const dx = Math.abs((ev.clientX - containerRect.left + container.scrollLeft) - x0);
              const dy = Math.abs((ev.clientY - containerRect.top + container.scrollTop) - y0);
              // Micro-drag (< 3px) sans shift/cmd = simple click dans le vide
              // → déselectionne tout.
              if (dx < 3 && dy < 3 && !ev.shiftKey && !ev.metaKey && !ev.ctrlKey) {
                setSelectedTlIds(new Set());
              }
              setRubberBand(null);
            };
            window.addEventListener("mousemove", onMove);
            window.addEventListener("mouseup", onUp);
          }}
          style={{ position: "relative", width: timelineInnerWidth, minHeight: "100%" }}>
          {/* Ruler avec ticks minor + labels majors — scrubable + sticky top
              (reste visible pendant le scroll vertical de la timeline). */}
          <div
            onMouseDown={beginRulerMouseDown}
            style={{ position: "sticky", top: 0, zIndex: 8, height: 20, borderBottom: "1px solid rgba(255,255,255,0.06)", cursor: "ew-resize", background: isDragging ? "rgba(0,0,0,0.85)" : "#0c0c0d" }}
          >
            {/* In/Out range — bande shaded + deux brackets NLE (barre solide +
                chevron + pointillé). Non-cliquables (le scrub de la ruler reste
                actif au-dessus). */}
            {totalDuration > 0 && (inPoint !== null || outPoint !== null) && (() => {
              const IO = "#4ba7ff";
              const inPct = inPoint !== null ? (inPoint / totalDuration) * 100 : 0;
              const outPct = outPoint !== null ? (outPoint / totalDuration) * 100 : 100;
              return (
                <>
                  {inPoint !== null && outPoint !== null && (
                    <div style={{ position: "absolute", left: `${inPct}%`, width: `${Math.max(0, outPct - inPct)}%`, top: 0, bottom: 0, background: "rgba(75,167,255,0.13)", borderTop: `1px solid rgba(75,167,255,0.55)`, borderBottom: `1px solid rgba(75,167,255,0.55)`, pointerEvents: "none" }} />
                  )}
                  {inPoint !== null && (() => {
                    const draggable = outPoint !== null;
                    const titleSuffix = draggable ? " · ziehen zum Verschieben" : "";
                    return (
                      <div title={`In · ${fmtTC(inPoint)}${titleSuffix}`} style={{ position: "absolute", left: `${inPct}%`, top: 0, height: 20, width: 0, pointerEvents: "none" }}>
                        <svg width="14" height="20" onMouseDown={draggable ? ((e) => beginBracketDrag(e, "in")) : undefined} style={{ position: "absolute", left: 0, top: 0, overflow: "visible", filter: "drop-shadow(0 0 2px rgba(75,167,255,0.6))", pointerEvents: draggable ? "auto" : "none", cursor: draggable ? "ew-resize" : "default" }}>
                          <rect x="0" y="0" width="2" height="20" fill={IO} />
                          <path d="M 2 4 L 8 10 L 2 16 Z" fill={IO} />
                          <line x1="11" y1="2" x2="11" y2="18" stroke={IO} strokeWidth="1.5" strokeDasharray="2 2" opacity="0.75" />
                        </svg>
                      </div>
                    );
                  })()}
                  {outPoint !== null && (() => {
                    const draggable = inPoint !== null;
                    const titleSuffix = draggable ? " · ziehen zum Verschieben" : "";
                    return (
                      <div title={`Out · ${fmtTC(outPoint)}${titleSuffix}`} style={{ position: "absolute", left: `${outPct}%`, top: 0, height: 20, width: 0, pointerEvents: "none" }}>
                        <svg width="14" height="20" onMouseDown={draggable ? ((e) => beginBracketDrag(e, "out")) : undefined} style={{ position: "absolute", left: -14, top: 0, overflow: "visible", filter: "drop-shadow(0 0 2px rgba(75,167,255,0.6))", pointerEvents: draggable ? "auto" : "none", cursor: draggable ? "ew-resize" : "default" }}>
                          <line x1="3" y1="2" x2="3" y2="18" stroke={IO} strokeWidth="1.5" strokeDasharray="2 2" opacity="0.75" />
                          <path d="M 12 4 L 6 10 L 12 16 Z" fill={IO} />
                          <rect x="12" y="0" width="2" height="20" fill={IO} />
                        </svg>
                      </div>
                    );
                  })()}
                </>
              );
            })()}
            {(() => {
              if (totalDuration <= 0) return null;
              const STEPS = [1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 1200];
              // Effective duration en pixel : zoom appliqué
              const targetTicks = Math.max(8, Math.floor(10 * zoom));
              const raw = totalDuration / targetTicks;
              const majorStep = STEPS.find((s) => s >= raw) ?? Math.ceil(raw / 60) * 60;
              const minorStep = majorStep / 5; // 5 subdivisions par major
              const majorCount = Math.floor(totalDuration / majorStep) + 1;
              const minorCount = Math.floor(totalDuration / minorStep) + 1;
              return (
                <>
                  {Array.from({ length: minorCount }, (_, i) => {
                    const t = i * minorStep;
                    if (t > totalDuration) return null;
                    const isMajor = Math.abs(t % majorStep) < 0.001;
                    return (
                      <div
                        key={`tick-${i}`}
                        style={{
                          position: "absolute",
                          left: `${(t / totalDuration) * 100}%`,
                          bottom: 0,
                          width: 1,
                          height: isMajor ? 8 : 4,
                          background: isMajor ? "rgba(255,255,255,0.35)" : "rgba(255,255,255,0.15)",
                        }}
                      />
                    );
                  })}
                  {Array.from({ length: majorCount }, (_, i) => {
                    const t = i * majorStep;
                    if (t > totalDuration) return null;
                    return (
                      <span
                        key={`label-${i}`}
                        style={{
                          position: "absolute",
                          left: `${(t / totalDuration) * 100}%`,
                          bottom: 10,
                          transform: "translateX(-50%)",
                          fontSize: 10,
                          color: "#8a8a8a",
                          fontFamily: "ui-monospace, monospace",
                          whiteSpace: "nowrap",
                        }}
                      >
                        {fmtSec(t)}
                      </span>
                    );
                  })}
                </>
              );
            })()}
          </div>
          {/* Marker-Zeile — reflète les segments sélectionnés + markers manuels */}
          <div style={{ position: "relative", height: 12, marginTop: 6 }}>
            {tlClips.filter((c) => selectedTlIds.has(c.tlId)).map((c) => (
              <div
                key={`marker-${c.tlId}`}
                style={{
                  position: "absolute",
                  left: `${clipToPct(c.start)}%`,
                  width: `${clipWidthPct(c.duration)}%`,
                  height: 3, top: 1,
                  background: "#e5c100",
                  borderRadius: 2,
                  boxShadow: "0 0 6px rgba(229,193,0,0.5)",
                }}
              />
            ))}
            {markers.map((m) => (
              <button
                key={m.id}
                onClick={(e) => { e.stopPropagation(); seekSeconds(m.time); }}
                onContextMenu={(e) => { e.preventDefault(); e.stopPropagation(); if (confirm(`Marker "${m.label}" löschen?`)) setMarkers((cur) => cur.filter((x) => x.id !== m.id)); }}
                title={`${m.label} — ${fmtTC(m.time)} · Rechtsklick zum Löschen`}
                style={{
                  position: "absolute",
                  left: `${(m.time / Math.max(totalDuration, 0.001)) * 100}%`,
                  top: 0, width: 10, height: 12,
                  transform: "translateX(-50%)",
                  background: "transparent", border: "none", cursor: "pointer",
                  padding: 0, zIndex: 3,
                }}
              >
                <div style={{ width: 10, height: 10, background: "#e0b84a", clipPath: "polygon(0 0, 100% 0, 50% 100%)" }} />
              </button>
            ))}
          </div>

          {/* Spacer constant avant les video rows (14px = marginTop 8 + height 6).
              La Groupe-Zeile violette a été retirée sur demande utilisateur. */}
          <div style={{ height: 6, marginTop: 8 }} />

          {/* Indicateur directionnel de drop (Task 3) — piloté UNIQUEMENT par
              ref+classList dans onDragOver (jamais setState). Ligne jaune fine en
              haut (insert au-dessus) ou en bas (escalade en dessous) de la rangée. */}
          <style>{`
            .cin-track-row.drop-above::before { content:""; position:absolute; top:-2px; left:0; right:0; height:2px; background:#e5c100; box-shadow:0 0 6px rgba(229,193,0,0.7); pointer-events:none; z-index:9; }
            .cin-track-row.drop-below::after { content:""; position:absolute; bottom:-2px; left:0; right:0; height:2px; background:#e5c100; box-shadow:0 0 6px rgba(229,193,0,0.7); pointer-events:none; z-index:9; }
            /* Piste cible pendant un drag : 2 lignes horizontales top+bottom +
               background très léger → l'utilisateur sait clairement OÙ le clip
               va atterrir, même sur la piste d'origine. */
            .cin-track-row.drop-target { background: rgba(229,193,0,0.06) !important; }
            .cin-track-row.drop-target::before { content:""; position:absolute; top:-1px; left:0; right:0; height:2px; background:rgba(229,193,0,0.85); box-shadow:0 0 6px rgba(229,193,0,0.6); pointer-events:none; z-index:9; }
            .cin-track-row.drop-target::after { content:""; position:absolute; bottom:-1px; left:0; right:0; height:2px; background:rgba(229,193,0,0.85); box-shadow:0 0 6px rgba(229,193,0,0.6); pointer-events:none; z-index:9; }
            /* Blade mode : force le curseur SVG ciseau sur TOUS les enfants du
               container timeline, y compris les clips (qui ont leur propre
               cursor: grab par défaut). */
            body[data-blade="true"] .cin-track-row,
            body[data-blade="true"] .cin-track-row *,
            body[data-blade="true"] [data-avtlid] {
              cursor: url("data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' width='28' height='28' viewBox='0 0 24 24' fill='none' stroke='%23e5c100' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'><circle cx='6' cy='6' r='3'/><circle cx='6' cy='18' r='3'/><path d='M20 4L8.5 15.5M20 20L8.5 8.5M8.1 8.1L12 12'/></svg>") 4 4, crosshair !important;
            }
          `}</style>
          {/* Videoclips — une rangée par piste vidéo (V1 = index 0 = priorité). */}
          {Array.from({ length: numVideoTracks }, (_, k) => numVideoTracks - 1 - k).map((ti) => {
            const tHidden = trackState(`v${ti}`).hidden;
            const vH = trackH(`v${ti}`, "v");
            return (
            <div key={`vrow${ti}`}
              ref={(el) => { if (el) videoRowRefs.current.set(ti, el); else videoRowRefs.current.delete(ti); }}
              className="cin-track-row"
              onDragOver={(e) => {
                // NO setState here (fires ~60 fps). Directional hint via ref+classList only.
                if (e.dataTransfer.types.includes("application/x-cinassist-media") || e.dataTransfer.types.includes("application/x-cinassist-tl")) {
                  e.preventDefault();
                  e.dataTransfer.dropEffect = e.dataTransfer.types.includes("application/x-cinassist-tl") ? "move" : "copy";
                  const r = e.currentTarget.getBoundingClientRect();
                  const above = (e.clientY - r.top) < r.height / 2;
                  e.currentTarget.classList.toggle("drop-above", above);
                  e.currentTarget.classList.toggle("drop-below", !above);
                }
              }}
              onDragLeave={(e) => { e.currentTarget.classList.remove("drop-above", "drop-below"); }}
              onDrop={(e) => {
                e.stopPropagation(); // ne pas laisser le handler global tirer
                e.preventDefault();
                const r = e.currentTarget.getBoundingClientRect();
                const above = (e.clientY - r.top) < r.height / 2;
                e.currentTarget.classList.remove("drop-above", "drop-below");
                const dropTime = r.width > 0 ? Math.max(0, ((e.clientX - r.left) / r.width) * totalDuration) : 0;
                const tlId = e.dataTransfer.getData("application/x-cinassist-tl");
                if (tlId) { smartDrop({ tlId, intendedVideoTrack: ti, dropTime }); return; }
                const clipId = e.dataTransfer.getData("application/x-cinassist-media") || e.dataTransfer.getData("text/plain");
                if (clipId) smartDrop({ media: clipId, intendedVideoTrack: ti, dropTime, insertNew: above ? "above" : "below" });
              }}
              style={{ position: "relative", height: vH, marginTop: 6, background: ti % 2 === 1 ? "rgba(255,255,255,0.015)" : "transparent" }}>
            {tlClips.filter((c) => (c.videoTrackIndex ?? 0) === ti).map((c) => {
              const sel = selectedTlIds.has(c.tlId);
              const locked = clipLocked(c);
              const clipSrc = c.proxyUrl || c.videoUrl || "";
              const isLoading = clipSrc !== "" && loadingSrcs.has(clipSrc);
              return (
                <div key={c.tlId}
                  data-avtlid={c.tlId} data-avrow="v"
                  onClick={(e) => clickTlClip(c.tlId, e, c.start)}
                  onContextMenu={(e) => openClipMenu(e, c)}
                  onMouseDown={(e) => beginClipDrag(e, c, "v")}
                  style={{ position: "absolute", left: `${clipToPct(c.start)}%`, width: `${clipWidthPct(c.duration)}%`, height: vH, borderRadius: 7, overflow: "hidden", background: sel ? "#0d1f3a" : "#232326", boxShadow: sel ? "0 0 0 2px #e5c100" : "none", cursor: locked ? "not-allowed" : "grab", opacity: tHidden ? 0.3 : 1, filter: tHidden ? "grayscale(1)" : "none" }}>
                  <div style={{ display: "flex", height: "100%" }}>
                    <div style={{ width: 42, background: c.stripUrl ? `url(${c.stripUrl}) center/cover no-repeat` : "linear-gradient(#7fa0b8,#3a5240)", flex: "none" }} />
                    {c.name && (
                      <div style={{ flex: 1, display: "flex", alignItems: "center", gap: 4, padding: "0 6px", fontSize: 10, color: sel ? "#dfe8ff" : "#d4d4d4" }}>
                        <FilmIcon c={sel ? "#8aa4d4" : "#9a9a9a"} />
                        <span style={{ whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{c.name}</span>
                      </div>
                    )}
                  </div>
                  {c.stripUrl && <div style={{ position: "absolute", inset: 0, backgroundImage: `url(${c.stripUrl})`, backgroundSize: "cover", backgroundPosition: "center", opacity: 0.35, pointerEvents: "none" }} />}
                  {/* Wave 2 : bande waveform de l'audio embarqué (bas 12px du clip vidéo) */}
                  {c.waveformUrl && (
                    <div style={{ position: "absolute", bottom: 0, left: 0, right: 0, height: 12, backgroundImage: `url(${c.waveformUrl})`, backgroundSize: "100% 100%", backgroundRepeat: "no-repeat", opacity: 0.7, pointerEvents: "none" }} />
                  )}
                  {/* Wave 1 : clip verrouillé — rayures diagonales + cadenas */}
                  {locked && (
                    <>
                      <div style={{ position: "absolute", inset: 0, background: "repeating-linear-gradient(45deg, rgba(229,193,0,0.16) 0 6px, transparent 6px 12px)", pointerEvents: "none" }} />
                      <div style={{ position: "absolute", top: 3, right: 4, zIndex: 6, pointerEvents: "none" }} title="Gesperrt">
                        <S w={12} c="#e5c100" sw={2}><rect x="4" y="11" width="16" height="10" rx="2" /><path d="M8 11V7a4 4 0 0 1 8 0v4" /></S>
                      </div>
                    </>
                  )}
                  {isLoading && (
                    <div style={{ position: "absolute", top: 4, left: 4, zIndex: 6, background: "rgba(0,0,0,0.7)", borderRadius: 4, padding: "2px 5px", display: "flex", alignItems: "center", gap: 4, fontSize: 9, color: "#fff", pointerEvents: "none" }} title="Video wird geladen…">
                      <svg width={10} height={10} viewBox="0 0 24 24">
                        <circle cx="12" cy="12" r="9" stroke="#e5c100" strokeWidth="3" fill="none" strokeDasharray="14 42" strokeLinecap="round">
                          <animateTransform attributeName="transform" type="rotate" from="0 12 12" to="360 12 12" dur="0.9s" repeatCount="indefinite" />
                        </circle>
                      </svg>
                      <span>Lädt</span>
                    </div>
                  )}
                  {/* Edge-Trim : poignées invisibles 6 px gauche/droite (drag = trim continu) */}
                  <div
                    onMouseDown={(ev) => beginEdgeTrim(ev, c.tlId, "left")}
                    onClick={(ev) => ev.stopPropagation()}
                    style={{ position: "absolute", left: 0, top: 0, bottom: 0, width: 6, cursor: "url(\"data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' width='24' height='24' viewBox='0 0 24 24'><rect x='11' y='4' width='2' height='16' fill='%236ad04a' stroke='%23000' stroke-width='0.5'/><path d='M11 9 L5 12 L11 15 Z' fill='%236ad04a' stroke='%23000' stroke-width='0.5'/><path d='M13 9 L19 12 L13 15 Z' fill='%236ad04a' stroke='%23000' stroke-width='0.5'/></svg>\") 12 12, ew-resize", zIndex: 5, background: "transparent", pointerEvents: locked ? "none" : "auto" }} />
                  <div
                    onMouseDown={(ev) => beginEdgeTrim(ev, c.tlId, "right")}
                    onClick={(ev) => ev.stopPropagation()}
                    style={{ position: "absolute", right: 0, top: 0, bottom: 0, width: 6, cursor: "url(\"data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' width='24' height='24' viewBox='0 0 24 24'><rect x='11' y='4' width='2' height='16' fill='%236ad04a' stroke='%23000' stroke-width='0.5'/><path d='M11 9 L5 12 L11 15 Z' fill='%236ad04a' stroke='%23000' stroke-width='0.5'/><path d='M13 9 L19 12 L13 15 Z' fill='%236ad04a' stroke='%23000' stroke-width='0.5'/></svg>\") 12 12, ew-resize", zIndex: 5, background: "transparent", pointerEvents: locked ? "none" : "auto" }} />
                </div>
              );
            })}
            {/* Roll-Trim : poignée au point de jonction de chaque paire de clips
                adjacents (B.start ≈ A.end). Rendue APRÈS les clips (donc après
                leurs poignées d'edge-trim) + zIndex 6 → prend le dessus quand
                deux poignées se recouvrent au même point. */}
            {(() => {
              const rowClips = tlClips.filter((c) => (c.videoTrackIndex ?? 0) === ti).slice().sort((x, y) => x.start - y.start);
              const pairs: [TLClip, TLClip][] = [];
              for (let i = 0; i + 1 < rowClips.length; i++) {
                const A = rowClips[i], B = rowClips[i + 1];
                if (Math.abs(B.start - (A.start + A.duration)) < 0.02) pairs.push([A, B]);
              }
              return pairs.map(([A, B]) => (
                <div key={`roll-${A.tlId}-${B.tlId}`}
                  onMouseDown={(ev) => beginRollTrim(ev, A.tlId, B.tlId)}
                  onClick={(ev) => ev.stopPropagation()}
                  onMouseEnter={(ev) => { ev.currentTarget.style.background = "rgba(229,193,0,0.4)"; }}
                  onMouseLeave={(ev) => { ev.currentTarget.style.background = "transparent"; }}
                  title="Roll-Trim — Schnittpunkt verschieben"
                  style={{ position: "absolute", left: `${clipToPct(A.start + A.duration)}%`, top: 0, bottom: 0, width: 4, marginLeft: -2, cursor: "ew-resize", zIndex: 6, background: "transparent" }}>
                  {/* Repère visuel permanent : trait vertical fin au cut point → signale l'adjacence */}
                  <div style={{ position: "absolute", left: "50%", top: 0, bottom: 0, width: 1, marginLeft: -0.5, background: "rgba(255,255,255,0.35)", pointerEvents: "none" }} />
                </div>
              ));
            })()}
            {/* Ghost overlay pour deletes proposés sur cette piste vidéo. */}
            <ProposalDeletesInRow tlClips={tlClips} kind="v" trackIndex={ti} clipToPct={clipToPct} clipWidthPct={clipWidthPct} />
            {/* #2 : poignée de redimensionnement PAR piste (bord inférieur) */}
            {trackResizeHandle(`v${ti}`, "v")}
            </div>
            );
          })}

          {/* Séparateur groupe vidéo↔audio (aligné avec le Trenner d'en-tête) */}
          <div ref={vaSeparatorRef} style={{ height: 2, marginTop: 2 }} />

          {/* Audioclips — une rangée par piste audio. Le M (stumm) coupe la
              source de la piste vidéo gagnante correspondante (mono-source). */}
          {Array.from({ length: numAudioTracks }, (_, ti) => {
            const aSt = trackState(`a${ti}`);
            const aH = trackH(`a${ti}`, "a");
            return (
            <div key={`arow${ti}`}
              ref={(el) => { if (el) audioRowRefs.current.set(ti, el); else audioRowRefs.current.delete(ti); }}
              onDragOver={(e) => {
                if (e.dataTransfer.types.includes("application/x-cinassist-media") || e.dataTransfer.types.includes("application/x-cinassist-tl")) {
                  e.preventDefault();
                  e.dataTransfer.dropEffect = e.dataTransfer.types.includes("application/x-cinassist-tl") ? "move" : "copy";
                }
              }}
              onDrop={(e) => {
                e.stopPropagation();
                e.preventDefault();
                // Un clip audio suit son video track (mono-source) : on place au X
                // visé sur la piste vidéo correspondante, l'audio route ensuite seul.
                const r = e.currentTarget.getBoundingClientRect();
                const dropTime = r.width > 0 ? Math.max(0, ((e.clientX - r.left) / r.width) * totalDuration) : 0;
                const tlId = e.dataTransfer.getData("application/x-cinassist-tl");
                if (tlId) { smartDrop({ tlId, intendedVideoTrack: ti, dropTime }); return; }
                const clipId = e.dataTransfer.getData("application/x-cinassist-media") || e.dataTransfer.getData("text/plain");
                if (clipId) smartDrop({ media: clipId, intendedVideoTrack: ti, dropTime });
              }}
              style={{ position: "relative", height: aH, marginTop: 4, background: aSt.mute ? "rgba(224,122,122,0.05)" : "transparent" }}>
            {tlClips.filter((c) => c.hasAudio && (c.audioTrackIndex ?? c.videoTrackIndex ?? 0) === ti).map((c) => {
              const sel = selectedTlIds.has(c.tlId);
              const locked = clipLocked(c);
              return (
                <div key={`a-${c.tlId}`}
                  data-avtlid={c.tlId} data-avrow="a"
                  onClick={(e) => clickTlClip(c.tlId, e, c.start)}
                  onContextMenu={(e) => openClipMenu(e, c)}
                  onMouseDown={(e) => beginClipDrag(e, c, "a")}
                  style={{ position: "absolute", left: `${clipToPct(audioStartOf(c))}%`, width: `${clipWidthPct(c.duration)}%`, height: aH, borderRadius: 7, overflow: "hidden", background: "#1f6b5f", boxShadow: sel ? "0 0 0 2px #e5c100" : "none", cursor: locked ? "not-allowed" : "grab", opacity: aSt.mute ? 0.45 : 1 }}>
                  <div style={{ display: "flex", alignItems: "center", gap: 4, padding: "4px 6px 0", fontSize: 10, color: "#cdeee7" }}>
                    <span style={{ flex: "none", display: "inline-flex" }} title={c.avLinked === false ? "Audio getrennt (unabhängig verschiebbar)" : "Audio mit Video verknüpft"}>
                      {c.avLinked === false ? <BrokenChainIcon /> : <ChainIcon />}
                    </span>
                    <MusicIcon />{c.name}
                  </div>
                  {/* Waveform dynamique (wavesurfer). Le SVG synthétique de base a
                      été retiré — le waveform réel (wavesurfer) prend le dessus, et
                      son fallback interne (PNG c.waveformUrl) reste dispo en secours. */}
                  <ClipWaveform
                    src={c.proxyUrl || c.videoUrl || ""}
                    mediaStart={c.mediaStart}
                    duration={c.duration}
                    sourceDuration={c.sourceDuration ?? c.duration}
                    color="#d0f5da"
                    height={Math.max(12, aH - 20)}
                    fallbackUrl={c.waveformUrl ?? undefined}
                  />
                  {locked && (
                    <>
                      <div style={{ position: "absolute", inset: 0, background: "repeating-linear-gradient(45deg, rgba(229,193,0,0.16) 0 6px, transparent 6px 12px)", pointerEvents: "none" }} />
                      <div style={{ position: "absolute", top: 3, right: 4, pointerEvents: "none" }} title="Gesperrt">
                        <S w={12} c="#e5c100" sw={2}><rect x="4" y="11" width="16" height="10" rx="2" /><path d="M8 11V7a4 4 0 0 1 8 0v4" /></S>
                      </div>
                    </>
                  )}
                  {/* Rubber-band gain : hit-area invisible de 12 px avec ligne jaune 2 px
                      centrée à l'intérieur. Hit-area large pour drag facile, ligne fine
                      pour lisibilité NLE. */}
                  {(() => {
                    const db = c.gainDb ?? 0;
                    const yPct = Math.max(4, Math.min(96, 50 - (db / 12) * 40));
                    return (
                      <div
                        onMouseDown={(ev) => beginGainDrag(ev, c.tlId)}
                        onClick={(ev) => ev.stopPropagation()}
                        title={`Gain: ${db > 0 ? "+" : ""}${db.toFixed(1)} dB · vertikal ziehen`}
                        style={{
                          position: "absolute",
                          left: 0, right: 0,
                          top: `calc(${yPct}% - 6px)`,
                          height: 12,
                          cursor: locked ? "not-allowed" : "ns-resize",
                          zIndex: 8,
                          pointerEvents: locked ? "none" : "auto",
                          background: "transparent",
                        }}
                      >
                        <div
                          style={{
                            position: "absolute",
                            left: 0, right: 0, top: 5, height: 2,
                            background: "#e5c100",
                            boxShadow: "0 0 0 1px rgba(0,0,0,0.7), 0 0 6px rgba(229,193,0,0.75)",
                            pointerEvents: "none",
                          }}
                        />
                      </div>
                    );
                  })()}
                  {/* Fade in/out — style DaVinci : SVG bezier masqué (droite quand curve=0,
                      courbé quand curve≠0) + rond handle au tip + rond central pour la courbure. */}
                  {c.duration > 0 && ((c.fadeIn ?? 0) > 0 || (c.fadeOut ?? 0) > 0) && (() => {
                    const inPct = Math.min(100, ((c.fadeIn ?? 0) / c.duration) * 100);
                    const outPct = Math.min(100, ((c.fadeOut ?? 0) / c.duration) * 100);
                    const inCurve = c.fadeInCurve ?? 0;
                    const outCurve = c.fadeOutCurve ?? 0;
                    const inCtrlY = 2 * (50 + inCurve * 40) - 50;
                    const outCtrlY = 2 * (50 + outCurve * 40) - 50;
                    return (
                      <svg style={{ position: "absolute", inset: 0, width: "100%", height: "100%", pointerEvents: "none", zIndex: 3 }} preserveAspectRatio="none" viewBox="0 0 100 100">
                        {(c.fadeIn ?? 0) > 0 && (
                          <path d={`M 0 0 L ${inPct} 0 Q ${inPct / 2} ${inCtrlY} 0 100 Z`} fill="rgba(0,0,0,0.45)" />
                        )}
                        {(c.fadeOut ?? 0) > 0 && (
                          <path d={`M ${100 - outPct} 0 L 100 0 L 100 100 Q ${100 - outPct / 2} ${outCtrlY} ${100 - outPct} 0 Z`} fill="rgba(0,0,0,0.45)" />
                        )}
                      </svg>
                    );
                  })()}
                  {/* Rond central Fade-in — drag vertical = ajuste la courbure. */}
                  {(c.fadeIn ?? 0) > 0 && c.duration > 0 && (() => {
                    const inPct = Math.min(100, ((c.fadeIn ?? 0) / c.duration) * 100);
                    const midY = 50 + (c.fadeInCurve ?? 0) * 40;
                    return (
                      <div
                        onMouseDown={(ev) => beginFadeCurveDrag(ev, c.tlId, "in")}
                        onClick={(ev) => ev.stopPropagation()}
                        title={`Kurve: ${((c.fadeInCurve ?? 0)).toFixed(2)} · vertikal ziehen zum Kurven`}
                        style={{
                          position: "absolute",
                          left: `calc(${inPct / 2}% - 5px)`,
                          top: `calc(${midY}% - 5px)`,
                          width: 10, height: 10, zIndex: 7,
                          cursor: locked ? "not-allowed" : "ns-resize",
                          background: (c.fadeInCurve ?? 0) !== 0 ? "#e5c100" : "#fff",
                          border: "1.5px solid rgba(0,0,0,0.65)", borderRadius: "50%",
                          boxShadow: "0 1px 3px rgba(0,0,0,0.55)",
                          pointerEvents: locked ? "none" : "auto",
                        }}
                      />
                    );
                  })()}
                  {(c.fadeOut ?? 0) > 0 && c.duration > 0 && (() => {
                    const outPct = Math.min(100, ((c.fadeOut ?? 0) / c.duration) * 100);
                    const midY = 50 + (c.fadeOutCurve ?? 0) * 40;
                    return (
                      <div
                        onMouseDown={(ev) => beginFadeCurveDrag(ev, c.tlId, "out")}
                        onClick={(ev) => ev.stopPropagation()}
                        title={`Kurve: ${((c.fadeOutCurve ?? 0)).toFixed(2)} · vertikal ziehen zum Kurven`}
                        style={{
                          position: "absolute",
                          right: `calc(${outPct / 2}% - 5px)`,
                          top: `calc(${midY}% - 5px)`,
                          width: 10, height: 10, zIndex: 7,
                          cursor: locked ? "not-allowed" : "ns-resize",
                          background: (c.fadeOutCurve ?? 0) !== 0 ? "#e5c100" : "#fff",
                          border: "1.5px solid rgba(0,0,0,0.65)", borderRadius: "50%",
                          boxShadow: "0 1px 3px rgba(0,0,0,0.55)",
                          pointerEvents: locked ? "none" : "auto",
                        }}
                      />
                    );
                  })()}
                  <div
                    onMouseDown={(ev) => beginFadeDrag(ev, c.tlId, "in")}
                    onClick={(ev) => ev.stopPropagation()}
                    title={`Fade in: ${(c.fadeIn ?? 0).toFixed(2)}s · ziehen zum Anpassen`}
                    style={{
                      position: "absolute",
                      left: `calc(${Math.min(100, ((c.fadeIn ?? 0) / c.duration) * 100)}% - 6px)`,
                      top: 2, width: 12, height: 12, zIndex: 6,
                      cursor: locked ? "not-allowed" : "ew-resize",
                      background: "#fff", border: "1.5px solid rgba(0,0,0,0.65)", borderRadius: "50%",
                      boxShadow: "0 1px 3px rgba(0,0,0,0.55)",
                      pointerEvents: locked ? "none" : "auto",
                    }}
                  />
                  <div
                    onMouseDown={(ev) => beginFadeDrag(ev, c.tlId, "out")}
                    onClick={(ev) => ev.stopPropagation()}
                    title={`Fade out: ${(c.fadeOut ?? 0).toFixed(2)}s · ziehen zum Anpassen`}
                    style={{
                      position: "absolute",
                      right: `calc(${Math.min(100, ((c.fadeOut ?? 0) / c.duration) * 100)}% - 6px)`,
                      top: 2, width: 12, height: 12, zIndex: 6,
                      cursor: locked ? "not-allowed" : "ew-resize",
                      background: "#fff", border: "1.5px solid rgba(0,0,0,0.65)", borderRadius: "50%",
                      boxShadow: "0 1px 3px rgba(0,0,0,0.55)",
                      pointerEvents: locked ? "none" : "auto",
                    }}
                  />
                </div>
              );
            })}
            {/* Ghost overlay pour deletes proposés sur cette piste audio. */}
            <ProposalDeletesInRow tlClips={tlClips} kind="a" trackIndex={ti} clipToPct={clipToPct} clipWidthPct={clipWidthPct} />
            {/* #2 : poignée de redimensionnement PAR piste audio (bord inférieur) */}
            {trackResizeHandle(`a${ti}`, "a")}
            </div>
            );
          })}
          {/* Ghost overlay pour splits proposés — lignes verticales qui traversent toutes les rows. */}
          <ProposalSplitsLayer totalDuration={totalDuration} />

          {/* Drop-Preview (DaVinci-style) : ghost du clip pendant le drag depuis
              Medien, positionné sur la row cible avec la bonne largeur. Affiche
              la strip visuelle du clip pour identification. */}
          {dropPreview && (() => {
            const targetRow = videoRowRefs.current.get(dropPreview.trackIdx);
            const container = timelineRef.current?.firstElementChild as HTMLDivElement | null;
            if (!targetRow || !container) return null;
            const rowRect = targetRow.getBoundingClientRect();
            const containerRect = container.getBoundingClientRect();
            const topPx = rowRect.top - containerRect.top;
            return (
              <>
                <div style={{
                  position: "absolute", left: `${dropPreview.leftPct}%`, width: `${dropPreview.widthPct}%`,
                  top: topPx, height: rowRect.height, pointerEvents: "none", zIndex: 14,
                  borderRadius: 7, border: "2px solid #e5c100",
                  background: dropPreview.stripUrl
                    ? `url(${dropPreview.stripUrl}) center/cover no-repeat`
                    : "rgba(229,193,0,0.12)",
                  boxShadow: "0 0 12px rgba(229,193,0,0.6), inset 0 0 0 1px rgba(0,0,0,0.4)",
                  overflow: "hidden",
                }}>
                  {/* Overlay sombre + label pour lisibilité du nom */}
                  <div style={{
                    position: "absolute", inset: 0,
                    background: "linear-gradient(rgba(0,0,0,0.55),rgba(0,0,0,0.2) 40%,rgba(0,0,0,0.55))",
                    display: "flex", alignItems: "center", padding: "0 8px",
                    fontSize: 11, color: "#fff", fontWeight: 600,
                    textShadow: "0 1px 2px rgba(0,0,0,0.8)",
                    whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis",
                  }}>
                    {dropPreview.name}
                  </div>
                </div>
                {/* Ligne verticale snap si magnet actif */}
                {dropPreview.snapPct != null && (
                  <div style={{
                    position: "absolute", left: `${dropPreview.snapPct}%`, top: 0, bottom: 0,
                    width: 1, marginLeft: -0.5,
                    background: "rgba(255,255,255,0.95)",
                    boxShadow: "0 0 6px rgba(255,255,255,0.9)",
                    pointerEvents: "none", zIndex: 15,
                  }} />
                )}
              </>
            );
          })()}

          {/* Rubber-band selection : rectangle bleu semi-transparent qui suit le
              drag depuis l'espace vide. Sélectionne les clips overlappés au mouseup. */}
          {rubberBand && (
            <div style={{
              position: "absolute",
              left: Math.min(rubberBand.x1, rubberBand.x2),
              top: Math.min(rubberBand.y1, rubberBand.y2),
              width: Math.abs(rubberBand.x2 - rubberBand.x1),
              height: Math.abs(rubberBand.y2 - rubberBand.y1),
              background: "rgba(100,180,255,0.15)",
              border: "1px solid rgba(100,180,255,0.6)",
              pointerEvents: "none",
              zIndex: 20,
            }} />
          )}

          {/* Guide vertical du blade mode : suit le curseur, affiche le TC.
              Rendu conditionnel : n'existe que si tool === "blade". */}
          {tool === "blade" && (
            <div ref={bladeCursorRef} style={{ position: "absolute", left: 0, top: 0, bottom: 0, width: 1, background: "#e5c100", boxShadow: "0 0 8px rgba(229,193,0,0.9)", pointerEvents: "none", zIndex: 15, display: "none", willChange: "transform" }}>
              <div ref={bladeLabelRef} style={{ position: "absolute", top: 24, left: 6, whiteSpace: "nowrap", fontSize: 11, fontFamily: "ui-monospace, monospace", color: "#1a1a1a", background: "#e5c100", padding: "2px 6px", borderRadius: 3, fontWeight: 600, boxShadow: "0 2px 6px rgba(0,0,0,0.5)" }} />
            </div>
          )}

          {/* Snap-Hilfslinie (DaVinci) — UN seul élément DOM piloté par ref+style
              pendant le drag (jamais setState). translateX en px depuis le bord
              gauche du conteneur ; caché tant qu'aucun snap n'est actif. */}
          <div
            ref={snapGuideRef}
            style={{ position: "absolute", left: 0, top: 0, bottom: 0, width: 3, background: "#e5c100", boxShadow: "0 0 14px rgba(229,193,0,1), 0 0 4px rgba(255,255,255,0.9)", pointerEvents: "none", zIndex: 15, display: "none", willChange: "transform" }}
          >
            <div
              ref={snapGuideLabelRef}
              style={{ position: "absolute", top: 4, left: 6, whiteSpace: "nowrap", fontSize: 11, fontWeight: 700, fontFamily: "ui-monospace, monospace", color: "#1a1a1a", background: "#e5c100", padding: "2px 7px", borderRadius: 4, boxShadow: "0 2px 6px rgba(0,0,0,0.6)" }}
            />
          </div>

          {/* HUD "distance to snap" — badge centré à mi-chemin entre l'edge
              draggée et le candidat le plus proche. transform + display piloté
              par ref pendant le drag → zéro React re-render. */}
          <div
            ref={snapDistanceRef}
            style={{ position: "absolute", left: 0, top: 24, pointerEvents: "none", zIndex: 12, display: "none", willChange: "transform", transform: "translateX(-50%)", fontSize: 10, fontFamily: "ui-monospace, monospace", padding: "2px 6px", borderRadius: 3, background: "#1a1a1a", color: "#e0e0e0", boxShadow: "0 2px 6px rgba(0,0,0,0.6)", whiteSpace: "nowrap" }}
          />

          {/* Abspielkopf (draggable) — hitbox large sur toute la ligne verticale */}
          <div style={{ position: "absolute", left: `${playheadPct}%`, top: 0, bottom: 0, width: 1, background: "#fff", pointerEvents: "none", zIndex: 10 }}>
            {/* Zone invisible élargie pour drag depuis n'importe où sur la ligne
                — désactivée en blade mode pour laisser passer le clic vers le
                cut (sinon la hitbox 29px absorbe les clics près du playhead). */}
            <div
              onMouseDown={startPlayheadDrag}
              style={{ position: "absolute", top: 0, bottom: 0, left: -14, width: 29, background: "transparent", pointerEvents: tool === "blade" ? "none" : "auto", cursor: "ew-resize" }}
              title="Abspielkopf ziehen"
            />
            {/* Triangle handle en haut */}
            <div
              onMouseDown={startPlayheadDrag}
              style={{ position: "absolute", top: 0, left: -8, width: 17, height: 20, background: "#e8e8e8", clipPath: "polygon(0 0,100% 0,100% 65%,50% 100%,0 65%)", pointerEvents: tool === "blade" ? "none" : "auto", cursor: "ew-resize" }}
            />
          </div>
        </div>
        </div>
        </div>

        {/* VU-Meter */}
        <div style={{ width: 68, flex: "none", display: "flex", padding: "8px 8px 10px 0", gap: 4 }}>
          <div style={{ display: "flex", flexDirection: "column", justifyContent: "space-between", fontSize: 9, color: "#6a6a6a", padding: "2px 0", textAlign: "right", width: 16 }}>
            {["0", "10", "20", "30", "40", "50"].map((n) => <span key={n}>{n}</span>)}
          </div>
          <div style={{ flex: 1, display: "flex", gap: 3 }}>
            {vu.map((v, i) => (
              <div key={i} style={{ flex: 1, borderRadius: 2, background: "#1a1a1c", position: "relative", overflow: "hidden" }}>
                <div style={{ position: "absolute", left: 0, right: 0, bottom: 0, height: `${v * 100}%`, background: "linear-gradient(#e02020 0%,#e0a020 22%,#e0d020 40%,#6ad04a 60%,#3aa83a 100%)", backgroundSize: `100% ${100 / v}%`, backgroundPosition: "bottom", transition: "height 30ms linear" }} />
              </div>
            ))}
          </div>
        </div>
      </div>
        </Panel>
      </PanelGroup>

      {/* ─── Untere Leiste ─── */}
      <div style={{ height: 50, flex: "none", display: "flex", alignItems: "center", padding: "0 18px", gap: 16, background: "#161617", borderTop: "1px solid #000", position: "relative" }}>
        <button onClick={goHome} style={sqBtn} title="Start"><S><path d="M3 11l9-8 9 8M5 9v11h14V9" /></S></button>
        <button onClick={() => setSettingsOpen((o) => !o)} style={{ ...sqBtn, background: settingsOpen ? "#3a3a3e" : "#242426" }} title="Einstellungen"><S sw={1.7}><circle cx="12" cy="12" r="3" /><path d="M19 12a7 7 0 0 0-.1-1.2l2-1.5-2-3.4-2.3 1a7 7 0 0 0-2-1.2l-.3-2.5H10.7l-.3 2.5a7 7 0 0 0-2 1.2l-2.3-1-2 3.4 2 1.5A7 7 0 0 0 5 12c0 .4 0 .8.1 1.2l-2 1.5 2 3.4 2.3-1a7 7 0 0 0 2 1.2l.3 2.5h2.6l.3-2.5a7 7 0 0 0 2-1.2l2.3 1 2-3.4-2-1.5c.1-.4.1-.8.1-1.2z" /></S></button>
        <div style={{ flex: 1, display: "flex", alignItems: "center", justifyContent: "center", gap: 40, fontSize: 13 }}>
          {TABS.map((t) => {
            const active = tab === t.id;
            const col = active ? "#b9d94a" : "#9a9a9a";
            return (
              <button key={t.id} onClick={() => setTab(t.id)} style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 5, color: col }}>
                <span style={{ display: "flex", alignItems: "center", gap: 6, fontWeight: active ? 600 : 400 }}>
                  {t.id === "cut" && <S c={col}><circle cx="6" cy="6" r="3" /><circle cx="6" cy="18" r="3" /><path d="M20 4L8.5 15.5M20 20L8.5 8.5" /></S>}
                  {t.id === "edit" && <S c={col} sw={1.7}><path d="M12 20h9M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4z" /></S>}
                  {t.id === "color" && <S c={col} sw={1.7}><path d="M12 3c4.5 0 8 3 8 7 0 3-2.5 4-4 4h-2a2 2 0 0 0-1 3.7A2 2 0 0 1 12 21a9 9 0 0 1 0-18z" /><circle cx="7.5" cy="11" r="1" /><circle cx="12" cy="7.5" r="1" /><circle cx="16.5" cy="11" r="1" /></S>}
                  {t.id === "sound" && <S c={col} sw={1.7}><path d="M9 18V5l12-2v13" /><circle cx="6" cy="18" r="3" /><circle cx="18" cy="16" r="3" /></S>}
                  {t.label}
                </span>
                <span style={{ width: 34, height: 2, background: active ? "#b9d94a" : "transparent", borderRadius: 2 }} />
              </button>
            );
          })}
        </div>
        <button onClick={toggleChatPanel}
          style={{ display: "flex", alignItems: "center", gap: 8, background: chatPanelOpen ? "#e5c100" : "#242426", borderRadius: 8, height: 34, padding: "0 14px", fontSize: 13, color: chatPanelOpen ? "#000" : "#cfcfcf", fontWeight: chatPanelOpen ? 600 : 400, boxShadow: chatPanelOpen ? "0 0 0 1px rgba(0,0,0,0.4), 0 2px 8px rgba(229,193,0,0.4)" : "none" }}>
          <S w={15} c="currentColor" sw={1.7}><path d="M13 3l2.5 6.5L22 12l-6.5 2.5L13 21l-2.5-6.5L4 12l6.5-2.5z" /></S>KI-Agent
        </button>
        <button onClick={() => setHistOpen((o) => !o)} style={{ display: "flex", alignItems: "center", gap: 8, background: "#242426", borderRadius: 8, height: 34, padding: "0 14px", fontSize: 13, color: "#cfcfcf" }}>
          <S w={15} sw={1.7}><path d="M3 12a9 9 0 1 0 3-6.7L3 8M3 4v4h4M12 8v4l3 2" /></S>Verlauf
          <S w={11} c="#8a8a8a" sw={2.4}><path d="M6 9l6 6 6-6" /></S>
        </button>
        {histOpen && (
          <div style={{ position: "absolute", bottom: 54, right: 130, background: "#242426", borderRadius: 8, padding: 4, zIndex: 10, boxShadow: "0 8px 24px rgba(0,0,0,.5)", maxHeight: 260, overflowY: "auto", minWidth: 220 }}>
            {timelines.length === 0 ? (
              <div style={{ padding: 12, fontSize: 12, color: "#7a7a7a", textAlign: "center" }}>Keine Verläufe.</div>
            ) : timelines.map((t) => (
              <button
                key={t.id}
                onClick={() => loadTimeline(t.id)}
                style={{
                  padding: "8px 12px", fontSize: 12, color: "#cfcfcf",
                  whiteSpace: "nowrap", borderBottom: "1px solid #1c1c1e",
                  display: "block", width: "100%", textAlign: "left",
                  background: "transparent", border: "none", cursor: "pointer",
                  fontFamily: "inherit",
                }}
                onMouseEnter={(e) => (e.currentTarget.style.background = "rgba(255,255,255,.04)")}
                onMouseLeave={(e) => (e.currentTarget.style.background = "transparent")}
              >
                <div style={{ fontWeight: 500 }}>{t.name}</div>
                <div style={{ fontSize: 10, color: "#7a7a7a", fontFamily: "ui-monospace, monospace" }}>
                  {t.erstellt_am ? new Date(t.erstellt_am).toLocaleString("de-DE") : "—"}
                </div>
              </button>
            ))}
          </div>
        )}
        <button onClick={undo} style={sqBtn} title="Rückgängig"><S><path d="M9 14L4 9l5-5M4 9h11a5 5 0 0 1 0 10h-3" /></S></button>
        <button onClick={redo} style={sqBtn} title="Wiederholen (Cmd+Shift+Z)"><S><path d="M15 14l5-5-5-5M20 9H9a5 5 0 0 0 0 10h3" /></S></button>
      </div>

      {/* ─── KI-Agent Drawer ─── */}
      {aiOpen && (
        <div style={{ position: "fixed", right: 18, bottom: 66, width: 400, maxHeight: 460, background: "#161617", borderRadius: 14, border: "1px solid #232326", boxShadow: "0 20px 60px rgba(0,0,0,.6)", display: "flex", flexDirection: "column", zIndex: 100 }}>
          <div style={{ padding: "12px 16px", borderBottom: "1px solid #232326", display: "flex", alignItems: "center", gap: 8 }}>
            <S c="#c9a4ff" sw={1.7}><path d="M13 3l2.5 6.5L22 12l-6.5 2.5L13 21l-2.5-6.5L4 12l6.5-2.5z" /></S>
            <span style={{ fontSize: 13, fontWeight: 600 }}>KI-Agent</span>
            <span style={{ marginLeft: "auto", fontSize: 10, color: "#7a7a7a" }}>qwen2.5:14b · 17 Tools</span>
            <button onClick={() => setAiOpen(false)} style={{ fontSize: 16, color: "#7a7a7a" }}>✕</button>
          </div>
          <div ref={aiListRef} style={{ flex: 1, minHeight: 0, overflowY: "auto", padding: "10px 16px" }}>
            {aiHistory.length === 0 && (
              <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                <div style={{ fontSize: 11, color: "#7a7a7a" }}>Vorschläge :</div>
                {["Wie viele Clips habe ich?", "Erzeuge einen kinematischen Rohschnitt", "Liste alle Sprecher auf"].map((s) => (
                  <button key={s} onClick={() => sendAi(s)}
                    style={{ padding: "8px 10px", borderRadius: 8, background: "#1a1a1c", border: "1px solid #232326", color: "#cfcfcf", fontSize: 11, textAlign: "left" }}>{s}</button>
                ))}
              </div>
            )}
            {aiHistory.map((m, i) => (
              <div key={i} style={{ margin: "6px 0", display: "flex", justifyContent: m.role === "user" ? "flex-end" : "flex-start" }}>
                <div style={{ maxWidth: "84%", padding: "7px 10px", borderRadius: 10, fontSize: 11.5, lineHeight: 1.5, background: m.role === "user" ? "#3a2a5a" : "#232326", color: "#f0f0f0" }}>{m.content}</div>
              </div>
            ))}
            {aiBusy && (
              <div style={{ margin: "6px 0", display: "flex", alignItems: "center", gap: 6, color: "#7a7a7a", fontSize: 11 }}>
                <S w={11} c="#c9a4ff" style={{ animation: "spin 1s linear infinite" }}><path d="M12 3v3M12 18v3M3 12h3M18 12h3M5.6 5.6l2.1 2.1M16.3 16.3l2.1 2.1M18.4 5.6l-2.1 2.1M7.7 16.3l-2.1 2.1" /></S>
                Agent überlegt…
              </div>
            )}
          </div>
          <form onSubmit={(e) => { e.preventDefault(); sendAi(); }}
            style={{ display: "flex", gap: 6, padding: 10, borderTop: "1px solid #232326" }}>
            <input ref={aiInputRef} autoFocus value={aiPrompt} onChange={(e) => setAiPrompt(e.target.value)}
              placeholder={aiBusy ? "Agent überlegt…" : "Frag den Agenten…"} disabled={aiBusy}
              style={{ flex: 1, height: 32, padding: "0 10px", background: "#1a1a1c", border: "1px solid #232326", borderRadius: 8, color: "#e0e0e0", fontSize: 12, fontFamily: "inherit", outline: "none" }} />
            <button type="submit" disabled={aiBusy || !aiPrompt.trim()}
              style={{ width: 32, height: 32, borderRadius: 8, border: "none", background: "#3a2a5a", color: aiPrompt.trim() && !aiBusy ? "#c9a4ff" : "#5a4a7a", cursor: aiPrompt.trim() && !aiBusy ? "pointer" : "not-allowed", display: "grid", placeItems: "center" }}>
              <S w={13} c="currentColor"><path d="M22 2L11 13M22 2l-7 20-4-9-9-4z" /></S>
            </button>
          </form>
        </div>
      )}

      {/* ─── Toasts ─── */}
      <div style={{ position: "fixed", right: 18, top: 60, display: "flex", flexDirection: "column", gap: 8, zIndex: 200, pointerEvents: "none" }}>
        {toasts.map((t) => {
          const colors = {
            ok:   { bg: "#0d3a1e", border: "#2fbf7e", text: "#c8f0d8" },
            warn: { bg: "#3a2d0d", border: "#e0b84a", text: "#f0dfa8" },
            err:  { bg: "#3a0d0d", border: "#e07a7a", text: "#f0c0c0" },
            info: { bg: "#1a1e3a", border: "#7c5cff", text: "#d0d0f0" },
          }[t.kind];
          return (
            <div key={t.id} style={{
              padding: "10px 14px", borderRadius: 10,
              background: colors.bg, border: `1px solid ${colors.border}`,
              color: colors.text, fontSize: 12, fontWeight: 500,
              boxShadow: "0 8px 24px rgba(0,0,0,.45)",
              maxWidth: 380, animation: "toast-in 0.18s ease-out",
              pointerEvents: "auto",
            }}>
              {t.msg}
            </div>
          );
        })}
      </div>

      {/* ─── HUD flottant Edge/Roll-Trim (delta signé + nouvelle durée) ─── */}
      {trimHud && (
        <div style={{
          position: "fixed", left: trimHud.x + 15, top: trimHud.y - 40, zIndex: 999,
          background: "rgba(0,0,0,0.85)", padding: "4px 8px", borderRadius: 4,
          fontSize: 11, color: "#fff", fontFamily: "ui-monospace, monospace",
          lineHeight: 1.4, pointerEvents: "none",
          borderLeft: `3px solid ${trimHud.delta >= 0 ? "#6ad04a" : "#e0a020"}`, // vert = extend, orange = shrink
        }}>
          {trimHud.label && <div style={{ fontSize: 9, color: "#aaa" }}>{trimHud.label}</div>}
          <div>{`${trimHud.delta >= 0 ? "+" : "-"}${fmtSec(Math.abs(trimHud.delta))}`}</div>
          <div>{fmtSec(trimHud.newDuration)}</div>
        </div>
      )}
      {/* ─── Tab-Overlays (Bearbeiten/Farbe/Ton) ─── */}
      {tab !== "cut" && (
        <div style={{
          position: "fixed", left: 18, right: 386, bottom: 66, height: 260,
          background: "rgba(15,15,17,0.96)", backdropFilter: "blur(6px)",
          border: "1px solid #2a2a2e", borderRadius: 12, padding: 20,
          display: "flex", flexDirection: "column", gap: 12,
          boxShadow: "0 20px 60px rgba(0,0,0,.6)", zIndex: 90,
        }}>
          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
            <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
              <span style={{ fontSize: 15, fontWeight: 600, color: "#b9d94a" }}>
                {tab === "edit" ? "Bearbeiten" : tab === "color" ? "Farbe" : "Ton"}
              </span>
              <span style={{ fontSize: 11, color: "#7a7a7a" }}>{selectedTlIds.size > 0 ? `${selectedTlIds.size} Clip(s) ausgewählt` : "Nichts ausgewählt"}</span>
            </div>
            <button onClick={() => setTab("cut")} style={{ fontSize: 11, color: "#7a7a7a", background: "transparent", border: "none", cursor: "pointer" }}>← Zurück zum Schnitt</button>
          </div>

          {tab === "edit" && (
            <div style={{ display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: 10, flex: 1 }}>
              <div style={{ background: "#1a1a1c", borderRadius: 8, padding: 12 }}>
                <div style={{ fontSize: 11, color: "#8a8a8a", marginBottom: 8 }}>Trimmen</div>
                <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
                  <button onClick={() => trimSelected("left")} disabled={!selectedTlIds.size} style={{ padding: "6px 10px", background: "#242426", borderRadius: 6, fontSize: 11, color: "#cfcfcf", border: "none", cursor: selectedTlIds.size ? "pointer" : "not-allowed", opacity: selectedTlIds.size ? 1 : 0.4 }}>Links −0,5 s</button>
                  <button onClick={() => trimSelected("right")} disabled={!selectedTlIds.size} style={{ padding: "6px 10px", background: "#242426", borderRadius: 6, fontSize: 11, color: "#cfcfcf", border: "none", cursor: selectedTlIds.size ? "pointer" : "not-allowed", opacity: selectedTlIds.size ? 1 : 0.4 }}>Rechts −0,5 s</button>
                  <button onClick={() => trimSelected("both")} disabled={!selectedTlIds.size} style={{ padding: "6px 10px", background: "#242426", borderRadius: 6, fontSize: 11, color: "#cfcfcf", border: "none", cursor: selectedTlIds.size ? "pointer" : "not-allowed", opacity: selectedTlIds.size ? 1 : 0.4 }}>Beide −1 s</button>
                </div>
              </div>
              <div style={{ background: "#1a1a1c", borderRadius: 8, padding: 12 }}>
                <div style={{ fontSize: 11, color: "#8a8a8a", marginBottom: 8 }}>Clip-Aktionen</div>
                <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
                  <button onClick={splitAtGlobalTime} style={{ padding: "6px 10px", background: "#242426", borderRadius: 6, fontSize: 11, color: "#cfcfcf", border: "none", cursor: "pointer" }}>Schneiden</button>
                  <button onClick={duplicateSelected} disabled={!selectedTlIds.size} style={{ padding: "6px 10px", background: "#242426", borderRadius: 6, fontSize: 11, color: "#cfcfcf", border: "none", cursor: selectedTlIds.size ? "pointer" : "not-allowed", opacity: selectedTlIds.size ? 1 : 0.4 }}>Duplizieren</button>
                  <button onClick={removeSelected} disabled={!selectedTlIds.size} style={{ padding: "6px 10px", background: "#3a0d0d", borderRadius: 6, fontSize: 11, color: "#e0a0a0", border: "none", cursor: selectedTlIds.size ? "pointer" : "not-allowed", opacity: selectedTlIds.size ? 1 : 0.4 }}>Löschen</button>
                </div>
              </div>
              <div style={{ background: "#1a1a1c", borderRadius: 8, padding: 12 }}>
                <div style={{ fontSize: 11, color: "#8a8a8a", marginBottom: 8 }}>Zwischenablage</div>
                <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
                  <button onClick={cutSelected} disabled={!selectedTlIds.size} style={{ padding: "6px 10px", background: "#242426", borderRadius: 6, fontSize: 11, color: "#cfcfcf", border: "none", cursor: selectedTlIds.size ? "pointer" : "not-allowed", opacity: selectedTlIds.size ? 1 : 0.4 }}>Ausschneiden</button>
                  <button onClick={copySelected} disabled={!selectedTlIds.size} style={{ padding: "6px 10px", background: "#242426", borderRadius: 6, fontSize: 11, color: "#cfcfcf", border: "none", cursor: selectedTlIds.size ? "pointer" : "not-allowed", opacity: selectedTlIds.size ? 1 : 0.4 }}>Kopieren</button>
                  <button onClick={paste} disabled={!clipboard.length} style={{ padding: "6px 10px", background: "#242426", borderRadius: 6, fontSize: 11, color: "#cfcfcf", border: "none", cursor: clipboard.length ? "pointer" : "not-allowed", opacity: clipboard.length ? 1 : 0.4 }}>Einfügen ({clipboard.length})</button>
                </div>
              </div>
            </div>
          )}

          {tab === "color" && (
            <div style={{ flex: 1, display: "flex", gap: 20, alignItems: "center", justifyContent: "center", flexDirection: "column", color: "#8a8a8a" }}>
              <div style={{ width: 80, height: 80, borderRadius: "50%", background: "conic-gradient(#ff4d4d,#ffd24d,#5dff8f,#4dd2ff,#a04dff,#ff4d9d,#ff4d4d)" }} />
              <div style={{ textAlign: "center", maxWidth: 460 }}>
                <div style={{ fontSize: 13, color: "#cfcfcf", marginBottom: 6 }}>Farbkorrektur — Roadmap V6</div>
                <div style={{ fontSize: 11 }}>
                  Kurven, Weißabgleich, LUT-Import und Stapel-Grading sind für die nächste Iteration geplant.
                  Für automatisches Grading kannst du den KI-Agenten fragen: <span style={{ color: "#c9a4ff" }}>&quot;Analysiere die Farbtemperatur aller Clips&quot;</span>.
                </div>
              </div>
              <button onClick={() => { setTab("cut"); setAiOpen(true); setAiPrompt("Analysiere die Farbtemperatur aller Clips."); }}
                style={{ padding: "8px 14px", background: "#3a2a5a", color: "#c9a4ff", borderRadius: 8, border: "1px solid #4a3a6a", fontSize: 12, cursor: "pointer" }}>
                KI-Agent öffnen
              </button>
            </div>
          )}

          {tab === "sound" && (
            <div style={{ flex: 1, display: "flex", gap: 20, padding: 12 }}>
              <div style={{ flex: 1, display: "flex", flexDirection: "column", gap: 12 }}>
                <div>
                  <div style={{ fontSize: 11, color: "#8a8a8a", marginBottom: 6 }}>Master-Lautstärke ({Math.round(volume * 100)}%)</div>
                  <input type="range" min={0} max={1} step={0.01} value={volume} onChange={(e) => { setVolume(parseFloat(e.target.value)); if (muted) setMuted(false); }} style={{ width: "100%", accentColor: "#b9d94a" }} />
                </div>
                <div style={{ display: "flex", gap: 6 }}>
                  <button onClick={() => setMuted((m) => !m)} style={{ padding: "6px 10px", borderRadius: 6, fontSize: 11, background: muted ? "#3a0d0d" : "#242426", color: muted ? "#e07a7a" : "#cfcfcf", border: "none", cursor: "pointer" }}>
                    {muted ? "Ton wieder an" : "Stumm"}
                  </button>
                  <button onClick={() => setVolume(1)} style={{ padding: "6px 10px", borderRadius: 6, fontSize: 11, background: "#242426", color: "#cfcfcf", border: "none", cursor: "pointer" }}>100%</button>
                  <button onClick={() => setVolume(0.5)} style={{ padding: "6px 10px", borderRadius: 6, fontSize: 11, background: "#242426", color: "#cfcfcf", border: "none", cursor: "pointer" }}>50%</button>
                </div>
                <div style={{ fontSize: 11, color: "#8a8a8a" }}>
                  Erweiterte Ton-Tools (EQ, Ducking, Musik-Bett, Sprecher-Trennung) über den KI-Agenten oder die Roadmap V6.
                </div>
              </div>
              <div style={{ width: 120, display: "flex", alignItems: "center", justifyContent: "center", gap: 4 }}>
                <div style={{ display: "flex", flexDirection: "column", justifyContent: "space-between", fontSize: 9, color: "#6a6a6a", textAlign: "right", width: 14 }}>
                  {["0", "10", "20", "30", "40", "50"].map((n) => <span key={n}>{n}</span>)}
                </div>
                <div style={{ display: "flex", gap: 3, height: 140 }}>
                  {vu.map((v, i) => (
                    <div key={i} style={{ width: 18, borderRadius: 2, background: "#1a1a1c", position: "relative", overflow: "hidden" }}>
                      <div style={{ position: "absolute", left: 0, right: 0, bottom: 0, height: `${v * 100}%`, background: "linear-gradient(#e02020 0%,#e0a020 22%,#e0d020 40%,#6ad04a 60%,#3aa83a 100%)", transition: "height 30ms linear" }} />
                    </div>
                  ))}
                </div>
              </div>
            </div>
          )}
        </div>
      )}

      {/* ─── Einstellungen Modal ─── */}
      {settingsOpen && (
        <div onClick={() => setSettingsOpen(false)} style={{ position: "fixed", inset: 0, background: "rgba(0,0,0,0.5)", zIndex: 300, display: "flex", alignItems: "center", justifyContent: "center" }}>
          <div onClick={(e) => e.stopPropagation()} style={{ width: 480, maxHeight: "80vh", background: "#161617", borderRadius: 14, border: "1px solid #232326", boxShadow: "0 20px 60px rgba(0,0,0,.7)", display: "flex", flexDirection: "column", overflow: "hidden" }}>
            <div style={{ padding: "14px 18px", borderBottom: "1px solid #232326", display: "flex", alignItems: "center", gap: 8 }}>
              <S c="#b9d94a" sw={1.7}><circle cx="12" cy="12" r="3" /><path d="M19 12a7 7 0 0 0-.1-1.2l2-1.5-2-3.4-2.3 1a7 7 0 0 0-2-1.2l-.3-2.5H10.7l-.3 2.5a7 7 0 0 0-2 1.2l-2.3-1-2 3.4 2 1.5A7 7 0 0 0 5 12c0 .4 0 .8.1 1.2l-2 1.5 2 3.4 2.3-1a7 7 0 0 0 2 1.2l.3 2.5h2.6l.3-2.5a7 7 0 0 0 2-1.2l2.3 1 2-3.4-2-1.5c.1-.4.1-.8.1-1.2z" /></S>
              <span style={{ fontSize: 14, fontWeight: 600 }}>Einstellungen</span>
              <button onClick={() => setSettingsOpen(false)} style={{ marginLeft: "auto", fontSize: 16, color: "#7a7a7a", background: "transparent", border: "none", cursor: "pointer" }}>✕</button>
            </div>
            <div style={{ padding: "16px 18px", overflowY: "auto", display: "flex", flexDirection: "column", gap: 14, fontSize: 12, color: "#cfcfcf" }}>
              <section>
                <div style={{ fontSize: 11, color: "#8a8a8a", marginBottom: 8 }}>Wiedergabe</div>
                <label style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 6 }}>
                  <span style={{ minWidth: 130 }}>Lautstärke</span>
                  <input type="range" min={0} max={1} step={0.01} value={volume} onChange={(e) => setVolume(parseFloat(e.target.value))} style={{ flex: 1, accentColor: "#b9d94a" }} />
                  <span style={{ minWidth: 40, textAlign: "right", fontFamily: "ui-monospace, monospace" }}>{Math.round(volume * 100)}%</span>
                </label>
                <label style={{ display: "flex", alignItems: "center", gap: 10 }}>
                  <input type="checkbox" checked={muted} onChange={(e) => setMuted(e.target.checked)} />
                  <span>Stummschalten</span>
                </label>
              </section>
              <section>
                <div style={{ fontSize: 11, color: "#8a8a8a", marginBottom: 8 }}>Timeline</div>
                <label style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 6 }}>
                  <span style={{ minWidth: 130 }}>Zoom</span>
                  <button onClick={zoomOut} style={{ width: 22, height: 22, borderRadius: 4, background: "#242426", color: "#cfcfcf", border: "none", cursor: "pointer" }}>−</button>
                  <input type="range" min={MIN_ZOOM} max={MAX_ZOOM} step={0.05} value={zoom} onChange={(e) => setZoom(parseFloat(e.target.value))} style={{ flex: 1, accentColor: "#b9d94a" }} />
                  <button onClick={zoomIn} style={{ width: 22, height: 22, borderRadius: 4, background: "#242426", color: "#cfcfcf", border: "none", cursor: "pointer" }}>+</button>
                  <span style={{ minWidth: 40, textAlign: "right", fontFamily: "ui-monospace, monospace" }}>{Math.round(zoom * 100)}%</span>
                </label>
                <button onClick={zoomFit} style={{ padding: "5px 10px", background: "#242426", borderRadius: 6, fontSize: 11, color: "#cfcfcf", border: "none", cursor: "pointer" }}>Auf 100% zurücksetzen</button>
              </section>
              <section>
                <div style={{ fontSize: 11, color: "#8a8a8a", marginBottom: 8 }}>Marker ({markers.length})</div>
                {markers.length === 0
                  ? <div style={{ fontSize: 11, color: "#7a7a7a" }}>Keine Marker gesetzt. Taste <kbd style={{ padding: "1px 5px", background: "#242426", borderRadius: 3, fontFamily: "ui-monospace, monospace" }}>M</kbd> zum Setzen.</div>
                  : <div style={{ maxHeight: 120, overflowY: "auto", display: "flex", flexDirection: "column", gap: 4 }}>
                      {markers.map((m) => (
                        <div key={m.id} style={{ display: "flex", alignItems: "center", gap: 8, padding: "5px 8px", background: "#1a1a1c", borderRadius: 4, fontSize: 11 }}>
                          <button onClick={() => { seekSeconds(m.time); setSettingsOpen(false); }} style={{ flex: 1, textAlign: "left", background: "transparent", border: "none", cursor: "pointer", color: "#cfcfcf", fontFamily: "inherit" }}>
                            <span>{m.label}</span>
                            <span style={{ marginLeft: 8, color: "#8a8a8a", fontFamily: "ui-monospace, monospace" }}>{fmtTC(m.time)}</span>
                          </button>
                          <button onClick={() => setMarkers((cur) => cur.filter((x) => x.id !== m.id))} title="Löschen" style={{ color: "#e07a7a", background: "transparent", border: "none", cursor: "pointer", fontSize: 12 }}>×</button>
                        </div>
                      ))}
                    </div>}
              </section>
              <section>
                <div style={{ fontSize: 11, color: "#8a8a8a", marginBottom: 8 }}>Tastaturkürzel</div>
                <div style={{ display: "grid", gridTemplateColumns: "auto 1fr", gap: "4px 12px", fontSize: 11 }}>
                  {[
                    ["Space", "Abspielen / Pause"],
                    ["← / →", "1 s zurück / vor (⇧ = 5 s)"],
                    ["Home / End", "Anfang / Ende"],
                    ["C oder S", "Am Playhead schneiden"],
                    ["M", "Marker setzen"],
                    ["F", "Vollbild"],
                    ["⌫", "Ausgewählte löschen"],
                    ["Cmd Z / ⇧Z", "Rückgängig / Wiederholen"],
                    ["Cmd + / −", "Timeline ein-/auszoomen"],
                    ["Cmd 0", "Zoom zurücksetzen"],
                  ].map(([k, l]) => (
                    <div key={k} style={{ display: "contents" }}>
                      <kbd style={{ padding: "2px 6px", background: "#242426", borderRadius: 3, fontFamily: "ui-monospace, monospace", color: "#cfcfcf", justifySelf: "start" }}>{k}</kbd>
                      <span style={{ color: "#8a8a8a" }}>{l}</span>
                    </div>
                  ))}
                </div>
              </section>
              <section>
                <div style={{ fontSize: 11, color: "#8a8a8a", marginBottom: 8 }}>Verbindung</div>
                <div style={{ fontSize: 11, color: "#8a8a8a", fontFamily: "ui-monospace, monospace" }}>
                  API: {API || "(same-origin)"}
                  <br />
                  Clips: {clips.length} · Timelines: {timelines.length} · Zwischenablage: {clipboard.length}
                </div>
              </section>
            </div>
          </div>
        </div>
      )}

      {/* ─── Menu contextuel (#3) — clip / en-tête / zone vide ─── */}
      {contextMenu && (
        <div data-context-menu
          style={{ position: "fixed", left: contextMenu.x, top: contextMenu.y, zIndex: 400, background: "#242426", borderRadius: 8, padding: 4, minWidth: 200, boxShadow: "0 10px 30px rgba(0,0,0,.6)", border: "1px solid #313134" }}>
          {contextMenu.items.map((it, i) => it.separator ? (
            <div key={i} style={{ height: 1, background: "#333", margin: "4px 6px" }} />
          ) : (
            <button key={i} disabled={it.disabled}
              onClick={() => { if (it.disabled) return; setContextMenu(null); it.onClick?.(); }}
              onMouseEnter={(e) => { if (!it.disabled) e.currentTarget.style.background = "#333"; }}
              onMouseLeave={(e) => (e.currentTarget.style.background = "transparent")}
              style={{ display: "flex", alignItems: "center", justifyContent: "space-between", width: "100%", textAlign: "left", padding: "7px 12px", borderRadius: 6, fontSize: 13, color: it.disabled ? "#5a5a5a" : "#cfcfcf", background: "transparent", border: "none", cursor: it.disabled ? "not-allowed" : "pointer", fontFamily: "inherit", whiteSpace: "nowrap" }}>
              <span>{it.label}</span>
              {it.kbd && <span style={{ marginLeft: 16, fontSize: 10, color: "#7a7a7a", fontFamily: "ui-monospace, monospace" }}>{it.kbd}</span>}
            </button>
          ))}
        </div>
      )}

      <style>{`@keyframes toast-in { from { opacity: 0; transform: translateY(-8px); } to { opacity: 1; transform: translateY(0); } }`}</style>

      {/* KI-Schnittassistent — floating chat panel + FAB. */}
      <ChatPanel />
    </div>
  );
}
