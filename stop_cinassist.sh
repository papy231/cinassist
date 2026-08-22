#!/bin/zsh
# Beendet alle laufenden CinAssist-Dienste beider Projekte.
# Danach lässt sich mit einem der Start-Skripte ein Projekt öffnen.
echo "Beende CinAssist-Dienste…"
pkill -f "uvicorn backend.main:app"                 && echo "  Backend beendet"
pkill -f "celery -A backend.core.celery_app worker" && echo "  Hintergrundarbeiter beendet"
pkill -f "next dev"                                 && echo "  Oberfläche beendet"
sleep 1
echo "Fertig."
