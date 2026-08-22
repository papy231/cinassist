"""
CinAssist — Vision description helper (Ollama-Bildmodelle).

Ziel: **faktische** Bildbeschreibungen für die Szenen-Analyse — so wenig Erfindung wie möglich.

Erkenntnisse (Test 2026-08-19 auf dem Pinky-Promise-Material, 448/896-px-Frames):
    - moondream (1.7 GB) beschreibt flüssig, erfindet aber regelmäßig zusätzliche Personen
      („another person partially visible…“), Geräte („remote controls … suggesting they were
      watching television“) und Gefühle. Bei 320-px-Thumbnails noch deutlich schlechter.
    - llava:7b mit einem engen Prompt („only what is clearly visible … do not guess feelings“)
      liefert knappere, deutlich weniger halluzinierte Sätze und zählt Personen zuverlässig
      (Frage „How many people are visible? number only“ → „2“). Kosten: ~6 s/Frame (M-Serie),
      Zähl-Frage ~0,5 s.
    → Primär llava:7b (wenn installiert), Fallback moondream. Beide Outputs werden zusätzlich
      deterministisch „entspekuliert“: Nebensätze mit „as if / possibly / suggesting / appears to
      be enjoying / likely / seems to“ werden abgeschnitten (nie ergänzt).

Verwendet von:
    - workers/ingest.py :: schritt_bildanalyse (ingestion)
    - scripts backfill
"""
from __future__ import annotations

import base64
import json
import logging
import re
import urllib.request
from pathlib import Path

logger = logging.getLogger("cinassist.vision_describe")

_CJK_RE = re.compile(r"[一-鿿㐀-䶿぀-ゟ゠-ヿ가-힯]")
# Achtung: die Vorgabe "in one sentence" triggert bei moondream einen Bug —
# das erste Token wird verschluckt und der Output beginnt mit "urn of…/urns of…"
# (z.B. "urn of a guitar…" statt "A picture of a guitar…"). Prompts OHNE
# Satz-Längen-Vorgabe liefern sauberen Text. Siehe _is_bad → "truncation_artifact".
_DEFAULT_PROMPT = "Describe the main subject, action and setting of this image."
_FALLBACK_PROMPT = "Describe this image."

# llava:7b — enger, faktischer Prompt (getestet: kaum Halluzinationen, keine Gefühlsdeutung).
_LLAVA_PROMPT = (
    "Describe only what is clearly visible in this image: number of people, their position and "
    "what they are doing, important objects, and the room or place. Do not guess feelings, "
    "intentions or what might happen. Two sentences maximum."
)
_COUNT_PROMPT = "How many people are visible in this image? Answer with a number only."

# Führendes verstümmeltes Token des moondream-Bugs (case-insensitiv).
_ARTIFACT_RE = re.compile(r"^\s*urns?\s+(of|with)\b", re.IGNORECASE)

OLLAMA_URL = "http://localhost:11434/api/generate"
OLLAMA_TAGS_URL = "http://localhost:11434/api/tags"

VISION_PRIMAER = "llava:7b"
VISION_FALLBACK = "moondream"

_verfuegbar_cache: dict[str, bool] = {}


def modell_verfuegbar(name: str) -> bool:
    """Prüft (gecacht) über /api/tags, ob ein Ollama-Modell lokal installiert ist."""
    if name in _verfuegbar_cache:
        return _verfuegbar_cache[name]
    try:
        with urllib.request.urlopen(OLLAMA_TAGS_URL, timeout=5) as r:
            tags = json.loads(r.read()).get("models", [])
        namen = {str(m.get("name", "")) for m in tags}
        ok = name in namen or f"{name}:latest" in namen or any(n.split(":")[0] == name for n in namen)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"Ollama /api/tags nicht erreichbar: {e}")
        ok = False
    _verfuegbar_cache[name] = ok
    return ok


def vision_modell() -> str | None:
    """Bestes installiertes Bildmodell (llava:7b > moondream), None wenn keins."""
    for m in (VISION_PRIMAER, VISION_FALLBACK):
        if modell_verfuegbar(m):
            return m
    return None


def _is_bad(output: str) -> tuple[bool, str]:
    """Erkennt unbrauchbare Ausgaben: leer, zu kurz, fremde Schrift, stark wiederholend, Artefakt."""
    if not output or len(output.strip()) < 15:
        return True, "too_short"
    if _CJK_RE.search(output):
        return True, "cjk_hallucination"
    if _ARTIFACT_RE.match(output):
        return True, "truncation_artifact"
    # Wiederholungserkennung: dasselbe Wort fünfmal oder öfter hintereinander
    tokens = output.split()
    if len(tokens) > 5:
        for i in range(len(tokens) - 4):
            if len(set(tokens[i:i + 5])) == 1:
                return True, "repetition"
    return False, ""


def _call(image_b64: str, prompt: str, temperature: float = 0.1, model: str = VISION_FALLBACK,
          num_predict: int = 120, timeout: float = 120.0) -> str:
    body = json.dumps({
        "model": model,
        "prompt": prompt,
        "images": [image_b64],
        "stream": False,
        "options": {"temperature": temperature, "num_predict": num_predict},
        # Modell nach kurzer Zeit wieder aus dem RAM lassen — neben Whisper/pyannote/CLIP im Worker und
        # qwen 14b für Berichte wurde der Worker sonst vom System abgeschossen (32-GB-Mac, kein Crash-Report).
        "keep_alive": "2m",
    }).encode()
    req = urllib.request.Request(
        OLLAMA_URL, data=body, headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read()).get("response", "").strip()


# ─── Entspekulieren ────────────────────────────────────────────────────────────
# Wendungen, die keine Beobachtung sind, sondern Deutung. Wird ein Satz dadurch
# eingeleitet, fliegt der ganze Satz; steht die Wendung als Nebensatz, wird ab dort
# abgeschnitten. Es wird NIE Text ergänzt — nur gekürzt.
_SPEKULATION = re.compile(
    r"(,\s*|\s+)(as if\b|as though\b|possibly\b|perhaps\b|probably\b|likely\b|maybe\b|"
    r"suggesting\b|which suggests\b|indicating\b|implying\b|seemingly\b|"
    r"seems? to (?:be )?(?!sitting|standing|lying|holding|looking)|appears? to (?:be )?(?:enjoying|relaxing|thinking|waiting|talking|engaged|having|watching|listening)|"
    r"(?:is|are) enjoying\b|might\b|may be\b|could be\b|it looks like\b|(?:in )?what appears to be a (?:cozy|casual|relaxed|comfortable|intimate)\b|"
    r"adding (?:some|a) \w+ (?:to|touch)|creating (?:a|an) \w+ (?:atmosphere|feel|mood)|giving (?:the|it) (?:a|an) \w+ (?:feel|look|touch)|"
    r"lived-in feel\b|cozy\b|(?:the|a) (?:overall )?(?:atmosphere|mood|vibe) (?:is|of)\b|"
    r"(?:his|her|their) (?:facial )?expression\b|(?:seems?|looks?|appears?) (?:to be )?(?:surprised|shocked|happy|sad|angry|upset|worried|tired|"
    r"excited|calm|relaxed|nervous|serious|concerned|confused|bored|annoyed|scared|frightened|amused|content|pleased|thoughtful|focused|"
    r"distracted|emotional|distressed|frustrated|anxious|comfortable|uncomfortable|deep in thought|lost in thought)\b)",
    re.IGNORECASE,
)


_HAENGEND = {"what", "which", "that", "who", "and", "or", "with", "in", "on", "of", "to", "a", "an", "the",
             "displaying", "showing", "casual", "cozy", "relaxed", "comfortable", "warm", "inviting", "calm",
             "is", "are", "has", "have", "very", "quite", "rather"}


def entspekulieren(text: str) -> str:
    """Kürzt Deutungen aus einer Bildbeschreibung heraus (deterministisch, nie ergänzend)."""
    if not text:
        return text
    saetze = re.split(r"(?<=[.!?])\s+", text.strip())
    out: list[str] = []
    for satz in saetze:
        s = satz.strip()
        if not s:
            continue
        m = _SPEKULATION.search(s)
        if m:
            s = s[: m.start()].rstrip(" ,;:")
            # Hängende Funktions-/Stimmungswörter am Schnittende entfernen („displaying what“, „a casual“)
            worte = s.split()
            while worte and worte[-1].lower().strip(",;:") in _HAENGEND:
                worte.pop()
            s = " ".join(worte).rstrip(" ,;:")
            if len(worte) < 4:
                continue
            if not s.endswith((".", "!", "?")):
                s += "."
        out.append(s)
    return " ".join(out).strip()


def describe_image(image_path: str | Path, max_retries: int = 2, model: str | None = None) -> str:
    """
    Beschreibt ein Bild mit dem besten verfügbaren Bildmodell, llava:7b, sonst moondream, mit Wiederholung.

    Zurück kommt die bereits entspekulierte Beschreibung oder eine leere Zeichenkette, wenn alles scheitert.
    """
    p = Path(image_path)
    if not p.exists():
        return ""
    b64 = base64.b64encode(p.read_bytes()).decode()
    modell = model or vision_modell()
    if modell is None:
        return ""

    if modell.startswith("llava"):
        attempts = [(_LLAVA_PROMPT, 0.0), (_LLAVA_PROMPT, 0.2), (_FALLBACK_PROMPT, 0.0)]
    else:
        attempts = [(_DEFAULT_PROMPT, 0.1), (_DEFAULT_PROMPT, 0.0), (_FALLBACK_PROMPT, 0.0)]

    out = ""
    for i, (prompt, temp) in enumerate(attempts[:max_retries + 1]):
        try:
            out = _call(b64, prompt, temp, model=modell, num_predict=90 if modell.startswith("llava") else 120)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"{modell} call failed (try {i + 1}): {e}")
            continue
        bad, reason = _is_bad(out)
        if not bad:
            return entspekulieren(out) or re.split(r"(?<=[.!?])\s+", out.strip())[0]
        logger.info(f"{modell} retry (try {i + 1}, reason={reason}): got {out[:60]!r}")
    return (entspekulieren(out) or out) if out else ""


def zaehle_personen(image_path: str | Path, model: str | None = None) -> int | None:
    """Fragt das Bildmodell nach der Personenzahl (Zahl-only-Prompt, temperature 0).

    None = keine Aussage (Modell fehlt / unlesbare Antwort). Dient als Ergänzung zur
    Gesichtserkennung (Haar findet in Totalen/Profilen oft nichts).
    """
    p = Path(image_path)
    if not p.exists():
        return None
    modell = model or vision_modell()
    if modell is None or not modell.startswith("llava"):
        return None  # moondream antwortet auf Zähl-Prompts leer (getestet)
    try:
        b64 = base64.b64encode(p.read_bytes()).decode()
        out = _call(b64, _COUNT_PROMPT, 0.0, model=modell, num_predict=6, timeout=60)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"{modell} count failed: {e}")
        return None
    m = re.search(r"\d+", out or "")
    if not m:
        worte = {"zero": 0, "none": 0, "no": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5}
        for w, n in worte.items():
            if re.search(rf"\b{w}\b", (out or "").lower()):
                return n
        return None
    n = int(m.group())
    return n if 0 <= n <= 20 else None
