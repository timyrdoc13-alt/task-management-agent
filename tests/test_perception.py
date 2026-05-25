from agent.perception import fast_classify_list


def test_kakie_zadachi_seychas():
    t = fast_classify_list("какие есть задачи сейчас?")
    assert t is not None
    assert t.intent == "list"
    assert t.list_scope == "active"


def test_ne_create_false_positive():
    assert fast_classify_list("поставь задачу проверить акты") is None


def test_overdue_scope():
    t = fast_classify_list("что просрочено?")
    assert t and t.list_scope == "overdue"


def test_chto_gotovo():
    t = fast_classify_list("что готово?")
    assert t is not None
    assert t.list_scope == "done"


def test_board_report_intent():
    t = fast_classify_list("отчёт за месяц по задачам")
    assert t is not None
    assert t.intent == "report"
    assert t.raw.get("period_label")


def test_artifact_intent():
    t = fast_classify_list("пришли файл по open webui")
    assert t is not None
    assert t.intent == "artifact"
    assert t.raw.get("topic_hint")
