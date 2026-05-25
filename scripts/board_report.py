"""Сводный отчёт по доске Kaiten (метрики за период), не файл ресёрча."""

from __future__ import annotations

import html
import re
from calendar import monthrange
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from kaiten_api import MSK, board_column_config, fetch_board_cards

_MONTHS = {
    "январ": 1,
    "феврал": 2,
    "март": 3,
    "апрел": 4,
    "ма": 5,
    "июн": 6,
    "июл": 7,
    "август": 8,
    "сентябр": 9,
    "октябр": 10,
    "ноябр": 11,
    "декабр": 12,
}


@dataclass
class Period:
    start: datetime
    end: datetime
    label: str


@dataclass
class BoardReportStats:
    period: Period
    total_on_board: int
    new_in_period: list[dict]
    completed_in_period: list[dict]
    blocked_now: list[dict]
    overdue_now: list[dict]
    snapshot: dict[str, int]
    truncated: bool = False


def _esc(s: str) -> str:
    return html.escape(s or "", quote=False)


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(MSK)
    except (TypeError, ValueError):
        return None


def _in_period(dt: datetime | None, start: datetime, end: datetime) -> bool:
    return dt is not None and start <= dt < end


def parse_board_report_period(text: str) -> Period:
    """Resolve reporting window from user text; default = current calendar month (MSK)."""
    t = (text or "").strip().lower()
    now = datetime.now(MSK)

    if re.search(r"прошл(?:ый|ого)\s+месяц", t):
        y, m = now.year, now.month - 1
        if m < 1:
            m, y = 12, y - 1
        return _month_period(y, m, label_prefix="за прошлый месяц")

    if re.search(r"(?:за\s+)?(?:эту|текущ(?:ую|ий))\s+недел", t):
        start = (now - timedelta(days=now.weekday())).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        end = start + timedelta(days=7)
        return Period(start, end, f"за неделю {start:%d.%m}–{(end - timedelta(days=1)):%d.%m}")

    if re.search(r"за\s+недел", t):
        end = now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
        start = end - timedelta(days=7)
        return Period(start, end, "за 7 дней")

    for stem, num in _MONTHS.items():
        if re.search(rf"\b{stem}", t):
            y = now.year
            ym = re.search(r"(20\d{2})", t)
            if ym:
                y = int(ym.group(1))
            return _month_period(y, num, label_prefix=f"за {stem}")

    if re.search(r"за\s+месяц|за\s+мес\b|итоги?\s+месяц", t):
        return _month_period(now.year, now.month)

    return _month_period(now.year, now.month)


def _month_period(year: int, month: int, label_prefix: str = "") -> Period:
    last = monthrange(year, month)[1]
    start = datetime(year, month, 1, tzinfo=MSK)
    end = datetime(year, month, last, 23, 59, 59, tzinfo=MSK) + timedelta(seconds=1)
    names = (
        "январь",
        "февраль",
        "март",
        "апрель",
        "май",
        "июнь",
        "июль",
        "август",
        "сентябрь",
        "октябрь",
        "ноябрь",
        "декабрь",
    )
    label = f"{label_prefix or 'за'} {names[month - 1]} {year}".strip()
    return Period(start, end, label)


def fetch_all_board_cards() -> tuple[list[dict], bool]:
    res = fetch_board_cards()
    if res.get("status") != "success":
        return [], False
    data = res.get("data") or {}
    return list(data.get("cards") or []), bool(data.get("truncated"))


def _column_key(column_id: int | None, cols: dict[str, dict]) -> str:
    if column_id is None:
        return "other"
    for key, meta in cols.items():
        ids = meta.get("column_ids") or [meta.get("column_id")]
        if column_id in ids:
            return key
    return "other"


def build_board_report(period: Period | None = None) -> BoardReportStats:
    period = period or parse_board_report_period("")
    cols = board_column_config()
    done_id = (cols.get("done") or {}).get("column_id")
    cards, truncated = fetch_all_board_cards()
    now = datetime.now(MSK)

    new_in: list[dict] = []
    done_in: list[dict] = []
    blocked: list[dict] = []
    overdue: list[dict] = []
    snap = {k: 0 for k in ("queue", "wip", "done", "other")}

    for c in cards:
        if c.get("archived"):
            continue
        key = _column_key(c.get("column_id"), cols)
        snap[key] = snap.get(key, 0) + 1

        created = _parse_dt(c.get("created"))
        updated = _parse_dt(c.get("updated"))
        completed = _parse_dt(c.get("completed_at"))
        col_changed = _parse_dt(c.get("column_changed_at"))

        if _in_period(created, period.start, period.end):
            new_in.append(c)

        finished = False
        if completed and _in_period(completed, period.start, period.end):
            finished = True
        elif done_id and _column_key(c.get("column_id"), cols) == "done":
            moved = col_changed or updated
            if _in_period(moved, period.start, period.end):
                finished = True
        if finished:
            done_in.append(c)

        if c.get("blocked"):
            blocked.append(c)

        due = _parse_dt(c.get("due_date"))
        if due and due < now and key in ("queue", "wip"):
            overdue.append(c)

    return BoardReportStats(
        period=period,
        total_on_board=len([c for c in cards if not c.get("archived")]),
        new_in_period=new_in,
        completed_in_period=done_in,
        blocked_now=blocked,
        overdue_now=overdue,
        snapshot=snap,
        truncated=truncated,
    )


def format_board_report_html(stats: BoardReportStats) -> str:
    p = stats.period
    snap = stats.snapshot
    lines = [
        f"📊 <b>Отчёт по задачам</b> ({_esc(p.label)})",
        "",
        f"<b>За период</b>",
        f"• Новых: <b>{len(stats.new_in_period)}</b>",
        f"• Завершено: <b>{len(stats.completed_in_period)}</b>",
        "",
        f"<b>Сейчас на доске</b> (всего {stats.total_on_board})",
        f"• Очередь: <b>{snap.get('queue', 0)}</b>",
        f"• В работе: <b>{snap.get('wip', 0)}</b>",
        f"• Готово: <b>{snap.get('done', 0)}</b>",
        f"• На стопе (blocked): <b>{len(stats.blocked_now)}</b>",
        f"• Просрочено (очередь/WIP): <b>{len(stats.overdue_now)}</b>",
    ]
    if stats.truncated:
        lines.append(
            "\n<i>⚠ Учтены не все карточки доски (лимит выборки). "
            "Увеличь max_cards в fetch_board_cards при необходимости.</i>"
        )

    def _lines_block(title: str, cards: list[dict], n: int = 5) -> None:
        if not cards:
            return
        lines.append(f"\n<b>{_esc(title)}</b>")
        for c in cards[:n]:
            cid = c.get("id", "?")
            title_c = _esc((c.get("title") or "")[:55])
            lines.append(f"• #{cid} {title_c}")
        if len(cards) > n:
            lines.append(f"<i>… ещё {len(cards) - n}</i>")

    _lines_block("Новые за период", stats.new_in_period)
    _lines_block("Завершённые за период", stats.completed_in_period)
    _lines_block("На стопе сейчас", stats.blocked_now, 8)
    return "\n".join(lines)
