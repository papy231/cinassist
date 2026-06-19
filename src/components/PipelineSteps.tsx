"use client";

/**
 * CinAssist — PipelineSteps
 *
 * Visualisiert die 9 Schritte der Ingestion-Pipeline mit konkreten Belegen pro Schritt.
 * Wird während des Uploads in der KI-Analyse-Overlay angezeigt.
 *
 * Datenfluss:
 *   Celery worker → Redis pub/sub → WebSocket → editorStore.activeJobs[].schrittHistory
 *   → PipelineSteps zeigt jeden Schritt an
 *
 * Hinweis: zur Vorführung gebaut. Kann später entfernt werden (eine Komponente, ein Import).
 */

import React from "react";

type SchrittDaten = Record<string, unknown>;

interface PipelineStepsProps {
  aktuellerSchritt?: string;
  schrittHistory: Record<string, SchrittDaten>;
}

interface StepDef {
  id: string;
  label: string;
  beschreibung: string;
}

// Die 9 Schritte in genau der Reihenfolge, wie sie im Celery-Worker laufen.
// Die `id` muss mit dem `schritt`-Feld aus backend/workers/ingest.py übereinstimmen.
const STEPS: StepDef[] = [
  { id: "metadaten",        label: "Metadaten lesen",       beschreibung: "ffprobe" },
  { id: "proxy",            label: "Proxy erstellen",       beschreibung: "FFmpeg → 960p H.264 / AAC" },
  { id: "audio",            label: "Audio extrahieren",     beschreibung: "FFmpeg → WAV 16 kHz Mono" },
  { id: "transkription",    label: "Transkription",         beschreibung: "mlx-whisper · large-v3-turbo (Apple MLX)" },
  { id: "szenenerkennung",  label: "Szenenerkennung",       beschreibung: "PySceneDetect · ContentDetector (HSV-Δ)" },
  { id: "visuelle_analyse", label: "Visuelle Analyse",      beschreibung: "PIL · 3 Frames pro Szene (Helligkeit/Kontrast/Bewegung/Schärfe/Energie)" },
  { id: "clip",             label: "Semantische Embeddings", beschreibung: "open-clip · ViT-B/32 (OpenAI, 512-dim)" },
  { id: "beschreibungen",   label: "Szenen-Beschreibungen", beschreibung: "LLaMA3 · Ollama (lokal)" },
  { id: "persistierung",    label: "Persistierung",         beschreibung: "PostgreSQL · clips + szenen" },
];

// Hübschere Schlüssel-Beschriftungen für die schritt_daten-Anzeige.
const SCHLUESSEL_LABELS: Record<string, string> = {
  dauer_s:             "Dauer",
  aufloesung:          "Auflösung",
  bildrate:            "Bildrate",
  codec:               "Codec",
  tool:                "Werkzeug",
  size_mb:             "Größe",
  size_kb:             "Größe",
  sample_rate:         "Abtastrate",
  channels:            "Kanäle",
  format:              "Format",
  ziel_aufloesung:     "Zielauflösung",
  preset:              "Encoder-Preset",
  segmente:            "Segmente",
  woerter:             "Wörter",
  sprache:             "Sprache",
  preview:             "Vorschau",
  text_komplett:       "Vollständiger Text",
  modell:              "Modell",
  provider:            "Anbieter",
  szenen:              "Szenen erkannt",
  algorithmus:         "Algorithmus",
  threshold:           "Schwellwert",
  min_dauer_s:         "kürzeste Szene",
  max_dauer_s:         "längste Szene",
  avg_dauer_s:         "mittlere Dauer",
  szenen_analysiert:   "Szenen analysiert",
  frames_pro_szene:    "Frames pro Szene",
  metriken:            "Metriken",
  energie_min:         "Energie min.",
  energie_max:         "Energie max.",
  energie_avg:         "Energie Ø",
  embeddings:          "Embeddings",
  embeddings_nonzero:  "nicht-null Vektoren",
  dimension:           "Dimension",
  device:              "Gerät",
  beschreibungen:      "Beschreibungen",
  alle:                "Alle Beschreibungen",
  szenen_gespeichert:  "Szenen gespeichert",
  tabellen:            "Tabellen",
  datenbank:           "Datenbank",
  reason:              "Grund",
};

// Schlüssel, deren Wert als ausführlicher Block (mehrzeilig) angezeigt wird,
// statt als Inline "Label: Wert".
const LONG_TEXT_KEYS = new Set(["text_komplett"]);
const LIST_KEYS = new Set(["alle"]);

function formatWert(schluessel: string, wert: unknown): string {
  if (wert === null || wert === undefined) return "—";
  if (Array.isArray(wert)) return wert.map(v => String(v)).join(", ");
  if (typeof wert === "object") return JSON.stringify(wert);

  // Einheiten anhängen, wo es Sinn ergibt
  if (schluessel === "dauer_s" || schluessel.endsWith("_dauer_s")) return `${wert} s`;
  if (schluessel === "size_mb") return `${wert} MB`;
  if (schluessel === "size_kb") return `${wert} KB`;
  if (schluessel === "bildrate") return `${wert} fps`;
  if (schluessel === "sample_rate") return `${wert} Hz`;
  if (schluessel === "dimension") return `${wert}-dim`;
  return String(wert);
}

function renderDaten(daten: SchrittDaten, skipped: boolean): React.ReactNode {
  const eintraege = Object.entries(daten).filter(([k]) => k !== "skipped");
  if (eintraege.length === 0) return null;

  const textColor = skipped ? "var(--orange)" : "var(--text)";

  return (
    <div style={{
      marginTop: 6,
      paddingLeft: 14,
      borderLeft: skipped
        ? "2px solid rgba(255,165,0,.4)"
        : "2px solid rgba(34,197,94,.35)",
      display: "flex",
      flexDirection: "column",
      gap: 4,
    }}>
      {eintraege.map(([schluessel, wert]) => {
        const label = SCHLUESSEL_LABELS[schluessel] || schluessel;

        // Block 1: ausführlicher Fließtext (z.B. Vollständige Transkription)
        if (LONG_TEXT_KEYS.has(schluessel) && typeof wert === "string") {
          return (
            <div key={schluessel} style={{ display: "flex", flexDirection: "column", gap: 3, marginTop: 4 }}>
              <span style={{ color: "var(--text3)", fontSize: 10 }}>{label}:</span>
              <div style={{
                fontSize: 10.5,
                color: textColor,
                fontFamily: "var(--mono)",
                padding: "8px 10px",
                background: "rgba(255,255,255,.025)",
                border: "1px solid var(--border)",
                borderRadius: 4,
                lineHeight: 1.55,
                whiteSpace: "pre-wrap",
                wordBreak: "break-word",
              }}>
                {wert}
              </div>
            </div>
          );
        }

        // Block 2: nummerierte Liste (z.B. alle Szenen-Beschreibungen)
        if (LIST_KEYS.has(schluessel) && Array.isArray(wert)) {
          return (
            <div key={schluessel} style={{ display: "flex", flexDirection: "column", gap: 4, marginTop: 4 }}>
              <span style={{ color: "var(--text3)", fontSize: 10 }}>{label}:</span>
              <div style={{
                display: "flex",
                flexDirection: "column",
                gap: 4,
                padding: "8px 10px",
                background: "rgba(255,255,255,.025)",
                border: "1px solid var(--border)",
                borderRadius: 4,
              }}>
                {wert.map((eintrag, i) => (
                  <div key={i} style={{
                    fontSize: 10.5,
                    color: textColor,
                    fontFamily: "var(--mono)",
                    lineHeight: 1.5,
                    wordBreak: "break-word",
                  }}>
                    {String(eintrag)}
                  </div>
                ))}
              </div>
            </div>
          );
        }

        // Standard: Inline "Label: Wert"
        return (
          <div key={schluessel} style={{ display: "flex", gap: 6, fontSize: 10, lineHeight: 1.45 }}>
            <span style={{ color: "var(--text3)", flexShrink: 0 }}>{label}:</span>
            <span style={{
              color: textColor,
              fontFamily: "var(--mono)",
              wordBreak: "break-word",
            }}>
              {formatWert(schluessel, wert)}
            </span>
          </div>
        );
      })}
    </div>
  );
}

export function PipelineSteps({ aktuellerSchritt, schrittHistory }: PipelineStepsProps) {
  const abgeschlossen = STEPS.filter(s => {
    const d = schrittHistory[s.id];
    return d !== undefined && !d.skipped;
  }).length;

  return (
    <div style={{ marginTop: 12 }}>
      {/* Header */}
      <div style={{
        display: "flex", justifyContent: "space-between", alignItems: "baseline",
        marginBottom: 8, paddingBottom: 6, borderBottom: "1px solid var(--border)",
      }}>
        <span style={{ fontSize: 10, fontWeight: 700, color: "var(--text3)", letterSpacing: ".05em", textTransform: "uppercase" }}>
          Ingestion-Pipeline
        </span>
        <span style={{ fontFamily: "var(--mono)", fontSize: 10, color: "var(--text3)" }}>
          {abgeschlossen} / {STEPS.length} Schritte
        </span>
      </div>

      {/* Steps */}
      <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
        {STEPS.map((step, idx) => {
          const daten = schrittHistory[step.id];
          const isDone = daten !== undefined && !daten.skipped;
          const isSkipped = daten !== undefined && Boolean(daten.skipped);
          const isRunning = aktuellerSchritt === step.id && !isDone && !isSkipped;
          const isPending = !isDone && !isSkipped && !isRunning;

          let bullet = "○";
          let bulletColor = "var(--text3)";
          if (isDone) { bullet = "✓"; bulletColor = "rgb(34,197,94)"; }
          else if (isSkipped) { bullet = "!"; bulletColor = "var(--orange)"; }
          else if (isRunning) { bullet = "●"; bulletColor = "var(--orange)"; }

          return (
            <div key={step.id} style={{
              display: "flex",
              alignItems: "flex-start",
              gap: 8,
              padding: "6px 8px",
              background: isRunning ? "rgba(255,165,0,.06)" : "transparent",
              border: `1px solid ${isRunning ? "rgba(255,165,0,.4)" : "transparent"}`,
              borderRadius: 5,
              opacity: isPending ? 0.45 : 1,
              transition: "all .25s",
            }}>
              <div style={{
                width: 14, height: 14, marginTop: 1,
                display: "flex", alignItems: "center", justifyContent: "center",
                flexShrink: 0,
                color: bulletColor,
                fontFamily: "var(--mono)",
                fontSize: 11, fontWeight: 700,
                animation: isRunning ? "pulse 1.5s ease infinite" : "none",
              }}>
                {bullet}
              </div>

              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ display: "flex", alignItems: "baseline", gap: 6, flexWrap: "wrap" }}>
                  <span style={{
                    fontSize: 11, fontWeight: 700,
                    color: isDone ? "var(--text)" : isSkipped ? "var(--orange)" : isRunning ? "var(--orange)" : "var(--text3)",
                  }}>
                    {idx + 1}. {step.label}
                  </span>
                  <span style={{ color: "var(--text3)", fontSize: 9.5 }}>
                    {step.beschreibung}
                  </span>
                </div>
                {(isDone || isSkipped) && daten && renderDaten(daten, isSkipped)}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
