from backend.core.sync.probe import (AssetProbe, analysiere_audio, analysiere_video, fingerprint,
                                     scanne_ordner, verwerfe_identische_container_tc)
from backend.core.sync.namen import NamensTeile
from backend.tests.conftest import AUDIO_DIR, VIDEO_DIR, korpus
from backend.tests.helfer import schreibe_bwf_wav


def test_scan_filtert_resource_forks_und_systemordner(tmp_path):
    (tmp_path / "A.MOV").write_bytes(b"x")
    (tmp_path / "._A.MOV").write_bytes(b"x")          # ExFAT-Resource-Fork mit Video-Endung
    (tmp_path / "b.mp4").write_bytes(b"x")
    (tmp_path / ".hidden.mov").write_bytes(b"x")
    (tmp_path / "notes.txt").write_bytes(b"x")
    for d in ("TRASH", "UNDO", "SETTINGS", "$RECYCLE.BIN"):
        (tmp_path / d).mkdir()
        (tmp_path / d / "C.MOV").write_bytes(b"x")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "D.MOV").write_bytes(b"x")
    (tmp_path / "sub" / "._D.MOV").write_bytes(b"x")
    r = scanne_ordner(tmp_path, "video")
    assert sorted(p.split("/")[-1] for p in r.dateien) == ["A.MOV", "D.MOV", "b.mp4"]
    assert r.ignoriert == 3
    ra = scanne_ordner(tmp_path, "audio")
    assert ra.dateien == []


def test_fingerprint_stabil(tmp_path):
    p = tmp_path / "x.bin"
    p.write_bytes(b"a" * 5000)
    assert fingerprint(p) == fingerprint(p)
    q = tmp_path / "y.bin"
    q.write_bytes(b"a" * 5001)
    assert fingerprint(p) != fingerprint(q)


def _probe(name, ctc, quelle="container"):
    return AssetProbe("video", name, name, 10.0, None, 4, 24.0, "prores", 1, ctc, 100.0, quelle, "24/1", "NDF",
                      None, "fp" + name, NamensTeile(None, None, None), None, container_tc=ctc)


def test_container_tc_identisch_wird_verworfen():
    ps = [_probe("a", "16:46:20:04"), _probe("b", "16:46:20:04"), _probe("c", "16:46:20:04"), _probe("d", "10:00:00:00")]
    n = verwerfe_identische_container_tc(ps)
    assert n == 3
    assert all(p.tc_quelle == "keine" and p.tc_start is None for p in ps[:3])
    assert ps[3].tc_quelle == "container" and ps[3].tc_start == "10:00:00:00"
    assert any("Export-Artefakt" in w for w in ps[0].warnungen)


def test_analysiere_audio_synthetisch(tmp_path):
    p = schreibe_bwf_wav(tmp_path / "+SZENE2-1-004.WAV", kanaele=2, dauer_s=1.0, time_reference=48000 * 3600,
                         scene="+SZENE2-1", take="004", tape="231118", tracks=["Record", "Safety"])
    pr = analysiere_audio(str(p), ordnername="11-18-23")
    assert pr.typ == "audio" and pr.tc_quelle == "bwf" and pr.tc_start == "01:00:00:00"
    assert (pr.namen.szene, pr.namen.plan, pr.namen.prise, pr.unbekannte_markierung) == (2, 1, 4, "+")
    assert pr.record_kanal == 0 and str(pr.datum) == "2023-11-18"


@korpus
def test_korpus_video_t001():
    pr = analysiere_video(str(VIDEO_DIR / "PPRM23_S004_S003_T001.MOV"))
    assert pr.tc_quelle == "ltc" and pr.tc_start == "12:57:04:07" and pr.ltc_kanal == 3
    assert pr.scratch_kanal is None                    # Kanäle 0–2 stumm → kein Scratch
    assert pr.container_tc == "16:46:20:04"
    assert (pr.namen.szene, pr.namen.plan, pr.namen.prise) == (4, 3, 1)
    assert pr.fps == 24.0 and abs(pr.dauer_s - 80.79) < 0.05


@korpus
def test_korpus_audio_szene4_3_002():
    pr = analysiere_audio(str(AUDIO_DIR / "+SZENE4-3-002.WAV"), ordnername="11-17-23")
    assert pr.tc_start == "12:57:01:14" and pr.tc_quelle == "bwf" and pr.record_kanal == 0
    assert pr.unbekannte_markierung == "+" and str(pr.datum) == "2023-11-17"
    assert any("umbenannt" in w for w in pr.warnungen)   # HISTORY/ORIGINAL_FILENAME ≠ CURRENT_FILENAME
