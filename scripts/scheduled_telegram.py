#!/usr/bin/env python3
"""Scheduled digests → Telegram (Kaiten + fintech/AI CIS via Serper + DeepSeek)."""

from __future__ import annotations

import argparse
import json
import logging
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from board_report import format_weekly_board_telegram  # noqa: E402
from kaiten_api import (  # noqa: E402
    ENV,
    MSK,
    card_assigned_to_user,
    column_labels_for_report,
    filter_cards_for_user,
    list_active_cards,
    list_overdue,
    list_today,
)
from user_directory import get_by_telegram_id  # noqa: E402
from llm import _call_deepseek  # noqa: E402
from research import serper_search  # noqa: E402
from task_views import _format_card_line, render_card_list  # noqa: E402
from telegram_out import broadcast_html, digest_chat_ids, esc  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger("scheduled_telegram")


def _card_lines(cards: list[dict], limit: int = 8) -> list[str]:
    lines: list[str] = []
    for c in cards[:limit]:
        cid = c.get("id", "?")
        title = esc((c.get("title") or "—")[:55])
        due = c.get("due_date") or c.get("due") or ""
        extra = f" · due {esc(str(due)[:10])}" if due else ""
        lines.append(f"• <code>#{cid}</code> {title}{extra}")
    return lines


def weather_digest() -> str:
    """Краткая сводка погоды (wttr.in) для утреннего дайджеста."""
    location = (ENV.get("WEATHER_LOCATION") or "Минск").strip()
    q = urllib.parse.quote(location)
    url = f"https://wttr.in/{q}?format=j1&lang=ru"
    now = datetime.now(MSK).strftime("%d.%m.%Y %H:%M")
    raw_json = ""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "kaiten-task-agent/1.0"})
        with urllib.request.urlopen(req, timeout=20) as resp:
            raw_json = resp.read().decode("utf-8", errors="replace")
    except (urllib.error.URLError, OSError, TimeoutError) as e:
        log.warning("weather urllib: %s, trying curl", e)
        try:
            proc = subprocess.run(
                ["curl", "-fsS", "-A", "kaiten-task-agent", url],
                capture_output=True,
                text=True,
                timeout=25,
                check=False,
            )
            if proc.returncode == 0:
                raw_json = proc.stdout
            else:
                raise RuntimeError(proc.stderr.strip() or f"curl exit {proc.returncode}")
        except (OSError, subprocess.TimeoutExpired, RuntimeError) as e2:
            log.warning("weather fetch: %s", e2)
            return (
                f"<b>🌤 Погода</b> ({now})\n"
                f"<i>Не удалось получить прогноз для {esc(location)}.</i>"
            )
    try:
        data = json.loads(raw_json)
    except json.JSONDecodeError as e:
        log.warning("weather json: %s", e)
        return (
            f"<b>🌤 Погода</b> ({now})\n"
            f"<i>Не удалось разобрать прогноз для {esc(location)}.</i>"
        )

    area = (data.get("nearest_area") or [{}])[0]
    place = (area.get("areaName") or [{}])[0].get("value") or location
    cur = (data.get("current_condition") or [{}])[0]
    temp = cur.get("temp_C", "?")
    feels = cur.get("FeelsLikeC", "?")
    wind = cur.get("windspeedKmph", "?")
    hum = cur.get("humidity", "?")
    desc = (
        (cur.get("lang_ru") or [{}])[0].get("value")
        or (cur.get("weatherDesc") or [{}])[0].get("value")
        or "—"
    )

    today = (data.get("weather") or [{}])[0]
    max_t = today.get("maxtempC", "?")
    min_t = today.get("mintempC", "?")
    rain = today.get("totalSnow_cm") or today.get("hourly", [{}])[0].get("chanceofrain", "?")

    lines = [
        f"<b>🌤 Погода</b> — {esc(place)} ({now})",
        f"Сейчас: <b>{esc(str(temp))}°C</b>, {esc(desc)}",
        f"Ощущается {esc(str(feels))}°C · ветер {esc(str(wind))} км/ч · влажность {esc(str(hum))}%",
        f"Сегодня: от {esc(str(min_t))}° до {esc(str(max_t))}°C",
    ]
    if str(rain) not in ("?", "0.0", "0"):
        lines.append(f"Осадки/дождь: {esc(str(rain))}")
    return "\n".join(lines)


def kaiten_work_digest(
    *,
    kaiten_user_id: int,
    display_name: str | None = None,
) -> str:
    """Просрочено и «в работе» только для карточек этого исполнителя в Kaiten."""
    now = datetime.now(MSK).strftime("%d.%m.%Y %H:%M")
    who = esc((display_name or "").strip() or f"user {kaiten_user_id}")
    parts = [f"<b>📋 Твои задачи Kaiten</b> — {who} ({now})", ""]

    overdue = filter_cards_for_user(list_overdue().get("data") or [], kaiten_user_id)
    if overdue:
        parts.append(render_card_list(overdue, "Просрочено"))
    else:
        parts.append("✅ <b>Просрочено</b>: нет.")

    res = list_active_cards(columns=["wip"], limit=50)
    labels = column_labels_for_report()
    by_label: dict[str, list[dict]] = defaultdict(list)
    if res.get("status") == "success":
        data = res.get("data") or {}
        for group in data.get("groups") or []:
            for card in group.get("cards") or []:
                if not card_assigned_to_user(card, kaiten_user_id):
                    continue
                col_id = card.get("column_id")
                label = labels.get(int(col_id)) if col_id is not None else group.get("title", "В работе")
                by_label[label or "В работе"].append(card)
        for card in data.get("uncategorized") or []:
            if not card_assigned_to_user(card, kaiten_user_id):
                continue
            col_id = card.get("column_id")
            label = labels.get(int(col_id)) if col_id is not None else "Прочие"
            by_label[label].append(card)

    parts.append("")
    if not by_label:
        parts.append("<i>В работе твоих карточек нет.</i>")
    else:
        total = sum(len(v) for v in by_label.values())
        parts.append(f"<b>В работе</b> — {total}")
        for label in sorted(by_label.keys()):
            cards = by_label[label]
            parts.append(f"\n{esc(label)} ({len(cards)})")
            for c in cards[:12]:
                parts.append(_format_card_line(c))
            if len(cards) > 12:
                parts.append(f"<i>… ещё {len(cards) - 12}</i>")

    return "\n".join(parts)


def send_morning_brief(
    *,
    dry_run: bool = False,
    chat_ids: list[int] | None = None,
) -> int:
    """Два сообщения: погода (общая), затем персональный Kaiten по исполнителю."""
    weather = weather_digest()
    targets = list(chat_ids) if chat_ids is not None else digest_chat_ids()
    if not targets:
        log.error("no digest chat ids")
        return 1

    if dry_run:
        print(weather)
        for cid in targets:
            user = get_by_telegram_id(cid)
            kid = user.kaiten_user_id if user else None
            print(f"\n--- chat {cid} kaiten_user={kid} ---\n")
            if kid:
                print(kaiten_work_digest(kaiten_user_id=kid, display_name=user.display_name if user else None))
            else:
                print("<i>нет привязки kaiten_user_id</i>")
        return 0

    ok = 0
    for cid in targets:
        user = get_by_telegram_id(cid)
        if not user or not user.kaiten_user_id:
            log.warning("skip chat %s: no Kaiten user mapping", cid)
            continue
        w_ok = broadcast_html(weather, [cid])
        time.sleep(0.4)
        kaiten = kaiten_work_digest(
            kaiten_user_id=user.kaiten_user_id,
            display_name=user.display_name,
        )
        k_ok = broadcast_html(kaiten, [cid])
        if w_ok and k_ok:
            ok += 1
        else:
            log.warning("morning brief chat %s weather=%s kaiten=%s", cid, w_ok, k_ok)
    log.info("morning brief sent to %s/%s chats", ok, len(targets))
    return 0 if ok == len(targets) else 1


def kaiten_digest(*, mode: str) -> str:
    overdue = (list_overdue().get("data") or [])
    today = (list_today().get("data") or [])
    now = datetime.now(MSK).strftime("%d.%m.%Y %H:%M")

    if mode == "morning":
        title = "📋 Kaiten · утро"
    else:
        title = "📋 Kaiten · вечер"

    parts = [
        f"<b>{title}</b> ({now})",
        f"Просрочено: <b>{len(overdue)}</b> · На сегодня: <b>{len(today)}</b>",
    ]
    if overdue:
        parts.append("\n<b>Просрочено</b>")
        parts.extend(_card_lines(overdue, 6))
    if today:
        parts.append("\n<b>Сегодня</b>")
        parts.extend(_card_lines(today, 6))
    if not overdue and not today:
        parts.append("\n<i>Нет просроченных и задач на сегодня.</i>")
    return "\n".join(parts)


FINTECH_QUERIES = [
    "финтех СНГ искусственный интеллект новости 2026",
    "AI banking Russia Kazakhstan fintech regulation",
    "генеративный ИИ банки Россия Беларусь Казахстан",
    "open banking machine learning CIS startup funding",
]


def _collect_serper_snippets() -> str:
    blocks: list[str] = []
    for q in FINTECH_QUERIES:
        try:
            hits = serper_search(q, limit=3)
        except Exception as e:
            log.warning("serper %r: %s", q, e)
            continue
        if not hits:
            continue
        lines = [f"Query: {q}"]
        for h in hits:
            lines.append(f"- {h.get('title', '')} | {h.get('url', '')}")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def _fintech_fallback_bullets(raw: str) -> str:
    lines = [f"<b>🤖 Финтех × ИИ · СНГ</b> ({datetime.now(MSK).strftime('%d.%m.%Y')})", "<i>Кратко по Serper (без синтеза LLM):</i>"]
    n = 0
    for block in raw.split("\n\n"):
        for line in block.splitlines():
            if line.startswith("- "):
                title, _, url = line[2:].partition(" | ")
                lines.append(f"• {esc(title[:80])} — <a href=\"{esc(url)}\">link</a>")
                n += 1
                if n >= 10:
                    break
        if n >= 10:
            break
    lines.append(f"\nИсточники: {n} ссылок")
    return "\n".join(lines)


def fintech_ai_digest() -> str:
    raw = _collect_serper_snippets()
    if not raw.strip():
        return (
            "<b>🤖 Финтех × ИИ · СНГ</b>\n"
            "<i>Поиск не вернул результатов (Serper). Проверь SERPER_API_KEY.</i>"
        )

    now = datetime.now(MSK).strftime("%d.%m.%Y")
    system = (
        "Ты редактор краткого дайджеста для Telegram (HTML). "
        "Пиши по-русски, 8–12 пунктов максимум, только факты из источников. "
        "Формат: буллеты • с <b>темой</b> и 1 предложением. "
        "В конце строка «Источники: N ссылок». Не выдумывай компании и цифры. "
        "Ответ только в поле content, без рассуждений."
    )
    user = (
        f"Дата: {now}. Тема: финтех и ИИ на рынке СНГ (РФ, KZ, BY, UZ и др.).\n\n"
        f"Сырые результаты поиска:\n{raw[:12000]}"
    )
    model = "deepseek-chat"
    text = ""
    try:
        resp = _call_deepseek(
            [{"role": "system", "content": system}, {"role": "user", "content": user}],
            model=model,
            temperature=0.25,
            max_tokens=1400,
            json_mode=False,
            timeout=90,
        )
        msg = resp.get("choices", [{}])[0].get("message", {}) or {}
        text = (msg.get("content") or msg.get("reasoning_content") or "").strip()
    except Exception as e:
        log.exception("deepseek digest")
        return _fintech_fallback_bullets(raw) + f"\n\n<i>LLM: {esc(str(e))}</i>"

    if not text or len(text) < 40:
        return _fintech_fallback_bullets(raw)
    if not text.startswith("<b>"):
        text = f"<b>🤖 Финтех × ИИ · СНГ</b> ({now})\n\n" + text
    return text


def kaiten_weekly_board() -> str:
    """Еженедельный отчёт по доске: поток, %, узкие места, рекомендации."""
    return format_weekly_board_telegram()


def main() -> int:
    p = argparse.ArgumentParser(description="Send scheduled digests to Telegram")
    p.add_argument(
        "job",
        choices=[
            "morning-brief",
            "kaiten-morning",
            "kaiten-evening",
            "fintech-ai",
            "kaiten-weekly",
        ],
    )
    p.add_argument("--dry-run", action="store_true", help="Print message, do not send")
    p.add_argument(
        "--chat-id",
        type=int,
        action="append",
        dest="chat_ids",
        help="Отправить только этим Telegram chat_id (тест)",
    )
    args = p.parse_args()

    if args.job in ("morning-brief", "kaiten-morning"):
        return send_morning_brief(dry_run=args.dry_run, chat_ids=args.chat_ids)
    elif args.job == "kaiten-evening":
        msg = kaiten_digest(mode="evening")
    elif args.job == "fintech-ai":
        msg = fintech_ai_digest()
    else:
        msg = kaiten_weekly_board()

    if args.dry_run:
        print(msg)
        return 0

    sent = broadcast_html(msg, silent=False)
    log.info("broadcast to %s chats, ok=%s", "digest", sent)
    return 0 if sent else 1


if __name__ == "__main__":
    sys.exit(main())
