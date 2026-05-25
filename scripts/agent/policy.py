"""Central autonomy policy — single source of truth for tool execution."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import TYPE_CHECKING

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.types import AgentContext, PolicyDecision, RiskClass  # noqa: E402
from kaiten_api import ENV  # noqa: E402

if TYPE_CHECKING:
    from llm import ExtractedTask


def _truthy(key: str, default: str = "false") -> bool:
    return ENV.get(key, default).lower() in {"1", "true", "yes", "on"}


def auto_research_enabled() -> bool:
    return _truthy("AUTO_RESEARCH_ENABLED", "true")


def auto_research_card_enabled() -> bool:
    """Full TG research pipeline (card + attach + done)."""
    if not auto_research_enabled():
        return False
    return _truthy("KAITEN_AGENT_AUTO_RESEARCH", "true")


def auto_cards_enabled() -> bool:
    """TG auto-create for intent=create (P2/P3, confidence)."""
    return _truthy("KAITEN_AGENT_AUTO_CARDS", "false")


def task_always_preview_enabled() -> bool:
    """Force TG preview+revision for every create (overrides auto_cards)."""
    return _truthy("KAITEN_TASK_ALWAYS_PREVIEW", "false")


def create_needs_preview(task: ExtractedTask, ctx: AgentContext) -> bool:
    if ctx.channel != "telegram":
        return True
    if task_always_preview_enabled():
        return True
    return not can_auto_create_card(task, ctx)


def channel_requires_approval(ctx: AgentContext) -> bool:
    """Cursor/CLI: writes need explicit commit unless already approved."""
    return ctx.channel in {"cursor", "cli"}


def evaluate_tool(
    tool_name: str,
    risk: RiskClass,
    ctx: AgentContext,
    *,
    commit: bool = False,
    approved: bool = False,
) -> PolicyDecision:
    if risk == "read_only" or risk == "compute_only":
        return "allow"
    if risk == "search_only":
        return "allow"
    if risk == "write_local":
        return "allow"
    if risk == "write_external_soft":
        if _truthy("KAITEN_AGENT_NO_AUTOCOMMENT", "false"):
            return "denied"
        return "allow"
    if risk == "destructive":
        return "allow" if commit and approved else "approval_required"
    if risk == "write_external":
        if channel_requires_approval(ctx):
            return "allow" if commit and approved else "approval_required"
        # telegram: commit flag set by workflow after policy checks
        return "allow" if commit else "approval_required"
    return "approval_required"


def can_auto_create_card(task: ExtractedTask, ctx: AgentContext) -> bool:
    if ctx.channel != "telegram":
        return False
    if not auto_cards_enabled():
        return False
    if task.sensitive_markers:
        return False
    if task.priority not in {"P2", "P3"}:
        return False
    min_conf = float(ENV.get("AUTO_CARD_MIN_CONFIDENCE", "0.85"))
    return task.confidence >= min_conf


def can_auto_research(task: ExtractedTask, ctx: AgentContext) -> bool:
    if ctx.channel != "telegram":
        return False
    if not auto_research_card_enabled():
        return False
    if task.sensitive_markers:
        return False
    min_conf = float(ENV.get("AUTO_RESEARCH_MIN_CONFIDENCE", "0.75"))
    return task.confidence >= min_conf


def research_needs_preview(task: ExtractedTask, ctx: AgentContext) -> bool:
    return not can_auto_research(task, ctx)
