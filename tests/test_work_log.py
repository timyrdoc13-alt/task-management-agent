from unittest.mock import patch

from work_log import (
    apply_work_log,
    parse_duration_minutes,
    parse_log_command,
    parse_work_log,
)


def test_parse_duration_hours():
    assert parse_duration_minutes("потратил 2 часа") == 120
    assert parse_duration_minutes("30 мин") == 30


def test_parse_work_log_with_time():
    req = parse_work_log("#65027044 сделал интеграцию API, 2ч")
    assert req is not None
    assert req.card_id == 65027044
    assert req.minutes == 120
    assert "интеграцию" in req.summary.lower()


def test_parse_work_log_comment_only():
    req = parse_work_log("#65027044 готово: проверил деплой")
    assert req is not None
    assert req.minutes is None


def test_parse_work_log_no_card():
    assert parse_work_log("сделал интеграцию") is None


def test_parse_log_command():
    req = parse_log_command("/log #64942144 2ч провели мит")
    assert req is not None
    assert req.card_id == 64942144
    assert req.minutes == 120
    assert "мит" in req.summary.lower()

    req2 = parse_log_command("/log 65027044 обсудили план")
    assert req2 is not None
    assert req2.card_id == 65027044
    assert req2.minutes is None


def test_apply_work_log_partial_success_comment_ok_time_fail():
    with patch("kaiten_api.add_comment") as mock_comment:
        with patch("kaiten_api.add_time_log") as mock_time:
            mock_comment.return_value = {"status": "success", "summary": "ok"}
            mock_time.return_value = {"status": "error", "summary": "no role_id"}
            out = apply_work_log(99, "сделали мит", "Тест", minutes=60)
    assert out["ok"] is True
    assert out.get("comment_ok") is True
    assert out.get("time_log_ok") is False
