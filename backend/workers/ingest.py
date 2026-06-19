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
from backend.core.database import SyncSessionLocal, Clip, Szene, Job

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
        r = redis.from_url("redis://localhost:6379/0")
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

    return {
        "dauer": dauer,
        "aufloesung": f"{breite}x{hoehe}",
        "bildrate": bildrate,
        "codec": video_stream.get("codec_name", "unbekannt"),
    }


# ═══════════════════════════════════════════════════════════
# SCHRITT 1: Audio extrahieren (FFmpeg → WAV 16kHz mono)
# ═══════════════════════════════════════════════════════════

def schritt_audio_extrahieren(video_pfad: str, job_id: str) -> str | None:
    """Extrahiert Audio als WAV 16kHz mono für Whisper."""
    _update_job(job_id, "laeuft", 5, "Audio wird extrahiert (FFmpeg)...", schritt="audio")

    audio_pfad = TEMP_DIR / f"{uuid.uuid4().hex}_audio.wav"

    cmd = [
        FFMPEG_BIN, "-y",
        "-i", video_pfad,
        "-vn",                          # Kein Video
        "-acodec", "pcm_s16le",         # PCM 16-bit
        "-ar", str(AUDIO_SAMPLE_RATE),  # 16kHz
        "-ac", "1",                     # Mono
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
        _update_job(
            job_id, "laeuft", 10, "Audio erfolgreich extrahiert.",
            schritt="audio",
            schritt_daten={
                "size_kb": audio_size_kb,
                "sample_rate": AUDIO_SAMPLE_RATE,
                "channels": 1,
                "format": "WAV PCM 16-bit",
            },
        )
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
    norm = text.strip().lower()
    if len(norm) <= 2:
        return True
    # Exakter Treffer in Halluzinations-Liste
    if norm in WHISPER_HALLUCINATIONS:
        return True
    # Auch sehr kurze Sätze ohne Inhalt (z.B. nur Satzzeichen)
    if len(norm.replace(".", "").replace(",", "").replace("!", "").replace("?", "").strip()) <= 2:
        return True
    # Repetitive Token-Halluzination (z.B. "Musik Musik Musik")
    if _ist_repetierte_halluzination(text):
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


def schritt_transkription(audio_pfad: str, job_id: str) -> dict | None:
    """Transkribiert Audio mit mlx-whisper (Apple Silicon optimiert).
    Mit Stille-Vorprüfung + Halluzinations-Filter, sodass stille Clips
    keinen erfundenen Pseudo-Dialog ("Vielen Dank") produzieren."""
    _update_job(job_id, "laeuft", 15, "Transkription läuft (mlx-whisper)...", schritt="transkription")

    # Vorprüfung: ist die Audiospur stumm? Wenn ja, Whisper überspringen.
    if _ist_audio_stille(audio_pfad):
        logger.info("Audiospur ist faktisch stumm — Whisper übersprungen, kein Pseudo-Dialog")
        _update_job(
            job_id, "laeuft", 30,
            "Stumme Audiospur erkannt — Transkription übersprungen.",
            schritt="transkription",
            schritt_daten={"segmente": 0, "woerter": 0, "sprache": "—", "preview": "(stumm)", "modell": "skipped"},
        )
        return {"text": "", "sprache": "de", "segmente": []}

    try:
        import mlx_whisper

        result = mlx_whisper.transcribe(
            audio_pfad,
            path_or_hf_repo=WHISPER_MODEL,
            language="de",
            word_timestamps=True,
        )

        transkription = {
            "text": result.get("text", ""),
            "sprache": result.get("language", "de"),
            "segmente": [],
        }

        # Halluzinations-Filter: bekannte Stille-Pseudotexte rauswerfen.
        halluzinationen_entfernt = 0
        for seg in result.get("segments", []):
            seg_text = seg["text"].strip()
            if _ist_halluzination(seg_text):
                halluzinationen_entfernt += 1
                continue
            transkription["segmente"].append({
                "start": round(seg["start"], 3),
                "end": round(seg["end"], 3),
                "text": seg_text,
                "woerter": [
                    {
                        "wort": w["word"].strip(),
                        "start": round(w["start"], 3),
                        "end": round(w["end"], 3),
                    }
                    for w in seg.get("words", [])
                ],
            })

        # Wenn ALLE Segmente Halluzinationen waren, ist der Gesamttext leer
        if halluzinationen_entfernt and not transkription["segmente"]:
            transkription["text"] = ""
            logger.info(f"Whisper hat nur Halluzinationen produziert — alle {halluzinationen_entfernt} entfernt")
        elif halluzinationen_entfernt:
            logger.info(f"{halluzinationen_entfernt} Whisper-Halluzinationen herausgefiltert")
            transkription["text"] = " ".join(s["text"] for s in transkription["segmente"])

        wort_count = sum(len(seg.get("woerter", [])) for seg in transkription["segmente"])
        preview = (transkription.get("text") or "").strip().replace("\n", " ")
        if len(preview) > 80:
            preview = preview[:77] + "…"
        _update_job(
            job_id, "laeuft", 30,
            f"Transkription fertig — {len(transkription['segmente'])} Segmente.",
            schritt="transkription",
            schritt_daten={
                "segmente": len(transkription["segmente"]),
                "woerter": wort_count,
                "sprache": transkription.get("sprache", "?"),
                "preview": preview or "(keine Sprache erkannt)",
                "modell": "whisper-large-v3-turbo",
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
    """Erzeugt CLIP-Embeddings für jede Szene (Mittelpunkt-Frame)."""
    _update_job(job_id, "laeuft", 55, "Visuelle Embeddings werden erstellt (CLIP)...", schritt="clip")

    try:
        import torch
        import open_clip
        from PIL import Image

        device = "mps" if torch.backends.mps.is_available() else "cpu"
        model, _, preprocess = open_clip.create_model_and_transforms(CLIP_MODEL, pretrained="openai", device=device)

        embeddings = []
        total = len(szenen)

        for i, szene in enumerate(szenen):
            mitte = (szene["start_zeit"] + szene["end_zeit"]) / 2

            # Frame per FFmpeg extrahieren
            frame_pfad = TEMP_DIR / f"clip_frame_{uuid.uuid4().hex}.jpg"
            cmd = [
                FFMPEG_BIN, "-y",
                "-ss", str(mitte),
                "-i", video_pfad,
                "-frames:v", "1",
                "-q:v", "2",
                str(frame_pfad),
            ]
            subprocess.run(cmd, capture_output=True, timeout=30)

            if frame_pfad.exists():
                image = preprocess(Image.open(str(frame_pfad))).unsqueeze(0).to(device)
                with torch.no_grad():
                    embedding = model.encode_image(image)
                    embedding = embedding / embedding.norm(dim=-1, keepdim=True)
                    embeddings.append(embedding.cpu().squeeze().tolist())
                frame_pfad.unlink()
            else:
                embeddings.append([0.0] * 512)

            fortschritt = 55 + int((i + 1) / total * 20)
            _update_job(job_id, "laeuft", fortschritt, f"CLIP Embedding {i+1}/{total}...")

        nonzero = sum(1 for e in embeddings if any(v != 0.0 for v in e))
        _update_job(
            job_id, "laeuft", 75, f"{len(embeddings)} Embeddings erstellt.",
            schritt="clip",
            schritt_daten={
                "embeddings": len(embeddings),
                "embeddings_nonzero": nonzero,
                "dimension": 512,
                "modell": "ViT-B/32 (OpenAI, open_clip)",
                "device": "mps" if (lambda: __import__('torch').backends.mps.is_available())() else "cpu",
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
        return [[0.0] * 512 for _ in szenen]


# ═══════════════════════════════════════════════════════════
# SCHRITT 5: Szenen beschreiben (Ollama / LLaMA3)
# ═══════════════════════════════════════════════════════════

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

    _update_job(job_id, "laeuft", 80, "Szenen werden beschrieben (LLaVA Vision)…", schritt="beschreibungen")

    try:
        import httpx

        beschreibungen: list[str] = []
        total = len(szenen)
        # Welches Modell wirklich verwendet wurde (zur Berichterstattung)
        used_model = "llava:7b"
        llava_failures = 0

        # Visual prompt — strikt faktisch, agnostisch zum Genre.
        # Sprache: bevorzugt Deutsch, akzeptiert aber Englisch falls
        # LLaVA-7B die Sprachvorgabe ignoriert (bekanntes Verhalten kleinerer
        # multimodaler Modelle). Wir erzwingen Deutsch nicht — eine
        # falsch-übersetzte Beschreibung wäre schlimmer als eine englische.
        VISION_PROMPT = (
            "ANTWORTE AUF DEUTSCH. "
            "Beschreibe, was in diesem Bild zu sehen ist — in 2-3 sachlichen Sätzen. "
            "Be strictly factual: name the subject, the framing "
            "(close-up / medium / wide / extreme close-up), the setting, lighting "
            "(natural / studio / dim / warm / cold) and any prominent objects. "
            "Do NOT interpret emotions, story or intentions. "
            "Do NOT invent dialogue or events. "
            "WICHTIG: Antworte auf Deutsch."
        )

        for i, szene in enumerate(szenen):
            beschreibung: str | None = None
            thumb_pfad = szene.get("thumbnail_pfad")

            # ─── 1. PRIMÄR : LLaVA auf das Thumbnail ──────────────────
            if thumb_pfad and Path(thumb_pfad).exists():
                try:
                    with open(thumb_pfad, "rb") as f:
                        img_b64 = base64.b64encode(f.read()).decode("utf-8")
                    resp = httpx.post(
                        f"{OLLAMA_BASE_URL}/api/generate",
                        json={
                            "model": "llava:7b",
                            "prompt": VISION_PROMPT,
                            "images": [img_b64],
                            "stream": False,
                            # num_predict 220 → erlaubt 2-3 vollständige Sätze ohne
                            # vorzeitiges Abschneiden. Vorher 100 = oft unvollständig.
                            "options": {"temperature": 0.2, "num_predict": 220},
                        },
                        timeout=180.0,
                    )
                    resp.raise_for_status()
                    text = resp.json().get("response", "").strip()
                    if text:
                        beschreibung = _normalize_llava(text)
                except Exception as e:
                    llava_failures += 1
                    logger.warning(f"LLaVA fehlgeschlagen für Szene {i+1}: {e}")

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

    try:
        # Clip aus DB laden
        clip = db.query(Clip).filter(Clip.id == clip_id).first()
        if not clip:
            _update_job(job_id, "fehler", 0, f"Clip {clip_id} nicht gefunden.")
            return {"error": "Clip nicht gefunden"}

        video_pfad = clip.dateipfad
        logger.info(f"Starte Ingestion für Clip {clip_id}: {clip.dateiname}")

        # ─── Video-Metadaten auslesen ────────────────────
        _update_job(job_id, "laeuft", 2, "Video-Metadaten werden gelesen...", schritt="metadaten")
        info = _get_video_info(video_pfad)
        clip.dauer = info["dauer"]
        clip.aufloesung = info["aufloesung"]
        clip.bildrate = info["bildrate"]
        clip.codec = info["codec"]
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
        # ─── Proxy für Browser-Vorschau erstellen (960p, H.264) ───
        _update_job(job_id, "laeuft", 4, "Proxy für Browser-Vorschau wird erstellt...", schritt="proxy")
        proxy_pfad = PROXY_DIR / f"{Path(video_pfad).stem}_proxy.mp4"
        if not proxy_pfad.exists():
            try:
                w, h = (int(x) for x in info["aufloesung"].split("x"))
                # Scale to max 960px wide/tall, keep aspect, divisible by 2
                if w >= h:
                    scale = "960:-2"
                else:
                    scale = "-2:960"
                subprocess.run([
                    FFMPEG_BIN, "-y", "-i", video_pfad,
                    "-vf", f"scale={scale}",
                    "-c:v", "libx264", "-preset", "fast", "-crf", "26",
                    # Keyframe alle 12 Frames (~0.5s bei 24fps) — ohne diese
                    # Einstellung haben Proxies typischerweise alle 2-3s einen
                    # Keyframe, was im HTML-<video>-Seek bis zu 2s Versatz
                    # erzeugt (HTML5 Video kann nur zum nächsten Keyframe
                    # springen, nicht zum exakten Frame).
                    "-g", "12", "-keyint_min", "12", "-sc_threshold", "0",
                    "-c:a", "aac", "-b:a", "128k",
                    "-movflags", "+faststart",
                    str(proxy_pfad),
                ], capture_output=True, timeout=600)
                logger.info(f"Proxy erstellt: {proxy_pfad}")
            except Exception as e:
                logger.warning(f"Proxy-Erstellung fehlgeschlagen: {e}")
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
        wf_pfad = PROXY_DIR / f"{Path(video_pfad).stem}_wf.png"
        if not wf_pfad.exists():
            try:
                subprocess.run([
                    FFMPEG_BIN, "-y", "-i", video_pfad,
                    "-filter_complex",
                    # 1920px breit, 80px hoch, hellgrüne Wellenform auf
                    # transparentem Hintergrund (für Overlay auf Timeline)
                    "showwavespic=s=1920x80:colors=#86efac:split_channels=0",
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
        strip_pfad = PROXY_DIR / f"{Path(video_pfad).stem}_strip.jpg"
        if not strip_pfad.exists() and info.get("dauer", 0) > 0:
            try:
                n_tiles = 24
                fps_rate = n_tiles / info["dauer"]
                subprocess.run([
                    FFMPEG_BIN, "-y", "-i", video_pfad,
                    "-vf", f"fps={fps_rate},scale=80:45,tile={n_tiles}x1",
                    "-frames:v", "1", "-q:v", "5",
                    str(strip_pfad),
                ], capture_output=True, timeout=120)
                logger.info(f"Thumbnail-Strip erstellt: {strip_pfad}")
            except Exception as e:
                logger.warning(f"Thumbnail-Strip-Erzeugung fehlgeschlagen: {e}")

        # ─── 1. Audio extrahieren ────────────────────────
        audio_pfad = schritt_audio_extrahieren(video_pfad, job_id)

        # ─── 2. Transkription ────────────────────────────
        transkription = {"text": "", "sprache": "de", "segmente": []}
        if audio_pfad:
            transkription = schritt_transkription(audio_pfad, job_id)
            # Temp-Audio löschen
            Path(audio_pfad).unlink(missing_ok=True)

        # ─── 3. Szenen erkennen ──────────────────────────
        szenen = schritt_szenen_erkennen(video_pfad, clip_id, job_id)

        # ─── 3b. Visuelle Analyse (PIL) ──────────────────
        analyse_visuelle = schritt_analyse_visuelle(video_pfad, szenen, job_id)

        # ─── 4. CLIP Embeddings ──────────────────────────
        embeddings = schritt_clip_embeddings(video_pfad, szenen, job_id)

        # ─── 5. Szenen beschreiben ───────────────────────
        beschreibungen = schritt_szenen_beschreiben(szenen, transkription, job_id)

        # ─── Ergebnisse in DB speichern ──────────────────
        _update_job(job_id, "laeuft", 97, "Ergebnisse werden gespeichert...", schritt="persistierung")

        for i, szene_data in enumerate(szenen):
            # Passende Transkriptions-Segmente finden
            seg_text = ""
            seg_json = []
            for seg in transkription.get("segmente", []):
                if (seg["start"] < szene_data["end_zeit"] and
                    seg["end"] > szene_data["start_zeit"]):
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
                analyse_visuelle=analyse_visuelle[i] if i < len(analyse_visuelle) else None,
            )
            db.add(szene)

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
        logger.exception(f"Ingestion fehlgeschlagen für {clip_id}: {e}")
        _update_job(job_id, "fehler", 0, f"Fehler: {str(e)}")
        clip = db.query(Clip).filter(Clip.id == clip_id).first()
        if clip:
            clip.status = "fehler"
            db.commit()
        raise

    finally:
        db.close()
