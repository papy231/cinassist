"use client";
/**
 * ClipAnalysisModal.tsx — vue détaillée de l'analyse d'un clip.
 *
 * Ouvert par clic droit sur un item de la Media Library. Fetch en parallèle
 * `/api/clips/{id}/analyse` (scènes + transcription + embeddings) et
 * `/api/clips/{id}/pipeline` (rapport des 9 étapes du pipeline d'ingest).
 *
 * Onglets :
 *  - Übersicht : métadonnées + statut de chaque étape
 *  - Szenen    : liste scènes avec thumbnails, timestamps, descriptions
 *  - Transkription : transcription complète (Whisper)
 *  - Pipeline  : rapport détaillé étape par étape
 */

import { useEffect, useState } from "react";

const API = process.env.NEXT_PUBLIC_API_URL ?? "";

type Szene = {
  id: string;
  szenen_nr: number;
  start: number;
  end: number;
  dauer: number;
  beschreibung: string | null;
  transkription: string | null;
  transkription_json: unknown;
  hat_embedding: boolean;
  thumbnail_pfad: string | null;
};

type Analyse = {
  clip_id: string;
  dateiname: string;
  quelle: string;
  dauer: number;
  aufloesung: string | null;
  bildrate: number | null;
  szenen_anzahl: number;
  szenen: Szene[];
};

type Pipeline = {
  clip_id: string;
  dateiname: string;
  schritt_history: Record<string, Record<string, unknown>>;
};

type Synthese = {
  thema: string;
  narration: string;
  visuell: string;
  ambiance: string;
  genre: string;
  /** Personnes réellement dans le clip (diarisées). */
  anwesende_personen?: string[];
  /** Personnes juste mentionnées dans le dialogue. */
  erwaehnte_personen?: string[];
  /** Legacy champ (avant scission) — merged côté frontend en anwesende. */
  personen?: string[];
  generated_at?: string;
  model?: string;
};

const fmtSec = (s: number) => {
  const m = Math.floor(s / 60);
  const rest = s - m * 60;
  return `${String(m).padStart(2, "0")}:${rest.toFixed(2).padStart(5, "0")}`;
};

/**
 * Convertit un chemin absolu filesystem (backend) en URL statique servie par
 * FastAPI. Le pipeline stocke `thumbnail_pfad` en absolu (ex:
 * `/Users/…/backend/temp/thumbs_X/szene_000.jpg`) → StaticFiles est monté sur
 * `/temp` côté FastAPI → il suffit de rebase après `/temp/`.
 */
const thumbUrl = (absPath: string | null): string | null => {
  if (!absPath) return null;
  const m = absPath.match(/\/temp\/(.+)$/);
  if (!m) return null;
  return `${API}/temp/${m[1]}`;
};

type WhisperSegment = { start: number; end: number; text: string; woerter?: unknown };

/**
 * Filet de sécurité : ré-applique le filtre anti-hallucination Whisper côté
 * frontend pour les clips analysés AVANT le fix backend (leurs "Danke",
 * "Untertitel…", "Musik Musik" sont déjà persistés en DB).
 */
const WHISPER_JUNK = new Set<string>([
  "danke", "danke schön", "vielen dank", "vielen dank fürs zuschauen",
  "thank you", "thanks", "thanks for watching",
  "musik", "music", "musique", "applaus", "applause", "geräusche", "noise",
  "soundtrack", "mahalo",
  "musik musik", "musik musik musik", "music music", "music music music",
  "untertitel der amara org community",
  "untertitel von stephanie geiges",
  "untertitelung des zdf",
  "untertitelung im auftrag des zdf",
  "untertitel im auftrag des zdf für funk 2017",
  "untertitelung aufgrund der amara org community",
  "sf produktion",
  "ja", "nein", "okay", "ok", "so", "you", "yeah", "yes", "no",
  "uh", "ah", "hm", "hmm", "mm", "um", "eh",
  "amen", "goodbye", "farewell", "bye",
  "♪", "♫", "♪♪",
]);
const isHallucination = (text: string): boolean => {
  const norm = text.trim().toLowerCase();
  if (norm.length <= 2) return true;
  const clean = norm.replace(/[.,!?;:…]+/g, "").trim();
  if (WHISPER_JUNK.has(clean)) return true;
  if (clean.length <= 2) return true;
  const words = clean.split(/\s+/);
  if (words.length >= 2 && new Set(words).size === 1) return true;
  if (words.length >= 4 && new Set(words).size <= 2) return true;
  return false;
};

export default function ClipAnalysisModal({ clipId, onClose }: { clipId: string; onClose: () => void }) {
  const [tab, setTab] = useState<"info" | "szenen" | "text" | "pipeline">("info");
  const [analyse, setAnalyse] = useState<Analyse | null>(null);
  const [pipeline, setPipeline] = useState<Pipeline | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  // Analyse encore en cours (backend renvoie 409 tant que status != "analysiert").
  const [analyzing, setAnalyzing] = useState(false);
  const [analyzingMsg, setAnalyzingMsg] = useState<string>("");

  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | null = null;

    const poll = async () => {
      try {
        const rA = await fetch(`${API}/api/clips/${clipId}/analyse`);
        // 409 = analyse pas encore terminée → pas une erreur : spinner + re-poll.
        if (rA.status === 409) {
          let msg = "Dieser Clip wird noch verarbeitet.";
          try { const body = await rA.json(); if (body?.detail) msg = String(body.detail); } catch {}
          if (cancelled) return;
          setAnalyzing(true);
          setAnalyzingMsg(msg);
          setError(null);
          setLoading(false);
          timer = setTimeout(poll, 3000);
          return;
        }
        if (!rA.ok) throw new Error(`Analyse HTTP ${rA.status}`);
        const a = await rA.json();
        const p = await fetch(`${API}/api/clips/${clipId}/pipeline`)
          .then((r) => (r.ok ? r.json() : null))
          .catch(() => null);
        if (cancelled) return;
        setAnalyse(a);
        setPipeline(p);
        setAnalyzing(false);
        setError(null);
        setLoading(false);
      } catch (e) {
        if (cancelled) return;
        setError((e as Error).message);
        setAnalyzing(false);
        setLoading(false);
      }
    };

    setLoading(true);
    setError(null);
    setAnalyse(null);
    setAnalyzing(false);
    poll();

    return () => { cancelled = true; if (timer) clearTimeout(timer); };
  }, [clipId]);

  const tabs: Array<{ key: typeof tab; label: string }> = [
    { key: "info", label: "Übersicht" },
    { key: "szenen", label: `Szenen ${analyse ? `(${analyse.szenen_anzahl})` : ""}` },
    { key: "text", label: "Transkription" },
    { key: "pipeline", label: "Bericht" },
  ];

  return (
    <div style={{ position: "fixed", inset: 0, zIndex: 3000, background: "rgba(0,0,0,0.72)", display: "flex", alignItems: "center", justifyContent: "center", padding: 20 }} onClick={onClose}>
      <div
        onClick={(e) => e.stopPropagation()}
        style={{ background: "#161617", border: "1px solid rgba(255,255,255,0.08)", borderRadius: 14, width: "min(920px, 95vw)", height: "min(760px, 92vh)", display: "flex", flexDirection: "column", overflow: "hidden", boxShadow: "0 20px 60px rgba(0,0,0,0.7)", color: "#e6e6e6" }}
      >
        {/* Header */}
        <div style={{ padding: "14px 20px", borderBottom: "1px solid #232326", display: "flex", alignItems: "center", gap: 12 }}>
          <div style={{ width: 8, height: 8, borderRadius: "50%", background: "#e5c100" }} />
          <div style={{ flex: 1, minWidth: 0 }}>
            <div style={{ fontSize: 14, fontWeight: 600, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
              {analyse?.dateiname ?? "Analyse laden…"}
            </div>
            {analyse && (
              <div style={{ fontSize: 11, color: "#8a8a8a", marginTop: 2 }}>
                {analyse.aufloesung ?? "?"} · {analyse.bildrate ?? "?"} fps · {fmtSec(analyse.dauer)} · {analyse.szenen_anzahl} Szenen
              </div>
            )}
          </div>
          <button onClick={onClose} style={{ background: "transparent", border: "none", color: "#aaa", cursor: "pointer", fontSize: 20, padding: "0 6px" }}>✕</button>
        </div>

        {/* Tabs */}
        <div style={{ display: "flex", gap: 4, padding: "8px 12px", borderBottom: "1px solid #232326", background: "#141415" }}>
          {tabs.map((t) => (
            <button
              key={t.key}
              onClick={() => setTab(t.key)}
              disabled={loading || analyzing}
              style={{ padding: "6px 12px", borderRadius: 6, background: tab === t.key ? "rgba(229,193,0,0.14)" : "transparent", border: "none", color: tab === t.key ? "#e5c100" : "#c8c8c8", fontSize: 12, cursor: "pointer", fontWeight: tab === t.key ? 600 : 400 }}
            >
              {t.label}
            </button>
          ))}
        </div>

        {/* Content */}
        <div style={{ flex: 1, overflowY: "auto", padding: 18 }}>
          {loading && <div style={{ color: "#888", textAlign: "center", padding: 40 }}>Lade Analyse-Daten…</div>}
          {analyzing && (
            <div style={{ display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", gap: 16, padding: 48, textAlign: "center" }}>
              <svg width={36} height={36} viewBox="0 0 24 24" fill="none" stroke="#e5c100" strokeWidth={2.4} strokeLinecap="round" strokeDasharray="14 42" style={{ animation: "spin 0.9s linear infinite" }}>
                <circle cx="12" cy="12" r="9" />
              </svg>
              <div style={{ fontSize: 14, fontWeight: 600, color: "#e5c100" }}>Analyse läuft…</div>
              <div style={{ fontSize: 12, color: "#8a8a8a", maxWidth: 380, lineHeight: 1.5 }}>
                {analyzingMsg || "Dieser Clip wird noch verarbeitet (Szenenerkennung, Transkription, CLIP-Embeddings)."}
              </div>
              <div style={{ fontSize: 11, color: "#666" }}>Aktualisiert automatisch…</div>
            </div>
          )}
          {error && <div style={{ color: "#e88", padding: 12, background: "rgba(255,120,120,0.08)", borderRadius: 6 }}>Fehler: {error}</div>}
          {!loading && !analyzing && !error && analyse && (
            <>
              {tab === "info" && <OverviewTab analyse={analyse} pipeline={pipeline} />}
              {tab === "szenen" && <ScenesTab szenen={analyse.szenen} />}
              {tab === "text" && <TranscriptTab szenen={analyse.szenen} />}
              {tab === "pipeline" && <BerichtTab clipId={analyse.clip_id} pipeline={pipeline} />}
            </>
          )}
        </div>
      </div>
    </div>
  );
}

// ─── TABS ───────────────────────────────────────────────────────────────────

function OverviewTab({ analyse, pipeline }: { analyse: Analyse; pipeline: Pipeline | null }) {
  const totalTranscribed = analyse.szenen.filter((s) => s.transkription).length;
  const withEmbedding = analyse.szenen.filter((s) => s.hat_embedding).length;
  const withDescription = analyse.szenen.filter((s) => s.beschreibung).length;
  const durMinutes = (analyse.dauer / 60).toFixed(1);
  const cards = [
    { label: "Dauer", value: `${durMinutes} min`, sub: fmtSec(analyse.dauer) },
    { label: "Auflösung", value: analyse.aufloesung ?? "?", sub: `${analyse.bildrate ?? "?"} fps` },
    { label: "Szenen erkannt", value: String(analyse.szenen_anzahl), sub: "scenedetect" },
    { label: "Transkribiert", value: `${totalTranscribed}/${analyse.szenen_anzahl}`, sub: "mlx-whisper" },
    { label: "CLIP-Embeddings", value: `${withEmbedding}/${analyse.szenen_anzahl}`, sub: "open_clip" },
    { label: "Beschreibungen", value: `${withDescription}/${analyse.szenen_anzahl}`, sub: "moondream · llama3" },
  ];
  return (
    <div>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: 10, marginBottom: 18 }}>
        {cards.map((c) => (
          <div key={c.label} style={{ background: "#1c1c1e", border: "1px solid rgba(255,255,255,0.05)", borderRadius: 8, padding: "12px 14px" }}>
            <div style={{ fontSize: 10, color: "#7a7a7a", textTransform: "uppercase", letterSpacing: 0.5 }}>{c.label}</div>
            <div style={{ fontSize: 18, fontWeight: 600, marginTop: 4, color: "#f0f0f0" }}>{c.value}</div>
            <div style={{ fontSize: 10, color: "#666", marginTop: 2 }}>{c.sub}</div>
          </div>
        ))}
      </div>
      {pipeline && (
        <div style={{ background: "#1c1c1e", borderRadius: 8, padding: 14 }}>
          <div style={{ fontSize: 11, color: "#8a8a8a", textTransform: "uppercase", letterSpacing: 0.5, marginBottom: 8 }}>Pipeline-Schritte</div>
          <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
            {Object.keys(pipeline.schritt_history).map((k) => (
              <span key={k} style={{ padding: "4px 10px", borderRadius: 12, background: "rgba(120,200,120,0.13)", border: "1px solid rgba(120,200,120,0.3)", fontSize: 11, color: "#96d996" }}>✓ {k}</span>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

function ScenesTab({ szenen }: { szenen: Szene[] }) {
  if (szenen.length === 0) return <div style={{ color: "#888" }}>Keine Szenen erkannt.</div>;
  // Reconstruit la transcription de chaque scène en NE prenant QUE les segments
  // dont le `start` tombe dans le range [s.start, s.end] — évite les doublons
  // dus au pipeline d'ingest qui attribue un même segment Whisper à plusieurs
  // scènes qu'il traverse.
  const trueTranscript = (s: Szene): string => {
    const raw = s.transkription_json;
    if (!Array.isArray(raw)) return "";
    const parts: string[] = [];
    for (const seg of raw) {
      if (!seg || typeof seg !== "object") continue;
      const w = seg as WhisperSegment;
      if (typeof w.start !== "number" || typeof w.text !== "string") continue;
      if (isHallucination(w.text)) continue;
      if (w.start >= s.start && w.start < s.end) parts.push(w.text.trim());
    }
    return parts.join(" ").trim();
  };
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
      {szenen.map((s) => {
        const txt = trueTranscript(s);
        return (
          <div key={s.id} style={{ display: "flex", gap: 12, padding: 10, background: "#1c1c1e", borderRadius: 8, border: "1px solid rgba(255,255,255,0.05)" }}>
            {(() => {
              const u = thumbUrl(s.thumbnail_pfad);
              return u ? (
                <img src={u} alt="" style={{ width: 120, height: 68, objectFit: "cover", borderRadius: 4, flexShrink: 0, background: "#000" }} />
              ) : (
                <div style={{ width: 120, height: 68, background: "#0e0e10", borderRadius: 4, flexShrink: 0, display: "flex", alignItems: "center", justifyContent: "center", color: "#555", fontSize: 10 }}>kein Frame</div>
              );
            })()}
            <div style={{ flex: 1, minWidth: 0 }}>
              <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 4 }}>
                <span style={{ fontSize: 12, fontWeight: 600, color: "#e5c100" }}>Szene {s.szenen_nr}</span>
                <span style={{ fontSize: 10, color: "#888", fontFamily: "ui-monospace, monospace" }}>{fmtSec(s.start)} → {fmtSec(s.end)} · {s.dauer.toFixed(2)}s</span>
                {s.hat_embedding && <span style={{ fontSize: 9, padding: "1px 6px", borderRadius: 8, background: "rgba(120,180,255,0.15)", color: "#9ac2ff" }}>CLIP</span>}
              </div>
              {s.beschreibung && (
                <div style={{ fontSize: 12, color: "#d0d0d0", lineHeight: 1.4, marginBottom: 4 }}>{s.beschreibung}</div>
              )}
              {txt ? (
                <div style={{ fontSize: 11, color: "#8a8a8a", fontStyle: "italic", lineHeight: 1.35, borderLeft: "2px solid rgba(229,193,0,0.35)", paddingLeft: 8, marginTop: 4 }}>
                  „{txt.length > 220 ? txt.slice(0, 220) + "…" : txt}"
                </div>
              ) : (
                <div style={{ fontSize: 10, color: "#555", fontStyle: "italic", marginTop: 4 }}>Keine Sprache in dieser Szene.</div>
              )}
            </div>
          </div>
        );
      })}
    </div>
  );
}

function TranscriptTab({ szenen }: { szenen: Szene[] }) {
  // Aplati toutes les scènes en une seule liste de segments Whisper.
  // NOTE : le pipeline d'ingest attribue un segment Whisper à TOUTES les scènes
  // qu'il traverse → un segment qui va de 4s à 13s apparaît dans les scènes 1
  // à 4. On dédoublonne par (start, end) — un seul segment par phrase, avec la
  // scène de rattachement = celle qui contient le START du segment.
  const seen = new Map<string, WhisperSegment & { szenen_nr: number }>();
  for (const s of szenen) {
    const raw = s.transkription_json;
    if (!Array.isArray(raw)) continue;
    for (const seg of raw) {
      if (!seg || typeof seg !== "object" || !("start" in seg) || !("end" in seg) || !("text" in seg)) continue;
      const w = seg as WhisperSegment;
      if (typeof w.start !== "number" || typeof w.end !== "number" || typeof w.text !== "string") continue;
      if (isHallucination(w.text)) continue;
      const key = `${w.start.toFixed(3)}_${w.end.toFixed(3)}`;
      const existing = seen.get(key);
      const belongsToThisScene = w.start >= s.start && w.start < s.end;
      // Priorité : la scène qui contient réellement le START du segment.
      if (!existing || belongsToThisScene) {
        seen.set(key, { ...w, szenen_nr: s.szenen_nr });
      }
    }
  }
  const segments = [...seen.values()].sort((a, b) => a.start - b.start);
  if (segments.length === 0) return <div style={{ color: "#888" }}>Keine Transkription vorhanden.</div>;
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
      {segments.map((seg, i) => (
        <div key={i} style={{ display: "flex", gap: 10, padding: "6px 8px", borderRadius: 6, borderLeft: "2px solid rgba(229,193,0,0.35)" }}>
          <span style={{ fontSize: 10, color: "#7a7a7a", fontFamily: "ui-monospace, monospace", flexShrink: 0, minWidth: 78, marginTop: 2 }}>
            {fmtSec(seg.start)}
            <span style={{ color: "#4a4a4a" }}> · </span>
            <span style={{ color: "#555" }}>S{seg.szenen_nr}</span>
          </span>
          <span style={{ fontSize: 13, color: "#e0e0e0", lineHeight: 1.5 }}>{seg.text.trim()}</span>
        </div>
      ))}
    </div>
  );
}

function BerichtTab({ clipId, pipeline }: { clipId: string; pipeline: Pipeline | null }) {
  const [synthese, setSynthese] = useState<Synthese | null>(null);
  const [cached, setCached] = useState<boolean>(false);
  const [loading, setLoading] = useState<boolean>(true);
  const [err, setErr] = useState<string | null>(null);

  const fetchSynthese = (refresh: boolean = false) => {
    setLoading(true);
    setErr(null);
    fetch(`${API}/api/clips/${clipId}/synthese${refresh ? "?refresh=true" : ""}`)
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`))))
      .then((d) => {
        setSynthese(d.synthese);
        setCached(!!d.cached);
      })
      .catch((e: Error) => setErr(e.message))
      .finally(() => setLoading(false));
  };

  useEffect(() => { fetchSynthese(false); /* eslint-disable-next-line react-hooks/exhaustive-deps */ }, [clipId]);

  const showPipelineDetails = pipeline && Object.keys(pipeline.schritt_history).length > 0;

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
      {loading && <div style={{ color: "#888", textAlign: "center", padding: 20 }}>Synthese wird von {synthese?.model ?? "LLM"} generiert…<br /><span style={{ fontSize: 10 }}>Erste Generierung : ~30–60s</span></div>}
      {err && <div style={{ color: "#e88", padding: 10, background: "rgba(255,120,120,0.08)", borderRadius: 6 }}>Fehler: {err}</div>}
      {synthese && !loading && (
        <>
          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
            <div style={{ fontSize: 10, color: "#666" }}>
              {cached ? "Cached · " : "Frisch generiert · "}
              Model : <code style={{ color: "#8a8a8a" }}>{synthese.model ?? "?"}</code>
              {synthese.generated_at && ` · ${new Date(synthese.generated_at).toLocaleString()}`}
            </div>
            <button
              onClick={() => fetchSynthese(true)}
              style={{ background: "rgba(229,193,0,0.12)", border: "1px solid rgba(229,193,0,0.3)", color: "#e5c100", borderRadius: 6, padding: "4px 10px", fontSize: 11, cursor: "pointer" }}
            >
              ↻ Neu generieren
            </button>
          </div>

          <div style={{ padding: "14px 16px", background: "linear-gradient(180deg, rgba(229,193,0,0.08) 0%, rgba(229,193,0,0) 100%)", borderRadius: 10, border: "1px solid rgba(229,193,0,0.25)" }}>
            <div style={{ fontSize: 10, color: "#e5c100", textTransform: "uppercase", letterSpacing: 0.5, marginBottom: 6 }}>Thema</div>
            <div style={{ fontSize: 15, color: "#f0f0f0", fontWeight: 500, lineHeight: 1.4 }}>{synthese.thema}</div>
          </div>

          <BerichtSection label="Narration" body={synthese.narration} />
          <BerichtSection label="Visueller Stil" body={synthese.visuell} />
          <BerichtSection label="Ambiance / Stimmung" body={synthese.ambiance} />

          {(() => {
            const anwesend = synthese.anwesende_personen ?? synthese.personen ?? [];
            const erwaehnt = synthese.erwaehnte_personen ?? [];
            return (
              <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: 10 }}>
                <div style={{ background: "#1c1c1e", borderRadius: 8, padding: "10px 12px", border: "1px solid rgba(255,255,255,0.05)" }}>
                  <div style={{ fontSize: 10, color: "#7a7a7a", textTransform: "uppercase", letterSpacing: 0.5, marginBottom: 4 }}>Genre / Format</div>
                  <div style={{ fontSize: 13, color: "#e0e0e0" }}>{synthese.genre || "—"}</div>
                </div>
                <div style={{ background: "#1c1c1e", borderRadius: 8, padding: "10px 12px", border: "1px solid rgba(120,200,120,0.15)" }}>
                  <div style={{ fontSize: 10, color: "#96d996", textTransform: "uppercase", letterSpacing: 0.5, marginBottom: 4 }} title="Personen, die tatsächlich im Video zu sehen/hören sind (diarisiert)">● Anwesend</div>
                  <div style={{ fontSize: 12, color: "#e0e0e0", lineHeight: 1.4 }}>{anwesend.length ? anwesend.join(", ") : "—"}</div>
                </div>
                <div style={{ background: "#1c1c1e", borderRadius: 8, padding: "10px 12px", border: "1px solid rgba(180,180,255,0.15)" }}>
                  <div style={{ fontSize: 10, color: "#9ac2ff", textTransform: "uppercase", letterSpacing: 0.5, marginBottom: 4 }} title="Personen, die im Dialog erwähnt werden aber NICHT im Video sind">○ Erwähnt</div>
                  <div style={{ fontSize: 12, color: "#e0e0e0", lineHeight: 1.4 }}>{erwaehnt.length ? erwaehnt.join(", ") : "—"}</div>
                </div>
              </div>
            );
          })()}

          {showPipelineDetails && (
            <details style={{ marginTop: 4, background: "#141415", borderRadius: 8, padding: "8px 12px", border: "1px solid rgba(255,255,255,0.04)" }}>
              <summary style={{ fontSize: 11, color: "#888", cursor: "pointer", padding: "4px 0" }}>Pipeline-Details anzeigen (technisch)</summary>
              <div style={{ display: "flex", flexDirection: "column", gap: 8, marginTop: 8 }}>
                {Object.entries(pipeline!.schritt_history).map(([step, data]) => (
                  <div key={step}>
                    <div style={{ fontSize: 11, color: "#e5c100", textTransform: "capitalize", marginBottom: 3 }}>{step}</div>
                    <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(170px, 1fr))", gap: 4, fontSize: 10 }}>
                      {Object.entries(data).map(([k, v]) => (
                        <div key={k} style={{ display: "flex", flexDirection: "column" }}>
                          <span style={{ color: "#6a6a6a" }}>{k}</span>
                          <span style={{ color: "#c0c0c0", fontFamily: "ui-monospace, monospace", wordBreak: "break-word" }}>{typeof v === "object" ? JSON.stringify(v).slice(0, 60) : String(v).slice(0, 60)}</span>
                        </div>
                      ))}
                    </div>
                  </div>
                ))}
              </div>
            </details>
          )}
        </>
      )}
    </div>
  );
}

function BerichtSection({ label, body }: { label: string; body: string }) {
  if (!body) return null;
  return (
    <div>
      <div style={{ fontSize: 10, color: "#7a7a7a", textTransform: "uppercase", letterSpacing: 0.5, marginBottom: 4 }}>{label}</div>
      <div style={{ fontSize: 13, color: "#e0e0e0", lineHeight: 1.6, background: "#1a1a1c", padding: "10px 12px", borderRadius: 6, borderLeft: "2px solid rgba(229,193,0,0.35)" }}>{body}</div>
    </div>
  );
}
