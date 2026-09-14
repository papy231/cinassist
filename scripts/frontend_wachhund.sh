#!/bin/zsh
# Wachhund für die Oberfläche auf einem Server (launchd, alle 60 s).
#
# Next.js hält offene Verbindungen zum Backend. Startet das Backend neu, bleiben
# diese Verbindungen tot: jede /api-Anfrage läuft in "socket hang up" und nach
# 30 s in einen 500er, bis die Oberfläche selbst neu gestartet wird. Dieses
# Skript prüft eine leichte API-Anfrage über die Oberfläche und startet sie bei
# Zeitüberschreitung oder Fehler neu.
#
#   FRONTEND_PORT   Port der Oberfläche (Standard 3003)
#   FRONTEND_LABEL  launchd-Label der Oberfläche (Standard com.discoverstudio.cinassist-frontend)
PORT="${FRONTEND_PORT:-3003}"
LABEL="${FRONTEND_LABEL:-com.discoverstudio.cinassist-frontend}"
code=$(curl -s -o /dev/null -m 8 -w '%{http_code}' "http://localhost:${PORT}/api/projekt")
if [[ "$code" != "200" ]]; then
  echo "$(date '+%F %T') Oberfläche antwortet nicht (HTTP ${code:-0}) – Neustart von ${LABEL}"
  launchctl kickstart -k "gui/$(id -u)/${LABEL}"
fi
