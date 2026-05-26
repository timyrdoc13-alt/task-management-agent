"""Telegram outbound helpers for scheduled digests."""

from __future__ import annotations

import html
import json
import logging
import urllib.error
import urllib.request
from typing import Iterable

from kaiten_api import ENV

log = logging.getLogger(__name__)


def _parse_chat_id_list(raw: str) -> list[int]:
    out: list[int] = []
    for part in raw.replace(";", ",").split(","):
        part = part.strip()
        if part.isdigit():
            out.append(int(part))
    return out


def digest_chat_ids() -> list[int]:
    """Кому слать дайджесты: TG_DIGEST_CHAT_IDS + все editor из telegram_users.yaml."""
    ids: set[int] = set()
    raw = (ENV.get("TG_DIGEST_CHAT_IDS") or ENV.get("TG_ADMIN_NOTIFY_IDS") or "").strip()
    ids.update(_parse_chat_id_list(raw))
    try:
        from user_directory import telegram_user_ids_with_bot_access

        ids.update(telegram_user_ids_with_bot_access())
    except Exception as e:
        log.warning("telegram_users.yaml: %s", e)
    if ids:
        return sorted(ids)
    default = (ENV.get("TG_DIGEST_CHAT_ID") or "228378111").strip()
    if default.isdigit():
        return [int(default)]
    return []


def _split_telegram_chunks(text: str, limit: int = 3900) -> list[str]:
    if len(text) <= limit:
        return [text]
    parts: list[str] = []
    rest = text
    while rest:
        if len(rest) <= limit:
            parts.append(rest)
            break
        cut = rest.rfind("\n\n", 0, limit)
        if cut < limit // 2:
            cut = rest.rfind("\n", 0, limit)
        if cut < limit // 2:
            cut = limit
        parts.append(rest[:cut].rstrip())
        rest = rest[cut:].lstrip()
    return parts


def send_html(chat_id: int, text: str, *, silent: bool = False) -> bool:
    token = (ENV.get("TG_BOT_TOKEN") or "").strip()
    if not token:
        log.error("TG_BOT_TOKEN missing")
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
        with urllib.request.urlopen(req, timeout=20) as resp:
            return 200 <= resp.status < 300
    except (urllib.error.URLError, OSError, TimeoutError) as e:
        log.warning("telegram send to %s failed: %s", chat_id, e)
        return False


def send_html_long(chat_id: int, text: str, *, silent: bool = False) -> bool:
    chunks = _split_telegram_chunks(text)
    ok_all = True
    for i, chunk in enumerate(chunks):
        prefix = f"<i>({i + 1}/{len(chunks)})</i>\n\n" if len(chunks) > 1 else ""
        if not send_html(chat_id, prefix + chunk, silent=silent):
            ok_all = False
    return ok_all


def broadcast_html(text: str, chat_ids: Iterable[int] | None = None, *, silent: bool = False) -> int:
    ids = list(chat_ids) if chat_ids is not None else digest_chat_ids()
    ok = 0
    for cid in ids:
        if send_html_long(cid, text, silent=silent):
            ok += 1
    return ok


def esc(s: str) -> str:
    return html.escape(str(s), quote=False)
