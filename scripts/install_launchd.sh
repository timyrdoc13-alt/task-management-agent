#!/bin/bash
# Install launchd jobs for kaiten-agent bot + reminder.
# Re-run safely: it unloads first.

set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
TARGET="$HOME/Library/LaunchAgents"
mkdir -p "$TARGET"

mkdir -p "$HOME/Library/Logs/kaiten-agent"

for job in com.kaiten-agent.bot com.kaiten-agent.reminder; do
  PLIST="$HERE/${job}.plist"
  DEST="$TARGET/${job}.plist"
  cp "$PLIST" "$DEST"
  launchctl unload "$DEST" 2>/dev/null || true
  launchctl load "$DEST"
  echo "loaded: $DEST"
done

echo ""
echo "Status:"
launchctl list | grep kaiten-agent || echo "(jobs not yet running, check logs)"
echo ""
echo "Logs (launchd):"
echo "  tail -f $HOME/Library/Logs/kaiten-agent/bot.stderr.log"
echo "  tail -f $HOME/Library/Logs/kaiten-agent/reminder.stderr.log"
echo ""
echo "Audit (API calls):"
echo "  tail -f \"$HOME/Library/Application Support/kaiten-agent/logs/calls.jsonl\""
