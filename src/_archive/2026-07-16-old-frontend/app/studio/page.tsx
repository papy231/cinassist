"use client";

/**
 * CinAssist Studio — Descript/Runway-inspiriertes moderne UI.
 *
 * Layout :
 *   ┌──────────── Header (44px) ────────────┐
 *   │  Logo   Menü  |  Projekt-Titel  |  Share
 *   ├──────────────┬────────────────────────┤
 *   │              │                        │
 *   │   Preview    │     Media-Browser       │
 *   │              │     (Grid Clips)        │
 *   ├──────────────┴────────────────────────┤
 *   │  Timeline (Ruler + Multi-Tracks)       │
 *   ├────────────────────────────────────────┤
 *   │  Bottom Tabs (KI / Suche / Historie)   │
 *   └────────────────────────────────────────┘
 *
 * Design-Tokens siehe studio.module.css.
 */

import { useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { motion, AnimatePresence } from "framer-motion";
import {
  Search,
  Bot,
  Clock as ClockIcon,
  Play,
  Pause,
  SkipBack,
  SkipForward,
  Volume2,
  Maximize2,
  Scissors,
  Layers,
  Sparkles,
  Share2,
  ChevronDown,
  Video,
  Music2,
  Type,
  Send,
  History,
  Plus,
  Save,
  Download,
  Upload,
  ArrowLeft,
} from "lucide-react";
import { fetchClips, type ClipDTO } from "@/lib/api";
import styles from "./studio.module.css";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8001";
const PX_PER_SEC_BASE = 32; // Timeline-Zoom : 32 px pro Sekunde × zoom
const MIN_ZOOM = 0.25;
const MAX_ZOOM = 6;

type BottomTab = "ai" | "search" | "history";

type TimelineClip = {
  tlId: string;
  clipId: string;
  name: string;
  start: number;      // Start-Sekunde in der Timeline
  duration: number;
  stripUrl: string | null;
  waveformUrl: string | null;
  colorAlt: boolean;
  hasAudio: boolean;
};

/* Verwandelt jede Clip-URL in eine absolute URL. */
function absUrl(u: string | null | undefined): string | null {
  if (!u) return null;
  return u.startsWith("http") ? u : `${API_BASE}${u}`;
}

function fmtSec(s: number | null | undefined): string {
  if (s == null || s < 0) return "0:00";
  const m = Math.floor(s / 60);
  const r = Math.floor(s % 60);
  return `${m}:${r.toString().padStart(2, "0")}`;
}

function fmtTC(s: number | null | undefined, fps: number = 30): string {
  if (s == null || s < 0) return "00:00:00:00";
  const total = Math.max(0, s);
  const hh = Math.floor(total / 3600);
  const mm = Math.floor((total % 3600) / 60);
  const ss = Math.floor(total % 60);
  const ff = Math.floor((total - Math.floor(total)) * fps);
  return `${hh.toString().padStart(2, "0")}:${mm.toString().padStart(2, "0")}:${ss.toString().padStart(2, "0")}:${ff.toString().padStart(2, "0")}`;
}

export default function StudioPage() {
  const [clips, setClips] = useState<ClipDTO[]>([]);
  const [tlClips, setTlClips] = useState<TimelineClip[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selectedClipId, setSelectedClipId] = useState<string | null>(null);
  const [selectedTlIds, setSelectedTlIds] = useState<Set<string>>(new Set());
  const [projectName, setProjectName] = useState<string>("The best travel moments");
  const [saveStatus, setSaveStatus] = useState<"idle" | "saving" | "saved" | "error">("idle");
  const [playing, setPlaying] = useState(false);
  const [globalTime, setGlobalTime] = useState(0);
  const [zoom, setZoom] = useState(1);
  const [search, setSearch] = useState("");
  const [bottomTab, setBottomTab] = useState<BottomTab | null>(null);
  const [aiPrompt, setAiPrompt] = useState("");
  const [aiHistory, setAiHistory] = useState<{ role: "user" | "agent"; content: string }[]>([]);
  const [aiBusy, setAiBusy] = useState(false);
  const [dropHover, setDropHover] = useState(false);
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const timelineScrollRef = useRef<HTMLDivElement | null>(null);
  const rafRef = useRef<number | null>(null);
  const historyRef = useRef<TimelineClip[][]>([]);

  const PX_PER_SEC = PX_PER_SEC_BASE * zoom;

  useEffect(() => {
    fetchClips()
      .then((c) => {
        const filtered = c.filter((x) => x.status === "analysiert" || x.status === "hochgeladen");
        setClips(filtered);
        // Auto-fill timeline avec tous les clips analysés au début
        let cursor = 0;
        const initial: TimelineClip[] = filtered.map((clip, i) => {
          const seg: TimelineClip = {
            tlId: `${clip.id}-${i}`,
            clipId: clip.id,
            name: clip.dateiname.replace(/\.[^/.]+$/, ""),
            start: cursor,
            duration: clip.dauer || 0,
            stripUrl: absUrl(clip.strip_url),
            waveformUrl: absUrl(clip.waveform_url),
            colorAlt: i % 3 === 1,
            hasAudio: !!clip.waveform_url,
          };
          cursor += clip.dauer || 0;
          return seg;
        });
        setTlClips(initial);
        if (filtered.length > 0) setSelectedClipId(filtered[0].id);
      })
      .catch((e) => setError((e as Error).message))
      .finally(() => setLoading(false));
  }, []);

  // Trouve le clip actif à un instant t global
  const activeTlClip = useMemo(
    () => tlClips.find((c) => globalTime >= c.start && globalTime < c.start + c.duration) || null,
    [tlClips, globalTime]
  );

  // Sync du video avec le globalTime + clip actif
  useEffect(() => {
    if (activeTlClip && selectedClipId !== activeTlClip.clipId) {
      setSelectedClipId(activeTlClip.clipId);
    }
  }, [activeTlClip, selectedClipId]);

  // Playhead animé via RAF pendant la lecture
  useEffect(() => {
    if (!playing) {
      if (rafRef.current) cancelAnimationFrame(rafRef.current);
      return;
    }
    let last = performance.now();
    const tick = (now: number) => {
      const dt = (now - last) / 1000;
      last = now;
      setGlobalTime((t) => {
        const next = t + dt;
        const total = tlClips.reduce((s, c) => s + c.duration, 0);
        if (next >= total) {
          setPlaying(false);
          return total;
        }
        return next;
      });
      rafRef.current = requestAnimationFrame(tick);
    };
    rafRef.current = requestAnimationFrame(tick);
    return () => {
      if (rafRef.current) cancelAnimationFrame(rafRef.current);
    };
  }, [playing, tlClips]);

  // Sauvegarde d'un snapshot avant modification (undo)
  const snapshot = () => {
    historyRef.current.push(tlClips);
    if (historyRef.current.length > 20) historyRef.current.shift();
  };
  const undo = () => {
    const prev = historyRef.current.pop();
    if (prev) setTlClips(prev);
  };

  const appendClipToTimeline = (clipId: string) => {
    const src = clips.find((c) => c.id === clipId);
    if (!src) return;
    snapshot();
    setTlClips((cur) => {
      const cursor = cur.reduce((s, c) => s + c.duration, 0);
      const seg: TimelineClip = {
        tlId: `${clipId}-${Date.now()}`,
        clipId,
        name: src.dateiname.replace(/\.[^/.]+$/, ""),
        start: cursor,
        duration: src.dauer || 0,
        stripUrl: absUrl(src.strip_url),
        waveformUrl: absUrl(src.waveform_url),
        colorAlt: cur.length % 3 === 1,
        hasAudio: !!src.waveform_url,
      };
      return [...cur, seg];
    });
  };

  const removeTlClip = (tlId: string) => {
    snapshot();
    setTlClips((cur) => {
      const filtered = cur.filter((c) => c.tlId !== tlId);
      // Recalcule les starts
      let cursor = 0;
      return filtered.map((c) => {
        const next = { ...c, start: cursor };
        cursor += c.duration;
        return next;
      });
    });
  };

  const seekTo = (t: number) => {
    setGlobalTime(Math.max(0, t));
  };

  const toggleSelect = (tlId: string, additive: boolean) => {
    setSelectedTlIds((cur) => {
      const next = new Set(additive ? cur : []);
      if (additive) {
        if (next.has(tlId)) next.delete(tlId);
        else next.add(tlId);
      } else {
        next.add(tlId);
      }
      return next;
    });
  };

  const removeSelectedClips = () => {
    if (selectedTlIds.size === 0) return;
    snapshot();
    setTlClips((cur) => {
      const filtered = cur.filter((c) => !selectedTlIds.has(c.tlId));
      let cursor = 0;
      return filtered.map((c) => {
        const next = { ...c, start: cursor };
        cursor += c.duration;
        return next;
      });
    });
    setSelectedTlIds(new Set());
  };

  const reorderClip = (tlId: string, targetIdx: number) => {
    snapshot();
    setTlClips((cur) => {
      const idx = cur.findIndex((c) => c.tlId === tlId);
      if (idx === -1 || idx === targetIdx) return cur;
      const copy = [...cur];
      const [moved] = copy.splice(idx, 1);
      const adjusted = targetIdx > idx ? targetIdx - 1 : targetIdx;
      copy.splice(Math.max(0, Math.min(copy.length, adjusted)), 0, moved);
      let cursor = 0;
      return copy.map((c) => {
        const next = { ...c, start: cursor };
        cursor += c.duration;
        return next;
      });
    });
  };

  const splitAtGlobalTime = () => {
    // Trouve le clip qui contient globalTime
    const idx = tlClips.findIndex((c) => globalTime > c.start && globalTime < c.start + c.duration);
    if (idx === -1) return;
    const clip = tlClips[idx];
    const cutLocal = globalTime - clip.start;
    if (cutLocal < 0.05 || cutLocal > clip.duration - 0.05) return;
    snapshot();
    setTlClips((cur) => {
      const copy = [...cur];
      const before: TimelineClip = { ...clip, duration: cutLocal };
      const after: TimelineClip = {
        ...clip,
        tlId: `${clip.clipId}-${Date.now()}-b`,
        start: clip.start + cutLocal,
        duration: clip.duration - cutLocal,
      };
      copy.splice(idx, 1, before, after);
      return copy;
    });
  };

  const saveCurrentTimeline = async () => {
    if (tlClips.length === 0) return;
    setSaveStatus("saving");
    try {
      const { saveTimeline } = await import("@/lib/api");
      const total = tlClips.reduce((s, c) => s + c.duration, 0);
      await saveTimeline({
        name: projectName || "Unbenannte Timeline",
        stil: "manuell",
        daten: {
          segmente: tlClips.map((c) => ({
            id: c.tlId,
            clip_id: c.clipId,
            label: c.name,
            track: "v1",
            start: c.start,
            dauer: c.duration,
            quelle: "A" as const,
          })),
          gesamtdauer: total,
        },
      });
      setSaveStatus("saved");
      setTimeout(() => setSaveStatus("idle"), 2000);
    } catch (e) {
      console.warn(e);
      setSaveStatus("error");
      setTimeout(() => setSaveStatus("idle"), 3000);
    }
  };

  // Video-Element steuern
  useEffect(() => {
    const v = videoRef.current;
    if (!v) return;
    if (playing) v.play().catch(() => setPlaying(false));
    else v.pause();
  }, [playing, selectedClipId]);

  const selectedClip = useMemo(
    () => clips.find((c) => c.id === selectedClipId) || null,
    [clips, selectedClipId]
  );

  const filteredClips = useMemo(() => {
    if (!search.trim()) return clips;
    const q = search.toLowerCase();
    return clips.filter((c) => c.dateiname.toLowerCase().includes(q));
  }, [clips, search]);

  const totalDuration = useMemo(
    () => tlClips.reduce((sum, c) => sum + c.duration, 0),
    [tlClips]
  );

  const timelineWidth = Math.max(800, totalDuration * PX_PER_SEC + 80);

  const rulerTicks = useMemo(() => {
    const ticks: { x: number; label: string; major: boolean }[] = [];
    const totalSec = Math.ceil(totalDuration);
    for (let i = 0; i <= totalSec; i++) {
      ticks.push({
        x: i * PX_PER_SEC,
        label: i % 5 === 0 ? fmtSec(i) : "",
        major: i % 5 === 0,
      });
    }
    return ticks;
  }, [totalDuration]);

  const playheadX = 40 + globalTime * PX_PER_SEC;

  // Sync du video : quand le clip actif change, remet le video au bon endroit
  useEffect(() => {
    if (!activeTlClip || !videoRef.current) return;
    const local = globalTime - activeTlClip.start;
    if (Math.abs(videoRef.current.currentTime - local) > 0.3) {
      videoRef.current.currentTime = Math.max(0, local);
    }
  }, [activeTlClip, globalTime]);

  async function sendAiPrompt(promptText?: string) {
    const text = (promptText ?? aiPrompt).trim();
    if (!text || aiBusy) return;
    setAiPrompt("");
    setAiHistory((h) => [...h, { role: "user", content: text }]);
    setAiBusy(true);
    try {
      const res = await fetch(`${API_BASE}/api/agent/run_sync`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ prompt: text }),
      });
      const data = await res.json();
      const answer = data.final_answer || "(keine Antwort)";
      setAiHistory((h) => [...h, { role: "agent", content: answer }]);
    } catch (e) {
      setAiHistory((h) => [
        ...h,
        { role: "agent", content: `Fehler: ${(e as Error).message}` },
      ]);
    } finally {
      setAiBusy(false);
    }
  }

  return (
    <div className={styles.wrapper}>
      {/* ══════════════════════════ HEADER ══════════════════════════ */}
      <header className={styles.header}>
        <div className={styles.logo}>
          <div className={styles.logoIcon}>C</div>
          <span className={styles.logoText}>CinAssist Studio</span>
        </div>

        <div className={styles.menuBar}>
          {["Datei", "Bearbeiten", "Ansicht", "Clip", "Timeline", "Export", "Hilfe"].map((m) => (
            <button key={m} className={styles.menuItem} type="button">
              {m}
            </button>
          ))}
        </div>

        <div className={styles.projectTitle}>
          <Video size={12} style={{ opacity: 0.6 }} />
          <span>Timeline 1 —</span>
          <input
            className={styles.projectTitleInput}
            value={projectName}
            onChange={(e) => setProjectName(e.target.value)}
            placeholder="Projektname…"
          />
          <ChevronDown size={11} style={{ opacity: 0.55 }} />
        </div>

        <div className={styles.headerRight}>
          <Link href="/" title="Zurück zum Dashboard">
            <button className={styles.iconBtn} type="button">
              <ArrowLeft size={13} />
            </button>
          </Link>
          <button
            className={styles.iconBtn}
            title={
              saveStatus === "saving" ? "Wird gespeichert…" :
              saveStatus === "saved" ? "Gespeichert ✓" :
              saveStatus === "error" ? "Speichern fehlgeschlagen" :
              "Speichern"
            }
            type="button"
            onClick={saveCurrentTimeline}
            disabled={saveStatus === "saving" || tlClips.length === 0}
            style={
              saveStatus === "saved" ? { color: "var(--studio-green)", borderColor: "var(--studio-green)" } :
              saveStatus === "error" ? { color: "var(--studio-red)", borderColor: "var(--studio-red)" } :
              undefined
            }
          >
            {saveStatus === "saving" ? (
              <motion.div animate={{ rotate: 360 }} transition={{ duration: 1, repeat: Infinity, ease: "linear" }}>
                <Sparkles size={13} />
              </motion.div>
            ) : (
              <Save size={13} />
            )}
          </button>
          <button className={styles.iconBtn} title="Rendern" type="button">
            <Download size={13} />
          </button>
          <button className={styles.shareBtn} type="button">
            <Share2 size={12} />
            Teilen
          </button>
        </div>
      </header>

      {/* ══════════════════════════ CONTENT ══════════════════════════ */}
      <div className={styles.content}>
        {/* ─── Preview ─── */}
        <section className={styles.previewPanel}>
          <div className={styles.previewHeader}>
            <span className={styles.previewTitle}>
              {selectedClip ? selectedClip.dateiname : "Vorschau"}
            </span>
            <span className={styles.previewSub}>
              {selectedClip
                ? `${selectedClip.aufloesung ?? "—"} · ${fmtSec(selectedClip.dauer)}`
                : "Kein Clip ausgewählt"}
            </span>
          </div>

          <div className={styles.previewBody}>
            {loading && (
              <div className={styles.previewPlaceholder}>
                <motion.div
                  animate={{ rotate: 360 }}
                  transition={{ duration: 1.2, repeat: Infinity, ease: "linear" }}
                >
                  <Sparkles size={22} />
                </motion.div>
                <span>Clips werden geladen…</span>
              </div>
            )}
            {!loading && !selectedClip && !error && (
              <div className={styles.previewPlaceholder}>
                <Video size={28} style={{ opacity: 0.5 }} />
                <span>Wähle einen Clip aus dem Medien-Browser.</span>
              </div>
            )}
            {error && (
              <div className={styles.previewPlaceholder} style={{ color: "#f45a5a" }}>
                <span>Backend nicht erreichbar: {error}</span>
              </div>
            )}
            {selectedClip && (
              <>
                <video
                  ref={videoRef}
                  key={selectedClip.id}
                  src={absUrl(selectedClip.proxy_url || selectedClip.video_url) || undefined}
                  onTimeUpdate={(e) => {
                    if (!activeTlClip) return;
                    // Ne met à jour le globalTime QUE si le video roule (évite les feedback loops
                    // lorsque l'on seek programmatique via activeTlClip effect).
                    if (playing) {
                      setGlobalTime(activeTlClip.start + e.currentTarget.currentTime);
                    }
                  }}
                  onEnded={() => setPlaying(false)}
                  playsInline
                />
                <AnimatePresence>
                  {!playing && (
                    <motion.button
                      className={styles.playOverlay}
                      initial={{ scale: 0.85, opacity: 0 }}
                      animate={{ scale: 1, opacity: 1 }}
                      exit={{ scale: 0.85, opacity: 0 }}
                      transition={{ duration: 0.18 }}
                      onClick={() => setPlaying(true)}
                      style={{ pointerEvents: "auto", cursor: "pointer" }}
                    >
                      <Play size={26} fill="white" />
                    </motion.button>
                  )}
                </AnimatePresence>
              </>
            )}
          </div>

          <div className={styles.previewFooter}>
            <span className={styles.timecode}>
              {fmtTC(globalTime)}
            </span>
            <div className={styles.transportControls}>
              <button
                className={styles.transportBtn}
                onClick={() => { seekTo(0); if (videoRef.current) videoRef.current.currentTime = 0; }}
                type="button"
              >
                <SkipBack size={14} />
              </button>
              <motion.button
                className={styles.transportBtnMain}
                onClick={() => setPlaying((p) => !p)}
                whileTap={{ scale: 0.94 }}
                type="button"
              >
                {playing ? <Pause size={15} /> : <Play size={15} fill="currentColor" />}
              </motion.button>
              <button
                className={styles.transportBtn}
                onClick={() => seekTo(totalDuration)}
                type="button"
              >
                <SkipForward size={14} />
              </button>
            </div>
            <button className={styles.iconBtn} type="button" title="Lautstärke">
              <Volume2 size={14} />
            </button>
            <button className={styles.iconBtn} type="button" title="Vollbild">
              <Maximize2 size={13} />
            </button>
          </div>
        </section>

        {/* ─── Media Panel ─── */}
        <aside className={styles.mediaPanel}>
          <div className={styles.mediaHeader}>
            {(["Medien", "Text", "Effekte"] as const).map((tab, i) => (
              <button
                key={tab}
                className={`${styles.mediaTab} ${i === 0 ? styles.mediaTabActive : ""}`}
                type="button"
              >
                {tab}
              </button>
            ))}
            <div style={{ marginLeft: "auto" }}>
              <button className={styles.iconBtn} type="button" title="Clip hochladen">
                <Plus size={13} />
              </button>
            </div>
          </div>

          <div className={styles.mediaSearch}>
            <div className={styles.searchWrapper}>
              <Search size={12} className={styles.searchIcon} />
              <input
                type="text"
                placeholder="Medien durchsuchen…"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                className={styles.searchInput}
              />
            </div>
          </div>

          <div className={styles.mediaGrid}>
            {filteredClips.length === 0 && (
              <div className={styles.mediaEmpty}>
                {loading ? "Wird geladen…" : "Keine Medien gefunden."}
              </div>
            )}
            {filteredClips.map((c) => {
              const strip = absUrl(c.strip_url);
              const active = c.id === selectedClipId;
              const onTimelineCount = tlClips.filter((t) => t.clipId === c.id).length;
              return (
                <motion.div
                  key={c.id}
                  className={`${styles.mediaCard} ${onTimelineCount > 0 ? styles.mediaCardOnTimeline : ""}`}
                  onClick={() => setSelectedClipId(c.id)}
                  onDoubleClick={() => appendClipToTimeline(c.id)}
                  style={active ? { borderColor: "var(--studio-accent-border)" } : undefined}
                  whileHover={{ y: -2, borderColor: "var(--studio-border-strong)" }}
                  whileTap={{ scale: 0.98 }}
                  transition={{ duration: 0.15 }}
                  draggable
                  onDragStart={(e) => {
                    const de = e as unknown as React.DragEvent<HTMLDivElement>;
                    de.dataTransfer?.setData("text/plain", c.id);
                    if (de.dataTransfer) de.dataTransfer.effectAllowed = "copy";
                  }}
                  title={`${c.dateiname} — Doppelklick oder Ziehen auf Timeline`}
                >
                  <div
                    className={styles.mediaThumb}
                    style={strip ? { backgroundImage: `url(${strip})` } : undefined}
                  >
                    {onTimelineCount > 0 && (
                      <div className={styles.mediaCardBadge}>×{onTimelineCount}</div>
                    )}
                    <div className={styles.mediaDuration}>{fmtSec(c.dauer)}</div>
                  </div>
                  <div className={styles.mediaCardFooter}>
                    <span
                      className={`${styles.mediaStatus} ${
                        c.status === "analysiert" ? styles.mediaStatusOk : styles.mediaStatusPending
                      }`}
                    />
                    <span className={styles.mediaName}>
                      {c.dateiname.replace(/\.[^/.]+$/, "")}
                    </span>
                  </div>
                </motion.div>
              );
            })}
          </div>
        </aside>
      </div>

      {/* ══════════════════════════ TIMELINE ══════════════════════════ */}
      <section className={styles.timelinePanel}>
        <div className={styles.timelineHeader}>
          <span className={styles.timelineTimecode}>{fmtTC(globalTime)}</span>
          <span style={{ fontSize: 11, color: "var(--studio-text-3)" }}>
            / {fmtTC(totalDuration)}
          </span>
          <div className={styles.timelineTools}>
            <button
              className={styles.timelineToolBtn}
              type="button"
              title="Rückgängig"
              onClick={undo}
              disabled={historyRef.current.length === 0}
            >
              ↶ Undo
            </button>
            <button
              className={styles.timelineToolBtn}
              type="button"
              title="Auszoomen"
              onClick={() => setZoom((z) => Math.max(MIN_ZOOM, z * 0.7))}
            >
              −
            </button>
            <span
              style={{
                fontFamily: "var(--studio-mono)",
                fontSize: 10,
                color: "var(--studio-text-3)",
                minWidth: 34,
                textAlign: "center",
              }}
            >
              {(zoom * 100).toFixed(0)}%
            </span>
            <button
              className={styles.timelineToolBtn}
              type="button"
              title="Einzoomen"
              onClick={() => setZoom((z) => Math.min(MAX_ZOOM, z / 0.7))}
            >
              +
            </button>
            <button
              className={styles.timelineToolBtn}
              type="button"
              title="Am Playhead schneiden (Blade)"
              onClick={splitAtGlobalTime}
            >
              <Scissors size={11} /> Split
            </button>
            <button
              className={styles.timelineToolBtn}
              type="button"
              title="Auswahl entfernen"
              onClick={removeSelectedClips}
              disabled={selectedTlIds.size === 0}
              style={
                selectedTlIds.size > 0
                  ? { color: "var(--studio-red)", borderColor: "var(--studio-red)" }
                  : undefined
              }
            >
              × {selectedTlIds.size > 0 ? `${selectedTlIds.size} entfernen` : "Entfernen"}
            </button>
            <button
              className={`${styles.timelineToolBtn} ${styles.timelineToolBtnActive}`}
              type="button"
              onClick={() => { setBottomTab("ai"); setAiPrompt("Generiere einen kinematischen Rohschnitt"); }}
              title="KI-Cut mit dem Agenten"
            >
              <Sparkles size={11} /> KI-Cut
            </button>
          </div>
        </div>

        <div
          ref={timelineScrollRef}
          className={styles.timelineBody}
          onDragOver={(e) => { e.preventDefault(); setDropHover(true); }}
          onDragLeave={() => setDropHover(false)}
          onDrop={(e) => {
            e.preventDefault();
            setDropHover(false);
            // Priorité au drag intra-timeline (reorder handled par ClipBlock)
            if (e.dataTransfer.getData("application/x-cinassist-tl")) return;
            const clipId = e.dataTransfer.getData("text/plain");
            if (clipId) appendClipToTimeline(clipId);
          }}
          onClick={(e) => {
            // Seek en cliquant dans le vide de la timeline (pas sur un clip)
            const target = e.target as HTMLElement;
            if (target.closest(`.${styles.timelineClip}`)) return;
            const container = timelineScrollRef.current;
            if (!container) return;
            const rect = container.getBoundingClientRect();
            const relX = e.clientX - rect.left + container.scrollLeft - 40;
            if (relX < 0) return;
            const t = relX / PX_PER_SEC;
            seekTo(Math.min(t, totalDuration));
          }}
          style={dropHover ? { background: "var(--studio-accent-soft)" } : undefined}
        >
          <div style={{ minWidth: timelineWidth, position: "relative" }}>
            {/* Ruler */}
            <div className={styles.timelineRuler} style={{ width: timelineWidth }}>
              {rulerTicks.map((t, i) => (
                <div key={i} style={{ position: "absolute", left: 40 + t.x, bottom: 0 }}>
                  <div className={styles.timelineRulerTick} style={{ height: t.major ? 10 : 4 }} />
                  {t.label && (
                    <div className={styles.timelineRulerLabel} style={{ transform: "translateX(-50%)" }}>
                      {t.label}
                    </div>
                  )}
                </div>
              ))}
            </div>

            {/* Playhead */}
            <div
              className={`${styles.timelinePlayhead} ${playing ? styles.timelinePlayheadPlaying : ""}`}
              style={{ left: playheadX }}
            >
              <div className={styles.timelinePlayheadHead} />
            </div>

            {/* Track V1 (Video) */}
            <TimelineTrack label="V1" icon={<Video size={10} />}>
              {tlClips.map((c, idx) => (
                <ClipBlock
                  key={`v-${c.tlId}`}
                  clip={c}
                  index={idx}
                  variant={c.colorAlt ? "videoAlt" : "video"}
                  pxPerSec={PX_PER_SEC}
                  selected={selectedTlIds.has(c.tlId)}
                  reorderable
                  onClick={(additive) => {
                    toggleSelect(c.tlId, additive);
                    setSelectedClipId(c.clipId);
                    if (!additive) seekTo(c.start);
                  }}
                  onRemove={() => removeTlClip(c.tlId)}
                  onReorderTo={(target) => reorderClip(c.tlId, target)}
                />
              ))}
            </TimelineTrack>

            {/* Track A1 (Audio) */}
            <TimelineTrack label="A1" icon={<Music2 size={10} />}>
              {tlClips
                .filter((c) => c.hasAudio)
                .map((c, idx) => (
                  <ClipBlock
                    key={`a-${c.tlId}`}
                    clip={c}
                    index={idx}
                    variant="audio"
                    pxPerSec={PX_PER_SEC}
                    selected={selectedTlIds.has(c.tlId)}
                    onClick={(additive) => {
                      toggleSelect(c.tlId, additive);
                      setSelectedClipId(c.clipId);
                      if (!additive) seekTo(c.start);
                    }}
                  />
                ))}
            </TimelineTrack>

            {/* Track Text (placeholder) */}
            <TimelineTrack label="T1" icon={<Type size={10} />}>
              <div
                style={{
                  position: "absolute",
                  left: 12,
                  top: "50%",
                  transform: "translateY(-50%)",
                  fontSize: 10,
                  color: "var(--studio-text-4)",
                }}
              >
                Zieh Untertitel hierher
              </div>
            </TimelineTrack>
          </div>
        </div>
      </section>

      {/* ══════════════════════════ BOTTOM BAR ══════════════════════════ */}
      <div className={styles.bottomBar}>
        <button
          className={`${styles.tabBtn} ${bottomTab === "ai" ? styles.tabBtnActive : ""}`}
          onClick={() => setBottomTab(bottomTab === "ai" ? null : "ai")}
          type="button"
        >
          <Bot size={12} className={styles.tabBtnAccent} /> KI-Agent
        </button>
        <button
          className={`${styles.tabBtn} ${bottomTab === "search" ? styles.tabBtnActive : ""}`}
          onClick={() => setBottomTab(bottomTab === "search" ? null : "search")}
          type="button"
        >
          <Search size={12} /> CLIP-Suche
        </button>
        <button
          className={`${styles.tabBtn} ${bottomTab === "history" ? styles.tabBtnActive : ""}`}
          onClick={() => setBottomTab(bottomTab === "history" ? null : "history")}
          type="button"
        >
          <History size={12} /> Verlauf
        </button>

        <div className={styles.bottomStatus}>
          <span className={styles.bottomDot} />
          <span>Ollama · lokal · 17 Tools</span>
          <span style={{ opacity: 0.5 }}>·</span>
          <span>{clips.length} Clips · {fmtSec(totalDuration)}</span>
        </div>
      </div>

      {/* Bottom Panel Drawer */}
      <AnimatePresence>
        {bottomTab && (
          <motion.div
            initial={{ y: 20, opacity: 0 }}
            animate={{ y: 0, opacity: 1 }}
            exit={{ y: 20, opacity: 0 }}
            transition={{ duration: 0.18 }}
            style={{
              position: "fixed",
              bottom: 60,
              right: 8,
              width: 400,
              maxHeight: 320,
              background: "var(--studio-panel)",
              border: "1px solid var(--studio-border)",
              borderRadius: "var(--studio-radius-panel)",
              boxShadow: "var(--studio-shadow-strong)",
              display: "flex",
              flexDirection: "column",
              zIndex: 100,
            }}
          >
            {bottomTab === "ai" && (
              <AiTab
                history={aiHistory}
                prompt={aiPrompt}
                busy={aiBusy}
                onPromptChange={setAiPrompt}
                onSubmit={() => sendAiPrompt()}
              />
            )}
            {bottomTab === "search" && <SearchTab clips={clips} />}
            {bottomTab === "history" && <HistoryTab />}
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}

/* ═══════════════════════════════════════════════════════════
   Sub-components
   ═══════════════════════════════════════════════════════════ */

function TimelineTrack({
  label,
  icon,
  children,
}: {
  label: string;
  icon: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <div className={styles.timelineTrack}>
      <div className={styles.timelineTrackLabel}>
        <span style={{ display: "flex", alignItems: "center", gap: 3 }}>
          {icon}
          {label}
        </span>
      </div>
      <div className={styles.timelineTrackClips}>{children}</div>
    </div>
  );
}

function ClipBlock({
  clip,
  index,
  variant,
  pxPerSec,
  selected,
  reorderable,
  onClick,
  onRemove,
  onReorderTo,
}: {
  clip: TimelineClip;
  index: number;
  variant: "video" | "videoAlt" | "audio";
  pxPerSec: number;
  selected?: boolean;
  reorderable?: boolean;
  onClick?: (additive: boolean) => void;
  onRemove?: () => void;
  onReorderTo?: (targetIdx: number) => void;
}) {
  const variantClass = {
    video: styles.timelineClipVideo,
    videoAlt: styles.timelineClipVideoAlt,
    audio: styles.timelineClipAudio,
  }[variant];

  const [dropSide, setDropSide] = useState<"left" | "right" | null>(null);
  const isAudio = variant === "audio";
  const bgUrl = isAudio ? clip.waveformUrl : clip.stripUrl;

  return (
    <motion.div
      layout
      className={`${styles.timelineClip} ${variantClass}`}
      style={{
        left: clip.start * pxPerSec,
        width: Math.max(6, clip.duration * pxPerSec - 2),
        boxShadow: selected ? "0 0 0 2px white, 0 6px 16px rgba(0,0,0,0.35)" : undefined,
      }}
      initial={{ opacity: 0, y: 4 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0, y: -4 }}
      transition={{ duration: 0.18 }}
      title={`${clip.name} · ${fmtSec(clip.duration)}`}
      onClick={(e) => {
        e.stopPropagation();
        onClick?.(e.metaKey || e.ctrlKey || e.shiftKey);
      }}
      onContextMenu={(e) => {
        if (!onRemove) return;
        e.preventDefault();
        e.stopPropagation();
        onRemove();
      }}
      draggable={reorderable ? true : undefined}
      onDragStart={reorderable ? (e) => {
        const de = e as unknown as React.DragEvent<HTMLDivElement>;
        de.dataTransfer?.setData("application/x-cinassist-tl", clip.tlId);
        if (de.dataTransfer) de.dataTransfer.effectAllowed = "move";
      } : undefined}
      onDragOver={reorderable ? (e) => {
        const de = e as unknown as React.DragEvent<HTMLDivElement>;
        if (!de.dataTransfer?.types?.includes("application/x-cinassist-tl")) return;
        de.preventDefault();
        const rect = (de.currentTarget as HTMLDivElement).getBoundingClientRect();
        const isLeftHalf = (de.clientX - rect.left) < rect.width / 2;
        setDropSide(isLeftHalf ? "left" : "right");
      } : undefined}
      onDragLeave={reorderable ? () => setDropSide(null) : undefined}
      onDrop={reorderable ? (e) => {
        const de = e as unknown as React.DragEvent<HTMLDivElement>;
        const draggedId = de.dataTransfer?.getData("application/x-cinassist-tl");
        setDropSide(null);
        if (!draggedId || draggedId === clip.tlId) return;
        de.preventDefault();
        de.stopPropagation();
        const rect = (de.currentTarget as HTMLDivElement).getBoundingClientRect();
        const isLeftHalf = (de.clientX - rect.left) < rect.width / 2;
        onReorderTo?.(index + (isLeftHalf ? 0 : 1));
      } : undefined}
    >
      {bgUrl && (
        <div
          className={isAudio ? styles.timelineClipWaveform : styles.timelineClipStrip}
          style={{ backgroundImage: `url(${bgUrl})` }}
        />
      )}
      <span className={styles.timelineClipName}>{clip.name}</span>
      {dropSide && (
        <div
          style={{
            position: "absolute",
            top: 0,
            bottom: 0,
            [dropSide]: -3,
            width: 3,
            background: "var(--studio-accent)",
            boxShadow: "0 0 8px var(--studio-accent)",
            pointerEvents: "none",
          }}
        />
      )}
    </motion.div>
  );
}

function AiTab({
  history,
  prompt,
  busy,
  onPromptChange,
  onSubmit,
}: {
  history: { role: "user" | "agent"; content: string }[];
  prompt: string;
  busy: boolean;
  onPromptChange: (v: string) => void;
  onSubmit: () => void;
}) {
  const bottomRef = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [history, busy]);

  const suggestions = [
    "Zeig mir alle Szenen mit Gesichtern",
    "Wie viele Sprecher habe ich?",
    "Erzeuge einen 30s-Teaser",
  ];

  return (
    <>
      <div
        style={{
          padding: "10px 14px",
          borderBottom: "1px solid var(--studio-border)",
          display: "flex",
          alignItems: "center",
          gap: 8,
        }}
      >
        <Bot size={13} style={{ color: "var(--studio-accent)" }} />
        <span style={{ fontSize: 12, fontWeight: 600 }}>KI-Agent</span>
        <span style={{ marginLeft: "auto", fontSize: 10, color: "var(--studio-text-3)" }}>
          qwen2.5:14b · 17 Tools
        </span>
      </div>
      <div style={{ flex: 1, minHeight: 0, overflowY: "auto", padding: "10px 14px" }}>
        {history.length === 0 && (
          <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
            <div style={{ fontSize: 11, color: "var(--studio-text-3)", marginBottom: 4 }}>
              Vorschläge :
            </div>
            {suggestions.map((s) => (
              <button
                key={s}
                onClick={() => onPromptChange(s)}
                type="button"
                style={{
                  padding: "8px 10px",
                  borderRadius: "var(--studio-radius-sm)",
                  background: "var(--studio-panel-2)",
                  border: "1px solid var(--studio-border)",
                  color: "var(--studio-text-2)",
                  fontSize: 11,
                  cursor: "pointer",
                  textAlign: "left",
                  fontFamily: "inherit",
                }}
              >
                {s}
              </button>
            ))}
          </div>
        )}
        {history.map((m, i) => (
          <div key={i} style={{ margin: "6px 0", display: "flex", justifyContent: m.role === "user" ? "flex-end" : "flex-start" }}>
            <div
              style={{
                maxWidth: "84%",
                padding: "7px 10px",
                borderRadius: 10,
                fontSize: 11.5,
                lineHeight: 1.5,
                background: m.role === "user" ? "var(--studio-accent)" : "var(--studio-panel-3)",
                color: m.role === "user" ? "white" : "var(--studio-text)",
              }}
            >
              {m.content}
            </div>
          </div>
        ))}
        {busy && (
          <div style={{ margin: "6px 0", display: "flex", alignItems: "center", gap: 6, color: "var(--studio-text-3)" }}>
            <motion.div
              animate={{ rotate: 360 }}
              transition={{ duration: 1.2, repeat: Infinity, ease: "linear" }}
            >
              <Sparkles size={11} />
            </motion.div>
            <span style={{ fontSize: 11 }}>Agent überlegt…</span>
          </div>
        )}
        <div ref={bottomRef} />
      </div>
      <form
        onSubmit={(e) => {
          e.preventDefault();
          onSubmit();
        }}
        style={{
          display: "flex",
          gap: 6,
          padding: 10,
          borderTop: "1px solid var(--studio-border)",
        }}
      >
        <input
          type="text"
          value={prompt}
          onChange={(e) => onPromptChange(e.target.value)}
          placeholder={busy ? "Agent überlegt…" : "Frag den Agenten…"}
          disabled={busy}
          style={{
            flex: 1,
            height: 30,
            padding: "0 10px",
            background: "var(--studio-panel-2)",
            border: "1px solid var(--studio-border)",
            borderRadius: 8,
            color: "var(--studio-text)",
            fontSize: 12,
            outline: "none",
            fontFamily: "inherit",
          }}
        />
        <button
          type="submit"
          disabled={busy || !prompt.trim()}
          style={{
            width: 30,
            height: 30,
            borderRadius: 8,
            border: "none",
            background: "var(--studio-accent)",
            color: "white",
            cursor: prompt.trim() && !busy ? "pointer" : "not-allowed",
            opacity: prompt.trim() && !busy ? 1 : 0.5,
            display: "grid",
            placeItems: "center",
          }}
        >
          <Send size={12} />
        </button>
      </form>
    </>
  );
}

function SearchTab({ clips }: { clips: ClipDTO[] }) {
  const [q, setQ] = useState("");
  const [results, setResults] = useState<
    { scene_id: string; clip_name: string; description: string; similarity: number }[]
  >([]);
  const [busy, setBusy] = useState(false);

  async function doSearch(e: React.FormEvent) {
    e.preventDefault();
    if (!q.trim() || busy) return;
    setBusy(true);
    try {
      const r = await fetch(`${API_BASE}/api/scenes/search`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ query: q, limit: 10 }),
      });
      const data = await r.json();
      setResults(data.results || []);
    } catch (e) {
      console.warn(e);
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <div
        style={{
          padding: "10px 14px",
          borderBottom: "1px solid var(--studio-border)",
          display: "flex",
          alignItems: "center",
          gap: 8,
        }}
      >
        <Search size={13} />
        <span style={{ fontSize: 12, fontWeight: 600 }}>CLIP-Textsuche</span>
        <span style={{ marginLeft: "auto", fontSize: 10, color: "var(--studio-text-3)" }}>
          {clips.length} Clips indiziert
        </span>
      </div>
      <form
        onSubmit={doSearch}
        style={{ display: "flex", gap: 6, padding: 10, borderBottom: "1px solid var(--studio-border)" }}
      >
        <input
          type="text"
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder="z. B. 'wide drone shot', 'coffee being poured'…"
          style={{
            flex: 1,
            height: 30,
            padding: "0 10px",
            background: "var(--studio-panel-2)",
            border: "1px solid var(--studio-border)",
            borderRadius: 8,
            color: "var(--studio-text)",
            fontSize: 12,
            outline: "none",
            fontFamily: "inherit",
          }}
        />
        <button
          type="submit"
          disabled={busy || !q.trim()}
          style={{
            padding: "0 12px",
            height: 30,
            borderRadius: 8,
            border: "none",
            background: "var(--studio-accent)",
            color: "white",
            fontSize: 11,
            fontWeight: 600,
            cursor: q.trim() && !busy ? "pointer" : "not-allowed",
            opacity: q.trim() && !busy ? 1 : 0.5,
          }}
        >
          Suchen
        </button>
      </form>
      <div style={{ flex: 1, overflowY: "auto", padding: "10px 14px" }}>
        {results.length === 0 && !busy && (
          <div style={{ fontSize: 11, color: "var(--studio-text-3)", textAlign: "center", padding: 20 }}>
            Gib eine Beschreibung ein, um Szenen semantisch zu durchsuchen.
          </div>
        )}
        {results.map((r) => (
          <div
            key={r.scene_id}
            style={{
              padding: "8px 10px",
              margin: "4px 0",
              borderRadius: 8,
              background: "var(--studio-panel-2)",
              border: "1px solid var(--studio-border)",
            }}
          >
            <div style={{ display: "flex", justifyContent: "space-between", gap: 6, marginBottom: 3 }}>
              <span style={{ fontSize: 11, fontWeight: 600, color: "var(--studio-text)" }}>
                {r.clip_name}
              </span>
              <span
                style={{
                  fontSize: 10,
                  color: "var(--studio-accent)",
                  fontFamily: "var(--studio-mono)",
                }}
              >
                {(r.similarity * 100).toFixed(0)}%
              </span>
            </div>
            <div style={{ fontSize: 11, color: "var(--studio-text-2)", lineHeight: 1.4 }}>
              {r.description || "(keine Beschreibung)"}
            </div>
          </div>
        ))}
      </div>
    </>
  );
}

function HistoryTab() {
  const [timelines, setTimelines] = useState<{ id: string; name: string; erstellt_am: string | null }[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    fetch(`${API_BASE}/api/timelines`)
      .then((r) => r.json())
      .then((data) => setTimelines(data || []))
      .catch(() => {})
      .finally(() => setLoading(false));
  }, []);

  return (
    <>
      <div
        style={{
          padding: "10px 14px",
          borderBottom: "1px solid var(--studio-border)",
          display: "flex",
          alignItems: "center",
          gap: 8,
        }}
      >
        <History size={13} />
        <span style={{ fontSize: 12, fontWeight: 600 }}>Timeline-Verlauf</span>
        <span style={{ marginLeft: "auto", fontSize: 10, color: "var(--studio-text-3)" }}>
          {timelines.length} gespeichert
        </span>
      </div>
      <div style={{ flex: 1, overflowY: "auto", padding: "10px 14px" }}>
        {loading && (
          <div style={{ fontSize: 11, color: "var(--studio-text-3)", textAlign: "center", padding: 12 }}>
            Wird geladen…
          </div>
        )}
        {!loading && timelines.length === 0 && (
          <div style={{ fontSize: 11, color: "var(--studio-text-3)", textAlign: "center", padding: 20 }}>
            Noch keine gespeicherten Timelines.
          </div>
        )}
        {timelines.map((t) => (
          <div
            key={t.id}
            style={{
              padding: "8px 10px",
              margin: "4px 0",
              borderRadius: 8,
              background: "var(--studio-panel-2)",
              border: "1px solid var(--studio-border)",
              cursor: "pointer",
            }}
          >
            <div style={{ fontSize: 11.5, fontWeight: 500, color: "var(--studio-text)" }}>{t.name}</div>
            <div
              style={{
                fontSize: 10,
                color: "var(--studio-text-3)",
                fontFamily: "var(--studio-mono)",
                marginTop: 2,
              }}
            >
              {t.erstellt_am ? new Date(t.erstellt_am).toLocaleString("de-DE") : "—"}
            </div>
          </div>
        ))}
      </div>
    </>
  );
}
