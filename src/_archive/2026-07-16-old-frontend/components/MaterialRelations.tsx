"use client";

/**
 * CinAssist — MaterialRelations
 *
 * Untersucht paarweise die Beziehung zwischen den hochgeladenen Clips:
 *   • Visuelle Ähnlichkeit (mittlere maximale CLIP-Cosine)
 *   • Audio-Ähnlichkeit (Chroma-CQT-Korrelation via librosa)
 *   • Zeitlicher Offset (für Multicam-Sync)
 *   • Klassifikation: multicam / related / different
 *
 * Hilft dem System, Multicam-Material zu erkennen — das war eine
 * berechtigte Kritik am ursprünglichen Pipeline-Design.
 */

import React, { useState, useEffect, useCallback } from "react";

const API = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8001";

interface RelationPair {
  a_id: string;
  b_id: string;
  a_name: string;
  b_name: string;
  visual_sim: number;
  audio_sim: number;
  audio_offset_s: number;
  classification: "multicam" | "related" | "different";
}

interface RelationsResponse {
  pairs: RelationPair[];
  multicam_groups: string[][];
  n: number;
  schwellen: { multicam: string; related: string };
}

function classColor(c: RelationPair["classification"]): string {
  if (c === "multicam") return "var(--green)";
  if (c === "related")  return "var(--orange)";
  return "var(--text3)";
}

function classLabel(c: RelationPair["classification"]): string {
  if (c === "multicam") return "Multicam";
  if (c === "related")  return "Verwandt";
  return "Verschieden";
}

export function MaterialRelations({ onClose, clipIds }: { onClose: () => void; clipIds: string[] }) {
  const [data, setData] = useState<RelationsResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const fetchRelations = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const r = await fetch(`${API}/api/ai/multicam`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ clip_ids: clipIds }),
      });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      setData(await r.json());
    } catch (err) {
      setError(err instanceof Error ? err.message : "Beziehungs-Analyse fehlgeschlagen");
    } finally {
      setLoading(false);
    }
  }, [clipIds]);

  useEffect(() => { fetchRelations(); }, [fetchRelations]);

  return (
    <div
      onClick={onClose}
      style={{
        position: "fixed", inset: 0, background: "rgba(0,0,0,.78)", backdropFilter: "blur(8px)",
        display: "flex", alignItems: "center", justifyContent: "center",
        zIndex: 500, padding: 20,
      }}
    >
      <div
        onClick={e => e.stopPropagation()}
        style={{
          background: "var(--bg1)", border: "1px solid var(--border2)",
          borderRadius: 10, width: "100%", maxWidth: 760,
          maxHeight: "88vh", overflow: "hidden",
          boxShadow: "0 20px 60px rgba(0,0,0,.6)",
          display: "flex", flexDirection: "column",
        }}
      >
        {/* Header */}
        <div style={{
          padding: "12px 18px", borderBottom: "1px solid var(--border)",
          display: "flex", alignItems: "center", gap: 10,
          background: "var(--bg2)", flexShrink: 0,
        }}>
          <div>
            <div style={{ fontSize: 13, fontWeight: 700, color: "var(--text)" }}>
              Material-Beziehungen · Multicam-Analyse
            </div>
            <div style={{ fontSize: 10, color: "var(--text3)", marginTop: 2 }}>
              Visuelle CLIP-Ähnlichkeit × Audio-Chroma-Korrelation (librosa)
            </div>
          </div>
          <button
            onClick={onClose}
            style={{
              marginLeft: "auto", background: "none", border: "none",
              color: "var(--text2)", cursor: "pointer", fontSize: 16, padding: "0 4px",
            }}
          >✕</button>
        </div>

        <div style={{ flex: 1, overflowY: "auto", padding: 18 }}>
          {loading && (
            <div style={{ textAlign: "center", padding: 60, color: "var(--text3)", fontSize: 11 }}>
              Vergleiche Audio (chroma_cqt) und Bild (CLIP) für jedes Clip-Paar…<br/>
              <span style={{ fontSize: 9, marginTop: 6, display: "inline-block" }}>
                ca. 2-4 Sekunden pro Paar
              </span>
            </div>
          )}
          {error && (
            <div style={{ textAlign: "center", padding: 60, color: "var(--red)", fontSize: 11 }}>
              ❌ {error}
            </div>
          )}
          {data && !loading && (
            <>
              {/* Multicam-Gruppen Banner */}
              {data.multicam_groups.length > 0 && (
                <div style={{
                  marginBottom: 14, padding: "10px 12px",
                  background: "rgba(34,197,94,.10)",
                  border: "1px solid rgba(34,197,94,.35)",
                  borderRadius: 6,
                }}>
                  <div style={{ fontSize: 10, fontWeight: 700, color: "var(--green)", letterSpacing: ".05em", textTransform: "uppercase", marginBottom: 4 }}>
                    ✓ Multicam-Gruppe erkannt
                  </div>
                  {data.multicam_groups.map((g, gi) => {
                    const names = g.map(id => {
                      const pair = data.pairs.find(p => p.a_id === id || p.b_id === id);
                      return pair ? (pair.a_id === id ? pair.a_name : pair.b_name) : id.slice(0, 8);
                    });
                    return (
                      <div key={gi} style={{ fontSize: 11, color: "var(--text)", lineHeight: 1.5 }}>
                        Gruppe {gi + 1}: {names.join(" · ")} — wahrscheinlich dieselbe Szene aus verschiedenen Winkeln
                      </div>
                    );
                  })}
                </div>
              )}

              {data.multicam_groups.length === 0 && (
                <div style={{
                  marginBottom: 14, padding: "10px 12px",
                  background: "rgba(255,255,255,.025)",
                  border: "1px solid var(--border)",
                  borderRadius: 6, fontSize: 11, color: "var(--text2)",
                }}>
                  Keine Multicam-Gruppe oberhalb der Schwellen gefunden — die Clips
                  scheinen verschiedene Szenen zu sein. Falls du weißt, dass es dieselbe
                  Performance ist, prüfe die Werte unten: meist liegt das Problem an
                  unterschiedlicher Audio-Qualität oder einer langen zeitlichen Lücke.
                </div>
              )}

              {/* Pair-Tabelle */}
              <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                {data.pairs.map(p => (
                  <div key={`${p.a_id}-${p.b_id}`} style={{
                    padding: 12, background: "var(--bg2)",
                    border: `1px solid ${p.classification === "multicam" ? "rgba(34,197,94,.35)" : "var(--border)"}`,
                    borderRadius: 6,
                  }}>
                    {/* Header der Karte */}
                    <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 8 }}>
                      <span style={{ fontFamily: "var(--mono)", fontSize: 11, color: "var(--text)" }}>
                        {p.a_name} <span style={{ color: "var(--text3)" }}>↔</span> {p.b_name}
                      </span>
                      <span style={{
                        marginLeft: "auto", padding: "2px 8px", borderRadius: 3,
                        fontSize: 10, fontWeight: 700,
                        color: classColor(p.classification),
                        border: `1px solid ${classColor(p.classification)}`,
                        background: `${classColor(p.classification)}1A`,
                      }}>
                        {classLabel(p.classification)}
                      </span>
                    </div>

                    {/* Bars: visuell + audio */}
                    <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                      <Bar label="Visuell (CLIP)" value={p.visual_sim} hint="mittlere max. Cosine über die Szenen-Embeddings beider Clips" color="#a855f7" />
                      <Bar label="Audio (Chroma)" value={p.audio_sim} hint="Chroma-CQT-Cosine: 1.0 = identische harmonische Struktur" color="#22c55e" />
                    </div>

                    {/* Audio-Offset */}
                    {Math.abs(p.audio_offset_s) > 0.05 && (
                      <div style={{
                        marginTop: 8, padding: "6px 9px",
                        background: "rgba(255,255,255,.025)", borderRadius: 4,
                        fontSize: 10, color: "var(--text2)", fontFamily: "var(--mono)",
                      }}>
                        ⏱ Zeitlicher Offset für Sync: {p.audio_offset_s > 0 ? "+" : ""}{p.audio_offset_s.toFixed(2)} s
                        <span style={{ color: "var(--text3)", marginLeft: 6, fontFamily: "var(--font)", fontStyle: "italic" }}>
                          (Cross-Korrelation der Chroma-Sequenz)
                        </span>
                      </div>
                    )}
                  </div>
                ))}
              </div>

              {/* Schwellen-Box */}
              <div style={{
                marginTop: 16, padding: "10px 12px",
                background: "rgba(255,255,255,.02)", border: "1px solid var(--border)",
                borderRadius: 5, fontSize: 10, color: "var(--text3)", lineHeight: 1.55,
              }}>
                <strong style={{ color: "var(--text2)" }}>Klassifikations-Schwellen (empirisch):</strong><br/>
                <span style={{ fontFamily: "var(--mono)" }}>
                  multicam  ⇐ {data.schwellen.multicam}<br/>
                  related   ⇐ {data.schwellen.related}<br/>
                  different ⇐ sonst
                </span><br/>
                <span style={{ fontStyle: "italic" }}>
                  Die Schwellen wurden anhand der Werte beim BYAM-Multicam-Material kalibriert. Eine formale
                  Validierung würde n &gt; 20 unabhängige Sessions erfordern — das ist im Bachelor-Rahmen
                  als Ausblick dokumentiert.
                </span>
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  );
}

function Bar({ label, value, hint, color }: { label: string; value: number; hint: string; color: string }) {
  const pct = Math.max(0, Math.min(1, value)) * 100;
  return (
    <div title={hint} style={{ display: "flex", alignItems: "center", gap: 8, cursor: "help" }}>
      <span style={{ fontSize: 10, color: "var(--text3)", width: 110 }}>{label}</span>
      <div style={{ flex: 1, height: 7, background: "rgba(255,255,255,.06)", borderRadius: 3, overflow: "hidden" }}>
        <div style={{ height: "100%", width: `${pct}%`, background: color, transition: "width .35s" }} />
      </div>
      <span style={{ fontFamily: "var(--mono)", fontSize: 10.5, fontWeight: 600, color: "var(--text)", minWidth: 44, textAlign: "right" }}>
        {(value * 100).toFixed(1)}%
      </span>
    </div>
  );
}
