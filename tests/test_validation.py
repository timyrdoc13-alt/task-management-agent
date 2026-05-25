from agent.validation import report_incomplete_heuristic, validate_research_report


def test_incomplete_table():
    md = "## TL;DR\nok\n\n| a | b |\n| --- | --- |\n| x | y ("
    v = validate_research_report(md)
    assert not v.ok
    assert v.incomplete


def test_complete_minimal():
    md = (
        "## TL;DR\n1. point\n\n"
        "## Дальнейшие шаги\n- step\n\n"
        "## Источники\n- [x](http://a)\n\n"
        + "x" * 2600
    )
    v = validate_research_report(md)
    assert v.ok


def test_finish_reason_length():
    assert report_incomplete_heuristic("## TL;DR\n", "length")
