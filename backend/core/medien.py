"""Hilfen für Medienpfade: Upload-Kopie vs. per Referenz importiertes Original.

Ein Clip mit `take_id` zeigt mit `dateipfad` auf das Original im Import-Ordner
(nie kopiert). Solche Clips werden nicht über /uploads/ ausgeliefert, sondern
über /api/sync/media/clip/{id}; Proxy/Waveform/Strip heißen nach der Clip-ID
(Dateinamen im Quellordner sind nicht eindeutig über Ordner hinweg).
"""

from __future__ import annotations

from pathlib import Path

from backend.core.config import UPLOAD_DIR


def ist_upload_datei(dateipfad: str | None) -> bool:
    if not dateipfad:
        return False
    try:
        Path(dateipfad).resolve().relative_to(UPLOAD_DIR.resolve())
        return True
    except (ValueError, OSError):
        return False


def clip_stem(clip) -> str:
    """Basisname für abgeleitete Dateien (Proxy/Waveform/Strip).

    Upload-Kopien heißen schon nach der Clip-UUID (Datei = {id}.{ext}) → Stem = Dateiname.
    Referenzierte Originale (Take-Clips, Medien-Ordner-Import) → Clip-UUID, sonst kollidieren
    gleichnamige Dateien aus verschiedenen Ordnern."""
    if getattr(clip, "take_id", None) or not ist_upload_datei(clip.dateipfad):
        return str(clip.id)
    return Path(clip.dateipfad).stem


def clip_video_url(clip) -> str | None:
    if not clip.dateipfad:
        return None
    if getattr(clip, "take_id", None) or not ist_upload_datei(clip.dateipfad):
        return f"/api/sync/media/clip/{clip.id}"
    return f"/uploads/{Path(clip.dateipfad).name}"


AUDIO_ENDUNGEN_UPLOAD = {".mp3", ".wav", ".m4a", ".aac", ".flac", ".aif", ".aiff", ".ogg", ".oga", ".opus", ".caf", ".bwf"}


def medientyp(dateipfad: str | None) -> str:
    """"audio" für reine Audiodateien (nach Endung), sonst "video"."""
    if not dateipfad:
        return "video"
    return "audio" if Path(dateipfad).suffix.lower() in AUDIO_ENDUNGEN_UPLOAD else "video"


def proxy_dateiname(clip) -> str:
    """Audio-Clips bekommen einen AAC-Proxy (.m4a), Video-Clips den H.264-Proxy (.mp4)."""
    return f"{clip_stem(clip)}_proxy.m4a" if medientyp(clip.dateipfad) == "audio" else f"{clip_stem(clip)}_proxy.mp4"


def medienart(clip) -> str:
    """"audio" | "video" | "av" — aus dem Ingestion-Etikett (hat_bild/hat_ton); vor der Analyse
    aus der Dateiendung geschätzt (Audiodatei → audio, sonst av als Annahme "hat wohl Ton")."""
    hb, ht = getattr(clip, "hat_bild", None), getattr(clip, "hat_ton", None)
    if hb is None and ht is None:
        return "audio" if medientyp(clip.dateipfad) == "audio" else "av"
    if not hb:
        return "audio"
    return "av" if ht else "video"
