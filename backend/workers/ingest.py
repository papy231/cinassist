"""
CinAssist — Ingestion Worker (Vollständige Analyse-Pipeline)

Sequenz nach Video-Upload:
  1. FFmpeg → Audio extrahieren (WAV 16kHz mono)
  2. mlx-whisper → Transkription mit Timestamps
  3. PySceneDetect → Szenen erkennen + Thumbnails
  4. CLIP → Jede Szene visuell embedden
  5. Ollama/LLaMA3 → Jede Szene beschreiben (1 Satz)

Alles 100% lokal auf Apple Silicon M3 Pro.
"""

import json
import logging
import re
import subprocess
import tempfile
import uuid
from pathlib import Path
from typing import Any

from backend.core.celery_app import celery_app
from backend.core.config import (
    AUDIO_SAMPLE_RATE,
    CLIP_MODEL,
    FFMPEG_BIN,
    FFPROBE_BIN,
    OLLAMA_BASE_URL,
    OLLAMA_MODEL,
    PROXY_DIR,
    SCENE_THRESHOLD,
    TEMP_DIR,
    WHISPER_MODEL,
)
from backend.core.database import SyncSessionLocal, Clip, Szene, Job, Take, TakeAudioLink
from backend.core.medien import clip_stem

logger = logging.getLogger("cinassist.ingest")


# ─── Hilfsfunktionen ────────────────────────────────────

def _update_job(
    job_id: str,
    status: str,
    fortschritt: int,
    nachricht: str,
    ergebnis: dict | None = None,
    schritt: str | None = None,
    schritt_daten: dict | None = None,
):
    """
    Job-Status in der Datenbank aktualisieren + Redis pubsub für WebSocket.

    Optional:
      schritt       — Kurz-ID des aktuellen Pipeline-Schritts
                      (z.B. "metadaten", "transkription", "szenenerkennung")
      schritt_daten — Konkrete Ergebnis-Daten (Belege) für diesen Schritt;
                      wenn gesetzt: Schritt gilt als abgeschlossen
                      wenn None: Schritt läuft gerade
    """
    db = SyncSessionLocal()
    try:
        job = db.query(Job).filter(Job.id == job_id).first()
        if job:
            job.status = status
            job.fortschritt = fortschritt
            job.nachricht = nachricht
            if ergebnis:
                job.ergebnis = ergebnis
            db.commit()
    finally:
        db.close()

    # Redis Pub/Sub für WebSocket-Benachrichtigung
    try:
        import redis
        from backend.core.config import REDIS_URL
        r = redis.from_url(REDIS_URL)
        payload: dict = {
            "status": status,
            "progress": fortschritt,
            "message": nachricht,
            "result": ergebnis,
        }
        if schritt is not None:
            payload["schritt"] = schritt
        if schritt_daten is not None:
            payload["schritt_daten"] = schritt_daten
        r.publish(f"job:{job_id}", json.dumps(payload))
    except Exception as e:
        logger.warning(f"Redis Pub/Sub fehlgeschlagen: {e}")


def _get_video_info(pfad: str) -> dict:
    """Video-Metadaten per ffprobe auslesen."""
    cmd = [
        FFPROBE_BIN, "-v", "quiet",
        "-print_format", "json",
        "-show_format", "-show_streams",
        pfad,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    if result.returncode != 0 or not (result.stdout or "").strip():
        raise RuntimeError(f"ffprobe konnte {pfad} nicht lesen (Datei/Volume nicht erreichbar?)")
    data = json.loads(result.stdout)

    video_stream = next(
        (s for s in data.get("streams", []) if s.get("codec_type") == "video"),
        {}
    )
    fmt = data.get("format", {})

    dauer = float(fmt.get("duration", 0))
    breite = int(video_stream.get("width", 0))
    hoehe = int(video_stream.get("height", 0))

    # Bildrate berechnen
    fps_str = video_stream.get("r_frame_rate", "24/1")
    num, den = fps_str.split("/") if "/" in fps_str else (fps_str, "1")
    bildrate = round(float(num) / float(den), 2)

    audio_stream = next((s for s in data.get("streams", []) if s.get("codec_type") == "audio"), {})
    return {
        "dauer": dauer,
        "aufloesung": f"{breite}x{hoehe}",
        "bildrate": bildrate,
        "codec": video_stream.get("codec_name") or audio_stream.get("codec_name") or "unbekannt",
        "hat_video": bool(video_stream),
        "audio_kanaele": int(audio_stream.get("channels") or 0),
    }


# ═══════════════════════════════════════════════════════════
# SCHRITT 0: Verknüpfter Ton (Sync-Modell, Take → TakeAudioLink)
# ═══════════════════════════════════════════════════════════
# Auf dem Referenz-Korpus enthält die Kamera-Spur nur LTC + Stille — Whisper auf
# diesem Signal halluziniert („Thank you.“). Hat der Clip einen Take mit
# verknüpftem WAV, wird die Spur `kanal_fuer_transkription` (Spur „Record“) um
# `offset_s` auf die Video-Zeitachse ausgerichtet; Whisper, Diarization, Proxy
# und Waveform laufen dann auf DIESEM Ton. Sonst: Kamera-Ton + Warnung.

def _verknuepfter_ton(db, clip: Clip) -> dict | None:
    """Primäres Audio des Takes (bestätigt > höchste Konfidenz > ältestes) oder None."""
    if not getattr(clip, "take_id", None):
        return None
    take = db.query(Take).filter(Take.id == clip.take_id).first()
    if not take:
        return None
    links = [lk for lk in take.audio_links if lk.methode != "verwaist" and lk.audio_asset is not None]
    if not links or take.status in ("unklar", "manuell_abgelehnt"):
        return {"take_id": str(take.id), "take_status": take.status, "audio_pfad": None,
                "warnung": "Kein verknüpfter Ton — Transkription auf Kamera-Ton"}
    # Auswahl: bestätigt > größte zeitliche ABDECKUNG des Videos > Konfidenz. (Beobachtet: ein Take mit
    # zwei verlinkten WAVs — 1,6-s-Fehlstart mit 0.99 und die echte 154-s-Aufnahme mit 0.987 — nahm den
    # Fehlstart → Transkript „Nice.“ statt der Szene.)
    video_dauer = float(getattr(clip, "dauer", None) or 0.0)

    def _abdeckung(lk) -> float:
        a_d = float(lk.audio_asset.dauer_s or 0.0); off = float(lk.offset_s or 0.0)
        if video_dauer <= 0:
            return a_d
        return max(0.0, min(video_dauer, off + a_d) - max(0.0, off))

    links.sort(key=lambda lk: (not bool(lk.bestaetigt), -_abdeckung(lk), -(lk.konfidenz or 0.0), lk.erstellt_am or 0))
    lk = links[0]
    a = lk.audio_asset
    kanal = int(lk.kanal_fuer_transkription or 0)
    kanal_grund = "Spur „Record“ (Mix)" if kanal == int(a.record_kanal or 0) else "vom Nutzer gewählt"
    # Kanalwahl: „sprachreichster“ = Kanal mit den meisten VAD-Sprachsekunden (oft die Angel, nicht der Mix)
    try:
        from backend.core import einstellungen as E
        if E.transkription().get("kanal", "sprachreichster") == "sprachreichster" and (a.kanaele or 0) > 1 \
                and not lk.bestaetigt and Path(a.pfad).exists():
            from backend.core.sync.tonklasse import klassifiziere_datei
            b = klassifiziere_datei(a.pfad, int(a.kanaele), sekunden=180.0, ffmpeg_bin=FFMPEG_BIN)
            best = None
            for k in b.kanaele:
                if k.sprache_s is not None and (best is None or k.sprache_s > best.sprache_s + 0.5):
                    best = k
            if best is not None and best.sprache_s and best.kanal != kanal:
                kanal, kanal_grund = best.kanal, f"sprachreichster Kanal ({best.sprache_s:.0f} s Sprache, VAD)"
    except Exception as e:  # nie blockieren
        logger.warning(f"Kanalwahl fehlgeschlagen, nutze Kanal {kanal}: {e}")
    return {
        "take_id": str(take.id), "take_status": take.status, "link_id": str(lk.id),
        "audio_pfad": a.pfad, "audio_dateiname": a.dateiname, "audio_asset_id": str(a.id),
        "kanal": kanal, "kanal_grund": kanal_grund, "offset_s": float(lk.offset_s or 0.0),
        "methode": lk.methode, "konfidenz": lk.konfidenz, "begruendung": lk.begruendung,
        "weitere_audios": len(links) - 1, "abdeckung_s": round(_abdeckung(lk), 1), "warnung": None,
    }


def _ton_ausrichten(ton: dict, video_dauer: float | None, job_id: str) -> str | None:
    """Verknüpftes WAV → Mono-Spur `kanal`, um offset_s auf Video-Zeit 0 ausgerichtet (48 kHz, TEMP).

    offset_s = audio_start − video_start:
      < 0 → Ton lief vor dem Bild los → die ersten |offset| s abschneiden (atrim)
      > 0 → Ton startete später → mit |offset| s Stille auffüllen (adelay)
    """
    if not ton or not ton.get("audio_pfad"):
        return None
    if not Path(ton["audio_pfad"]).exists():
        logger.warning(f"Verknüpftes Audio nicht erreichbar: {ton['audio_pfad']}")
        return None
    k = ton["kanal"]
    off = ton["offset_s"]
    filt = f"pan=mono|c0=c{k}"
    if off < 0:
        filt += f",atrim=start={-off:.3f},asetpts=PTS-STARTPTS"
    elif off > 0:
        filt += f",adelay={int(round(off * 1000))}:all=1"
    ziel = TEMP_DIR / f"{uuid.uuid4().hex}_ton48k.wav"
    cmd = [FFMPEG_BIN, "-y", "-nostdin", "-i", ton["audio_pfad"], "-vn", "-af", filt,
           "-ar", "48000", "-ac", "1", "-acodec", "pcm_s16le"]
    if video_dauer and video_dauer > 0:
        cmd += ["-t", f"{video_dauer:.3f}"]
    cmd.append(str(ziel))
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    except subprocess.TimeoutExpired:
        logger.error("Ton-Ausrichtung: Timeout")
        return None
    if r.returncode != 0 or not ziel.exists() or ziel.stat().st_size == 0:
        logger.error(f"Ton-Ausrichtung fehlgeschlagen: {r.stderr[-300:]}")
        ziel.unlink(missing_ok=True)
        return None
    return str(ziel)


def _audio_dauer_s(pfad: str) -> float:
    """Dauer der Audiodatei in Sekunden (ffprobe; 0.0 wenn nicht bestimmbar)."""
    try:
        r = subprocess.run([FFPROBE_BIN, "-v", "error", "-show_entries", "format=duration",
                            "-of", "csv=p=0", pfad], capture_output=True, text=True, timeout=30)
        return float((r.stdout or "0").strip() or 0.0)
    except Exception:
        return 0.0


def _anzahl_audio_kanaele(pfad: str) -> int:
    """Kanäle des ersten Audiostreams (0 = keine Audiospur)."""
    try:
        r = subprocess.run([FFPROBE_BIN, "-v", "error", "-select_streams", "a:0", "-show_entries", "stream=channels",
                            "-of", "csv=p=0", pfad], capture_output=True, text=True, timeout=30)
        return int((r.stdout or "0").strip().split(",")[0] or 0)
    except Exception:
        return 0


# ═══════════════════════════════════════════════════════════
# SCHRITT 1: Audio extrahieren (FFmpeg → WAV 16kHz mono)
# ═══════════════════════════════════════════════════════════

def schritt_audio_extrahieren(video_pfad: str, job_id: str, ton_pfad: str | None = None,
                              ton: dict | None = None, tonbefund=None) -> str | None:
    """Extrahiert Audio als WAV 16kHz mono für Whisper.

    Quelle = das ausgerichtete verknüpfte WAV (`ton_pfad`, Sync-Modell), sonst die
    Kamera-Spur des Videos — dann mit Warnung „Transkription auf Kamera-Ton“.
    """
    _update_job(job_id, "laeuft", 5, "Audio wird extrahiert (FFmpeg)...", schritt="audio")

    audio_pfad = TEMP_DIR / f"{uuid.uuid4().hex}_audio.wav"
    quelle = ton_pfad or video_pfad
    filter_args: list[str] = ["-ac", "1"]
    tonbefund = None

    # Ohne verknüpften Ton: erst prüfen, ob die Kameraspur überhaupt Nutzton enthält.
    # Auf dem Referenz-Korpus sind Kanal 0–2 stumm und Kanal 3 ist LTC → ein blinder
    # Mono-Downmix liefert Whisper nur Timecode-Gezirpe → Halluzination („Thank you.“).
    if not ton_pfad:
        try:
            from backend.core.sync.tonklasse import klassifiziere_datei
            kanaele = _anzahl_audio_kanaele(video_pfad)
            if tonbefund is not None and kanaele > 0:
                pass  # vom Aufrufer vorab klassifiziert (vor Proxy/Waveform)
            if kanaele == 0:
                _update_job(job_id, "laeuft", 10, "Video ohne Audiospur — keine Transkription.", schritt="audio",
                            schritt_daten={"quelle": "Kamera-Ton", "klasse": "keine_spur", "skipped": True,
                                           "warnung": "Video hat keine Audiospur"})
                return None
            if tonbefund is None:
                tonbefund = klassifiziere_datei(video_pfad, kanaele, ffmpeg_bin=FFMPEG_BIN)
            if not tonbefund.hat_nutzton:
                logger.info(f"Kein Nutzton in {video_pfad}: {tonbefund.zusammenfassung}")
                _update_job(job_id, "laeuft", 10, "Kamera-Ton enthält keinen Nutzton (Stille/Timecode/Rauschen) — Transkription übersprungen.",
                            schritt="audio",
                            schritt_daten={"quelle": "Kamera-Ton", "klasse": "kein_nutzton", "skipped": True,
                                           "kanaele": [{"kanal": k.kanal, "klasse": k.klasse, "detail": k.detail} for k in tonbefund.kanaele],
                                           "warnung": tonbefund.warnungen[0] if tonbefund.warnungen else tonbefund.zusammenfassung})
                return None
            # Nur Nutzton-Kanäle mischen (LTC/Stille draußen lassen).
            if len(tonbefund.nutzton_kanaele) < kanaele:
                expr = "+".join(f"c{k}" for k in tonbefund.nutzton_kanaele)
                filter_args = ["-af", f"pan=mono|c0={expr}"]
        except Exception as e:  # Klassifikation darf die Pipeline nie stoppen
            logger.warning(f"Ton-Klassifikation fehlgeschlagen, nutze Downmix: {e}")

    # Pegel-Normalisierung NUR für die Whisper-Kopie (leise Angel/Lavalier-Spuren, −50 dBFS und weniger,
    # würden sonst als „stumm“ gelten oder schlecht erkannt): dynaudnorm hebt sanft an, ohne zu clippen.
    if filter_args and filter_args[0] == "-af":
        filter_args = ["-af", filter_args[1] + ",dynaudnorm=g=11:f=250:p=0.9"]
    else:
        filter_args = [*filter_args, "-af", "dynaudnorm=g=11:f=250:p=0.9"]
    cmd = [
        FFMPEG_BIN, "-y",
        "-i", quelle,
        "-vn",                          # Kein Video
        *filter_args,                   # Mono (nur Nutzton-Kanäle) + Normalisierung
        "-acodec", "pcm_s16le",         # PCM 16-bit
        "-ar", str(AUDIO_SAMPLE_RATE),  # 16kHz
        str(audio_pfad),
    ]

    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=300,
        )
        if result.returncode != 0:
            logger.error(f"FFmpeg Audio-Fehler: {result.stderr}")
            return None

        audio_size_kb = round(audio_pfad.stat().st_size / 1024, 1)
        daten: dict = {
            "size_kb": audio_size_kb,
            "sample_rate": AUDIO_SAMPLE_RATE,
            "channels": 1,
            "format": "WAV PCM 16-bit",
        }
        if ton_pfad and ton:
            daten.update({
                "quelle": "verknüpftes WAV",
                "audio_dateiname": ton.get("audio_dateiname"),
                "kanal": ton.get("kanal"),
                "offset_s": ton.get("offset_s"),
                "methode": ton.get("methode"),
                "konfidenz": ton.get("konfidenz"),
            })
            nachricht = f"Audio aus verknüpftem WAV ({ton.get('audio_dateiname')}, Kanal {ton.get('kanal')}, Offset {ton.get('offset_s'):+.3f} s)."
        else:
            daten.update({"quelle": "Kamera-Ton", "warnung": "Transkription auf Kamera-Ton — kein verknüpftes Audio"})
            if tonbefund is not None:
                daten["klasse"] = "nutzton"
                daten["nutzton_kanaele"] = tonbefund.nutzton_kanaele
                daten["kanaele"] = [{"kanal": k.kanal, "klasse": k.klasse, "detail": k.detail} for k in tonbefund.kanaele]
                if tonbefund.ltc_kanaele:
                    daten["warnung"] += f" — LTC auf Kanal {', '.join(map(str, tonbefund.ltc_kanaele))} ausgeschlossen"
            nachricht = "Audio aus Kamera-Spur extrahiert (kein verknüpftes WAV — Transkription auf Kamera-Ton)."
        _update_job(job_id, "laeuft", 10, nachricht, schritt="audio", schritt_daten=daten)
        return str(audio_pfad)

    except subprocess.TimeoutExpired:
        logger.error("FFmpeg Audio-Extraktion: Timeout")
        return None


# ═══════════════════════════════════════════════════════════
# SCHRITT 2: Transkription (mlx-whisper)
# ═══════════════════════════════════════════════════════════

# Bekannte Whisper-Halluzinationen bei stiller / sehr leiser Audiospur.
# Whisper neigt dazu, bei Stille trotzdem etwas zu produzieren — meist aus
# seinem Trainingsset gelernte Pseudo-Untertitel-Phrasen. Diese filtern wir
# raus, sonst landet "Vielen Dank" als Pseudo-Dialog in der DB und
# verwirrt den Chat-Assistenten und die Dialog-Treue-Metrik.
WHISPER_HALLUCINATIONS = {
    "vielen dank.", "vielen dank", "danke schön.", "danke schön",
    "danke.", "danke", "vielen dank fürs zuschauen.",
    "thank you.", "thank you", "thanks for watching.",
    "[musik]", "[music]", "[applaus]", "[applause]",
    "musik", "musik.", "musik musik", "musik musik musik",
    "music", "music.", "music music", "music music music",
    "musique", "musique.", "♪", "♫", "♪♪", "mahalo.",
    "untertitel der amara.org-community", "untertitel von stephanie geiges",
    "untertitelung des zdf", "untertitelung im auftrag des zdf",
    "untertitel im auftrag des zdf für funk, 2017",
    "untertitelung aufgrund der amara.org-community",
    "sf produktion ", "sf produktion",
    "soundtrack", "[geräusche]", "[noise]", "geräusche",
}

# Fassung OHNE Satzzeichen, verwendet nach der Normalisierung durch _ist_halluzination.
# Häufige Fälle: kurze Einzelwörter, bekannte Fehlerzeugungen von Whisper.
WHISPER_HALLUCINATIONS_CLEAN = {
    # Danke variants
    "danke", "danke schön", "vielen dank", "vielen dank fürs zuschauen",
    # Thank you
    "thank you", "thanks", "thanks for watching",
    # Bracketed noise markers
    "musik", "music", "musique", "applaus", "applause", "geräusche", "noise",
    "soundtrack", "mahalo",
    # Kurze Wiederholungen
    "musik musik", "musik musik musik", "music music", "music music music",
    # Symboles musique
    "♪", "♫", "♪♪",
    # Subtitle credits
    "untertitel der amara org community",
    "untertitel von stephanie geiges",
    "untertitelung des zdf",
    "untertitelung im auftrag des zdf",
    "untertitel im auftrag des zdf für funk 2017",
    "untertitelung aufgrund der amara org community",
    "sf produktion",
    # Mehrdeutige Einzelwörter, die Whisper auf Stille erzeugt
    "ja", "nein", "okay", "ok", "so", "you", "yeah", "yes", "no",
    "uh", "ah", "hm", "hmm", "mm", "um", "eh",
    "amen", "goodbye", "farewell", "bye",
}


def _json_sauber(obj):
    """Rekursiv: float NaN/±Inf → None (JSON-fähig), Rest unverändert."""
    import math
    if isinstance(obj, float):
        return None if (math.isnan(obj) or math.isinf(obj)) else obj
    if isinstance(obj, dict):
        return {k: _json_sauber(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_json_sauber(v) for v in obj]
    return obj


def _ist_repetierte_halluzination(text: str) -> bool:
    """
    Erkennt repetitive Wort-Halluzinationen wie 'Musik Musik Musik Musik' oder
    'Untertitel Untertitel'. Whisper neigt dazu, ein einzelnes Token zu
    wiederholen, wenn es nichts versteht.
    """
    woerter = text.strip().lower().replace(".", "").replace(",", "").split()
    if len(woerter) < 2:
        return False
    # Wenn alle Wörter identisch sind → Halluzination
    if len(set(woerter)) == 1 and len(woerter) >= 2:
        return True
    # Wenn nur 1-2 unterschiedliche Tokens für ≥ 4 Wörter → Halluzination
    if len(woerter) >= 4 and len(set(woerter)) <= 2:
        return True
    return False


def _ist_audio_stille(audio_pfad: str, rms_schwelle: float = 0.005) -> bool:
    """
    Prüft, ob die Audiospur faktisch stumm ist. RMS-Schwelle 0.005 entspricht
    -46 dBFS — alles darunter ist nicht mehr sinnvoll transkribierbar.
    """
    try:
        import librosa
        import numpy as np
        y, _ = librosa.load(audio_pfad, sr=16000, mono=True, duration=120.0)
        if len(y) == 0:
            return True
        rms = float(np.sqrt(np.mean(y ** 2)))
        peak = float(np.abs(y).max())
        logger.info(f"Audio-Pegel: RMS={rms:.4f} ({20 * np.log10(rms + 1e-9):.1f} dBFS), Peak={peak:.4f}")
        return rms < rms_schwelle
    except Exception as exc:
        logger.warning(f"Stille-Erkennung fehlgeschlagen: {exc} — fortfahren mit Whisper")
        return False


def _ist_halluzination(text: str) -> bool:
    """Prüft, ob das Segment einer bekannten Whisper-Stille-Halluzination entspricht."""
    import re as _re
    norm = text.strip().lower()
    if len(norm) <= 2:
        return True
    # Vereinheitlicht die Zeichensetzung vor dem Vergleich: "Danke!" == "danke." == "danke ?"
    norm_clean = _re.sub(r"[.,!?;:…]+", "", norm).strip()
    if norm_clean in WHISPER_HALLUCINATIONS_CLEAN:
        return True
    if norm in WHISPER_HALLUCINATIONS:
        return True
    # Auch sehr kurze Sätze ohne Inhalt (z.B. nur Satzzeichen)
    if len(norm_clean) <= 2:
        return True
    # Repetitive Token-Halluzination (z.B. "Musik Musik Musik")
    if _ist_repetierte_halluzination(text):
        return True
    # Zeichen-Stottern ohne Leerzeichen („ぜぜぜぜぜぜ“, „aaaaaaaa“, „!!!!!!!!“)
    if _re.search(r"(.)\1{7,}", norm):
        return True
    # Fremdes Schriftsystem in einer lateinisch geschriebenen Zielsprache (Whisper-Kipp bei Stille/Rauschen):
    # überwiegend CJK/Kana/Hangul/Kyrillisch/Arabisch → Halluzination.
    try:
        from backend.core import einstellungen as _E
        sprache = (_E.transkription().get("sprache") or "de").lower()
    except Exception:  # noqa: BLE001
        sprache = "de"
    if sprache in ("de", "en", "fr", "es", "it", "pt", "nl"):
        fremd = len(_re.findall(r"[\u3040-\u30ff\u3400-\u9fff\uac00-\ud7af\u0400-\u04ff\u0600-\u06ff]", norm))
        if fremd and fremd >= max(3, len(norm_clean) // 3):
            return True
    return False


def _normalize_llava(text: str) -> str:
    """
    Normalisiert LLaVA-Output zu einer kompakten, lesbaren Beschreibung.

    Behandelt drei Formate, die LLaVA häufig produziert:
      (a) Eine einzelne Phrase: "Bild. Mann mit Gitarre auf Bühne." → behalten
      (b) Strukturierte Bullet-Liste:
            Bild 1: Plan
            * Person: A man playing a guitar
            * Framing: Close-up
            * Setting: ...
          → fusioniert zu einer fließenden Komma-getrennten Phrase
      (c) Sehr kurze Ausgabe wie nur "Bild" → behalten als Hinweis "(Beschreibung
          schwach)" mit Original-Text

    Zweck: vermeidet, dass `text.split(".")[0]` die ganze Struktur wegwirft.
    """
    import re
    t = text.strip()
    if not t:
        return ""
    # "Bild N: Plan." / "Bild 1: Plan" o.ä. Präfix entfernen (inkl. Punkt danach)
    t = re.sub(r'^Bild\s*\d*\s*[:.]?\s*(Plan)?\s*[.:]?\s*', '', t, flags=re.IGNORECASE)
    t = re.sub(r'^Plan\s*[:.]?\s*', '', t, flags=re.IGNORECASE)
    # Bullet-Marker AM ANFANG: "* Field: Value" → "Value"
    t = re.sub(r'^[*\-]\s*\w+:\s*', '', t)
    # Bullet-Marker MITTENDRIN (nach Newline): "\n* Field: Value" → ", Value"
    t = re.sub(r'[\n\r]+\s*[*\-]\s*\w+:\s*', ', ', t)
    # Reste von Bullets / Doppel-Newlines reinigen
    t = re.sub(r'[\n\r]+', ' ', t)
    t = re.sub(r'\s{2,}', ' ', t).strip(' ,;:')
    # Wenn nach Bereinigung gar nichts mehr da ist → Fallback-Marker
    if len(t) < 8:
        return f"(LLaVA-Antwort schwach: '{text[:40]}')"
    # Auf maximal ~360 Zeichen kürzen, immer am letzten Satzende (kein
    # Mid-Sentence-Cut). Vorher 240 = oft "die Person spielt eine…"
    # abgeschnitten. Jetzt 360 + Suche nach letztem Punkt im Fenster.
    if len(t) > 360:
        # Suche nach letztem Satzende ". " im erweiterten Fenster
        cut_at = t[:360].rfind(". ")
        if cut_at > 120:
            t = t[:cut_at + 1]
        else:
            # Kein gutes Satzende gefunden — suche nach "; " oder ", " als Fallback
            for sep in ["; ", ", "]:
                alt = t[:360].rfind(sep)
                if alt > 120:
                    t = t[:alt] + "."
                    break
            else:
                t = t[:357] + "…"
    # Stelle sicher, dass der Text mit einem Satzzeichen endet
    if t and t[-1] not in ".!?…":
        t = t + "."
    return t


def _vad_segmente(audio_pfad: str, rand_s: float = 0.4, luecke_s: float = 1.5) -> list[tuple[float, float]] | None:
    """Sprachregionen (Silero VAD) im 16-kHz-Mono-WAV; None, wenn VAD nicht verfügbar."""
    try:
        import numpy as np
        from backend.core.sync.tonklasse import _vad
        m = _vad()
        if m is None:
            return None
        import torch
        from silero_vad import get_speech_timestamps
        raw = subprocess.run([FFMPEG_BIN, "-v", "error", "-i", audio_pfad, "-ac", "1", "-ar", "16000", "-f", "f32le", "-"],
                             capture_output=True, timeout=300).stdout
        x = np.frombuffer(raw, dtype=np.float32).copy()
        dauer = len(x) / 16000.0
        ts = get_speech_timestamps(torch.from_numpy(x), m, sampling_rate=16000, return_seconds=True,
                                   min_speech_duration_ms=150)
        segs: list[list[float]] = []
        for t in ts:
            a, e = max(0.0, t["start"] - rand_s), min(dauer, t["end"] + rand_s)
            if segs and a - segs[-1][1] < luecke_s:
                segs[-1][1] = e
            else:
                segs.append([a, e])
        return [(round(a, 2), round(e, 2)) for a, e in segs]
    except Exception as e:  # noqa: BLE001 — VAD darf die Transkription nie verhindern
        logger.warning(f"VAD nicht verfügbar/fehlgeschlagen: {e}")
        return None


def schritt_transkription(audio_pfad: str, job_id: str) -> dict | None:
    """Transkribiert Audio mit mlx-whisper (Apple Silicon optimiert).
    Mit Stille-Vorprüfung + Halluzinations-Filter, sodass stille Clips
    keinen erfundenen Pseudo-Dialog ("Vielen Dank") produzieren."""
    _update_job(job_id, "laeuft", 15, "Transkription läuft (mlx-whisper)...", schritt="transkription")

    # VAD-Vorprüfung (Silero, lokal): Whisper nur auf erkannte Sprachregionen loslassen.
    # Ohne das transkribiert Whisper 80 s Quasi-Stille zu „Ich helang euch so! ×5“.
    # Der VAD hat Vorrang vor dem RMS-Stille-Check: leise, aber echte Sprache (Angel weit weg) zählt.
    vad_segmente = _vad_segmente(audio_pfad)
    vad_sekunden = sum(e - a for a, e in vad_segmente) if vad_segmente is not None else None
    if vad_segmente is None and _ist_audio_stille(audio_pfad):
        logger.info("Audiospur ist faktisch stumm — Whisper übersprungen, kein Pseudo-Dialog")
        _update_job(
            job_id, "laeuft", 30,
            "Stumme Audiospur erkannt — Transkription übersprungen.",
            schritt="transkription",
            schritt_daten={"segmente": 0, "woerter": 0, "sprache": "—", "preview": "(stumm)", "modell": "skipped"},
        )
        return {"text": "", "sprache": "de", "segmente": [], "uebersprungen": True}
    if vad_segmente is not None and vad_sekunden < 0.5:
        logger.info("VAD: keine Sprache in der Audiospur — Whisper übersprungen")
        _update_job(
            job_id, "laeuft", 30,
            "Keine Sprache erkannt (VAD) — Transkription übersprungen.",
            schritt="transkription",
            schritt_daten={"segmente": 0, "woerter": 0, "sprache": "—", "preview": "(keine Sprache)",
                           "modell": "skipped", "vad_sprache_s": round(vad_sekunden, 1)},
        )
        return {"text": "", "sprache": "de", "segmente": [], "uebersprungen": True}

    try:
        import mlx_whisper

        # language=None bedeutet automatische Erkennung. Behebt den Fall, dass
        # Englisches Material wurde mit erzwungenem language="de" nicht transkribiert.
        opts: dict = {}
        if vad_segmente:
            # Nur Sprachregionen (±0,4 s, Lücken < 1,5 s zusammengezogen); Stille zwischen
            # den Regionen sieht Whisper gar nicht erst.
            opts["clip_timestamps"] = [t for a, e in vad_segmente for t in (a, e)]
            opts["hallucination_silence_threshold"] = 2.0
        # Projekt-Einstellungen: feste Sprache (statt Auto-Erkennung je Datei — die kippte auf kurzen
        # Sätzen ins Englische/Spanische), Glossar als initial_prompt (Namen/Begriffe), Modellqualität.
        from backend.core import einstellungen as E
        te = E.transkription()
        sprache = None if (te.get("sprache") or "auto") == "auto" else te["sprache"]
        prompt = E.initial_prompt()
        if prompt:
            opts["initial_prompt"] = prompt
        result = mlx_whisper.transcribe(
            audio_pfad,
            path_or_hf_repo=E.whisper_repo(),
            language=sprache,
            word_timestamps=True,
            **opts,
        )

        transkription = {
            "text": result.get("text", ""),
            "sprache": result.get("language", "de"),
            "segmente": [],
        }

        def _aufbereiten(rohsegmente) -> tuple[list[dict], int]:
            """Halluzinations-Filter + Glossar + Format — für Haupt- UND Rettungspass identisch."""
            kept: list[dict] = []
            entfernt = 0
            for seg in rohsegmente:
                seg_text = seg["text"].strip()
                if _ist_halluzination(seg_text):
                    entfernt += 1
                    continue
                # Glossar-Schreibweise deterministisch nachziehen („Juri“ → „Yuri“) — Wort für Wort, Satzzeichen bleiben.
                if te.get("glossar"):
                    for w in seg.get("words", []):
                        w["word"] = " " + E.glossar_angleichen(w["word"].strip()) if w.get("word", "").startswith(" ") else E.glossar_angleichen(w.get("word", ""))
                    seg_text = " ".join(E.glossar_angleichen(tok) for tok in seg_text.split())
                kept.append({
                    "start": round(seg["start"], 3),
                    "end": round(seg["end"], 3),
                    "text": seg_text,
                    "woerter": [
                        {
                            "wort": w["word"].strip(),
                            "start": round(w["start"], 3),
                            "end": round(w["end"], 3),
                            "p": round(float(w.get("probability", 1.0)), 3),   # Wort-Konfidenz (Whisper)
                        }
                        for w in seg.get("words", [])
                    ],
                    "avg_logprob": round(float(seg.get("avg_logprob", 0.0)), 3),
                    "no_speech_prob": round(float(seg.get("no_speech_prob", 0.0)), 3),
                })
            return kept, entfernt

        transkription["segmente"], halluzinationen_entfernt = _aufbereiten(result.get("segments", []))

        # ─── Rettungspass: VAD-blinde Sprache nachholen ─────────────────────────
        # Silero-VAD übersieht distanzierte/hallige Sprache (Totale, Rufe quer durch
        # den Raum — Befund Szene 4.1: 112-s-Take, VAD fand nur den Slate → Whisper
        # transkribierte per clip_timestamps NUR den Slate, die gespielten Repliken
        # („Scheiße, Mann!“ …) fehlten komplett und die Beats blieben leer).
        # Deshalb: war die VAD-Abdeckung verdächtig klein, läuft Whisper einmal
        # ÜBER DIE GANZE DATEI. Aus dem Volllauf übernehmen wir nur Segmente, die
        # (a) keine Pass-1-Region doppeln, (b) den Halluzinations-Filter überleben
        # und (c) eine mittlere Wort-Konfidenz ≥ 0.35 haben (fail-closed gegen die
        # Stille-Halluzinationen, deretwegen der VAD überhaupt eingeführt wurde).
        rettung_segmente = 0
        try:
            audio_dauer = _audio_dauer_s(audio_pfad)
        except Exception:  # noqa: BLE001
            audio_dauer = 0.0
        if (vad_segmente is not None and audio_dauer > 20.0
                and (vad_sekunden or 0.0) < max(10.0, 0.15 * audio_dauer)):
            logger.info(
                f"VAD-Abdeckung niedrig ({vad_sekunden:.1f} s von {audio_dauer:.1f} s) — Whisper-Rettungspass über die ganze Datei")
            opts_voll = {k: v for k, v in opts.items() if k not in ("clip_timestamps", "hallucination_silence_threshold")}
            result_voll = mlx_whisper.transcribe(
                audio_pfad,
                path_or_hf_repo=E.whisper_repo(),
                language=sprache,
                word_timestamps=True,
                **opts_voll,
            )
            voll_kept, voll_entfernt = _aufbereiten(result_voll.get("segments", []))
            halluzinationen_entfernt += voll_entfernt
            vorhanden = [(s["start"], s["end"]) for s in transkription["segmente"]]
            for seg in voll_kept:
                if any(min(seg["end"], e) - max(seg["start"], a) > 0.2 for a, e in vorhanden):
                    continue  # Region hat Pass 1 schon abgedeckt
                ws = seg.get("woerter") or []
                p_mittel = sum(w.get("p") or 0.0 for w in ws) / len(ws) if ws else 0.0
                if p_mittel < 0.35:
                    continue  # zu unsicher → mutmaßliche Stille-Halluzination
                seg["rettungspass"] = True
                transkription["segmente"].append(seg)
                rettung_segmente += 1
            transkription["segmente"].sort(key=lambda s: s["start"])
            if rettung_segmente:
                logger.info(f"Rettungspass: {rettung_segmente} zusätzliche Segmente übernommen")

        # Gesamttext immer aus den behaltenen Segmenten neu aufbauen
        if halluzinationen_entfernt and not transkription["segmente"]:
            transkription["text"] = ""
            logger.info(f"Whisper hat nur Halluzinationen produziert — alle {halluzinationen_entfernt} entfernt")
        elif halluzinationen_entfernt or rettung_segmente:
            if halluzinationen_entfernt:
                logger.info(f"{halluzinationen_entfernt} Whisper-Halluzinationen herausgefiltert")
            transkription["text"] = " ".join(s["text"] for s in transkription["segmente"])

        # NaN/Inf aus Whisper (avg_logprob/no_speech_prob/Wort-p bei large-v3 beobachtet) → None,
        # sonst wirft Postgres beim JSON-Insert „Token NaN is invalid“ und die ganze Analyse scheitert.
        transkription["segmente"] = _json_sauber(transkription["segmente"])

        wort_count = sum(len(seg.get("woerter", [])) for seg in transkription["segmente"])
        preview = (transkription.get("text") or "").strip().replace("\n", " ")
        if len(preview) > 80:
            preview = preview[:77] + "…"
        _update_job(
            job_id, "laeuft", 30,
            f"Transkription fertig — {len(transkription['segmente'])} Segmente.",
            schritt="transkription",
            schritt_daten={
                "vad_sprache_s": round(vad_sekunden, 1) if vad_sekunden is not None else None,
                "vad_regionen": len(vad_segmente) if vad_segmente else 0,
                "rettungspass_segmente": rettung_segmente,
                "segmente": len(transkription["segmente"]),
                "woerter": wort_count,
                "sprache": transkription.get("sprache", "?"),
                "preview": preview or "(keine Sprache erkannt)",
                "modell": E.whisper_repo().rsplit("/", 1)[-1],
            },
        )
        return transkription

    except ImportError:
        logger.warning("mlx-whisper nicht installiert — Transkription übersprungen.")
        _update_job(
            job_id, "laeuft", 30, "mlx-whisper nicht verfügbar, übersprungen.",
            schritt="transkription",
            schritt_daten={"skipped": True, "reason": "mlx-whisper nicht installiert"},
        )
        return {"text": "", "sprache": "de", "segmente": []}

    except Exception as e:
        logger.error(f"Transkription fehlgeschlagen: {e}")
        _update_job(
            job_id, "laeuft", 30, f"Transkriptionsfehler: {e}",
            schritt="transkription",
            schritt_daten={"skipped": True, "reason": str(e)[:120]},
        )
        return {"text": "", "sprache": "de", "segmente": []}


# ═══════════════════════════════════════════════════════════
# SCHRITT 3: Szenen erkennen (PySceneDetect)
# ═══════════════════════════════════════════════════════════

def schritt_szenen_erkennen(video_pfad: str, clip_id: str, job_id: str) -> list[dict]:
    """Erkennt Szenenwechsel mit PySceneDetect ContentDetector."""
    _update_job(job_id, "laeuft", 35, "Szenenerkennung läuft (PySceneDetect)...", schritt="szenenerkennung")

    try:
        from scenedetect import open_video, SceneManager
        from scenedetect.detectors import ContentDetector

        video = open_video(video_pfad)
        scene_manager = SceneManager()
        scene_manager.add_detector(ContentDetector(threshold=SCENE_THRESHOLD))
        scene_manager.detect_scenes(video)

        scene_list = scene_manager.get_scene_list()

        szenen = []
        thumbnails_dir = TEMP_DIR / f"thumbs_{clip_id}"
        thumbnails_dir.mkdir(exist_ok=True)

        for i, (start, end) in enumerate(scene_list):
            start_sek = start.get_seconds()
            end_sek = end.get_seconds()
            mitte_sek = (start_sek + end_sek) / 2

            # Thumbnail per FFmpeg extrahieren
            thumb_pfad = thumbnails_dir / f"szene_{i:03d}.jpg"
            thumb_cmd = [
                FFMPEG_BIN, "-y",
                "-ss", str(mitte_sek),
                "-i", video_pfad,
                "-frames:v", "1",
                "-q:v", "3",
                "-vf", "scale=320:-1",
                str(thumb_pfad),
            ]
            subprocess.run(thumb_cmd, capture_output=True, timeout=30)

            szenen.append({
                "szenen_nr": i + 1,
                "start_zeit": round(start_sek, 3),
                "end_zeit": round(end_sek, 3),
                "dauer": round(end_sek - start_sek, 3),
                "thumbnail_frame": int(mitte_sek * 24),  # ca. Frame
                "thumbnail_pfad": str(thumb_pfad) if thumb_pfad.exists() else None,
            })

        # Wenn keine Szenen erkannt → ganzes Video als eine Szene
        if not szenen:
            info = _get_video_info(video_pfad)
            # Thumbnail aus der Videomitte extrahieren — sonst hätte die
            # Einzel-Szene kein Vorschaubild und die LLaVA-Beschreibung
            # würde übersprungen (Fallback auf LLaMA3 ohne Bildbezug).
            mitte_sek = info["dauer"] / 2
            thumb_pfad = thumbnails_dir / "szene_000.jpg"
            subprocess.run(
                [FFMPEG_BIN, "-y", "-ss", str(mitte_sek), "-i", video_pfad,
                 "-frames:v", "1", "-q:v", "3", "-vf", "scale=320:-1",
                 str(thumb_pfad)],
                capture_output=True, timeout=30,
            )
            szenen.append({
                "szenen_nr": 1,
                "start_zeit": 0.0,
                "end_zeit": info["dauer"],
                "dauer": info["dauer"],
                "thumbnail_frame": int(mitte_sek * 24),
                "thumbnail_pfad": str(thumb_pfad) if thumb_pfad.exists() else None,
            })

        durations = [s["dauer"] for s in szenen]
        _update_job(
            job_id, "laeuft", 50, f"{len(szenen)} Szenen erkannt.",
            schritt="szenenerkennung",
            schritt_daten={
                "szenen": len(szenen),
                "algorithmus": "PySceneDetect ContentDetector (HSV)",
                "threshold": SCENE_THRESHOLD,
                "min_dauer_s": round(min(durations), 2) if durations else None,
                "max_dauer_s": round(max(durations), 2) if durations else None,
                "avg_dauer_s": round(sum(durations) / len(durations), 2) if durations else None,
            },
        )
        return szenen

    except ImportError:
        logger.warning("PySceneDetect nicht installiert — Video wird in Segmente aufgeteilt.")
        info = _get_video_info(video_pfad)
        total = info["dauer"]
        target_chunk = 4.0  # ~4s pro Segment für kinematischen Schnitt
        n = max(1, min(8, round(total / target_chunk)))
        chunk = total / n

        thumbnails_dir = TEMP_DIR / f"thumbs_{clip_id}"
        thumbnails_dir.mkdir(exist_ok=True)

        szenen = []
        for i in range(n):
            t_start = round(i * chunk, 3)
            t_end   = round(min(total, (i + 1) * chunk), 3)
            mitte   = (t_start + t_end) / 2

            thumb_pfad = thumbnails_dir / f"szene_{i:03d}.jpg"
            subprocess.run(
                [FFMPEG_BIN, "-y", "-ss", str(mitte), "-i", video_pfad,
                 "-frames:v", "1", "-q:v", "3", "-vf", "scale=320:-1",
                 str(thumb_pfad)],
                capture_output=True, timeout=30,
            )
            szenen.append({
                "szenen_nr":      i + 1,
                "start_zeit":     t_start,
                "end_zeit":       t_end,
                "dauer":          round(t_end - t_start, 3),
                "thumbnail_frame": int(mitte * 24),
                "thumbnail_pfad": str(thumb_pfad) if thumb_pfad.exists() else None,
            })

        _update_job(
            job_id, "laeuft", 50, f"{len(szenen)} Segmente erstellt (PySceneDetect n. verf.).",
            schritt="szenenerkennung",
            schritt_daten={
                "szenen": len(szenen),
                "algorithmus": "Fallback — gleichmäßige Aufteilung",
                "threshold": None,
            },
        )
        return szenen


# ═══════════════════════════════════════════════════════════
# SCHRITT 3b: Visuelle Analyse (PIL — kein CLIP nötig)
# ═══════════════════════════════════════════════════════════

def _extract_frame(video_pfad: str, t_sek: float, size: tuple[int, int] = (64, 64)) -> "Image | None":
    """Extrahiert einen einzelnen Frame per FFmpeg und gibt PIL Image zurück."""
    import tempfile
    from PIL import Image as _Img
    tmp = Path(video_pfad).parent / f"_frame_{uuid.uuid4().hex}.jpg"
    try:
        r = subprocess.run(
            [FFMPEG_BIN, "-y", "-ss", str(max(0.0, t_sek)), "-i", video_pfad,
             "-frames:v", "1", "-q:v", "3", "-vf", f"scale={size[0]}:{size[1]}",
             str(tmp)],
            capture_output=True, timeout=15,
        )
        if r.returncode == 0 and tmp.exists():
            return _Img.open(str(tmp)).convert("RGB")
    except Exception:
        pass
    finally:
        tmp.unlink(missing_ok=True)
    return None


def _pixel_diff(img_a: "Image", img_b: "Image") -> float:
    """Mittlere absolute Pixeldifferenz zweier RGB-Bilder gleicher Größe (0–1)."""
    a = list(img_a.getdata())
    b = list(img_b.getdata())
    n = len(a)
    if n == 0:
        return 0.0
    total = sum(abs(a[i][c] - b[i][c]) for i in range(n) for c in range(3))
    return min(1.0, total / (n * 3 * 255))


def _blur_score_pil(img: "Image") -> float:
    """
    Schärfe-Score via Laplace-Approximation (reines PIL, kein OpenCV).

    Berechnet die Varianz der zweiten Ableitung der Luminanz:
    - Hohe Varianz → scharfes Bild → Score nahe 1.0
    - Niedrige Varianz → unscharf/blur → Score nahe 0.0

    Normalisierung: sigmoid-ähnliche Kurve auf Pixelblock-Ebene.
    """
    gray = list(img.resize((32, 32)).convert("L").getdata())
    w = 32
    laplace: list[float] = []
    for y in range(1, 31):
        for x in range(1, 31):
            idx = y * w + x
            val = (
                -4 * gray[idx]
                + gray[idx - 1] + gray[idx + 1]
                + gray[idx - w] + gray[idx + w]
            )
            laplace.append(val)
    if not laplace:
        return 0.5
    mean_l = sum(laplace) / len(laplace)
    variance = sum((v - mean_l) ** 2 for v in laplace) / len(laplace)
    # Typische scharfe Bilder: variance 200–800 → normalisieren auf 0–1
    return round(min(1.0, variance / 600.0), 3)


def schritt_analyse_visuelle(video_pfad: str, szenen: list[dict], job_id: str) -> list[dict]:
    """
    Visuelle Multi-Frame-Analyse (v4 — 3 Frames + Blur).

    Pro Szene werden 3 Frames extrahiert (25% / 50% / 75% der Szene):
      - luminosite   (0–1)  : mittlere Helligkeit (Frame 50%)
      - temperature  (str)  : Farbtemperatur warm|neutral|kalt (Frame 50%)
      - kontrast     (0–1)  : Std-Dev Luminanz (Frame 50%)
      - mouvement    (0–1)  : Echter temporal flow — mean(diff(F1,F2), diff(F2,F3))
      - schärfe      (0–1)  : Laplace-Varianz (Frame 50%) — Qualitätsfilter
      - qualitaet    (0–1)  : Schärfe × Belichtungsqualität
      - energie      (0–1)  : kontrast×0.40 + mouvement×0.35 + luminosite×0.15 + schärfe×0.10

    Nur PIL/Pillow — kein OpenCV nötig.
    """
    _update_job(job_id, "laeuft", 52, "Visuelle Multi-Frame-Analyse läuft (PIL v4)...", schritt="visuelle_analyse")

    fallback = {
        "luminosite": 0.5, "temperature": "neutral",
        "kontrast": 0.5, "mouvement": 0.5,
        "schaerfe": 0.5, "qualitaet": 0.5, "energie": 0.5,
    }

    try:
        from PIL import Image

        results = []

        for szene in szenen:
            meta = dict(fallback)
            start = szene["start_zeit"]
            end   = szene["end_zeit"]
            dauer = max(0.01, end - start)

            # ── 3 Frames extrahieren: 25% / 50% / 75% ────────
            t25 = start + dauer * 0.25
            t50 = start + dauer * 0.50
            t75 = start + dauer * 0.75

            f50 = _extract_frame(video_pfad, t50, (64, 64))
            f25 = _extract_frame(video_pfad, t25, (32, 32))
            f75 = _extract_frame(video_pfad, t75, (32, 32))

            # ── Frame 50%: Farb- und Kontrastanalyse ──────────
            if f50:
                try:
                    pixels = list(f50.getdata())
                    r_vals = [p[0] for p in pixels]
                    g_vals = [p[1] for p in pixels]
                    b_vals = [p[2] for p in pixels]
                    n_pix  = len(pixels)

                    # Helligkeit
                    lum = (sum(r_vals) + sum(g_vals) + sum(b_vals)) / (3 * n_pix * 255)
                    meta["luminosite"] = round(lum, 3)

                    # Farbtemperatur
                    avg_r = sum(r_vals) / n_pix
                    avg_b = sum(b_vals) / n_pix
                    rb = avg_r / (avg_b + 1.0)
                    meta["temperature"] = "warm" if rb > 1.25 else ("kalt" if rb < 0.8 else "neutral")

                    # Kontrast (std dev Luminanz)
                    lum_v = [0.299 * p[0] + 0.587 * p[1] + 0.114 * p[2] for p in pixels]
                    mean_l = sum(lum_v) / n_pix
                    std_l  = (sum((v - mean_l) ** 2 for v in lum_v) / n_pix) ** 0.5
                    meta["kontrast"] = round(min(1.0, std_l / 80.0), 3)

                    # Schärfe (Laplace)
                    meta["schaerfe"] = _blur_score_pil(f50)

                    # Belichtungsqualität: Strafe für Über-/Unterbelichtung
                    # Ideal: lum zwischen 0.25 und 0.75
                    expo_penalty = max(0.0, lum - 0.80) * 3 + max(0.0, 0.15 - lum) * 2
                    meta["qualitaet"] = round(min(1.0, meta["schaerfe"] * (1.0 - expo_penalty)), 3)

                except Exception:
                    pass

            # ── Echter Temporal Flow (3 frames) ──────────────
            # diff(F25→F50) + diff(F50→F75) → mittlere Bewegungsintensität
            motion_samples: list[float] = []

            if f25 and f50:
                f50_small = f50.resize((32, 32))
                motion_samples.append(_pixel_diff(f25, f50_small))

            if f50 and f75:
                f50_small2 = f50.resize((32, 32))
                motion_samples.append(_pixel_diff(f50_small2, f75))

            if motion_samples:
                # Gewichtung: letzte Differenz leicht stärker (mehr representativ für Szenen-Energie)
                if len(motion_samples) == 2:
                    raw_motion = motion_samples[0] * 0.45 + motion_samples[1] * 0.55
                else:
                    raw_motion = motion_samples[0]
                # Verstärken: echte Bewegung ist oft subtil im 32×32-Downscale
                meta["mouvement"] = round(min(1.0, raw_motion * 2.5), 3)

            # ── Energie (v4 — Qualitäts-gewichtet) ───────────
            # Qualität fließt leicht ein: unscharfe Szenen werden abgewertet
            meta["energie"] = round(
                meta["kontrast"]   * 0.40
                + meta["mouvement"] * 0.35
                + meta["luminosite"] * 0.15
                + meta["schaerfe"]   * 0.10,
                3,
            )

            results.append(meta)

        energies = [r.get("energie", 0.0) for r in results]
        _update_job(
            job_id, "laeuft", 54, f"{len(results)} Szenen visuell analysiert (3-Frame).",
            schritt="visuelle_analyse",
            schritt_daten={
                "szenen_analysiert": len(results),
                "frames_pro_szene": 3,
                "metriken": ["helligkeit", "kontrast", "bewegung", "schärfe", "energie"],
                "energie_min": round(min(energies), 3) if energies else None,
                "energie_max": round(max(energies), 3) if energies else None,
                "energie_avg": round(sum(energies) / len(energies), 3) if energies else None,
            },
        )
        return results

    except ImportError:
        logger.warning("PIL nicht verfügbar — visuelle Analyse übersprungen.")
        _update_job(
            job_id, "laeuft", 54, "PIL nicht verfügbar.",
            schritt="visuelle_analyse",
            schritt_daten={"skipped": True, "reason": "PIL nicht installiert"},
        )
        return [dict(fallback) for _ in szenen]


# ═══════════════════════════════════════════════════════════
# SCHRITT 4: CLIP Embeddings (visuell)
# ═══════════════════════════════════════════════════════════

def schritt_clip_embeddings(video_pfad: str, szenen: list[dict], job_id: str) -> list[list[float]]:
    """Erzeugt CLIP-Embeddings für jede Szene — Mittelung mehrerer Frames.

    Statt nur des Mittel-Frames werden CLIP_FRAMES Frames gleichmäßig innerhalb
    der Szene abgetastet (z.B. 25/50/75 %) und ihre Embeddings gemittelt — robuster
    gegen unrepräsentative Einzelframes und den absoluten Szenenanfang (Filmklappe).
    Modell/Checkpoint kommen aus core/clip_encoder (identisch zur Textsuche).
    """
    _update_job(job_id, "laeuft", 55, "Visuelle Embeddings werden erstellt (CLIP)...", schritt="clip")

    try:
        from backend.core import clip_encoder
        from backend.core.config import CLIP_FRAMES, CLIP_MODEL, CLIP_PRETRAINED

        n_frames = max(1, int(CLIP_FRAMES))
        # gleichmäßige Fraktionen im offenen Intervall (0,1): 3 → 0.25 / 0.5 / 0.75
        fraktionen = [(k + 1) / (n_frames + 1) for k in range(n_frames)]
        dim = clip_encoder.embed_dim()

        embeddings: list[list[float]] = []
        total = len(szenen)

        for i, szene in enumerate(szenen):
            start = float(szene["start_zeit"])
            dauer = max(0.0, float(szene["end_zeit"]) - start)

            frame_pfade = []
            for fr in fraktionen:
                t = start + dauer * fr
                fp = TEMP_DIR / f"clip_frame_{uuid.uuid4().hex}.jpg"
                subprocess.run(
                    [FFMPEG_BIN, "-y", "-ss", str(t), "-i", video_pfad,
                     "-frames:v", "1", "-q:v", "2", str(fp)],
                    capture_output=True, timeout=30,
                )
                if fp.exists():
                    frame_pfade.append(fp)

            emb = clip_encoder.embed_images_mean(frame_pfade)
            for fp in frame_pfade:
                try:
                    fp.unlink()
                except OSError:
                    pass

            embeddings.append(emb.tolist() if emb is not None else [0.0] * dim)

            fortschritt = 55 + int((i + 1) / total * 20)
            _update_job(job_id, "laeuft", fortschritt, f"CLIP Embedding {i+1}/{total}...")

        nonzero = sum(1 for e in embeddings if any(v != 0.0 for v in e))
        _update_job(
            job_id, "laeuft", 75, f"{len(embeddings)} Embeddings erstellt.",
            schritt="clip",
            schritt_daten={
                "embeddings": len(embeddings),
                "embeddings_nonzero": nonzero,
                "dimension": dim,
                "frames_pro_szene": n_frames,
                "modell": f"{CLIP_MODEL} / {CLIP_PRETRAINED} (open_clip)",
                "device": clip_encoder.get_device(),
            },
        )
        return embeddings

    except ImportError:
        logger.warning("CLIP/torch nicht installiert — Embeddings übersprungen.")
        _update_job(
            job_id, "laeuft", 75, "CLIP nicht verfügbar, Embeddings übersprungen.",
            schritt="clip",
            schritt_daten={"skipped": True, "reason": "open_clip/torch nicht installiert"},
        )
        return [[] for _ in szenen]


# ═══════════════════════════════════════════════════════════
# SCHRITT 5: Szenen beschreiben (Ollama / LLaMA3)
# ═══════════════════════════════════════════════════════════

def schritt_diarization(audio_pfad: str, job_id: str) -> dict:
    """
    Sprechertrennung auf der herausgelösten Tonspur.

    Ohne pyannote oder HF-Token geschieht stillschweigend nichts, zurück kommt
    {"available": False, ...}, und die Strecke läuft ungestört weiter.
    """
    _update_job(job_id, "laeuft", 68, "Speaker diarization läuft…", schritt="diarization")
    try:
        from backend.core.diarize import diarize_audio
        # Projekt-Einstellung „max. Sprecher“ (z. B. 2 bei einer Zwei-Personen-Szene): pyannote zersplittert
        # emotionale/schreiende Sprache sonst gern in 4–5 „Sprecher“ (beobachtet auf dem Korpus).
        max_sp = None
        try:
            from backend.core import einstellungen as _E
            v = _E.projekt().get("max_sprecher")
            max_sp = int(v) if v not in (None, "", 0, "0") else None
        except Exception:  # noqa: BLE001
            max_sp = None
        result = diarize_audio(audio_pfad, max_speakers=max_sp)
    except Exception as e:
        logger.warning(f"Diarization module indisponible : {e}")
        result = {"available": False, "error": str(e), "segments": [], "speakers": [], "total_speakers": 0}

    if not result.get("available"):
        _update_job(
            job_id, "laeuft", 70,
            f"Diarization übersprungen ({result.get('error', 'no HF token')}).",
            schritt="diarization",
            schritt_daten={"skipped": True, "reason": result.get("error")},
        )
    else:
        _update_job(
            job_id, "laeuft", 70,
            f"Diarization: {result['total_speakers']} Sprecher, {len(result['segments'])} Segmente.",
            schritt="diarization",
            schritt_daten={
                "total_speakers": result["total_speakers"],
                "segments": len(result["segments"]),
            },
        )
    return result


def schritt_face_detection(szenen: list[dict], job_id: str) -> list[dict]:
    """
    Face detection + framing classification (Vague 1.3).

    Nutzt die Haar-Kaskade von OpenCV auf dem Hauptvorschaubild jeder Szene.
    Zurück kommt eine zu `szenen` passende Liste mit {face_count, framing, faces}.
    Framing ∈ {extreme_closeup, closeup, medium, wide_with_person, wide_no_person}.

    Rechenzeit: etwa fünf bis achtzig Millisekunden je Szene auf dem M4, beim ersten Aufruf 50 ms Anlauf.
    """
    _update_job(job_id, "laeuft", 78, "Face detection läuft…", schritt="face_detection")
    try:
        from backend.core.face_detect import detect_faces
    except Exception as e:
        logger.warning(f"face_detect indisponible: {e}")
        return [{"face_count": 0, "framing": None, "faces": None} for _ in szenen]

    results: list[dict] = []
    for szene in szenen:
        thumb = szene.get("thumbnail_pfad")
        if thumb and Path(thumb).exists():
            try:
                r = detect_faces(thumb)
            except Exception as e:
                logger.warning(f"face_detect scene {szene.get('szenen_nr')} failed: {e}")
                r = {"face_count": 0, "framing": None, "faces": None}
        else:
            r = {"face_count": 0, "framing": None, "faces": None}
        results.append(r)

    face_scenes = sum(1 for r in results if r.get("face_count", 0) > 0)
    _update_job(
        job_id, "laeuft", 79, f"Face detection: {face_scenes}/{len(szenen)} Szenen mit Personen.",
        schritt="face_detection",
        schritt_daten={"face_scenes": face_scenes, "total": len(szenen)},
    )
    return results


def schritt_szenen_beschreiben(
    szenen: list[dict],
    transkription: dict,
    job_id: str,
) -> list[str]:
    """
    Beschreibt jede Szene in einem Satz mittels einer GENRE-AGNOSTISCHEN
    Vision-Language-Pipeline.

    Algorithmus :
      1. Primärquelle = LLaVA (Bild-LLM) auf dem Thumbnail jeder Szene
         → faktische visuelle Beschreibung, kein Hallucination
         → funktioniert auf JEDEM Genre (Musik, Doku, Interview, Sport, …)
      2. Fallback = LLaMA3 textuelle Beschreibung aus Dialog (falls
         LLaVA nicht installiert ist oder das Thumbnail fehlt)

    Resultate werden direkt in szenen.beschreibung gespeichert und vom
    KI-Chat-Assistenten (api/chat.py) für seinen Katalog gelesen.
    """
    import base64

    _update_job(job_id, "laeuft", 80, "Szenen werden beschrieben (Moondream Vision)…", schritt="beschreibungen")

    try:
        import httpx

        beschreibungen: list[str] = []
        total = len(szenen)
        used_model = "moondream"
        llava_failures = 0

        # Moondream verlangt kurze, unmittelbare Eingaben. Verschachtelte Eingaben
        # Mehrfachbedingungen und ein vorgeschriebenes Format führen zu leeren oder erfundenen Antworten.
        # Auf einem Mac mini M4 geprüft: etwa 0,4 s je Beschreibung, sachliche englische Ausgabe.
        VISION_PROMPT = "Describe the scene in this image in one sentence."

        for i, szene in enumerate(szenen):
            beschreibung: str | None = None
            thumb_pfad = szene.get("thumbnail_pfad")

            # ─── 1. PRIMÄR: Moondream Vision, mit Wiederholung gegen erfundene Inhalte ──
            if thumb_pfad and Path(thumb_pfad).exists():
                try:
                    from backend.core.vision_describe import describe_image
                    text = describe_image(thumb_pfad, max_retries=2)
                    if text:
                        beschreibung = _normalize_llava(text)
                    else:
                        llava_failures += 1
                except Exception as e:
                    llava_failures += 1
                    logger.warning(f"Moondream fehlgeschlagen für Szene {i+1}: {e}")

            # ─── 2. FALLBACK : LLaMA3 textuell aus Dialog ────────────
            # Falls LLaVA fehlschlägt (z.B. fehlendes Thumbnail oder Modell-
            # Timeout), fallen wir auf LLaMA3 zurück. Wichtig: dieser Fallback
            # erzeugt KEINE echte Visualbeschreibung — LLaMA3 hat keine Bild-
            # Wahrnehmung. Er produziert vielmehr einen Dialog-basierten
            # Pseudo-Kontext, der für Material mit Sprache brauchbar ist.
            # Für stille Clips erzeugt er teilweise generische Phrasen —
            # das ist eine bekannte Limitation, in der Methodik dokumentiert.
            if not beschreibung:
                segment_text = ""
                for seg in transkription.get("segmente", []):
                    if (seg["start"] < szene["end_zeit"]
                            and seg["end"] > szene["start_zeit"]):
                        segment_text += seg["text"] + " "

                prompt = (
                    f"Beschreibe diese Szene in EINEM kurzen sachlichen Satz auf Deutsch.\n\n"
                    f"Szene {szene['szenen_nr']}: {szene['start_zeit']:.1f}s – {szene['end_zeit']:.1f}s "
                    f"(Dauer: {szene['dauer']:.1f}s)\n"
                )
                if segment_text.strip():
                    prompt += f"Dialog: \"{segment_text.strip()}\"\n"
                prompt += "\nBeschreibung (1 Satz):"

                try:
                    resp = httpx.post(
                        f"{OLLAMA_BASE_URL}/api/generate",
                        json={
                            "model": OLLAMA_MODEL,
                            "prompt": prompt,
                            "stream": False,
                            "options": {"temperature": 0.3, "num_predict": 80},
                        },
                        timeout=60.0,
                    )
                    resp.raise_for_status()
                    beschreibung = resp.json().get("response", "").strip()
                    if used_model == "llava:7b":
                        used_model = "llava:7b + llama3 (fallback)"
                except Exception as e:
                    logger.warning(f"LLaMA3 Fallback fehlgeschlagen für Szene {i+1}: {e}")
                    beschreibung = f"Szene {szene['szenen_nr']}: {szene['dauer']:.1f}s"

            beschreibungen.append(beschreibung or "")
            fortschritt = 80 + int((i + 1) / total * 15)
            _update_job(job_id, "laeuft", fortschritt, f"Beschreibung {i+1}/{total}…")

        preview = next((b for b in beschreibungen if b and not b.startswith("Szene ")), "")
        if len(preview) > 100:
            preview = preview[:97] + "…"
        _update_job(
            job_id, "laeuft", 95, f"{len(beschreibungen)} Szenen beschrieben.",
            schritt="beschreibungen",
            schritt_daten={
                "beschreibungen": len(beschreibungen),
                "modell": used_model,
                "provider": "Ollama (lokal)",
                "vision_basiert": llava_failures < total,
                "llava_failures": llava_failures,
                "preview": preview or "(keine Beschreibung)",
            },
        )
        return beschreibungen

    except ImportError:
        logger.warning("httpx nicht installiert — Beschreibungen übersprungen.")
        _update_job(
            job_id, "laeuft", 95, "Ollama nicht verfügbar, Beschreibungen übersprungen.",
            schritt="beschreibungen",
            schritt_daten={"skipped": True, "reason": "httpx nicht installiert"},
        )
        return [f"Szene {s['szenen_nr']}: {s['dauer']:.1f}s" for s in szenen]



# ─────────────────────────────────────────────────────────────
# SCHRITT 5+5b (neu): Bildanalyse mit Stichproben-Frames
# ─────────────────────────────────────────────────────────────

def _stichproben_fraktionen(dauer: float) -> list[float]:
    """Relative Zeitpunkte je Szene — adaptiv: ein Frame alle `BILD_INTERVALL_S` Sekunden (Config),
    mindestens `BILD_MIN_FRAMES`, höchstens `BILD_MAX_FRAMES`, gleichmäßig verteilt und nie am
    absoluten Rand (Klappe/Ausblende). Dailies sind meist EINE Szene über 1–4 Minuten — ein einzelner
    Mittel-Frame beschreibt den Take nicht; 3 feste Punkte auch nicht. Welche der Frames dann wirklich
    beschrieben werden, entscheidet die Ähnlichkeitsprüfung in `schritt_bildanalyse` (nur Wechsel)."""
    from backend.core.config import BILD_INTERVALL_S, BILD_MIN_FRAMES, BILD_MAX_FRAMES
    if dauer <= 0:
        return [0.5]
    n = int(round(dauer / max(1.0, BILD_INTERVALL_S)))
    n = max(BILD_MIN_FRAMES, min(BILD_MAX_FRAMES, n))
    if n == 1:
        return [0.5]
    # gleichmäßig in (0,1): n=2 → 0.25/0.75, n=3 → 0.167/0.5/0.833 …
    return [(k + 0.5) / n for k in range(n)]


def _frame_extrahieren(quelle: str, t: float, ziel: Path, breite: int = 896) -> bool:
    try:
        subprocess.run(
            [FFMPEG_BIN, "-y", "-loglevel", "error", "-ss", f"{max(0.0, t):.3f}", "-i", quelle,
             "-frames:v", "1", "-q:v", "2", "-vf", f"scale={breite}:-2", str(ziel)],
            capture_output=True, timeout=60,
        )
    except Exception as e:  # noqa: BLE001
        logger.warning(f"Frame-Extraktion fehlgeschlagen ({t:.1f}s): {e}")
        return False
    return ziel.exists() and ziel.stat().st_size > 0


def schritt_bildanalyse(szenen: list[dict], transkription: dict, job_id: str, clip_id: str,
                        bild_quelle: str, original_quelle: str | None = None) -> list[dict]:
    """Beschreibung + Personen + Framing je Szene aus MEHREREN Stichproben-Frames (896 px, aus dem Proxy).

    Pro Szene: 1–3 Frames (Anfang/Mitte/Ende) → je Frame
      • faktische Bildbeschreibung (llava:7b, Fallback moondream; „entspekuliert“, s. vision_describe)
      • Personenzahl per Bildmodell (Zahl-only-Prompt)
      • Gesichter (Haar frontal+profil, 896 px)
    Aggregation: `beschreibung` = Beschreibung des Mittel-Frames (bzw. die längste bei Gleichstand),
    `stichproben` = alle Frames mit Zeit/Beschreibung/Personen (für UI + Bericht),
    `personen` = Median der Bildmodell-Zählung, `face_count` = Max Haar, `framing` aus Gesichts-
    größe — ohne Gesicht, aber Personen ≥ 1 → „wide_with_person“ (Haar versagt in Totalen/Profil).
    Kein Bildmodell → Fallback wie bisher (LLaMA3 aus Dialog, ehrlich als nicht-visuell markiert).
    """
    from backend.core import vision_describe as V

    original_quelle = original_quelle or bild_quelle
    total = len(szenen)
    modell = V.vision_modell()
    _update_job(job_id, "laeuft", 78, f"Bildanalyse ({modell or 'kein Bildmodell'}) — Stichproben je Szene…", schritt="beschreibungen")

    try:
        from backend.core.face_detect import detect_faces
    except Exception as e:  # noqa: BLE001
        logger.warning(f"face_detect indisponible: {e}")
        detect_faces = None  # type: ignore[assignment]

    thumbs_dir = TEMP_DIR / f"thumbs_{clip_id}"
    thumbs_dir.mkdir(exist_ok=True)

    # CLIP-Ähnlichkeit zwischen aufeinanderfolgenden Frames: „gleiches Bild“ → nicht neu beschreiben.
    try:
        from backend.core import clip_encoder as _ce
        from backend.core.config import BILD_GLEICH_SCHWELLE as _gleich
    except Exception:  # noqa: BLE001
        _ce, _gleich = None, 2.0

    ergebnisse: list[dict] = []
    llava_failures = 0
    for i, szene in enumerate(szenen):
        start = float(szene["start_zeit"]); dauer = max(0.0, float(szene["end_zeit"]) - start)
        proben: list[dict] = []
        letzte_emb = None
        letzte_beschr: dict | None = None
        uebersprungen = 0
        for k, fr in enumerate(_stichproben_fraktionen(dauer)):
            t = start + dauer * fr
            fp = thumbs_dir / f"probe_{i:03d}_{k}.jpg"
            if not _frame_extrahieren(bild_quelle, t, fp):
                # Proxy defekt/zu kurz? → Original versuchen, bevor der Frame verloren geht.
                if bild_quelle == original_quelle or not _frame_extrahieren(original_quelle, t, fp):
                    continue
            probe: dict = {"t": round(t, 2), "datei": str(fp), "beschreibung": None, "personen": None,
                           "face_count": 0, "faces": [], "max_area_ratio": 0.0, "gleich_wie": None}
            gleich = False
            if _ce is not None:
                try:
                    emb = _ce.embed_image(fp)
                    if letzte_emb is not None and float((emb * letzte_emb).sum()) >= _gleich:
                        gleich = True
                    letzte_emb = emb
                except Exception as e:  # noqa: BLE001
                    logger.warning(f"CLIP-Ähnlichkeit Szene {i+1}/{k}: {e}")
            if modell and gleich and letzte_beschr is not None:
                # Bild unverändert gegenüber dem vorigen Frame → Beschreibung/Zählung übernehmen (kein Modellaufruf)
                probe["beschreibung"] = letzte_beschr["beschreibung"]; probe["personen"] = letzte_beschr["personen"]
                probe["gleich_wie"] = letzte_beschr["t"]; uebersprungen += 1
            elif modell:
                try:
                    txt = V.describe_image(fp, max_retries=1, model=modell)
                    probe["beschreibung"] = txt or None
                    if not txt:
                        llava_failures += 1
                    probe["personen"] = V.zaehle_personen(fp, model=modell)
                    letzte_beschr = probe
                except Exception as e:  # noqa: BLE001
                    llava_failures += 1
                    logger.warning(f"Bildmodell fehlgeschlagen Szene {i+1}/{k}: {e}")
            if detect_faces is not None:
                try:
                    r = detect_faces(fp)
                    probe["face_count"] = int(r.get("face_count", 0)); probe["faces"] = r.get("faces") or []
                    probe["max_area_ratio"] = float(r.get("max_area_ratio", 0.0) or 0.0)
                except Exception as e:  # noqa: BLE001
                    logger.warning(f"face_detect Szene {i+1}/{k}: {e}")
            proben.append(probe)

        # ── Aggregation ──
        beschr = [p["beschreibung"] for p in proben if p.get("beschreibung")]
        mitte = proben[len(proben) // 2]["beschreibung"] if proben else None
        beschreibung = mitte or (max(beschr, key=len) if beschr else None)
        zaehlungen = sorted(p["personen"] for p in proben if isinstance(p.get("personen"), int))
        personen = zaehlungen[len(zaehlungen) // 2] if zaehlungen else None
        face_count = max((p["face_count"] for p in proben), default=0)
        best = max(proben, key=lambda p: p["max_area_ratio"], default=None)
        if best and best["face_count"] > 0:
            from backend.core.face_detect import _classify_framing
            framing = _classify_framing(best["max_area_ratio"], best["face_count"])
        elif personen and personen > 0:
            framing = "wide_with_person"
        else:
            framing = "wide_no_person" if proben else None

        # ── Fallback ohne Bildmodell: LLaMA3 aus Dialog (nicht visuell!) ──
        if not beschreibung and not modell:
            beschreibung = _dialog_fallback_beschreibung(szene, transkription)

        ergebnisse.append({
            "beschreibung": beschreibung,
            "stichproben": [{"t": p["t"], "datei": p["datei"], "beschreibung": p["beschreibung"],
                             "personen": p["personen"], "face_count": p["face_count"], "gleich_wie": p.get("gleich_wie")} for p in proben],
            "uebersprungen_gleich": uebersprungen,
            "personen": personen,
            "face_count": face_count,
            "framing": framing,
            "faces": (best or {}).get("faces") if best else None,
            "modell": modell,
        })
        _update_job(job_id, "laeuft", 78 + int((i + 1) / max(1, total) * 17), f"Bildanalyse {i+1}/{total} ({len(proben)} Frames, {uebersprungen} unverändert)…")

    preview = next((e["beschreibung"] for e in ergebnisse if e.get("beschreibung")), "")
    if len(preview) > 100:
        preview = preview[:97] + "…"
    _update_job(
        job_id, "laeuft", 95, f"{len(ergebnisse)} Szenen beschrieben.", schritt="beschreibungen",
        schritt_daten={"beschreibungen": len(ergebnisse), "modell": modell or "llama3 (Dialog-Fallback)",
                       "provider": "Ollama (lokal)", "vision_basiert": modell is not None,
                       "llava_failures": llava_failures, "stichproben": sum(len(e["stichproben"]) for e in ergebnisse),
                       "unveraendert_uebernommen": sum(e.get("uebersprungen_gleich", 0) for e in ergebnisse),
                       "intervall_s": __import__("backend.core.config", fromlist=["BILD_INTERVALL_S"]).BILD_INTERVALL_S,
                       "preview": preview or "(keine Beschreibung)"},
    )
    face_scenes = sum(1 for e in ergebnisse if (e.get("personen") or 0) > 0 or e.get("face_count", 0) > 0)
    _update_job(job_id, "laeuft", 95, f"Personen in {face_scenes}/{total} Szenen.", schritt="face_detection",
                schritt_daten={"face_scenes": face_scenes, "total": total, "quelle": "llava-Zählung + Haar (frontal/profil)"})
    return ergebnisse


def _mit_bilddaten(analyse: dict | None, bild: dict | None) -> dict | None:
    """Stichproben + Personenzahl der Bildanalyse in `szenen.analyse_visuelle` (JSON) ablegen — keine neue Spalte."""
    if not bild:
        return analyse
    out = dict(analyse or {})
    if bild.get("stichproben") is not None:
        out["stichproben"] = bild["stichproben"]
    if bild.get("personen") is not None:
        out["personen"] = bild["personen"]
    return out


def _dialog_fallback_beschreibung(szene: dict, transkription: dict) -> str | None:
    """LLaMA3-Textfallback ohne Bild — nur wenn KEIN Bildmodell installiert ist. Kennzeichnet sich selbst."""
    try:
        import httpx
    except ImportError:
        return None
    segment_text = " ".join(seg["text"] for seg in transkription.get("segmente", [])
                            if seg["start"] < szene["end_zeit"] and seg["end"] > szene["start_zeit"]).strip()
    if not segment_text:
        return None
    prompt = (f"Fasse den folgenden Dialogausschnitt in EINEM sachlichen deutschen Satz zusammen. "
              f"Erfinde nichts, was nicht gesagt wird.\n\nDialog: \"{segment_text[:1500]}\"\n\nZusammenfassung (1 Satz):")
    try:
        resp = httpx.post(f"{OLLAMA_BASE_URL}/api/generate",
                          json={"model": OLLAMA_MODEL, "prompt": prompt, "stream": False,
                                "options": {"temperature": 0.0, "num_predict": 80}}, timeout=60.0)
        resp.raise_for_status()
        txt = resp.json().get("response", "").strip()
        return f"(ohne Bild, aus Dialog) {txt}" if txt else None
    except Exception as e:  # noqa: BLE001
        logger.warning(f"LLaMA3 Dialog-Fallback fehlgeschlagen: {e}")
        return None


def _phase1_derivate(db, clip: Clip, video_pfad: str, job_id: str) -> dict:
    """Phase 1 der Ingestion — schnell und ohne KI: Metadaten, verknüpfter Ton (Sync), Kameraspur-
    Klassifikation, Proxy (mit Ton), Waveform, Thumbnail-Strip. Wird von `cinassist.proxy_schnell`
    (Vorab-Lauf für ALLE Clips, damit die Timeline sofort spielt) und von `ingestion_pipeline` genutzt;
    vorhandene Derivate werden übersprungen. Rückgabe: Kontext für Phase 2 (Whisper, Szenen, CLIP …)."""
    # ─── Video-Metadaten auslesen ────────────────────
    _update_job(job_id, "laeuft", 2, "Video-Metadaten werden gelesen...", schritt="metadaten")
    info = _get_video_info(video_pfad)
    if info["dauer"] and info["dauer"] > 0:
        clip.dauer = info["dauer"]
    if info["aufloesung"] and info["aufloesung"] != "0x0":
        clip.aufloesung = info["aufloesung"]
    clip.bildrate = info["bildrate"] or clip.bildrate
    clip.codec = info["codec"] or clip.codec
    db.commit()
    _update_job(
        job_id, "laeuft", 3, "Metadaten gelesen.",
        schritt="metadaten",
        schritt_daten={
            "dauer_s": round(info["dauer"], 1),
            "aufloesung": info["aufloesung"],
            "bildrate": info["bildrate"],
            "codec": info["codec"],
            "tool": "ffprobe",
        },
    )
    # ─── 0. Verknüpfter Ton (Sync-Modell) ─────────────
    # Clip mit take_id → primäres TakeAudioLink → WAV-Spur um offset_s ausrichten.
    # Proxy, Waveform, Whisper und Diarization laufen dann auf diesem Ton.
    ton = _verknuepfter_ton(db, clip)
    ton_pfad = None
    if ton is not None:
        _update_job(job_id, "laeuft", 3, "Verknüpfter Ton wird ausgerichtet…", schritt="sync")
        ton_pfad = _ton_ausrichten(ton, info["dauer"], job_id) if ton.get("audio_pfad") else None
        sync_daten = {k: v for k, v in ton.items() if k not in ("audio_pfad",)}
        if ton_pfad:
            sync_daten["ausgerichtet"] = True
            nachricht = f"Ton verknüpft: {ton['audio_dateiname']} (Kanal {ton['kanal']}, Offset {ton['offset_s']:+.3f} s, {ton['methode']}, Konfidenz {ton['konfidenz']})"
        else:
            sync_daten["ausgerichtet"] = False
            sync_daten["warnung"] = ton.get("warnung") or "Verknüpftes Audio konnte nicht ausgerichtet werden — Transkription auf Kamera-Ton"
            nachricht = sync_daten["warnung"]
        _update_job(job_id, "laeuft", 3, nachricht, schritt="sync", schritt_daten=sync_daten)
    stem = clip_stem(clip)
    hat_verknuepften_ton = ton_pfad is not None    # für das Medienart-Etikett (ton_pfad wird später aufgeräumt)

    # ─── 0b. Kameraspur klassifizieren (nur ohne verknüpften Ton) ───
    # Stille / LTC-Timecode / stationäres Rauschen = KEIN Ton → Proxy stumm, keine Waveform,
    # keine Audiohälfte auf der Timeline. Echte Atmo/Musik ohne Sprache bleibt Ton.
    kamera_befund = None
    kamera_ton_nutzbar = True
    if ton_pfad is None and info.get("hat_video", True) and int(info.get("audio_kanaele") or 0) > 0:
        try:
            from backend.core.sync.tonklasse import klassifiziere_datei
            kamera_befund = klassifiziere_datei(video_pfad, int(info["audio_kanaele"]), ffmpeg_bin=FFMPEG_BIN)
            kamera_ton_nutzbar = kamera_befund.hat_ton
            if not kamera_ton_nutzbar:
                logger.info(f"Kameraspur ohne Ton (Stille/LTC/Rauschen) → wird nicht importiert: {kamera_befund.zusammenfassung}")
                _update_job(job_id, "laeuft", 3, "Kameraspur enthält keinen Ton (Stille/Timecode/Rauschen) — Audio wird nicht importiert.",
                            schritt="sync", schritt_daten={"kamera_ton": "kein_ton", "kanaele": [{"kanal": k.kanal, "klasse": k.klasse, "detail": k.detail} for k in kamera_befund.kanaele]})
        except Exception as e:  # nie blockieren
            logger.warning(f"Kameraspur-Klassifikation fehlgeschlagen: {e}")
    elif ton_pfad is None and int(info.get("audio_kanaele") or 0) == 0 and info.get("hat_video", True):
        kamera_ton_nutzbar = False

    # ─── Reine Audiodatei (mp3/wav/…)? → reduzierte Pipeline ───
    # AAC-Proxy statt Video-Proxy, keine Vignetten/Szenen/CLIP/Gesichter; Waveform + VAD +
    # Whisper + Diarization wie gewohnt; genau EINE Szene über die ganze Länge (für Suche/Agent).
    nur_audio = not info.get("hat_video", True)

    # ─── Proxy für Browser-Vorschau erstellen (960p, H.264 | Audio: AAC .m4a) ───
    _update_job(job_id, "laeuft", 4, "Proxy für Browser-Vorschau wird erstellt...", schritt="proxy")
    proxy_pfad = PROXY_DIR / (f"{stem}_proxy.m4a" if nur_audio else f"{stem}_proxy.mp4")
    # 0-Byte-Proxy = früherer FFmpeg-Fehler → löschen und neu versuchen
    if proxy_pfad.exists() and proxy_pfad.stat().st_size == 0:
        proxy_pfad.unlink()
    # Abgeschnittener Proxy (Platte während des Encodes ausgeworfen: 13 s statt 132 s beobachtet) →
    # neu bauen, sonst laufen Stichproben-Frames/Strip/Timeline ins Leere.
    if proxy_pfad.exists() and info.get("dauer"):
        try:
            pd = subprocess.run([FFPROBE_BIN, "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0",
                                 str(proxy_pfad)], capture_output=True, text=True, timeout=30).stdout.strip()
            if not pd or float(pd) < float(info["dauer"]) - 2.0:
                logger.warning(f"Proxy zu kurz ({pd or '?'} s statt {info['dauer']:.0f} s) — wird neu erstellt: {proxy_pfad.name}")
                proxy_pfad.unlink()
                for alt in (PROXY_DIR / f"{stem}_wf.png", PROXY_DIR / f"{stem}_strip.jpg"):
                    alt.unlink(missing_ok=True)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"Proxy-Dauer nicht prüfbar ({e}) — Proxy wird neu erstellt")
            proxy_pfad.unlink(missing_ok=True)
    # Stummer Proxy (beobachtet: Proxy wurde mit dem 1,6-s-Fehlstart-WAV bei +152 s gemuxt → 155 s Stille, obwohl das
    # richtige WAV den Take deckt) → neu bauen, wenn jetzt verknüpfter Ton da ist.
    if proxy_pfad.exists() and not nur_audio and ton_pfad:
        try:
            vd = subprocess.run([FFMPEG_BIN, "-v", "info", "-i", str(proxy_pfad), "-af", "volumedetect", "-f", "null", "-"],
                                capture_output=True, text=True, timeout=120).stderr
            m = re.search(r"max_volume:\s*(-?[\d.]+) dB", vd)
            if m and float(m.group(1)) < -60.0:
                logger.warning(f"Proxy-Ton stumm (max {m.group(1)} dB) trotz verknüpftem WAV — Proxy wird neu erstellt: {proxy_pfad.name}")
                proxy_pfad.unlink()
                (PROXY_DIR / f"{stem}_wf.png").unlink(missing_ok=True)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"Proxy-Stille nicht prüfbar: {e}")
    if nur_audio and not proxy_pfad.exists():
        try:
            subprocess.run([
                FFMPEG_BIN, "-y", "-nostdin", "-i", video_pfad, "-vn",
                "-c:a", "aac", "-b:a", "160k", "-ac", "2", "-movflags", "+faststart", "-f", "ipod",
                str(proxy_pfad),
            ], capture_output=True, timeout=600)
            logger.info(f"Audio-Proxy erstellt: {proxy_pfad}")
        except Exception as e:
            logger.warning(f"Audio-Proxy fehlgeschlagen: {e}")
        if proxy_pfad.exists() and proxy_pfad.stat().st_size == 0:
            proxy_pfad.unlink()
    if not nur_audio and not proxy_pfad.exists():
        try:
            w, h = (int(x) for x in info["aufloesung"].split("x"))
            # Scale to max 960px wide/tall, keep aspect, divisible by 2
            if w >= h:
                scale = "960:-2"
            else:
                scale = "-2:960"
            # Mit verknüpftem Ton: zweiter Input (ausgerichtetes WAV) statt Kamera-Spur.
            # Ohne brauchbaren Kameraton: Proxy ohne Audiospur (-an) — sonst würde der Player
            # Timecode-Gezirpe/Rauschen abspielen.
            # `apad` + `-shortest`: der Ton wird bis zum Videoende mit Stille aufgefüllt, das Video NIE
            # gekürzt (vorher schnitt -shortest den Proxy dort ab, wo das verlinkte WAV endete: 112 s statt 132 s).
            ton_inputs = ["-i", ton_pfad, "-map", "0:v:0", "-map", "1:a:0", "-af", "apad", "-shortest"] if ton_pfad else ([] if kamera_ton_nutzbar else ["-an"])
            subprocess.run([
                FFMPEG_BIN, "-y", "-i", video_pfad, *ton_inputs,
                "-vf", f"scale={scale}",
                "-c:v", "libx264", "-preset", "fast", "-crf", "26",
                # Keyframe alle 12 Frames (~0.5s bei 24fps) — ohne diese
                # Einstellung haben Proxies typischerweise alle 2-3s einen
                # Keyframe, was im HTML-<video>-Seek bis zu 2s Versatz
                # erzeugt (HTML5 Video kann nur zum nächsten Keyframe
                # springen, nicht zum exakten Frame).
                "-g", "12", "-keyint_min", "12", "-sc_threshold", "0",
                # -ac 2 : 5.1/7.1-Quellen (z. B. Big Buck Bunny) auf Stereo
                # downmixen — der AAC-Encoder lehnt "6 channels" ab und
                # FFmpeg schreibt sonst eine 0-Byte-Datei. Bei -an keine Audio-Codec-Optionen.
                *([] if "-an" in ton_inputs else ["-c:a", "aac", "-ac", "2", "-b:a", "128k"]),
                "-movflags", "+faststart",
                str(proxy_pfad),
            ], capture_output=True, timeout=600)
            logger.info(f"Proxy erstellt: {proxy_pfad}")
        except Exception as e:
            logger.warning(f"Proxy-Erstellung fehlgeschlagen: {e}")
        # Fehlgeschlagener Lauf hinterlässt 0-Byte-Datei → aufräumen,
        # sonst liefert die API eine kaputte proxy_url aus (416 → schwarzer Player).
        if proxy_pfad.exists() and proxy_pfad.stat().st_size == 0:
            proxy_pfad.unlink()
    if proxy_pfad.exists():
        proxy_size_mb = round(proxy_pfad.stat().st_size / (1024 * 1024), 2)
        _update_job(
            job_id, "laeuft", 4, "Proxy bereit.",
            schritt="proxy",
            schritt_daten={
                "size_mb": proxy_size_mb,
                "ziel_aufloesung": "max 960px",
                "codec": "H.264 / AAC",
                "preset": "fast, CRF 26",
                "ton": "verknüpftes WAV (ausgerichtet)" if ton_pfad else "Kamera-Spur",
            },
        )
    else:
        _update_job(
            job_id, "laeuft", 4, "Proxy nicht erzeugt.",
            schritt="proxy",
            schritt_daten={"skipped": True, "reason": "FFmpeg-Fehler"},
        )

    # ─── 1b. Waveform-PNG für Audio-Visualisierung in der Timeline ───
    # Wird direkt aus dem Original-Video extrahiert (kein separater
    # Audio-Extraktions-Schritt nötig). Speicherort: proxies/{id}_wf.png
    wf_pfad = PROXY_DIR / f"{stem}_wf.png"
    if not kamera_ton_nutzbar and ton_pfad is None and wf_pfad.exists():
        wf_pfad.unlink()   # frühere Analyse hatte die Rausch-/LTC-Waveform erzeugt
    if not wf_pfad.exists() and (kamera_ton_nutzbar or ton_pfad is not None):
        try:
            subprocess.run([
                FFMPEG_BIN, "-y", "-i", (ton_pfad or video_pfad),
                "-filter_complex",
                # HD-Wellenform: 5760×160 (3× Breite, 2× Höhe) für DaVinci-
                # ähnliches Detail beim Reinzoomen. `scale=cbrt` (Kubik-
                # wurzel) hebt leise Signale sichtbar an — sonst verschwin-
                # den ruhige Dialogpassagen komplett in der Mittellinie.
                "showwavespic=s=5760x160:colors=#d0f5da:split_channels=0:scale=cbrt",
                "-frames:v", "1",
                str(wf_pfad),
            ], capture_output=True, timeout=120)
            logger.info(f"Waveform erstellt: {wf_pfad}")
        except Exception as e:
            logger.warning(f"Waveform-Erstellung fehlgeschlagen: {e}")

    # ─── 1c. Thumbnail-Strip für Video-Segmente in der Timeline ───
    # 24 gleichmäßig verteilte Frames, in einer horizontalen Linie
    # zusammengefügt (DaVinci/Premiere-Stil). Speicherort:
    # proxies/{id}_strip.jpg.
    strip_pfad = PROXY_DIR / f"{stem}_strip.jpg"
    if not nur_audio and not strip_pfad.exists() and info.get("dauer", 0) > 0:
        try:
            # Strip haute résolution (Niveau 1) : 60 tuiles × 192×108 px
            # ergeben 11520×108 px. Verträgt eine vier- bis sechsfache Dehnung ohne
            # sichtbare Treppenbildung, nachdem ein Clip geschnitten wurde.
            n_tiles = 60
            fps_rate = n_tiles / info["dauer"]
            # Quelle = Proxy (960p H.264, Keyframe alle 12 Frames) statt Original: 4K-HEVC-Originale
            # brauchen für den Voll-Decode > 3 min (Timeout). Hochkant-Videos füllen die 16:9-Kachel
            # per zentriertem Crop (wie der Filmstreifen in Resolve/Premiere) — nicht verzerrt, keine
            # schwarzen Lücken.
            strip_quelle = str(proxy_pfad) if proxy_pfad.exists() and proxy_pfad.stat().st_size > 0 else video_pfad
            subprocess.run([
                FFMPEG_BIN, "-y", "-i", strip_quelle,
                "-vf", f"fps={fps_rate},scale=192:108:force_original_aspect_ratio=increase,crop=192:108,tile={n_tiles}x1",
                "-frames:v", "1", "-q:v", "3",
                str(strip_pfad),
            ], capture_output=True, timeout=600)
            logger.info(f"Thumbnail-Strip erstellt (60×192×108): {strip_pfad}")
        except Exception as e:
            logger.warning(f"Thumbnail-Strip-Erzeugung fehlgeschlagen: {e}")

    return {"info": info, "ton": ton, "ton_pfad": ton_pfad, "kamera_befund": kamera_befund,
            "kamera_ton_nutzbar": kamera_ton_nutzbar, "nur_audio": nur_audio, "stem": stem,
            "hat_verknuepften_ton": hat_verknuepften_ton, "proxy_pfad": proxy_pfad}


# ═══════════════════════════════════════════════════════════
# NEU TRANSKRIBIEREN: nur Tonspur → Whisper → Szenen-Transkript aktualisieren (Proxy/Szenen bleiben)
# ═══════════════════════════════════════════════════════════

@celery_app.task(bind=True, name="cinassist.transkribieren", max_retries=1)
def transkribieren(self, clip_id: str, job_id: str) -> dict[str, Any]:
    db = SyncSessionLocal()
    ton_pfad = None
    audio_pfad = None
    try:
        clip = db.query(Clip).filter(Clip.id == clip_id).first()
        if not clip:
            _update_job(job_id, "fehler", 0, "Clip nicht gefunden.")
            return {"error": "Clip nicht gefunden"}
        video_pfad = clip.dateipfad
        if not Path(video_pfad).exists():
            _update_job(job_id, "fehler", 0, "Datei nicht erreichbar (Volume gemountet?).")
            return {"error": "Datei nicht erreichbar"}
        _update_job(job_id, "laeuft", 5, "Ton wird vorbereitet…", schritt="sync")
        info = _get_video_info(video_pfad)
        ton = _verknuepfter_ton(db, clip)
        if ton is not None and ton.get("audio_pfad"):
            ton_pfad = _ton_ausrichten(ton, info["dauer"], job_id)
            _update_job(job_id, "laeuft", 8, f"Verknüpfter Ton: {ton['audio_dateiname']}, Kanal {ton['kanal']} ({ton.get('kanal_grund','')})",
                        schritt="sync", schritt_daten={k: v for k, v in ton.items() if k != "audio_pfad"})
        audio_pfad = schritt_audio_extrahieren(video_pfad, job_id, ton_pfad=ton_pfad, ton=ton)
        if not audio_pfad:
            _update_job(job_id, "fertig", 100, "Kein Nutzton — nichts zu transkribieren.", schritt="transkription",
                        schritt_daten={"skipped": True})
            return {"clip_id": clip_id, "segmente": 0}
        transkription = schritt_transkription(audio_pfad, job_id) or {"segmente": []}
        if transkription.get("uebersprungen"):
            _update_job(job_id, "fertig", 100, "Übersprungen (keine Sprache erkannt) — bisheriges Transkript bleibt.", schritt="transkription",
                        schritt_daten={"skipped": True})
            return {"clip_id": clip_id, "segmente": 0, "uebersprungen": True}
        szenen = db.query(Szene).filter(Szene.clip_id == clip.id).order_by(Szene.szenen_nr).all()
        if not szenen:
            dauer = float(info.get("dauer") or clip.dauer or 0.0)
            szenen = [Szene(clip_id=clip.id, szenen_nr=1, start_zeit=0.0, end_zeit=dauer, dauer=dauer)]
            db.add(szenen[0])
        for sz in szenen:
            segs = [seg for seg in transkription.get("segmente", []) if sz.start_zeit <= seg["start"] < sz.end_zeit]
            sz.transkription = (" ".join(seg["text"] for seg in segs).strip() or None)
            sz.transkription_json = segs or None
        db.commit()
        n = len(transkription.get("segmente", []))
        _update_job(job_id, "fertig", 100, f"Neu transkribiert — {n} Segmente.", schritt="transkription",
                    schritt_daten={"segmente": n, "sprache": transkription.get("sprache")})
        return {"clip_id": clip_id, "segmente": n}
    except Exception as e:
        logger.exception(f"Neu-Transkription fehlgeschlagen: {e}")
        _update_job(job_id, "fehler", 0, f"Fehler: {e}")
        return {"error": str(e)}
    finally:
        for p in (ton_pfad, audio_pfad):
            if p:
                Path(p).unlink(missing_ok=True)
        db.close()


# ═══════════════════════════════════════════════════════════
# VORAB-TASK: Phase 1 für einen Clip (Proxy/Waveform/Strip) — läuft für ALLE Clips vor der Analyse
# ═══════════════════════════════════════════════════════════

@celery_app.task(bind=True, name="cinassist.proxy_schnell", max_retries=1)
def proxy_schnell(self, clip_id: str, job_id: str) -> dict[str, Any]:
    """Nur Phase 1: Metadaten + verknüpfter Ton + Proxy + Waveform + Strip (~30 s je ProRes-Take).
    Wird beim „In Medien übernehmen“ für alle Clips ZUERST eingereiht, danach die volle Analyse —
    so ist jeder Clip nach Minuten in der Timeline abspielbar, statt erst nach der letzten Analyse."""
    db = SyncSessionLocal()
    ton_pfad: str | None = None
    try:
        clip = db.query(Clip).filter(Clip.id == clip_id).first()
        if not clip:
            _update_job(job_id, "fehler", 0, f"Clip {clip_id} nicht gefunden.")
            return {"error": "Clip nicht gefunden"}
        video_pfad = clip.dateipfad
        from backend.core.sync.probe import volume_gemountet
        if not volume_gemountet(video_pfad) or not Path(video_pfad).exists():
            _update_job(job_id, "wartend", 0, "Datenträger/Datei nicht erreichbar — Vorschau wartet, neuer Versuch in 60 s.", schritt="metadaten")
            db.close()
            raise self.retry(countdown=60, max_retries=240)
        ph = _phase1_derivate(db, clip, video_pfad, job_id)
        ton_pfad = ph["ton_pfad"]
        # Vorläufiges Medienart-Etikett (Phase 2 bestätigt es).
        clip.hat_bild = bool(ph["info"].get("hat_video", True))
        clip.hat_ton = bool(ph["nur_audio"] or ph["hat_verknuepften_ton"]
                            or (ph["kamera_ton_nutzbar"] and int(ph["info"].get("audio_kanaele") or 0) > 0))
        db.commit()
        _update_job(job_id, "fertig", 100, "Vorschau bereit (Proxy, Waveform, Vignetten).", schritt="proxy",
                    schritt_daten={"phase": 1})
        return {"clip_id": clip_id, "proxy": str(ph["proxy_pfad"])}
    except Exception as e:
        from celery.exceptions import Retry
        if isinstance(e, Retry):
            raise
        logger.warning(f"proxy_schnell {clip_id} fehlgeschlagen: {e}")
        _update_job(job_id, "fehler", 0, f"Vorschau fehlgeschlagen: {e}")
        return {"error": str(e)}
    finally:
        if ton_pfad:
            Path(ton_pfad).unlink(missing_ok=True)
        db.close()


# ═══════════════════════════════════════════════════════════
# HAUPT-TASK: Ingestion Pipeline
# ═══════════════════════════════════════════════════════════

@celery_app.task(bind=True, name="cinassist.ingest", max_retries=1)
def ingestion_pipeline(self, clip_id: str, job_id: str) -> dict[str, Any]:
    """
    Vollständige Ingestion-Pipeline für ein hochgeladenes Video.

    Wird automatisch nach Upload gestartet.
    Fortschritt wird per Redis Pub/Sub an WebSocket gesendet.
    """
    db = SyncSessionLocal()
    ton_pfad: str | None = None   # ausgerichtetes verknüpftes WAV (TEMP), wird immer aufgeräumt
    video_pfad: str | None = None

    try:
        # Clip aus DB laden
        clip = db.query(Clip).filter(Clip.id == clip_id).first()
        if not clip:
            _update_job(job_id, "fehler", 0, f"Clip {clip_id} nicht gefunden.")
            return {"error": "Clip nicht gefunden"}

        video_pfad = clip.dateipfad
        logger.info(f"Starte Ingestion für Clip {clip_id}: {clip.dateiname}")

        # Datenträger weg (USB-Platte ausgeworfen, I/O-Fehler)? Nicht scheitern, sondern warten:
        # Job bleibt „wartend“ mit klarer Meldung, Task wird in 60 s erneut eingereiht (bis zu 4 h).
        # Clip-Status und Metadaten bleiben unangetastet — die Analyse läuft von selbst weiter,
        # sobald das Volume zurück ist.
        from backend.core.sync.probe import volume_gemountet
        if not volume_gemountet(video_pfad):
            # Nur der Mountpoint wird geprüft (lokaler stat auf /Volumes) — KEIN Zugriff auf die USB-Platte,
            # solange sie weg ist. Analysen laufen ohnehin strikt nacheinander (Worker --pool=solo).
            _update_job(job_id, "wartend", 0, "Datenträger nicht gemountet — Analyse wartet, neuer Versuch in 60 s.", schritt="metadaten")
            db.close()
            raise self.retry(countdown=60, max_retries=240)
        if not Path(video_pfad).exists():
            _update_job(job_id, "wartend", 0, "Datei nicht erreichbar — Analyse wartet, neuer Versuch in 60 s.", schritt="metadaten")
            db.close()
            raise self.retry(countdown=60, max_retries=240)

        # ─── Phase 1: Metadaten, verknüpfter Ton, Proxy, Waveform, Strip (siehe _phase1_derivate) ───
        ph = _phase1_derivate(db, clip, video_pfad, job_id)
        info = ph["info"]; ton = ph["ton"]; ton_pfad = ph["ton_pfad"]; kamera_befund = ph["kamera_befund"]
        kamera_ton_nutzbar = ph["kamera_ton_nutzbar"]; nur_audio = ph["nur_audio"]; stem = ph["stem"]
        hat_verknuepften_ton = ph["hat_verknuepften_ton"]

        # ─── 1. Audio extrahieren (verknüpfter Ton, sonst Kamera-Spur) ───
        audio_pfad = schritt_audio_extrahieren(video_pfad, job_id, ton_pfad=ton_pfad, ton=ton, tonbefund=kamera_befund)
        if ton_pfad:
            Path(ton_pfad).unlink(missing_ok=True)
            ton_pfad = None

        # ─── 2. Transkription ────────────────────────────
        transkription = {"text": "", "sprache": "de", "segmente": []}
        diarization = {"available": False, "segments": [], "speakers": [], "total_speakers": 0}
        if audio_pfad:
            transkription = schritt_transkription(audio_pfad, job_id)
            # ─── 2b. Speaker diarization (Vague 1.2) ──────
            # Fehlt das HF-Token, geschieht stillschweigend nichts: diarization["available"] = False.
            diarization = schritt_diarization(audio_pfad, job_id)
            # Temp-Audio löschen
            Path(audio_pfad).unlink(missing_ok=True)
        else:
            _update_job(job_id, "laeuft", 30, "Transkription übersprungen — kein Nutzton in der Quelle.",
                        schritt="transkription",
                        schritt_daten={"skipped": True, "reason": "kein Nutzton (Stille/Timecode/Rauschen) oder keine Audiospur",
                                       "segmente": 0, "woerter": 0})

        if nur_audio:
            # Eine Szene über die ganze Datei; keine Bildanalyse.
            dauer_a = float(info.get("dauer") or 0.0)
            szenen = [{"szenen_nr": 1, "start_zeit": 0.0, "end_zeit": dauer_a, "dauer": dauer_a,
                       "thumbnail_frame": None, "thumbnail_pfad": None}]
            analyse_visuelle, embeddings, beschreibungen, faces_by_scene = [], [], [], []
            _update_job(job_id, "laeuft", 90, "Audiodatei — Bildanalyse übersprungen.", schritt="szenenerkennung",
                        schritt_daten={"anzahl": 1, "skipped": True, "reason": "reine Audiodatei"})
        else:
            # ─── 3. Szenen erkennen ──────────────────────────
            szenen = schritt_szenen_erkennen(video_pfad, clip_id, job_id)

            # ─── 3b. Visuelle Analyse (PIL) ──────────────────
            analyse_visuelle = schritt_analyse_visuelle(video_pfad, szenen, job_id)

            # ─── 4. CLIP Embeddings ──────────────────────────
            embeddings = schritt_clip_embeddings(video_pfad, szenen, job_id)

            # ─── 5 + 5b. Bildanalyse: Stichproben-Frames → Beschreibung + Personen + Framing ───
            # Frames kommen aus dem Proxy (schnell, 896 px) statt aus dem 4K-Original.
            _pp = ph.get("proxy_pfad")
            bild_quelle = str(_pp) if _pp and Path(_pp).exists() and Path(_pp).stat().st_size > 0 else video_pfad
            bild = schritt_bildanalyse(szenen, transkription, job_id, clip_id, bild_quelle, original_quelle=video_pfad)
            beschreibungen = [b.get("beschreibung") or "" for b in bild]
            faces_by_scene = [{"face_count": b.get("face_count", 0), "framing": b.get("framing"),
                               "faces": b.get("faces"), "personen": b.get("personen"),
                               "stichproben": b.get("stichproben")} for b in bild]

        # ─── Ergebnisse in DB speichern ──────────────────
        _update_job(job_id, "laeuft", 97, "Ergebnisse werden gespeichert...", schritt="persistierung")

        # Sprecher an die Whisper-Segmente heften (größte zeitliche Überlappung mit einem Diarization-Turn).
        # Landet in `szenen.transkription_json[*].sprecher` — Grundlage für „wer sagt was“ im Bericht/UI.
        if diarization.get("available") and diarization.get("segments"):
            _turns = diarization["segments"]
            for _seg in transkription.get("segmente", []):
                _best, _ov = None, 0.0
                for _t in _turns:
                    o = min(float(_seg["end"]), float(_t["end"])) - max(float(_seg["start"]), float(_t["start"]))
                    if o > _ov:
                        _ov, _best = o, _t["speaker"]
                if _best and _ov > 0:
                    _seg["sprecher"] = _best

        # Re-Analyse (Retry, „Neu analysieren“) darf KEINE Duplikate erzeugen: alte Szenen/Sprecher weg.
        from backend.core.database import Speaker as _Sp, SceneSpeaker as _SSp
        _alte = db.query(Szene.id).filter(Szene.clip_id == clip_id).all()
        if _alte:
            _ids = [r[0] for r in _alte]
            db.query(_SSp).filter(_SSp.scene_id.in_(_ids)).delete(synchronize_session=False)
            db.query(Szene).filter(Szene.clip_id == clip_id).delete(synchronize_session=False)
        db.query(_Sp).filter(_Sp.clip_id == clip_id).delete(synchronize_session=False)
        db.flush()

        for i, szene_data in enumerate(szenen):
            # Passende Transkriptions-Segmente finden.
            # Korrektur vom 19.07.2026: zuvor prüfte eine Überschneidung, die zuordnete
            # DASSELBE Whisper-Segment ALLEN Szenen zugeordnet, die es überspannte;
            # dadurch stand die Transkription mehrfach da. Geprüft wird nun,
            # nun zählt allein der ANFANG: ein Segment gehört zu GENAU einer
            # genau eine Szene, nämlich die, in der das Segment beginnt.
            seg_text = ""
            seg_json = []
            for seg in transkription.get("segmente", []):
                if szene_data["start_zeit"] <= seg["start"] < szene_data["end_zeit"]:
                    seg_text += seg["text"] + " "
                    seg_json.append(seg)

            szene = Szene(
                clip_id=clip_id,
                szenen_nr=szene_data["szenen_nr"],
                start_zeit=szene_data["start_zeit"],
                end_zeit=szene_data["end_zeit"],
                dauer=szene_data["dauer"],
                thumbnail_frame=szene_data.get("thumbnail_frame"),
                thumbnail_pfad=szene_data.get("thumbnail_pfad"),
                clip_embedding=embeddings[i] if i < len(embeddings) else None,
                beschreibung=beschreibungen[i] if i < len(beschreibungen) else None,
                transkription=seg_text.strip() or None,
                transkription_json=seg_json or None,
                analyse_visuelle=_mit_bilddaten(analyse_visuelle[i] if i < len(analyse_visuelle) else None,
                                                faces_by_scene[i] if i < len(faces_by_scene) else None),
                face_count=(faces_by_scene[i].get("face_count", 0) if i < len(faces_by_scene) else 0),
                framing=(faces_by_scene[i].get("framing") if i < len(faces_by_scene) else None),
                faces_data=(faces_by_scene[i].get("faces") if i < len(faces_by_scene) else None),
            )
            db.add(szene)

        # ─── 6. Speaker + SceneSpeaker persistieren (Vague 1.2) ──
        if diarization.get("available") and diarization.get("segments"):
            from backend.core.database import Speaker, SceneSpeaker
            from backend.core.diarize import summarize_by_speaker, match_speakers_to_scenes

            # Zwischenspeichern, um die Kennungen der Szenen zu erhalten
            db.flush()

            summary = summarize_by_speaker(diarization["segments"])
            speaker_row_by_label: dict[str, Speaker] = {}
            for label, agg in summary.items():
                sp = Speaker(
                    clip_id=clip_id,
                    label_auto=label,
                    total_speaking_time=agg["total_time"],
                    segment_count=agg["segment_count"],
                )
                db.add(sp)
                speaker_row_by_label[label] = sp
            db.flush()  # erzeugt die Sprecher-IDs

            # Zuordnung Szene zu Sprecher
            # Die eben angelegten Szenen erneut abfragen, um ihre Kennungen zu erhalten
            from sqlalchemy import select as _sel
            scene_rows = db.execute(
                _sel(Szene).where(Szene.clip_id == clip_id).order_by(Szene.szenen_nr)
            ).scalars().all()
            scene_ranges = [(float(s.start_zeit), float(s.end_zeit)) for s in scene_rows]
            per_scene = match_speakers_to_scenes(diarization["segments"], scene_ranges)
            for scene_row, sp_map in zip(scene_rows, per_scene):
                for label, dur in sp_map.items():
                    sp = speaker_row_by_label.get(label)
                    if sp:
                        db.add(SceneSpeaker(
                            scene_id=scene_row.id,
                            speaker_id=sp.id,
                            speaking_time=dur,
                        ))

        # Medienart-Etikett: hat_bild / hat_ton (siehe Clip-Modell)
        clip.hat_bild = bool(info.get("hat_video", True))
        clip.hat_ton = bool(
            nur_audio
            or hat_verknuepften_ton
            or (kamera_ton_nutzbar and int(info.get("audio_kanaele") or 0) > 0)
        )

        # Clip-Status aktualisieren
        clip.status = "analysiert"
        db.commit()

        _update_job(
            job_id, "laeuft", 99, f"{len(szenen)} Szenen in PostgreSQL gespeichert.",
            schritt="persistierung",
            schritt_daten={
                "szenen_gespeichert": len(szenen),
                "tabellen": ["clips (UPDATE)", "szenen (INSERT)"],
                "datenbank": "PostgreSQL",
            },
        )

        ergebnis = {
            "clip_id": clip_id,
            "szenen_anzahl": len(szenen),
            "dauer": info["dauer"],
            "aufloesung": info["aufloesung"],
            "transkription_laenge": len(transkription.get("text", "")),
            "hat_embeddings": any(e != [0.0] * 512 for e in embeddings),
        }

        _update_job(job_id, "fertig", 100, "Analyse abgeschlossen.", ergebnis)
        logger.info(f"Ingestion fertig für {clip_id}: {len(szenen)} Szenen")
        return ergebnis

    except Exception as e:
        from celery.exceptions import Retry
        if isinstance(e, Retry):
            raise                                   # geplantes Warten auf den Datenträger, kein Fehler
        try:
            db.rollback()                           # Session aus dem Fehlerzustand holen
        except Exception:
            pass
        # Transienter DB-Fehler (Deadlock, Verbindungsabbruch)? → kurz warten und neu anlaufen.
        from sqlalchemy.exc import OperationalError, DBAPIError, DataError
        if isinstance(e, DataError):
            # Kein transienter Fehler (zu langer Wert / ungültiges JSON) — Wiederholen hilft nicht: klar melden.
            logger.error(f"Ingestion {clip_id}: Datenfehler beim Speichern — {str(e)[:400]}")
        elif isinstance(e, (OperationalError, DBAPIError)) and getattr(e, "connection_invalidated", False) is not None:
            logger.warning(f"Ingestion {clip_id}: DB-Fehler ({type(e).__name__}) — neuer Versuch in 30 s")
            _update_job(job_id, "wartend", 0, "Datenbank kurz nicht erreichbar/Deadlock — Analyse wird in 30 s wiederholt.", schritt="metadaten")
            db.close()
            raise self.retry(countdown=30, max_retries=5)
        # Datenträger mittendrin verschwunden (USB-Platte wirft sich aus)? → nicht als Fehler werten,
        # sondern wie oben warten und später komplett neu anlaufen. Prüfung über den gemerkten Pfad
        # (`video_pfad`), NICHT über das ORM-Objekt — dessen Zugriff scheitert nach einem DB-Fehler.
        try:
            pfad_weg = bool(video_pfad) and not Path(video_pfad).exists()
        except Exception:
            pfad_weg = False
        if pfad_weg:
            logger.warning(f"Ingestion {clip_id}: Datei während der Analyse verschwunden — warte auf Datenträger ({e})")
            _update_job(job_id, "wartend", 0, "Datenträger während der Analyse verschwunden — Analyse wartet, neuer Versuch in 60 s.", schritt="metadaten")
            db.close()
            raise self.retry(countdown=60, max_retries=240)
        logger.exception(f"Ingestion fehlgeschlagen für {clip_id}: {e}")
        _update_job(job_id, "fehler", 0, f"Fehler: {str(e)}")
        clip = db.query(Clip).filter(Clip.id == clip_id).first()
        if clip:
            clip.status = "fehler"
            db.commit()
        raise

    finally:
        if ton_pfad:
            Path(ton_pfad).unlink(missing_ok=True)
        db.close()
