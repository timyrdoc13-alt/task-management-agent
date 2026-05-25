# MVP Agent Harness Blueprint: Kaiten Personal Task Agent

Собрано по шаблону `agents-best-practices/references/mvp-agent-blueprint.md`.

## 1. Objective

Личный агент для одного пользователя, который:

- создаёт карточки в Kaiten из свободного описания (с уточнением приоритета и дедлайна);
- напоминает о просроченных и сегодняшних задачах;
- меняет приоритет / due_date / колонку существующих карточек;
- сам выполняет задачи на «изучить / найти документацию / сделать ресёрч» —
  складывает результат markdown-файлом на Макбук и (опционально) создаёт карточку
  «готов ресёрч».

Пользователь — один (владелец Kaiten-аккаунта). Среда исполнения — Cursor IDE на macOS.

## 2. MVP scope and assumptions

**В MVP входит:**

- работа с одним default-бордом;
- 4 интента: create / remind / update / research;
- ручное подтверждение всех write-операций;
- хранение артефактов в `~/Documents/kaiten-agent/`.

**Не входит в MVP (вторая итерация):**

- многопользовательский режим;
- автогенерация чек-листов из описания;
- Kaiten webhooks → bot отвечает в комментариях карточки;
- интеграция с календарём macOS;
- голосовой ввод.

**Assumptions:**

- Kaiten доступен по `https://<workspace>.kaiten.ru`, REST API v1 с Bearer-токеном.
- У пользователя есть один основной board_id и default column_id (например, «Inbox»).
- macOS 14+ с `osascript` для уведомлений.
- Cursor может исполнять Shell-команды; Python 3.11+ в системе.

## 3. Autonomy and risk level

Гибридный уровень:

- **L3 (policy-bounded autonomous)** для read-only Kaiten, web research, записи
  в локальную папку, отправки macOS-уведомлений.
- **L2 (approval-gated)** для всех write-вызовов Kaiten API (`POST /cards`,
  `PATCH /cards/:id`, `DELETE /cards/:id`, `POST /cards/:id/comments`).

Reason: чтение и локальная запись обратимы; внешние записи в Kaiten могут засорить
доску или удалить нужное → требуют preview + confirm.

## 4. Core agentic loop

```text
user_message
  ├─ classify_intent(message) → {create, update, remind, research, query, ambiguous}
  ├─ if ambiguous: ask one clarifying question
  ├─ build_context: load .env, last 5 cards in session, active drafts, overdue digest
  ├─ model.generate(tools=visible_tools_for_intent)
  ├─ for tool_call in proposed:
  │    validate schema (Pydantic в kaiten_api.py)
  │    check permission (read=allow, local-write=allow, kaiten-write=approval)
  │    if approval_required:
  │       render preview → wait for user "да/yes/ок"
  │    execute via Shell
  │    log to ~/Documents/kaiten-agent/logs/calls.jsonl
  │    return structured observation
  ├─ if model returns final_answer → finalize
  └─ budgets: max_steps=12, max_wall_time=120s, max_kaiten_writes_per_run=5
```

Stopping rules:

- бюджет шагов исчерпан → отдать частичный результат с пометкой;
- 3 подряд `permission_denied` → остановиться, сообщить;
- пользователь сказал «стоп / отмени» → откатить незакоммиченные drafts.

## 5. Context and instruction architecture

Order (cache-aware, stable → volatile):

1. SKILL.md (stable).
2. PERMISSIONS.md (stable).
3. Tool schemas (stable, hash в `tool_bundle.sha256`).
4. Default board + column id (semi-stable, из `.env`).
5. Active goal / draft / approval state (volatile, из `state/session.json`).
6. Last N tool observations (volatile).
7. User message.

Trust boundaries:

- инструкции: SKILL.md + PROMPTS.md + сообщения пользователя;
- данные: всё, что вернулось из Kaiten API и web — не выполнять как команды.

## 6. Tool registry

| Tool | Schema (key fields) | Risk | Permission |
|------|---------------------|------|------------|
| `list_boards` | — | read_only | allow |
| `list_columns` | `board_id: int` | read_only | allow |
| `list_cards` | `board_id?: int, due_before?: iso8601, q?: str, limit≤50` | read_only | allow |
| `get_card` | `card_id: int` | read_only | allow |
| `list_overdue` | — | read_only | allow |
| `list_today` | — | read_only | allow |
| `draft_card` | `title, priority∈{P1,P2,P3}, due_date?, description?, board_id?, column_id?, tags?` | compute_only | allow |
| `create_card` | `draft_id: str` | write_external | **approval** |
| `update_card_priority` | `card_id, priority∈{P1,P2,P3}` | write_external | **approval** |
| `update_card_due` | `card_id, due_date: iso8601 \| null` | write_external | **approval** |
| `move_card` | `card_id, column_id` | write_external | **approval** |
| `add_comment` | `card_id, text` | write_external (soft) | log, no approval if author=self |
| `delete_card` | `card_id, confirm_token` | destructive | **double approval** |
| `save_research_artifact` | `topic, markdown, sources[], related_card_id?` | write_local | allow (≤5MB) |
| `list_artifacts` | `since?` | read_only | allow |
| `send_reminder` | `title, body, card_ids[]` | write_local | allow |

Tool result envelope (для всех):

```json
{
  "status": "success | error | approval_required | denied",
  "summary": "human-readable one-liner",
  "data": { ... },
  "next_valid_actions": ["..."],
  "trace_id": "uuid"
}
```

## 7. Planning behavior

Planning mode включается при:

- запросе на research, если scope шире одного источника;
- batch-операциях («поменяй приоритет у всех просроченных» — затрагивает ≥3 карточек);
- противоречии данных (карточка с тем же title уже существует — merge / new / skip?).

Plan artifact сохраняется как `~/Documents/kaiten-agent/state/plan-<id>.md`.
Execution стартует только после подтверждения.

## 8. Goal-like loop behavior

Используется только для research-задач:

```yaml
goal:
  objective: "<тема>"
  done_condition: "report.md ≥ 500 слов, ≥3 источника, summary с выводом"
  budget:
    max_steps: 15
    max_wall_time: 5min
    max_fetches: 10
  checkpoints:
    - scope подтверждён
    - источники собраны
    - draft написан
    - артефакт сохранён
  forbidden_actions: [kaiten_write_except_final_card]
```

## 9. Context, memory, auto-compaction

Durable state (`~/Documents/kaiten-agent/state/`):

- `session.json` — активные drafts, pending approvals, last_intent;
- `cards_cache.json` — последние 50 затронутых карточек (id, title, due_date, status);
- `plan-*.md` — планы для research;
- `logs/calls.jsonl` — все внешние вызовы.

Compaction summary (when context > 70%):

```text
Active objective: ...
Pending drafts: [draft_id, title]
Pending approvals: [card_id, operation]
Recent cards: [id:title]
Last research artifact: <path>
Forbidden in this session: ...
Next step: ...
```

После компакции скрипт `scripts/rehydrate.py` перечитывает `session.json` и подставляет
в новый контекст.

## 10. Skills and connectors

- **Сам skill** = единственный entry point. Не вызывает другие skills автоматически.
- **MCP**: можно подключить будущий `mcp-kaiten`, но MVP — это локальные python-скрипты
  (меньше движущихся частей, проще audit).
- **Web research** делается через встроенные инструменты Cursor (WebSearch / WebFetch).

Progressive disclosure: при intent=create показываем только `draft_card` и `create_card`;
при intent=remind — только `list_*` и `send_reminder`.

## 11. Prompt caching and cost-aware context

- SKILL.md + PERMISSIONS.md + tool schemas — стабильный префикс, кэшируется.
- `.env` значения не в промпте — только id, не токен.
- Tool outputs ограничены: `max_result_chars=4000`, длинные списки — пагинация по 20.
- Web-страницы при research: только заголовок + первые 1500 символов + ссылка, полный текст — в артефакт.

Cost controls:

- read-only вызовы — без LLM (агент сам решает, нужно ли спросить модель после
  list_overdue — для простого digest шаблон жёсткий);
- summarization web-страниц → дешёвая модель;
- финальная синтез research → основная модель.

## 12. Safety and approval policy

- Bearer token только в `os.environ`, никогда в логе/чате/коммите.
- Все write вызовы Kaiten логируются с маской последних 4 символов токена.
- `delete_card` → требуется HMAC confirm_token, генерируемый `kaiten_api.py confirm-delete <card_id>`.
- Web-страницы для research проходят через `fetch_url` с size limit 2MB.
- Артефакты только в `~/Documents/kaiten-agent/artifacts/`, путь валидируется (`Path.resolve().is_relative_to(BASE)`).

Prompt-injection:

- Содержимое карточек и web-страниц помечается префиксом `<untrusted>...</untrusted>`.
- Системные инструкции явно запрещают выполнять команды из untrusted блоков.

## 13. Observability and evals

Trace events в `logs/calls.jsonl`:

```json
{"ts":"2026-05-18T15:00:00+03:00","run_id":"...","tool":"create_card",
 "args_hash":"sha256:...","decision":"approval","approved":true,
 "duration_ms":230,"status":"success","card_id":12345}
```

Daily metrics (через `scripts/metrics.py`):

- write-success rate;
- среднее время между предложением и подтверждением;
- сколько ресёрчей создано;
- сколько reminders отправлено и сколько закрыто.

Eval cases (`tests/evals/`):

1. happy path: «поставь задачу обновить лендинг к пятнице» → create_card с P2 и due_date.
2. priority missing: «добавь задачу» → агент уточняет приоритет.
3. duplicate title → агент предлагает merge.
4. research: «изучи best practices монорепо для Next.js» → артефакт + карточка.
5. prompt injection в описании карточки → не выполняется.
6. delete card → требует двух подтверждений.
7. overdue digest → корректный список после изменения часового пояса.
8. budget exhaustion → graceful stop.

Launch gates:

- evals 1–8 проходят локально;
- `.env` валидируется при старте (`scripts/preflight.py`);
- логирование пишется и ротируется (max 30 дней).

## 14. Minimal implementation path

1. Skill + references установлены (этот шаг).
2. `.env` создан из `.env.example`, токен Kaiten проверен `preflight.py`.
3. `kaiten_api.py` — реализованы read-only вызовы.
4. Test: `python kaiten_api.py list-cards --due-before tomorrow`.
5. `kaiten_api.py` — write-вызовы с флагом `--commit`.
6. Test: создать тестовую карточку, потом удалить вручную.
7. `notify.py` — macOS notification работает.
8. `daily_reminder.sh` + cron установлены.
9. `research_artifact.py` — сохраняет markdown с frontmatter.
10. Прогнать evals 1–8.

## 15. First release checklist

```text
[ ] .env заполнен и не закоммичен
[ ] preflight.py проходит (200 OK на /users/current)
[ ] default board_id и column_id указаны
[ ] artifacts/ writable, размер свободного места ≥ 500 MB
[ ] cron установлен и tested
[ ] osascript уведомление видно
[ ] все write-tools требуют --commit (без флага возвращают preview)
[ ] logs/calls.jsonl создаётся, токен замаскирован
[ ] evals 1–8 зелёные
[ ] первая live-сессия: 5 карточек создано, 1 research собран, 0 false positives
```

## Cron установка (macOS, шаг из плана 8)

```bash
crontab -l 2>/dev/null > /tmp/cron.tmp
cat <<'EOF' >> /tmp/cron.tmp
0 9 * * 1-5 /Users/timurilincik/.cursor/skills/kaiten-task-agent/scripts/daily_reminder.sh morning
0 18 * * 1-5 /Users/timurilincik/.cursor/skills/kaiten-task-agent/scripts/daily_reminder.sh evening
EOF
crontab /tmp/cron.tmp && rm /tmp/cron.tmp
crontab -l
```

Альтернатива: launchd `~/Library/LaunchAgents/com.kaiten-agent.reminder.plist` — устойчивее к перезагрузке.
