"use client";

import { useEffect, useState } from "react";
import { motion } from "framer-motion";
import { Search, Bell, Plus, SlidersHorizontal, TrendingUp, Loader2, AlertCircle } from "lucide-react";
import { Dock } from "@/components/Dock";
import { AppSidebar } from "@/components/AppSidebar";
import { ProjectCard, FeaturedCard, type Project } from "@/components/ProjectCard";
import { fetchClips, type ClipDTO } from "@/lib/api";

/* Gradients rotatifs déterministes selon l'index — apparence stable au reload */
const GRADIENTS = [
  "linear-gradient(145deg, #060d1e 0%, #0b1f4a 35%, #152f70 60%, #060e22 100%)",
  "linear-gradient(145deg, #1a0800 0%, #5c1f00 40%, #c8580a 70%, #3d1200 100%)",
  "linear-gradient(145deg, #071510 0%, #0c2e1c 50%, #165c30 75%, #061008 100%)",
  "linear-gradient(145deg, #120a1a 0%, #3a0f48 45%, #7c1fa8 75%, #160820 100%)",
  "linear-gradient(145deg, #090909 0%, #181818 50%, #242424 80%, #0c0c0c 100%)",
  "linear-gradient(145deg, #00101a 0%, #003366 45%, #0066cc 70%, #001a33 100%)",
];

function statusToStatus(s: string): Project["status"] {
  if (s === "analysiert" || s === "fertig") return "rendu";
  if (s === "laeuft" || s === "wartend") return "en_cours";
  return "brouillon";
}

function fmtDuration(seconds: number | null): string {
  if (!seconds || seconds <= 0) return "—";
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60);
  return `${m}:${s.toString().padStart(2, "0")}`;
}

function fmtRelative(iso: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  const diffMs = Date.now() - d.getTime();
  const min = Math.floor(diffMs / 60_000);
  const hr = Math.floor(min / 60);
  const day = Math.floor(hr / 24);
  if (min < 1) return "gerade eben";
  if (min < 60) return `vor ${min} Min.`;
  if (hr < 24) return `vor ${hr}h`;
  if (day < 7) return `vor ${day} Tag${day > 1 ? "en" : ""}`;
  return d.toLocaleDateString("de-DE", { day: "2-digit", month: "short" });
}

function progressFromStatus(s: string): number {
  if (s === "analysiert" || s === "fertig") return 100;
  if (s === "laeuft") return 55;
  if (s === "wartend") return 15;
  return 5;
}

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8001";

function withApi(url: string | null | undefined): string | null {
  if (!url) return null;
  return url.startsWith("http") ? url : `${API_BASE}${url}`;
}

function clipToProject(clip: ClipDTO, index: number, isFeatured = false): Project {
  const nameNoExt = clip.dateiname.replace(/\.[^/.]+$/, "");
  const desc = [
    clip.aufloesung ?? "unbekannte Auflösung",
    clip.dauer ? `${fmtDuration(clip.dauer)} lang` : null,
    clip.dateigroesse_mb ? `${clip.dateigroesse_mb} MB` : null,
    clip.status === "analysiert" ? "Analyse abgeschlossen." : `Status: ${clip.status}`,
  ].filter(Boolean).join(" · ");

  const tags: string[] = [];
  if (clip.quelle) tags.push(`Quelle ${clip.quelle}`);
  if (clip.aufloesung) tags.push(clip.aufloesung);
  if (clip.bildrate) tags.push(`${Math.round(clip.bildrate)}fps`);

  return {
    id: clip.id,
    title: nameNoExt,
    description: desc,
    duration: fmtDuration(clip.dauer),
    clips: 1,
    progress: progressFromStatus(clip.status),
    updatedAt: fmtRelative(clip.erstellt_am),
    tags,
    status: statusToStatus(clip.status),
    featured: isFeatured,
    gradient: GRADIENTS[index % GRADIENTS.length],
    stripUrl: withApi(clip.strip_url),
    waveformUrl: withApi(clip.waveform_url),
  };
}

export default function Dashboard() {
  const [clips, setClips] = useState<ClipDTO[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetchClips()
      .then((data) => {
        setClips(data);
        setError(null);
      })
      .catch((e) => setError((e as Error).message))
      .finally(() => setLoading(false));
  }, []);

  const totalClips = clips.length;
  const analyzed = clips.filter((c) => c.status === "analysiert" || c.status === "fertig").length;
  const running = clips.filter((c) => c.status === "laeuft" || c.status === "wartend").length;
  const totalDuration = clips.reduce((sum, c) => sum + (c.dauer ?? 0), 0);
  const totalGb = clips.reduce((sum, c) => sum + (c.dateigroesse_mb ?? 0), 0) / 1024;

  const stats = [
    { label: "Clips gesamt",   value: String(totalClips), change: `${analyzed} analysiert`, color: "var(--gold)" },
    { label: "In Bearbeitung", value: String(running),    change: running > 0 ? "Aktive Jobs" : "Nichts aktiv", color: "var(--fx)"   },
    { label: "Gesamtdauer",    value: fmtDuration(totalDuration), change: `${Math.round(totalDuration)}s`, color: "var(--blue)"  },
    { label: "Speicher",       value: `${totalGb.toFixed(2)} GB`, change: `${Math.round(totalGb * 1024)} MB`, color: "var(--grade)" },
  ];

  const projects: Project[] = clips.map((c, i) => clipToProject(c, i, i === 0));
  const featured = projects.find((p) => p.featured);
  const rest = projects.filter((p) => !p.featured);

  return (
    <div style={{ display: "flex", height: "100dvh", background: "var(--bg)", overflow: "hidden" }}>
      <AppSidebar />

      <div
        style={{
          marginLeft: "var(--sidebar-w)",
          marginBottom: "var(--bar-h)",
          flex: 1,
          display: "flex",
          flexDirection: "column",
          overflow: "hidden",
        }}
      >
        {/* Top bar */}
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: 10,
            padding: "0 20px",
            height: 46,
            borderBottom: "1px solid var(--b0)",
            background: "var(--bg-up)",
            flexShrink: 0,
          }}
        >
          <span style={{ fontSize: 13, color: "var(--t2)", fontWeight: 400 }}>Projekte</span>
          <span style={{ color: "var(--t4)", fontSize: 12 }}>/</span>
          <span style={{ fontSize: 13, color: "var(--t0)", fontWeight: 500, letterSpacing: "-0.01em" }}>
            Alle Clips
          </span>

          <div style={{ flex: 1 }} />

          <div
            style={{
              display: "flex", alignItems: "center", gap: 8,
              background: "var(--bg-raised)", border: "1px solid var(--b1)",
              borderRadius: 9, padding: "0 10px", width: 200, height: 28,
            }}
          >
            <Search size={11} style={{ color: "var(--t3)", flexShrink: 0 }} />
            <input
              type="text"
              placeholder="Clip suchen…"
              style={{
                background: "none", border: "none", outline: "none",
                color: "var(--t0)", fontSize: 12, flex: 1, caretColor: "var(--gold)",
              }}
            />
            <kbd style={{ fontSize: 9, color: "var(--t3)", fontFamily: "var(--mono)", letterSpacing: "0.02em" }}>⌘K</kbd>
          </div>

          <motion.button
            style={{
              display: "flex", alignItems: "center", gap: 5,
              height: 28, padding: "0 10px", borderRadius: 9,
              background: "var(--bg-raised)", border: "1px solid var(--b1)",
              color: "var(--t1)", fontSize: 12, cursor: "pointer",
            }}
            whileHover={{ borderColor: "var(--b2)", color: "var(--t0)" }}
            whileTap={{ scale: 0.96 }}
          >
            <SlidersHorizontal size={11} /> Filtern
          </motion.button>

          <motion.button
            style={{
              width: 28, height: 28, borderRadius: 9, display: "flex",
              alignItems: "center", justifyContent: "center",
              background: "var(--bg-raised)", border: "1px solid var(--b1)",
              cursor: "pointer", position: "relative",
            }}
            whileHover={{ borderColor: "var(--b2)" }}
            whileTap={{ scale: 0.93 }}
          >
            <Bell size={12} style={{ color: "var(--t1)" }} />
            {running > 0 && (
              <span style={{
                position: "absolute", top: 5, right: 5,
                width: 5, height: 5, borderRadius: "50%",
                background: "var(--gold)", border: "1.5px solid var(--bg-raised)",
              }} />
            )}
          </motion.button>

          <motion.a
            href="/editor"
            style={{
              display: "flex", alignItems: "center", gap: 5,
              height: 28, padding: "0 12px", borderRadius: 9,
              background: "var(--gold-s)", border: "1px solid var(--gold-b)",
              color: "var(--gold)", fontSize: 12, fontWeight: 600, cursor: "pointer",
              letterSpacing: "-0.01em", textDecoration: "none",
            }}
            whileHover={{ background: "rgba(212,168,83,0.2)" }}
            whileTap={{ scale: 0.95 }}
          >
            <Plus size={12} strokeWidth={2.5} /> Clip hochladen
          </motion.a>
        </div>

        {/* Content area */}
        <div style={{ flex: 1, overflowY: "auto", padding: "20px 24px" }}>
          <motion.div
            style={{ marginBottom: 20 }}
            initial={{ opacity: 0, y: 10 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.35, ease: [0.22, 1, 0.36, 1] }}
          >
            <h1 style={{ fontSize: 26, fontWeight: 300, letterSpacing: "-0.03em", color: "var(--t0)", marginBottom: 4 }}>
              Guten Tag, Pascal.
            </h1>
            <p style={{ fontSize: 13, color: "var(--t2)" }}>
              {loading
                ? "Clips werden geladen…"
                : error
                ? "Verbindung zum Backend fehlgeschlagen."
                : `${totalClips} Clip${totalClips !== 1 ? "s" : ""} · ${analyzed} analysiert · ${running} in Bearbeitung`}
            </p>
          </motion.div>

          {/* Stats strip */}
          <motion.div
            style={{
              display: "grid",
              gridTemplateColumns: "repeat(4, 1fr)",
              gap: 1,
              marginBottom: 20,
              borderRadius: 14,
              overflow: "hidden",
              border: "1px solid var(--b0)",
            }}
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            transition={{ duration: 0.35, delay: 0.08 }}
          >
            {stats.map((s) => (
              <div
                key={s.label}
                style={{
                  background: "var(--bg-up)",
                  padding: "14px 18px",
                  borderRight: "1px solid var(--b0)",
                }}
              >
                <div style={{ fontSize: 22, fontWeight: 300, color: s.color, letterSpacing: "-0.03em", lineHeight: 1, marginBottom: 4 }}>
                  {s.value}
                </div>
                <div style={{ fontSize: 12, color: "var(--t1)", fontWeight: 500, letterSpacing: "-0.01em", marginBottom: 2 }}>
                  {s.label}
                </div>
                <div style={{ display: "flex", alignItems: "center", gap: 4 }}>
                  <TrendingUp size={9} style={{ color: "var(--t3)" }} />
                  <span style={{ fontSize: 10, color: "var(--t3)" }}>{s.change}</span>
                </div>
              </div>
            ))}
          </motion.div>

          {/* Section header */}
          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 14 }}>
            <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
              <span className="label">Aktuelle Clips</span>
              <span style={{
                fontSize: 10, fontWeight: 600, padding: "1px 6px", borderRadius: 5,
                background: "var(--s2)", border: "1px solid var(--b0)", color: "var(--t2)",
                fontFamily: "var(--mono)",
              }}>
                {totalClips}
              </span>
            </div>
            <div style={{ display: "flex", gap: 6 }}>
              {["Alle", "Analysiert", "In Bearbeitung", "Entwürfe"].map((f, i) => (
                <motion.button
                  key={f}
                  style={{
                    fontSize: 11, fontWeight: i === 0 ? 600 : 400,
                    padding: "3px 10px", borderRadius: 7, cursor: "pointer",
                    background: i === 0 ? "var(--s2)" : "transparent",
                    border: `1px solid ${i === 0 ? "var(--b1)" : "transparent"}`,
                    color: i === 0 ? "var(--t0)" : "var(--t2)",
                  }}
                  whileHover={{ color: "var(--t0)" }}
                >
                  {f}
                </motion.button>
              ))}
            </div>
          </div>

          {/* Loading */}
          {loading && (
            <div style={{ display: "flex", alignItems: "center", justifyContent: "center", padding: 40, color: "var(--t3)" }}>
              <Loader2 size={16} className="animate-spin" style={{ marginRight: 8 }} />
              <span style={{ fontSize: 13 }}>Clips werden geladen…</span>
            </div>
          )}

          {/* Error */}
          {error && !loading && (
            <div style={{
              display: "flex", alignItems: "center", gap: 10,
              padding: 16, borderRadius: 12,
              background: "rgba(200,80,60,0.08)", border: "1px solid rgba(200,80,60,0.25)",
              color: "#ef8878",
            }}>
              <AlertCircle size={16} />
              <div style={{ fontSize: 13 }}>
                Backend nicht erreichbar: {error}
              </div>
            </div>
          )}

          {/* Empty */}
          {!loading && !error && totalClips === 0 && (
            <div style={{ textAlign: "center", padding: "48px 20px", color: "var(--t2)" }}>
              <div style={{ fontSize: 15, marginBottom: 6, color: "var(--t1)" }}>
                Noch keine Clips.
              </div>
              <div style={{ fontSize: 12, marginBottom: 16 }}>
                Lade dein erstes Rushmaterial hoch, um die Analyse zu starten.
              </div>
              <a
                href="/editor"
                style={{
                  display: "inline-flex", alignItems: "center", gap: 6,
                  padding: "8px 14px", borderRadius: 9,
                  background: "var(--gold-s)", border: "1px solid var(--gold-b)",
                  color: "var(--gold)", fontSize: 12, fontWeight: 600,
                  textDecoration: "none",
                }}
              >
                <Plus size={12} strokeWidth={2.5} /> Clip hochladen
              </a>
            </div>
          )}

          {/* Grid */}
          {!loading && !error && totalClips > 0 && (
            <div style={{ display: "grid", gridTemplateColumns: "2fr 1fr 1fr", gap: 14 }}>
              {featured && (
                <motion.div
                  style={{ gridRow: "span 2" }}
                  initial={{ opacity: 0, y: 16 }}
                  animate={{ opacity: 1, y: 0 }}
                  transition={{ duration: 0.4, ease: [0.22, 1, 0.36, 1] }}
                >
                  <FeaturedCard project={featured} />
                </motion.div>
              )}

              {rest.map((p, i) => (
                <ProjectCard key={p.id} project={p} index={i} />
              ))}
            </div>
          )}
        </div>
      </div>

      <Dock />
    </div>
  );
}
