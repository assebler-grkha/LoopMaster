# Loop Architectures: security_audit, doc_generator, changelog_gen

## AgentDB Storage Standard

All loops store results in agentdb with consistent schema:

```
doc_id:    {loop_name}-{project_name}-{timestamp}
domain:    {loop_name}
content:   Human-readable report (markdown)
metadata:  JSON with structured fields for search
```

### Searchable Metadata Fields (common)

```json
{
  "project": "project-name",
  "timestamp": "ISO-8601",
  "score": 85,
  "status": "pass|warn|fail",
  "tags": ["security", "secrets", "dependencies"]
}
```

### Search Pattern

```bash
python tests/mcp_tool.py agentdb_search --query "security_audit project-name" --domain "security_audit"
```

---

## Loop 1: security_audit

**Problem:** Manual security reviews miss secrets in code, vulnerable dependencies, and insecure patterns. Need automated, repeatable security scanning.

**Trigger:** User says "проверь проект на безопасность", "security audit", "check for secrets/vulns".

**Architecture (7 steps):**

```
Step 1: grep_secrets (ShellExecutor)
  Command: grep -rn --include="*.py" --include="*.js" --include="*.env" \
    -E "(password|secret|api_key|token|AWS_|PRIVATE_KEY)" {project_dir}
  Output: {grep_secrets.stdout} — raw grep hits

Step 2: check_deps (ShellExecutor)
  Command: cd {project_dir} && pip-audit --format json 2>/dev/null || \
    npm audit --json 2>/dev/null || echo '{"note":"no package manager found"}'
  Output: {check_deps.stdout} — JSON with CVE list

Step 3: scan_security (ShellExecutor)
  Command: python {bridge} aislop_scan --path {project_dir}
  Output: {scan_security.stdout} — aislop JSON with security findings

Step 4: classify (LLMStep, model=@fast)
  Prompt: "Classify these security findings by severity.\n\n\
    SECRETS:\n{grep_secrets.stdout}\n\n\
    DEPENDENCIES:\n{check_deps.stdout}\n\n\
    CODE QUALITY:\n{scan_security.stdout}\n\n\
    Return JSON: {critical: [], high: [], medium: [], low: []}"
  Output: {classify}

Step 5: report (LLMStep, model=@smart)
  Prompt: "Write a security audit report for project '{project_name}'.\n\n\
    Classified findings:\n{classify}\n\n\
    Format as:\n\
    # Security Audit Report\n\
    ## Critical\n...\n## High\n...\n## Recommendations\n..."
  Output: {report}

Step 6: store (ShellExecutor)
  Command: python {bridge} agentdb_store \
    --doc_id "security_audit-{project_name}-{timestamp}" \
    --domain "security_audit" \
    --content "{report}" \
    --metadata '{"project":"{project_name}","critical":N,"high":N,"status":"{status}"}'
  Output: {store.stdout}

Step 7: summary (LLMStep, model=@fast)
  Prompt: "Summarize the security audit in 2-3 sentences.\n{report}"
  Output: {summary}
```

**Conditional (after Step 4):**
```python
Conditional(
    condition="'critical' in '{classify}'.lower() and '[]' not in '{classify}'.split('critical')[1][:20]",
    then_steps=[extra_analysis_step],  # deep dive on critical items
    else_steps=[],  # proceed normally
)
```

**AgentDB Output Standard:**
- `doc_id`: `security_audit-{project}-{YYYY-MM-DD-HH-MM}`
- `domain`: `security_audit`
- `metadata.tags`: `["security", "secrets", "dependencies", "CVE"]`
- `metadata.status`: `"fail"` if critical>0, `"warn"` if high>0, `"pass"` otherwise

**Search examples:**
```bash
# Find all security audits for a project
agentdb_search --query "security_audit my-project" --domain "security_audit"

# Find audits with critical findings
agentdb_search --query "critical CVE" --domain "security_audit"
```

---

## Loop 2: doc_generator

**Problem:** Documentation drifts from code. Functions get renamed, signatures change, docs become stale.

**Trigger:** User says "обнови доки", "generate API docs", "docs are out of date".

**Architecture (6 steps):**

```
Step 1: find_functions (ShellExecutor)
  Command: python {bridge} search_graph --query "function method class" \
    --project {cm_project} --limit 50
  Output: {find_functions.stdout} — JSON list of qualified names

Step 2: extract_names (LLMStep, model=@fast)
  Prompt: "Extract all qualified_name values from this JSON.\n\
    Return one name per line, no extra text.\n\n{find_functions.stdout}"
  Output: {extract_names} — newline-separated list

Step 3: read_sources (ShellExecutor) — iterates top 5 functions
  # NOTE: LoopMaster doesn't have native loops. Use Parallel for top 5:
  Parallel(
    Step("read_1", executor=ShellExecutor(command=[
      sys.executable, BRIDGE, "get_snippet",
      "--qualified_name", "{extract_names_line_1}",
      "--project", CM_PROJECT
    ])),
    Step("read_2", ...),  # line 2
    Step("read_3", ...),  # line 3
    Step("read_4", ...),  # line 4
    Step("read_5", ...),  # line 5
  )

Step 4: generate_docs (LLMStep, model=@smart)
  Prompt: "Generate API documentation in markdown for these functions.\n\n\
    Function 1 ({name_1}):\n{read_1.stdout}\n\n\
    Function 2 ({name_2}):\n{read_2.stdout}\n\n\
    ...\n\n\
    Format: ## function_name\n### Signature\n### Description\n### Parameters\n### Returns\n### Example"
  Output: {generate_docs}

Step 5: write_docs (ShellExecutor)
  Command: echo '{generate_docs}' > {project_dir}/docs/api/{project_name}.md
  Output: {write_docs.stdout}

Step 6: store (ShellExecutor)
  Command: python {bridge} agentdb_store \
    --doc_id "docs-{project_name}-{timestamp}" \
    --domain "doc_generator" \
    --content "{generate_docs}" \
    --metadata '{"project":"{project_name}","functions_count":N,"status":"generated"}'
  Output: {store.stdout}
```

**Conditional (after Step 4):**
```python
Conditional(
    condition="os.path.exists('{project_dir}/docs/api/{project_name}.md')",
    then_steps=[diff_step],  # show diff with existing docs
    else_steps=[write_docs],  # create new file
)
```

**AgentDB Output Standard:**
- `doc_id`: `docs-{project}-{YYYY-MM-DD-HH-MM}`
- `domain`: `doc_generator`
- `metadata.tags`: `["documentation", "api", "auto-generated"]`
- `metadata.functions_count`: number of functions documented

**Search examples:**
```bash
# Find all doc generations for a project
agentdb_search --query "doc_generator my-project" --domain "doc_generator"

# Find what was documented
agentdb_search --query "generate_docs" --domain "doc_generator"
```

---

## Loop 3: changelog_gen (advanced — GitHub push)

**Problem:** Changelogs are manually written, often incomplete, and forget to link to versions/releases.

**Trigger:** User says "сгенерируй чэнджлог", "create changelog", "what changed since v1.2.0".

**Architecture (8 steps):**

```
Step 1: git_log (ShellExecutor)
  Command: cd {project_dir} && git log --oneline -50 --pretty=format:"%h|%s|%an|%ai"
  Output: {git_log.stdout} — pipe-delimited commit list

Step 2: git_tags (ShellExecutor)
  Command: cd {project_dir} && git tag --sort=-v:refname | head -5
  Output: {git_tags.stdout} — recent version tags

Step 3: git_diff_stat (ShellExecutor)
  Command: cd {project_dir} && git diff --stat {last_tag}..HEAD 2>/dev/null || \
    git diff --stat HEAD~20..HEAD
  Output: {git_diff_stat.stdout} — file change stats

Step 4: classify_commits (LLMStep, model=@fast)
  Prompt: "Classify these commits by type.\n\n\
    COMMITS:\n{git_log.stdout}\n\n\
    Return JSON: {\n\
      \"feat\": [\"commit messages\"],\n\
      \"fix\": [...],\n\
      \"chore\": [...],\n\
      \"docs\": [...],\n\
      \"refactor\": [...],\n\
      \"perf\": [...],\n\
      \"test\": [...]\n\
    }"
  Output: {classify_commits}

Step 5: determine_version (LLMStep, model=@fast)
  Prompt: "Based on these classified commits, determine the next semantic version.\n\n\
    Current tags: {git_tags.stdout}\n\
    Classified commits:\n{classify_commits}\n\n\
    Rules:\n\
    - feat → minor bump\n\
    - fix → patch bump\n\
    - BREAKING CHANGE → major bump\n\n\
    Return ONLY the new version string (e.g. '1.3.0')."
  Output: {determine_version}

Step 6: generate_changelog (LLMStep, model=@smart)
  Prompt: "Generate a Keep-a-Changelog entry for version {determine_version}.\n\n\
    Classified commits:\n{classify_commits}\n\n\
    Diff stats:\n{git_diff_stat.stdout}\n\n\
    Format:\n\
    ## [{determine_version}] - {today}\n\n\
    ### Added\n- ...\n\n\
    ### Changed\n- ...\n\n\
    ### Fixed\n- ...\n\n\
    ### Deprecated\n- ...\n\n\
    Only include non-empty sections."
  Output: {generate_changelog}

Step 7: push_to_github (ShellExecutor)
  # Requires GITHUB_TOKEN env var and gh CLI
  Command: cd {project_dir} && \
    gh release create v{determine_version} \
      --title "v{determine_version}" \
      --notes "{generate_changelog}" \
      --target main
  Output: {push_to_github.stdout}

Step 8: store (ShellExecutor)
  Command: python {bridge} agentdb_store \
    --doc_id "changelog-{project_name}-{determine_version}" \
    --domain "changelog_gen" \
    --content "{generate_changelog}" \
    --metadata '{"project":"{project_name}","version":"{determine_version}","release_url":"{push_to_github.stdout}","commits_count":N}'
  Output: {store.stdout}
```

**Conditional (after Step 5):**
```python
Conditional(
    condition="'major' in '{determine_version}'.lower() or int('{determine_version}'.split('.')[0]) > int('{last_major_version}')",
    then_steps=[breaking_changes_step],  # extra analysis for major bumps
    else_steps=[],  # proceed normally
)
```

**AgentDB Output Standard:**
- `doc_id`: `changelog-{project}-{v1.3.0}`
- `domain`: `changelog_gen`
- `metadata.tags`: `["changelog", "release", "version"]`
- `metadata.version`: `"1.3.0"`
- `metadata.release_url`: `"https://github.com/.../releases/tag/v1.3.0"`

**Search examples:**
```bash
# Find changelogs for a project
agentdb_search --query "changelog_gen my-project" --domain "changelog_gen"

# Find specific version
agentdb_search --query "changelog v1.3.0" --domain "changelog_gen"

# List all versions
agentdb_search --query "changelog" --domain "changelog_gen" --limit 20
```

**GitHub Requirements:**
- `gh` CLI installed and authenticated (`gh auth status`)
- `GITHUB_TOKEN` env var or `gh` auth login
- Repository must have releases enabled
- `last_tag` used as base for release notes

---

## Cross-Loop Consistency

| Aspect | security_audit | doc_generator | changelog_gen |
|--------|---------------|---------------|---------------|
| Bridge | `tests/mcp_tool.py` | `tests/mcp_tool.py` | `tests/mcp_tool.py` |
| AgentDB domain | `security_audit` | `doc_generator` | `changelog_gen` |
| doc_id pattern | `{loop}-{project}-{ts}` | `{loop}-{project}-{ts}` | `{loop}-{project}-{ver}` |
| LLM models | @fast (classify) + @smart (report) | @fast (extract) + @smart (generate) | @fast (classify+version) + @smart (changelog) |
| Shell tools | grep, pip-audit, aislop | codebase-memory | git, gh CLI |
| Conditional | critical → extra analysis | file exists → diff | major → breaking changes analysis |
| Output | markdown report | markdown docs | markdown changelog + GitHub release |
