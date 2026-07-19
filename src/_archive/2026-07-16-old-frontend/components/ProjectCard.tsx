"use client";

import { motion } from "framer-motion";
import Link from "next/link";
import { Clock, Film, ArrowUpRight } from "lucide-react";

export type Project = {
  id: string;
  title: string;
  description: string;
  duration: string;
  clips: number;
  progress: number;
  updatedAt: string;
  gradient: string;
  tags: string[];
  status: "en_cours" | "rendu" | "brouillon";
  featured?: boolean;
  stripUrl?: string | null;
  waveformUrl?: string | null;
};

const STATUS = {
  en_cours: { label: "In Arbeit",  color: "var(--gold)", bg: "var(--gold-s)" },
  rendu:    { label: "Gerendert",  color: "var(--fx)",   bg: "var(--fx-s)"   },
  brouillon:{ label: "Entwurf",   color: "var(--t2)",   bg: "var(--s1)"     },
};

/* ── Small card ─────────────────────────────────────────── */
export function ProjectCard({ project, index }: { project: Project; index: number }) {
  const s = STATUS[project.status];

  return (
    <motion.div
      initial={{ opacity: 0, y: 20 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.38, delay: index * 0.055, ease: [0.22, 1, 0.36, 1] }}
    >
      <Link href={`/project/${project.id}`}>
        <motion.article
          className="group relative rounded-2xl overflow-hidden cursor-pointer"
          style={{
            background: "var(--bg-up)",
            border: "1px solid var(--b0)",
          }}
          whileHover={{ y: -3, borderColor: "var(--b2)" }}
          transition={{ duration: 0.2, ease: [0.2, 0, 0, 1] }}
        >
          {/* Thumbnail */}
          <div className="relative overflow-hidden" style={{ height: 148 }}>
            <div
              className="absolute inset-0 transition-transform duration-500 group-hover:scale-[1.03]"
              style={{
                background: project.stripUrl
                  ? `${project.gradient}, url(${project.stripUrl}) center/cover no-repeat`
                  : project.gradient,
              }}
            />
            {project.stripUrl && (
              <div
                className="absolute inset-0 pointer-events-none"
                style={{
                  backgroundImage: `url(${project.stripUrl})`,
                  backgroundSize: "cover",
                  backgroundPosition: "center",
                  opacity: 0.85,
                }}
              />
            )}
            {project.waveformUrl && (
              <div
                className="absolute left-4 right-4 bottom-3 h-8 pointer-events-none"
                style={{
                  backgroundImage: `url(${project.waveformUrl})`,
                  backgroundSize: "100% 100%",
                  backgroundRepeat: "no-repeat",
                  opacity: 0.6,
                  filter: "drop-shadow(0 0 4px rgba(0,0,0,0.5))",
                }}
              />
            )}

            {/* Film strip perfs */}
            {[0, 1].map((side) => (
              <div
                key={side}
                className="absolute top-0 bottom-0 flex flex-col justify-around items-center py-3"
                style={{
                  [side === 0 ? "left" : "right"]: 0,
                  width: 18,
                  background: "rgba(0,0,0,0.45)",
                }}
              >
                {Array.from({ length: 5 }).map((_, i) => (
                  <div
                    key={i}
                    style={{
                      width: 9,
                      height: 12,
                      background: "rgba(0,0,0,0.65)",
                      borderRadius: 2,
                    }}
                  />
                ))}
              </div>
            ))}

            {/* Status */}
            <div
              className="absolute top-3 left-6"
              style={{
                padding: "3px 8px",
                borderRadius: 99,
                background: s.bg,
                border: `1px solid ${s.color}40`,
                color: s.color,
                fontSize: 10,
                fontWeight: 600,
                letterSpacing: "0.04em",
                backdropFilter: "blur(8px)",
              }}
            >
              {s.label}
            </div>

            {/* Duration */}
            <div
              className="absolute bottom-3 right-6 mono"
              style={{
                padding: "2px 7px",
                borderRadius: 6,
                background: "rgba(0,0,0,0.55)",
                color: "rgba(255,255,255,0.7)",
                backdropFilter: "blur(8px)",
              }}
            >
              {project.duration}
            </div>

            {/* Hover arrow */}
            <motion.div
              className="absolute top-3 right-6 flex items-center justify-center rounded-full"
              style={{
                width: 28,
                height: 28,
                background: "rgba(255,255,255,0.12)",
                border: "1px solid rgba(255,255,255,0.22)",
                backdropFilter: "blur(8px)",
              }}
              initial={{ opacity: 0, scale: 0.7 }}
              whileHover={{ opacity: 1, scale: 1 }}
              transition={{ duration: 0.15 }}
            >
              <ArrowUpRight size={13} style={{ color: "#fff" }} />
            </motion.div>
          </div>

          {/* Body */}
          <div className="p-4">
            <h3
              className="mb-1 leading-tight"
              style={{ color: "var(--t0)", fontSize: 13, fontWeight: 600, letterSpacing: "-0.01em" }}
            >
              {project.title}
            </h3>
            <p
              className="mb-3 leading-relaxed line-clamp-2"
              style={{ color: "var(--t2)", fontSize: 12 }}
            >
              {project.description}
            </p>

            {/* Tags */}
            <div className="flex flex-wrap gap-1.5 mb-3">
              {project.tags.map((tag) => (
                <span
                  key={tag}
                  style={{
                    padding: "2px 7px",
                    borderRadius: 6,
                    background: "var(--s1)",
                    border: "1px solid var(--b0)",
                    color: "var(--t2)",
                    fontSize: 10,
                    fontWeight: 500,
                  }}
                >
                  {tag}
                </span>
              ))}
            </div>

            {/* Progress */}
            <div className="mb-3">
              <div className="flex justify-between mb-1.5" style={{ color: "var(--t3)", fontSize: 11 }}>
                <span>Fortschritt</span>
                <span className="mono">{project.progress}%</span>
              </div>
              <div
                className="w-full rounded-full overflow-hidden"
                style={{ height: 2.5, background: "var(--s2)" }}
              >
                <motion.div
                  style={{
                    height: "100%",
                    background: `linear-gradient(90deg, var(--gold), #2563eb)`,
                    borderRadius: 99,
                  }}
                  initial={{ width: 0 }}
                  animate={{ width: `${project.progress}%` }}
                  transition={{ duration: 0.9, delay: index * 0.055 + 0.3, ease: [0.22, 1, 0.36, 1] }}
                />
              </div>
            </div>

            {/* Footer */}
            <div className="flex items-center justify-between" style={{ color: "var(--t3)", fontSize: 11 }}>
              <div className="flex items-center gap-1.5">
                <Film size={10} />
                <span>{project.clips} clips</span>
              </div>
              <div className="flex items-center gap-1.5">
                <Clock size={10} />
                <span>{project.updatedAt}</span>
              </div>
            </div>
          </div>
        </motion.article>
      </Link>
    </motion.div>
  );
}

/* ── Featured (hero) card ────────────────────────────────── */
export function FeaturedCard({ project }: { project: Project }) {
  const s = STATUS[project.status];

  return (
    <Link href={`/project/${project.id}`}>
      <motion.article
        className="group relative rounded-3xl overflow-hidden cursor-pointer"
        style={{ border: "1px solid var(--b1)" }}
        initial={{ opacity: 0, y: 16 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.5, ease: [0.22, 1, 0.36, 1] }}
        whileHover={{ y: -2, borderColor: "var(--b2)" }}
      >
        {/* Full-bleed image */}
        <div className="relative overflow-hidden" style={{ height: 280 }}>
          <div
            className="absolute inset-0 transition-transform duration-700 group-hover:scale-[1.03]"
            style={{ background: project.gradient }}
          />
          {project.stripUrl && (
            <div
              className="absolute inset-0 pointer-events-none transition-transform duration-700 group-hover:scale-[1.03]"
              style={{
                backgroundImage: `url(${project.stripUrl})`,
                backgroundSize: "cover",
                backgroundPosition: "center",
                opacity: 0.75,
              }}
            />
          )}
          {project.waveformUrl && (
            <div
              className="absolute left-6 right-6 bottom-24 h-12 pointer-events-none"
              style={{
                backgroundImage: `url(${project.waveformUrl})`,
                backgroundSize: "100% 100%",
                backgroundRepeat: "no-repeat",
                opacity: 0.5,
                filter: "drop-shadow(0 0 6px rgba(0,0,0,0.6))",
              }}
            />
          )}

          {/* Cinematic overlay */}
          <div
            className="absolute inset-0"
            style={{
              background:
                "linear-gradient(to top, rgba(9,9,18,0.95) 0%, rgba(9,9,18,0.3) 45%, rgba(9,9,18,0) 70%)",
            }}
          />

          {/* Top row */}
          <div className="absolute top-5 inset-x-6 flex items-center justify-between">
            <div
              style={{
                padding: "3px 10px",
                borderRadius: 99,
                background: s.bg,
                border: `1px solid ${s.color}40`,
                color: s.color,
                fontSize: 10,
                fontWeight: 600,
                letterSpacing: "0.04em",
                backdropFilter: "blur(8px)",
              }}
            >
              {s.label}
            </div>
            <motion.div
              className="flex items-center justify-center rounded-full"
              style={{
                width: 34,
                height: 34,
                background: "rgba(255,255,255,0.1)",
                border: "1px solid rgba(255,255,255,0.2)",
                backdropFilter: "blur(8px)",
              }}
              whileHover={{ background: "rgba(255,255,255,0.2)", scale: 1.08 }}
              whileTap={{ scale: 0.94 }}
            >
              <div
                style={{
                  width: 0,
                  height: 0,
                  borderLeft: "13px solid rgba(255,255,255,0.9)",
                  borderTop: "8px solid transparent",
                  borderBottom: "8px solid transparent",
                  marginLeft: 2,
                }}
              />
            </motion.div>
          </div>

          {/* Bottom text */}
          <div className="absolute bottom-6 inset-x-6">
            <div className="label mb-2" style={{ color: "rgba(212,168,83,0.7)" }}>
              Empfohlenes Projekt
            </div>
            <h2
              style={{
                color: "var(--t0)",
                fontSize: 24,
                fontWeight: 600,
                letterSpacing: "-0.025em",
                lineHeight: 1.2,
                marginBottom: 6,
              }}
            >
              {project.title}
            </h2>
            <p style={{ color: "rgba(242,242,255,0.55)", fontSize: 13 }}>
              {project.description}
            </p>
          </div>
        </div>

        {/* Footer strip */}
        <div
          className="flex items-center justify-between px-6 py-4"
          style={{ background: "var(--bg-up)", borderTop: "1px solid var(--b0)" }}
        >
          <div className="flex items-center gap-4" style={{ color: "var(--t2)", fontSize: 12 }}>
            <div className="flex items-center gap-1.5">
              <Film size={11} />
              <span>{project.clips} clips</span>
            </div>
            <div className="flex items-center gap-1.5">
              <Clock size={11} />
              <span>{project.updatedAt}</span>
            </div>
          </div>

          <div className="flex items-center gap-3" style={{ flex: "0 0 200px" }}>
            <div className="flex-1">
              <div
                className="w-full rounded-full overflow-hidden"
                style={{ height: 3, background: "var(--s2)" }}
              >
                <motion.div
                  style={{
                    height: "100%",
                    background: "linear-gradient(90deg, var(--gold), #2563eb)",
                    borderRadius: 99,
                  }}
                  initial={{ width: 0 }}
                  animate={{ width: `${project.progress}%` }}
                  transition={{ duration: 1, delay: 0.4, ease: [0.22, 1, 0.36, 1] }}
                />
              </div>
            </div>
            <span className="mono" style={{ color: "var(--t2)", fontSize: 11 }}>
              {project.progress}%
            </span>
          </div>
        </div>
      </motion.article>
    </Link>
  );
}
