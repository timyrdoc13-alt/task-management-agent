"""Telegram users ↔ Kaiten assignees (config/telegram_users.yaml)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

_CONFIG = Path(__file__).resolve().parent.parent / "config" / "telegram_users.yaml"


@dataclass(frozen=True)
class KaitenUser:
    kaiten_user_id: int
    display_name: str
    username: str | None = None
    telegram_id: int | None = None
    role: str = "editor"
    aliases: tuple[str, ...] = ()

    @property
    def can_use_bot(self) -> bool:
        return self.telegram_id is not None and self.role == "editor"


@dataclass
class AssigneeResolution:
    kaiten_user_id: int
    display_name: str
    source: str  # sender | text | owner_hint
    ambiguous: list[str] | None = None


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip().lower())


@lru_cache(maxsize=1)
def load_users() -> list[KaitenUser]:
    if not _CONFIG.exists():
        return []
    raw = yaml.safe_load(_CONFIG.read_text(encoding="utf-8")) or {}
    out: list[KaitenUser] = []
    for row in raw.get("users") or []:
        if not row.get("kaiten_user_id"):
            continue
        aliases = tuple(_norm(a) for a in (row.get("aliases") or []) if a)
        out.append(
            KaitenUser(
                kaiten_user_id=int(row["kaiten_user_id"]),
                display_name=str(row.get("display_name") or "").strip(),
                username=(row.get("username") or None),
                telegram_id=int(row["telegram_id"]) if row.get("telegram_id") else None,
                role=str(row.get("role") or "editor"),
                aliases=aliases,
            )
        )
    return out


def telegram_user_ids_with_bot_access() -> set[int]:
    return {u.telegram_id for u in load_users() if u.telegram_id and u.can_use_bot}


def get_by_telegram_id(telegram_id: int | None) -> KaitenUser | None:
    if not telegram_id:
        return None
    for u in load_users():
        if u.telegram_id == telegram_id:
            return u
    return None


def get_by_kaiten_user_id(kaiten_user_id: int | None) -> KaitenUser | None:
    if not kaiten_user_id:
        return None
    for u in load_users():
        if u.kaiten_user_id == int(kaiten_user_id):
            return u
    return None


def _word_match(part: str, t: str, words: set[str]) -> bool:
    """Match name/alias as a token — avoid «ян» inside «яндекс»."""
    if part in words:
        return True
    return bool(
        re.search(
            rf"(?<![а-яёa-z0-9_]){re.escape(part)}(?![а-яёa-z0-9_])",
            t,
            re.I,
        )
    )


_SELF_ASSIGNEE = re.compile(
    r"(?:"
    r"на\s+меня|для\s+меня|мне\b|себе\b|"
    r"задач[ауюыё]?\s+на\s+меня|"
    r"создай(?:те)?\s+(?:задач[ауюыё]?\s+)?на\s+меня"
    r")",
    re.I | re.U,
)


def _match_tokens(text: str) -> list[KaitenUser]:
    t = _norm(text)
    if len(t) < 2:
        return []
    words = set(re.findall(r"[а-яёa-z0-9_]+", t, re.I))
    hits: list[KaitenUser] = []
    for u in load_users():
        parts = [
            _norm(u.display_name),
            _norm(u.username or ""),
            *u.aliases,
        ]
        for part in parts:
            if len(part) < 2:
                continue
            if _word_match(part, t, words):
                hits.append(u)
                break
    # dedupe
    seen: set[int] = set()
    unique: list[KaitenUser] = []
    for u in hits:
        if u.kaiten_user_id not in seen:
            seen.add(u.kaiten_user_id)
            unique.append(u)
    return unique


def resolve_assignee(
    text: str,
    *,
    telegram_user_id: int | None,
    owner_hint: str | None = None,
) -> AssigneeResolution | None:
    """Default: sender. Override if name/alias or owner_hint matches another user."""
    sender = get_by_telegram_id(telegram_user_id)
    default = sender
    if not default and not load_users():
        return None
    if not default:
        default = None

    combined = f"{text} {(owner_hint or '')}".strip()
    if default and _SELF_ASSIGNEE.search(combined):
        named = [u for u in _match_tokens(text) if u.kaiten_user_id != default.kaiten_user_id]
        if owner_hint:
            named.extend(
                u
                for u in _match_tokens(owner_hint)
                if u.kaiten_user_id != default.kaiten_user_id
            )
        if not named:
            return AssigneeResolution(
                default.kaiten_user_id,
                default.display_name,
                "sender",
            )

    candidates: list[KaitenUser] = []
    if owner_hint:
        candidates.extend(_match_tokens(owner_hint))
    candidates.extend(_match_tokens(text))

    if not candidates:
        if default:
            return AssigneeResolution(
                default.kaiten_user_id,
                default.display_name,
                "sender",
            )
        return None

    if len(candidates) == 1:
        u = candidates[0]
        if default and u.kaiten_user_id == default.kaiten_user_id:
            return AssigneeResolution(u.kaiten_user_id, u.display_name, "sender")
        src = "owner_hint" if owner_hint and _match_tokens(owner_hint) else "text"
        return AssigneeResolution(u.kaiten_user_id, u.display_name, src)

    names = [c.display_name for c in candidates]
    pick = candidates[0]
    if default and any(c.kaiten_user_id == default.kaiten_user_id for c in candidates):
        pick = default
        return AssigneeResolution(pick.kaiten_user_id, pick.display_name, "sender")
    return AssigneeResolution(
        pick.kaiten_user_id,
        pick.display_name,
        "text",
        ambiguous=names,
    )


def resolve_for_context(
    ctx: Any,
    task: Any,
) -> AssigneeResolution | None:
    raw = getattr(task, "raw", None) or {}
    text = (raw.get("user_text") or getattr(task, "title", "") or "").strip()
    return resolve_assignee(
        text,
        telegram_user_id=getattr(ctx, "user_id", None),
        owner_hint=getattr(task, "owner_hint", None),
    )
