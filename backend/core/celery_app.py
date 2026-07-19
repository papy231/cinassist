"""CinAssist — Celery App"""

from celery import Celery
from backend.core.config import CELERY_BROKER_URL, CELERY_RESULT_BACKEND, TIMEZONE

celery_app = Celery(
    "cinassist",
    broker=CELERY_BROKER_URL,
    backend=CELERY_RESULT_BACKEND,
    include=["backend.workers.ingest", "backend.workers.export"],
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone=TIMEZONE,
    enable_utc=True,
    task_track_started=True,
    worker_prefetch_multiplier=1,      # ein Task pro Worker (KI-lastig)
    task_acks_late=True,
    worker_max_tasks_per_child=10,     # Nach 10 Tasks Worker neustarten (Speicher)
)
