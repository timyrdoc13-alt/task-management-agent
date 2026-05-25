from work_log import parse_duration_minutes, parse_work_log


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
