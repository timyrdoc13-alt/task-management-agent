from unittest.mock import MagicMock, patch

from card_actions import (
    CardAction,
    _regex_guess_action,
    build_action_plan,
    has_status_narrative,
    is_explicit_card_command,
    parse_card_id,
    should_offer_card_choice,
)


def test_parse_card_id_from_kaiten_url():
    assert parse_card_id("https://company.kaiten.ru/64942144") == 64942144
    assert parse_card_id("см. kaiten.ru/c/65027044 в описании") == 65027044


def test_is_explicit_card_command_narrative_vs_imperative():
    cid = 64942144
    assert is_explicit_card_command(f"#64942144 отдали в работу") is False
    assert has_status_narrative(f"#64942144 отдали в работу", cid) is True
    assert is_explicit_card_command(f"перенеси #64942144 в работу") is True


def test_regex_no_false_positive_narrative_in_work():
    text = "#64942144 отдали в работу, ждём ревью"
    assert _regex_guess_action(text) is None


def test_regex_matches_imperative_move():
    act = _regex_guess_action("перенеси #64942144 в работу")
    assert act is not None
    assert act.action == "move_column"
    assert act.column_target == "wip"


def test_should_offer_card_choice_general_narrative():
    cid = 64942144
    assert should_offer_card_choice(
        f"#64942144 (https://timyrdoc.kaiten.ru/{cid}) - провели мит",
        cid,
    )
    assert should_offer_card_choice(
        f"в задаче {cid} отдали 2 КП в подготовку ВебКапиталу",
        cid,
    )
    assert not should_offer_card_choice(f"перенеси #{cid} в работу", cid)
    assert not should_offer_card_choice(f"#{cid}", cid)


def test_build_action_plan_noop_same_column():
    action = CardAction(action="move_column", card_id=99, column_target="wip")
    harness = MagicMock()
    harness.execute_tool.return_value = {
        "status": "success",
        "data": {"title": "Test", "column_id": 42},
    }
    with patch("card_actions.column_id_for_target", return_value=(42, "wip")):
        with patch("card_actions._column_title", return_value="В работе"):
            plan = build_action_plan(action, harness)
    assert plan["ok"] is True
    assert plan.get("noop") is True
    assert "уже" in plan.get("message", "").lower()
    assert plan["ops"] == []
