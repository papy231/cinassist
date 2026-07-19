#!/usr/bin/env bash
# CinAssist — Lokaler Start aller Dienste (macOS / Apple Silicon)
# Aufruf:  ./start.sh
# Stop:    ./stop.sh
set -e
cd "$(dirname "$0")"

echo "▶ 1/4  PostgreSQL + Redis (Docker)…"
docker compose up -d
# auf DB warten
for i in $(seq 1 20); do
  docker exec cinassist-db pg_isready -U cinassist >/dev/null 2>&1 && break
  sleep 1
done

echo "▶ 2/4  Backend (FastAPI :8001)…"
nohup backend/.venv/bin/uvicorn backend.main:app --host 0.0.0.0 --port 8001 \
  > /tmp/cinassist_backend.log 2>&1 &

echo "▶ 3/4  Celery Worker (solo-Pool — wichtig wegen PyTorch+fork auf macOS)…"
# --pool=solo verhindert den fork()-SIGABRT-Crash beim Laden von CLIP/PyTorch.
OBJC_DISABLE_INITIALIZE_FORK_SAFETY=YES KMP_DUPLICATE_LIB_OK=TRUE OMP_NUM_THREADS=1 \
  nohup backend/.venv/bin/celery -A backend.core.celery_app:celery_app worker \
  --pool=solo --loglevel=info \
  > /tmp/cinassist_celery.log 2>&1 &

echo "▶ 4/4  Frontend (Next.js :3003)…"
nohup npm run dev -- -p 3003 > /tmp/cinassist_frontend.log 2>&1 &

echo
echo "✓ Gestartet.  Frontend → http://localhost:3003   |   API → http://localhost:8001/docs"
echo "  Logs:  /tmp/cinassist_backend.log  /tmp/cinassist_celery.log  /tmp/cinassist_frontend.log"
echo "  Hinweis: Ollama muss separat laufen (ollama serve) für Szenen-Beschreibungen."
