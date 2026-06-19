"""
CinAssist — KI-Schnitt API

POST /api/ai/cut  → KI-basierten Schnitt anfordern

Stile:
  - kinematisch: Visuell abwechslungsreich, narrativer Bogen, kurze dynamische Schnitte
  - dokumentarisch: Chronologisch, längere Takes, Dialog-fokussiert
  - schnell: Kurze Szenen, hohe Abwechslung, Highlight-Reel
"""

import json
import logging
import math
import uuid

import httpx
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.config import OLLAMA_BASE_URL, OLLAMA_MODEL
from backend.core.database import get_db, Clip, Szene, Timeline, Job

logger = logging.getLogger("cinassist.ai")
router = APIRouter(prefix="/api/ai", tags=["KI"])


# ─── Stil-Konfigurationen ──────────────────────────────
STIL_CONFIG = {
    "kinematisch": {
        "min_szenen_dauer": 1.5,       # Mindestdauer pro Szene (Sek.)
        "max_szenen_dauer": 12.0,      # Maximaldauer pro Szene
        "visuell_diversitaet": 0.7,    # Hohe visuelle Abwechslung (0-1)
        "tempo": 0.6,                  # Mittleres Tempo (0=langsam, 1=schnell)
        "dialog_gewicht": 0.3,         # Weniger Dialog, mehr visuell
        "llm_anweisung": (
            "Du bist ein preisgekrönter Filmeditor. Erstelle einen kinematischen Schnitt.\n"
            "Regeln:\n"
            "- Beginne mit einer starken Eröffnungsszene (visuell eindrucksvoll)\n"
            "- Variiere die Schnitttempo: kurze dynamische Schnitte wechseln mit ruhigeren Momenten\n"
            "- Maximiere visuelle Abwechslung zwischen aufeinanderfolgenden Szenen\n"
            "- Setze Dialog-Szenen als narrativen Ankerpunkt\n"
            "- Baue zu einem visuellen Höhepunkt auf\n"
            "- Schließe mit einer ruhigen, einprägsamen Szene ab\n"
        ),
    },
    "dokumentarisch": {
        "min_szenen_dauer": 3.0,
        "max_szenen_dauer": 30.0,
        "visuell_diversitaet": 0.3,
        "tempo": 0.3,
        "dialog_gewicht": 0.8,
        "llm_anweisung": (
            "Du bist ein Dokumentarfilm-Editor. Erstelle einen chronologischen, ruhigen Schnitt.\n"
            "Regeln:\n"
            "- Behalte die zeitliche Reihenfolge bei\n"
            "- Bevorzuge längere Takes mit Dialog\n"
            "- Vermeide schnelle Schnitte\n"
            "- Lass den Zuschauer die Szene aufnehmen\n"
        ),
    },
    "schnell": {
        "min_szenen_dauer": 0.5,
        "max_szenen_dauer": 4.0,
        "visuell_diversitaet": 0.9,
        "tempo": 0.9,
        "dialog_gewicht": 0.1,
        "llm_anweisung": (
            "Du bist ein Music-Video-Editor. Erstelle einen schnellen, energetischen Schnitt.\n"
            "Regeln:\n"
            "- Sehr kurze Szenen (0.5–4 Sekunden)\n"
            "- Maximale visuelle Abwechslung\n"
            "- Highlight-Reel-Stil\n"
            "- Ignoriere Dialog, fokussiere auf visuelle Wirkung\n"
        ),
    },
}


class AiCutRequest(BaseModel):
    stil: str = "kinematisch"
    prompt: str | None = None
    clip_ids: list[str]


# ─── Hilfsfunktionen ───────────────────────────────────

def _cosine_similarity(a: list[float], b: list[float]) -> float:
    """Kosinusähnlichkeit zwischen zwei CLIP-Embeddings."""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def _ist_nullvektor(embedding: list[float] | None) -> bool:
    """Prüft ob ein Embedding ein Nullvektor ist (= nicht analysiert)."""
    if not embedding:
        return True
    return all(v == 0.0 for v in embedding)


def _szene_score(
    szene_data: dict,
    prev_embedding: list[float] | None,
    config: dict,
) -> float:
    """
    Bewertet eine Szene für die Auswahl.
    
    Faktoren:
    - Dauer im gewünschten Bereich
    - Visuelle Diversität zum Vorgänger (CLIP-Embedding-Abstand)
    - Dialog vorhanden (wenn gewünscht)
    - Beschreibungs-Qualität
    """
    score = 0.0

    # Dauer-Score: Bevorzuge Szenen im gewünschten Dauer-Bereich
    dauer = szene_data["dauer"]
    min_d = config["min_szenen_dauer"]
    max_d = config["max_szenen_dauer"]
    if min_d <= dauer <= max_d:
        score += 1.0
    elif dauer < min_d:
        score += max(0.0, dauer / min_d)
    else:
        score += max(0.0, 1.0 - (dauer - max_d) / max_d)

    # Visuelle Diversität (CLIP-basiert)
    embedding = szene_data.get("embedding")
    if not _ist_nullvektor(embedding) and not _ist_nullvektor(prev_embedding):
        similarity = _cosine_similarity(embedding, prev_embedding)
        # Hohe Diversität = niedrige Ähnlichkeit = guter Score
        diversitaet = 1.0 - similarity
        score += diversitaet * config["visuell_diversitaet"] * 2.0

    # Dialog-Score
    if szene_data.get("transkription"):
        score += config["dialog_gewicht"]

    # Beschreibungs-Qualität (Szene wurde gut analysiert)
    if szene_data.get("beschreibung") and len(szene_data["beschreibung"]) > 10:
        score += 0.2

    return score


async def _llm_szenen_ordnung(
    szenen_infos: list[dict],
    config: dict,
    user_prompt: str | None,
) -> list[int]:
    """
    Fragt Ollama/LLaMA3 nach der optimalen Szenen-Reihenfolge.
    
    Gibt eine geordnete Liste von Szenen-Indizes zurück.
    Fallback: Gibt die Score-basierte Reihenfolge zurück.
    """
    if not szenen_infos:
        return []

    # Szenen-Beschreibungen für LLM aufbereiten
    szenen_text = ""
    for i, info in enumerate(szenen_infos):
        dauer = info["dauer"]
        beschreibung = info.get("beschreibung", "Keine Beschreibung")
        dialog = info.get("transkription", "")
        szenen_text += f"  [{i}] {beschreibung} (Dauer: {dauer:.1f}s)"
        if dialog:
            szenen_text += f' — Dialog: "{dialog[:80]}"'
        szenen_text += "\n"

    prompt = (
        f"{config['llm_anweisung']}\n"
        f"Hier sind die verfügbaren Szenen:\n{szenen_text}\n"
    )
    if user_prompt:
        prompt += f"Zusätzlicher Wunsch des Nutzers: {user_prompt}\n\n"

    prompt += (
        f"Wähle die besten Szenen aus und gib die optimale Reihenfolge als JSON-Array von Indizes zurück.\n"
        f"Beispiel: [2, 0, 5, 3, 1]\n"
        f"Antworte NUR mit dem JSON-Array, nichts anderes."
    )

    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{OLLAMA_BASE_URL}/api/generate",
                json={
                    "model": OLLAMA_MODEL,
                    "prompt": prompt,
                    "stream": False,
                    "options": {
                        "temperature": 0.2,
                        "num_predict": 200,
                    },
                },
                timeout=120.0,
            )
            response.raise_for_status()
            data = response.json()
            antwort = data.get("response", "").strip()

            # JSON-Array aus der Antwort extrahieren
            # LLM gibt manchmal Text drumherum
            start = antwort.find("[")
            end = antwort.rfind("]")
            if start != -1 and end != -1:
                array_str = antwort[start:end + 1]
                indizes = json.loads(array_str)
                # Validieren: nur gültige Indizes
                max_idx = len(szenen_infos) - 1
                valid = [i for i in indizes if isinstance(i, int) and 0 <= i <= max_idx]
                if valid:
                    logger.info(f"LLM Szenen-Ordnung: {valid}")
                    return valid

    except Exception as e:
        logger.warning(f"LLM Szenen-Ordnung fehlgeschlagen: {e}")

    # Fallback: Alle Szenen in Originalreihenfolge
    return list(range(len(szenen_infos)))


# ─── Hauptendpunkt ──────────────────────────────────────

@router.post("/cut")
async def ai_schnitt(body: AiCutRequest, db: AsyncSession = Depends(get_db)):
    """
    Erstellt eine KI-optimierte Timeline aus den gegebenen Clips.
    
    Pipeline:
    1. Alle Szenen + Embeddings + Beschreibungen laden
    2. Szenen bewerten (Dauer, visuelle Diversität, Dialog)
    3. LLaMA3 fragt nach optimaler narrativer Reihenfolge
    4. Timeline zusammenbauen mit Track-Zuordnung
    """
    if not body.clip_ids:
        raise HTTPException(400, "Mindestens ein Clip erforderlich.")

    config = STIL_CONFIG.get(body.stil, STIL_CONFIG["kinematisch"])

    # ─── 1. Alle Szenen aus allen Clips laden ────────────
    alle_szenen = []  # [{szene_db, clip_db, embedding, ...}]

    for clip_id in body.clip_ids:
        result = await db.execute(select(Clip).where(Clip.id == clip_id))
        clip = result.scalar_one_or_none()
        if not clip:
            continue

        szenen_result = await db.execute(
            select(Szene)
            .where(Szene.clip_id == clip_id)
            .order_by(Szene.szenen_nr)
        )
        szenen = szenen_result.scalars().all()

        if szenen:
            for szene in szenen:
                alle_szenen.append({
                    "clip_id": str(clip.id),
                    "clip_dateiname": clip.dateiname,
                    "quelle": clip.quelle,
                    "szene_nr": szene.szenen_nr,
                    "start_zeit": szene.start_zeit,
                    "end_zeit": szene.end_zeit,
                    "dauer": szene.dauer,
                    "embedding": szene.clip_embedding,
                    "beschreibung": szene.beschreibung,
                    "transkription": szene.transkription,
                })
        else:
            # Kein Szenen-Split → ganzer Clip als eine Szene
            alle_szenen.append({
                "clip_id": str(clip.id),
                "clip_dateiname": clip.dateiname,
                "quelle": clip.quelle,
                "szene_nr": 1,
                "start_zeit": 0.0,
                "end_zeit": clip.dauer or 10.0,
                "dauer": clip.dauer or 10.0,
                "embedding": None,
                "beschreibung": None,
                "transkription": None,
            })

    if not alle_szenen:
        raise HTTPException(400, "Keine verwertbaren Clips/Szenen gefunden.")

    # ─── 2. Szenen sortieren und ablegen ─────────────────
    # Grundregel: Szenen innerhalb eines Clips bleiben chronologisch.
    # Clips werden abwechselnd interleaved (A-B-A-B Schnittmuster).

    # Nach Clip gruppieren, Szenen chronologisch
    clip_gruppen: dict[str, list[dict]] = {}
    for sz in alle_szenen:
        cid = sz["clip_id"]
        clip_gruppen.setdefault(cid, []).append(sz)
    for scenes in clip_gruppen.values():
        scenes.sort(key=lambda s: s["start_zeit"])

    # Clips abwechselnd interleaven für visuellen Kontrast
    clip_ids_sorted = list(clip_gruppen.keys())
    geordnet: list[dict] = []

    if len(clip_ids_sorted) == 1:
        # Nur ein Clip → alle Szenen chronologisch
        geordnet = clip_gruppen[clip_ids_sorted[0]]
    else:
        # Mehrere Clips → Round-Robin interleave
        iterators = {cid: iter(scenes) for cid, scenes in clip_gruppen.items()}
        while iterators:
            exhausted = []
            for cid in clip_ids_sorted:
                if cid not in iterators:
                    continue
                try:
                    geordnet.append(next(iterators[cid]))
                except StopIteration:
                    exhausted.append(cid)
            for cid in exhausted:
                del iterators[cid]

    # Filter: Szenen die zu kurz sind überspringen
    min_d = config["min_szenen_dauer"]
    geordnet = [s for s in geordnet if s["dauer"] >= min_d] or geordnet

    # ─── 3. LLaMA3 für narrative Beschreibung (optional) ──
    # Bei genügend Szenen: LLaMA3 kann empfehlen welche wegzulassen
    if len(geordnet) > 6:
        try:
            llm_order = await _llm_szenen_ordnung(geordnet, config, body.prompt)
            # Nur umordnen wenn LLM sinnvolle Antwort gab
            if llm_order and len(llm_order) >= len(geordnet) // 2:
                reordered = []
                used = set()
                for idx in llm_order:
                    if 0 <= idx < len(geordnet) and idx not in used:
                        reordered.append(geordnet[idx])
                        used.add(idx)
                for i, sz in enumerate(geordnet):
                    if i not in used:
                        reordered.append(sz)
                geordnet = reordered
        except Exception as e:
            logger.warning(f"LLM-Optimierung übersprungen: {e}")

    # ─── 4. Timeline-Segmente bauen ──────────────────────
    # Alles auf V1 (Video) + A1 (Audio-Spiegel) — sauber sequenziell.
    # Multi-Track Overlays kann der Nutzer danach manuell einrichten.
    segmente = []
    cursor = 0.0

    # Farben pro Quell-Clip alternieren für bessere visuelle Trennung
    video_farben = ["orange", "blue", "purple"]
    clip_farb_map: dict[str, str] = {}
    farb_idx = 0

    for szene in geordnet:
        dauer = szene["dauer"]
        if config["tempo"] > 0.8:
            dauer = min(config["max_szenen_dauer"], dauer)

        # Farbe pro Quell-Clip zuweisen (gleicher Clip = gleiche Farbe)
        cid = szene["clip_id"]
        if cid not in clip_farb_map:
            clip_farb_map[cid] = video_farben[farb_idx % len(video_farben)]
            farb_idx += 1
        v_color = clip_farb_map[cid]

        # Lesbarer Label
        name_kurz = szene["clip_dateiname"].rsplit(".", 1)[0]
        if len(name_kurz) > 14:
            name_kurz = name_kurz[:14] + "…"
        beschreibung = szene.get("beschreibung", "")
        if beschreibung and len(beschreibung) > 3:
            label = f"{name_kurz} · {beschreibung[:35]}"
        else:
            label = name_kurz

        seg_id = str(uuid.uuid4())
        group_id = f"grp-ai-{seg_id[:8]}"

        # Video auf V1
        segmente.append({
            "id": seg_id,
            "clip_id": szene["clip_id"],
            "szene_nr": szene["szene_nr"],
            "label": label,
            "track": "v1",
            "start": round(cursor, 3),
            "dauer": round(dauer, 3),
            "mediaStart": round(szene["start_zeit"], 3),
            "quelle": szene["quelle"],
            "beschreibung": szene.get("beschreibung"),
            "color": v_color,
            "groupId": group_id,
            "ai": True,
        })

        # Audio-Spiegel auf A1
        segmente.append({
            "id": str(uuid.uuid4()),
            "clip_id": szene["clip_id"],
            "szene_nr": szene["szene_nr"],
            "label": f"♪ {name_kurz}",
            "track": "a1",
            "start": round(cursor, 3),
            "dauer": round(dauer, 3),
            "mediaStart": round(szene["start_zeit"], 3),
            "quelle": szene["quelle"],
            "color": "green",
            "groupId": group_id,
            "ai": True,
        })

        cursor += dauer

    if not segmente:
        raise HTTPException(400, "Keine Szenen nach Filterung übrig.")

    # ─── 5. Timeline in DB speichern ─────────────────────
    tl_id = str(uuid.uuid4())
    tl_daten = {
        "segmente": segmente,
        "gesamtdauer": round(cursor, 3),
        "stil": body.stil,
        "szenen_gesamt": len(alle_szenen),
        "szenen_ausgewaehlt": len(segmente),
    }

    tl = Timeline(
        id=tl_id,
        name=f"KI-Schnitt ({body.stil})",
        stil=body.stil,
        prompt=body.prompt,
        daten=tl_daten,
        gesamtdauer=round(cursor, 3),
    )
    db.add(tl)
    await db.commit()

    return {
        "timeline_id": tl_id,
        "segmente_anzahl": len(segmente),
        "gesamtdauer": round(cursor, 3),
        "szenen_gesamt": len(alle_szenen),
        "daten": tl_daten,
    }
