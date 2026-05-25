from llm import ExtractedTask, _apply_short_description, kaiten_description


def test_kaiten_description_prefers_short():
    t = ExtractedTask(
        title="T",
        short_description="Коротко о задаче.",
        description_md="## Длинно\nмного текста",
    )
    assert kaiten_description(t) == "Коротко о задаче."


def test_fallback_short_from_md():
    t = ExtractedTask(
        title="Заголовок",
        description_md="## Контекст\nПервое предложение. Второе. Третье.",
    )
    _apply_short_description(t, {})
    assert "Первое" in t.short_description
