"use client";

/**
 * CinAssist — SceneDetail
 *
 * Zeigt für eine einzelne Szene ALLE Rohdaten an, die das System extrahiert hat:
 *   • Transkription (vollständiger Text)
 *   • Wort-Zeitstempel (jedes Wort mit start/end in Sekunden)
 *   • LLaMA3-Beschreibung (vollständige Antwort)
 *   • Visuelle Metriken (Helligkeit, Kontrast, Bewegung, Schärfe, Energie, …)
 *   • CLIP-Embedding-Status (Dimension + Norm)
 *
 * Wird vom Pipeline-Bericht-Modal unterhalb der PipelineSteps angezeigt.
 */

import React from "react";
import type { SzeneDetail } from "@/lib/api";

interface SceneDetailProps {
  szene: SzeneDetail;
}

// Labels für die analyse_visuelle-Felder (PIL-Metriken aus ingest.py:362-481)
const VIS_LABELS: Record<string, string> = {
  luminosite:  "Helligkeit",
  temperature: "Farbtemperatur",
  kontrast:    "Kontrast",
  mouvement:   "Bewegung",
  schaerfe:    "Schärfe",
  qualitaet:   "Qualität",
  energie:     "Energie",
};

function formatZeit(s: number): string {
  const sek = Math.floor(s);
  const ms = Math.round((s - sek) * 1000);
  return `${sek}.${String(ms).padStart(3, "0").slice(0, 2)} s`;
}

function formatVisuellWert(schluessel: string, wert: unknown): string {
  if (wert === null || wert === undefined) return "—";
  if (typeof wert === "number") {
    // Helligkeitsmetriken sind alle [0,1] — auf 3 Nachkommastellen
    return wert.toFixed(3);
  }
  return String(wert);
}

export function SceneDetail({ szene }: SceneDetailProps) {
  const va = szene.analyse_visuelle || {};

  return (
    <div style={{
      border: "1px solid var(--border)",
      borderRadius: 8,
      padding: 12,
      background: "rgba(255,255,255,.015)",
      marginBottom: 8,
    }}>
      {/* Header: Szenen-Nr + Zeitbereich + Dauer + Thumbnail */}
      <div style={{ display: "flex", alignItems: "flex-start", gap: 12, marginBottom: 10 }}>
        {szene.thumbnail_url && (
          <img
            src={`http://127.0.0.1:8001${szene.thumbnail_url}`}
            alt={`Szene ${szene.szenen_nr}`}
            style={{
              width: 80, height: 45,
              objectFit: "cover",
              borderRadius: 4,
              border: "1px solid var(--border)",
              flexShrink: 0,
            }}
          />
        )}
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ fontSize: 12, fontWeight: 700, color: "var(--text)", marginBottom: 3 }}>
            Szene {szene.szenen_nr}
          </div>
          <div style={{ fontSize: 10, color: "var(--text3)", fontFamily: "var(--mono)" }}>
            {formatZeit(szene.start_zeit)} → {formatZeit(szene.end_zeit)}
            <span style={{ marginLeft: 8 }}>· Dauer: {szene.dauer.toFixed(2)} s</span>
          </div>
          <div style={{
            fontSize: 9, color: "var(--text3)", marginTop: 4, fontStyle: "italic",
            padding: "2px 6px", background: "rgba(168,85,247,.08)",
            border: "1px solid rgba(168,85,247,.25)", borderRadius: 3,
            display: "inline-block",
          }}>
            ⏱ Szenengrenze: PySceneDetect (HSV-Threshold 27, Castellano 2014)
          </div>
        </div>
      </div>

      {/* Transkription (vollständig für diese Szene) */}
      {szene.transkription && (
        <Section label="Transkription (Whisper)">
          <Block>{szene.transkription}</Block>
        </Section>
      )}

      {/* Wort-Zeitstempel — die Rohausgabe von Whisper, Wort für Wort */}
      {szene.woerter_zeitstempel.length > 0 && (
        <Section label={`Wort-Zeitstempel (${szene.woerter_zeitstempel.length} Wörter)`}>
          <div style={{
            display: "flex", flexWrap: "wrap", gap: 4,
            padding: "8px 10px",
            background: "rgba(255,255,255,.025)",
            border: "1px solid var(--border)",
            borderRadius: 4,
          }}>
            {szene.woerter_zeitstempel.map((w, i) => (
              <span
                key={i}
                title={`start=${w.start?.toFixed(3)}s · end=${w.end?.toFixed(3)}s`}
                style={{
                  fontFamily: "var(--mono)",
                  fontSize: 9.5,
                  padding: "2px 5px",
                  background: "var(--bg4)",
                  borderRadius: 3,
                  color: "var(--text)",
                  cursor: "help",
                  display: "inline-flex", gap: 4, alignItems: "baseline",
                }}
              >
                <span>{w.wort}</span>
                <span style={{ color: "var(--text3)", fontSize: 8 }}>
                  {w.start !== null ? `${w.start.toFixed(2)}` : "?"}
                </span>
              </span>
            ))}
          </div>
        </Section>
      )}

      {/* LLaVA Visualbeschreibung (Bild-faktisch, kein Story-Halluzinieren) */}
      {szene.beschreibung && (
        <Section label="LLaVA-Visualbeschreibung (Thumbnail-basiert)">
          <Block italic>{szene.beschreibung}</Block>
        </Section>
      )}

      {/* Visuelle Analyse (PIL — Rohwerte pro Metrik) */}
      {szene.analyse_visuelle && Object.keys(va).length > 0 && (
        <Section label="Visuelle Analyse (PIL · 3-Frame)">
          <div style={{
            display: "grid",
            gridTemplateColumns: "repeat(auto-fill, minmax(140px, 1fr))",
            gap: 6,
            padding: "8px 10px",
            background: "rgba(255,255,255,.025)",
            border: "1px solid var(--border)",
            borderRadius: 4,
          }}>
            {Object.entries(va).map(([k, v]) => (
              <div key={k} style={{
                display: "flex", justifyContent: "space-between",
                fontSize: 10, lineHeight: 1.4,
              }}>
                <span style={{ color: "var(--text3)" }}>
                  {VIS_LABELS[k] || k}
                </span>
                <span style={{
                  color: "var(--text)",
                  fontFamily: "var(--mono)",
                  fontWeight: 600,
                }}>
                  {formatVisuellWert(k, v)}
                </span>
              </div>
            ))}
          </div>
        </Section>
      )}

      {/* CLIP-Embedding-Info */}
      {szene.embedding_vorhanden && (
        <Section label="CLIP-Embedding (ViT-B/32)">
          <div style={{
            display: "flex", gap: 14, flexWrap: "wrap",
            padding: "8px 10px",
            background: "rgba(255,255,255,.025)",
            border: "1px solid var(--border)",
            borderRadius: 4,
            fontSize: 10,
          }}>
            <Inline label="Dimension" value={`${szene.embedding_dimension}-dim`} />
            <Inline label="L2-Norm" value={szene.embedding_norm?.toFixed(4) ?? "—"} />
            <Inline label="Status" value="gespeichert in szenen.clip_embedding" />
          </div>
        </Section>
      )}
    </div>
  );
}

function Section({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div style={{ marginBottom: 8 }}>
      <div style={{
        fontSize: 9.5,
        fontWeight: 700,
        color: "var(--text3)",
        letterSpacing: ".04em",
        textTransform: "uppercase",
        marginBottom: 4,
      }}>
        {label}
      </div>
      {children}
    </div>
  );
}

function Block({ children, italic = false }: { children: React.ReactNode; italic?: boolean }) {
  return (
    <div style={{
      fontSize: 10.5,
      color: "var(--text)",
      fontFamily: "var(--mono)",
      padding: "8px 10px",
      background: "rgba(255,255,255,.025)",
      border: "1px solid var(--border)",
      borderRadius: 4,
      lineHeight: 1.55,
      whiteSpace: "pre-wrap",
      wordBreak: "break-word",
      fontStyle: italic ? "italic" : "normal",
    }}>
      {children}
    </div>
  );
}

function Inline({ label, value }: { label: string; value: string }) {
  return (
    <div style={{ display: "flex", gap: 4 }}>
      <span style={{ color: "var(--text3)" }}>{label}:</span>
      <span style={{ color: "var(--text)", fontFamily: "var(--mono)", fontWeight: 600 }}>{value}</span>
    </div>
  );
}
