# Ревью идеи и ключевые решения: JSON-движок автономных циклов

> Статус: PROPOSAL (ветка `refactor/json-loop-engine`)
> Связанные ADR: supersede частично [ADR-001](../../adr/001_python_dsl_as_source_of_truth.md) → [ADR-011](../../adr/011_json_config_as_execution_format.md)
> Опирается на: ADR-002 (runtime interpretation), ADR-003 (data-only checkpoints), ADR-006 (MCP as transport)

## 1. Формулировка задачи (как понял)

1. **Циклы из JSON**: движок берёт задачу из БД, парсит JSON-конфигурацию (шаги, ветвления, порядок), исполняет.
2. **Импорт кода из БД**: JSON ссылается на блоки кода, которые хранятся в БД и выполняются циклом.
3. **Общение через JSON с внешними LLM / агентом / MCP**: цикл отдаёт ответы, задаёт вопросы, получает ответы; умеет приостанавливаться в ожидании ответа.
4. **Уведомления о событиях**, которые агент может принимать и на которые может отвечать своевременно.
5. **Непрерывность агента**: пока цикл работает, агент продолжает свою работу — никакого блокирующего ожидания.

## 2. Ревью идеи

### Сильные стороны

| # | Аспект | Почему это выигрыш |
|---|--------|--------------------|
| 1 | Цикл = данные (JSON), а не код | Циклы можно хранить в БД, версионировать, передавать по MCP, **генерировать агентом на лету** без записи .py файлов |
| 2 | Блоки кода в БД | Переиспользование, pinning версий, будущий marketplace (совпадает с ROADMAP), аудит изменений |
| 3 | HITL через JSON-сообщения | Естественное продолжение ADR-006; durable pause/resume уже поддержан чекпоинтами |
| 4 | Асинхронность | Движок уже живёт отдельно (MCP/CLI); остаётся формализовать «fire-and-forget» запуск |

### Риски и их снятие

| # | Риск | Решение |
|---|------|---------|
| R1 | Противоречие с ADR-001 («Python DSL as source of truth») | Новый ADR-011: **JSON — execution format**, Python DSL — authoring format. Обратная совместимость полная: существующие 13 циклов продолжают работать; добавляется компилятор Python→JSON по образцу yaml_export.py |
| R2 | Импорт произвольного кода из БД = RCE-поверхность | Code Block Store с SHA-256 pinning в конфиге, capabilities-модель, исполнение только через изолированный subprocess (ShellExecutor уже убивает process tree), запрет `import`-based исполнения в процессе движка |
| R3 | Выражения условий в JSON → temptation к `eval()` | Переиспользуем существующий AST-whitelist парсер (`core/condition.py`, 104 строки). В JSON условия хранятся строками того же синтаксиса |
| R4 | «Цикл ждёт ответа» = потеря состояния при падении | Durable pause: state `WAITING_INPUT` + чекпоинт + сообщение в БД. Resume детерминирован (ADR-003 data-only checkpoints, branch stickiness уже решён) |
| R5 | Агент не умеет получать push | Честная poll-модель: inbox/outbox в SQLite + MCP-инструменты чтения. Push невозможен в opencode — не притворяемся, что возможен |
| R6 | JSON-конфиги разрастаются в «язык в языке» | Жёсткая граница v1: 8 типов узлов, никаких пользовательских функций в JSON; вся логика — в блоках кода или выражениях AST-whitelist |

### Вердикт

Идея жизнеспособна и логично завершает эволюцию проекта: MCP-as-transport (ADR-006) уже превратил циклы в данные для агента — доводим до конца. Главные нетривиальные части: безопасность code blocks (R2) и durable HITL (R4).

## 3. Конкретные решения по пунктам

### П.1 «Цикл из JSON» → LoopSpec v1 + JsonLoader + LoopStore

- **LoopSpec** — декларативный JSON (см. [01-json-loop-spec.md](01-json-loop-spec.md)): шаги 8 типов (`llm`, `shell`, `http`, `mcp`, `code`, `human`, `parallel`, `conditional`), шаблоны `{var}` как сейчас, условия строками AST-whitelist.
- **JsonLoader** компилирует LoopSpec → существующие `Step`/`Parallel`/`Conditional` объекты. **Ядро движка не меняется** — меняется источник определений. Это снимает 80% риска: вся проверенная машинерия (budget, error policy, heartbeat, checkpoint, replay) работает как есть.
- **Валидация**: JSON Schema (`schemas/loopspec-v1.schema.json`) + семантические проверки (уникальность id шагов, существование ссылок на переменные — best effort, ссылки на существующие блоки кода).
- **БД**: таблица `loops(id, name, version, spec_json, sha256, created_at)` в существующем `.loopmaster/jobs.db` (JobStore расширяется, второй файл БД не заводим). Задача ставится через `loop_run(spec=…|loop_name=…)`.

### П.2 «Импорт блоков кода из БД» → CodeBlockStore

- Таблица `code_blocks(name, version, language, source, sha256, entrypoint, capabilities_json, created_at)`; в LoopSpec ссылка вида `"ref": "auto-fixer@2.1.0"` + опционально `"sha256"` для pinning.
- Исполнение: извлечение во временный файл → subprocess (python/shell/node) через ShellExecutor с timeout, cwd, ограничением env. **Ничего не импортируем в процесс движка.**
- Capabilities (декларативно, для аудита и будущих политик): `net`, `fs:read:<path>`, `fs:write:<path>`.
- Детали: [02-code-block-store.md](02-code-block-store.md).

### П.3 «Общение через JSON, вопросы-ответы, pause» → Message Protocol + step `human`

- Единый конверт сообщения (см. [03-hitl-protocol.md](03-hitl-protocol.md)) в таблице `messages` той же БД: типы `question|answer|event|status`, адресация `loop:<name>#<step>` ↔ `agent|mcp:<server>|human`.
- Шаг `"type": "human"` создаёт question, переводит job в `WAITING_INPUT`, сохраняет чекпоинт и **освобождает поток**.
- Ответ приходит через MCP `loop_respond(job_id, msg_id, answer)` → job возвращается в очередь → резюм с ветки ожидания (branch stickiness уже есть).
- Таймауты с политиками: `default_answer | skip | fail | escalate`.
- Вопросы к внешней LLM — это обычный шаг `llm`; вопросы к MCP — шаг `mcp`. Протокол сообщений покрывает всё единым конвертом.

### П.4 «Уведомления без прерывания агента» → Outbox + poll-MCP

- Все события пишутся в outbox (таблица `notifications`) + дублируются в существующий SSE EventEmitter для интерактивных клиентов.
- Агент опрашивает: `loop_inbox(unread_only=true)` — дёшево, вызывается когда удобно. Критичные (`needs_input`) видны при следующем же обращении агента к любому loopmaster-инструменту (в ответ каждого инструмента добавляем поле `pending_messages: N`).
- Протокол для AGENTS.md: «перед началом и после завершения задачи — проверить loop_inbox». Детали: [04-notifications.md](04-notifications.md).

### П.5 «Работа агента не прерывается» → detached execution

- Запуск: `loop_run(..., mode="detached")` ставит job в БД и мгновенно возвращается; фоновый воркер (поток MCP-сервера или отдельный процесс `loop-engine serve --worker`) подхватывает.
- Агент никогда не блокируется: статус — `loop_status` (poll), ответы — `loop_respond`, события — `loop_inbox`.
- Zombie detection/heartbeat уже реализованы — переиспользуются.

## 4. Что НЕ делаем (осознанно)

- Не переписываем ядро движка под JSON-интерпретатор — только загрузчик (ADR-002 runtime interpretation сохраняется).
- Не строим визуальный редактор/DAG-UI.
- Не вводим собственный язык выражений — только существующий AST-whitelist синтаксис.
- Не обещаем push-уведомлений агенту — только poll + маркер в ответах инструментов.
- Не мигрируем 13 существующих Python-циклов насильно — dual-mode бесконечно, миграция опциональна (Фаза 6 плана).

## 5. Состав пакета документов

| Документ | Содержание |
|----------|------------|
| [01-json-loop-spec.md](01-json-loop-spec.md) | Спецификация LoopSpec v1: схема, типы узлов, примеры |
| [02-code-block-store.md](02-code-block-store.md) | CodeBlockStore: схема БД, capabilities, безопасность исполнения |
| [03-hitl-protocol.md](03-hitl-protocol.md) | Message envelope, состояния job, таймауты, resume |
| [04-notifications.md](04-notifications.md) | Outbox/inbox, приоритеты, интеграция с агентом |
| [05-implementation-plan.md](05-implementation-plan.md) | Фазы, оценки, критерии готовности |
| [ADR-011](../../adr/011_json_config_as_execution_format.md) | Архитектурное решение: JSON as execution format |
