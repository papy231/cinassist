"use client";

/**
 * JobsBadge — Live-Anzeige laufender Analyse-Jobs.
 *
 * Pollt alle 4 Sekunden /api/clips und leitet die Clips mit
 * Status "laeuft" / "wartend" ab. Zeigt einen Zähler-Badge + Dropdown
 * mit Liste der aktiven Jobs. Klick auf einen Job → /project/{id}.
 */

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { motion, AnimatePresence } from "framer-motion";
import { Activity, ChevronUp, Loader2 } from "lucide-react";
import { fetchClips, type ClipDTO } from "@/lib/api";

const POLL_MS = 4_000;

function shortenName(name: string, max = 32): string {
  const noExt = name.replace(/\.[^/.]+$/, "");
  return noExt.length > max ? noExt.slice(0, max - 1) + "…" : noExt;
}

export function JobsBadge() {
  const [running, setRunning] = useState<ClipDTO[]>([]);
  const [open, setOpen] = useState(false);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const menuRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    let cancelled = false;
    const tick = async () => {
      try {
        const clips = await fetchClips();
        if (cancelled) return;
        const active = clips.filter(
          (c) => c.status === "laeuft" || c.status === "wartend"
        );
        setRunning(active);
      } catch {
        // silent — Backend kann offline sein
      }
      timerRef.current = setTimeout(tick, POLL_MS);
    };
    tick();
    return () => {
      cancelled = true;
      if (timerRef.current) clearTimeout(timerRef.current);
    };
  }, []);

  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    };
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, [open]);

  const count = running.length;
  const disabled = count === 0;

  return (
    <div ref={menuRef} style={{ position: "relative", display: "flex", alignItems: "center" }}>
      <motion.button
        onClick={() => !disabled && setOpen((v) => !v)}
        disabled={disabled}
        title={disabled ? "Keine aktiven Jobs" : `${count} aktive${count === 1 ? "r" : ""} Job${count === 1 ? "" : "s"}`}
        style={{
          display: "flex",
          alignItems: "center",
          gap: 6,
          padding: "5px 10px",
          borderRadius: 8,
          background: disabled ? "transparent" : "var(--gold-s)",
          border: `1px solid ${disabled ? "var(--b0)" : "var(--gold-b)"}`,
          color: disabled ? "var(--t3)" : "var(--gold)",
          fontSize: 11,
          fontWeight: 500,
          cursor: disabled ? "default" : "pointer",
        }}
        whileHover={disabled ? undefined : { scale: 1.02 }}
        whileTap={disabled ? undefined : { scale: 0.97 }}
      >
        {count > 0 ? (
          <Loader2 size={11} className="animate-spin" />
        ) : (
          <Activity size={11} />
        )}
        <span>{count > 0 ? `${count} Job${count > 1 ? "s" : ""}` : "Idle"}</span>
        {count > 0 && (
          <motion.span
            animate={{ rotate: open ? 0 : 180 }}
            transition={{ duration: 0.15 }}
          >
            <ChevronUp size={10} />
          </motion.span>
        )}
      </motion.button>

      <AnimatePresence>
        {open && count > 0 && (
          <motion.div
            initial={{ opacity: 0, y: 6, scale: 0.98 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: 6, scale: 0.98 }}
            transition={{ duration: 0.15 }}
            style={{
              position: "absolute",
              bottom: "calc(100% + 8px)",
              right: 0,
              minWidth: 300,
              maxHeight: 320,
              overflowY: "auto",
              background: "var(--bg-up)",
              border: "1px solid var(--b1)",
              borderRadius: 12,
              boxShadow: "0 12px 40px rgba(0,0,0,0.4)",
              padding: 8,
              zIndex: 100,
            }}
          >
            <div
              style={{
                fontSize: 10,
                fontWeight: 600,
                textTransform: "uppercase",
                letterSpacing: "0.06em",
                color: "var(--t3)",
                padding: "6px 8px 8px",
              }}
            >
              Aktive Jobs
            </div>
            {running.map((c) => (
              <Link
                key={c.id}
                href={`/project/${c.id}`}
                onClick={() => setOpen(false)}
                style={{ textDecoration: "none" }}
              >
                <motion.div
                  style={{
                    display: "flex",
                    alignItems: "center",
                    gap: 10,
                    padding: "8px 10px",
                    borderRadius: 8,
                    cursor: "pointer",
                  }}
                  whileHover={{ background: "rgba(255,255,255,0.04)" }}
                >
                  <Loader2
                    size={12}
                    className="animate-spin"
                    style={{ color: "var(--gold)", flexShrink: 0 }}
                  />
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div
                      style={{
                        fontSize: 12,
                        color: "var(--t0)",
                        fontWeight: 500,
                        overflow: "hidden",
                        textOverflow: "ellipsis",
                        whiteSpace: "nowrap",
                      }}
                    >
                      {shortenName(c.dateiname)}
                    </div>
                    <div style={{ fontSize: 10, color: "var(--t3)" }}>
                      Status: {c.status} · Quelle {c.quelle}
                    </div>
                  </div>
                </motion.div>
              </Link>
            ))}
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}
