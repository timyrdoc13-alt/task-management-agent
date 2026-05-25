# Setup — Kaiten Task Agent

## 1. Заполнить .env

```bash
cd ~/.cursor/skills/kaiten-task-agent
cp .env.example .env
# отредактируй .env: токен берётся в Kaiten → Профиль → API-токены
```

Требуемые переменные:

```dotenv
KAITEN_BASE_URL=https://<workspace>.kaiten.ru
KAITEN_API_TOKEN=<bearer-token>
KAITEN_DEFAULT_BOARD_ID=<id>
KAITEN_DEFAULT_COLUMN_ID=<id>      # обычно "Очередь" / "Inbox"
KAITEN_DEFAULT_LANE_ID=<id>        # дефолтная дорожка
TZ=Europe/Moscow

# Поведение
KAITEN_AGENT_AUTO_CARDS=false      # автосоздание карточек после research
KAITEN_AGENT_NO_AUTOCOMMENT=false  # запретить автокомменты
KAITEN_AGENT_LOG_LEVEL=info
```

## 2. Установить зависимости

```bash
python3 -m venv ~/.cursor/skills/kaiten-task-agent/.venv
source ~/.cursor/skills/kaiten-task-agent/.venv/bin/activate
pip install -r ~/.cursor/skills/kaiten-task-agent/requirements.txt
```

## 3. Найти board_id и column_id

```bash
python3 scripts/kaiten_api.py list-boards
python3 scripts/kaiten_api.py list-columns --board-id <id>
```

Запиши id в `.env`.

## 4. Preflight (проверка)

```bash
python3 scripts/preflight.py
```

Должно вывести:

```
✓ .env loaded
✓ token valid (user: <ваше имя>)
✓ board <id> exists ("<title>")
✓ column <id> exists ("<title>")
✓ artifacts dir writable: ~/Documents/kaiten-agent/artifacts
✓ osascript available
ready.
```

## 5. Cron / launchd

Cron вариант:

```bash
( crontab -l 2>/dev/null; cat <<EOF
0 9 * * 1-5 $HOME/.cursor/skills/kaiten-task-agent/scripts/daily_reminder.sh morning
0 18 * * 1-5 $HOME/.cursor/skills/kaiten-task-agent/scripts/daily_reminder.sh evening
EOF
) | crontab -
```

launchd вариант — см. `scripts/com.kaiten-agent.reminder.plist` (создаётся при первом запуске).

## 6. Тест-карточка

В Cursor чате:

```
поставь задачу проверить kaiten-agent, P3, сегодня
```

Должен прийти preview, после "да" — карточка появится. После — удали её вручную из UI или:

```
удали #<id>
```

## 7. Логи

- `~/Documents/kaiten-agent/logs/calls.jsonl` — все вызовы;
- `~/Documents/kaiten-agent/logs/reminders.log` — отправленные напоминания;
- `~/Documents/kaiten-agent/state/session.json` — drafts, approvals;
- `~/Documents/kaiten-agent/artifacts/` — результаты ресёрча.

Ротация: 30 дней, скрипт `scripts/rotate_logs.sh` (в cron, 0 3 * * 0).
