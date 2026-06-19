"use client";

/**
 * CinAssist — MaterialAtlas
 *
 * Visualisiert den 512-dim CLIP-Repräsentationsraum aller analysierten Szenen
 * als 2D-Scatterplot (PCA-Projektion, server-side berechnet).
 *
 * Was es zeigt:
 *   • Jede Szene = 1 Punkt, gefärbt nach Quelldatei
 *   • Visuell ähnliche Szenen = nahe beieinander
 *   • Wird ein Prompt eingegeben, erscheint dieser ebenfalls als Stern im
 *     gleichen Raum + die Top-K relevantesten Szenen leuchten auf.
 *
 * Wofür?
 *   Beweis, dass der semantische Raum tatsächlich gelernt ist:
 *   Cluster sind sichtbar, Prompt-Nähe = Selektions-Relevanz.
 *   Macht "Zero-Shot Retrieval" überprüfbar statt geglaubt.
 */

import React, { useState, useEffect, useCallback, useRef } from "react";

const API = "http://localhost:8001";

interface AtlasScene {
  id: string;
  clip_id: string;
  clip_dateiname: string;
  szenen_nr: number;
  start: number;
  end: number;
  dauer: number;
  beschreibung: string | null;
  transkription: string;
  thumbnail_url: string | null;
  x: number;
  y: number;
}

interface AtlasPrompt {
  text: string;
  x: number;
  y: number;
  top_k: { scene_id: string; sim: number }[];
}

interface AtlasResponse {
  scenes: AtlasScene[];
  prompt: AtlasPrompt | null;
  variance_explained: [number, number];
  n: number;
  fehler?: string;
}

// Farbpalette pro Quelldatei (max 12 unterschiedliche Clips)
const CLIP_COLORS = [
  "#f97316", "#3b82f6", "#a855f7", "#22c55e",
  "#eab308", "#ef4444", "#06b6d4", "#ec4899",
  "#84cc16", "#f59e0b", "#8b5cf6", "#14b8a6",
];

export function MaterialAtlas({ onClose, clipIds }: { onClose: () => void; clipIds?: string[] }) {
  const [data, setData] = useState<AtlasResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [prompt, setPrompt] = useState("");
  const [hoveredScene, setHoveredScene] = useState<AtlasScene | null>(null);
  const svgRef = useRef<SVGSVGElement>(null);

  // Initial load
  const fetchAtlas = useCallback(async (promptText?: string) => {
    setLoading(true);
    setError(null);
    try {
      const r = await fetch(`${API}/api/ai/atlas`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          clip_ids: clipIds && clipIds.length > 0 ? clipIds : null,
          prompt: promptText || null,
        }),
      });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const json: AtlasResponse = await r.json();
      setData(json);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Atlas konnte nicht geladen werden");
    } finally {
      setLoading(false);
    }
  }, [clipIds]);

  useEffect(() => {
    fetchAtlas();
  }, [fetchAtlas]);

  const handlePromptSubmit = useCallback(() => {
    if (prompt.trim()) fetchAtlas(prompt.trim());
  }, [prompt, fetchAtlas]);

  // Build a clip→color map
  const clipColorMap = new Map<string, string>();
  if (data) {
    let i = 0;
    for (const s of data.scenes) {
      if (!clipColorMap.has(s.clip_id)) {
        clipColorMap.set(s.clip_id, CLIP_COLORS[i % CLIP_COLORS.length]);
        i++;
      }
    }
  }

  // Plot-Geometrie: SVG viewBox [-1.15, -1.15] → [1.15, 1.15]
  // Punkte sind in [-1, 1] normalisiert (Backend skaliert)
  const PLOT_W = 720;
  const PLOT_H = 460;
  const M = 1.15;
  const toPx = (x: number, y: number) => ({
    px: ((x + M) / (2 * M)) * PLOT_W,
    py: ((-y + M) / (2 * M)) * PLOT_H,  // y invertieren (SVG ↓)
  });

  const topKIds = new Set(data?.prompt?.top_k.map(k => k.scene_id) ?? []);

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
          borderRadius: 10, width: "100%", maxWidth: 880,
          maxHeight: "90vh", overflow: "hidden",
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
              Material-Atlas · CLIP-Embedding-Raum
            </div>
            <div style={{ fontSize: 10, color: "var(--text3)", marginTop: 2 }}>
              PCA-Projektion der 512-dim Szenen-Embeddings auf 2D
              {data && !error && ` · ${data.n} Szenen · erklärte Varianz: ${(data.variance_explained[0] * 100).toFixed(1)} % + ${(data.variance_explained[1] * 100).toFixed(1)} %`}
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

        {/* Prompt input */}
        <div style={{
          padding: "10px 18px", borderBottom: "1px solid var(--border)",
          display: "flex", gap: 8, alignItems: "center", flexShrink: 0,
        }}>
          <input
            type="text"
            value={prompt}
            onChange={e => setPrompt(e.target.value)}
            onKeyDown={e => { if (e.key === "Enter") handlePromptSubmit(); }}
            placeholder="Prompt eingeben — z.B. 'intimate close-up of a singer' …"
            style={{
              flex: 1, padding: "7px 10px", borderRadius: 5,
              background: "var(--bg3)", border: "1px solid var(--border)",
              color: "var(--text)", fontSize: 11, fontFamily: "var(--font)",
            }}
          />
          <button
            onClick={handlePromptSubmit}
            disabled={!prompt.trim() || loading}
            style={{
              padding: "7px 14px",
              background: prompt.trim() && !loading ? "var(--orange)" : "var(--bg4)",
              color: "white", border: "none", borderRadius: 5,
              fontSize: 11, fontWeight: 600, cursor: prompt.trim() && !loading ? "pointer" : "not-allowed",
              fontFamily: "var(--font)",
            }}
          >Projizieren</button>
          {data?.prompt && (
            <button
              onClick={() => { setPrompt(""); fetchAtlas(); }}
              style={{
                padding: "7px 10px", background: "none", color: "var(--text3)",
                border: "1px solid var(--border)", borderRadius: 5, fontSize: 10,
                cursor: "pointer", fontFamily: "var(--font)",
              }}
            >Zurücksetzen</button>
          )}
        </div>

        {/* Plot */}
        <div style={{ flex: 1, overflow: "auto", padding: 18, position: "relative" }}>
          {loading && (
            <div style={{ textAlign: "center", padding: 60, color: "var(--text3)", fontSize: 11 }}>
              Berechne PCA…
            </div>
          )}
          {error && (
            <div style={{ textAlign: "center", padding: 60, color: "var(--red)", fontSize: 11 }}>
              ❌ {error}
            </div>
          )}
          {data?.fehler && (
            <div style={{ textAlign: "center", padding: 60, color: "var(--text3)", fontSize: 11 }}>
              {data.fehler}
            </div>
          )}
          {data && !loading && !data.fehler && (
            <>
              <svg
                ref={svgRef}
                width={PLOT_W} height={PLOT_H}
                viewBox={`0 0 ${PLOT_W} ${PLOT_H}`}
                style={{
                  background: "var(--bg2)", border: "1px solid var(--border)",
                  borderRadius: 6, display: "block", margin: "0 auto",
                }}
              >
                {/* Axes */}
                <line x1={0} y1={PLOT_H / 2} x2={PLOT_W} y2={PLOT_H / 2} stroke="var(--border)" strokeDasharray="3,3" />
                <line x1={PLOT_W / 2} y1={0} x2={PLOT_W / 2} y2={PLOT_H} stroke="var(--border)" strokeDasharray="3,3" />

                {/* Axis labels */}
                <text x={PLOT_W - 8} y={PLOT_H / 2 - 5} fill="var(--text3)" fontSize={10} textAnchor="end" fontFamily="monospace">PC1 ({(data.variance_explained[0] * 100).toFixed(1)}%)</text>
                <text x={PLOT_W / 2 + 6} y={12} fill="var(--text3)" fontSize={10} fontFamily="monospace">PC2 ({(data.variance_explained[1] * 100).toFixed(1)}%)</text>

                {/* Scenes */}
                {data.scenes.map(s => {
                  const { px, py } = toPx(s.x, s.y);
                  const isTopK = topKIds.has(s.id);
                  const color = clipColorMap.get(s.clip_id) ?? "#888";
                  const r = isTopK ? 7 : 4.5;
                  return (
                    <g key={s.id}
                       onMouseEnter={() => setHoveredScene(s)}
                       onMouseLeave={() => setHoveredScene(null)}
                       style={{ cursor: "pointer" }}>
                      {isTopK && (
                        <circle cx={px} cy={py} r={r + 4} fill="none" stroke={color} strokeWidth={1} opacity={0.5} />
                      )}
                      <circle
                        cx={px} cy={py} r={r}
                        fill={color}
                        opacity={isTopK ? 1 : 0.75}
                        stroke={isTopK ? "white" : "rgba(255,255,255,.15)"}
                        strokeWidth={isTopK ? 1.5 : 0.5}
                      />
                    </g>
                  );
                })}

                {/* Prompt marker */}
                {data.prompt && (() => {
                  const { px, py } = toPx(data.prompt.x, data.prompt.y);
                  return (
                    <g>
                      <circle cx={px} cy={py} r={14} fill="none" stroke="white" strokeWidth={1.5} opacity={0.5} />
                      <circle cx={px} cy={py} r={9} fill="none" stroke="white" strokeWidth={1.5} opacity={0.8} />
                      <polygon
                        points={`${px},${py - 7} ${px + 2},${py - 2} ${px + 7},${py - 2} ${px + 3},${py + 1} ${px + 5},${py + 7} ${px},${py + 3} ${px - 5},${py + 7} ${px - 3},${py + 1} ${px - 7},${py - 2} ${px - 2},${py - 2}`}
                        fill="white"
                      />
                      <text x={px + 12} y={py - 8} fill="white" fontSize={11} fontWeight={700} fontFamily="monospace">
                        prompt
                      </text>
                    </g>
                  );
                })()}

                {/* Hover tooltip */}
                {hoveredScene && (() => {
                  const { px, py } = toPx(hoveredScene.x, hoveredScene.y);
                  const tipX = Math.min(px + 12, PLOT_W - 220);
                  const tipY = Math.max(py - 60, 10);
                  return (
                    <g pointerEvents="none">
                      <rect x={tipX} y={tipY} width={210} height={50}
                            fill="rgba(20,20,20,.95)" stroke="var(--border2)" rx={4} />
                      <text x={tipX + 8} y={tipY + 14} fill="var(--text)" fontSize={10} fontWeight={700}>
                        {hoveredScene.clip_dateiname.length > 24 ? hoveredScene.clip_dateiname.slice(0, 22) + "…" : hoveredScene.clip_dateiname}
                      </text>
                      <text x={tipX + 8} y={tipY + 28} fill="var(--text3)" fontSize={9} fontFamily="monospace">
                        Szene {hoveredScene.szenen_nr} · {hoveredScene.start.toFixed(1)}–{hoveredScene.end.toFixed(1)}s
                      </text>
                      {hoveredScene.beschreibung && (
                        <text x={tipX + 8} y={tipY + 42} fill="var(--text2)" fontSize={9}>
                          {hoveredScene.beschreibung.length > 30 ? hoveredScene.beschreibung.slice(0, 28) + "…" : hoveredScene.beschreibung}
                        </text>
                      )}
                    </g>
                  );
                })()}
              </svg>

              {/* Legend */}
              <div style={{
                marginTop: 12, display: "flex", flexWrap: "wrap", gap: 12,
                justifyContent: "center", fontSize: 10, color: "var(--text2)",
              }}>
                {Array.from(clipColorMap.entries()).map(([cid, color]) => {
                  const scene = data.scenes.find(s => s.clip_id === cid);
                  return (
                    <div key={cid} style={{ display: "flex", alignItems: "center", gap: 5 }}>
                      <div style={{ width: 10, height: 10, borderRadius: "50%", background: color }} />
                      <span style={{ fontFamily: "monospace" }}>{scene?.clip_dateiname || cid.slice(0, 8)}</span>
                    </div>
                  );
                })}
              </div>

              {/* Top-K table */}
              {data.prompt && data.prompt.top_k.length > 0 && (
                <div style={{ marginTop: 14, padding: 12, background: "var(--bg2)", border: "1px solid var(--border)", borderRadius: 6 }}>
                  <div style={{ fontSize: 10, fontWeight: 700, color: "var(--orange)", letterSpacing: ".05em", textTransform: "uppercase", marginBottom: 6 }}>
                    Top-8 relevanteste Szenen für „{data.prompt.text}“
                  </div>
                  <div style={{ display: "flex", flexDirection: "column", gap: 3 }}>
                    {data.prompt.top_k.map((tk, i) => {
                      const sc = data.scenes.find(s => s.id === tk.scene_id);
                      if (!sc) return null;
                      return (
                        <div key={tk.scene_id} style={{
                          display: "flex", alignItems: "center", gap: 8,
                          fontSize: 10.5, color: "var(--text2)",
                          padding: "3px 0", borderBottom: i < 7 ? "1px solid var(--border)" : "none",
                        }}>
                          <span style={{ width: 16, color: "var(--text3)", fontFamily: "monospace" }}>#{i + 1}</span>
                          <div style={{ width: 10, height: 10, borderRadius: "50%", background: clipColorMap.get(sc.clip_id), flexShrink: 0 }} />
                          <span style={{ flex: 1, fontFamily: "monospace", fontSize: 10 }}>
                            {sc.clip_dateiname.slice(0, 22)} · Szene {sc.szenen_nr}
                          </span>
                          <span style={{ fontFamily: "monospace", fontWeight: 600, color: "var(--green)" }}>
                            {(tk.sim * 100).toFixed(1)}%
                          </span>
                        </div>
                      );
                    })}
                  </div>
                </div>
              )}

              {/* Pédagogie */}
              <div style={{
                marginTop: 12, padding: "9px 12px", fontSize: 10,
                color: "var(--text3)", lineHeight: 1.55,
                background: "rgba(255,255,255,.02)", borderRadius: 5,
                border: "1px solid var(--border)",
              }}>
                <strong style={{ color: "var(--text2)" }}>Wie wird das gerechnet?</strong><br/>
                Jede Szene besitzt ein 512-dim CLIP-Embedding (ViT-B/32). Die PCA-Zerlegung
                via SVD findet die zwei Richtungen mit maximaler Varianz im Datensatz. Die
                erklärte Varianz oben gibt an, wie viel Information in der 2D-Projektion
                erhalten bleibt. Ein Prompt wird mit dem gleichen CLIP-Text-Encoder kodiert
                und in dieselbe Basis projiziert — räumliche Nähe = semantische Nähe.
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
