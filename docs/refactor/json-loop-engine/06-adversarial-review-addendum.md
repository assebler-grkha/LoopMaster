# 06 · Adversarial Review Addendum — Weak Spots & Required Fixes

> Источник: ревью плана HITL/конструктора против реального кода LoopMaster (2026-08-25).
> Дополняет спеки 00–05 этой папки. Пункты привязаны к фазам F1–F6 из 05-implementation-plan.md.
> Все ссылки на код проверены по состоянию ветки `refactor/json-loop-engine`.

## 01-json-loop-spec.md → фаза F1 (JsonLoader)

**A1. Валидация плейсхолдеров при загрузке.**
JsonLoader обязан на этапе load сверять все `{var}`-ссылки с известными источниками вывода шагов; неизвестная ссылка → ошибка загрузки, а не литерал в рантайме. Класс бага подтверждён живьём: в прогоне arch_debate контекст передавался с ключом `task` вместо `goal`, `{goal}` остался литералом во всех промптах (scenario12 использует `{goal}` в строках 15, 39, 65, 85, 166, 179, 217, 236, 406) — дебаты прошли без реальных требований.

**A2. Отклонение невалидных условий при валидации схемы.**
AST-whitelist в `core/condition.py` запрещает `ast.Call`: условие `len('...') > 50` молча вычисляется как False (живой пример — scenario12:227). Спека должна отклонять условия вне Compare/BoolOp форм уже на load; только bare-сравнения (`x == 'yes'`).

**A3. timeout компилировать в конструктор executor'а.**
Поле `Step.timeout` — мёртвый код для executor-шагов: `core/types.py:146-152` уходит напрямую в `executor.execute(ctx_data)`. Единственный рабочий механизм — `ShellExecutor(command=..., timeout=N)` в конструкторе (проверка в `shell.py:125`). Поле спеки `timeout_ms` обязано компилироваться именно туда.

## 02-code-block-store.md → фаза F3 (CodeBlockStore)

**A4. Windows argv-лимит 32 767 символов.**
Аргументы блока больше ~30К крашат CreateProcess (winerror 206). Квотирование list2cmdline корректно (`shell.py:92-93`), лимит — нет. Транспорт больших данных: stdin-pipe (лучший), temp JSON-file, или паттерн NDJSON-over-stdio из `executors/mcp.py:58-123`. Переменные окружения тоже ограничены ~32К — не решение.

**A5. Типизированная схема аргументов блоков.**
Bridge делает `json.loads` на каждом значении (`tests/mcp_tool.py:44-48`) → `"yes"` превращается в `true`, `"42"` в число молча. Без явной типизации ответы человека искажаются.

**A6. Доступ к выводу шагов — только `.stdout`.**
Shell-шаг кладёт в ctx весь объект ShellResult (`state.py:157-158`); точный плейсхолдер `{step}` вернёт repr датакласса (`executors/base.py:35-39`). Обязательное правило спеки и шаблонного линтера: `{step.stdout}`. Форма переживает checkpoint-resume (объект до краша / dict после — оба обрабатывает `resolve_path_value`, `executors/base.py:16-24`). LLM-шаги кладут строку напрямую.

## 03-hitl-protocol.md → фаза F4 (HITL)

**A7. Подключить checkpoint_dir в MCP-путь.**
`scripts/loopmaster_mcp.py:416-428` строит LoopEngine БЕЗ `checkpoint_dir` → чекпойнты memory-only (`make_checkpoint` пишет на диск только при заданном dir, `state.py:82-89`). Рестарт MCP-процесса = потеря всего прогона и дубли вопросов. Для HITL это блокер: передать `checkpoint_dir=".loopmaster/checkpoints"`; опционально инструмент loop_resume через `load_latest_and_migrate` (`checkpoint/__init__.py:94-110`).

**A8. Идемпотентность вопросов.**
Детерминированный id вопроса: `sha256(job_id + step_name)` + `INSERT ... ON CONFLICT DO NOTHING`. Покрывает retry=2 из ErrorPolicy и повтор после рестарта.

**A9. Stale-детектор убивает ждущий джоб.**
Ветка `elif stale >900s` (`scripts/loopmaster_mcp.py:221-240`) помечает running-джоб failed без проверки живости владельца; обновление updated_at происходит только между шагами. Человек, думающий >15 мин, получит «failed» при живом воркере, затем статус перезапишется завершением — flapping. Исправления: heartbeat-поток, трогающий updated_at каждые ~60с; ИЛИ статус waiting_input исключён из stale-логики; startup-sweep (`job_store.mark_interrupted_jobs_on_startup`, уже PID-aware) расширить на `waiting_input`.

**A10. Межпроцессные гонки.**
`update_job`/`record_step_result` — SELECT→mutate→UPDATE под per-process RLock (`job_store.py:169-280`) — не защищает от дублей MCP-процессов. Нужны узкие условные UPDATE: ответ — `WHERE answer IS NULL` (ровно один раз), переходы статусов — `WHERE status='running'`, проверка rowcount.

**A11. SKIP-политика травит цепочку вопроса.**
Упавший шаг не пишет в ctx (`state.py:152`); неразрешённые плейсхолдеры остаются литералами (`executors/base.py:43`). При SKIP литерал `{ask_X}` станет текстом вопроса, и wait будет ждать ответа на мусор весь таймаут. CLI register_question должен громко отклонять вход вида `\{[a-zA-Z_]\w*\}`.

## 04-notifications.md → фаза F5

**A12. EventEmitter не подключён в MCP-пути.**
Движок создаётся без event_emitter (`loopmaster_mcp.py:416-428`) → все события runner/state — no-op; EventEmitter чисто in-memory (`events/__init__.py`). Реализация: либо прокинуть emitter, либо писать events прямо из существующих хуков `_on_step`/`_handle_run_completion`. SQLite-таблица events в jobs.db предпочтительнее jsonl: WAL уже включён, транзакционный append без рваных строк, запросы из инструментов.

## 05-implementation-plan.md → кросс-фазовые

**A13. Detached-run vs дубликаты процессов (F2).**
opencode плодит копии MCP-сервера с общей jobs.db (наблюдалось 3 инстанса). Воркер detached-запуска обязан захватывать lease: host_pid в metrics + heartbeat; запуск при живом lease → отказ. Иначе джоб выполнится дважды.

**A14. Межпроцессный cancel не работает.**
`loop_cancel` ставит строку в DB (`script:256-266`), но движок смотрит только in-process `_cancel_events` (`script:95`) — джоб продолжает работать и перезапишет статус. Runner должен поллить DB cancel-флаг между шагами.

**A15. Гигиена миграций.**
CREATE TABLE IF NOT EXISTS в `_init_schema` ок (WAL, busy_timeout=5000) + мелкий retry + PRAGMA user_version гейтинг, чтобы steady-state вызовы не выполняли DDL. Никогда не держать RLock стора во время сна poll-циклов (иначе сериализуются все инструменты процесса).

**A16. Циклы импортов.**
Не ожидаются: прецедент lazy-import есть (`types.py:134-137`); job_store не тянет core, blocks-библиотеке хватит core.types + executors.base.

## Сводка приоритетов

| Приоритет | Пункты |
|---|---|
| Блокеры | A7 (checkpoint_dir), A9 (stale vs ожидание), A13 (lease воркера) |
| Обязательно | A1, A2, A3, A6, A8, A10, A11, A14 |
| Желательно | A4, A5, A12, A15 |
| Справочно | A16 |
