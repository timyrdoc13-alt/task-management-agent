"""Multi-step agent workflows (research, create card)."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Callable

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.harness import AgentHarness  # noqa: E402
from agent.job_store import (  # noqa: E402
    create_job,
    is_job_cancelled,
    research_idempotency_key,
    update_job,
)
from agent.types import AgentContext, WorkflowResult  # noqa: E402
from agent.validation import validate_research_report  # noqa: E402
from kaiten_api import ENV  # noqa: E402
from llm import ExtractedTask, kaiten_description  # noqa: E402
from admin_notify import notify_admins_card_created_sync  # noqa: E402
from user_directory import resolve_for_context  # noqa: E402


EmitFn = Callable[..., None]


def _format_research_title(task: ExtractedTask, topic: str) -> str:
    return f"{task.icon} [Ресёрч] {topic[:80]}".strip()


def run_create_card_workflow(
    harness: AgentHarness,
    task: ExtractedTask,
    *,
    column_id: int | None,
    commit: bool = True,
) -> WorkflowResult:
    title = f"{task.icon} {task.title}".strip()
    assignee = resolve_for_context(harness.ctx, task)
    responsible_id = assignee.kaiten_user_id if assignee else harness.ctx.metadata.get("kaiten_user_id")
    draft = harness.execute_tool(
        "draft_card",
        {
            "title": title,
            "priority": task.priority,
            "due_date": task.due_date_iso,
            "description": kaiten_description(task),
            "column_id": column_id,
            "tags": task.tags,
            "responsible_user_id": responsible_id,
        },
        commit=True,
    )
    if draft.get("status") not in ("success", "approval_required"):
        return WorkflowResult("error", draft.get("summary", "draft failed"), steps=harness.steps)

    draft_id = (draft.get("data") or {}).get("draft_id")
    if not draft_id:
        return WorkflowResult("error", "draft_id missing", steps=harness.steps)
    created = harness.execute_tool("create_card", {"draft_id": draft_id}, commit=commit)
    if created.get("status") != "success":
        return WorkflowResult("error", created.get("summary", "create failed"), steps=harness.steps)

    data = created.get("data") or {}
    extra: dict[str, Any] = {
        "card_id": data.get("card_id"),
        "url": data.get("url"),
    }
    if assignee:
        extra["assignee"] = assignee.display_name
        extra["assignee_kaiten_user_id"] = assignee.kaiten_user_id
        extra["assignee_source"] = assignee.source
        if assignee.ambiguous:
            extra["assignee_ambiguous"] = assignee.ambiguous
    if data.get("assign_warning"):
        extra["assign_warning"] = data["assign_warning"]
    if harness.ctx.channel == "telegram":
        notify_admins_card_created_sync(task, harness.ctx, extra, kind="create")
    return WorkflowResult(
        "success",
        f"card #{data.get('card_id')} created",
        data=extra,
        steps=harness.steps,
        trace_id=harness.ctx.run_id,
    )


def run_research_workflow(
    harness: AgentHarness,
    task: ExtractedTask,
    *,
    topic: str,
    column_id: int | None,
    emit: EmitFn | None = None,
) -> WorkflowResult:
    chat_id = harness.ctx.chat_id or 0
    idem = research_idempotency_key(chat_id, topic)
    existing = create_job(
        "research",
        idem,
        {"topic": topic, "task": task.to_dict()},
        channel=harness.ctx.channel,
        chat_id=chat_id,
        trace_id=harness.ctx.run_id,
    )
    if existing.get("status") == "running":
        return WorkflowResult(
            "error",
            "research already in progress",
            data={"job_id": existing.get("id")},
            steps=harness.steps,
        )
    if existing.get("status") == "completed" and existing.get("result"):
        return WorkflowResult(
            "success",
            "idempotent replay — job already completed",
            data=existing["result"],
            steps=harness.steps,
            trace_id=existing.get("trace_id", ""),
        )

    job_id = existing["id"]
    harness.ctx.job_id = job_id
    update_job(job_id, status="running")

    harness.ctx.metadata["emit_fn"] = emit
    harness.ctx.metadata["should_cancel_fn"] = lambda: is_job_cancelled(job_id)
    initial_desc = kaiten_description(task) or (
        f"## Тема\n{topic}\n\n## Definition of done\nОтчёт DOCX во вложениях."
    )
    assignee = resolve_for_context(harness.ctx, task)
    responsible_id = assignee.kaiten_user_id if assignee else harness.ctx.metadata.get("kaiten_user_id")
    draft = harness.execute_tool(
        "draft_card",
        {
            "title": _format_research_title(task, topic),
            "priority": "P3",
            "description": initial_desc,
            "column_id": column_id,
            "tags": ["research"] + (task.tags or []),
            "responsible_user_id": responsible_id,
        },
        commit=True,
    )
    if draft.get("status") not in ("success", "approval_required"):
        update_job(job_id, status="failed", error=draft.get("summary"))
        return WorkflowResult("error", "draft failed", steps=harness.steps)

    draft_id = (draft.get("data") or {}).get("draft_id")
    if not draft_id:
        update_job(job_id, status="failed", error="draft_id missing")
        return WorkflowResult("error", "draft_id missing", steps=harness.steps)

    created = harness.execute_tool(
        "create_card",
        {"draft_id": draft_id},
        commit=True,
    )
    if created.get("status") != "success":
        update_job(job_id, status="failed", error=created.get("summary"))
        return WorkflowResult("error", "create card failed", steps=harness.steps)

    cid = (created.get("data") or {}).get("card_id")
    url = (created.get("data") or {}).get("url")
    if harness.ctx.channel == "telegram":
        notify_data: dict[str, Any] = {"card_id": cid, "url": url}
        if assignee:
            notify_data["assignee"] = assignee.display_name
            notify_data["assignee_kaiten_user_id"] = assignee.kaiten_user_id
        notify_admins_card_created_sync(task, harness.ctx, notify_data, kind="research")
    harness.execute_tool("move_to_wip", {"card_id": cid}, commit=True)

    research = harness.execute_tool("run_research", {"topic": topic}, commit=True)
    if research.get("status") != "success":
        err_type = research.get("error_type")
        summary = research.get("summary", "research failed")
        if err_type == "cancelled":
            update_job(job_id, status="cancelled", error=summary)
            return WorkflowResult(
                "error",
                "research cancelled",
                data={"card_id": cid, "url": url, "cancelled": True},
                steps=harness.steps,
            )
        harness.execute_tool(
            "add_comment",
            {"card_id": cid, "text": f"❌ Ресёрч: {summary[:400]}"},
            commit=True,
        )
        update_job(job_id, status="failed", error=summary)
        return WorkflowResult(
            "error",
            summary,
            data={"card_id": cid, "url": url},
            steps=harness.steps,
        )

    payload = research.get("data") or {}
    meta = payload.get("meta") or {}
    fetched_n = int(meta.get("fetched") or 0)
    report_path = payload.get("report_path")
    report_md = payload.get("report_md_path", report_path)
    kaiten_desc = payload.get("kaiten_description") or ""

    md_path = Path(report_md) if report_md else None
    markdown = md_path.read_text(encoding="utf-8") if md_path and md_path.exists() else ""
    validation = validate_research_report(markdown)
    report_ok = validation.ok

    if kaiten_desc:
        harness.execute_tool(
            "update_card_description",
            {"card_id": cid, "description": kaiten_desc},
            commit=True,
        )

    if report_path:
        harness.execute_tool(
            "attach_file",
            {"card_id": cid, "path": report_path},
            commit=True,
        )

    harness.execute_tool(
        "add_comment",
        {
            "card_id": cid,
            "text": (
                f"Ресёрч готов. Источников: {fetched_n}, {meta.get('wall_time_s', 0)}s. "
                f"Валидация: {'OK' if report_ok else validation.summary()}"
            ),
        },
        commit=True,
    )

    moved_done = False
    if fetched_n > 0 and report_ok:
        mv = harness.execute_tool("move_to_done", {"card_id": cid}, commit=True)
        moved_done = mv.get("status") == "success"
    elif not report_ok:
        harness.execute_tool(
            "add_comment",
            {
                "card_id": cid,
                "text": f"⚠ Отчёт не прошёл валидацию: {validation.summary()}. Карточка в WIP.",
            },
            commit=True,
        )

    result_data: dict[str, Any] = {
        "card_id": cid,
        "url": url,
        "report_path": report_path,
        "report_md_path": report_md,
        "meta": meta,
        "validation": {
            "ok": report_ok,
            "missing": validation.missing_sections,
            "incomplete": validation.incomplete,
            "reasons": validation.reasons,
        },
        "moved_to_done": moved_done,
        "fetched": fetched_n,
        "trace": harness.export_trace(),
    }
    update_job(job_id, status="completed", result=result_data)
    return WorkflowResult(
        "success" if report_ok or fetched_n > 0 else "partial",
        "research workflow finished",
        data=result_data,
        steps=harness.steps,
        trace_id=harness.ctx.run_id,
    )
