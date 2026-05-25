"""Parse TG work-update messages: comment + optional time on a Kaiten card."""

from __future__ import annotations

import re
from dataclasses import dataclass

from card_actions import parse_card_id

_WORK_VERBS = re.compile(
    r"(?:"
    r"сделал|сделано|выполнил|готово|отчит|написал|потратил|затрек|"
    r"трудозатрат|работал|проработал|завершил|закрыл|добавь\s+коммент|"
    r"комментар|отметь\s+работ|залогируй|списал\s+время"
    r")",
    re.I | re.U,
)

_TIME = re.compile(
    r"(\d+(?:[.,]\d+)?)\s*(час(?:а|ов)?|ч\b|h\b|мин(?:ут(?:ы)?)?|m\b)",
    re.I,
)


@dataclass
class WorkLogRequest:
    card_id: int
    summary: str
    minutes: int | None = None


def parse_duration_minutes(text: str) -> int | None:
    m = _TIME.search(text or "")
    if not m:
        return None
    val = float(m.group(1).replace(",", "."))
    unit = m.group(2).lower()
    if unit.startswith("ч") or unit in {"h", "ч"}:
        return max(1, int(round(val * 60)))
    return max(1, int(round(val)))


def parse_log_command(text: str) -> WorkLogRequest | None:
    """Parse /log #64942144 2ч провели мит or /log 64942144 текст."""
    raw = (text or "").strip()
    if not raw:
        return None
    if raw.lower().startswith("/log"):
        raw = raw[4:].strip()
    if not raw:
        return None
    cid = parse_card_id(raw)
    if not cid:
        return None
    summary = raw
    summary = re.sub(r"#\d{5,10}\b", "", summary)
    summary = re.sub(r"\b\d{7,10}\b", "", summary, count=1)
    summary = _TIME.sub("", summary)
    summary = re.sub(r"\s+", " ", summary).strip(" ,.—")
    if len(summary) < 3:
        summary = "Работа по задаче (TG /log)"
    minutes = parse_duration_minutes(raw)
    return WorkLogRequest(card_id=cid, summary=summary[:500], minutes=minutes)


def parse_work_log(text: str) -> WorkLogRequest | None:
    """#12345 сделал X, 2ч — comment + optional time log."""
    raw = (text or "").strip()
    if not raw:
        return None
    cid = parse_card_id(raw)
    if not cid:
        return None
    if not _WORK_VERBS.search(raw):
        return None
    summary = raw
    summary = re.sub(r"#\d{5,10}\b", "", summary)
    summary = re.sub(r"\b\d{7,10}\b", "", summary, count=1)
    summary = _TIME.sub("", summary)
    summary = re.sub(r"\s+", " ", summary).strip(" ,.—")
    if len(summary) < 3:
        summary = "Работа по задаче (TG)"
    minutes = parse_duration_minutes(raw)
    return WorkLogRequest(card_id=cid, summary=summary[:500], minutes=minutes)


def format_work_comment(author: str, summary: str, minutes: int | None) -> str:
    lines = [f"**{author}** (Telegram)", "", summary]
    if minutes:
        h, m = divmod(minutes, 60)
        if h and m:
            lines.append(f"\n⏱ {h} ч {m} мин")
        elif h:
            lines.append(f"\n⏱ {h} ч")
        else:
            lines.append(f"\n⏱ {m} мин")
    return "\n".join(lines)


def apply_work_log(
    card_id: int,
    summary: str,
    author: str,
    *,
    minutes: int | None = None,
) -> dict:
    """Add TG comment; optional Kaiten time log when minutes and role_id set."""
    from kaiten_api import add_comment, add_time_log

    text = format_work_comment(author, summary, minutes)
    comment_res = add_comment(card_id, text)
    out: dict = {"comment": comment_res, "card_id": card_id}
    comment_ok = comment_res.get("status") == "success"
    out["comment_ok"] = comment_ok
    if not comment_ok:
        out["ok"] = False
        return out
    out["ok"] = True
    if minutes:
        tl = add_time_log(card_id, minutes, comment=summary[:500])
        out["time_log"] = tl
        out["time_log_ok"] = tl.get("status") == "success"
        if tl.get("status") != "success":
            out["time_log_skipped_reason"] = tl.get("summary", "")
    return out
