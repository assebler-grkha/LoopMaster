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

### P1: Важные (2-4 недели)

#### Streaming progress через SSE
**Проблема:** Нет реального streaming progress.

#### Loop versioning и migration
**Проблема:** source_hash может не совпасть после рефакторинга.

#### OpenTelemetry интеграция
**Проблема:** MetricsCollector — свой формат, нет интеграции с Grafana/Datadog.

### P2: Желаемые (1-2 месяца)

#### Conditional branching в DSL
```python
Conditional(
    condition=lambda ctx: "error" in ctx.get("analyze", ""),
    then_steps=[Step("retry", model="gpt-4", prompt="Retry")],
    else_steps=[Step("proceed", model="gpt-4", prompt="Proceed")],
)
```

#### Tool execution bridge
Встроенные executors: ShellExecutor, HTTPExecutor, MCPToolExecutor.

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
- Streaming progress (SSE)
- Loop versioning и migration
- OpenTelemetry integration
- Conditional branching в DSL
- 5+ production-ready templates

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
6. ⏳ Streaming progress (SSE)
7. ⏳ Loop versioning
8. ⏳ OpenTelemetry
