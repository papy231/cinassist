"""Ein dünner, robuster JSON-Aufruf gegen Ollama (qwen2.5:14b bevorzugt) für die Kontext-Schicht.

Temperatur 0, `format=json`, Modell nach kurzer Zeit entladen (RAM neben Whisper/CLIP/llava). Kein Retry-Wunder:
bei kaputtem JSON wird `None` zurückgegeben und die aufrufende Schicht markiert `unsicher`.
"""
from __future__ import annotations

import json
import logging
import os
import urllib.request

from backend.core.config import OLLAMA_BASE_URL, OLLAMA_MODEL
from backend.core.vision_describe import modell_verfuegbar

logger = logging.getLogger("cinassist.skript.llm")


def modell() -> str:
    return os.getenv("OLLAMA_SYNTHESE_MODEL") or ("qwen2.5:14b" if modell_verfuegbar("qwen2.5:14b") else OLLAMA_MODEL)


def frage_json(prompt: str, num_predict: int = 1200, timeout: float = 300.0, num_ctx: int = 8192) -> dict | list | None:
    body = json.dumps({
        "model": modell(), "prompt": prompt, "stream": False, "format": "json", "keep_alive": "3m",
        "options": {"temperature": 0.0, "num_predict": num_predict, "top_p": 0.8, "num_ctx": num_ctx},
    }).encode()
    req = urllib.request.Request(f"{OLLAMA_BASE_URL}/api/generate", data=body, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = (json.loads(r.read()).get("response") or "").strip()
    except Exception as e:  # noqa: BLE001
        logger.warning(f"LLM nicht erreichbar: {e}")
        return None
    try:
        return json.loads(raw)
    except Exception:  # noqa: BLE001
        logger.warning(f"LLM-JSON unlesbar: {raw[:200]!r}")
        return None
