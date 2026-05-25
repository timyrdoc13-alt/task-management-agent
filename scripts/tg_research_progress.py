"""Telegram UX for long research jobs — step messages and/or pinned status edits."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING

from kaiten_api import ENV

if TYPE_CHECKING:
    from aiogram import Bot

log = logging.getLogger("kaiten-bot.research-progress")

TG_STREAM_EDIT_INTERVAL = float(ENV.get("TG_STREAM_EDIT_INTERVAL_SEC", "1.2"))


def research_progress_mode() -> str:
    """steps | edit | both — default steps (reliable vs single edit_message)."""
    return (ENV.get("TG_RESEARCH_PROGRESS_MODE", "steps") or "steps").strip().lower()


def _truncate_html(text: str, limit: int = 4000) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 20] + "\n…"


class TgResearchProgress:
    """Consumes research emit queue; posts milestones to chat."""

    def __init__(
        self,
        bot: Bot,
        chat_id: int,
        pin_message_id: int,
        topic: str,
        *,
        html_escape,
    ) -> None:
        self._bot = bot
        self._chat_id = chat_id
        self._pin_id = pin_message_id
        self._topic = topic[:80]
        self._escape = html_escape
        self._mode = research_progress_mode()
        self._header = f"🔄 <b>{self._escape(self._topic)}</b>\n"
        self._pin_body = self._header + "🔍 Старт…"
        self._tldr = ""
        self._last_status = ""
        self._last_edit = 0.0
        self._last_step_sent = ""

    async def run_consumer(self, queue: asyncio.Queue) -> None:
        while True:
            try:
                ev = await asyncio.wait_for(queue.get(), timeout=900)
            except asyncio.TimeoutError:
                continue
            phase = ev.get("phase")
            if phase == "done":
                break
            if phase == "status":
                text = ev.get("text", "")
                self._pin_body = self._header + self._escape(text)
                await self._maybe_send_step(text)
            elif phase == "tldr_delta":
                self._tldr += ev.get("text", "")
            if phase not in ("status", "tldr_delta"):
                continue
            await self._maybe_edit_pin()

    async def _maybe_send_step(self, status_text: str) -> None:
        if self._mode not in {"steps", "both"}:
            return
        if not status_text or status_text == self._last_step_sent:
            return
        self._last_step_sent = status_text
        try:
            await self._bot.send_message(
                self._chat_id,
                f"📍 {self._escape(status_text)}",
            )
        except Exception as e:
            log.warning("research step message failed chat=%s: %s", self._chat_id, e)

    async def _maybe_edit_pin(self) -> None:
        if self._mode not in {"edit", "both"}:
            return
        now = time.time()
        if now - self._last_edit < TG_STREAM_EDIT_INTERVAL:
            return
        body = self._pin_body
        if self._tldr:
            body += (
                f"\n\n<b>Кратко</b>\n<pre>{self._escape(self._tldr[-2500:])}</pre>"
                "\n\n<i>Полный отчёт готовится…</i>"
            )
        await self._edit_pin(body)
        self._last_edit = now

    async def finalize_pin_before_attach(self) -> None:
        if self._mode not in {"edit", "both"} or not self._tldr:
            return
        body = (
            self._pin_body
            + f"\n\n<b>Кратко</b>\n<pre>{self._escape(self._tldr[-2500:])}</pre>"
            "\n\n<i>Финализирую DOCX и Kaiten…</i>"
        )
        await self._edit_pin(body)

    async def _edit_pin(self, body: str) -> None:
        try:
            await self._bot.edit_message_text(
                _truncate_html(body),
                self._chat_id,
                self._pin_id,
            )
        except Exception as e:
            log.warning(
                "research pin edit failed chat=%s msg=%s: %s",
                self._chat_id,
                self._pin_id,
                e,
            )
