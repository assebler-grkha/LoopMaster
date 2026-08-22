# Audit: Windows-only os.startfile in CLI docs command

**Severity:** P3 — Low  
**File:** `cli/app.py`  
**Tags:** portability

## Problem

`docs` command uses `os.startfile` which is Windows-only. Fails on Linux/macOS.

## Impact

- `loop-engine docs` crashes on non-Windows platforms

## Fix Plan

1. Use platform-agnostic approach:
```python
import sys
import subprocess

if sys.platform == "win32":
    os.startfile(docs_path)
elif sys.platform == "darwin":
    subprocess.run(["open", str(docs_path)])
else:
    subprocess.run(["xdg-open", str(docs_path)])
```

2. Or use `webbrowser.open` for HTML docs:
```python
import webbrowser
webbrowser.open(docs_path.as_uri())
```

### Tests
- Docs command works on Windows
- Docs command works on Linux/macOS
---
## Status

**Status:** FIXED

**Commit:** batch fix (#07-#16)
