"""Générateur de timeline pilotée par prompt — cœur de la Bachelorarbeit.

Architecture Plan → Retrieve → Assemble :

1. **plan_timeline** : qwen2.5:14b décompose le prompt utilisateur en slots
   séquentiels (intent, durée min/max, framing, needs_speaker/dialogue).
2. **retrieve_candidates** : pour chaque slot, filtre + score CLIP les scènes du
   projet et retourne les top-K candidats.
3. **assemble_timeline** : 2ᵉ passe LLM qui voit les candidats et produit la
   liste ordonnée finale de segments (compatible _SEGMENT_STASH).

Chaque phase log son sortie JSON dans `outputs/timeline_gen_logs/` pour
l'analyse et l'évaluation de la thèse.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

import httpx
import numpy as np
from rank_bm25 import BM25Okapi
from sqlalchemy import and_, exists, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from backend.core.database import Clip, SceneSpeaker, Speaker, Szene


logger = logging.getLogger("cinassist.timeline_gen")

OLLAMA_URL = "http://localhost:11434/api/generate"
AGENT_MODEL = "qwen2.5:14b"
DEFAULT_TIMEOUT_S = 600

VALID_FRAMINGS = {
    "extreme_closeup",
    "closeup",
    "medium",
    "wide_with_person",
    "wide_no_person",
    "any",
}

LOG_DIR = Path(__file__).resolve().parent.parent / "outputs" / "timeline_gen_logs"


# ─── Klappen-Filter (Filmklappe / clapperboard ausschließen) ─
# Rushes beginnen mit einer Klappe (Sync bei Doppelsystem-Ton). Die Szenenerkennung
# isoliert sie oft als eigene Szene — nutzlos für den Schnitt und mit irreführendem
# CLIP-Embedding. Solche Szenen werden aus dem Kandidaten-Pool ausgeschlossen.
_KLAPPE_STRONG = (
    "clapperboard", "clapper board", "clapper", "slate", "klappe", "film slate",
    "holding the board", "holding a board", "white board with black text",
    "whiteboard with black", "chalkboard with", "marker board",
)
_KLAPPE_WEAK = ("white board", "whiteboard", "black text", "yellow stripe", "a board with")


def _looks_like_klappe(beschreibung: "str | None", start_zeit: float) -> bool:
    d = (beschreibung or "").lower()
    if not d:
        return False
    if any(k in d for k in _KLAPPE_STRONG):
        return True
    # Schwache Signale nur am Take-Anfang werten (dort steht die Klappe).
    if float(start_zeit or 0.0) < 3.0 and sum(1 for k in _KLAPPE_WEAK if k in d) >= 2:
        return True
    return False


# ─── Pool summarizer (feeds pool-aware planner) ──────────────

async def summarize_pool(db: AsyncSession, clip_ids: list[str],
                          sample_descriptions: int = 5) -> dict:
    """Résumé compact du pool disponible pour injection dans le prompt planner.

    Le planner qui ne connaît pas le pool génère des slots impossibles
    (ex: 'extreme_closeup of hands' sur un pool 100% wide_no_person).
    Ce résumé lui permet de calibrer.
    """
    if not clip_ids:
        return {"total_scenes": 0}

    stmt = select(Szene).where(Szene.clip_id.in_(clip_ids)).where(
        Szene.clip_embedding.isnot(None)
    )
    scenes = list((await db.execute(stmt)).scalars().all())
    scenes = [s for s in scenes if not _looks_like_klappe(s.beschreibung, s.start_zeit)]
    if not scenes:
        return {"total_scenes": 0}

    # Framings
    from collections import Counter
    framing_counts = Counter(s.framing for s in scenes if s.framing)

    # Durées
    durs = sorted([float(s.dauer) for s in scenes if s.dauer])
    dur_stats = {}
    if durs:
        dur_stats = {
            "min_s": round(durs[0], 1),
            "max_s": round(durs[-1], 1),
            "median_s": round(durs[len(durs) // 2], 1),
        }

    # Speakers (label_manual prioritaire, sinon label_auto ; jusqu'à 8)
    speaker_stmt = (
        select(Speaker)
        .where(Speaker.clip_id.in_(clip_ids))
        .order_by(Speaker.total_speaking_time.desc())
        .limit(8)
    )
    speakers = list((await db.execute(speaker_stmt)).scalars().all())
    speaker_labels = [sp.label_manual or sp.label_auto for sp in speakers]

    # Sample descriptions : prend 1 scène par clip max (diversité), jusqu'à N
    seen_clips: set[str] = set()
    samples: list[str] = []
    for sc in scenes:
        if sc.beschreibung and str(sc.clip_id) not in seen_clips:
            desc = sc.beschreibung.strip().replace("\n", " ")[:140]
            samples.append(desc)
            seen_clips.add(str(sc.clip_id))
            if len(samples) >= sample_descriptions:
                break

    # Ratio scènes avec transcription utile
    with_text = sum(1 for s in scenes
                    if s.transkription and len(s.transkription.strip()) >= 20)
    with_speaker_time = sum(1 for s in scenes if s.face_count and s.face_count > 0)

    return {
        "total_scenes": len(scenes),
        "framing_counts": dict(framing_counts),
        "duration_stats": dur_stats,
        "speakers": speaker_labels,
        "sample_descriptions": samples,
        "scenes_with_dialogue": with_text,
        "scenes_with_faces": with_speaker_time,
    }


def _format_pool_summary_for_prompt(pool: dict) -> str:
    """Rendu texte du résumé pool pour injection dans le prompt planner."""
    if pool.get("total_scenes", 0) == 0:
        return ""
    lines = [
        "=== VERFÜGBARER SZENEN-POOL ===",
        f"Insgesamt {pool['total_scenes']} Szenen mit CLIP-Embedding.",
    ]
    fc = pool.get("framing_counts") or {}
    if fc:
        lines.append("Framings verfügbar: " + ", ".join(
            f"{k}={v}" for k, v in sorted(fc.items(), key=lambda x: -x[1])
        ))
    ds = pool.get("duration_stats") or {}
    if ds:
        lines.append(
            f"Szenendauern: min {ds['min_s']}s, median {ds['median_s']}s, max {ds['max_s']}s."
        )
    if pool.get("scenes_with_dialogue"):
        lines.append(f"Davon {pool['scenes_with_dialogue']} mit Dialog/Transkription, "
                     f"{pool.get('scenes_with_faces', 0)} mit erkannten Gesichtern.")
    sp = pool.get("speakers") or []
    if sp:
        lines.append("Bekannte Sprecher: " + ", ".join(sp))
    samples = pool.get("sample_descriptions") or []
    if samples:
        lines.append("Beispiele für Szenenbeschreibungen (moondream):")
        for s in samples:
            lines.append(f"  - {s}")
    lines.append(
        "WICHTIG: Passe deine Slots an diesen Pool an — vermeide Framings mit Count=0, "
        "wähle needs_speaker=true nur wenn genügend Dialog-Szenen vorhanden sind."
    )
    return "\n".join(lines)


# ─── Phase 1 : Planner ───────────────────────────────────────

PLANNER_SYSTEM_PROMPT = """Du bist ein Cutting-Assistent, der aus einer natürlichen Beschreibung
einen strukturierten Schnittplan generiert. Der Plan besteht aus einer geordneten
Liste von SLOTS (Einstellungen), die nacheinander geschnitten werden.

Für jeden Slot gibst du an:
- intent_de: kurze visuelle Beschreibung auf Deutsch (was zu sehen ist)
- intent_en: dieselbe Beschreibung auf Englisch (für CLIP-Retrieval)
- duration_min_s / duration_max_s: realistische Dauer (2-15 Sekunden)
- framing_hint: EINER von: extreme_closeup, closeup, medium, wide_with_person, wide_no_person, any
- needs_speaker: true wenn eine sprechende Person zu sehen sein muss, sonst false
- needs_dialogue: true wenn Ton/Dialog dieses Slots wichtig ist, sonst false (B-Roll)
- notes_de: kurzer Regie-Hinweis (Stimmung, Tempo, Übergang)

WICHTIGE REGELN:
1. Die Summe der Mittelwerte (duration_min+duration_max)/2 aller Slots
   MUSS ungefähr der Zieldauer entsprechen (±15%).
2. Anzahl Slots: für kurze Cuts (<30s) 4-8 Slots, für mittlere (30-90s)
   8-16 Slots, für lange (>90s) 15-30 Slots.
3. Denke narrativ: Einführung → Entwicklung → Höhepunkt → Ausklang.
4. Wechsle Framings ab (nicht 5 Closeups hintereinander).
5. framing_hint = "any" nur wenn wirklich keine visuelle Präferenz besteht.

Antworte AUSSCHLIESSLICH mit gültigem JSON in genau diesem Format:

{
  "narrative_intent_de": "Kurzer Satz zum Gesamtbogen des Cuts",
  "target_duration_s": 90,
  "planned_total_duration_s": 87,
  "slots": [
    {
      "slot_id": 1,
      "intent_de": "Etablierendes Weitwinkel-Bild der leeren Küche am frühen Morgen",
      "intent_en": "wide establishing shot of an empty kitchen in the early morning",
      "duration_min_s": 4.0,
      "duration_max_s": 7.0,
      "framing_hint": "wide_no_person",
      "needs_speaker": false,
      "needs_dialogue": false,
      "notes_de": "Ruhig, atmosphärisch, sanft einleitend"
    }
  ]
}
"""


def _build_planner_prompt(user_prompt: str, duration_s: float,
                          num_slots_hint: int | None,
                          pool_summary: dict | None = None) -> str:
    hint_line = (
        f"\nRICHTWERT für Anzahl Slots: ca. {num_slots_hint}." if num_slots_hint else ""
    )
    pool_block = ""
    cap_line = ""
    if pool_summary and pool_summary.get("total_scenes", 0) > 0:
        pool_block = "\n\n" + _format_pool_summary_for_prompt(pool_summary)
        # Plafond dur : avec dédoublonnage, chaque scène ne remplit qu'un slot.
        # Sur-planifier (mehr Slots als Szenen) garantit des trous → wir deckeln.
        total_scenes = int(pool_summary["total_scenes"])
        cap_line = (
            f"\n\nHARTE OBERGRENZE: Es gibt nur {total_scenes} verschiedene Szenen im Pool. "
            f"Erzeuge HÖCHSTENS {total_scenes} Slots und dehne stattdessen deren Dauer, "
            f"um die Zieldauer zu erreichen. Lieber wenige, längere, passende Einstellungen "
            f"als viele leere Slots."
        )
    return (
        f"{PLANNER_SYSTEM_PROMPT}"
        f"{pool_block}{cap_line}\n\n"
        f"=== AUFGABE ===\n"
        f"Zieldauer: {duration_s:.0f} Sekunden.\n"
        f"Beschreibung des gewünschten Cuts:\n{user_prompt.strip()}{hint_line}\n\n"
        "Generiere jetzt den JSON-Plan."
    )


async def _call_ollama_json(prompt: str, temperature: float = 0.3,
                            timeout_s: int = DEFAULT_TIMEOUT_S) -> tuple[dict, float]:
    t0 = time.time()
    async with httpx.AsyncClient(timeout=timeout_s) as client:
        r = await client.post(
            OLLAMA_URL,
            json={
                "model": AGENT_MODEL,
                "prompt": prompt,
                "format": "json",
                "stream": False,
                "options": {"temperature": temperature},
            },
        )
        r.raise_for_status()
        data = r.json()
    wall = time.time() - t0
    parsed = json.loads(data.get("response", "{}"))
    return parsed, wall


def validate_plan(plan: dict, target_duration_s: float) -> list[str]:
    errors: list[str] = []
    if not isinstance(plan, dict):
        return ["plan is not a dict"]
    slots = plan.get("slots")
    if not isinstance(slots, list) or not slots:
        return ["plan has no slots"]

    total_mid = 0.0
    for i, slot in enumerate(slots):
        prefix = f"slot[{i}]"
        for req in ("intent_de", "intent_en", "duration_min_s", "duration_max_s",
                    "framing_hint", "needs_speaker", "needs_dialogue"):
            if req not in slot:
                errors.append(f"{prefix}: missing '{req}'")
        if slot.get("framing_hint") not in VALID_FRAMINGS:
            errors.append(f"{prefix}: framing_hint invalid ({slot.get('framing_hint')!r})")
        try:
            dmin = float(slot.get("duration_min_s", 0))
            dmax = float(slot.get("duration_max_s", 0))
            if dmin <= 0 or dmax < dmin:
                errors.append(f"{prefix}: invalid duration min={dmin} max={dmax}")
            else:
                total_mid += (dmin + dmax) / 2
        except (TypeError, ValueError):
            errors.append(f"{prefix}: duration not numeric")

    deviation = abs(total_mid - target_duration_s) / max(target_duration_s, 1e-6)
    if deviation > 0.25:
        errors.append(
            f"total mid-duration ({total_mid:.1f}s) deviates {deviation*100:.0f}% "
            f"from target ({target_duration_s:.0f}s)"
        )
    return errors


async def plan_timeline(user_prompt: str, target_duration_s: float,
                        num_slots_hint: int | None = None,
                        temperature: float = 0.3,
                        pool_summary: dict | None = None) -> dict:
    """Phase 1 : produit un plan structuré depuis un prompt utilisateur.

    Si `pool_summary` est fourni (via summarize_pool), il est injecté dans le
    prompt pour rendre le planner « pool-aware » — il évite alors les slots
    impossibles (framings absents, needs_speaker sur pool sans dialogue).

    Retourne le dict tel que sorti par qwen (validé, mais peut contenir des
    warnings dans `_validation_warnings` si le plan a des soucis récupérables).
    """
    prompt = _build_planner_prompt(user_prompt, target_duration_s,
                                   num_slots_hint, pool_summary)
    plan, wall = await _call_ollama_json(prompt, temperature)
    warnings = validate_plan(plan, target_duration_s)
    plan["_meta"] = {
        "wall_s": round(wall, 2),
        "model": AGENT_MODEL,
        "temperature": temperature,
        "user_prompt": user_prompt,
        "pool_aware": bool(pool_summary and pool_summary.get("total_scenes", 0) > 0),
    }
    if warnings:
        plan["_validation_warnings"] = warnings
    return plan


# ─── Phase 2 : Retriever ─────────────────────────────────────

def _embed_text_lazy(text: str) -> np.ndarray:
    """Emprunte le CLIP text encoder de backend.api.search (lazy singleton)."""
    from backend.api.search import _embed_text
    return _embed_text(text)


def _tokenize_lazy(text: str | None) -> list[str]:
    """Réutilise le tokenizer léger multilingue de search.py."""
    from backend.api.search import _tokenize
    return _tokenize(text)


def _cosine(a: np.ndarray, b: np.ndarray) -> float:
    na = float(np.linalg.norm(a))
    nb = float(np.linalg.norm(b))
    if na == 0 or nb == 0:
        return 0.0
    return float(np.dot(a, b) / (na * nb))


async def _fetch_pool_scenes(db: AsyncSession, clip_ids: list[str]) -> list[Szene]:
    """Charge toutes les scènes analysables (embedding présent) du projet
    en une requête, avec leurs clips associés."""
    stmt = (
        select(Szene)
        .options(selectinload(Szene.clip))
        .where(Szene.clip_id.in_(clip_ids))
        .where(Szene.clip_embedding.isnot(None))
    )
    return list((await db.execute(stmt)).scalars().all())


async def _fetch_scene_speaker_map(db: AsyncSession, scene_ids: list[str]) -> dict[str, float]:
    """Retourne {scene_id: max_speaking_time} pour filtre `needs_speaker`."""
    if not scene_ids:
        return {}
    stmt = select(SceneSpeaker.scene_id, SceneSpeaker.speaking_time).where(
        SceneSpeaker.scene_id.in_(scene_ids)
    )
    rows = (await db.execute(stmt)).all()
    out: dict[str, float] = {}
    for sid, st in rows:
        key = str(sid)
        out[key] = max(out.get(key, 0.0), float(st or 0.0))
    return out


def _passes_constraints(scene: Szene, slot: dict, speaker_map: dict[str, float],
                        use_framing: bool, use_speaker: bool,
                        use_dialogue: bool, use_duration: bool) -> bool:
    """Vérifie un sous-ensemble configurable de contraintes du slot.

    Utilisé par la relaxation graduée : on désactive les contraintes une à une
    (framing → dialogue → speaker → durée) quand un slot ne trouve aucun
    candidat, plutôt que de rendre un slot vide.
    """
    # Durée
    if use_duration:
        if scene.dauer is None or scene.dauer < float(slot.get("duration_min_s", 0)) - 0.2:
            return False
    else:
        # Même en mode relâché, la scène doit rester montable.
        if scene.dauer is None or scene.dauer < 1.0:
            return False
    # Framing
    if use_framing:
        fh = slot.get("framing_hint", "any")
        if fh != "any" and scene.framing != fh:
            return False
    # Speaker requirement
    if use_speaker and slot.get("needs_speaker"):
        if speaker_map.get(str(scene.id), 0.0) < 0.5:
            return False
    # Dialogue requirement
    if use_dialogue and slot.get("needs_dialogue"):
        if not scene.transkription or len(scene.transkription.strip()) < 20:
            return False
    return True


# Ordre de relaxation : on garde le plus longtemps possible les contraintes
# « fortes » (speaker/dialogue = fidélité du sens) et on lâche d'abord les
# « molles » (framing = préférence de cadrage, puis durée). Chaque tier est
# tracé pour la thèse (skipped_ratio → coverage, précision par tier).
_RELAX_TIERS = [
    # (use_framing, use_speaker, use_dialogue, use_duration, label)
    (True,  True,  True,  True,  "strict"),
    (False, True,  True,  True,  "relaxed_framing"),
    (False, True,  False, True,  "relaxed_framing+dialogue"),
    (False, False, False, True,  "relaxed_visual_only"),
    (False, False, False, False, "relaxed_all"),
]


def _filter_scenes_tiered(pool: list[Szene], slot: dict,
                          speaker_map: dict[str, float]) -> tuple[list[int], str]:
    """Renvoie (indices filtrés, label du tier utilisé).

    Descend les tiers jusqu'à trouver au moins un candidat. Si même le tier le
    plus permissif est vide (pool réellement vide), renvoie ([], "none").
    """
    for use_framing, use_speaker, use_dialogue, use_duration, label in _RELAX_TIERS:
        idx = [
            i for i, s in enumerate(pool)
            if _passes_constraints(s, slot, speaker_map,
                                   use_framing, use_speaker, use_dialogue, use_duration)
        ]
        if idx:
            return idx, label
    return [], "none"


async def _rewrite_slot_intents(slots: list[dict]) -> dict[str, str]:
    """Enrichit les intent_en de tous les slots via query rewriting (llama3),
    en parallèle. Retourne {slot_id: rewritten_intent}. Fallback silencieux sur
    l'intent original en cas d'échec (géré dans _rewrite_query)."""
    import asyncio
    from backend.api.search import _rewrite_query
    intents = [(str(s.get("slot_id")),
                (s.get("intent_en") or s.get("intent_de") or "")) for s in slots]
    rewritten = await asyncio.gather(*[_rewrite_query(t) for _, t in intents])
    return {sid: rw for (sid, _), rw in zip(intents, rewritten)}


async def retrieve_candidates(plan: dict, project_clip_ids: list[str],
                              db: AsyncSession, top_k: int = 5,
                              dedupe_across_slots: bool = True,
                              weight_clip: float = 0.6,
                              weight_text: float = 0.4,
                              use_query_rewrite: bool = False) -> dict:
    """Phase 2 : pour chaque slot, ranke top-K scènes par score hybride
    (CLIP visuel + BM25 texte sur beschreibung + transkription) après filtres
    durs. Retourne un dict {slot_id: [candidate, ...]}.

    Un candidate = {scene_id, clip_id, clip_path, clip_name, start_zeit, dauer,
                    framing, face_count, beschreibung, transkription,
                    score, clip_score, text_score}.
    """
    t0 = time.time()
    slots = plan.get("slots") or []
    if not slots or not project_clip_ids:
        return {"slots": {}, "_meta": {"wall_s": 0.0, "pool_size": 0}}

    pool_raw = await _fetch_pool_scenes(db, project_clip_ids)
    pool = [s for s in pool_raw if not _looks_like_klappe(s.beschreibung, s.start_zeit)]
    klappen_entfernt = len(pool_raw) - len(pool)
    speaker_map = await _fetch_scene_speaker_map(db, [str(s.id) for s in pool])

    # Corpus BM25 partagé (calculé une fois, queried par slot).
    # Non-empty pour éviter que BM25Okapi crash sur des docs vides.
    corpus_tokens = [
        _tokenize_lazy((s.beschreibung or "") + " " + (s.transkription or ""))
        for s in pool
    ]
    non_empty_idx = [i for i, toks in enumerate(corpus_tokens) if toks]
    bm25 = None
    if non_empty_idx:
        bm25 = BM25Okapi([corpus_tokens[i] for i in non_empty_idx])

    # Normalise les poids
    total_w = weight_clip + weight_text
    if total_w <= 0:
        weight_clip, weight_text, total_w = 0.6, 0.4, 1.0
    w_clip = weight_clip / total_w
    w_text = weight_text / total_w

    # Query rewriting optionnel : enrichit les intents avant embedding CLIP.
    rewrite_map: dict[str, str] = {}
    if use_query_rewrite:
        try:
            rewrite_map = await _rewrite_slot_intents(slots)
        except Exception as e:
            logger.warning(f"Query rewriting global failed, using raw intents: {e}")
            rewrite_map = {}

    result: dict[str, list[dict]] = {}
    relaxations: dict[str, str] = {}
    used: set[str] = set()
    last_used: str | None = None

    for slot in slots:
        slot_id = str(slot.get("slot_id"))
        # Filtrage gradué : relâche les contraintes une à une plutôt que de
        # rendre un slot vide (cf. _RELAX_TIERS).
        filtered_idx, relax_label = _filter_scenes_tiered(pool, slot, speaker_map)
        relaxations[slot_id] = relax_label
        if not filtered_idx:
            result[slot_id] = []
            continue

        # Composante CLIP (visuel) sur intent_en (fallback intent_de),
        # éventuellement enrichi par query rewriting.
        intent_en = slot.get("intent_en") or slot.get("intent_de") or ""
        query_text = rewrite_map.get(slot_id, intent_en) if use_query_rewrite else intent_en
        try:
            query_emb = _embed_text_lazy(query_text)
        except Exception as e:
            logger.warning(f"CLIP embed failed for slot {slot_id}: {e}")
            result[slot_id] = []
            continue

        # Composante BM25 (texte) — query = intent_de + intent_en pour maximiser
        # les hits multilingues sur le corpus (moondream EN + transkription DE/FR/EN).
        query_tokens = _tokenize_lazy(
            (slot.get("intent_de") or "") + " " + (slot.get("intent_en") or "")
        )

        # BM25 scores normalisés [0,1] pour toutes les scènes du pool
        text_scores_pool = np.zeros(len(pool), dtype=np.float32)
        if bm25 is not None and query_tokens:
            raw = bm25.get_scores(query_tokens)
            max_raw = float(raw.max()) if raw.size > 0 else 0.0
            if max_raw > 0:
                for j, idx in enumerate(non_empty_idx):
                    text_scores_pool[idx] = float(raw[j]) / max_raw

        # Score hybride sur les scènes filtrées. Quand le framing a été relâché,
        # on garde un petit bonus pour les scènes dont le cadrage colle quand même
        # au slot : parmi des candidats « de compromis », le plus proche remonte.
        fh = slot.get("framing_hint", "any")
        scored: list[tuple[float, float, float, Szene]] = []  # (combined, clip, text, scene)
        for i in filtered_idx:
            sc = pool[i]
            try:
                sc_emb = np.asarray(sc.clip_embedding, dtype=np.float32)
                clip_score = _cosine(query_emb, sc_emb)
            except Exception:
                continue
            text_score = float(text_scores_pool[i])
            combined = w_clip * clip_score + w_text * text_score
            if fh != "any" and sc.framing == fh:
                combined += 0.03
            scored.append((combined, clip_score, text_score, sc))

        top = _pick_top_k_hybrid(scored, top_k, used if dedupe_across_slots else set())
        if dedupe_across_slots and not top and scored:
            # Pool épuisé par le dédoublonnage : plutôt qu'un slot vide, on
            # réemploie une scène déjà utilisée (une prise longue peut servir
            # à plusieurs slots via des in-points différents). On évite le
            # doublon adjacent quand c'est possible.
            avoid = {last_used} if last_used else set()
            top = _pick_top_k_hybrid(scored, top_k, avoid)
            if not top:
                top = _pick_top_k_hybrid(scored, top_k, set())
            if top:
                relaxations[slot_id] = (relaxations.get(slot_id, "strict") + "+reuse")
        if dedupe_across_slots and top:
            last_used = str(top[0][3].id)
            used.add(last_used)  # réserve seulement le top-1
        result[slot_id] = [_scene_to_candidate(combined, clip_s, text_s, sc)
                           for combined, clip_s, text_s, sc in top]

    wall = time.time() - t0
    from collections import Counter
    relax_counts = dict(Counter(relaxations.values()))
    return {
        "slots": result,
        "relaxations": relaxations,
        "_meta": {
            "wall_s": round(wall, 2),
            "pool_size": len(pool),
            "klappen_excluded": klappen_entfernt,
            "pool_with_text": len(non_empty_idx),
            "top_k": top_k,
            "dedupe_across_slots": dedupe_across_slots,
            "weight_clip": round(w_clip, 3),
            "weight_text": round(w_text, 3),
            "relaxation_tiers": relax_counts,
            "query_rewrite": bool(use_query_rewrite),
        },
    }


def _pick_top_k_hybrid(scored: list[tuple[float, float, float, Szene]], k: int,
                       used: set[str]) -> list[tuple[float, float, float, Szene]]:
    scored.sort(key=lambda x: x[0], reverse=True)
    out: list[tuple[float, float, float, Szene]] = []
    for tup in scored:
        if str(tup[3].id) in used:
            continue
        out.append(tup)
        if len(out) >= k:
            break
    return out


def _scene_to_candidate(combined: float, clip_score: float, text_score: float,
                        scene: Szene) -> dict:
    """Sérialise une Szene + scores hybrides pour output JSON.

    Inclut transkription_json (segments Whisper avec timestamps) et
    analyse_visuelle → utilisés par smart_trim_start.
    """
    clip = scene.clip
    return {
        "scene_id": str(scene.id),
        "clip_id": str(scene.clip_id),
        "clip_path": clip.dateipfad if clip else None,
        "clip_name": clip.dateiname if clip else None,
        "szenen_nr": scene.szenen_nr,
        "start_zeit": float(scene.start_zeit or 0.0),
        "end_zeit": float(scene.end_zeit or 0.0),
        "dauer": float(scene.dauer or 0.0),
        "framing": scene.framing,
        "face_count": scene.face_count,
        "beschreibung": scene.beschreibung,
        "transkription": (scene.transkription[:200] + "…") if scene.transkription and len(scene.transkription) > 200 else scene.transkription,
        "transkription_json": scene.transkription_json,
        "analyse_visuelle": scene.analyse_visuelle,
        "score": round(combined, 4),
        "clip_score": round(clip_score, 4),
        "text_score": round(text_score, 4),
    }


# ─── Phase 3 : Assembler ─────────────────────────────────────

def _target_duration_for_slot(slot: dict, scene_dauer: float) -> float:
    """Choisit une durée cible réaliste bornée par la scène disponible."""
    dmin = float(slot.get("duration_min_s", 2.0))
    dmax = float(slot.get("duration_max_s", dmin))
    if scene_dauer <= dmin:
        return max(1.0, scene_dauer)
    return min(dmax, scene_dauer)


def _trim_start_in_scene(scene: dict, target_duration: float) -> float:
    """Fallback centré (V1). Utilisé quand smart_trim_start n'a pas assez
    d'informations pour décider."""
    scene_dauer = float(scene.get("dauer") or 0.0)
    if target_duration >= scene_dauer:
        return 0.0
    return max(0.0, (scene_dauer - target_duration) / 2.0)


def _extract_word_times_in_scene(scene: dict) -> list[float]:
    """Retourne la liste des timestamps de mots (en secondes) RELATIVE à la
    scène. transkription_json contient les timestamps au niveau du clip (pas
    de la scène) — on soustrait scene.start_zeit pour convertir.
    """
    tj = scene.get("transkription_json")
    if not tj or not isinstance(tj, list):
        return []
    scene_start = float(scene.get("start_zeit") or 0.0)
    scene_dauer = float(scene.get("dauer") or 0.0)
    times: list[float] = []
    for seg in tj:
        if not isinstance(seg, dict):
            continue
        # Priorité aux mots individuels si dispo (plus fin)
        woerter = seg.get("woerter") or seg.get("words") or []
        if woerter:
            for w in woerter:
                if not isinstance(w, dict):
                    continue
                try:
                    t = float(w.get("start")) - scene_start
                except (TypeError, ValueError):
                    continue
                if 0.0 <= t <= scene_dauer:
                    times.append(t)
        else:
            # Sinon un point par segment (au start)
            try:
                t = float(seg.get("start")) - scene_start
            except (TypeError, ValueError):
                continue
            if 0.0 <= t <= scene_dauer:
                times.append(t)
    return sorted(times)


def _best_window_by_words(word_times: list[float], scene_dauer: float,
                          target_duration: float, maximize: bool = True,
                          step_s: float = 0.5) -> float:
    """Fenêtre glissante de largeur `target_duration` sur [0, scene_dauer].
    Retourne le rel_start qui MAX (ou MIN) le nombre de mots dans la fenêtre.
    Tie-break : plus proche du centre de la scène.
    """
    if target_duration >= scene_dauer:
        return 0.0
    max_start = scene_dauer - target_duration
    center = max_start / 2.0
    if not word_times:
        return center

    best_start = center
    best_count = -1 if maximize else 10**9
    # Positions candidates : bornes discrètes + juste avant chaque mot
    candidates = set([0.0, max_start, center])
    step = max(0.2, step_s)
    x = 0.0
    while x <= max_start:
        candidates.add(round(x, 3))
        x += step
    # Aussi les "starts qui alignent le premier mot au tout début de la fenêtre"
    for t in word_times:
        c = max(0.0, min(max_start, t - 0.15))
        candidates.add(round(c, 3))

    def _count_in(start: float) -> int:
        end = start + target_duration
        return sum(1 for t in word_times if start <= t <= end)

    for c in sorted(candidates):
        n = _count_in(c)
        better = (n > best_count) if maximize else (n < best_count)
        if better or (n == best_count and abs(c - center) < abs(best_start - center)):
            best_count = n
            best_start = c
    return round(best_start, 3)


def smart_trim_start(scene: dict, slot: dict, target_duration: float) -> tuple[float, str]:
    """Sélectionne le meilleur rel_start dans la scène en fonction du slot.

    Retourne (rel_start, strategy_used) où strategy_used décrit la stratégie
    appliquée pour la trace thèse.
    """
    scene_dauer = float(scene.get("dauer") or 0.0)
    if target_duration >= scene_dauer:
        return 0.0, "full_scene"

    needs_dialogue = bool(slot.get("needs_dialogue"))
    needs_speaker = bool(slot.get("needs_speaker"))

    word_times = _extract_word_times_in_scene(scene)

    if needs_dialogue or needs_speaker:
        if word_times:
            rel = _best_window_by_words(word_times, scene_dauer, target_duration,
                                        maximize=True)
            return rel, ("max_words_dialogue" if needs_dialogue else "max_words_speaker")
        # Pas de timestamps → fallback centré
        return _trim_start_in_scene(scene, target_duration), "centered_no_transcript"

    # B-roll pur : éviter les mots qui traînent si transcription dispo
    if word_times:
        rel = _best_window_by_words(word_times, scene_dauer, target_duration,
                                    maximize=False)
        return rel, "min_words_broll"

    return _trim_start_in_scene(scene, target_duration), "centered_default"


def _heuristic_pick_and_trim(plan: dict, candidates: dict) -> tuple[list[dict], list[dict]]:
    """Assembler heuristique V1 : pour chaque slot prend le top-1 candidat,
    calcule trim_start + target_duration, produit segments render-ready.

    Retourne (segments, per_slot_decisions) où per_slot_decisions est le log
    de la décision par slot (pour la trace thèse).
    """
    slots_c = candidates.get("slots") or {}
    relaxations = candidates.get("relaxations") or {}
    slots = plan.get("slots") or []

    segments: list[dict] = []
    decisions: list[dict] = []
    scene_use_count: dict[str, int] = {}

    for slot in slots:
        sid = str(slot["slot_id"])
        picks = slots_c.get(sid, [])
        decision: dict[str, Any] = {
            "slot_id": sid,
            "intent_de": slot.get("intent_de"),
            "relaxation": relaxations.get(sid, "strict"),
        }
        if not picks:
            decision["outcome"] = "skipped_no_candidate"
            decisions.append(decision)
            continue
        pick = picks[0]
        target_dur = _target_duration_for_slot(slot, float(pick.get("dauer") or 0.0))
        rel_start, trim_strategy = smart_trim_start(pick, slot, target_dur)
        # Réemploi : si la scène a déjà servi, décale la fenêtre pour produire
        # un plan visuellement différent au lieu d'un doublon exact.
        n_prev = scene_use_count.get(pick["scene_id"], 0)
        if n_prev > 0:
            scene_dauer = float(pick.get("dauer") or 0.0)
            max_start = max(0.0, scene_dauer - target_dur)
            if max_start > 0:
                rel_start = round(min(max_start, (n_prev / (n_prev + 1)) * max_start), 3)
                trim_strategy += "+reuse_offset"
        scene_use_count[pick["scene_id"]] = n_prev + 1
        media_start = float(pick.get("start_zeit") or 0.0) + rel_start
        segments.append({
            "clip_path": pick["clip_path"],
            "clip_name": pick["clip_name"],
            "media_start": round(media_start, 3),
            "duration": round(target_dur, 3),
            "src_scene_id": pick["scene_id"],
        })
        decision.update({
            "outcome": "picked",
            "scene_id": pick["scene_id"],
            "clip_name": pick["clip_name"],
            "clip_score": pick["clip_score"],
            "text_score": pick.get("text_score"),
            "score": pick.get("score", pick["clip_score"]),
            "target_duration_s": round(target_dur, 3),
            "trim_start_in_scene_s": round(rel_start, 3),
            "trim_strategy": trim_strategy,
            "framing": pick["framing"],
            "runner_ups": [
                {"scene_id": p["scene_id"], "score": p.get("score", p.get("clip_score"))}
                for p in picks[1:3]
            ],
        })
        decisions.append(decision)

    return segments, decisions


ASSEMBLER_SYSTEM_PROMPT = """Du bist ein Cutting-Assistent im letzten Schritt der
Timeline-Generierung. Für jeden Slot des Plans hast du eine Liste von
Kandidaten (Szenen) mit CLIP-Score, Framing, Beschreibung und Transkription.

Deine Aufgabe: Für JEDEN Slot GENAU EINE Szene auswählen und angeben, wie viele
Sekunden davon verwendet werden sollen und ab welcher Position innerhalb der
Szene der Schnitt beginnt (trim_start_s).

REGELN:
- Wähle immer eine Szene aus, wenn Kandidaten vorhanden sind (auch wenn Score
  niedrig ist — nur wenn die Liste leer ist, überspringe den Slot).
- Bevorzuge in der Regel den Top-Kandidaten (höchster CLIP-Score). Wähle nur
  einen Runner-Up, wenn seine Beschreibung dem Slot-Intent inhaltlich deutlich
  besser entspricht.
- target_duration_s MUSS zwischen duration_min_s und duration_max_s des Slots
  liegen und darf die Dauer der gewählten Szene NICHT überschreiten.
- trim_start_s MUSS zwischen 0 und (scene.dauer - target_duration_s) liegen.
- Vermeide, dieselbe Szene in aufeinanderfolgenden Slots zu wählen.

Antworte AUSSCHLIESSLICH mit gültigem JSON:

{
  "picks": [
    {
      "slot_id": 1,
      "scene_id": "uuid...",
      "target_duration_s": 5.0,
      "trim_start_s": 1.2,
      "rationale_de": "kurzer Grund, warum diese Szene passt"
    }
  ]
}
"""


def _compact_candidates_for_llm(plan: dict, candidates: dict, per_slot_max: int = 3) -> str:
    """Construit une représentation compacte des candidats pour le prompt LLM."""
    slots_c = candidates.get("slots") or {}
    lines: list[str] = []
    for slot in plan.get("slots") or []:
        sid = str(slot["slot_id"])
        picks = (slots_c.get(sid) or [])[:per_slot_max]
        header = (f"SLOT {sid}: {slot.get('intent_de')} | framing={slot.get('framing_hint')} | "
                  f"dur {slot.get('duration_min_s')}-{slot.get('duration_max_s')}s")
        lines.append(header)
        if not picks:
            lines.append("  (keine Kandidaten)")
            continue
        for c in picks:
            desc = (c.get("beschreibung") or "").strip().replace("\n", " ")[:120]
            trans = (c.get("transkription") or "").strip().replace("\n", " ")[:80]
            lines.append(
                f"  - id={c['scene_id']} score={c['clip_score']:.3f} "
                f"framing={c.get('framing')} dauer={c.get('dauer'):.1f}s"
            )
            if desc:
                lines.append(f"    beschr: {desc}")
            if trans:
                lines.append(f"    transk: {trans}")
    return "\n".join(lines)


async def _llm_pick_and_trim(plan: dict, candidates: dict,
                             temperature: float = 0.2) -> tuple[list[dict], list[dict], dict]:
    """Assembler mode LLM : demande à qwen de picker + trim pour chaque slot.
    Fallback silencieux vers heuristique si la réponse est invalide."""
    compact = _compact_candidates_for_llm(plan, candidates)
    prompt = (
        f"{ASSEMBLER_SYSTEM_PROMPT}\n\n"
        f"=== PLAN NARRATIV ===\n{plan.get('narrative_intent_de', '')}\n\n"
        f"=== KANDIDATEN PRO SLOT ===\n{compact}\n\n"
        "Generiere jetzt das JSON mit picks."
    )
    llm_meta: dict[str, Any] = {}
    try:
        picks_resp, wall = await _call_ollama_json(prompt, temperature)
        llm_meta = {"wall_s": round(wall, 2), "raw_pick_count": len(picks_resp.get("picks") or [])}
    except Exception as e:
        logger.warning(f"LLM assembler failed, falling back to heuristic: {e}")
        segments, decisions = _heuristic_pick_and_trim(plan, candidates)
        return segments, decisions, {"error": str(e), "fallback": "heuristic"}

    # Index candidats par (slot_id, scene_id) pour valider les picks
    slots_c = candidates.get("slots") or {}
    relaxations = candidates.get("relaxations") or {}
    idx: dict[tuple[str, str], dict] = {}
    for sid, cand_list in slots_c.items():
        for c in cand_list:
            idx[(str(sid), str(c["scene_id"]))] = c

    segments: list[dict] = []
    decisions: list[dict] = []
    picks = picks_resp.get("picks") or []
    picks_by_slot = {str(p.get("slot_id")): p for p in picks if isinstance(p, dict)}

    for slot in plan.get("slots") or []:
        sid = str(slot["slot_id"])
        decision: dict[str, Any] = {
            "slot_id": sid,
            "intent_de": slot.get("intent_de"),
            "relaxation": relaxations.get(sid, "strict"),
        }
        pick_req = picks_by_slot.get(sid)
        cand_list = slots_c.get(sid) or []
        if not cand_list:
            decision["outcome"] = "skipped_no_candidate"
            decisions.append(decision)
            continue

        chosen = None
        rationale = None
        if pick_req:
            key = (sid, str(pick_req.get("scene_id")))
            chosen = idx.get(key)
            rationale = pick_req.get("rationale_de")

        if not chosen:
            chosen = cand_list[0]  # fallback top-1
            decision["fallback"] = "top1_llm_pick_invalid"

        scene_dauer = float(chosen.get("dauer") or 0.0)
        # Trim + duration : clamp aux contraintes du slot ET aux limites de la scène
        try:
            target_dur = float(pick_req.get("target_duration_s")) if pick_req else _target_duration_for_slot(slot, scene_dauer)
        except (TypeError, ValueError):
            target_dur = _target_duration_for_slot(slot, scene_dauer)
        target_dur = max(1.0, min(target_dur, scene_dauer,
                                  float(slot.get("duration_max_s", scene_dauer))))
        target_dur = max(target_dur, min(float(slot.get("duration_min_s", 1.0)), scene_dauer))
        trim_strategy = "llm_provided"
        try:
            rel_start = float(pick_req.get("trim_start_s")) if pick_req else None
        except (TypeError, ValueError):
            rel_start = None
        if rel_start is None:
            rel_start, trim_strategy = smart_trim_start(chosen, slot, target_dur)
        rel_start = max(0.0, min(rel_start, max(0.0, scene_dauer - target_dur)))

        media_start = float(chosen.get("start_zeit") or 0.0) + rel_start
        segments.append({
            "clip_path": chosen["clip_path"],
            "clip_name": chosen["clip_name"],
            "media_start": round(media_start, 3),
            "duration": round(target_dur, 3),
            "src_scene_id": chosen["scene_id"],
        })
        decision.update({
            "outcome": "picked",
            "scene_id": chosen["scene_id"],
            "clip_name": chosen["clip_name"],
            "clip_score": chosen["clip_score"],
            "text_score": chosen.get("text_score"),
            "score": chosen.get("score", chosen["clip_score"]),
            "target_duration_s": round(target_dur, 3),
            "trim_start_in_scene_s": round(rel_start, 3),
            "trim_strategy": trim_strategy,
            "framing": chosen["framing"],
            "rationale_de": rationale,
        })
        decisions.append(decision)

    return segments, decisions, llm_meta


async def assemble_timeline(plan: dict, candidates: dict,
                            mode: str = "heuristic") -> dict:
    """Phase 3 : à partir du plan et des candidats, choisit un pick définitif
    par slot avec trim + ordering.

    mode = "heuristic" (rapide, top-1 + centre) ou "llm" (qwen picke).
    """
    t0 = time.time()
    if mode == "llm":
        segments, decisions, llm_meta = await _llm_pick_and_trim(plan, candidates)
    else:
        segments, decisions = _heuristic_pick_and_trim(plan, candidates)
        llm_meta = {}

    total_dur = sum(float(s["duration"]) for s in segments)
    return {
        "segments": segments,
        "decisions": decisions,
        "_meta": {
            "wall_s": round(time.time() - t0, 2),
            "mode": mode,
            "segment_count": len(segments),
            "total_duration_s": round(total_dur, 2),
            "skipped_slots": sum(1 for d in decisions if d.get("outcome") == "skipped_no_candidate"),
            "llm": llm_meta,
        },
    }


# ─── Mode « material-first » : histoire depuis le pool ───────
#
# Approche inverse du pipeline prompt-driven : au lieu de partir d'une intention
# et de chercher des scènes qui collent (risque de compromis quand le matériel
# manque), on montre au LLM TOUT le matériel réel et on lui demande de bâtir la
# story la plus cohérente POSSIBLE avec ça. Aucun plan inventé → pas de bas
# scores de retrieval. Idéal pour un « premier montage » sur un pool arbitraire.

STORY_SYSTEM_PROMPT = """Du bist ein erfahrener Cutter. Unten steht das GESAMTE
verfügbare Rohmaterial (Szenen mit ID, Beschreibung, Cadrage, Dauer). Deine
Aufgabe: daraus die kohärenteste kurze Geschichte / Stimmung bauen, die mit
GENAU DIESEM Material möglich ist.

REGELN:
- Erfinde NICHTS, was nicht im Material vorkommt. Nutze nur vorhandene scene_id.
- Ordne die gewählten Szenen dramaturgisch sinnvoll: Anfang → Entwicklung → Schluss.
- Suche einen roten Faden (gemeinsames Thema, Stimmung, Zeitverlauf).
- Gib pro gewählter Szene eine Dauer in Sekunden (>=1, <= Szenendauer).
- Nutze nicht zwingend alle Szenen; lieber wenige passende als ein Sammelsurium.
- Wenn eine Zieldauer vorgegeben ist, nähere die Gesamtdauer daran an (±20%).

Antworte AUSSCHLIESSLICH mit gültigem JSON:
{
  "story_title": "kurzer Titel",
  "narrative_intent_de": "1-2 Sätze zum roten Faden",
  "segments": [
    {"scene_id": "uuid...", "duration_s": 5.0, "reason_de": "warum diese Szene hier"}
  ]
}
"""


async def _fetch_story_pool(db: AsyncSession, clip_ids: list[str]) -> list[Szene]:
    """Scènes analysables (embedding présent, hors Klappe) avec clip chargé."""
    stmt = (
        select(Szene)
        .options(selectinload(Szene.clip))
        .where(Szene.clip_id.in_(clip_ids))
        .where(Szene.clip_embedding.isnot(None))
    )
    scenes = list((await db.execute(stmt)).scalars().all())
    return [s for s in scenes if not _looks_like_klappe(s.beschreibung, s.start_zeit)]


def _format_story_inventory(scenes: list[Szene]) -> str:
    lines = []
    for s in scenes:
        desc = (s.beschreibung or "").strip().replace("\n", " ")[:140]
        has_dlg = bool(s.transkription and len(s.transkription.strip()) >= 20)
        lines.append(
            f"- id={s.id} | cadrage={s.framing} | dauer={float(s.dauer or 0):.1f}s"
            f" | dialog={'ja' if has_dlg else 'nein'} | {desc}"
        )
    return "\n".join(lines)


async def generate_story_from_pool(db: AsyncSession, clip_ids: list[str],
                                   target_duration_s: float | None = None,
                                   temperature: float = 0.4) -> dict:
    """Mode material-first : construit une timeline narrative à partir du pool réel.

    Retourne un dict compatible avec assemble_timeline (segments render-ready +
    decisions + _meta) pour réutiliser le reste de la chaîne (stash, render, UI).
    """
    t0 = time.time()
    scenes = await _fetch_story_pool(db, clip_ids)
    if not scenes:
        return {"segments": [], "decisions": [],
                "_meta": {"wall_s": 0.0, "mode": "story", "segment_count": 0,
                          "total_duration_s": 0.0, "skipped_slots": 0, "pool_size": 0}}

    by_id = {str(s.id): s for s in scenes}
    target_line = (f"\nZieldauer: ca. {target_duration_s:.0f} Sekunden."
                   if target_duration_s else "")
    prompt = (
        f"{STORY_SYSTEM_PROMPT}\n\n"
        f"=== VERFÜGBARES MATERIAL ({len(scenes)} Szenen) ==={target_line}\n"
        f"{_format_story_inventory(scenes)}\n\n"
        "Generiere jetzt die Geschichte als JSON."
    )
    story, wall = await _call_ollama_json(prompt, temperature)

    segments: list[dict] = []
    decisions: list[dict] = []
    scene_use_count: dict[str, int] = {}
    for i, seg in enumerate(story.get("segments") or [], 1):
        sid = str(seg.get("scene_id"))
        scene = by_id.get(sid)
        decision: dict[str, Any] = {"order": i, "scene_id": sid,
                                    "reason_de": seg.get("reason_de")}
        if not scene:
            decision["outcome"] = "skipped_invalid_scene_id"
            decisions.append(decision)
            continue
        scene_dauer = float(scene.dauer or 0.0)
        try:
            target_dur = float(seg.get("duration_s") or 0.0)
        except (TypeError, ValueError):
            target_dur = 0.0
        target_dur = max(1.0, min(target_dur or 4.0, scene_dauer))
        # Réutilise le trim intelligent (B-roll → min mots, dialogue → max mots).
        pseudo_slot = {
            "needs_dialogue": bool(scene.transkription and len(scene.transkription.strip()) >= 20),
            "needs_speaker": False,
            "duration_min_s": 1.0, "duration_max_s": scene_dauer,
        }
        scene_dict = {
            "dauer": scene_dauer, "start_zeit": float(scene.start_zeit or 0.0),
            "transkription_json": scene.transkription_json,
        }
        rel_start, trim_strategy = smart_trim_start(scene_dict, pseudo_slot, target_dur)
        n_prev = scene_use_count.get(sid, 0)
        if n_prev > 0:
            max_start = max(0.0, scene_dauer - target_dur)
            if max_start > 0:
                rel_start = round(min(max_start, (n_prev / (n_prev + 1)) * max_start), 3)
                trim_strategy += "+reuse_offset"
        scene_use_count[sid] = n_prev + 1
        media_start = float(scene.start_zeit or 0.0) + rel_start
        clip = scene.clip
        segments.append({
            "clip_path": clip.dateipfad if clip else None,
            "clip_name": clip.dateiname if clip else None,
            "media_start": round(media_start, 3),
            "duration": round(target_dur, 3),
            "src_scene_id": sid,
        })
        decision.update({
            "outcome": "picked",
            "clip_name": clip.dateiname if clip else None,
            "framing": scene.framing,
            "target_duration_s": round(target_dur, 3),
            "trim_start_in_scene_s": round(rel_start, 3),
            "trim_strategy": trim_strategy,
        })
        decisions.append(decision)

    # Post-fill : le LLM sous-vise souvent la Zieldauer. Si on est nettement en
    # dessous, on étend chaque plan vers la fin de sa scène (proportionnel à la
    # marge dispo) pour approcher la cible — sans dépasser les scènes réelles.
    if target_duration_s and segments:
        total = sum(float(s["duration"]) for s in segments)
        if total < target_duration_s * 0.9:
            picked = [d for d in decisions if d.get("outcome") == "picked"]
            heads = []
            for s in segments:
                sc = by_id.get(s["src_scene_id"])
                scene_end = ((float(sc.start_zeit or 0.0) + float(sc.dauer or 0.0))
                             if sc else s["media_start"] + s["duration"])
                heads.append(max(0.0, round(scene_end - (s["media_start"] + s["duration"]), 3)))
            total_head = sum(heads)
            if total_head > 0:
                add = min(target_duration_s - total, total_head)
                for s, d, h in zip(segments, picked, heads):
                    extra = min(h, add * (h / total_head))
                    if extra > 0.05:
                        s["duration"] = round(s["duration"] + extra, 3)
                        d["target_duration_s"] = s["duration"]
                        d["trim_strategy"] = (d.get("trim_strategy", "") + "+postfill")

    total_dur = sum(float(s["duration"]) for s in segments)
    return {
        "story_title": story.get("story_title"),
        "narrative_intent_de": story.get("narrative_intent_de"),
        "segments": segments,
        "decisions": decisions,
        "_meta": {
            "wall_s": round(time.time() - t0, 2),
            "llm_wall_s": round(wall, 2),
            "mode": "story",
            "model": AGENT_MODEL,
            "pool_size": len(scenes),
            "segment_count": len(segments),
            "total_duration_s": round(total_dur, 2),
            "skipped_slots": sum(1 for d in decisions if d.get("outcome") != "picked"),
            "target_duration_s": target_duration_s,
        },
    }


# ─── Log helpers ─────────────────────────────────────────────

def _log_stage(stage: str, payload: dict, run_id: str) -> Path:
    """Écrit payload JSON dans outputs/timeline_gen_logs/{run_id}/{stage}.json."""
    run_dir = LOG_DIR / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    path = run_dir / f"{stage}.json"
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str))
    return path
