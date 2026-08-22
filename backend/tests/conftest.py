"""Pytest-Konfiguration für die Sync-Tests.

Aufruf (aus dem Repo-Root):   backend/.venv/bin/python -m pytest backend/tests -q

- Korpus-Tests brauchen das Referenz-Volume (`CINASSIST_KORPUS`, Default
  /Volumes/DSCVR/DOKUMENTEN/SHORTCUT 24) — sonst `skip`.
- DB-Tests laufen gegen eine EIGENE Datenbank `cinassist_test` (nie gegen `cinassist`);
  ohne erreichbares Postgres → `skip`.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Test-DB VOR dem ersten Import von backend.core.* setzen (Engine wird beim Import gebaut).
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://cinassist:cinassist@localhost:5432/cinassist_test")
# Derivate der Tests nicht in den Projekt-Ordner schreiben.
os.environ.setdefault("CINASSIST_DATA_DIR", str(Path(os.environ.get("TMPDIR", "/tmp")) / "cinassist_test_data"))

KORPUS = Path(os.environ.get("CINASSIST_KORPUS", "/Volumes/DSCVR/DOKUMENTEN/SHORTCUT 24"))
VIDEO_DIR = KORPUS / "ROHMAT_VIDEO"
AUDIO_DIR = KORPUS / "ROHMAT_AUDIO" / "11-17-23"


def korpus_vorhanden() -> bool:
    return (VIDEO_DIR / "PPRM23_S004_S003_T001.MOV").exists() and (AUDIO_DIR / "+SZENE4-3-002.WAV").exists()


korpus = pytest.mark.skipif(not korpus_vorhanden(), reason="Referenz-Korpus (SanDisk) nicht gemountet")


def _db_erreichbar() -> bool:
    try:
        import sqlalchemy
        from backend.core.config import DATABASE_URL_SYNC
        eng = sqlalchemy.create_engine(DATABASE_URL_SYNC)
        with eng.connect() as c:
            c.execute(sqlalchemy.text("SELECT 1"))
        return True
    except Exception:
        return False


db = pytest.mark.skipif(not _db_erreichbar(), reason="Postgres (cinassist_test) nicht erreichbar")


@pytest.fixture(scope="session")
def db_session():
    """Sync-Session gegen cinassist_test mit frischem Schema."""
    import asyncio
    from backend.core.database import init_db, SyncSessionLocal, Base, sync_engine
    Base.metadata.drop_all(sync_engine)
    asyncio.run(init_db())
    s = SyncSessionLocal()
    yield s
    s.close()
