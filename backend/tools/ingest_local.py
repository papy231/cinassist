"""Ingestion synchrone de fichiers vidéo locaux (bypass Celery/Redis).

Bildet nach, was der Endpunkt POST /api/clips/upload tut, also das Kopieren nach uploads/
und das Anlegen von Clip und Auftrag, und führt die Aufnahmestrecke unmittelbar aus
(`ingestion_pipeline.apply`). Nützlich, wenn Redis örtlich nicht erreichbar ist,
etwa hinter einem SSH-Tunnel, oder um einen Prüfbestand zu füllen.

Mehrfach ausführbar: Dateien, deren dateiname bereits "analysiert" ist, werden übersprungen.

Usage:
    backend/.venv/bin/python -m backend.tools.ingest_local test_rushes/*.mp4
    backend/.venv/bin/python -m backend.tools.ingest_local --quelle B datei.mov
"""

from __future__ import annotations

import argparse
import shutil
import sys
import uuid
from pathlib import Path

from sqlalchemy import select

from backend.core.config import UPLOAD_DIR
from backend.core.database import SyncSessionLocal, Clip, Job
from backend.workers.ingest import ingestion_pipeline


ERLAUBTE_ENDUNGEN = {".mp4", ".mov", ".avi", ".mkv", ".webm"}


def ingest_file(pfad: Path, quelle: str) -> dict:
    endung = pfad.suffix.lower()
    if endung not in ERLAUBTE_ENDUNGEN:
        return {"datei": str(pfad), "status": "skip", "grund": f"endung {endung}"}

    dateiname = pfad.name
    db = SyncSessionLocal()
    try:
        vorhandene = list(db.execute(select(Clip).where(Clip.dateiname == dateiname)).scalars().all())
        already = [c for c in vorhandene if c.status == "analysiert"]
        if already:
            return {"datei": dateiname, "status": "skip", "grund": "bereits analysiert",
                    "clip_id": str(already[0].id)}

        clip_id = str(uuid.uuid4())
        ziel_pfad = UPLOAD_DIR / f"{clip_id}{endung}"
        shutil.copy2(pfad, ziel_pfad)
        dateigroesse = ziel_pfad.stat().st_size

        clip = Clip(id=clip_id, dateiname=dateiname, dateipfad=str(ziel_pfad),
                    quelle=quelle, dateigroesse=dateigroesse, status="hochgeladen")
        db.add(clip)
        job_id = str(uuid.uuid4())
        job = Job(id=job_id, typ="ingestion", clip_id=clip_id, status="wartend",
                  fortschritt=0, nachricht="Lokale Ingestion (eager)")
        db.add(job)
        db.commit()
    finally:
        db.close()

    # Strecke im gleichen Ablauf; bind=True wird richtig behandelt, ein Vermittler ist nicht nötig.
    result = ingestion_pipeline.apply(args=(clip_id, job_id))
    ok = result.successful()
    return {
        "datei": dateiname,
        "status": "ok" if ok else "fehler",
        "clip_id": clip_id,
        "ergebnis": (result.result if ok else str(result.result)),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("dateien", nargs="+", help="Videodateien (Globs vom Shell expandiert)")
    ap.add_argument("--quelle", default="A", choices=["A", "B"])
    args = ap.parse_args()

    paths = [Path(p) for p in args.dateien]
    print(f"Ingestion von {len(paths)} Datei(en) [quelle={args.quelle}]\n")
    for p in paths:
        if not p.exists():
            print(f"  ✗ {p} — nicht gefunden")
            continue
        print(f"  → {p.name} …", flush=True)
        r = ingest_file(p, args.quelle)
        mark = {"ok": "✓", "skip": "–", "fehler": "✗"}.get(r["status"], "?")
        extra = r.get("grund") or (r.get("ergebnis") if r["status"] == "ok" else "")
        print(f"  {mark} {r['datei']}: {r['status']} {extra}\n", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
