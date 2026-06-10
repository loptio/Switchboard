#!/bin/zsh
# Remove the Switchboard launchd user agents (worker + API). No sudo.
set -euo pipefail

UID_=$(id -u)
for name in com.switchboard.worker com.switchboard.api; do
  launchctl bootout "gui/$UID_/$name" 2>/dev/null && echo "stopped $name" || echo "$name was not loaded"
  rm -f "$HOME/Library/LaunchAgents/$name.plist"
done
echo "uninstalled (logs kept in ~/Library/Logs/switchboard)"
