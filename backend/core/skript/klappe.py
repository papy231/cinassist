"""Sprech-Klappe, Produktions-Sprech und Spiel-Einheiten aus einem Take-Transkript — deterministisch.

Befund am Korpus (Pinky Promise): Whisper hört die gesprochene Klappe als
  „Scene 2.1, Take 3“ · „Szene 3.2, Teil 3“ · „In 5.1.1. Take 3“ · „Sie in 5.2.1, Take 2“ · „Scene 4.1, Day 2“ ·
  „2.1, Date 2“ · „Scene 5, 5.2.2, Take 1“ · „Kameraloi, Scene 5.1.1, Take 2“
Die Szenennummer der Klappe ist die DREHBUCH-Nummer (erster Teil) + Einstellung (Rest); die Kamera-
Dateinamen (S004_S004_T002) sind davon unabhängig und oft „falsch“.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

# Klappe: Szenennummer (mit Punkten) + Take-Nummer (Take/Teil/Date/Day/Tag/Tape = Whisper-Varianten)
_SLATE_NUM = re.compile(r"\b(\d{1,2}(?:\.\d{1,2}){0,3})\b")
_SLATE_TAKE = re.compile(r"\b(?:take|teil|date|day|tag|tape|dig)\s*(\d{1,2})\b", re.IGNORECASE)
_SLATE_HINT = re.compile(r"\b(scene|szene|take|teil|klappe|slate|kamera|set)\b", re.IGNORECASE)

# Produktions-Sprech (Set-Kommandos / Regie / Abbrüche) — vollständige kurze Äußerungen oder typische Phrasen
_PRODUKTION_VOLL = re.compile(
    r"^(?:und\s+)?(?:kamera\s*(?:läuft|laeuft|ab)?|läuft|laeuft|ton\s*(?:läuft|laeuft|ab)|set|action|und\s+bitte|bitte(?:\s+schön)?|danke(?:\s+schön)?|"
    r"cut|schnitt|aus|stopp?|okay|ok|ja|gut|super|sorry|nochmal|noch\s+mal|von\s+vorne|auf\s+anfang|klappe|bereit|ruhe\s+bitte|ruhe|"
    # Englische Set-Kommandos. Sie klingen wie Dialog, sind aber Absprachen und
    # verfälschen sonst den Abgleich gegen das Drehbuch.
    r"(?:i'?m|we'?re|camera'?s?|sound'?s?|everyone'?s?)\s+set|rolling|speed|marker|picture'?s?\s+up|"
    r"back\s+to\s+one|quiet\s+please|standing\s+by)[.!?,\s]*$",
    re.IGNORECASE,
)
_PRODUKTION_PHRASE = re.compile(
    r"(können wir (?:den|das|die) (?:direkt )?noch\s?mal|mach(?:en wir)? noch\s?mal auf anfang|noch\s?mal auf anfang|von vorne|"
    r"kamera\s*(?:läuft|laeuft)|ton\s*(?:läuft|laeuft)|ich bin schon angekommen|und bitte|wir drehen|bitte noch\s?mal|"
    r"nummer \d+|kamera\? nummer|klappe|\bcut\b|abbruch|das war's|das wars|danke,? das (?:war|haben wir)|"
    # Englische Set-Phrasen, aus demselben Grund wie oben.
    r"can we reset|let'?s reset|from the top|take it again|one more time|"
    r"(?:camera|sound|we|i)'?(?:s|re|m)? set\b|and action\b)",
    re.IGNORECASE,
)
_ANREDE_STOP = {"bitte", "danke", "hallo", "hey", "okay", "ja", "nein", "gut", "super", "sorry", "set", "scene", "szene", "take",
                "kamera", "läuft", "fuck", "scheiße", "alter", "mann", "man", "komm", "warte", "moment", "oh", "ach", "und", "aber",
                "was", "wer", "wie", "wo", "jetzt", "los", "stopp", "cut", "aus", "nochmal", "bitte.", "nee", "ne"}
_HOEFLICH = re.compile(r"^(?:bitte(?:\s+schön)?|danke(?:\s+schön)?|ja|okay|ok|gut|super|aus|stopp?)[.!?,\s]*$", re.IGNORECASE)
_DANKE = re.compile(r"^danke", re.IGNORECASE)
_AUSSTIEG = re.compile(r"\b(sorry|entschuldigung|tschuldigung|haha|hihi|lach|lol|war das|nochmal|noch mal|gut so|war gut|passt|danke|cut|okay|geil|super)\b", re.IGNORECASE)
_ABBRUCH = re.compile(r"(noch\s?mal|von vorne|auf anfang|sorry|abbruch|cut\b|das war nichts|nee,? warte|warte mal|moment)", re.IGNORECASE)


@dataclass
class Einheit:
    """Eine Spiel-/Produktionseinheit (Satz) mit Zeit, Sprecher und Klassifikation."""
    start: float
    end: float
    text: str
    sprecher: str | None = None
    art: str = "spiel"             # spiel | produktion | slate
    woerter: list[dict] = field(default_factory=list)
    ausstieg_kandidat: bool = False   # evtl. Aus-dem-Spiel-Fallen am Ende (wird nach dem Alignment entschieden)


@dataclass
class Klappe:
    szene: str | None              # "5.2.1"
    take: int | None
    quelle: str                    # audio | keine
    roh: str | None = None

    @property
    def skript_szene(self) -> str | None:
        return self.szene.split(".")[0] if self.szene else None

    @property
    def einstellung(self) -> str | None:
        return self.szene


def _norm_szene(token: str) -> str:
    teile = [t for t in token.split(".") if t != ""]
    return ".".join(str(int(t)) for t in teile)


def parse_klappe(text: str) -> Klappe:
    """Klappe aus dem Anfang des Transkripts. Nimmt die spezifischste Szenennummer (meiste Punkte) im
    Bereich um Take-/Scene-Hinweise; „Scene 5, 5.2.2“ → 5.2.2."""
    if not text:
        return Klappe(None, None, "keine")
    kopf = text[:400]
    if not _SLATE_HINT.search(kopf) and not re.search(r"\d\.\d", kopf):
        return Klappe(None, None, "keine")
    kandidaten = [m.group(1) for m in _SLATE_NUM.finditer(kopf)]
    take = None
    mt = _SLATE_TAKE.search(kopf)
    if mt:
        take = int(mt.group(1))
        # Take-Nummer selbst ist kein Szenen-Kandidat
        kandidaten = [k for k in kandidaten if not (k == mt.group(1) and "." not in k)]
    szene = None
    if kandidaten:
        mit_punkt = [k for k in kandidaten if "." in k]
        if mit_punkt:
            szene = max(mit_punkt, key=lambda k: (k.count("."), -kandidaten.index(k)))
        else:
            # „Scene 2, Take 1“ → Szene 2 (nur wenn ein Scene-/Szene-Wort davor steht)
            m = re.search(r"(?:scene|szene)\s*(\d{1,2})\b", kopf, re.IGNORECASE)
            szene = m.group(1) if m else None
    if szene is None and take is None:
        return Klappe(None, None, "keine")
    return Klappe(_norm_szene(szene) if szene else None, take, "audio", roh=kopf[:120])


def klassifiziere_einheit(text: str) -> str:
    t = text.strip()
    if not t:
        return "produktion"
    if parse_klappe(t).quelle == "audio" and len(t.split()) <= 10:
        return "slate"
    if _PRODUKTION_VOLL.match(t):
        return "produktion"
    if len(t.split()) <= 12 and _PRODUKTION_PHRASE.search(t):
        return "produktion"
    # Kurzer Zuruf, der mit „Kamera“/„Ton“ beginnt = Set-Kommando — Whisper hört
    # „Kamera läuft!“ auch als „Kamera, Lois.“ o.ä. (Befund Szene 4.1 T2).
    if len(t.split()) <= 3 and re.match(r"^(?:und\s+)?(?:kamera|ton)\b", t, re.IGNORECASE):
        return "produktion"
    return "spiel"


_SATZENDE = re.compile(r"[.!?…]+$")


def einheiten_aus_segmenten(segmente: list[dict]) -> list[Einheit]:
    """Whisper-Segmente → Satz-Einheiten mit Zeiten (über Wortzeitstempel, wenn vorhanden) + Klassifikation.

    Ein Segment „Babe? Musst du nicht aufwachen? Ich geh erst mal Tee machen.“ wird in drei Einheiten zerlegt,
    damit jede Skriptzeile einzeln zugeordnet werden kann."""
    out: list[Einheit] = []
    for seg in segmente or []:
        text = str(seg.get("text", "")).strip()
        if not text:
            continue
        woerter = seg.get("woerter") or []
        sprecher = seg.get("sprecher")
        if woerter and all(isinstance(w, dict) and w.get("start") is not None for w in woerter):
            buf: list[dict] = []
            for wi, w in enumerate(woerter):
                buf.append(w)
                wt = str(w.get("wort", w.get("word", ""))).strip()
                naechstes = str(woerter[wi + 1].get("wort", woerter[wi + 1].get("word", ""))).strip() if wi + 1 < len(woerter) else ""
                # „2.“ gefolgt von „1“ (Whisper zerlegt „2.1“) ist kein Satzende; „Take 3.“ gefolgt von „Babe?“ schon.
                zahl_fortsetzung = bool(re.fullmatch(r"\d+[.,]+", wt)) and naechstes[:1].isdigit()
                if _SATZENDE.search(wt) and not zahl_fortsetzung:
                    out.append(Einheit(float(buf[0]["start"]), float(buf[-1].get("end") or buf[-1]["start"]),
                                       " ".join(str(x.get("wort", x.get("word", ""))).strip() for x in buf).strip(),
                                       sprecher, woerter=buf))
                    buf = []
            if buf:
                out.append(Einheit(float(buf[0]["start"]), float(buf[-1].get("end") or buf[-1]["start"]),
                                   " ".join(str(x.get("wort", x.get("word", ""))).strip() for x in buf).strip(),
                                   sprecher, woerter=buf))
        else:
            # Ohne Wortzeiten: Sätze anteilig über die Segmentdauer verteilen
            saetze = [s for s in re.split(r"(?<=[.!?…])\s+", text) if s.strip()]
            s0, s1 = float(seg.get("start", 0.0)), float(seg.get("end", 0.0))
            gesamt = sum(len(s) for s in saetze) or 1
            t = s0
            for s in saetze:
                d = (s1 - s0) * len(s) / gesamt
                out.append(Einheit(t, t + d, s.strip(), sprecher))
                t += d
    # Reine Anrede-Einheiten („Offelia?“, „Babe.“) mit der direkt folgenden Einheit verschmelzen (< 1,2 s Abstand):
    # „Offelia? Bist du da?“ ist EINE Skriptzeile — getrennt ist der Name allein nicht zuordenbar.
    zusammen: list[Einheit] = []
    i = 0
    while i < len(out):
        e = out[i]
        m_anrede = re.fullmatch(r"(?:hey,?\s+|oh,?\s+)?([A-ZÄÖÜ][a-zäöüß]{2,})[,.!?]*", e.text.strip())
        if (i + 1 < len(out) and m_anrede and m_anrede.group(1).lower() not in _ANREDE_STOP
                and out[i + 1].start - e.end < 1.2 and out[i + 1].sprecher == e.sprecher):
            n = out[i + 1]
            zusammen.append(Einheit(e.start, n.end, f"{e.text.strip()} {n.text.strip()}", e.sprecher, woerter=e.woerter + n.woerter))
            i += 2
            continue
        zusammen.append(e)
        i += 1
    out = zusammen
    for e in out:
        e.art = klassifiziere_einheit(e.text)
    return out


@dataclass
class TakeBefund:
    klappe: Klappe
    einheiten: list[Einheit]
    spiel_start: float | None
    spiel_ende: float | None
    ng: dict


def analysiere_take(segmente: list[dict], dauer: float | None = None) -> TakeBefund:
    einheiten = einheiten_aus_segmenten(segmente)
    # Klappe aus den ROHEN Segmenttexten (Wort-Rekonstruktion kann „2.1“ zerlegen)
    kopf = " ".join(str(sg.get("text", "")) for sg in (segmente or []) if float(sg.get("start", 0.0)) < 25.0)
    klappe = parse_klappe(kopf)
    # Zwei-Pass: kurze Höflichkeits-/Zustimmungswörter („Bitte“, „Ja“, „Okay“, „Danke“) sind nur VOR dem
    # ersten echten Spielsatz (bzw. direkt nach der Klappe) Set-Kommandos — mitten im Spiel sind sie Text
    # („Bitte. Bitte. Bleib bei mir.“). „Danke“ am Ende bleibt Produktion (Regie-Cut).
    # Vor-Slate-Regel: Spiel beginnt nie vor der Klappe — alles, was vor dem Ende der ERSTEN
    # Klappen-Ansage startet, ist Set-Business („Leute!“, „Kamera läuft“ in Whisper-Varianten),
    # auch wenn der Wortlaut nicht im Produktions-Lexikon steht.
    erste_slate = min((e.end for e in einheiten if e.art == "slate"), default=None)
    if erste_slate is not None:
        for e in einheiten:
            if e.art == "spiel" and e.start < erste_slate:
                e.art = "produktion"
    erster_spiel = next((e.start for e in einheiten if e.art == "spiel"), None)
    letzte_slate = max((e.end for e in einheiten if e.art == "slate"), default=None)
    for e in einheiten:
        if e.art == "produktion" and _HOEFLICH.match(e.text) and erster_spiel is not None and e.start > erster_spiel:
            if not (_DANKE.match(e.text) and e.start >= max(x.end for x in einheiten) - 8.0):
                e.art = "spiel"
    # Crew-Stimme am Ende: ein Sprecher-Label, das in den letzten 15 % zum ersten Mal auftaucht, ist Set-Kommunikation
    # („Danke, das haben wir“ vom Regisseur), nicht Spiel — auch wenn der Wortlaut nicht im Produktions-Lexikon steht.
    if einheiten:
        t_ende = max(e.end for e in einheiten)
        grenze = t_ende * 0.85
        frueh = {e.sprecher for e in einheiten if e.sprecher and e.start < grenze}
        for e in einheiten:
            if e.art == "spiel" and e.sprecher and e.start >= grenze and e.sprecher not in frueh and len(e.text.split()) <= 12:
                e.art = "produktion"
    # Ausstieg aus dem Spiel am Take-Ende („Oh, sorry.“ nach 17 s Stille, Lachen, Crew): ab der ersten solchen Einheit
    # im letzten Drittel gilt alles als Produktion — der Schnitt darf dort nie hineinlaufen.
    if einheiten:
        t_ende = max(e.end for e in einheiten)
        letztes_spiel_ende = None
        ausstieg_ab = None
        spiel_einheiten = [e for e in einheiten if e.art == "spiel"]
        for e in einheiten:
            if e.art != "spiel":
                continue
            if e.start >= 0.66 * t_ende and letztes_spiel_ende is not None:
                luecke = e.start - letztes_spiel_ende
                kurz = len(e.text.split()) <= 6
                if (_AUSSTIEG.search(e.text) and (luecke >= 4.0 or kurz)) or (luecke >= 8.0 and kurz):
                    # Ruf-SERIE = gespielte Performance, kein Ausstieg: kurze Zurufe („Hallo?“,
                    # „Hey, sag mal!“ Richtung TV/Tür), die sich binnen 25 s wiederholen, sind Spiel —
                    # ein echter Ausstieg ist EIN Satz („Oh, sorry.“), gefolgt von Crew/Stille.
                    # (Befund 4.1 T3: fünf Rufe 70–90 s wurden als Produktion abgeschnitten, die
                    # Handlung „steht auf, geht zum Fernseher, beschwert sich“ verschwand.)
                    ist_ruf = kurz and len(e.text.split()) <= 4 and not _AUSSTIEG.search(e.text)
                    serie = ist_ruf and any(x is not e and e.end < x.start <= e.start + 25.0
                                            and len(x.text.split()) <= 4 and not _AUSSTIEG.search(x.text)
                                            for x in spiel_einheiten)
                    if serie:
                        letztes_spiel_ende = e.end
                        continue
                    ausstieg_ab = e.start
                    break
            letztes_spiel_ende = e.end
        # Nur MARKIEREN — endgültig entschieden wird nach dem Skript-Alignment (eine Einheit, die sicher eine Skriptzeile
        # trifft, ist kein Ausstieg: „Was war meine Schuld?“ nach 50 s Stille ist die letzte Zeile der Szene 3).
        for e in einheiten:
            e.ausstieg_kandidat = ausstieg_ab is not None and e.start >= ausstieg_ab and e.art == "spiel"
    spiel = [e for e in einheiten if e.art == "spiel"]
    spiel_start = spiel[0].start if spiel else None
    spiel_ende = spiel[-1].end if spiel else None
    gruende: list[str] = []
    abbruch = False
    # Abbruch: Produktions-Sprech NACH Spielbeginn, das eine Wiederholung verlangt
    if spiel_start is not None:
        for e in einheiten:
            if e.art == "produktion" and e.start > spiel_start and _ABBRUCH.search(e.text):
                abbruch = True
                gruende.append(f"Abbruch-Sprech bei {e.start:.0f}s: „{e.text[:60]}“")
                break
    kurz = bool(dauer and dauer < 25 and (spiel_start is None or (spiel_ende or 0) - spiel_start < 10))
    if kurz:
        gruende.append("sehr kurz (< 25 s, kaum Spiel)")
    if spiel_start is None:
        gruende.append("kein Spiel-Dialog (stumme Handlung oder Ton fehlt)")
    return TakeBefund(klappe, einheiten, spiel_start, spiel_ende,
                      {"abbruch": abbruch, "kurz": kurz, "gruende": gruende})
