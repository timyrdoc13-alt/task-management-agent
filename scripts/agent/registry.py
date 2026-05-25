"""Tool registry — named tools with risk class and JSON schema hints."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from agent.types import AgentContext, RiskClass

ToolHandler = Callable[[dict[str, Any], AgentContext, bool], dict[str, Any]]


@dataclass
class ToolDefinition:
    name: str
    description: str
    risk: RiskClass
    parameters: dict[str, Any]
    handler: ToolHandler
    rate_bucket: str | None = None


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolDefinition] = {}

    def register(self, tool: ToolDefinition) -> None:
        self._tools[tool.name] = tool

    def get(self, name: str) -> ToolDefinition | None:
        return self._tools.get(name)

    def list_tools(self) -> list[dict[str, Any]]:
        return [
            {
                "name": t.name,
                "description": t.description,
                "risk": t.risk,
                "parameters": t.parameters,
            }
            for t in self._tools.values()
        ]


REGISTRY = ToolRegistry()
