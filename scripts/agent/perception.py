"""Fast-path perception (regex) before LLM — cheap, deterministic routing."""

from __future__ import annotations

import re

from llm import ExtractedTask

# «Какие задачи сейчас» — не путать с create/research
_LIST_ACTIVE = re.compile(
    r"(?:"
    r"какие\s+(?:есть\s+)?(?:задач|карточк|тикет|дела)"
    r"|что\s+(?:у\s+меня\s+)?(?:сейчас|в\s+работе|на\s+доске|в\s+очереди|открыто)"
    r"|(?:покажи|выведи|список|перечисли|дай)\s+.{0,20}(?:задач|карточк|тикет)"
    r"|(?:текущ|активн).{0,12}(?:задач|карточк)"
    r"|что\s+в\s+kaiten"
    r"|мои\s+задачи"
    r")",
    re.I | re.U,
)

_LIST_TODAY = re.compile(
    r"(?:что\s+)?(?:на\s+)?сегодня|сегодняшн|due\s+today|дедлайн\s+сегодня",
    re.I,
)

_LIST_DONE = re.compile(
    r"(?:"
    r"что\s+готово"
    r"|(?:что|покажи|список|какие).{0,24}(?:готово|заверш|сделан|закрыт|выполнен)"
    r"|(?:готово|завершённые|завершенные|сделанные)(?:\s+задач|\s+карточк)?"
    r"|колонк[аи]\s+готово"
    r"|^/done\b"
    r")",
    re.I,
)

_LIST_WIP = re.compile(
    r"что\s+в\s+работе|в\s+работе\s+(?:сейчас|задач)|^/wip\b",
    re.I,
)

_LIST_QUEUE = re.compile(
    r"что\s+в\s+очереди|очередь\s+задач|^/queue\b",
    re.I,
)

_LIST_OVERDUE = re.compile(
    r"просроч|опоздал|overdue|что\s+горит|горящ",
    re.I,
)

_LIST_DIGEST = re.compile(
    r"дайджест|сводк|что\s+срочно|итог\s+по\s+задач",
    re.I,
)

_LIST_ALL = re.compile(
    r"все\s+задач|вся\s+доск|полный\s+список|включая\s+готово",
    re.I,
)

# Не срабатывать на «поставь задачу …»
_CREATE_HINT = re.compile(
    r"(?:поставь|создай|заведи|добавь|нужно\s+сделать|сделай\s+задач)",
    re.I,
)

_RESEARCH_HINT = re.compile(
    r"(?:изучи|исследуй|найди\s+инф|ресёрч|research|собери\s+справк)",
    re.I,
)

_BOARD_REPORT = re.compile(
    r"(?:"
    r"(?:отч[её]т|статистик|итог).{0,40}(?:по\s+задач|за\s+месяц|за\s+неделю|за\s+мес)"
    r"|(?:отч[её]т|итоги?)\s+за\s+\w+"
    r"|сколько\s+(?:сделали|заверш|готово|новых|на\s+стопе|в\s+работе)"
    r"|^/report\b"
    r")",
    re.I,
)

_ARTIFACT = re.compile(
    r"(?:"
    r"(?:пришли|скинь|отправь|дай|покажи)\s+.{0,25}(?:файл|документ|docx|результат|справк)"
    r"|результат\s+(?:по|работы|#|№)"
    r"|^/file\b"
    r"|report\.docx"
    r")",
    re.I,
)


def fast_classify_list(user_text: str) -> ExtractedTask | None:
    """Return list intent without LLM when phrasing is unambiguous."""
    text = (user_text or "").strip()
    if len(text) < 4:
        return None
    if _CREATE_HINT.search(text) or _RESEARCH_HINT.search(text):
        return None

    if _ARTIFACT.search(text):
        from report_delivery import parse_artifact_request

        cid, topic_hint, latest = parse_artifact_request(text)
        return ExtractedTask(
            intent="artifact",
            title="Файл ресёрча",
            icon="📎",
            description_md="",
            list_scope="latest" if latest else None,
            confidence=0.92,
            raw={
                "fast_path": "perception.artifact",
                "card_id": cid,
                "topic_hint": topic_hint,
            },
        )

    if _BOARD_REPORT.search(text) or re.fullmatch(
        r"отч[её]т(?:\s+по\s+задачам?)?", text, re.I
    ):
        from board_report import parse_board_report_period

        period = parse_board_report_period(text)
        return ExtractedTask(
            intent="report",
            title="Отчёт по задачам",
            icon="📊",
            description_md="",
            confidence=0.93,
            raw={
                "fast_path": "perception.board_report",
                "period_label": period.label,
            },
        )

    scope = None
    confidence = 0.88

    if _LIST_DONE.search(text):
        scope = "done"
        confidence = 0.94
    elif _LIST_WIP.search(text):
        scope = "wip"
    elif _LIST_QUEUE.search(text):
        scope = "queue"
    elif _LIST_OVERDUE.search(text):
        scope = "overdue"
    elif _LIST_TODAY.search(text) and not _LIST_ACTIVE.search(text):
        scope = "today"
    elif _LIST_DIGEST.search(text):
        scope = "digest"
    elif _LIST_ALL.search(text):
        scope = "all"
    elif _LIST_ACTIVE.search(text):
        scope = "active"
    elif re.search(r"^/tasks\b", text, re.I):
        scope = "active"
        confidence = 0.99
    elif re.search(r"^/board\b", text, re.I):
        scope = "all"
        confidence = 0.99

    if not scope:
        return None

    return ExtractedTask(
        intent="list",
        title="Список задач",
        icon="📋",
        description_md="",
        list_scope=scope,
        confidence=confidence,
        raw={"fast_path": "perception.list", "list_scope": scope},
    )
