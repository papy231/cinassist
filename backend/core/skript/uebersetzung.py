"""Skript-Dialogzeilen in die Drehsprache übersetzen (qwen, einmalig, editierbar) — für lexikalische
Ähnlichkeit im Alignment und für die Anzeige neben dem gesprochenen Text. Keine Erfindung: Zeile für Zeile,
Figurennamen bleiben, Zeilenzahl muss stimmen (sonst verworfen)."""
from __future__ import annotations

from backend.core.skript.llm import frage_json

SPRACHEN = {"de": "Deutsch", "en": "Englisch", "fr": "Französisch", "es": "Spanisch", "it": "Italienisch"}


def uebersetze_zeilen(zeilen: list[str], ziel: str = "de", quelle: str | None = None) -> list[str] | None:
    if not zeilen:
        return []
    ziel_name = SPRACHEN.get(ziel, ziel)
    nummeriert = "\n".join(f"{i+1}. {t}" for i, t in enumerate(zeilen))
    prompt = f"""Übersetze die folgenden Drehbuch-Dialogzeilen {('aus dem ' + SPRACHEN.get(quelle, quelle)) if quelle else ''} ins {ziel_name} — natürlich gesprochen, wie Schauspieler es sagen würden, Zeile für Zeile, NICHTS hinzufügen oder weglassen. Eigennamen und Kosenamen (z. B. „Babe“) unverändert lassen. Antworte NUR als JSON: {{"zeilen": ["…", "…"]}} mit GENAU {len(zeilen)} Einträgen in derselben Reihenfolge.

{nummeriert}"""
    out = frage_json(prompt, num_predict=200 + 40 * len(zeilen))
    if not isinstance(out, dict):
        return None
    z = out.get("zeilen")
    if not isinstance(z, list) or len(z) != len(zeilen):
        return None
    return [str(x).strip() for x in z]
