"use client";

import { motion } from "framer-motion";
import { Search, Bell, Plus, SlidersHorizontal, TrendingUp } from "lucide-react";
import { Dock } from "@/components/Dock";
import { AppSidebar } from "@/components/AppSidebar";
import { ProjectCard, FeaturedCard, type Project } from "@/components/ProjectCard";

const PROJECTS: Project[] = [
  {
    id: "1",
    title: "L'Heure Bleue",
    description: "Zeitgenössisches Drama — Wong Kar-wai-Einfluss. Farbkorrektur läuft, kühle Atmosphäre, tiefe Blautöne.",
    duration: "18:24",
    clips: 64,
    progress: 72,
    updatedAt: "Vor 2h",
    tags: ["Drama", "4K", "Farbkorrektur"],
    status: "en_cours",
    featured: true,
    gradient: "linear-gradient(145deg, #060d1e 0%, #0b1f4a 35%, #152f70 60%, #060e22 100%)",
  },
  {
    id: "2",
    title: "Pub Discover Studio",
    description: "Werbespot 30s. KI-Clip-Verlängerung bei 3 Übergängen. Musik via AudioCraft.",
    duration: "0:31",
    clips: 12,
    progress: 95,
    updatedAt: "Gestern",
    tags: ["Werbespot", "Clip-Verlängerung", "MusicGen"],
    status: "en_cours",
    gradient: "linear-gradient(145deg, #1a0800 0%, #5c1f00 40%, #c8580a 70%, #3d1200 100%)",
  },
  {
    id: "3",
    title: "Hafen Hamburg",
    description: "Dokumentarfilm 42 Min. SAM2-Rotoskopie bei Wasserszenen. LUT 'Hafen-Dunst'.",
    duration: "42:17",
    clips: 187,
    progress: 38,
    updatedAt: "Vor 3 Tagen",
    tags: ["Dokumentar", "SAM2", "LUT KI"],
    status: "en_cours",
    gradient: "linear-gradient(145deg, #071510 0%, #0c2e1c 50%, #165c30 75%, #061008 100%)",
  },
  {
    id: "4",
    title: "Fragmente",
    description: "Experimenteller Essay. 4K→8K-Upscaling via Real-ESRGAN auf digitalisierten 16mm-Archiven.",
    duration: "7:45",
    clips: 31,
    progress: 100,
    updatedAt: "Letzte Woche",
    tags: ["Experimentell", "Real-ESRGAN"],
    status: "rendu",
    gradient: "linear-gradient(145deg, #120a1a 0%, #3a0f48 45%, #7c1fa8 75%, #160820 100%)",
  },
  {
    id: "5",
    title: "Nacht in St. Pauli",
    description: "Film Noir. LUT-Prompt: 'Film Noir 40er Jahre, hoher Kontrast, 35mm-Korn'.",
    duration: "12:08",
    clips: 48,
    progress: 15,
    updatedAt: "Vor 5 Tagen",
    tags: ["Film Noir", "LUT-Prompt"],
    status: "brouillon",
    gradient: "linear-gradient(145deg, #090909 0%, #181818 50%, #242424 80%, #0c0c0c 100%)",
  },
  {
    id: "6",
    title: "Resonanz",
    description: "Musikvideo Elektro. Automatische Schnitt-/BPM-Synchronisation. MusicGen für instrumentale Variationen.",
    duration: "4:22",
    clips: 89,
    progress: 60,
    updatedAt: "Vor 4 Tagen",
    tags: ["Musikvideo", "MusicGen", "BPM-Sync"],
    status: "en_cours",
    gradient: "linear-gradient(145deg, #00101a 0%, #003366 45%, #0066cc 70%, #001a33 100%)",
  },
];

const STATS = [
  { label: "In Arbeit",       value: "3",   change: "+1 diesen Monat", color: "var(--gold)" },
  { label: "Gerendert",      value: "1",   change: "Diesen Monat",    color: "var(--fx)"   },
  { label: "Clips gesamt",   value: "431", change: "+64 kürzlich",    color: "var(--blue)"  },
  { label: "KI angewendet",  value: "12×", change: "7 Typen",        color: "var(--grade)" },
];

export default function Dashboard() {
  const featured = PROJECTS.find((p) => p.featured)!;
  const rest = PROJECTS.filter((p) => !p.featured);

  return (
    <div style={{ display: "flex", height: "100dvh", background: "var(--bg)", overflow: "hidden" }}>
      <AppSidebar />

      {/* Main */}
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
          {/* Breadcrumb */}
          <span style={{ fontSize: 13, color: "var(--t2)", fontWeight: 400 }}>Projekte</span>
          <span style={{ color: "var(--t4)", fontSize: 12 }}>/</span>
          <span style={{ fontSize: 13, color: "var(--t0)", fontWeight: 500, letterSpacing: "-0.01em" }}>
            Alle Projekte
          </span>

          <div style={{ flex: 1 }} />

          {/* Search */}
          <div
            style={{
              display: "flex",
              alignItems: "center",
              gap: 8,
              background: "var(--bg-raised)",
              border: "1px solid var(--b1)",
              borderRadius: 9,
              padding: "0 10px",
              width: 200,
              height: 28,
            }}
          >
            <Search size={11} style={{ color: "var(--t3)", flexShrink: 0 }} />
            <input
              type="text"
              placeholder="Projekt suchen…"
              style={{
                background: "none", border: "none", outline: "none",
                color: "var(--t0)", fontSize: 12, flex: 1,
                caretColor: "var(--gold)",
              }}
            />
            <kbd style={{ fontSize: 9, color: "var(--t3)", fontFamily: "var(--mono)", letterSpacing: "0.02em" }}>
              ⌘K
            </kbd>
          </div>

          {/* Filter */}
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
            <SlidersHorizontal size={11} />
            Filtern
          </motion.button>

          {/* Notifications */}
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
            <span style={{
              position: "absolute", top: 5, right: 5,
              width: 5, height: 5, borderRadius: "50%",
              background: "var(--gold)", border: "1.5px solid var(--bg-raised)",
            }} />
          </motion.button>

          {/* New project */}
          <motion.button
            style={{
              display: "flex", alignItems: "center", gap: 5,
              height: 28, padding: "0 12px", borderRadius: 9,
              background: "var(--gold-s)", border: "1px solid var(--gold-b)",
              color: "var(--gold)", fontSize: 12, fontWeight: 600, cursor: "pointer",
              letterSpacing: "-0.01em",
            }}
            whileHover={{ background: "rgba(212,168,83,0.2)" }}
            whileTap={{ scale: 0.95 }}
          >
            <Plus size={12} strokeWidth={2.5} />
            Neues Projekt
          </motion.button>
        </div>

        {/* Content area */}
        <div style={{ flex: 1, overflowY: "auto", padding: "20px 24px" }}>

          {/* Page title */}
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
              3 Projekte in Arbeit · 2 Jobs ausstehend · letzte Aktivität vor 2h
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
            {STATS.map((s) => (
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
              <span className="label">Aktuelle Projekte</span>
              <span style={{
                fontSize: 10, fontWeight: 600, padding: "1px 6px", borderRadius: 5,
                background: "var(--s2)", border: "1px solid var(--b0)", color: "var(--t2)",
                fontFamily: "var(--mono)",
              }}>
                {PROJECTS.length}
              </span>
            </div>
            <div style={{ display: "flex", gap: 6 }}>
              {["Alle", "In Arbeit", "Gerendert", "Entwürfe"].map((f, i) => (
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

          {/* Grid */}
          <div style={{ display: "grid", gridTemplateColumns: "2fr 1fr 1fr", gap: 14 }}>
            {/* Featured */}
            <motion.div
              style={{ gridRow: "span 2" }}
              initial={{ opacity: 0, y: 16 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ duration: 0.4, ease: [0.22, 1, 0.36, 1] }}
            >
              <FeaturedCard project={featured} />
            </motion.div>

            {/* Rest */}
            {rest.map((p, i) => (
              <ProjectCard key={p.id} project={p} index={i} />
            ))}
          </div>
        </div>
      </div>

      <Dock />
    </div>
  );
}
