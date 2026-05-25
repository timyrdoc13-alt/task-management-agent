from board_report import _column_key, build_board_report, format_board_report_html, parse_board_report_period


def test_parse_period_month():
    p = parse_board_report_period("отчёт за месяц")
    assert p.start.month == p.end.month - 1 or p.start.day == 1


def test_parse_period_may():
    p = parse_board_report_period("итоги за май 2026")
    assert p.start.month == 5
    assert p.start.year == 2026


def test_format_report_smoke():
    stats = build_board_report()
    text = format_board_report_html(stats)
    assert "Отчёт по задачам" in text
    assert "Новых:" in text
    assert "На стопе" in text


def test_column_key_matches_wip_subcolumn():
    cols = {
        "wip": {
            "column_id": 6128315,
            "parent_column_id": 6128312,
            "column_ids": [6128312, 6128315, 6128316],
            "title": "В работе",
        }
    }
    assert _column_key(6128315, cols) == "wip"
    assert _column_key(6128312, cols) == "wip"
    assert _column_key(6128311, cols) == "other"
