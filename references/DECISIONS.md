# Architecture Decisions

Записываем решения, чтобы они не терялись между сессиями (durable knowledge,
agents-best-practices §8 — repeated context outside chat).

## ADR-001. Порядок работ

**Решение:** Сначала локальный MVP (Cursor → Kaiten), потом TG-вход с LLM.
**Статус:** реализован, smoke-test прошёл 2026-05-18.

## ADR-002. Среда исполнения TG-демона

**Изначально:** VPS.
**Пересмотр 2026-05-18 15:56:** **launchd на Маке** (пользователь подтвердил
«пока на маке разворачиваем»).
**Следствия:**
- Бот работает только когда Mac не спит. Длинный sleep → задачи накапливаются
  на TG-сервере и подтянутся при пробуждении.
- Secrets живут локально в `.env` (chmod 600), не на стороннем сервере.
- Артефакты ресёрча сразу видны в Finder в `~/Documents/kaiten-agent/artifacts/`.
- macOS notifications работают.
**Переезд на VPS** становится отдельной задачей фазы 3 — без переписывания
кода (Dockerfile приготовим заранее).

## ADR-003. Уровень автономии TG-входа: balanced

- `intent=create`, priority ∈ {P2, P3}, confidence ≥ `AUTO_CARD_MIN_CONFIDENCE` (0.85),
  без sensitive markers → карточка создаётся сразу.
- `intent=create`, priority=P1 ИЛИ confidence < 0.85 ИЛИ sensitive → preview +
  inline-кнопки [Создать] [Изменить] [Отмена].
- `intent=research`, confidence ≥ `AUTO_RESEARCH_MIN_CONFIDENCE` (0.75),
  без sensitive markers → авто-research → файл → карточка переезжает в «Готово».
- destructive и external-send → всегда кнопки.

**Sensitive markers** (regex/keywords, блокируют auto при любом confidence):
- `оплати|заплати|переведи|отправь деньги|сумма|счёт`
- `удали|снеси|drop|delete`
- `отправь клиенту|напиши клиенту|разошли|разослать`
- последовательность 13–19 цифр (карты)
- email-адрес рядом со словами «отправить/написать»

## ADR-004. LLM-роутинг

**Решение:** оба, по контексту.
- **DeepSeek `deepseek-v4-flash`** — классификация интента, извлечение полей,
  быстрые ответы бота.
- **DeepSeek `deepseek-v4-pro`** — финальный синтез research-отчёта.
- **Cursor (встроенная модель)** — интерактивные сессии в IDE.

API: OpenAI-совместимый (`base_url=https://api.deepseek.com`, header
`Authorization: Bearer ...`). Legacy id `deepseek-chat` deprecated с 2026-07-24.

**Контракт ответа LLM** (Pydantic-схема в `scripts/llm.py`):
```json
{
  "intent": "create|research|update|reminder|ambiguous",
  "title": "...",
  "icon": "📑",
  "description_md": "## Контекст\n...\n## Чек-лист\n...\n## Definition of done\n...",
  "priority": "P1|P2|P3",
  "due_date_iso": null,
  "tags": [],
  "owner_hint": null,
  "can_self_execute": false,
  "research_topic": null,
  "sensitive_markers": [],
  "confidence": 0.0
}
```

## ADR-005. Что значит «сделай сам»

Не голосовое разрешение, а **тип действия**:

| Тип | Auto-execute |
|---|---|
| Информационный поиск, документация, сравнение, сборка списка | да |
| Чтение/анализ внутренних данных | да |
| Запись локального артефакта | да |
| Создание карточки P2/P3 без sensitive | да |
| Создание карточки P1 | нет, кнопки |
| Любая отправка во внешний мир (email, message, webhook) | нет |
| Финансовые действия | нет, никогда |
| Удаление/изменение в Kaiten | нет, кнопки |
| Запуск Cursor agent на код | фаза 3 |

## ADR-006. Lane routing

P1 → lane `KAITEN_LANE_URGENT` (Срочно).
P2/P3 → lane `KAITEN_LANE_NORMAL` (Обычный приоритет).
Для других досок без двух дорожек — fallback на единственную доступную.

## ADR-007. Стиль карточек: эмодзи + DoD

При **каждом** create/update карточки:

1. В заголовке 1 эмодзи-икона по теме (📑 акты, 🤝 договор, 🧪 гипотезы,
   📚 обучение, 🚀 запуск, 🐛 баг, ⚙ инфра, 💰 финансы, 📞 звонок,
   🛒 закупка, 📝 документ, 🔍 ресёрч, ✅ готово, ⚠ риск).
2. Описание — markdown с секциями:
   - `## Контекст` или `## Задача`
   - `## Чек-лист` / `## Что сделать`
   - `## Ответственный` (если есть)
   - `## Definition of done`
3. Перед записью — preview в чат, ждём «да» / `approval_token` /
   inline-кнопку.
4. На фазе 2 эту структуру генерирует DeepSeek по контракту ADR-004.

## ADR-008. Auto-research поток

```text
TG message "изучи / разберись / найди..."
  -> intent_classifier (regex+sensitive) -> ok
  -> deepseek_extract -> {research_topic, can_self_execute=true, confidence}
  -> if confidence>=0.75:
       -> create card "🔍 [Ресёрч] <topic>", P3, lane=Обычный, column=Очередь
       -> reply TG: "🔄 Принял ресёрч «...» (#card_id)"
       -> move card -> column "Написание кода" (WIP)
       -> async research_runner:
            -> DeepSeek formulate 3-5 search queries
            -> DuckDuckGo HTML, top 3 results per query
            -> fetch each URL (urllib, 2MB cap, strip tags)
            -> DeepSeek-v4-pro synthesize markdown report (sources cited)
            -> save ~/Documents/kaiten-agent/artifacts/<date>-<slug>/report.md
            -> PUT file as Kaiten attachment
            -> add comment "Ресёрч готов. Источники: 5. Время: 2:14"
            -> move card -> column "Готово"
            -> reply TG: "✅ Готово, файл прикреплён к #card_id, ссылка..."
  -> if confidence<0.75 OR sensitive: preview + buttons
```

Бюджеты:
- `AUTO_RESEARCH_MAX_FETCHES=8`
- `AUTO_RESEARCH_WALL_TIME_SEC=300`
- макс. размер артефакта 5 MB

## ADR-009. DeepSeek параметры

- `response_format: {type: "json_object"}` для classify/extract.
- `temperature=0.2` для извлечения, `0.7` для синтеза.
- В user prompt всегда явное `"Output JSON only"` + пример схемы (требование
  DeepSeek для надёжной валидации JSON).
- Retry: 2 попытки, exponential backoff 1s/3s.
- Cost-cap: `max_tokens=1500` для extract, `4000` для synthesize.
- Если ответ не пройдёт Pydantic — один retry с `temperature=0.0`,
  потом fallback на `intent=ambiguous` + preview.

## Open questions для фазы 3

1. VPS-перенос: Dockerfile уже в коробке, нужен ли webhook вместо long polling?
2. Voice → text: TG voice messages через DeepSeek (когда появится whisper-like)?
3. Multi-board routing: классификатор «куда» по содержанию.
4. Cursor agent на код: автоматический PR для intent=code.
