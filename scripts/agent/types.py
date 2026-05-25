"""Shared types for the Kaiten agent harness."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

Channel = Literal["cursor", "telegram", "cli"]
RiskClass = Literal[
    "read_only",
    "write_local",
    "search_only",
    "compute_only",
    "write_external",
    "write_external_soft",
    "destructive",
]
PolicyDecision = Literal["allow", "approval_required", "denied"]
RunStatus = Literal["pending", "running", "completed", "failed", "cancelled"]


@dataclass
class AgentContext:
    channel: Channel
    user_id: int | None = None
    chat_id: int | None = None
    run_id: str = ""
    job_id: str | None = None
    idempotency_key: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class StepRecord:
    step: str
    tool: str
    status: str
    summary: str
    trace_id: str = ""
    duration_ms: int = 0
    data: Any = None


@dataclass
class WorkflowResult:
    status: str
    summary: str
    data: dict[str, Any] = field(default_factory=dict)
    steps: list[StepRecord] = field(default_factory=list)
    trace_id: str = ""
