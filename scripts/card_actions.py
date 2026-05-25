"""Parse and apply Kaiten card updates/deletes from natural language (TG / CLI)."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from kaiten_api import ENV, envelope, parse_iso_date  # noqa: E402

try:
    from agent.harness import AgentHarness  # noqa: E402
    from agent.types import AgentContext  # noqa: E402
except ImportError:
    AgentHarness = None  # type: ignore
    AgentContext = None  # type: ignore

try:
    from llm import _call_deepseek  # noqa: E402
except ImportError:
    _call_deepseek = None


@dataclass
class CardAction:
    action: str = "none"  # delete|set_priority|set_due|move_column|set_title|set_description|close|none
    card_id: int | None = None
    priority: str | None = None
    due_date_iso: str | None = None
    due_clear: bool = False
    column_target: str | None = None  # очередь|wip|готово|написание
    title: str | None = None
    description_md: str | None = None
    confidence: float = 0.0
    raw: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "card_id": self.card_id,
            "priority": self.priority,
            "due_date_iso": self.due_date_iso,
            "due_clear": self.due_clear,
            "column_target": self.column_target,
            "title": self.title,
            "description_md": self.description_md,
            "confidence": self.confidence,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "CardAction":
        return cls(
            action=str(d.get("action", "none")),
            card_id=int(d["card_id"]) if d.get("card_id") else None,
            priority=d.get("priority"),
            due_date_iso=d.get("due_date_iso"),
            due_clear=bool(d.get("due_clear")),
            column_target=d.get("column_target"),
            title=d.get("title"),
            description_md=d.get("description_md"),
            confidence=float(d.get("confidence", 0)),
        )


UPDATE_PROMPT = """Ты извлекаешь действие над существующей карточкой Kaiten из сообщения на русском.
Верни только JSON.

Поля:
- action: одно из:
  "delete" — удалить карточку
  "set_priority" — сменить приоритет (P1/P2/P3, срочно= P1)
  "set_due" — поставить или сменить дедлайн (due_date_iso YYYY-MM-DD; сегодня/завтра)
  "clear_due" — убрать дедлайн
  "move_column" — перенести в колонку (column_target)
  "set_title" — переименовать (title — новый заголовок без эмодзи)
  "set_description" — заменить описание (description_md)
  "close" — закрыть / в готово
  "none" — не про изменение карточки
- card_id: число из #64942139 или "карточка 64942139", иначе null
- priority: P1|P2|P3 или null
- due_date_iso: YYYY-MM-DD или null
- due_clear: true если убрать дедлайн
- column_target: "очередь"|"wip"|"готово"|"написание кода" или null
- title, description_md: строки или null
- confidence: 0..1

column_target: очередь/inbox=очередь, в работе/wip/код=wip, готово/done/закрыть=готово.

Пример: "сдвинь #64942139 на завтра, приоритет P2"
{"action":"set_due","card_id":64942139,"priority":"P2","due_date_iso":"2026-05-19","due_clear":false,
 "column_target":null,"title":null,"description_md":null,"confidence":0.9}
"""


def parse_card_id(text: str) -> int | None:
    m = re.search(
        r"kaiten(?:\.[\w-]+)?/(?:c/)?(\d{5,10})\b",
        text,
        re.I,
    )
    if m:
        return int(m.group(1))
    m = re.search(r"(?:#|карточк[аеи]\s*|card\s*)(\d{5,10})\b", text, re.I)
    if m:
        return int(m.group(1))
    m = re.search(r"\b(\d{7,10})\b", text)
    return int(m.group(1)) if m else None


_EXPLICIT_CARD_CMD = re.compile(
    r"(?:"
    r"\b(?:удали|удалить|снеси|убери\s+карточку)\b|"
    r"\b(?:закрой|закрыть|перенеси\s+в\s+готово)\b|"
    r"\b(?:перенеси|сдвинь|поставь|перемести)\b(?:\s+\S+){0,6}?\s+"
    r"(?:в|на)\s+(?:очеред|wip|готово|работ|написан|колонк)|"
    r"\b(?:перенеси|сдвинь|поставь|перемести)\s+(?:в|на)\b|"
    r"(?:^|\s)(?:wip|очередь)(?:\s|$|[,.!?])|"
    r"\b(?:приоритет|срочн|P[123])\b|"
    r"\b(?:убери|сними|без)\s+дедлайн|"
    r"\b(?:дедлайн|due|срок)\b|"
    r"\b(?:завтра|сегодня)\b.*(?:дедлайн|срок|due)|"
    r"\b(?:переименуй|описание|заголовок)\b"
    r")",
    re.I | re.U,
)

_STATUS_NARRATIVE = re.compile(
    r"(?:"
    r"\b(?:отдал[иа]?|передал[иа]?|ушл[ао]|попал[ао]?|перевел[иа]?|отправил[иа]?)\b"
    r".{0,40}?(?:работ|wip|очеред|готово|написан)|"
    r"\bв\s+работ[уе]\b|"
    r"\b(?:на\s+)?(?:написани[ея]|ревью)\b|"
    r"\b(?:в|на)\s+(?:очеред|wip|готово)\b"
    r")",
    re.I | re.U,
)


def is_explicit_card_command(text: str) -> bool:
    """True when user clearly requests a card edit (not status narrative only)."""
    return bool(_EXPLICIT_CARD_CMD.search(text or ""))


def has_status_narrative(text: str, card_id: int | None) -> bool:
    """Status/column story with card id but no explicit edit imperative."""
    if not card_id:
        return False
    return bool(_STATUS_NARRATIVE.search(text or ""))


def strip_card_references(text: str, card_id: int | None = None) -> str:
    """Remove card id, Kaiten URLs, and markdown links — leave user narrative."""
    s = text or ""
    s = re.sub(
        r"https?://[^\s)]*kaiten[^\s)]*/(?:c/)?\d{5,10}\b",
        "",
        s,
        flags=re.I,
    )
    s = re.sub(r"\(https?://[^\)]+\)", "", s)
    s = re.sub(r"#\d{5,10}\b", "", s)
    if card_id:
        s = re.sub(rf"\b{card_id}\b", "", s, count=1)
    s = re.sub(r"\b(?:карточк[аеи]|card)\s*", "", s, flags=re.I)
    s = re.sub(r"\s+", " ", s).strip(" ,.—-")
    return s


def should_offer_card_choice(text: str, card_id: int | None) -> bool:
    """Card id + free-text update without explicit edit command → TG buttons (U1)."""
    if not card_id or is_explicit_card_command(text):
        return False
    return len(strip_card_references(text, card_id)) >= 3


def _regex_guess_action(text: str) -> CardAction | None:
    low = text.lower()
    cid = parse_card_id(text)
    if not cid:
        return None
    if re.search(r"\b(удали|удалить|снеси|убери\s+карточку)\b", low):
        return CardAction(action="delete", card_id=cid, confidence=0.85)

    act = CardAction(card_id=cid, confidence=0.8)
    primary = "none"

    if re.search(r"\b(закрой|закрыть|в\s+готово|перенеси\s+в\s+готово)\b", low):
        act.action = "close"
        act.column_target = "готово"
        primary = "close"
    elif re.search(
        r"\b(?:перенеси|сдвинь|поставь|перемести)\b.*(?:очеред|wip|готово|написан|колонк|работ)",
        low,
    ) or re.search(r"(?:^|\s)(?:wip|очередь)(?:\s|$|[,.!?])", low):
        act.action = "move_column"
        if "очеред" in low:
            act.column_target = "очередь"
        elif re.search(r"\b(?:wip|написан)\b", low) or re.search(
            r"\b(?:перенеси|сдвинь|поставь|перемести)\b.*\bработ", low
        ):
            act.column_target = "wip"
        elif "готово" in low:
            act.column_target = "готово"
        primary = "move_column"

    pr_m = re.search(r"\b(P[123])\b", text, re.I)
    if pr_m or re.search(r"\b(приоритет|срочн)\b", low):
        if pr_m:
            act.priority = pr_m.group(1).upper()
            if primary == "none":
                act.action = "set_priority"
                primary = "set_priority"

    if re.search(r"\b(убери|сними|без)\s+дедлайн", low):
        act.due_clear = True
        if primary == "none":
            act.action = "clear_due"
            primary = "clear_due"
    elif re.search(r"\b(завтра|сегодня|дедлайн|due|срок|сдвинь|перенеси)\b", low):
        if "завтра" in low:
            act.due_date_iso = parse_iso_date("завтра").strftime("%Y-%m-%d")
        elif "сегодня" in low:
            act.due_date_iso = parse_iso_date("сегодня").strftime("%Y-%m-%d")
        if act.due_date_iso and primary == "none":
            act.action = "set_due"
            primary = "set_due"

    if primary == "none":
        return None
    if act.action == "none":
        act.action = primary
    return act


def extract_card_action(user_text: str) -> CardAction:
    guessed = _regex_guess_action(user_text)
    if guessed and guessed.confidence >= 0.8:
        if guessed.due_date_iso in {"сегодня", "завтра"}:
            guessed.due_date_iso = parse_iso_date(guessed.due_date_iso).strftime("%Y-%m-%d")
        return guessed

    if _call_deepseek is None:
        return guessed or CardAction(card_id=parse_card_id(user_text))

    messages = [
        {"role": "system", "content": UPDATE_PROMPT},
        {"role": "user", "content": f"Сообщение:\n{user_text}\n\nJSON:"},
    ]
    try:
        resp = _call_deepseek(messages, temperature=0.1, max_tokens=600)
        data = json.loads(resp["choices"][0]["message"]["content"])
    except Exception:
        return guessed or CardAction(card_id=parse_card_id(user_text), action="none", confidence=0)

    act = CardAction(
        action=str(data.get("action", "none")),
        card_id=int(data["card_id"]) if data.get("card_id") else parse_card_id(user_text),
        priority=(str(data["priority"]).upper() if data.get("priority") else None),
        due_date_iso=data.get("due_date_iso"),
        due_clear=bool(data.get("due_clear")),
        column_target=data.get("column_target"),
        title=data.get("title"),
        description_md=data.get("description_md"),
        confidence=float(data.get("confidence", 0)),
        raw=data,
    )
    if act.priority and act.priority not in {"P1", "P2", "P3"}:
        act.priority = None
    return act


def column_id_for_target(target: str | None) -> tuple[int | None, str]:
    if not target:
        return None, "?"
    from kaiten_api import board_column_config

    t = target.lower().strip()
    aliases = {
        "очередь": "queue",
        "inbox": "queue",
        "очереди": "queue",
        "wip": "wip",
        "в работе": "wip",
        "написание кода": "wip",
        "написание": "wip",
        "код": "wip",
        "ревью": "wip",
        "готово": "done",
        "done": "done",
        "закрыто": "done",
    }
    cols = board_column_config()
    for key, bucket in aliases.items():
        if key in t and bucket in cols:
            return int(cols[bucket]["column_id"]), key
    if t.isdigit():
        return int(t), f"column {t}"
    return None, target


def _column_title(col_id: int | None) -> str:
    if not col_id:
        return "?"
    from kaiten_api import board_column_config

    for meta in board_column_config().values():
        ids = meta.get("column_ids") or [meta.get("column_id")]
        if col_id in ids:
            return str(meta.get("title") or "?")
    return f"col#{col_id}"


def _ensure_harness(harness: AgentHarness | None) -> AgentHarness:
    if harness is not None:
        return harness
    from agent.harness import AgentHarness as _H
    from agent.types import AgentContext as _C

    return _H(_C(channel="cli"))


def _tool_get_card(harness: AgentHarness, card_id: int) -> dict:
    return harness.execute_tool("get_card", {"card_id": card_id})


def build_action_plan(action: CardAction, harness: AgentHarness | None = None) -> dict:
    """Preview plan: human summary + list of operations."""
    if not action.card_id:
        return {"ok": False, "error": "Не указан номер карточки (#64942139)."}
    h = _ensure_harness(harness)
    card_res = _tool_get_card(h, action.card_id)
    if card_res.get("status") != "success":
        return {"ok": False, "error": f"Карточка #{action.card_id} не найдена."}
    card = card_res["data"]
    ops: list[str] = []
    patch_preview: dict[str, Any] = {}

    if action.action == "delete":
        ops.append(f"🗑 Удалить карточку #{action.card_id} «{card.get('title', '')[:60]}»")
    elif action.action == "none":
        return {"ok": False, "error": "Не удалось понять действие. Пример: «сдвинь #123 на завтра P2»"}
    else:
        if action.action == "close":
            action.column_target = action.column_target or "готово"
        if action.priority:
            ops.append(f"Приоритет → {action.priority}" + (" (asap)" if action.priority == "P1" else ""))
            patch_preview["priority"] = action.priority
        if action.due_clear or action.action == "clear_due":
            ops.append("Убрать дедлайн")
            patch_preview["due_date"] = None
        elif action.due_date_iso or action.action == "set_due":
            ops.append(f"Дедлайн → {action.due_date_iso}")
            patch_preview["due_date"] = action.due_date_iso
        if action.column_target or action.action in ("move_column", "close"):
            col_id, label = column_id_for_target(action.column_target or "готово")
            if not col_id:
                return {"ok": False, "error": f"Не понял колонку: {action.column_target}"}
            current_col = card.get("column_id")
            if current_col is not None and int(current_col) == int(col_id):
                title = _column_title(col_id)
                return {
                    "ok": True,
                    "noop": True,
                    "message": f"Карточка #{action.card_id} уже в колонке «{title}».",
                    "card_id": action.card_id,
                    "card_title": card.get("title", ""),
                    "card_url": f"{ENV.get('KAITEN_BASE_URL', '').rstrip('/')}/{action.card_id}",
                    "column_now": title,
                    "ops": [],
                    "patch_preview": {},
                    "action": action.action,
                }
            ops.append(f"Колонка → «{_column_title(col_id)}» ({label})")
            patch_preview["column_id"] = col_id
        if action.title or action.action == "set_title":
            t = action.title or ""
            if t:
                ops.append(f"Заголовок → «{t[:80]}»")
                patch_preview["title"] = t
        if action.description_md or action.action == "set_description":
            if action.description_md:
                ops.append("Заменить описание (markdown)")
                patch_preview["description"] = action.description_md[:8000]

    if not ops:
        return {"ok": False, "error": "Нет операций для применения."}

    return {
        "ok": True,
        "card_id": action.card_id,
        "card_title": card.get("title", ""),
        "card_url": f"{ENV.get('KAITEN_BASE_URL', '').rstrip('/')}/{action.card_id}",
        "column_now": _column_title(card.get("column_id")),
        "ops": ops,
        "patch_preview": patch_preview,
        "action": action.action,
    }


def apply_card_action(
    action: CardAction,
    commit: bool = True,
    *,
    harness: AgentHarness | None = None,
    approved: bool = False,
) -> dict:
    """Execute CardAction through AgentHarness (policy + audit)."""
    h = _ensure_harness(harness)
    plan = build_action_plan(action, h)
    if not plan.get("ok"):
        return envelope("error", plan.get("error", "invalid"), error_type="invalid_arguments")

    cid = plan["card_id"]
    approved = approved or commit

    if action.action == "delete":
        return h.execute_tool("confirm_delete", {"card_id": cid}, commit=commit, approved=approved)

    results: list[dict] = []

    if plan["patch_preview"].get("column_id"):
        col = plan["patch_preview"].get("column_id")
        if col:
            results.append(
                h.execute_tool(
                    "move_card",
                    {"card_id": cid, "column_id": int(col)},
                    commit=commit,
                    approved=approved,
                )
            )

    if action.priority or plan["patch_preview"].get("priority"):
        pr = action.priority or plan["patch_preview"]["priority"]
        results.append(
            h.execute_tool(
                "update_card_priority",
                {"card_id": cid, "priority": pr},
                commit=commit,
                approved=approved,
            )
        )

    if "due_date" in plan["patch_preview"]:
        due = plan["patch_preview"]["due_date"]
        if due is None:
            results.append(
                h.execute_tool(
                    "update_card_due",
                    {"card_id": cid, "clear": True},
                    commit=commit,
                    approved=approved,
                )
            )
        else:
            results.append(
                h.execute_tool(
                    "update_card_due",
                    {"card_id": cid, "due_date": due},
                    commit=commit,
                    approved=approved,
                )
            )

    patch: dict[str, Any] = {}
    if plan["patch_preview"].get("title"):
        patch["title"] = plan["patch_preview"]["title"]
    if plan["patch_preview"].get("description"):
        patch["description"] = plan["patch_preview"]["description"]

    if patch:
        results.append(
            h.execute_tool(
                "patch_card",
                {"card_id": cid, "patch": patch},
                commit=commit,
                approved=approved,
            )
        )

    failed = [r for r in results if r.get("status") not in ("success", "approval_required")]
    if failed:
        return failed[0]
    return envelope(
        "success",
        f"обновлено #{cid}: " + "; ".join(plan["ops"]),
        {"card_id": cid, "url": plan["card_url"], "results": [r.get("summary") for r in results]},
    )
