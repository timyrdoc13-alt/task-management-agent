from board_report import (
    _column_key,
    build_board_report,
    format_board_report_html,
    format_column_snapshot_lines,
    parse_board_report_period,
)


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
    assert "Поступило (новых)" in text
    assert "На стопе" in text
    assert "Сейчас на доске" in text


def test_format_column_snapshot_uses_kaiten_titles():
    lines = format_column_snapshot_lines({6128316: 2, 6128807: 1})
    text = "\n".join(lines)
    assert "В работе · Ревью" in text or "Ревью" in text
    assert "Готово к защите" in text
    assert "WIP" not in text
    assert "blocked" not in text.lower()


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
