"""
[DEPRECATED — 2026-07-20]
Dieses Modul, geführter Schnitt über Katalog und [VORSCHLAG:]-Marken, wurde abgelöst durch
`backend/api/agent.py` (ReAct + 17 tools, streaming SSE via /api/agent/run).
Der Router ist in `main.py` stillgelegt, Import und include_router sind auskommentiert.
Die Datei bleibt erhalten, um die Entwicklung des Entwurfs für die Bachelorarbeit zu belegen.
NICHT ohne Absprache wieder anschließen, zwei parallele Assistenten stiften Verwirrung.

---
CinAssist — KI-Chat-Assistent für geführte Schnittgespräche

Endpunkte:
  POST /api/ai/chat       → eine Runde Konversation mit dem Assistenten

Workflow:
  1. Frontend lädt Clips
  2. Frontend ruft /api/ai/chat ohne `messages` auf
     → Backend baut Katalog aus DB-Szenen + sendet an Ollama
     → Antwort: 2-3 Sätze "was ich sehe" + offene Frage
  3. User antwortet, Frontend ruft /api/ai/chat mit erweiterter History
  4. Wenn der Assistent das Anliegen verstanden hat,
     antwortet er mit einem [VORSCHLAG: "..."]-Tag
  5. Frontend extrahiert den Prompt und ruft /api/ai/cut damit

Vorteil gegenüber dem alten "Stil-Chips"-Workflow:
  • Kein erzwungenes Kategorisieren — der User redet wie mit einem Kollegen
  • Der Assistent zeigt erst was ER SIEHT, bevor er fragt
  • 100 % lokal (llama3 via Ollama), keine Cloud-Abhängigkeit
  • Deterministisch reproduzierbar bei festem temperature
"""

from __future__ import annotations

import logging
import re

import httpx
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from backend.core.config import OLLAMA_BASE_URL, OLLAMA_MODEL
from backend.core.database import get_db, Clip
from backend.api.ai import _audio_chroma_correlation, _visual_clip_similarity, _detect_beats

logger = logging.getLogger("cinassist.chat")
router = APIRouter(prefix="/api/ai", tags=["KI-Chat"])


# ─── Datenstrukturen ────────────────────────────────────

class ChatMessage(BaseModel):
    role: str = Field(..., description="'user' oder 'assistant'")
    content: str


class ChatRequest(BaseModel):
    clip_ids: list[str] = Field(..., description="IDs der Clips, über die wir reden")
    messages: list[ChatMessage] = Field(default_factory=list, description="Bisherige Konversation")


class ChatResponse(BaseModel):
    message: str
    proposed_prompt: str | None = Field(None, description="Wenn gesetzt: Vorschlag für /api/ai/cut")
    proposed_stil: str | None = Field(None, description="energetisch | ausgewogen | ruhig — steuert Tempo/Dauer/Bogen")


# ─── Katalog-Aufbau ─────────────────────────────────────

# Cache der Multicam-Detektion: vermeidet, dass librosa bei jeder Chat-Runde
# erneut über die Audios läuft. Key = sortierte Clip-IDs als String.
_MULTICAM_CACHE: dict[str, str] = {}


def _detecte_multicam_summary(clips) -> str:
    """
    Liefert einen kurzen deutschsprachigen Hinweis-Block zu möglichen
    Multicam-Beziehungen. Wird in den System-Prompt-Katalog injiziert, damit
    der Assistent KONKRET sagen kann "diese drei Clips sind drei Winkel
    derselben Szene" statt sie als unabhängig zu behandeln.

    Methode: paarweise CLIP-Visualkorrelation + librosa Chroma-Cosine.
    Schwellen: visuell ≥ 0.78 ODER audio ≥ 0.60 → 'related' / 'multicam'.
    """
    if len(clips) < 2:
        return ""
    key = "|".join(sorted(str(c.id) for c in clips))
    if key in _MULTICAM_CACHE:
        return _MULTICAM_CACHE[key]

    multicam_pairs: list[tuple[str, str, float, float, float]] = []
    related_pairs: list[tuple[str, str, float, float]] = []

    for i, a in enumerate(clips):
        for b in clips[i + 1:]:
            emb_a = [s.clip_embedding for s in a.szenen if s.clip_embedding]
            emb_b = [s.clip_embedding for s in b.szenen if s.clip_embedding]
            visual = _visual_clip_similarity(emb_a, emb_b)
            audio, offset = _audio_chroma_correlation(a.dateipfad, b.dateipfad)
            if (visual >= 0.85 and audio >= 0.65) or (audio >= 0.95 and visual >= 0.75):
                multicam_pairs.append((a.dateiname, b.dateiname, visual, audio, offset))
            elif visual >= 0.78 or audio >= 0.60:
                related_pairs.append((a.dateiname, b.dateiname, visual, audio))

    parts: list[str] = []
    if multicam_pairs:
        parts.append("\n═══ AUTOMATISCH ERKANNTE MULTICAM-BEZIEHUNGEN ═══")
        parts.append(
            "Folgende Clip-Paare sind nach CLIP-Visualkorrelation UND Audio-Chroma-Korrelation\n"
            "höchstwahrscheinlich DIESELBE SZENE aus verschiedenen Kamerawinkeln:"
        )
        for a, b, v, au, off in multicam_pairs:
            parts.append(f"  • {a}  ↔  {b}   (visuell {v:.2f} · audio {au:.2f} · offset {off:+.2f}s)")
        parts.append(
            "→ Behandle diese Clips als Multicam in deinen Vorschlägen "
            "(z.B. 'Schnitt zwischen den 3 Kamerawinkeln auf Beats') und sage explizit, dass "
            "du dies erkannt hast.\n"
        )
    elif related_pairs:
        parts.append("\n═══ MÖGLICHE BEZIEHUNGEN ZWISCHEN CLIPS ═══")
        parts.append(
            "Folgende Paare zeigen Korrelation, aber unterhalb der Multicam-Schwelle "
            "(könnte z.B. dasselbe Set aber verschiedene Takes sein):"
        )
        for a, b, v, au in related_pairs:
            parts.append(f"  • {a}  ↔  {b}   (visuell {v:.2f} · audio {au:.2f})")
        parts.append("")

    out = "\n".join(parts)
    _MULTICAM_CACHE[key] = out
    return out


def _energie_emoji(e: float) -> str:
    """Visuelles Markierung für die Energie-Stufe (rein für LLM-Scan-Lesbarkeit)."""
    if e >= 0.75: return "🔥"
    if e >= 0.55: return "⚡"
    if e >= 0.35: return "•"
    return "·"


def _clip_profil(clip) -> dict:
    """
    Berechnet aggregierte Statistiken für einen Clip aus Szenen-Daten.
    Liefert kompakte Profil-Metriken, die der Chat-Assistent direkt verwerten kann.
    """
    szenen = list(clip.szenen)
    if not szenen:
        return {}

    # Visuelle Aggregate aus analyse_visuelle
    luminosite_vals, energie_vals, mouvement_vals, kontrast_vals = [], [], [], []
    for s in szenen:
        av = s.analyse_visuelle or {}
        if isinstance(av.get("luminosite"), (int, float)):
            luminosite_vals.append(float(av["luminosite"]))
        if isinstance(av.get("energie"), (int, float)):
            energie_vals.append(float(av["energie"]))
        if isinstance(av.get("mouvement"), (int, float)):
            mouvement_vals.append(float(av["mouvement"]))
        if isinstance(av.get("kontrast"), (int, float)):
            kontrast_vals.append(float(av["kontrast"]))

    # Dialog-Statistiken
    transkripte = [(s.transkription or "").strip() for s in szenen if (s.transkription or "").strip()]
    gesamt_woerter = sum(len(t.split()) for t in transkripte)
    szenen_mit_dialog = sum(1 for t in transkripte if t)
    dauer = float(clip.dauer or 0) or 1.0
    dialog_dichte = gesamt_woerter / dauer  # Wörter pro Sekunde

    # Embedding-Health
    embed_normen = []
    for s in szenen:
        if s.clip_embedding and any(v != 0 for v in s.clip_embedding):
            import math
            n = math.sqrt(sum(v * v for v in s.clip_embedding))
            embed_normen.append(n)

    def mean(xs): return (sum(xs) / len(xs)) if xs else 0.0
    def maxof(xs): return max(xs) if xs else 0.0
    def minof(xs): return min(xs) if xs else 0.0

    profil = {
        "n_szenen":         len(szenen),
        "mean_szenen_dauer": mean([s.dauer for s in szenen]),
        "luminanz_mean":    mean(luminosite_vals),
        "energie_mean":     mean(energie_vals),
        "energie_max":      maxof(energie_vals),
        "energie_min":      minof(energie_vals),
        "energie_spread":   maxof(energie_vals) - minof(energie_vals),
        "mouvement_mean":   mean(mouvement_vals),
        "kontrast_mean":    mean(kontrast_vals),
        "gesamt_woerter":   gesamt_woerter,
        "szenen_mit_dialog": szenen_mit_dialog,
        "dialog_dichte":    dialog_dichte,
        "embed_norm_mean":  mean(embed_normen),
        "embed_anteil":     len(embed_normen) / len(szenen) if szenen else 0.0,
    }
    return profil


# Cache der Beat-Detection im Chat-Kontext (vermeidet Doppelarbeit)
_BEATS_CHAT_CACHE: dict[str, dict] = {}


def _schluessel_beobachtungen(clips) -> str:
    """
    Berechnet die SCHLÜSSEL-FAKTEN über das Material in Python — präzise,
    nicht halluziniert. Diese Fakten werden dem LLM vorberechnet übergeben,
    damit es sie nur noch konversationell präsentieren muss.

    Grund: LLaMA3-7B ist zu klein, um Zahlen zuverlässig aus einem langen
    Katalog zu extrahieren — es kopiert stattdessen Beispielwerte. Indem wir
    die echten Zahlen liefern, kann es nicht mehr halluzinieren.
    """
    alle_szenen: list[tuple] = []  # (clip, szene)
    for clip in clips:
        for sz in clip.szenen:
            alle_szenen.append((clip, sz))
    if not alle_szenen:
        return ""

    def _energie(sz) -> float:
        av = sz.analyse_visuelle or {}
        v = av.get("energie")
        return float(v) if isinstance(v, (int, float)) else 0.0

    # Energie-Extrema
    peak_clip, peak_sz = max(alle_szenen, key=lambda cs: _energie(cs[1]))
    low_clip, low_sz = min(alle_szenen, key=lambda cs: _energie(cs[1]))

    total_dauer = sum(float(c.dauer or 0) for c in clips)
    total_szenen = len(alle_szenen)
    total_woerter = sum(
        len((sz.transkription or "").split()) for _, sz in alle_szenen
    )

    # Positionshinweis: wo liegt eine Szene innerhalb IHRES Clips?
    def _position(clip, sz) -> str:
        clip_szenen = sorted(clip.szenen, key=lambda s: s.szenen_nr)
        n = len(clip_szenen)
        if n <= 1:
            return "im Clip"
        try:
            idx = [s.szenen_nr for s in clip_szenen].index(sz.szenen_nr)
        except ValueError:
            return "im Clip"
        frac = idx / max(1, n - 1)
        if frac < 0.34:
            return "gegen Anfang"
        if frac < 0.67:
            return "in der Mitte"
        return "gegen Ende"

    lines = ["═══ SCHLÜSSEL-BEOBACHTUNGEN (vom System EXAKT berechnet — diese Fakten sind WAHR, übersetze sie in natürliche Sprache) ═══"]
    lines.append(f"  • Material: {len(clips)} Clip(s), {total_szenen} Szene(n), {total_dauer:.0f}s gesamt")
    lines.append(
        f"  • Energiereichster Moment: im Clip '{peak_clip.dateiname}', "
        f"{_position(peak_clip, peak_sz)}"
    )
    lines.append(
        f"  • Ruhigster Moment: im Clip '{low_clip.dateiname}', "
        f"{_position(low_clip, low_sz)}"
    )
    if total_dauer > 0:
        if total_woerter == 0:
            lines.append("  • Dialog: KEINE Sprache erkannt — reines Bild-/Musikmaterial")
        elif total_woerter < 15:
            lines.append(f"  • Dialog: nur sehr wenig Sprache ({total_woerter} Worte) — Bild/Musik dominiert")
        else:
            lines.append(f"  • Dialog: {total_woerter} Worte — Sprache ist präsent")

    # Tempo vom ersten Clip
    try:
        c0 = clips[0]
        cid = str(c0.id)
        if cid not in _BEATS_CHAT_CACHE:
            _BEATS_CHAT_CACHE[cid] = _detect_beats(cid, c0.dateipfad)
        tempo = _BEATS_CHAT_CACHE[cid].get("tempo", 0)
        if tempo > 0:
            lines.append(f"  • Tempo (Clip 1): ≈ {tempo:.0f} BPM")
    except Exception:
        pass

    lines.append("")
    return "\n".join(lines)


def _build_catalog(clips) -> str:
    """
    Baut den FULL-ACCESS Katalog für den Chat-Assistenten.

    Designprinzip: Der Chat ist die Boss-Final-Stufe der Pipeline. Phase 1
    (Ingestion) und Phase 2 (Multimodal-Analyse) existieren, um Phase 3
    (Schnitt-Generierung über den Chat) zu speisen. Folglich muss der Chat
    SÄMTLICHE Phase-2-Daten in einer kompakten, scanbaren Form sehen.

    Aufbau:
      1) MATERIAL-OVERVIEW: aggregierte Stats über alle Clips
      2) PRO CLIP: Profil-Block (Visuell + Audio + Dialog-Statistik + Tempo)
      3) PRO CLIP: ALLE Szenen als kompakte 1-Zeilen-Einträge mit
         Energie-Markierung, Description, Dialog-Snippet, AV-Metriken
    """
    lines: list[str] = []

    # ── 1) Material-Overview (global) ────────────────────
    total_dauer = sum(float(c.dauer or 0) for c in clips)
    total_szenen = sum(len(c.szenen) for c in clips)
    total_woerter = sum(
        sum(len((s.transkription or "").split()) for s in c.szenen)
        for c in clips
    )
    lines.append("═══ MATERIAL-OVERVIEW ═══")
    lines.append(f"  Clips: {len(clips)} · Dauer gesamt: {total_dauer:.0f}s · Szenen: {total_szenen}")
    lines.append(f"  Dialog: {total_woerter} Worte gesamt, {(total_woerter / total_dauer) if total_dauer else 0:.2f} Wörter/s")

    # ── 2) Pro Clip: Profil + alle Szenen ───────────────
    for i, clip in enumerate(clips, 1):
        szenen = sorted(clip.szenen, key=lambda s: s.szenen_nr)
        n = len(szenen)
        dauer = float(clip.dauer or 0)
        p = _clip_profil(clip)

        lines.append("")
        lines.append(f"═══ CLIP {i} — {clip.dateiname} ({dauer:.0f}s, {n} Szenen) ═══")

        # ─ Tempo (librosa, gecached) ─
        try:
            cid = str(clip.id)
            if cid not in _BEATS_CHAT_CACHE:
                _BEATS_CHAT_CACHE[cid] = _detect_beats(cid, clip.dateipfad)
            tempo_info = _BEATS_CHAT_CACHE[cid]
            tempo = tempo_info.get("tempo", 0)
            if tempo > 0:
                lines.append(f"  Audio: tempo ≈ {tempo:.0f} BPM ({len(tempo_info.get('beats', []))} Beats erkannt)")
        except Exception:
            pass

        if p:
            lines.append(
                f"  Visuell: Luminanz {p['luminanz_mean']:.2f} · Bewegung {p['mouvement_mean']:.2f} · "
                f"Kontrast {p['kontrast_mean']:.2f}"
            )
            lines.append(
                f"  Energie: ⌀ {p['energie_mean']:.2f}  (min {p['energie_min']:.2f} → max {p['energie_max']:.2f}, "
                f"Spannweite {p['energie_spread']:.2f})"
            )
            lines.append(
                f"  Dialog: {p['gesamt_woerter']} Worte in {p['szenen_mit_dialog']}/{n} Szenen "
                f"({p['dialog_dichte']:.2f} W/s)"
            )
            lines.append(
                f"  CLIP-Embeddings: {p['embed_anteil'] * 100:.0f}% der Szenen haben gültige Vektoren "
                f"(mean L2-Norm {p['embed_norm_mean']:.1f})"
            )

        # ─ ALLE Szenen als kompakte UNIFIZIERTE Zeilen ─
        # Jede Zeile enthält ALLES, was Phase 2 zu dieser Szene weiß:
        #   - PySceneDetect: Zeitbereich + Dauer
        #   - PIL-Analyse: Energie, Bewegung, Kontrast, Luminanz, Farbtemperatur
        #   - LLaVA: Visualbeschreibung (Subjekt, Framing, Setting, Beleuchtung)
        #   - Whisper: Transkript (nur wenn nicht-leer und nicht-halluziniert)
        lines.append("  Szenen (PIL-Metriken + LLaVA + Whisper kombiniert):")
        for s in szenen:
            av = s.analyse_visuelle or {}
            e        = float(av.get("energie", 0) or 0)
            bewegung = float(av.get("mouvement", 0) or 0)
            kontrast = float(av.get("kontrast", 0) or 0)
            luminanz = float(av.get("luminosite", 0) or 0)
            schaerfe = float(av.get("schaerfe", 0) or 0)
            farbtemp = str(av.get("temperature", "") or "")[:6]
            mark = _energie_emoji(e)

            # LLaVA-Beschreibung: kompakt, ohne Newlines
            descr = (s.beschreibung or "").strip().replace("\n", " ").replace("  ", " ")
            if len(descr) > 130:
                descr = descr[:127] + "…"

            # Whisper-Transkript: nur wenn Inhalt da
            trans = (s.transkription or "").strip().replace("\n", " ")
            if len(trans) > 70:
                trans = trans[:67] + "…"

            # Eine Zeile: alle Phase-2-Daten kompakt zusammengeführt
            metriken = f"E{e:.2f} B{bewegung:.2f} K{kontrast:.2f} L{luminanz:.2f} S{schaerfe:.2f}"
            if farbtemp:
                metriken += f" {farbtemp}"
            zeile = (
                f"    S{s.szenen_nr:02d} [{s.start_zeit:5.1f}-{s.end_zeit:5.1f}s, {s.dauer:4.1f}s] "
                f"{mark} {metriken}\n"
                f"         🎬 {descr or '(keine Beschreibung)'}"
            )
            lines.append(zeile)
            if trans:
                lines.append(f'         💬 "{trans}"')
            else:
                lines.append('         💬 (stumm)')

    return "\n".join(lines)


# ─── System-Prompt (das Herzstück) ──────────────────────

SYSTEM_PROMPT_TEMPLATE = """Du bist ein erfahrener Schnittassistent. Du sprichst mit dem Editor wie ein Kollege: konkret, knapp, neugierig, nie belehrend.

Du bist die ENDSTUFE einer mehrstufigen KI-Pipeline. Phase 1 (Ingestion) und Phase 2 (Multimodal-Analyse via PySceneDetect, Whisper, LLaVA, CLIP) haben für dich folgende Daten bereitgestellt:

{catalog}

═══ KATALOG-LEGENDE ═══

  S01 [12.3-25.1s, 12.8s] 🔥 E0.87 B0.65 K0.51 L0.42 S0.78 warm
       🎬 <LLaVA-Visualbeschreibung — Subjekt, Framing, Setting, Beleuchtung>
       💬 "<Whisper-Transkript>" oder "(stumm)"

  • S01            = Szene 1 (PySceneDetect via HSV-Threshold 27)
  • [12.3-25.1s]   = Zeitbereich im Quellclip
  • 12.8s          = Szenendauer
  • 🔥/⚡/•/·       = Energie-Marker (≥0.75 / ≥0.55 / ≥0.35 / niedrig)
  • E (Energie)    = Gesamt-Mix aus Bewegung + Kontrast + Audio
  • B (Bewegung)   = Pixel-Differenz zwischen 3 Sample-Frames (PIL)
  • K (Kontrast)   = Histogramm-Spreizung (PIL)
  • L (Luminanz)   = mittlere Helligkeit (PIL)
  • S (Schärfe)    = Laplacian-Varianz (PIL)
  • warm/kalt      = Farbtemperatur via RGB-Mean (PIL)
  • 🎬 LLaVA       = Vision-Language-Model, FAKTISCH (keine Story-Erfindung)
  • 💬 Whisper     = lokal transkribiert; "(stumm)" wenn Audio leer
                     oder Halluzination ("Vielen Dank" etc.) gefiltert

  Pro Clip stehen zusätzlich aggregierte Profile zur Verfügung:
    Audio: tempo ≈ X BPM (wenn detektiert)
    Visuell: Luminanz/Bewegung/Kontrast-Mittelwerte
    Energie: Spannweite min→max (wichtig für Erkennung von Climax-Momenten)
    Dialog-Dichte: Wörter pro Sekunde

  Wenn das System Multicam-Beziehungen erkannt hat, stehen diese GANZ OBEN
  im Katalog mit visueller + Audio-Korrelations-Wert + Zeit-Offset.

═══ GRUNDREGELN ═══

1. ARBEITE NUR MIT DEM KATALOG. Erfinde keine Inhalte, Charaktere oder Handlungen, die nicht klar in den Visualbeschreibungen oder Dialogen stehen. Wenn unsicher: "ich bin mir nicht sicher".

2. NUTZE DIE QUANTITATIVEN DATEN AKTIV. Wenn du eine Strategie vorschlägst, BEGRÜNDE sie mit konkreten Zahlen aus dem Katalog:
   • "Im Bereich von Szene 8-12 ist die Energie hoch (E0.8+) — dort lohnt sich ein Climax"
   • "Tempo ist 124 BPM — Schnitte alle 4 Beats (1.9s) passen rhythmisch"
   • "Dialog ist nur in 4 von 30 Szenen (Dichte 0.12 W/s) — Musik dominiert, weniger Worte schneiden"
   • "Multicam erkannt — wir können zwischen den 3 Winkeln auf jedem 8. Beat wechseln"
   Sei spezifisch. Allgemeine Aussagen sind verboten, wenn konkrete Zahlen verfügbar sind.

3. ERKENNE DAS GENRE aus den Daten. Anpassungsfähig sein:
   • Musikperformance → Tempo erkannt + niedrige Dialog-Dichte → über Kamera-Winkel, Rhythmus, Beat-Sync reden
   • Interview / Vlog → hohe Dialog-Dichte → über Dialog-Momente, Reaktionen, Pausen reden
   • Dokumentation → mittlere Dialog-Dichte + viele Szenen → über Erzählstrang reden
   • Sport / Action → hohe Energie-Spannweite → über Schlüsselmomente, Tempo reden
   • Natur / Reise → niedrige Bewegung + hohe Luminanz-Varianz → über Stimmung reden
   Sprich NICHT in vorgefertigten Kategorien — leite das Genre aus den Profilen ab.

4. ERKENNE MULTICAM. Wenn das System unter "AUTOMATISCH ERKANNTE MULTICAM-BEZIEHUNGEN" Paare aufgelistet hat, behandle sie als Winkel derselben Szene. Erwähne das in der ersten Nachricht explizit ("Aus drei Kamerawinkeln derselben Aufnahme gefilmt"). Schlage Strategien vor, die das ausnutzen (Winkel-Wechsel, Beat-Sync).

5. NUTZE DEN GANZEN KATALOG. Du siehst ALLE Szenen, nicht nur die ersten paar. Erwähne die interessantesten Szenen mit ihrer Nummer und Zeit — der User soll sehen, dass du tatsächlich gelesen hast.

═══ ERSTE NACHRICHT (KONVERSATION LEER) ═══

Du sprichst mit einem MENSCHEN, nicht mit einer Maschine. Schreibe einen
flüssigen, natürlichen Text — wie ein Kollege, der gerade das Material
gesichtet hat und dir erzählt, was ihm aufgefallen ist.

ABSOLUT VERBOTEN in der sichtbaren Nachricht:
  ✗ Struktur-Labels wie "TEIL 1", "TEIL 2", "Beobachtung 1:"
  ✗ Rohe Zahlen wie "E0.44", "Szene 2", "0.36", "B0.65"
  ✗ Technische Begriffe wie "Energie-Wert", "Katalog", "Faktenblock"
  ✗ Aufzählungen mit "*" oder "-" für die Beobachtungen

RICHTIG: Übersetze die SCHLÜSSEL-BEOBACHTUNGEN in MENSCHLICHE Sprache.
  Statt "Energie-Höhepunkt: Clip 1 Szene 2 (E0.44)"
    → "der lebendigste Moment liegt im ersten Video, gleich am Anfang"
  Statt "Dialog: KEINE Sprache erkannt"
    → "es wird nicht gesprochen — Bild und Musik tragen alles"
  Statt "Multicam erkannt (Audio 0.97)"
    → "die drei Clips zeigen dieselbe Szene aus verschiedenen Blickwinkeln"

Deine erste Nachricht hat GENAU drei kurze Absätze. KURZ HALTEN — der
dritte Absatz mit den A/B/C-Wegen ist PFLICHT und darf NICHT fehlen.

  Absatz 1 (1-2 Sätze): Was zeigen die Videos + wie hängen sie zusammen
    (ähnlich? dieselbe Szene aus mehreren Winkeln? verschiedene Momente?).
    NICHT jeden Clip einzeln durchgehen — eine Gesamtaussage.

  Absatz 2 (1-2 Sätze): Was ist dir aufgefallen — der lebendigste und der
    ruhigste Moment (in Worten: "am Anfang", "in der Mitte", "gegen Ende"),
    ob gesprochen wird.

  Absatz 3 — PFLICHT, exakt dieses Format:

    Ich sehe drei Möglichkeiten, das zu schneiden:

    A) <Strategie in einem Halbsatz> — <warum, menschliche Sprache>
    B) <Strategie> — <warum>
    C) <Strategie> — <warum>

    Mein Tipp wäre <A, B oder C>. Aber sag mir, wie du es dir vorstellst.

⚠ Wenn der dritte Absatz mit den drei Wegen A/B/C fehlt, ist die Antwort
FALSCH. Halte Absatz 1 und 2 KURZ, damit Energie für Absatz 3 bleibt.

REGELN FÜR DIE DREI WEGE:
  • Jeder Weg = eine SCHNITT-Strategie (WIE geschnitten wird), kurz.
  • Jeder Weg hat einen "weil"-Grund in MENSCHLICHER Sprache (kein "E0.44").
  • Die Wege ergeben sich aus DIESEM Material — nie dieselben generischen
    drei Optionen für jedes Video.
  • Die WAHREN Fakten stehen im SCHLÜSSEL-BEOBACHTUNGEN-Block — stütze dich
    darauf, aber zeige die rohen Werte NIEMALS dem User.

═══ FOLGE-NACHRICHTEN ═══

- Stelle EINE konkrete Frage zur gewählten Richtung — verweise dabei auf
  konkrete Szenen/Werte ("Soll der Peak bei Szene 7 am Ende stehen, oder
  früher?"). Keine vagen Fragen.
- Maximal 2-3 Sätze.

═══ WENN DAS ANLIEGEN KLAR IST (nach 1-3 Austauschen) ═══

Antworte in dieser GENAUEN Struktur :

  1. Spiegele in EINEM Satz die WIRKLICH vom User gewählte Richtung und
     erkläre kurz, WARUM der gewählte Stil zum Material passt (mit einem
     Daten-Bezug). Beispiel der Art: "Gut — ein energetischer Schnitt, das
     passt zu den 124 BPM und dem Energie-Peak in Szene 7."
     (Kopiere NIEMALS das Schema-Beispiel unten wörtlich.)
  2. Leere Zeile.
  3. Schließe ab mit ZWEI Tags untereinander :

  [VORSCHLAG: "english visual prompt here"]
  [STIL: energetisch ODER ausgewogen ODER ruhig]

───── DIE ZWEI TAGS ERKLÄRT ─────

  [VORSCHLAG: "..."]  = WAS visuell gesucht wird (englischer CLIP-Prompt).
  [STIL: ...]         = WIE geschnitten wird. Genau EINER dieser drei Werte:
     • energetisch  → schnelle, kurze Schnitte, hohe Energie, dramatischer Bogen
       (für: Action, schnelle Musik, Werbung, sportliche Energie)
     • ausgewogen   → mittlere Schnittlänge, dramatischer Bogen
       (für: die meisten Musikvideos, Trailer, Kurzfilm)
     • ruhig        → lange Einstellungen, sparsame Schnitte, CHRONOLOGISCH
       (für: Dokumentation, Interview, intime/langsame Stimmung, Landschaft)

  Wähle [STIL] passend zur vom User gewählten A/B/C-Richtung:
     "Energetisch / schneller Wechsel"  → [STIL: energetisch]
     "Intim und langsam / lange Close-Ups" → [STIL: ruhig]
     "Dokumentarisch / chronologisch"   → [STIL: ruhig]
     "Ausgewogen / Bogen"               → [STIL: ausgewogen]

────────────────────────────────
BEISPIEL-SCHEMA (Platzhalter — NICHT wörtlich kopieren!) :

  Gut, gehen wir mit <der vom User gewählten Richtung in deinen Worten>.

  [VORSCHLAG: "<englischer prompt passend zum Material>"]
  [STIL: <energetisch|ausgewogen|ruhig je nach Wahl>]

────────────────────────────────
ABSOLUT VERBOTEN — folgende falsche Formate :

  ✗ english search prompt for visual retrieval system: "..."   (keine Klammern)
  ✗ Suchprompt: "..."                                          (falscher Schlüssel)
  ✗ Das Beispiel wörtlich kopieren ("intime Close-Ups...")
  ✗ Mit deutschem Text in den VORSCHLAG-Anführungszeichen
  ✗ [STIL] weglassen — beide Tags sind PFLICHT

NUR [VORSCHLAG: "english text"] + [STIL: wert] WIRD VOM SYSTEM VERARBEITET.
────────────────────────────────

Englische Prompt-Inspiration je nach Genre :
  Musik:        "close-up of singer face with strong emotional expression"
  Doku:         "wide establishing shots of the location"
  Interview:    "speaker giving direct eye contact to camera"
  Sport/Action: "moments of high-speed movement and impact"
  Werbung:      "product close-ups with clean composition"
  Reise/Natur:  "golden hour landscape with warm lighting"
  Event:        "candid emotional reactions of guests"
  Tutorial:     "demonstration of the key step in detail"

═══ ABSOLUTE VERBOTE ═══

- Niemals Inhalte erfinden, die nicht im Katalog stehen
- Niemals "Als KI-Assistent…", "Gerne!", "Lass mich wissen…"
- Niemals mehr als 4 Sätze pro Nachricht
- Niemals mehrere Fragen in einer Nachricht (außer A/B/C im ersten Schritt)
- Niemals dieselbe drei A/B/C-Vorlage verwenden — IMMER an das tatsächliche Material anpassen

STIL: konkret, knapp, kollegial, neugierig — nie vage. Sprich Deutsch."""


# ─── Ollama-Aufruf ──────────────────────────────────────

async def _call_ollama_chat(system: str, messages: list[dict]) -> str:
    """
    Ruft Ollamas /api/chat-Endpunkt auf (chat completion Format, nicht /api/generate).
    Vorteil: native Multi-Turn-Konversation, kein manuelles Prompt-Zusammenkleben.
    """
    payload = {
        "model": OLLAMA_MODEL,
        "messages": [{"role": "system", "content": system}] + messages,
        "stream": False,
        "options": {
            "temperature": 0.5,     # leicht kreativ aber konsistent
            # 620 Tokens: die erste Nachricht ist jetzt eine echte Analyse
            # (3-4 Beobachtungen + Einschätzung + 3 begründete Wege +
            # Empfehlung) — 380 würde mitten im Satz abschneiden.
            "num_predict": 620,
        },
    }
    async with httpx.AsyncClient(timeout=90.0) as client:
        resp = await client.post(f"{OLLAMA_BASE_URL}/api/chat", json=payload)
        resp.raise_for_status()
        data = resp.json()
        return data.get("message", {}).get("content", "").strip()


def _drei_wege_block(multicam_aktiv: bool) -> str:
    """
    Liefert den GARANTIERTEN A/B/C-Optionsblock für die erste Nachricht.

    Grund: LLaMA3-7B ist zu klein, um nach einer ausführlichen Beschreibung
    ZUVERLÄSSIG noch den strukturierten A/B/C-Block zu produzieren — es
    "vergisst" ihn. Wir lassen das LLM die Beschreibung machen (das kann es)
    und hängen die drei Wege deterministisch in Python an. Die Optionen
    passen sich dem Material an (Multicam erkannt? → eigener Weg C).

    Die Frontend-Logik extrahiert "A)/B)/C)" → klickbare Buttons.
    """
    weg_c = (
        "C) Multicam-Wechsel — zwischen den Kamerawinkeln auf den Rhythmus schneiden"
        if multicam_aktiv else
        "C) Ausgewogen — ein dramatischer Bogen: ruhiger Einstieg, Höhepunkt, Ausklang"
    )
    return (
        "\n\nIch sehe drei Möglichkeiten, das zu schneiden:\n\n"
        "A) Energetisch — schnelle, kurze Schnitte, die das Tempo betonen\n"
        "B) Ruhig — lange Einstellungen, sparsame Schnitte, chronologisch\n"
        f"{weg_c}\n\n"
        "Welche spricht dich an? Klick eine Option — oder beschreib deine eigene Idee."
    )


# ─── Endpunkt ───────────────────────────────────────────

@router.post("/chat", response_model=ChatResponse)
async def chat_turn(body: ChatRequest, db: AsyncSession = Depends(get_db)) -> ChatResponse:
    """
    Eine Runde Konversation mit dem Schnittassistenten.

    • Mit leerem messages-Array: der Assistent erzeugt seine erste Nachricht
      (Was sehe ich? + offene Frage).
    • Mit messages-Array: der Assistent reagiert auf den letzten User-Turn.
    • Wenn der Assistent das Anliegen verstanden hat: Antwort enthält
      `proposed_prompt`, den das Frontend an /api/ai/cut weitergeben kann.
    """
    if not body.clip_ids:
        raise HTTPException(400, "Mindestens ein Clip muss ausgewählt sein.")

    # ── Clips mit Szenen laden (lazyload würde async problematisch sein) ──
    result = await db.execute(
        select(Clip)
        .where(Clip.id.in_(body.clip_ids))
        .options(selectinload(Clip.szenen))
    )
    clips = result.scalars().all()
    if not clips:
        raise HTTPException(404, "Ausgewählte Clips wurden nicht gefunden.")

    # ── Katalog + System-Prompt vorbereiten ──
    catalog = _build_catalog(clips)
    # Multicam-Erkennung: per CLIP + librosa-Chroma. Wird in den Katalog
    # injiziert, sodass das LLM KONKRET sagen kann "diese 3 Clips sind 3
    # Winkel derselben Szene" statt sie als unabhängig zu behandeln.
    multicam_block = _detecte_multicam_summary(clips)
    # Schlüssel-Beobachtungen: vom System EXAKT berechnete Fakten. Stehen
    # ganz oben, damit das LLM echte Zahlen verwendet statt zu halluzinieren.
    fakten_block = _schluessel_beobachtungen(clips)
    catalog = fakten_block + "\n" + (multicam_block + "\n" if multicam_block else "") + catalog
    system = SYSTEM_PROMPT_TEMPLATE.format(catalog=catalog)

    messages = [{"role": m.role, "content": m.content} for m in body.messages]

    # Bei leerer History: synthetische erste Eingabe, um den Assistenten zu starten
    ist_erste_nachricht = not messages
    if not messages:
        messages = [{"role": "user", "content": "Hi! Was siehst du in meinen Clips?"}]
        logger.info(f"Chat startet — {len(clips)} Clips im Katalog")
    else:
        logger.info(f"Chat-Turn {len(messages)} — {len(clips)} Clips im Katalog")

    # ── Ollama anrufen ──
    try:
        antwort = await _call_ollama_chat(system, messages)
    except httpx.HTTPError as exc:
        logger.error(f"Ollama-Anruf fehlgeschlagen: {exc}")
        raise HTTPException(503, f"Ollama nicht erreichbar: {exc}")
    except Exception as exc:
        logger.exception(f"Unerwarteter Fehler im Chat: {exc}")
        raise HTTPException(500, f"Chat-Fehler: {exc}")

    # ── Erste Nachricht: A/B/C-Optionen GARANTIEREN ──
    # LLaMA3-7B "vergisst" nach einer langen Beschreibung oft den
    # A/B/C-Block. Wir prüfen und hängen ihn deterministisch an, falls er
    # fehlt — so erscheinen IMMER klickbare Optionen.
    if ist_erste_nachricht:
        hat_abc = bool(re.search(r'^\s*A\)', antwort, re.MULTILINE))
        if not hat_abc:
            antwort = antwort.rstrip() + _drei_wege_block(bool(multicam_block))
            logger.info("A/B/C-Optionsblock deterministisch angehängt (LLM hatte ihn ausgelassen)")

    # ── Strukturierter Vorschlag extrahieren ──
    # Erkennt mehrere Formatvarianten, die LLaMA3 manchmal produziert.
    # Reihenfolge: spezifisch → generisch.
    proposed_prompt: str | None = None
    patterns: list[tuple[str, re.Pattern]] = [
        # 1) Kanonisches Format: [VORSCHLAG: "..."]
        (r'\[VORSCHLAG\]', re.compile(r'\[VORSCHLAG:\s*"([^"]+)"\]', re.IGNORECASE)),
        # 2) Variante ohne Klammern: VORSCHLAG: "..."
        (r'VORSCHLAG: "..."',  re.compile(r'\bVORSCHLAG:\s*"([^"]+)"',    re.IGNORECASE)),
        # 3) LLM-Variante: english search prompt for visual retrieval system: "..."
        (r'english search prompt: "..."',
         re.compile(r'english\s+(?:search\s+)?prompt[^"]{0,40}:\s*"([^"]+)"', re.IGNORECASE)),
        # 4) Andere Variante: search prompt: "..."
        (r'search prompt: "..."',
         re.compile(r'\bsearch\s+prompt[^"]{0,30}:\s*"([^"]+)"', re.IGNORECASE)),
        # 5) Suchprompt: "..."
        (r'Suchprompt: "..."',
         re.compile(r'\bSuchprompt[^"]{0,30}:\s*"([^"]+)"', re.IGNORECASE)),
    ]
    matched_pattern_name: str | None = None
    for name, pat in patterns:
        m = pat.search(antwort)
        if m:
            proposed_prompt = m.group(1).strip()
            matched_pattern_name = name
            # Bereinige die sichtbare Nachricht: entferne die gesamte Vorschlag-Zeile
            # (z.B. "english search prompt for visual retrieval system: "..."")
            antwort_clean = pat.sub('', antwort)
            # Entferne mehrere aufeinanderfolgende Leerzeilen / Trailing-Reste
            antwort_clean = re.sub(r'\n\s*\n\s*\n+', '\n\n', antwort_clean).strip()
            # Wenn nach der Bereinigung kaum noch was übrig ist, behalte das Original
            if len(antwort_clean) >= 20:
                antwort = antwort_clean
            break

    if proposed_prompt:
        logger.info(f"Vorschlag extrahiert via {matched_pattern_name}: '{proposed_prompt}'")

    # ── [STIL: ...] extrahieren ──
    # Der Stil steuert Tempo, Schnittlänge und Bogenform im Cut-Endpoint.
    # Akzeptierte Werte: energetisch / ausgewogen / ruhig.
    proposed_stil: str | None = None
    stil_match = re.search(r'\[STIL:\s*(energetisch|ausgewogen|ruhig)\s*\]', antwort, re.IGNORECASE)
    if stil_match:
        proposed_stil = stil_match.group(1).lower()
        # Stil-Tag aus der sichtbaren Nachricht entfernen
        antwort = re.sub(r'\[STIL:[^\]]*\]', '', antwort)
        antwort = re.sub(r'\n\s*\n\s*\n+', '\n\n', antwort).strip()
        logger.info(f"Stil extrahiert: '{proposed_stil}'")
    elif proposed_prompt:
        # Fallback: wenn ein Vorschlag da ist aber kein expliziter Stil,
        # leite ihn heuristisch aus dem sichtbaren Text ab.
        low = antwort.lower()
        if any(w in low for w in ("schnell", "energetisch", "dynamisch", "rasant", "action")):
            proposed_stil = "energetisch"
        elif any(w in low for w in ("ruhig", "langsam", "intim", "chronologisch", "dokumentar", "sparsam")):
            proposed_stil = "ruhig"
        else:
            proposed_stil = "ausgewogen"
        logger.info(f"Stil heuristisch abgeleitet: '{proposed_stil}'")

    return ChatResponse(
        message=antwort,
        proposed_prompt=proposed_prompt,
        proposed_stil=proposed_stil,
    )
