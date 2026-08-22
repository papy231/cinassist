#!/bin/zsh
# Startet das Projekt „projekt2".
#
#   Datenbank      cinassist_projekt2
#   Medien         ~/cinassist_projekt2
#   Warteschlange  Redis, Datenbank 1
#   Backend        http://localhost:8001
#   Oberfläche     http://localhost:3000
#
# Alle Projekte teilen sich Port 8001. Das ist unbedenklich, weil ohnehin nur eines
# zur Zeit geöffnet sein kann, und es erlaubt den Wechsel aus dem Programm heraus:
# die Oberfläche muss ihr Ziel nie ändern.
#
# Mit CINASSIST_NUR_DIENSTE=1 werden nur Backend und Arbeiter gestartet, ohne die
# Oberfläche. Diesen Weg nimmt der Projektwechsel im Programm.
set -e
cd "$(dirname "$0")"

if [[ -z "$CINASSIST_NUR_DIENSTE" ]] && pgrep -f "next dev" > /dev/null; then
  echo "Es läuft bereits eine CinAssist-Oberfläche."
  echo "Bitte zuerst  ./stop_cinassist.sh  ausführen."
  exit 1
fi

# Jede Angabe wird ausdrücklich gesetzt, keine aus der Umgebung übernommen. Beim
# Wechsel aus dem Programm erbt das neue Projekt sonst den Medienordner des alten
# und legt seine Vorschaudateien im fremden Verzeichnis ab.
export DATABASE_URL="postgresql+asyncpg://cinassist:cinassist@localhost:5432/cinassist_projekt2"
export CINASSIST_DATA_DIR="$HOME/cinassist_projekt2"
export REDIS_URL="redis://localhost:6379/1"
export CINASSIST_BACKEND_URL="http://localhost:8001"
export NEXT_PUBLIC_API_URL="http://localhost:8001"
export NEXT_PUBLIC_WS_URL="ws://localhost:8001"

echo "Projekt: projekt2"
backend/.venv/bin/celery -A backend.core.celery_app worker --pool=solo --loglevel=info -n projekt2@%h &
backend/.venv/bin/uvicorn backend.main:app --host 0.0.0.0 --port 8001 &
if [[ -z "$CINASSIST_NUR_DIENSTE" ]]; then
  npm run dev -- --port 3000 &
fi
wait
