"""Prüfbericht eines Schnittplans — HTML, selbsttragend, zum manuellen Gegenlesen:
je Szene: Skriptzeile (Original + Übersetzung) → Take/Einstellung, Timeline-Position, In/Out, tatsächlich gesprochener
Text, Alignment-Score, Grund/Beleg; stumme Segmente mit Bildbeschreibung; Cutaways; Lücken. Kein LLM."""
from __future__ import annotations

import html
from collections import defaultdict

from backend.core.database import Clip, Schnittplan, Skript, TakeKontext, SzenenKontext


def _tc(s: float | None) -> str:
    if s is None:
        return "–"
    m = int(s // 60); r = s - m * 60
    return f"{m}:{r:04.1f}"


def html_bericht(db, plan: Schnittplan, sk: Skript) -> str:
    tk_by_clip = {str(t.clip_id): t for t in db.query(TakeKontext).all()}
    ctx_by_szene = {c.skript_szene_id: c for c in db.query(SzenenKontext).all()}
    clips = {str(c.id): c for c in db.query(Clip).all()}
    zeilen_by_id = {str(z.id): z for sz in sk.szenen for z in sz.zeilen}
    eintraege = plan.eintraege or []
    by_szene: dict[str, list[dict]] = defaultdict(list)
    for e in eintraege:
        by_szene[e["szene"]].append(e)
    luecken = (plan.statistik or {}).get("luecken") or []
    tot = ok = 0
    out: list[str] = []
    esc = html.escape
    out.append(f"""<!doctype html><html lang="de"><head><meta charset="utf-8"><title>Prüfbericht – {esc(plan.name)}</title>
<style>
body{{font:13px/1.45 -apple-system,Segoe UI,Helvetica,Arial,sans-serif;color:#222;margin:24px;max-width:1300px}}
h1{{font-size:20px;margin:0 0 4px}} h2{{font-size:16px;margin:26px 0 8px;border-bottom:2px solid #ddd;padding-bottom:4px}}
.meta{{color:#666;margin-bottom:16px}} table{{border-collapse:collapse;width:100%;margin:6px 0 14px}}
th,td{{border:1px solid #e2e2e2;padding:5px 7px;vertical-align:top;text-align:left}} th{{background:#f4f4f4;font-weight:600}}
td.ok{{background:#eef8e8}} td.miss{{background:#fdecec}} td.tc{{font-family:ui-monospace,Menlo,monospace;white-space:nowrap}}
.small{{color:#777;font-size:11.5px}} .tag{{display:inline-block;padding:1px 6px;border-radius:8px;font-size:11px;background:#eee;margin-right:4px}}
.tag.dialog{{background:#e3f1c9}} .tag.stumm{{background:#fff3c4}} .tag.insert{{background:#dbe8ff}} .tag.cutaway{{background:#ffd9d9}} .tag.audio{{background:#e5e5e5}}
.check{{width:26px;text-align:center}} .legend{{margin:8px 0 18px;color:#555}}
</style></head><body>
<h1>Prüfbericht: {esc(plan.name)}</h1>
<div class="meta">Drehbuch „{esc(sk.titel or sk.name)}“ · {len(sk.szenen)} Szenen · {len(eintraege)} Timeline-Einträge · Gesamt {_tc((plan.statistik or {}).get('dauer_s'))} ·
Erzeugt {plan.erstellt_am:%d.%m.%Y %H:%M} · Modus {esc(str((plan.parameter or {}).get('modus', 'rohschnitt')))}</div>
<div class="legend">Lesart: <b>Skript</b> = Zeile aus dem Drehbuch (Original → Übersetzung) · <b>Gesagt</b> = tatsächlich transkribierter Satz im gewählten Take ·
<b>Score</b> = Ähnlichkeit Satz↔Skriptzeile (≥ 0,66 sicher, 0,55–0,66 plausibel) · <b>TL</b> = Position auf der Timeline · <b>In–Out</b> = Zeit im Original-Clip.
Spalte ✓ zum Abhaken beim Gegenlesen.</div>
""")
    for sz in sk.szenen:
        eintr = by_szene.get(sz.nummer, [])
        dialog = [z for z in sz.zeilen if z.art == "dialog"]
        out.append(f"<h2>Szene {esc(sz.nummer)} — {esc(sz.ueberschrift or '')}</h2>")
        # Skript-Aktionen als Kontext
        akt = [z for z in sz.zeilen if z.art == "aktion"]
        if akt:
            out.append("<div class='small'><b>Regie/Aktion laut Skript:</b> " + " · ".join(esc(a.text[:140]) for a in akt[:6]) + ("…" if len(akt) > 6 else "") + "</div>")
        if dialog:
            out.append("<table><tr><th class='check'>✓</th><th>Z</th><th>Figur</th><th>Skript (Original → Übersetzung)</th><th>Take · Einstellung</th><th class='tc'>TL</th><th class='tc'>In–Out</th><th>Gesagt</th><th>Score</th><th>Grund</th></tr>")
            for z in dialog:
                tot += 1
                hits = [e for e in eintr if z.nr in (e.get("zeilen") or [])]
                skript_txt = f"<b>{esc(z.text)}</b>" + (f"<br><span class='small'>⟶ {esc(z.text_ziel)}</span>" if z.text_ziel else "")
                if not hits:
                    grund = next((l.get("grund") for l in luecken if str(l.get("szene")) == sz.nummer and l.get("zeile") == z.nr), "fehlt")
                    out.append(f"<tr><td class='check'>☐</td><td>Z{z.nr}</td><td>{esc(z.figur or '')}</td><td>{skript_txt}</td><td class='miss' colspan='5'><b>FEHLT</b> — {esc(str(grund))}</td><td></td></tr>")
                    continue
                ok += 1
                e = hits[0]; tk = tk_by_clip.get(e["clip_id"]); c = clips.get(e["clip_id"])
                gesagt = [it for it in (tk.zeilen or []) if it.get("skript_zeile_id") == str(z.id) and e["in_s"] - 1 <= float(it["start"]) <= e["out_s"] + 1] if tk else []
                g_txt = "<br>".join(f"<span class='small'>{_tc(float(it['start']))}</span> „{esc(it['text'])}“" for it in gesagt[:4]) or "<span class='small'>(Satz außerhalb des Segments)</span>"
                score = max((float(it.get("score") or 0) for it in gesagt), default=0.0)
                out.append(f"<tr><td class='check'>☐</td><td>Z{z.nr}</td><td>{esc(z.figur or '')}</td><td>{skript_txt}</td>"
                           f"<td class='ok'>{esc(c.dateiname[7:21] if c else e['clip_id'][:8])}<br><span class='small'>E {esc(str(e.get('einstellung')))} · Take {e.get('take') or '?'}</span></td>"
                           f"<td class='tc'>{_tc(e.get('tl_start'))}</td><td class='tc'>{_tc(e['in_s'])}–{_tc(e['out_s'])}</td><td>{g_txt}</td>"
                           f"<td>{score:.2f}</td><td class='small'>{esc(e.get('grund') or '')}</td></tr>")
            out.append("</table>")
        # Beat-Matrix (Szenen-Takt): Beats × Takes — wo ist welcher Moment belegt, welcher wurde gewählt
        ctx = ctx_by_szene.get(sz.id)
        takt_beats = (ctx.takt if ctx else None) or []
        if takt_beats:
            takes_sz = [t for t in tk_by_clip.values() if t.skript_szene_id == sz.id and t.takt]
            takes_sz.sort(key=lambda t: ((t.einstellung or ""), (t.slate_take or 0)))
            gewaehlt: dict[int, dict] = {}
            for e in eintr:
                for b in (e.get("beats") or []):
                    gewaehlt.setdefault(int(b), e)
            out.append("<details open><summary><b>Beat-Matrix</b> (Szenen-Takt: je Beat eine Quelle, genau einmal — Take-Wechsel nur an Beat-Grenzen)</summary>")
            out.append("<div style='overflow-x:auto'><table><tr><th>Beat</th>" + "".join(
                f"<th class='small'>{esc(str(t.einstellung or '?'))} T{t.slate_take or '?'}<br>{esc(clips[str(t.clip_id)].dateiname[7:21]) if str(t.clip_id) in clips else ''}</th>" for t in takes_sz) + "</tr>")
            for b in takt_beats:
                bn = int(b["nr"])
                titel = (f"Z{b['dialog_nr']} {esc(b.get('figur') or '')}: „{esc((b.get('text_de') or b.get('text') or '')[:60])}“" if b.get("dialog_nr") is not None
                         else f"<i>{esc(b.get('art'))}</i>: {esc((b.get('text') or '')[:70])}")
                zellen = []
                for t in takes_sz:
                    sp = next((x for x in (t.takt or []) if int(x["beat"]) == bn), None)
                    if not sp:
                        zellen.append("<td class='small' style='color:#bbb'>–</td>"); continue
                    gw = gewaehlt.get(bn)
                    ist_gewaehlt = gw is not None and gw.get("clip_id") == str(t.clip_id)
                    cls = "ok" if ist_gewaehlt else ("" if sp.get("evidenz") else "small")
                    mark = ("<b>★ </b>" if ist_gewaehlt else "") + ("A" if sp.get("anker") else "") + ("s" if float(sp.get("sem") or 0) > 0 else "") + ("V" if sp.get("vqa") else "") + ("E" if sp.get("eroeffnung") else "")
                    style = "" if sp.get("evidenz") else " style='color:#aaa'"
                    zellen.append(f"<td class='{cls}'{style}>{mark}<br><span class='tc small'>{_tc(float(sp['start']))}–{_tc(float(sp['end']))}</span></td>")
                out.append(f"<tr><td class='small'><b>B{bn}</b> {titel}</td>" + "".join(zellen) + "</tr>")
            out.append("</table></div><div class='small'>★ = gewählte Quelle · A = alignierte Skriptzeile (Anker) · s = eindeutige Improvisation (semantisch) · V = Skript-Aktion im Bild bestätigt · E = Eröffnung (Spiel vor der ersten Zeile) · grau = Durchgangs-Beat ohne eigene Evidenz (nur Fortsetzung).</div></details>")
        # Nicht-Dialog-Einträge
        rest = [e for e in eintr if e.get("art") != "dialog"]
        if rest:
            out.append("<table><tr><th class='check'>✓</th><th>Art</th><th>Take · Einstellung</th><th class='tc'>TL</th><th class='tc'>In–Out</th><th>Dauer</th><th>Was zu sehen ist (Bildbeschreibung im Fenster)</th><th>Grund / Beleg</th></tr>")
            for e in rest:
                tk = tk_by_clip.get(e["clip_id"]); c = clips.get(e["clip_id"])
                bild = [b for b in ((tk.bildverlauf or []) if tk else []) if b.get("t") is not None and e["in_s"] - 10 <= float(b["t"]) <= e["out_s"] + 10]
                bild_txt = "<br>".join(f"<span class='small'>{_tc(float(b['t']))}</span> {esc(b['beschreibung'][:150])}" for b in bild[:3]) or "<span class='small'>–</span>"
                art = e.get("art")
                out.append(f"<tr><td class='check'>☐</td><td><span class='tag {art}'>{art}</span>{'<br><span class=small>Bild ohne Ton</span>' if e.get('video_only') else ''}{'<br><span class=small>nur Ton (Brücke)</span>' if e.get('audio_only') else ''}</td>"
                           f"<td>{esc(c.dateiname[7:21] if c else e['clip_id'][:8])}<br><span class='small'>E {esc(str(e.get('einstellung')))} · Take {e.get('take') or '?'}</span></td>"
                           f"<td class='tc'>{_tc(e.get('tl_start'))}</td><td class='tc'>{_tc(e['in_s'])}–{_tc(e['out_s'])}</td><td>{e['dauer']:.1f} s</td><td>{bild_txt}</td>"
                           f"<td class='small'>{esc(e.get('grund') or '')}<br>{esc(' · '.join(e.get('beleg') or [])[:220])}</td></tr>")
            out.append("</table>")
        # Skript-Aktionen im Bild (Bildprüfung)
        cov = (ctx_by_szene.get(sz.id).aktions_coverage if ctx_by_szene.get(sz.id) else None) or {}
        if cov:
            out.append("<table><tr><th class='check'>✓</th><th>A</th><th>Skript-Aktion</th><th>Status</th><th>Belegt in (Take · Einstellung · Zeit)</th></tr>")
            for nr_s, a in sorted(cov.items(), key=lambda kv: int(kv[0])):
                st = a.get("status")
                cls = "ok" if st == "gedreht" else ("miss" if st == "fehlt" else "")
                takes_txt = " · ".join(f"{esc((t.get('dateiname') or '')[7:21])} {esc(str(t.get('einstellung') or ''))}"
                                       + (" " + ", ".join(f"{_tc(sp[0])}–{_tc(sp[1])}" for sp in (t.get('spans') or [])) if t.get('spans') else (f" (CLIP +{t.get('clip_sim_rel'):.2f})" if t.get('clip_sim_rel') is not None else ""))
                                       for t in (a.get("takes") or [])[:6])
                out.append(f"<tr><td class='check'>☐</td><td>A{esc(nr_s)}</td><td>{esc(str(a.get('text'))[:160])}</td><td class='{cls}'>{esc(str(st))}</td><td class='small'>{takes_txt or '–'}</td></tr>")
            out.append("</table>")
        if not eintr:
            out.append("<p class='small'>Keine Einträge für diese Szene.</p>")
    out.append(f"<h2>Bilanz</h2><p><b>{ok}/{tot}</b> Dialogzeilen des Drehbuchs sind im Schnitt mit einem konkreten Take verbunden.")
    if luecken:
        out.append("<br>Lücken: " + " · ".join(f"Sz {esc(str(l.get('szene')))} Z{l.get('zeile')} ({esc(str(l.get('grund'))[:70])})" for l in luecken))
    out.append("</p><p class='small'>Hinweise zum Gegenlesen: Score &lt; 0,62 = die Zuordnung ist plausibel, aber nicht sicher — Take öffnen (Skript &amp; Kontext → Details) und ggf. Klappe/Einstellung korrigieren. Stumme Segmente sind über Klappe + Bewegung positioniert; ob das Bild exakt die Skript-Aktion zeigt, prüft derzeit nur der Mensch.</p></body></html>")
    return "".join(out)
