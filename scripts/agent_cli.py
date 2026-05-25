#!/usr/bin/env python3
"""CLI for agent harness — introspection and controlled tool execution (Cursor/CI)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from agent.harness import AgentHarness  # noqa: E402
from agent.registry import REGISTRY  # noqa: E402
from agent.tools import register_all_tools  # noqa: E402
from agent.types import AgentContext  # noqa: E402

register_all_tools()


def main() -> None:
    p = argparse.ArgumentParser(description="Kaiten agent harness")
    p.add_argument(
        "command",
        choices=["list-tools", "tool-schema", "execute-tool"],
    )
    p.add_argument("--tool", help="tool name")
    p.add_argument("--args", help="JSON object of tool arguments", default="{}")
    p.add_argument(
        "--channel",
        choices=["cli", "cursor", "telegram"],
        default="cli",
    )
    p.add_argument("--commit", action="store_true", help="commit write (not preview)")
    p.add_argument(
        "--approved",
        action="store_true",
        help="user approved write (required for cursor/cli writes)",
    )
    args = p.parse_args()

    if args.command == "list-tools":
        print(json.dumps(REGISTRY.list_tools(), ensure_ascii=False, indent=2))
        return

    if args.command == "tool-schema":
        t = REGISTRY.get(args.tool or "")
        if not t:
            print(json.dumps({"error": "unknown tool"}))
            sys.exit(1)
        print(
            json.dumps(
                {
                    "name": t.name,
                    "risk": t.risk,
                    "parameters": t.parameters,
                    "description": t.description,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return

    if not args.tool:
        print(json.dumps({"error": "--tool required"}))
        sys.exit(1)
    try:
        tool_args = json.loads(args.args)
    except json.JSONDecodeError as e:
        print(json.dumps({"error": f"invalid --args JSON: {e}"}))
        sys.exit(1)

    ctx = AgentContext(channel=args.channel)  # type: ignore[arg-type]
    harness = AgentHarness(ctx)
    result = harness.execute_tool(
        args.tool,
        tool_args,
        commit=args.commit,
        approved=args.approved,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if result.get("status") in {"error", "denied"}:
        sys.exit(1)


if __name__ == "__main__":
    main()
