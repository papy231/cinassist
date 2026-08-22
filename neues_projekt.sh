#!/bin/zsh
# Legt ein neues, vollständig leeres CinAssist-Projekt an.
#
#   ./neues_projekt.sh "Mein zweiter Film"
#
# Angelegt werden eine eigene Datenbank, ein eigener Medienordner, eine eigene
# Warteschlange und ein eigenes Start-Skript. Bestehende Projekte bleiben
# unberührt; es wird nichts überschrieben und nichts gelöscht.
set -e
cd "$(dirname "$0")"

if [[ -z "$1" ]]; then
  echo "Aufruf:  ./neues_projekt.sh \"Name des Projekts\""
  exit 1
fi

NAME="$1"
# Kennung aus dem Namen: Kleinbuchstaben, Umlaute aufgelöst, nur Buchstaben,
# Ziffern und Unterstrich. Sie trägt Datenbank, Ordner und Start-Skript.
# Die Umformung läuft über Python, weil sed und tr auf macOS mit Umlauten und
# mehrfachen Trennzeichen nicht verlässlich umgehen.
KENNUNG=$(backend/.venv/bin/python -c '
import re, sys, unicodedata
name = sys.argv[1].lower()
for a, b in (("ä","ae"), ("ö","oe"), ("ü","ue"), ("ß","ss")):
    name = name.replace(a, b)
name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
print(re.sub(r"_+", "_", re.sub(r"[^a-z0-9]", "_", name)).strip("_"))
' "$NAME")

if [[ -z "$KENNUNG" ]]; then
  echo "Aus dem Namen lässt sich keine Kennung bilden. Bitte Buchstaben verwenden."
  exit 1
fi

DB="cinassist_$KENNUNG"
DATEN="$HOME/cinassist_$KENNUNG"
SKRIPT="start_$KENNUNG.sh"

# Alle Projekte hören auf Port 8001. Das ist unbedenklich, weil nur eines zur Zeit
# geöffnet sein kann, und es erlaubt den Wechsel aus dem Programm heraus: die
# Oberfläche muss ihr Ziel nie ändern. Getrennt bleibt die Warteschlange.
PORT=8001
REDIS=0
for f in start_*.sh; do
  [[ -e "$f" ]] || continue
  r=$(grep -o 'redis://localhost:6379/[0-9]*' "$f" | head -1 | sed 's|.*/||')
  [[ -n "$r" && "$r" -gt "$REDIS" ]] && REDIS=$r
done
REDIS=$((REDIS + 1))

if [[ "$REDIS" -gt 15 ]]; then
  echo "Redis stellt nur die Datenbanken 0 bis 15 bereit. Bitte ein altes Projekt entfernen."
  exit 1
fi

if psql -h localhost -p 5432 -lqt | cut -d'|' -f1 | grep -qw "$DB"; then
  echo "Die Datenbank $DB gibt es bereits. Bitte einen anderen Namen wählen."
  exit 1
fi
if [[ -e "$SKRIPT" ]]; then
  echo "Das Skript $SKRIPT gibt es bereits. Bitte einen anderen Namen wählen."
  exit 1
fi

echo "Neues Projekt: $NAME"
echo "  Datenbank      $DB"
echo "  Medien         $DATEN"
echo "  Backend        Port $PORT"
echo "  Warteschlange  Redis, Datenbank $REDIS"
echo

createdb -h localhost -p 5432 -O cinassist "$DB"
mkdir -p "$DATEN"

# Den Klarnamen im Datenordner hinterlegen, damit die Oberfläche zeigen kann,
# welches Projekt gerade geöffnet ist.
backend/.venv/bin/python -c '
import json, sys
json.dump({"name": sys.argv[1]}, open(sys.argv[2], "w"), ensure_ascii=False, indent=1)
' "$NAME" "$DATEN/projekt.json"

DATABASE_URL="postgresql+asyncpg://cinassist:cinassist@localhost:5432/$DB" \
CINASSIST_DATA_DIR="$DATEN" \
backend/.venv/bin/python -c "
import asyncio, sys
sys.path.insert(0, '.')
from backend.core.database import init_db
asyncio.run(init_db())
"

cat > "$SKRIPT" <<EOF
#!/bin/zsh
# Startet das Projekt „$NAME".
#
#   Datenbank     $DB
#   Medien        $DATEN
#   Backend       http://localhost:$PORT
#   Oberfläche    http://localhost:3000
set -e
cd "\$(dirname "\$0")"

# Next.js lässt pro Projektordner nur einen Entwicklungsserver zu. Läuft noch ein
# anderes Projekt, wird hier abgebrochen statt halb zu starten.
if [[ -z "\$CINASSIST_NUR_DIENSTE" ]] && pgrep -f "next dev" > /dev/null; then
  echo "Es läuft bereits eine CinAssist-Oberfläche."
  echo "Bitte zuerst  ./stop_cinassist.sh  ausführen."
  exit 1
fi

export DATABASE_URL="postgresql+asyncpg://cinassist:cinassist@localhost:5432/$DB"
export CINASSIST_DATA_DIR="$DATEN"
export REDIS_URL="redis://localhost:6379/$REDIS"
export CINASSIST_BACKEND_URL="http://localhost:$PORT"
# Die Oberfläche ruft einen Teil der Schnittstellen unmittelbar auf, nicht über die
# Weiterleitung. Beide Wege müssen auf dasselbe Backend zeigen.
export NEXT_PUBLIC_API_URL="http://localhost:$PORT"
export NEXT_PUBLIC_WS_URL="ws://localhost:$PORT"

echo "Projekt: $NAME"
backend/.venv/bin/celery -A backend.core.celery_app worker --pool=solo --loglevel=info -n $KENNUNG@%h &
backend/.venv/bin/uvicorn backend.main:app --host 0.0.0.0 --port $PORT &
if [[ -z "\$CINASSIST_NUR_DIENSTE" ]]; then
  npm run dev -- --port 3000 &
fi
wait
EOF
chmod +x "$SKRIPT"

echo
echo "Angelegt. Zum Öffnen:"
echo
echo "    ./stop_cinassist.sh && ./$SKRIPT"
echo
