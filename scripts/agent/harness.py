"""Agent harness: policy → rate limit → tool → structured observation."""

from __future__ import annotations

import secrets
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.policy import evaluate_tool  # noqa: E402
from agent.rate_limits import RateLimitError, RateLimiter  # noqa: E402
from agent.registry import REGISTRY  # noqa: E402
from agent.types import AgentContext, StepRecord  # noqa: E402
from kaiten_api import envelope, log_call  # noqa: E402


class AgentHarness:
    def __init__(self, ctx: AgentContext) -> None:
        self.ctx = ctx
        self.ctx.run_id = ctx.run_id or secrets.token_hex(8)
        self.steps: list[StepRecord] = []
        self.limiter = RateLimiter()

    def execute_tool(
        self,
        tool_name: str,
        args: dict[str, Any],
        *,
        commit: bool = False,
        approved: bool = False,
    ) -> dict[str, Any]:
        tool = REGISTRY.get(tool_name)
        if not tool:
            return envelope("error", f"unknown tool: {tool_name}", error_type="unknown_tool")

        decision = evaluate_tool(tool_name, tool.risk, self.ctx, commit=commit, approved=approved)
        if decision == "denied":
            out = envelope("denied", f"tool {tool_name} denied by policy", error_type="permission_denied")
            self._record_step(tool_name, out, 0)
            return out
        if decision == "approval_required":
            if commit and not approved:
                out = envelope(
                    "denied",
                    f"tool {tool_name} requires --approved on {self.ctx.channel}",
                    error_type="approval_required",
                    data={"tool": tool_name, "hint": "use agent_cli execute-tool --approved"},
                )
                self._record_step(tool_name, out, 0)
                return out
            if not commit:
                out = tool.handler(args, self.ctx, False)
                self._record_step(tool_name, out, 0)
                return out

        if tool.rate_bucket:
            try:
                self.limiter.check(tool.rate_bucket)
            except RateLimitError as e:
                out = envelope(
                    "error",
                    str(e),
                    error_type="rate_limited",
                    data={"retry_after_s": e.retry_after_s},
                )
                self._record_step(tool_name, out, 0)
                return out

        t0 = time.time()
        try:
            result = tool.handler(args, self.ctx, commit)
        except Exception as e:
            result = envelope("error", str(e)[:500], error_type="execution_error")
        duration = int((time.time() - t0) * 1000)

        if tool.rate_bucket and result.get("status") == "success":
            self.limiter.record(tool.rate_bucket)

        trace_id = result.get("trace_id", "")
        self._record_step(tool_name, result, duration, trace_id)
        log_call(
            tool_name,
            args,
            result.get("status", "unknown"),
            duration,
            {"trace_id": trace_id, "run_id": self.ctx.run_id, "channel": self.ctx.channel},
        )
        return result

    def _record_step(
        self,
        tool_name: str,
        result: dict[str, Any],
        duration_ms: int,
        trace_id: str = "",
    ) -> None:
        self.steps.append(
            StepRecord(
                step=f"tool:{tool_name}",
                tool=tool_name,
                status=result.get("status", "unknown"),
                summary=result.get("summary", "")[:200],
                trace_id=trace_id or result.get("trace_id", ""),
                duration_ms=duration_ms,
                data=result.get("data"),
            )
        )

    def export_trace(self) -> dict[str, Any]:
        return {
            "run_id": self.ctx.run_id,
            "job_id": self.ctx.job_id,
            "channel": self.ctx.channel,
            "steps": [
                {
                    "tool": s.tool,
                    "status": s.status,
                    "summary": s.summary,
                    "trace_id": s.trace_id,
                    "duration_ms": s.duration_ms,
                }
                for s in self.steps
            ],
        }
