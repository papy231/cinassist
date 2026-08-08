#!/bin/bash
# Sync CinAssist from Mac mini to this MacBook.
# Usage: ./scripts/sync-from-mac-mini.sh [--media]
#   default: code only (fast)
#   --media: also sync backend/{proxies,uploads,data,temp} (slower)

set -e

PROJ="$( cd "$( dirname "${BASH_SOURCE[0]}" )/.." && pwd )"
MC="pascalnikiema@macmini.tailef3707.ts.net"

echo "▶ Sync code from Mac mini → $PROJ"
rsync -avz --delete \
  --exclude node_modules --exclude .next --exclude .venv \
  --exclude __pycache__ --exclude '*.pyc' \
  --exclude backend/proxies --exclude backend/uploads \
  --exclude backend/temp --exclude backend/outputs \
  --exclude '*.bak' --exclude '.env.local' \
  "$MC:~/Projects/cinassist/" "$PROJ/"

if [ "$1" = "--media" ]; then
  echo "▶ Sync media (this may take a few minutes)"
  rsync -avz "$MC:~/Projects/cinassist/backend/proxies/" "$PROJ/backend/proxies/"
  rsync -avz "$MC:~/Projects/cinassist/backend/uploads/" "$PROJ/backend/uploads/"
  rsync -avz "$MC:~/Projects/cinassist/backend/data/"    "$PROJ/backend/data/"
  rsync -avz "$MC:~/Projects/cinassist/backend/temp/"    "$PROJ/backend/temp/"
fi

echo "✅ Sync done. Restart backend + frontend to pick up code changes."
