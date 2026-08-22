"""Integration gegen `cinassist_test`: Import per Referenz (idempotent) + Matching (deterministisch)
auf dem Abnahme-Korpus „Szene 4“ (ROHMAT_VIDEO/PPRM23_S004_* + ROHMAT_AUDIO/11-17-23/*SZENE4*).

Befund gegenüber der Erwartungstabelle des Auftrags (die nur Einstellung 3 kannte):
- `+SZENE4-3-001` ist KEIN Waisen-Ton — sein Timecode (12:09:21,8, 42,9 s) liegt zu 100 % in
  `PPRM23_S004_S002_T002.MOV` (12:09:24,0, 38,8 s) → sicher, mit Warnung „Einstellungs-Nummern
  widersprechen sich“.
- `T006` ist KEIN Waisen-Bild — `SZENE4-4-001.WAV` (13:30:23,3) deckt es zu 100 % → sicher.
Beides ist Timecode-Evidenz derselben Uhr; die Erwartung „verwaist“ gilt nur für die
Teilmenge Einstellung 3 (siehe test_matcher.py). Wir testen die Daten, nicht die Tabelle.
"""

import os
import uuid
from pathlib import Path

import pytest

from backend.tests.conftest import AUDIO_DIR, VIDEO_DIR, db, korpus

pytestmark = [korpus, db]


def _link_ordner(tmp_path: Path, quelle: Path, muster: str, name: str) -> Path:
    ziel = tmp_path / name
    ziel.mkdir()
    for p in sorted(quelle.glob(muster)):
        if p.name.startswith("._"):
            continue
        os.symlink(p, ziel / p.name)
    (ziel / "._PPRM23_FAKE.MOV").write_bytes(b"\x00\x05\x16\x07")   # Resource-Fork-Attrappe
    (ziel / "._SZENE_FAKE.WAV").write_bytes(b"\x00\x05\x16\x07")
    return ziel


@pytest.fixture(scope="module")
def importe(db_session, tmp_path_factory):
    from backend.core.database import OrdnerImport
    from backend.workers.sync import fuehre_import_aus
    tmp = tmp_path_factory.mktemp("korpus")
    v_dir = _link_ordner(tmp, VIDEO_DIR, "PPRM23_S004_*.MOV", "video")
    a_dir = _link_ordner(tmp, AUDIO_DIR, "*SZENE4*.WAV", "audio")
    iv = OrdnerImport(id=uuid.uuid4(), pfad=str(v_dir), typ="video")
    ia = OrdnerImport(id=uuid.uuid4(), pfad=str(a_dir), typ="audio")
    db_session.add_all([iv, ia]); db_session.commit()
    rv = fuehre_import_aus(db_session, str(iv.id))
    ra = fuehre_import_aus(db_session, str(ia.id))
    return db_session, iv, ia, rv, ra


def test_import_zaehlt_und_filtert(importe):
    from backend.core.database import MediaAsset
    db_, iv, ia, rv, ra = importe
    assert rv["anzahl_dateien"] == 14 and rv["anzahl_ignoriert"] == 1 and rv["fehler"] == []
    assert ra["anzahl_dateien"] == 14 and ra["anzahl_ignoriert"] == 1 and ra["fehler"] == []
    assert rv["container_tc_verworfen"] == 14                  # 16:46:20:04 überall → verworfen
    assert db_.query(MediaAsset).count() == 28
    v = db_.query(MediaAsset).filter(MediaAsset.dateiname == "PPRM23_S004_S003_T001.MOV").one()
    assert v.tc_quelle == "ltc" and v.tc_start == "12:57:04:07" and v.ltc_kanal == 3 and v.dateigroesse > 1_500_000_000
    a = db_.query(MediaAsset).filter(MediaAsset.dateiname == "+SZENE4-3-002.WAV").one()
    assert a.tc_quelle == "bwf" and a.record_kanal == 0 and a.unbekannte_markierung == "+"


def test_reimport_ist_idempotent(importe):
    from backend.core.database import MediaAsset
    from backend.workers.sync import fuehre_import_aus
    db_, iv, ia, *_ = importe
    n = db_.query(MediaAsset).count()
    r = fuehre_import_aus(db_, str(ia.id))
    assert r["neu"] == 0 and r["aktualisiert"] == 14
    assert db_.query(MediaAsset).count() == n


def _snapshot(db_):
    from backend.core.database import Take
    rows = []
    for t in db_.query(Take).all():
        v = t.video_asset.dateiname if t.video_asset else None
        rows.append((v, t.status, tuple(sorted((l.audio_asset.dateiname, round(l.offset_s, 3), l.methode) for l in t.audio_links))))
    return sorted(rows, key=str)


def test_matching_abnahme_szene4(importe):
    from backend.core.database import Take
    from backend.workers.sync import fuehre_matching_aus
    db_, *_ = importe
    r = fuehre_matching_aus(db_)
    assert r["warnungen"] == []
    by_video = {t.video_asset.dateiname: t for t in db_.query(Take).all() if t.video_asset}

    erwartet = {  # Offsets aus den Timecodes der Tabelle im Auftrag (Audio bext − Video LTC)
        "PPRM23_S004_S003_T001.MOV": ("+SZENE4-3-002.WAV", -2.708),
        "PPRM23_S004_S003_T002.MOV": ("+SZENE4-3-003.WAV", -3.117),
        "PPRM23_S004_S003_T003.MOV": ("+SZENE4-3-004.WAV", -3.077),
        "PPRM23_S004_S003_T004.MOV": ("+SZENE4-3-005.WAV", -2.243),
        "PPRM23_S004_S003_T005.MOV": ("SZENE4-3-006.WAV", -2.702),
    }
    for vname, (aname, off) in erwartet.items():
        t = by_video[vname]
        assert t.status == "sicher", (vname, t.status, t.warnungen)
        assert [l.audio_asset.dateiname for l in t.audio_links] == [aname]
        l = t.audio_links[0]
        assert l.methode == "timecode" and abs(l.offset_s - off) <= 0.05, (vname, l.offset_s)
        assert any("Take-Nummern verschoben" in w for w in l.warnungen)
        assert l.kanal_fuer_transkription == 0
    # "+" auf allen 5 markierten Audios gemeldet
    plus_links = [l for t in by_video.values() for l in t.audio_links if l.audio_asset.dateiname.startswith("+")]
    assert len(plus_links) == 5 and all(any("unbekannte_markierung" in w for w in l.warnungen) for l in plus_links)

    # Befund (siehe Modul-Docstring): beide vermeintlichen Waisen haben Timecode-Partner.
    t001 = by_video["PPRM23_S004_S002_T002.MOV"]
    assert t001.status == "sicher" and t001.audio_links[0].audio_asset.dateiname == "+SZENE4-3-001.WAV"
    assert any("Einstellungs-Nummern" in w for w in t001.audio_links[0].warnungen)
    t006 = by_video["PPRM23_S004_S003_T006.MOV"]
    assert t006.status == "sicher" and t006.audio_links[0].audio_asset.dateiname == "SZENE4-4-001.WAV"

    # Container-TC-Verwerfung ist am Take sichtbar
    assert any("Export-Artefakt" in w for w in by_video["PPRM23_S004_S003_T001.MOV"].warnungen)


def test_matching_zweimal_identisch(importe):
    from backend.workers.sync import fuehre_matching_aus
    db_, *_ = importe
    fuehre_matching_aus(db_)
    s1 = _snapshot(db_)
    fuehre_matching_aus(db_)
    assert _snapshot(db_) == s1
