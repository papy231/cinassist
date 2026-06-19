"""CinAssist — Datenbank (PostgreSQL + SQLAlchemy)"""

import uuid
from datetime import datetime

from sqlalchemy import (
    Column, String, Float, Integer, Text, DateTime, Boolean, JSON,
    ForeignKey, Enum as SAEnum, create_engine,
)
from sqlalchemy.dialects.postgresql import UUID, ARRAY
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase, relationship, sessionmaker

from backend.core.config import DATABASE_URL, DATABASE_URL_SYNC

# ─── Async Engine ────────────────────────────────────────
async_engine = create_async_engine(DATABASE_URL, echo=False, pool_size=10)
AsyncSessionLocal = async_sessionmaker(async_engine, class_=AsyncSession, expire_on_commit=False)

# ─── Sync Engine (für Celery Worker) ────────────────────
sync_engine = create_engine(DATABASE_URL_SYNC, echo=False, pool_size=5)
SyncSessionLocal = sessionmaker(bind=sync_engine)


class Base(DeclarativeBase):
    pass


# ─── Clip / Video ────────────────────────────────────────
class Clip(Base):
    __tablename__ = "clips"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    dateiname = Column(String(255), nullable=False)           # Originaler Dateiname
    dateipfad = Column(String(512), nullable=False)           # Pfad in /uploads/
    quelle = Column(String(1), nullable=False)                # "A" oder "B"
    dauer = Column(Float, nullable=True)                      # Sekunden
    aufloesung = Column(String(20), nullable=True)            # z.B. "1920x1080"
    bildrate = Column(Float, nullable=True)                   # FPS
    codec = Column(String(50), nullable=True)
    dateigroesse = Column(Integer, nullable=True)             # Bytes
    erstellt_am = Column(DateTime, default=datetime.utcnow)
    status = Column(String(20), default="hochgeladen")        # hochgeladen, analysiert, fehler

    szenen = relationship("Szene", back_populates="clip", cascade="all, delete-orphan")
    jobs = relationship("Job", back_populates="clip", cascade="all, delete-orphan")


# ─── Szene (aus PySceneDetect) ───────────────────────────
class Szene(Base):
    __tablename__ = "szenen"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    clip_id = Column(UUID(as_uuid=True), ForeignKey("clips.id", ondelete="CASCADE"), nullable=False)
    szenen_nr = Column(Integer, nullable=False)
    start_zeit = Column(Float, nullable=False)                # Sekunden
    end_zeit = Column(Float, nullable=False)
    dauer = Column(Float, nullable=False)
    thumbnail_frame = Column(Integer, nullable=True)          # Frame-Nummer
    thumbnail_pfad = Column(String(512), nullable=True)

    # KI-Analyse
    clip_embedding = Column(ARRAY(Float), nullable=True)      # CLIP-Vektor (512-dim)
    beschreibung = Column(Text, nullable=True)                # LLaMA3-Beschreibung
    transkription = Column(Text, nullable=True)               # Whisper-Text für dieses Segment
    transkription_json = Column(JSON, nullable=True)          # Whisper mit Timestamps
    analyse_visuelle = Column(JSON, nullable=True)            # Pixel-Analyse: luminosité, température, contraste, mouvement, énergie

    clip = relationship("Clip", back_populates="szenen")


# ─── Job (Celery Task Tracking) ─────────────────────────
class Job(Base):
    __tablename__ = "jobs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    typ = Column(String(50), nullable=False)                  # "ingestion", "extend", "export"
    clip_id = Column(UUID(as_uuid=True), ForeignKey("clips.id", ondelete="CASCADE"), nullable=True)
    celery_task_id = Column(String(255), nullable=True)
    status = Column(String(20), default="wartend")            # wartend, laeuft, fertig, fehler
    fortschritt = Column(Integer, default=0)                  # 0-100
    nachricht = Column(Text, nullable=True)
    ergebnis = Column(JSON, nullable=True)
    erstellt_am = Column(DateTime, default=datetime.utcnow)
    aktualisiert_am = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    clip = relationship("Clip", back_populates="jobs")


# ─── Timeline ───────────────────────────────────────────
class Timeline(Base):
    __tablename__ = "timelines"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(255), default="Unbenannt")
    stil = Column(String(50), nullable=True)
    prompt = Column(Text, nullable=True)
    daten = Column(JSON, nullable=False)                      # Komplette Timeline-JSON
    gesamtdauer = Column(Float, nullable=True)
    erstellt_am = Column(DateTime, default=datetime.utcnow)


# ─── DB initialisieren ──────────────────────────────────
async def init_db():
    async with async_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # Migrations: add columns that may not exist in older DBs
        from sqlalchemy import text
        migrations = [
            "ALTER TABLE szenen ADD COLUMN IF NOT EXISTS analyse_visuelle JSONB DEFAULT NULL",
        ]
        for sql in migrations:
            try:
                await conn.execute(text(sql))
            except Exception:
                pass


async def get_db() -> AsyncSession:
    async with AsyncSessionLocal() as session:
        yield session
