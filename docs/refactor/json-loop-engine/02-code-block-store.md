# Code Block Store — блоки кода в БД

> Статус: PROPOSAL. Хранилище переиспользуемых исполняемых блоков, на которые ссылается LoopSpec (`"type": "code"`).

## 1. Модель данных (jobs.db, таблица)

```sql
CREATE TABLE IF NOT EXISTS code_blocks (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  version TEXT NOT NULL,              -- SemVer
  language TEXT NOT NULL,             -- python | shell | node (v1: python, shell)
  entrypoint TEXT NOT NULL DEFAULT 'main',
  source BLOB NOT NULL,               -- исходник блока (utf-8)
  sha256 TEXT NOT NULL,               -- hex(source)
  capabilities_json TEXT NOT NULL DEFAULT '[]',
  description TEXT,
  created_at TEXT NOT NULL,
  UNIQUE(name, version)
);
CREATE INDEX idx_code_blocks_name ON code_blocks(name);
```

Иммутабельность: `(name, version)` уникальна; обновление = новая версия. Это же гарантирует воспроизводимость pinning'а.

## 2. Ссылка из LoopSpec

```json
{ "id": "fix", "type": "code", "ref": "test-fixer@1.4.2", "sha256": "9f2c…", "input": {...} }
```

- `ref` обязателен, формат `name@semver`.
- `sha256` опционален: pinning конкретной сборки (рекомендуется для продовых конфигов; валидатор fail-fast при несовпадении).

## 3. Контракт блока (python / shell)

Блок получает на stdin JSON `{"input": <input из LoopSpec>, "context": <текущий контекст job>}` и пишет в stdout JSON:

```json
{ "ok": true, "output": {"patched_files": ["a.py"]}, "logs": ["fixed 2 tests"] }
```

- `output` сливается в контекст шага (как stdout обычного шага, но структурированно).
- Ненулевой exit code или `{"ok": false}` = ошибка шага → обычный ErrorPolicy путь.
- Лимит размера stdout: 1 MiB (защита от раздувания контекста).

## 4. Capabilities

Декларативные метки в `capabilities_json`, назначаются автором блока при регистрации, показываются при `block_get`, проверяются валидатором против явных запретов конфига цикла (`"deny_capabilities": ["net"]`):

| Capability | Значение |
|-----------|----------|
| `net` | Блок ходит в сеть |
| `fs:read:<prefix>` / `fs:write:<prefix>` | Доступ к путям (информативно в v1, enforce в v2) |

**Честное ограничение:** Python не даёт настоящего sandboxing'а. Capabilities в v1 — контракт доверия + аудит, а не изоляция. Реальная изоляция — subprocess с timeout/cwd/env (ниже); жёсткий enforce — будущая работа (Windows Job Objects / контейнер).

## 5. Безопасность исполнения

1. **Никаких import/exec в процессе движка.** Всегда subprocess: временный файл `%TEMP%/loopmaster-blocks/<sha256>/main.py` → `python main.py` (или bash/shell).
2. Timeout (обязателен, default 60s), kill process tree (ShellExecutor уже умеет: `os.setsid` / `taskkill /T`).
3. Окружение: чистый env + whitelist переменных (`PATH`, `PYTHONIOENCODING`, явно разрешённые в конфиге).
4. cwd: временная директория блока; доступ к workspace — только через переданные пути в input.
5. Кэш извлечённых файлов по sha256: одинаковый хеш = одинаковый код (защита от TOCTOU между верификацией и запуском: файл извлекается уже после проверки хеша, директория read-only).
6. Rate: количество одновременных code-блоков ограничено supervisor'ом (как parallel steps).

## 6. API

### CLI
```
loop-engine block add NAME@VERSION --lang python --source file.py --caps net,"fs:write:src/"
loop-engine block get test-fixer@1.4.2 [--verify]
loop-engine block list [PATTERN]
loop-engine block verify test-fixer@1.4.2   # пересчитать sha256, сверить
```

### MCP
| Инструмент | Вход | Действие |
|------------|------|----------|
| `block_list` | `{pattern?}` | Имена+версии+описания (без source) |
| `block_get` | `{ref}` | Метаданные + source + sha256 |
| `block_add` | `{name, version, language, source, capabilities?, description?}` | Регистрация; возвращает sha256 |

Агент может сам регистрировать новые блоки (`block_add`) и собирать из них циклы — это и есть конструктор из постановки задачи.

## 7. Жизненный цикл

```
register(block_add) → referenced in LoopSpec → validate (exists + sha256 match)
→ run (extract→subprocess→stdout JSON) → output merged into context
→ deprecate (флаг deprecated=true, исполнение остаётся возможным, new refs предупреждают)
```
