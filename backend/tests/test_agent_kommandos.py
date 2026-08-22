"""Validierung der Agent-Timeline-Kommandos (`pruefe_timeline_kommandos`) — pur, ohne DB/LLM.

Vertrag: nie werfen; kaputte Kommandos → `fehler`, gefährliche → geklemmt + `warnungen`;
alles Durchgelassene ist vom Editor direkt anwendbar (bekannte tlIds, Zeiten in [0, total]).
"""
from backend.api.agent_kontext_tools import pruefe_timeline_kommandos

TL = {
    "totalDuration": 100.0,
    "clips": [
        {"tlId": "tl-1", "clipId": "c1", "start": 0.0, "duration": 40.0},
        {"tlId": "tl-2", "clipId": "c2", "start": 40.0, "duration": 60.0},
    ],
}


def test_leere_liste_ist_fehler():
    ok, warn, fehler = pruefe_timeline_kommandos([], TL)
    assert ok == [] and fehler


def test_kein_snapshot_grenzen_bleiben_offen():
    ok, warn, fehler = pruefe_timeline_kommandos([{"type": "split", "at": 12.0}], None)
    assert len(ok) == 1 and ok[0]["at"] == 12.0 and not fehler


def test_trim_gueltig():
    ok, warn, fehler = pruefe_timeline_kommandos([{"type": "trim", "tlId": "tl-2", "side": "right", "delta": -2}], TL)
    assert len(ok) == 1 and ok[0]["delta"] == -2 and not warn and not fehler


def test_trim_unbekannte_tlid_ist_fehler():
    ok, warn, fehler = pruefe_timeline_kommandos([{"type": "trim", "tlId": "nope", "side": "right", "delta": -2}], TL)
    assert ok == [] and any("existiert nicht" in f for f in fehler)


def test_trim_darf_clip_nicht_ausloeschen():
    ok, warn, fehler = pruefe_timeline_kommandos([{"type": "trim", "tlId": "tl-1", "side": "right", "delta": -45}], TL)
    assert len(ok) == 1
    assert ok[0]["delta"] == -39.5           # 40 s Clip → mind. 0,5 s bleiben
    assert any("auslöschen" in w for w in warn)


def test_deleterange_wird_geklemmt():
    ok, warn, fehler = pruefe_timeline_kommandos([{"type": "deleteRange", "from": 95, "to": 130}], TL)
    assert len(ok) == 1 and ok[0]["to"] == 100.0 and ok[0]["ripple"] is True
    assert any("begrenzt" in w for w in warn)


def test_deleterange_leer_ist_fehler():
    ok, warn, fehler = pruefe_timeline_kommandos([{"type": "deleteRange", "from": 50, "to": 50}], TL)
    assert ok == [] and fehler


def test_delete_filtert_unbekannte_ids():
    ok, warn, fehler = pruefe_timeline_kommandos([{"type": "delete", "tlIds": ["tl-1", "geist"]}], TL)
    assert len(ok) == 1 and ok[0]["tlIds"] == ["tl-1"] and any("ignoriert" in w for w in warn)


def test_unbekannter_typ_ist_fehler_rest_geht_durch():
    ok, warn, fehler = pruefe_timeline_kommandos(
        [{"type": "kaboom"}, {"type": "addMarker", "at": 10, "label": "x"}], TL)
    assert len(ok) == 1 and ok[0]["type"] == "addMarker"
    assert any("unbekannter Typ" in f for f in fehler)


def test_setfade_klemmt_dauer():
    ok, warn, fehler = pruefe_timeline_kommandos([{"type": "setFade", "tlId": "tl-1", "side": "in", "duration": 99}], TL)
    assert len(ok) == 1 and ok[0]["duration"] == 10.0


def test_setgain_klemmt():
    ok, _, _ = pruefe_timeline_kommandos([{"type": "setGain", "tlId": "tl-1", "gainDb": 40}], TL)
    assert ok[0]["gainDb"] == 12.0


def test_loadsequence_saeubert_segmente():
    ok, warn, fehler = pruefe_timeline_kommandos([{"type": "loadSequence", "segments": [
        {"clipId": "c1", "mediaStart": -3, "duration": 5},
        {"duration": 5},                       # ohne clipId → fällt raus
    ]}], TL)
    assert len(ok) == 1 and len(ok[0]["segments"]) == 1
    assert ok[0]["segments"][0]["mediaStart"] == 0.0


def test_insert_defaults():
    ok, warn, fehler = pruefe_timeline_kommandos([{"type": "insert", "clipId": "c9", "at": 5}], TL)
    assert len(ok) == 1 and ok[0]["mode"] == "insert" and ok[0]["videoTrackIndex"] == 0


def test_insert_videoonly_passthrough():
    ok, warn, fehler = pruefe_timeline_kommandos(
        [{"type": "insert", "clipId": "c9", "at": 5, "videoTrackIndex": 1, "mode": "append", "videoOnly": 1}], TL)
    assert len(ok) == 1 and ok[0]["videoOnly"] is True and ok[0]["videoTrackIndex"] == 1
