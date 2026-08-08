"""Set de prompts de référence pour l'évaluation quantitative du générateur
timeline-from-prompt (Kapitel 4 der Bachelorarbeit).

Chaque prompt est reproductible et couvre un profil de cas d'usage cible.
Ajouter de nouveaux prompts est bienvenu, mais NE PAS modifier les existants
(sinon les benchmarks historiques ne sont plus comparables).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class BenchmarkPrompt:
    id: str                     # slug stable (utilisé comme clé de row)
    prompt: str                 # texte user
    duration_s: float           # zieldauer
    profile: str                # broll_nature | interview | narrative_mixed | edge
    notes: str = ""             # ce qu'on attend qualitativement


# ─── B-roll / paysage / naturstimmung (pas de personne, pas de dialogue) ─────

BROLL = [
    BenchmarkPrompt(
        id="broll_nature_short",
        prompt="15 Sekunden ruhige Naturaufnahmen, Wasser und Landschaft, keine Menschen",
        duration_s=15,
        profile="broll_nature",
        notes="Court, doit filtrer wide_no_person, éviter dialogue accidentel",
    ),
    BenchmarkPrompt(
        id="broll_nature_60s",
        prompt="60 Sekunden ruhige Naturstimmung, Weitwinkel-Landschaften mit sanftem Übergang, keine Menschen",
        duration_s=60,
        profile="broll_nature",
        notes="Base test — reference pour la reg",
    ),
    BenchmarkPrompt(
        id="broll_urban",
        prompt="30 Sekunden Stadtbilder, Straßen und Gebäude aus der Distanz, kein Fokus auf Personen",
        duration_s=30,
        profile="broll_nature",
        notes="Urbain plutôt que nature, pas beaucoup dans pool",
    ),
    BenchmarkPrompt(
        id="broll_water_calm",
        prompt="20 Sekunden ruhige Wasseraufnahmen: Wellen, Fluss, See — meditativ, keine Menschen",
        duration_s=20,
        profile="broll_nature",
        notes="Force le retrieve à trouver pexels_waves + water scenes",
    ),
    BenchmarkPrompt(
        id="broll_energy_dynamic",
        prompt="25 Sekunden dynamische Actionszenen, schnelle Bewegung, energische Weitwinkel-Bilder",
        duration_s=25,
        profile="broll_nature",
        notes="Test discrimination CLIP action vs ruhig",
    ),
]

# ─── Interview / talking head (dialogue + speaker) ───────────────────────────

INTERVIEW = [
    BenchmarkPrompt(
        id="interview_40s_closeup",
        prompt="40 Sekunden Interview-Ausschnitt: Sprecher redet in Nahaufnahmen und Halbtotalen, kein B-Roll",
        duration_s=40,
        profile="interview",
        notes="Full dialog, doit prioriser closeup/medium, max_words_dialogue trim",
    ),
    BenchmarkPrompt(
        id="interview_60s_serious",
        prompt="60 Sekunden ernstes Gespräch eines Sprechers direkt in die Kamera, ausschliesslich Nahaufnahmen",
        duration_s=60,
        profile="interview",
        notes="Contrainte framing dure closeup",
    ),
    BenchmarkPrompt(
        id="interview_broll_mix",
        prompt="45 Sekunden: Sprecher erklärt seine Arbeit (Nahaufnahmen), dazwischen kurze B-Roll seiner Umgebung als Illustration",
        duration_s=45,
        profile="interview",
        notes="Alternance A-roll/B-roll — testé au tout début, référence",
    ),
    BenchmarkPrompt(
        id="interview_short",
        prompt="15 Sekunden Statement einer Person, Nahaufnahme, klare Aussage",
        duration_s=15,
        profile="interview",
        notes="Cut court avec fort besoin dialogue",
    ),
    BenchmarkPrompt(
        id="interview_multi_speaker",
        prompt="50 Sekunden Diskussion mit mehreren Sprechern im Wechsel, Halbtotalen bevorzugt",
        duration_s=50,
        profile="interview",
        notes="Multi-speaker — tests si retriever varie la source",
    ),
]

# ─── Narratif mixte (compo complexe, framings variés) ────────────────────────

NARRATIVE = [
    BenchmarkPrompt(
        id="narrative_lonely_cook",
        prompt="90 Sekunden über die Einsamkeit des Kochs am frühen Morgen in der leeren Küche, Wechsel zwischen Weitwinkel und Nahaufnahmen, endet auf einem stillen Close-up seines Gesichts",
        duration_s=90,
        profile="narrative_mixed",
        notes="Prompt initial de la thèse — narratif fort, framings mixtes",
    ),
    BenchmarkPrompt(
        id="narrative_documentary_open",
        prompt="30 Sekunden Doku-Intro: Weitwinkel-Establisher, dann Nahaufnahme eines menschlichen Details, endet auf einem Halbtotal einer aktiven Person",
        duration_s=30,
        profile="narrative_mixed",
        notes="Freytag-esque, structure claire",
    ),
    BenchmarkPrompt(
        id="narrative_music_video",
        prompt="45 Sekunden Musikclip: rhythmische Wechsel zwischen Nahaufnahmen von Bewegung und Weitwinkel-Umgebung",
        duration_s=45,
        profile="narrative_mixed",
        notes="Rythme rapide, testera dur/max_s des slots",
    ),
    BenchmarkPrompt(
        id="narrative_teaser_energetic",
        prompt="20 Sekunden energischer Teaser mit schnellen Schnitten, wechselnde Framings, aufsteigende Intensität",
        duration_s=20,
        profile="narrative_mixed",
        notes="Cut court dense, ~8-10 slots minimum",
    ),
    BenchmarkPrompt(
        id="narrative_slow_atmospheric",
        prompt="120 Sekunden langsamer, atmosphärischer Cut: lange Weitwinkel, gelegentliche Nahaufnahmen als Akzent",
        duration_s=120,
        profile="narrative_mixed",
        notes="Cut long, teste convergence duration + diversité",
    ),
]

# ─── Edge cases (durées / contraintes extrêmes) ──────────────────────────────

EDGE = [
    BenchmarkPrompt(
        id="edge_very_short",
        prompt="8 Sekunden Micro-Cut: 2 Weitwinkel + 1 Closeup, sehr schnell",
        duration_s=8,
        profile="edge",
        notes="Très court — teste borne inférieure",
    ),
    BenchmarkPrompt(
        id="edge_no_person_hard",
        prompt="30 Sekunden Landschaftsimpressionen, ABSOLUT KEINE MENSCHEN im Bild, nur Natur",
        duration_s=30,
        profile="edge",
        notes="Contrainte dure — testera pool-aware planner",
    ),
    BenchmarkPrompt(
        id="edge_all_closeup",
        prompt="45 Sekunden ausschliesslich Nahaufnahmen von Gesichtern oder Details, NIE Weitwinkel",
        duration_s=45,
        profile="edge",
        notes="Contrainte dure framing — pool a peu de closeups",
    ),
]

# ─── Set complet ─────────────────────────────────────────────────────────────

ALL_PROMPTS: list[BenchmarkPrompt] = BROLL + INTERVIEW + NARRATIVE + EDGE

BY_PROFILE = {
    "broll_nature": BROLL,
    "interview": INTERVIEW,
    "narrative_mixed": NARRATIVE,
    "edge": EDGE,
}


def get_by_id(prompt_id: str) -> BenchmarkPrompt | None:
    for p in ALL_PROMPTS:
        if p.id == prompt_id:
            return p
    return None


def get_by_profile(profile: str) -> list[BenchmarkPrompt]:
    return BY_PROFILE.get(profile, [])
