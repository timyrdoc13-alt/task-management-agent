"""Format Kaiten card lists for Telegram (HTML)."""

from __future__ import annotations

import html
import re
from typing import Any

from kaiten_api import ENV, list_active_cards, list_overdue, list_today


def _esc(s: str) -> str:
    return html.escape(s or "", quote=False)


def _format_card_line(card: dict) -> str:
    cid = card.get("id", "?")
    title = _esc((card.get("title") or "без названия")[:70])
    due = card.get("due_date")
    due_s = ""
    if due:
        due_s = f" · due {_esc(str(due)[:16])}"
    asap = " 🔥" if card.get("asap") else ""
    url = card.get("url") or f"{ENV.get('KAITEN_BASE_URL', '').rstrip('/')}/{cid}"
    return f"• <a href=\"{url}\">#{cid}</a> {title}{asap}{due_s}"


def _section(title: str, cards: list[dict], *, max_items: int = 12) -> list[str]:
    if not cards:
        return []
    lines = [f"<b>{_esc(title)}</b> ({len(cards)})"]
    for c in cards[:max_items]:
        lines.append(_format_card_line(c))
    if len(cards) > max_items:
        lines.append(f"<i>… ещё {len(cards) - max_items}</i>")
    return lines


_SCOPE_UI = {
    "active": ("📋", "Очередь и в работе", "В колонке «Готово» ничего не показываю."),
    "all": ("📋", "Вся доска", "Карточек на доске нет."),
    "done": ("✅", "Готово", "В колонке «Готово» пусто — завершённых карточек нет."),
    "wip": ("⚙️", "В работе", "В колонке «В работе» карточек нет."),
    "queue": ("📥", "Очередь", "В «Очереди» карточек нет."),
}

_COLUMN_ORDER = ("queue", "wip", "done")


def render_active_board(data: dict[str, Any], scope: str = "active") -> str:
    icon, title, empty_msg = _SCOPE_UI.get(scope, _SCOPE_UI["active"])
    groups = {g.get("key"): g for g in (data.get("groups") or [])}
    parts = [f"{icon} <b>{_esc(title)}</b>", ""]
    total = 0
    order = _COLUMN_ORDER if scope in {"active", "all"} else (scope,)
    for key in order:
        group = groups.get(key)
        if not group:
            continue
        cards = group.get("cards") or []
        if not cards and scope not in {key, "all"}:
            continue
        total += len(cards)
        parts.extend(_section(group.get("title", key), cards))
        parts.append("")
    if scope in {"active", "all"}:
        for key, group in groups.items():
            if key in order:
                continue
            cards = group.get("cards") or []
            if cards:
                total += len(cards)
                parts.extend(_section(group.get("title", key), cards))
                parts.append("")
    other = data.get("uncategorized") or []
    if other and scope == "all":
        parts.extend(_section("Прочие колонки", other))
        parts.append("")
        total += len(other)
    if total == 0:
        return empty_msg
    parts.insert(1, f"Всего: <b>{total}</b>\n")
    return "\n".join(parts).strip()


def render_card_list(cards: list[dict], header: str) -> str:
    if not cards:
        return f"✅ {_esc(header)}: пусто."
    lines = [f"<b>{_esc(header)}</b> ({len(cards)})", ""]
    for c in cards[:15]:
        lines.append(_format_card_line(c))
    if len(cards) > 15:
        lines.append(f"\n<i>… ещё {len(cards) - 15}</i>")
    return "\n".join(lines)


def render_digest(harness=None) -> str:
    if harness is not None:
        return render_digest_via_harness(harness)
    parts = []
    ov = list_overdue().get("data") or []
    if ov:
        parts.append(f"⚠ <b>Просрочено ({len(ov)}):</b>")
        for c in ov[:10]:
            parts.append(_format_card_line(c))
    td = list_today().get("data") or []
    seen = {c["id"] for c in ov}
    td_only = [c for c in td if c["id"] not in seen]
    if td_only:
        parts.append(f"\n📅 <b>Сегодня ({len(td_only)}):</b>")
        for c in td_only[:10]:
            parts.append(_format_card_line(c))
    active = list_active_cards(include_done=False, limit=50)
    if active.get("status") == "success":
        data = active.get("data") or {}
        groups = data.get("groups") or []
        n = sum(len(g.get("cards") or []) for g in groups)
        n += len(data.get("uncategorized") or [])
        if n and not parts:
            parts.append(render_active_board(data))
            return "\n".join(parts).strip()
        if n and parts:
            parts.append(f"\n📌 <b>На доске (очередь + WIP): {n}</b>")
            for g in groups:
                for c in (g.get("cards") or [])[:5]:
                    parts.append(_format_card_line(c))
    if not parts:
        return "✅ Просроченных, сегодняшних и активных на доске нет."
    return "\n".join(parts).strip()


_SCOPE_COLUMNS: dict[str, list[str]] = {
    "active": ["queue", "wip"],
    "all": ["queue", "wip", "done"],
    "done": ["done"],
    "wip": ["wip"],
    "queue": ["queue"],
}


def render_task_list_via_harness(harness, scope: str) -> str:
    """Render list using AgentHarness (policy + rate limits + audit)."""
    scope = (scope or "active").lower()
    if scope == "today":
        res = harness.execute_tool("list_today", {})
        if res.get("status") != "success":
            return f"❌ {_esc(res.get('summary', ''))}"
        return render_card_list(res.get("data") or [], "Сегодня")
    if scope == "overdue":
        res = harness.execute_tool("list_overdue", {})
        if res.get("status") != "success":
            return f"❌ {_esc(res.get('summary', ''))}"
        return render_card_list(res.get("data") or [], "Просрочено")
    if scope == "digest":
        return render_digest(harness)
    columns = _SCOPE_COLUMNS.get(scope, ["queue", "wip"])
    res = harness.execute_tool(
        "list_active_cards",
        {"columns": columns, "limit": 50},
    )
    if res.get("status") != "success":
        return f"❌ Не удалось загрузить доску: {_esc(res.get('summary', ''))}"
    return render_active_board(res.get("data") or {}, scope=scope)


def render_digest_via_harness(harness) -> str:
    parts = []
    ov = harness.execute_tool("list_overdue", {})
    ov_cards = ov.get("data") or [] if ov.get("status") == "success" else []
    if ov_cards:
        parts.append(f"⚠ <b>Просрочено ({len(ov_cards)}):</b>")
        for c in ov_cards[:10]:
            parts.append(_format_card_line(c))
    td = harness.execute_tool("list_today", {})
    td_cards = td.get("data") or [] if td.get("status") == "success" else []
    seen = {c["id"] for c in ov_cards}
    td_only = [c for c in td_cards if c["id"] not in seen]
    if td_only:
        parts.append(f"\n📅 <b>Сегодня ({len(td_only)}):</b>")
        for c in td_only[:10]:
            parts.append(_format_card_line(c))
    active = harness.execute_tool(
        "list_active_cards", {"columns": ["queue", "wip"], "limit": 50}
    )
    if active.get("status") == "success":
        data = active.get("data") or {}
        groups = data.get("groups") or []
        n = sum(len(g.get("cards") or []) for g in groups)
        n += len(data.get("uncategorized") or [])
        if n and not parts:
            parts.append(render_active_board(data))
            return "\n".join(parts).strip()
        if n and parts:
            parts.append(f"\n📌 <b>На доске (очередь + WIP): {n}</b>")
            for g in groups:
                for c in (g.get("cards") or [])[:5]:
                    parts.append(_format_card_line(c))
    if not parts:
        return "✅ Просроченных, сегодняшних и активных на доске нет."
    return "\n".join(parts).strip()


def render_task_list(scope: str) -> str:
    """scope: active | today | overdue | digest | all"""
    scope = (scope or "active").lower()
    if scope == "today":
        return render_card_list(list_today().get("data") or [], "Сегодня")
    if scope == "overdue":
        return render_card_list(list_overdue().get("data") or [], "Просрочено")
    if scope == "digest":
        return render_digest()
    columns = _SCOPE_COLUMNS.get(scope, ["queue", "wip"])
    res = list_active_cards(columns=columns, limit=50)
    if res.get("status") != "success":
        return f"❌ Не удалось загрузить доску: {_esc(res.get('summary', ''))}"
    return render_active_board(res.get("data") or {}, scope=scope)
