"""
CinAssist — Vision description helper (moondream via Ollama).

Wrappe l'appel Ollama moondream avec :
    - Prompt court validé (le plus complexe fait halluciner moondream).
    - Retry si output vide, trop court, ou contient des caractères CJK
      (moondream hallucine ~20% du temps en chinois aléatoire — bug connu).

Utilisé par :
    - workers/ingest.py :: schritt_szenen_beschreiben (ingestion)
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

# Führendes verstümmeltes Token des moondream-Bugs (case-insensitiv).
_ARTIFACT_RE = re.compile(r"^\s*urns?\s+(of|with)\b", re.IGNORECASE)

OLLAMA_URL = "http://localhost:11434/api/generate"


def _is_bad(output: str) -> tuple[bool, str]:
    """Détecte outputs foireux : vide, trop court, CJK, très répétitif, artefact."""
    if not output or len(output.strip()) < 15:
        return True, "too_short"
    if _CJK_RE.search(output):
        return True, "cjk_hallucination"
    if _ARTIFACT_RE.match(output):
        return True, "truncation_artifact"
    # Détection répétition : même token répété 5+ fois consécutivement
    tokens = output.split()
    if len(tokens) > 5:
        for i in range(len(tokens) - 4):
            if len(set(tokens[i:i + 5])) == 1:
                return True, "repetition"
    return False, ""


def _call(image_b64: str, prompt: str, temperature: float = 0.1) -> str:
    body = json.dumps({
        "model": "moondream",
        "prompt": prompt,
        "images": [image_b64],
        "stream": False,
        "options": {"temperature": temperature, "num_predict": 120},
    }).encode()
    req = urllib.request.Request(
        OLLAMA_URL, data=body, headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read()).get("response", "").strip()


def describe_image(image_path: str | Path, max_retries: int = 2) -> str:
    """
    Décrit une image via moondream avec retry robuste.

    Retourne la description ou une string vide si toutes les tentatives échouent.
    """
    p = Path(image_path)
    if not p.exists():
        return ""
    b64 = base64.b64encode(p.read_bytes()).decode()

    attempts = [
        (_DEFAULT_PROMPT, 0.1),
        (_DEFAULT_PROMPT, 0.0),  # deterministic retry
        (_FALLBACK_PROMPT, 0.0),
    ]
    for i, (prompt, temp) in enumerate(attempts[:max_retries + 1]):
        try:
            out = _call(b64, prompt, temp)
        except Exception as e:
            logger.warning(f"moondream call failed (try {i + 1}): {e}")
            continue
        bad, reason = _is_bad(out)
        if not bad:
            return out
        logger.info(f"moondream retry (try {i + 1}, reason={reason}): got {out[:60]!r}")
    return out or ""
