"""
Pre-compute CLIP text embeddings for the cinematic prompts used by the
KI-Schnitt-Algorithmus (Structured Cut).

Ce script est exécuté UNE SEULE FOIS (manuellement ou lors du setup) :
il encode chaque prompt textuel via le text encoder de CLIP ViT-B/32
et sauvegarde les vecteurs 512-dim dans backend/data/prompt_embeddings.json.

Au runtime, ai.py charge ce JSON et calcule des scores de scènes par
similarité cosinus image↔texte, sans jamais avoir à recharger CLIP.

Référence : Radford et al. (2021) "Learning Transferable Visual Models
From Natural Language Supervision" — ICML 2021.

Lancer avec :
    python -m backend.tools.build_prompt_embeddings
ou directement :
    .venv/bin/python backend/tools/build_prompt_embeddings.py
"""

from __future__ import annotations

import json
from pathlib import Path

import torch
import open_clip

# ─── Prompt-Sammlungen ───────────────────────────────────────
# Jede Sammlung definiert eine "Achse" der visuellen Bewertung.
# Diese werden in ai.py per Kosinus-Ähnlichkeit gegen das pro-Szenen-
# Embedding gemessen. Die Listen sind nach Hand kuratiert auf Basis
# der Film-Grammatik (Murch 2001).

PROMPTS: dict[str, list[str]] = {
    # Visuell interessante Shots — wirken auf Aufmerksamkeit/Energie
    "interesting": [
        "a cinematic shot with strong composition and dramatic lighting",
        "a visually striking film frame with depth and detail",
        "a captivating moment with rich color and contrast",
        "a beautiful establishing landscape shot",
    ],
    # Visuell schwache Shots — Strafterm gegen schlechte Aufnahmen
    "boring": [
        "a blurry out-of-focus shot",
        "a static empty scene with no subject",
        "a dark unclear frame with bad exposure",
        "a flat featureless image without interest",
    ],
    # Action — hohe Bewegung, dynamische Komposition
    "action": [
        "an action shot with dynamic movement and energy",
        "a fast chase scene with motion blur",
        "a dramatic moment of physical action",
    ],
    # Ruhig — kontemplative oder einführende Aufnahmen
    "calm": [
        "a peaceful quiet contemplative scene",
        "a slow gentle shot with stillness",
        "a serene landscape with no movement",
    ],
    # Dialog / Talking head — für A-Roll Klassifikation
    "dialog": [
        "a person talking directly to camera",
        "a close-up interview shot of someone speaking",
        "two people having a conversation in dialogue",
    ],
}


def main() -> None:
    print("📦 Loading CLIP ViT-B/32 (text encoder)...")
    device = "mps" if torch.backends.mps.is_available() else "cpu"
    print(f"   device = {device}")

    model, _, _ = open_clip.create_model_and_transforms(
        "ViT-B/32", pretrained="openai", device=device
    )
    tokenizer = open_clip.get_tokenizer("ViT-B/32")
    model.eval()

    def encode(prompts: list[str]) -> list[list[float]]:
        tokens = tokenizer(prompts).to(device)
        with torch.no_grad():
            embs = model.encode_text(tokens)
            # L2-normalize so cosine similarity = dot product
            embs = embs / embs.norm(dim=-1, keepdim=True)
        return embs.cpu().tolist()

    data: dict[str, list[list[float]]] = {}
    for category, prompts in PROMPTS.items():
        print(f"🔤 Encoding {len(prompts)} prompts for category '{category}'...")
        data[category] = encode(prompts)

    # Save to backend/data/prompt_embeddings.json
    backend_dir = Path(__file__).resolve().parent.parent
    out = backend_dir / "data" / "prompt_embeddings.json"
    out.parent.mkdir(parents=True, exist_ok=True)

    out.write_text(json.dumps({
        "model": "ViT-B/32 (OpenAI)",
        "dim": 512,
        "categories": list(PROMPTS.keys()),
        "prompts": PROMPTS,
        "embeddings": data,
    }, indent=2))

    print(f"\n✓ Wrote {out}")
    print(f"   File size: {out.stat().st_size / 1024:.1f} KB")
    total = sum(len(v) for v in data.values())
    print(f"   {total} embeddings × 512 dim")


if __name__ == "__main__":
    main()
