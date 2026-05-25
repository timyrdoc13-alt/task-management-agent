# ADR-005. Полноценный agent harness

**Статус:** принято 2026-05-18

## Решение

Ввести пакет `scripts/agent/` как control plane:

- `harness.py` — policy → rate limit → tool → observation + trace
- `registry.py` + `tools.py` — именованные tools с risk class
- `policy.py` — единая автономия (`KAITEN_AGENT_AUTO_CARDS`, `KAITEN_AGENT_AUTO_RESEARCH`)
- `job_store.py` — SQLite jobs + idempotency
- `validation.py` — gate перед `move_to_done`
- `workflows.py` — research / create_card как multi-step workflows
- `pending_store.py` — approvals на диске
- `process_lock.py` — один экземпляр бота

## Каналы

| Канал | Writes |
|-------|--------|
| `cursor` / `cli` | `agent_cli execute-tool --commit --approved` или preview без commit |
| `telegram` | auto если policy + env flags; иначе inline preview → `approved=True` |

## Единый control plane

Все side effects (Kaiten + отчёт по доске + поиск артефакта) — через `AgentHarness.execute_tool`.
Прямой `kaiten_api` в боте не используется; CLI `kaiten_api.py` остаётся для ручной отладки.

Зарегистрированные tools: см. `python scripts/agent_cli.py list-tools`

## Env

- `KAITEN_AGENT_AUTO_CARDS=true` — TG auto-create P2/P3
- `KAITEN_AGENT_AUTO_RESEARCH=true` — TG full research pipeline
- `AUTO_RESEARCH_ENABLED` — master kill switch
