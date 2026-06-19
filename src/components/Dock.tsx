"use client";

import { motion } from "framer-motion";
import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  LayoutGrid,
  Scissors,
  Palette,
  Sparkles,
  Package,
  FileOutput,
} from "lucide-react";

type BarItem = {
  id: string;
  label: string;
  sub?: string;
  icon: React.ElementType;
  href: string;
  color: string;
  sep?: boolean;
};

const ITEMS: BarItem[] = [
  {
    id: "projects",
    label: "Projekte",
    sub: "Project Manager",
    icon: LayoutGrid,
    href: "/",
    color: "var(--blue)",
  },
  {
    id: "montage",
    label: "Schnitt",
    sub: "Cut · Rough Cut",
    icon: Scissors,
    href: "#montage",
    color: "var(--cut)",
    sep: true,
  },
  {
    id: "etalonnage",
    label: "Farbkorrektur",
    sub: "Color · LUT KI",
    icon: Palette,
    href: "#etalonnage",
    color: "var(--grade)",
  },
  {
    id: "effets",
    label: "Effekte",
    sub: "Wan2.1 · SAM2",
    icon: Sparkles,
    href: "#effets",
    color: "var(--fx)",
  },
  {
    id: "models",
    label: "Modelle",
    sub: "8 KI-Modelle",
    icon: Package,
    href: "/models",
    color: "rgba(234,234,244,0.5)",
    sep: true,
  },
  {
    id: "export",
    label: "Exportieren",
    sub: "Render · Deliver",
    icon: FileOutput,
    href: "#export",
    color: "rgba(234,234,244,0.5)",
  },
];

export function Dock() {
  const pathname = usePathname();

  return (
    <div
      style={{
        position: "fixed",
        bottom: 0,
        left: "var(--sidebar-w)",
        right: 0,
        height: "var(--bar-h)",
        background: "var(--bg-bar)",
        borderTop: "1px solid var(--b0)",
        display: "flex",
        alignItems: "stretch",
        zIndex: 40,
      }}
    >
      {ITEMS.map((item, i) => {
        const Icon = item.icon;
        const isActive =
          item.href === "/"
            ? pathname === "/"
            : item.href !== "#montage" &&
              item.href !== "#etalonnage" &&
              item.href !== "#effets" &&
              item.href !== "#export" &&
              pathname === item.href;

        return (
          <div key={item.id} style={{ display: "flex", alignItems: "stretch" }}>
            {item.sep && i > 0 && (
              <div
                style={{
                  width: 1,
                  background: "var(--b0)",
                  alignSelf: "stretch",
                  margin: "10px 4px",
                }}
              />
            )}
            <Link href={item.href} style={{ textDecoration: "none" }}>
              <motion.div
                className="relative flex flex-col items-center justify-center gap-0.5"
                style={{
                  height: "100%",
                  padding: "0 20px",
                  cursor: "pointer",
                  color: isActive ? item.color : "var(--t2)",
                  minWidth: 80,
                }}
                whileHover={{ color: isActive ? item.color : "var(--t1)" }}
                whileTap={{ scale: 0.97 }}
                transition={{ duration: 0.1 }}
              >
                {/* Active top accent */}
                {isActive && (
                  <motion.div
                    layoutId="bar-active"
                    style={{
                      position: "absolute",
                      top: 0,
                      left: 8,
                      right: 8,
                      height: 2,
                      background: item.color,
                      borderRadius: "0 0 3px 3px",
                    }}
                    transition={{ type: "spring", stiffness: 500, damping: 35 }}
                  />
                )}
                <Icon
                  size={15}
                  strokeWidth={isActive ? 2 : 1.7}
                  style={{ color: isActive ? item.color : "inherit" }}
                />
                <span
                  style={{
                    fontSize: 11,
                    fontWeight: isActive ? 600 : 400,
                    letterSpacing: "-0.01em",
                    color: isActive ? item.color : "inherit",
                    lineHeight: 1,
                  }}
                >
                  {item.label}
                </span>
              </motion.div>
            </Link>
          </div>
        );
      })}

      {/* Right spacer + Ollama badge */}
      <div style={{ flex: 1 }} />
      <div
        className="flex items-center gap-2 px-5"
        style={{ borderLeft: "1px solid var(--b0)" }}
      >
        <motion.div
          style={{
            width: 6,
            height: 6,
            borderRadius: "50%",
            background: "var(--fx)",
            boxShadow: "0 0 6px var(--fx)",
          }}
          animate={{ opacity: [1, 0.3, 1] }}
          transition={{ duration: 2.5, repeat: Infinity }}
        />
        <span style={{ fontSize: 11, color: "var(--t3)", fontWeight: 500 }}>
          LLaMA 3 · local
        </span>
      </div>
    </div>
  );
}
