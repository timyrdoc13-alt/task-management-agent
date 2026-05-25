#!/usr/bin/env python3
"""Preflight checks for kaiten-task-agent. Run before first use."""

from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from kaiten_api import ARTIFACTS_DIR, ENV, LOGS_DIR, http  # noqa: E402


def ok(msg: str) -> None:
    print(f"✓ {msg}")


def fail(msg: str) -> None:
    print(f"✗ {msg}")
    sys.exit(1)


def main() -> int:
    if not ENV.get("KAITEN_BASE_URL"):
        fail("KAITEN_BASE_URL not set in .env")
    if not ENV.get("KAITEN_API_TOKEN"):
        fail("KAITEN_API_TOKEN not set in .env")
    ok(".env loaded")

    code, data = http("GET", "/users/current")
    if code >= 400:
        fail(f"token check failed: HTTP {code} {data}")
    user = (data or {}).get("full_name") or (data or {}).get("email") or "<unknown>"
    ok(f"token valid (user: {user})")

    board_id = ENV.get("KAITEN_DEFAULT_BOARD_ID")
    column_id = ENV.get("KAITEN_DEFAULT_COLUMN_ID")
    if not board_id:
        fail("KAITEN_DEFAULT_BOARD_ID not set")
    code, data = http("GET", f"/boards/{board_id}")
    if code >= 400:
        fail(f"board {board_id}: HTTP {code}")
    ok(f"board {board_id} exists (\"{data.get('title')}\")")

    if column_id:
        cols = {c["id"]: c for c in data.get("columns", [])}
        if int(column_id) not in cols:
            fail(f"column {column_id} not found on board {board_id}")
        ok(f"column {column_id} exists (\"{cols[int(column_id)].get('title')}\")")

    if not LOGS_DIR.exists():
        fail(f"logs dir missing: {LOGS_DIR}")
    try:
        (LOGS_DIR / ".w").write_text("ok")
        (LOGS_DIR / ".w").unlink()
    except OSError as e:
        fail(f"logs dir not writable: {e}")
    ok(f"logs dir writable: {LOGS_DIR}")

    if not ARTIFACTS_DIR.exists():
        fail(f"artifacts dir missing: {ARTIFACTS_DIR}")
    test = ARTIFACTS_DIR / ".write_test"
    try:
        test.write_text("ok")
        test.unlink()
    except OSError as e:
        fail(f"artifacts dir not writable: {e}")
    ok(f"artifacts dir writable: {ARTIFACTS_DIR}")

    if shutil.which("osascript"):
        ok("osascript available")
    else:
        print("⚠ osascript not found — macOS notifications won't work")

    print("ready.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
