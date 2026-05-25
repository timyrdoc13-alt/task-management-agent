import json
from pathlib import Path

import pytest

from report_delivery import (
    find_report,
    list_artifacts,
    parse_report_request,
    tag_artifact_with_card,
)


def test_parse_report_request_card_id():
    cid, topic, latest = parse_report_request("пришли отчёт #64956648")
    assert cid == 64956648
    assert topic is None
    assert latest is False


def test_parse_report_request_latest():
    cid, topic, latest = parse_report_request("дай последний отчёт")
    assert cid is None
    assert latest is True


def test_parse_artifact_request_topic():
    from report_delivery import parse_artifact_request

    cid, topic, latest = parse_artifact_request("файл по теме open webui")
    assert cid is None
    assert "open webui" in (topic or "").lower()
    assert latest is False


def test_find_and_tag_artifact(tmp_path, monkeypatch):
    monkeypatch.setattr("report_delivery.ARTIFACTS_DIR", tmp_path)
    art_dir = tmp_path / "2026-05-18-test-topic"
    art_dir.mkdir()
    (art_dir / "report.md").write_text("# Test\n\nBody", encoding="utf-8")
    (art_dir / "report.docx").write_bytes(b"PK")
    meta = {"topic": "Test Topic", "created_at": "2026-05-18"}
    (art_dir / "meta.json").write_text(json.dumps(meta), encoding="utf-8")

    tag_artifact_with_card(art_dir, 12345)
    found = find_report(card_id=12345)
    assert found is not None
    assert found.card_id == 12345
    assert found.docx_path and found.docx_path.exists()

    arts = list_artifacts(limit=5)
    assert len(arts) == 1
    assert find_report(topic_query="test topic") is not None
    assert find_report(latest=True) is not None
