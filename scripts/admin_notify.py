"""Уведомления админу о задачах, созданных через Telegram-бота."""

from __future__ import annotations

import html
import json
import logging
import urllib.error
import urllib.request
from typing import TYPE_CHECKING, Any

from kaiten_api import ENV
from user_directory import get_by_kaiten_user_id, get_by_telegram_id

if TYPE_CHECKING:
    from aiogram import Bot
    from agent.types import AgentContext
    from llm import ExtractedTask

log = logging.getLogger("kaiten-bot.admin_notify")


def _truthy(key: str, default: str = "true") -> bool:
    return ENV.get(key, default).lower() in {"1", "true", "yes", "on"}


def admin_telegram_ids() -> set[int]:
    raw = ENV.get("TG_ADMIN_NOTIFY_IDS") or ENV.get("TG_ADMIN_USER_ID") or "228378111"
    return {int(x) for x in raw.split(",") if x.strip().isdigit()}


def notify_on_card_create_enabled() -> bool:
    return _truthy("TG_NOTIFY_ON_CARD_CREATE", "true")


def skip_self_notify() -> bool:
    return _truthy("TG_NOTIFY_SKIP_SELF", "true")


def notify_assignee_enabled() -> bool:
    return _truthy("TG_NOTIFY_ASSIGNEE", "true")


def assignee_notify_silent() -> bool:
    return _truthy("TG_NOTIFY_ASSIGNEE_SILENT", "false")


def macos_notify_enabled() -> bool:
    return _truthy("TG_ADMIN_MACOS_NOTIFY", "true")


def _esc(s: str) -> str:
    return html.escape(s or "", quote=False)


def creator_display_name(ctx: AgentContext) -> str:
    if ctx.user_id:
        u = get_by_telegram_id(ctx.user_id)
        if u:
            return u.display_name
    name = ctx.metadata.get("display_name")
    if name:
        return str(name)
    uname = ctx.metadata.get("telegram_username")
    if uname:
        return f"@{uname}"
    if ctx.user_id:
        return f"TG {ctx.user_id}"
    return "неизвестно"


def format_card_created_notice(
    task: ExtractedTask,
    ctx: AgentContext,
    wf_data: dict[str, Any],
    *,
    kind: str = "create",
) -> str:
    """Короткое HTML-сообщение для админа."""
    title = (task.title or "без названия")[:120]
    creator = creator_display_name(ctx)
    due = task.due_date_iso or "не указан"
    prio = task.priority or "P2"
    prio_s = f"{prio} 🔥" if prio == "P1" else prio
    assignee = wf_data.get("assignee")
    cid = wf_data.get("card_id")
    url = wf_data.get("url") or ""

    header = "🔬 Новый ресёрч" if kind == "research" else "🆕 Новая задача"
    lines = [
        f"{header}",
        f"<b>Задача:</b> {_esc(title)}",
        f"<b>Кто завёл:</b> {_esc(creator)}",
        f"<b>Срок:</b> <code>{_esc(due)}</code>",
        f"<b>Срочность:</b> <code>{_esc(prio_s)}</code>",
    ]
    if assignee and _esc(assignee) != _esc(creator):
        lines.append(f"<b>Исполнитель:</b> {_esc(str(assignee))}")
    if cid:
        lines.append(f"<b>Карточка:</b> #{cid}")
    if url:
        lines.append(url)
    return "\n".join(lines)


def format_assignee_notice(
    task: ExtractedTask,
    ctx: AgentContext,
    wf_data: dict[str, Any],
    *,
    kind: str = "create",
) -> str:
    """Сообщение исполнителю: кто завёл задачу и ссылка на карточку."""
    title = (task.title or "без названия")[:120]
    creator = creator_display_name(ctx)
    due = task.due_date_iso or "не указан"
    prio = task.priority or "P2"
    prio_s = f"{prio} 🔥" if prio == "P1" else prio
    cid = wf_data.get("card_id")
    url = wf_data.get("url") or ""

    if kind == "research":
        header = f"🔬 <b>{_esc(creator)}</b> поставил(а) тебе ресёрч"
    else:
        header = f"📋 <b>{_esc(creator)}</b> поставил(а) тебе задачу"

    lines = [
        header,
        "",
        f"<b>{_esc(title)}</b>",
        f"Срок: <code>{_esc(due)}</code> · срочность: <code>{_esc(prio_s)}</code>",
    ]
    if cid:
        lines.append(f"Карточка: <b>#{cid}</b>")
    if url:
        lines.append(url)
    lines.append("")
    lines.append("<i>Открой в Kaiten или напиши боту /tasks</i>")
    return "\n".join(lines)


def format_macos_body(
    task: ExtractedTask,
    ctx: AgentContext,
    wf_data: dict[str, Any],
) -> str:
    creator = creator_display_name(ctx)
    due = task.due_date_iso or "—"
    prio = task.priority or "P2"
    assignee = wf_data.get("assignee")
    parts = [
        (task.title or "")[:80],
        f"от {creator}",
        f"срок {due}",
        prio,
    ]
    if assignee and assignee != creator:
        parts.append(f"→ {assignee}")
    cid = wf_data.get("card_id")
    if cid:
        parts.append(f"#{cid}")
    return " · ".join(parts)


def _telegram_send_html(chat_id: int, text: str, *, silent: bool = True) -> bool:
    token = ENV.get("TG_BOT_TOKEN", "")
    if not token:
        return False
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_notification": silent,
        "disable_web_page_preview": True,
    }
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return 200 <= resp.status < 300
    except (urllib.error.URLError, OSError, TimeoutError) as e:
        log.warning("telegram send to %s failed: %s", chat_id, e)
        return False


def notify_assignee_card_created_sync(
    task: ExtractedTask,
    ctx: AgentContext,
    wf_data: dict[str, Any],
    *,
    kind: str = "create",
) -> None:
    """TG исполнителю, если у него есть telegram_id в config и он не создатель."""
    if not notify_assignee_enabled():
        return
    kid = wf_data.get("assignee_kaiten_user_id")
    if not kid:
        return
    assignee = get_by_kaiten_user_id(int(kid))
    if not assignee or not assignee.telegram_id:
        log.debug("assignee notify skip: no telegram for kaiten_user_id=%s", kid)
        return
    creator_id = ctx.user_id
    if creator_id and assignee.telegram_id == creator_id:
        return
    if wf_data.get("assignee") and creator_display_name(ctx) == wf_data.get("assignee"):
        return
    text = format_assignee_notice(task, ctx, wf_data, kind=kind)
    ok = _telegram_send_html(
        assignee.telegram_id,
        text,
        silent=assignee_notify_silent(),
    )
    if ok:
        log.info(
            "assignee notify sent tg=%s kaiten=%s card=%s",
            assignee.telegram_id,
            kid,
            wf_data.get("card_id"),
        )


def notify_admins_card_created_sync(
    task: ExtractedTask,
    ctx: AgentContext,
    wf_data: dict[str, Any],
    *,
    kind: str = "create",
) -> None:
    """Синхронно (из workflow thread): TG админам + исполнителю + опционально macOS."""
    if notify_on_card_create_enabled():
        creator_id = ctx.user_id
        text = format_card_created_notice(task, ctx, wf_data, kind=kind)
        for admin_id in admin_telegram_ids():
            if skip_self_notify() and creator_id and admin_id == creator_id:
                continue
            _telegram_send_html(admin_id, text, silent=True)

    notify_assignee_card_created_sync(task, ctx, wf_data, kind=kind)

    if macos_notify_enabled():
        try:
            import notify as macos_notify  # noqa: WPS433

            macos_notify.notify(
                "Kaiten · новая задача",
                format_macos_body(task, ctx, wf_data),
            )
        except Exception as e:
            log.debug("admin macos notify failed: %s", e)


async def notify_admins_card_created(
    bot: Bot,
    task: ExtractedTask,
    ctx: AgentContext,
    wf_data: dict[str, Any],
    *,
    kind: str = "create",
) -> None:
    import asyncio

    await asyncio.to_thread(
        notify_admins_card_created_sync, task, ctx, wf_data, kind=kind
    )
