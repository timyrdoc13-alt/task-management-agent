"""Durable job ledger + idempotency for long-running agent workflows."""

from __future__ import annotations

import json
import secrets
import sqlite3
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

from kaiten_api import MSK, STATE_DIR

JOBS_DB = STATE_DIR / "jobs.sqlite"


def _connect() -> sqlite3.Connection:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(JOBS_DB), timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with _connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY,
                idempotency_key TEXT UNIQUE,
                job_type TEXT NOT NULL,
                status TEXT NOT NULL,
                channel TEXT,
                chat_id INTEGER,
                trace_id TEXT,
                payload_json TEXT,
                result_json TEXT,
                error TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status, updated_at)"
        )
        conn.commit()


@contextmanager
def _db():
    init_db()
    conn = _connect()
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def _now() -> str:
    return datetime.now(MSK).isoformat(timespec="seconds")


def create_job(
    job_type: str,
    idempotency_key: str,
    payload: dict[str, Any],
    *,
    channel: str = "telegram",
    chat_id: int | None = None,
    trace_id: str | None = None,
) -> dict[str, Any]:
    existing = get_job_by_key(idempotency_key)
    if existing:
        if existing["status"] in {"pending", "running", "completed"}:
            return existing
        with _db() as conn:
            conn.execute(
                "DELETE FROM jobs WHERE idempotency_key = ?", (idempotency_key,)
            )
    job_id = "job_" + secrets.token_hex(6)
    trace_id = trace_id or secrets.token_hex(8)
    ts = _now()
    with _db() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO jobs
            (id, idempotency_key, job_type, status, channel, chat_id, trace_id,
             payload_json, result_json, error, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, ?, ?)
            """,
            (
                job_id,
                idempotency_key,
                job_type,
                "pending",
                channel,
                chat_id,
                trace_id,
                json.dumps(payload, ensure_ascii=False),
                ts,
                ts,
            ),
        )
    return get_job(job_id) or {}


def update_job(
    job_id: str,
    *,
    status: str | None = None,
    result: dict | None = None,
    error: str | None = None,
) -> None:
    with _db() as conn:
        row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if not row:
            return
        st = status or row["status"]
        result_json = (
            json.dumps(result, ensure_ascii=False) if result is not None else row["result_json"]
        )
        err = error if error is not None else row["error"]
        conn.execute(
            """
            UPDATE jobs SET status = ?, result_json = ?, error = ?, updated_at = ?
            WHERE id = ?
            """,
            (st, result_json, err, _now(), job_id),
        )


def get_job(job_id: str) -> dict[str, Any] | None:
    with _db() as conn:
        row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    return _row_to_dict(row) if row else None


def get_job_by_key(idempotency_key: str) -> dict[str, Any] | None:
    with _db() as conn:
        row = conn.execute(
            "SELECT * FROM jobs WHERE idempotency_key = ?", (idempotency_key,)
        ).fetchone()
    return _row_to_dict(row) if row else None


def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    d = dict(row)
    for key in ("payload_json", "result_json"):
        if d.get(key):
            try:
                d[key.replace("_json", "")] = json.loads(d[key])
            except json.JSONDecodeError:
                d[key.replace("_json", "")] = None
    return d


def research_idempotency_key(chat_id: int, topic: str) -> str:
    import hashlib

    digest = hashlib.sha256(topic.strip().lower().encode()).hexdigest()[:16]
    return f"research:{chat_id}:{digest}"


def get_running_job(
    chat_id: int,
    job_type: str = "research",
) -> dict[str, Any] | None:
    with _db() as conn:
        row = conn.execute(
            """
            SELECT * FROM jobs
            WHERE chat_id = ? AND job_type = ? AND status = 'running'
            ORDER BY updated_at DESC LIMIT 1
            """,
            (chat_id, job_type),
        ).fetchone()
    return _row_to_dict(row) if row else None


def cancel_job(job_id: str) -> dict[str, Any] | None:
    """Mark running job cancelled (cooperative — workflow checks status)."""
    job = get_job(job_id)
    if not job:
        return None
    if job.get("status") != "running":
        return job
    update_job(job_id, status="cancelled", error="cancelled by user")
    return get_job(job_id)


def cancel_job_by_key(idempotency_key: str) -> dict[str, Any] | None:
    job = get_job_by_key(idempotency_key)
    if not job:
        return None
    if job.get("status") != "running":
        return job
    return cancel_job(job["id"])


def is_job_cancelled(job_id: str) -> bool:
    job = get_job(job_id)
    return bool(job and job.get("status") == "cancelled")
