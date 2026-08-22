#!/bin/zsh
# Wechselt das geöffnete Projekt, ohne die Oberfläche anzufassen.
#
# Backend und Arbeiter des laufenden Projekts werden beendet, dann die des Ziels
# gestartet — auf demselben Port 8001, weshalb die Oberfläche stehen bleiben und
# nur neu laden muss.
#
#   ./wechsel_projekt.sh ./start_bingo_night.sh
#
# Wird vom Programm aufgerufen, lässt sich aber auch von Hand benutzen.
set -e
cd "$(dirname "$0")"

ziel="$1"
if [[ -z "$ziel" || ! -x "$ziel" ]]; then
  echo "Aufruf: ./wechsel_projekt.sh ./start_<projekt>.sh"
  exit 1
fi

echo "── Wechsel nach $ziel ──"
pkill -f "uvicorn backend.main:app" 2>/dev/null || true
pkill -f "celery -A backend.core.celery_app worker" 2>/dev/null || true

# Uvicorn fährt auf SIGTERM geordnet herunter und wartet dabei auf offene
# Verbindungen. Die Oberfläche fragt während des Wechsels im Sekundentakt nach,
# ob das neue Backend schon da ist — und hält das alte damit am Leben. Nach einer
# Schonfrist wird deshalb hart beendet, sonst bleibt der Port belegt und der neue
# Dienst scheitert an „address already in use".
frei=0
for i in {1..20}; do
  if ! lsof -ti :8001 > /dev/null 2>&1; then frei=1; break; fi
  sleep 0.25
done
if [[ $frei -eq 0 ]]; then
  echo "Port 8001 nach 5 s noch belegt — beende hart."
  lsof -ti :8001 2>/dev/null | xargs -r kill -9 2>/dev/null || true
  for i in {1..20}; do
    if ! lsof -ti :8001 > /dev/null 2>&1; then frei=1; break; fi
    sleep 0.25
  done
fi
if [[ $frei -eq 0 ]]; then
  echo "Port 8001 lässt sich nicht freigeben. Abbruch."
  exit 1
fi

export CINASSIST_NUR_DIENSTE=1
exec "$ziel"
