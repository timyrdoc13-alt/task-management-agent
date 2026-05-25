import os

from agent.policy import (
    can_auto_create_card,
    can_auto_research,
    create_needs_preview,
    task_always_preview_enabled,
)
from agent.types import AgentContext
from llm import ExtractedTask


def _task(**kw):
    base = dict(
        intent="create",
        title="t",
        icon="📝",
        description_md="",
        priority="P2",
        due_date_iso=None,
        owner_hint=None,
        tags=[],
        research_topic=None,
        sensitive_markers=[],
        confidence=0.9,
        can_self_execute=True,
    )
    base.update(kw)
    return ExtractedTask(**base)


def test_auto_cards_respects_env(monkeypatch):
    monkeypatch.setenv("KAITEN_AGENT_AUTO_CARDS", "false")
    from kaiten_api import ENV

    ENV["KAITEN_AGENT_AUTO_CARDS"] = "false"
    ctx = AgentContext(channel="telegram", chat_id=1)
    assert not can_auto_create_card(_task(), ctx)


def test_always_preview_overrides_auto_cards(monkeypatch):
    from kaiten_api import ENV

    ENV["KAITEN_AGENT_AUTO_CARDS"] = "true"
    ENV["KAITEN_TASK_ALWAYS_PREVIEW"] = "true"
    assert task_always_preview_enabled()
    ctx = AgentContext(channel="telegram", chat_id=1)
    assert create_needs_preview(_task(confidence=0.99), ctx)


def test_auto_research_requires_flag(monkeypatch):
    from kaiten_api import ENV

    ENV["KAITEN_AGENT_AUTO_RESEARCH"] = "false"
    ENV["AUTO_RESEARCH_ENABLED"] = "true"
    ctx = AgentContext(channel="telegram", chat_id=1)
    assert not can_auto_research(_task(intent="research"), ctx)
