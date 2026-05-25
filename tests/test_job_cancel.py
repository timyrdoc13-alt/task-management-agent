from agent.job_store import (
    cancel_job,
    create_job,
    get_running_job,
    is_job_cancelled,
    update_job,
)


def test_cancel_running_job(monkeypatch, tmp_path):
    monkeypatch.setattr("agent.job_store.STATE_DIR", tmp_path)
    monkeypatch.setattr("agent.job_store.JOBS_DB", tmp_path / "jobs.sqlite")
    job = create_job("research", "idem:1", {"topic": "t"}, chat_id=42)
    update_job(job["id"], status="running")
    assert get_running_job(42)["id"] == job["id"]
    cancel_job(job["id"])
    assert is_job_cancelled(job["id"])
    assert get_running_job(42) is None
