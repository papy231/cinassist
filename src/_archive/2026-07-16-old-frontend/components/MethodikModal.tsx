"use client";

/**
 * MethodikModal — Erklärung der CinAssist-Pipeline (Bachelorarbeit-Anker).
 *
 * Zitierbare Übersicht über: Ingestion (ffprobe, PySceneDetect),
 * multimodale Analyse (Whisper, LLaVA, CLIP), Prompt-getriebene Selektion
 * (Cosine-Similarity, PCA), Rhythmus-Sync (librosa), Metriken, Reproduzierbarkeit.
 *
 * Extrahiert aus editor/page.tsx (Zeilen 2927–3081) für bessere Wartbarkeit.
 */

import React from "react";

export function MethodikModal({ onClose }: { onClose: () => void }) {
  return (
    <div
      onClick={onClose}
      style={{
        position: "fixed", inset: 0, background: "rgba(0,0,0,.7)",
        display: "flex", alignItems: "center", justifyContent: "center",
        zIndex: 500, padding: 20,
      }}
    >
      <div
        onClick={(e) => e.stopPropagation()}
        style={{
          background: "var(--bg1)", border: "1px solid var(--border2)",
          borderRadius: 10, maxWidth: 720, width: "100%",
          maxHeight: "85vh", overflowY: "auto",
          boxShadow: "0 20px 60px rgba(0,0,0,.6)",
        }}
      >
        <div style={{
          padding: "14px 18px", borderBottom: "1px solid var(--border)",
          display: "flex", alignItems: "center", gap: 10,
          background: "var(--bg2)", position: "sticky", top: 0,
        }}>
          <span style={{ fontSize: 13, fontWeight: 700, color: "var(--text)" }}>
            Methodik · CinAssist Pipeline
          </span>
          <span style={{ fontSize: 10, color: "var(--text3)", marginLeft: 4 }}>
            100 % lokal · reproduzierbar · zitierbar
          </span>
          <button
            onClick={onClose}
            style={{
              marginLeft: "auto", background: "none", border: "none",
              color: "var(--text2)", cursor: "pointer", fontSize: 16,
              padding: "0 4px",
            }}
          >
            ✕
          </button>
        </div>

        <div style={{ padding: "16px 20px", fontSize: 12, color: "var(--text2)", lineHeight: 1.65 }}>

          <MethodikSection title="Phase 1 — Ingestion">
            <MethodikItem label="ffprobe" desc="Metadaten-Extraktion (Auflösung, Bildrate, Codec, Audiokanäle)." />
            <MethodikItem label="Proxy (H.264, -g 12)" desc="Vorschau-Stream mit frequenten Keyframes für präzises Seek." />
            <MethodikItem label="Waveform-PNG (showwavespic)" desc="Visuelle Audio-Vorschau in der Timeline." />
            <MethodikItem label="Thumbnail-Strip" desc="Frame-Streifen für Clip-Navigation." />
          </MethodikSection>

          <MethodikSection title="Phase 2 — Multimodale Analyse">
            <MethodikItem
              label="PySceneDetect · ContentDetector"
              desc="Szenengrenzen via HSV-Differenz (Threshold 27, empirisch von den Autoren)."
              ref="Castellano, B. 2014–2024 · github.com/Breakthrough/PySceneDetect"
            />
            <MethodikItem
              label="Whisper large-v3"
              desc="Lokale Sprache-zu-Text Transkription, Sprache automatisch erkannt."
              ref="Radford et al., 2022 · OpenAI"
            />
            <MethodikItem
              label="LLaVA-7B (über Ollama)"
              desc="Vision-Language-Modell beschreibt FAKTISCH, was im Thumbnail sichtbar ist. Ersetzt LLaMA3-Halluzinationen bei dialogarmem Material."
              ref="Liu et al., NeurIPS 2023 · llava-vl.github.io"
            />
            <MethodikItem
              label="CLIP ViT-B/32"
              desc="512-dimensionale Embeddings pro Szenen-Thumbnail. Ermöglicht zero-shot Text-zu-Bild Retrieval."
              ref="Radford et al., ICML 2021 · openai.com/research/clip"
            />
          </MethodikSection>

          <MethodikSection title="Phase 3 — Prompt-getriebene Selektion">
            <MethodikItem
              label="Cosine-Similarity"
              desc={"sim(t, s) = (E_t · E_s) / (‖E_t‖ · ‖E_s‖)  —  Prompt-Embedding gegen jedes Szenen-Embedding."}
            />
            <MethodikItem
              label="Top-K Auswahl"
              desc="Szenen nach Relevanz sortiert, dann zeitlich auf der Timeline geordnet. Keine LLM-Halluzination, deterministisch."
            />
            <MethodikItem
              label="PCA-Atlas (◉)"
              desc="Lineare 2D-Projektion des 512-dim CLIP-Raums via SVD. Macht den semantischen Raum sichtbar und überprüfbar — der Prompt wird in denselben Raum projiziert, räumliche Nähe = Selektions-Relevanz."
            />
          </MethodikSection>

          <MethodikSection title="Phase 4 — Rhythmus-Synchronisation (optional, ♪)">
            <MethodikItem
              label="librosa.beat.beat_track"
              desc="Onset-strength + dynamic programming auf der Audio-Spur des Master-Clips. Liefert Tempo (BPM) und Beat-Zeitpunkte."
              ref="Ellis, JNMR 2007 · librosa.org"
            />
            <MethodikItem
              label="Beat-Snapping"
              desc="Schnittgrenzen werden auf den nächsten Beat ≥ Zielposition gesnappt; Segment-Mindestlänge = N Beats (Default 4). Macht Musik-Cuts rhythmisch tight statt visuell-aber-rhythmisch-zufällig."
            />
          </MethodikSection>

          <MethodikSection title="Quantitative Metriken">
            <MethodikFormula k="Diversität" v="|einzigartige Clip-Quellen| / Anzahl Segmente" />
            <MethodikFormula k="Wechselrate" v="Schnitte / Dauer (Schnitte pro Sekunde)" />
            <MethodikFormula k="Dialog-Treue" v="Σ Worte in Auswahl / Σ Worte gesamt" />
            <MethodikFormula k="Prompt-Relevanz" v="mean(cosine sim) der gewählten Szenen" />
          </MethodikSection>

          <MethodikSection title="Reproduzierbarkeit">
            <MethodikItem label="Lokale Modelle" desc="Kein Cloud-Call. Whisper, LLaVA, LLaMA3 laufen über Ollama. CLIP über PyTorch + Metal." />
            <MethodikItem label="Deterministische Selektion" desc="Top-K nach cosine similarity ist stabil. LLM-Refinement abgeschaltet, Temperatur fixiert (T=0.2)." />
            <MethodikItem label="Versionierter State" desc="Jede Szene speichert: Bildgrenzen, Embeddings, Transkript, Beschreibung in PostgreSQL." />
          </MethodikSection>

          <MethodikSection title="Bekannte Grenzen">
            <MethodikItem label="Multicam-Sync" desc="Kein automatischer Sync via Audiokorrelation — aktuell werden Multicam-Winkel als separate Clips behandelt; Audio-Drift wird über den Master-Clip umgangen." />
            <MethodikItem label="Semantischer Höhepunkt-Detektor" desc="Top-K reiht relevante Szenen, schneidet aber nicht explizit auf semantische Höhepunkte (Refrain, Punchline). Eine Erweiterung wäre prosodie-basierte (Whisper-Wort-Energie) Peak-Detection." />
            <MethodikItem label="Beats vs. Visual Cuts" desc="Wenn Beat-Sync aktiv ist und das Material kein klares Beat-Pattern hat (z.B. Sprach-Dokumentation), kann die rhythmische Snap-Regel ungewollte Verzerrungen erzeugen. Toggle deaktivieren in solchen Fällen." />
          </MethodikSection>

        </div>
      </div>
    </div>
  );
}

function MethodikSection({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div style={{ marginBottom: 16 }}>
      <div style={{
        fontSize: 10, fontWeight: 700, letterSpacing: ".08em",
        textTransform: "uppercase", color: "var(--orange)",
        marginBottom: 6, paddingBottom: 4, borderBottom: "1px solid var(--border)",
      }}>
        {title}
      </div>
      <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>{children}</div>
    </div>
  );
}

function MethodikItem({ label, desc, ref }: { label: string; desc: string; ref?: string }) {
  return (
    <div style={{ padding: "5px 0" }}>
      <div style={{ fontSize: 11.5, fontWeight: 600, color: "var(--text)" }}>{label}</div>
      <div style={{ fontSize: 11, color: "var(--text2)", marginTop: 2 }}>{desc}</div>
      {ref && (
        <div style={{ fontSize: 10, color: "var(--text3)", fontStyle: "italic", marginTop: 2 }}>
          {ref}
        </div>
      )}
    </div>
  );
}

function MethodikFormula({ k, v }: { k: string; v: string }) {
  return (
    <div style={{ display: "flex", gap: 10, alignItems: "baseline", padding: "3px 0" }}>
      <span style={{ fontSize: 11.5, fontWeight: 600, color: "var(--text)", minWidth: 120 }}>{k}</span>
      <span style={{ fontFamily: "var(--mono)", fontSize: 10.5, color: "var(--text2)" }}>{v}</span>
    </div>
  );
}
