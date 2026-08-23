# LoopMaster MCP — Road Map

## Текущее состояние

- MCP-сервер: **6 инструментов** (loop_list, loop_get, loop_result, loop_status, loop_cancel, loop_run)
- **loop_run**: выполнение циклов через LLM API с env-based провайдерами
- Multi-provider LLM client: OpenAI, Anthropic, Google, OpenRouter, Custom
- Тестовые циклы: simple_test, error_handling_test

## Цель

Превратить LoopMaster из "хранилища рецептов" в **исполнительный движок** для агентных систем.

**Позиционирование**: "LangGraph is overkill for simple loops. LoopMaster is the lightweight alternative." — "jQuery для AI loops".

## Архитектура

```
opencode (агент)                    MCP-сервер LoopMaster
        │                                    │
        ├─ loop_list ──────────────────────→ │ (список циклов)
        │                                    │
        ├─ loop_run("planning", {...}) ────→ │
        │                                    ├─ Шаг 1: вызов LLM API
        │                                    ├─ Шаг 2: вызов LLM API  
        │                                    ├─ Шаг 3: повтор при ошибке
        │                                    └─ Результат
        │ ←───── {status: "completed"} ──────┤
```

**Ключевые моменты:**
1. **Агент (opencode)** решает: какой цикл вызвать, с какими параметрами
2. **MCP-сервер** выполняет: вызывает LLM API для каждого шага, управляет ошибками, чекпоинтами
3. **Агент** получает: готовый результат без деталей выполнения

## Приоритеты

### P0: Kритические (Фаза 1: Stabilize — Завершена ✅)

#### ✅ LLM API интеграция
- Multi-provider support: OpenAI, Anthropic, Google, OpenRouter, Custom
- Env-based конфигурация (LOOPMASTER_LLM_PROVIDER, API_KEY, MODEL)
- `loop_run` инструмент для выполнения циклов

#### ✅ SQLite persistence для jobs (`JobStore`)
- Постоянное хранилище `.loopmaster/jobs.db` на базе SQLite с WAL-режимом
- Потокобезопасность (`threading.RLock`) и устойчивость к перезапускам MCP-сервера
- Атомарное обновление шагов и санитизация зависших задач при старте

#### ✅ Prompt injection hardening & Safe resolution
- Безопасное regex-разрешение `{var}` и `{{var}}` без поломки JSON-структур
- Корректная изоляция контекста через `Context.to_dict()` (`deepcopy`)

#### ✅ Unify engines
- Единый execution path: `loop_run` в MCP полностью делегирует выполнение в ядро `LoopEngine`
- Сквозной сбор метрик, раздельный учет input/output токенов и расчет затрат

### P1: Важные (Фаза 2: Scale & Observability)

#### ✅ Streaming progress через SSE
- Стандартизованный SSE W3C эмиттер (`src/loopmaster/events/sse.py`, `format_sse`)
- Потоковый парсинг LLM чанков в реальном времени для OpenAI, Anthropic и Google Gemini
- Потокобезопасная трансляция событий в `LoopEngine` и MCP подписках (`loop_started`, `step_started`, `step_chunk`, `step_completed`, `loop_completed`)
- Защита от утечек памяти: ограничение размера истории событий и исключение токенов-чанков из истории

#### ✅ Loop versioning и migration
- Чистый stdlib SemVer парсер (`SemVer`) с поддержкой версий `1.0.0`, `v1.2.0`, `1.0`, `0.x.y` и защитой от downgrade
- Политики совместимости `CompatibilityPolicy`: `STRICT`, `SEMVER_COMPATIBLE` (по умолчанию с warning на `source_hash`), `PERMISSIVE`
- Декларативный реестр миграций `@register_migration` с поиском кратчайшего пути через BFS и защитой от циклов
- Транзакционное глубокое копирование `deepcopy` перед миграцией и синхронизация переименований шагов `rename_checkpoint_step`
- Прозрачная интеграция в `LoopEngine.run` и `CheckpointManager.load_and_migrate`

#### ✅ OpenTelemetry интеграция
- Встроенная легковесная подсистема OpenTelemetry-трассировки и метрик (`src/loopmaster/telemetry`) без обязательных сторонних зависимостей
- Формат экспорта OTLP Proto3 JSON (`/v1/traces`, `/v1/metrics`) с наносекундными временными метками и типизированными атрибутами
- Асинхронная изоляция через `ContextVar` с поддержкой W3C `traceparent` (`00-{trace_id}-{span_id}-{flags}`)
- Неблокирующий фоновый экспортер `OTLPHttpSpanExporter` с bounded queue, батчингом и отказоустойчивостью к сбоям сети
- Сквозная инструментация `LoopEngine` (root `loop.<name>`), `StepExecutor` (internal `step.<name>`), и LLM вызовов (client `llm.<model>` с GenAI семантическими конвенциями)
- Поддержка экспорта метрик `MetricsCollector.to_otlp_payload` в OTLP `ResourceMetrics`

### P2: Желаемые (1-2 месяца)

#### ✅ Conditional branching в DSL
- Декларативный блок `Conditional(condition, then_steps, else_steps, name)`
- Безопасный парсер условий `evaluate_condition` на базе AST whitelist без уязвимостей `eval()`
- Защита от потери ветки при возобновлении из чекпоинта (branch stickiness on resume)
- Изоляция скоупа сбора дочерних шагов на базе `id(s)` без конфликтов с мутабельными датаклассами
- Сквозная интеграция с SSE (`branch_selected`), OpenTelemetry (`conditional.<name>` спаны), YAML экспортом и MCP discovery

#### ✅ Tool execution bridge
- Встроенные executors: `ShellExecutor`, `HTTPExecutor`, `MCPToolExecutor` на базе чистой стандартной библиотеки Python (zero dependencies)
- Безопасное исполнение Shell-процессов с изоляцией сессий `os.setsid`, очисткой дерева процессов (`taskkill /F /T` / `os.killpg`) и защитой от shell injection
- Полнофункциональный `HTTPExecutor` с захватом тел ошибок `HTTPError`, поддержкой 204 No Content и проверкой `allowed_status`
- Легковесный клиент `MCPToolExecutor` с NDJSON framing, протокольным handshake (`initialize` -> `notifications/initialized` -> `tools/call`), таймаутами и защитой от stderr deadlock
- Рекурсивная шаблонизация переменных с поддержкой dot-notation (`{step.stdout}`, `{http.body.user.id}`) и сохранением структуры данных в контексте
- Автоматическая OTel-инструментация с `SpanKind.CLIENT` (`tool.shell`, `tool.http`, `tool.mcp`)

#### ✅ Model Registry, Semantic Aliases & Intelligent Policy
- Централизованный каталог моделей `ModelRegistry` со встроенными тарифами, контекстными окнами и тегами
- Семантические алиасы (`@fast`, `@smart`, `@coding`, `@cheap`, `@reasoning`, `@fallback`, `@auto`) для создания вендор-агностичных циклов
- Политики `ModelPolicy` с режимами `PERMISSIVE`, `STRICT` (запрет неодобренных моделей) и `ALIAS_ONLY`
- Потолковые лимиты `max_cost_per_step` для предотвращения случайных перерасходов на длинных промптах
- Интеллектуальный эвристический роутер `auto_route_model` на основе анализа сложности задачи и остатка бюджета
- Мульти-провайдерное динамическое переключение в `LLMClient` (OpenAI, Anthropic, Google Gemini, Local/Ollama) с SSOT тарификацией в `CostTracker`
- MCP-инструменты `model_list` (без утечки секретов) и `model_recommend`
- Официальное руководство для AI-агентов [`docs/AGENT_GUIDE.md`](docs/AGENT_GUIDE.md)

#### Loop marketplace
Registry/pypi для loop definitions.

## Поддерживаемые провайдеры

| Провайдер | API Key Env | Base URL | Модели |
|---|---|---|---|
| OpenAI | `OPENAI_API_KEY` | `https://api.openai.com/v1` | gpt-4, gpt-4-turbo, gpt-3.5-turbo |
| Anthropic | `ANTHROPIC_API_KEY` | `https://api.anthropic.com` | claude-3-opus, claude-3-sonnet |
| Google | `GOOGLE_API_KEY` | `https://generativelanguage.googleapis.com` | gemini-pro |
| OpenRouter | `OPENROUTER_API_KEY` | `https://openrouter.ai/api/v1` | Любые модели |
| Custom | `CUSTOM_API_KEY` | Произвольный | Любые OpenAI-совместимые |

## Безопасность

- API ключи **никогда** не коммитятся в git
- Ключи передаются только через env variables
- `.env` файл добавлен в `.gitignore`

## Стратегия развития

**Фаза 1: Stabilize (Завершена ✅)**
- ✅ SQLite persistence для jobs
- ✅ Prompt injection hardening
- ✅ Устранить дублирование engines
- ✅ 100% test coverage для core (300 тестов)
- ⏳ Publish to PyPI

**Фаза 2: Differentiate (2-4 месяца)**
- ✅ Streaming progress (SSE & Real-time events)
- ⏳ Loop versioning и migration
- ⏳ OpenTelemetry integration
- ⏳ Conditional branching в DSL
- ⏳ 5+ production-ready templates

**Фаза 3: Scale (4-6 месяцев)**
- Tool execution bridge (Shell/HTTP/MCP)
- Loop marketplace / registry
- Multi-agent orchestration patterns
- Performance optimization (async parallel)

## Следующие шаги

1. ✅ LLM API интеграция (loop_run + llm_client)
2. ✅ Context import fix
3. ✅ SQLite persistence для jobs (JobStore)
4. ✅ Safe prompt resolution
5. ✅ Unify engines (LoopEngine)
6. ✅ Streaming progress (SSE & Real-time events)
7. ⏳ Loop versioning & migration
8. ⏳ OpenTelemetry
