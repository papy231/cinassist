"""Matcher-Kaskade auf synthetischen Assets — die 7 Fälle aus dem Auftrag (Szene 4 / Einstellung 3)
und die Regeln, die der volle Korpus erzwungen hat (Durchlauf-Ton, Randüberlappung, parallele Videos)."""

from datetime import date
from fractions import Fraction

from backend.core.sync.matcher import AssetInfo, matche, matche_nach_dateiname
from backend.core.sync.namen import NamensTeile

R24 = Fraction(24)
TAG = date(2023, 11, 17)


def tc(h, m, s, f=0.0):
    return h * 3600 + m * 60 + s + f


def video(id_, name, start, dauer, szene, plan, prise, scratch=None):
    return AssetInfo(id_, "video", name, dauer, start, R24, "ltc", None,
                     NamensTeile(szene, plan, prise), scratch_kanal=scratch)


def audio(id_, name, start, dauer, szene, plan, prise, mark=None):
    return AssetInfo(id_, "audio", name, dauer, start, R24, "bwf", TAG,
                     NamensTeile(szene, plan, prise, mark), record_kanal=0)


# Werte aus dem Auftrag (Szene 4 / Einstellung 3), Dauern aus dem Korpus.
VIDEOS = [
    video("v1", "PPRM23_S004_S003_T001.MOV", tc(12, 57, 4, 7 / 24), 80.8, 4, 3, 1),
    video("v2", "PPRM23_S004_S003_T002.MOV", tc(13, 0, 46, 12 / 24), 84.2, 4, 3, 2),
    video("v3", "PPRM23_S004_S003_T003.MOV", tc(13, 9, 27, 1 / 24), 85.8, 4, 3, 3),
    video("v4", "PPRM23_S004_S003_T004.MOV", tc(13, 12, 6, 15 / 24), 81.5, 4, 3, 4),
    video("v5", "PPRM23_S004_S003_T005.MOV", tc(13, 15, 4, 1 / 24), 86.9, 4, 3, 5),
    video("v6", "PPRM23_S004_S003_T006.MOV", tc(13, 30, 25, 15 / 24), 36.5, 4, 3, 6),
]
AUDIOS = [
    audio("a1", "+SZENE4-3-001.WAV", tc(12, 9, 21, 0.8), 42.9, 4, 3, 1, "+"),
    audio("a2", "+SZENE4-3-002.WAV", tc(12, 57, 1, 0.583), 81.9, 4, 3, 2, "+"),
    audio("a3", "+SZENE4-3-003.WAV", tc(13, 0, 43, 0.38), 87.2, 4, 3, 3, "+"),
    audio("a4", "+SZENE4-3-004.WAV", tc(13, 9, 23, 0.96), 88.3, 4, 3, 4, "+"),
    audio("a5", "+SZENE4-3-005.WAV", tc(13, 12, 4, 0.38), 84.6, 4, 3, 5, "+"),
    audio("a6", "SZENE4-3-006.WAV", tc(13, 15, 1, 0.33), 89.8, 4, 3, 6),
]


def _by_video(erg):
    return {t.video_id: t for t in erg.takes}


def test_szene4_plan3_die_sieben_faelle():
    erg = matche(VIDEOS, AUDIOS)
    tv = _by_video(erg)
    erwartet = {"v1": ("a2", -2.708), "v2": ("a3", -3.12), "v3": ("a4", -3.082), "v4": ("a5", -2.245), "v5": ("a6", -2.712)}
    for vid, (aid, off) in erwartet.items():
        t = tv[vid]
        assert t.status == "sicher", (vid, t.status, t.warnungen)
        assert len(t.links) == 1 and t.links[0].audio_id == aid
        assert t.links[0].methode == "timecode"
        assert abs(t.links[0].offset_s - off) <= 0.05, (vid, t.links[0].offset_s)
        assert t.links[0].konfidenz >= 0.95
        assert any("Take-Nummern verschoben" in w for w in t.links[0].warnungen), t.links[0].warnungen
        assert "Timecode:" in t.links[0].begruendung
    # T006: Bild allein
    assert tv["v6"].status == "verwaist" and not tv["v6"].links
    # +SZENE4-3-001: Ton allein (Probe)
    waisen = [t for t in erg.takes if t.video_id is None]
    assert len(waisen) == 1 and waisen[0].audio_ids_verwaist == ["a1"]
    assert any("unbekannte_markierung" in w for w in waisen[0].warnungen)
    # "+" wird auf den 4 markierten, verknüpften WAVs gemeldet
    plus = [t for t in erg.takes if t.links and any("unbekannte_markierung" in w for w in t.links[0].warnungen)]
    assert len(plus) == 4
    # Wellenform ehrlich als nicht anwendbar (kein Scratch)
    assert erg.statistik["wellenform_nicht_anwendbar"] == 5


def test_deterministisch():
    e1 = matche(VIDEOS, AUDIOS)
    e2 = matche(list(reversed(VIDEOS)), list(reversed(AUDIOS)))
    f = lambda e: [(t.video_id, t.status, [(l.audio_id, l.offset_s, l.methode, l.konfidenz) for l in t.links]) for t in e.takes]
    assert f(e1) == f(e2)


def test_ton_laeuft_ueber_zwei_takes_durch():
    """Ein WAV deckt zwei aufeinanderfolgende Videos zu > 80 % → beide sicher, je eigener Offset."""
    v1 = video("v1", "S001_T001.MOV", tc(11, 18, 32), 19.3, 1, 1, 1)
    v2 = video("v2", "S001_T002.MOV", tc(11, 19, 38), 98.8, 1, 1, 2)
    a = audio("a", "SZENE1-002.WAV", tc(11, 18, 34), 163.0, 1, None, 2)
    erg = matche([v1, v2], [a])
    tv = _by_video(erg)
    assert tv["v1"].status == "sicher" and tv["v1"].links[0].offset_s == 2.0
    assert tv["v2"].status == "sicher" and tv["v2"].links[0].offset_s == -64.0
    assert any("mehrere Video-Takes" in w for w in tv["v1"].links[0].warnungen)


def test_randueberlappung_blockiert_nicht():
    """Audio startet 6 s vor Ende des Vorgänger-Videos: starker Treffer bleibt sicher, Rand nur Hinweis."""
    v_alt = video("valt", "S003_T003.MOV", tc(13, 29, 26), 63.5, 3, 2, 3)
    v_neu = video("vneu", "S004_T006.MOV", tc(13, 30, 25, 0.6), 36.5, 4, 3, 6)
    a = audio("a", "SZENE4-4-001.WAV", tc(13, 30, 23, 0.3), 40.6, 4, 4, 1)
    erg = matche([v_alt, v_neu], [a])
    tv = _by_video(erg)
    assert tv["vneu"].status == "sicher" and tv["vneu"].links[0].audio_id == "a"
    assert tv["valt"].status == "verwaist" and not tv["valt"].links
    assert any("Randüberlappung" in w for w in tv["valt"].warnungen)


def test_multicam_gleicher_ton_an_beide_kameras():
    """Zwei Videos laufen weitgehend parallel (≥ 50 % Überlappung) und dasselbe Audio deckt beide
    → Multicam-Gruppe: Ton an BEIDE (je Offset), plausibel, nicht blockierend."""
    v1 = video("v1", "S003_S001_T001.MOV", tc(11, 45, 43), 131.8, 3, 1, 1)
    v2 = video("v2", "S004_S001_T004.MOV", tc(11, 45, 58), 92.8, 4, 1, 4)
    a = audio("a", "SZENE4-005.WAV", tc(11, 45, 55), 98.9, 4, None, 5)
    erg = matche([v1, v2], [a])
    tv = _by_video(erg)
    assert tv["v1"].status == "plausibel" and tv["v2"].status == "plausibel"
    assert tv["v1"].links[0].audio_id == "a" and tv["v2"].links[0].audio_id == "a"
    assert abs(tv["v1"].links[0].offset_s - 12.0) < 0.01 and abs(tv["v2"].links[0].offset_s - (-3.0)) < 0.01
    assert tv["v1"].multicam_gruppe and tv["v1"].multicam_gruppe == tv["v2"].multicam_gruppe
    assert any("Multicam" in w for w in tv["v1"].links[0].warnungen)
    assert erg.statistik["multicam_gruppen"] == 1
    assert not [t for t in erg.takes if t.video_id is None]     # Audio ist nicht verwaist


def test_teilweise_parallele_videos_bleiben_unklar():
    """Videos überlappen sich nur zu ~30 % und dasselbe Audio deckt beide stark → Konflikt → unklar."""
    v1 = video("v1", "A.MOV", tc(10, 0, 0), 60.0, 1, 1, 1)
    v2 = video("v2", "B.MOV", tc(10, 0, 42), 60.0, 1, 1, 2)     # 18 s Überlappung = 30 %
    a = audio("a", "SZENE1-001.WAV", tc(9, 59, 58), 110.0, 1, 1, 1)
    erg = matche([v1, v2], [a])
    tv = _by_video(erg)
    assert tv["v1"].status == "unklar" and tv["v2"].status == "unklar"
    assert {k.audio_id for k in tv["v1"].kandidaten} == {"a"}
    waisen = [t for t in erg.takes if t.video_id is None]
    assert waisen and waisen[0].audio_ids_verwaist == ["a"]


def test_teilueberlappung_plausibel_und_ohne_tc_verwaist():
    v = video("v", "S001_T001.MOV", tc(10, 0, 0), 60.0, 1, 1, 1)
    a = audio("a", "SZENE1-001.WAV", tc(10, 0, 40), 60.0, 1, 1, 1)     # 20 s / 60 s = 33 %
    erg = matche([v], [a])
    t = _by_video(erg)["v"]
    assert t.status == "plausibel" and t.links[0].konfidenz < 0.95
    ohne = AssetInfo("x", "video", "ohne_tc.MOV", 30.0, None, None, "keine", None, NamensTeile(None, None, None))
    erg2 = matche([ohne], [a])
    t2 = _by_video(erg2)["x"]
    assert t2.status == "verwaist" and any("ohne verwertbaren Timecode" in w for w in t2.warnungen)


def test_wellenform_bestaetigt_oder_widerspricht():
    class R:
        def __init__(self, off): self.anwendbar, self.offset_s, self.konfidenz, self.begruendung = True, off, 0.8, "wf"
    v = video("v", "S001_T001.MOV", tc(10, 0, 0), 60.0, 1, 1, 1, scratch=0)
    a = audio("a", "SZENE1-001.WAV", tc(9, 59, 58), 70.0, 1, 1, 1)
    ok = matche([v], [a], waveform_fn=lambda a_, v_: R(-2.0))
    assert ok.takes[0].status == "sicher" and "Wellenform bestätigt" in ok.takes[0].links[0].begruendung
    schlecht = matche([v], [a], waveform_fn=lambda a_, v_: R(-1.0))
    assert schlecht.takes[0].status == "plausibel" and any("widerspricht" in w for w in schlecht.takes[0].links[0].warnungen)


def test_stufe2_ohne_tc_mit_scratch():
    class R:
        anwendbar, offset_s, konfidenz, begruendung = True, -1.25, 0.8, "wf ok"
    v = AssetInfo("v", "video", "S001_S001_T001.MOV", 30.0, None, None, "keine", None, NamensTeile(1, 1, 1), scratch_kanal=0)
    a = AssetInfo("a", "audio", "SZENE1-1-001.WAV", 40.0, None, None, "keine", TAG, NamensTeile(1, 1, 1))
    erg = matche([v], [a], waveform_fn=lambda a_, v_: R())
    t = erg.takes[0]
    assert t.status == "plausibel" and t.links[0].methode == "waveform" and t.links[0].offset_s == -1.25


def test_dateiname_nur_auf_wunsch():
    erg = matche(VIDEOS[:1], [audio("ax", "SZENE4-3-001.WAV", None, 50.0, 4, 3, 1)])   # kein TC → nichts automatisch
    assert not _by_video(erg)["v1"].links
    manuell = matche_nach_dateiname(VIDEOS[:1], [audio("ax", "SZENE4-3-001.WAV", None, 50.0, 4, 3, 1)])
    assert len(manuell) == 1 and manuell[0][1].methode == "dateiname" and manuell[0][1].konfidenz <= 0.3
