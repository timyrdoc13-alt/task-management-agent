import tempfile
from pathlib import Path

import agent.job_store as js


def test_idempotency(monkeypatch, tmp_path):
    monkeypatch.setattr(js, "JOBS_DB", tmp_path / "jobs.sqlite")
    monkeypatch.setattr(js, "STATE_DIR", tmp_path)
    j1 = js.create_job("research", "k:test:abc", {"topic": "x"}, chat_id=1)
    j2 = js.create_job("research", "k:test:abc", {"topic": "x"}, chat_id=1)
    assert j1["id"] == j2["id"]
