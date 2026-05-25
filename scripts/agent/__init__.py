"""Kaiten task agent harness."""

# Side effect: populate ToolRegistry before any execute_tool() call.
from agent.tools import register_all_tools  # noqa: E402

register_all_tools()

from agent.harness import AgentHarness
from agent.policy import (
    can_auto_create_card,
    can_auto_research,
    evaluate_tool,
    research_needs_preview,
)
from agent.workflows import run_create_card_workflow, run_research_workflow

__all__ = [
    "AgentHarness",
    "AgentContext",
    "can_auto_create_card",
    "can_auto_research",
    "evaluate_tool",
    "research_needs_preview",
    "run_create_card_workflow",
    "run_research_workflow",
]

from agent.types import AgentContext  # noqa: E402
