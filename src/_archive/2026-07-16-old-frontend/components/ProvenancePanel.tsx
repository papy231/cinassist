"use client";

/**
 * ProvenancePanel — "Warum dieses Segment?"
 *
 * Zeigt die Herkunft eines vom KI-Selektionssystem gewählten Timeline-Clips:
 * LLaVA-Beschreibung, Whisper-Transkript, CLIP-Prompt-Relevanz, Energie,
 * Interessantheit, Rolle in der Erzählung. Macht die Selektion transparent —
 * kein Black-Box-Resultat.
 *
 * Extrahiert aus editor/page.tsx (Zeilen 2450–2559) für bessere Wartbarkeit.
 */

import React from "react";
import type { TLClip } from "@/stores/editorStore";
import type { ClipDTO } from "@/lib/api";

type Props = {
  selectedClip: TLClip;
  sourceClip: ClipDTO | null;
  onClose: () => void;
};

export function ProvenancePanel({ selectedClip: sel, sourceClip, onClose }: Props) {
  return (
    <div
      style={{
        position: "fixed", right: 12, bottom: 44, zIndex: 90,
        width: 320, maxHeight: "55vh", overflowY: "auto",
        background: "var(--bg2)", border: "1px solid var(--border2)",
        borderRadius: 8, boxShadow: "0 12px 36px rgba(0,0,0,.55)",
        fontFamily: "var(--font)",
      }}
    >
      <div style={{
        display: "flex", alignItems: "center", gap: 6,
        padding: "8px 11px", borderBottom: "1px solid var(--border)",
        background: "var(--bg3)",
      }}>
        <div style={{ width: 6, height: 6, borderRadius: "50%", background: "var(--green)" }} />
        <span style={{
          fontSize: 10, fontWeight: 700, color: "var(--text)",
          letterSpacing: ".04em", textTransform: "uppercase",
        }}>
          Warum dieses Segment?
        </span>
        <button
          onClick={onClose}
          style={{
            marginLeft: "auto", background: "none", border: "none",
            color: "var(--text3)", cursor: "pointer", fontSize: 13,
            padding: 0, lineHeight: 1,
          }}
        >
          ✕
        </button>
      </div>

      <div style={{ padding: "10px 12px", fontSize: 11, color: "var(--text2)", lineHeight: 1.5 }}>
        {/* Identität */}
        <div style={{ marginBottom: 8 }}>
          <div style={{ fontSize: 10, color: "var(--text3)" }}>Quelle</div>
          <div style={{ fontSize: 11, color: "var(--text)", fontWeight: 600 }}>
            {sourceClip?.dateiname ?? "—"}
            {sel.szeneNr !== undefined ? ` · Szene ${sel.szeneNr}` : ""}
          </div>
          <div style={{
            fontSize: 10, color: "var(--text3)",
            fontFamily: "var(--mono)", marginTop: 2,
          }}>
            {sel.mediaStart.toFixed(2)}s → {(sel.mediaStart + sel.dauer).toFixed(2)}s · Dauer {sel.dauer.toFixed(2)}s
          </div>
        </div>

        {/* CLIP-Prompt-Relevanz */}
        {sel.promptRelevance !== null && sel.promptRelevance !== undefined && (
          <div style={{
            marginBottom: 10, padding: "7px 9px",
            background: "rgba(34,197,94,.08)",
            border: "1px solid rgba(34,197,94,.25)",
            borderRadius: 5,
          }}>
            <div style={{
              fontSize: 9.5, fontWeight: 700, color: "var(--green)",
              letterSpacing: ".05em", textTransform: "uppercase", marginBottom: 3,
            }}>
              CLIP-Prompt-Relevanz
            </div>
            <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
              <div style={{
                flex: 1, height: 5, background: "rgba(255,255,255,.08)",
                borderRadius: 2, overflow: "hidden",
              }}>
                <div style={{
                  width: `${(sel.promptRelevance * 100).toFixed(0)}%`,
                  height: "100%", background: "var(--green)",
                }} />
              </div>
              <span style={{
                fontFamily: "var(--mono)", fontSize: 11,
                color: "var(--text)", fontWeight: 600,
              }}>
                {(sel.promptRelevance * 100).toFixed(0)}%
              </span>
            </div>
            <div style={{
              fontSize: 9.5, color: "var(--text3)", marginTop: 3,
              fontStyle: "italic",
            }}>
              cosine sim(prompt, scene) in CLIP-512-dim Raum
            </div>
          </div>
        )}

        {/* Sekundär-Scores */}
        {(sel.energie !== null || sel.interessantheit !== null) && (
          <div style={{ display: "flex", gap: 8, marginBottom: 10 }}>
            {sel.energie !== null && sel.energie !== undefined && (
              <MiniStat label="Energie" value={`${(sel.energie * 100).toFixed(0)}%`} />
            )}
            {sel.interessantheit !== null && sel.interessantheit !== undefined && (
              <MiniStat label="Interessant" value={`${(sel.interessantheit * 100).toFixed(0)}%`} />
            )}
            {sel.rolle && <MiniStat label="Rolle" value={sel.rolle} />}
          </div>
        )}

        {/* LLaVA-Visualbeschreibung */}
        {sel.beschreibung && (
          <div style={{ marginBottom: 9 }}>
            <div style={{
              fontSize: 9.5, fontWeight: 700, color: "var(--orange)",
              letterSpacing: ".05em", textTransform: "uppercase", marginBottom: 3,
            }}>
              LLaVA · Visualbeschreibung
            </div>
            <div style={{
              fontSize: 10.5, color: "var(--text)", lineHeight: 1.45,
              fontStyle: "italic",
            }}>
              {sel.beschreibung}
            </div>
          </div>
        )}

        {/* Whisper-Transkript */}
        {sel.transkription && sel.transkription.trim().length > 0 && (
          <div>
            <div style={{
              fontSize: 9.5, fontWeight: 700, color: "var(--blue)",
              letterSpacing: ".05em", textTransform: "uppercase", marginBottom: 3,
            }}>
              Whisper · Transkript
            </div>
            <div style={{
              fontSize: 10, color: "var(--text2)", lineHeight: 1.45,
              fontFamily: "var(--mono)",
              padding: "5px 7px",
              background: "rgba(255,255,255,.025)",
              border: "1px solid var(--border)", borderRadius: 4,
              maxHeight: 80, overflowY: "auto",
            }}>
              {sel.transkription}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

function MiniStat({ label, value }: { label: string; value: string }) {
  return (
    <div style={{
      flex: 1, padding: "5px 7px",
      background: "rgba(255,255,255,.025)",
      border: "1px solid var(--border)", borderRadius: 4,
    }}>
      <div style={{
        fontSize: 9, color: "var(--text3)",
        textTransform: "uppercase", letterSpacing: ".04em",
      }}>
        {label}
      </div>
      <div style={{
        fontSize: 11, color: "var(--text)", fontWeight: 600,
        fontFamily: "var(--mono)",
      }}>
        {value}
      </div>
    </div>
  );
}
