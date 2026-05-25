"""DeepSeek client + Pydantic schemas + intent classifier with regex prefilter.

Architecture:
- regex prefilter detects sensitive markers BEFORE calling LLM (cheap safety).
- DeepSeek extracts structured fields per ADR-004 schema.
- Result is validated; if invalid → one retry with temperature=0, then fallback.
"""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any

from kaiten_api import load_env

ENV = load_env()


SENSITIVE_PATTERNS = [
    (r"\b(оплати|заплати|переведи|отправь\s+деньги|выстави\s+счёт)\b", "financial"),
    (r"\b(удали|снеси|стер[еи]|drop\s+table|delete\s+from)\b", "destructive"),
    (r"\b(отправь|напиши|разошл[иёе])\s+(клиенту|подрядчику|партн[её]ру)\b", "external_send"),
    (r"\b\d{13,19}\b", "card_number_like"),
    (r"[\w\.-]+@[\w\.-]+\.\w+", "email_in_action"),
]


def detect_sensitive(text: str) -> list[str]:
    """Return list of sensitive marker types found in text."""
    found = []
    low = text.lower()
    for pat, label in SENSITIVE_PATTERNS:
        if re.search(pat, low, re.U):
            if label == "email_in_action":
                # only flag email if there's also an action verb nearby
                if re.search(r"\b(отправ|напиш|разошл|пошл)", low, re.U):
                    found.append(label)
            else:
                found.append(label)
    return list(dict.fromkeys(found))


@dataclass
class ExtractedTask:
    intent: str = "ambiguous"  # create | research | update | reminder | list | report | artifact | ambiguous
    list_scope: str | None = None  # active | today | overdue | digest | all
    title: str = ""
    icon: str = "📝"
    description_md: str = ""
    short_description: str = ""  # 2–3 предложения для превью и описания в Kaiten
    priority: str = "P2"  # P1 | P2 | P3
    due_date_iso: str | None = None
    tags: list[str] = field(default_factory=list)
    owner_hint: str | None = None
    can_self_execute: bool = False
    research_topic: str | None = None
    sensitive_markers: list[str] = field(default_factory=list)
    confidence: float = 0.0
    raw: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "intent": self.intent,
            "title": self.title,
            "icon": self.icon,
            "description_md": self.description_md,
            "short_description": self.short_description,
            "priority": self.priority,
            "due_date_iso": self.due_date_iso,
            "tags": self.tags,
            "owner_hint": self.owner_hint,
            "can_self_execute": self.can_self_execute,
            "research_topic": self.research_topic,
            "sensitive_markers": self.sensitive_markers,
            "confidence": self.confidence,
            "list_scope": self.list_scope,
        }


SYSTEM_PROMPT = """Ты — классификатор задач для личного PM-агента. Получаешь свободный текст по-русски.
Возвращай **только JSON** строго по схеме (ничего вне JSON).

Поля:
- intent: "create" (поставить задачу человеку или себе),
          "research" (изучить/найти/собрать инфу — можно сделать самому),
          "update" (изменить существующую карточку),
          "reminder" (синоним list: просрочено + сегодня — устаревший, предпочитай list),
          "list" (показать задачи на доске / список / что сейчас / что в работе),
          "report" (сводка по доске: сколько сделано/новых/на стопе за месяц или неделю),
          "artifact" (файл результата ресёрча DOCX: по #карточке, по теме, или последний),
          "ambiguous" (не понятно — нужно уточнить).
- list_scope: только если intent=list или reminder: "active" | "today" | "overdue" | "digest" | "all" | "done" | "wip" | "queue".
  - active — очередь + в работе (по умолчанию для «какие задачи сейчас»);
  - done — ТОЛЬКО колонка «Готово» («что готово», «что завершено»);
  - wip / queue — только одна колонка;
  - today / overdue — по дедлайну;
  - digest — просрочено + сегодня;
  - all — все три колонки.
- title: короткий заголовок без эмодзи, 5–80 символов.
- icon: один уместный эмодзи (📑 акты, 🤝 договор, 🧪 гипотезы, 📚 обучение,
        🚀 запуск, 🐛 баг, ⚙ инфра, 💰 финансы, 📞 звонок, 🛒 закупка,
        📝 документ, 🔍 ресёрч, ⚠ риск, 📅 встреча).
- short_description: суть задачи 2–3 коротких предложения plain text (без markdown),
                   для превью в Telegram и поля description в Kaiten.
- description_md: markdown с секциями ## Контекст, ## Чек-лист, ## Ответственный
                  (если есть), ## Definition of done. 4–12 строк (детали).
- priority: P1 (срочно/asap/горит), P2 (обычно), P3 (низкий).
- due_date_iso: YYYY-MM-DD или null. "сегодня"=today, "завтра"=today+1,
                "к пятнице"=ближайшая пятница, "на этой неделе"=пятница.
- tags: до 4 коротких тэгов на латинице/кириллице.
- owner_hint: имя человека, если упомянут как ответственный, иначе null.
- can_self_execute: true ТОЛЬКО если intent=research и задача чисто
                    информационная без внешней отправки.
- research_topic: суть исследования одной строкой, если intent=research (обычно: ИИ, агенты, LLM/RAG, цифровая трансформация в банке).
- sensitive_markers: пустой массив (его заполнит другой код).
- confidence: 0.0..1.0, насколько уверен в извлечении.

Пример:
Вход: "Срочно проверить акты по 315 спеке, я сам"
Выход:
{"intent":"create","title":"Проверить акты по 315 спеке","icon":"📑",
 "short_description":"Проверить комплект актов по спецификации 315. Сверить позиции, подписи и объёмы. Зафиксировать замечания или подтвердить комплект.",
 "description_md":"## Задача\\nПроверить акты по спецификации 315.\\n\\n## Чек-лист\\n- [ ] Сверить позиции\\n- [ ] Подписи и даты\\n- [ ] Объёмы\\n\\n## Ответственный\\nЯ (Тимур)\\n\\n## Definition of done\\nВсе акты по 315 подтверждены или заведены замечания.",
 "priority":"P1","due_date_iso":null,"tags":["акты","315"],
 "owner_hint":"Тимур","can_self_execute":false,
 "research_topic":null,"sensitive_markers":[],"confidence":0.92}
"""


def _is_transient_network_err(exc: BaseException) -> bool:
    if isinstance(exc, (ConnectionResetError, BrokenPipeError)):
        return True
    if isinstance(exc, OSError) and getattr(exc, "errno", None) in (54, 32, 104):
        return True
    msg = str(exc).lower()
    return "connection reset" in msg or "broken pipe" in msg


def _deepseek_request(
    api_key: str, base: str, payload: dict, timeout: int,
) -> urllib.request.Request:
    """Build a fresh Request (must not be reused across retries — body is consumed)."""
    req = urllib.request.Request(
        f"{base}/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
    )
    req.add_header("Authorization", f"Bearer {api_key}")
    req.add_header("Content-Type", "application/json")
    return req


def _call_deepseek(messages: list[dict], model: str | None = None,
                   temperature: float = 0.2, max_tokens: int = 1500,
                   json_mode: bool = True, timeout: int = 60) -> dict:
    api_key = ENV.get("DEEPSEEK_API_KEY")
    base = ENV.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com").rstrip("/")
    if not api_key:
        raise RuntimeError("DEEPSEEK_API_KEY missing")
    payload = {
        "model": model or ENV.get("DEEPSEEK_MODEL_FAST", "deepseek-v4-flash"),
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if json_mode:
        payload["response_format"] = {"type": "json_object"}
    max_attempts = int(ENV.get("DEEPSEEK_MAX_RETRIES", "5"))
    last_err: str | None = None
    for attempt in range(max_attempts):
        req = _deepseek_request(api_key, base, payload, timeout)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as e:
            last_err = f"HTTP {e.code}: {e.read().decode('utf-8', 'replace')[:200]}"
            if e.code in {429, 502, 503, 504} and attempt < max_attempts - 1:
                time.sleep(min(2.0 ** attempt, 16.0))
                continue
            break
        except Exception as e:
            last_err = str(e)
            if attempt >= max_attempts - 1:
                break
            if _is_transient_network_err(e):
                time.sleep(min(2.0 ** attempt, 16.0))
            else:
                time.sleep(1.0 if attempt == 0 else 3.0)
    raise RuntimeError(f"DeepSeek failed: {last_err}")


def stream_deepseek(
    messages: list[dict],
    model: str | None = None,
    temperature: float = 0.3,
    max_tokens: int = 1200,
    timeout: int = 120,
):
    """Yield text deltas from DeepSeek chat/completions (stream=True)."""
    api_key = ENV.get("DEEPSEEK_API_KEY")
    base = ENV.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com").rstrip("/")
    if not api_key:
        raise RuntimeError("DEEPSEEK_API_KEY missing")
    payload = {
        "model": model or ENV.get("DEEPSEEK_MODEL_FAST", "deepseek-v4-flash"),
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": True,
    }
    req = _deepseek_request(api_key, base, payload, timeout)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            for raw in resp:
                line = raw.decode("utf-8", errors="replace").strip()
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                try:
                    obj = json.loads(data)
                except json.JSONDecodeError:
                    continue
                choices = obj.get("choices") or []
                if not choices:
                    continue
                delta = choices[0].get("delta") or {}
                piece = delta.get("content") or ""
                if piece:
                    yield piece
    except Exception as e:
        raise RuntimeError(f"DeepSeek stream failed: {e}") from e


TLDR_STREAM_PROMPT = """Ты — аналитик R&D банка. Пишешь КРАТКОЕ резюме для Telegram (plain text).
5–7 нумерованных пунктов, 1–2 предложения каждый. Только факты из данных пользователя.
Без markdown-заголовков, без mermaid. Если данных мало — честно укажи пробелы.
{domain_context}"""


def stream_research_tldr(
    topic: str,
    sources_text: list[dict],
    facts: dict | None = None,
):
    """Stream a short TL;DR preview (fast model) for live TG updates."""
    domain = _research_domain_block()
    ctx_parts = []
    if facts:
        ctx_parts.append("Факты (JSON):\n" + json.dumps(facts, ensure_ascii=False)[:12000])
    else:
        for i, s in enumerate(sources_text[:6]):
            ctx_parts.append(
                f"[{i+1}] {s.get('title','')}\n{s.get('text','')[:1200]}"
            )
    messages = [
        {"role": "system", "content": TLDR_STREAM_PROMPT.format(domain_context=domain)},
        {
            "role": "user",
            "content": (
                f"Тема: {topic}\n\nДанные:\n" + "\n\n".join(ctx_parts) + "\n\nРезюме:"
            ),
        },
    ]
    yield from stream_deepseek(
        messages,
        model=ENV.get("DEEPSEEK_MODEL_FAST", "deepseek-v4-flash"),
        temperature=0.25,
        max_tokens=int(ENV.get("RESEARCH_TLDR_MAX_TOKENS", "900")),
        timeout=int(ENV.get("RESEARCH_TLDR_TIMEOUT_SEC", "90")),
    )


def _fallback_short(description_md: str, title: str) -> str:
    plain = re.sub(r"[#*_`\[\]]+", " ", description_md or "")
    plain = re.sub(r"\s+", " ", plain).strip()
    if plain:
        parts = re.split(r"(?<=[.!?])\s+", plain)
        short = " ".join(parts[:3]).strip()
        if short:
            return short[:500]
    return (title or "Задача")[:240]


def _apply_short_description(task: ExtractedTask, data: dict) -> None:
    short = str(data.get("short_description", "")).strip()
    if not short:
        short = _fallback_short(task.description_md, task.title)
    task.short_description = short[:500]


def _task_from_llm_data(
    data: dict,
    *,
    sensitive: list[str],
    user_text: str | None = None,
) -> ExtractedTask:
    task = ExtractedTask(
        intent=str(data.get("intent", "ambiguous")),
        title=str(data.get("title", "")).strip()[:240],
        icon=str(data.get("icon", "📝"))[:4],
        description_md=str(data.get("description_md", "")),
        priority=str(data.get("priority", "P2")).upper(),
        due_date_iso=data.get("due_date_iso") or None,
        tags=[str(t)[:30] for t in (data.get("tags") or [])][:4],
        owner_hint=data.get("owner_hint") or None,
        can_self_execute=bool(data.get("can_self_execute")),
        research_topic=data.get("research_topic") or None,
        list_scope=data.get("list_scope") or None,
        sensitive_markers=sensitive,
        confidence=float(data.get("confidence", 0.0)),
        raw=dict(data),
    )
    _apply_short_description(task, data)
    if user_text:
        task.raw["user_text"] = user_text
    return task


REVISE_TASK_PROMPT = """Ты редактор черновика задачи для Kaiten (русский).
Получишь JSON черновика и правки пользователя. Верни **только JSON** с теми же полями, что в схеме классификатора:
intent, title, icon, short_description (2–3 предложения), description_md, priority, due_date_iso,
tags, owner_hint, can_self_execute, research_topic, list_scope, confidence.
Меняй только то, о чём просит пользователь; остальное сохрани. short_description всегда обнови под новый смысл."""


def revise_task_draft(task: ExtractedTask, user_feedback: str) -> ExtractedTask:
    """Refine draft fields from free-text user corrections (preview loop)."""
    feedback = (user_feedback or "").strip()
    if not feedback:
        return task
    payload = task.to_dict()
    messages = [
        {"role": "system", "content": REVISE_TASK_PROMPT},
        {
            "role": "user",
            "content": (
                f"Черновик:\n{json.dumps(payload, ensure_ascii=False)}\n\n"
                f"Правки пользователя:\n{feedback}\n\nВерни JSON."
            ),
        },
    ]
    try:
        resp = _call_deepseek(messages, temperature=0.15)
        data = json.loads(resp["choices"][0]["message"]["content"])
    except Exception:
        try:
            resp = _call_deepseek(messages, temperature=0.0)
            data = json.loads(resp["choices"][0]["message"]["content"])
        except Exception:
            out = ExtractedTask(
                intent=task.intent,
                title=task.title,
                icon=task.icon,
                description_md=(task.description_md or "")
                + f"\n\n<i>Правка:</i> {feedback[:400]}",
                priority=task.priority,
                due_date_iso=task.due_date_iso,
                tags=list(task.tags),
                owner_hint=task.owner_hint,
                can_self_execute=task.can_self_execute,
                research_topic=task.research_topic,
                list_scope=task.list_scope,
                sensitive_markers=list(task.sensitive_markers),
                confidence=task.confidence,
                raw=dict(task.raw),
            )
            out.raw["revise_error"] = "llm_failed"
            _apply_short_description(out, {})
            return out

    revised = _task_from_llm_data(data, sensitive=list(task.sensitive_markers))
    revised.raw["user_text"] = task.raw.get("user_text") or ""
    revised.raw["revised_from_feedback"] = feedback[:500]
    if revised.intent not in {"create", "research", "update", "list", "report", "artifact", "ambiguous"}:
        revised.intent = task.intent
    if revised.priority not in {"P1", "P2", "P3"}:
        revised.priority = task.priority
    return revised


def kaiten_description(task: ExtractedTask) -> str:
    """Text stored on the Kaiten card."""
    short = (task.short_description or "").strip()
    if short:
        return short
    return (task.description_md or f"## Задача\n{task.title}").strip()


def extract_task(user_text: str) -> ExtractedTask:
    """Classify user message and extract structured task fields."""
    sensitive = detect_sensitive(user_text)
    from agent.perception import fast_classify_list  # noqa: WPS433 — avoid import cycle

    fast = fast_classify_list(user_text)
    if fast:
        fast.sensitive_markers = sensitive
        if sensitive:
            fast.can_self_execute = False
        fast.raw["user_text"] = user_text
        return fast

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": f"Сообщение:\n{user_text}\n\nВерни JSON по схеме."},
    ]
    try:
        resp = _call_deepseek(messages)
        content = resp["choices"][0]["message"]["content"]
        data = json.loads(content)
    except Exception:
        # one retry with t=0
        try:
            resp = _call_deepseek(messages, temperature=0.0)
            content = resp["choices"][0]["message"]["content"]
            data = json.loads(content)
        except Exception:
            fail = ExtractedTask(
                intent="ambiguous",
                title=user_text[:60],
                description_md="## Задача\n" + user_text,
                sensitive_markers=sensitive,
                confidence=0.0,
                raw={"error": "llm_failed", "user_text": user_text},
            )
            _apply_short_description(fail, {})
            return fail

    task = _task_from_llm_data(data, sensitive=sensitive, user_text=user_text)
    if task.priority not in {"P1", "P2", "P3"}:
        task.priority = "P2"
    if task.intent == "reminder":
        task.intent = "list"
        task.list_scope = task.list_scope or "digest"
    if task.intent == "artifact" and data.get("card_id"):
        task.raw["card_id"] = int(data["card_id"])
    if task.intent not in {
        "create",
        "research",
        "update",
        "list",
        "report",
        "artifact",
        "ambiguous",
    }:
        task.intent = "ambiguous"
    if task.intent == "list" and not task.list_scope:
        task.list_scope = "active"
    _scopes = {"active", "today", "overdue", "digest", "all", "done", "wip", "queue"}
    if task.list_scope and task.list_scope not in _scopes:
        task.list_scope = "active"
    if task.intent == "list" and task.list_scope == "all" and re.search(
        r"готово", user_text, re.I
    ) and not re.search(r"вся\s+доск|все\s+задач", user_text, re.I):
        task.list_scope = "done"
    if sensitive:
        task.can_self_execute = False
    return task


def _research_domain_block() -> str:
    try:
        from research_context import RESEARCH_DOMAIN_CONTEXT  # noqa: WPS433

        return RESEARCH_DOMAIN_CONTEXT
    except ImportError:
        return ""


RESEARCH_SYNTHESIS_PROMPT = """Ты — lead-аналитик по внедрению ИИ в банке (архитектура, продукт, ИБ, эксплуатация).
Пишешь развёрнутый отчёт на русском по предоставленным источникам и извлечённым фактам.

{domain_context}

Правила качества:
1. Опирайся ТОЛЬКО на источники и блок «Извлечённые факты». Не выдумывай цифры, регуляторику, названия продуктов банка.
2. Если данных нет — «в источниках не найдено» в ## Пробелы в данных.
3. Маркируй: «подтверждено» vs «гипотеза» vs «требует валидации в банке».
4. TL;DR: 5–7 нумерованных пунктов для руководителя (ценность, риск, срок, зависимости).
5. Обязательно: ## Контекст для банка (процесс, роли, периметр, данные).
6. Обязательно: ## Рекомендуемая архитектура (компоненты, интеграции, закрытый контур).
7. Обязательно: ## Best practices и референсы (конкретные практики/кейсы из источников, не общие слова).
8. Обязательно: ## PoC → Pilot → Production (этапы, критерии выхода, метрики).
9. Таблицы markdown: до 2 (сравнение стеков / рисков / вариантов).
10. Диаграммы: 1–2 блока ```mermaid (flowchart или C4-style graph) — архитектура или поток агента.
    Подпись перед блоком: «Рис. N. …». Код mermaid на английских id узлов, подписи можно на русском.
11. ## Источники: каждый источник — тип [официальная дока|статья|GitHub|кейс|блог], название, URL, 1 строка «что взяли».
12. ## Дальнейшие шаги: 5–8 конкретных действий для R&D (с владельцем: ИБ / архитектура / данные / продукт).
13. Без воды и маркетинга вендоров. Типографика: — и «ёлочки».

Структура markdown:
# Заголовок
## TL;DR
## Контекст для банка
## Основные выводы
### (3–6 подразделов по теме)
## Рекомендуемая архитектура
(текст + mermaid)
## Сравнение вариантов
(таблица)
## Best practices и референсы
## Безопасность, комплаенс и эксплуатация
## PoC → Pilot → Production
## Риски и ограничения
## Пробелы в данных
## Источники
## Дальнейшие шаги

Объём: 1200–2500 слов. Приоритет: закончи все обязательные разделы; таблицы — компактно (до 5 строк)."""


try:
    from agent.validation import report_needs_continuation as _report_incomplete
except ImportError:
    def _report_incomplete(markdown: str, finish_reason: str | None = None) -> bool:
        return (finish_reason or "").lower() == "length"


RESEARCH_FACTS_PROMPT = """Ты извлекаешь структурированные факты из сырых текстов веб-страниц для банковского AI-ресёрча.
Верни только JSON.

{domain_context}

Схема:
{{
  "topic": "...",
  "facts": [
    {{"claim": "...", "source_index": 1, "confidence": "high|medium|low", "tags": ["architecture","security",...]}}
  ],
  "best_practices": ["..."],
  "risks": ["..."],
  "glossary": {{"term": "definition"}},
  "open_questions": ["..."]
}}

Правила: не более 40 facts; claim — одно предложение; только то, что явно есть в тексте."""


def extract_research_facts(topic: str, sources_text: list[dict]) -> dict | None:
    """Optional pass 1: structured facts from sources (RESEARCH_TWO_PASS=true)."""
    sources_block = "\n\n".join(
        f"### [{i+1}] {s.get('title', '')[:80]}\nURL: {s.get('url', '')}\n\n{s.get('text', '')[:2800]}"
        for i, s in enumerate(sources_text[:12])
    )
    domain = _research_domain_block()
    system = RESEARCH_FACTS_PROMPT.format(domain_context=domain)
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": f"Тема: {topic}\n\nИсточники:\n{sources_block}\n\nJSON:"},
    ]
    try:
        resp = _call_deepseek(
            messages,
            model=ENV.get("DEEPSEEK_MODEL_SMART", "deepseek-v4-pro"),
            temperature=0.1,
            max_tokens=4000,
            json_mode=True,
            timeout=120,
        )
        return json.loads(resp["choices"][0]["message"]["content"])
    except Exception:
        return None


def _research_sources_block(
    sources_text: list[dict], max_src: int, char_cap: int,
    *, titles_only: bool = False,
) -> str:
    parts = []
    for i, s in enumerate(sources_text[:max_src]):
        head = (
            f"### Источник {i + 1}: {s.get('title', '')[:80]}\n"
            f"URL: {s.get('url', '')}"
        )
        if titles_only:
            parts.append(head)
        else:
            parts.append(f"{head}\n\n{s.get('text', '')[:char_cap]}")
    return "\n\n".join(parts)


def _synthesize_research_attempt(
    topic: str,
    sources_text: list[dict],
    wall_time_used_s: float,
    facts: dict | None,
    max_src: int,
    char_cap: int,
    max_tokens: int,
) -> str:
    titles_only = bool(facts)
    sources_block = _research_sources_block(
        sources_text, max_src, char_cap, titles_only=titles_only,
    )
    domain = _research_domain_block()
    system = RESEARCH_SYNTHESIS_PROMPT.format(domain_context=domain)
    facts_block = ""
    if facts:
        facts_block = (
            "\n\n--- ИЗВЛЕЧЁННЫЕ ФАКТЫ (JSON) ---\n"
            + json.dumps(facts, ensure_ascii=False, indent=2)
        )
    excerpt_note = ""
    if facts:
        excerpt_note = (
            "\n(Полные тексты источников не включены — только список названий/URL; "
            "опирайся на блок фактов.)"
        )
    messages = [
        {"role": "system", "content": system},
        {
            "role": "user",
            "content": (
                f"Тема исследования: {topic}\n"
                f"Время сбора: {wall_time_used_s:.0f} с.\n"
                f"Источников с текстом: {len(sources_text)}\n"
                f"{facts_block}\n\n"
                f"--- ИСТОЧНИКИ{excerpt_note} ---\n{sources_block}\n\n"
                "Составь полный отчёт по структуре system prompt. Включи mermaid-диаграммы."
            ),
        },
    ]
    resp = _call_deepseek(
        messages,
        model=ENV.get("DEEPSEEK_MODEL_SMART", "deepseek-v4-pro"),
        temperature=float(ENV.get("RESEARCH_SYNTH_TEMPERATURE", "0.35")),
        max_tokens=max_tokens,
        json_mode=False,
        timeout=int(ENV.get("RESEARCH_SYNTH_TIMEOUT_SEC", "240")),
    )
    content = resp["choices"][0]["message"]["content"] or ""
    finish = (resp["choices"][0].get("finish_reason") or "").lower()
    max_rounds = int(ENV.get("RESEARCH_SYNTH_CONTINUE_ROUNDS", "2"))
    cont_tokens = int(ENV.get("RESEARCH_SYNTH_CONTINUE_MAX_TOKENS", "4500"))
    model = ENV.get("DEEPSEEK_MODEL_SMART", "deepseek-v4-pro")
    temperature = float(ENV.get("RESEARCH_SYNTH_TEMPERATURE", "0.35"))
    timeout = int(ENV.get("RESEARCH_SYNTH_TIMEOUT_SEC", "240"))

    for _ in range(max_rounds):
        if not _report_incomplete(content, finish):
            break
        tail_hint = "\n".join(content.rstrip().splitlines()[-8:])
        cont_messages = messages + [
            {"role": "assistant", "content": content},
            {
                "role": "user",
                "content": (
                    "Отчёт оборвался (лимит длины ответа). Продолжи СТРОГО с места обрыва.\n"
                    "Не повторяй уже написанные разделы. Сначала допиши оборванную таблицу/абзац.\n"
                    "Затем допиши недостающие разделы по структуре system prompt, минимум:\n"
                    "## Best practices и референсы, ## Безопасность, комплаенс и эксплуатация, "
                    "## PoC → Pilot → Production, ## Риски и ограничения, ## Пробелы в данных, "
                    "## Источники, ## Дальнейшие шаги.\n\n"
                    f"Конец текущего черновика:\n…\n{tail_hint}"
                ),
            },
        ]
        resp = _call_deepseek(
            cont_messages,
            model=model,
            temperature=temperature,
            max_tokens=cont_tokens,
            json_mode=False,
            timeout=timeout,
        )
        piece = (resp["choices"][0]["message"]["content"] or "").strip()
        if piece:
            content = content.rstrip() + "\n\n" + piece
        finish = (resp["choices"][0].get("finish_reason") or "").lower()

    if _report_incomplete(content, finish):
        content += (
            "\n\n---\n\n> ⚠️ Отчёт сокращён лимитом модели. "
            "Увеличьте `RESEARCH_SYNTH_MAX_TOKENS` / `RESEARCH_SYNTH_CONTINUE_MAX_TOKENS` "
            "и перезапустите ресёрч.\n"
        )
    return content


def synthesize_research(
    topic: str,
    sources_text: list[dict],
    wall_time_used_s: float,
    facts: dict | None = None,
) -> str:
    """Generate markdown report from collected sources. Returns markdown string."""
    max_src = int(ENV.get("RESEARCH_MAX_SOURCES_IN_PROMPT", "12"))
    char_cap = int(ENV.get("RESEARCH_CHARS_PER_SOURCE", "4500"))
    max_tokens = int(ENV.get("RESEARCH_SYNTH_MAX_TOKENS", "10000"))

    if facts:
        max_src = min(max_src, int(ENV.get("RESEARCH_SYNTH_MAX_SOURCES_WITH_FACTS", "8")))
        char_cap = min(char_cap, int(ENV.get("RESEARCH_SYNTH_CHARS_WITH_FACTS", "2000")))

    try:
        return _synthesize_research_attempt(
            topic, sources_text, wall_time_used_s, facts,
            max_src, char_cap, max_tokens,
        )
    except RuntimeError:
        fb_src = min(4, len(sources_text))
        fb_cap = int(ENV.get("RESEARCH_SYNTH_FALLBACK_CHARS", "1500"))
        fb_tokens = int(ENV.get("RESEARCH_SYNTH_FALLBACK_MAX_TOKENS", "3500"))
        return _synthesize_research_attempt(
            topic, sources_text, wall_time_used_s, facts,
            fb_src, fb_cap, fb_tokens,
        )


def search_queries_for(topic: str) -> list[str]:
    """Ask LLM to expand topic into search queries (web / future search API)."""
    try:
        from research_context import RESEARCH_DOMAIN_CONTEXT, RESEARCH_QUERY_ANGLES  # noqa: WPS433
    except ImportError:
        RESEARCH_DOMAIN_CONTEXT = ""
        RESEARCH_QUERY_ANGLES = ""

    messages = [
        {
            "role": "system",
            "content": (
                "Ты — research-планировщик для банковского R&D (ИИ, агенты, цифровая трансформация).\n"
                f"{RESEARCH_DOMAIN_CONTEXT}\n\n"
                f"{RESEARCH_QUERY_ANGLES}\n\n"
                "Составь 8–10 поисковых запросов для веб-поиска.\n"
                "Правила:\n"
                "- минимум 5 на английском (official docs, GitHub, architecture, enterprise, banking);\n"
                "- 2–3 на русском (внедрение, кейс, регуляторика если уместно);\n"
                "- углы: архитектура, security/compliance, RAG/agents, MLOps, сравнение стеков, "
                "case study financial services, pilot production;\n"
                "- конкретные имена из темы; без кавычек; до 14 слов.\n"
                'Верни JSON: {"queries":["..."]}.'
            ),
        },
        {"role": "user", "content": f"Тема: {topic}\nJSON only."},
    ]
    try:
        resp = _call_deepseek(messages, temperature=0.25, max_tokens=600)
        content = resp["choices"][0]["message"]["content"]
        data = json.loads(content)
        qs = data.get("queries") or []
        limit = int(ENV.get("RESEARCH_MAX_QUERIES", "10"))
        return [str(q) for q in qs if q][:limit] or [topic]
    except Exception:
        return [topic]


if __name__ == "__main__":
    import sys
    text = " ".join(sys.argv[1:]) or "изучи best practices monorepo Next.js 15"
    print(f"Sensitive: {detect_sensitive(text)}")
    t = extract_task(text)
    print(json.dumps(t.to_dict(), ensure_ascii=False, indent=2))
