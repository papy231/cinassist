"""
Backfill LLaVA-Visualbeschreibungen für bereits analysierte Clips.

Hintergrund:
    Die ursprüngliche Phase-2-Pipeline ließ LLaMA3 jede Szene beschreiben
    NUR auf Basis des transkribierten Dialogs + Dauer. Bei Musikvideos
    oder Material ohne aussagekräftigen Dialog erfindet LLaMA3 narrative
    Interpretationen ("ein Mann verteidigt sich gegen Vorwürfe…"), die
    nicht im Bild sind.

Dieses Skript:
    1. Lädt alle Szenen eines oder mehrerer Clips aus der DB
    2. Schickt für jede Szene den thumbnail_pfad an LLaVA via Ollama
    3. LLaVA beschreibt FAKTISCH, was IM BILD sichtbar ist (keine Story)
    4. Schreibt die neue Beschreibung in szenen.beschreibung zurück

Ausführen:
    python -m backend.tools.backfill_llava_descriptions <clip_id_1> [<clip_id_2> ...]

oder ohne Argumente: backfillt ALLE analysierten Clips.
"""

from __future__ import annotations

import base64
import sys
from pathlib import Path

import httpx
from sqlalchemy import select

# Bootstrap-Imports für Standalone-Ausführung
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from backend.core.config import OLLAMA_BASE_URL  # noqa: E402
from backend.core.database import SyncSessionLocal, Clip, Szene  # noqa: E402

LLAVA_MODEL = "llava:7b"

VISION_PROMPT = (
    "Describe what is visually present in this image in ONE concise German sentence. "
    "Be strictly factual: name the subject, the framing (close-up / medium / wide), "
    "the setting, lighting and any prominent objects. "
    "Do NOT interpret emotions, story or intentions. "
    "Do NOT invent dialogue or events. "
    "Start with 'Plan' or 'Bild'."
)


def _beschreibe_thumbnail(thumb_pfad: str) -> str | None:
    """Sendet das Thumbnail an LLaVA und liefert die deutsche Beschreibung."""
    p = Path(thumb_pfad)
    if not p.exists():
        print(f"   ⚠ Thumbnail fehlt: {thumb_pfad}")
        return None
    with open(p, "rb") as f:
        img_b64 = base64.b64encode(f.read()).decode("utf-8")
    try:
        resp = httpx.post(
            f"{OLLAMA_BASE_URL}/api/generate",
            json={
                "model": LLAVA_MODEL,
                "prompt": VISION_PROMPT,
                "images": [img_b64],
                "stream": False,
                "options": {"temperature": 0.2, "num_predict": 100},
            },
            timeout=180.0,
        )
        resp.raise_for_status()
        text = resp.json().get("response", "").strip()
        # Normalisierung statt naivem split(".")[0]:
        # behandelt Bullet-Listen, Mehrzeilen-Output, "Bild N:" Präfix
        from backend.workers.ingest import _normalize_llava
        return _normalize_llava(text) if text else None
    except Exception as exc:
        print(f"   ⚠ LLaVA-Fehler: {exc}")
        return None


def main() -> None:
    target_clip_ids = sys.argv[1:]
    db = SyncSessionLocal()
    try:
        if target_clip_ids:
            print(f"🎯 Spezifische Clip-IDs : {len(target_clip_ids)}")
            clips_q = db.execute(select(Clip).where(Clip.id.in_(target_clip_ids)))
        else:
            print("🎯 Alle analysierten Clips")
            clips_q = db.execute(select(Clip).where(Clip.status == "analysiert"))
        clips = clips_q.scalars().all()
        if not clips:
            print("Keine Clips zum Backfill gefunden.")
            return

        for clip in clips:
            print(f"\n📹 {clip.dateiname}  (id={clip.id})")
            szenen_q = db.execute(
                select(Szene)
                .where(Szene.clip_id == clip.id)
                .order_by(Szene.szenen_nr)
            )
            szenen = szenen_q.scalars().all()
            print(f"   {len(szenen)} Szene(n)")

            for sz in szenen:
                if not sz.thumbnail_pfad:
                    print(f"   Szene {sz.szenen_nr}: kein Thumbnail, übersprungen")
                    continue
                print(f"   Szene {sz.szenen_nr}: → LLaVA …", end=" ", flush=True)
                neue_beschreibung = _beschreibe_thumbnail(sz.thumbnail_pfad)
                if neue_beschreibung:
                    sz.beschreibung = neue_beschreibung
                    print(f"✓")
                    print(f"      {neue_beschreibung}")
                else:
                    print("✗")
            db.commit()
            print(f"   ✓ Clip '{clip.dateiname}' aktualisiert")

        print("\n✅ Backfill abgeschlossen.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
