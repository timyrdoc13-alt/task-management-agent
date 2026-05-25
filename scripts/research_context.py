"""Shared domain context for auto-research (banking AI / digital transformation)."""

RESEARCH_DOMAIN_CONTEXT = """
Контекст заказчика (применяй ко всем ресёрчам по умолчанию):
- Команда R&D банка: цифровая трансформация, внедрение ИИ в продукты и процессы.
- Типовые темы: LLM и локальные модели в закрытом контуре, RAG по внутренним документам,
  AI-агенты и ассистенты (операционные, для сотрудников, клиентские сценарии),
  оркестрация (LangGraph, CrewAI, MCP), интеграция с BPM/CRM/АБС, MLOps, observability.
- Ограничения: периметр банка, ИБ, комплаенс (152-ФЗ, отраслевые требования ЦБ где уместно),
  запрет утечки данных во внешние SaaS без согласования, аудит и трассируемость решений.
- Ценность отчёта: практичность для PoC/Pilot/Production, оценка рисков, TCO/усилия,
  референсы внедрений (банки, финтех, enterprise), не академическая вода.

Если тема не про ИИ — всё равно ищи связь с автоматизацией, данными и процессами банка.
""".strip()

RESEARCH_QUERY_ANGLES = """
Углы поиска для банковского ИИ (выбери релевантные к теме):
- architecture / reference architecture / air-gapped deployment
- security compliance banking LLM on-prem
- RAG enterprise document assistant financial services
- AI agent orchestration human-in-the-loop banking
- MLOps LLM monitoring guardrails
- case study bank generative AI implementation
- open source vs vendor (GigaChat, YandexGPT, local Ollama/vLLM) — только если в теме
- ROI TCO pilot production rollout
"""
