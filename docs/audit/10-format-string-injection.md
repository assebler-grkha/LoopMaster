# Audit: Format string injection in templates

**Severity:** P2 — Medium  
**File:** `templates/__init__.py`  
**Lines:** generate_code function  
**Tags:** security, injection

## Problem

`generate_code` uses `str.format()` with user-provided task/tool names. If user passes malicious input like `{__import__('os').system('rm -rf /')}`, it executes during format.

## Impact

- Arbitrary code execution via format string injection
- User-controlled template names can escape sandbox

## Fix Plan

1. Use safe formatting:
```python
# Instead of:
code = template.format(task=task_name, tool=tool_name)

# Use:
code = template.replace("{{task}}", task_name).replace("{{tool}}", tool_name)
```

2. Or use Template with safe substitution:
```python
from string import Template
code = Template(template).safe_substitute(task=task_name, tool=tool_name)
```

3. Validate/sanitize inputs:
```python
import re
if re.search(r'[{}]', task_name):
    raise ValueError(f"Invalid characters in task name: {task_name}")
```

### Tests
- Malicious task name → rejected or escaped
- Normal task name → works correctly
---
## Status

**Status:** FIXED

**Commit:** batch fix (#07-#16)
