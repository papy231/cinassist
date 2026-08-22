"""Fester Satz von Anfragen für die quantitative Auswertung der
Timeline-Erzeugung (Kapitel 4 der Bachelorarbeit).

Jede Anfrage ist wiederholbar und deckt ein typisches Anwendungsprofil ab.
Neue Anfragen dürfen ergänzt werden, die bestehenden jedoch NICHT verändert
werden, sonst sind frühere Vergleichsläufe nicht mehr vergleichbar.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class BenchmarkPrompt:
    id: str                     # feste Kennung, dient als Schlüssel der Zeile
    prompt: str                 # Anfragetext
    duration_s: float           # Zieldauer in Sekunden
    profile: str                # broll_nature | interview | narrative_mixed | edge
    notes: str = ""             # was inhaltlich erwartet wird


# ─── B-Roll, Landschaft, Naturstimmung (keine Person, kein Dialog) ───────────

BROLL = [
    BenchmarkPrompt(
        id="broll_nature_short",
        prompt="15 Sekunden ruhige Naturaufnahmen, Wasser und Landschaft, keine Menschen",
        duration_s=15,
        profile="broll_nature",
        notes="Kurz; muss wide_no_person filtern und zufälligen Dialog vermeiden",
    ),
    BenchmarkPrompt(
        id="broll_nature_60s",
        prompt="60 Sekunden ruhige Naturstimmung, Weitwinkel-Landschaften mit sanftem Übergang, keine Menschen",
        duration_s=60,
        profile="broll_nature",
        notes="Grundfall, Bezugspunkt für Vergleiche",
    ),
    BenchmarkPrompt(
        id="broll_urban",
        prompt="30 Sekunden Stadtbilder, Straßen und Gebäude aus der Distanz, kein Fokus auf Personen",
        duration_s=30,
        profile="broll_nature",
        notes="Städtisch statt Natur, im Bestand wenig vorhanden",
    ),
    BenchmarkPrompt(
        id="broll_water_calm",
        prompt="20 Sekunden ruhige Wasseraufnahmen: Wellen, Fluss, See — meditativ, keine Menschen",
        duration_s=20,
        profile="broll_nature",
        notes="Zwingt den Abruf auf Wasser-Szenen",
    ),
    BenchmarkPrompt(
        id="broll_energy_dynamic",
        prompt="25 Sekunden dynamische Actionszenen, schnelle Bewegung, energische Weitwinkel-Bilder",
        duration_s=25,
        profile="broll_nature",
        notes="Prüft, ob CLIP Bewegung von Ruhe unterscheidet",
    ),
]

# ─── Interview, sprechende Person (Dialog und Sprecher) ──────────────────────

INTERVIEW = [
    BenchmarkPrompt(
        id="interview_40s_closeup",
        prompt="40 Sekunden Interview-Ausschnitt: Sprecher redet in Nahaufnahmen und Halbtotalen, kein B-Roll",
        duration_s=40,
        profile="interview",
        notes="Durchgehend Dialog; Nah und Halbtotale bevorzugt, Schnitt an Wortgrenzen",
    ),
    BenchmarkPrompt(
        id="interview_60s_serious",
        prompt="60 Sekunden ernstes Gespräch eines Sprechers direkt in die Kamera, ausschliesslich Nahaufnahmen",
        duration_s=60,
        profile="interview",
        notes="Harte Bedingung auf Nahaufnahme",
    ),
    BenchmarkPrompt(
        id="interview_broll_mix",
        prompt="45 Sekunden: Sprecher erklärt seine Arbeit (Nahaufnahmen), dazwischen kurze B-Roll seiner Umgebung als Illustration",
        duration_s=45,
        profile="interview",
        notes="Wechsel zwischen A-Roll und B-Roll; früh geprüft, dient als Bezugspunkt",
    ),
    BenchmarkPrompt(
        id="interview_short",
        prompt="15 Sekunden Statement einer Person, Nahaufnahme, klare Aussage",
        duration_s=15,
        profile="interview",
        notes="Kurzer Schnitt mit starkem Dialogbedarf",
    ),
    BenchmarkPrompt(
        id="interview_multi_speaker",
        prompt="50 Sekunden Diskussion mit mehreren Sprechern im Wechsel, Halbtotalen bevorzugt",
        duration_s=50,
        profile="interview",
        notes="Mehrere Sprecher; prüft, ob der Abruf die Quelle wechselt",
    ),
]

# ─── Gemischt erzählend (komplexer Aufbau, wechselnde Einstellungsgrößen) ────

NARRATIVE = [
    BenchmarkPrompt(
        id="narrative_lonely_cook",
        prompt="90 Sekunden über die Einsamkeit des Kochs am frühen Morgen in der leeren Küche, Wechsel zwischen Weitwinkel und Nahaufnahmen, endet auf einem stillen Close-up seines Gesichts",
        duration_s=90,
        profile="narrative_mixed",
        notes="Ursprüngliche Anfrage der Arbeit; stark erzählend, gemischte Einstellungen",
    ),
    BenchmarkPrompt(
        id="narrative_documentary_open",
        prompt="30 Sekunden Doku-Intro: Weitwinkel-Establisher, dann Nahaufnahme eines menschlichen Details, endet auf einem Halbtotal einer aktiven Person",
        duration_s=30,
        profile="narrative_mixed",
        notes="Klarer dramaturgischer Aufbau",
    ),
    BenchmarkPrompt(
        id="narrative_music_video",
        prompt="45 Sekunden Musikclip: rhythmische Wechsel zwischen Nahaufnahmen von Bewegung und Weitwinkel-Umgebung",
        duration_s=45,
        profile="narrative_mixed",
        notes="Schneller Rhythmus; prüft die Dauergrenzen der Slots",
    ),
    BenchmarkPrompt(
        id="narrative_teaser_energetic",
        prompt="20 Sekunden energischer Teaser mit schnellen Schnitten, wechselnde Framings, aufsteigende Intensität",
        duration_s=20,
        profile="narrative_mixed",
        notes="Kurzer, dichter Schnitt; mindestens acht bis zehn Slots",
    ),
    BenchmarkPrompt(
        id="narrative_slow_atmospheric",
        prompt="120 Sekunden langsamer, atmosphärischer Cut: lange Weitwinkel, gelegentliche Nahaufnahmen als Akzent",
        duration_s=120,
        profile="narrative_mixed",
        notes="Langer Schnitt; prüft Zeittreue und Vielfalt",
    ),
]

# ─── Grenzfälle (extreme Dauern und Bedingungen) ─────────────────────────────

EDGE = [
    BenchmarkPrompt(
        id="edge_very_short",
        prompt="8 Sekunden Micro-Cut: 2 Weitwinkel + 1 Closeup, sehr schnell",
        duration_s=8,
        profile="edge",
        notes="Sehr kurz; prüft die untere Grenze",
    ),
    BenchmarkPrompt(
        id="edge_no_person_hard",
        prompt="30 Sekunden Landschaftsimpressionen, ABSOLUT KEINE MENSCHEN im Bild, nur Natur",
        duration_s=30,
        profile="edge",
        notes="Harte Bedingung; prüft die bestandsbewusste Planung",
    ),
    BenchmarkPrompt(
        id="edge_all_closeup",
        prompt="45 Sekunden ausschliesslich Nahaufnahmen von Gesichtern oder Details, NIE Weitwinkel",
        duration_s=45,
        profile="edge",
        notes="Harte Bedingung auf die Einstellungsgröße; im Bestand wenige Nahaufnahmen",
    ),
]

# ─── Vollständiger Satz ──────────────────────────────────────────────────────

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
