#!/bin/zsh
# Install (or reinstall) the Switchboard launchd user agents: the worker
# (scheduler + pending-run drain, wrapped in caffeinate) and the control-plane
# API on 127.0.0.1:8400. Idempotent; no sudo. Run from anywhere.
set -euo pipefail

DEPLOY_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$DEPLOY_DIR")"
LOG_DIR="$HOME/Library/Logs/switchboard"
AGENTS_DIR="$HOME/Library/LaunchAgents"
UID_=$(id -u)

mkdir -p "$LOG_DIR" "$AGENTS_DIR"

for name in com.switchboard.worker com.switchboard.api; do
  # Rewrite the placeholders into the user's real paths.
  sed -e "s|__PROJECT_DIR__|$PROJECT_DIR|g" -e "s|__LOG_DIR__|$LOG_DIR|g" \
    "$DEPLOY_DIR/$name.plist" > "$AGENTS_DIR/$name.plist"
  plutil -lint -s "$AGENTS_DIR/$name.plist"
  # Reload cleanly whether or not it was already loaded.
  launchctl bootout "gui/$UID_/$name" 2>/dev/null || true
  launchctl bootstrap "gui/$UID_" "$AGENTS_DIR/$name.plist"
  echo "loaded $name"
done

echo
echo "Switchboard services installed:"
echo "  worker : launchctl print gui/$UID_/com.switchboard.worker | head -20"
echo "  api    : http://127.0.0.1:8400/docs"
echo "  logs   : $LOG_DIR/{worker,api}.log"
echo "Uninstall: deploy/uninstall.sh"
