#!/usr/bin/env python3
"""macOS notification helper. Logs every reminder."""

from __future__ import annotations

import argparse
import json
import shlex
import subprocess
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from kaiten_api import LOGS_DIR, MSK  # noqa: E402

REMINDER_LOG = LOGS_DIR / "reminders.log"


def notify(title: str, body: str) -> bool:
    title_q = title.replace('"', "'").replace("\\", "")
    body_q = body.replace('"', "'").replace("\\", "")
    script = f'display notification "{body_q}" with title "{title_q}" sound name "Glass"'
    try:
        subprocess.run(["osascript", "-e", script], check=True, timeout=5)
        return True
    except Exception as e:
        print(f"notify failed: {e}", file=sys.stderr)
        return False


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--title", required=True)
    p.add_argument("--body", required=True)
    p.add_argument("--card-ids", default="", help="comma-separated")
    args = p.parse_args()

    sent = notify(args.title, args.body)
    rec = {
        "ts": datetime.now(MSK).isoformat(timespec="seconds"),
        "title": args.title,
        "body": args.body,
        "card_ids": [x for x in args.card_ids.split(",") if x],
        "sent": sent,
    }
    try:
        REMINDER_LOG.parent.mkdir(parents=True, exist_ok=True)
        with REMINDER_LOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except OSError:
        pass
    print(json.dumps(rec, ensure_ascii=False))
    return 0 if sent else 1


if __name__ == "__main__":
    sys.exit(main())
