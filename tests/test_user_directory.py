from user_directory import get_by_telegram_id, resolve_assignee, telegram_user_ids_with_bot_access


def test_telegram_whitelist_includes_new_users():
    ids = telegram_user_ids_with_bot_access()
    assert 458002471 in ids
    assert 522378116 in ids
    assert 228378111 in ids


def test_get_by_telegram():
    u = get_by_telegram_id(458002471)
    assert u is not None
    assert u.kaiten_user_id == 1030973
    assert "Подкопаев" in u.display_name


def test_default_assignee_is_sender():
    r = resolve_assignee("поставь задачу проверить акты", telegram_user_id=522378116)
    assert r is not None
    assert r.kaiten_user_id == 1030974
    assert r.source == "sender"


def test_assign_by_name_in_text():
    r = resolve_assignee(
        "поставь Яну задачу по API",
        telegram_user_id=522378116,
    )
    assert r is not None
    assert r.kaiten_user_id == 1030973
    assert r.source in {"text", "owner_hint"}


def test_yandex_does_not_match_yan():
    r = resolve_assignee(
        "Создай задачу на меня пояснительная записка по договорам Яндекс",
        telegram_user_id=228378111,
    )
    assert r is not None
    assert r.kaiten_user_id == 594738
    assert r.source == "sender"


def test_na_menya_assigns_sender():
    r = resolve_assignee(
        "создай задачу на меня проверить отчёт",
        telegram_user_id=228378111,
    )
    assert r.kaiten_user_id == 594738
