"""Validation gates before committing agent side effects."""

from __future__ import annotations

import re
from dataclasses import dataclass

REQUIRED_SECTIONS = (
    "## TL;DR",
    "## Дальнейшие шаги",
    "## Источники",
)

RECOMMENDED_SECTIONS = (
    "## Рекомендуемая архитектура",
    "## Риски и ограничения",
    "## PoC → Pilot → Production",
)


@dataclass
class ReportValidation:
    ok: bool
    missing_sections: list[str]
    incomplete: bool
    reasons: list[str]

    def summary(self) -> str:
        parts = []
        if self.missing_sections:
            parts.append("нет разделов: " + ", ".join(self.missing_sections))
        if self.incomplete:
            parts.append("текст оборван (таблица/лимит модели)")
        return "; ".join(parts) or "ok"


def report_incomplete_heuristic(markdown: str, finish_reason: str | None = None) -> bool:
    if (finish_reason or "").lower() == "length":
        return True
    text = markdown or ""
    tail = text.rstrip().splitlines()
    if not tail:
        return True
    last = tail[-1].strip()
    if last.startswith("|") and (not last.endswith("|") or last.count("|") < 3):
        return True
    if last.endswith("(") or last.endswith("|"):
        return True
    return False


def validate_research_report(
    markdown: str,
    *,
    finish_reason: str | None = None,
    min_chars: int = 2500,
) -> ReportValidation:
    text = markdown or ""
    missing = [h for h in REQUIRED_SECTIONS if h not in text]
    incomplete = report_incomplete_heuristic(text, finish_reason)
    reasons: list[str] = []
    if len(text) < min_chars and "## TL;DR" in text:
        reasons.append(f"слишком короткий отчёт ({len(text)} симв.)")
    for h in RECOMMENDED_SECTIONS:
        if h not in text:
            reasons.append(f"рекомендуется добавить {h}")
    ok = not missing and not incomplete and len(text) >= min_chars
    return ReportValidation(
        ok=ok,
        missing_sections=missing,
        incomplete=incomplete,
        reasons=reasons,
    )


def report_needs_continuation(markdown: str, finish_reason: str | None = None) -> bool:
    """Used by llm synthesize continue loop."""
    if (finish_reason or "").lower() == "length":
        return True
    for marker in REQUIRED_SECTIONS:
        if marker not in (markdown or ""):
            return True
    return report_incomplete_heuristic(markdown, finish_reason)
