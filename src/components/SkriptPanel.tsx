"use client";
/**
 * Skript & Kontext — Drehbuch hochladen → Kontext aufbauen (Takes → Szenen → Story) → Rohschnitt erzeugen → in die Timeline.
 *
 * Drei Spalten: links Skript-Szenen (Zeilen, Übersetzung, Takes, Zusammenfassung), Mitte Szenen-Kontext/Ranking,
 * rechts Story + Rohschnitt. Alles, was die Automatik entschieden hat, ist sichtbar und korrigierbar (Take-Klappe,
 * Bewertung circled/ng, Übersetzung). Konzept: backend/KONTEXT_TIMELINE_KONZEPT.md
 */
import { useCallback, useEffect, useRef, useState } from "react";
import {
  fetchSkript, uploadSkript, fetchSkriptJob, kontextAufbauen, aktionenPruefen, ordnerNachSkriptSortieren, fetchKontextTakes, fetchTakeKontext, setTakeKontext,
  gesichterErkennen, fetchGesichter, gesichtBenennen, type GesichtDTO,
  updateSkriptZeile, schnittplanErzeugen, fetchSchnittplaene, fetchSchnittplan,
  type SkriptDTO, type SkriptSzeneDTO, type StoryKontextDTO, type TakeKontextDTO, type SchnittplanDTO, type SkriptJobDTO,
} from "@/lib/api";

const fmt = (s: number | null | undefined) => {
  if (s == null || !Number.isFinite(s)) return "–";
  const m = Math.floor(s / 60); const r = Math.floor(s - m * 60);
  return `${m}:${String(r).padStart(2, "0")}`;
};
const btn = (primary = false): React.CSSProperties => ({
  padding: "7px 12px", borderRadius: 7, border: primary ? "none" : "1px solid #2c2c30", cursor: "pointer", fontSize: 12,
  background: primary ? "#b9d94a" : "#1c1c1f", color: primary ? "#000" : "#ddd", fontWeight: primary ? 700 : 500,
});
const card: React.CSSProperties = { background: "#141416", border: "1px solid #232326", borderRadius: 10, padding: 12 };
const label: React.CSSProperties = { fontSize: 10, color: "#7a7a7a", textTransform: "uppercase", letterSpacing: 0.5, marginBottom: 6 };

type Props = {
  onPlanLaden: (plan: SchnittplanDTO, modus: "ersetzen" | "anhaengen") => void;
  toast?: (msg: string, art?: "ok" | "err" | "info", ms?: number) => void;
};

export default function SkriptPanel({ onPlanLaden, toast }: Props) {
  const [skript, setSkript] = useState<SkriptDTO | null>(null);
  const [story, setStory] = useState<StoryKontextDTO | null>(null);
  const [takes, setTakes] = useState<TakeKontextDTO[]>([]);
  const [job, setJob] = useState<SkriptJobDTO | null>(null);
  const [aktSzene, setAktSzene] = useState<string | null>(null);
  const [plaene, setPlaene] = useState<Array<{ id: string; name: string; erstellt_am: string | null; statistik: SchnittplanDTO["statistik"] }>>([]);
  const [plan, setPlan] = useState<SchnittplanDTO | null>(null);
  const [takeDetail, setTakeDetail] = useState<TakeKontextDTO | null>(null);
  const [gesichter, setGesichter] = useState<GesichtDTO[]>([]);
  const [dragOver, setDragOver] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const say = useCallback((m: string, a: "ok" | "err" | "info" = "info", ms = 2500) => toast?.(m, a, ms), [toast]);

  const laden = useCallback(async () => {
    try {
      const d = await fetchSkript(); setSkript(d.skript); setStory(d.story);
      if (d.skript && !aktSzene) setAktSzene(d.skript.szenen[0]?.id ?? null);
      const t = await fetchKontextTakes(); setTakes(t.takes);
      const p = await fetchSchnittplaene(); setPlaene(p);
      const g = await fetchGesichter(); setGesichter(g);
    } catch { /* Backend offline */ }
  }, [aktSzene]);
  useEffect(() => { void laden(); }, [laden]);

  // Job-Polling (Import / Kontext / Schnittplan)
  useEffect(() => {
    if (!job || job.status === "fertig" || job.status === "fehler") return;
    const t = setInterval(() => {
      fetchSkriptJob(job.id).then((j) => {
        setJob(j);
        if (j.status === "fertig") { setBusy(null); void laden(); say(j.nachricht ?? "Fertig.", "ok", 3500); if (j.typ === "schnittplan" && j.ergebnis?.plan_id) void fetchSchnittplan(String(j.ergebnis.plan_id)).then(setPlan); }
        if (j.status === "fehler") { setBusy(null); say(j.nachricht ?? "Fehler.", "err", 5000); }
      }).catch(() => {});
    }, 2500);
    return () => clearInterval(t);
  }, [job, laden, say]);

  // Sprache des Drehs. Sie bestimmt, in welche Sprache das Drehbuch übersetzt wird
  // UND in welcher Sprache Whisper die Aufnahmen liest. Bis hierher war sie fest auf
  // Deutsch verdrahtet; bei einem englischen Dreh entstanden dadurch unbrauchbare
  // Transkripte, ohne dass es irgendwo sichtbar wurde.
  const [drehsprache, setDrehsprache] = useState("de");
  // Liegt bereits ein Drehbuch vor, übernimmt die Auswahl dessen Drehsprache, damit
  // ein Austausch nicht versehentlich auf die Voreinstellung zurückfällt.
  useEffect(() => {
    const z = skript?.ziel_sprache;
    if (z) setDrehsprache((a) => (a === "de" && z !== "de" ? z : a));
  }, [skript?.ziel_sprache]);
  const starteUpload = async (file: File) => {
    try {
      setBusy("upload");
      const r = await uploadSkript(file, drehsprache);
      if (r.transkription_angepasst) {
        say(`Transkription steht jetzt auf ${drehsprache.toUpperCase()} (vorher ${(r.transkription_vorher ?? "—").toUpperCase()}).`, "ok", 6000);
      }
      setJob({ id: r.job_id, typ: "skript_import", status: "wartend", fortschritt: 0, nachricht: "Drehbuch wird gelesen…", ergebnis: null });
    }
    catch (e) { setBusy(null); say((e as Error).message, "err"); }
  };
  const SprachWahl = () => (
    <label style={{ display: "inline-flex", alignItems: "center", gap: 7, fontSize: 12, color: "#9a9a9a" }}>
      Sprache des Drehs
      <select
        value={drehsprache}
        onChange={(e) => setDrehsprache(e.target.value)}
        title="In dieser Sprache wird auf dem Set gesprochen. Sie steuert die Übersetzung des Drehbuchs und die Transkription der Aufnahmen."
        style={{ background: "#111", color: "#ddd", border: "1px solid #2a2a2e", borderRadius: 6, padding: "4px 6px", fontSize: 12, fontFamily: "inherit" }}
      >
        <option value="de">Deutsch</option>
        <option value="en">Englisch</option>
        <option value="fr">Französisch</option>
        <option value="es">Spanisch</option>
        <option value="it">Italienisch</option>
      </select>
    </label>
  );
  const starteKontext = async () => {
    try { setBusy("kontext"); const r = await kontextAufbauen(true); setJob({ id: r.job_id, typ: "kontext", status: "wartend", fortschritt: 0, nachricht: "Kontext-Aufbau wartet…", ergebnis: null }); }
    catch (e) { setBusy(null); say((e as Error).message, "err"); }
  };
  const starteBildpruefung = async () => {
    try { setBusy("aktionen"); const r = await aktionenPruefen(); setJob({ id: r.job_id, typ: "aktionen", status: "wartend", fortschritt: 0, nachricht: "Bildprüfung wartet…", ergebnis: null }); }
    catch (e) { setBusy(null); say((e as Error).message, "err"); }
  };
  const starteGesichter = async () => {
    try { setBusy("gesichter"); const r = await gesichterErkennen(); setJob({ id: r.job_id, typ: "gesichter", status: "wartend", fortschritt: 0, nachricht: "Gesichtserkennung wartet…", ergebnis: null }); }
    catch (e) { setBusy(null); say((e as Error).message, "err"); }
  };
  const sortiereOrdner = async () => {
    try { const r = await ordnerNachSkriptSortieren(); say(`${r.verschoben} Clips in Skript-Szenen-Ordner verschoben (${r.ordner.join(", ")}).`, "ok", 4000); }
    catch (e) { say((e as Error).message, "err"); }
  };
  const startePlan = async (modus: "rohschnitt" | "feinschnitt" = "rohschnitt") => {
    try { setBusy("plan"); const r = await schnittplanErzeugen({ modus, name: modus === "feinschnitt" ? undefined : undefined }); setJob({ id: r.job_id, typ: "schnittplan", status: "wartend", fortschritt: 0, nachricht: modus === "feinschnitt" ? "Feinschnitt wird zusammengestellt…" : "Rohschnitt wird zusammengestellt…", ergebnis: null }); }
    catch (e) { setBusy(null); say((e as Error).message, "err"); }
  };

  const szene = skript?.szenen.find((s) => s.id === aktSzene) ?? null;
  const takesDerSzene = szene ? takes.filter((t) => t.skript_szene_id === szene.id) : [];
  const zugeordnet = takes.filter((t) => t.skript_szene_id).length;

  // ── Start-Zustand: kein Skript ──
  if (!skript) {
    return (
      <div style={{ flex: 1, display: "flex", alignItems: "center", justifyContent: "center" }}>
        <div
          onDragOver={(e) => { e.preventDefault(); setDragOver(true); }} onDragLeave={() => setDragOver(false)}
          onDrop={(e) => { e.preventDefault(); setDragOver(false); const f = e.dataTransfer.files?.[0]; if (f) void starteUpload(f); }}
          style={{ width: 520, padding: 36, borderRadius: 16, border: `2px dashed ${dragOver ? "#b9d94a" : "#3a3a3e"}`, background: dragOver ? "rgba(185,217,74,0.06)" : "#121214", textAlign: "center" }}
        >
          <svg width="44" height="44" viewBox="0 0 24 24" fill="none" stroke="#b9d94a" strokeWidth="1.6" style={{ margin: "0 auto 12px" }}><path d="M14 3H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9z" /><path d="M14 3v6h6M8 13h8M8 17h6" /></svg>
          <div style={{ fontSize: 16, fontWeight: 700, color: "#eee", marginBottom: 6 }}>Drehbuch hochladen</div>
          <div style={{ fontSize: 12, color: "#9a9a9a", lineHeight: 1.6, marginBottom: 16 }}>
            PDF, TXT oder Fountain. Das System liest alle Szenen und Dialogzeilen, übersetzt sie in die Drehsprache, ordnet jeden analysierten Take einer Skriptszene zu (gesprochene Klappe + Dialog-Alignment) und baut daraus Szenen- und Story-Kontext — die Grundlage des Rohschnitts.
          </div>
          <input ref={fileRef} type="file" accept=".pdf,.txt,.fountain,.md" style={{ display: "none" }} onChange={(e) => { const f = e.target.files?.[0]; if (f) void starteUpload(f); }} />
          <div style={{ display: "flex", alignItems: "center", justifyContent: "center", gap: 14, flexWrap: "wrap" }}>
            <SprachWahl />
            <button style={btn(true)} onClick={() => fileRef.current?.click()} disabled={busy === "upload"}>{busy === "upload" ? "Wird gelesen…" : "Datei wählen"}</button>
          </div>
          <div style={{ marginTop: 10, fontSize: 11, color: "#7a7a7a", lineHeight: 1.5 }}>
            Die gewählte Sprache gilt auch für die Transkription der Aufnahmen. Stimmt sie nicht
            mit dem Gesprochenen überein, wird das Transkript unbrauchbar.
          </div>
          {job && job.status !== "fertig" && <div style={{ marginTop: 14, fontSize: 11, color: "#b9d94a" }}>{job.nachricht} ({job.fortschritt}%)</div>}
        </div>
      </div>
    );
  }

  return (
    <div style={{ flex: 1, display: "flex", flexDirection: "column", gap: 10, minHeight: 0 }}>
      {/* Kopf */}
      <div style={{ display: "flex", alignItems: "center", gap: 12, flexWrap: "wrap" }}>
        <div>
          <div style={{ fontSize: 15, fontWeight: 700, color: "#f0f0f0" }}>„{skript.titel ?? skript.name}“ <span style={{ fontSize: 11, color: "#7a7a7a", fontWeight: 400 }}>{skript.szenen.length} Szenen · Skript {skript.sprache?.toUpperCase()} → Dreh {skript.ziel_sprache?.toUpperCase()} · {zugeordnet}/{takes.length} Takes zugeordnet</span></div>
        </div>
        <div style={{ marginLeft: "auto", display: "flex", gap: 8, alignItems: "center" }}>
          {job && job.status !== "fertig" && job.status !== "fehler" && (
            <span style={{ fontSize: 11, color: "#b9d94a", maxWidth: 420, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>⟳ {job.nachricht} ({job.fortschritt}%)</span>
          )}
          <input ref={fileRef} type="file" accept=".pdf,.txt,.fountain,.md" style={{ display: "none" }} onChange={(e) => { const f = e.target.files?.[0]; if (f) void starteUpload(f); }} />
          <SprachWahl />
          <button style={btn()} onClick={() => fileRef.current?.click()} disabled={!!busy}>Anderes Drehbuch…</button>
          <button style={btn()} onClick={() => void starteKontext()} disabled={!!busy} title="Klappen, Spiel/Produktion, Skript-Alignment je Take → Szenen-Kontext (LLM) → Story">{busy === "kontext" ? "Kontext wird aufgebaut…" : zugeordnet ? "Kontext neu aufbauen" : "Kontext aufbauen"}</button>
          <button style={btn()} onClick={() => void starteBildpruefung()} disabled={!!busy || !zugeordnet} title="Skript-gesteuerte Bildprüfung: Fragen aus den Aktionszeilen jeder Szene → Bildmodell auf dichten Frames aller Takes der Szene → welche Skript-Aktion ist wann im Bild (gedreht / unsicher / fehlt)">{busy === "aktionen" ? "Bildprüfung läuft…" : "Bildprüfung nach Skript"}</button>
          <button style={btn()} onClick={() => void starteGesichter()} disabled={!!busy || !zugeordnet} title="Gesichter aller Takes → Personen (Cluster) → Namen aus der Skript-Präsenz je Szene → wer ist wann im Bild (für Reaktionsschnitte)">{busy === "gesichter" ? "Gesichter…" : "Gesichter ↔ Figuren"}</button>
          <button style={btn()} onClick={() => void sortiereOrdner()} disabled={!!busy || !zugeordnet} title="Clips in die Medien-Ordner „Szene N“ der Skript-Szene verschieben (Klappe/Alignment), nicht nach Kamera-Dateinamen">Ordner nach Szenen sortieren</button>
          <button style={btn()} onClick={() => void startePlan("rohschnitt")} disabled={!!busy || !zugeordnet} title="Regelbasierter Rohschnitt in Skript-Reihenfolge (Master + Coverage, Inserts, stumme Einstellungen)">{busy === "plan" ? "…" : "Rohschnitt"}</button>
          <button style={btn(true)} onClick={() => void startePlan("feinschnitt")} disabled={!!busy || !zugeordnet} title="Feinschnitt: Cutaways in Sprechpausen mit Ton-Brücke (L-Cut), Höhepunkte statt Blöcke bei Handlung ohne Dialog, enge Handles, Fades an Szenengrenzen">{busy === "plan" ? "Schnitt wird erzeugt…" : "Feinschnitt erzeugen"}</button>
        </div>
      </div>

      <div style={{ flex: 1, display: "grid", gridTemplateColumns: "300px 1fr 360px", gap: 10, minHeight: 0 }}>
        {/* Szenenliste */}
        <div style={{ ...card, overflowY: "auto", padding: 8 }}>
          <div style={label}>Skript-Szenen</div>
          {skript.szenen.map((s) => (
            <button key={s.id} onClick={() => setAktSzene(s.id)} style={{ display: "block", width: "100%", textAlign: "left", padding: "8px 10px", borderRadius: 8, marginBottom: 4, border: "1px solid " + (s.id === aktSzene ? "rgba(185,217,74,0.5)" : "transparent"), background: s.id === aktSzene ? "rgba(185,217,74,0.08)" : "transparent", color: "#ddd", cursor: "pointer" }}>
              <div style={{ fontSize: 12, fontWeight: 600 }}>{s.nummer}. {s.innen_aussen} {s.ort} <span style={{ color: "#7a7a7a", fontWeight: 400 }}>– {s.tageszeit}</span></div>
              <div style={{ fontSize: 10, color: "#8a8a8a", marginTop: 2 }}>{s.zeilen.filter((z) => z.art === "dialog").length} Dialogzeilen · {s.takes} Takes{s.kontext?.zusammenfassung ? " · Kontext ✓" : ""}</div>
            </button>
          ))}
          {gesichter.length > 0 && (
            <div style={{ marginTop: 10, paddingTop: 10, borderTop: "1px solid #232326" }}>
              <div style={label}>Personen (Gesichter ↔ Figuren)</div>
              {gesichter.filter((g) => g.takes >= 2).map((g) => (
                <div key={g.id} style={{ display: "flex", gap: 8, alignItems: "center", padding: "4px 0" }}>
                  {g.thumb_url ? <img src={g.thumb_url} alt="" style={{ width: 40, height: 40, objectFit: "cover", borderRadius: 6, background: "#000" }} /> : <div style={{ width: 40, height: 40, borderRadius: 6, background: "#222" }} />}
                  <div style={{ flex: 1, minWidth: 0, fontSize: 11 }}>
                    <div style={{ display: "flex", gap: 4, alignItems: "center" }}>
                      <input defaultValue={g.name_film ?? ""} placeholder="Film-Name" onBlur={(e) => { const v = e.target.value.trim(); if (v !== (g.name_film ?? "")) void gesichtBenennen(g.id, { name_film: v }).then(() => { say("Name gespeichert.", "ok"); void laden(); }); }} style={{ width: 90, background: "#111", color: "#ddd", border: "1px solid #2a2a2e", borderRadius: 4, padding: "2px 5px", fontSize: 11 }} />
                      <span style={{ color: "#7a7a7a" }}>= Skript</span>
                      <input defaultValue={g.name_skript ?? ""} placeholder="FIGUR" onBlur={(e) => { const v = e.target.value.trim(); if (v.toUpperCase() !== (g.name_skript ?? "")) void gesichtBenennen(g.id, { name_skript: v }).then(() => { say("Zuordnung gespeichert.", "ok"); void laden(); }); }} style={{ width: 80, background: "#111", color: "#ddd", border: "1px solid #2a2a2e", borderRadius: 4, padding: "2px 5px", fontSize: 11, textTransform: "uppercase" }} />
                    </div>
                    <div style={{ color: "#7a7a7a", marginTop: 2 }}>{g.takes} Takes · {g.anzahl} Gesichter{g.score != null ? ` · Skript-Präsenz ${g.score.toFixed(2)}` : ""}{g.manuell ? " · manuell" : ""}{g.szenen_anteil ? ` · Szenen: ${Object.entries(g.szenen_anteil).filter(([, v]) => v > 0).map(([k, v]) => `${k} (${Math.round(v * 100)}%)`).join(", ")}` : ""}</div>
                  </div>
                </div>
              ))}
            </div>
          )}
          {story && (
            <div style={{ marginTop: 10, paddingTop: 10, borderTop: "1px solid #232326" }}>
              <div style={label}>Story</div>
              <div style={{ fontSize: 11, color: "#cfcfcf", lineHeight: 1.5 }}>{story.zusammenfassung ?? "—"}</div>
              {story.figuren && story.figuren.length > 0 && (
                <div style={{ marginTop: 8, fontSize: 11, color: "#bdbdbd" }}>
                  <span style={{ color: "#7a7a7a" }}>Figuren (Skript → Dreh): </span>
                  {story.figuren.map((f) => `${f.skript} → ${f.film ?? "?"}`).join(" · ")}
                </div>
              )}
              {story.motive && story.motive.length > 0 && <div style={{ marginTop: 6, fontSize: 11, color: "#9ac2ff" }}>Motive: {story.motive.join(" · ")}</div>}
            </div>
          )}
        </div>

        {/* Szene: Skript + Kontext + Takes */}
        <div style={{ ...card, overflowY: "auto" }}>
          {szene ? <SzeneAnsicht szene={szene} takes={takesDerSzene} onTakeDetail={(id) => fetchTakeKontext(id).then(setTakeDetail).catch((e) => say((e as Error).message, "err"))} onRefresh={laden} say={say} /> : <div style={{ color: "#777" }}>Szene wählen.</div>}
        </div>

        {/* Rohschnitt */}
        <div style={{ ...card, overflowY: "auto", display: "flex", flexDirection: "column", gap: 10 }}>
          <div style={label}>Rohschnitt</div>
          {plaene.length === 0 && !plan && <div style={{ fontSize: 12, color: "#8a8a8a", lineHeight: 1.5 }}>Noch kein Rohschnitt. Erst „Kontext aufbauen“, dann „Rohschnitt erzeugen“ — jedes Segment kommt mit Grund und Beleg.</div>}
          {plaene.length > 0 && (
            <select value={plan?.id ?? ""} onChange={(e) => { const id = e.target.value; if (id) void fetchSchnittplan(id).then(setPlan); }} style={{ background: "#111", color: "#ddd", border: "1px solid #2a2a2e", borderRadius: 6, padding: "5px 6px", fontSize: 12 }}>
              <option value="">Rohschnitt wählen…</option>
              {plaene.map((p) => <option key={p.id} value={p.id}>{p.name} · {p.statistik?.eintraege ?? "?"} Seg · {fmt(p.statistik?.dauer_s)}</option>)}
            </select>
          )}
          {plan && (
            <>
              <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
                <button style={btn(true)} onClick={() => onPlanLaden(plan, "ersetzen")}>In Timeline laden</button>
                <button style={btn()} onClick={() => onPlanLaden(plan, "anhaengen")}>Anhängen</button>
                <a href={`/api/skript/schnittplan/${plan.id}/bericht`} target="_blank" rel="noreferrer" style={{ ...btn(), textDecoration: "none", display: "inline-block" }} title="Prüfbericht zum Gegenlesen: Skriptzeile → Take, Zeit, gesagter Text, Score, Grund">Prüfbericht öffnen</a>
              </div>
              <div style={{ fontSize: 11, color: "#9a9a9a" }}>{plan.statistik?.eintraege} Segmente · {fmt(plan.statistik?.dauer_s)} · {plan.statistik?.szenen_mit_material}/{plan.statistik?.szenen} Szenen mit Material</div>
              {plan.statistik?.luecken && plan.statistik.luecken.length > 0 && (
                <details style={{ fontSize: 11, color: "#e5c100" }}>
                  <summary style={{ cursor: "pointer" }}>{plan.statistik.luecken.length} Lücke(n) — nicht gedreht/gefunden</summary>
                  <ul style={{ margin: "4px 0 0 16px", padding: 0, color: "#cfcfcf" }}>
                    {plan.statistik.luecken.map((l, i) => <li key={i}>Sz {String(l.szene)}{l.zeile != null ? ` Z${String(l.zeile)} ${String(l.figur ?? "")}: „${String(l.text ?? "").slice(0, 60)}“` : ""} — {String(l.grund)}</li>)}
                  </ul>
                </details>
              )}
              <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
                {plan.eintraege.map((e) => (
                  <div key={e.nr} style={{ padding: "6px 8px", borderRadius: 6, background: "#1a1a1d", borderLeft: `3px solid ${e.art === "dialog" ? "#b9d94a" : e.art === "insert" ? "#9ac2ff" : e.art === "cutaway" ? "#f0a0a0" : e.art === "audio" ? "#7a7a7a" : "#e5c100"}` }}>
                    <div style={{ fontSize: 11, color: "#eee", display: "flex", gap: 6 }}>
                      <span style={{ color: "#7a7a7a", minWidth: 18 }}>{e.nr}</span>
                      <span style={{ fontWeight: 600 }}>Sz {e.szene}</span>
                      <span style={{ color: "#9a9a9a" }}>{e.einstellung ?? "?"} T{e.take ?? "?"}</span>
                      <span style={{ marginLeft: "auto", fontFamily: "ui-monospace, monospace", color: "#9a9a9a" }}>{fmt(e.in_s)}–{fmt(e.out_s)} · {e.dauer.toFixed(1)}s</span>
                    </div>
                    <div style={{ fontSize: 10, color: "#8a8a8a", marginTop: 2 }}>{e.dateiname} · {e.grund}</div>
                    {e.beleg.length > 0 && <div style={{ fontSize: 10, color: "#6f8fb0", marginTop: 2 }}>{e.beleg[0]}{e.beleg.length > 1 ? ` (+${e.beleg.length - 1})` : ""}</div>}
                  </div>
                ))}
              </div>
            </>
          )}
        </div>
      </div>

      {takeDetail && <TakeDetailModal tk={takeDetail} szenen={skript.szenen} gesichter={gesichter} onClose={() => setTakeDetail(null)} onSaved={() => { setTakeDetail(null); void laden(); }} say={say} />}
    </div>
  );
}

function SzeneAnsicht({ szene, takes, onTakeDetail, onRefresh, say }: { szene: SkriptSzeneDTO; takes: TakeKontextDTO[]; onTakeDetail: (clipId: string) => void; onRefresh: () => void; say: (m: string, a?: "ok" | "err" | "info", ms?: number) => void }) {
  const [editZeile, setEditZeile] = useState<string | null>(null);
  const [editText, setEditText] = useState("");
  const k = szene.kontext;
  const ranking = k?.take_ranking ?? [];
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
      <div>
        <div style={{ fontSize: 14, fontWeight: 700, color: "#f0f0f0" }}>{szene.nummer}. {szene.ueberschrift}</div>
        <div style={{ fontSize: 11, color: "#8a8a8a" }}>Figuren (Skript): {szene.figuren.join(", ") || "—"} · {takes.length} Takes zugeordnet</div>
      </div>
      {k?.zusammenfassung && (
        <div style={{ background: "rgba(185,217,74,0.06)", border: "1px solid rgba(185,217,74,0.25)", borderRadius: 8, padding: 10 }}>
          <div style={label}>Was passiert · wie gedreht</div>
          <div style={{ fontSize: 12, color: "#e6e6e6", lineHeight: 1.55 }}>{k.zusammenfassung}</div>
          {k.figuren && k.figuren.length > 0 && <div style={{ fontSize: 11, color: "#bdbdbd", marginTop: 6 }}>Figuren: {k.figuren.map((f) => `${f.skript} → ${f.film ?? "?"}`).join(" · ")}</div>}
          {k.beats && k.beats.length > 0 && (
            <ol style={{ margin: "8px 0 0 16px", padding: 0, fontSize: 11, color: "#cfcfcf", lineHeight: 1.5 }}>
              {k.beats.map((b, i) => <li key={i} style={{ color: b.gedreht === false ? "#e5c100" : "#cfcfcf" }}>{b.text} {b.gedreht === false && "(nicht gedreht)"}</li>)}
            </ol>
          )}
          {k.unsicher && k.unsicher.length > 0 && <div style={{ fontSize: 10, color: "#e5c100", marginTop: 6 }}>Unsicher: {k.unsicher.join(" · ")}</div>}
        </div>
      )}
      {k?.aktions_coverage && Object.keys(k.aktions_coverage).length > 0 && (
        <div style={{ background: "#17181b", border: "1px solid #232326", borderRadius: 8, padding: 10 }}>
          <div style={label}>Skript-Aktionen im Bild (Bildprüfung)</div>
          {Object.entries(k.aktions_coverage).sort((a, b) => Number(a[0]) - Number(b[0])).map(([nr, a]) => (
            <div key={nr} style={{ display: "grid", gridTemplateColumns: "30px 74px 1fr", gap: 8, fontSize: 11, padding: "3px 0", borderBottom: "1px dashed #222" }}>
              <span style={{ color: "#6a6a6a", fontFamily: "ui-monospace, monospace" }}>A{nr}</span>
              <span style={{ color: a.status === "gedreht" ? "#96d996" : a.status === "unsicher" ? "#e5c100" : "#e88", fontWeight: 600 }}>{a.status}</span>
              <span style={{ color: "#cfcfcf" }}>
                {a.text.slice(0, 110)}{a.text.length > 110 ? "…" : ""}
                {a.takes.length > 0 && <span style={{ display: "block", color: "#8a8a8a", marginTop: 2 }}>{a.takes.slice(0, 4).map((t) => `${t.dateiname?.slice(7, 21) ?? t.clip_id.slice(0, 8)} ${t.einstellung ?? ""}${t.spans && t.spans.length ? ` ${t.spans.map((sp) => `${fmt(sp[0])}–${fmt(sp[1])}`).join(", ")}` : t.clip_sim_rel != null ? ` (CLIP +${t.clip_sim_rel.toFixed(2)})` : ""}`).join(" · ")}{a.takes.length > 4 ? ` · +${a.takes.length - 4}` : ""}</span>}
              </span>
            </div>
          ))}
        </div>
      )}
      <div>
        <div style={label}>Skript</div>
        <div style={{ display: "flex", flexDirection: "column", gap: 3 }}>
          {szene.zeilen.map((z) => (
            <div key={z.id} style={{ display: "grid", gridTemplateColumns: "30px 90px 1fr", gap: 8, fontSize: 11, padding: "3px 4px", borderRadius: 4, background: z.art === "dialog" ? "#1a1a1d" : "transparent" }}>
              <span style={{ color: "#6a6a6a", fontFamily: "ui-monospace, monospace" }}>{z.art === "dialog" ? `Z${z.nr}` : z.art === "aktion" ? `A${z.nr}` : ""}</span>
              <span style={{ color: z.art === "dialog" ? "#b9d94a" : "#7a7a7a", fontWeight: 600 }}>{z.art === "dialog" ? z.figur : z.art === "uebergang" ? "—" : "Aktion"}</span>
              <span style={{ color: z.art === "dialog" ? "#eee" : "#9a9a9a", fontStyle: z.art === "aktion" ? "italic" : "normal", lineHeight: 1.45 }}>
                {z.text}
                {z.art === "dialog" && (
                  editZeile === z.id ? (
                    <span style={{ display: "flex", gap: 6, marginTop: 3 }}>
                      <input value={editText} onChange={(e) => setEditText(e.target.value)} style={{ flex: 1, background: "#111", color: "#ddd", border: "1px solid #2a2a2e", borderRadius: 4, padding: "2px 6px", fontSize: 11 }} />
                      <button style={btn(true)} onClick={() => { void updateSkriptZeile(z.id, { text_ziel: editText }).then(() => { setEditZeile(null); onRefresh(); say("Übersetzung gespeichert.", "ok"); }).catch((e) => say((e as Error).message, "err")); }}>OK</button>
                      <button style={btn()} onClick={() => setEditZeile(null)}>✕</button>
                    </span>
                  ) : (
                    <span style={{ display: "block", color: "#9ac2ff", marginTop: 1, cursor: "text" }} title="Klicken zum Korrigieren der Übersetzung" onClick={() => { setEditZeile(z.id); setEditText(z.text_ziel ?? ""); }}>
                      ⟶ {z.text_ziel ?? <i style={{ color: "#666" }}>noch nicht übersetzt</i>}{z.text_ziel_quelle === "manuell" ? " ✎" : ""}
                    </span>
                  )
                )}
              </span>
            </div>
          ))}
        </div>
      </div>
      <div>
        <div style={label}>Takes · Ranking (je Einstellung, mit Gründen)</div>
        {ranking.length === 0 && takes.length === 0 && <div style={{ fontSize: 11, color: "#777" }}>Keine Takes zugeordnet — „Kontext aufbauen“.</div>}
        <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
          {(ranking.length ? ranking : takes.map((t) => ({ clip_id: t.clip_id, dateiname: t.dateiname, einstellung: t.einstellung ?? "?", take: t.slate_take, score: 0, gruende: [], abdeckung: t.abdeckung, spiel: [t.spiel_start_s, t.spiel_ende_s] as [number | null, number | null], ng: t.ng }))).map((r) => {
            const tk = takes.find((t) => t.clip_id === r.clip_id);
            return (
              <div key={r.clip_id} style={{ display: "flex", alignItems: "center", gap: 8, padding: "6px 8px", borderRadius: 6, background: "#1a1a1d", fontSize: 11 }}>
                <span style={{ fontWeight: 700, color: "#b9d94a", minWidth: 52 }}>{r.einstellung} T{r.take ?? "?"}</span>
                <span style={{ color: "#ddd", flex: 1, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{r.dateiname?.replace(/\.[^.]+$/, "")}</span>
                <span style={{ color: "#9a9a9a" }} title="Anteil gedeckter Skriptzeilen">{r.abdeckung != null ? `${Math.round(r.abdeckung * 100)} %` : "—"}</span>
                <span style={{ color: "#9a9a9a", fontFamily: "ui-monospace, monospace" }}>{fmt(r.spiel?.[0])}–{fmt(r.spiel?.[1])}</span>
                {r.ng?.abbruch && <span style={{ color: "#e5c100" }} title={(r.ng.gruende ?? []).join(" · ")}>Abbruch</span>}
                {tk?.slate_konflikt && <span style={{ color: "#e5c100" }} title="Sprech-Klappe ≠ Dateiname">Klappe≠Datei</span>}
                {tk?.slate_quelle && tk.slate_quelle !== "audio" && <span style={{ color: "#7a7a7a" }} title="Quelle der Szenenzuordnung (keine Sprech-Klappe erkannt)">via {tk.slate_quelle === "dateiname" ? "Dateiname" : tk.slate_quelle === "inhalt" ? "Dialog-Inhalt" : tk.slate_quelle}</span>}
                {tk?.bewertung && <span style={{ color: tk.bewertung === "ng" ? "#e88" : "#96d996" }}>{tk.bewertung}</span>}
                <span style={{ color: "#7a7a7a", fontFamily: "ui-monospace, monospace" }}>{r.score.toFixed(2)}</span>
                <button style={{ ...btn(), padding: "3px 8px" }} onClick={() => onTakeDetail(r.clip_id)}>Details</button>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}

function TakeDetailModal({ tk, szenen, gesichter, onClose, onSaved, say }: { tk: TakeKontextDTO; szenen: SkriptSzeneDTO[]; gesichter: GesichtDTO[]; onClose: () => void; onSaved: () => void; say: (m: string, a?: "ok" | "err" | "info", ms?: number) => void }) {
  const [slate, setSlate] = useState(tk.slate_szene ?? "");
  const [takeNr, setTakeNr] = useState(tk.slate_take != null ? String(tk.slate_take) : "");
  const [bew, setBew] = useState(tk.bewertung ?? "");
  const szeneNr = (slate || "").split(".")[0];
  const sz = szenen.find((s) => s.nummer === szeneNr);
  const zeilenMap = new Map((sz?.zeilen ?? []).map((z) => [z.id, z]));
  return (
    <div onClick={onClose} style={{ position: "fixed", inset: 0, zIndex: 3000, background: "rgba(0,0,0,0.7)", display: "flex", alignItems: "center", justifyContent: "center" }}>
      <div onClick={(e) => e.stopPropagation()} style={{ width: "min(900px, 94vw)", height: "min(720px, 90vh)", background: "#161617", border: "1px solid #2a2a2e", borderRadius: 12, display: "flex", flexDirection: "column", overflow: "hidden" }}>
        <div style={{ padding: "12px 16px", borderBottom: "1px solid #232326", display: "flex", alignItems: "center", gap: 12 }}>
          <div style={{ fontWeight: 700, color: "#eee" }}>{tk.dateiname}</div>
          <div style={{ fontSize: 11, color: "#8a8a8a" }}>Spiel {fmt(tk.spiel_start_s)}–{fmt(tk.spiel_ende_s)} · Abdeckung {tk.abdeckung != null ? Math.round(tk.abdeckung * 100) + " %" : "—"} · Quelle {tk.slate_quelle}{tk.slate_konflikt ? " · Klappe ≠ Dateiname" : ""}</div>
          <button onClick={onClose} style={{ marginLeft: "auto", background: "transparent", border: "none", color: "#aaa", fontSize: 18, cursor: "pointer" }}>✕</button>
        </div>
        <div style={{ padding: 12, display: "flex", gap: 10, alignItems: "center", borderBottom: "1px solid #232326", fontSize: 12, color: "#ddd", flexWrap: "wrap" }}>
          <label>Klappe (Szene.Einstellung) <input value={slate} onChange={(e) => setSlate(e.target.value)} placeholder="5.2.1" style={{ width: 70, marginLeft: 6, background: "#111", color: "#ddd", border: "1px solid #2a2a2e", borderRadius: 4, padding: "3px 6px" }} /></label>
          <label>Take <input value={takeNr} onChange={(e) => setTakeNr(e.target.value)} style={{ width: 40, marginLeft: 6, background: "#111", color: "#ddd", border: "1px solid #2a2a2e", borderRadius: 4, padding: "3px 6px" }} /></label>
          <label>Bewertung
            <select value={bew} onChange={(e) => setBew(e.target.value)} style={{ marginLeft: 6, background: "#111", color: "#ddd", border: "1px solid #2a2a2e", borderRadius: 4, padding: "3px 6px" }}>
              <option value="">— automatisch</option><option value="circled">circled (bevorzugen)</option><option value="ok">ok</option><option value="ng">ng (nicht verwenden)</option>
            </select>
          </label>
          <button style={btn(true)} onClick={() => { void setTakeKontext(tk.clip_id, { slate_szene: slate, slate_take: takeNr ? Number(takeNr) : undefined, bewertung: bew || null }).then((r) => { say(r.hinweis ?? "Gespeichert.", "ok", 4000); onSaved(); }).catch((e) => say((e as Error).message, "err")); }}>Speichern</button>
          <span style={{ fontSize: 10, color: "#7a7a7a" }}>Manuelle Klappe gewinnt gegen Automatik.</span>
        </div>
        <div style={{ flex: 1, overflowY: "auto", padding: 12, display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
          <div>
            <div style={label}>Gesprochen → Skriptzeile</div>
            {(tk.zeilen ?? []).map((z, i) => {
              const sk = z.skript_zeile_id ? zeilenMap.get(z.skript_zeile_id) : null;
              return (
                <div key={i} style={{ display: "grid", gridTemplateColumns: "44px 1fr", gap: 6, fontSize: 11, padding: "2px 0", color: z.art === "spiel" ? "#ddd" : "#666" }}>
                  <span style={{ fontFamily: "ui-monospace, monospace", color: "#7a7a7a" }}>{fmt(z.start)}</span>
                  <span>
                    {z.art !== "spiel" && <span style={{ color: "#6a6a6a", marginRight: 4 }}>[{z.art}]</span>}
                    {z.sprecher && <span style={{ color: "#9ac2ff", marginRight: 4 }}>{z.sprecher.replace("SPEAKER_0", "Sprecher ")}</span>}
                    {z.text}
                    {sk && <span style={{ color: "#b9d94a" }}> → Z{sk.nr} {sk.figur}: „{sk.text.slice(0, 50)}“ ({z.score?.toFixed(2)})</span>}
                  </span>
                </div>
              );
            })}
          </div>
          <div>
            <div style={label}>Bildverlauf</div>
            {(tk.bildverlauf ?? []).map((b, i) => (
              <div key={i} style={{ fontSize: 11, color: "#cfcfcf", padding: "2px 0" }}><span style={{ fontFamily: "ui-monospace, monospace", color: "#7a7a7a", marginRight: 6 }}>{fmt(b.t)}</span>{b.beschreibung}{b.personen != null ? ` (${b.personen} P.)` : ""}</div>
            ))}
            {tk.ng?.gruende && tk.ng.gruende.length > 0 && <div style={{ marginTop: 8, fontSize: 11, color: "#e5c100" }}>NG-Signale: {tk.ng.gruende.join(" · ")}</div>}
            {tk.gesichter && Object.keys(tk.gesichter).length > 0 && (
              <div style={{ marginTop: 10 }}>
                <div style={label}>Im Bild (Gesichter)</div>
                {Object.entries(tk.gesichter).sort((a, b) => b[1].frames - a[1].frames).map(([gid, g]) => {
                  const p = gesichter.find((x) => x.id === gid);
                  return <div key={gid} style={{ fontSize: 11, color: "#cfcfcf", padding: "2px 0" }}>{p?.name_film ?? p?.name_skript ?? `Person ${p?.idx ?? "?"}`}: {Math.round(g.anteil * 100)} % der Frames · {g.spans.map((sp) => `${fmt(sp[0])}–${fmt(sp[1])}`).join(", ")}</div>;
                })}
              </div>
            )}
            {tk.aktionen && Object.keys(tk.aktionen).length > 0 && (
              <div style={{ marginTop: 10 }}>
                <div style={label}>Skript-Aktionen im Bild (Bildprüfung)</div>
                {Object.entries(tk.aktionen).sort((a, b) => Number(a[0]) - Number(b[0])).map(([nr, a]) => (
                  <div key={nr} style={{ fontSize: 11, color: a.spans && a.spans.length ? "#96d996" : "#7a7a7a", padding: "2px 0" }}>
                    A{nr} {a.label ? `„${a.label}“` : ""}: {a.spans && a.spans.length ? a.spans.map((sp) => `${fmt(sp[0])}–${fmt(sp[1])}`).join(", ") : "nicht bestätigt"} <span style={{ color: "#6a6a6a" }}>({a.ja}/{a.frames} Frames{a.clip_sim_rel != null ? `, CLIP +${a.clip_sim_rel.toFixed(2)}` : ""})</span>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
