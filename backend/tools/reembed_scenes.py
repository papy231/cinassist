"""Re-embed aller Szenen mit dem aktuellen CLIP-Encoder (core/clip_encoder).

Nötig nach einem Encoder-Wechsel: Dimension/Modell ändert sich, alte Embeddings
werden inkompatibel. Extrahiert CLIP_FRAMES Frames pro Szene und mittelt sie.

Aufruf (dev):   backend/.venv/bin/python -m backend.tools.reembed_scenes
Aufruf (prof):  DATABASE_URL=postgresql+asyncpg://cinassist:cinassist@localhost:5433/cinassist \
                backend/.venv/bin/python -m backend.tools.reembed_scenes
"""

from __future__ import annotations

import asyncio
import subprocess
import uuid

from sqlalchemy import select

from backend.core import clip_encoder
from backend.core.config import CLIP_FRAMES, FFMPEG_BIN, TEMP_DIR
from backend.core.database import AsyncSessionLocal, Clip, Szene


async def main() -> None:
    n_frames = max(1, int(CLIP_FRAMES))
    fraktionen = [(k + 1) / (n_frames + 1) for k in range(n_frames)]
    TEMP_DIR.mkdir(parents=True, exist_ok=True)
    dim = clip_encoder.embed_dim()
    print(f"CLIP-Encoder: dim={dim}, frames/Szene={n_frames}")

    async with AsyncSessionLocal() as db:
        rows = (await db.execute(
            select(Szene, Clip).join(Clip, Clip.id == Szene.clip_id)
            .order_by(Clip.dateiname, Szene.szenen_nr)
        )).all()
        print(f"{len(rows)} Szenen werden neu eingebettet…")
        ok = 0
        for szene, clip in rows:
            start = float(szene.start_zeit or 0.0)
            dauer = max(0.0, float(szene.end_zeit or 0.0) - start)
            paths = []
            for fr in fraktionen:
                t = start + dauer * fr
                fp = TEMP_DIR / f"reembed_{uuid.uuid4().hex}.jpg"
                subprocess.run(
                    [FFMPEG_BIN, "-y", "-ss", str(t), "-i", clip.dateipfad,
                     "-frames:v", "1", "-q:v", "2", str(fp)],
                    capture_output=True, timeout=60,
                )
                if fp.exists():
                    paths.append(fp)
            emb = clip_encoder.embed_images_mean(paths)
            for fp in paths:
                try:
                    fp.unlink()
                except OSError:
                    pass
            if emb is not None:
                szene.clip_embedding = emb.tolist()
                ok += 1
                print(f"  OK  {clip.dateiname} #{szene.szenen_nr} ({len(paths)} frames)")
            else:
                print(f"  --  {clip.dateiname} #{szene.szenen_nr}: kein Frame extrahiert")
        await db.commit()
        print(f"Fertig: {ok}/{len(rows)} Szenen re-embedded (dim={dim}).")


if __name__ == "__main__":
    asyncio.run(main())
