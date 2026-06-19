"""
CinAssist — WebSocket für Job-Status

ws://localhost:8000/ws/jobs/{job_id}

Sendet Fortschritts-Updates in Echtzeit:
  { "status": "laeuft", "progress": 68, "message": "CLIP Embedding 3/5..." }
  { "status": "fertig", "result": { ... } }
  { "status": "fehler", "message": "..." }
"""

import asyncio
import json
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

router = APIRouter()
logger = logging.getLogger("cinassist.ws")


@router.websocket("/ws/jobs/{job_id}")
async def job_websocket(websocket: WebSocket, job_id: str):
    """WebSocket-Endpoint für Echtzeit Job-Status Updates."""
    await websocket.accept()
    logger.info(f"WebSocket verbunden: Job {job_id}")

    from backend.core.database import AsyncSessionLocal, Job
    from sqlalchemy import select

    async def _db_state() -> dict | None:
        """Aktuellen Job-Status aus der DB lesen."""
        async with AsyncSessionLocal() as db:
            res = await db.execute(select(Job).where(Job.id == job_id))
            job = res.scalar_one_or_none()
            if not job:
                return None
            return {
                "status": job.status,
                "progress": job.fortschritt,
                "message": job.nachricht or "",
                "result": job.ergebnis,
            }

    try:
        # ── Sofort aktuellen Stand senden (verhindert Stuck-at-0%) ──
        current = await _db_state()
        if current:
            await websocket.send_json(current)
            if current["status"] in ("fertig", "fehler"):
                return

        # ── Pub/Sub + DB-Polling als Fallback ────────────────────────
        try:
            import redis.asyncio as aioredis
            r = aioredis.from_url("redis://localhost:6379/0")
            pubsub = r.pubsub()
            await pubsub.subscribe(f"job:{job_id}")
            use_pubsub = True
        except ImportError:
            use_pubsub = False

        letzter_fortschritt = current["progress"] if current else -1
        letzter_db_check = asyncio.get_event_loop().time()

        while True:
            # Pub/Sub Nachricht prüfen
            if use_pubsub:
                message = await pubsub.get_message(
                    ignore_subscribe_messages=True,
                    timeout=0.5,
                )
                if message and message["type"] == "message":
                    data = json.loads(message["data"])
                    await websocket.send_json(data)
                    if data.get("status") in ("fertig", "fehler"):
                        break

            # DB-Polling alle 1.5s als Fallback (fängt verpasste Pub/Sub-Msgs)
            now = asyncio.get_event_loop().time()
            if now - letzter_db_check >= 1.5:
                letzter_db_check = now
                state = await _db_state()
                if state:
                    if state["progress"] != letzter_fortschritt or state["status"] in ("fertig", "fehler"):
                        letzter_fortschritt = state["progress"]
                        await websocket.send_json(state)
                        if state["status"] in ("fertig", "fehler"):
                            break
                else:
                    break

            await asyncio.sleep(0.1)

        if use_pubsub:
            await pubsub.unsubscribe(f"job:{job_id}")
            await r.close()

    except WebSocketDisconnect:
        logger.info(f"WebSocket getrennt: Job {job_id}")
    except Exception as e:
        logger.error(f"WebSocket Fehler: {e}")
    finally:
        try:
            await websocket.close()
        except Exception:
            pass
