# План реализации — JSON Loop Engine

> Статус: SHIPPED (Ф1–Ф6 реализованы в ветке `refactor/json-loop-engine`, коммиты df27b1b…0c0e68e; финальное ревью пройдено).

## Фазы

### Фаза 0 — Спецификация (этот пакет) ✅
Документы 00–04 + ADR-011. Коммит в `refactor/json-loop-engine`.

### Фаза 1 — JsonLoader + валидация (~2-3 дня)
- [x] `schemas/loopspec-v1.schema.json`
- [x] `src/loopmaster/spec/loader.py`: parse → validate (schema + semantic) → IR (Step/Parallel/Conditional)
- [x] AST-выражения условий через существующий `core/condition.py` (адаптер строки→AST)
- [x] CLI: `loop-engine validate loop.json`, `run loop.json --dry-run`
- [x] Тесты: happy path, каждая ошибка валидации, маппинг всех 8 типов узлов
- **Критерий готовности:** JSON-цикл исполняется локальным движком синхронно, все 374 старых теста зелёные.

### Фаза 2 — LoopStore + detached запуск (~2 дня)
- [x] Таблицы `loops`, расширение JobStore (+миграция SemVer registry — механизм есть)
- [x] `loop_run(spec_json|loop_name, mode="detached")` в MCP + фоновый воркер (поток/процесс)
- [x] `loop_status` показывает detached jobs (уже умеет через JobStore)
- **Критерий:** агент ставит job и продолжает работать; job доезжает; zombie detection работает.

### Фаза 3 — CodeBlockStore (~3 дня)
- [x] Таблица `code_blocks`, extract-by-sha256 кэш, subprocess runner (timeout/env/cwd/process-tree kill)
- [x] CLI block add/get/list/verify; MCP block_add/block_get/block_list
- [x] Capabilities: декларация + deny_capabilities валидация
- [x] Security-тесты: sha256 mismatch, timeout kill, oversized stdout, env isolation
- **Критерий:** цикл из Фазы 1 использует code-блок из БД; подмена хеша ловится fail-fast.

### Фаза 4 — HITL (~3 дня)
- [x] Таблица `messages`; HumanInputExecutor; state WAITING_INPUT; resume-path
- [x] MCP `loop_respond`, `loop_questions`; таймаут-свипка (ленивая + минутная)
- [x] Политики on_timeout; идемпотентность ответов; cancel закрывает вопросы
- [x] Тесты: pause→respond→resume, crash между паузой и ответом, timeout каждой политики, двойной respond
- **Критерий:** цикл ставит вопрос, процесс может умереть, ответ после перезапуска корректно резюмится.

### Фаза 5 — Notifications (~1-2 дня)
- [x] Таблица `notifications`; эмиссия из движка (hook в EventEmitter — уже есть шина)
- [x] MCP `loop_inbox`; поле `pending_notifications` во всех ответах инструментов
- [x] `.loopmaster/inbox/critical.json` fallback; ретеншн-свипка
- [x] Обновить AGENTS.md / docs/AGENT_GUIDE.md протоколом опроса
- **Критерий:** e2e — цикл ждёт ввода, агент видит needs_input без явного опроса, отвечает, получает результат.

### Фаза 6 — Усыновление и полировка (~2 дня)
- [x] Компилятор Python DSL → LoopSpec (`export --format json`, по образцу yaml_export.py) — dual-mode навсегда
- [x] 1-2 показательных цикла из loops/ перевести в JSON как примеры (не насильно остальные)
- [x] README/ROADMAP обновление; LOOP-CATALOG пометки
- **Критерий:** документация согласована, примеры работают end-to-end.

## Порядок и зависимости

```
Ф0 ──► Ф1 ──► Ф2 ──► Ф5
        │      │
        └─► Ф3 └─► Ф4 ──► Ф6
```
Ф3 и Ф4 независимы друг от друга (могут идти параллельно после Ф1). Ф5 зависит только от JobStore+EventEmitter (можно даже раньше Ф4), но логично после HITL.

## Риски реализации

| Риск | Митигация |
|------|-----------|
| Воркер detached в процессе MCP-сервера умирает вместе с сервером | Опция standalone `loop-engine serve --worker` для длинных циклов; heartbeat/zombie уже покрывают |
| Разрастание JSON Schema | Строгий v1 (8 узлов), изменения только через версию формата + миграции |
| Смешение состояний job (старые Python-jobs vs новые) | Единый JobStore, поле `spec_source: python\|json` |
| Таймаут-свипка пропустит истёкший вопрос при простое | Свипка привязана к любому обращению к БД + минутный тик воркера |

## Definition of Done (весь рефакторинг)

- Все тесты зелёные (старые 374 + новые ~80-100), aislop ≥ baseline.
- E2E сценарий постановки задачи выполняется полностью через MCP без блокировки агента.
- Документы 00–04 актуализированы по факту реализации (не расходятся с кодом).
