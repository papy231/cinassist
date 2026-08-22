"use client";

/**
 * CinAssist — Ordner-Browser (Modal). Lokale App: statt Datei-Upload wählt der Nutzer einen Ordner
 * auf dem Rechner/Volume; er wird per Referenz importiert (nichts wird kopiert).
 * Genutzt von: Medien-Panel („Ordner importieren …“) und Synchronisation (Schritt 1).
 */

import { useCallback, useEffect, useState } from "react";
import { durchsucheOrdner, type OrdnerEintrag, type OrdnerListe } from "@/lib/api";

const PANEL2 = "#242426", BORDER = "#2a2a2e", TXT = "#cfcfcf", MUTED = "#8a8a8a", ACCENT = "#b9d94a";
const btn = (aktiv = false, farbe = ACCENT, disabled = false): React.CSSProperties => ({
  background: aktiv ? farbe : PANEL2, color: aktiv ? "#000" : TXT, border: `1px solid ${aktiv ? farbe : BORDER}`,
  borderRadius: 6, padding: "6px 11px", fontSize: 12, cursor: disabled ? "not-allowed" : "pointer",
  fontWeight: aktiv ? 600 : 400, opacity: disabled ? 0.38 : 1,
});
const inp: React.CSSProperties = { background: "#111", color: TXT, border: `1px solid ${BORDER}`, borderRadius: 6, padding: "6px 8px", fontSize: 12 };

// ─── Ordner-Browser (Modal) ──────────────────────────────

export default function OrdnerBrowser({ typ, onWahl, onSchliessen }: {
  typ: "video" | "audio"; onWahl: (pfad: string) => void; onSchliessen: () => void;
}) {
  const [liste, setListe] = useState<OrdnerListe | null>(null);
  const [fehler, setFehler] = useState<string | null>(null);
  const [manuell, setManuell] = useState("");
  const lade = useCallback(async (pfad?: string | null) => {
    try { const l = await durchsucheOrdner(pfad); setListe(l); setFehler(null); }
    catch (e) { setFehler((e as Error).message); }
  }, []);
  useEffect(() => { const t = setTimeout(() => { void lade(); }, 0); return () => clearTimeout(t); }, [lade]);
  const anzahl = (e: OrdnerEintrag) => typ === "video" ? e.videos : e.audios;
  const hier = liste?.pfad ? (typ === "video" ? liste.videos ?? 0 : liste.audios ?? 0) : 0;
  return (
    <div onClick={onSchliessen} style={{ position: "fixed", inset: 0, background: "rgba(0,0,0,.55)", zIndex: 200, display: "flex", alignItems: "center", justifyContent: "center" }}>
      <div onClick={(e) => e.stopPropagation()} style={{ width: 640, maxHeight: "80vh", background: "#161617", border: `1px solid ${BORDER}`, borderRadius: 12, padding: 16, display: "flex", flexDirection: "column", gap: 10, color: TXT, fontSize: 12, boxShadow: "0 24px 80px rgba(0,0,0,.7)" }}>
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          <b style={{ fontSize: 14, color: ACCENT }}>{typ === "video" ? "Video-Ordner wählen" : "Audio-Ordner wählen"}</b>
          <span style={{ color: MUTED }}>Der Ordner wird nur referenziert — nichts wird kopiert.</span>
          <button onClick={onSchliessen} style={{ marginLeft: "auto", ...btn() }}>Schließen</button>
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 6, background: "#111", borderRadius: 6, padding: "6px 8px" }}>
          <button style={btn(false, ACCENT, !liste?.eltern && !liste?.pfad)} disabled={!liste?.eltern && !liste?.pfad} onClick={() => void lade(liste?.eltern ?? null)}>↑</button>
          <span style={{ fontFamily: "monospace", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{liste?.pfad ?? "Einstiegspunkte"}</span>
          {liste?.pfad && <span style={{ marginLeft: "auto", color: hier ? ACCENT : MUTED, whiteSpace: "nowrap" }}>{hier} {typ === "video" ? "Videos" : "Audios"} direkt hier</span>}
        </div>
        <div style={{ overflowY: "auto", flex: 1, border: `1px solid ${BORDER}`, borderRadius: 6 }}>
          {fehler && <div style={{ color: "#e2574a", padding: 10 }}>{fehler}</div>}
          {liste?.eintraege.length === 0 && <div style={{ color: MUTED, padding: 10 }}>Keine Unterordner.</div>}
          {liste?.eintraege.map((e) => (
            <div key={e.pfad} onDoubleClick={() => void lade(e.pfad)} onClick={() => void lade(e.pfad)}
              style={{ display: "flex", alignItems: "center", gap: 10, padding: "7px 10px", borderBottom: `1px solid #1a1a1c`, cursor: "pointer" }}>
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke={MUTED} strokeWidth="1.8" strokeLinejoin="round" aria-hidden><path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z" /></svg>
              <span style={{ flex: 1 }}>{e.name}</span>
              {(e.videos > 0 || e.audios > 0) && (
                <span style={{ color: MUTED }}>{e.videos ? `${e.videos} Video` : ""}{e.videos && e.audios ? " · " : ""}{e.audios ? `${e.audios} Audio` : ""}</span>
              )}
              {anzahl(e) > 0 && (
                <button style={btn(true)} onClick={(ev) => { ev.stopPropagation(); onWahl(e.pfad); }}>Diesen Ordner nehmen</button>
              )}
            </div>
          ))}
        </div>
        <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
          {liste?.pfad && (
            <button style={btn(hier > 0)} disabled={!liste.pfad} onClick={() => onWahl(liste.pfad!)}
              title={hier ? "" : "Direkt in diesem Ordner liegen keine passenden Dateien — Unterordner werden trotzdem rekursiv importiert"}>
              Aktuellen Ordner nehmen{hier ? ` (${hier} Dateien)` : " (rekursiv)"}
            </button>
          )}
          <input placeholder="… oder Pfad einfügen (/Volumes/…)" value={manuell} onChange={(e) => setManuell(e.target.value)} style={{ ...inp, flex: 1 }}
            onKeyDown={(e) => { if (e.key === "Enter" && manuell.trim()) onWahl(manuell.trim()); }} />
          <button style={btn(false, ACCENT, !manuell.trim())} disabled={!manuell.trim()} onClick={() => onWahl(manuell.trim())}>Übernehmen</button>
        </div>
      </div>
    </div>
  );
}

