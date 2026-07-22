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
    # Synthèse contextuelle générée par LLM à la demande (cache).
    # Structure : {"thema": str, "narration": str, "visuell": str,
    # "ambiance": str, "genre": str, "personen": [str, ...],
    # "generated_at": iso8601, "model": str}
    synthese_json = Column(JSON, nullable=True)

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
    beschreibung = Column(Text, nullable=True)                # Moondream-Vision-Beschreibung
    transkription = Column(Text, nullable=True)               # Whisper-Text für dieses Segment
    transkription_json = Column(JSON, nullable=True)          # Whisper mit Timestamps
    analyse_visuelle = Column(JSON, nullable=True)            # Pixel-Analyse: luminosité, température, contraste, mouvement, énergie

    # Face detection (Vague 1.3)
    face_count = Column(Integer, nullable=True, default=0)    # Nombre de visages détectés
    framing = Column(String(30), nullable=True)               # extreme_closeup|closeup|medium|wide_with_person|wide_no_person
    faces_data = Column(JSON, nullable=True)                  # bboxes + area_ratios

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


# ─── Speaker + SceneSpeaker (Vague 1.2 - Diarization) ──
class Speaker(Base):
    __tablename__ = "speakers"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    clip_id = Column(UUID(as_uuid=True), ForeignKey("clips.id", ondelete="CASCADE"), nullable=False)
    label_auto = Column(String(50), nullable=False)          # "SPEAKER_00", "SPEAKER_01"...
    label_manual = Column(String(100), nullable=True)         # "Anna", "Marc" — set par user
    total_speaking_time = Column(Float, default=0.0)          # secondes cumulées
    segment_count = Column(Integer, default=0)


class SceneSpeaker(Base):
    """Association many-to-many : quelle voix apparaît dans quelle scène, combien de temps."""
    __tablename__ = "scene_speakers"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    scene_id = Column(UUID(as_uuid=True), ForeignKey("szenen.id", ondelete="CASCADE"), nullable=False)
    speaker_id = Column(UUID(as_uuid=True), ForeignKey("speakers.id", ondelete="CASCADE"), nullable=False)
    speaking_time = Column(Float, default=0.0)                # secondes de parole dans cette scène


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
            "ALTER TABLE szenen ADD COLUMN IF NOT EXISTS face_count INTEGER DEFAULT 0",
            "ALTER TABLE szenen ADD COLUMN IF NOT EXISTS framing VARCHAR(30)",
            "ALTER TABLE szenen ADD COLUMN IF NOT EXISTS faces_data JSONB DEFAULT NULL",
        ]
        for sql in migrations:
            try:
                await conn.execute(text(sql))
            except Exception:
                pass


async def get_db() -> AsyncSession:
    async with AsyncSessionLocal() as session:
        yield session
