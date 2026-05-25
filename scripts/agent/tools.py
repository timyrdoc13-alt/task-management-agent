"""Tool handlers wired to kaiten_api and research modules."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.registry import REGISTRY, ToolDefinition  # noqa: E402
from agent.types import AgentContext  # noqa: E402
from board_report import (  # noqa: E402
    build_board_report,
    format_board_report_html,
    parse_board_report_period,
)
from kaiten_api import (  # noqa: E402
    add_comment,
    assign_card_responsible,
    attach_file,
    confirm_delete,
    create_card,
    delete_card,
    draft_card,
    envelope,
    get_card,
    list_active_cards,
    list_overdue,
    list_today,
    move_card,
    move_to_done,
    move_to_wip,
    patch_card,
    update_card_description,
    update_card_due,
    update_card_priority,
)
from report_delivery import find_report  # noqa: E402
from research import run_research  # noqa: E402


def _wrap_result(result: dict[str, Any]) -> dict[str, Any]:
    return result


def _h_list_overdue(args: dict, ctx: AgentContext, commit: bool) -> dict:
    return list_overdue()


def _h_list_today(args: dict, ctx: AgentContext, commit: bool) -> dict:
    return list_today()


def _h_list_active_cards(args: dict, ctx: AgentContext, commit: bool) -> dict:
    columns = args.get("columns")
    if isinstance(columns, str):
        columns = [columns]
    return list_active_cards(
        include_done=bool(args.get("include_done", False)),
        columns=columns,
        limit=int(args.get("limit", 50)),
    )


def _h_get_card(args: dict, ctx: AgentContext, commit: bool) -> dict:
    return get_card(int(args["card_id"]))


def _h_draft_card(args: dict, ctx: AgentContext, commit: bool) -> dict:
    rid = args.get("responsible_user_id") or ctx.metadata.get("kaiten_user_id")
    return draft_card(
        args["title"],
        args.get("priority", "P2"),
        args.get("due_date"),
        args.get("description", ""),
        args.get("board_id"),
        args.get("column_id"),
        args.get("tags"),
        responsible_user_id=int(rid) if rid else None,
    )


def _h_assign_card_responsible(args: dict, ctx: AgentContext, commit: bool) -> dict:
    return assign_card_responsible(int(args["card_id"]), int(args["user_id"]), commit)


def _h_create_card(args: dict, ctx: AgentContext, commit: bool) -> dict:
    return create_card(args["draft_id"], commit)


def _h_move_wip(args: dict, ctx: AgentContext, commit: bool) -> dict:
    return move_to_wip(int(args["card_id"]))


def _h_move_done(args: dict, ctx: AgentContext, commit: bool) -> dict:
    return move_to_done(int(args["card_id"]))


def _h_update_description(args: dict, ctx: AgentContext, commit: bool) -> dict:
    return update_card_description(int(args["card_id"]), args["description"], commit)


def _h_attach_file(args: dict, ctx: AgentContext, commit: bool) -> dict:
    return attach_file(int(args["card_id"]), args["path"])


def _h_add_comment(args: dict, ctx: AgentContext, commit: bool) -> dict:
    return add_comment(int(args["card_id"]), args["text"])


def _h_run_research(args: dict, ctx: AgentContext, commit: bool) -> dict:
    from research import ResearchCancelledError  # noqa: WPS433

    emit = ctx.metadata.get("emit_fn")
    should_cancel = ctx.metadata.get("should_cancel_fn")
    try:
        out = run_research(
            args["topic"],
            emit=emit,
            should_cancel=should_cancel,
        )
        return envelope("success", "research completed", out)
    except ResearchCancelledError as e:
        return envelope("error", str(e), error_type="cancelled")


def _h_move_card(args: dict, ctx: AgentContext, commit: bool) -> dict:
    return move_card(int(args["card_id"]), int(args["column_id"]), commit)


def _h_update_card_priority(args: dict, ctx: AgentContext, commit: bool) -> dict:
    return update_card_priority(int(args["card_id"]), args["priority"], commit)


def _h_update_card_due(args: dict, ctx: AgentContext, commit: bool) -> dict:
    due = args.get("due_date")
    if args.get("clear"):
        due = None
    return update_card_due(int(args["card_id"]), due, commit)


def _h_patch_card(args: dict, ctx: AgentContext, commit: bool) -> dict:
    patch = args.get("patch") or {}
    return patch_card(int(args["card_id"]), patch, commit)


def _h_confirm_delete(args: dict, ctx: AgentContext, commit: bool) -> dict:
    return confirm_delete(int(args["card_id"]))


def _h_delete_card(args: dict, ctx: AgentContext, commit: bool) -> dict:
    return delete_card(int(args["card_id"]), args.get("token", ""), commit)


def _h_board_period_report(args: dict, ctx: AgentContext, commit: bool) -> dict:
    text = args.get("user_text") or ""
    period = parse_board_report_period(text)
    stats = build_board_report(period)
    return envelope(
        "success",
        stats.period.label,
        {
            "html": format_board_report_html(stats),
            "period_label": stats.period.label,
            "stats": {
                "new": len(stats.new_in_period),
                "completed": len(stats.completed_in_period),
                "blocked": len(stats.blocked_now),
                "overdue": len(stats.overdue_now),
                "snapshot": stats.snapshot,
                "truncated": stats.truncated,
            },
        },
    )


def _h_find_research_artifact(args: dict, ctx: AgentContext, commit: bool) -> dict:
    art = find_report(
        card_id=int(args["card_id"]) if args.get("card_id") else None,
        topic_query=args.get("topic"),
        latest=bool(args.get("latest")),
    )
    if not art:
        return envelope("error", "artifact not found", error_type="not_found")
    return envelope(
        "success",
        art.topic,
        {
            "topic": art.topic,
            "card_id": art.card_id,
            "docx_path": str(art.docx_path) if art.docx_path else None,
            "md_path": str(art.md_path) if art.md_path else None,
            "dir_path": str(art.dir_path),
        },
    )


def register_all_tools() -> None:
    tools = [
        ToolDefinition(
            "list_overdue",
            "List overdue Kaiten cards",
            "read_only",
            {"type": "object", "properties": {}},
            _h_list_overdue,
            "kaiten_read",
        ),
        ToolDefinition(
            "list_today",
            "List cards due today",
            "read_only",
            {"type": "object", "properties": {}},
            _h_list_today,
            "kaiten_read",
        ),
        ToolDefinition(
            "list_active_cards",
            "List cards in Queue/WIP (and optionally Done) on default board",
            "read_only",
            {
                "type": "object",
                "properties": {
                    "include_done": {"type": "boolean"},
                    "columns": {
                        "type": "array",
                        "items": {"type": "string", "enum": ["queue", "wip", "done"]},
                    },
                    "limit": {"type": "integer"},
                },
            },
            _h_list_active_cards,
            "kaiten_read",
        ),
        ToolDefinition(
            "get_card",
            "Get card by id",
            "read_only",
            {
                "type": "object",
                "properties": {"card_id": {"type": "integer"}},
                "required": ["card_id"],
            },
            _h_get_card,
            "kaiten_read",
        ),
        ToolDefinition(
            "draft_card",
            "Create local draft (no Kaiten POST)",
            "compute_only",
            {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "priority": {"type": "string"},
                    "description": {"type": "string"},
                    "column_id": {"type": "integer"},
                    "tags": {"type": "array"},
                    "responsible_user_id": {"type": "integer"},
                },
                "required": ["title"],
            },
            _h_draft_card,
        ),
        ToolDefinition(
            "assign_card_responsible",
            "Set Kaiten card responsible (assignee)",
            "write_external",
            {
                "type": "object",
                "properties": {
                    "card_id": {"type": "integer"},
                    "user_id": {"type": "integer"},
                },
                "required": ["card_id", "user_id"],
            },
            _h_assign_card_responsible,
            "kaiten_write",
        ),
        ToolDefinition(
            "create_card",
            "POST card from draft",
            "write_external",
            {
                "type": "object",
                "properties": {
                    "draft_id": {"type": "string"},
                },
                "required": ["draft_id"],
            },
            _h_create_card,
            "kaiten_write",
        ),
        ToolDefinition(
            "move_to_wip",
            "Move card to WIP column",
            "write_external",
            {"type": "object", "properties": {"card_id": {"type": "integer"}}, "required": ["card_id"]},
            _h_move_wip,
            "kaiten_write",
        ),
        ToolDefinition(
            "move_to_done",
            "Move card to Done column",
            "write_external",
            {"type": "object", "properties": {"card_id": {"type": "integer"}}, "required": ["card_id"]},
            _h_move_done,
            "kaiten_write",
        ),
        ToolDefinition(
            "update_card_description",
            "PATCH card description",
            "write_external",
            {
                "type": "object",
                "properties": {
                    "card_id": {"type": "integer"},
                    "description": {"type": "string"},
                },
                "required": ["card_id", "description"],
            },
            _h_update_description,
            "kaiten_write",
        ),
        ToolDefinition(
            "attach_file",
            "Attach file to card",
            "write_external",
            {
                "type": "object",
                "properties": {"card_id": {"type": "integer"}, "path": {"type": "string"}},
                "required": ["card_id", "path"],
            },
            _h_attach_file,
            "kaiten_write",
        ),
        ToolDefinition(
            "add_comment",
            "Add comment to card",
            "write_external_soft",
            {
                "type": "object",
                "properties": {"card_id": {"type": "integer"}, "text": {"type": "string"}},
                "required": ["card_id", "text"],
            },
            _h_add_comment,
        ),
        ToolDefinition(
            "run_research",
            "Web search, synthesize report, save artifacts",
            "search_only",
            {"type": "object", "properties": {"topic": {"type": "string"}}, "required": ["topic"]},
            _h_run_research,
            "web_fetch",
        ),
        ToolDefinition(
            "board_period_report",
            "Board metrics for a calendar period (new, done, blocked)",
            "read_only",
            {
                "type": "object",
                "properties": {"user_text": {"type": "string"}},
            },
            _h_board_period_report,
            "kaiten_read",
        ),
        ToolDefinition(
            "find_research_artifact",
            "Locate research DOCX/MD on disk by card id or topic",
            "read_only",
            {
                "type": "object",
                "properties": {
                    "card_id": {"type": "integer"},
                    "topic": {"type": "string"},
                    "latest": {"type": "boolean"},
                },
            },
            _h_find_research_artifact,
        ),
        ToolDefinition(
            "move_card",
            "Move card to column",
            "write_external",
            {
                "type": "object",
                "properties": {
                    "card_id": {"type": "integer"},
                    "column_id": {"type": "integer"},
                },
                "required": ["card_id", "column_id"],
            },
            _h_move_card,
            "kaiten_write",
        ),
        ToolDefinition(
            "update_card_priority",
            "Set card priority P1/P2/P3",
            "write_external",
            {
                "type": "object",
                "properties": {
                    "card_id": {"type": "integer"},
                    "priority": {"type": "string", "enum": ["P1", "P2", "P3"]},
                },
                "required": ["card_id", "priority"],
            },
            _h_update_card_priority,
            "kaiten_write",
        ),
        ToolDefinition(
            "update_card_due",
            "Set or clear due date",
            "write_external",
            {
                "type": "object",
                "properties": {
                    "card_id": {"type": "integer"},
                    "due_date": {"type": "string"},
                    "clear": {"type": "boolean"},
                },
                "required": ["card_id"],
            },
            _h_update_card_due,
            "kaiten_write",
        ),
        ToolDefinition(
            "patch_card",
            "Patch title/description on card",
            "write_external",
            {
                "type": "object",
                "properties": {
                    "card_id": {"type": "integer"},
                    "patch": {"type": "object"},
                },
                "required": ["card_id", "patch"],
            },
            _h_patch_card,
            "kaiten_write",
        ),
        ToolDefinition(
            "confirm_delete",
            "Generate HMAC token for card deletion",
            "compute_only",
            {
                "type": "object",
                "properties": {"card_id": {"type": "integer"}},
                "required": ["card_id"],
            },
            _h_confirm_delete,
        ),
        ToolDefinition(
            "delete_card",
            "Delete card with confirm token",
            "destructive",
            {
                "type": "object",
                "properties": {
                    "card_id": {"type": "integer"},
                    "token": {"type": "string"},
                },
                "required": ["card_id", "token"],
            },
            _h_delete_card,
            "kaiten_write",
        ),
    ]
    for t in tools:
        REGISTRY.register(t)


register_all_tools()
