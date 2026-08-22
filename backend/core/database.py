"""CinAssist — Datenbank (PostgreSQL + SQLAlchemy)"""

import uuid
from datetime import datetime

from sqlalchemy import (
    Column, String, Float, Integer, BigInteger, Text, DateTime, Date, Boolean, JSON,
    ForeignKey, Enum as SAEnum, create_engine, Index,
)
from sqlalchemy.dialects.postgresql import UUID, ARRAY
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase, relationship, sessionmaker, backref

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
    dateipfad = Column(String(1024), nullable=False)          # Pfad in /uploads/ ODER (take_id gesetzt) Original per Referenz
    quelle = Column(String(1), nullable=False)                # "A" oder "B"
    dauer = Column(Float, nullable=True)                      # Sekunden
    aufloesung = Column(String(20), nullable=True)            # z.B. "1920x1080"
    bildrate = Column(Float, nullable=True)                   # FPS
    codec = Column(String(50), nullable=True)
    dateigroesse = Column(BigInteger, nullable=True)          # Bytes (ProRes > 2 GB → BigInteger)
    erstellt_am = Column(DateTime, default=datetime.utcnow)
    status = Column(String(20), default="hochgeladen")        # hochgeladen, analysiert, fehler
    # Vom Sprachmodell bei Bedarf erzeugte Zusammenfassung, zwischengespeichert.
    # Structure : {"thema": str, "narration": str, "visuell": str,
    # "ambiance": str, "genre": str, "personen": [str, ...],
    # "generated_at": iso8601, "model": str}
    synthese_json = Column(JSON, nullable=True)
    # Sync-Modell: ein Clip ist eine dünne Schicht über einem Take (Video-Asset + 0..n Audios).
    # NULL = klassischer Upload (Datei liegt kopiert in /uploads/). Gesetzt = Referenz auf das
    # Original im Import-Ordner (dateipfad absolut, KEINE Kopie) + verknüpfter Ton fürs Whisper.
    take_id = Column(UUID(as_uuid=True), ForeignKey("takes.id", ondelete="SET NULL"), nullable=True)
    # Medien-Ordner (Bin) im Medien-Panel; NULL = Wurzel.
    ordner_id = Column(UUID(as_uuid=True), ForeignKey("medien_ordner.id", ondelete="SET NULL"), nullable=True)
    # Medienart-Etikett (von der Ingestion gesetzt): hat_bild = Videospur vorhanden;
    # hat_ton = brauchbarer Ton (Kameraspur mit echtem Ton, verknüpftes WAV oder reine Audiodatei).
    # NULL = noch nicht analysiert. API leitet daraus medienart ∈ {video, audio, av} ab.
    hat_bild = Column(Boolean, nullable=True)
    hat_ton = Column(Boolean, nullable=True)

    szenen = relationship("Szene", back_populates="clip", cascade="all, delete-orphan")
    jobs = relationship("Job", back_populates="clip", cascade="all, delete-orphan")
    take = relationship("Take", back_populates="clips", foreign_keys=[take_id])
    ordner = relationship("MedienOrdner", back_populates="clips", foreign_keys=[ordner_id])


# ─── Medien-Ordner (Bins im Medien-Panel) ───────────────
class MedienOrdner(Base):
    """Hierarchischer Ordner (Bin) für Clips — wie in Resolve/Premiere. Kein Bezug zum Dateisystem,
    außer `quelle_pfad`, wenn der Ordner aus einem Ordner-Import entstanden ist."""
    __tablename__ = "medien_ordner"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(255), nullable=False)
    eltern_id = Column(UUID(as_uuid=True), ForeignKey("medien_ordner.id", ondelete="CASCADE"), nullable=True)
    quelle_pfad = Column(String(1024), nullable=True)         # Import-Ordner (Referenz), falls vorhanden
    erstellt_am = Column(DateTime, default=datetime.utcnow)

    clips = relationship("Clip", back_populates="ordner", foreign_keys="Clip.ordner_id")
    kinder = relationship("MedienOrdner", cascade="all, delete-orphan",
                          backref=backref("eltern", remote_side=[id]))


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
    label_manual = Column(String(100), nullable=True)         # "Anna", "Marc", von Hand gesetzt
    total_speaking_time = Column(Float, default=0.0)          # secondes cumulées
    segment_count = Column(Integer, default=0)


class SceneSpeaker(Base):
    """Verknüpfung: welche Stimme in welcher Szene vorkommt und wie lange."""
    __tablename__ = "scene_speakers"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    scene_id = Column(UUID(as_uuid=True), ForeignKey("szenen.id", ondelete="CASCADE"), nullable=False)
    speaker_id = Column(UUID(as_uuid=True), ForeignKey("speakers.id", ondelete="CASCADE"), nullable=False)
    speaking_time = Column(Float, default=0.0)                # Sprechzeit in dieser Szene, in Sekunden


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


# ═══ Sync-Modell (Audio/Video-Zuordnung bei der Ingestion) ═══════════════
# Regeln: Originale werden referenziert, nie kopiert (pfad absolut + fingerprint);
# jede Zuordnung trägt methode / konfidenz / begruendung; `unklar` blockiert die Analyse.

class OrdnerImport(Base):
    """Ein Scan eines Ordners (Video- oder Audio-Rohmaterial)."""
    __tablename__ = "ordner_importe"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    pfad = Column(String(1024), nullable=False)               # absoluter Ordnerpfad (Quell-Volume)
    typ = Column(String(5), nullable=False)                   # "video" | "audio"
    gescannt_am = Column(DateTime, default=datetime.utcnow)
    anzahl_dateien = Column(Integer, default=0)
    anzahl_ignoriert = Column(Integer, default=0)             # ._*, $RECYCLE.BIN, TRASH …
    volume_uuid = Column(String(64), nullable=True)           # diskutil Volume UUID (macOS)
    volume_root = Column(String(255), nullable=True)          # z. B. /Volumes/DSCVR
    status = Column(String(20), default="wartend")            # wartend, laeuft, fertig, fehler
    fehler = Column(Text, nullable=True)
    job_id = Column(UUID(as_uuid=True), nullable=True)

    assets = relationship("MediaAsset", back_populates="ordner_import")


class MediaAsset(Base):
    """Eine physische Mediendatei — referenziert, nie kopiert."""
    __tablename__ = "media_assets"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    typ = Column(String(5), nullable=False)                   # "video" | "audio"
    pfad = Column(String(1024), nullable=False)               # absolut, auf dem Quell-Volume
    dateiname = Column(String(255), nullable=False)
    dauer_s = Column(Float, nullable=True)
    sample_rate = Column(Integer, nullable=True)
    kanaele = Column(Integer, nullable=True)
    fps = Column(Float, nullable=True)
    codec = Column(String(50), nullable=True)
    dateigroesse = Column(BigInteger, nullable=True)
    tc_start = Column(String(11), nullable=True)              # "HH:MM:SS:FF" oder NULL
    tc_start_s = Column(Float, nullable=True)                 # Sekunden seit Mitternacht (Rechenbasis)
    tc_quelle = Column(String(10), nullable=False, default="keine")   # bwf|ixml|ltc|container|keine
    tc_rate = Column(String(10), nullable=True)               # "24/1"
    tc_flag = Column(String(3), nullable=True)                # NDF | DF
    ixml_json = Column(JSON, nullable=True)                   # roher iXML + bext-Auszug (Audio)
    fingerprint = Column(String(64), nullable=False)          # sha256(erste 4 MB + Größe) — Idempotenz
    ordner_import_id = Column(UUID(as_uuid=True), ForeignKey("ordner_importe.id", ondelete="SET NULL"), nullable=True)
    ltc_kanal = Column(Integer, nullable=True)                # Video: Kanal mit LTC
    scratch_kanal = Column(Integer, nullable=True)            # Video: erster nicht-stiller Nicht-LTC-Kanal
    record_kanal = Column(Integer, default=0)                 # Audio: Spur "Record" (Mix) sonst 0
    container_tc = Column(String(11), nullable=True)          # roher Container-Tag (auch wenn verworfen)
    szene = Column(Integer, nullable=True)                    # aus Name/iXML (nur Gruppierung/Warnung)
    plan = Column(Integer, nullable=True)
    prise = Column(Integer, nullable=True)
    unbekannte_markierung = Column(String(4), nullable=True)  # z. B. "+" — nie interpretiert
    datum = Column(Date, nullable=True)                       # Drehtag (iXML TAPE / Ordner / Name)
    warnungen = Column(JSON, nullable=True)                   # list[str]
    erstellt_am = Column(DateTime, default=datetime.utcnow)
    aktualisiert_am = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    ordner_import = relationship("OrdnerImport", back_populates="assets")

    __table_args__ = (
        Index("ix_media_assets_fingerprint", "fingerprint", unique=True),
        Index("ix_media_assets_typ_pfad", "typ", "pfad"),
    )


class Take(Base):
    """Arbeitseinheit: ein Video + 0..n Audios (oder Audio ohne Video → verwaist)."""
    __tablename__ = "takes"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    video_asset_id = Column(UUID(as_uuid=True), ForeignKey("media_assets.id", ondelete="CASCADE"), nullable=True)
    szene = Column(Integer, nullable=True)
    plan = Column(Integer, nullable=True)
    prise = Column(Integer, nullable=True)
    # sicher | plausibel | unklar | verwaist | manuell_bestaetigt | manuell_abgelehnt
    status = Column(String(20), nullable=False, default="unklar")
    warnungen = Column(JSON, nullable=True)                   # list[str]
    kandidaten_json = Column(JSON, nullable=True)             # bei unklar: [{audio_asset_id, video_asset_id, offset_s, ueberlappung, begruendung}]
    automatisch = Column(Boolean, default=True)               # False = manuell angelegt/verändert (Re-Run lässt es stehen)
    multicam_gruppe = Column(String(64), nullable=True)       # gleicher Ton auf parallel laufenden Kameras (Matcher)
    erstellt_am = Column(DateTime, default=datetime.utcnow)
    aktualisiert_am = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    video_asset = relationship("MediaAsset", foreign_keys=[video_asset_id])
    audio_links = relationship("TakeAudioLink", back_populates="take", cascade="all, delete-orphan",
                               order_by="TakeAudioLink.erstellt_am")
    clips = relationship("Clip", back_populates="take", foreign_keys="Clip.take_id")


class TakeAudioLink(Base):
    """Ein Audio, das an einen Take gebunden ist — immer mit Methode, Konfidenz, Begründung."""
    __tablename__ = "take_audio_links"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    take_id = Column(UUID(as_uuid=True), ForeignKey("takes.id", ondelete="CASCADE"), nullable=False)
    audio_asset_id = Column(UUID(as_uuid=True), ForeignKey("media_assets.id", ondelete="CASCADE"), nullable=False)
    offset_s = Column(Float, nullable=False, default=0.0)     # audio_start − video_start, signiert, ms-genau
    # timecode | waveform | klappe | dateiname | manuell | verwaist (Audio ohne Video, Offset bedeutungslos)
    methode = Column(String(12), nullable=False)
    konfidenz = Column(Float, nullable=False, default=0.0)    # 0..1
    begruendung = Column(Text, nullable=False, default="")
    kanal_fuer_transkription = Column(Integer, default=0)     # Spur "Record" sonst 0
    warnungen = Column(JSON, nullable=True)
    bestaetigt = Column(Boolean, default=False)               # manuell bestätigt (Offset/Zuordnung)
    erstellt_am = Column(DateTime, default=datetime.utcnow)

    take = relationship("Take", back_populates="audio_links")
    audio_asset = relationship("MediaAsset", foreign_keys=[audio_asset_id])

    __table_args__ = (
        Index("ix_take_audio_links_take", "take_id"),
        Index("ix_take_audio_links_audio", "audio_asset_id"),
    )


# ─── DB initialisieren ──────────────────────────────────
# ─────────────────────────────────────────────────────────────
# Kontext-Schicht (2026-08): Drehbuch → Take-/Szenen-/Story-Kontext → Schnittplan
# Konzept: backend/KONTEXT_TIMELINE_KONZEPT.md
# ─────────────────────────────────────────────────────────────

class Skript(Base):
    """Ein importiertes Drehbuch (PDF/TXT/Fountain). Genau eins ist `aktiv`."""
    __tablename__ = "skripte"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(255), nullable=False)
    titel = Column(String(255), nullable=True)
    sprache = Column(String(8), nullable=True)                 # Sprache des Skripts (en/de/…)
    ziel_sprache = Column(String(8), nullable=True)            # Sprache des Drehs (Transkripte), z. B. de
    quelle_pfad = Column(String(1024), nullable=True)
    roh_text = Column(Text, nullable=True)
    aktiv = Column(Boolean, default=True)
    status = Column(String(20), default="importiert")          # importiert | uebersetzt | fehler
    erstellt_am = Column(DateTime, default=datetime.utcnow)

    szenen = relationship("SkriptSzene", back_populates="skript", cascade="all, delete-orphan",
                          order_by="SkriptSzene.reihenfolge")


class SkriptSzene(Base):
    __tablename__ = "skript_szenen"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    skript_id = Column(UUID(as_uuid=True), ForeignKey("skripte.id", ondelete="CASCADE"), nullable=False)
    nummer = Column(String(16), nullable=False)                # "1", "2", … wie im Skript
    reihenfolge = Column(Integer, nullable=False)
    ueberschrift = Column(String(255), nullable=True)          # INT. ORPHEUS’S LIVING ROOM – MORNING
    innen_aussen = Column(String(8), nullable=True)            # INT | EXT
    ort = Column(String(255), nullable=True)
    tageszeit = Column(String(64), nullable=True)
    figuren = Column(JSON, nullable=True)                      # ["ORPHEUS", "EURYDICE"]
    zusammenfassung = Column(Text, nullable=True)              # LLM, kurz, mit Belegen
    zusammenfassung_json = Column(JSON, nullable=True)         # beats/figuren/ort/stimmung (Skript-Sicht)

    skript = relationship("Skript", back_populates="szenen")
    zeilen = relationship("SkriptZeile", back_populates="szene", cascade="all, delete-orphan",
                          order_by="SkriptZeile.nr")


class SkriptZeile(Base):
    """Eine Skriptzeile: Dialog (figur + text) oder Aktion/Regieanweisung (figur=None)."""
    __tablename__ = "skript_zeilen"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    szene_id = Column(UUID(as_uuid=True), ForeignKey("skript_szenen.id", ondelete="CASCADE"), nullable=False)
    nr = Column(Integer, nullable=False)
    art = Column(String(12), nullable=False)                   # dialog | aktion | uebergang
    figur = Column(String(64), nullable=True)
    regie = Column(String(255), nullable=True)                 # "(to Eurydice)"
    text = Column(Text, nullable=False)                        # Originalsprache
    text_ziel = Column(Text, nullable=True)                    # Übersetzung in die Drehsprache (LLM, editierbar)
    text_ziel_quelle = Column(String(16), nullable=True)       # llm | manuell

    szene = relationship("SkriptSzene", back_populates="zeilen")


class TakeKontext(Base):
    """L2 — was ein Take (Clip) ist: Klappe, Spiel vs. Produktion, Dialogzeilen mit Skript-Zuordnung."""
    __tablename__ = "take_kontext"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    clip_id = Column(UUID(as_uuid=True), ForeignKey("clips.id", ondelete="CASCADE"), nullable=False, unique=True)
    skript_szene_id = Column(UUID(as_uuid=True), ForeignKey("skript_szenen.id", ondelete="SET NULL"), nullable=True)
    slate_szene = Column(String(32), nullable=True)            # "5.2.1" (Sprech-Klappe) — Skript-Szene = erster Teil
    slate_take = Column(Integer, nullable=True)
    slate_quelle = Column(String(16), nullable=True)           # audio | dateiname | manuell | keine
    slate_konflikt = Column(Boolean, default=False)            # Sprech-Klappe ≠ Dateiname
    einstellung = Column(String(32), nullable=True)            # Setup innerhalb der Szene ("2.1", "2.2", "5.2.1")
    spiel_start_s = Column(Float, nullable=True)
    spiel_ende_s = Column(Float, nullable=True)
    ng = Column(JSON, nullable=True)                           # {"abbruch": bool, "kurz": bool, "gruende": [...]}
    zeilen = Column(JSON, nullable=True)                       # [{start,end,sprecher,text,art,skript_zeile_id,score}]
    abdeckung = Column(Float, nullable=True)                   # Anteil der Skript-Dialogzeilen der Szene, die der Take deckt
    bildverlauf = Column(JSON, nullable=True)                  # [{t, beschreibung, personen}]
    bewertung = Column(String(12), nullable=True)              # manuell: circled | ok | ng
    notiz = Column(Text, nullable=True)
    aktionen = Column(JSON, nullable=True)                     # Skript-Aktionen im Bild: {aktion_nr: {spans:[[a,b]], ja:n, frames:m, clip_sim:x}}
    gesichter = Column(JSON, nullable=True)                    # {cluster_id: {anteil, frames, spans:[[a,b]]}} — wer ist wann im Bild
    takt = Column(JSON, nullable=True)                         # Beat-Segmentierung des Takes (monoton): [{beat, start, end, belege:[...], staerke}]
    aktualisiert_am = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class GesichtsCluster(Base):
    """Eine erkannte Person (Gesichts-Cluster über den Korpus) mit Skript-/Film-Namen."""
    __tablename__ = "gesichts_cluster"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    skript_id = Column(UUID(as_uuid=True), ForeignKey("skripte.id", ondelete="CASCADE"), nullable=True)
    idx = Column(Integer, nullable=False)                      # Index des Clusters im Lauf
    anzahl = Column(Integer, default=0)                        # Gesichter im Cluster
    takes = Column(Integer, default=0)                         # Takes mit diesem Gesicht
    name_skript = Column(String(64), nullable=True)            # ORPHEUS
    name_film = Column(String(64), nullable=True)              # Ophelia
    score = Column(Float, nullable=True)                       # Übereinstimmung Präsenz Skript↔Bild
    manuell = Column(Boolean, default=False)
    thumb_pfad = Column(String(512), nullable=True)
    szenen_anteil = Column(JSON, nullable=True)                # {szene: anteil}
    erstellt_am = Column(DateTime, default=datetime.utcnow)


class SzenenKontext(Base):
    """L3 — pro Skript-Szene: was wurde gedreht, was passiert, Coverage, Take-Ranking."""
    __tablename__ = "szenen_kontext"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    skript_szene_id = Column(UUID(as_uuid=True), ForeignKey("skript_szenen.id", ondelete="CASCADE"), nullable=False, unique=True)
    zusammenfassung = Column(Text, nullable=True)
    beats = Column(JSON, nullable=True)                        # [{nr, text, skript_zeilen:[nr], takes:[clip_id]}]
    figuren = Column(JSON, nullable=True)                      # [{skript:"ORPHEUS", film:"Ophelia", beleg}]
    coverage = Column(JSON, nullable=True)                     # {einstellung: {clip_id: abdeckung}}
    take_ranking = Column(JSON, nullable=True)                 # [{clip_id, einstellung, score, gruende:[...]}]
    belege = Column(JSON, nullable=True)
    unsicher = Column(JSON, nullable=True)
    aktions_coverage = Column(JSON, nullable=True)             # {aktion_nr: {status: gedreht|unsicher|fehlt, takes:[{clip_id, spans}]}}
    takt = Column(JSON, nullable=True)                         # deterministische Beats der Szene (Skript-Takt): [{nr, zeilen, aktionen, dialog_nr, figur, text}]
    manuell_geprueft = Column(Boolean, default=False)
    aktualisiert_am = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class StoryKontext(Base):
    """L4 — eine Zeile pro Skript: Figuren (Skript↔Film-Namen), Arc, Motive, Inserts."""
    __tablename__ = "story_kontext"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    skript_id = Column(UUID(as_uuid=True), ForeignKey("skripte.id", ondelete="CASCADE"), nullable=False, unique=True)
    zusammenfassung = Column(Text, nullable=True)
    figuren = Column(JSON, nullable=True)                      # [{skript, film, rolle, belege}]
    szenenfolge = Column(JSON, nullable=True)                  # ["1","2",…]
    arc = Column(JSON, nullable=True)                          # [{szene, wendepunkt}]
    motive = Column(JSON, nullable=True)
    unsicher = Column(JSON, nullable=True)
    aktualisiert_am = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class Schnittplan(Base):
    """L5 — ein generierter Rohschnitt: Einträge (Take-Segmente) in Skript-Reihenfolge, mit Begründung."""
    __tablename__ = "schnittplaene"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    skript_id = Column(UUID(as_uuid=True), ForeignKey("skripte.id", ondelete="CASCADE"), nullable=True)
    name = Column(String(255), nullable=False)
    parameter = Column(JSON, nullable=True)                    # Stil/Regeln
    eintraege = Column(JSON, nullable=True)                    # [{nr, szene, clip_id, in_s, out_s, dauer, zeilen:[...], grund, beleg}]
    statistik = Column(JSON, nullable=True)
    erstellt_am = Column(DateTime, default=datetime.utcnow)


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
            # Sync-Modell (2026-08): Clip → Take-Referenz + BigInteger für > 2-GB-Originale
            "ALTER TABLE clips ADD COLUMN IF NOT EXISTS take_id UUID REFERENCES takes(id) ON DELETE SET NULL",
            "ALTER TABLE clips ALTER COLUMN dateigroesse TYPE BIGINT",
            "ALTER TABLE clips ALTER COLUMN dateipfad TYPE VARCHAR(1024)",
            "ALTER TABLE clips ADD COLUMN IF NOT EXISTS ordner_id UUID REFERENCES medien_ordner(id) ON DELETE SET NULL",
            "ALTER TABLE clips ADD COLUMN IF NOT EXISTS hat_bild BOOLEAN",
            "ALTER TABLE clips ADD COLUMN IF NOT EXISTS hat_ton BOOLEAN",
            "ALTER TABLE takes ADD COLUMN IF NOT EXISTS multicam_gruppe VARCHAR(64)",
            "ALTER TABLE take_kontext ADD COLUMN IF NOT EXISTS aktionen JSON",
            "ALTER TABLE szenen_kontext ADD COLUMN IF NOT EXISTS aktions_coverage JSON",
            "ALTER TABLE take_kontext ADD COLUMN IF NOT EXISTS gesichter JSON",
            "ALTER TABLE take_kontext ADD COLUMN IF NOT EXISTS takt JSON",
            "ALTER TABLE szenen_kontext ADD COLUMN IF NOT EXISTS takt JSON",
        ]
        for sql in migrations:
            try:
                await conn.execute(text(sql))
            except Exception:
                pass


async def get_db() -> AsyncSession:
    async with AsyncSessionLocal() as session:
        yield session
