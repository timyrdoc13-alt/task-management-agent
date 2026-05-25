# Permission Matrix — Kaiten Task Agent

| Action | Risk class | Default | Override | Audit |
|--------|-----------|---------|----------|-------|
| list_boards / list_columns / list_cards / get_card / list_overdue / list_today | read_only | **allow** | — | log only on debug |
| save_research_artifact | write_local | **allow** if path ⊂ `~/Documents/kaiten-agent/artifacts/` and size ≤ 5MB | — | log path + size |
| send_reminder | write_local | **allow** | — | log |
| web_search / fetch_url | search_only | **allow**, ≤10 fetches per run, ≤2MB per page | — | log url + status |
| draft_card | compute_only | **allow**, no side effects | — | log |
| create_card | write_external | **approval_required** | `KAITEN_AGENT_AUTO_CARDS=true` only for research-result cards (P3) | log args hash + result card_id |
| update_card_priority | write_external | **approval_required** | — | log before/after |
| update_card_due | write_external | **approval_required** | — | log before/after |
| move_card | write_external | **approval_required** | — | log before/after column |
| add_comment (author=self) | write_external_soft | **allow**, log | `KAITEN_AGENT_NO_AUTOCOMMENT=true` блокирует | log text length |
| delete_card | destructive | **double approval** (HMAC token from `kaiten_api.py confirm-delete <id>`) | — | log + keep title snapshot 30 days |
| install MCP / new connector | privileged | **deny in MVP** | manual user action only | n/a |

## Approval flow

```text
1. Model proposes write tool call.
2. Harness wraps in preview JSON:
   { "operation": "create_card",
     "args": { ... },
     "diff": { ... },                # для update — before/after
     "approval_token": "ak_<random6>" }
3. Cursor рисует это и ждёт пользователя.
4. Пользователь отвечает текстом, содержащим approval_token, либо "yes/да/ок".
5. Harness шлёт реальный запрос, сохраняет trace.
6. На любой ответ кроме явного approve — denied_result("user_rejected").
```

## Rate limits

| Bucket | Limit | Window |
|--------|-------|--------|
| Kaiten reads | 60 | per minute |
| Kaiten writes | 5 | per run (max_kaiten_writes_per_run) |
| Web fetches | 10 | per run |
| send_reminder | 5 | per hour |
| save_research_artifact | 3 | per run |

Превышение → `rate_limited` со временем повтора.

## Secrets handling

- `KAITEN_API_TOKEN` читается только в `scripts/*.py`, не передаётся в STDOUT/STDERR.
- В чате токен никогда не цитируется; маска `***last4`.
- При логировании headers — `Authorization: Bearer ***<last4>`.
- `.env` в `.gitignore`.
