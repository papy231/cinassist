"use client";

import { motion } from "framer-motion";
import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  LayoutGrid,
  FolderOpen,
  BrainCircuit,
  ListOrdered,
  Settings2,
  Zap,
  Activity,
  HardDrive,
  ChevronRight,
} from "lucide-react";

const NAV = [
  {
    section: "Workspace",
    items: [
      { id: "projects", label: "Projekte",      icon: LayoutGrid,   href: "/" },
      { id: "media",    label: "Mediathek",     icon: FolderOpen,   href: "/media" },
    ],
  },
  {
    section: "KI-Tools",
    items: [
      { id: "models",   label: "KI-Modelle",    icon: BrainCircuit, href: "/models" },
      { id: "queue",    label: "Job-Warteschlange", icon: ListOrdered, href: "/queue", badge: "2" },
    ],
  },
  {
    section: "System",
    items: [
      { id: "settings", label: "Einstellungen", icon: Settings2,    href: "/settings" },
    ],
  },
];

const SYSTEM = [
  { label: "M3 Pro MPS", sub: "GPU aktiv",      icon: Zap,       color: "var(--gold)", pulse: true  },
  { label: "3 / 8",      sub: "Modelle",        icon: BrainCircuit, color: "var(--blue)", pulse: false },
  { label: "284 GB",     sub: "Medien",         icon: HardDrive, color: "var(--grade)", pulse: false },
];

export function AppSidebar() {
  const pathname = usePathname();

  return (
    <aside
      style={{
        position: "fixed",
        top: 0,
        left: 0,
        bottom: 0,
        width: "var(--sidebar-w)",
        background: "var(--bg-sidebar)",
        borderRight: "1px solid var(--b0)",
        display: "flex",
        flexDirection: "column",
        zIndex: 40,
        userSelect: "none",
      }}
    >
      {/* Logo */}
      <div
        style={{
          padding: "16px 16px 12px",
          borderBottom: "1px solid var(--b0)",
        }}
      >
        <div className="flex items-center gap-2.5">
          <div
            style={{
              width: 28,
              height: 28,
              borderRadius: 8,
              background: "radial-gradient(circle at 40% 35%, rgba(37,99,235,0.2), rgba(37,99,235,0.05))",
              border: "1px solid rgba(37,99,235,0.35)",
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              flexShrink: 0,
            }}
          >
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none">
              <path d="M2 8h20M2 12h20M7 4l-5 4 5 4M17 4l5 4-5 4M2 20l10-4 10 4"
                stroke="var(--gold)" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"/>
            </svg>
          </div>
          <div>
            <div style={{ fontSize: 13, fontWeight: 700, color: "var(--t0)", letterSpacing: "-0.02em", lineHeight: 1 }}>
              <span style={{ color: "var(--t2)", fontWeight: 400 }}>HAW </span>CineAssist
            </div>
            <div style={{ fontSize: 10, color: "var(--t3)", marginTop: 2, fontWeight: 500 }}>
              v0.1 · M3 Pro
            </div>
          </div>
        </div>
      </div>

      {/* Navigation */}
      <nav style={{ flex: 1, overflowY: "auto", padding: "10px 8px" }}>
        {NAV.map((group) => (
          <div key={group.section} style={{ marginBottom: 20 }}>
            <div
              className="label"
              style={{ padding: "4px 8px 6px", display: "block" }}
            >
              {group.section}
            </div>
            {group.items.map((item) => {
              const Icon = item.icon;
              const active = pathname === item.href;
              return (
                <Link key={item.id} href={item.href} style={{ textDecoration: "none" }}>
                  <motion.div
                    className="flex items-center gap-2.5 px-2.5 py-2 rounded-xl"
                    style={{
                      background: active ? "var(--s2)" : "transparent",
                      border: `1px solid ${active ? "var(--b1)" : "transparent"}`,
                      color: active ? "var(--t0)" : "var(--t1)",
                      marginBottom: 1,
                      cursor: "pointer",
                    }}
                    whileHover={{
                      background: active ? "var(--s2)" : "var(--s1)",
                      color: "var(--t0)",
                    }}
                    transition={{ duration: 0.12 }}
                  >
                    {active && (
                      <motion.div
                        layoutId="sidebar-indicator"
                        style={{
                          position: "absolute",
                          left: 0,
                          width: 2.5,
                          height: 18,
                          background: "var(--gold)",
                          borderRadius: "0 2px 2px 0",
                        }}
                        transition={{ type: "spring", stiffness: 500, damping: 35 }}
                      />
                    )}
                    <Icon
                      size={15}
                      style={{ color: active ? "var(--gold)" : "var(--t2)", flexShrink: 0 }}
                      strokeWidth={active ? 2 : 1.7}
                    />
                    <span style={{ fontSize: 13, fontWeight: active ? 500 : 400, flex: 1, letterSpacing: "-0.01em" }}>
                      {item.label}
                    </span>
                    {"badge" in item && item.badge && (
                      <span
                        style={{
                          fontSize: 10,
                          fontWeight: 600,
                          padding: "1px 6px",
                          borderRadius: 99,
                          background: "var(--gold-s)",
                          color: "var(--gold)",
                          border: "1px solid var(--gold-b)",
                          fontFamily: "var(--mono)",
                        }}
                      >
                        {item.badge}
                      </span>
                    )}
                  </motion.div>
                </Link>
              );
            })}
          </div>
        ))}
      </nav>

      {/* System status */}
      <div style={{ borderTop: "1px solid var(--b0)", padding: "10px 8px" }}>
        {/* Ollama */}
        <div
          className="flex items-center gap-2 px-2.5 py-2 rounded-xl mb-2"
          style={{ background: "var(--s1)", border: "1px solid var(--b0)" }}
        >
          <motion.div
            style={{
              width: 7,
              height: 7,
              borderRadius: "50%",
              background: "var(--fx)",
              boxShadow: "0 0 8px var(--fx)",
              flexShrink: 0,
            }}
            animate={{ opacity: [1, 0.3, 1] }}
            transition={{ duration: 2.5, repeat: Infinity }}
          />
          <div style={{ flex: 1, minWidth: 0 }}>
            <div style={{ fontSize: 11, fontWeight: 600, color: "var(--t0)", letterSpacing: "-0.01em" }}>
              LLaMA 3
            </div>
            <div style={{ fontSize: 10, color: "var(--t3)" }}>Ollama · local · actif</div>
          </div>
          <ChevronRight size={11} style={{ color: "var(--t3)" }} />
        </div>

        {SYSTEM.map((s) => {
          const Icon = s.icon;
          return (
            <div
              key={s.label}
              className="flex items-center gap-2 px-2.5 py-1.5"
            >
              <Icon size={12} style={{ color: s.color, flexShrink: 0 }} />
              <span style={{ fontSize: 11, color: "var(--t2)", flex: 1, letterSpacing: "-0.01em" }}>
                {s.label}
              </span>
              <span style={{ fontSize: 10, color: "var(--t3)", fontFamily: "var(--mono)" }}>
                {s.sub}
              </span>
            </div>
          );
        })}
      </div>
    </aside>
  );
}
