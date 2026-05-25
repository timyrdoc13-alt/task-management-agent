"""Результат ресёрча на диске (DOCX/MD) — не сводный отчёт по доске (см. board_report.py)."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from kaiten_api import ARTIFACTS_DIR, MSK, slugify
from report_export import kaiten_card_description


@dataclass
class ReportArtifact:
    topic: str
    dir_path: Path
    docx_path: Path | None
    md_path: Path | None
    meta: dict
    card_id: int | None = None

    def summary_text(self, max_chars: int = 3500) -> str:
        md = self.md_path
        if md and md.exists():
            text = md.read_text(encoding="utf-8")
            desc = kaiten_card_description(text, self.topic)
            if desc:
                return desc[:max_chars]
        return (self.meta.get("kaiten_description") or self.topic)[:max_chars]


def _load_meta(meta_path: Path) -> dict | None:
    try:
        return json.loads(meta_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _artifact_from_dir(dir_path: Path, meta: dict) -> ReportArtifact | None:
    docx = dir_path / "report.docx"
    md = dir_path / "report.md"
    if not docx.exists() and not md.exists():
        return None
    return ReportArtifact(
        topic=str(meta.get("topic") or dir_path.name),
        dir_path=dir_path,
        docx_path=docx if docx.exists() else None,
        md_path=md if md.exists() else None,
        meta=meta,
        card_id=int(meta["card_id"]) if meta.get("card_id") else None,
    )


def list_artifacts(limit: int = 20) -> list[ReportArtifact]:
    if not ARTIFACTS_DIR.exists():
        return []
    found: list[tuple[float, ReportArtifact]] = []
    for meta_path in ARTIFACTS_DIR.glob("*/meta.json"):
        meta = _load_meta(meta_path)
        if not meta:
            continue
        art = _artifact_from_dir(meta_path.parent, meta)
        if art:
            found.append((meta_path.stat().st_mtime, art))
    found.sort(key=lambda x: x[0], reverse=True)
    return [a for _, a in found[:limit]]


def find_report(
    *,
    card_id: int | None = None,
    topic_query: str | None = None,
    latest: bool = False,
) -> ReportArtifact | None:
    """Resolve artifact by Kaiten card id, topic substring, or most recent."""
    arts = list_artifacts(limit=50)
    if card_id:
        for a in arts:
            if a.card_id == card_id:
                return a
        # fallback: match folder slug from card title via get_card — caller may pass topic
    if topic_query:
        q = topic_query.strip().lower()
        if len(q) >= 3:
            for a in arts:
                if q in (a.topic or "").lower():
                    return a
            slug = slugify(topic_query)
            for a in arts:
                if slug and slug in a.dir_path.name.lower():
                    return a
    if latest and arts:
        return arts[0]
    return None


def tag_artifact_with_card(artifact_dir: Path | str, card_id: int) -> None:
    """Link artifact folder to Kaiten card for later «пришли отчёт #id»."""
    meta_path = Path(artifact_dir) / "meta.json"
    if not meta_path.exists():
        return
    meta = _load_meta(meta_path) or {}
    meta["card_id"] = card_id
    meta["card_linked_at"] = datetime.now(MSK).isoformat(timespec="seconds")
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")


def parse_artifact_request(text: str) -> tuple[int | None, str | None, bool]:
    """Extract card_id, topic hint, or latest flag for research file delivery."""
    from card_actions import parse_card_id

    cid = parse_card_id(text)
    if cid:
        return cid, None, False
    if re.search(r"(?:последн|свеж|latest|недавн)", text, re.I):
        return None, None, True
    m = re.search(
        r"(?:файл|документ|docx|результат|справк)\s+(?:по\s+теме|по|про|about)\s+(.+)",
        text,
        re.I,
    )
    if m:
        return None, m.group(1).strip()[:120], False
    m = re.search(r"(?:по\s+теме|про|about)\s+(.+)", text, re.I)
    if m:
        return None, m.group(1).strip()[:120], False
    return None, None, False


def parse_report_request(text: str) -> tuple[int | None, str | None, bool]:
    """Backward-compatible alias for parse_artifact_request."""
    return parse_artifact_request(text)
