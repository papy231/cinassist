#!/usr/bin/env bash
# CinAssist — Stoppt alle lokalen Dienste
cd "$(dirname "$0")"
echo "■ Stoppe Backend, Celery, Frontend…"
pkill -f "uvicorn backend.main" 2>/dev/null || true
pkill -f "celery -A backend"    2>/dev/null || true
pkill -f "next dev"             2>/dev/null || true
echo "■ Stoppe Docker (PostgreSQL + Redis)…"
docker compose down
echo "✓ Gestoppt."
