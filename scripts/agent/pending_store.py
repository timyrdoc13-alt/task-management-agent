"""Persistent approval previews (survive bot restart)."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from kaiten_api import STATE_DIR

PENDING_PATH = STATE_DIR / "pending.json"
TTL_SEC = 3600


def _load() -> dict[str, dict[str, Any]]:
    if not PENDING_PATH.exists():
        return {}
    try:
        data = json.loads(PENDING_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


def _save(data: dict[str, dict[str, Any]]) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    PENDING_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _prune(data: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    now = time.time()
    return {
        k: v
        for k, v in data.items()
        if now - float(v.get("created", now)) < TTL_SEC
    }


def get(token: str) -> dict[str, Any] | None:
    data = _prune(_load())
    _save(data)
    return data.get(token)


def put(token: str, payload: dict[str, Any]) -> None:
    data = _prune(_load())
    payload = dict(payload)
    payload.setdefault("created", time.time())
    data[token] = payload
    _save(data)


def pop(token: str) -> dict[str, Any] | None:
    data = _prune(_load())
    val = data.pop(token, None)
    _save(data)
    return val


def find_task_preview_by_chat(chat_id: int) -> tuple[str, dict[str, Any]] | None:
    """Active task/research preview awaiting confirm or text revision."""
    data = _prune(_load())
    for token, payload in data.items():
        if payload.get("chat_id") == chat_id and payload.get("kind") == "task_preview":
            return token, payload
    return None
