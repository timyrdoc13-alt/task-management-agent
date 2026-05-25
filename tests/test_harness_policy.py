from agent.harness import AgentHarness
from agent.types import AgentContext


def test_cli_write_requires_approved_on_commit():
    h = AgentHarness(AgentContext(channel="cli"))
    r = h.execute_tool(
        "move_card",
        {"card_id": 1, "column_id": 2},
        commit=True,
        approved=False,
    )
    assert r["status"] == "denied"
    assert r.get("error_type") == "approval_required"


def test_cli_write_preview_without_commit():
    h = AgentHarness(AgentContext(channel="cli"))
    r2 = h.execute_tool(
        "move_card",
        {"card_id": 1, "column_id": 2},
        commit=False,
        approved=False,
    )
    assert r2["status"] == "approval_required"


def test_board_report_read_always_allowed():
    h = AgentHarness(AgentContext(channel="cli"))
    r = h.execute_tool("board_period_report", {"user_text": "отчёт за месяц"})
    assert r["status"] == "success"
    assert "html" in (r.get("data") or {})


def test_find_artifact_not_found():
    h = AgentHarness(AgentContext(channel="cli"))
    r = h.execute_tool(
        "find_research_artifact",
        {"topic": "zzz-nonexistent-topic-xyzzy-12345", "latest": False},
    )
    assert r["status"] == "error"
