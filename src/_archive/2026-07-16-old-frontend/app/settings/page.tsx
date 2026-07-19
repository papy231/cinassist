"use client";

/**
 * /settings — Read-only Systemkonfiguration.
 *
 * Zeigt aktuelle Konfiguration (Modelle, Provider, Thresholds, Sprache).
 * Bearbeitung erfolgt via env vars + Backend-Neustart, nicht via UI —
 * absichtlich, um Konsistenz zu wahren.
 */

import { useEffect, useState } from "react";
import { motion } from "framer-motion";
import Link from "next/link";
import {
  ArrowLeft, Loader2, AlertCircle, CheckCircle2, XCircle,
  Cpu, Mic, Eye, Video, Users, Cloud, Settings as Cog,
} from "lucide-react";
import { Dock } from "@/components/Dock";
import { AppSidebar } from "@/components/AppSidebar";

type SystemConfig = {
  agent: { model: string; max_iterations: number; temperature: number };
  whisper: { model: string; sample_rate: number; language: string };
  ollama: { base_url: string; description_model: string; vision_model: string; agent_model: string };
  clip_embedding: { model: string; dimension: number };
  scene_detection: { threshold: number; backend: string };
  diarization: { model: string; hf_token_configured: boolean; min_speaker_time_s: number };
  cloud_providers: {
    claude: { available: boolean; model: string };
    openai: { available: boolean; model: string };
    gemini: { available: boolean; model: string };
  };
  system: { timezone: string; cors_origins: string[]; ffmpeg: string; ffprobe: string };
};

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8001";

export default function SettingsPage() {
  const [cfg, setCfg] = useState<SystemConfig | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetch(`${API_BASE}/api/system/config`)
      .then((r) => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return r.json();
      })
      .then((d) => setCfg(d))
      .catch((e) => setError((e as Error).message))
      .finally(() => setLoading(false));
  }, []);

  return (
    <div style={{ display: "flex", height: "100dvh", background: "var(--bg)", overflow: "hidden" }}>
      <AppSidebar />

      <div style={{
        marginLeft: "var(--sidebar-w)",
        marginBottom: "var(--bar-h)",
        flex: 1,
        display: "flex",
        flexDirection: "column",
        overflow: "hidden",
      }}>
        {/* Top bar */}
        <div style={{
          display: "flex", alignItems: "center", gap: 12,
          padding: "0 20px", height: 46,
          borderBottom: "1px solid var(--b0)",
          background: "var(--bg-up)",
          flexShrink: 0,
        }}>
          <Link href="/" style={{
            display: "flex", alignItems: "center", gap: 5,
            fontSize: 12, color: "var(--t2)", textDecoration: "none",
          }}>
            <ArrowLeft size={12} /> Zurück
          </Link>
          <span style={{ color: "var(--t4)", fontSize: 12 }}>/</span>
          <span style={{ fontSize: 13, color: "var(--t0)", fontWeight: 500 }}>
            Einstellungen
          </span>
        </div>

        <div style={{ flex: 1, overflowY: "auto", padding: "24px 32px" }}>
          <motion.div
            style={{ marginBottom: 24 }}
            initial={{ opacity: 0, y: 10 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.35 }}
          >
            <h1 style={{ fontSize: 24, fontWeight: 300, letterSpacing: "-0.03em", color: "var(--t0)", marginBottom: 4 }}>
              Einstellungen
            </h1>
            <p style={{ fontSize: 13, color: "var(--t2)" }}>
              Systemkonfiguration und aktive KI-Modelle. Änderungen erfolgen via env vars + Backend-Neustart.
            </p>
          </motion.div>

          {loading && (
            <div style={{ display: "flex", alignItems: "center", gap: 8, color: "var(--t3)", padding: 40, justifyContent: "center" }}>
              <Loader2 size={16} className="animate-spin" />
              <span style={{ fontSize: 13 }}>Konfiguration wird geladen…</span>
            </div>
          )}

          {error && !loading && (
            <div style={{
              display: "flex", alignItems: "center", gap: 10,
              padding: 16, borderRadius: 12,
              background: "rgba(200,80,60,0.08)", border: "1px solid rgba(200,80,60,0.25)",
              color: "#ef8878",
            }}>
              <AlertCircle size={16} />
              <div style={{ fontSize: 13 }}>Backend nicht erreichbar: {error}</div>
            </div>
          )}

          {cfg && !loading && !error && (
            <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(360px, 1fr))", gap: 14 }}>
              <Section icon={<Cpu size={13} />} title="Agent (ReAct)">
                <Row label="Modell" value={cfg.agent.model} />
                <Row label="Max Iterationen" value={String(cfg.agent.max_iterations)} />
                <Row label="Temperatur" value={String(cfg.agent.temperature)} />
              </Section>

              <Section icon={<Mic size={13} />} title="Whisper (Transkription)">
                <Row label="Modell" value={cfg.whisper.model} />
                <Row label="Sample-Rate" value={`${cfg.whisper.sample_rate} Hz`} />
                <Row label="Sprache" value={cfg.whisper.language} />
              </Section>

              <Section icon={<Eye size={13} />} title="Ollama (Lokal)">
                <Row label="Base URL" value={cfg.ollama.base_url} />
                <Row label="Beschreibungs-LLM" value={cfg.ollama.description_model} />
                <Row label="Vision (Bilder)" value={cfg.ollama.vision_model} />
                <Row label="Agent" value={cfg.ollama.agent_model} />
              </Section>

              <Section icon={<Video size={13} />} title="Vision (CLIP)">
                <Row label="Modell" value={cfg.clip_embedding.model} />
                <Row label="Embedding-Dimension" value={String(cfg.clip_embedding.dimension)} />
              </Section>

              <Section icon={<Video size={13} />} title="Szenen-Erkennung">
                <Row label="Backend" value={cfg.scene_detection.backend} />
                <Row label="Schwellenwert" value={String(cfg.scene_detection.threshold)} />
              </Section>

              <Section icon={<Users size={13} />} title="Diarization (Sprecher)">
                <Row label="Modell" value={cfg.diarization.model} />
                <Row
                  label="HF Token"
                  value={cfg.diarization.hf_token_configured ? "✓ konfiguriert" : "✗ fehlt"}
                  valueColor={cfg.diarization.hf_token_configured ? "var(--fx)" : "#ef8878"}
                />
                <Row label="Min. Sprechzeit" value={`${cfg.diarization.min_speaker_time_s}s`} />
              </Section>

              <Section icon={<Cloud size={13} />} title="Cloud-Provider">
                <ProviderRow name="Claude" data={cfg.cloud_providers.claude} />
                <ProviderRow name="OpenAI" data={cfg.cloud_providers.openai} />
                <ProviderRow name="Gemini" data={cfg.cloud_providers.gemini} />
              </Section>

              <Section icon={<Cog size={13} />} title="System">
                <Row label="Zeitzone" value={cfg.system.timezone} />
                <Row label="FFmpeg" value={cfg.system.ffmpeg} />
                <Row label="FFprobe" value={cfg.system.ffprobe} />
                <Row label="CORS-Origins" value={`${cfg.system.cors_origins.length} Eintrag/e`} />
              </Section>
            </div>
          )}

          {cfg && (
            <div style={{
              marginTop: 24, padding: 14, borderRadius: 12,
              background: "var(--bg-up)", border: "1px solid var(--b0)",
              fontSize: 12, color: "var(--t2)", lineHeight: 1.6,
            }}>
              <div style={{ fontWeight: 600, color: "var(--t1)", marginBottom: 6 }}>
                Konfiguration ändern
              </div>
              Um Werte zu ändern, setze die passenden Umgebungsvariablen (
              <code style={{ background: "var(--s2)", padding: "1px 5px", borderRadius: 3, fontFamily: "var(--mono)", fontSize: 11 }}>
                OLLAMA_MODEL
              </code>,{" "}
              <code style={{ background: "var(--s2)", padding: "1px 5px", borderRadius: 3, fontFamily: "var(--mono)", fontSize: 11 }}>
                WHISPER_MODEL
              </code>,{" "}
              <code style={{ background: "var(--s2)", padding: "1px 5px", borderRadius: 3, fontFamily: "var(--mono)", fontSize: 11 }}>
                CLAUDE_API_KEY
              </code>{" "}
              …) in <code style={{ background: "var(--s2)", padding: "1px 5px", borderRadius: 3, fontFamily: "var(--mono)", fontSize: 11 }}>.env</code> und starte das Backend neu.
              Details in <code style={{ background: "var(--s2)", padding: "1px 5px", borderRadius: 3, fontFamily: "var(--mono)", fontSize: 11 }}>backend/core/config.py</code>.
            </div>
          )}
        </div>
      </div>

      <Dock />
    </div>
  );
}

/* ─── Sub-components ────────────────────────────────────── */
function Section({ icon, title, children }: { icon: React.ReactNode; title: string; children: React.ReactNode }) {
  return (
    <div style={{
      background: "var(--bg-up)", border: "1px solid var(--b0)",
      borderRadius: 12, padding: 14,
    }}>
      <div style={{
        display: "flex", alignItems: "center", gap: 6,
        color: "var(--t1)", fontSize: 11, fontWeight: 600,
        textTransform: "uppercase", letterSpacing: "0.06em",
        marginBottom: 10,
      }}>
        {icon}
        {title}
      </div>
      <div>{children}</div>
    </div>
  );
}

function Row({ label, value, valueColor }: { label: string; value: string; valueColor?: string }) {
  return (
    <div style={{
      display: "flex", alignItems: "center", justifyContent: "space-between",
      padding: "5px 0", fontSize: 12, borderBottom: "1px solid rgba(255,255,255,0.03)",
    }}>
      <span style={{ color: "var(--t2)" }}>{label}</span>
      <span style={{
        color: valueColor || "var(--t0)",
        fontFamily: "var(--mono)", fontSize: 11,
        maxWidth: 200, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
      }} title={value}>
        {value}
      </span>
    </div>
  );
}

function ProviderRow({ name, data }: { name: string; data: { available: boolean; model: string } }) {
  return (
    <div style={{
      display: "flex", alignItems: "center", justifyContent: "space-between",
      padding: "5px 0", fontSize: 12, borderBottom: "1px solid rgba(255,255,255,0.03)",
    }}>
      <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
        {data.available
          ? <CheckCircle2 size={12} style={{ color: "var(--fx)" }} />
          : <XCircle size={12} style={{ color: "var(--t4)" }} />}
        <span style={{ color: data.available ? "var(--t0)" : "var(--t3)", fontWeight: 500 }}>
          {name}
        </span>
      </div>
      <span style={{
        color: data.available ? "var(--t1)" : "var(--t4)",
        fontFamily: "var(--mono)", fontSize: 10,
      }} title={data.model}>
        {data.available ? data.model : "kein API-Key"}
      </span>
    </div>
  );
}
