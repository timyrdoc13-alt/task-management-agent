"""Сводный отчёт по доске Kaiten (метрики за период), не файл ресёрча."""

from __future__ import annotations

import html
import re
from calendar import monthrange
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from kaiten_api import (
    ENV,
    MSK,
    board_column_config,
    board_columns_for_report,
    column_labels_for_report,
    fetch_board_cards,
)

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
    stale_wip: list[dict]
    snapshot: dict[str, int]
    snapshot_by_column: dict[int, int]
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


def previous_calendar_week_period(now: datetime | None = None) -> Period:
    """Прошлая календарная неделя (пн 00:00 — вс 23:59 MSK) для еженедельного отчёта."""
    now = now or datetime.now(MSK)
    this_monday = (now - timedelta(days=now.weekday())).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    start = this_monday - timedelta(days=7)
    end = this_monday
    last_day = end - timedelta(seconds=1)
    label = f"за неделю {start:%d.%m}–{last_day:%d.%m.%Y}"
    return Period(start, end, label)


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


def _bucket_titles(cols: dict[str, dict]) -> dict[str, str]:
    return {
        "queue": (cols.get("queue") or {}).get("title") or "Очередь",
        "wip": (cols.get("wip") or {}).get("title") or "В работе",
        "done": (cols.get("done") or {}).get("title") or "Готово",
    }


def _card_column_label(card: dict, labels: dict[int, str] | None = None) -> str:
    cid = card.get("column_id")
    if cid is None:
        return "без колонки"
    labels = labels or column_labels_for_report()
    return labels.get(int(cid), f"колонка #{cid}")


def _card_url(card: dict) -> str:
    cid = card.get("id", "")
    base = ENV.get("KAITEN_BASE_URL", "").rstrip("/")
    return (card.get("url") or f"{base}/{cid}").strip()


def _format_card_line(card: dict) -> str:
    cid = card.get("id", "?")
    title = _esc((card.get("title") or "")[:55])
    url = html.escape(_card_url(card), quote=True)
    return f'• <a href="{url}">#{cid}</a> {title}'


def format_column_snapshot_lines(
    snapshot_by_column: dict[int, int],
    *,
    include_zero: bool = False,
) -> list[str]:
    """Строки отчёта: подписи колонок как в Kaiten (листья, с путём родитель · дочерняя)."""
    order = board_columns_for_report()
    known = {c["id"] for c in order}
    lines: list[str] = []
    for col in order:
        n = snapshot_by_column.get(col["id"], 0)
        if not include_zero and n == 0:
            continue
        lines.append(f"• {_esc(col['label'])}: <b>{n}</b>")
    other = sum(
        n for cid, n in snapshot_by_column.items() if cid not in known and n > 0
    )
    if other:
        lines.append(f"• Другие колонки: <b>{other}</b>")
    if not lines:
        lines.append("• <i>нет карточек на доске</i>")
    return lines


def _overdue_column_phrase(stats: BoardReportStats) -> str:
    labels = column_labels_for_report()
    by_col: dict[str, int] = {}
    for card in stats.overdue_now:
        name = _card_column_label(card, labels)
        by_col[name] = by_col.get(name, 0) + 1
    if not by_col:
        return ""
    parts = [f"«{_esc(k)}» — {v}" for k, v in sorted(by_col.items(), key=lambda x: -x[1])]
    return ", ".join(parts[:4])


def _sanitize_report_text(text: str, names: dict[str, str]) -> str:
    """Убрать служебный английский из ответа LLM и сделать #id ссылками."""
    if not text:
        return text
    repl = [
        (r"\bstale_wip\b", "зависшие задачи"),
        (r"\boverdue\b", "просроченные"),
        (r"\bblocked\b", "на стопе"),
        (r"\bWIP\b", names.get("wip", "в работе")),
        (r"\bnew\b", "новые"),
        (r"\bcompleted\b", "завершённые"),
    ]
    out = text
    for pat, sub in repl:
        out = re.sub(pat, sub, out, flags=re.IGNORECASE)
    base = ENV.get("KAITEN_BASE_URL", "").rstrip("/")
    if base:

        def link_card(m: re.Match[str]) -> str:
            cid = m.group(1)
            return f'<a href="{_esc(base)}/{cid}">#{cid}</a>'

        out = re.sub(r"(?<![\"'>])#(\d{5,})\b", link_card, out)
    return out


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
    stale_wip: list[dict] = []
    snap = {k: 0 for k in ("queue", "wip", "done", "other")}
    snap_cols: dict[int, int] = {}
    stale_days = 5

    for c in cards:
        if c.get("archived"):
            continue
        col_id = c.get("column_id")
        if col_id is not None:
            snap_cols[int(col_id)] = snap_cols.get(int(col_id), 0) + 1
        key = _column_key(col_id, cols)
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

        if key == "wip":
            moved = col_changed or updated or created
            if moved and (now - moved).days >= stale_days:
                stale_wip.append(
                    {
                        **c,
                        "_days_in_column": (now - moved).days,
                        "_column_label": _card_column_label(c),
                    }
                )

    stale_wip.sort(key=lambda x: x.get("_days_in_column", 0), reverse=True)

    return BoardReportStats(
        period=period,
        total_on_board=len([c for c in cards if not c.get("archived")]),
        new_in_period=new_in,
        completed_in_period=done_in,
        blocked_now=blocked,
        overdue_now=overdue,
        stale_wip=stale_wip,
        snapshot=snap,
        snapshot_by_column=snap_cols,
        truncated=truncated,
    )


def _completion_pct(stats: BoardReportStats) -> str:
    new_n = len(stats.new_in_period)
    done_n = len(stats.completed_in_period)
    if new_n == 0:
        return "— (не было новых за период)"
    pct = round(100.0 * done_n / new_n, 1)
    return f"<b>{pct}%</b> ({done_n} из {new_n} новых)"


def _wip_load_phrase(stats: BoardReportStats) -> str | None:
    cols_cfg = board_column_config()
    wip_ids = set((cols_cfg.get("wip") or {}).get("column_ids") or [])
    parts: list[str] = []
    for col in board_columns_for_report():
        if col["id"] not in wip_ids:
            continue
        n = stats.snapshot_by_column.get(col["id"], 0)
        if n:
            parts.append(f"«{_esc(col['label'])}» — {n}")
    if not parts:
        return None
    return ", ".join(parts)


def _bottleneck_lines(stats: BoardReportStats) -> list[str]:
    snap = stats.snapshot
    cols = board_column_config()
    names = _bucket_titles(cols)
    queue_n = snap.get("queue", 0)
    wip_n = snap.get("wip", 0)
    lines: list[str] = []
    if wip_n >= max(queue_n * 2, 8) and wip_n > 3:
        load = _wip_load_phrase(stats)
        lines.append(
            f"• Перегруз в работе: <b>{wip_n}</b> карточек "
            f"(в «{_esc(names['queue'])}» — {queue_n})"
        )
        if load:
            lines.append(f"  <i>{load}</i>")
    if stats.blocked_now:
        lines.append(f"• <b>На стопе</b>: {len(stats.blocked_now)} карточек")
    if stats.overdue_now:
        detail = _overdue_column_phrase(stats)
        line = f"• <b>Просрочено</b>: {len(stats.overdue_now)}"
        if detail:
            line += f" ({detail})"
        lines.append(line)
    if stats.stale_wip:
        col = _esc(stats.stale_wip[0].get("_column_label") or names["wip"])
        lines.append(
            f"• <b>Зависли</b> ≥5 дн.: {len(stats.stale_wip)} "
            f"(дольше всех в «{col}»: {stats.stale_wip[0].get('_days_in_column', '?')} дн.)"
        )
    if done_n := len(stats.completed_in_period):
        if new_n := len(stats.new_in_period):
            if done_n < new_n * 0.5 and new_n >= 3:
                lines.append(
                    f"• <b>Отставание</b>: закрыто меньше половины новых ({done_n} vs {new_n})"
                )
    if not lines:
        lines.append("• Явных узких мест нет — поток ровный")
    return lines


def build_weekly_recommendations(stats: BoardReportStats) -> str:
    """Тезисные рекомендации (DeepSeek + rule fallback)."""
    snap = stats.snapshot
    cols = board_column_config()
    names = _bucket_titles(cols)
    column_breakdown = [
        {"колонка": c["label"], "карточек": stats.snapshot_by_column.get(c["id"], 0)}
        for c in board_columns_for_report()
        if stats.snapshot_by_column.get(c["id"], 0) > 0
    ]
    summary = {
        "период": stats.period.label,
        "новых_за_период": len(stats.new_in_period),
        "завершено": len(stats.completed_in_period),
        "колонки": column_breakdown,
        "на_стопе": len(stats.blocked_now),
        "просрочено": len(stats.overdue_now),
        "зависшие_5д_и_более": [
            {
                "id": c.get("id"),
                "название": (c.get("title") or "")[:80],
                "колонка": c.get("_column_label"),
                "дней": c.get("_days_in_column"),
            }
            for c in stats.stale_wip[:6]
        ],
        "просроченные_примеры": [
            {"id": c.get("id"), "название": (c.get("title") or "")[:60]}
            for c in stats.overdue_now[:5]
        ],
    }
    try:
        from llm import _call_deepseek  # noqa: WPS433

        system = (
            "Ты операционный коуч по Kanban. Дай 4–6 коротких рекомендаций по-русски для Telegram HTML. "
            "Формат: буллеты •, можно <b>выделение</b>. Только действия. "
            "Используй точные названия колонок из поля «колонки» в метриках. "
            "Задачи указывай как #12345 (номер карточки). "
            "Запрещены английские термины: WIP, stale_wip, overdue, blocked, new, completed."
        )
        user = f"Метрики доски:\n{summary}"
        resp = _call_deepseek(
            [{"role": "system", "content": system}, {"role": "user", "content": user}],
            model="deepseek-chat",
            temperature=0.3,
            max_tokens=900,
            json_mode=False,
            timeout=75,
        )
        msg = resp.get("choices", [{}])[0].get("message", {}) or {}
        text = (msg.get("content") or msg.get("reasoning_content") or "").strip()
        if text and len(text) > 30:
            return _sanitize_report_text(text, names)
    except Exception:
        pass

    tips: list[str] = []
    if stats.stale_wip:
        tips.append(f"• Разобрать зависшие: {_format_card_line(stats.stale_wip[0])}")
    if stats.blocked_now:
        tips.append(
            f"• Снять стоп с {len(stats.blocked_now)} карточек или зафиксировать срок"
        )
    if stats.overdue_now:
        tips.append(f"• Перепланировать {len(stats.overdue_now)} просроченных")
    if snap.get("wip", 0) > snap.get("queue", 0) + 3:
        tips.append(
            f"• Ограничить «{_esc(names['wip'])}»: не брать новое, пока не закрыты 2–3 текущих"
        )
    if len(stats.completed_in_period) < max(len(stats.new_in_period), 1):
        tips.append("• На следующую неделю: цель — закрыть ≥ числа новых задач")
    if not tips:
        tips.append("• Поддерживать ритм: ежедневный разбор очереди 10 мин")
    return "\n".join(tips[:6])


def format_board_report_html(stats: BoardReportStats, *, include_recommendations: bool = False) -> str:
    p = stats.period
    cols = board_column_config()
    names = _bucket_titles(cols)
    lines = [
        f"📊 <b>Отчёт по задачам</b> ({_esc(p.label)})",
        "",
        f"<b>За период</b>",
        f"• Поступило (новых): <b>{len(stats.new_in_period)}</b>",
        f"• Завершили (колонка {_esc(names['done'])}): <b>{len(stats.completed_in_period)}</b>",
        f"• Закрытие потока: {_completion_pct(stats)}",
        "",
        f"<b>Сейчас на доске</b> (всего {stats.total_on_board})",
        *format_column_snapshot_lines(stats.snapshot_by_column),
        f"• На стопе: <b>{len(stats.blocked_now)}</b>",
    ]
    overdue_detail = _overdue_column_phrase(stats)
    if stats.overdue_now:
        line = f"• Просрочено: <b>{len(stats.overdue_now)}</b>"
        if overdue_detail:
            line += f" — {overdue_detail}"
        lines.append(line)
    lines.extend(
        [
            "",
            "<b>Где буксуем</b>",
            *_bottleneck_lines(stats),
        ]
    )
    if include_recommendations:
        lines.extend(["", "<b>Рекомендации</b>", build_weekly_recommendations(stats)])
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
            lines.append(_format_card_line(c))
        if len(cards) > n:
            lines.append(f"<i>… ещё {len(cards) - n}</i>")

    _lines_block("Новые за период", stats.new_in_period)
    _lines_block("Завершённые за период", stats.completed_in_period)
    _lines_block("Зависли без движения ≥5 дн.", stats.stale_wip, 6)
    _lines_block("На стопе сейчас", stats.blocked_now, 8)
    return "\n".join(lines)


def format_weekly_board_telegram() -> str:
    """Еженедельный отчёт для scheduled Telegram (прошлая календарная неделя)."""
    period = previous_calendar_week_period()
    stats = build_board_report(period)
    return format_board_report_html(stats, include_recommendations=True)
