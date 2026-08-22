"use client";

/**
 * CinAssist — Synchronisation (Validierung VOR der Analyse) — geführter Ablauf in 3 Schritten
 *
 *   1  Ordner wählen      Kamera-Videos + Ton-WAVs per Referenz (Ordner-Browser, keine Kopie)
 *   2  Synchronisieren    Kaskade Timecode → Wellenform → Klappe → Dateiname (läuft nach Import automatisch)
 *   3  Prüfen & Analyse   jede Zuordnung mit Status/Methode/Offset/Begründung, A/B-Player,
 *                         Korrekturen; „Analyse starten“ erst, wenn kein Take mehr `unklar` ist
 *
 * A/B-Player: EIN Referenztakt (die `<video>`), das `<audio>` folgt mit Schwellen-Drift-Korrektur
 * (nie `currentTime` pro Frame). Kontroll-Werkzeug, NICHT die NLE-Timeline (die hat ihre MasterClock).
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import OrdnerBrowser from "@/components/OrdnerBrowser";
import {
  inMedienUebernehmen, connectJobWs, deleteImport, fetchAssets, fetchImporte, fetchTakes,
  importOrdner, linkAnlegen, linkLoeschen, linkOffsetSetzen, mediaUrl, runSync, takeAblehnen,
  takeBestaetigen, takeOhneAudioBestaetigen, vorschauAnfordern, syncZuruecksetzen, findeOrdner,
  type JobUpdate, type MediaAssetDTO, type OrdnerImportDTO, type OrdnerKandidat, type InMedienOptionen,
  type TakeDTO, type TakeStatus,
} from "@/lib/api";

// ─── Farben / Hilfen ─────────────────────────────────────

const STATUS_FARBE: Record<TakeStatus, string> = {
  sicher: "#5fbf6a", plausibel: "#e0b84a", unklar: "#e2574a", verwaist: "#7a7a7a",
  manuell_bestaetigt: "#7fb2ff", manuell_abgelehnt: "#555",
};
const STATUS_LABEL: Record<TakeStatus, string> = {
  sicher: "sicher", plausibel: "plausibel", unklar: "unklar", verwaist: "verwaist",
  manuell_bestaetigt: "bestätigt", manuell_abgelehnt: "abgelehnt",
};
const STATUS_ERKLAERUNG: Record<TakeStatus, string> = {
  sicher: "Timecode beider Geräte überlappt zu > 80 % — kein Handlungsbedarf, Analyse möglich.",
  plausibel: "Teil-Überlappung oder nur Wellenform/Klappe — im A/B-Player kurz gegenhören.",
  unklar: "Mehrere Kandidaten — bitte entscheiden (Audio zuordnen, ohne Ton freigeben oder ablehnen). Blockiert die Analyse.",
  verwaist: "Kein getrennter Partner gefunden, also Bild ohne Ton-Datei oder Ton ohne Bild. Liegt der Ton in der Kamera, ist das der Normalfall und die Aufnahme lässt sich unverändert weiterverarbeiten. Andernfalls von Hand verknüpfen.",
  manuell_bestaetigt: "Von dir bestätigt.",
  manuell_abgelehnt: "Von dir ausgeschlossen — wird nicht analysiert.",
};
const PANEL = "#1c1c1e", PANEL2 = "#242426", BORDER = "#2a2a2e", TXT = "#cfcfcf", MUTED = "#8a8a8a", ACCENT = "#b9d94a";

const btn = (aktiv = false, farbe = ACCENT, disabled = false): React.CSSProperties => ({
  background: aktiv ? farbe : PANEL2, color: aktiv ? "#000" : TXT, border: `1px solid ${aktiv ? farbe : BORDER}`,
  borderRadius: 6, padding: "6px 11px", fontSize: 12, cursor: disabled ? "not-allowed" : "pointer",
  fontWeight: aktiv ? 600 : 400, opacity: disabled ? 0.38 : 1,
});
const inp: React.CSSProperties = { background: "#111", color: TXT, border: `1px solid ${BORDER}`, borderRadius: 6, padding: "6px 8px", fontSize: 12 };
const karte: React.CSSProperties = { background: PANEL, border: `1px solid ${BORDER}`, borderRadius: 10, padding: 12 };

function fmtOffset(s: number | null | undefined): string {
  if (s === null || s === undefined) return "—";
  return `${s >= 0 ? "+" : "−"}${Math.abs(s).toFixed(3)} s`;
}
function fmtDauer(s: number | null | undefined): string {
  if (!s && s !== 0) return "—";
  const m = Math.floor(s / 60), r = s - m * 60;
  return `${m}:${r.toFixed(1).padStart(4, "0")}`;
}
function gruppenKey(t: TakeDTO): string { return `${t.szene ?? "?"}|${t.plan ?? "?"}`; }
function gruppenLabel(t: TakeDTO): string { return `Szene ${t.szene ?? "?"} · Einstellung ${t.plan ?? "?"}`; }
function kurzPfad(p: string): string { const t = p.split("/").filter(Boolean); return t.length > 3 ? "…/" + t.slice(-3).join("/") : p; }

/** Kleiner Schritt-Chip: 1/2/3 mit Zustand. */
function Schritt({ nr, label, zustand }: { nr: number; label: string; zustand: "offen" | "aktiv" | "fertig" }) {
  const col = zustand === "fertig" ? "#5fbf6a" : zustand === "aktiv" ? ACCENT : MUTED;
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
      <span style={{ width: 22, height: 22, borderRadius: 11, background: zustand === "offen" ? "transparent" : col, border: `1px solid ${col}`,
        color: zustand === "offen" ? col : "#000", display: "inline-flex", alignItems: "center", justifyContent: "center", fontWeight: 700, fontSize: 12 }}>
        {zustand === "fertig" ? "✓" : nr}
      </span>
      <span style={{ color: zustand === "offen" ? MUTED : TXT, fontWeight: zustand === "aktiv" ? 600 : 400 }}>{label}</span>
    </div>
  );
}

// ─── Finder-Drop: Ordnername + Dateinamen aus dem DataTransfer sammeln ───────
// Browser liefern für gedroppte Ordner keinen Pfad — nur Einträge (webkitGetAsEntry). Wir sammeln
// Name + bis zu 400 Dateinamen (2 Ebenen tief) und lassen das Backend den Ordner wiederfinden.
type DropBefund = { name: string | null; dateien: string[] };
async function leseDrop(dt: DataTransfer): Promise<DropBefund> {
  type Entry = { isFile: boolean; isDirectory: boolean; name: string; createReader?: () => { readEntries: (ok: (e: Entry[]) => void, err: (e: unknown) => void) => void } };
  const items = Array.from(dt.items ?? []);
  const entries: Entry[] = items.map((it) => (it as DataTransferItem & { webkitGetAsEntry?: () => Entry | null }).webkitGetAsEntry?.() ?? null).filter(Boolean) as Entry[];
  const dateien: string[] = [];
  const lies = async (e: Entry, tiefe: number): Promise<void> => {
    if (dateien.length >= 400) return;
    if (e.isFile) { dateien.push(e.name); return; }
    if (e.isDirectory && tiefe <= 2 && e.createReader) {
      const reader = e.createReader();
      // readEntries liefert Batches — solange lesen, bis leer.
      for (;;) {
        const batch: Entry[] = await new Promise((ok, err) => reader.readEntries(ok, err));
        if (!batch.length) break;
        for (const k of batch) await lies(k, tiefe + 1);
        if (dateien.length >= 400) break;
      }
    }
  };
  const ordner = entries.filter((e) => e.isDirectory);
  const name = ordner.length === 1 && entries.length === 1 ? ordner[0].name : null;
  for (const e of entries) await lies(e, 0);
  if (!entries.length) for (const f of Array.from(dt.files ?? [])) dateien.push(f.name);
  return { name, dateien };
}

// ─── A/B-Player ──────────────────────────────────────────

function ABPlayer({ videoUrl, audioUrl, offsetS, audioName, wirdErzeugt }: {
  videoUrl: string | null; audioUrl: string | null; offsetS: number; audioName: string | null; wirdErzeugt: boolean;
}) {
  const vRef = useRef<HTMLVideoElement>(null);
  const aRef = useRef<HTMLAudioElement>(null);
  const [mode, setMode] = useState<"A" | "B">("B");
  const [readout, setReadout] = useState({ v: 0, a: 0, drift: 0 });
  const offsetRef = useRef(offsetS);
  const modeRef = useRef(mode);
  useEffect(() => { offsetRef.current = offsetS; }, [offsetS]);
  useEffect(() => { modeRef.current = mode; }, [mode]);

  useEffect(() => {
    let raf = 0;
    // Ein Abgleich-Schritt: Audio folgt der Video-Uhr. Wird von rAF (sichtbar), von
    // Video-Events (play/pause/seek/rate) UND einem Intervall (Tab im Hintergrund —
    // dort steht rAF still, das Audio liefe sonst allein weiter) aufgerufen.
    const abgleich = (hart = false) => {
      const v = vRef.current, a = aRef.current;
      if (!v || !a) return;
      // offset = audio_start − video_start  ⇒  audio_time = video_time − offset
      const ziel = v.currentTime - offsetRef.current;
      const imBereich = ziel >= 0 && (!isFinite(a.duration) || ziel < a.duration);
      v.muted = modeRef.current === "B";
      a.muted = modeRef.current === "A";
      if (a.playbackRate !== v.playbackRate) a.playbackRate = v.playbackRate;
      if (v.paused || v.ended || v.seeking || !imBereich) {
        if (!a.paused) a.pause();
        if (imBereich && (hart || Math.abs(a.currentTime - ziel) > 0.03)) a.currentTime = ziel;
      } else {
        const drift = a.currentTime - ziel;
        if (a.paused) { a.currentTime = ziel; void a.play().catch(() => {}); }
        else if (hart || Math.abs(drift) > 0.08) a.currentTime = ziel;   // nur bei echtem Drift re-seeken
      }
      setReadout({ v: v.currentTime, a: a.currentTime, drift: imBereich ? a.currentTime - ziel : 0 });
    };
    const tick = () => { abgleich(false); raf = requestAnimationFrame(tick); };
    raf = requestAnimationFrame(tick);
    const iv = window.setInterval(() => abgleich(false), 250);
    const v = vRef.current;
    const a = aRef.current;
    const hart = () => abgleich(true);
    const weich = () => abgleich(false);
    v?.addEventListener("play", hart);
    v?.addEventListener("pause", weich);
    v?.addEventListener("seeked", hart);
    v?.addEventListener("ratechange", weich);
    v?.addEventListener("ended", weich);
    return () => {
      cancelAnimationFrame(raf);
      window.clearInterval(iv);
      v?.removeEventListener("play", hart);
      v?.removeEventListener("pause", weich);
      v?.removeEventListener("seeked", hart);
      v?.removeEventListener("ratechange", weich);
      v?.removeEventListener("ended", weich);
      a?.pause();
    };
  }, [videoUrl, audioUrl]);

  if (!videoUrl) {
    return (
      <div style={{ height: 160, display: "flex", alignItems: "center", justifyContent: "center", background: "#000", borderRadius: 6, color: MUTED }}>
        {wirdErzeugt ? "A/B-Vorschau wird erzeugt (480p-Proxy + Ton) …" : "Keine Vorschau."}
      </div>
    );
  }
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
      <video key={videoUrl} ref={vRef} src={videoUrl} controls playsInline preload="auto" style={{ width: "100%", maxHeight: 300, background: "#000", borderRadius: 6 }} />
      {audioUrl && <audio key={audioUrl} ref={aRef} src={audioUrl} preload="auto" />}
      <div style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 12, flexWrap: "wrap" }}>
        <span style={{ color: MUTED }}>Ton hören:</span>
        <button style={btn(mode === "A")} onClick={() => setMode("A")} title="Original-Kameraspur (auf diesem Material: Stille/Timecode)">A — Kamera</button>
        <button style={btn(mode === "B", ACCENT, !audioUrl)} onClick={() => setMode("B")} disabled={!audioUrl} title={audioUrl ? "verknüpftes WAV, um den Offset verschoben" : "kein verknüpftes Audio"}>
          B — verknüpft{audioName ? ` (${audioName})` : ""}
        </button>
        <span style={{ marginLeft: "auto", color: MUTED, fontVariantNumeric: "tabular-nums" }}>
          Video {readout.v.toFixed(2)} s · Audio {readout.a.toFixed(2)} s · Drift {(readout.drift * 1000).toFixed(0)} ms
        </span>
      </div>
      <div style={{ color: MUTED }}>Lippen und Klappe passen? Dann „Bestätigen“. Sonst Offset unten anpassen — die Vorschau folgt sofort.</div>
    </div>
  );
}

// ─── Haupt-Panel ─────────────────────────────────────────

export default function SyncPanel({ onAnalyseGestartet }: { onAnalyseGestartet?: (opts?: { zuMedien?: boolean; anzahl?: number }) => void }) {
  const [takes, setTakes] = useState<TakeDTO[]>([]);
  const [zaehler, setZaehler] = useState<Partial<Record<TakeStatus, number>>>({});
  const [assets, setAssets] = useState<MediaAssetDTO[]>([]);
  const [importe, setImporte] = useState<OrdnerImportDTO[]>([]);
  const [browser, setBrowser] = useState<"video" | "audio" | null>(null);
  const [auswahl, setAuswahl] = useState<string | null>(null);
  const [filter, setFilter] = useState<TakeStatus | "alle">("alle");
  const [suche, setSuche] = useState("");
  const [zu, setZu] = useState<Set<string>>(new Set());
  const [jobs, setJobs] = useState<Record<string, JobUpdate & { label: string; art: string }>>({});
  const [meldung, setMeldung] = useState<string | null>(null);
  const [fehler, setFehler] = useState<string | null>(null);
  const [rattachAudio, setRattachAudio] = useState("");
  const [rattachSuche, setRattachSuche] = useState("");
  const [offsetEntwurf, setOffsetEntwurf] = useState<Record<string, number>>({});
  const [unklarTrotzdem, setUnklarTrotzdem] = useState(false);
  const [autoSync, setAutoSync] = useState(false);
  const [dropZiel, setDropZiel] = useState<"video" | "audio" | null>(null);
  const [pfadWahl, setPfadWahl] = useState<{ typ: "video" | "audio"; kandidaten: OrdnerKandidat[] } | null>(null);
  const [medienDialog, setMedienDialog] = useState<{ nurTake?: string } | null>(null);
  const [medienOpt, setMedienOpt] = useState<InMedienOptionen>({ ordnung: "szene", ton_separat: false, waisen_video: true, waisen_audio: false, analyse: true });
  // Ohne bekannte Szene wäre „Nach Szene“ ein leeres Versprechen: alles käme in einen
  // Sammelordner. Die Voreinstellung folgt daher dem, was das Material hergibt.
  useEffect(() => {
    if (!medienDialog) return;
    const bekannt = takes.some((t) => t.szene != null);
    setMedienOpt((o) => (bekannt || o.ordnung !== "szene" ? o : { ...o, ordnung: "flach" }));
  }, [medienDialog, takes]);
  const [busy, setBusy] = useState(false);
  const [vorschauLaeuft, setVorschauLaeuft] = useState<Set<string>>(new Set());
  const [schrittOffen, setSchrittOffen] = useState<1 | 2 | 3>(1);
  const geladen = useRef(false);

  const laden = useCallback(async () => {
    try {
      const [t, i, a] = await Promise.all([fetchTakes(), fetchImporte(), fetchAssets()]);
      setTakes(t.takes); setZaehler(t.status_zaehler); setImporte(i); setAssets(a); setFehler(null);
      if (!geladen.current) {
        geladen.current = true;
        setSchrittOffen(t.takes.length > 0 ? 3 : a.length > 0 ? 2 : 1);
      }
    } catch (e) { setFehler(`Backend nicht erreichbar oder Fehler: ${(e as Error).message}`); }
  }, []);
  useEffect(() => { void laden(); }, [laden]);

  const verfolgeJob = useCallback((jobId: string, label: string, art: string, danach?: (u: JobUpdate) => void) => {
    setJobs((j) => ({ ...j, [jobId]: { status: "wartend", progress: 0, message: "wartet auf Worker …", label, art } as JobUpdate & { label: string; art: string } }));
    connectJobWs(jobId, (u) => {
      setJobs((j) => ({ ...j, [jobId]: { ...u, label, art } }));
      if (u.status === "fertig" || u.status === "fehler") {
        setTimeout(() => setJobs((j) => { const c = { ...j }; delete c[jobId]; return c; }), u.status === "fehler" ? 15000 : 3000);
        void laden().then(() => danach?.(u));
      }
    });
  }, [laden]);

  const aktion = useCallback(async (f: () => Promise<unknown>, ok?: string) => {
    setBusy(true); setFehler(null);
    try { await f(); if (ok) setMeldung(ok); await laden(); }
    catch (e) { setFehler((e as Error).message); }
    finally { setBusy(false); }
  }, [laden]);

  const matchen = useCallback(() => aktion(async () => {
    const r = await runSync();
    setSchrittOffen(2);
    verfolgeJob(r.job_id, "Synchronisieren", "sync", (u) => {
      if (u.status === "fertig") {
        const s = (u.result ?? {}) as { takes?: number; unklar?: number; statistik?: Record<string, number> };
        setMeldung(`Synchronisiert: ${s.statistik?.stufe1_sicher ?? 0} sicher · ${s.unklar ?? 0} unklar · ${(s.statistik?.verwaist_video ?? 0) + (s.statistik?.verwaist_audio ?? 0)} ohne Partner`);
        setSchrittOffen(3);
        if ((s.unklar ?? 0) > 0) setFilter("unklar");
      }
    });
  }), [aktion, verfolgeJob]);

  const laufendeImporte = useRef(0);
  const importieren = (pfad: string, typ: "video" | "audio") => aktion(async () => {
    const r = await importOrdner(pfad, typ);
    laufendeImporte.current += 1;
    verfolgeJob(r.job_id, `${typ === "video" ? "Video" : "Audio"}-Import ${kurzPfad(pfad)}`, "import", (u) => {
      laufendeImporte.current = Math.max(0, laufendeImporte.current - 1);
      // Beide Ordner drin und kein Import mehr unterwegs → automatisch synchronisieren.
      if (u.status === "fertig" && autoSync && laufendeImporte.current === 0) {
        void fetchImporte().then((imp) => {
          const hatV = imp.some((i) => i.typ === "video" && i.status === "fertig");
          const hatA = imp.some((i) => i.typ === "audio" && i.status === "fertig");
          if (hatV && hatA) void matchen();
        });
      }
    });
  });

  const ordnerDrop = async (dt: DataTransfer, typ: "video" | "audio") => {
    setDropZiel(null);
    setBusy(true); setFehler(null);
    try {
      const b = await leseDrop(dt);
      if (!b.name && b.dateien.length === 0) { setFehler("Nichts Verwertbares abgelegt."); return; }
      setMeldung(`Suche „${b.name ?? `${b.dateien.length} Dateien`}“ auf dem Rechner …`);
      const r = await findeOrdner(b.name, b.dateien, typ);
      const gute = r.kandidaten.filter((k) => k.quote >= 0.5);
      if (gute.length === 1) { setMeldung(null); await importieren(gute[0].pfad, typ); }
      else if (gute.length > 1) { setMeldung(null); setPfadWahl({ typ, kandidaten: gute }); }
      else { setMeldung(null); setFehler(`Ordner „${b.name ?? "?"}“ auf dem Rechner nicht gefunden (Browser liefert keinen Pfad) — bitte über „Ordner wählen …“ auswählen.`); setBrowser(typ); }
    } catch (e) { setFehler((e as Error).message); }
    finally { setBusy(false); }
  };

  const analyse = (nurTake?: string) => aktion(async () => {
    const r = await inMedienUebernehmen(medienOpt, nurTake ? [nurTake] : undefined, unklarTrotzdem);
    // Analysen NICHT hier als Job-Chips verfolgen — ihr Fortschritt gehört ins Medien-Panel
    // (Badge „Analyse“ je Kachel, Zähler im Fuß). Hier nur eine Zusammenfassung.
    const ordnungText = r.ordnung === "szene" ? "nach Szene/Einstellung" : r.ordnung === "chronologisch" ? "nach Drehtag" : "flach";
    setMeldung(`${r.medien.length} Medium/Medien übernommen (${ordnungText})${r.gestartet.length ? `, ${r.gestartet.length} Analyse(n) laufen im Medien-Panel` : ""}${r.uebersprungen.length ? `, ${r.uebersprungen.length} übersprungen` : ""}.`);
    onAnalyseGestartet?.({ zuMedien: !nurTake && r.medien.length > 0, anzahl: r.gestartet.length });
  });

  const vorschau = useCallback((takeId: string) => {
    setVorschauLaeuft((s) => new Set(s).add(takeId));
    vorschauAnfordern(takeId).then((r) => {
      if (r.fertig) { setVorschauLaeuft((s) => { const n = new Set(s); n.delete(takeId); return n; }); void laden(); }
      else if (r.job_id) verfolgeJob(r.job_id, "A/B-Vorschau", "vorschau", () => setVorschauLaeuft((s) => { const n = new Set(s); n.delete(takeId); return n; }));
    }).catch((e) => { setFehler((e as Error).message); setVorschauLaeuft((s) => { const n = new Set(s); n.delete(takeId); return n; }); });
  }, [laden, verfolgeJob]);

  // Take auswählen → Vorschau automatisch anstoßen, wenn sie fehlt.
  const sel = takes.find((t) => t.id === auswahl) ?? null;
  useEffect(() => {
    if (!sel || !sel.video) return;
    const linkOhne = sel.links.some((l) => l.methode !== "verwaist" && !l.vorschau_audio_url);
    if ((!sel.vorschau_video_url || linkOhne) && !vorschauLaeuft.has(sel.id)) vorschau(sel.id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sel?.id, sel?.vorschau_video_url, sel?.links.length]);

  const gefiltert = useMemo(() => {
    const q = suche.trim().toLowerCase();
    return takes.filter((t) => (filter === "alle" || t.status === filter) && (!q ||
      (t.video?.dateiname ?? "").toLowerCase().includes(q) || t.links.some((l) => (l.audio?.dateiname ?? "").toLowerCase().includes(q))));
  }, [takes, filter, suche]);
  const gruppen = useMemo(() => {
    const m = new Map<string, TakeDTO[]>();
    gefiltert.forEach((t) => { const k = gruppenKey(t); if (!m.has(k)) m.set(k, []); m.get(k)!.push(t); });
    return [...m.entries()].sort(([a], [b]) => a.localeCompare(b, undefined, { numeric: true }));
  }, [gefiltert]);
  const zuerst = useMemo(() => takes.filter((t) => t.status === "unklar"), [takes]);
  const audios = useMemo(() => assets.filter((a) => a.typ === "audio").sort((a, b) => a.dateiname.localeCompare(b.dateiname)), [assets]);
  const rattachListe = useMemo(() => {
    const q = rattachSuche.trim().toLowerCase();
    return q ? audios.filter((a) => a.dateiname.toLowerCase().includes(q) || (a.tc_start ?? "").includes(q)) : audios;
  }, [audios, rattachSuche]);

  const leer = importe.length === 0 && takes.length === 0 && Object.keys(jobs).length === 0;
  const videoImporte = importe.filter((i) => i.typ === "video");
  const audioImporte = importe.filter((i) => i.typ === "audio");
  const hatVideos = assets.some((a) => a.typ === "video");
  const hatAudios = assets.some((a) => a.typ === "audio");
  const nUnklar = zaehler.unklar ?? 0;
  const analysierbar = takes.filter((t) => t.video && t.status !== "manuell_abgelehnt" && t.status !== "unklar" && t.clip_status !== "analysiert").length;
  const blockiert = nUnklar > 0 && !unklarTrotzdem;
  const jobListe = Object.entries(jobs);
  const laeuft = (art: string) => jobListe.some(([, j]) => j.art === art && j.status !== "fertig" && j.status !== "fehler");
  const primaerLink = sel?.links.find((l) => l.methode !== "verwaist") ?? sel?.links[0] ?? null;
  const offsetLive = primaerLink ? (offsetEntwurf[primaerLink.id] ?? primaerLink.offset_s) : 0;

  // Ein getrennter Ton-Ordner ist nicht nötig. Liegt der Ton in der Kamera, gibt es
  // nichts zuzuordnen: der Abgleich legt je Video einen Take an, und der Weg zu
  // Schritt 3 bleibt offen. Nur ohne Bildmaterial lässt sich nichts tun.
  const nurKameraton = hatVideos && !hatAudios;
  const grundMatching = !hatVideos ? (hatAudios ? "Es fehlt der Video-Ordner." : "Zuerst Ordner importieren (Schritt 1).") : laeuft("import") ? "Import läuft noch …" : null;
  const grundAnalyse = takes.length === 0 ? "Zuerst synchronisieren (Schritt 2)." : blockiert ? `${nUnklar} Take(s) sind unklar — unten entscheiden (oder „unklar trotzdem“).` : analysierbar === 0 ? "Alle Takes sind schon übernommen." : null;

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%", color: TXT, fontSize: 12, gap: 10 }}>
      {browser && <OrdnerBrowser typ={browser} onSchliessen={() => setBrowser(null)} onWahl={(p) => { setBrowser(null); void importieren(p, browser); }} />}
      {medienDialog && (() => {
        const opt = medienOpt;
        const set = (p: Partial<InMedienOptionen>) => setMedienOpt({ ...opt, ...p });
        const nTakes = medienDialog.nurTake ? 1 : analysierbar;
        // Szene und Einstellung kennt der Abgleich nur aus dem Dateinamen. Passt das
        // Muster nicht, landet bei „Nach Szene“ alles in einem Ordner „Ohne Szene“.
        // Die echte Zuordnung entsteht erst nach der Auswertung, im Kontext-Schritt.
        const szeneBekannt = takes.some((t) => t.szene != null);
        const nWaisenAudio = takes.filter((t) => !t.video && t.status !== "manuell_abgelehnt").length;
        const Radio = ({ wert, label, hinweis }: { wert: InMedienOptionen["ordnung"]; label: string; hinweis: string }) => (
          <label style={{ display: "flex", gap: 8, alignItems: "flex-start", padding: "6px 8px", borderRadius: 6, background: opt.ordnung === wert ? "#2a2f1e" : PANEL2, cursor: "pointer" }}>
            <input type="radio" name="ordnung" checked={opt.ordnung === wert} onChange={() => set({ ordnung: wert })} />
            <span><b>{label}</b><br /><span style={{ color: MUTED }}>{hinweis}</span></span>
          </label>
        );
        return (
          <div onClick={() => setMedienDialog(null)} style={{ position: "fixed", inset: 0, background: "rgba(0,0,0,.55)", zIndex: 200, display: "flex", alignItems: "center", justifyContent: "center" }}>
            <div onClick={(e) => e.stopPropagation()} style={{ width: 620, background: "#161617", border: `1px solid ${BORDER}`, borderRadius: 12, padding: 18, color: TXT, fontSize: 12, display: "flex", flexDirection: "column", gap: 12 }}>
              <div>
                <b style={{ fontSize: 14, color: ACCENT }}>In Medien übernehmen — {nTakes} Take(s)</b>
                <div style={{ color: MUTED, marginTop: 4 }}>Jeder Take wird <b style={{ color: TXT }}>ein Medium</b>: das Video mit seinem zugeordneten, synchronen Ton (der Proxy trägt den verknüpften Ton, Offset eingerechnet). Die Timeline bleibt unberührt.</div>
              </div>
              <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                <b>Ordnung im Medien-Panel</b>
                <Radio wert="szene" label="Nach Szene / Einstellung"
                  hinweis={szeneBekannt
                    ? "Szene 4 › Einstellung 3 › Takes — die Reihenfolge des Drehbuchs (Standard für den Schnitt)."
                    : "Bei diesem Material ist die Szene noch unbekannt, die Dateinamen tragen sie nicht. Alles landet in einem Ordner „Ohne Szene“."} />
                <Radio wert="chronologisch" label="Nach Drehtag (chronologisch)" hinweis="Drehtag 2023-11-17 › Takes; im Panel nach Timecode sortierbar — die Reihenfolge des Drehs (Multicam liegt nebeneinander)." />
                <Radio wert="flach" label="Flach" hinweis="Alle Takes direkt in „Medien“ — du sortierst selbst (Sortierung Szene/Take oder Timecode)." />
                {!szeneBekannt && (
                  <div style={{ color: MUTED, background: "rgba(224,184,74,.08)", border: "1px solid rgba(224,184,74,.25)", borderRadius: 6, padding: "7px 9px", lineHeight: 1.5 }}>
                    Die Szenen sind hier noch nicht bekannt. Sie entstehen erst nach der Auswertung, wenn die
                    gesprochene Klappe und die Transkription gegen das Drehbuch gehalten werden. Deshalb ist
                    <b style={{ color: TXT }}> Flach</b> vorausgewählt. Danach lassen sich die Medien unter
                    <b style={{ color: TXT }}> Skript &amp; Kontext</b> mit einem Klick nach Drehbuchszenen sortieren.
                  </div>
                )}
              </div>
              <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                <b>Ton</b>
                <label style={{ display: "flex", gap: 8, alignItems: "center" }}><input type="checkbox" checked disabled /> Ton ist im Video-Medium enthalten (synchron, Offset eingerechnet) — immer</label>
                <label style={{ display: "flex", gap: 8, alignItems: "center" }}><input type="checkbox" checked={opt.ton_separat} onChange={(e) => set({ ton_separat: e.target.checked })} /> Zusätzlich jedes verknüpfte WAV als eigenes Audio-Medium (Unterordner „Ton“ — für Kanalwahl / Neusync später)</label>
              </div>
              <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                <b>Waisen</b>
                <label style={{ display: "flex", gap: 8, alignItems: "center" }}><input type="checkbox" checked={opt.waisen_video} onChange={(e) => set({ waisen_video: e.target.checked })} /> Bild ohne Ton übernehmen (Kamera-Ton nur, wenn brauchbar)</label>
                <label style={{ display: "flex", gap: 8, alignItems: "center" }}><input type="checkbox" checked={opt.waisen_audio} onChange={(e) => set({ waisen_audio: e.target.checked })} /> Ton ohne Bild als Audio-Medium übernehmen (Ordner „Nur Ton“){nWaisenAudio ? ` — ${nWaisenAudio} WAV` : ""}</label>
              </div>
              <label style={{ display: "flex", gap: 8, alignItems: "center" }}><input type="checkbox" checked={opt.analyse} onChange={(e) => set({ analyse: e.target.checked })} /> Analyse sofort starten (Proxy, Vignetten, Whisper auf dem verknüpften Ton, Szenen)</label>
              {nUnklar > 0 && !medienDialog.nurTake && <div style={{ color: "#e2574a" }}>{nUnklar} unklare Take(s) werden {unklarTrotzdem ? "OHNE Ton übernommen" : "nicht übernommen (erst entscheiden oder „unklar trotzdem“)"}.</div>}
              <div style={{ display: "flex", gap: 8, justifyContent: "flex-end" }}>
                <button style={btn()} onClick={() => setMedienDialog(null)}>Abbrechen</button>
                <button style={btn(true, "#7fb2ff")} onClick={() => { const nt = medienDialog.nurTake; setMedienDialog(null); void analyse(nt); }}>Übernehmen</button>
              </div>
            </div>
          </div>
        );
      })()}
      {pfadWahl && (
        <div onClick={() => setPfadWahl(null)} style={{ position: "fixed", inset: 0, background: "rgba(0,0,0,.55)", zIndex: 200, display: "flex", alignItems: "center", justifyContent: "center" }}>
          <div onClick={(e) => e.stopPropagation()} style={{ width: 560, background: "#161617", border: `1px solid ${BORDER}`, borderRadius: 12, padding: 16, color: TXT, fontSize: 12, display: "flex", flexDirection: "column", gap: 8 }}>
            <b style={{ color: ACCENT }}>Mehrere passende Ordner gefunden — welcher ist gemeint?</b>
            {pfadWahl.kandidaten.map((k) => (
              <button key={k.pfad} style={{ ...btn(), textAlign: "left" }} onClick={() => { const t = pfadWahl.typ; setPfadWahl(null); void importieren(k.pfad, t); }}>
                {k.pfad} <span style={{ color: MUTED }}>· {k.medien} Medien · {(k.quote * 100).toFixed(0)} % Übereinstimmung</span>
              </button>
            ))}
            <button style={btn()} onClick={() => setPfadWahl(null)}>Abbrechen</button>
          </div>
        </div>
      )}

      {/* Kopf: Titel + Schritte + Meldungen */}
      <div style={{ display: "flex", alignItems: "center", gap: 18, flexWrap: "wrap" }}>
        <span style={{ fontSize: 15, fontWeight: 600, color: ACCENT }}>Synchronisation</span>
        <Schritt nr={1} label="Ordner wählen" zustand={hatVideos && hatAudios ? "fertig" : schrittOffen === 1 ? "aktiv" : "offen"} />
        <span style={{ color: MUTED }}>→</span>
        <Schritt nr={2} label="Automatisch synchronisieren" zustand={takes.length > 0 ? "fertig" : schrittOffen === 2 || laeuft("sync") ? "aktiv" : "offen"} />
        <span style={{ color: MUTED }}>→</span>
        <Schritt nr={3} label="Prüfen & in Medien übernehmen" zustand={schrittOffen === 3 ? "aktiv" : "offen"} />
        <button style={{ ...btn(), marginLeft: "auto" }} onClick={() => void laden()} disabled={busy}>Aktualisieren</button>
        <button style={btn(false, "#e2574a", busy || (importe.length === 0 && takes.length === 0))} disabled={busy || (importe.length === 0 && takes.length === 0)}
          title="Importe, Takes, Zuordnungen, daraus erzeugte Clips und A/B-Vorschauen entfernen — die Originaldateien bleiben unberührt"
          onClick={() => { if (window.confirm("Alles zurücksetzen? Importe, Takes, Zuordnungen, daraus erzeugte Clips und Vorschauen werden entfernt. Die Originaldateien auf dem Datenträger bleiben unberührt.")) void aktion(async () => { const r = await syncZuruecksetzen(); setAuswahl(null); setFilter("alle"); setSchrittOffen(1); setMeldung(`Zurückgesetzt: ${r.geloescht.importe} Importe, ${r.geloescht.assets} Assets, ${r.geloescht.takes} Takes, ${r.geloescht.clips} Clips`); onAnalyseGestartet?.(); }); }}>
          Alles zurücksetzen
        </button>
      </div>
      {(jobListe.length > 0 || meldung || fehler) && (
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
          {jobListe.map(([id, j]) => (
            <span key={id} style={{ background: PANEL2, borderRadius: 6, padding: "4px 10px", color: j.status === "fehler" ? "#e2574a" : TXT, display: "flex", gap: 8, alignItems: "center" }}>
              <span style={{ width: 90, height: 6, background: "#111", borderRadius: 3, overflow: "hidden", display: "inline-block" }}>
                <span style={{ display: "block", width: `${j.progress}%`, height: "100%", background: j.status === "fehler" ? "#e2574a" : ACCENT }} />
              </span>
              <b>{j.label}</b> {j.progress}% — {j.message}
            </span>
          ))}
          {meldung && <span style={{ color: ACCENT, background: "rgba(185,217,74,.08)", borderRadius: 6, padding: "4px 10px" }}>{meldung} <button style={{ background: "none", border: "none", color: MUTED, cursor: "pointer" }} onClick={() => setMeldung(null)}>×</button></span>}
          {fehler && <span style={{ color: "#e2574a", background: "rgba(226,87,74,.1)", borderRadius: 6, padding: "4px 10px" }}>⚠ {fehler} <button style={{ background: "none", border: "none", color: MUTED, cursor: "pointer" }} onClick={() => setFehler(null)}>×</button></span>}
        </div>
      )}

      {/* Startansicht: nichts importiert → zwei große Kacheln, kein Jargon */}
      {leer && (
        <div style={{ flex: 1, display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", gap: 18, textAlign: "center" }}>
          <div style={{ fontSize: 22, fontWeight: 600 }}>Bild und Ton zusammenbringen</div>
          <div style={{ color: MUTED, maxWidth: 560, lineHeight: 1.6 }}>
            Wähle den Ordner mit den <b style={{ color: TXT }}>Kamera-Videos</b> und den Ordner mit den <b style={{ color: TXT }}>Ton-Aufnahmen</b>.
            CinAssist findet zusammengehörige Aufnahmen automatisch — du prüfst nur noch, was unklar ist.
          </div>
          <div style={{ display: "flex", gap: 16, marginTop: 6 }}>
            {(["video", "audio"] as const).map((typ) => (
              <div key={typ} onClick={() => setBrowser(typ)}
                onDragOver={(e) => { if (e.dataTransfer.types.includes("Files")) { e.preventDefault(); e.stopPropagation(); e.dataTransfer.dropEffect = "link"; if (dropZiel !== typ) setDropZiel(typ); } }}
                onDragLeave={(e) => { if (e.currentTarget === e.target) setDropZiel(null); }}
                onDrop={(e) => { if (e.dataTransfer.types.includes("Files")) { e.preventDefault(); e.stopPropagation(); void ordnerDrop(e.dataTransfer, typ); } }}
                style={{ width: 260, padding: "26px 20px", background: dropZiel === typ ? "rgba(185,217,74,0.10)" : PANEL, border: `${dropZiel === typ ? "2px" : "1px"} dashed ${typ === "video" ? "#7fb2ff" : "#e0b84a"}`, borderRadius: 14, cursor: "pointer", display: "flex", flexDirection: "column", alignItems: "center", gap: 10 }}>
                <svg width="40" height="40" viewBox="0 0 24 24" fill="none" stroke={typ === "video" ? "#7fb2ff" : "#e0b84a"} strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
                  {typ === "video"
                    ? <><rect x="2" y="6" width="14" height="12" rx="2" /><path d="M16 10l6-3v10l-6-3z" /><circle cx="7" cy="12" r="2" /></>
                    : <><rect x="9" y="2" width="6" height="12" rx="3" /><path d="M5 10a7 7 0 0 0 14 0" /><path d="M12 17v5M8 22h8" /></>}
                </svg>
                <b style={{ fontSize: 14, color: typ === "video" ? "#7fb2ff" : "#e0b84a" }}>{typ === "video" ? "Kamera-Videos" : "Ton-Aufnahmen"}</b>
                <span style={{ color: MUTED }}>{typ === "video" ? "MOV / MP4 von der Kamera" : "WAV vom Tonrecorder"}</span>
                <button style={{ ...btn(true), marginTop: 4 }} onClick={(e) => { e.stopPropagation(); setBrowser(typ); }}>Ordner wählen …</button>
              </div>
            ))}
          </div>
          <div style={{ color: MUTED }}>Ordner einfach aus dem Finder auf eine Kachel ziehen. Es wird nichts kopiert oder verändert — die Dateien bleiben, wo sie sind.</div>
          <div style={{ display: "flex", gap: 28, marginTop: 8, color: MUTED }}>
            <span><b style={{ color: TXT }}>1</b> Abgleich startet automatisch</span>
            <span><b style={{ color: TXT }}>2</b> Zweifelhafte Takes im A/B-Player anhören</span>
            <span><b style={{ color: TXT }}>3</b> Als synchrone Medien ins Medien-Panel übernehmen</span>
          </div>
        </div>
      )}

      {/* Schritt 1 + 2 nebeneinander */}
      {!leer && <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: 10 }}>
        {(["video", "audio"] as const).map((typ) => {
          const liste = typ === "video" ? videoImporte : audioImporte;
          const n = assets.filter((a) => a.typ === typ).length;
          const zielAktiv = dropZiel === typ;
          return (
            <div key={typ} style={{ ...karte, outline: zielAktiv ? `2px dashed ${typ === "video" ? "#7fb2ff" : "#e0b84a"}` : "none", background: zielAktiv ? "rgba(185,217,74,0.06)" : PANEL }}
              onDragOver={(e) => { if (e.dataTransfer.types.includes("Files")) { e.preventDefault(); e.stopPropagation(); e.dataTransfer.dropEffect = "link"; if (!zielAktiv) setDropZiel(typ); } }}
              onDragLeave={(e) => { if (e.currentTarget === e.target) setDropZiel(null); }}
              onDrop={(e) => { if (e.dataTransfer.types.includes("Files")) { e.preventDefault(); e.stopPropagation(); void ordnerDrop(e.dataTransfer, typ); } }}>
              <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 8 }}>
                <b style={{ color: typ === "video" ? "#7fb2ff" : "#e0b84a" }}>{typ === "video" ? "1a · Kamera-Videos" : "1b · Ton-Aufnahmen (WAV)"}</b>
                <span style={{ color: MUTED }}>{n ? `${n} Dateien erfasst` : "noch nichts"}</span>
                <button style={{ ...btn(liste.length === 0), marginLeft: "auto" }} onClick={() => setBrowser(typ)} disabled={busy}>
                  {liste.length ? "Weiteren Ordner …" : "Ordner wählen …"}
                </button>
              </div>
              {liste.length === 0 && <div style={{ color: MUTED }}>{typ === "video" ? "Noch kein Video-Ordner — MOV/MP4 von der Kamera." : "Noch kein Audio-Ordner. Nur nötig, wenn der Ton getrennt aufgezeichnet wurde, etwa als WAV vom Tonrecorder. Liegt der Ton in der Kamera, bleibt dieses Feld leer."} <span style={{ opacity: 0.8 }}>Ordner aus dem Finder hierher ziehen oder „Ordner wählen …“.</span></div>}
              {liste.map((i) => (
                <div key={i.id} title={i.pfad} style={{ display: "flex", gap: 8, alignItems: "center", padding: "3px 0" }}>
                  <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{kurzPfad(i.pfad)}</span>
                  <span style={{ color: MUTED, whiteSpace: "nowrap" }}>{i.anzahl_dateien} Dateien{i.anzahl_ignoriert ? ` · ${i.anzahl_ignoriert} ._* ignoriert` : ""}</span>
                  {i.status !== "fertig" && <span style={{ color: ACCENT }}>{i.status} …</span>}
                  {!i.volume_gemountet && <span style={{ color: "#e2574a" }}>Volume nicht gemountet</span>}
                  {i.fehler && <span style={{ color: "#e0b84a" }} title={i.fehler}>⚠ Fehler</span>}
                  <button title="Aus der Liste entfernen (Dateien bleiben unberührt)" style={{ marginLeft: "auto", background: "transparent", border: "none", color: MUTED, cursor: "pointer" }}
                    onClick={() => void aktion(() => deleteImport(i.id))}>×</button>
                </div>
              ))}
            </div>
          );
        })}
        <div style={karte}>
          <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 8 }}>
            <b style={{ color: ACCENT }}>2 · Automatisch synchronisieren</b>
            <label style={{ marginLeft: "auto", display: "flex", gap: 4, alignItems: "center", color: MUTED }} title="Nach dem zweiten Import automatisch starten">
              <input type="checkbox" checked={autoSync} onChange={(e) => setAutoSync(e.target.checked)} /> automatisch
            </label>
          </div>
          <div style={{ color: MUTED, marginBottom: 8 }} title="Reihenfolge: Timecode → Wellenform → Klappe → Dateiname. Jede Zuordnung bekommt Status, Offset, Konfidenz und Begründung; nichts wird still entschieden.">
            {nurKameraton
              ? <>Es liegt kein getrennter Ton vor. Der Abgleich legt je Video einen Take an, der Ton der Kamera bleibt dabei erhalten.</>
              : <>Zusammengehörige Bild- und Tonaufnahmen werden über den Timecode gefunden. Jede Zuordnung ist nachvollziehbar und lässt sich prüfen. <span style={{ textDecoration: "underline dotted" }}>Wie?</span></>}
          </div>
          <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
            <button style={{ ...btn(!grundMatching, ACCENT, !!grundMatching || busy), padding: "9px 16px", fontSize: 13 }} disabled={!!grundMatching || busy} onClick={() => void matchen()}>
              {laeuft("sync") ? "Synchronisiert …" : takes.length ? "▶ Erneut synchronisieren" : "▶ Synchronisieren"}
            </button>
            {grundMatching && <span style={{ color: MUTED }}>{grundMatching}</span>}
            {takes.length > 0 && !grundMatching && <span style={{ color: MUTED }}>Manuelle Entscheidungen bleiben erhalten.</span>}
          </div>
        </div>
      </div>}

      {/* Schritt 3 */}
      {!leer && <div style={{ ...karte, flex: 1, minHeight: 0, display: "flex", flexDirection: "column", gap: 8 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
          <b style={{ color: ACCENT }}>3 · Prüfen & in Medien übernehmen</b>
          {takes.length > 0 && (["sicher", "plausibel", "unklar", "verwaist", "manuell_bestaetigt", "manuell_abgelehnt"] as TakeStatus[]).map((s) => (
            <button key={s} title={STATUS_ERKLAERUNG[s]} onClick={() => setFilter(filter === s ? "alle" : s)}
              style={{ ...btn(filter === s, STATUS_FARBE[s]), display: "flex", gap: 6, alignItems: "center", padding: "3px 8px" }}>
              <span style={{ width: 8, height: 8, borderRadius: 4, background: STATUS_FARBE[s], display: "inline-block" }} />
              {STATUS_LABEL[s]} {zaehler[s] ?? 0}
            </button>
          ))}
          <input placeholder="Suchen (Dateiname) …" value={suche} onChange={(e) => setSuche(e.target.value)} style={{ ...inp, width: 170 }} />
          <div style={{ marginLeft: "auto", display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
            {nUnklar > 0 && (
              <label style={{ display: "flex", gap: 4, alignItems: "center", color: MUTED }} title="Unklare Takes werden ohne verknüpften Ton analysiert (Warnung „Transkription auf Kamera-Ton“)">
                <input type="checkbox" checked={unklarTrotzdem} onChange={(e) => setUnklarTrotzdem(e.target.checked)} /> unklar trotzdem
              </label>
            )}
            <button style={btn(!grundAnalyse, "#7fb2ff", !!grundAnalyse || busy)} disabled={!!grundAnalyse || busy} onClick={() => setMedienDialog({})}
              title={grundAnalyse ?? "Jeder Take wird EIN Medium (Video + zugeordneter, synchroner Ton) im Medien-Panel — mit Wahl der Ordnung"}>
              In Medien übernehmen{analysierbar && !grundAnalyse ? ` (${analysierbar} Takes)` : ""}
            </button>
            {grundAnalyse && <span style={{ color: nUnklar > 0 ? "#e2574a" : MUTED }}>{grundAnalyse}</span>}
          </div>
        </div>

        <div style={{ display: "flex", gap: 10, flex: 1, minHeight: 0 }}>
          {/* Liste */}
          <div style={{ flex: "0 0 44%", overflowY: "auto", background: "#161617", border: `1px solid ${BORDER}`, borderRadius: 8 }}>
            {takes.length === 0 && (
              <div style={{ padding: 18, color: MUTED, lineHeight: 1.6 }}>
                {laeuft("import") ? "Import läuft — danach startet der Abgleich automatisch." : laeuft("sync") ? "Abgleich läuft …" : !hatVideos ? "Es fehlt noch der Video-Ordner (oben, 1a)." : nurKameraton ? "Kein getrennter Ton-Ordner. Liegt der Ton in der Kamera, ist das richtig so — „Synchronisieren“ starten, jedes Video wird dann zu einem eigenen Take." : "Beide Ordner sind da — „Synchronisieren“ starten."}
              </div>
            )}
            {zuerst.length > 0 && filter === "alle" && !suche && (
              <div style={{ padding: "6px 10px", background: "rgba(226,87,74,.12)", borderBottom: `1px solid ${BORDER}`, color: "#e2574a", position: "sticky", top: 0, zIndex: 1 }}>
                <b>Zuerst entscheiden:</b> {zuerst.length} unklare(r) Take(s) — {zuerst.map((t) => t.video?.dateiname ?? "?").join(", ")}
                <button style={{ ...btn(false, "#e2574a"), marginLeft: 8, padding: "2px 8px" }} onClick={() => setFilter("unklar")}>nur unklare zeigen</button>
              </div>
            )}
            {gruppen.map(([k, liste]) => {
              const offen = !zu.has(k);
              return (
                <div key={k}>
                  <div onClick={() => setZu((s) => { const n = new Set(s); if (n.has(k)) n.delete(k); else n.add(k); return n; })}
                    style={{ padding: "6px 10px", background: "#1a1a1c", borderBottom: `1px solid ${BORDER}`, cursor: "pointer", display: "flex", gap: 8 }}>
                    <span style={{ color: MUTED }}>{offen ? "▾" : "▸"}</span><b>{gruppenLabel(liste[0])}</b><span style={{ color: MUTED }}>{liste.length} Take(s)</span>
                  </div>
                  {offen && liste.map((t) => {
                    const primaer = t.links.find((l) => l.methode !== "verwaist") ?? null;
                    const warn = t.warnungen.length + t.links.reduce((n, l) => n + l.warnungen.length, 0);
                    const aktiv = t.id === auswahl;
                    return (
                      <div key={t.id} onClick={() => { setAuswahl(t.id); setRattachAudio(""); }}
                        style={{ padding: "6px 10px", borderBottom: `1px solid #1a1a1c`, cursor: "pointer", background: aktiv ? "#2a2f1e" : "transparent", display: "grid", gridTemplateColumns: "10px 1fr auto", gap: 8, alignItems: "center" }}>
                        <span title={STATUS_LABEL[t.status]} style={{ width: 10, height: 10, borderRadius: 5, background: STATUS_FARBE[t.status] }} />
                        <div style={{ minWidth: 0 }}>
                          <div style={{ display: "flex", gap: 8, alignItems: "baseline" }}>
                            <span style={{ fontWeight: 600, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{t.video ? t.video.dateiname : "— (Ton ohne Bild)"}</span>
                            {t.video && <span style={{ color: MUTED, fontVariantNumeric: "tabular-nums" }}>{t.video.tc_start ?? "kein TC"} · {fmtDauer(t.video.dauer_s)}</span>}
                            {t.multicam_gruppe && <span title="Multicam: lief parallel zu einer anderen Kamera, gleicher Ton" style={{ color: "#e0b84a", border: "1px solid #e0b84a", borderRadius: 4, padding: "0 4px", fontSize: 10 }}>MULTICAM</span>}
                            {t.clip_id && <span style={{ color: "#7fb2ff" }} title={`Clip ${t.clip_status}`}>● {t.clip_status}</span>}
                          </div>
                          {t.links.map((l) => (
                            <div key={l.id} style={{ color: l.methode === "verwaist" ? MUTED : TXT, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
                              ♪ {l.audio?.dateiname ?? l.audio_asset_id}
                              {l.methode !== "verwaist" && <span style={{ color: MUTED }}> · {l.methode} · {fmtOffset(l.offset_s)} · {(l.konfidenz * 100).toFixed(0)} %</span>}
                              {l.audio?.unbekannte_markierung && <span style={{ color: "#e0b84a" }} title="unbekannte Markierung — nicht interpretiert"> [{l.audio.unbekannte_markierung}?]</span>}
                            </div>
                          ))}
                          {t.status === "unklar" && <div style={{ color: "#e2574a" }}>{t.kandidaten.length} Kandidat(en) — anklicken und entscheiden</div>}
                          {t.status === "verwaist" && t.video && <div style={{ color: MUTED }}>{nurKameraton ? "Ton in der Kamera" : "kein Ton gefunden"}</div>}
                        </div>
                        <span style={{ color: warn ? "#e0b84a" : MUTED, whiteSpace: "nowrap" }} title="Warnungen">{warn ? `⚠ ${warn}` : ""}{primaer?.bestaetigt ? " ✓" : ""}</span>
                      </div>
                    );
                  })}
                </div>
              );
            })}
          </div>

          {/* Detail */}
          <div style={{ flex: 1, overflowY: "auto", background: "#161617", border: `1px solid ${BORDER}`, borderRadius: 8, padding: 12, display: "flex", flexDirection: "column", gap: 10 }}>
            {!sel && <div style={{ color: MUTED }}>{takes.length ? "Links einen Take anklicken — die A/B-Vorschau wird automatisch erzeugt." : ""}</div>}
            {sel && (<>
              <div style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
                <span title={STATUS_ERKLAERUNG[sel.status]} style={{ background: STATUS_FARBE[sel.status], color: "#000", borderRadius: 4, padding: "2px 8px", fontWeight: 600 }}>{STATUS_LABEL[sel.status]}</span>
                <b>{sel.video?.dateiname ?? "Ton ohne Bild"}</b>
                {sel.multicam_gruppe && (() => { const partner = takes.filter((t) => t.multicam_gruppe === sel.multicam_gruppe && t.id !== sel.id).map((t) => t.video?.dateiname).filter(Boolean); return <span title="Diese Kameras liefen parallel und teilen denselben Ton" style={{ color: "#e0b84a", border: "1px solid #e0b84a", borderRadius: 4, padding: "1px 6px" }}>MULTICAM{partner.length ? ` mit ${partner.join(", ")}` : ""}</span>; })()}
                {sel.video && <span style={{ color: MUTED }}>TC {sel.video.tc_start ?? "—"} ({sel.video.tc_quelle}{sel.video.ltc_kanal !== null ? `, Kanal ${sel.video.ltc_kanal}` : ""}) · {sel.video.fps ?? "?"} fps · {fmtDauer(sel.video.dauer_s)}{sel.video.scratch_kanal === null ? " · kein Kamera-Scratch" : ` · Scratch Kanal ${sel.video.scratch_kanal}`}</span>}
              </div>
              <div style={{ color: MUTED }}>{STATUS_ERKLAERUNG[sel.status]}</div>
              <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
                <button style={btn(sel.status !== "manuell_bestaetigt", "#5fbf6a", busy || sel.status === "manuell_bestaetigt")} disabled={busy || sel.status === "manuell_bestaetigt"} onClick={() => void aktion(() => takeBestaetigen(sel.id), "Take bestätigt")}>✓ Bestätigen</button>
                {sel.video && <button style={btn(false, ACCENT, busy)} disabled={busy} title="Bewusst ohne Ton freigeben — Transkription läuft dann auf der Kameraspur" onClick={() => void aktion(() => takeOhneAudioBestaetigen(sel.id), "Ohne Ton freigegeben")}>Ohne Ton freigeben</button>}
                <button style={btn(false, "#e2574a", busy || sel.status === "manuell_abgelehnt")} disabled={busy || sel.status === "manuell_abgelehnt"} title="Von der Analyse ausschließen" onClick={() => void aktion(() => takeAblehnen(sel.id), "Take abgelehnt")}>Ablehnen</button>
                {sel.video && <button style={btn(false, "#7fb2ff", busy || sel.status === "unklar" || sel.status === "manuell_abgelehnt")} disabled={busy || sel.status === "unklar" || sel.status === "manuell_abgelehnt"}
                  title={sel.status === "unklar" ? "erst entscheiden" : "Nur diesen Take als Medium übernehmen"} onClick={() => setMedienDialog({ nurTake: sel.id })}>▶ Nur diesen Take übernehmen</button>}
                <button style={btn(false, ACCENT, busy || vorschauLaeuft.has(sel.id))} disabled={busy || vorschauLaeuft.has(sel.id)} onClick={() => vorschau(sel.id)} title="A/B-Vorschau (neu) erzeugen">{vorschauLaeuft.has(sel.id) ? "Vorschau läuft …" : "Vorschau neu erzeugen"}</button>
              </div>

              {sel.video && <ABPlayer videoUrl={mediaUrl(sel.vorschau_video_url)} audioUrl={mediaUrl(primaerLink?.vorschau_audio_url)} offsetS={offsetLive} audioName={primaerLink?.audio?.dateiname ?? null} wirdErzeugt={vorschauLaeuft.has(sel.id)} />}

              {/* Kandidaten (unklar) — vor den Links, weil hier die Entscheidung fällt */}
              {sel.kandidaten.length > 0 && (
                <div style={{ background: PANEL2, borderRadius: 8, padding: 10, border: "1px solid rgba(226,87,74,.5)" }}>
                  <b style={{ color: "#e2574a" }}>Kandidaten — bitte wählen</b>
                  <div style={{ color: MUTED, marginBottom: 6 }}>Mehrere Zuordnungen sind zeitlich möglich (z. B. zwei parallel laufende Kameras). Der Automat entscheidet hier bewusst nicht.</div>
                  {sel.kandidaten.map((k, i) => (
                    <div key={i} style={{ display: "flex", gap: 8, alignItems: "center", padding: "4px 0", borderTop: i ? `1px solid ${BORDER}` : "none" }}>
                      <div style={{ flex: 1 }}>
                        <div>♪ <b>{k.audio_dateiname}</b> ↔ {k.video_dateiname} · Überlappung {(k.ueberlappung_ratio * 100).toFixed(0)} % ({k.ueberlappung_s.toFixed(1)} s) · Offset {fmtOffset(k.offset_s)}</div>
                        <div style={{ color: MUTED }}>{k.begruendung}</div>
                      </div>
                      {sel.video && k.video_asset_id === sel.video_asset_id && (
                        <button style={btn(true, "#7fb2ff", busy)} disabled={busy} onClick={() => void aktion(() => linkAnlegen(sel.id, k.audio_asset_id, k.offset_s), "Audio zugeordnet — Take bestätigt")}>Dieses Audio nehmen</button>
                      )}
                    </div>
                  ))}
                </div>
              )}

              {/* Links */}
              {sel.links.map((l) => {
                const entwurf = offsetEntwurf[l.id] ?? l.offset_s;
                const geaendert = Math.abs(entwurf - l.offset_s) > 1e-6;
                return (
                  <div key={l.id} style={{ background: PANEL2, borderRadius: 8, padding: 10, display: "flex", flexDirection: "column", gap: 6 }}>
                    <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
                      <b>♪ {l.audio?.dateiname}</b>
                      {l.audio && <span style={{ color: MUTED }}>TC {l.audio.tc_start ?? "—"} ({l.audio.tc_quelle}) · {l.audio.kanaele} Kanäle · Transkription Kanal {l.kanal_fuer_transkription}{l.audio.ixml && Array.isArray((l.audio.ixml as { tracks?: { index: number; name: string }[] }).tracks) ? ` („${((l.audio.ixml as { tracks: { index: number; name: string }[] }).tracks.find((t) => t.index === l.kanal_fuer_transkription + 1)?.name ?? "?")}“)` : ""} · {fmtDauer(l.audio.dauer_s)}</span>}
                      <span style={{ marginLeft: "auto", color: MUTED }}>{l.methode} · Konfidenz {(l.konfidenz * 100).toFixed(0)} %{l.bestaetigt ? " · ✓ bestätigt" : ""}</span>
                      <button style={btn(false, "#e2574a", busy)} disabled={busy} title="Audio von diesem Take lösen" onClick={() => void aktion(() => linkLoeschen(l.id), "Audio abgehängt")}>Abhängen</button>
                    </div>
                    <div style={{ lineHeight: 1.5 }}>{l.begruendung}</div>
                    {l.warnungen.map((w, i) => <div key={i} style={{ color: "#e0b84a" }}>⚠ {w}</div>)}
                    {l.methode !== "verwaist" && (
                      <div style={{ display: "flex", gap: 6, alignItems: "center", flexWrap: "wrap" }}>
                        <span style={{ color: MUTED }} title="offset = Audio-Start − Video-Start; negativ = Ton lief vor dem Bild los">Offset:</span>
                        {[-1, -0.1, -0.01].map((d) => <button key={d} style={btn()} onClick={() => setOffsetEntwurf((o) => ({ ...o, [l.id]: +(entwurf + d).toFixed(3) }))}>{d} s</button>)}
                        <input type="number" step="0.001" value={entwurf} onChange={(e) => setOffsetEntwurf((o) => ({ ...o, [l.id]: parseFloat(e.target.value) || 0 }))} style={{ ...inp, width: 100, fontVariantNumeric: "tabular-nums" }} />
                        {[0.01, 0.1, 1].map((d) => <button key={d} style={btn()} onClick={() => setOffsetEntwurf((o) => ({ ...o, [l.id]: +(entwurf + d).toFixed(3) }))}>+{d} s</button>)}
                        <button style={btn(geaendert, "#7fb2ff", !geaendert || busy)} disabled={!geaendert || busy} onClick={() => void aktion(async () => { await linkOffsetSetzen(l.id, entwurf); setOffsetEntwurf((o) => { const c = { ...o }; delete c[l.id]; return c; }); }, "Offset gespeichert")}>Speichern</button>
                        {geaendert && <button style={btn()} onClick={() => setOffsetEntwurf((o) => { const c = { ...o }; delete c[l.id]; return c; })}>Zurücksetzen</button>}
                      </div>
                    )}
                  </div>
                );
              })}

              {/* Anderes Audio anhängen */}
              {sel.video && (
                <div style={{ background: PANEL2, borderRadius: 8, padding: 10, display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
                  <span style={{ color: MUTED }}>{sel.links.length ? "Weiteres / anderes Audio anhängen:" : "Audio manuell anhängen:"}</span>
                  <input placeholder="Audio suchen …" value={rattachSuche} onChange={(e) => setRattachSuche(e.target.value)} style={{ ...inp, width: 150 }} />
                  <select value={rattachAudio} onChange={(e) => setRattachAudio(e.target.value)} style={{ ...inp, maxWidth: 360 }}>
                    <option value="">— wählen —</option>
                    {rattachListe.map((a) => <option key={a.id} value={a.id}>{a.dateiname} · {a.tc_start ?? "kein TC"} · {fmtDauer(a.dauer_s)}</option>)}
                  </select>
                  <button style={btn(!!rattachAudio, "#7fb2ff", !rattachAudio || busy)} disabled={!rattachAudio || busy} title="Offset aus Timecode, sonst 0 s (im A/B-Player prüfen)"
                    onClick={() => void aktion(async () => { await linkAnlegen(sel.id, rattachAudio, null); setRattachAudio(""); }, "Audio angehängt (manuell)")}>Anhängen</button>
                </div>
              )}

              {sel.warnungen.length > 0 && (
                <div style={{ background: PANEL2, borderRadius: 8, padding: 10 }}>
                  <b>Hinweise</b>
                  {sel.warnungen.map((w, i) => <div key={i} style={{ color: w.startsWith("Mehrdeutig") ? "#e2574a" : "#e0b84a", padding: "2px 0" }}>⚠ {w}</div>)}
                </div>
              )}
              {sel.video && sel.video.warnungen.length > 0 && (
                <div style={{ color: MUTED }}>{sel.video.warnungen.map((w, i) => <div key={i}>· {w}</div>)}</div>
              )}
            </>)}
          </div>
        </div>
      </div>}
    </div>
  );
}
