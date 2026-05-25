#!/bin/bash
# Daily reminder for kaiten-task-agent. Called by cron/launchd.
# Usage: daily_reminder.sh [morning|evening]

set -euo pipefail

MODE="${1:-}"
if [ -z "$MODE" ]; then
  h=$(date +%H)
  if [ "$h" -lt 14 ]; then MODE=morning; else MODE=evening; fi
fi
HERE="$(cd "$(dirname "$0")" && pwd)"
PY="${HERE}/../.venv/bin/python3"
[ -x "$PY" ] || PY="$(command -v python3)"

cd "${HERE}/.."

OVERDUE_JSON="$("$PY" scripts/kaiten_api.py list-overdue 2>/dev/null || echo '{}')"
TODAY_JSON="$("$PY" scripts/kaiten_api.py list-today 2>/dev/null || echo '{}')"

OVERDUE_COUNT=$(echo "$OVERDUE_JSON" | "$PY" -c "import sys,json; d=json.load(sys.stdin).get('data') or []; print(len(d))")
TODAY_COUNT=$(echo "$TODAY_JSON" | "$PY" -c "import sys,json; d=json.load(sys.stdin).get('data') or []; print(len(d))")

if [ "$OVERDUE_COUNT" = "0" ] && [ "$TODAY_COUNT" = "0" ]; then
  echo "no due cards, no notification"
  exit 0
fi

if [ "$MODE" = "morning" ]; then
  TITLE="Kaiten · сегодня ${TODAY_COUNT}, просрочено ${OVERDUE_COUNT}"
else
  TITLE="Kaiten вечером · просрочено ${OVERDUE_COUNT}"
fi

BODY=$(printf '%s\n%s' "$OVERDUE_JSON" "$TODAY_JSON" | "$PY" -c '
import sys, json
raw = sys.stdin.read().strip()
# Two concatenated JSON objects separated by newline; split robustly
parts, depth, start = [], 0, 0
for i, ch in enumerate(raw):
    if ch == "{":
        depth += 1
    elif ch == "}":
        depth -= 1
        if depth == 0:
            parts.append(raw[start:i+1])
            start = i + 1
seen = set()
items = []
for p in parts:
    try:
        data = json.loads(p).get("data") or []
    except Exception:
        continue
    for c in data:
        cid = c.get("id", 0)
        if cid in seen:
            continue
        seen.add(cid)
        title = (c.get("title") or "")[:40]
        items.append("#%d %s" % (cid, title))
        if len(items) >= 5:
            break
print(" · ".join(items) if items else "—")
')

"$PY" scripts/notify.py --title "$TITLE" --body "$BODY" || true
