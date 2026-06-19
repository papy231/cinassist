"""
CinAssist — KI-Schnitt API  (v3 — Multi-Provider + Enhanced Analysis)

POST /api/ai/cut

Provider-Support:
  auto        — Automatisch (Claude → OpenAI → Gemini → Ollama, je nach API-Keys)
  ollama      — Lokal via Ollama (Standard, kein API-Key nötig)
  claude      — Anthropic Claude 3.5 Sonnet / Opus (CLAUDE_API_KEY erforderlich)
  openai      — OpenAI GPT-4o / o1 (OPENAI_API_KEY erforderlich)
  gemini      — Google Gemini 1.5 Pro (GEMINI_API_KEY erforderlich)

Stile:
  kinematisch  — Narrativer Bogen: Ouverture → Steigende Handlung → Höhepunkt → Cloture
  dokumentar   — Chronologisch, Dialog-fokussiert, ruhige Schnitte
  werbespot    — Kurze, energetische Schnitte, visueller Impact
  kurzfilm     — Ausgeglichenes Tempo, narrativer Fokus
  social_media — Sehr kurze Szenen, maximale Energie, kein Dialog

Verbesserungen v3:
  - Multi-Provider LLM (Claude, OpenAI, Gemini, Ollama)
  - Audio-aware Scene Subdivision (Whisper Pause Detection)
  - Histogram-basierte visuelle Diversität (ohne CLIP)
  - Long/Short Rhythmus-Regulierung im kinematischen Bogen
  - Bugfix: remaining-Schleife korrekt
  - Angereicherte LLM-Prompts (Chain-of-Thought für Claude/GPT-4)
  - Qualitäts-Schwelle + Max-Szenen Filterung
"""

import json
import logging
import math
import os
import uuid
from pathlib import Path
from typing import Literal

import httpx
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.config import (
    OLLAMA_BASE_URL, OLLAMA_MODEL,
    CLAUDE_API_KEY, CLAUDE_MODEL,
    OPENAI_API_KEY, OPENAI_MODEL,
    GEMINI_API_KEY, GEMINI_MODEL,
)
from backend.core.database import get_db, Clip, Szene, Timeline

logger = logging.getLogger("cinassist.ai")
router = APIRouter(prefix="/api/ai", tags=["KI"])


# ═══════════════════════════════════════════════════════════
# CLIP ZERO-SHOT PROMPT EMBEDDINGS
# ═══════════════════════════════════════════════════════════
# Diese Embeddings ersetzen die alte heuristische Energie-Formel
# (0.40·kontrast + 0.35·mouvement + ...). Stattdessen wird ein Szenen-Score
# direkt aus der Kosinus-Ähnlichkeit zwischen dem Bild-Embedding und
# vordefinierten Text-Prompts berechnet (Radford et al., ICML 2021).
#
# Vorteile gegenüber der alten Formel:
#   • Keine arbitären Koeffizienten — alle Werte aus dem gemeinsamen
#     Vektorraum von CLIP
#   • Vollständig nachvollziehbar — die Prompts sind explizit und auditierbar
#   • Reproduzierbar — deterministisch bei festem Input
#
# Die Datei wird einmalig erzeugt via:
#     python -m backend.tools.build_prompt_embeddings
# ───────────────────────────────────────────────────────────

_PROMPT_EMB_FILE = Path(__file__).resolve().parent.parent / "data" / "prompt_embeddings.json"
_PROMPT_EMBS: dict[str, list[list[float]]] = {}

try:
    if _PROMPT_EMB_FILE.exists():
        _data = json.loads(_PROMPT_EMB_FILE.read_text())
        _PROMPT_EMBS = _data.get("embeddings", {})
        logger.info(
            f"CLIP-Prompt-Embeddings geladen: {list(_PROMPT_EMBS.keys())} "
            f"({sum(len(v) for v in _PROMPT_EMBS.values())} Vektoren)"
        )
    else:
        logger.warning(
            f"Prompt-Embeddings nicht gefunden ({_PROMPT_EMB_FILE}). "
            f"Fallback auf heuristische Energie-Formel. "
            f"Ausführen: python -m backend.tools.build_prompt_embeddings"
        )
except Exception as exc:
    logger.warning(f"Konnte Prompt-Embeddings nicht laden: {exc}")
    _PROMPT_EMBS = {}


# ═══════════════════════════════════════════════════════════
# CLIP TEXT ENCODER — Live-Encoding von User-Prompts
# ═══════════════════════════════════════════════════════════
# Für prompt-getriebenen Schnitt encodiert das System den eingegebenen
# Text per CLIP Text-Encoder zur Laufzeit (lazy: erst beim ersten Aufruf
# geladen). Das ist die Kern-Funktionalität von CLIP — die Möglichkeit,
# beliebige Texte und Bilder im gleichen 512-dimensionalen Vektorraum
# zu vergleichen (Radford et al., 2021).
#
# Workflow:
#   user_prompt → CLIP text encoder → 512-dim embedding (L2-normalisiert)
#   → für jede Szene: relevance = cos(scene_emb, prompt_emb)
#   → Top-K-Szenen mit höchster Relevanz auswählen
# ───────────────────────────────────────────────────────────

_clip_text_model = None      # type: ignore[var-annotated]
_clip_tokenizer = None       # type: ignore[var-annotated]
_clip_device = "cpu"


def _get_clip_text_encoder():
    """
    Lazy-load des CLIP-Modells beim ersten Aufruf.
    Das Modell wird im Modul-Scope gecached, sodass weitere Aufrufe
    nahezu kostenlos sind. Erstes Laden ~3-5s.
    """
    global _clip_text_model, _clip_tokenizer, _clip_device
    if _clip_text_model is None:
        try:
            import torch
            import open_clip
            _clip_device = "mps" if torch.backends.mps.is_available() else "cpu"
            model, _, _ = open_clip.create_model_and_transforms(
                "ViT-B/32", pretrained="openai", device=_clip_device
            )
            model.eval()
            _clip_text_model = model
            _clip_tokenizer = open_clip.get_tokenizer("ViT-B/32")
            logger.info(f"CLIP-Text-Encoder geladen (device={_clip_device})")
        except Exception as exc:
            logger.error(f"CLIP-Text-Encoder konnte nicht geladen werden: {exc}")
            return None, None
    return _clip_text_model, _clip_tokenizer


def _encode_prompt(prompt: str) -> list[float] | None:
    """
    Encodiert einen Text-Prompt mit dem CLIP-Text-Encoder.

    Rückgabe:
      512-dim L2-normalisierter Vektor (Liste von float)
      oder None, falls CLIP nicht verfügbar.
    """
    import torch
    model, tokenizer = _get_clip_text_encoder()
    if model is None or tokenizer is None:
        return None
    try:
        tokens = tokenizer([prompt]).to(_clip_device)
        with torch.no_grad():
            emb = model.encode_text(tokens)
            emb = emb / emb.norm(dim=-1, keepdim=True)
        return emb.cpu().squeeze().tolist()
    except Exception as exc:
        logger.warning(f"Prompt-Encoding fehlgeschlagen für '{prompt[:60]}…': {exc}")
        return None


# ═══════════════════════════════════════════════════════════
# BEAT-DETECTION via librosa (rhythm-aware editing)
# ═══════════════════════════════════════════════════════════
# Wozu?
#   Bei Musikmaterial ist der visuelle Cut-Rhythmus nur halb so wichtig wie
#   der musikalische. Ein content-aware aber rhythm-blind Schnitt wirkt
#   immer ein bisschen "off-time". Mit librosa.beat.beat_track extrahieren
#   wir die Beat-Zeiten und snappen Schnittgrenzen darauf.
#
# Methode:
#   librosa.beat.beat_track basiert auf onset-strength + dynamic programming
#   (Ellis, 2007 · "Beat Tracking by Dynamic Programming"). Cache pro Clip,
#   überlebt nicht den Prozess-Neustart (akzeptabel für Demo).
# ───────────────────────────────────────────────────────────

_BEATS_CACHE: dict[str, dict] = {}  # clip_id -> { "tempo": float, "beats": [seconds...] }


def _detect_beats(clip_id: str, audio_path: str) -> dict:
    """
    Liefert {"tempo": bpm, "beats": [t in seconds]} für ein Audio.
    Bei Fehler oder fehlendem librosa: leeres Resultat.
    """
    if clip_id in _BEATS_CACHE:
        return _BEATS_CACHE[clip_id]
    try:
        import librosa
        # 22050 Hz mono = librosa-Standard, schnell und ausreichend für Beats
        y, sr = librosa.load(audio_path, sr=22050, mono=True)
        tempo, beat_frames = librosa.beat.beat_track(y=y, sr=sr)
        beat_times = librosa.frames_to_time(beat_frames, sr=sr).tolist()
        # tempo kann je nach librosa-Version array oder scalar sein
        try:
            tempo_val = float(tempo) if hasattr(tempo, "__len__") is False else float(tempo[0])
        except Exception:
            tempo_val = float(tempo) if not hasattr(tempo, "tolist") else float(tempo.tolist()[0])
        out = {"tempo": tempo_val, "beats": beat_times}
        _BEATS_CACHE[clip_id] = out
        logger.info(f"Beats detected for {clip_id}: tempo={tempo_val:.1f} BPM, {len(beat_times)} beats")
        return out
    except Exception as exc:
        logger.warning(f"Beat-Detection fehlgeschlagen für {clip_id}: {exc}")
        return {"tempo": 0.0, "beats": []}


def _next_beat_at_or_after(beats: list[float], t: float) -> float | None:
    """Erster Beat >= t. Liefert None, falls keiner gefunden."""
    for b in beats:
        if b >= t:
            return b
    return None


# ═══════════════════════════════════════════════════════════
# STIL-KONFIGURATIONEN
# ═══════════════════════════════════════════════════════════

STIL_CONFIG: dict[str, dict] = {
    "kinematisch": {
        "min_dauer": 1.5,
        "max_dauer": 12.0,
        "tempo": 0.6,                  # 0=langsam, 1=schnell
        "dialog_gewicht": 0.3,
        "energie_schwelle": 0.45,      # Mindestenergieniveau für Action-Szenen
        "arc": True,                   # Narrativer Bogen aktiviert
        "uebergaenge": {               # Automatische Übergänge zwischen Clips
            "default":   {"type": "dissolve", "dauer": 0.5},
            "ouverture": {"type": "fade",     "dauer": 0.8},  # langsames Einblenden
            "action":    {"type": "dissolve", "dauer": 0.3},  # flüchtiger Schnitt
            "climax":    {"type": "dissolve", "dauer": 0.2},  # sehr schnell
            "cloture":   {"type": "fadeblack", "dauer": 0.9}, # Ausblenden zum Schwarz
        },
    },
    "dokumentar": {
        "min_dauer": 3.0,
        "max_dauer": 40.0,
        "tempo": 0.25,
        "dialog_gewicht": 0.85,
        "energie_schwelle": 0.2,
        "arc": False,
        "uebergaenge": {
            "default": {"type": "dissolve", "dauer": 0.6},
        },
    },
    "werbespot": {
        "min_dauer": 0.8,
        "max_dauer": 5.0,
        "tempo": 0.9,
        "dialog_gewicht": 0.1,
        "energie_schwelle": 0.5,
        "arc": False,
        "uebergaenge": {
            "default": {"type": "wipeleft", "dauer": 0.15},
        },
    },
    "kurzfilm": {
        "min_dauer": 2.0,
        "max_dauer": 20.0,
        "tempo": 0.45,
        "dialog_gewicht": 0.55,
        "energie_schwelle": 0.35,
        "arc": True,
        "uebergaenge": {
            "default": {"type": "dissolve", "dauer": 0.5},
        },
    },
    "social_media": {
        "min_dauer": 0.5,
        "max_dauer": 3.5,
        "tempo": 0.95,
        "dialog_gewicht": 0.05,
        "energie_schwelle": 0.55,
        "arc": False,
        "uebergaenge": None,  # Harte Schnitte
    },
}

# Backward compat aliases
STIL_CONFIG["dokumentarisch"] = STIL_CONFIG["dokumentar"]
STIL_CONFIG["schnell"] = STIL_CONFIG["werbespot"]


class AiCutRequest(BaseModel):
    stil: str = "kinematisch"
    prompt: str | None = None
    clip_ids: list[str]
    provider: Literal["auto", "ollama", "claude", "openai", "gemini"] = Field(
        "ollama", description="LLM-Provider für die Verfeinerung"
    )
    llm_modell: str | None = Field(
        None, description="Modell überschreiben, z.B. 'claude-3-5-sonnet-20241022'"
    )
    # LLM-Verfeinerung ist standardmäßig DEAKTIVIERT, um Reproduzierbarkeit zu
    # garantieren. Sie kann optional über das UI aktiviert werden, aber das
    # Resultat hängt dann vom externen Modell ab (Claude/GPT/Ollama).
    llm_aktiviert: bool = Field(False, description="LLM-Verfeinerung aktivieren/deaktivieren")
    max_szenen: int | None = Field(None, ge=1, description="Maximale Szenenanzahl in der Timeline")
    qualitaet_schwelle: float = Field(
        0.0, ge=0.0, le=1.0, description="Mindest-Energie-Schwelle (0=keine Filterung)"
    )
    # Übergänge (Crossfade, Wipe, etc.) sind standardmäßig DEAKTIVIERT.
    # Grund: HTML5 Video mit Dual-Video-Crossfade verursacht sichtbare
    # Stutter und Re-Loads im Browser. Harte Schnitte sind technisch sauberer
    # und entsprechen dem Standard im professionellen Filmschnitt. Bei
    # Export (FFmpeg-side) wären Übergänge sauber renderbar — das wäre eine
    # zukünftige Erweiterung.
    mit_uebergaengen: bool = Field(False, description="Crossfade/Wipe-Übergänge zwischen Szenen einfügen")
    # Beat-Sync (librosa): wenn aktiviert, werden Schnittgrenzen auf die nächsten
    # Beats des ersten Master-Clips gesnappt. Macht Musikclips rhythmisch tight,
    # statt visuell-aber-rhythmisch-zufällig.
    beat_sync: bool = Field(False, description="Schnittgrenzen auf Musik-Beats ausrichten (librosa)")
    beat_pro_segment: int = Field(4, ge=1, le=16, description="Anzahl Beats pro Segment, wenn beat_sync aktiv")


# ═══════════════════════════════════════════════════════════
# VISUELLE HILFS-FUNKTIONEN
# ═══════════════════════════════════════════════════════════

def _cosine_similarity(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def _ist_nullvektor(e: list[float] | None) -> bool:
    return not e or all(v == 0.0 for v in e)


def _szene_energie(szene: dict) -> float:
    """
    Szenen-Score basierend auf CLIP Zero-Shot-Klassifikation.

    Anstelle der alten heuristischen Formel (0.40·kontrast + 0.35·mouvement +
    0.15·luminosite + 0.10·schaerfe), wird der Score nun aus der Kosinus-
    Ähnlichkeit zwischen dem CLIP-Bild-Embedding der Szene und zwei Sätzen
    von textuellen Prompts ('action' vs 'calm') berechnet.

    Formel:
        score = 0.5 + 2.0 · (avg_cos(emb, action_prompts) − avg_cos(emb, calm_prompts))
        score = clamp(score, 0, 1)

    Vorteile:
      • Keine willkürlichen Koeffizienten
      • Alle Bewertungswerte aus dem gemeinsamen Bild-Text-Raum von CLIP
      • Vollständig reproduzierbar (deterministisch)

    Referenz: Radford et al. (2021), "Learning Transferable Visual Models
    From Natural Language Supervision", ICML 2021, OpenAI.

    Fallback (in absteigender Reihenfolge):
      1. CLIP zero-shot (primär)
      2. Heuristische Formel (falls Prompt-Embeddings fehlen)
      3. Dauer-Heuristik (falls auch das Bild-Embedding fehlt)
    """
    emb = szene.get("embedding")
    has_emb = not _ist_nullvektor(emb)

    # ── 1. PRIMÄR: CLIP Zero-Shot Action vs. Calm ──────────────
    if has_emb and "action" in _PROMPT_EMBS and "calm" in _PROMPT_EMBS:
        action_prompts = _PROMPT_EMBS["action"]
        calm_prompts   = _PROMPT_EMBS["calm"]
        if action_prompts and calm_prompts:
            avg_action = sum(_cosine_similarity(emb, p) for p in action_prompts) / len(action_prompts)
            avg_calm   = sum(_cosine_similarity(emb, p) for p in calm_prompts)   / len(calm_prompts)
            raw = avg_action - avg_calm           # typischerweise [-0.10, +0.15]
            score = 0.5 + 2.0 * raw               # rescale auf ~[0, 1]
            return round(max(0.0, min(1.0, score)), 3)

    # ── 2. FALLBACK: heuristische Formel (alte Variante) ──────
    av = szene.get("analyse_visuelle") or {}
    if av.get("energie") is not None:
        return float(av["energie"])
    if av:
        kontrast   = float(av.get("kontrast",   0.5))
        mouvement  = float(av.get("mouvement",  0.5))
        luminosite = float(av.get("luminosite", 0.5))
        return round(min(1.0, kontrast * 0.45 + mouvement * 0.35 + luminosite * 0.20), 3)

    # ── 3. LETZTES FALLBACK: Dauer-Heuristik ────────────────────
    dauer = szene["dauer"]
    if dauer < 2.5:    return 0.78
    elif dauer < 5.0:  return 0.60
    elif dauer < 10.0: return 0.42
    else:              return 0.25


def _szene_interessantheit(szene: dict) -> float:
    """
    Sekundärer Score: Qualität / "Interessantheit" einer Szene.

    Gleiche Methode wie `_szene_energie`, aber gegen die Prompts
    'interesting' (gut komponiert, dramatisch beleuchtet, …) versus
    'boring' (verschwommen, leer, dunkel, …).

    Wird in der Qualitäts-Filterung verwendet (statt der alten
    `qualitaet_schwelle`, die auf der PIL-Schärfe basierte).

    Score in [0, 1] : 1 = sehr interessant, 0 = sehr langweilig.
    """
    emb = szene.get("embedding")
    if _ist_nullvektor(emb):
        return 0.5
    if "interesting" not in _PROMPT_EMBS or "boring" not in _PROMPT_EMBS:
        return 0.5

    interesting = _PROMPT_EMBS["interesting"]
    boring      = _PROMPT_EMBS["boring"]
    if not interesting or not boring:
        return 0.5

    avg_int  = sum(_cosine_similarity(emb, p) for p in interesting) / len(interesting)
    avg_bor  = sum(_cosine_similarity(emb, p) for p in boring)      / len(boring)
    raw = avg_int - avg_bor
    score = 0.5 + 2.0 * raw
    return round(max(0.0, min(1.0, score)), 3)


def _histogram_distance(av_a: dict, av_b: dict) -> float:
    """
    Visueller Abstand zweier Szenen anhand der PIL-Metriken.
    Proxy für Farb-Histogramm-Abstand — funktioniert ohne CLIP.

    Kombiniert:
    - Helligkeitsdifferenz (luminosite)
    - Kontrastdifferenz
    - Farbtemperatur-Distanz (warm/neutral/kalt)
    """
    if not av_a or not av_b:
        return 0.5

    lum_dist  = abs(float(av_a.get("luminosite", 0.5)) - float(av_b.get("luminosite", 0.5)))
    kon_dist  = abs(float(av_a.get("kontrast",   0.5)) - float(av_b.get("kontrast",   0.5)))

    temp_num  = {"warm": 1.0, "neutral": 0.5, "kalt": 0.0}
    temp_a = temp_num.get(av_a.get("temperature", "neutral"), 0.5)
    temp_b = temp_num.get(av_b.get("temperature", "neutral"), 0.5)
    temp_dist = abs(temp_a - temp_b)

    # Bewegungs-Differenz (hohe Bewegung ↔ ruhige Szene = guter Kontrast)
    mouv_dist = abs(float(av_a.get("mouvement", 0.5)) - float(av_b.get("mouvement", 0.5)))

    return round(
        lum_dist  * 0.30
        + kon_dist  * 0.25
        + temp_dist * 0.30
        + mouv_dist * 0.15,
        3,
    )


def _visual_diversity(a: dict, b: dict) -> float:
    """
    Visueller Abstand zweier Szenen (0=identisch, 1=maximal verschieden).

    Priorität:
    1. CLIP Embeddings (Cosinus-Abstand — am genauesten)
    2. PIL-Farbmetriken via Histogramm-Proxy
    3. Clip-ID Heuristik
    """
    ea, eb = a.get("embedding"), b.get("embedding")
    if not _ist_nullvektor(ea) and not _ist_nullvektor(eb):
        return 1.0 - _cosine_similarity(ea, eb)

    av_a = a.get("analyse_visuelle") or {}
    av_b = b.get("analyse_visuelle") or {}
    if av_a and av_b:
        return _histogram_distance(av_a, av_b)

    # Fallback: unterschiedlicher Clip = guter Kontrast
    return 0.75 if a["clip_id"] != b["clip_id"] else 0.30


# ═══════════════════════════════════════════════════════════
# SZENEN-KLASSIFIKATION (Rollen im kinematischen Bogen)
# ═══════════════════════════════════════════════════════════

ROLLE_OUVERTURE  = "ouverture"   # Starke Eröffnung
ROLLE_ACTION     = "action"      # Dynamische Handlung
ROLLE_TRANSITION = "transition"  # Verbindend, Dialog, Atempause
ROLLE_CLIMAX     = "climax"      # Höhepunkt
ROLLE_CLOTURE    = "cloture"     # Ruhiger Abschluss

# Narrativer Videofilm-Typ (A-Roll / B-Roll)
NAR_A_ROLL      = "a_roll"       # Hauptaufnahme — Sprecher/Interview (primär)
NAR_B_ROLL      = "b_roll"       # Schnittbild   — Umgebung/Aktion (sekundär)
NAR_ESTABLISHING = "establishing" # Establishing Shot — Ort einführen


def _detecte_role_narratif(szene: dict) -> str:
    """
    Klassifiziert eine Szene als A-Roll, B-Roll oder Establishing Shot.

    A-Roll   — Primäres Filmmaterial: Person spricht, Interview, Voice-over-Synchron.
               Signale: Transkription vorhanden + geringe Bewegung (statische Kamera)
    B-Roll   — Schnittbild: Umgebungsaufnahmen, Action, Details ohne Sprache.
               Signale: kein Dialog + hohe oder mittlere Bewegung
    Establishing — Überblicksaufnahme am Beginn einer Szene (weit, hell, ruhig).
               Signale: hohe Luminosität + geringe Bewegung + lange Einstellung

    Diese Klassifikation wird als `_typ_narratif` an der Szene gespeichert und
    in `_rolle_kinematisch()` sowie `_pick_best()` verwendet.
    """
    av         = szene.get("analyse_visuelle") or {}
    mouvement  = float(av.get("mouvement",  0.5))
    luminosite = float(av.get("luminosite", 0.5))
    kontrast   = float(av.get("kontrast",   0.45))
    schaerfe   = float(av.get("schaerfe",   0.5))
    hat_dialog = bool((szene.get("transkription") or "").strip())
    dauer      = szene.get("dauer", 3.0)

    # ── A-Roll: Dialog + relativ ruhige Kamera ────────────
    # Typisches Interview/Sprecherkopf: Sprecher ist statisch,
    # Kamera hält still → mouvement niedrig
    if hat_dialog and mouvement < 0.65:
        return NAR_A_ROLL

    # ── Establishing: Kein Dialog, breite helle Aufnahme ──
    # Keine Sprache, Szene ist lang genug, Bild ist hell/breit
    if (not hat_dialog
            and luminosite > 0.52
            and mouvement < 0.38
            and dauer >= 3.5
            and kontrast < 0.60):
        return NAR_ESTABLISHING

    # ── B-Roll: alles übrige ──────────────────────────────
    return NAR_B_ROLL


# ═══════════════════════════════════════════════════════════
# SZENEN-TEILUNG (Audio-bewusst)
# ═══════════════════════════════════════════════════════════

def _find_natural_cut_points(szene: dict, n_chunks: int) -> list[float]:
    """
    Sucht natürliche Schnitt-Punkte basierend auf Whisper-Timestamps.

    Strategie:
    1. Sprachpausen > 300ms identifizieren (Lücken zwischen Segmenten)
    2. Für jeden idealen Schnittpunkt die nächste Pause suchen (max 1.5s Abstand)
    3. Falls keine Transkription: gleichmäßige Aufteilung

    Vorteil: Kein Wort wird mitten in einer Silbe geschnitten.
    """
    start  = szene["start_zeit"]
    end    = szene["end_zeit"]
    dauer  = end - start
    step   = dauer / n_chunks

    ideal_cuts = [start + i * step for i in range(1, n_chunks)]

    transkription_json = szene.get("transkription_json") or []
    if not transkription_json:
        return ideal_cuts

    # Pausen zwischen Sprach-Segmenten finden
    segs = sorted(transkription_json, key=lambda s: s.get("start", 0))
    pauses: list[float] = []
    for i in range(len(segs) - 1):
        gap_start = segs[i].get("end", 0)
        gap_end   = segs[i + 1].get("start", 0)
        if gap_end - gap_start > 0.3:
            midpoint = (gap_start + gap_end) / 2
            if start < midpoint < end:
                pauses.append(midpoint)

    if not pauses:
        return ideal_cuts

    # Jeden idealen Schnittpunkt zur nächsten Pause verschieben (wenn ≤ 1.5s entfernt)
    adjusted: list[float] = []
    for ideal in ideal_cuts:
        nearby = [p for p in pauses if abs(p - ideal) <= 1.5]
        if nearby:
            adjusted.append(min(nearby, key=lambda p: abs(p - ideal)))
        else:
            adjusted.append(ideal)

    return adjusted


def _subdivise_scenes(szenen: list[dict], stil: str) -> list[dict]:
    """
    Teilt lange Szenen in kürzere Sub-Szenen auf (v3 — Audio-aware).

    Ziel-Dauer pro Stil:
      kinematisch:  ~4s   werbespot:    ~2.5s
      kurzfilm:     ~5s   social_media: ~1.5s
      dokumentar:   keine Teilung

    Energie-Kurve: Sinus-Profil (niedrig → hoch → niedrig pro Szene).
    Schnitt-Punkte: bevorzugt Sprachpausen aus Whisper-Timestamps.
    """
    target = {
        "kinematisch":    4.0,
        "kurzfilm":       5.0,
        "werbespot":      2.5,
        "social_media":   1.5,
        "schnell":        2.0,
        "dokumentar":    60.0,
        "dokumentarisch":60.0,
    }.get(stil, 4.0)

    result = []
    for sz in szenen:
        dauer = sz["dauer"]
        if dauer <= target * 1.8:
            result.append(sz)
            continue

        n = max(2, min(6, round(dauer / target)))
        clip_dauer  = sz.get("_clip_dauer") or dauer
        base_energie = sz.get("_energie", 0.5)

        # Sinus-Energie-Profil: Anfang/Ende ruhig, Mitte energetisch
        if n == 2:
            factors = [0.60, 0.90]
        elif n == 3:
            factors = [0.40, 1.00, 0.40]
        else:
            factors = [
                0.30 + 0.70 * math.sin(i / (n - 1) * math.pi)
                for i in range(n)
            ]

        # Audio-bewusste Schnittpunkte ermitteln
        cut_points = _find_natural_cut_points(sz, n)
        boundaries = [sz["start_zeit"]] + cut_points + [sz["end_zeit"]]

        # Auf genau n+1 Grenzen bringen
        while len(boundaries) > n + 1:
            boundaries.pop(-2)
        while len(boundaries) < n + 1:
            mid = (boundaries[-2] + boundaries[-1]) / 2
            boundaries.insert(-1, mid)

        for i in range(n):
            sub_start = boundaries[i]
            sub_end   = boundaries[i + 1]
            sub_dauer = sub_end - sub_start
            if sub_dauer < 0.25:
                continue

            result.append({
                **sz,
                "_uid":       f"{sz['_uid']}-s{i}",
                "start_zeit": round(sub_start, 3),
                "end_zeit":   round(sub_end,   3),
                "dauer":      round(sub_dauer, 3),
                "_energie":   round(base_energie * (factors[i] if i < len(factors) else 1.0), 3),
                "_pos_pct":   round(sub_start / max(1.0, clip_dauer), 3),
            })

    return result


def _rolle_kinematisch(szene: dict, energie: float, pos_pct: float) -> str:
    """
    Weist einer Szene ihre Rolle im kinematischen Bogen zu  (v4 — A/B-Roll-aware).

    pos_pct:     Relative Position im Quell-Clip (0 = Anfang, 1 = Ende)
    typ_narratif: A-Roll / B-Roll / Establishing beeinflusst die Rolle

    A-Roll  → bevorzugt TRANSITION (Dialog-Szenen passen als Bindeglied)
    Establishing → bevorzugt OUVERTURE (weite Einstellungen eröffnen eine Sequenz)
    B-Roll  → bevorzugt ACTION / CLIMAX (dynamische Schnittbilder)
    """
    dauer = szene["dauer"]
    av = szene.get("analyse_visuelle") or {}
    mouvement  = float(av.get("mouvement",  0.5))
    kontrast   = float(av.get("kontrast",   0.5))
    temperatur = av.get("temperature", "neutral")
    hat_dialog = bool((szene.get("transkription") or "").strip())
    typ_narratif = szene.get("_typ_narratif", NAR_B_ROLL)

    # ── Establishing Shot: Klarer Öffner oder ruhiger Cloture ──
    if typ_narratif == NAR_ESTABLISHING:
        if pos_pct <= 0.30:
            return ROLLE_OUVERTURE
        if pos_pct >= 0.70:
            return ROLLE_CLOTURE
        return ROLLE_TRANSITION  # in der Mitte: Etablierung als Atempause

    # ── A-Roll: Dialog-Szenen → Transition oder Cloture ────
    if typ_narratif == NAR_A_ROLL:
        if energie <= 0.45 and pos_pct >= 0.60 and temperatur in ("warm", "neutral"):
            return ROLLE_CLOTURE
        # Dialog-Szene mit hoher Energie (z.B. hitzige Diskussion) → Climax
        if energie >= 0.68 and mouvement >= 0.50:
            return ROLLE_CLIMAX
        return ROLLE_TRANSITION

    # ── B-Roll: Standard kinematische Regeln ────────────────

    # Ouverture: visuell stark, früh im Clip, keine lange Rede
    if (energie >= 0.55 and 2.0 <= dauer <= 10.0
            and pos_pct <= 0.40 and not hat_dialog):
        return ROLLE_OUVERTURE

    # Cloture: ruhig, warm/neutral, Ende des Clips, etwas länger
    if (energie <= 0.48 and dauer >= 3.0 and pos_pct >= 0.60
            and temperatur in ("warm", "neutral")):
        return ROLLE_CLOTURE

    # Climax: hohe Energie + hohe Bewegung, mittlere Länge
    if energie >= 0.70 and mouvement >= 0.55 and 1.5 <= dauer <= 9.0:
        return ROLLE_CLIMAX

    # Action: kurz, bewegt oder kontrastreich
    if dauer <= 5.5 and (mouvement >= 0.45 or kontrast >= 0.50):
        return ROLLE_ACTION

    # Transition: hat Dialog oder mittellang
    if hat_dialog or 4.0 <= dauer <= 14.0:
        return ROLLE_TRANSITION

    return ROLLE_ACTION


# ═══════════════════════════════════════════════════════════
# KINEMATISCHER BOGEN-ALGORITHMUS
# ═══════════════════════════════════════════════════════════

def _sequence_score(sequence: list[dict]) -> float:
    """
    Bewertet eine vollständige Sequenz für Beam Search.

    Kriterien (gewichtet):
      ① Mittlere Energie entlang der Sequenz               × 0.25
      ② Mittlere visuelle Diversität zwischen Nachbarn      × 0.35
      ③ A-Roll/B-Roll Alternierungsrate                     × 0.25
      ④ Clip-Wechsel-Rate (verschiedene Quell-Clips)        × 0.15
    """
    if not sequence:
        return 0.0
    n = len(sequence)

    # ① Energie
    energie_score = sum(s.get("_energie", 0.5) for s in sequence) / n

    # ② Visuelle Diversität
    if n >= 2:
        diversitaet = sum(
            _visual_diversity(sequence[i], sequence[i + 1])
            for i in range(n - 1)
        ) / (n - 1)
    else:
        diversitaet = 0.5

    # ③ A/B-Roll Alternierung
    if n >= 2:
        wechsel_ab = sum(
            1 for i in range(n - 1)
            if sequence[i].get("_typ_narratif") != sequence[i + 1].get("_typ_narratif")
        ) / (n - 1)
    else:
        wechsel_ab = 0.5

    # ④ Clip-Wechsel-Rate
    if n >= 2:
        clip_wechsel = sum(
            1 for i in range(n - 1)
            if sequence[i]["clip_id"] != sequence[i + 1]["clip_id"]
        ) / (n - 1)
    else:
        clip_wechsel = 0.5

    return (
        energie_score  * 0.20
        + diversitaet  * 0.30
        + wechsel_ab   * 0.20
        + clip_wechsel * 0.30   # erhöht: Clip-Alternierung ist kritisch
    )


def _beam_fill(
    start_arc: list[dict],
    candidates: list[dict],
    beam_width: int = 3,
) -> list[dict]:
    """
    Beam-Search-Füllung: erweitert start_arc durch alle Kandidaten.

    Hält beam_width konkurrierende Sequenzen und gebt die beste zurück.
    Komplexität: O(beam_width × |candidates|²) — gut für bis zu ~30 Szenen.

    Args:
        start_arc:   Bereits geordnete Szenen (fixiert — werden nicht verschoben)
        candidates:  Noch einzuordnende Szenen (beliebige Reihenfolge)
        beam_width:  Anzahl paralleler Beam-Kandidaten (Standard: 3)

    Returns:
        Vollständige Sequenz (start_arc + bestmöglich geordnete candidates)
    """
    if not candidates:
        return start_arc

    # Beam: Liste von (Sequenz-so-weit, noch-nicht-platziert)
    beams: list[tuple[list[dict], list[dict]]] = [(list(start_arc), list(candidates))]

    while True:
        # Alle Beams bereits vollständig?
        if all(not remaining for _, remaining in beams):
            break

        next_beams: list[tuple[list[dict], list[dict]]] = []

        for seq, remaining in beams:
            if not remaining:
                next_beams.append((seq, remaining))
                continue

            prev = seq[-1] if seq else None
            prev_typ = (prev or {}).get("_typ_narratif", NAR_B_ROLL)
            prev_clip = (prev or {}).get("clip_id")

            # Bewerte alle Erweiterungs-Kandidaten
            scored: list[tuple[float, dict, list[dict]]] = []
            for i, cand in enumerate(remaining):
                new_seq = seq + [cand]
                new_rem = remaining[:i] + remaining[i + 1:]

                # Lokales Einschrittsscore (schnell)
                local = 0.0
                if prev:
                    local += _visual_diversity(prev, cand) * 0.35
                typ = cand.get("_typ_narratif", NAR_B_ROLL)
                if prev_typ == NAR_A_ROLL and typ != NAR_A_ROLL:
                    local += 0.25
                elif prev_typ == NAR_A_ROLL and typ == NAR_A_ROLL:
                    local -= 0.35
                # Clip-Alternierung: starke Gewichtung
                if cand.get("clip_id") != prev_clip:
                    local += 0.40   # starker Bonus für Clip-Wechsel
                else:
                    local -= 0.50   # starke Strafe für gleichen Clip
                local += cand.get("_energie", 0.5) * 0.10

                scored.append((local, cand, new_rem))

            scored.sort(key=lambda x: x[0], reverse=True)

            # Top-beam_width Erweitertungen übernehmen
            for sc, cand, new_rem in scored[:beam_width]:
                next_beams.append((seq + [cand], new_rem))

        # Beams auf beam_width beschränken (nach globalem Score)
        next_beams.sort(key=lambda x: _sequence_score(x[0]), reverse=True)
        beams = next_beams[:beam_width]

    # Bester Beam
    beams.sort(key=lambda x: _sequence_score(x[0]), reverse=True)
    return beams[0][0]


def _pick_best(
    pool: list[dict],
    used_ids: set[str],
    prev_scene: dict | None,
    avoid_clip_id: str | None,
) -> dict | None:
    """
    Wählt die beste Szene aus dem Pool  (v4 — A/B-Roll-aware).

    Scoring:
      + Visuelle Diversität zum Vorgänger
      - Bonus für Clip-Wechsel (avoid_clip_id)
      - Stark penalisiert: zwei aufeinanderfolgende A-Roll-Szenen
      + Bonus wenn vorherige Szene A-Roll und aktuelle B-Roll ist (Wechsel)
    """
    candidates = [s for s in pool if s["_uid"] not in used_ids]
    if not candidates:
        return None

    # Anderen Clip bevorzugen
    alt = [s for s in candidates if s["clip_id"] != avoid_clip_id]
    if alt:
        candidates = alt

    prev_typ = (prev_scene or {}).get("_typ_narratif", NAR_B_ROLL)

    def _score(s: dict) -> float:
        score = 0.0

        # Visuelle Diversität (0–1)
        if prev_scene:
            score += _visual_diversity(prev_scene, s) * 0.60

        # A-Roll/B-Roll Alternierungs-Bonus
        typ = s.get("_typ_narratif", NAR_B_ROLL)
        if prev_typ == NAR_A_ROLL and typ != NAR_A_ROLL:
            score += 0.30   # Bonus: Wechsel von A zu B raus aus dem Dialog
        elif prev_typ == NAR_A_ROLL and typ == NAR_A_ROLL:
            score -= 0.45   # Starke Strafe: zwei Interview-Szenen hintereinander

        # Energie fließt leicht ein (lieber energetischere Szenen bevorzugen)
        score += s.get("_energie", 0.5) * 0.10

        return score

    candidates.sort(key=_score, reverse=True)
    return candidates[0]


def _zwinge_narrativen_bogen(szenen: list[dict]) -> list[dict]:
    """
    Erzwingt eine dramatische Bogenform auf eine bereits ausgewählte
    Szenen-Menge — UNABHÄNGIG von der Quell-Reihenfolge.

    Struktur (Aristotelisch / Drei-Akt-Schema kompakt):
      1. Eröffnung:   niedrigste Energie (ruhiger Einstieg)
      2. Aufbau:      Szenen aufsteigend nach Energie
      3. Höhepunkt:   höchste Energie
      4. Auflösung:   eine zweite ruhige Szene am Ende (falls vorhanden)

    Diese Funktion ist orthogonal zum Kandidaten-Pool: sie ordnet NUR um.
    Sie wird NACH der Selektion (MMR + Multicam-Dedup) aufgerufen, sodass
    der Inhalt unverändert bleibt aber die zeitliche Reihenfolge eine
    erzählerische Logik bekommt.
    """
    if len(szenen) < 2:
        return list(szenen)

    def _score(s: dict) -> float:
        return s.get("_energie", 0.0)

    # Nach Energie sortieren
    by_energy = sorted(szenen, key=_score)

    if len(szenen) == 2:
        # Bei 2 Szenen: low-energy zuerst, high-energy danach
        return by_energy

    # Eröffnung: niedrigste Energie
    opening = by_energy[0]
    # Höhepunkt: höchste Energie
    climax = by_energy[-1]
    # Optionaler Ausklang: zweitniedrigste, falls genug Szenen
    closing: dict | None = None
    if len(szenen) >= 4:
        closing = by_energy[1]
    # Aufbau: mittlere Energien aufsteigend
    used_ids = {id(opening), id(climax)}
    if closing is not None:
        used_ids.add(id(closing))
    middle = [s for s in by_energy if id(s) not in used_ids]
    middle.sort(key=_score)  # aufsteigend zum Höhepunkt

    arc = [opening] + middle + [climax]
    if closing is not None:
        arc.append(closing)
    # Bogen-Rollen markieren (für Tooltip-Anzeige + Methodik-Belege)
    arc[0]["_rolle"] = ROLLE_OUVERTURE
    arc[-1]["_rolle"] = ROLLE_CLOTURE if closing is not None else ROLLE_CLIMAX
    if closing is not None and len(arc) >= 2:
        arc[-2]["_rolle"] = ROLLE_CLIMAX
    for s in arc[1:-1 if closing is None else -2]:
        # Alles dazwischen markieren als action/transition je nach Energie
        s["_rolle"] = ROLLE_ACTION if s.get("_energie", 0) >= 0.5 else ROLLE_TRANSITION
    return arc


def _baue_kinematischen_bogen(szenen: list[dict]) -> list[dict]:
    """
    Baut den kinematischen Bogen (v3):

    [1 Ouverture] →
    [N×25% Action / kurze Schnitte, visuell divers] →
    [N×20% Transition / Atempause, Dialog] →
    [N×25% Aufbau Energie] →
    [1–2 Climax] →
    [1 Cloture]

    Verbesserungen v3:
    - Bugfix: remaining-Schleife verkleinert sich korrekt
    - Rhythmus: niemals 2× selber Clip hintereinander (4 Passes)
    - Rhythmus: Long/Short-Alternierung (keine 3 langen Szenen in Folge)
    - Farbtemperatur-Bonus: warm↔kalt-Übergänge werden bevorzugt
    """
    if not szenen:
        return []

    used: set[str] = set()
    arc: list[dict] = []

    # ── Pool nach Rolle aufteilen ──────────────────────────
    by_rolle: dict[str, list[dict]] = {
        ROLLE_OUVERTURE:  [],
        ROLLE_ACTION:     [],
        ROLLE_TRANSITION: [],
        ROLLE_CLIMAX:     [],
        ROLLE_CLOTURE:    [],
    }
    for s in szenen:
        by_rolle[s["_rolle"]].append(s)

    for lst in by_rolle.values():
        lst.sort(key=lambda s: s["_energie"], reverse=True)

    n = len(szenen)

    def prev_clip() -> str | None:
        return arc[-1]["clip_id"] if arc else None

    def add(s: dict) -> None:
        arc.append(s)
        used.add(s["_uid"])

    def pick_from(*rollen: str, fallback_all: bool = True) -> dict | None:
        for r in rollen:
            s = _pick_best(by_rolle[r], used, arc[-1] if arc else None, prev_clip())
            if s:
                return s
        if fallback_all:
            return _pick_best(szenen, used, arc[-1] if arc else None, prev_clip())
        return None

    # ─── 1. Ouverture ─────────────────────────────────────
    s = pick_from(ROLLE_OUVERTURE, ROLLE_ACTION)
    if s:
        add(s)

    # ─── 2. Steigende Handlung (≈25%) ──────────────────────
    n_action1 = max(1, round(n * 0.25))
    for _ in range(n_action1):
        s = pick_from(ROLLE_ACTION, ROLLE_CLIMAX)
        if s:
            add(s)

    # ─── 3. Entwicklung / Atempause (≈20%) ────────────────
    n_trans = max(1, round(n * 0.20))
    for _ in range(n_trans):
        s = pick_from(ROLLE_TRANSITION, ROLLE_CLOTURE, ROLLE_ACTION)
        if s:
            add(s)

    # ─── 4. Aufbau Energie (≈25%) ─────────────────────────
    n_action2 = max(1, round(n * 0.25))
    for _ in range(n_action2):
        s = pick_from(ROLLE_ACTION, ROLLE_CLIMAX, ROLLE_OUVERTURE)
        if s:
            add(s)

    # ─── 5. Höhepunkt (1–2 Szenen) ────────────────────────
    n_climax = min(2, max(1, round(n * 0.10)))
    for _ in range(n_climax):
        s = pick_from(ROLLE_CLIMAX, ROLLE_ACTION)
        if s:
            add(s)

    # ─── 6. Verbleibende Szenen via Beam-Search einfügen ──
    # Statt gieriger Einzelauswahl: Beam-Width=3 findet die global
    # bessere Reihenfolge für alle noch nicht platzierten Szenen.
    remaining = [s for s in szenen if s["_uid"] not in used]
    remaining.sort(key=lambda s: s["_energie"], reverse=True)
    if remaining:
        arc = _beam_fill(arc, remaining, beam_width=3)
        used = {s["_uid"] for s in arc}

    # ─── 7. Cloture ans Ende ──────────────────────────────
    unused_cloture = [s for s in szenen
                      if s["_rolle"] == ROLLE_CLOTURE and s["_uid"] not in used]
    if unused_cloture:
        unused_cloture.sort(key=lambda s: s["_energie"])
        add(unused_cloture[0])
    elif arc:
        calme_idx = min(
            range(1, len(arc)),
            key=lambda i: arc[i]["_energie"],
            default=None,
        )
        if calme_idx is not None and calme_idx != len(arc) - 1:
            arc.append(arc.pop(calme_idx))

    # ─── 8. Clip-Wechsel Korrektur (4 Passes) ─────────────
    for _pass in range(4):
        improved = False
        for i in range(1, len(arc)):
            if arc[i]["clip_id"] != arc[i - 1]["clip_id"]:
                continue
            for j in range(i + 1, len(arc)):
                if arc[j]["clip_id"] != arc[i - 1]["clip_id"]:
                    arc.insert(i, arc.pop(j))
                    improved = True
                    break
        if not improved:
            break

    # ─── 9. Long/Short Rhythmus-Regulierung (NEU v3) ──────
    # Niemals 3 aufeinanderfolgende "lange" Szenen (> 6s)
    LONG_THRESHOLD  = 6.0
    SHORT_THRESHOLD = 4.0
    for _attempt in range(3):
        changed = False
        for i in range(1, len(arc) - 1):
            if (arc[i - 1]["dauer"] > LONG_THRESHOLD
                    and arc[i]["dauer"] > LONG_THRESHOLD
                    and i + 1 < len(arc)
                    and arc[i + 1]["dauer"] > LONG_THRESHOLD):
                # Suche weiter hinten eine kurze Szene und schiebe sie vor
                for j in range(i + 2, len(arc)):
                    if arc[j]["dauer"] < SHORT_THRESHOLD:
                        arc.insert(i + 1, arc.pop(j))
                        changed = True
                        break
        if not changed:
            break

    # ─── 10. A-Roll/B-Roll Alternierung (NEU v4) ──────────
    # Regel: Zwei aufeinanderfolgende A-Roll-Szenen werden durch die
    # nächstbeste B-Roll-Szene dazwischen aufgebrochen.
    # Verhindert lange Dialogblöcke: A-A → A-B-A
    for _attempt in range(4):
        changed = False
        for i in range(1, len(arc)):
            if (arc[i - 1].get("_typ_narratif") == NAR_A_ROLL
                    and arc[i].get("_typ_narratif") == NAR_A_ROLL):
                # Suche die nächste B-Roll-Szene weiter hinten
                for j in range(i + 1, len(arc)):
                    if arc[j].get("_typ_narratif") != NAR_A_ROLL:
                        arc.insert(i, arc.pop(j))
                        changed = True
                        break
        if not changed:
            break

    # ─── 11. Strikte Clip-Alternierung ────────────────────
    # Für 2 Clips: maximal 1 gleicher Clip hintereinander (ABABAB…)
    # Für 3+ Clips: maximal 2 gleiche Clips hintereinander
    clip_ids_uniq = list({s["clip_id"] for s in arc})
    MAX_CONSEC = 1 if len(clip_ids_uniq) <= 2 else 2
    for _attempt in range(6):
        changed = False
        for i in range(len(arc) - MAX_CONSEC):
            if all(arc[i + k]["clip_id"] == arc[i]["clip_id"]
                   for k in range(MAX_CONSEC + 1)):
                # Suche eine Szene eines anderen Clips weiter hinten
                for j in range(i + MAX_CONSEC + 1, len(arc)):
                    if arc[j]["clip_id"] != arc[i]["clip_id"]:
                        arc.insert(i + MAX_CONSEC, arc.pop(j))
                        changed = True
                        break
        if not changed:
            break

    return arc


# ═══════════════════════════════════════════════════════════
# EINFACHER ALGORITHMUS (dokumentar, werbespot, social_media)
# ═══════════════════════════════════════════════════════════

def _baue_einfachen_schnitt(szenen: list[dict], config: dict) -> list[dict]:
    """
    Für Stile ohne narrativen Bogen.

    dokumentar  → chronologisch, Dialog bevorzugen
    werbespot   → nach Energie sortieren (höchste zuerst), kurz
    social_media → rein nach Energie
    """
    dialog_gewicht = config["dialog_gewicht"]

    def score(s: dict) -> float:
        e = s["_energie"]
        d = 1.0 if s.get("transkription") else 0.0
        return e * (1.0 - dialog_gewicht) + d * dialog_gewicht

    if dialog_gewicht >= 0.7:
        result = sorted(szenen, key=lambda s: (s["clip_id"], s["start_zeit"]))
    else:
        result = sorted(szenen, key=score, reverse=True)

    # Clip-Wechsel bevorzugen
    if len(result) >= 4:
        ordered: list[dict] = []
        remaining = list(result)
        while remaining:
            prev_cid = ordered[-1]["clip_id"] if ordered else None
            candidates = [s for s in remaining if s["clip_id"] != prev_cid] or remaining
            ordered.append(candidates[0])
            remaining.remove(candidates[0])
        result = ordered

    return result


# ═══════════════════════════════════════════════════════════
# MULTI-PROVIDER LLM — ABSTRAKTIONSSCHICHT
# ═══════════════════════════════════════════════════════════

async def _detect_best_provider() -> str:
    """Wählt automatisch den besten verfügbaren Provider."""
    if CLAUDE_API_KEY:
        return "claude"
    if OPENAI_API_KEY:
        return "openai"
    if GEMINI_API_KEY:
        return "gemini"
    return "ollama"


async def _llm_call_async(
    provider: str,
    system_prompt: str,
    user_prompt: str,
    modell: str | None = None,
    temperature: float = 0.2,
    max_tokens: int = 800,
) -> str | None:
    """
    Unified async LLM-Aufruf für alle Provider.
    Gibt den Antwort-Text zurück, oder None bei Fehler/Timeout.
    """
    resolved = provider if provider != "auto" else await _detect_best_provider()

    try:
        if resolved == "claude":
            return await _llm_claude(system_prompt, user_prompt, modell, temperature, max_tokens)
        elif resolved == "openai":
            return await _llm_openai(system_prompt, user_prompt, modell, temperature, max_tokens)
        elif resolved == "gemini":
            return await _llm_gemini(system_prompt, user_prompt, modell, temperature, max_tokens)
        else:
            # Ollama kombiniert system + user in einem Prompt
            combined = f"{system_prompt}\n\n{user_prompt}" if system_prompt else user_prompt
            return await _llm_ollama(combined, modell, temperature, max_tokens)
    except Exception as exc:
        logger.warning(f"LLM [{resolved}] Fehler: {exc}")
        return None


async def _llm_claude(
    system: str,
    user: str,
    modell: str | None,
    temperature: float,
    max_tokens: int,
) -> str | None:
    """Anthropic Claude via Messages API."""
    if not CLAUDE_API_KEY:
        return None

    m = modell or CLAUDE_MODEL
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": CLAUDE_API_KEY,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": m,
                "max_tokens": max_tokens,
                "temperature": temperature,
                "system": system,
                "messages": [{"role": "user", "content": user}],
            },
            timeout=120.0,
        )
        resp.raise_for_status()
        data = resp.json()
        return data["content"][0]["text"]


async def _llm_openai(
    system: str,
    user: str,
    modell: str | None,
    temperature: float,
    max_tokens: int,
) -> str | None:
    """OpenAI GPT-4o / o1 via Chat Completions API."""
    if not OPENAI_API_KEY:
        return None

    m = modell or OPENAI_MODEL
    # o1-Modelle unterstützen kein temperature-Feld
    is_o1 = m.startswith("o1")

    payload: dict = {
        "model": m,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "max_tokens": max_tokens,
    }
    if not is_o1:
        payload["temperature"] = temperature
        payload["response_format"] = {"type": "json_object"}

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            "https://api.openai.com/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {OPENAI_API_KEY}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=120.0,
        )
        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]["content"]


async def _llm_gemini(
    system: str,
    user: str,
    modell: str | None,
    temperature: float,
    max_tokens: int,
) -> str | None:
    """Google Gemini via REST API (kein SDK nötig)."""
    if not GEMINI_API_KEY:
        return None

    m = modell or GEMINI_MODEL
    url = (
        f"https://generativelanguage.googleapis.com/v1beta"
        f"/models/{m}:generateContent?key={GEMINI_API_KEY}"
    )
    combined = f"{system}\n\n{user}" if system else user

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            url,
            json={
                "contents": [{"parts": [{"text": combined}]}],
                "generationConfig": {
                    "temperature": temperature,
                    "maxOutputTokens": max_tokens,
                },
            },
            timeout=120.0,
        )
        resp.raise_for_status()
        data = resp.json()
        return data["candidates"][0]["content"]["parts"][0]["text"]


async def _llm_ollama(
    prompt: str,
    modell: str | None,
    temperature: float,
    max_tokens: int,
) -> str | None:
    """Ollama lokal via Generate API."""
    m = modell or OLLAMA_MODEL
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{OLLAMA_BASE_URL}/api/generate",
            json={
                "model": m,
                "prompt": prompt,
                "stream": False,
                "options": {"temperature": temperature, "num_predict": max_tokens},
            },
            timeout=90.0,
        )
        resp.raise_for_status()
        return resp.json().get("response", "").strip()


# ═══════════════════════════════════════════════════════════
# LLM PROMPT-BUILDER
# ═══════════════════════════════════════════════════════════

def _build_cut_system_prompt(stil: str) -> str:
    """
    Reichhaltiger System-Prompt für Claude/GPT-4/Gemini.
    Erklärt die Aufgabe, den Stil und die Analyse-Kriterien.
    """
    stil_desc = {
        "kinematisch": (
            "CINEMATIC CUT — Classic narrative arc:\n"
            "  Ouverture (striking opening) → Rising Action (dynamic short cuts) → "
            "  Transition/Dialogue (narrative anchor, breathing space) → "
            "  Energy Build-up → CLIMAX (peak energy/motion) → "
            "  Clôture (quiet, memorable ending)\n"
            "Rules: Start strong. Build progressively. Dialog in the middle. "
            "End calm. Maximize visual contrast between consecutive scenes. "
            "Alternate long and short scenes for rhythm."
        ),
        "dokumentar": (
            "DOCUMENTARY CUT — Chronological, reflective:\n"
            "Preserve temporal order within clips. Prioritize dialogue scenes. "
            "Use slow, deliberate pacing. Avoid abrupt energy jumps. "
            "Let the narrative breathe. Logic over spectacle."
        ),
        "werbespot": (
            "ADVERTISEMENT CUT — Maximum impact, minimum length:\n"
            "Open immediately with highest energy. Use only short scenes (< 4s). "
            "Maximize visual variety between each shot. Ignore dialogue. "
            "Every scene must earn its place by visual impact alone."
        ),
        "kurzfilm": (
            "SHORT FILM CUT — Balanced pacing, narrative coherence:\n"
            "Balance energy and dialogue. Build a 3-act structure. "
            "Use scene descriptions to maintain narrative logic. "
            "Mix deliberately long (character/dialogue) and short (action) scenes."
        ),
        "social_media": (
            "SOCIAL MEDIA CUT — Instant attention, maximum energy:\n"
            "Use ONLY the highest energy scenes. Maximum 3s per scene. "
            "Open with the single most impactful moment. "
            "Zero tolerance for slow scenes, talking heads, or static shots."
        ),
    }.get(stil, "Create an optimal professional film cut.")

    return (
        "You are a world-class film editor with decades of experience in "
        "cinematography, narrative structure, and visual storytelling.\n\n"
        f"EDITING STYLE:\n{stil_desc}\n\n"
        "ANALYSIS SIGNALS AVAILABLE PER SCENE:\n"
        "  • Energy (0.0–1.0): Combined visual energy (contrast + motion + brightness)\n"
        "  • Motion (0.0–1.0): Estimated movement / optical flow\n"
        "  • Contrast (0.0–1.0): Visual contrast / detail complexity\n"
        "  • Brightness (0.0–1.0): Frame luminosity\n"
        "  • Temperature: warm | neutral | kalt — dominant color temperature\n"
        "  • Role: ouverture | action | transition | climax | cloture\n"
        "  • Description: AI-generated scene summary (use for narrative coherence)\n"
        "  • Transcript: Speech content (prioritize for dialogue placement)\n\n"
        "CRITICAL RULES:\n"
        "1. Avoid 3+ consecutive scenes from the same source clip\n"
        "2. Alternate energy levels — no 3+ high-energy scenes in a row\n"
        "3. Color temperature transitions (warm→kalt) create visual interest\n"
        "4. Descriptions must flow logically (semantic coherence)\n"
        "5. Response format: ONLY a JSON array of scene indices\n"
        "   Example: [2, 0, 5, 3, 1]\n"
        "   You MAY omit weak scenes. Minimum: half the available scenes.\n"
        "   DO NOT include any explanation text."
    )


def _build_cut_user_prompt(
    szenen: list[dict],
    user_prompt: str | None,
    provider: str,
) -> str:
    """
    Strukturierter User-Prompt mit allen Szenen-Metadaten.
    Für reasoning-fähige Modelle (Claude) wird ein Denk-Hinweis hinzugefügt.
    """
    lines: list[str] = []

    for i, s in enumerate(szenen):
        av      = s.get("analyse_visuelle") or {}
        temp    = av.get("temperature", "?")
        mouv    = f"{float(av.get('mouvement',  0.5)):.2f}"
        kontr   = f"{float(av.get('kontrast',   0.5)):.2f}"
        lum     = f"{float(av.get('luminosite', 0.5)):.2f}"
        energie = f"{s['_energie']:.2f}"
        rolle   = s.get("_rolle", "?")
        beschr  = (s.get("beschreibung") or "").strip()
        dialog  = (s.get("transkription") or "").strip()

        line = (
            f"[{i:02d}] {s['dauer']:.1f}s | {rolle.upper():<12} | "
            f"Energy={energie} | Motion={mouv} | Contrast={kontr} | "
            f"Brightness={lum} | Temp={temp}"
        )
        if beschr:
            line += f"\n       Description: {beschr}"
        if dialog:
            line += f'\n       Speech: "{dialog[:100]}"'
        lines.append(line)

    msg = f"SCENES TO EDIT ({len(szenen)} total):\n" + "\n".join(lines)

    if user_prompt:
        msg += f"\n\nDIRECTOR'S NOTE: {user_prompt}"

    # Für Claude: subtiler Chain-of-Thought Hinweis
    if provider in ("claude", "auto"):
        msg += (
            "\n\nIdentify: (a) best opening scene, (b) peak energy scene for climax, "
            "(c) dialogue scenes for the middle, (d) quietest scene for closing. "
            "Then construct the optimal sequence."
        )

    msg += "\n\nOptimal sequence (JSON array of indices ONLY):"
    return msg


def _parse_llm_response(antwort: str, szenen: list[dict]) -> list[dict] | None:
    """
    Extrahiert valide Szenen-Reihenfolge aus beliebiger LLM-Antwort.
    Robust gegen reasoning-Text, JSON-Objects, und formatierte Ausgaben.
    """
    indizes: list[int] | None = None

    # Strategie 1: letztes JSON-Array in der Antwort (nach Chain-of-Thought)
    s = antwort.rfind("[")
    e = antwort.rfind("]")
    if s != -1 and e != -1 and s < e:
        try:
            indizes = json.loads(antwort[s : e + 1])
        except Exception:
            pass

    # Strategie 2: JSON-Object mit Array-Wert (OpenAI JSON mode)
    if indizes is None:
        try:
            data = json.loads(antwort)
            if isinstance(data, list):
                indizes = data
            elif isinstance(data, dict):
                for v in data.values():
                    if isinstance(v, list):
                        indizes = v
                        break
        except Exception:
            pass

    if not indizes:
        logger.warning("LLM: Kein valides JSON-Array in Antwort gefunden.")
        return None

    valide = [i for i in indizes if isinstance(i, int) and 0 <= i < len(szenen)]

    if len(valide) < max(2, len(szenen) // 3):
        logger.warning(f"LLM: Zu wenige valide Indizes ({len(valide)}/{len(szenen)}): {valide}")
        return None

    neue_reihenfolge: list[dict] = []
    seen: set[int] = set()
    for idx in valide:
        if idx not in seen:
            neue_reihenfolge.append(szenen[idx])
            seen.add(idx)

    # Nicht gewählte Szenen ans Ende (LLM-Auswahl dominiert, aber nichts geht verloren)
    for i, s_ in enumerate(szenen):
        if i not in seen:
            neue_reihenfolge.append(s_)

    logger.info(f"LLM: Reihenfolge optimiert — {len(valide)} Szenen ausgewählt: {valide}")
    return neue_reihenfolge


# ═══════════════════════════════════════════════════════════
# LLM-VERFEINERUNG (Multi-Provider — v3)
# ═══════════════════════════════════════════════════════════

async def _llm_verfeinern(
    szenen: list[dict],
    stil: str,
    user_prompt: str | None,
    provider: str = "auto",
    llm_modell: str | None = None,
) -> list[dict] | None:
    """
    Verfeinerung der Szenen-Reihenfolge via LLM (v3).

    Unterstützt: Claude 3.5, GPT-4o, Gemini 1.5 Pro, Ollama/LLaMA3.
    Provider-Auswahl bei 'auto': Claude → OpenAI → Gemini → Ollama.

    Claude/GPT-4 erhalten Chain-of-Thought Hinweise für besseres Reasoning.
    Gibt optimierte Reihenfolge zurück, oder None bei Fehler/Timeout.
    """
    if not szenen:
        return None

    system = _build_cut_system_prompt(stil)
    user   = _build_cut_user_prompt(szenen, user_prompt, provider)

    antwort = await _llm_call_async(
        provider      = provider,
        system_prompt = system,
        user_prompt   = user,
        modell        = llm_modell,
        temperature   = 0.2,
        max_tokens    = min(800, len(szenen) * 15 + 100),
    )

    if not antwort:
        return None

    return _parse_llm_response(antwort, szenen)


# ═══════════════════════════════════════════════════════════
# TIMELINE ZUSAMMENBAUEN
# ═══════════════════════════════════════════════════════════

# Schlüsselwörter, die eine Filler-Szene (Nicht-Inhalt) signalisieren.
# Erkannt in der LLaVA-Beschreibung ODER im Transkript. Konservativ
# gewählt — nur eindeutige Endcard-/Social-Media-Signale, keine vagen
# Begriffe, damit echtes Material nie fälschlich ausgeschlossen wird.
_FILLER_KEYWORDS = (
    "subscribe", "abonnier", "abonnez",
    "follow us", "folgt uns", "social media", "soziale medien",
    "thumbs up", "daumen hoch", "leave a like", "like and subscribe",
    "subscribe button", "notification bell", "glocke",
    "end card", "endcard", "endtafel", "abspann", "credits roll",
    "intro card", "title card", "titeltafel",
)


def _ist_filler_szene(szene: dict) -> bool:
    """
    Erkennt Nicht-Inhalt-Szenen: Social-Media-Endcards ("FOLLOW US",
    "Subscribe"), Logo-/Titeltafeln, Abspann. Diese werden aus der
    Schnitt-Auswahl ausgeschlossen.

    Grundlage: die faktische LLaVA-Beschreibung. Wenn LLaVA z.B.
    "a thumbs up icon and text saying SUBSCRIBE" beschreibt, ist die Szene
    eindeutig eine Endcard und kein inhaltliches Material.
    """
    text = ((szene.get("beschreibung") or "") + " " + (szene.get("transkription") or "")).lower()
    if not text.strip():
        return False
    treffer = sum(1 for kw in _FILLER_KEYWORDS if kw in text)
    # Ein einzelnes starkes Signal genügt (z.B. "subscribe")
    return treffer >= 1


def _merge_kontinuierliche_szenen(geordnet: list[dict]) -> list[dict]:
    """
    Verschmilzt aufeinanderfolgende Szenen, die aus DEMSELBEN Clip stammen
    und zeitlich direkt anschließen (Lücke < 0.2s). Ein Schnitt zwischen
    durchlaufendem Material ist ein "Phantom-Schnitt" ohne Wirkung — der
    Zuschauer sieht keine Veränderung.

    Toleranz 0.2s: deckt PySceneDetect-Grenzen und Sub-Szenen-Splits ab.
    """
    if len(geordnet) < 2:
        return geordnet
    merged: list[dict] = [dict(geordnet[0])]
    for sz in geordnet[1:]:
        prev = merged[-1]
        kontinuierlich = (
            sz.get("clip_id") == prev.get("clip_id")
            and abs(float(sz.get("start_zeit", 0)) - float(prev.get("end_zeit", 0))) < 0.2
        )
        if kontinuierlich:
            # Verschmelzen: prev übernimmt das Ende von sz
            prev["end_zeit"] = sz.get("end_zeit", prev.get("end_zeit"))
            prev["dauer"] = round(float(prev["end_zeit"]) - float(prev.get("start_zeit", 0)), 3)
        else:
            merged.append(dict(sz))
    if len(merged) < len(geordnet):
        logger.info(
            f"Phantom-Schnitte verschmolzen: {len(geordnet)} → {len(merged)} Szenen"
        )
    return merged


def _baue_timeline(szenen_geordnet: list[dict], config: dict) -> tuple[list[dict], float]:
    """
    Baut V1 + A1 Segmente aus der geordneten Szenen-Liste.
    Wendet Tempo-Korrektur an (Szenen kürzen/anpassen).
    """
    video_farben = ["orange", "blue", "purple"]
    farb_map: dict[str, str] = {}
    farb_idx = 0

    segmente = []
    cursor = 0.0
    max_d = config["max_dauer"]
    tempo   = config["tempo"]
    uebergaenge: dict | None = config.get("uebergaenge")  # None = harter Schnitt

    # Beat-Sync: Beats werden als Timeline-Positionen interpretiert.
    # Jede Segmentgrenze wird auf den nächsten Beat >= Zielposition gesnappt.
    beat_times: list[float] = config.get("beat_times") or []
    beats_pro_segment: int = int(config.get("beats_pro_segment", 4))
    beat_tempo: float = float(config.get("beat_tempo", 0.0))
    beat_dauer = (60.0 / beat_tempo) if beat_tempo > 0 else 0.0  # Sekunden pro Beat
    # Mindest-Schnittlänge bei beat_sync = N Beats (kein zu schnelles Stroboskop)
    beat_min_d = max(beat_dauer * beats_pro_segment, 0.5) if beat_dauer > 0 else 0.0

    for idx, szene in enumerate(szenen_geordnet):
        dauer = szene["dauer"]

        # Tempo: sehr schnelle Stile kappen die Dauer
        if tempo >= 0.85:
            dauer = min(max_d, dauer)
        elif tempo >= 0.6:
            # Kinematisch: kappen nur wenn deutlich zu lang
            if dauer > max_d:
                dauer = max_d

        dauer = max(0.25, dauer)

        # Beat-Sync: snappt die Dauer auf das nächste Vielfache von N Beats
        if beat_times:
            ziel_ende = cursor + max(dauer, beat_min_d)
            naechster_beat = _next_beat_at_or_after(beat_times, ziel_ende)
            if naechster_beat is not None and naechster_beat > cursor + 0.2:
                dauer = naechster_beat - cursor

        cid = szene["clip_id"]
        if cid not in farb_map:
            farb_map[cid] = video_farben[farb_idx % len(video_farben)]
            farb_idx += 1
        v_color = farb_map[cid]

        name = szene["clip_dateiname"].rsplit(".", 1)[0]
        if len(name) > 14:
            name = name[:14] + "…"

        beschreibung = szene.get("beschreibung") or ""
        rolle = szene.get("_rolle", "")
        rolle_prefix = {
            ROLLE_OUVERTURE: "",
            ROLLE_CLIMAX:    "",
            ROLLE_CLOTURE:   "",
        }.get(rolle, "")
        # Label gehalten kurz: nur Dateiname. Beschreibung bleibt im
        # `beschreibung`-Feld gespeichert (für Tooltip / Hover-Anzeige).
        label = f"{rolle_prefix}{name}"

        seg_id   = str(uuid.uuid4())
        group_id = f"grp-ai-{seg_id[:8]}"

        # Übergang: erstes Segment hat keinen, alle weiteren erhalten einen
        trans: dict | None = None
        if idx > 0 and uebergaenge:
            rolle = szene.get("_rolle", "")
            trans = uebergaenge.get(rolle) or uebergaenge.get("default")

        # Video (V1) — inkl. Selektions-Provenienz für Tooltip "Warum dieses Cut?"
        v_seg: dict = {
            "id": seg_id,
            "clip_id": cid,
            "szene_nr": szene["szene_nr"],
            "label": label,
            "track": "v1",
            "start": round(cursor, 3),
            "dauer": round(dauer, 3),
            "mediaStart": round(szene["start_zeit"], 3),
            "quelle": szene["quelle"],
            "beschreibung": beschreibung,
            "transkription": szene.get("transkription") or "",
            "rolle": rolle or None,
            "prompt_relevance": round(szene["_prompt_relevance"], 3) if szene.get("_prompt_relevance") is not None else None,
            "energie": round(szene["_energie"], 3) if szene.get("_energie") is not None else None,
            "interessantheit": round(szene["_interessantheit"], 3) if szene.get("_interessantheit") is not None else None,
            "color": v_color,
            "groupId": group_id,
            "ai": True,
        }
        if trans:
            v_seg["transition"] = trans
        segmente.append(v_seg)

        # Audio (A1 — gespiegelt). Farbe = die des Quell-Clips, damit
        # man auf einen Blick sieht, welcher Clip wohin gehört.
        segmente.append({
            "id": str(uuid.uuid4()),
            "clip_id": cid,
            "szene_nr": szene["szene_nr"],
            "label": f"♪ {name}",
            "track": "a1",
            "start": round(cursor, 3),
            "dauer": round(dauer, 3),
            "mediaStart": round(szene["start_zeit"], 3),
            "quelle": szene["quelle"],
            "color": v_color,
            "groupId": group_id,
            "ai": True,
        })

        cursor += dauer

    return segmente, round(cursor, 3)


# ═══════════════════════════════════════════════════════════
# EVALUATIONS-METRIKEN (quantitative Selbstbewertung)
# ═══════════════════════════════════════════════════════════
# Drei objektive Metriken werden auf jeder erzeugten Sequenz berechnet,
# um die Qualität des Schnitts nachvollziehbar zu machen. Sie ersetzen
# die fehlende User-Studie und liefern dem Editor sofortiges Feedback.
# ───────────────────────────────────────────────────────────

def _berechne_metriken(geordnet: list[dict]) -> dict[str, float | int]:
    """
    Berechnet drei quantitative Metriken für die finale Sequenz:

    1. **Diversität** (0–1) — mittlerer Kosinus-Abstand der CLIP-Embeddings
       zwischen aufeinanderfolgenden Szenen. Höher = mehr visuelle Abwechslung.

    2. **Clip-Wechselrate** (0–1) — Anteil der Übergänge, bei denen sich der
       Quell-Clip ändert. Höher = besseres Mischen der Source-Clips.

    3. **Dialog-Treue** (0–1) — Anteil der Schnittpunkte, die NICHT mitten in
       einem von Whisper transkribierten Wort liegen. Höher = sauberere Audio-
       Übergänge (kein abgeschnittenes Wort beim Cut).

    Diese Metriken sind die einzigen Werte, die in der Antwort eine
    objektive Bewertung der Schnittqualität erlauben.
    """
    n = len(geordnet)
    if n < 2:
        return {
            "diversitaet":    0.0,
            "wechselrate":    0.0,
            "dialog_treue":   1.0,
            "szenen_anzahl":  n,
            "uebergaenge":    0,
        }

    # ── Metrik 1: visuelle Diversität via CLIP-Kosinus-Abstand ────
    distanzen: list[float] = []
    for i in range(n - 1):
        emb_a = geordnet[i].get("embedding")
        emb_b = geordnet[i + 1].get("embedding")
        if not _ist_nullvektor(emb_a) and not _ist_nullvektor(emb_b):
            sim = _cosine_similarity(emb_a, emb_b)
            distanzen.append(max(0.0, 1.0 - sim))  # Kosinus-Abstand, geclamped auf [0, 1]
    diversitaet = sum(distanzen) / len(distanzen) if distanzen else 0.0

    # ── Metrik 2: Clip-Wechselrate ───────────────────────────────
    wechsel = sum(
        1 for i in range(n - 1)
        if geordnet[i]["clip_id"] != geordnet[i + 1]["clip_id"]
    )
    wechselrate = wechsel / (n - 1)

    # ── Metrik 3: Dialog-Treue ───────────────────────────────────
    # Ein Schnitt ist "treu", wenn das Ende einer Szene NICHT mitten in einem
    # Whisper-transkribierten Wort liegt. Wir prüfen die letzten 0.05s jeder
    # Szene (außer der letzten) gegen das transkription_json.
    treu = 0
    geprueft = 0
    for i in range(n - 1):
        szene = geordnet[i]
        cut_zeit = szene["end_zeit"]
        woerter_json = szene.get("transkription_json") or []
        if not woerter_json:
            # Kein Dialog → standardmäßig als "treu" gezählt (kein Wort, das man unterbricht)
            treu += 1
            geprueft += 1
            continue
        # Alle Wörter dieser Szene durchgehen
        alle_woerter = []
        for seg in woerter_json:
            alle_woerter.extend(seg.get("woerter", []))
        # Liegt cut_zeit STRICT innerhalb eines Wortes (start, end) ?
        in_wort = any(
            w.get("start", 0) < cut_zeit - 0.02 < w.get("end", 0)
            for w in alle_woerter
        )
        if not in_wort:
            treu += 1
        geprueft += 1
    dialog_treue = treu / geprueft if geprueft > 0 else 1.0

    # ── Metrik 4 (optional): Prompt-Relevanz ────────────────────
    # Nur berechnet, wenn der User einen Prompt eingegeben hat.
    # Mittlere CLIP-Cosinus-Ähnlichkeit aller gewählten Szenen zum Prompt-Vektor.
    prompt_relevance: float | None = None
    rel_values = [s.get("_prompt_relevance") for s in geordnet if "_prompt_relevance" in s]
    if rel_values:
        prompt_relevance = round(sum(rel_values) / len(rel_values), 3)

    result: dict[str, float | int | None] = {
        "diversitaet":    round(diversitaet, 3),
        "wechselrate":    round(wechselrate, 3),
        "dialog_treue":   round(dialog_treue, 3),
        "szenen_anzahl":  n,
        "uebergaenge":    n - 1,
    }
    if prompt_relevance is not None:
        result["prompt_relevance"] = prompt_relevance
    return result


# ═══════════════════════════════════════════════════════════
# HAUPT-ENDPUNKT
# ═══════════════════════════════════════════════════════════

@router.post("/cut")
async def ai_schnitt(body: AiCutRequest, db: AsyncSession = Depends(get_db)):
    """
    Erstellt eine KI-optimierte Timeline (v3).

    Pipeline:
      1. Szenen + alle Metadaten aus DB laden (inkl. transkription_json)
      2. Energie zuweisen (PIL + CLIP + Heuristik)
      3. Qualitäts-Schwelle anwenden (qualitaet_schwelle)
      4. Szenen-Subdivision (audio-aware Whisper pause detection)
      5. Rollen zuweisen + Szenen filtern (min_dauer)
      6. Kinematischen Bogen / Einfachen Schnitt aufbauen
      7. LLM-Verfeinerung (multi-provider: Claude / GPT-4 / Gemini / Ollama)
      8. Max-Szenen Begrenzung
      9. Timeline-Segmente V1+A1 erzeugen
      10. In DB speichern + JSON zurückgeben
    """
    if not body.clip_ids:
        raise HTTPException(400, "Mindestens ein Clip erforderlich.")

    # Konfiguration laden + Übergänge ggf. ausschalten (Default: keine
    # Übergänge, harter Schnitt — sauberer im Browser-Playback).
    config = dict(STIL_CONFIG.get(body.stil, STIL_CONFIG["kinematisch"]))
    if not body.mit_uebergaengen:
        config["uebergaenge"] = None
    min_d  = config["min_dauer"]

    # ─── Beat-Sync: Beats vom ersten Master-Clip berechnen ──
    # Die Beats werden als Timeline-Positionen interpretiert; das setzt voraus,
    # dass das Musikmaterial homogen ist (z.B. dieselbe Performance aus
    # verschiedenen Kameras). Für gemischtes Material wäre Per-Clip-Beats
    # eine zukünftige Erweiterung.
    if body.beat_sync and body.clip_ids:
        master_id = body.clip_ids[0]
        res_master = await db.execute(select(Clip).where(Clip.id == master_id))
        master_clip = res_master.scalar_one_or_none()
        if master_clip and master_clip.dateipfad:
            beats_data = _detect_beats(master_id, master_clip.dateipfad)
            config["beat_times"] = beats_data.get("beats", [])
            config["beat_tempo"] = beats_data.get("tempo", 0.0)
            config["beats_pro_segment"] = body.beat_pro_segment
            logger.info(
                f"Beat-Sync aktiv: {len(config['beat_times'])} Beats @ "
                f"{config['beat_tempo']:.1f} BPM, {body.beat_pro_segment} Beats/Segment"
            )

    # ─── 1. Szenen aus DB laden (inkl. transkription_json) ──
    alle_szenen: list[dict] = []

    for clip_id in body.clip_ids:
        res_c = await db.execute(select(Clip).where(Clip.id == clip_id))
        clip  = res_c.scalar_one_or_none()
        if not clip:
            continue

        res_s = await db.execute(
            select(Szene)
            .where(Szene.clip_id == clip_id)
            .order_by(Szene.szenen_nr)
        )
        szenen = res_s.scalars().all()
        total_clip_dauer = float(clip.dauer or 1.0)

        if szenen:
            for sz in szenen:
                alle_szenen.append({
                    "_uid":              str(sz.id),
                    "clip_id":           str(clip.id),
                    "clip_dateiname":    clip.dateiname,
                    "quelle":            clip.quelle,
                    "szene_nr":          sz.szenen_nr,
                    "start_zeit":        sz.start_zeit,
                    "end_zeit":          sz.end_zeit,
                    "dauer":             sz.dauer,
                    "embedding":         sz.clip_embedding,
                    "beschreibung":      sz.beschreibung,
                    "transkription":     sz.transkription,
                    "transkription_json": sz.transkription_json,  # NEU: für audio-aware cuts
                    "analyse_visuelle":  sz.analyse_visuelle,
                    "_pos_pct":  sz.start_zeit / total_clip_dauer if total_clip_dauer > 0 else 0.0,
                    "_clip_dauer": total_clip_dauer,
                })
        else:
            alle_szenen.append({
                "_uid":              str(uuid.uuid4()),
                "clip_id":           str(clip.id),
                "clip_dateiname":    clip.dateiname,
                "quelle":            clip.quelle,
                "szene_nr":          1,
                "start_zeit":        0.0,
                "end_zeit":          clip.dauer or 10.0,
                "dauer":             clip.dauer or 10.0,
                "embedding":         None,
                "beschreibung":      None,
                "transkription":     None,
                "transkription_json": None,
                "analyse_visuelle":  None,
                "_pos_pct":          0.0,
                "_clip_dauer":       float(clip.dauer or 10.0),
            })

    if not alle_szenen:
        raise HTTPException(400, "Keine verwertbaren Clips/Szenen gefunden.")

    # ─── 2. Energie zuweisen (CLIP zero-shot, fallback heuristisch) ──
    for sz in alle_szenen:
        sz["_energie"] = _szene_energie(sz)

    # ─── 2a. User-Prompt encodieren (Kernstück der Voie C) ──
    # Wenn der User einen Text eingegeben hat, encodieren wir ihn via
    # CLIP-Text-Encoder. Anschließend wird jede Szene gegen diesen
    # Prompt-Vektor gescored. Das ist die echte Intent-Definition.
    prompt_emb: list[float] | None = None
    if body.prompt and body.prompt.strip():
        prompt_emb = _encode_prompt(body.prompt.strip())
        if prompt_emb is not None:
            logger.info(f"Prompt encoded: '{body.prompt[:80]}…' → 512-dim")
            for sz in alle_szenen:
                emb = sz.get("embedding")
                if not _ist_nullvektor(emb):
                    sz["_prompt_relevance"] = max(0.0, _cosine_similarity(emb, prompt_emb))
                else:
                    sz["_prompt_relevance"] = 0.0
        else:
            logger.warning("Prompt-Encoding fehlgeschlagen — Fallback auf Stil-Logik")

    # ─── 2b. Qualitäts-Schwelle anwenden ─────────────────
    # Szenen unterhalb der Mindest-Energie ausschließen
    if body.qualitaet_schwelle > 0.0:
        gefiltert = [s for s in alle_szenen if s["_energie"] >= body.qualitaet_schwelle]
        if gefiltert:
            alle_szenen = gefiltert
            logger.info(
                f"Qualitäts-Filter: {len(alle_szenen)} Szenen behalten "
                f"(Schwelle={body.qualitaet_schwelle})"
            )

    # ─── 2b'. Filler-Szenen aussortieren ─────────────────
    # Social-Media-Endcards ("FOLLOW US", "Subscribe", Daumen-hoch-Grafiken),
    # Logo-Karten und Intro/Outro-Texttafeln sind KEIN inhaltliches Material.
    # Sie werden anhand der LLaVA-Beschreibung erkannt und ausgeschlossen,
    # sodass die finale Timeline nur substanzielle Szenen enthält.
    inhalt = [s for s in alle_szenen if not _ist_filler_szene(s)]
    filler_anzahl = len(alle_szenen) - len(inhalt)
    if filler_anzahl and inhalt:
        alle_szenen = inhalt
        logger.info(
            f"Filler-Filter: {filler_anzahl} Nicht-Inhalt-Szenen ausgeschlossen "
            f"(Social-Media-Cards / Endtafeln), {len(alle_szenen)} Inhalts-Szenen behalten"
        )

    # ─── 2c. Szenen unterteilen (audio-aware) ─────────────
    alle_szenen = _subdivise_scenes(alle_szenen, body.stil)
    # Nach der Unterteilung haben die neuen Sub-Szenen noch keinen
    # Prompt-Score. Wir vererben den Score der Mutter-Szene.
    if prompt_emb is not None:
        for sz in alle_szenen:
            if "_prompt_relevance" not in sz:
                # Sub-Szene: das Embedding zeigt auf die Mutter-Szene
                emb = sz.get("embedding")
                if not _ist_nullvektor(emb):
                    sz["_prompt_relevance"] = max(0.0, _cosine_similarity(emb, prompt_emb))
                else:
                    sz["_prompt_relevance"] = 0.0

    # ─── 3. Rollen zuweisen + nach min_dauer filtern ──────
    for sz in alle_szenen:
        sz["_typ_narratif"] = _detecte_role_narratif(sz)
        sz["_rolle"] = _rolle_kinematisch(sz, sz["_energie"], sz["_pos_pct"])

    kandidaten = [s for s in alle_szenen if s["dauer"] >= min_d]
    if not kandidaten:
        kandidaten = alle_szenen  # Fallback: alles behalten

    # ─── 3b. Multicam-Dedup ───────────────────────────────
    # Wenn Clips als Multicam-Gruppen erkannt sind, behalten wir pro
    # Zeitfenster nur die "beste" Szene. Verhindert visuelle Doppelungen
    # (z.B. dass das "FOLLOW US"-Outro 3× hintereinander erscheint, weil
    # jede der 3 Kameras es enthält).
    multicam_groups: list[set[str]] = []
    try:
        # Wir holen die clips erneut, weil _get_multicam_groups die Audio-
        # Pfade braucht. Verwendet bereits geladene Clip-Objekte über db.
        from sqlalchemy.orm import selectinload
        res_mc = await db.execute(
            select(Clip)
            .where(Clip.id.in_(body.clip_ids))
            .options(selectinload(Clip.szenen))
        )
        clips_mc = res_mc.scalars().all()
        multicam_groups = _get_multicam_groups(clips_mc)
        if multicam_groups:
            anzahl_vor = len(kandidaten)
            kandidaten = _dedupe_multicam_candidates(kandidaten, multicam_groups, bucket_sec=6.0)
            logger.info(
                f"Multicam-Dedup aktiv: {len(multicam_groups)} Gruppe(n), "
                f"{anzahl_vor} → {len(kandidaten)} Kandidaten behalten"
            )
    except Exception as exc:
        logger.warning(f"Multicam-Dedup übersprungen: {exc}")

    # ─── 4. Sequenz-Algorithmus ───────────────────────────
    # Wenn ein Prompt gegeben ist → Voie C : prompt-driven Selection
    # (Top-K-Szenen mit höchster Prompt-Relevanz, in chronologischer
    # Reihenfolge der Quelle).
    # Sonst → klassische Stil-basierte Auswahl (kinematischer Bogen etc.)
    if prompt_emb is not None and any(s.get("_prompt_relevance", 0) > 0 for s in kandidaten):
        # ── PROMPT-DRIVEN PATH ─────────────────────────────
        # Bestimme die Ziel-Anzahl der Szenen:
        # • body.max_szenen falls explizit gesetzt
        # • sonst: leite aus dem Stil ab (min_dauer / max_dauer)
        ziel_anzahl = body.max_szenen if body.max_szenen else max(
            5, min(15, int(60 / config["max_dauer"]))
        )

        # ─── MMR Re-Ranking (Maximal Marginal Relevance) ───
        # Statt naivem Top-K nehmen wir eine balance von:
        #   (1) Relevanz   = cos(prompt, scene)
        #   (2) Diversität = 1 - max(cos(scene, schon_gewählte))
        # Formel (Carbonell & Goldstein, 1998):
        #   MMR(s) = λ · sim(s, q) - (1-λ) · max sim(s, s') für s' in selected
        # λ=0.7: 70 % Relevanz, 30 % Diversitäts-Penalty.
        # Effekt: vermeidet, dass alle ausgewählten Szenen visuell ähnlich sind.
        LAMBDA = 0.7
        # Vorfilterung auf 3×ziel_anzahl mit höchster Relevanz, MMR auf diesem Pool
        sortiert = sorted(kandidaten, key=lambda s: s.get("_prompt_relevance", 0), reverse=True)
        pool = sortiert[:max(ziel_anzahl * 3, 30)]

        def _cos_szenen(a: dict, b: dict) -> float:
            ea, eb = a.get("embedding"), b.get("embedding")
            if not ea or not eb:
                return 0.0
            return max(0.0, _cosine_similarity(ea, eb))

        ausgewaehlt: list[dict] = []
        verfuegbar = list(pool)
        while len(ausgewaehlt) < ziel_anzahl and verfuegbar:
            def _mmr_score(c: dict) -> float:
                rel = c.get("_prompt_relevance", 0.0)
                if not ausgewaehlt:
                    return LAMBDA * rel  # erster Pick: pur Relevanz
                max_div_sim = max(_cos_szenen(c, s) for s in ausgewaehlt)
                return LAMBDA * rel - (1 - LAMBDA) * max_div_sim
            best = max(verfuegbar, key=_mmr_score)
            ausgewaehlt.append(best)
            verfuegbar.remove(best)

        # ─── Reihenfolge je nach Stil ──────────────────────
        # config["arc"] entscheidet:
        #   • arc=True  → Aristotelische Bogenform (Eröffnung → Höhepunkt →
        #     Ausklang). Für energetisch / ausgewogen.
        #   • arc=False → CHRONOLOGISCHE Reihenfolge. Für ruhig / dokumentar:
        #     der User will eine sachliche, zeitlich geordnete Abfolge, KEINE
        #     dramatische Umstellung.
        if config.get("arc", True):
            geordnet = _zwinge_narrativen_bogen(ausgewaehlt)
            ordnung = "narrativer Bogen"
        else:
            geordnet = sorted(ausgewaehlt, key=lambda s: (s["clip_id"], s["start_zeit"]))
            ordnung = "chronologisch"
        rollen_anzahl = {r: sum(1 for s in geordnet if s.get("_rolle") == r) for r in
                         [ROLLE_OUVERTURE, ROLLE_ACTION, ROLLE_TRANSITION, ROLLE_CLIMAX, ROLLE_CLOTURE]}
        logger.info(
            f"Prompt-driven + MMR ({ordnung}): {len(geordnet)} Szenen ausgewählt "
            f"(von {len(kandidaten)} Kandidaten, Pool {len(pool)}), "
            f"mittlere Relevanz = "
            f"{sum(s.get('_prompt_relevance', 0) for s in geordnet) / max(1, len(geordnet)):.3f} · "
            f"Rollen={rollen_anzahl}"
        )
    elif config["arc"]:
        # ── ARC-DRIVEN PATH (Default) ─────────────────────
        geordnet = _baue_kinematischen_bogen(kandidaten)
    else:
        # ── SIMPLE PATH (Werbespot/Social Media) ──────────
        geordnet = _baue_einfachen_schnitt(kandidaten, config)

    if not geordnet:
        raise HTTPException(400, "Keine Szenen nach Algorithmus übrig.")

    # ─── 5. LLM-Verfeinerung (Multi-Provider — optional) ──
    llm_provider_verwendet: str | None = None
    if body.llm_aktiviert and len(geordnet) >= 3:
        try:
            llm_result = await _llm_verfeinern(
                szenen     = geordnet,
                stil       = body.stil,
                user_prompt = body.prompt,
                provider   = body.provider,
                llm_modell = body.llm_modell,
            )
            if llm_result and len(llm_result) >= max(2, len(geordnet) // 2):
                # Welcher Provider hat tatsächlich geantwortet?
                resolved = body.provider
                if resolved == "auto":
                    resolved = (
                        "claude" if CLAUDE_API_KEY else
                        "openai" if OPENAI_API_KEY else
                        "gemini" if GEMINI_API_KEY else
                        "ollama"
                    )
                llm_provider_verwendet = resolved
                geordnet = llm_result
                logger.info(f"LLM [{resolved}]: {len(geordnet)} Szenen optimiert")
        except Exception as exc:
            logger.warning(f"LLM-Schritt übersprungen: {exc}")

    # ─── 6. Max-Szenen Begrenzung ─────────────────────────
    if body.max_szenen and len(geordnet) > body.max_szenen:
        geordnet = geordnet[: body.max_szenen]
        logger.info(f"Max-Szenen Begrenzung: {len(geordnet)} Szenen")

    # ─── 6b. Kontinuierliche Szenen verschmelzen ──────────
    # Wenn zwei aufeinanderfolgende Szenen aus DEMSELBEN Clip stammen UND
    # zeitlich direkt aneinander anschließen, ist ein Schnitt dazwischen
    # sinnlos — es ist faktisch durchlaufendes Material. Wir verschmelzen
    # sie zu einer Szene. Das eliminiert "Phantom-Schnitte".
    geordnet = _merge_kontinuierliche_szenen(geordnet)

    # ─── 7. Timeline-Segmente bauen ──────────────────────
    segmente, gesamtdauer = _baue_timeline(geordnet, config)

    if not segmente:
        raise HTTPException(400, "Keine Segmente erzeugt.")

    # ─── 8. In DB speichern ───────────────────────────────
    tl_id = str(uuid.uuid4())
    # ─── Quantitative Evaluationsmetriken ────────────────
    # Diese drei Metriken liefern eine objektive Selbstbewertung der
    # erzeugten Sequenz. Sie ersetzen das Fehlen einer formalen User-Studie
    # und sind die einzigen Zahlen, die der Editor zur Beurteilung der
    # Schnittqualität heranziehen sollte.
    metriken = _berechne_metriken(geordnet)
    logger.info(
        f"Cut-Metriken: Diversität={metriken['diversitaet']}, "
        f"Wechselrate={metriken['wechselrate']}, "
        f"Dialog-Treue={metriken['dialog_treue']}"
    )

    tl_daten = {
        "segmente":           segmente,
        "gesamtdauer":        gesamtdauer,
        "stil":               body.stil,
        "llm_provider":       llm_provider_verwendet,
        "szenen_gesamt":      len(alle_szenen),
        "szenen_ausgewaehlt": len([s for s in segmente if s["track"] == "v1"]),
        "arc_rollen": {
            ROLLE_OUVERTURE:  sum(1 for s in geordnet if s["_rolle"] == ROLLE_OUVERTURE),
            ROLLE_ACTION:     sum(1 for s in geordnet if s["_rolle"] == ROLLE_ACTION),
            ROLLE_TRANSITION: sum(1 for s in geordnet if s["_rolle"] == ROLLE_TRANSITION),
            ROLLE_CLIMAX:     sum(1 for s in geordnet if s["_rolle"] == ROLLE_CLIMAX),
            ROLLE_CLOTURE:    sum(1 for s in geordnet if s["_rolle"] == ROLLE_CLOTURE),
        },
        "metriken": metriken,
        "scoring_methode": "CLIP zero-shot (action vs calm prompts)" if _PROMPT_EMBS else "heuristic fallback",
        "beat_sync": {
            "aktiv":   body.beat_sync,
            "tempo":   round(config.get("beat_tempo", 0.0), 1) if body.beat_sync else None,
            "anzahl_beats": len(config.get("beat_times") or []) if body.beat_sync else None,
            "beats_pro_segment": body.beat_pro_segment if body.beat_sync else None,
        },
    }

    tl = Timeline(
        id=tl_id,
        name=f"Strukturierter Schnitt ({body.stil})",
        stil=body.stil,
        prompt=body.prompt,
        daten=tl_daten,
        gesamtdauer=gesamtdauer,
    )
    db.add(tl)
    await db.commit()

    return {
        "timeline_id":        tl_id,
        "segmente_anzahl":    tl_daten["szenen_ausgewaehlt"],
        "gesamtdauer":        gesamtdauer,
        "szenen_gesamt":      len(alle_szenen),
        "llm_provider":       llm_provider_verwendet,
        "arc_rollen":         tl_daten["arc_rollen"],
        "metriken":           metriken,
        "scoring_methode":    tl_daten["scoring_methode"],
        "daten":              tl_daten,
    }


# ═══════════════════════════════════════════════════════════
# MATERIAL-ATLAS · 2D-Projektion des CLIP-Embedding-Raums
# ═══════════════════════════════════════════════════════════
# Wozu?
#   Macht den 512-dim CLIP-Repräsentationsraum sichtbar.
#   Jede Szene ist ein Punkt in 2D, geclustert nach visueller Ähnlichkeit.
#   Wird ein Prompt mitgegeben, wird auch dessen Text-Embedding
#   in den gleichen 2D-Raum projiziert — der Atlas zeigt visuell, welche
#   Szenen "nahe" am Prompt liegen.
#
# Methode:
#   PCA via numpy SVD (keine externen Abhängigkeiten wie sklearn nötig).
#   Erste zwei Hauptkomponenten = lineare Projektion mit maximaler Varianz.
#   Wir geben nur normalisierte Koordinaten zurück; das Frontend rendert
#   einen Scatterplot.
# ───────────────────────────────────────────────────────────

class AtlasRequest(BaseModel):
    clip_ids: list[str] | None = Field(None, description="Optionale Filterung — sonst alle analysierten Clips")
    prompt: str | None = Field(None, description="Optionaler Text-Prompt zur Projektion in denselben Raum")


@router.post("/atlas")
async def material_atlas(body: AtlasRequest, db: AsyncSession = Depends(get_db)):
    """
    Berechnet eine 2D-PCA-Projektion aller Szenen-Embeddings.

    Antwort:
      {
        scenes: [
          { id, clip_id, clip_dateiname, szenen_nr, x, y,
            beschreibung, thumbnail_url, transkription, dauer }
        ],
        prompt: { text, x, y, top_k: [scene_id, ...] } | null,
        variance_explained: [pc1, pc2],   # Anteil 0..1
        n: int                            # Anzahl Szenen
      }
    """
    import numpy as np

    # ─── Szenen + Embeddings laden ────────────────────────
    stmt = select(Szene, Clip).join(Clip, Szene.clip_id == Clip.id).where(Clip.status == "analysiert")
    if body.clip_ids:
        stmt = stmt.where(Clip.id.in_(body.clip_ids))
    rows = (await db.execute(stmt)).all()

    scenes: list[dict] = []
    embeds: list[list[float]] = []
    for sz, clip in rows:
        emb = sz.clip_embedding
        if not emb or all(v == 0.0 for v in emb):
            continue
        scenes.append({
            "id":              str(sz.id),
            "clip_id":         str(clip.id),
            "clip_dateiname":  clip.dateiname,
            "szenen_nr":       sz.szenen_nr,
            "start":           sz.start_zeit,
            "end":             sz.end_zeit,
            "dauer":           sz.dauer,
            "beschreibung":    sz.beschreibung,
            "transkription":   (sz.transkription or "")[:140],
            "thumbnail_url":   f"/uploads/{Path(sz.thumbnail_pfad).name}" if sz.thumbnail_pfad else None,
        })
        embeds.append(emb)

    if len(embeds) < 2:
        return {
            "scenes": scenes,
            "prompt": None,
            "variance_explained": [0.0, 0.0],
            "n": len(scenes),
            "fehler": "Mindestens 2 Szenen mit Embedding nötig.",
        }

    X = np.array(embeds, dtype=np.float32)           # (n, 512)
    mean = X.mean(axis=0, keepdims=True)
    Xc = X - mean
    # SVD: U·diag(S)·Vt = Xc. Die ersten 2 Spalten von V (Zeilen von Vt)
    # sind die zwei Hauptkomponenten.
    _, S, Vt = np.linalg.svd(Xc, full_matrices=False)
    components = Vt[:2]                              # (2, 512)
    coords = Xc @ components.T                       # (n, 2)

    # Erklärte Varianz: S² / Σ S²
    variance_total = float((S ** 2).sum()) or 1.0
    variance_explained = [float((S[0] ** 2) / variance_total), float((S[1] ** 2) / variance_total)]

    # Auf [-1, 1] normalisieren, damit Frontend einheitlich rendert
    cmax = float(np.abs(coords).max()) or 1.0
    coords_n = coords / cmax

    for i, sc in enumerate(scenes):
        sc["x"] = float(coords_n[i, 0])
        sc["y"] = float(coords_n[i, 1])

    # ─── Optionaler Prompt — in denselben Raum projizieren ──
    prompt_out: dict | None = None
    if body.prompt and body.prompt.strip():
        p_emb = _encode_prompt(body.prompt.strip())
        if p_emb is not None:
            p_vec = np.array(p_emb, dtype=np.float32) - mean.squeeze()
            p_coords = p_vec @ components.T              # (2,)
            p_coords_n = p_coords / cmax
            # Top-K: cosine sim zwischen Prompt-Embedding (unzentriert,
            # L2-normalisiert) und jedem Szenen-Embedding.
            p_norm = np.array(p_emb, dtype=np.float32)
            X_norm = X / (np.linalg.norm(X, axis=1, keepdims=True) + 1e-8)
            sims = X_norm @ p_norm
            top_k_idx = np.argsort(sims)[-8:][::-1].tolist()
            prompt_out = {
                "text": body.prompt.strip(),
                "x": float(p_coords_n[0]),
                "y": float(p_coords_n[1]),
                "top_k": [
                    {"scene_id": scenes[i]["id"], "sim": float(sims[i])}
                    for i in top_k_idx
                ],
            }

    return {
        "scenes": scenes,
        "prompt": prompt_out,
        "variance_explained": variance_explained,
        "n": len(scenes),
    }


# ═══════════════════════════════════════════════════════════
# MATERIAL-BEZIEHUNGEN · Multicam-Erkennung
# ═══════════════════════════════════════════════════════════
# Wozu?
#   Bei Multicam-Material (mehrere Kameras filmen dieselbe Szene aus
#   verschiedenen Winkeln) muss das System erkennen, dass die Clips
#   KORRELIERT sind. Sonst werden sie als unabhängige Aufnahmen behandelt
#   und der Schnitt ignoriert die strukturelle Beziehung.
#
# Wir kombinieren zwei orthogonale Signale:
#   • Visuell: mittlere maximale CLIP-Cosine zwischen den Szenen-Embeddings
#     der beiden Clips. Hoch = ähnliche Bildinhalte (gleiche Szene).
#   • Audio: Cross-Korrelation der Chroma-Features. Hoch = gleiche Musik.
#
# Schwellen (empirisch, dokumentiert):
#   • visuell ≥ 0.78 ODER audio ≥ 0.60 → "related"
#   • visuell ≥ 0.85 UND audio ≥ 0.65 → "multicam"
# ───────────────────────────────────────────────────────────

class MultiCamRequest(BaseModel):
    clip_ids: list[str] = Field(..., description="Mindestens 2 Clips zum Vergleich")


def _audio_chroma_correlation(path_a: str, path_b: str, max_sec: float = 45.0) -> tuple[float, float]:
    """
    Vergleicht zwei Audios via Chroma-Features (12-dim, harmonisch).
    Liefert (Korrelation 0..1, zeitlicher Offset in Sekunden).
    Chroma ist gut für Musik, da invariant gegenüber Klangfarbe und teils Tonart.
    """
    try:
        import librosa
        import numpy as np
        y_a, sr = librosa.load(path_a, sr=22050, mono=True, duration=max_sec)
        y_b, _  = librosa.load(path_b, sr=22050, mono=True, duration=max_sec)
        # Auf gleiche Länge kürzen (das kürzere bestimmt)
        n = min(len(y_a), len(y_b))
        if n < sr * 2:
            return 0.0, 0.0
        y_a, y_b = y_a[:n], y_b[:n]
        # Chroma-CQT: 12-Bin pro Frame
        chroma_a = librosa.feature.chroma_cqt(y=y_a, sr=sr, hop_length=2048)
        chroma_b = librosa.feature.chroma_cqt(y=y_b, sr=sr, hop_length=2048)
        # Aggregat-Vergleich: mittlere Pitchklassen-Verteilung
        # (robust auch wenn die Clips nicht exakt synchron starten)
        mean_a = chroma_a.mean(axis=1)
        mean_b = chroma_b.mean(axis=1)
        # Cosine zwischen den 12-dim Verteilungen
        cos = float(np.dot(mean_a, mean_b) / (np.linalg.norm(mean_a) * np.linalg.norm(mean_b) + 1e-8))

        # Zeitlicher Offset via Frame-weise Cross-Korrelation
        # Frame-Hop = 2048 / 22050 ≈ 0.093 s/Frame
        hop_sec = 2048.0 / sr
        # Cross-Korrelation über Frame-Achse (mean over chroma bins)
        sig_a = chroma_a.mean(axis=0)
        sig_b = chroma_b.mean(axis=0)
        # Normalisieren
        sig_a = sig_a - sig_a.mean()
        sig_b = sig_b - sig_b.mean()
        if sig_a.std() < 1e-6 or sig_b.std() < 1e-6:
            offset = 0.0
        else:
            corr = np.correlate(sig_a, sig_b, mode="full") / (sig_a.std() * sig_b.std() * len(sig_a))
            peak = int(np.argmax(corr))
            offset_frames = peak - (len(sig_b) - 1)
            offset = float(offset_frames * hop_sec)
        return max(0.0, cos), offset
    except Exception as exc:
        logger.warning(f"Audio-Korrelation fehlgeschlagen ({path_a} vs {path_b}): {exc}")
        return 0.0, 0.0


def _visual_clip_similarity(embeddings_a: list[list[float]], embeddings_b: list[list[float]]) -> float:
    """
    Liefert Pair-Visual-Similarity als mittlere maximale Cosine zwischen
    den Szenen-Embeddings von Clip A und Clip B.
    """
    import numpy as np
    if not embeddings_a or not embeddings_b:
        return 0.0
    A = np.array([e for e in embeddings_a if e and any(v != 0 for v in e)], dtype=np.float32)
    B = np.array([e for e in embeddings_b if e and any(v != 0 for v in e)], dtype=np.float32)
    if len(A) == 0 or len(B) == 0:
        return 0.0
    A = A / (np.linalg.norm(A, axis=1, keepdims=True) + 1e-8)
    B = B / (np.linalg.norm(B, axis=1, keepdims=True) + 1e-8)
    # Pairwise cosine: A · B.T → (nA, nB)
    sims = A @ B.T
    # Für jedes A das Maximum gegen B nehmen, dann mitteln
    max_per_a = sims.max(axis=1)
    return float(max_per_a.mean())


# Cache: Multicam-Gruppen pro Clip-Set (sortierter Schlüssel).
# Wird sowohl vom Chat als auch von ai_schnitt verwendet, um doppelte
# librosa+CLIP-Berechnungen zu vermeiden.
_MULTICAM_GROUPS_CACHE: dict[str, list[set[str]]] = {}


def _get_multicam_groups(clips) -> list[set[str]]:
    """
    Liefert die Multicam-Gruppen für eine Liste von Clips als Liste von
    Sets von clip_ids. Verwendet denselben Schwellen-Algorithmus wie der
    /api/ai/multicam-Endpoint, aber als reusable Helper.

    Wird gecached, da die librosa-Audio-Analyse teuer ist.
    """
    if len(clips) < 2:
        return []
    key = "|".join(sorted(str(c.id) for c in clips))
    if key in _MULTICAM_GROUPS_CACHE:
        return _MULTICAM_GROUPS_CACHE[key]

    parent: dict[str, str] = {str(c.id): str(c.id) for c in clips}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: str, b: str) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    for i, a in enumerate(clips):
        for b in clips[i + 1:]:
            emb_a = [s.clip_embedding for s in a.szenen if s.clip_embedding]
            emb_b = [s.clip_embedding for s in b.szenen if s.clip_embedding]
            visual = _visual_clip_similarity(emb_a, emb_b)
            audio, _ = _audio_chroma_correlation(a.dateipfad, b.dateipfad)
            if (visual >= 0.85 and audio >= 0.65) or (audio >= 0.95 and visual >= 0.75):
                union(str(a.id), str(b.id))

    groups_map: dict[str, set[str]] = {}
    for c in clips:
        cid = str(c.id)
        r = find(cid)
        groups_map.setdefault(r, set()).add(cid)
    groups = [g for g in groups_map.values() if len(g) >= 2]
    _MULTICAM_GROUPS_CACHE[key] = groups
    return groups


def _dedupe_multicam_candidates(
    szenen: list[dict],
    multicam_groups: list[set[str]],
    bucket_sec: float = 6.0,
) -> list[dict]:
    """
    Multicam-Dedup MIT Kamera-Variation:

    Phase 1: Pro Zeit-Bucket (gleicher Gruppe) — sammle alle Kandidaten.
    Phase 2: Iteriere chronologisch durch die Buckets. Wähle pro Bucket
             diejenige Kamera, die zuletzt am WENIGSTEN verwendet wurde.
             Bei Gleichstand: höchster Score.

    Effekt: bei Multicam (z.B. Performance aus 3 Winkeln) entsteht ein
    natürlicher Wechsel zwischen den Kameras, anstatt dass ein Winkel
    dominiert. Das ist das, was ein menschlicher Multicam-Editor tut.
    """
    if not multicam_groups:
        return szenen

    # clip_id → group_idx
    clip_to_group: dict[str, int] = {}
    for gi, group in enumerate(multicam_groups):
        for cid in group:
            clip_to_group[cid] = gi

    def _score(s: dict) -> float:
        return s.get("_prompt_relevance", 0.0) or s.get("_energie", 0.0)

    # Phase 1: Bucket-Sammlung
    buckets: dict[tuple[int, int], list[dict]] = {}
    standalone: list[dict] = []
    for sz in szenen:
        gi = clip_to_group.get(sz["clip_id"])
        if gi is None:
            standalone.append(sz)
            continue
        bi = int(sz["start_zeit"] / bucket_sec)
        buckets.setdefault((gi, bi), []).append(sz)

    # Phase 2: chronologischer Walk-Through mit Kamera-Variation
    # Pro Gruppe halten wir einen Nutzungs-Zähler je Kamera; Bei Auswahl
    # bevorzugen wir die am wenigsten genutzte Kamera, danach Score.
    nutzung_je_gruppe: dict[int, dict[str, int]] = {}
    chosen: list[dict] = []
    for (gi, bi) in sorted(buckets.keys()):
        kandidaten = buckets[(gi, bi)]
        zaehler = nutzung_je_gruppe.setdefault(gi, {})

        def _rank(s: dict) -> tuple:
            # 1. Wenig genutzte Kamera bevorzugt (kleinster Zähler zuerst)
            # 2. Bei Gleichstand: höchster Score (also negativer Score sortiert klein nach klein)
            return (zaehler.get(s["clip_id"], 0), -_score(s))

        gewaehlt = min(kandidaten, key=_rank)
        zaehler[gewaehlt["clip_id"]] = zaehler.get(gewaehlt["clip_id"], 0) + 1
        chosen.append(gewaehlt)

    chosen.extend(standalone)
    return chosen


def _multicam_kamera_variation(geordnet: list[dict], multicam_groups: list[set[str]]) -> list[dict]:
    """
    Nach der Selektion: tausche aufeinanderfolgende Szenen DESSELBEN Clips
    durch andere Winkel desselben Moments aus, sofern verfügbar. Macht die
    Auswahl visuell abwechslungsreich (echtes Multicam-Editing-Gefühl).

    Achtung: dies erfordert, dass _dedupe_multicam_candidates VORHER alle
    Kandidaten gesehen hat — die nicht-gewählten Geschwister sind verloren.
    Diese Funktion ist daher ein No-Op solange wir die Alternativen nicht
    mit anliefern. Erstmal als Stub. Erweiterung für später.
    """
    return geordnet


@router.post("/multicam")
async def multicam_analyse(body: MultiCamRequest, db: AsyncSession = Depends(get_db)):
    """
    Analysiert paarweise die Beziehung zwischen den Clips:
      • Visuelle Ähnlichkeit (CLIP-Embeddings)
      • Audio-Ähnlichkeit (librosa Chroma-CQT)
      • Zeitlicher Offset (Cross-Korrelation der Chroma-Sequenz)
      • Klassifikation: multicam / related / different
    """
    if len(body.clip_ids) < 2:
        raise HTTPException(400, "Mindestens 2 Clips für die Beziehungs-Analyse.")

    # Clips + Szenen laden
    from sqlalchemy.orm import selectinload
    res = await db.execute(
        select(Clip).where(Clip.id.in_(body.clip_ids)).options(selectinload(Clip.szenen))
    )
    clips = res.scalars().all()
    clips_by_id = {str(c.id): c for c in clips}

    pairs: list[dict] = []
    for i, a_id in enumerate(body.clip_ids):
        for b_id in body.clip_ids[i + 1:]:
            a = clips_by_id.get(a_id)
            b = clips_by_id.get(b_id)
            if not a or not b:
                continue
            emb_a = [s.clip_embedding for s in a.szenen if s.clip_embedding]
            emb_b = [s.clip_embedding for s in b.szenen if s.clip_embedding]
            visual = _visual_clip_similarity(emb_a, emb_b)
            audio, offset = _audio_chroma_correlation(a.dateipfad, b.dateipfad)

            # Klassifikation — zwei Pfade zum "multicam"-Label:
            #   (a) klassisch: visuell hoch UND audio hoch
            #   (b) audio-dominiert: identische Tonspur + plausibler Bild-
            #       Inhalt → genügt für Multicam (typisch bei Performance-
            #       Aufnahmen, wo verschiedene Kamerawinkel visuell stark
            #       voneinander abweichen können, die Musik aber gleich ist).
            if (visual >= 0.85 and audio >= 0.65) or (audio >= 0.95 and visual >= 0.75):
                cls = "multicam"
            elif visual >= 0.78 or audio >= 0.60:
                cls = "related"
            else:
                cls = "different"

            pairs.append({
                "a_id": str(a.id),
                "b_id": str(b.id),
                "a_name": a.dateiname,
                "b_name": b.dateiname,
                "visual_sim": round(visual, 3),
                "audio_sim":  round(audio, 3),
                "audio_offset_s": round(offset, 2),
                "classification": cls,
            })

    # Multicam-Gruppen ableiten (Union-Find auf "multicam"-Kanten)
    parent: dict[str, str] = {cid: cid for cid in body.clip_ids}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: str, b: str) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    for p in pairs:
        if p["classification"] == "multicam":
            union(p["a_id"], p["b_id"])

    groups_map: dict[str, list[str]] = {}
    for cid in body.clip_ids:
        r = find(cid)
        groups_map.setdefault(r, []).append(cid)
    groups = [g for g in groups_map.values() if len(g) >= 2]

    return {
        "pairs": pairs,
        "multicam_groups": groups,
        "n": len(body.clip_ids),
        "schwellen": {
            "multicam": "(visuell ≥ 0.85 UND audio ≥ 0.65)  ODER  (audio ≥ 0.95 UND visuell ≥ 0.75)",
            "related":  "visuell ≥ 0.78 ODER audio ≥ 0.60",
        },
    }


# ═══════════════════════════════════════════════════════════
# REORGANIZE · User-Timeline narrativ neu ordnen
# ═══════════════════════════════════════════════════════════
# Wozu?
#   Der Editor hat seine Plans bereits ausgewählt und auf der Timeline
#   platziert. Er weiß WAS er drin haben will, ist sich aber nicht sicher
#   über die REIHENFOLGE. Dieser Endpoint nimmt die existierende Selektion
#   und ordnet sie nach dramaturgischer Bogenform: ruhige Eröffnung →
#   Steigerung → Höhepunkt → Ausklang. Die ausgewählten Plans bleiben
#   ALLE drin — nur ihre zeitliche Reihenfolge ändert sich.
# ───────────────────────────────────────────────────────────

class ReorganizeSegment(BaseModel):
    id: str
    clip_id: str | None = None
    szene_nr: int | None = None
    dauer: float
    mediaStart: float = 0.0
    track: str
    groupId: str | None = None
    label: str | None = None


class ReorganizeRequest(BaseModel):
    segmente: list[ReorganizeSegment]


@router.post("/reorganize")
async def reorganize_timeline(body: ReorganizeRequest, db: AsyncSession = Depends(get_db)):
    """
    Nimmt eine vom User platzierte Timeline und ordnet sie narrativ neu.

    Methode:
      1. Pro Video-Segment die zugehörige Szene aus DB nachschlagen (für Energie)
      2. Narrativen Bogen anwenden (low → high → low)
      3. Audio-Segmente folgen ihren Video-Geschwistern (groupId)
      4. Neue start-Zeiten berechnen
    """
    if not body.segmente:
        raise HTTPException(400, "Keine Segmente zum Umordnen.")

    # Video-Segmente trennen (V1) — Audio (A1) folgt via groupId
    video_segs = [s for s in body.segmente if s.track.lower().startswith("v")]
    audio_segs = [s for s in body.segmente if not s.track.lower().startswith("v")]
    if not video_segs:
        raise HTTPException(400, "Keine Video-Segmente zum Umordnen.")

    # Für jedes Video-Segment die zugehörige Szene aus DB laden (Energie + Rolle)
    enriched: list[dict] = []
    for vs in video_segs:
        szene_energie = 0.5
        if vs.szene_nr is not None and vs.clip_id:
            res = await db.execute(
                select(Szene)
                .where(Szene.clip_id == vs.clip_id, Szene.szenen_nr == vs.szene_nr)
            )
            sz = res.scalar_one_or_none()
            if sz and sz.analyse_visuelle:
                av = sz.analyse_visuelle
                if isinstance(av.get("energie"), (int, float)):
                    szene_energie = float(av["energie"])
        enriched.append({
            "_uid": vs.id,
            "_orig": vs,
            "_energie": szene_energie,
            "dauer": vs.dauer,
        })

    # Narrativen Bogen anwenden
    arc = _zwinge_narrativen_bogen(enriched)

    # Neue start-Zeiten berechnen, Reihenfolge gemäß arc
    neue_video_segs: list[dict] = []
    neue_audio_segs: list[dict] = []
    cursor = 0.0
    for a in arc:
        vs: ReorganizeSegment = a["_orig"]
        neue_video_segs.append({
            "id": vs.id,
            "clip_id": vs.clip_id,
            "szene_nr": vs.szene_nr,
            "track": vs.track,
            "start": round(cursor, 3),
            "dauer": round(vs.dauer, 3),
            "mediaStart": vs.mediaStart,
            "groupId": vs.groupId,
            "label": vs.label,
            "rolle": a.get("_rolle"),
        })
        # Audio-Geschwister (gleiche groupId) mit gleichem start + dauer
        if vs.groupId:
            for aud in audio_segs:
                if aud.groupId == vs.groupId:
                    neue_audio_segs.append({
                        "id": aud.id,
                        "clip_id": aud.clip_id,
                        "szene_nr": aud.szene_nr,
                        "track": aud.track,
                        "start": round(cursor, 3),
                        "dauer": round(aud.dauer, 3),
                        "mediaStart": aud.mediaStart,
                        "groupId": aud.groupId,
                        "label": aud.label,
                    })
        cursor += vs.dauer

    return {
        "segmente": neue_video_segs + neue_audio_segs,
        "anzahl": len(neue_video_segs),
        "gesamtdauer": round(cursor, 3),
        "arc_rollen": {
            r: sum(1 for s in neue_video_segs if s.get("rolle") == r)
            for r in [ROLLE_OUVERTURE, ROLLE_ACTION, ROLLE_TRANSITION, ROLLE_CLIMAX, ROLLE_CLOTURE]
        },
        "methodik": "Aristotelische Bogenform: Eröffnung (niedrige Energie) → Aufbau → Höhepunkt → Ausklang",
    }


@router.get("/providers")
async def llm_providers():
    """
    Gibt zurück, welche LLM-Provider aktuell konfiguriert sind.
    Hilft dem Frontend, die verfügbaren Optionen anzuzeigen.
    """
    return {
        "verfuegbar": {
            "ollama": True,  # immer verfügbar (lokal)
            "claude": bool(CLAUDE_API_KEY),
            "openai": bool(OPENAI_API_KEY),
            "gemini": bool(GEMINI_API_KEY),
        },
        "standard": (
            "claude" if CLAUDE_API_KEY else
            "openai" if OPENAI_API_KEY else
            "gemini" if GEMINI_API_KEY else
            "ollama"
        ),
        "modelle": {
            "claude": CLAUDE_MODEL,
            "openai": OPENAI_MODEL,
            "gemini": GEMINI_MODEL,
            "ollama": OLLAMA_MODEL,
        },
    }
