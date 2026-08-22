"""Kontext-Schicht L2–L4: Take-Kontext, Szenen-Kontext, Story-Kontext — aus Skript + Clip-Fakten.

Reihenfolge: importiere_skript() → baue_take_kontexte() → baue_szenen_kontexte() → baue_story_kontext().
Deterministische Teile (Klappe, Spiel/Produktion, Alignment, Coverage, Ranking) laufen ohne LLM; das LLM
schreibt nur Zusammenfassungen/Beats/Figuren-Zuordnung mit Belegpflicht. Alles wird persistiert und ist editierbar.
"""
from __future__ import annotations

import logging
import re
import uuid
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from backend.core.database import (Clip, Szene, Take, Skript, SkriptSzene, SkriptZeile, TakeKontext,
                                   SzenenKontext, StoryKontext)
from backend.core.skript import parser as P
from backend.core.skript import klappe as K
from backend.core.skript import alignment as A
from backend.core.skript.llm import frage_json
from backend.core.skript.uebersetzung import uebersetze_zeilen

logger = logging.getLogger("cinassist.skript.kontext")


# ─────────────────────────────────────────────────────────────
# Skript-Import
# ─────────────────────────────────────────────────────────────

def importiere_skript(db, pfad: str, name: str | None = None, ziel_sprache: str = "de",
                      uebersetzen: bool = True) -> Skript:
    roh = P.parse_datei(pfad)
    if not roh.szenen:
        raise ValueError("Keine Szenen erkannt — Format nicht lesbar (erwartet „1. INT. ORT – ZEIT“ oder „SZENE 1 …“).")
    # Sprache grob: Anteil typischer Wörter
    txt = roh.roh_text.lower()
    en = sum(txt.count(w) for w in (" the ", " and ", " you ", " is "))
    de = sum(txt.count(w) for w in (" der ", " die ", " und ", " ist ", " nicht "))
    sprache = "en" if en >= de else "de"
    for alt in db.query(Skript).filter(Skript.aktiv.is_(True)).all():
        alt.aktiv = False
    sk = Skript(id=uuid.uuid4(), name=name or Path(pfad).name, titel=roh.titel, sprache=sprache,
                ziel_sprache=ziel_sprache, quelle_pfad=str(pfad), roh_text=roh.roh_text, aktiv=True, status="importiert")
    db.add(sk)
    for idx, sz in enumerate(roh.szenen):
        s = SkriptSzene(id=uuid.uuid4(), skript_id=sk.id, nummer=sz.nummer, reihenfolge=idx, ueberschrift=sz.ueberschrift,
                        innen_aussen=sz.innen_aussen, ort=sz.ort, tageszeit=sz.tageszeit, figuren=sz.figuren)
        db.add(s)
        for nr, z in enumerate(sz.zeilen):
            db.add(SkriptZeile(id=uuid.uuid4(), szene_id=s.id, nr=nr, art=z.art, figur=z.figur, regie=z.regie, text=z.text))
    db.commit()
    if uebersetzen and sprache != ziel_sprache:
        uebersetze_skript(db, sk)
    return sk


def uebersetze_skript(db, sk: Skript) -> int:
    """Dialogzeilen je Szene in die Drehsprache (nur wo noch keine manuelle Übersetzung steht)."""
    n = 0
    for sz in sk.szenen:
        offen = [z for z in sz.zeilen if z.art == "dialog" and z.text_ziel_quelle != "manuell"]
        if not offen:
            continue
        out = uebersetze_zeilen([z.text for z in offen], sk.ziel_sprache or "de", sk.sprache)
        if out:
            for z, t in zip(offen, out):
                z.text_ziel = t; z.text_ziel_quelle = "llm"; n += 1
    sk.status = "uebersetzt" if n else sk.status
    db.commit()
    return n


def aktives_skript(db) -> Skript | None:
    return db.query(Skript).filter(Skript.aktiv.is_(True)).order_by(Skript.erstellt_am.desc()).first()


# ─────────────────────────────────────────────────────────────
# L2 — Take-Kontext
# ─────────────────────────────────────────────────────────────

def _zeilen_refs(sz: SkriptSzene) -> list[A.SkriptZeileRef]:
    return [A.SkriptZeileRef(id=str(z.id), nr=z.nr, figur=z.figur, text=z.text, text_ziel=z.text_ziel)
            for z in sz.zeilen if z.art == "dialog"]


def _bildverlauf(db, clip: Clip) -> list[dict]:
    out: list[dict] = []
    for s in db.query(Szene).filter(Szene.clip_id == clip.id).order_by(Szene.szenen_nr).all():
        av = s.analyse_visuelle if isinstance(s.analyse_visuelle, dict) else {}
        for p in av.get("stichproben") or []:
            if p.get("beschreibung") and not p.get("gleich_wie"):
                out.append({"t": p.get("t"), "beschreibung": p["beschreibung"], "personen": p.get("personen")})
        if not av.get("stichproben") and s.beschreibung:
            out.append({"t": s.start_zeit, "beschreibung": s.beschreibung, "personen": av.get("personen")})
    return out


def _namens_ersetzung(db, sk: Skript) -> dict[str, str]:
    """Dreh-Name → Skript-Name (aus der deterministischen Zuordnung eines früheren Laufs), für das Alignment:
    „Ophelia, bist du da?“ wird vor dem Vergleich zu „Orpheus, bist du da?“ — die Embeddings/Lexik sehen dann den
    Skriptnamen. Leer beim allerersten Lauf (dann liefert der zweite Lauf die Verbesserung)."""
    try:
        from backend.core import einstellungen as _E5
        glossar = _E5.transkription().get("glossar") or []
    except Exception:  # noqa: BLE001
        glossar = []
    out: dict[str, str] = {}
    try:
        for d in figuren_aus_alignment(db, sk, glossar):
            if d.get("film") and d.get("skript") and d["stimmen"] >= 2:
                out[d["film"]] = d["skript"].capitalize()
    except Exception:  # noqa: BLE001
        pass
    return out


def _glossar_text(text: str) -> str:
    try:
        from backend.core import einstellungen as _E6
        return " ".join(_E6.glossar_angleichen(w) for w in text.split())
    except Exception:  # noqa: BLE001
        return text


def _ersetze_namen(text: str, mapping: dict[str, str]) -> str:
    for film, skript in mapping.items():
        text = re.sub(r"\b" + re.escape(film) + r"\b", skript, text)
    return text


def baue_take_kontexte(db, sk: Skript, nur_clip_ids: list[str] | None = None, zwei_paesse: bool = True) -> list[TakeKontext]:
    szenen = {sz.nummer: sz for sz in sk.szenen}
    zeilen_je_szene = {sz.nummer: _zeilen_refs(sz) for sz in sk.szenen}
    namen_map = _namens_ersetzung(db, sk)
    q = db.query(Clip).filter(Clip.status == "analysiert")
    if nur_clip_ids:
        q = q.filter(Clip.id.in_(nur_clip_ids))
    clips = q.order_by(Clip.dateiname).all()
    out: list[TakeKontext] = []
    for clip in clips:
        segs: list[dict] = []
        for s in db.query(Szene).filter(Szene.clip_id == clip.id).order_by(Szene.szenen_nr).all():
            if isinstance(s.transkription_json, list):
                segs.extend(s.transkription_json)
        segs.sort(key=lambda x: float(x.get("start", 0.0)))
        befund = K.analysiere_take(segs, clip.dauer)
        take = db.query(Take).filter(Take.id == clip.take_id).first() if clip.take_id else None

        # Klappe: Audio > Dateiname (schwach) ; Konflikt merken
        slate_szene, slate_take, quelle = befund.klappe.szene, befund.klappe.take, befund.klappe.quelle
        datei_szene = f"{take.szene}.{take.plan}" if take and take.szene is not None and take.plan is not None else None
        konflikt = False
        if slate_szene is None and datei_szene:
            slate_szene, quelle = datei_szene, "dateiname"
            slate_take = slate_take or (take.prise if take else None)
        elif slate_szene and datei_szene and slate_szene.split(".")[0] != datei_szene.split(".")[0]:
            konflikt = True
        skript_nr = slate_szene.split(".")[0] if slate_szene else None

        spiel = [e for e in befund.einheiten if e.art == "spiel"]
        # Glossar-Schreibweise wortweise nachziehen („Offelia“ → „Ophelia“), dann Dreh-Namen → Skript-Namen
        saetze = [_ersetze_namen(_glossar_text(e.text), namen_map) if namen_map else _glossar_text(e.text) for e in spiel]
        # Fallback ohne brauchbare Klappe: welche Szene passt inhaltlich?
        if (skript_nr not in szenen or quelle == "dateiname") and saetze:
            pass_ = A.szenen_passung(saetze, {n: z for n, z in zeilen_je_szene.items() if z})
            if pass_:
                best_nr = max(pass_, key=pass_.get)
                if pass_[best_nr] >= 0.6 and (skript_nr not in szenen or pass_[best_nr] >= pass_.get(skript_nr, 0) + 0.05):
                    if skript_nr != best_nr:
                        quelle = "inhalt"
                    skript_nr = best_nr
        sz = szenen.get(skript_nr) if skript_nr else None

        zeilen_json: list[dict] = []
        abdeckung = None
        if sz is not None:
            refs = zeilen_je_szene.get(sz.nummer, [])
            erg = A.aligne(saetze, refs) if refs and saetze else None
            zu = {z.einheit_idx: z for z in (erg.zuordnungen if erg else [])}
            abdeckung = erg.abdeckung if erg else (None if refs else 1.0)
            # Ausstieg am Take-Ende endgültig: erste Kandidaten-Einheit OHNE sicheren Skript-Treffer → ab dort Produktion
            si_tmp = 0
            ausstieg_ab = None
            for e in befund.einheiten:
                if e.art == "spiel":
                    z = zu.get(si_tmp); si_tmp += 1
                    if e.ausstieg_kandidat and not (z and z.zeile_id and z.score >= 0.6):
                        ausstieg_ab = e.start
                        break
            if ausstieg_ab is not None:
                for e in befund.einheiten:
                    if e.art == "spiel" and e.start >= ausstieg_ab:
                        e.art = "produktion"
                sp_rest = [e for e in befund.einheiten if e.art == "spiel"]
                befund.spiel_ende = sp_rest[-1].end if sp_rest else befund.spiel_start
                befund.ng.setdefault("gruende", []).append(f"Ausstieg aus dem Spiel bei {ausstieg_ab:.0f}s (Take-Ende)")
            si = 0
            for e in befund.einheiten:
                item = {"start": round(e.start, 2), "end": round(e.end, 2), "sprecher": e.sprecher, "text": e.text, "art": e.art,
                        "skript_zeile_id": None, "skript_zeile_nr": None, "score": None}
                if e.art == "spiel":
                    z = zu.get(si); si += 1
                    if z:
                        item.update({"skript_zeile_id": z.zeile_id, "skript_zeile_nr": z.zeile_nr, "score": z.score})
                zeilen_json.append(item)
        else:
            zeilen_json = [{"start": round(e.start, 2), "end": round(e.end, 2), "sprecher": e.sprecher, "text": e.text, "art": e.art,
                            "skript_zeile_id": None, "skript_zeile_nr": None, "score": None} for e in befund.einheiten]

        tk = db.query(TakeKontext).filter(TakeKontext.clip_id == clip.id).first()
        if tk is None:
            tk = TakeKontext(id=uuid.uuid4(), clip_id=clip.id)
            db.add(tk)
        manuell = tk.slate_quelle == "manuell"
        if not manuell:
            tk.skript_szene_id = sz.id if sz else None
            tk.slate_szene = slate_szene; tk.slate_take = slate_take; tk.slate_quelle = quelle
            tk.slate_konflikt = konflikt
            tk.einstellung = slate_szene
        tk.spiel_start_s = befund.spiel_start; tk.spiel_ende_s = befund.spiel_ende
        tk.ng = befund.ng; tk.zeilen = zeilen_json; tk.abdeckung = abdeckung
        tk.bildverlauf = _bildverlauf(db, clip)
        tk.aktualisiert_am = datetime.utcnow()
        out.append(tk)
    db.commit()
    # Zweiter Pass: erst jetzt gibt es eine Figuren-Zuordnung (Dreh-Namen ↔ Skript-Namen) → mit Namensersetzung
    # neu alignen (mehr Treffer bei Anrede-Zeilen wie „Orpheus, are you there?“ ↔ „Ophelia, bist du da?“).
    if zwei_paesse and not namen_map and _namens_ersetzung(db, sk):
        return baue_take_kontexte(db, sk, nur_clip_ids, zwei_paesse=False)
    return out


# ─────────────────────────────────────────────────────────────
# L3 — Szenen-Kontext
# ─────────────────────────────────────────────────────────────

_NAME_STOP = {"hallo", "hey", "bitte", "danke", "okay", "ok", "ja", "nein", "mann", "man", "alter", "komm", "wach", "warte", "moment",
              "scheiße", "oh", "ach", "und", "aber", "was", "wo", "wie", "wenn", "das", "der", "die", "ich", "du", "wir", "sie", "es", "ist",
              "bist", "musst", "geh", "dann", "also", "jetzt", "nur", "nicht", "noch", "los", "bleib", "schau", "mach", "sag", "lass", "gut",
              "super", "sorry", "set", "scene", "szene", "take", "kamera", "fuck", "babe", "hier", "da", "so", "na", "ey", "yo", "boah"}


def angesprochene_namen(tks: list[TakeKontext], glossar: list[str] | None = None) -> dict[str, int]:
    """Deterministisch: Namen, die im Spiel-Dialog als Anrede stehen („Yuri, bist du da?“, „Ophelia!“) — plus
    Glossar-Treffer. Gibt Name → Häufigkeit. Grundlage für die Figuren-Zuordnung Skript ↔ Dreh."""
    zaehl: dict[str, int] = defaultdict(int)
    gl = {g.lower(): g for g in (glossar or []) if g and " " not in g}
    for tk in tks:
        for it in tk.zeilen or []:
            if it.get("art") != "spiel":
                continue
            text = str(it.get("text", ""))
            # Anrede-Position: am Satzanfang, direkt gefolgt von Komma/Ausruf/Frage oder Satzende („Yuri, bist du da?“,
            # „Ophelia!“, „Hey, Yuri.“). Deutsche Substantive mitten im Satz (Schuld, Proben) fallen so raus.
            for m in re.finditer(r"^(?:hey,?\s+|oh,?\s+|ach,?\s+)?([A-ZÄÖÜ][a-zäöüß]{2,})(?=\s*[,!?.]|$)", text.strip()):
                w = m.group(1)
                if w.lower() in _NAME_STOP:
                    continue
                try:
                    from backend.core import einstellungen as _E2
                    w = _E2.glossar_angleichen(w)      # „Offelia“ → „Ophelia“ (phonetisch, nur Glossar)
                except Exception:  # noqa: BLE001
                    pass
                zaehl[gl.get(w.lower(), w)] += 1
            for wl, g in gl.items():
                if re.search(r"\b" + re.escape(wl) + r"\b", text.lower()):
                    zaehl[g] += 0   # sichtbar machen, auch ohne Anrede-Position
    # Rauschen raus: nur Namen, die ≥ 2× vorkommen oder im Glossar stehen
    return {n: c for n, c in sorted(zaehl.items(), key=lambda kv: -kv[1]) if c >= 2 or n.lower() in gl}

def take_score(tk: TakeKontext, max_spiel: float) -> tuple[float, list[str]]:
    """Deterministisches Ranking: Abdeckung, kein Abbruch, Länge des Spiels, manuelle Bewertung."""
    gruende: list[str] = []
    s = 0.0
    if tk.abdeckung is not None:
        s += 0.5 * tk.abdeckung; gruende.append(f"Skriptzeilen gedeckt: {round(tk.abdeckung * 100)} %")
    ng = tk.ng or {}
    if ng.get("abbruch"):
        s -= 0.3; gruende.append("Abbruch im Take")
    else:
        s += 0.2
    if ng.get("kurz"):
        s -= 0.2; gruende.append("sehr kurz")
    spiel = (tk.spiel_ende_s or 0) - (tk.spiel_start_s or 0) if tk.spiel_start_s is not None else 0.0
    if max_spiel > 0:
        s += 0.2 * min(1.0, spiel / max_spiel)
    # Bildprüfung: Take zeigt bestätigte Skript-Aktionen → besser (je Aktion +0,15, max. +0,45)
    bestaetigt = sum(1 for a in (tk.aktionen or {}).values() if a.get("spans"))
    if bestaetigt:
        # nur Aktionen mit ≥ 2 bestätigten Frames zählen (Einzeltreffer sind Rauschen)
        solide = sum(1 for a in (tk.aktionen or {}).values() if a.get("spans") and (a.get("ja") or 0) >= 2)
        s += min(0.75, 0.25 * solide) + (0.05 if bestaetigt > solide else 0.0)
        gruende.append(f"{solide} Skript-Aktion(en) im Bild bestätigt (≥ 2 Frames)")
    if tk.bewertung == "circled":
        s += 1.0; gruende.append("vom Nutzer markiert (circled)")
    elif tk.bewertung == "ng":
        s -= 1.0; gruende.append("vom Nutzer als NG markiert")
    elif tk.bewertung == "ok":
        s += 0.3
    return round(s, 3), gruende


def figuren_aus_alignment(db, sk: Skript, glossar: list[str] | None = None) -> list[dict]:
    """Deterministische Figuren-Zuordnung Skript ↔ Dreh über das Zeilen-Alignment: Steht in einer Skriptzeile ein
    Figurname als Anrede („Orpheus, are you there?“, „This isn't funny Eury“) und in der zugeordneten gesprochenen
    Einheit ein Anrede-Name („Ophelia, bist du da?“), ist das eine Stimme für FIGUR(Skript) = Name(Dreh).
    Liefert [{skript, film, stimmen, belege[]}] sortiert nach Stimmen; Kosenamen (Babe) zählen nicht."""
    szenen = {str(sz.id): sz for sz in sk.szenen}
    zeilen = {str(z.id): z for sz in sk.szenen for z in sz.zeilen}
    figuren = sorted({(z.figur or "").upper() for sz in sk.szenen for z in sz.zeilen if z.art == "dialog" and z.figur} |
                     {f.upper() for sz in sk.szenen for f in (sz.figuren or [])} |
                     # Figuren, die nur in Aktionen/Anreden vorkommen (EURYDICE spricht nie, wird aber angesprochen)
                     {w.upper() for sz in sk.szenen for z in sz.zeilen for w in re.findall(r"\b([A-Z][a-z]{3,})\b", z.text)
                      if w.upper() in {(zz.regie or "").upper().replace("TO ", "") for s2 in sk.szenen for zz in s2.zeilen if zz.regie}})
    gl = {g.lower(): g for g in (glossar or []) if g and " " not in g}
    stimmen: dict[tuple[str, str], list[str]] = defaultdict(list)
    for tk in db.query(TakeKontext).all():
        for it in tk.zeilen or []:
            if it.get("art") != "spiel" or not it.get("skript_zeile_id"):
                continue
            z = zeilen.get(it["skript_zeile_id"])
            if not z:
                continue
            # Skript-Anrede: Figurname (oder Präfix ≥ 4, „Eury“) als eigenes Wort im Zeilentext, aber nicht der Sprecher selbst
            skript_namen = []
            for f in figuren:
                if f == (z.figur or "").upper():
                    continue
                for w in re.findall(r"\b([A-Z][a-z]{3,})\b", z.text):
                    if f.startswith(w.upper()) and len(w) >= 4:
                        skript_namen.append(f)
            if not skript_namen:
                continue
            text = str(it.get("text", "")).strip()
            m = re.match(r"^(?:hey,?\s+|oh,?\s+|ach,?\s+)?([A-ZÄÖÜ][a-zäöüß]{2,})(?=\s*[,!?.]|$)", text)
            if not m:
                continue
            n = m.group(1)
            if n.lower() in _NAME_STOP:
                continue
            try:
                from backend.core import einstellungen as _E3
                n = _E3.glossar_angleichen(n)
            except Exception:  # noqa: BLE001
                pass
            n = gl.get(n.lower(), n)
            for f in set(skript_namen):
                stimmen[(f, n)].append(f"Sz {szenen.get(str(z.szene_id)).nummer if szenen.get(str(z.szene_id)) else '?'} Z{z.nr} „{z.text[:40]}“ ↔ {it['start']:.0f}s „{text[:40]}“")
    # je Skript-Figur der Name mit den meisten Stimmen
    beste: dict[str, tuple[str, list[str]]] = {}
    for (f, n), bel in stimmen.items():
        if f not in beste or len(bel) > len(beste[f][1]):
            beste[f] = (n, bel)
    out = [{"skript": f, "film": n, "stimmen": len(bel), "belege": bel[:4], "quelle": "alignment"} for f, (n, bel) in beste.items()]
    out.sort(key=lambda d: -d["stimmen"])
    return out


def baue_szenen_kontexte(db, sk: Skript, mit_llm: bool = True) -> list[SzenenKontext]:
    out: list[SzenenKontext] = []
    takes_je_szene: dict[uuid.UUID, list[TakeKontext]] = defaultdict(list)
    for tk in db.query(TakeKontext).all():
        if tk.skript_szene_id:
            takes_je_szene[tk.skript_szene_id].append(tk)
    clips = {c.id: c for c in db.query(Clip).all()}

    for sz in sk.szenen:
        tks = takes_je_szene.get(sz.id, [])
        # Coverage + Ranking je Einstellung
        je_einst: dict[str, list[TakeKontext]] = defaultdict(list)
        for tk in tks:
            je_einst[tk.einstellung or "?"].append(tk)
        max_spiel = max(((tk.spiel_ende_s or 0) - (tk.spiel_start_s or 0)) for tk in tks) if tks else 0.0
        coverage: dict[str, dict] = {}
        ranking: list[dict] = []
        for einst, lst in sorted(je_einst.items()):
            coverage[einst] = {str(tk.clip_id): tk.abdeckung for tk in lst}
            for tk in lst:
                sc, gr = take_score(tk, max_spiel)
                c = clips.get(tk.clip_id)
                ranking.append({"clip_id": str(tk.clip_id), "dateiname": c.dateiname if c else None, "einstellung": einst,
                                "take": tk.slate_take, "score": sc, "gruende": gr, "abdeckung": tk.abdeckung,
                                "spiel": [tk.spiel_start_s, tk.spiel_ende_s], "ng": tk.ng})
        ranking.sort(key=lambda r: (-r["score"], r["einstellung"] or ""))

        # Zeilen-Coverage über alle Takes: welche Skriptzeile wurde überhaupt gedreht?
        dialog = [z for z in sz.zeilen if z.art == "dialog"]
        gedeckt: dict[str, list[str]] = defaultdict(list)
        for tk in tks:
            for item in tk.zeilen or []:
                if item.get("skript_zeile_id"):
                    gedeckt[item["skript_zeile_id"]].append(str(tk.clip_id))
        fehlend = [z.nr for z in dialog if str(z.id) not in gedeckt]

        sk_ctx = db.query(SzenenKontext).filter(SzenenKontext.skript_szene_id == sz.id).first()
        if sk_ctx is None:
            sk_ctx = SzenenKontext(id=uuid.uuid4(), skript_szene_id=sz.id)
            db.add(sk_ctx)
        sk_ctx.coverage = coverage
        sk_ctx.take_ranking = ranking
        unsicher: list[str] = []
        if not tks:
            unsicher.append("Keine Takes dieser Szene im Material gefunden (Klappe/Inhalt).")
        if fehlend:
            unsicher.append(f"Skriptzeilen ohne Treffer in allen Takes: {', '.join(str(n) for n in fehlend)}")
        if mit_llm and not sk_ctx.manuell_geprueft:
            llm = _szenen_llm(sz, tks, clips, dialog, gedeckt, db=db)
            if llm:
                sk_ctx.zusammenfassung = llm.get("zusammenfassung")
                sk_ctx.beats = llm.get("beats")
                sk_ctx.figuren = llm.get("figuren")
                sk_ctx.belege = llm.get("belege")
                unsicher.extend(llm.get("unsicher") or [])
            else:
                unsicher.append("LLM-Zusammenfassung nicht verfügbar (Ollama).")
        sk_ctx.unsicher = unsicher
        sk_ctx.aktualisiert_am = datetime.utcnow()
        out.append(sk_ctx)
        db.commit()          # pro Szene — LLM-Schritte dauern, Fortschritt soll sichtbar/persistent sein
    return out


def _szenen_llm(sz: SkriptSzene, tks: list[TakeKontext], clips: dict, dialog: list[SkriptZeile], gedeckt: dict, db=None) -> dict | None:
    skript_block = "\n".join(
        (f"[Z{z.nr}] {z.figur}{' (' + z.regie + ')' if z.regie else ''}: „{z.text}“" + (f"  ⟶ DE: „{z.text_ziel}“" if z.text_ziel else ""))
        if z.art == "dialog" else (f"[A{z.nr}] {z.text}" if z.art == "aktion" else "")
        for z in sz.zeilen)
    take_blocks = []
    # Prompt-Budget: bester Take je Einstellung (max. 8), 22 Dialogsätze, 5 Bildzeilen — qwen 14b braucht sonst
    # > 3 min pro Szene (Prompt-Eval dominiert).
    beste_je_einst: dict[str, TakeKontext] = {}
    for tk in tks:
        k = tk.einstellung or "?"
        if k not in beste_je_einst or (tk.abdeckung or 0) > (beste_je_einst[k].abdeckung or 0):
            beste_je_einst[k] = tk
    for tk in sorted(beste_je_einst.values(), key=lambda t: ((t.einstellung or ""), t.slate_take or 0))[:8]:
        c = clips.get(tk.clip_id)
        zeilen = [i for i in (tk.zeilen or []) if i["art"] == "spiel"][:22]
        ztxt = "\n".join(f"    {i['start']:.0f}s {('[' + str(i['sprecher']) + ']') if i.get('sprecher') else ''} „{i['text']}“"
                         + (f" → Z{i['skript_zeile_nr']} ({i['score']:.2f})" if i.get("skript_zeile_id") else "") for i in zeilen)
        bild = "\n".join(f"    {b['t']:.0f}s: {b['beschreibung'][:160]}" for b in (tk.bildverlauf or [])[:5])
        take_blocks.append(
            f"- Take {c.dateiname if c else tk.clip_id} · Einstellung {tk.einstellung} · Take {tk.slate_take} · Spiel {tk.spiel_start_s}–{tk.spiel_ende_s}s · "
            f"Skript-Abdeckung {tk.abdeckung} · NG {tk.ng}\n  Dialog:\n{ztxt or '    (kein Dialog)'}\n  Bild:\n{bild or '    (keine Beschreibung)'}")
    try:
        from backend.core import einstellungen as _E
        glossar = _E.transkription().get("glossar") or []
    except Exception:  # noqa: BLE001
        glossar = []
    namen = angesprochene_namen(tks, glossar)
    namen_txt = ", ".join(f"{n} ({c}×)" for n, c in namen.items()) if namen else "(keine Namen als Anrede erkannt)"
    zuord = figuren_aus_alignment(db, sz.skript, glossar) if db is not None else []
    zuord_txt = "; ".join(f"{d['skript']} = {d['film']} ({d['stimmen']} Belege, z. B. {d['belege'][0] if d['belege'] else ''})" for d in zuord) or "(keine)"
    prompt = f"""Du bist ein faktischer Schnitt-Assistent. Du bekommst EINE Drehbuchszene (Skript, ggf. englisch) und die gedrehten Takes (deutsch: Dialog mit Zeit und Sprecher, Bildbeschreibungen mit Zeit, Zuordnung zu Skriptzeilen Z0…). Du erfindest nichts: jede Aussage muss sich auf eine Skriptzeile, eine Dialogzeile (mit Zeit) oder eine Bildbeschreibung stützen.

AUFGABE (Antwort NUR als JSON, alle Texte DEUTSCH):
{{
  "zusammenfassung": "Was in dieser Szene laut Skript passiert und wie es gedreht wurde — 3–5 Sätze. Nenne, was gedreht ist und was fehlt.",
  "beats": [{{"nr": 1, "text": "Beat in einem Satz", "skript_zeilen": ["Z0","A1"], "typ": "dialog|aktion", "gedreht": true}}]  (max. 8 Beats),
  "figuren": [{{"skript": "ORPHEUS", "film": "gesprochener Name im Dreh oder null", "rolle": "ein Satz", "beleg": "Zitat mit Zeit"}}],
  "belege": ["3–4 Zitate aus Dialog (mit Zeit) oder Bild, die zeigen, dass die Szene so gedreht wurde"],
  "unsicher": ["was nicht belegbar ist"]
}}
REGELN: Figuren im Dreh können andere Namen/Geschlechter haben als im Skript: ordne über die DIALOGLAGE zu — wer wird mit welchem Namen angesprochen, während im Skript an derselben Stelle eine Figur angesprochen wird (z. B. Skript FRED: „Orpheus, are you there?“ ↔ Dreh „Ophelia, bist du da?“ ⇒ ORPHEUS = Ophelia; Skript ORPHEUS: „This isn't funny Eury“ ↔ Dreh „Yuri, bist du da?“ ⇒ EURYDICE = Yuri). Im Dreh als Anrede gesprochene Namen (deterministisch gezählt): {namen_txt}. **Deterministische Zuordnung aus dem Zeilen-Alignment (gilt als Fakt, übernimm sie): {zuord_txt}.** „Babe“/„Eury“ sind Kosenamen, keine Figurnamen. Sprecher-Labels (SPEAKER_00) sind KEINE Namen. Wenn kein Beleg: film=null. Keine Gefühlsdeutung ohne Dialogbeleg.

── SKRIPT — Szene {sz.nummer}: {sz.ueberschrift} ──
{skript_block}

── GEDREHT ({len(tks)} Takes) ──
{chr(10).join(take_blocks) if take_blocks else '(keine Takes zugeordnet)'}

Antworte JETZT nur mit dem JSON."""
    out = frage_json(prompt, num_predict=1500, num_ctx=8192, timeout=480.0)
    if isinstance(out, dict):
        return out
    # Zweiter, kürzerer Versuch: nur Zusammenfassung + Figuren (lange Szenen sprengen sonst das Token-Budget)
    kurz = prompt.replace('"beats": [{"nr": 1, "text": "Beat in einem Satz", "skript_zeilen": ["Z0","A1"], "typ": "dialog|aktion", "gedreht": true}]  (max. 8 Beats),\n', "")
    kurz = kurz.replace('"belege": ["3–4 Zitate aus Dialog (mit Zeit) oder Bild, die zeigen, dass die Szene so gedreht wurde"],\n', "")
    out = frage_json(kurz, num_predict=900, num_ctx=8192, timeout=480.0)
    return out if isinstance(out, dict) else None


# ─────────────────────────────────────────────────────────────
# L4 — Story-Kontext
# ─────────────────────────────────────────────────────────────

def baue_story_kontext(db, sk: Skript, mit_llm: bool = True) -> StoryKontext:
    st = db.query(StoryKontext).filter(StoryKontext.skript_id == sk.id).first()
    if st is None:
        st = StoryKontext(id=uuid.uuid4(), skript_id=sk.id)
        db.add(st)
    ctxs = {c.skript_szene_id: c for c in db.query(SzenenKontext).all()}
    st.szenenfolge = [sz.nummer for sz in sk.szenen]
    # Figuren-Zuordnung: Mehrheit über die Szenen-Kontexte (deterministisch aggregiert)
    stimmen: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for sz in sk.szenen:
        c = ctxs.get(sz.id)
        for f in (c.figuren if c and isinstance(c.figuren, list) else []):
            if isinstance(f, dict) and f.get("skript"):
                film_raw = f.get("film")
                film_s = "" if film_raw is None or str(film_raw).strip().lower() in ("null", "none", "?") else str(film_raw)
                stimmen[str(f["skript"]).upper()][film_s] += 1
    try:
        from backend.core import einstellungen as _E4
        glossar = _E4.transkription().get("glossar") or []
    except Exception:  # noqa: BLE001
        glossar = []
    figuren = figuren_aus_alignment(db, sk, glossar)            # deterministisch, mit Belegen — gewinnt
    bekannt = {f["skript"] for f in figuren}
    for skript_name, votes in stimmen.items():
        if skript_name in bekannt:
            continue
        film, n = max(votes.items(), key=lambda kv: kv[1])
        if film and (film.upper().startswith("SPEAKER") or film.strip().lower() in ("null", "none", "?", "")):
            film = None
        figuren.append({"skript": skript_name, "film": film or None, "stimmen": n, "quelle": "szenen-kontext"})
    st.figuren = figuren
    unsicher: list[str] = []
    if mit_llm:
        szenen_txt = "\n".join(
            f"Szene {sz.nummer} ({sz.ueberschrift}): {(ctxs.get(sz.id).zusammenfassung if ctxs.get(sz.id) else None) or '(keine Zusammenfassung)'}"
            for sz in sk.szenen)
        prompt = f"""Du bist ein faktischer Dramaturgie-Assistent. Fasse die Geschichte des Drehbuchs „{sk.titel or sk.name}“ aus den Szenen-Zusammenfassungen zusammen — NUR was dort steht. Antworte NUR als JSON, deutsch:
{{"zusammenfassung": "5–8 Sätze: Figuren, Ausgangslage, Wendepunkte, Ende.",
  "arc": [{{"szene": "2", "wendepunkt": "ein Satz"}}],
  "motive": ["wiederkehrende Motive/Requisiten mit Szenenbezug, z. B. 'kleiner Finger (Pinky Promise): Szene 2, 5'"],
  "unsicher": ["…"]}}

Figuren (Skript → Dreh): {figuren}
{szenen_txt}"""
        out = frage_json(prompt, num_predict=900)
        if isinstance(out, dict):
            st.zusammenfassung = out.get("zusammenfassung"); st.arc = out.get("arc"); st.motive = out.get("motive")
            unsicher.extend(out.get("unsicher") or [])
        else:
            unsicher.append("LLM-Zusammenfassung nicht verfügbar (Ollama).")
    st.unsicher = unsicher
    st.aktualisiert_am = datetime.utcnow()
    db.commit()
    return st
