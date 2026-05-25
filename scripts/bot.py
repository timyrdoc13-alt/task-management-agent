"""Telegram bot. aiogram, long polling. Whitelist by TG_ALLOWED_CHATS.

Flow:
  message -> classify -> {ambiguous|create|research|update|reminder}
  create + safe + P2/P3 + conf>=0.85 -> create card immediately
  create + risky/P1/low-conf      -> inline preview with buttons
  research + safe + conf>=0.75    -> async run_research -> attach -> Готово
  research + risky/low-conf       -> ask confirmation
  list / reminder                 -> task board (active, today, overdue, digest)

Pending previews: `agent/pending_store.py` on disk (survives restart).
All Kaiten side effects go through `AgentHarness.execute_tool`.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from kaiten_api import ENV, log_call  # noqa: E402
from card_actions import (  # noqa: E402
    CardAction,
    apply_card_action,
    build_action_plan,
    extract_card_action,
    parse_card_id,
    should_offer_card_choice,
)
from agent import (  # noqa: E402
    AgentContext,
    AgentHarness,
    create_needs_preview,
    research_needs_preview,
    run_create_card_workflow,
    run_research_workflow,
)
from agent.job_store import (  # noqa: E402
    cancel_job,
    get_job_by_key,
    get_running_job,
    research_idempotency_key,
)
from agent.types import WorkflowResult  # noqa: E402
from tg_research_progress import TgResearchProgress  # noqa: E402
from work_log import apply_work_log, parse_log_command, parse_work_log  # noqa: E402
from agent.pending_store import find_task_preview_by_chat  # noqa: E402
from agent.pending_store import get as pending_get  # noqa: E402
from agent.pending_store import pop as pending_pop  # noqa: E402
from agent.pending_store import put as pending_put  # noqa: E402
from agent.process_lock import BotLockError, BotProcessLock  # noqa: E402
from llm import ExtractedTask, extract_task, revise_task_draft  # noqa: E402
from report_delivery import (  # noqa: E402
    ReportArtifact,
    find_report,
    list_artifacts,
    tag_artifact_with_card,
)
from task_views import render_task_list_via_harness  # noqa: E402
from user_directory import get_by_telegram_id, resolve_for_context, telegram_user_ids_with_bot_access  # noqa: E402

# Сообщения про изменение/удаление существующих карточек
_CARD_OP_HINT = re.compile(
    r"(?:#\d{5,10}|\b\d{7,10}\b|удали|удалить|закрой|закрыть|перенеси|сдвинь|"
    r"приоритет|дедлайн|готово|колонк|переименуй|описание)",
    re.I,
)

try:
    import notify as _macos_notify  # noqa: E402 — scripts/notify.py
except ImportError:
    _macos_notify = None

try:
    from aiogram import Bot, Dispatcher, F, types
    from aiogram.client.default import DefaultBotProperties
    from aiogram.enums import ChatAction, ParseMode
    from aiogram.filters import Command
    from aiogram.types import (
        CallbackQuery,
        FSInputFile,
        InlineKeyboardButton,
        InlineKeyboardMarkup,
        Message,
    )
except ImportError:
    print("aiogram not installed. Run: pip install -r requirements.txt", file=sys.stderr)
    sys.exit(1)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
log = logging.getLogger("kaiten-bot")

BOT_TOKEN = ENV.get("TG_BOT_TOKEN", "")
_env_allowed = {
    int(x) for x in (ENV.get("TG_ALLOWED_USERS") or ENV.get("TG_ALLOWED_CHATS") or "").split(",")
    if x.strip().isdigit()
}
ALLOWED_USERS = _env_allowed | telegram_user_ids_with_bot_access()
ALLOWED_CHATS = {
    int(x) for x in (ENV.get("TG_ALLOWED_CHATS") or "").split(",") if x.strip().isdigit()
}
AUTO_CARD_MIN = float(ENV.get("AUTO_CARD_MIN_CONFIDENCE", "0.85"))
AUTO_RESEARCH_MIN = float(ENV.get("AUTO_RESEARCH_MIN_CONFIDENCE", "0.75"))
AUTO_RESEARCH_ENABLED = (ENV.get("AUTO_RESEARCH_ENABLED", "true").lower() == "true")

if not BOT_TOKEN:
    print("TG_BOT_TOKEN missing in .env", file=sys.stderr)
    sys.exit(1)
if not ALLOWED_USERS and not ALLOWED_CHATS:
    print("TG_ALLOWED_USERS or TG_ALLOWED_CHATS required", file=sys.stderr)
    sys.exit(1)

bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()

_BOT_LOCK = BotProcessLock()


def _approval_token() -> str:
    import secrets

    return "ak_" + secrets.token_hex(3)


def _preview_text(task: ExtractedTask, ctx: AgentContext | None = None) -> str:
    lines = [
        f"<b>{task.icon} {task.title}</b>",
        "",
        f"intent: <code>{task.intent}</code> · priority: <code>{task.priority}</code> · conf: {task.confidence:.2f}",
    ]
    if ctx:
        assignee = resolve_for_context(ctx, task)
        if assignee:
            lines.append(f"Исполнитель: <b>{_html_escape(assignee.display_name)}</b>")
            if assignee.ambiguous:
                lines.append(
                    "<i>⚠ Несколько совпадений по имени — взял: "
                    + _html_escape(assignee.display_name)
                    + "</i>"
                )
    if task.due_date_iso:
        lines.append(f"due: <code>{task.due_date_iso}</code>")
    if task.owner_hint:
        lines.append(f"owner: {task.owner_hint}")
    if task.tags:
        lines.append("tags: " + ", ".join(task.tags))
    if task.sensitive_markers:
        lines.append("⚠ sensitive: " + ", ".join(task.sensitive_markers))
    lines.append("")
    short = (task.short_description or "").strip()
    if short:
        lines.append("<i>Описание (в Kaiten):</i>")
        lines.append(f"<pre>{_html_escape(short)}</pre>")
    if task.description_md and task.description_md.strip() != short:
        body = task.description_md
        if len(body) > 800:
            body = body[:800] + "..."
        lines.append("<i>Детали:</i>")
        lines.append(f"<pre>{_html_escape(body)}</pre>")
    elif not short:
        lines.append("<i>Описание:</i> <пусто>")
    return "\n".join(lines)


def _html_escape(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _task_from_pending(d: dict) -> ExtractedTask:
    fields = {k: v for k, v in d.items() if k in ExtractedTask.__annotations__}
    return ExtractedTask(**fields)


def _preview_kb(token: str, *, delete: bool = False) -> InlineKeyboardMarkup:
    yes_label = "🗑 Удалить" if delete else "✅ Применить"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=yes_label, callback_data=f"yes:{token}"),
                InlineKeyboardButton(text="❌ Отмена", callback_data=f"no:{token}"),
            ]
        ]
    )


def _card_choice_kb(token: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="💬 Комментарий", callback_data=f"cact:c:{token}"
                ),
                InlineKeyboardButton(text="⏱ Время", callback_data=f"cact:t:{token}"),
            ],
            [
                InlineKeyboardButton(
                    text="✏ Изменить карточку", callback_data=f"cact:e:{token}"
                ),
                InlineKeyboardButton(text="❌ Отмена", callback_data=f"cact:n:{token}"),
            ],
        ]
    )


def _tg_context(msg: Message | CallbackQuery) -> AgentContext:
    user = msg.from_user
    chat = msg.chat if isinstance(msg, Message) else msg.message.chat
    ctx = AgentContext(
        channel="telegram",
        user_id=user.id if user else None,
        chat_id=chat.id if chat else None,
    )
    if user:
        ku = get_by_telegram_id(user.id)
        if ku:
            ctx.metadata["kaiten_user_id"] = ku.kaiten_user_id
            ctx.metadata["display_name"] = ku.display_name
            ctx.metadata["role"] = ku.role
        if user.username:
            ctx.metadata["telegram_username"] = user.username
    return ctx


def _tg_harness(msg: Message | CallbackQuery) -> AgentHarness:
    return AgentHarness(_tg_context(msg))


def _is_allowed(msg: Message | CallbackQuery) -> bool:
    user = msg.from_user
    if not user:
        return False
    chat = msg.chat if isinstance(msg, Message) else msg.message.chat
    if ALLOWED_USERS and user.id in ALLOWED_USERS:
        return True
    if ALLOWED_CHATS and chat and chat.id in ALLOWED_CHATS:
        return True
    return False


@dp.message(Command("start"))
async def on_start(msg: Message) -> None:
    if not _is_allowed(msg):
        log.warning("unauthorized /start from %s", msg.from_user)
        return
    await msg.answer(
        "🤖 Kaiten-агент на связи.\n\n"
        "Просто пиши задачи или вопросы по-человечески:\n"
        "• «поставь задачу проверить акты, срочно»\n"
        "• «какие задачи сейчас?» / «что готово?» — список с доски\n"
        "• «отчёт за месяц» — сводка по задачам (сделано, новые, на стопе)\n"
        "• «пришли файл #64956648» — DOCX результата ресёрча\n"
        "• «что сегодня?» / «что просрочено?»\n"
        "• «изучи monorepo для Next.js 15» — сделаю сам и положу файл в карточку\n"
        "• «сдвинь #64942139 на завтра, P2» — изменить карточку\n"
        "• «#64942139 сделал интеграцию, 2ч» — комментарий и время в Kaiten\n"
        "• /log #64942139 2ч мит — явная запись времени и комментария\n"
        "• «удали #64942139» — удалить (с подтверждением)\n"
        "• /cancel — остановить текущий ресёрч\n\n"
        "Перед созданием задачи покажу черновик — можно править текстом."
    )


@dp.message(Command("cancel"))
async def on_cancel(msg: Message) -> None:
    if not _is_allowed(msg):
        return
    running = get_running_job(msg.chat.id, "research")
    if not running:
        await msg.answer("Нет активного ресёрча в этом чате.")
        return
    cancel_job(running["id"])
    await msg.answer(
        f"⏹ Запрошена отмена ресёрча.\n"
        f"Job: <code>{_html_escape(running.get('id', ''))}</code>\n"
        f"<i>Остановка на ближайшем этапе (поиск/чтение/синтез).</i>"
    )


@dp.message(Command("help"))
async def on_help(msg: Message) -> None:
    if not _is_allowed(msg):
        return
    await msg.answer(
        "Команды:\n"
        "/tasks — очередь и в работе\n"
        "/today — дедлайн сегодня\n"
        "/overdue — просрочено\n"
        "/board — вся доска\n"
        "/done — только колонка «Готово»\n"
        "/report — сводка по задачам за месяц\n"
        "/file — последний файл ресёрча (DOCX)\n"
        "/card 64942139 — показать карточку\n"
        "/log #64942139 2ч текст — комментарий + time log (без глаголов «сделал»)\n"
        "/cancel — остановить текущий ресёрч\n"
        "/start — справка\n\n"
        "Работа по карточке: «#64942139 сделал X, 2 часа» или /log #id 2ч текст\n\n"
        "Изменение карточки (укажи #id):\n"
        "• сдвинь / закрой / в готово\n"
        "• приоритет P1 P2 P3\n"
        "• дедлайн сегодня / завтра / убери дедлайн\n"
        "• удали — удаление\n"
        "• переименуй / новое описание"
    )


async def _send_board_report(msg: Message, task: ExtractedTask) -> None:
    text = msg.text or msg.caption or ""

    def _run() -> dict:
        h = _tg_harness(msg)
        return h.execute_tool("board_period_report", {"user_text": text})

    res = await asyncio.to_thread(_run)
    if res.get("status") != "success":
        await msg.answer(f"❌ {_html_escape(res.get('summary', 'ошибка отчёта'))}")
        return
    html = (res.get("data") or {}).get("html") or ""
    await msg.answer(html)


async def _deliver_artifact(msg: Message, task: ExtractedTask) -> None:
    raw = task.raw or {}
    card_id = raw.get("card_id")
    topic_hint = raw.get("topic_hint") or task.research_topic
    latest = task.list_scope == "latest" or not card_id and not topic_hint

    def _resolve_artifact() -> ReportArtifact | None:
        h = _tg_harness(msg)
        hint = topic_hint
        if card_id and not find_report(card_id=int(card_id)):
            gc = h.execute_tool("get_card", {"card_id": int(card_id)})
            if gc.get("status") == "success":
                title = (gc.get("data") or {}).get("title") or ""
                title = re.sub(r"^[^\w]+\s*", "", title)
                title = re.sub(r"^\[Ресёрч\]\s*", "", title, flags=re.I)
                hint = title.strip() or hint
        r = h.execute_tool(
            "find_research_artifact",
            {
                "card_id": int(card_id) if card_id else None,
                "topic": hint,
                "latest": latest,
            },
        )
        if r.get("status") != "success":
            return None
        d = r.get("data") or {}
        return ReportArtifact(
            topic=d.get("topic", ""),
            dir_path=Path(d["dir_path"]),
            docx_path=Path(d["docx_path"]) if d.get("docx_path") else None,
            md_path=Path(d["md_path"]) if d.get("md_path") else None,
            meta={},
            card_id=d.get("card_id"),
        )

    art = await asyncio.to_thread(_resolve_artifact)
    if not art:
        recent = await asyncio.to_thread(list_artifacts, 5)
        hint = "\n".join(
            f"• {a.topic[:50]} (#{a.card_id or '—'})" for a in recent[:5]
        )
        await msg.answer(
            "❌ Файл не найден. Укажи <code>#id</code> карточки, тему или сделай новый ресёрч.\n\n"
            f"<b>Недавние:</b>\n{_html_escape(hint) if hint else 'пусто'}"
        )
        return
    await _send_research_artifact(msg.chat.id, art)


async def _send_research_artifact(chat_id: int, art: ReportArtifact) -> None:
    path = art.docx_path or art.md_path
    if not path or not path.exists():
        await bot.send_message(chat_id, "❌ Файл результата на диске не найден.")
        return
    caption = f"📎 {_html_escape(art.topic[:180])}"
    if art.card_id:
        caption += f"\nКарточка #{art.card_id}"
    try:
        await bot.send_document(chat_id, FSInputFile(str(path)), caption=caption[:1024])
    except Exception as e:
        log.exception("send_document failed")
        await bot.send_message(
            chat_id,
            f"⚠ Не отправил файл: {_html_escape(str(e)[:200])}\n"
            f"Путь: <code>{_html_escape(str(path))}</code>",
        )
        return
    summary = art.summary_text(3200)
    if summary:
        await bot.send_message(
            chat_id,
            f"<b>Кратко</b>\n<pre>{_html_escape(summary)}</pre>",
        )


async def _send_task_list(msg: Message, scope: str) -> None:
    ctx = _tg_context(msg)

    def _run() -> str:
        harness = AgentHarness(ctx)
        return render_task_list_via_harness(harness, scope)

    await msg.answer(await asyncio.to_thread(_run))


@dp.message(Command("tasks"))
async def on_tasks(msg: Message) -> None:
    if not _is_allowed(msg):
        return
    await _send_task_list(msg, "active")


@dp.message(Command("board"))
async def on_board(msg: Message) -> None:
    if not _is_allowed(msg):
        return
    await _send_task_list(msg, "all")


@dp.message(Command("done"))
async def on_done(msg: Message) -> None:
    if not _is_allowed(msg):
        return
    await _send_task_list(msg, "done")


@dp.message(Command("report"))
async def on_report_cmd(msg: Message) -> None:
    if not _is_allowed(msg):
        return
    await _send_board_report(msg, ExtractedTask(intent="report", confidence=1.0))


@dp.message(Command("file"))
async def on_file_cmd(msg: Message) -> None:
    if not _is_allowed(msg):
        return
    await _deliver_artifact(
        msg, ExtractedTask(intent="artifact", list_scope="latest", confidence=1.0)
    )


@dp.message(Command("today"))
async def on_today(msg: Message) -> None:
    if not _is_allowed(msg):
        return
    await _send_task_list(msg, "today")


@dp.message(Command("overdue"))
async def on_overdue(msg: Message) -> None:
    if not _is_allowed(msg):
        return
    await _send_task_list(msg, "overdue")


@dp.message(Command("log"))
async def on_log_cmd(msg: Message) -> None:
    if not _is_allowed(msg):
        return
    req = parse_log_command(msg.text or "")
    if not req:
        await msg.answer(
            "Использование: <code>/log #64942144 2ч провели мит</code> "
            "или <code>/log 64942144 текст</code>"
        )
        return
    await _handle_work_log(msg, req)


@dp.message(Command("card"))
async def on_card_cmd(msg: Message) -> None:
    if not _is_allowed(msg):
        return
    parts = (msg.text or "").split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip().isdigit():
        await msg.answer("Использование: <code>/card 64942139</code>")
        return
    cid = int(parts[1].strip())

    def _run() -> dict:
        return _tg_harness(msg).execute_tool("get_card", {"card_id": cid})

    res = await asyncio.to_thread(_run)
    if res.get("status") != "success":
        await msg.answer(f"Карточка #{cid} не найдена.")
        return
    c = res["data"]
    base = ENV.get("KAITEN_BASE_URL", "").rstrip("/")
    await msg.answer(
        f"<b>#{cid}</b> {_html_escape((c.get('title') or '')[:80])}\n"
        f"Колонка: {c.get('column_id')} · asap: {c.get('asap')} · due: {c.get('due_date')}\n"
        f"{base}/{cid}"
    )


@dp.message(F.text)
async def on_text(msg: Message) -> None:
    if not _is_allowed(msg):
        log.warning("unauthorized text from %s", msg.from_user)
        return
    text = msg.text.strip()
    await bot.send_chat_action(msg.chat.id, ChatAction.TYPING)

    work_req = parse_work_log(text)
    if work_req:
        await _handle_work_log(msg, work_req)
        return

    preview = find_task_preview_by_chat(msg.chat.id)
    if preview and not text.startswith("/"):
        token, pending = preview
        task = _task_from_pending(pending.get("task") or {})
        revised = await asyncio.to_thread(revise_task_draft, task, text)
        pending_put(
            token,
            {
                **pending,
                "kind": "task_preview",
                "task": revised.to_dict(),
            },
        )
        ctx = _tg_context(msg)
        body = _preview_text(revised, ctx)
        body += "\n\n<i>Обновил черновик. Кнопка «Применить» или новая правка текстом.</i>"
        await msg.answer(body, reply_markup=_preview_kb(token))
        return

    card_id = parse_card_id(text)
    if card_id and should_offer_card_choice(text, card_id):
        token = _approval_token()
        pending_put(
            token,
            {
                "kind": "card_choice",
                "card_id": card_id,
                "text": text,
                "chat_id": msg.chat.id,
            },
        )
        await msg.answer(
            f"Карточка <b>#{card_id}</b> — что сделать с сообщением?\n"
            f"<i>{_html_escape(text[:200])}</i>",
            reply_markup=_card_choice_kb(token),
        )
        return

    # Изменение / удаление карточки (приоритет над create/research)
    if _CARD_OP_HINT.search(text) or card_id:
        card_action = await asyncio.to_thread(extract_card_action, text)
        if card_action.action != "none" and card_action.card_id:
            await _handle_card_action(msg, card_action)
            return

    task = await asyncio.to_thread(extract_task, text)
    log.info("extracted: intent=%s priority=%s conf=%.2f sensitive=%s",
             task.intent, task.priority, task.confidence, task.sensitive_markers)
    log_call("tg_extract", {"intent": task.intent, "priority": task.priority,
                              "conf": task.confidence}, "ok", 0,
              {"chat_id": msg.chat.id})

    if task.intent == "list":
        scope = task.list_scope or "active"
        await _send_task_list(msg, scope)
        return

    if task.intent == "report":
        await _send_board_report(msg, task)
        return

    if task.intent == "artifact":
        await _deliver_artifact(msg, task)
        return

    if task.intent == "ambiguous":
        await msg.answer(
            "🤔 Не понял интент. Уточни одним сообщением: задача (кому, когда), "
            "ресёрч (что изучить), или напоминание (сегодня/просрочено)?"
        )
        return

    if task.intent == "research":
        if not AUTO_RESEARCH_ENABLED:
            await msg.answer("⚙ Авто-ресёрч выключен в .env (AUTO_RESEARCH_ENABLED=false).")
            return
        ctx = _tg_context(msg)
        if research_needs_preview(task, ctx):
            await _send_preview_with_buttons(msg, task)
            return
        _spawn_auto_research(msg.chat.id, task, ctx)
        return

    if task.intent == "create":
        ctx = _tg_context(msg)
        if create_needs_preview(task, ctx):
            await _send_preview_with_buttons(msg, task)
            return
        await _create_now(msg, task, ctx)
        return

    if task.intent == "update":
        card_action = await asyncio.to_thread(extract_card_action, text)
        if card_action.card_id and card_action.action != "none":
            await _handle_card_action(msg, card_action)
        else:
            await msg.answer(
                "✏ Укажи номер карточки: <code>#64942139</code> и что сделать "
                "(приоритет, дедлайн, в готово, удалить)."
            )
        return


async def _handle_card_action(msg: Message, action: CardAction) -> None:
    harness = _tg_harness(msg)

    def _plan() -> dict:
        return build_action_plan(action, harness)

    plan = await asyncio.to_thread(_plan)
    if not plan.get("ok"):
        await msg.answer(f"❌ {_html_escape(plan.get('error', 'ошибка'))}")
        return
    if plan.get("noop"):
        await msg.answer(f"ℹ️ {_html_escape(plan.get('message', 'Без изменений'))}")
        return

    token = _approval_token()
    is_delete = action.action == "delete"

    if is_delete:
        def _confirm() -> dict:
            return harness.execute_tool("confirm_delete", {"card_id": plan["card_id"]})

        conf = await asyncio.to_thread(_confirm)
        del_tok = (conf.get("data") or {}).get("token", "")
        pending_put(token, {
            "kind": "delete",
            "card_id": plan["card_id"],
            "delete_token": del_tok,
            "chat_id": msg.chat.id,
            "plan": plan,
        })
    else:
        pending_put(token, {
            "kind": "update",
            "action": action.to_dict(),
            "chat_id": msg.chat.id,
            "plan": plan,
        })

    lines = [
        f"<b>{'Удаление' if is_delete else 'Изменение'} карточки #{plan['card_id']}</b>",
        f"«{_html_escape(plan['card_title'][:70])}»",
        f"Сейчас: колонка <code>{_html_escape(plan['column_now'])}</code>",
        "",
        "<b>Будет сделано:</b>",
    ]
    lines.extend(f"• {_html_escape(op)}" for op in plan["ops"])
    lines.append(f"\n{plan['card_url']}")
    lines.append("\n<i>Подтверди кнопкой ниже.</i>")

    await msg.answer("\n".join(lines), reply_markup=_preview_kb(token, delete=is_delete))


def _resolve_priority_to_lane(priority: str) -> int | None:
    if priority == "P1":
        v = ENV.get("KAITEN_LANE_URGENT")
    else:
        v = ENV.get("KAITEN_LANE_NORMAL")
    return int(v) if v else None


def _resolve_column_queue() -> int | None:
    v = ENV.get("KAITEN_COL_QUEUE") or ENV.get("KAITEN_DEFAULT_COLUMN_ID")
    return int(v) if v else None


def _format_title(task: ExtractedTask) -> str:
    return f"{task.icon} {task.title}".strip()


async def _reply_card_created(
    chat_id: int,
    task: ExtractedTask,
    ctx: AgentContext,
    wf,
) -> None:
    cid = wf.data.get("card_id")
    url = wf.data.get("url")
    assignee_line = ""
    if wf.data.get("assignee"):
        assignee_line = f"\nИсполнитель: <b>{_html_escape(wf.data['assignee'])}</b>"
    warn = wf.data.get("assign_warning")
    warn_line = f"\n⚠ {_html_escape(warn)}" if warn else ""
    kind = "research" if task.intent == "research" else "create"
    await bot.send_message(
        chat_id,
        f"✅ Карточка создана: <b>{_html_escape(_format_title(task))}</b>\n"
        f"#{cid} · {task.priority}{assignee_line}{warn_line}\n{url}",
    )


async def _create_now(msg: Message, task: ExtractedTask, ctx: AgentContext) -> None:
    def _run():
        harness = AgentHarness(ctx)
        return run_create_card_workflow(
            harness, task, column_id=_resolve_column_queue(), commit=True
        )

    wf = await asyncio.to_thread(_run)
    if wf.status != "success":
        await msg.answer(f"❌ {_html_escape(wf.summary)}")
        return
    await _reply_card_created(msg.chat.id, task, ctx, wf)


async def _send_preview_with_buttons(msg: Message, task: ExtractedTask) -> None:
    token = _approval_token()
    ctx = _tg_context(msg)
    pending_put(
        token,
        {
            "kind": "task_preview",
            "task": task.to_dict(),
            "chat_id": msg.chat.id,
            "telegram_user_id": msg.from_user.id if msg.from_user else None,
        },
    )
    body = _preview_text(task, ctx)
    body += (
        "\n\n<i>Проверь черновик. Кнопка «Применить» — создам в Kaiten. "
        "Или напиши правки одним сообщением (описание, срок, исполнитель).</i>"
    )
    await msg.answer(body, reply_markup=_preview_kb(token))


@dp.callback_query(F.data.startswith("yes:"))
async def on_yes(cq: CallbackQuery) -> None:
    if not _is_allowed(cq):
        return
    token = cq.data.split(":", 1)[1]
    p = pending_pop(token)
    if not p:
        await cq.answer("Превью устарело, повтори.", show_alert=True)
        return

    kind = p.get("kind", "create")
    chat_id = p["chat_id"]

    if kind == "delete":
        await cq.answer("Удаляю...")
        cid = p["card_id"]
        del_tok = p.get("delete_token", "")
        h = AgentHarness(AgentContext(channel="telegram", chat_id=chat_id))

        def _delete() -> dict:
            return h.execute_tool(
                "delete_card",
                {"card_id": cid, "token": del_tok},
                commit=True,
                approved=True,
            )

        res = await asyncio.to_thread(_delete)
        if res.get("status") == "success":
            await bot.send_message(chat_id, f"🗑 Карточка <b>#{cid}</b> удалена.")
        else:
            await bot.send_message(
                chat_id,
                f"❌ Не удалил #{cid}: <code>{_html_escape(str(res.get('summary')))}</code>",
            )
    elif kind == "update":
        await cq.answer("Применяю...")
        action = CardAction.from_dict(p["action"])
        h = AgentHarness(AgentContext(channel="telegram", chat_id=chat_id))
        res = await asyncio.to_thread(
            apply_card_action, action, True, harness=h, approved=True
        )
        plan = p.get("plan") or {}
        url = plan.get("card_url", "")
        if res.get("status") == "success":
            await bot.send_message(
                chat_id,
                f"✅ Карточка <b>#{plan.get('card_id')}</b> обновлена.\n{res.get('summary', '')}\n{url}",
            )
        else:
            await bot.send_message(
                chat_id,
                f"❌ Ошибка: <code>{_html_escape(str(res.get('summary')))}</code>\n{url}",
            )
    else:
        d = p["task"]
        task = _task_from_pending(d)
        await cq.answer("Создаю...")
        if task.intent == "research":
            ctx = AgentContext(
                channel="telegram",
                chat_id=chat_id,
                user_id=p.get("telegram_user_id"),
            )
            ku = get_by_telegram_id(p.get("telegram_user_id"))
            if ku:
                ctx.metadata["kaiten_user_id"] = ku.kaiten_user_id
            _spawn_auto_research(chat_id, task, ctx)
        else:
            ctx = AgentContext(
                channel="telegram",
                chat_id=chat_id,
                user_id=p.get("telegram_user_id"),
            )
            ku = get_by_telegram_id(p.get("telegram_user_id"))
            if ku:
                ctx.metadata["kaiten_user_id"] = ku.kaiten_user_id
            await _create_now_chat(chat_id, task, ctx)

    try:
        await cq.message.edit_reply_markup()
    except Exception:
        pass


@dp.callback_query(F.data.startswith("no:"))
async def on_no(cq: CallbackQuery) -> None:
    if not _is_allowed(cq):
        return
    token = cq.data.split(":", 1)[1]
    pending_pop(token)
    await cq.answer("Отменено.")
    try:
        await cq.message.edit_reply_markup()
    except Exception:
        pass


@dp.callback_query(F.data.startswith("cact:"))
async def on_card_choice(cq: CallbackQuery) -> None:
    if not _is_allowed(cq):
        return
    parts = (cq.data or "").split(":")
    if len(parts) < 3:
        await cq.answer("Неверная кнопка.", show_alert=True)
        return
    action_key, token = parts[1], parts[2]
    if action_key == "n":
        pending_pop(token)
        await cq.answer("Отменено.")
        try:
            await cq.message.edit_reply_markup()
        except Exception:
            pass
        return

    p = pending_get(token)
    if not p or p.get("kind") != "card_choice":
        await cq.answer("Превью устарело, повтори.", show_alert=True)
        return

    chat_id = p["chat_id"]
    card_id = int(p["card_id"])
    stored_text = p.get("text") or ""
    pending_pop(token)
    try:
        await cq.message.edit_reply_markup()
    except Exception:
        pass

    ctx = _tg_context(cq)
    author = str(ctx.metadata.get("display_name") or "Пользователь")

    if action_key == "c":
        await cq.answer("Комментарий...")
        summary = stored_text.strip()
        if len(summary) < 3:
            summary = "Обновление (Telegram)"
        out = await asyncio.to_thread(
            apply_work_log, card_id, summary[:500], author, minutes=None
        )
        if not out.get("ok"):
            err = (out.get("comment") or {}).get("summary", "ошибка")
            await bot.send_message(chat_id, f"❌ {_html_escape(str(err)[:400])}")
            return
        await bot.send_message(chat_id, f"✅ Комментарий на #{card_id}")
        return

    if action_key == "t":
        from work_log import parse_duration_minutes

        minutes = parse_duration_minutes(stored_text)
        if not minutes:
            await cq.answer("Укажи длительность", show_alert=True)
            await bot.send_message(
                chat_id,
                "⏱ Не нашёл длительность в тексте. Пример: "
                "<code>/log #64942144 2ч провели мит</code>",
            )
            return
        await cq.answer("Время...")
        summary = re.sub(
            r"(\d+(?:[.,]\d+)?)\s*(?:час(?:а|ов)?|ч\b|h\b|мин(?:ут(?:ы)?)?|m\b)",
            "",
            stored_text,
            flags=re.I,
        )
        summary = re.sub(r"#\d{5,10}\b", "", summary)
        summary = re.sub(r"\b\d{7,10}\b", "", summary, count=1)
        summary = re.sub(r"\s+", " ", summary).strip(" ,.—")
        if len(summary) < 3:
            summary = "Работа по задаче (TG)"
        out = await asyncio.to_thread(
            apply_work_log, card_id, summary[:500], author, minutes=minutes
        )
        lines = [f"✅ Записал на #{card_id}: комментарий"]
        if out.get("time_log_ok"):
            lines.append(f"⏱ {minutes} мин в time log")
        elif out.get("time_log") is not None:
            tl = out.get("time_log") or {}
            lines.append(
                f"⚠ время не записано: {_html_escape(tl.get('summary', 'нет role_id')[:120])}"
            )
        if not out.get("ok"):
            err = (out.get("comment") or {}).get("summary", "ошибка")
            await bot.send_message(chat_id, f"❌ {_html_escape(str(err)[:400])}")
            return
        await bot.send_message(chat_id, "\n".join(lines))
        return

    if action_key == "e":
        await cq.answer("Изменение...")
        card_action = await asyncio.to_thread(extract_card_action, stored_text)
        if card_action.action == "none" or not card_action.card_id:
            await bot.send_message(
                chat_id,
                "✏ Не понял изменение. Пример: "
                "<code>перенеси #64942139 в готово</code>",
            )
            return
        msg = cq.message
        if msg:
            await _handle_card_action(msg, card_action)
        return

    await cq.answer("Неизвестное действие.", show_alert=True)


async def _create_now_chat(chat_id: int, task: ExtractedTask, ctx: AgentContext) -> None:
    def _run():
        harness = AgentHarness(ctx)
        return run_create_card_workflow(
            harness, task, column_id=_resolve_column_queue(), commit=True
        )

    wf = await asyncio.to_thread(_run)
    if wf.status != "success":
        await bot.send_message(chat_id, f"❌ {_html_escape(wf.summary)}")
        return
    await _reply_card_created(chat_id, task, ctx, wf)


async def _auto_research_message_safe(
    chat_id: int, task: ExtractedTask, ctx: AgentContext
) -> None:
    try:
        await _auto_research_message(chat_id, task, ctx)
    except Exception as e:
        log.exception("research job failed")
        await bot.send_message(
            chat_id, f"❌ Ресёрч: {_html_escape(str(e)[:500])}"
        )


def _spawn_auto_research(chat_id: int, task: ExtractedTask, ctx: AgentContext) -> None:
    """Fire-and-forget research job so polling stays responsive."""
    asyncio.create_task(_auto_research_message_safe(chat_id, task, ctx))


async def _handle_work_log(msg: Message, req) -> None:
    ctx = _tg_context(msg)
    author = str(ctx.metadata.get("display_name") or "Пользователь")

    def _run() -> dict:
        return apply_work_log(req.card_id, req.summary, author, minutes=req.minutes)

    out = await asyncio.to_thread(_run)
    if not out.get("ok"):
        c = out.get("comment") or {}
        err = c.get("summary") or "ошибка комментария"
        await msg.answer(f"❌ {_html_escape(str(err)[:400])}")
        return
    lines = [f"✅ Записал на #{req.card_id}: комментарий"]
    if req.minutes:
        if out.get("time_log_ok"):
            lines.append(f"⏱ {req.minutes} мин в time log")
        elif out.get("time_log") is not None:
            tl = out.get("time_log") or {}
            lines.append(
                f"⚠ время не записано: {_html_escape(tl.get('summary', 'нет role_id')[:120])}"
            )
    await msg.answer("\n".join(lines))


async def _deliver_research_outcome(
    chat_id: int,
    topic: str,
    wf: WorkflowResult,
    *,
    replay_note: str | None = None,
) -> None:
    if wf.status == "error":
        data = wf.data or {}
        if data.get("cancelled"):
            await bot.send_message(chat_id, "⏹ Ресёрч остановлен.")
            return
        await bot.send_message(chat_id, f"❌ {_html_escape(wf.summary)}")
        return

    data = wf.data or {}
    cid = data.get("card_id")
    url = data.get("url", "")
    meta = data.get("meta") or {}
    fetched_n = int(data.get("fetched") or meta.get("fetched") or 0)
    report_md = data.get("report_md_path", "")
    report_path = data.get("report_path", "")
    attach_name = Path(report_path).name if report_path else "report.docx"
    validation = data.get("validation") or {}
    report_ok = validation.get("ok", True)
    moved_done = data.get("moved_to_done", False)

    if moved_done:
        status_line = "📌 Статус: карточка в колонке <b>«Готово»</b>"
    elif not report_ok:
        status_line = (
            f"⚠ Отчёт не прошёл проверку: {_html_escape(str(validation.get('missing', '')))} "
            f"{_html_escape(str(validation.get('incomplete', '')))}. Карточка в WIP."
        )
    elif fetched_n == 0:
        status_line = "⚠ Источники не загрузились — карточка в WIP."
    else:
        status_line = "📌 Карточка обновлена (без переноса в Готово)."

    header = "🎉 <b>Ресёрч завершён</b>\n\n"
    if replay_note:
        header = f"ℹ️ <b>{_html_escape(replay_note)}</b>\n\n" + header

    await bot.send_message(
        chat_id,
        header
        + f"{status_line}\n"
        f"📎 Результат: <code>{_html_escape(attach_name)}</code> (файл ниже)\n"
        f"<b>Тема:</b> {_html_escape(topic[:80])}\n"
        f"<b>Карточка:</b> #{cid}\n"
        f"<b>Ссылка:</b> {url}\n\n"
        f"📊 Источников: {fetched_n} · ⏱ {meta.get('wall_time_s', 0)}s\n"
        f"🔖 trace: <code>{_html_escape((wf.trace_id or '')[:16])}</code>",
    )

    if report_md:
        await asyncio.to_thread(tag_artifact_with_card, Path(report_md).parent, cid)
    art = await asyncio.to_thread(
        find_report, card_id=cid, topic_query=topic, latest=False
    )
    if art:
        await _send_research_artifact(chat_id, art)
    elif report_path and Path(report_path).exists():
        await _send_research_artifact(
            chat_id,
            ReportArtifact(
                topic=topic,
                dir_path=Path(report_md).parent if report_md else Path(report_path).parent,
                docx_path=Path(report_path) if str(report_path).endswith(".docx") else None,
                md_path=Path(report_md) if report_md else None,
                meta=meta,
                card_id=cid,
            ),
        )

    if _macos_notify is not None and cid:
        try:
            await asyncio.to_thread(
                _macos_notify.notify,
                f"Kaiten · #{cid}",
                f"{attach_name}: {topic[:80]}",
            )
        except Exception:
            pass


async def _auto_research_message(
    chat_id: int, task: ExtractedTask, ctx: AgentContext
) -> None:
    topic = task.research_topic or task.title
    idem = research_idempotency_key(chat_id, topic)
    existing = get_job_by_key(idem)
    if existing and existing.get("status") == "running":
        await bot.send_message(
            chat_id,
            f"⏳ Ресёрч по этой теме уже выполняется (job {existing.get('id')}). "
            f"Отмена: /cancel",
        )
        return
    if existing and existing.get("status") == "completed" and existing.get("result"):
        wf = WorkflowResult(
            "success",
            "idempotent replay — job already completed",
            data=existing["result"],
            trace_id=existing.get("trace_id", ""),
        )
        await _deliver_research_outcome(
            chat_id,
            topic,
            wf,
            replay_note=(
                "Этот ресёрч уже был выполнен ранее — отдаю сохранённый результат. "
                "Чтобы запустить заново, измени формулировку темы."
            ),
        )
        return

    status_msg = await bot.send_message(
        chat_id, f"🔄 Стартую ресёрч «{_html_escape(topic[:80])}»…"
    )
    msg_id = status_msg.message_id

    loop = asyncio.get_running_loop()
    progress_q: asyncio.Queue = asyncio.Queue()

    def thread_emit(phase: str, **data) -> None:
        loop.call_soon_threadsafe(progress_q.put_nowait, {"phase": phase, **data})

    progress = TgResearchProgress(
        bot, chat_id, msg_id, topic, html_escape=_html_escape
    )
    consumer = asyncio.create_task(progress.run_consumer(progress_q))

    await bot.send_chat_action(chat_id, ChatAction.TYPING)

    def _run():
        harness = AgentHarness(ctx)
        harness.ctx.metadata["emit_fn"] = thread_emit
        return run_research_workflow(
            harness,
            task,
            topic=topic,
            column_id=_resolve_column_queue(),
            emit=thread_emit,
        )

    try:
        wf = await asyncio.to_thread(_run)
    except Exception as e:
        log.exception("research failed")
        await progress_q.put({"phase": "done"})
        await consumer
        try:
            await bot.edit_message_text(
                f"❌ Ресёрч: {_html_escape(str(e)[:500])}", chat_id, msg_id
            )
        except Exception as edit_err:
            log.warning("research error edit failed: %s", edit_err)
            await bot.send_message(chat_id, f"❌ Ресёрч: {_html_escape(str(e)[:500])}")
        return

    await progress_q.put({"phase": "done"})
    await progress.finalize_pin_before_attach()
    await consumer

    replay = "idempotent replay" in (wf.summary or "").lower()
    await _deliver_research_outcome(
        chat_id,
        topic,
        wf,
        replay_note=(
            "Повторный запуск по той же теме — результат из кэша job."
            if replay
            else None
        ),
    )


async def main() -> None:
    try:
        _BOT_LOCK.acquire()
    except BotLockError as e:
        log.error("%s", e)
        sys.exit(1)
    log.info("Bot starting. Allowed users: %s chats: %s", ALLOWED_USERS, ALLOWED_CHATS)
    log.info(
        "Policy: auto_cards=%s always_preview=%s auto_research=%s progress=%s",
        ENV.get("KAITEN_AGENT_AUTO_CARDS"),
        ENV.get("KAITEN_TASK_ALWAYS_PREVIEW"),
        ENV.get("KAITEN_AGENT_AUTO_RESEARCH", "true"),
        ENV.get("TG_RESEARCH_PROGRESS_MODE", "steps"),
    )
    try:
        await bot.delete_webhook(drop_pending_updates=False)
        await dp.start_polling(bot)
    finally:
        _BOT_LOCK.release()


if __name__ == "__main__":
    asyncio.run(main())
