"use client";

import { useEffect, useState, use } from "react";
import { motion } from "framer-motion";
import Link from "next/link";
import {
  ArrowLeft, Play, Pause, Film, Loader2, AlertCircle,
  Clock, Users, Camera, MessageSquare, Sparkles, Download, Bot,
} from "lucide-react";
import { Dock } from "@/components/Dock";
import { AppSidebar } from "@/components/AppSidebar";
import { fetchClipDetails, fetchAnalyse, type ClipDTO, type AnalyseDTO, type SzeneDTO } from "@/lib/api";

/* ─── Helpers ────────────────────────────────────────────── */
function fmt(seconds: number | null | undefined): string {
  if (seconds == null || seconds < 0) return "—";
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60);
  return `${m}:${s.toString().padStart(2, "0")}`;
}

function statusLabel(s: string): string {
  const map: Record<string, string> = {
    analysiert: "Analysiert",
    fertig: "Fertig",
    laeuft: "Läuft…",
    wartend: "Wartend",
    hochgeladen: "Hochgeladen",
    fehler: "Fehler",
  };
  return map[s] || s;
}

function statusColor(s: string): string {
  if (s === "analysiert" || s === "fertig") return "var(--fx)";
  if (s === "laeuft" || s === "wartend") return "var(--gold)";
  if (s === "fehler") return "#ef4444";
  return "var(--t3)";
}

function framingLabel(f: string | null | undefined): string {
  const map: Record<string, string> = {
    extreme_closeup: "Extreme Nahaufnahme",
    closeup: "Nahaufnahme",
    medium: "Halbtotale",
    wide_with_person: "Totale (mit Person)",
    wide_no_person: "Totale (ohne Person)",
  };
  return f ? (map[f] || f) : "—";
}

/* ══════════════════════════════════════════════════════════
   PAGE
═════════════════════════════════════════════════════════ */
export default function ClipDetailPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const [clip, setClip] = useState<ClipDTO | null>(null);
  const [analyse, setAnalyse] = useState<AnalyseDTO | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selectedScene, setSelectedScene] = useState<SzeneDTO | null>(null);

  useEffect(() => {
    Promise.all([fetchClipDetails(id), fetchAnalyse(id).catch(() => null)])
      .then(([c, a]) => {
        setClip(c);
        setAnalyse(a);
        if (a?.szenen?.length) setSelectedScene(a.szenen[0]);
        setError(null);
      })
      .catch((e) => setError((e as Error).message))
      .finally(() => setLoading(false));
  }, [id]);

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
          <span style={{ fontSize: 13, color: "var(--t2)" }}>Clips</span>
          <span style={{ color: "var(--t4)", fontSize: 12 }}>/</span>
          <span style={{ fontSize: 13, color: "var(--t0)", fontWeight: 500 }}>
            {clip?.dateiname || (loading ? "Lädt…" : "Unbekannt")}
          </span>

          <div style={{ flex: 1 }} />

          {clip && (
            <>
              <div style={{
                display: "flex", alignItems: "center", gap: 6,
                padding: "3px 10px", borderRadius: 7,
                background: "var(--s2)", border: "1px solid var(--b1)",
              }}>
                <div style={{
                  width: 6, height: 6, borderRadius: "50%",
                  background: statusColor(clip.status),
                }} />
                <span style={{ fontSize: 11, fontWeight: 500, color: "var(--t1)" }}>
                  {statusLabel(clip.status)}
                </span>
              </div>
              <a href={`/agent?clip=${clip.id}`} style={{
                display: "flex", alignItems: "center", gap: 5,
                height: 28, padding: "0 12px", borderRadius: 9,
                background: "var(--gold-s)", border: "1px solid var(--gold-b)",
                color: "var(--gold)", fontSize: 12, fontWeight: 600,
                textDecoration: "none",
              }}>
                <Bot size={12} /> Mit Agent bearbeiten
              </a>
            </>
          )}
        </div>

        {/* Content */}
        <div style={{ flex: 1, overflow: "hidden", padding: 20 }}>
          {loading && (
            <div style={{
              height: "100%", display: "flex", alignItems: "center", justifyContent: "center",
              color: "var(--t3)",
            }}>
              <Loader2 size={16} className="animate-spin" style={{ marginRight: 8 }} />
              <span style={{ fontSize: 13 }}>Clip wird geladen…</span>
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
              <div style={{ fontSize: 13 }}>Fehler: {error}</div>
            </div>
          )}

          {!loading && !error && clip && (
            <div style={{
              display: "grid",
              gridTemplateColumns: "minmax(0, 1fr) 320px",
              gap: 16,
              height: "100%",
              minHeight: 0,
            }}>
              {/* Left: player + scenes */}
              <div style={{ display: "flex", flexDirection: "column", gap: 14, minHeight: 0 }}>
                {/* Player */}
                <ClipPlayer clip={clip} />

                {/* Scenes */}
                <div style={{
                  flex: 1, minHeight: 0,
                  background: "var(--bg-up)", border: "1px solid var(--b0)",
                  borderRadius: 12, padding: 14, display: "flex", flexDirection: "column",
                }}>
                  <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 10 }}>
                    <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                      <Film size={13} style={{ color: "var(--t2)" }} />
                      <span className="label">Szenen</span>
                      <span style={{
                        fontSize: 10, fontWeight: 600, padding: "1px 6px", borderRadius: 5,
                        background: "var(--s2)", border: "1px solid var(--b0)", color: "var(--t2)",
                        fontFamily: "var(--mono)",
                      }}>
                        {analyse?.szenen_anzahl ?? 0}
                      </span>
                    </div>
                    <span style={{ fontSize: 11, color: "var(--t3)" }}>
                      Dauer: {fmt(clip.dauer)}
                    </span>
                  </div>

                  {!analyse?.szenen?.length ? (
                    <div style={{
                      flex: 1, display: "flex", alignItems: "center", justifyContent: "center",
                      color: "var(--t3)", fontSize: 12,
                    }}>
                      {clip.status === "analysiert"
                        ? "Keine Szenen erkannt."
                        : "Analyse noch nicht abgeschlossen."}
                    </div>
                  ) : (
                    <div style={{
                      flex: 1, overflowY: "auto", display: "flex", flexDirection: "column", gap: 4,
                    }}>
                      {analyse.szenen.map((sz) => (
                        <motion.div
                          key={sz.szenen_nr}
                          onClick={() => setSelectedScene(sz)}
                          style={{
                            display: "flex", alignItems: "center", gap: 10,
                            padding: "8px 10px", borderRadius: 8,
                            background: selectedScene?.szenen_nr === sz.szenen_nr
                              ? "var(--gold-s)"
                              : "rgba(255,255,255,0.025)",
                            border: `1px solid ${
                              selectedScene?.szenen_nr === sz.szenen_nr
                                ? "var(--gold-b)"
                                : "rgba(255,255,255,0.06)"
                            }`,
                            cursor: "pointer",
                          }}
                          whileHover={{ borderColor: "rgba(255,255,255,0.14)" }}
                        >
                          <div style={{
                            width: 34, height: 22, borderRadius: 5,
                            background: "var(--s2)",
                            display: "grid", placeItems: "center",
                            fontFamily: "var(--mono)", fontSize: 10,
                            color: "var(--t1)",
                          }}>
                            {sz.szenen_nr}
                          </div>
                          <div style={{ flex: 1, minWidth: 0 }}>
                            <div style={{
                              fontSize: 12, color: "var(--t0)", fontWeight: 500,
                              overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
                            }}>
                              {sz.beschreibung || "(keine Beschreibung)"}
                            </div>
                            <div style={{ fontSize: 10, color: "var(--t3)", marginTop: 1 }}>
                              {fmt(sz.start_zeit)} · {sz.dauer.toFixed(1)}s
                              {sz.hat_embedding && " · CLIP"}
                            </div>
                          </div>
                        </motion.div>
                      ))}
                    </div>
                  )}
                </div>
              </div>

              {/* Right: scene detail + metadata */}
              <div style={{ display: "flex", flexDirection: "column", gap: 14, overflow: "hidden" }}>
                {/* Metadata */}
                <div style={{
                  background: "var(--bg-up)", border: "1px solid var(--b0)",
                  borderRadius: 12, padding: 14,
                }}>
                  <div className="label" style={{ marginBottom: 10 }}>METADATEN</div>
                  <MetaRow icon={<Clock size={12} />} label="Dauer" value={fmt(clip.dauer)} />
                  <MetaRow icon={<Camera size={12} />} label="Auflösung" value={clip.aufloesung || "—"} />
                  <MetaRow icon={<Film size={12} />} label="Bildrate" value={clip.bildrate ? `${clip.bildrate.toFixed(2)} fps` : "—"} />
                  <MetaRow icon={<Sparkles size={12} />} label="Quelle" value={clip.quelle} />
                  <MetaRow icon={<Download size={12} />} label="Größe" value={clip.dateigroesse_mb ? `${clip.dateigroesse_mb} MB` : "—"} />
                </div>

                {/* Scene detail */}
                {selectedScene && (
                  <div style={{
                    flex: 1, minHeight: 0, overflow: "hidden",
                    background: "var(--bg-up)", border: "1px solid var(--b0)",
                    borderRadius: 12, padding: 14,
                    display: "flex", flexDirection: "column",
                  }}>
                    <div className="label" style={{ marginBottom: 10 }}>
                      SZENE {selectedScene.szenen_nr}
                    </div>
                    <div style={{ flex: 1, overflowY: "auto", fontSize: 12, color: "var(--t1)" }}>
                      {selectedScene.beschreibung && (
                        <>
                          <div style={{ fontSize: 10, color: "var(--t3)", marginBottom: 4 }}>BESCHREIBUNG</div>
                          <div style={{ marginBottom: 12, lineHeight: 1.5 }}>{selectedScene.beschreibung}</div>
                        </>
                      )}
                      {selectedScene.transkription && (
                        <>
                          <div style={{ fontSize: 10, color: "var(--t3)", marginBottom: 4, display: "flex", alignItems: "center", gap: 5 }}>
                            <MessageSquare size={10} /> TRANSKRIPTION
                          </div>
                          <div style={{
                            marginBottom: 12, lineHeight: 1.5,
                            padding: 8, background: "var(--s2)", borderRadius: 6,
                            fontStyle: "italic",
                          }}>
                            &ldquo;{selectedScene.transkription}&rdquo;
                          </div>
                        </>
                      )}
                      <MetaRow icon={<Clock size={11} />} label="Beginn" value={fmt(selectedScene.start_zeit)} />
                      <MetaRow icon={<Clock size={11} />} label="Ende" value={fmt(selectedScene.end_zeit)} />
                      <MetaRow icon={<Clock size={11} />} label="Dauer" value={`${selectedScene.dauer.toFixed(2)}s`} />
                      <MetaRow icon={<Sparkles size={11} />} label="Embedding" value={selectedScene.hat_embedding ? "✓ CLIP" : "—"} />
                    </div>
                  </div>
                )}
              </div>
            </div>
          )}
        </div>
      </div>

      <Dock />
    </div>
  );
}

/* ─── Sub-components ────────────────────────────────────── */
function MetaRow({ icon, label, value }: { icon: React.ReactNode; label: string; value: string }) {
  return (
    <div style={{
      display: "flex", alignItems: "center", justifyContent: "space-between",
      padding: "5px 0", fontSize: 12,
    }}>
      <div style={{ display: "flex", alignItems: "center", gap: 6, color: "var(--t2)" }}>
        {icon}
        <span>{label}</span>
      </div>
      <span style={{ color: "var(--t0)", fontFamily: "var(--mono)", fontSize: 11 }}>{value}</span>
    </div>
  );
}

function ClipPlayer({ clip }: { clip: ClipDTO }) {
  const [videoRef, setVideoRef] = useState<HTMLVideoElement | null>(null);

  const apiBase = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8001";
  const absolutize = (u: string | null) =>
    u ? (u.startsWith("http") ? u : `${apiBase}${u}`) : null;

  const fullVideoUrl = absolutize(clip.proxy_url || clip.video_url);
  const stripUrl = absolutize(clip.strip_url);
  const waveformUrl = absolutize(clip.waveform_url);

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
      <div style={{
        background: "#000", borderRadius: 12, overflow: "hidden",
        border: "1px solid var(--b0)",
        aspectRatio: "16 / 9", position: "relative",
      }}>
        {fullVideoUrl ? (
          <video
            ref={setVideoRef}
            src={fullVideoUrl}
            style={{ width: "100%", height: "100%", objectFit: "contain" }}
            controls
          />
        ) : (
          <div style={{
            height: "100%", display: "flex", alignItems: "center", justifyContent: "center",
            color: "var(--t3)", fontSize: 12,
          }}>
            Kein Vorschauvideo verfügbar.
          </div>
        )}
      </div>

      {/* Thumbnail strip (24 tiles) */}
      {stripUrl && (
        <div
          title="Thumbnail-Strip"
          style={{
            height: 40,
            borderRadius: 8,
            border: "1px solid var(--b0)",
            backgroundImage: `url(${stripUrl})`,
            backgroundSize: "cover",
            backgroundPosition: "center",
          }}
        />
      )}

      {/* Waveform */}
      {waveformUrl && (
        <div
          title="Wellenform"
          style={{
            height: 60,
            borderRadius: 8,
            border: "1px solid var(--b0)",
            background: "var(--bg-up)",
            padding: 4,
            position: "relative",
          }}
        >
          <div
            style={{
              width: "100%", height: "100%",
              backgroundImage: `url(${waveformUrl})`,
              backgroundSize: "100% 100%",
              backgroundRepeat: "no-repeat",
              filter: "invert(1) brightness(0.9)",
            }}
          />
        </div>
      )}
    </div>
  );
}
