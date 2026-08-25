# HITL-протокол — общение цикла с агентом/LLM/MCP через JSON

> Статус: PROPOSAL. Единый message envelope в jobs.db; шаг `human` = durable pause.

## 1. Message Envelope (таблица `messages`)

```sql
CREATE TABLE IF NOT EXISTS messages (
  msg_id TEXT PRIMARY KEY,            -- uuid4
  job_id TEXT NOT NULL,
  from_addr TEXT NOT NULL,            -- "loop:code-review#confirm" | "agent" | "mcp:hexstrike"
  to_addr TEXT NOT NULL,              -- "agent" | "human" | "mcp:<server>" | "loop:<name>#<step>"
  type TEXT NOT NULL,                 -- question|answer|event|status
  payload_json TEXT NOT NULL,
  reply_to TEXT,                      -- msg_id вопроса
  status TEXT NOT NULL,               -- pending|answered|expired|cancelled
  created_at TEXT NOT NULL,
  expires_at TEXT,
  answered_json TEXT
);
CREATE INDEX idx_messages_job ON messages(job_id);
CREATE INDEX idx_messages_inbox ON messages(to_addr, status);
```

Пример question:
```json
{ "msg_id": "…", "job_id": "job_42", "from_addr": "loop:nightly-repo-audit#alert",
  "to_addr": "agent", "type": "question",
  "payload": { "text": "High-risk находки: …", "options": ["review","ignore"] },
  "expires_at": "2026-08-26T00:00:00Z", "status": "pending" }
```

## 2. Жизненный цикл шага `human`

```
движок дошёл до human-шага
→ создать question (status=pending)
→ job.state = WAITING_INPUT, чекпоинт (data-only, ADR-003)
→ поток воркера освобождён  ──────────────►  движок свободен для других jobs

агент/человек отвечает:
loop_respond(job_id, msg_id, answer="yes")
→ messages.status=answered, answered_json={"answer":"yes"}
→ job → QUEUED → resume: контекст получает {<step_id>: answer}
→ branch stickiness гарантирует продолжение по той же ветке conditional
```

Ключевое: **pause не потребляет ресурсов** — это чекпоинт + запись в БД. Падение процесса между паузой и ответом безопасно: resume находит pending question и ждёт дальше.

## 3. Таймауты и политики

`timeout` (ISO-8601 duration: `"30m"`, `"24h"`). По истечении — `on_timeout`:

| Политика | Поведение |
|----------|-----------|
| `default_answer` | В контекст кладётся `default_answer`, вопрос помечается `answered(auto)` |
| `skip` | Шаг пропущен, `{step_id}` = null |
| `fail` | Шаг/цикл падает (ErrorPolicy путь) |
| `escalate` | Новый question с повышенным приоритетом `critical` (см. 04), таймер сбрасывается |

Проверка таймаутов — ленивая, при обращениях к БД + периодическая свипка в воркере (раз в минуту). Никаких фоновых таймеров на каждый вопрос.

## 4. Направления общения (матрица)

| От | Кому | Механизм |
|----|------|----------|
| loop step `llm` | внешняя LLM | обычный LLM client (multi-provider) — «отдавать ответы» модели |
| loop step `mcp` | MCP-сервер | MCPToolExecutor |
| loop step `human` (`ask:"agent"`) | агент opencode | question в inbox → агент отвечает `loop_respond` |
| loop step `human` (`ask:"human"`) | человек | notification `needs_input` (см. 04); ответ — через агента или CLI `loop-engine respond` |
| agent | loop | `loop_respond` (ответы), `loop_status` (опрос) |
| loop | агент (события без ожидания) | outbox events (см. 04), не блокирует цикл |

## 5. MCP-инструменты (новые)

| Инструмент | Вход | Выход |
|------------|------|-------|
| `loop_respond` | `{job_id, msg_id, answer}` | Новое состояние job / следующее действие |
| `loop_questions` | `{job_id?}` | Открытые questions (pending) |
| `loop_run` (расширенный) | `{loop_name \| spec_json, variables?, mode?: "detached"\|"sync"}` | job_id немедленно при detached |

`mode="detached"` — дефолт для соответствия требованию «работа агента не прерывается»: инструмент возвращается сразу, исполнение — в фоне.

## 6. Идемпотентность и гонки

- Повторный `loop_respond` на answered/expired вопрос → ошибка `already_answered` (не молчаливый успех).
- Ответ после истечения таймаута, но до свипки: принимается, если вопрос ещё `pending` (гонка в пользу человека).
- Два воркера не подхватят один job: существующий PID-aware механизм JobStore.
- Отмена job с pending question: вопросы получают `status=cancelled`.

## 7. Что НЕ входит в v1

- Push/websocket к агенту (технически недоступно для opencode).
- Диалоги из нескольких сообщений внутри одного human-шага (один вопрос — один ответ; серия вопросов = серия шагов).
- Вложения/файлы в конверте (payload только JSON; файлы — путями).
