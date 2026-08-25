# Security Audit — LoopMaster

**Дата:** 2026-08-26 · **Коммит:** main@301144e · **Метод:** субагент codebase-memory-auditor (Tier 3), все утверждения верифицированы прямыми чтениями с диска; граф устарел и не использовался как источник. PoC построены статически, код не исполнялся.

## VERDICT

Главная тема — **коллапс доверенной границы workspace**: три независимых пути (`loop_list`/`loop_get` discovery-exec, автозагрузка `.loopmaster/hooks.py`, CLI `run`) исполняют произвольный Python в процессе MCP-хоста или полностью обходят новый предохранитель `h_shell_allowlist` (H8 покрывает только spec_json-путь loop_run/loop_save). Вторичные темы: наследование host-env в двух executor'ах (утечка секретов), path traversal в entrypoint code-блоков, cross-process TOCTOU на shared SQLite, неэффективный timeout MCPToolExecutor. Сам хук H8 построен корректно (fail-closed, shlex-parity, shell=true запрещён) — проблема в его досягаемости (R3/R4). Эксплуатация предполагает недоверенный workspace-файл / автора спеки / co-resident процесс с доступом к `.loopmaster/jobs.db`.

## CRITICAL

- **R1 — Arbitrary code exec при перечислении циклов**: `discovery.py:34-46` find_loop_files ищет *.py по подстроке "@Loop"/"from loopmaster"; `discovery.py:49-66` load_loop_def_object → exec_module. Вызовы: tools_loops.py:42-48 (loop_list), :68-75 (loop_get, модуль исполняется повторно), tools_run.py:192-201,321,334 (legacy путь). PoC: innocent.py с "# from loopmaster" + urllib exfil OPENAI_API_KEY → любой вызов loop_list исполняет его. Fix: AST-only discovery (ast.parse без exec), ограничить scan-root директорией loops/, кэш по (path,mtime,size).
- **R2 — Автозагрузка .loopmaster/hooks.py из CWD при старте сервера**: hooks.py:101-118 load_user_hooks → exec_module, безусловно из runtime.py:31. Клонированный репо = персистентный RCE в каждой сессии; failure silent. Fix: opt-in env-флаг, логать загруженное, hash allowlist.

## HIGH

- **R3 — CLI run = полный bypass политики**: cli/app.py:130-141 (_run_json_file), :144-165 (_run_python_file) — ноль hooks.trigger; register_builtins() только в mcp/runtime.py:30. LM_SHELL_ALLOWLIST обходится запуском через CLI. Fix: единый chokepoint run_spec_with_policy для CLI/MCP/workers + regression-тест.
- **R4 — Legacy loop_name путь без хуков**: tools_run.py:300-337 — spec_json ветка триггерит BEFORE_LOOP_RUN (:59), loop_name ветка — нет. DetachedRunner.submit тоже публичен без trigger. Fix: trigger в legacy ветке; рассмотреть перенос внутрь submit.
- **R5 — Наследование host-env**: shell.py:147 и mcp.py:164 custom_env=dict(os.environ) → все API-ключи утекают каждому child (PoC: шаг ["printenv"] → stdout → results → loop_status). Fix: port _base_env(env_allow) из code_block.py:54-66; inherit только по явному opt-in.
- **R6 — Entrypoint traversal**: code_store.py:25-94 сохраняет entrypoint verbatim; code_block.py:133-152 пишет/исполняет target_dir/f"{entrypoint}.py" → "../../evil/main" уходит за кэш. Mitigated тем, что block_add не экспонирует entrypoint — но store это shared SQLite. Fix: ^[A-Za-z0-9][A-Za-z0-9._-]*$ при save + resolve().is_relative_to containment при extract; hardening mkdtemp/0700.
- **R7 — MCPToolExecutor.timeout не работает**: mcp.py:188-209 future.result(timeout) бросает, но with-block делает shutdown(wait=True) на readline()-blocked _run → вечное зависание шага (в detached занимает слот max_concurrent=32 навсегда). Плюс readline() без лимита (OOM) и response id не сверяется (response confusion). Fix: daemon-reader+queue c timeout, readline(limit), drop mismatched id.

## MEDIUM

- **R8 — win32 OpenProcess NULL = dead вкл ERROR_ACCESS_DENIED** (store_models.py:76-84): живой elevated-процесс ⇒ его jobs reaped нижепривилегированным инстансом; PID reuse не обрабатывается. Consumers: job_ops.py:304-356, tools_loops.py:160-176, worker.py:152-156. Fix: GetLastError()==5 → alive; lease на (pid, create_time).
- **R9 — Cross-process TOCTOU**: terminal-guard update_job (job_ops.py:107-157) read→check→write под threading lock only; create_job upsert сбрасывает живые rows (:49-85); worker lease racy (:152-165). Несколько MCP-инстансов поддерживаются дизайном. Fix: атомарные условные UPDATE + rowcount; insert-if-absent вместо upsert-reset.
- **R10 — HTTPExecutor**: SSRF через templated url (http.py:80-100); unbounded resp.read()/err.read() (:104-117); redirects вкл HTTPS→HTTP downgrade с ре-отправкой Authorization; **NEW: file:// и ftp:// принимаются** → url:"file:///etc/passwd" возвращает локальный файл в results (persisted). CRLF refuted (stdlib). Fix: scheme allowlist http/https, deny link-local/private, cap bytes, HTTPRedirectHandler без downgrades.
- **R11 — HITL forgery**: msg_id=sha256(job_id|from_addr) детерминирован (message_store.py:49) + from_addr детерминирован (human_input.py:128) + loop_respond без identity (tools_hitl.py:43-52) → co-resident сессия может ответить первой. Bounded single-host/by-design частично. Fix: per-question nonce, тип/лимит answer.

## LOW / hardening

- **R12** Poison-guard regex mismatch: _POISON_REF_RE single-brace (store_models.py:28) vs resolver double-brace → "{{step1.stdout}}" проходит guard, но substitution single-pass (base.py:41-45) — второго порядка раскрытия НЕТ, только литералы в контексте. Align regexes. Auto-answer default_answer не эмитит notification.
- **R13** Escalate-forever: on_timeout=escalate переармит deadline бесконечно (human_input.py:164-168) → пин worker thread/slot 32. Fix: max_escalations или fail после N.
- **R14** critical.json и checkpoints пишутся с default umask (notification_store.py:36-55; checkpoint/__init__.py:47-63 — контекст может содержать секреты, plaintext, без retention). POSIX os.open 0600 + доки.
- **R15** Webhook пересылает summary/detail verbatim включая str(exc)[:120] (worker.py:268,333) — возможна утечка секретов на webhook-endpoint; multiline в логи (log-forgery).
- **R16** Info-leak: db paths в ошибках (code_block.py:113); host_pid в loop_status (tools_loops.py:189-190); loop_respond answer Any без лимита; loop_get создаёт junk job row на каждый осмотр.

## NEW (missed checklist)

- **N1** file:///ftp:// local read (fold R10) — Medium.
- **N2** CostTracker.set_budget/is_over_budget dead code — enforcement только check_budget_limits; max_cost/max_tokens НЕ считают tool/shell шаги (executor результаты без tokens/cost), max_steps считает всё (state.py:110,147). Спека жжёт subprocess/network ресурсы при tight max_cost.
- **N3** Capabilities advisory-only: net/fs:* ни во что не транслируются, блок с нулём capabilities имеет полный fs/net в subprocess (code_block.py:124-131). Реализовать sandbox или переименовать в "advisory tags".
- **N4** Google API key в URL query (llm/client.py:235, streaming.py:243) — попадает в proxy/error logs; предпочесть x-goog-api-key header.
- **N5** LLM base_url env-steerable + Authorization ре-отправляется на cross-host redirects.
- **N6** Legacy jobs без watcher: шаг >900s → следующий loop_status пометит живой job failed (tools_loops.py:168-175) — availability bug.
- **N7** Discovery двойной exec каждого модуля (tools_loops.py:71 + load_loop_def; tools_run.py:198+:334).

## REFUTED / ADJUSTED

1. Poison-guard second-order substitution — НЕ подтверждена (single-pass re.sub) → LOW (R12).
2. Budget: max_steps СЧИТАЕТ tool-шаги ✔; max_cost/max_tokens — нет (см. N2).
3. CRLF header injection — stdlib отклоняет CR/LF в header values → regression test only.
4. Condition eval context — безопасно: имена только из ctx_data.get, Attribute/call/subscript → False (condition.py:29-67).
5. validate_code_refs bypass — не найден; рекурсия совпадает с builder/hook walker. Но capabilities advisory (→N3).
6. Posix is_pid_alive branch уже корректен (PermissionError→alive).

## UNVERIFIED

llm/streaming.py, models/* internals, core/{heartbeat,policies,supervisor,replay}.py, agents/**, telemetry/**, metrics/, checkpoint/migration.py, mcp/models_tools.py, mcp/loop_store.py, cli/blocks.py, templates/**, scripts/**, tests/**, skills/**; фактический umask целевых хостов; практика multi-instance shared DB; transport auth fastmcp (предположен локальный stdio).

## VERIFIED-SAFE

- SQL injection: параметризация везде (job_ops/code_store/message_store/notification_store, вкл LIKE и dynamic IN).
- Condition eval: AST whitelist sound, eval/exec нет.
- Template resolution: single-pass, без recursive expansion и code evaluation.
- Code-block isolation core: minimal env, cwd pinned, capped drained pipes, process-tree kill, sha256-verified cache.
- Хук H8 сам по себе: fail-closed, shlex-parity, shell=true ban, templated/path-separated/UNC exe ban, walk идентичен валидатору. Weakness = reachability (R3/R4), не конструкция.
- Spec validator: unknown-key rejection, semver/name/enums, semantic placeholder validation, dup-name detection.
- HITL state machine atomics (single-process): conditional UPDATE + rowcount.
- HookVeto → clean errors на loop_save/loop_run(spec_json).
- Zombie/process hygiene ShellExecutor/CodeBlockExecutor.
