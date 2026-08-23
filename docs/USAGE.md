# LoopMaster — Полная документация

## Что такое LoopMaster

LoopMaster — это MCP-сервер, который предоставляет **структурированные циклы (loops)** для AI-агентов. Каждый цикл — это последовательность шагов с промптами, моделями, политиками обработки ошибок и бюджетами.

**Ключевая идея:** Агент (opencode) принимает решение какой цикл вызвать, а LoopMaster выполняет его через LLM API, управляя ошибками, повторами и чекпоинтами.

---

## Архитектура

```
┌─────────────────┐     ┌──────────────────────────┐
│   opencode      │     │   LoopMaster MCP Server   │
│   (агент)       │     │                          │
│                 │────>│  loop_list → discovery    │
│                 │<────│  loop_get  → definition   │
│                 │────>│  loop_run  → execution    │
│                 │<────│  loop_result → tracking   │
└─────────────────┘     └──────────────────────────┘
                                │
                                ▼
                        ┌──────────────┐
                        │  LLM API     │
                        │  (OpenAI,    │
                        │  Anthropic,  │
                        │  Google,     │
                        │  OpenRouter) │
                        └──────────────┘
```

---

## Доступные инструменты (MCP Tools)

### 1. loop_list
Открывает доступные циклы в указанной директории.

**Параметры:**
- `search_dir` (str, optional) — директория для поиска

**Возврат:** Список циклов с именами, версиями и количеством шагов.

### 2. loop_get
Получает полное определение цикла для выполнения.

**Параметры:**
- `loop_name` (str) — имя цикла
- `search_dir` (str, optional) — директория для поиска

**Возврат:** JSON с определением цикла, инструкциями по выполнению и job_id.

### 3. loop_run
Выполняет цикл end-to-end через LLM API.

**Параметры:**
- `loop_name` (str) — имя цикла
- `context` (str) — JSON с входными переменными
- `model` (str, optional) — переопределение модели
- `search_dir` (str, optional) — директория для поиска

**Возврат:** Результат выполнения с статусом, затраченным временем и токенами.

### 4. loop_result
Сообщает результат выполнения шага.

**Параметры:**
- `job_id` (str) — ID задачи
- `step_name` (str) — имя шага
- `success` (bool) — успешность
- `output` (str) — вывод (при успехе)
- `error` (str) — ошибка (при неудаче)

### 5. loop_status
Проверяет статус выполнения цикла.

**Параметры:**
- `job_id` (str) — ID задачи

### 6. loop_cancel
Отменяет выполнение цикла.

**Параметры:**
- `job_id` (str) — ID задачи

---

## Настройка окружения

### Необходимые переменные окружения

```bash
# Основные настройки
LOOPMASTER_LLM_PROVIDER=openai          # openai | anthropic | google | openrouter | custom
LOOPMASTER_LLM_API_KEY=sk-xxx           # API ключ
LOOPMASTER_LLM_MODEL=gpt-4              # Модель по умолчанию

# Провайдеры (альтернатива одному LLM_PROVIDER)
LOOPMASTER_OPENAI_API_KEY=sk-xxx
LOOPMASTER_ANTHROPIC_API_KEY=sk-ant-xxx
LOOPMASTER_OPENROUTER_API_KEY=sk-or-xxx
LOOPMASTER_GOOGLE_API_KEY=xxx

# Базовый URL (опционально, для custom)
LOOPMASTER_LLM_BASE_URL=

# Директория с циклами (опционально)
LOOPMASTER_LOOPS_DIR=/path/to/loops
```

### Поддерживаемые провайдеры

| Провайдер | API Key Env | Base URL | Модели по умолчанию |
|-----------|-------------|----------|---------------------|
| OpenAI | `OPENAI_API_KEY` | `https://api.openai.com/v1` | gpt-4, gpt-4-turbo, gpt-3.5-turbo |
| Anthropic | `ANTHROPIC_API_KEY` | `https://api.anthropic.com` | claude-3-opus, claude-3-sonnet |
| Google | `GOOGLE_API_KEY` | `https://generativelanguage.googleapis.com` | gemini-pro |
| OpenRouter | `OPENROUTER_API_KEY` | `https://openrouter.ai/api/v1` | Любые модели |
| Custom | `CUSTOM_API_KEY` | Произвольный | Любые OpenAI-совместимые |

---

## Создание циклов

### Базовый синтаксис

```python
from loopmaster.core.types import Loop, Step

@Loop(name="my_loop", version="0.1.0")
def my_loop(ctx):
    Step("step1", model="gpt-4", prompt="Твой промпт здесь")
    Step("step2", model="gpt-4", prompt="Используй результат: {{step1}}")
    return ctx
```

### Доступные аргументы Step

- `name` (str) — имя шага (обязательно)
- `model` (str) — модель для этого шага
- `prompt` (str) — промпт с {{переменными}}
- `tool` (str) — инструмент для вызова
- `input` (dict) — входные данные для инструмента
- `retry` (int) — количество повторов при ошибке
- `timeout` (int) — таймаут в секундах
- `on_error` (ErrorPolicy) — политика обработки ошибок

### Обработка ошибок

```python
from loopmaster.core.types import Loop, Step, ErrorPolicy, RecoveryAction

@Loop(name="robust_loop", version="0.1.0")
def robust_loop(ctx):
    Step("risky_step", 
         model="gpt-4", 
         prompt="Выполни сложную задачу",
         on_error=ErrorPolicy(
             retry=3,
             backoff=2.0,
             on_failure=RecoveryAction.RETRY
         ))
    Step("fallback_step",
         model="gpt-4",
         prompt="Альтернативный вариант",
         on_error=ErrorPolicy(
             retry=1,
             on_failure=RecoveryAction.FALLBACK,
             fallback_model="gpt-3.5-turbo"
         ))
    return ctx
```

### Бюджет

```python
from loopmaster.core.types import Loop, Step, Budget

@Loop(name="budgeted_loop", version="0.1.0")
def budgeted_loop(ctx):
    # Бюджет ограничивает максимальные затраты
    ctx._budget = Budget(max_cost=5.0, max_tokens=10000)
    Step("expensive_step", model="gpt-4", prompt="Дорогая операция")
    return ctx
```

---

## Сценарии использования

### Сценарий 1: Генерация кода с ревью

```python
@Loop(name="code_gen_review", version="0.1.0")
def code_gen_review(ctx):
    Step("generate", model="gpt-4", 
         prompt="Сгенерируй код для: {{task_description}}")
    Step("review", model="gpt-4",
         prompt="Проведи ревью кода: {{generate}}")
    Step("refactor", model="gpt-4",
         prompt="Отрефактори по замечаниям: {{review}}")
    return ctx
```

**Использование:**
```bash
loop_run(loop_name="code_gen_review", context='{"task_description": "REST API для задач"}')
```

### Сценарий 2: Анализ документации

```python
@Loop(name="doc_analysis", version="0.1.0")
def doc_analysis(ctx):
    Step("extract", model="gpt-4",
         prompt="Извлеки ключевые моменты из: {{document}}")
    Step("summarize", model="gpt-4",
         prompt="Сформируй краткое резюме: {{extract}}")
    Step("action_items", model="gpt-4",
         prompt="Выдели план действий: {{summarize}}")
    return ctx
```

### Сценарий 3: Тестирование с исправлениями

```python
@Loop(name="test_fix", version="0.1.0")
def test_fix(ctx):
    Step("run_tests", model="gpt-4",
         prompt="Запусти тесты для: {{code}}")
    Step("analyze_failures", model="gpt-4",
         prompt="Проанализируй падения: {{run_tests}}")
    Step("fix_code", model="gpt-4",
         prompt="Исправь ошибки: {{analyze_failures}}")
    return ctx
```

### Сценарий 4: Обучение на ошибках

```python
@Loop(name="learning_loop", version="0.1.0")
def learning_loop(ctx):
    Step("attempt", model="gpt-4",
         prompt="Попытайся решить: {{problem}}")
    Step("evaluate", model="gpt-4",
         prompt="Оцени решение: {{attempt}}")
    Step("reflect", model="gpt-4",
         prompt="Извлеки уроки: {{evaluate}}")
    Step("retry_improved", model="gpt-4",
         prompt="Реши заново с учётом: {{reflect}}")
    return ctx
```

### Сценарий 5: Мультиагентное взаимодействие

```python
@Loop(name="multi_agent", version="0.1.0")
def multi_agent(ctx):
    Step("agent1_proposal", model="gpt-4",
         prompt="Предложи решение: {{task}}")
    Step("agent2_critique", model="claude-3-opus",
         prompt="Прокритикуй: {{agent1_proposal}}")
    Step("synthesis", model="gpt-4",
         prompt="Синтезируй лучшие идеи: {{agent1_proposal}} + {{agent2_critique}}")
    return ctx
```

---

## Интеграция с агентными системами

### Режим 1: Агент как исполнитель (Agent-as-Executor)

**Без внешних API.** Агент (opencode) сам является LLM и исполнителем. LoopMaster работает как **оркестратор задач** — предоставляет структуру, а агент выполняет каждый шаг.

**Поток:**
```
Агент → loop_list (найти циклы)
Агент → loop_get (получить определение + job_id)
Агент → [выполнить шаг вручную — прочитать код, найти баг, написать исправление]
Агент → loop_result (сообщить результат)
Агент → [следующий шаг]
Агент → loop_result (завершить)
Агент → loop_status (проверить итог)
```

**Примеры сценариев:**

| Сценарий | LoopMaster роль | Роль агента |
|----------|----------------|------------|
| Code review | analyze → review → fix → verify | Читает код, находит баги, исправляет, проверяет |
| Research | search → read → synthesize → summarize | Ищет, читает, систематизирует |
| Refactoring | identify → plan → refactor → test | Находит проблемы, планирует, рефакторит, тестирует |
| Bug investigation | reproduce → analyze → fix → verify | Воспроизводит, анализирует, исправляет, проверяет |
| Documentation | scan code → generate → review → publish | Сканирует, генерирует, проверяет |

**Пример code review loop:**

```python
from loopmaster.core.types import Loop, Step

@Loop(name="code_review", version="0.1.0")
def code_review(ctx):
    Step("analyze", model="gpt-4", prompt="Проанализируй код: {{file_path}}")
    Step("review", model="gpt-4", prompt="Проведи ревью: {{analyze}}")
    Step("fix", model="gpt-4", prompt="Исправь замечания: {{review}}")
    Step("verify", model="gpt-4", prompt="Проверь исправления: {{fix}}")
    return ctx
```

**Использование:**
```bash
# 1. Найти циклы
loop_list(search_dir="/path/to/loops")

# 2. Получить определение (создаёт job_id)
loop_get(loop_name="code_review")
# → Возвращает: steps, job_id, инструкции

# 3. Агент выполняет шаг "analyze" (читает файл, анализирует)
# 4. Сообщает результат
loop_result(job_id="code_review_123", step_name="analyze", success=True, output="Найдены 3 замечания...")

# 5. Следующий шаг "review" — агент проверяет найденные проблемы
# 6. Сообщает результат
loop_result(job_id="code_review_123", step_name="review", success=True, output="Ревью завершено...")

# 7. И так далее...
```

**Преимущества:**
- Зависимость только от агента (никаких внешних API)
- Полный контроль над каждым шагом
- Работают политики ошибок, бюджеты, чекпоинты
- Агент получает чёткий план действий

### Режим 2: Автоматическое выполнение через API

LoopMaster выполняет цикл end-to-end через LLM API (OpenAI, Anthropic, Google, OpenRouter, Custom).

```bash
# Настройка окружения
export LOOPMASTER_LLM_PROVIDER=openai
export LOOPMASTER_LLM_API_KEY=sk-xxx
export LOOPMASTER_LLM_MODEL=gpt-4

# Выполнение
loop_run(loop_name="code_gen_review", context='{"task": "..."}')
```

### Режим 3: Гибридный

Агент управляет стратегией, LoopMaster выполняет отдельные шаги через API.

```python
# Агент решает: какой цикл вызвать
# LoopMaster выполняет steps через API
# Агент анализирует результат и решает: продолжать или остановиться
```

### opencode

LoopMaster интегрирован с opencode через MCP. Агент может:

1. **Найти циклы:** `loop_list(search_dir="/path/to/loops")`
2. **Получить определение:** `loop_get(loop_name="code_gen_review")`
3. **Выполнить:** `loop_run(loop_name="code_gen_review", context='{"task": "..."}')`
4. **Отследить:** `loop_status(job_id="...")`

### AgentDB

LoopMaster может интегрироваться с базой данных агента:

```python
# Сохранение паттернов
Step("save_pattern", tool="agentdb_store", 
     input={"domain": "code-patterns", "content": "{{learned_pattern}}"})

# Загрузка контекста
Step("load_context", tool="agentdb_search",
     input={"query": "{{problem_type}}", "domain": "solutions"})
```

### Автономный режим

LoopMaster может работать без агента, выполняя циклы напрямую через API:

```bash
# Установка переменных окружения
export LOOPMASTER_LLM_PROVIDER=openai
export LOOPMASTER_LLM_API_KEY=sk-xxx
export LOOPMASTER_LLM_MODEL=gpt-4

# Выполнение цикла
python -c "
from scripts.loopmaster_mcp import loop_run
result = loop_run('simple_test', '{}')
print(result)
"
```

---

## Проактивное использование LoopMaster агентом

Агент (opencode) должен **проактивно** предлагать использование LoopMaster, когда распознаёт подходящую задачу. Ниже — правила, триггеры и порядок действий.

### Когда предлагать цикл

| Триггер | Пример задачи | Рекомендуемый цикл |
|---------|---------------|-------------------|
| Многовариантная задача | "Сделай code review файла X" | `code-review` (parallel: security + performance + style) |
| Повторяющийся паттерн | "Напиши тесты для модуля Y" | `test-generation` (generate → validate → fix) |
| Итеративное улучшение | "Улучши этот код" | `reflection` (execute → evaluate → revise) |
| Research + synthesis | "Исследуй тему Z" | `research` (search → analyze → synthesize) |
| Анализ с ветвлениями | "Проверь условия и действуй" | `Conditional` + шаги |
| Бюджетный контроль | "Сделай дёшево, до $0.50" | Loop с `Budget(max_cost=...)` |

### Когда НЕ предлагать

- Однострочные вопросы ("Что такое Python?")
- Задачи, требующие уточнений у пользователя
- Прямые команды без неявной структуры ("Исправь баг")
- Задачи, которые быстрее выполнить вручную (1-2 шага)

### Порядок действий при проактивном предложении

```
1. loop_list()                           — найти доступные циклы
2. Если подходящего нет:
   loop-engine init name -t template    — создать из шаблона
3. Спросить подтверждение:
   "Запустить цикл [name] для [task]?"
4. loop_run(name, context)              — выполнить
5. loop_status(job_id)                  — проверить результат
```

### Пример проактивного поведения

**Пользователь:** "Проведи ревью этого файла"

**Агент (без LoopMaster):** Читает файл, пишет замечания вручную.

**Агент (с LoopMaster):**
```
→ loop_list() — найден цикл "code-review" v1.0.0 (4 шага)
→ "Эту задачу лучше выполнить циклом code-review:
   parallel анализ (security + performance + style) → merge.
   Запустить?"
→ loop_run("code-review", '{"file_path": "src/main.py"}')
→ Отправляет пользователю структурированный отчёт
```

### Документация для справок

| Документ | Содержание |
|----------|-----------|
| [`AGENT_GUIDE.md`](AGENT_GUIDE.md) | Выбор моделей, алиасы (@fast, @smart, @coding), best practices |
| [`AGENTS.md`](AGENTS.md) | Полный справочник MCP-инструментов (loop_list, loop_run, loop_result) |
| [`loops/`](../loops/) | Примеры готовых циклов (simple, error_handling, conditional, parallel) |

---

## Примеры циклов

### Простой тестовый цикл

```python
"""Простой тестовый цикл для проверки LoopMaster."""
from loopmaster.core.types import Loop, Step

@Loop(name="simple_test", version="0.1.0")
def simple_test_loop(ctx):
    Step("greet", model="gpt-4", prompt="Поприветствуй пользователя")
    Step("task", model="gpt-4", prompt="Объясни что такое LoopMaster в 2 предложениях")
    Step("summary", model="gpt-4", prompt="Подведи итог: {{greet}} и {{task}}")
    return ctx
```

### Цикл с обработкой ошибок

```python
"""Цикл с политикой обработки ошибок и бюджетом."""
from loopmaster.core.types import Loop, Step, ErrorPolicy, RecoveryAction, Budget

@Loop(name="error_handling_test", version="0.1.0")
def error_handling_loop(ctx):
    Step("risky_step", model="gpt-4", 
         prompt="Выполни рискованную операцию",
         on_error=ErrorPolicy(retry=2, backoff=1.0, on_failure=RecoveryAction.RETRY))
    Step("fallback_step", model="gpt-4",
         prompt="Альтернативный вариант",
         on_error=ErrorPolicy(retry=1, on_failure=RecoveryAction.FALLBACK, fallback_model="gpt-3.5-turbo"))
    Step("final", model="gpt-4", prompt="Финальный шаг")
    return ctx
```

---

## Безопасность

1. **API ключи** nunca не коммитятся в git
2. **Переменные окружения** — безопасный способ хранения секретов
3. **Бюджеты** — ограничение максимальных затрат
4. **Таймауты** — предотвращение зависания
5. **Политики ошибок** — контроль при сбоях

---

## Часто задаваемые вопросы

### Q: Какой провайдер выбрать?
A: **OpenAI** — для большинства задач. **Anthropic** — для сложного анализа. **OpenRouter** — для доступа к разным моделям.

### Q: Как передать контекст в цикл?
A: Используйте `context` в `loop_run` или `{{переменные}}` в промптах.

### Q: Можно ли использовать разные модели в одном цикле?
A: Да, указывайте `model` для каждого шага.

### Q: Что делать при ошибке?
A: LoopMaster автоматически повторяет шаги согласно `ErrorPolicy`. При критических ошибках цикл останавливается.

---

## Контрибуция

1. Fork репозиторий
2. Создайте ветку `feature/your-feature`
3. Внесите изменения
4. Запустите тесты: `pytest`
5. Отправьте PR

---

## Лицензия

MIT License