# kaiten-task-agent

Personal Cursor skill: создаёт карточки в Kaiten, напоминает о задачах, спрашивает
приоритет, а исследовательские/документационные подзадачи делает сам и складывает
результат в `~/Documents/kaiten-agent/artifacts/`.

Спроектировано по [agents-best-practices](https://github.com/DenisSergeevitch/agents-best-practices):
модель предлагает — harness валидирует, спрашивает разрешение, выполняет, логирует.

## Docker Compose (VPS)

```bash
cd /opt/kaiten-agent   # после rsync проекта и .env на сервер
docker compose up -d --build
docker compose logs -f kaiten-bot
```

Подробно: `~/Documents/kaiten-agent/docs/05-docker-compose.md`

## Установка (macOS / launchd)

```bash
# 1. этот skill уже в ~/.cursor/skills/kaiten-task-agent/

# 2. конфиг
cd ~/.cursor/skills/kaiten-task-agent
cp .env.example .env
# отредактируй: KAITEN_BASE_URL, KAITEN_API_TOKEN, KAITEN_DEFAULT_BOARD_ID, KAITEN_DEFAULT_COLUMN_ID

# 3. найти board_id / column_id
python3 scripts/kaiten_api.py list-boards
python3 scripts/kaiten_api.py list-columns --board-id <id>

# 4. preflight
python3 scripts/preflight.py

# 5. cron (по желанию)
( crontab -l 2>/dev/null; cat <<EOF
0 9 * * 1-5 $HOME/.cursor/skills/kaiten-task-agent/scripts/daily_reminder.sh morning
0 18 * * 1-5 $HOME/.cursor/skills/kaiten-task-agent/scripts/daily_reminder.sh evening
EOF
) | crontab -
```

## Использование в Cursor

Skill активируется автоматически. Примеры реплик:

- «Поставь задачу обновить лендинг к пятнице» → агент уточнит приоритет, покажет preview, спросит «да?», создаст карточку.
- «Что сегодня?» / «Что просрочено?» → digest и macOS-уведомление.
- «Сдвинь #1402 на завтра, приоритет P3» → preview с diff, после «да» — PATCH.
- «Изучи best practices монорепо Next.js» → planning mode, ресёрч, markdown в `artifacts/`, карточка P3 с ссылкой.

## Структура

```
~/.cursor/skills/kaiten-task-agent/
├── SKILL.md
├── README.md
├── .env.example
├── requirements.txt
├── references/
│   ├── BLUEPRINT.md      # полный MVP blueprint
│   ├── PERMISSIONS.md    # матрица прав
│   ├── PROMPTS.md        # шаблоны под интенты
│   └── SETUP.md          # установка по шагам
├── prompts/              # версионируемые промпты
├── tests/                # pytest (policy, validation, harness)
└── scripts/
    ├── agent/            # harness: policy, tools, workflows, jobs
    ├── agent_cli.py      # list-tools / tool-schema
    ├── bot.py            # Telegram (через harness)
    ├── kaiten_api.py     # typed tools + envelope
    ├── llm.py            # classify + synthesize
    ├── research.py       # web + report pipeline
    ├── preflight.py
    ├── notify.py
    └── daily_reminder.sh

~/Documents/kaiten-agent/artifacts/   # отчёты ресёрча

~/Library/Application Support/kaiten-agent/
├── logs/calls.jsonl      # аудит (trace_id, run_id)
├── state/jobs.sqlite     # job ledger + idempotency
├── state/pending.json    # TG approvals
└── state/session.json    # drafts
```

## Безопасность

- **Cursor:** write-операции Kaiten — preview + `--commit`.
- **Telegram:** автономия через `KAITEN_AGENT_AUTO_CARDS` / `KAITEN_AGENT_AUTO_RESEARCH` (см. `references/ADR-005-agent-harness.md`).
- Все TG side effects — через `AgentHarness` (rate limits, trace).
- `delete_card` — HMAC + кнопка подтверждения.
- Отчёт в «Готово» только если `validation.ok` (полные разделы, не обрезан).

## Тесты

```bash
cd ~/.cursor/skills/kaiten-task-agent
pip install -r requirements-dev.txt
PYTHONPATH=scripts pytest tests/ -q
```
