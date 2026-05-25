#!/usr/bin/env python3
"""Kaiten API typed tools. Every call returns a structured JSON envelope.

Usage:
    python kaiten_api.py list-boards
    python kaiten_api.py list-columns --board-id 123
    python kaiten_api.py list-cards --board-id 123 [--due-before 2026-05-20] [--q text]
    python kaiten_api.py list-overdue
    python kaiten_api.py list-today
    python kaiten_api.py get-card --id 1234
    python kaiten_api.py draft-card --title "..." --priority P2 [--due 2026-05-20] [--desc "..."]
    python kaiten_api.py create-card --draft-id <id> --commit
    python kaiten_api.py update-priority --id 1234 --priority P1 --commit
    python kaiten_api.py update-due --id 1234 --due 2026-05-20 --commit
    python kaiten_api.py move-card --id 1234 --column-id 99 --commit
    python kaiten_api.py add-comment --id 1234 --text "..."
    python kaiten_api.py confirm-delete --id 1234       # prints HMAC token
    python kaiten_api.py delete-card --id 1234 --token <hmac> --commit

Без --commit все write-операции возвращают preview и НЕ обращаются к Kaiten.
"""

from __future__ import annotations

import argparse
import dataclasses
import hashlib
import hmac
import json
import os
import re
import secrets
import sys
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

try:
    from zoneinfo import ZoneInfo
except ImportError:
    ZoneInfo = None

MSK = ZoneInfo("Europe/Moscow") if ZoneInfo else timezone(timedelta(hours=3))

# Runtime: KAITEN_RUNTIME_DIR (Docker/Linux) or macOS Application Support.
_default_runtime = (
    Path(os.environ["KAITEN_RUNTIME_DIR"]).expanduser()
    if os.environ.get("KAITEN_RUNTIME_DIR")
    else Path.home() / "Library" / "Application Support" / "kaiten-agent"
)
_RUNTIME = _default_runtime
STATE_DIR = _RUNTIME / "state"
LOGS_DIR = _RUNTIME / "logs"
# artifacts: KAITEN_ARTIFACTS_DIR or ~/Documents/kaiten-agent/artifacts (macOS default)
_docs_base = Path.home() / "Documents" / "kaiten-agent"
ARTIFACTS_DIR = Path(
    os.environ.get("KAITEN_ARTIFACTS_DIR", str(_docs_base / "artifacts"))
).expanduser()

for d in (STATE_DIR, LOGS_DIR, ARTIFACTS_DIR):
    d.mkdir(parents=True, exist_ok=True)

SESSION_PATH = STATE_DIR / "session.json"
CALLS_LOG = LOGS_DIR / "calls.jsonl"

# Одноразово подтянуть session.json из старого пути ~/Documents/...
_OLD_SESSION = Path.home() / "Documents" / "kaiten-agent" / "state" / "session.json"
if _OLD_SESSION.exists() and not SESSION_PATH.exists():
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        SESSION_PATH.write_bytes(_OLD_SESSION.read_bytes())
    except OSError:
        pass

PRIORITY_TAGS = {"P1": "urgent", "P3": "low-priority"}


def load_env() -> dict[str, str]:
    env_path = Path(__file__).resolve().parent.parent / ".env"
    env: dict[str, str] = {}
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip().strip('"').strip("'")
    _overlays = (
        "KAITEN_",
        "TG_",
        "DEEPSEEK_",
        "SERPER_",
        "TAVILY_",
        "BRAVE_",
        "AUTO_",
        "RESEARCH_",
        "RATE_",
        "JINA_",
        "KROKI_",
    )
    for k, v in os.environ.items():
        if k == "TZ" or any(k.startswith(p) for p in _overlays):
            env[k] = v
    return env


ENV = load_env()
HMAC_SECRET = (ENV.get("KAITEN_API_TOKEN", "") + "::delete-confirm").encode()


def envelope(
    status: str,
    summary: str,
    data: Any = None,
    next_actions: list[str] | None = None,
    error_type: str | None = None,
) -> dict[str, Any]:
    return {
        "status": status,
        "summary": summary,
        "data": data,
        "next_valid_actions": next_actions or [],
        "error_type": error_type,
        "trace_id": secrets.token_hex(8),
        "ts": datetime.now(MSK).isoformat(timespec="seconds"),
    }


def log_call(tool: str, args: dict, result_status: str, duration_ms: int, extra: dict | None = None) -> None:
    args_safe = dict(args)
    if "token" in args_safe:
        args_safe["token"] = "***"
    token = ENV.get("KAITEN_API_TOKEN", "")
    last4 = token[-4:] if len(token) >= 4 else "----"
    rec = {
        "ts": datetime.now(MSK).isoformat(timespec="seconds"),
        "tool": tool,
        "args_hash": hashlib.sha256(
            json.dumps(args_safe, sort_keys=True, ensure_ascii=False).encode()
        ).hexdigest()[:16],
        "status": result_status,
        "duration_ms": duration_ms,
        "token_last4": last4,
        **(extra or {}),
    }
    line = json.dumps(rec, ensure_ascii=False) + "\n"
    try:
        LOGS_DIR.mkdir(parents=True, exist_ok=True)
        with CALLS_LOG.open("a", encoding="utf-8") as f:
            f.write(line)
    except OSError:
        alt = Path("/tmp") / "kaiten-agent-calls.jsonl"
        try:
            with alt.open("a", encoding="utf-8") as f:
                f.write(line)
        except OSError:
            pass


def http(method: str, path: str, body: dict | None = None) -> tuple[int, Any]:
    base = ENV.get("KAITEN_BASE_URL", "").rstrip("/")
    token = ENV.get("KAITEN_API_TOKEN", "")
    if not base or not token:
        raise RuntimeError("KAITEN_BASE_URL or KAITEN_API_TOKEN missing in .env")
    url = f"{base}/api/v1{path}"
    data = None if body is None else json.dumps(body).encode()
    max_attempts = int(ENV.get("KAITEN_HTTP_MAX_RETRIES", "3"))
    timeout = int(ENV.get("KAITEN_HTTP_TIMEOUT_SEC", "15"))
    last_code, last_resp = 0, {"error": "no response"}
    for attempt in range(max_attempts):
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Authorization", f"Bearer {token}")
        req.add_header("Accept", "application/json")
        if data:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read().decode("utf-8")
                return resp.status, json.loads(raw) if raw else None
        except urllib.error.HTTPError as e:
            body_err = e.read().decode("utf-8", errors="replace")
            last_code, last_resp = e.code, {"error": body_err}
            if e.code in {429, 502, 503, 504} and attempt < max_attempts - 1:
                time.sleep(min(2.0 ** attempt, 8.0))
                continue
            return last_code, last_resp
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            last_code, last_resp = 0, {"error": str(e)}
            if attempt < max_attempts - 1:
                time.sleep(min(2.0 ** attempt, 8.0))
                continue
            return last_code, last_resp
    return last_code, last_resp


def parse_iso_date(s: str) -> datetime:
    s = s.strip()
    if s in {"сегодня", "today"}:
        return datetime.now(MSK).replace(hour=23, minute=59, second=0, microsecond=0)
    if s in {"завтра", "tomorrow"}:
        return (datetime.now(MSK) + timedelta(days=1)).replace(
            hour=23, minute=59, second=0, microsecond=0
        )
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})$", s)
    if m:
        y, mo, d = map(int, m.groups())
        return datetime(y, mo, d, 23, 59, tzinfo=MSK)
    return datetime.fromisoformat(s).astimezone(MSK)


def _parse_api_dt(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(MSK)
    except (TypeError, ValueError):
        return None


def slugify(s: str, max_len: int = 40) -> str:
    s = re.sub(r"[^\w\s-]", "", s, flags=re.U).strip().lower()
    s = re.sub(r"[\s_-]+", "-", s)
    return s[:max_len] or "untitled"


@dataclass
class Draft:
    draft_id: str
    title: str
    description: str
    board_id: int
    column_id: int
    lane_id: int | None
    priority: str
    due_date: str | None
    tags: list[str]
    created_at: str
    responsible_user_id: int | None = None


def load_drafts() -> dict[str, dict]:
    if not SESSION_PATH.exists():
        return {}
    try:
        return json.loads(SESSION_PATH.read_text()).get("drafts", {})
    except Exception:
        return {}


def save_drafts(drafts: dict[str, dict]) -> None:
    state = {}
    if SESSION_PATH.exists():
        try:
            state = json.loads(SESSION_PATH.read_text())
        except Exception:
            state = {}
    state["drafts"] = drafts
    state["updated_at"] = datetime.now(MSK).isoformat(timespec="seconds")
    SESSION_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2))


def with_timing(fn, *args, **kwargs):
    t0 = time.time()
    res = fn(*args, **kwargs)
    return res, int((time.time() - t0) * 1000)


def list_boards() -> dict:
    code, data = http("GET", "/spaces?with_boards=true")
    if code >= 400:
        return envelope("error", f"HTTP {code}", data, error_type="http_error")
    boards = []
    for space in data or []:
        for b in space.get("boards", []) or []:
            boards.append(
                {
                    "id": b["id"],
                    "title": b.get("title"),
                    "space_id": space.get("id"),
                    "space_title": space.get("title"),
                }
            )
    return envelope("success", f"{len(boards)} boards", boards[:50], ["list_columns"])


def list_columns(board_id: int) -> dict:
    code, data = http("GET", f"/boards/{board_id}")
    if code >= 400:
        return envelope("error", f"HTTP {code}", data, error_type="http_error")
    cols = [
        {"id": c["id"], "title": c.get("title"), "sort_order": c.get("sort_order")}
        for c in (data or {}).get("columns", [])
    ]
    return envelope("success", f"{len(cols)} columns", cols, ["list_cards", "draft_card"])


def list_cards(
    board_id: int | None = None,
    due_before: str | None = None,
    q: str | None = None,
    overdue_only: bool = False,
    limit: int = 30,
    offset: int = 0,
) -> dict:
    params = []
    if board_id:
        params.append(f"board_id={board_id}")
    if q:
        params.append(f"query={urllib.parse.quote(q)}")
    if overdue_only:
        params.append("archived=false")
        params.append("state=1,2")
    params.append(f"limit={min(limit, 50)}")
    if offset > 0:
        params.append(f"offset={offset}")
    qs = "&".join(params)
    code, data = http("GET", f"/cards?{qs}")
    if code >= 400:
        return envelope("error", f"HTTP {code}", data, error_type="http_error")
    items = []
    now = datetime.now(MSK)
    cutoff = parse_iso_date(due_before) if due_before else None
    for c in data or []:
        due = c.get("due_date")
        due_dt = None
        if due:
            try:
                due_dt = datetime.fromisoformat(due.replace("Z", "+00:00")).astimezone(MSK)
            except Exception:
                pass
        if overdue_only and (not due_dt or due_dt >= now):
            continue
        if cutoff and (not due_dt or due_dt > cutoff):
            continue
        created_dt = _parse_api_dt(c.get("created"))
        updated_dt = _parse_api_dt(c.get("updated"))
        completed_dt = _parse_api_dt(c.get("completed_at"))
        col_changed_dt = _parse_api_dt(c.get("column_changed_at"))
        items.append(
            {
                "id": c["id"],
                "title": c.get("title"),
                "due_date": due_dt.isoformat(timespec="minutes") if due_dt else None,
                "asap": c.get("asap"),
                "column_id": c.get("column_id"),
                "state": c.get("state"),
                "archived": bool(c.get("archived")),
                "blocked": bool(c.get("blocked")),
                "created": created_dt.isoformat(timespec="seconds") if created_dt else None,
                "updated": updated_dt.isoformat(timespec="seconds") if updated_dt else None,
                "completed_at": completed_dt.isoformat(timespec="seconds")
                if completed_dt
                else None,
                "column_changed_at": col_changed_dt.isoformat(timespec="seconds")
                if col_changed_dt
                else None,
                "url": f"{ENV.get('KAITEN_BASE_URL','').rstrip('/')}/{c['id']}",
            }
        )
    return envelope(
        "success",
        f"{len(items)} cards",
        items,
        ["get_card", "update_card_priority", "update_card_due", "move_card"],
    )


def fetch_board_cards(
    board_id: int | None = None,
    *,
    page_size: int = 50,
    max_cards: int = 250,
) -> dict:
    """Paginated cards for analytics (board report)."""
    board_id = board_id or _int_env("KAITEN_DEFAULT_BOARD_ID")
    all_items: list[dict] = []
    offset = 0
    truncated = False
    while len(all_items) < max_cards:
        res = list_cards(board_id=board_id, limit=page_size, offset=offset)
        if res.get("status") != "success":
            return res
        batch = res.get("data") or []
        if not batch:
            break
        all_items.extend(batch)
        if len(batch) < page_size:
            break
        offset += page_size
        if len(all_items) >= max_cards:
            truncated = True
            all_items = all_items[:max_cards]
            break
    return envelope(
        "success",
        f"{len(all_items)} cards",
        {"cards": all_items, "truncated": truncated, "board_id": board_id},
    )


def patch_card(card_id: int, patch: dict[str, Any], commit: bool) -> dict:
    allowed = {"title", "description", "due_date"}
    body = {k: v for k, v in patch.items() if k in allowed}
    if not body:
        return envelope("error", "empty patch", error_type="invalid_arguments")
    if not commit:
        return envelope(
            "approval_required",
            f"would patch #{card_id}",
            {"card_id": card_id, "patch": body},
        )
    code, resp = http("PATCH", f"/cards/{card_id}", body)
    if code >= 400:
        return envelope("error", f"HTTP {code}", resp, error_type="http_error")
    return envelope("success", f"patched #{card_id}", resp)


def _int_env(key: str) -> int | None:
    v = ENV.get(key, "")
    return int(v) if v and str(v).isdigit() else None


def _int_list_env(key: str) -> list[int]:
    raw = (ENV.get(key) or "").strip()
    if not raw:
        return []
    out: list[int] = []
    for part in raw.replace(";", ",").split(","):
        part = part.strip()
        if part.isdigit():
            out.append(int(part))
    return out


_COLUMN_TREE_CACHE: tuple[int, list[dict[str, Any]]] | None = None


def fetch_board_columns_tree(board_id: int | None = None) -> list[dict[str, Any]]:
    """Board columns including subcolumns (Kaiten nests WIP under a parent column)."""
    global _COLUMN_TREE_CACHE
    bid = board_id or _int_env("KAITEN_DEFAULT_BOARD_ID")
    if not bid:
        return []
    if _COLUMN_TREE_CACHE and _COLUMN_TREE_CACHE[0] == bid:
        return _COLUMN_TREE_CACHE[1]
    code, cols = http("GET", f"/boards/{bid}/columns")
    if code >= 400 or not isinstance(cols, list):
        return []
    _COLUMN_TREE_CACHE = (bid, cols)
    return cols


def _column_tree_entry(column_id: int, tree: list[dict[str, Any]]) -> dict[str, Any] | None:
    for col in tree:
        if col.get("id") == column_id:
            return col
        for sub in col.get("subcolumns") or []:
            if sub.get("id") == column_id:
                return sub
    return None


def _expand_column_ids(primary_id: int, tree: list[dict[str, Any]]) -> list[int]:
    """Parent column id + all subcolumn ids (cards often live in subcolumns only)."""
    ids = [primary_id]
    for col in tree:
        if col.get("id") != primary_id:
            continue
        for sub in col.get("subcolumns") or []:
            sid = sub.get("id")
            if sid and sid not in ids:
                ids.append(int(sid))
    return ids


def _default_move_column_id(primary_id: int, tree: list[dict[str, Any]]) -> int:
    """Target column for move-to-wip: leaf subcolumn, not empty parent."""
    for col in tree:
        if col.get("id") != primary_id:
            continue
        subs = sorted(
            col.get("subcolumns") or [],
            key=lambda s: (s.get("sort_order") is None, s.get("sort_order") or 0),
        )
        if not subs:
            return primary_id
        for sub in subs:
            title = (sub.get("title") or "").strip().lower()
            if "ревью" in title or title == "review":
                continue
            return int(sub["id"])
        return int(subs[0]["id"])
    return primary_id


def board_column_config() -> dict[str, dict[str, Any]]:
    """Configured columns for default board (queue / wip / done).

    Each bucket has column_ids (all ids that count for reports/filters) and
    column_id (primary id used for move_card — leaf subcolumn when applicable).
    """
    labels = {
        "queue": "Очередь",
        "wip": "В работе",
        "done": "Готово",
    }
    env_keys = {
        "queue": ("KAITEN_COL_QUEUE", "KAITEN_COL_QUEUE_IDS", "KAITEN_DEFAULT_COLUMN_ID"),
        "wip": ("KAITEN_COL_WIP", "KAITEN_COL_WIP_IDS", "KAITEN_COL_WIP_MOVE"),
        "done": ("KAITEN_COL_DONE", "KAITEN_COL_DONE_IDS", None),
    }
    tree = fetch_board_columns_tree()
    out: dict[str, dict[str, Any]] = {}
    for key, label in labels.items():
        primary_key, ids_key, fallback_key = env_keys[key]
        cid = _int_env(primary_key) or (_int_env(fallback_key) if fallback_key else None)
        if not cid:
            continue
        extra = _int_list_env(ids_key)
        column_ids = list(dict.fromkeys(extra + _expand_column_ids(cid, tree)))
        move_override = _int_env("KAITEN_COL_WIP_MOVE") if key == "wip" else None
        if key == "wip" and move_override:
            move_id = move_override
        elif key == "wip":
            move_id = _default_move_column_id(cid, tree)
        else:
            move_id = cid
        out[key] = {
            "column_id": move_id,
            "parent_column_id": cid,
            "column_ids": column_ids,
            "title": label,
        }
    return out


def list_active_cards(
    include_done: bool = False,
    limit: int = 50,
    columns: list[str] | None = None,
) -> dict:
    """Cards on default board filtered by column keys: queue, wip, done."""
    board_id = _int_env("KAITEN_DEFAULT_BOARD_ID")
    res = list_cards(board_id=board_id, limit=min(limit, 50))
    if res.get("status") != "success":
        return res
    cols = board_column_config()
    if not cols:
        return envelope(
            "success",
            "cards (no column filter)",
            {
                "groups": [{"key": "all", "title": "Все", "cards": res.get("data") or []}],
                "uncategorized": [],
                "total": len(res.get("data") or []),
                "scope": columns or ["queue", "wip"],
            },
        )
    if columns:
        keys = [k for k in columns if k in cols]
    else:
        keys = ["queue", "wip"] + (["done"] if include_done else [])
    keys = [k for k in keys if k in cols]
    active_ids: set[int] = set()
    id_to_key: dict[int, str] = {}
    for k in keys:
        for cid in cols[k].get("column_ids") or [cols[k]["column_id"]]:
            active_ids.add(int(cid))
            id_to_key[int(cid)] = k
    buckets: dict[str, list] = {k: [] for k in keys}
    uncategorized: list = []
    for card in res.get("data") or []:
        col_id = card.get("column_id")
        if col_id not in active_ids:
            continue
        key = id_to_key.get(col_id)
        if key and key in buckets:
            buckets[key].append(card)
        else:
            uncategorized.append(card)
    groups = [
        {
            "key": key,
            "title": cols[key]["title"],
            "column_id": cols[key]["column_id"],
            "cards": buckets.get(key, []),
        }
        for key in ("queue", "wip", "done")
        if key in buckets
    ]
    total = sum(len(g["cards"]) for g in groups) + len(uncategorized)
    return envelope(
        "success",
        f"{total} cards on board",
        {
            "groups": groups,
            "uncategorized": uncategorized,
            "total": total,
            "scope": keys,
        },
        ["get_card", "list_cards"],
    )


def list_overdue() -> dict:
    return list_cards(overdue_only=True, limit=50)


def list_today() -> dict:
    tomorrow = (datetime.now(MSK) + timedelta(days=1)).strftime("%Y-%m-%d")
    return list_cards(due_before=tomorrow, limit=50)


def get_card(card_id: int) -> dict:
    code, data = http("GET", f"/cards/{card_id}")
    if code >= 400:
        return envelope("error", f"HTTP {code}", data, error_type="http_error")
    return envelope("success", f"card #{card_id}", data, ["update_card_priority", "add_comment"])


def assign_card_responsible(card_id: int, user_id: int, commit: bool) -> dict:
    """Add user to card and set role responsible (type=2)."""
    if not commit:
        return envelope(
            "approval_required",
            f"would assign user {user_id} responsible on #{card_id}",
            {"card_id": card_id, "user_id": user_id},
        )
    code, resp = http("POST", f"/cards/{card_id}/members", {"user_id": user_id})
    if code >= 400:
        return envelope("error", f"add member HTTP {code}", resp, error_type="http_error")
    member_id = (resp or {}).get("id") or user_id
    code2, resp2 = http("PATCH", f"/cards/{card_id}/members/{member_id}", {"type": 2})
    if code2 >= 400:
        return envelope(
            "error",
            f"set responsible HTTP {code2}",
            resp2,
            error_type="http_error",
        )
    name = (resp or {}).get("full_name") if isinstance(resp, dict) else str(user_id)
    return envelope(
        "success",
        f"responsible: {name}",
        {"card_id": card_id, "user_id": user_id, "member": resp, "role": resp2},
    )


def draft_card(
    title: str,
    priority: str = "P2",
    due: str | None = None,
    description: str = "",
    board_id: int | None = None,
    column_id: int | None = None,
    tags: list[str] | None = None,
    responsible_user_id: int | None = None,
) -> dict:
    if priority not in {"P1", "P2", "P3"}:
        return envelope("error", "priority must be P1|P2|P3", error_type="invalid_arguments")
    if not title or len(title) > 240:
        return envelope("error", "title 1..240 chars", error_type="invalid_arguments")
    board_id = int(board_id or ENV.get("KAITEN_DEFAULT_BOARD_ID") or 0)
    column_id = int(column_id or ENV.get("KAITEN_DEFAULT_COLUMN_ID") or 0)
    lane_id_env = ENV.get("KAITEN_DEFAULT_LANE_ID")
    lane_id = int(lane_id_env) if lane_id_env else None
    if not board_id or not column_id:
        return envelope("error", "board_id/column_id missing", error_type="invalid_arguments")
    tag_list = list(tags or [])
    if priority in PRIORITY_TAGS and PRIORITY_TAGS[priority] not in tag_list:
        tag_list.append(PRIORITY_TAGS[priority])
    draft_id = f"dr_{secrets.token_hex(4)}"
    draft = Draft(
        draft_id=draft_id,
        title=title.strip(),
        description=description.strip(),
        board_id=board_id,
        column_id=column_id,
        lane_id=lane_id,
        priority=priority,
        due_date=parse_iso_date(due).isoformat() if due else None,
        tags=tag_list,
        created_at=datetime.now(MSK).isoformat(timespec="seconds"),
        responsible_user_id=int(responsible_user_id) if responsible_user_id else None,
    )
    drafts = load_drafts()
    drafts[draft_id] = asdict(draft)
    save_drafts(drafts)
    approval_token = f"ak_{secrets.token_hex(3)}"
    return envelope(
        "success",
        f"draft {draft_id} saved",
        {**asdict(draft), "approval_token": approval_token},
        ["create_card --draft-id " + draft_id + " --commit"],
    )


def create_card(draft_id: str, commit: bool) -> dict:
    drafts = load_drafts()
    if draft_id not in drafts:
        return envelope("error", f"unknown draft {draft_id}", error_type="not_found")
    d = drafts[draft_id]
    body = {
        "title": d["title"],
        "description": d["description"] or None,
        "board_id": d["board_id"],
        "column_id": d["column_id"],
        "asap": d["priority"] == "P1",
    }
    if d.get("lane_id"):
        body["lane_id"] = d["lane_id"]
    if d.get("due_date"):
        body["due_date"] = d["due_date"]
    body = {k: v for k, v in body.items() if v is not None}
    if not commit:
        return envelope(
            "approval_required",
            "preview only; rerun with --commit",
            {"would_post": body, "draft_id": draft_id},
            ["create_card --draft-id " + draft_id + " --commit"],
        )
    code, resp = http("POST", "/cards", body)
    if code >= 400:
        return envelope("error", f"HTTP {code}", resp, error_type="http_error")
    drafts.pop(draft_id, None)
    save_drafts(drafts)
    card_id = (resp or {}).get("id")
    data: dict[str, Any] = {
        "card_id": card_id,
        "url": f"{ENV.get('KAITEN_BASE_URL','').rstrip('/')}/{card_id}",
    }
    rid = d.get("responsible_user_id")
    if rid and card_id:
        assigned = assign_card_responsible(int(card_id), int(rid), True)
        data["assign"] = assigned
        if assigned.get("status") != "success":
            data["assign_warning"] = assigned.get("summary")
    return envelope(
        "success",
        f"created card #{card_id}",
        data,
        ["get_card", "add_comment"],
    )


def update_card_priority(card_id: int, priority: str, commit: bool) -> dict:
    if priority not in {"P1", "P2", "P3"}:
        return envelope("error", "priority must be P1|P2|P3", error_type="invalid_arguments")
    body = {"asap": priority == "P1"}
    if not commit:
        return envelope(
            "approval_required",
            f"would set priority={priority} on #{card_id}",
            {"card_id": card_id, "patch": body, "priority": priority},
            [f"update-priority --id {card_id} --priority {priority} --commit"],
        )
    code, resp = http("PATCH", f"/cards/{card_id}", body)
    if code >= 400:
        return envelope("error", f"HTTP {code}", resp, error_type="http_error")
    return envelope("success", f"priority={priority} on #{card_id}", resp)


def update_card_due(card_id: int, due: str | None, commit: bool) -> dict:
    iso = parse_iso_date(due).isoformat() if due else None
    body = {"due_date": iso}
    if not commit:
        return envelope(
            "approval_required",
            f"would set due={iso} on #{card_id}",
            {"card_id": card_id, "patch": body},
            [f"update-due --id {card_id} --due {due or 'null'} --commit"],
        )
    code, resp = http("PATCH", f"/cards/{card_id}", body)
    if code >= 400:
        return envelope("error", f"HTTP {code}", resp, error_type="http_error")
    return envelope("success", f"due updated on #{card_id}", resp)


def update_card_description(card_id: int, description: str, commit: bool = True) -> dict:
    body = {"description": description[:8000]}
    if not commit:
        return envelope(
            "approval_required",
            f"would update description on #{card_id}",
            {"card_id": card_id, "patch": body},
        )
    code, resp = http("PATCH", f"/cards/{card_id}", body)
    if code >= 400:
        return envelope("error", f"HTTP {code}", resp, error_type="http_error")
    return envelope("success", f"description updated on #{card_id}", resp)


def move_card(card_id: int, column_id: int, commit: bool) -> dict:
    body = {"column_id": column_id}
    if not commit:
        return envelope(
            "approval_required",
            f"would move #{card_id} to column {column_id}",
            {"card_id": card_id, "patch": body},
            [f"move-card --id {card_id} --column-id {column_id} --commit"],
        )
    code, resp = http("PATCH", f"/cards/{card_id}", body)
    if code >= 400:
        return envelope("error", f"HTTP {code}", resp, error_type="http_error")
    return envelope("success", f"moved #{card_id}", resp)


def add_comment(card_id: int, text: str) -> dict:
    if not text or len(text) > 10000:
        return envelope("error", "text 1..10000 chars", error_type="invalid_arguments")
    if ENV.get("KAITEN_AGENT_NO_AUTOCOMMENT", "").lower() == "true":
        return envelope("denied", "autocomments disabled by env", error_type="permission_denied")
    code, resp = http("POST", f"/cards/{card_id}/comments", {"text": text})
    if code >= 400:
        return envelope("error", f"HTTP {code}", resp, error_type="http_error")
    return envelope("success", f"comment added on #{card_id}", resp)


def add_time_log(
    card_id: int,
    minutes: int,
    *,
    comment: str | None = None,
    role_id: int | None = None,
) -> dict:
    """POST /cards/{id}/time-logs — requires KAITEN_TIMELOG_ROLE_ID in .env."""
    if minutes < 1 or minutes > 24 * 60:
        return envelope(
            "error",
            "minutes must be 1..1440",
            error_type="invalid_arguments",
        )
    rid = role_id
    if rid is None:
        raw = (ENV.get("KAITEN_TIMELOG_ROLE_ID") or "").strip()
        if not raw.isdigit():
            return envelope(
                "error",
                "KAITEN_TIMELOG_ROLE_ID not set — comment only",
                error_type="config_error",
            )
        rid = int(raw)
    body: dict[str, Any] = {"role_id": rid, "time_spent": minutes}
    if comment:
        body["comment"] = comment[:500]
    code, resp = http("POST", f"/cards/{card_id}/time-logs", body)
    if code >= 400:
        return envelope("error", f"HTTP {code}", resp, error_type="http_error")
    return envelope("success", f"time log {minutes}m on #{card_id}", resp)


def make_delete_token(card_id: int) -> str:
    return "del_" + hmac.new(HMAC_SECRET, str(card_id).encode(), hashlib.sha256).hexdigest()[:10]


def confirm_delete(card_id: int) -> dict:
    tok = make_delete_token(card_id)
    return envelope(
        "approval_required",
        f"send: delete-card --id {card_id} --token {tok} --commit",
        {"card_id": card_id, "token": tok},
        [f"delete-card --id {card_id} --token {tok} --commit"],
    )


def delete_card(card_id: int, token: str, commit: bool) -> dict:
    expected = make_delete_token(card_id)
    if not hmac.compare_digest(token or "", expected):
        return envelope("denied", "invalid confirm token", error_type="permission_denied")
    if not commit:
        return envelope("approval_required", "rerun with --commit", {"card_id": card_id})
    code, resp = http("DELETE", f"/cards/{card_id}")
    if code >= 400:
        return envelope("error", f"HTTP {code}", resp, error_type="http_error")
    return envelope("success", f"deleted #{card_id}", resp)


def attach_file(card_id: int, file_path: str) -> dict:
    """PUT /api/v1/cards/{card_id}/files — multipart upload."""
    path = Path(file_path).expanduser().resolve()
    if not path.exists():
        return envelope("error", f"file not found: {path}", error_type="not_found")
    if path.stat().st_size > 50 * 1024 * 1024:
        return envelope("error", "file >50MB", error_type="too_large")
    base = ENV.get("KAITEN_BASE_URL", "").rstrip("/")
    token = ENV.get("KAITEN_API_TOKEN", "")
    if not base or not token:
        return envelope("error", "env not set", error_type="config")
    boundary = secrets.token_hex(16)
    mime = "application/octet-stream"
    if path.suffix == ".md":
        mime = "text/markdown"
    elif path.suffix in {".txt"}:
        mime = "text/plain"
    elif path.suffix == ".json":
        mime = "application/json"
    elif path.suffix == ".docx":
        mime = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    elif path.suffix == ".pdf":
        mime = "application/pdf"
    body = bytearray()
    body += f"--{boundary}\r\n".encode()
    body += f'Content-Disposition: form-data; name="file"; filename="{path.name}"\r\n'.encode()
    body += f"Content-Type: {mime}\r\n\r\n".encode()
    body += path.read_bytes()
    body += f"\r\n--{boundary}--\r\n".encode()
    req = urllib.request.Request(
        f"{base}/api/v1/cards/{card_id}/files", data=bytes(body), method="PUT"
    )
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read())
        return envelope(
            "success",
            f"attached {path.name} to #{card_id}",
            {"file_id": data.get("id"), "name": data.get("name"), "url": data.get("url"), "size": data.get("size")},
            ["move_to_done", "add_comment"],
        )
    except urllib.error.HTTPError as e:
        return envelope("error", f"HTTP {e.code}", {"error": e.read().decode("utf-8", "replace")}, error_type="http_error")


def move_to_done(card_id: int, commit: bool = True) -> dict:
    done = ENV.get("KAITEN_COL_DONE")
    if not done:
        return envelope("error", "KAITEN_COL_DONE not set", error_type="config")
    return move_card(card_id, int(done), commit=commit)


def move_to_wip(card_id: int) -> dict:
    cols = board_column_config()
    wip = (cols.get("wip") or {}).get("column_id")
    if not wip:
        return move_card(card_id, int(ENV.get("KAITEN_COL_QUEUE", 0) or 0), commit=True)
    return move_card(card_id, int(wip), commit=True)


def save_research_artifact(topic: str, markdown_path: str, sources_json: str | None = None) -> dict:
    src = Path(markdown_path).expanduser().resolve()
    if not src.exists():
        return envelope("error", "markdown file not found", error_type="not_found")
    if src.stat().st_size > 5 * 1024 * 1024:
        return envelope("error", "artifact >5MB", error_type="too_large")
    date = datetime.now(MSK).strftime("%Y-%m-%d")
    dest_dir = ARTIFACTS_DIR / f"{date}-{slugify(topic)}"
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / "report.md"
    dest.write_bytes(src.read_bytes())
    if sources_json:
        try:
            (dest_dir / "sources.json").write_text(sources_json)
        except Exception:
            pass
    meta = {
        "topic": topic,
        "created_at": datetime.now(MSK).isoformat(timespec="seconds"),
        "report": str(dest),
        "size_bytes": dest.stat().st_size,
    }
    (dest_dir / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2))
    return envelope(
        "success",
        f"artifact saved: {dest_dir}",
        meta,
        ["create_card with link to artifact"],
    )


def cli() -> int:
    p = argparse.ArgumentParser(description="Kaiten typed tools")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("list-boards")
    c = sub.add_parser("list-columns")
    c.add_argument("--board-id", type=int, required=True)

    c = sub.add_parser("list-cards")
    c.add_argument("--board-id", type=int)
    c.add_argument("--due-before", type=str)
    c.add_argument("--q", type=str)
    c.add_argument("--limit", type=int, default=30)

    sub.add_parser("list-overdue")
    sub.add_parser("list-today")

    c = sub.add_parser("get-card")
    c.add_argument("--id", type=int, required=True)

    c = sub.add_parser("draft-card")
    c.add_argument("--title", required=True)
    c.add_argument("--priority", default="P2", choices=["P1", "P2", "P3"])
    c.add_argument("--due", type=str)
    c.add_argument("--desc", type=str, default="")
    c.add_argument("--board-id", type=int)
    c.add_argument("--column-id", type=int)
    c.add_argument("--tags", type=str, help="comma-separated")

    c = sub.add_parser("create-card")
    c.add_argument("--draft-id", required=True)
    c.add_argument("--commit", action="store_true")

    c = sub.add_parser("update-priority")
    c.add_argument("--id", type=int, required=True)
    c.add_argument("--priority", required=True, choices=["P1", "P2", "P3"])
    c.add_argument("--commit", action="store_true")

    c = sub.add_parser("update-due")
    c.add_argument("--id", type=int, required=True)
    c.add_argument("--due", type=str, required=True)
    c.add_argument("--commit", action="store_true")

    c = sub.add_parser("move-card")
    c.add_argument("--id", type=int, required=True)
    c.add_argument("--column-id", type=int, required=True)
    c.add_argument("--commit", action="store_true")

    c = sub.add_parser("add-comment")
    c.add_argument("--id", type=int, required=True)
    c.add_argument("--text", type=str, required=True)

    c = sub.add_parser("confirm-delete")
    c.add_argument("--id", type=int, required=True)

    c = sub.add_parser("delete-card")
    c.add_argument("--id", type=int, required=True)
    c.add_argument("--token", type=str, required=True)
    c.add_argument("--commit", action="store_true")

    c = sub.add_parser("save-artifact")
    c.add_argument("--topic", required=True)
    c.add_argument("--markdown", required=True, help="path to markdown")
    c.add_argument("--sources", help="JSON string with sources array")

    args = p.parse_args()

    dispatch = {
        "list-boards": lambda: list_boards(),
        "list-columns": lambda: list_columns(args.board_id),
        "list-cards": lambda: list_cards(args.board_id, args.due_before, args.q, limit=args.limit),
        "list-overdue": lambda: list_overdue(),
        "list-today": lambda: list_today(),
        "get-card": lambda: get_card(args.id),
        "draft-card": lambda: draft_card(
            args.title, args.priority, args.due, args.desc, args.board_id, args.column_id,
            [t.strip() for t in (args.tags or "").split(",") if t.strip()],
        ),
        "create-card": lambda: create_card(args.draft_id, args.commit),
        "update-priority": lambda: update_card_priority(args.id, args.priority, args.commit),
        "update-due": lambda: update_card_due(args.id, args.due, args.commit),
        "move-card": lambda: move_card(args.id, args.column_id, args.commit),
        "add-comment": lambda: add_comment(args.id, args.text),
        "confirm-delete": lambda: confirm_delete(args.id),
        "delete-card": lambda: delete_card(args.id, args.token, args.commit),
        "save-artifact": lambda: save_research_artifact(args.topic, args.markdown, args.sources),
    }

    result, dur = with_timing(dispatch[args.cmd])
    log_call(args.cmd, vars(args), result.get("status", "?"), dur)
    json.dump(result, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")
    return 0 if result.get("status") in {"success", "approval_required"} else 2


if __name__ == "__main__":
    sys.exit(cli())
