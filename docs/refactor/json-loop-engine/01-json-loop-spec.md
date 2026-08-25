# LoopSpec v1 — JSON-формат определения цикла

> Статус: PROPOSAL. JSON-конфигурация цикла как execution format. Python DSL остаётся authoring format (ADR-011).

## 1. Топ-уровень

```json
{
  "loop": "code-review",              // обяз.: имя (slug)
  "version": "1.0.0",                 // обяз.: SemVer
  "description": "...",
  "execution": "engine",              // engine | agent (см. §8; дефолт engine)
  "budget": { "max_cost": 5.00, "max_tokens": 100000, "max_steps": 20 },
  "error_policy": {                   // дефолты для шагов (см. §6)
    "retry": 3, "backoff": 1.0,
    "on_failure": "abort"             // abort|skip|retry|fallback
  },
  "variables": { "repo": ".", "lang": "python" },   // стартовый контекст
  "deny_capabilities": ["net"],       // запрет capabilities для code-блоков
  "steps": [ /* см. §2 */ ]
}
```

Валидация двухслойная: JSON Schema (`schemas/loopspec-v1.schema.json`) → семантический валидатор (уникальность `id`, ссылки на блоки существуют и sha256 совпадает, условия парсятся AST-парсером, `{var}`-шаблоны разрешимы best-effort).

## 2. Типы узлов (8)

### 2.1 `llm` — вызов модели
```json
{ "id": "analyze", "type": "llm", "model": "@coding",
  "prompt": "Проанализируй diff:\n{fetch.output}",
  "system": "Ты ревьюер.", "save_as": "analysis" }
```
`model` — идентификатор или алиас реестра (`@fast`, `@smart`, …) — без изменений. Результат шага доступен как `{<id>}` либо `{save_as}`.

### 2.2 `shell`
```json
{ "id": "fetch", "type": "shell", "run": "git -C {repo} diff --stat", "timeout_s": 30 }
```

### 2.3 `http`
```json
{ "id": "ci_status", "type": "http", "method": "GET", "url": "https://ci/api/{repo}/status", "headers": {} }
```

### 2.4 `mcp` — обращение к MCP-серверу/инструменту
```json
{ "id": "search", "type": "mcp", "server": "api-mega-list", "tool": "search_apis_tool",
  "input": { "query": "{topic}" }, "timeout_s": 60 }
```
Исполняется существующим MCPToolExecutor (NDJSON stdio). Ответ сервера = output шага.

### 2.5 `code` — импорт блока из БД
```json
{ "id": "fix", "type": "code", "ref": "test-fixer@1.4.2", "sha256": "9f2c…",
  "input": { "mode": "fast" } }
```
См. [02-code-block-store.md](02-code-block-store.md).

### 2.6 `human` — вопрос агенту/человеку, durable pause
```json
{ "id": "confirm", "type": "human",
  "question": "Применяю патч из {fix}. Продолжать?",
  "options": ["yes", "no", "edit"],
  "default_answer": "yes",
  "timeout": "24h",
  "on_timeout": "default_answer",   // default_answer|skip|fail|escalate
  "ask": "agent"                    // agent | human | mcp:<server>
}
```
См. [03-hitl-protocol.md](03-hitl-protocol.md).

### 2.7 `parallel`
```json
{ "id": "reviews", "type": "parallel",
  "steps": [
    { "id": "security", "type": "llm", "model": "@smart", "prompt": "Security: {fetch.output}" },
    { "id": "style",    "type": "llm", "model": "@fast",  "prompt": "Style: {fetch.output}" }
  ] }
```
Один уровень вложенности в v1; результаты доступны по внутренним id.

### 2.8 `conditional`
```json
{ "id": "gate", "type": "conditional",
  "if": "'critical' in analysis",
  "then": [ /* узлы */ ],
  "else": [ /* узлы */ ] }
```
Выражение `if` — строка синтаксиса существующего AST-whitelist парсера (`core/condition.py`). Ветвление вычисляется на контексте snapshot'а; branch stickiness при resume сохраняется штатно.

## 3. Компиляция в IR

JsonLoader транслирует LoopSpec → существующие объекты ядра:

| LoopSpec узел | Объект ядра |
|---|---|
| llm/shell/http/mcp/code/human | `Step(name=id, model=…, tool=…, executor=…)` + executor-фабрика |
| parallel | `Parallel(*steps)` |
| conditional | `Conditional(expr, then_steps, else_steps)` |

Ядро движка, budget, ErrorPolicy, heartbeat, checkpoint/replay — **без изменений**. Новые executor'ы: CodeBlockExecutor, HumanInputExecutor (последний не «выполняет», а ставит паузу — см. 03).

## 4. Шаблоны переменных

Как сейчас: `{var}` и dot-notation `{step.field}` подставляются из immutable context snapshot. Отсутствующая переменная = ошибка валидации (strict) или пустая строка (`"lenient": true`) — по выбору.

## 5. Пример полного цикла

```json
{
  "loop": "nightly-repo-audit",
  "version": "1.2.0",
  "budget": { "max_cost": 3.00 },
  "variables": { "repo": "C:/Projects/Ideas/LoopMaster" },
  "steps": [
    { "id": "fetch",   "type": "shell", "run": "git -C {repo} log --oneline -20 && git -C {repo} diff HEAD~5 --stat" },
    { "id": "audit",   "type": "llm", "model": "@coding", "prompt": "Найди риски в изменениях:\n{fetch}" },
    { "id": "deepen",  "type": "code", "ref": "risk-scanner@0.3.0", "input": { "findings": "{audit}" } },
    { "id": "gate",    "type": "conditional", "if": "'high' in deepen.risk_level",
      "then": [ { "id": "alert", "type": "human", "question": "High-risk находки: {deepen}. Действия?", "options": ["review","ignore"] } ],
      "else": [ { "id": "log_only", "type": "shell", "run": "echo low-risk" } ] },
    { "id": "report",  "type": "llm", "model": "@fast", "prompt": "Итоговый отчёт: {audit} / {deepen}" }
  ]
}
```

## 6. Политики ошибок

Значения `on_failure`: `abort | skip | retry | fallback`. Поля: `retry:int`, `backoff:float`, `fallback_model:str`. Маппинг 1:1 на `ErrorPolicy`/`RecoveryAction`. Per-step override — поле `error_policy` внутри узла.

## 7. Ограничения v1 (осознанные)

- Нет `while`/циклов по коллекциям (`foreach`) — добавляется в v2 после опыта эксплуатации (риск зацикливания + бюджет).
- Parallel — один уровень вложенности.
- Нет пользовательских функций/скриптов inline — вся логика кода только через Code Block Store (аудируемо).

## 8. Режимы исполнения: `engine` vs `agent`

Один и тот же LoopSpec исполняется двумя способами — это уже нативная модель проекта («OpenCode IS the LLM provider»), формализуем её.

### `execution: "engine"` (дефолт)
Фоновый воркер сам исполняет шаги; шаги `llm` требуют настроенного внешнего провайдера. Агент только ставит задачу и читает уведомления.

### `execution: "agent"`
Внешний LLM не нужен. Агент через MCP:
1. `loop_get(loop_name)` → получает JSON-спеку + job_id;
2. идёт по шагам **сам**: `shell` выполняет своими инструментами, `code` берёт блок (`block_get`) и запускает, `conditional` вычисляет по контексту, шаг `llm` выполняет собственной моделью (это и есть замена провайдера);
3. после каждого шага отчитывается `loop_result(job_id, step_id, output=…)` → состояние и чекпоинты пишутся в БД штатно (resume/crash-recovery работают и здесь);
4. шаг `human` в этом режиме тривиален: агент спрашивает пользователя прямо в диалоге и отвечает себе же через `loop_result`.

Требования к спеке для agent-режима:
- каждый узел должен быть понятен без движка: у `code`-шагов используется текстовый алиас блока (`block.description` / короткая инструкция) — агент понимает «что делает этот блок» без чтения исходника;
- условия `if` — простые выражения над контекстом, которые агент может оценить сам;
- `parallel` в agent-режиме деградирует до последовательного выполнения (если агент не может распараллелить сам).

Выбор режима: поле `execution` в спеке или override при `loop_run(mode="agent")`. Движок при `mode="agent"` ничего не исполняет — только ведёт учёт прогресса (state machine + чекпоинты).
