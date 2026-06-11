#!/bin/zsh
# Reload the running Switchboard services after a code change. BOTH the worker
# (writes data) and the API (serves it) run as separate launchd processes and
# neither hot-reloads, so a code change needs both restarted — forgetting the API
# is why a new endpoint can 404 while the worker already behaves correctly.
#
# The frontend dev server (vite) hot-reloads from disk and is NOT managed here;
# restart it manually if needed:
#   cd frontend && VITE_API_TARGET=http://localhost:8400 npm run dev
set -euo pipefail

UID_=$(id -u)
for name in com.switchboard.worker com.switchboard.api; do
  if launchctl kickstart -k "gui/$UID_/$name" 2>/dev/null; then
    echo "reloaded $name"
  else
    echo "$name not loaded — run deploy/install.sh first" >&2
  fi
done
echo "Done. (DB migrations are separate: .venv/bin/python -m alembic upgrade head)"
