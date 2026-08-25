# LoopSpec v1 — JSON-формат определения цикла

> Статус: РЕАЛИЗОВАН (Ф1–Ф6). JSON-конфигурация цикла как execution format. Python DSL остаётся authoring format (ADR-011); компилятор DSL→JSON — `src/loopmaster/spec/compiler.py`, CLI `loop-engine export FILE --format json`.

## 1. Топ-уровень

```json
{
  "loopmaster": "1.0",                // обяз.: маркер версии спеки (SPEC_VERSION)
  "name": "code-review",              // обяз.: имя (slug, ^[a-z][a-z0-9_-]*$)
  "version": "1.0.0",                 // обяз.: SemVer
  "description": "...",
  "execution": "engine",              // engine | agent (см. §8; дефолт engine)
  "budget": { "max_cost": 5.00, "max_tokens": 100000, "max_steps": 20 },
  "error_policy": {                   // дефолты для шагов (см. §6)
    "retry": 3, "backoff": 1.0,
    "on_failure": "abort"             // abort|skip|retry|fallback
  },
  "notify": ["info", "needs_input", "critical"],   // фильтр уведомлений (см. 04)
  "context": { "repo": ".", "lang": "python" },    // стартовый контекст
  "deny_capabilities": ["net"],       // запрет capabilities для code-блоков
  "steps": [ /* см. §2 */ ]
}
```

Валидация двухслойная: JSON Schema (`schemas/loopspec-v1.schema.json`) → семантический валидатор (уникальность `name`, AST-парсинг условий, разрешимость `{var}`-шаблонов против контекста и выводов предыдущих шагов; существование code-блоков и sha256 проверяется на слое MCP — `validate_code_refs` в `loop_save`/`loop_run`).

## 2. Типы узлов (8)

Каждый узел имеет обязательное поле `name` (уникально в пределах цикла).

### 2.1 `llm` — вызов модели
```json
{ "name": "analyze", "type": "llm",
  "model": "@coding",
  "prompt": "Проанализируй diff:\n{fetch.stdout}",
  "timeout": 120 }
```
`model` — идентификатор или алиас реестра (`@fast`, `@smart`, …). Результат шага доступен как `{<name>}`. `timeout` (сек) компилируется в Step.

### 2.2 `shell`
```json
{ "name": "fetch", "type": "shell",
  "command": "git -C {repo} diff --stat",
  "cwd": ".", "env": { "GIT_PAGER": "cat" },
  "capture_output": true, "check": false, "shell": false,
  "timeout": 30 }
```

### 2.3 `http`
```json
{ "name": "ci_status", "type": "http", "method": "GET",
  "url": "https://ci/api/{repo}/status",
  "headers": { "Accept": "application/json" },
  "json_data": null, "data": null,
  "json_output": true, "allowed_status": [200, 201],
  "timeout": 30 }
```

### 2.4 `mcp` — обращение к MCP-серверу/инструменту
```json
{ "name": "search", "type": "mcp",
  "server_command": ["python", "-m", "api_mega_list_mcp"],
  "tool_name": "search_apis_tool",
  "arguments": { "query": "{topic}" },
  "cwd": null, "env": null,
  "timeout": 60 }
```
Исполняется существующим MCPToolExecutor (NDJSON stdio). Ответ сервера = output шага.

### 2.5 `code` — импорт блока из БД
```json
{ "name": "fix", "type": "code", "ref": "test-fixer@1.4.2", "sha256": "9f2c…",
  "input": { "mode": "fast" }, "timeout": 60 }
```
См. [02-code-block-store.md](02-code-block-store.md).

### 2.6 `human` — вопрос агенту/человеку, durable pause
```json
{ "name": "confirm", "type": "human",
  "question": "Применяю патч из {fix}. Продолжать?",
  "ask": "agent",                   // agent | human | mcp:<server>
  "options": ["yes", "no", "edit"],
  "timeout": "24h",
  "default_answer": "yes",
  "on_timeout": "default_answer"    // default_answer|skip|fail|escalate
}
```
`timeout` — duration-строка (`30m`, `24h`, `1h30m`). `escalate` создаёт critical-notification и продлевает ожидание на ещё один период (см. 03/04). См. [03-hitl-protocol.md](03-hitl-protocol.md).

### 2.7 `parallel`
```json
{ "name": "reviews", "type": "parallel",
  "steps": [
    { "name": "security", "type": "llm", "model": "@smart", "prompt": "Security: {fetch.stdout}" },
    { "name": "style",    "type": "llm", "model": "@fast",  "prompt": "Style: {fetch.stdout}" }
  ] }
```
Один уровень вложенности в v1; результаты доступны по внутренним `name`. Поле `name` у узла опционально при компиляции из DSL, но loader требует его всегда — компилятор генерирует автоимя `group-N`. **В engine-режиме v1 дети parallel исполняются последовательно** (общий контекст не потокобезопасен; true-concurrency — в ROADMAP v2). Ссылки между sibling'ами внутри одной группы запрещены валидацией.

### 2.8 `conditional`
```json
{ "name": "gate", "type": "conditional",
  "condition": "'critical' in analysis",
  "then": [ /* узлы */ ],
  "else": [ /* узлы */ ] }
```
Выражение `condition` — строка синтаксиса AST-whitelist парсера (`core/condition.py`: сравнения, and/or/not, имена, литералы, унарный минус для чисел); `{var}`-плейсхолдеры подставляются перед парсингом. Ветвление вычисляется на контексте snapshot'а; branch stickiness при resume сохраняется штатно.

## 3. Компиляция в IR

JsonLoader транслирует LoopSpec → существующие объекты ядра:

| LoopSpec узел | Объект ядра |
|---|---|
| llm/shell/http/mcp/code/human | `Step(name=…, model=…, executor=…) + executor (ShellExecutor/HTTPExecutor/MCPToolExecutor/CodeBlockExecutor/HumanInputExecutor) |
| parallel | `Parallel(*steps)` — в engine-режиме v1 дети исполняются последовательно |
| conditional | `Conditional(condition, then_steps, else_steps)` |

Ядро движка, budget, ErrorPolicy, heartbeat, checkpoint/replay — **без изменений**. Новые executor'ы: CodeBlockExecutor, HumanInputExecutor (последний не «выполняет», а ставит паузу — см. 03).

## 4. Шаблоны переменных

Как сейчас: `{var}` и dot-notation `{step.field}` подставляются из immutable context snapshot. Отсутствующая переменная = ошибка валидации (strict) или пустая строка (`"lenient": true`) — по выбору.

## 5. Пример полного цикла

```json
{
  "loopmaster": "1.0",
  "name": "nightly-repo-audit",
  "version": "1.2.0",
  "budget": { "max_cost": 3.00 },
  "context": { "repo": "C:/Projects/Ideas/LoopMaster" },
  "steps": [
    { "name": "fetch",   "type": "shell", "command": "git -C {repo} log --oneline -20" },
    { "name": "audit",   "type": "llm", "model": "@coding", "prompt": "Найди риски в изменениях:\n{fetch.stdout}" },
    { "name": "deepen",  "type": "code", "ref": "risk-scanner@0.3.0", "input": { "findings": "{audit}" } },
    { "name": "gate",    "type": "conditional", "condition": "'high' in deepen.risk_level",
      "then": [ { "name": "alert", "type": "human", "question": "High-risk находки: {deepen.output}. Действия?", "options": ["review","ignore"], "default_answer": "review" } ],
      "else": [ { "name": "log_only", "type": "shell", "command": "echo low-risk" } ] },
    { "name": "report",  "type": "llm", "model": "@fast", "prompt": "Итоговый отчёт: {audit} / {deepen}" }
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
