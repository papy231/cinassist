"use client";

/**
 * CinAssist — TimelineEditor
 *
 * Professionelle Timeline-Anzeige im Stil von DaVinci Resolve:
 * - Ruler (Zeitachse oben, skalierbar)
 * - 4 Spuren: V2 (B-Roll), V1 (Hauptschnitt), A1 (Dialog), Musik
 * - Clips als farbige Blöcke mit Filmstreifen-Effekt
 * - Playhead (weiße Linie), mit Maus verschiebbar
 * - Drag & Drop, Resize-Handles links/rechts
 * - Rechtsklick → Kontextmenü
 * - KI-Banner oben
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { motion, AnimatePresence } from "framer-motion";
import {
  Scissors, ZoomIn, ZoomOut, Play, Pause, SkipBack, SkipForward,
  Sparkles, X, Check, Trash2, Type, Film, Music2, Mic,
  ChevronRight,
} from "lucide-react";
import {
  useTimelineStore,
  type TimelineClip,
  getClipFarbe,
} from "@/stores/timelineStore";

// ─── Konstanten ─────────────────────────────────────────

const SPUR_HOEHE = 50;
const RULER_HOEHE = 26;
const LABEL_BREITE = 52;
const MIN_PX_PER_SEC = 5;
const MAX_PX_PER_SEC = 80;

type SpurInfo = {
  id: "V2" | "V1" | "A1" | "Musik";
  label: string;
  icon: React.ElementType;
  farbe: string;
};

const SPUREN: SpurInfo[] = [
  { id: "V2",    label: "V2",    icon: Film,   farbe: "rgba(37,99,235,0.3)" },
  { id: "V1",    label: "V1",    icon: Film,   farbe: "rgba(37,99,235,0.5)" },
  { id: "A1",    label: "A1",    icon: Mic,    farbe: "rgba(34,197,94,0.4)"  },
  { id: "Musik", label: "♪",     icon: Music2, farbe: "rgba(168,85,247,0.4)" },
];

// ─── Zeitformat ─────────────────────────────────────────

function formatTimecode(seconds: number): string {
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = Math.floor(seconds % 60);
  const f = Math.floor((seconds % 1) * 24);
  return `${String(h).padStart(2, "0")}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}:${String(f).padStart(2, "0")}`;
}

// ─── Kontextmenü ────────────────────────────────────────

interface ContextMenuProps {
  x: number;
  y: number;
  clipId: string;
  onClose: () => void;
}

function ContextMenu({ x, y, clipId, onClose }: ContextMenuProps) {
  const { removeClip, splitClipAt, playheadPx, updateClip, clips } = useTimelineStore();
  const clip = clips.find((c) => c.id === clipId);

  const items = [
    {
      label: "Umbenennen",
      icon: Type,
      action: () => {
        const neuerName = prompt("Neuer Name:", clip?.label || "");
        if (neuerName) updateClip(clipId, { label: neuerName });
      },
    },
    {
      label: "Hier schneiden",
      icon: Scissors,
      action: () => splitClipAt(clipId, playheadPx),
    },
    {
      label: "KI-Verlängerung +3s",
      icon: Sparkles,
      action: () => {
        // TODO: POST /api/jobs/extend aufrufen
        if (clip) {
          const added = 3 * useTimelineStore.getState().pxPerSecond;
          updateClip(clipId, { widthPx: clip.widthPx + added });
        }
      },
    },
    { label: "divider" },
    {
      label: "Löschen",
      icon: Trash2,
      action: () => removeClip(clipId),
      danger: true,
    },
  ];

  useEffect(() => {
    const close = () => onClose();
    window.addEventListener("click", close);
    return () => window.removeEventListener("click", close);
  }, [onClose]);

  return (
    <motion.div
      initial={{ opacity: 0, scale: 0.95 }}
      animate={{ opacity: 1, scale: 1 }}
      exit={{ opacity: 0, scale: 0.95 }}
      transition={{ duration: 0.1 }}
      style={{
        position: "fixed",
        left: x,
        top: y,
        zIndex: 1000,
        background: "#1a1c1e",
        border: "1px solid rgba(255,255,255,0.1)",
        borderRadius: 10,
        padding: "4px 0",
        minWidth: 200,
        boxShadow: "0 12px 40px rgba(0,0,0,0.6)",
      }}
    >
      {items.map((item, i) =>
        item.label === "divider" ? (
          <div
            key={i}
            style={{
              height: 1,
              background: "rgba(255,255,255,0.06)",
              margin: "4px 8px",
            }}
          />
        ) : (
          <button
            key={item.label}
            onClick={(e) => {
              e.stopPropagation();
              item.action?.();
              onClose();
            }}
            style={{
              display: "flex",
              alignItems: "center",
              gap: 8,
              width: "100%",
              padding: "7px 12px",
              background: "none",
              border: "none",
              cursor: "pointer",
              color: item.danger ? "#ef4444" : "#dde2e8",
              fontSize: 12,
              fontWeight: 500,
              textAlign: "left",
            }}
            onMouseEnter={(e) =>
              (e.currentTarget.style.background = "rgba(255,255,255,0.06)")
            }
            onMouseLeave={(e) =>
              (e.currentTarget.style.background = "none")
            }
          >
            {item.icon && <item.icon size={13} style={{ opacity: 0.6, flexShrink: 0 }} />}
            {item.label}
          </button>
        )
      )}
    </motion.div>
  );
}

// ─── Clip-Block ─────────────────────────────────────────

interface ClipBlockProps {
  clip: TimelineClip;
  spurHoehe: number;
  onContextMenu: (e: React.MouseEvent, clipId: string) => void;
}

function ClipBlock({ clip, spurHoehe, onContextMenu }: ClipBlockProps) {
  const { selectedClipId, selectClip, moveClip, resizeClip } = useTimelineStore();
  const isSelected = selectedClipId === clip.id;
  const blockRef = useRef<HTMLDivElement>(null);
  const [dragging, setDragging] = useState(false);

  // Drag-Logik
  const handleMouseDown = useCallback(
    (e: React.MouseEvent) => {
      if (e.button !== 0) return;
      e.stopPropagation();
      selectClip(clip.id);

      const startX = e.clientX;
      const startPx = clip.startPx;

      const handleMove = (me: MouseEvent) => {
        setDragging(true);
        const dx = me.clientX - startX;
        moveClip(clip.id, startPx + dx);
      };

      const handleUp = () => {
        setDragging(false);
        window.removeEventListener("mousemove", handleMove);
        window.removeEventListener("mouseup", handleUp);
      };

      window.addEventListener("mousemove", handleMove);
      window.addEventListener("mouseup", handleUp);
    },
    [clip.id, clip.startPx, moveClip, selectClip]
  );

  // Resize Handle
  const handleResize = useCallback(
    (e: React.MouseEvent, fromLeft: boolean) => {
      e.stopPropagation();
      const startX = e.clientX;
      const startWidth = clip.widthPx;
      const startStart = clip.startPx;

      const handleMove = (me: MouseEvent) => {
        const dx = me.clientX - startX;
        if (fromLeft) {
          const newWidth = startWidth - dx;
          if (newWidth > 10) {
            resizeClip(clip.id, newWidth, true);
          }
        } else {
          resizeClip(clip.id, startWidth + dx, false);
        }
      };

      const handleUp = () => {
        window.removeEventListener("mousemove", handleMove);
        window.removeEventListener("mouseup", handleUp);
      };

      window.addEventListener("mousemove", handleMove);
      window.addEventListener("mouseup", handleUp);
    },
    [clip.id, clip.widthPx, clip.startPx, resizeClip]
  );

  const isVideo = clip.track === "V1" || clip.track === "V2";

  return (
    <div
      ref={blockRef}
      onMouseDown={handleMouseDown}
      onContextMenu={(e) => {
        e.preventDefault();
        onContextMenu(e, clip.id);
      }}
      style={{
        position: "absolute",
        left: clip.startPx,
        top: 2,
        width: clip.widthPx,
        height: spurHoehe - 4,
        borderRadius: 6,
        background: clip.farbe || getClipFarbe(clip.track, clip.source),
        border: `1px solid ${
          isSelected
            ? "rgba(255,255,255,0.4)"
            : "rgba(255,255,255,0.08)"
        }`,
        cursor: dragging ? "grabbing" : "grab",
        overflow: "hidden",
        userSelect: "none",
        transition: dragging ? "none" : "border-color 0.15s",
      }}
    >
      {/* Filmstreifen-Effekt (nur Video-Spuren) */}
      {isVideo && (
        <div
          style={{
            position: "absolute",
            inset: 0,
            background: `repeating-linear-gradient(
              90deg,
              rgba(255,255,255,0.03) 0px,
              rgba(255,255,255,0.03) 1px,
              transparent 1px,
              transparent 16px
            )`,
            pointerEvents: "none",
          }}
        />
      )}

      {/* Waveform-Effekt (Audio-Spuren) */}
      {!isVideo && (
        <div
          style={{
            position: "absolute",
            inset: 0,
            background: `repeating-linear-gradient(
              90deg,
              transparent 0px,
              rgba(255,255,255,0.06) 2px,
              transparent 4px,
              transparent 6px
            )`,
            pointerEvents: "none",
          }}
        />
      )}

      {/* KI-Badge */}
      {clip.aiGenerated && (
        <div
          style={{
            position: "absolute",
            top: 3,
            right: 3,
            width: 14,
            height: 14,
            borderRadius: 4,
            background: "rgba(168,85,247,0.5)",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
          }}
        >
          <Sparkles size={8} color="white" />
        </div>
      )}

      {/* Label */}
      <div
        style={{
          padding: "3px 6px",
          fontSize: 10,
          fontWeight: 600,
          color: "rgba(255,255,255,0.7)",
          whiteSpace: "nowrap",
          overflow: "hidden",
          textOverflow: "ellipsis",
          letterSpacing: "-0.01em",
          pointerEvents: "none",
        }}
      >
        {clip.label}
      </div>

      {/* Quelle-Badge */}
      <div
        style={{
          position: "absolute",
          bottom: 3,
          left: 5,
          fontSize: 8,
          fontWeight: 700,
          color: "rgba(255,255,255,0.3)",
          fontFamily: "var(--mono)",
        }}
      >
        {clip.source}
      </div>

      {/* Resize-Handles */}
      <div
        onMouseDown={(e) => handleResize(e, true)}
        style={{
          position: "absolute",
          left: 0,
          top: 0,
          width: 5,
          height: "100%",
          cursor: "ew-resize",
          background: isSelected ? "rgba(255,255,255,0.15)" : "transparent",
        }}
      />
      <div
        onMouseDown={(e) => handleResize(e, false)}
        style={{
          position: "absolute",
          right: 0,
          top: 0,
          width: 5,
          height: "100%",
          cursor: "ew-resize",
          background: isSelected ? "rgba(255,255,255,0.15)" : "transparent",
        }}
      />
    </div>
  );
}

// ═══════════════════════════════════════════════════════════
// HAUPT-KOMPONENTE: TimelineEditor
// ═══════════════════════════════════════════════════════════

export default function TimelineEditor() {
  const {
    clips,
    playheadPx,
    isPlaying,
    currentTime,
    pxPerSecond,
    totalDuration,
    scrollLeft,
    aiBanner,
    setPlayheadPx,
    setIsPlaying,
    setCurrentTime,
    setPxPerSecond,
    setScrollLeft,
    setAiBanner,
    selectClip,
  } = useTimelineStore();

  const containerRef = useRef<HTMLDivElement>(null);
  const animRef = useRef<number | null>(null);
  const lastTimeRef = useRef<number>(0);

  // Kontextmenü
  const [contextMenu, setContextMenu] = useState<{
    x: number;
    y: number;
    clipId: string;
  } | null>(null);

  // ─── Wiedergabe-Simulation ────────────────────────────

  useEffect(() => {
    if (isPlaying) {
      lastTimeRef.current = performance.now();

      const animate = (now: number) => {
        const dt = (now - lastTimeRef.current) / 1000;
        lastTimeRef.current = now;

        const newTime = currentTime + dt;
        if (newTime >= totalDuration) {
          setCurrentTime(0);
          setIsPlaying(false);
          return;
        }
        setCurrentTime(newTime);
        animRef.current = requestAnimationFrame(animate);
      };

      animRef.current = requestAnimationFrame(animate);
    }

    return () => {
      if (animRef.current) cancelAnimationFrame(animRef.current);
    };
  }, [isPlaying, currentTime, totalDuration, setCurrentTime, setIsPlaying]);

  // ─── Ruler-Klick → Playhead setzen ───────────────────

  const handleRulerClick = useCallback(
    (e: React.MouseEvent) => {
      const rect = e.currentTarget.getBoundingClientRect();
      const x = e.clientX - rect.left + scrollLeft;
      setPlayheadPx(x);
    },
    [scrollLeft, setPlayheadPx]
  );

  // ─── Hintergrund-Klick → Deselection ─────────────────

  const handleBgClick = useCallback(() => {
    selectClip(null);
    setContextMenu(null);
  }, [selectClip]);

  // ─── Kontextmenü ─────────────────────────────────────

  const handleContextMenu = useCallback(
    (e: React.MouseEvent, clipId: string) => {
      e.preventDefault();
      setContextMenu({ x: e.clientX, y: e.clientY, clipId });
    },
    []
  );

  // ─── Zoom ────────────────────────────────────────────

  const zoomIn = () => setPxPerSecond(Math.min(MAX_PX_PER_SEC, pxPerSecond * 1.5));
  const zoomOut = () => setPxPerSecond(Math.max(MIN_PX_PER_SEC, pxPerSecond / 1.5));

  // ─── Scroll ──────────────────────────────────────────

  const handleScroll = useCallback(
    (e: React.UIEvent<HTMLDivElement>) => {
      setScrollLeft(e.currentTarget.scrollLeft);
    },
    [setScrollLeft]
  );

  // ─── Ruler-Markierungen berechnen ────────────────────

  const timelineBreite = totalDuration * pxPerSecond;

  const rulerMarks: { px: number; label: string; major: boolean }[] = [];
  {
    // Passendes Intervall basierend auf Zoom
    let interval = 1; // Sekunden
    if (pxPerSecond < 10) interval = 10;
    else if (pxPerSecond < 20) interval = 5;
    else if (pxPerSecond < 40) interval = 2;
    else interval = 1;

    for (let t = 0; t <= totalDuration; t += interval) {
      const px = t * pxPerSecond;
      const m = Math.floor(t / 60);
      const s = Math.floor(t % 60);
      const label =
        interval >= 10
          ? `${m}:${String(s).padStart(2, "0")}`
          : `${m}:${String(s).padStart(2, "0")}`;
      const major = t % (interval * 5) === 0 || t === 0;
      rulerMarks.push({ px, label, major });
    }
  }

  const zoomProzent = Math.round((pxPerSecond / 20) * 100);

  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        height: "100%",
        background: "var(--bg0, #080909)",
        borderTop: "1px solid rgba(255,255,255,0.06)",
        overflow: "hidden",
        userSelect: "none",
      }}
    >
      {/* ─── KI-Banner ─────────────────────────────────── */}
      <AnimatePresence>
        {aiBanner?.visible && (
          <motion.div
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: "auto", opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            style={{
              background: "linear-gradient(90deg, rgba(168,85,247,0.12), rgba(59,130,246,0.08))",
              borderBottom: "1px solid rgba(168,85,247,0.2)",
              padding: "8px 14px",
              display: "flex",
              alignItems: "center",
              justifyContent: "space-between",
              gap: 12,
            }}
          >
            <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
              <Sparkles size={14} color="#a855f7" />
              <span style={{ fontSize: 12, fontWeight: 500, color: "#dde2e8" }}>
                {aiBanner.message ||
                  `✦ KI hat eine ${aiBanner.stil}-Timeline erstellt — ${aiBanner.segmente} Segmente aus A + B`}
              </span>
            </div>
            <div style={{ display: "flex", gap: 6 }}>
              <button
                onClick={() => setAiBanner(null)}
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: 4,
                  padding: "4px 10px",
                  borderRadius: 6,
                  background: "rgba(239,68,68,0.15)",
                  border: "1px solid rgba(239,68,68,0.3)",
                  color: "#ef4444",
                  fontSize: 11,
                  fontWeight: 600,
                  cursor: "pointer",
                }}
              >
                <X size={11} /> Ablehnen
              </button>
              <button
                onClick={() => setAiBanner(null)}
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: 4,
                  padding: "4px 10px",
                  borderRadius: 6,
                  background: "rgba(34,197,94,0.15)",
                  border: "1px solid rgba(34,197,94,0.3)",
                  color: "#22c55e",
                  fontSize: 11,
                  fontWeight: 600,
                  cursor: "pointer",
                }}
              >
                <Check size={11} /> Anwenden
              </button>
            </div>
          </motion.div>
        )}
      </AnimatePresence>

      {/* ─── Toolbar ───────────────────────────────────── */}
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          padding: "4px 10px",
          background: "rgba(255,255,255,0.02)",
          borderBottom: "1px solid rgba(255,255,255,0.06)",
          gap: 8,
          flexShrink: 0,
        }}
      >
        {/* Transport */}
        <div style={{ display: "flex", alignItems: "center", gap: 2 }}>
          <ToolbarBtn icon={SkipBack} onClick={() => setCurrentTime(0)} tooltip="Anfang" />
          <ToolbarBtn
            icon={isPlaying ? Pause : Play}
            onClick={() => setIsPlaying(!isPlaying)}
            tooltip={isPlaying ? "Pause" : "Wiedergabe"}
            accent
          />
          <ToolbarBtn icon={SkipForward} onClick={() => setCurrentTime(totalDuration)} tooltip="Ende" />
        </div>

        {/* Timecode */}
        <div
          style={{
            fontFamily: "var(--mono)",
            fontSize: 12,
            fontWeight: 500,
            color: "rgba(255,255,255,0.5)",
            letterSpacing: "0.05em",
            background: "rgba(255,255,255,0.04)",
            padding: "3px 10px",
            borderRadius: 6,
            border: "1px solid rgba(255,255,255,0.06)",
          }}
        >
          {formatTimecode(currentTime)}
        </div>

        {/* Werkzeuge */}
        <div style={{ display: "flex", alignItems: "center", gap: 2 }}>
          <ToolbarBtn icon={Scissors} tooltip="Schneiden" />
        </div>

        {/* Zoom */}
        <div style={{ display: "flex", alignItems: "center", gap: 4 }}>
          <ToolbarBtn icon={ZoomOut} onClick={zoomOut} tooltip="Rauszoomen" />
          <span
            style={{
              fontSize: 10,
              fontWeight: 600,
              color: "rgba(255,255,255,0.3)",
              fontFamily: "var(--mono)",
              minWidth: 36,
              textAlign: "center",
            }}
          >
            {zoomProzent}%
          </span>
          <ToolbarBtn icon={ZoomIn} onClick={zoomIn} tooltip="Reinzoomen" />
        </div>
      </div>

      {/* ─── Timeline-Bereich ──────────────────────────── */}
      <div
        ref={containerRef}
        style={{
          flex: 1,
          display: "flex",
          overflow: "hidden",
          minHeight: 0,
        }}
      >
        {/* Spur-Labels (fest links) */}
        <div
          style={{
            width: LABEL_BREITE,
            flexShrink: 0,
            display: "flex",
            flexDirection: "column",
            borderRight: "1px solid rgba(255,255,255,0.06)",
          }}
        >
          {/* Ruler-Platzhalter */}
          <div style={{ height: RULER_HOEHE, borderBottom: "1px solid rgba(255,255,255,0.06)" }} />

          {SPUREN.map((spur) => (
            <div
              key={spur.id}
              style={{
                height: SPUR_HOEHE,
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                borderBottom: "1px solid rgba(255,255,255,0.04)",
              }}
            >
              <span
                style={{
                  fontSize: 10,
                  fontWeight: 700,
                  color: "rgba(255,255,255,0.25)",
                  fontFamily: "var(--mono)",
                  letterSpacing: "0.03em",
                }}
              >
                {spur.label}
              </span>
            </div>
          ))}
        </div>

        {/* Scrollbarer Bereich */}
        <div
          onScroll={handleScroll}
          onClick={handleBgClick}
          style={{
            flex: 1,
            overflowX: "auto",
            overflowY: "hidden",
            position: "relative",
          }}
        >
          <div style={{ width: timelineBreite, minWidth: "100%", position: "relative" }}>
            {/* Ruler */}
            <div
              onClick={handleRulerClick}
              style={{
                height: RULER_HOEHE,
                position: "relative",
                borderBottom: "1px solid rgba(255,255,255,0.08)",
                background: "rgba(255,255,255,0.015)",
                cursor: "pointer",
              }}
            >
              {rulerMarks.map((mark, i) => (
                <div
                  key={i}
                  style={{
                    position: "absolute",
                    left: mark.px,
                    top: 0,
                    height: "100%",
                    display: "flex",
                    flexDirection: "column",
                    alignItems: "center",
                  }}
                >
                  <div
                    style={{
                      width: 1,
                      height: mark.major ? 10 : 6,
                      background: mark.major
                        ? "rgba(255,255,255,0.15)"
                        : "rgba(255,255,255,0.06)",
                      marginTop: "auto",
                    }}
                  />
                  {mark.major && (
                    <span
                      style={{
                        position: "absolute",
                        top: 2,
                        left: 4,
                        fontSize: 9,
                        fontFamily: "var(--mono)",
                        color: "rgba(255,255,255,0.2)",
                        whiteSpace: "nowrap",
                      }}
                    >
                      {mark.label}
                    </span>
                  )}
                </div>
              ))}
            </div>

            {/* Spuren */}
            {SPUREN.map((spur) => {
              const spurClips = clips.filter((c) => c.track === spur.id);
              return (
                <div
                  key={spur.id}
                  style={{
                    height: SPUR_HOEHE,
                    position: "relative",
                    borderBottom: "1px solid rgba(255,255,255,0.04)",
                    background:
                      spur.id === "V1"
                        ? "rgba(255,255,255,0.015)"
                        : "transparent",
                  }}
                >
                  {/* Raster-Linien */}
                  {rulerMarks
                    .filter((m) => m.major)
                    .map((mark, i) => (
                      <div
                        key={i}
                        style={{
                          position: "absolute",
                          left: mark.px,
                          top: 0,
                          width: 1,
                          height: "100%",
                          background: "rgba(255,255,255,0.025)",
                          pointerEvents: "none",
                        }}
                      />
                    ))}

                  {/* Clips */}
                  {spurClips.map((clip) => (
                    <ClipBlock
                      key={clip.id}
                      clip={clip}
                      spurHoehe={SPUR_HOEHE}
                      onContextMenu={handleContextMenu}
                    />
                  ))}
                </div>
              );
            })}

            {/* Playhead */}
            <div
              style={{
                position: "absolute",
                left: playheadPx,
                top: 0,
                width: 1,
                height: RULER_HOEHE + SPUREN.length * SPUR_HOEHE,
                background: "white",
                pointerEvents: "none",
                zIndex: 50,
                boxShadow: "0 0 6px rgba(255,255,255,0.3)",
              }}
            >
              {/* Playhead-Kopf */}
              <div
                style={{
                  position: "absolute",
                  top: 0,
                  left: -5,
                  width: 11,
                  height: 8,
                  background: "white",
                  clipPath: "polygon(0 0, 100% 0, 50% 100%)",
                }}
              />
            </div>
          </div>
        </div>
      </div>

      {/* Kontextmenü */}
      <AnimatePresence>
        {contextMenu && (
          <ContextMenu
            x={contextMenu.x}
            y={contextMenu.y}
            clipId={contextMenu.clipId}
            onClose={() => setContextMenu(null)}
          />
        )}
      </AnimatePresence>
    </div>
  );
}

// ─── Toolbar-Button ─────────────────────────────────────

function ToolbarBtn({
  icon: Icon,
  onClick,
  tooltip,
  accent,
}: {
  icon: React.ElementType;
  onClick?: () => void;
  tooltip?: string;
  accent?: boolean;
}) {
  return (
    <button
      onClick={onClick}
      title={tooltip}
      style={{
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        width: 28,
        height: 28,
        borderRadius: 6,
        background: accent ? "rgba(255,255,255,0.08)" : "transparent",
        border: "none",
        color: accent ? "white" : "rgba(255,255,255,0.35)",
        cursor: "pointer",
        transition: "all 0.12s",
      }}
      onMouseEnter={(e) => {
        e.currentTarget.style.background = "rgba(255,255,255,0.1)";
        e.currentTarget.style.color = "rgba(255,255,255,0.7)";
      }}
      onMouseLeave={(e) => {
        e.currentTarget.style.background = accent
          ? "rgba(255,255,255,0.08)"
          : "transparent";
        e.currentTarget.style.color = accent
          ? "white"
          : "rgba(255,255,255,0.35)";
      }}
    >
      <Icon size={14} />
    </button>
  );
}
