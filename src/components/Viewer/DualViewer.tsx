"use client";

/**
 * CinAssist — DualViewer
 *
 * Zwei Panels nebeneinander (Quelle | Ausgabe):
 * - Links: Quelle — zeigt den ausgewählten Clip aus dem Media Pool
 * - Rechts: Ausgabe — zeigt das Ergebnis der KI-Timeline
 * - Timecodes (Format: 00:00:00:00)
 * - Draggable Trennlinie
 * - Tabs: Quelle | Ausgabe | Vergleich
 * - Waveform-Dekoration unten
 * - Play/Pause → simuliert Fortschritt
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { motion, AnimatePresence } from "framer-motion";
import {
  Play, Pause, SkipBack, SkipForward, Volume2, VolumeX,
  Maximize2, Minimize2, Eye, Layers,
} from "lucide-react";

// ─── Typen ──────────────────────────────────────────────

type ViewerTab = "quelle" | "ausgabe" | "vergleich";

interface DualViewerProps {
  quelleUrl?: string;    // Video-URL Quelle
  ausgabeUrl?: string;   // Video-URL Ausgabe
  quelleName?: string;   // Clip-Name Quelle
  ausgabeName?: string;  // Clip-Name Ausgabe
}

// ─── Zeitformat ─────────────────────────────────────────

function formatTimecode(seconds: number): string {
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = Math.floor(seconds % 60);
  const f = Math.floor((seconds % 1) * 24);
  return `${String(h).padStart(2, "0")}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}:${String(f).padStart(2, "0")}`;
}

// ─── Waveform-SVG ───────────────────────────────────────

function WaveformDecoration({ breite, farbe }: { breite: number; farbe: string }) {
  const punkte = Math.max(20, Math.floor(breite / 3));
  const path = Array.from({ length: punkte }, (_, i) => {
    const x = (i / punkte) * breite;
    const y = 12 + Math.sin(i * 0.4) * 6 + Math.random() * 4;
    return `${i === 0 ? "M" : "L"} ${x.toFixed(1)} ${y.toFixed(1)}`;
  }).join(" ");

  return (
    <svg
      width={breite}
      height={24}
      viewBox={`0 0 ${breite} 24`}
      style={{ display: "block", opacity: 0.4 }}
    >
      <path d={path} fill="none" stroke={farbe} strokeWidth={1.5} />
    </svg>
  );
}

// ─── Viewer-Panel ───────────────────────────────────────

interface ViewerPanelProps {
  label: string;
  name?: string;
  videoUrl?: string;
  zeit: number;
  dauer: number;
  isPlaying: boolean;
  onPlay: () => void;
  onSeek: (t: number) => void;
  akzent: string;
  badge?: string;
}

function ViewerPanel({
  label,
  name,
  videoUrl,
  zeit,
  dauer,
  isPlaying,
  onPlay,
  onSeek,
  akzent,
  badge,
}: ViewerPanelProps) {
  const videoRef = useRef<HTMLVideoElement>(null);
  const progressWidth = dauer > 0 ? (zeit / dauer) * 100 : 0;

  return (
    <div
      style={{
        flex: 1,
        display: "flex",
        flexDirection: "column",
        minWidth: 0,
        background: "#0a0a0c",
        borderRadius: 8,
        overflow: "hidden",
        border: "1px solid rgba(255,255,255,0.05)",
      }}
    >
      {/* Header */}
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          padding: "5px 10px",
          borderBottom: "1px solid rgba(255,255,255,0.05)",
          background: "rgba(255,255,255,0.02)",
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
          <div
            style={{
              width: 6,
              height: 6,
              borderRadius: "50%",
              background: akzent,
            }}
          />
          <span
            style={{
              fontSize: 10,
              fontWeight: 600,
              letterSpacing: "0.06em",
              textTransform: "uppercase",
              color: "rgba(255,255,255,0.3)",
            }}
          >
            {label}
          </span>
          {badge && (
            <span
              style={{
                fontSize: 9,
                fontWeight: 700,
                padding: "1px 6px",
                borderRadius: 4,
                background: `${akzent}20`,
                color: akzent,
                border: `1px solid ${akzent}40`,
              }}
            >
              {badge}
            </span>
          )}
        </div>
        <span
          style={{
            fontFamily: "var(--mono)",
            fontSize: 10,
            color: "rgba(255,255,255,0.25)",
          }}
        >
          {formatTimecode(zeit)}
        </span>
      </div>

      {/* Video-Bereich / Platzhalter */}
      <div
        style={{
          flex: 1,
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          position: "relative",
          minHeight: 0,
          background: "linear-gradient(180deg, #07080a 0%, #0c0d10 100%)",
        }}
      >
        {videoUrl ? (
          <video
            ref={videoRef}
            src={videoUrl}
            style={{
              maxWidth: "100%",
              maxHeight: "100%",
              objectFit: "contain",
            }}
            muted
          />
        ) : (
          <div
            style={{
              display: "flex",
              flexDirection: "column",
              alignItems: "center",
              gap: 8,
            }}
          >
            {/* Filmstreifen-Platzhalter */}
            <div
              style={{
                width: 80,
                height: 56,
                borderRadius: 8,
                background: "rgba(255,255,255,0.03)",
                border: "1px solid rgba(255,255,255,0.06)",
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
              }}
            >
              <Eye size={20} color="rgba(255,255,255,0.1)" />
            </div>
            <span style={{ fontSize: 11, color: "rgba(255,255,255,0.15)", fontWeight: 500 }}>
              {name || "Kein Clip ausgewählt"}
            </span>
          </div>
        )}

        {/* Timecode-Overlay */}
        {dauer > 0 && (
          <div
            style={{
              position: "absolute",
              bottom: 28,
              right: 8,
              fontFamily: "var(--mono)",
              fontSize: 11,
              fontWeight: 500,
              color: "rgba(255,255,255,0.3)",
              background: "rgba(0,0,0,0.5)",
              padding: "2px 6px",
              borderRadius: 4,
            }}
          >
            {formatTimecode(zeit)} / {formatTimecode(dauer)}
          </div>
        )}
      </div>

      {/* Waveform + Fortschritt */}
      <div style={{ position: "relative", height: 24, background: "rgba(255,255,255,0.015)" }}>
        <WaveformDecoration breite={500} farbe={akzent} />
        {/* Fortschrittsbalken */}
        <div
          style={{
            position: "absolute",
            bottom: 0,
            left: 0,
            height: 2,
            width: `${progressWidth}%`,
            background: akzent,
            transition: "width 0.1s linear",
          }}
        />
      </div>
    </div>
  );
}

// ═══════════════════════════════════════════════════════════
// HAUPT-KOMPONENTE: DualViewer
// ═══════════════════════════════════════════════════════════

export default function DualViewer({
  quelleUrl,
  ausgabeUrl,
  quelleName = "Quelle",
  ausgabeName = "Ausgabe",
}: DualViewerProps) {
  // ─── State ────────────────────────────────────────────
  const [activeTab, setActiveTab] = useState<ViewerTab>("quelle");
  const [isPlaying, setIsPlaying] = useState(false);
  const [currentTime, setCurrentTime] = useState(0);
  const [duration] = useState(55.0); // Simuliert
  const [volume, setVolume] = useState(80);
  const [isMuted, setIsMuted] = useState(false);
  const [isFullscreen, setIsFullscreen] = useState(false);

  // Draggable Trennlinie
  const [splitPos, setSplitPos] = useState(50); // Prozent
  const containerRef = useRef<HTMLDivElement>(null);
  const animRef = useRef<number | null>(null);
  const lastTimeRef = useRef(0);

  // ─── Wiedergabe-Simulation ────────────────────────────

  useEffect(() => {
    if (isPlaying) {
      lastTimeRef.current = performance.now();
      const animate = (now: number) => {
        const dt = (now - lastTimeRef.current) / 1000;
        lastTimeRef.current = now;

        setCurrentTime((prev) => {
          const next = prev + dt;
          if (next >= duration) {
            setIsPlaying(false);
            return 0;
          }
          return next;
        });
        animRef.current = requestAnimationFrame(animate);
      };
      animRef.current = requestAnimationFrame(animate);
    }
    return () => {
      if (animRef.current) cancelAnimationFrame(animRef.current);
    };
  }, [isPlaying, duration]);

  // ─── Trennlinie ziehen ────────────────────────────────

  const handleDividerDrag = useCallback(
    (e: React.MouseEvent) => {
      e.preventDefault();
      const container = containerRef.current;
      if (!container) return;

      const handleMove = (me: MouseEvent) => {
        const rect = container.getBoundingClientRect();
        const x = me.clientX - rect.left;
        const pct = Math.max(20, Math.min(80, (x / rect.width) * 100));
        setSplitPos(pct);
      };

      const handleUp = () => {
        window.removeEventListener("mousemove", handleMove);
        window.removeEventListener("mouseup", handleUp);
        document.body.style.cursor = "";
      };

      document.body.style.cursor = "col-resize";
      window.addEventListener("mousemove", handleMove);
      window.addEventListener("mouseup", handleUp);
    },
    []
  );

  // ─── Keyboard Shortcuts ───────────────────────────────

  useEffect(() => {
    const handleKey = (e: KeyboardEvent) => {
      if (e.code === "Space") {
        e.preventDefault();
        setIsPlaying((p) => !p);
      } else if (e.code === "ArrowLeft") {
        setCurrentTime((t) => Math.max(0, t - 1));
      } else if (e.code === "ArrowRight") {
        setCurrentTime((t) => Math.min(duration, t + 1));
      } else if (e.code === "Home") {
        setCurrentTime(0);
      } else if (e.code === "End") {
        setCurrentTime(duration);
      }
    };

    window.addEventListener("keydown", handleKey);
    return () => window.removeEventListener("keydown", handleKey);
  }, [duration]);

  // ─── Tabs ─────────────────────────────────────────────

  const TABS: { id: ViewerTab; label: string }[] = [
    { id: "quelle", label: "Quelle" },
    { id: "ausgabe", label: "Ausgabe" },
    { id: "vergleich", label: "Vergleich" },
  ];

  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        height: "100%",
        background: "#0a0a0c",
        overflow: "hidden",
        userSelect: "none",
      }}
    >
      {/* ─── Tab-Leiste + Transport ────────────────────── */}
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          padding: "0 10px",
          height: 32,
          borderBottom: "1px solid rgba(255,255,255,0.06)",
          background: "rgba(255,255,255,0.015)",
          flexShrink: 0,
        }}
      >
        {/* Tabs */}
        <div style={{ display: "flex", gap: 0 }}>
          {TABS.map((tab) => (
            <button
              key={tab.id}
              onClick={() => setActiveTab(tab.id)}
              style={{
                padding: "6px 12px",
                fontSize: 11,
                fontWeight: activeTab === tab.id ? 600 : 500,
                color:
                  activeTab === tab.id
                    ? "rgba(255,255,255,0.8)"
                    : "rgba(255,255,255,0.25)",
                background:
                  activeTab === tab.id
                    ? "rgba(255,255,255,0.06)"
                    : "transparent",
                border: "none",
                borderRadius: "6px 6px 0 0",
                cursor: "pointer",
                transition: "all 0.12s",
              }}
            >
              {tab.label}
            </button>
          ))}
        </div>

        {/* Transport */}
        <div style={{ display: "flex", alignItems: "center", gap: 2 }}>
          <VBtn icon={SkipBack} onClick={() => setCurrentTime(0)} />
          <VBtn
            icon={isPlaying ? Pause : Play}
            onClick={() => setIsPlaying(!isPlaying)}
            accent
          />
          <VBtn icon={SkipForward} onClick={() => setCurrentTime(duration)} />

          <div style={{ width: 1, height: 14, background: "rgba(255,255,255,0.06)", margin: "0 6px" }} />

          <VBtn
            icon={isMuted ? VolumeX : Volume2}
            onClick={() => setIsMuted(!isMuted)}
          />
          <VBtn
            icon={isFullscreen ? Minimize2 : Maximize2}
            onClick={() => setIsFullscreen(!isFullscreen)}
          />
        </div>
      </div>

      {/* ─── Viewer-Bereich ────────────────────────────── */}
      <div
        ref={containerRef}
        style={{
          flex: 1,
          display: "flex",
          minHeight: 0,
          position: "relative",
        }}
      >
        {activeTab === "vergleich" ? (
          /* Vergleich: Side by Side */
          <>
            <div style={{ width: `${splitPos}%`, minWidth: 0 }}>
              <ViewerPanel
                label="Quelle"
                name={quelleName}
                videoUrl={quelleUrl}
                zeit={currentTime}
                dauer={duration}
                isPlaying={isPlaying}
                onPlay={() => setIsPlaying(!isPlaying)}
                onSeek={setCurrentTime}
                akzent="#2563eb"
              />
            </div>

            {/* Trennlinie */}
            <div
              onMouseDown={handleDividerDrag}
              style={{
                width: 6,
                cursor: "col-resize",
                background: "rgba(255,255,255,0.03)",
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                flexShrink: 0,
                zIndex: 10,
              }}
            >
              <div
                style={{
                  width: 2,
                  height: 40,
                  borderRadius: 2,
                  background: "rgba(255,255,255,0.15)",
                }}
              />
            </div>

            <div style={{ width: `${100 - splitPos}%`, minWidth: 0 }}>
              <ViewerPanel
                label="Ausgabe"
                name={ausgabeName}
                videoUrl={ausgabeUrl}
                zeit={currentTime}
                dauer={duration}
                isPlaying={isPlaying}
                onPlay={() => setIsPlaying(!isPlaying)}
                onSeek={setCurrentTime}
                akzent="#3b82f6"
                badge="KI"
              />
            </div>
          </>
        ) : (
          /* Einzelansicht */
          <ViewerPanel
            label={activeTab === "quelle" ? "Quelle" : "Ausgabe"}
            name={activeTab === "quelle" ? quelleName : ausgabeName}
            videoUrl={activeTab === "quelle" ? quelleUrl : ausgabeUrl}
            zeit={currentTime}
            dauer={duration}
            isPlaying={isPlaying}
            onPlay={() => setIsPlaying(!isPlaying)}
            onSeek={setCurrentTime}
            akzent={activeTab === "quelle" ? "#2563eb" : "#3b82f6"}
            badge={activeTab === "ausgabe" ? "KI" : undefined}
          />
        )}
      </div>

      {/* ─── Untere Statusleiste ───────────────────────── */}
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          padding: "3px 10px",
          borderTop: "1px solid rgba(255,255,255,0.06)",
          background: "rgba(255,255,255,0.015)",
          flexShrink: 0,
        }}
      >
        <span
          style={{
            fontSize: 10,
            color: "rgba(255,255,255,0.2)",
            fontFamily: "var(--mono)",
          }}
        >
          {formatTimecode(currentTime)} / {formatTimecode(duration)}
        </span>

        {/* Lautstärke-Slider */}
        <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
          <span style={{ fontSize: 9, color: "rgba(255,255,255,0.15)" }}>
            {isMuted ? "STUMM" : `${volume}%`}
          </span>
          <input
            type="range"
            min={0}
            max={100}
            value={isMuted ? 0 : volume}
            onChange={(e) => {
              setVolume(parseInt(e.target.value));
              if (isMuted) setIsMuted(false);
            }}
            style={{
              width: 60,
              height: 3,
              accentColor: "#2563eb",
              cursor: "pointer",
            }}
          />
        </div>

        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <span style={{ fontSize: 9, color: "rgba(255,255,255,0.15)" }}>
            1920×1080
          </span>
          <span style={{ fontSize: 9, color: "rgba(255,255,255,0.15)" }}>
            24fps
          </span>
        </div>
      </div>
    </div>
  );
}

// ─── Viewer-Button ──────────────────────────────────────

function VBtn({
  icon: Icon,
  onClick,
  accent,
}: {
  icon: React.ElementType;
  onClick?: () => void;
  accent?: boolean;
}) {
  return (
    <button
      onClick={onClick}
      style={{
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        width: 26,
        height: 26,
        borderRadius: 5,
        background: accent ? "rgba(255,255,255,0.08)" : "transparent",
        border: "none",
        color: accent ? "white" : "rgba(255,255,255,0.3)",
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
          : "rgba(255,255,255,0.3)";
      }}
    >
      <Icon size={13} />
    </button>
  );
}
