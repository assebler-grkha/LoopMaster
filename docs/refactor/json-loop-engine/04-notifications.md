# Notifications — уведомления без прерывания агента

> Статус: PROPOSAL. Outbox-модель + poll через MCP. Push невозможен для opencode — не имитируем его.

## 1. Принцип

Все события циклов пишутся в **outbox** (SQLite, таблица ниже) и дублируются в существующий SSE EventEmitter (для интерактивных клиентов, где он подключён). Агент читает outbox, **когда ему удобно**, через дешёвые poll-инструменты. Работа агента никогда не блокируется.

```sql
CREATE TABLE IF NOT EXISTS notifications (
  notif_id TEXT PRIMARY KEY,
  job_id TEXT,
  priority TEXT NOT NULL,             -- info | needs_input | critical
  event TEXT NOT NULL,                -- loop_started|step_completed|waiting_input|loop_failed|…
  summary TEXT NOT NULL,              -- одна строка для быстрого сканирования
  detail_json TEXT,
  read_by_agent INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL
);
CREATE INDEX idx_notif_unread ON notifications(read_by_agent, priority);
```

## 2. Приоритеты

| Приоритет | Пример | Ожидаемая реакция агента |
|-----------|--------|--------------------------|
| `info` | loop_completed, cost report | Можно игнорировать до удобного момента |
| `needs_input` | waiting_input (question pending) | Ответить при первой возможности (`loop_respond`) |
| `critical` | loop_failed после ретраев, budget exhausted, escalate | Посмотреть в текущей же сессии |

## 3. Канал доставки к агенту (3 уровня)

1. **Маркер в каждом ответе MCP-инструмента** (ключевой механизм): любой ответ любого loopmaster-инструмента содержит поле `"pending_notifications": {"needs_input": 1, "critical": 0}`. Агент, работающий с циклами, видит сигнал даже не опрашивая inbox специально.
2. **`loop_inbox(unread_only=true, limit=20)`** — явное чтение; помечает прочитанным (опционально `mark_read=false`).
3. **Файловый fallback**: `.loopmaster/inbox/critical.json` перезаписывается при каждом critical-событии — читается любым инструментом файловой системы, работает даже без MCP.

## 4. Протокол для AGENTS.md (секция LoopMaster)

> - Перед началом новой задачи и после её завершения вызови `loop_inbox(unread_only=true)`.
> - Если есть `needs_input` — ответь на открытые вопросы (`loop_questions` → `loop_respond`) до старта новой работы.
> - Если увидел `pending_notifications.critical > 0` в любом ответе инструмента — разбери до продолжения.

Это контракт дисциплины опроса, а не техническая гарантия — так честно.

## 5. Связь с HITL

Шаг `human` создаёт одновременно: message (question, см. 03) + notification `needs_input`. Ответ через `loop_respond` закрывает оба (message.answered + notification.read).

## 6. Ретеншн

- `notifications.read_by_agent=1` и старше 7 дней → удаляются свипкой воркера.
- `messages` хранятся 30 дней (аудит), затем архивируются в `.loopmaster/archive/`.

## 7. Анти-спам

- Не более 1 notification на шаг (промежуточные step_chunk'и не уведомляют).
- `info`-события можно отключить per-job: `"notify": ["needs_input", "critical"]` в LoopSpec (дефолт — все три).

> **Исключение:** notification `waiting_input` от human-шага создаётся **в обход** notify-фильтра — иначе фильтр без `needs_input` привёл бы к dead-lock (никто не узнает о pending вопросе). Фильтр применяется только к событиям жизненного цикла (`loop_started`, `loop_completed`, `loop_failed`).
