"""Eigenständiger Prototyp der Anfrage-Zerlegung (Phase 1 der Timeline-Erzeugung).

Nimmt eine Nutzereingabe und eine Zieldauer und erzeugt daraus eine gegliederte JSON-Zerlegung:
eine geordnete Liste von Slots mit Absicht, Dauer und Bedingungen zu Einstellungsgröße, Sprecher und Dialog.

Zweck: prüfen, ob qwen2.5:14b verlässliches und verwertbares JSON liefert, BEVOR es
in den Assistenten eingebunden wird. Ohne Bindung an die Datenbank oder den übrigen Dienst.

Usage :
    python -m backend.tools.prototype_timeline_planner \\
        --prompt "90s über die Einsamkeit des Kochs, Wechsel VO und Wide Shots" \\
        --duration 90
"""

from __future__ import annotations

import argparse
import json
import sys
import time

import httpx


OLLAMA_URL = "http://localhost:11434/api/generate"
AGENT_MODEL = "qwen2.5:14b"

VALID_FRAMINGS = {
    "extreme_closeup",
    "closeup",
    "medium",
    "wide_with_person",
    "wide_no_person",
    "any",
}


SYSTEM_PROMPT = """Du bist ein Cutting-Assistent, der aus einer natürlichen Beschreibung
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


def build_prompt(user_prompt: str, duration_s: float, num_slots_hint: int | None) -> str:
    hint_line = (
        f"\nRICHTWERT für Anzahl Slots: ca. {num_slots_hint}." if num_slots_hint else ""
    )
    return (
        f"{SYSTEM_PROMPT}\n\n"
        f"=== AUFGABE ===\n"
        f"Zieldauer: {duration_s:.0f} Sekunden.\n"
        f"Beschreibung des gewünschten Cuts:\n{user_prompt.strip()}{hint_line}\n\n"
        "Generiere jetzt den JSON-Plan."
    )


def call_ollama(full_prompt: str, temperature: float = 0.3, timeout_s: int = 600) -> tuple[dict, float]:
    t0 = time.time()
    with httpx.Client(timeout=timeout_s) as client:
        r = client.post(
            OLLAMA_URL,
            json={
                "model": AGENT_MODEL,
                "prompt": full_prompt,
                "format": "json",
                "stream": False,
                "options": {"temperature": temperature},
            },
        )
        r.raise_for_status()
        data = r.json()
    wall = time.time() - t0
    resp_text = data.get("response", "")
    parsed = json.loads(resp_text)
    return parsed, wall


def validate_plan(plan: dict, target_duration: float) -> list[str]:
    """Gibt eine Liste von Fehlern und Hinweisen zurück, leer, wenn die Zerlegung stimmt."""
    errors: list[str] = []
    if not isinstance(plan, dict):
        return ["Plan ist kein dict"]
    slots = plan.get("slots")
    if not isinstance(slots, list) or not slots:
        return ["Plan enthält keine slots-Liste"]

    total_mid = 0.0
    for i, slot in enumerate(slots):
        prefix = f"slot[{i}]"
        for req in ("intent_de", "intent_en", "duration_min_s", "duration_max_s",
                    "framing_hint", "needs_speaker", "needs_dialogue"):
            if req not in slot:
                errors.append(f"{prefix}: fehlt Feld '{req}'")
        framing = slot.get("framing_hint")
        if framing not in VALID_FRAMINGS:
            errors.append(f"{prefix}: framing_hint '{framing}' ungültig "
                          f"(erwartet {sorted(VALID_FRAMINGS)})")
        try:
            dmin = float(slot.get("duration_min_s", 0))
            dmax = float(slot.get("duration_max_s", 0))
            if dmin <= 0 or dmax <= 0 or dmax < dmin:
                errors.append(f"{prefix}: ungültige Dauer min={dmin} max={dmax}")
            else:
                total_mid += (dmin + dmax) / 2
        except (TypeError, ValueError):
            errors.append(f"{prefix}: Dauer nicht numerisch")

    deviation = abs(total_mid - target_duration) / max(target_duration, 1e-6)
    if deviation > 0.25:
        errors.append(
            f"Summe der mittleren Dauer ({total_mid:.1f}s) weicht "
            f"{deviation*100:.0f}% von Ziel ({target_duration:.0f}s) ab (max 25%)"
        )
    return errors


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--prompt", required=True, help="Beschreibung des gewünschten Cuts (DE/EN/FR)")
    ap.add_argument("--duration", type=float, required=True, help="Zieldauer in Sekunden")
    ap.add_argument("--num-slots-hint", type=int, default=None, help="Richtwert für Slot-Anzahl")
    ap.add_argument("--temperature", type=float, default=0.3)
    ap.add_argument("--raw", action="store_true", help="Nur den JSON-Plan ausgeben (kein Header)")
    args = ap.parse_args()

    full_prompt = build_prompt(args.prompt, args.duration, args.num_slots_hint)

    if not args.raw:
        print(f"→ Modell: {AGENT_MODEL} (temperature={args.temperature})", file=sys.stderr)
        print(f"→ Zieldauer: {args.duration:.0f}s", file=sys.stderr)
        print(f"→ Prompt: {args.prompt[:100]}...", file=sys.stderr)

    try:
        plan, wall = call_ollama(full_prompt, args.temperature)
    except json.JSONDecodeError as e:
        print(f"ERROR: JSON-Parsing fehlgeschlagen: {e}", file=sys.stderr)
        return 2
    except httpx.HTTPError as e:
        print(f"ERROR: Ollama-Aufruf fehlgeschlagen: {e}", file=sys.stderr)
        return 3

    if not args.raw:
        print(f"→ Wall-Zeit: {wall:.1f}s", file=sys.stderr)

    errors = validate_plan(plan, args.duration)
    if errors:
        print("⚠️  Validierungswarnungen:", file=sys.stderr)
        for e in errors:
            print(f"   - {e}", file=sys.stderr)

    print(json.dumps(plan, indent=2, ensure_ascii=False))
    return 0 if not errors else 1


if __name__ == "__main__":
    sys.exit(main())
