from admin_notify import (
    format_assignee_notice,
    format_card_created_notice,
    format_macos_body,
    notify_assignee_enabled,
    skip_self_notify,
)
from agent.types import AgentContext
from llm import ExtractedTask


def test_format_admin_notice():
    task = ExtractedTask(
        title="Проверить акты",
        priority="P1",
        due_date_iso="2026-05-25",
    )
    ctx = AgentContext(channel="telegram", user_id=458002471)
    text = format_card_created_notice(
        task,
        ctx,
        {"card_id": 123, "url": "https://x/123", "assignee": "Ян Подкопаев"},
    )
    assert "Проверить акты" in text
    assert "Ян" in text
    assert "P1" in text
    assert "2026-05-25" in text
    assert "#123" in text


def test_macos_body_short():
    task = ExtractedTask(title="Задача", priority="P2")
    ctx = AgentContext(channel="telegram", user_id=522378116)
    body = format_macos_body(task, ctx, {"card_id": 99})
    assert "Задача" in body
    assert "#99" in body


def test_skip_self_default():
    assert skip_self_notify() is True


def test_format_assignee_notice():
    task = ExtractedTask(
        title="Проверить API",
        priority="P2",
        due_date_iso="2026-05-20",
    )
    ctx = AgentContext(channel="telegram", user_id=228378111)
    text = format_assignee_notice(
        task,
        ctx,
        {"card_id": 64999, "url": "https://timyrdoc.kaiten.ru/64999"},
    )
    assert "поставил" in text.lower()
    assert "Проверить API" in text
    assert "#64999" in text
    assert "timyrdoc" in text


def test_notify_assignee_enabled_default():
    assert notify_assignee_enabled() is True
