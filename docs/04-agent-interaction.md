# LoopMaster — Agent Interaction Architecture

## Overview

LoopMaster interacts with agent applications (OpenCode, Claude Code, Cursor, etc.) at the **configuration level** — reading and writing config files, system prompts, and other agent-specific files. This is NOT about interacting with the AI model; it's about safely modifying agent configuration.

## Adapter Pattern

### AgentAdapter Base Class

```python
class AgentAdapter(ABC):
    """Base class for agent-specific adapters."""

    @abstractmethod
    def discover(self) -> AgentInfo:
        """Discover if this agent is installed and where."""
        ...

    @abstractmethod
    def read_config(self) -> dict:
        """Read agent configuration files."""
        ...

    @abstractmethod
    def read_system_prompt(self) -> str:
        """Read the agent's system prompt."""
        ...

    @abstractmethod
    def write_config(self, config: dict) -> None:
        """Write agent configuration (with safety checks)."""
        ...

    @abstractmethod
    def inject_loop_context(self, loop_context: str) -> None:
        """Inject loop instructions into agent's system prompt."""
        ...

    @abstractmethod
    def validate_config(self) -> bool:
        """Validate that agent config is in expected state."""
        ...

    @abstractmethod
    def restore_original(self) -> None:
        """Restore all files to pre-modification state."""
        ...
```

### Concrete Adapters

| Agent | Adapter | Config Locations |
|---|---|---|
| OpenCode | `OpenCodeAdapter` | `~/.config/opencode/`, `opencode.json` |
| Claude Code | `ClaudeCodeAdapter` | `~/.claude/`, `.claude/settings.json` |
| Cursor | `CursorAdapter` | `.cursorrules`, `.cursor/` |
| Custom | `CustomAdapter` | User-specified paths |

### AgentRegistry

```python
class AgentRegistry:
    """Auto-discovers installed agents."""

    def discover_all(self) -> list[AgentInfo]:
        """Scan known locations for all supported agents."""
        ...

    def get_adapter(self, agent_type: str) -> AgentAdapter:
        """Get the appropriate adapter for an agent type."""
        ...
```

## Safe Configuration Modification

### ConfigManager

```python
class ConfigManager:
    """Safe config modification with snapshot and rollback."""

    def __init__(self, adapter: AgentAdapter):
        self.adapter = adapter
        self.snapshots: dict[str, bytes] = {}

    def snapshot_all(self) -> None:
        """Snapshot ALL agent files before any change."""
        for file_path in self.adapter.config_files:
            self.snapshots[file_path] = file_path.read_bytes()

    def atomic_write(self, file_path: Path, content: bytes) -> None:
        """Write to temp file, then atomic rename. Verify after write."""
        temp_path = file_path.with_suffix(".tmp")
        temp_path.write_bytes(content)
        temp_path.rename(file_path)
        # Verify
        if file_path.read_bytes() != content:
            self.rollback()
            raise ConfigError("Write verification failed")

    def rollback(self) -> None:
        """Restore all files from snapshots."""
        for file_path, content in self.snapshots.items():
            file_path.write_bytes(content)

    def dry_run_diff(self) -> dict[str, str]:
        """Show what would change without making changes."""
        ...
```

### Safety Guarantees

1. **Snapshot before change:** ALL agent files are snapshotted before any modification
2. **Atomic writes:** Write to temp file → rename → verify
3. **Rollback on failure:** Any error restores original state
4. **Dry-run preview:** Show diff before applying changes
5. **File locking:** Prevent concurrent modification

## Prompt Injection

### PromptManager

```python
class PromptManager:
    """System prompt injection via HTML comment markers."""

    START_MARKER = "<!-- LOOP_ENGINEER:start -->"
    END_MARKER = "<!-- LOOP_ENGINEER:end -->"

    def inject(self, original_prompt: str, loop_instructions: str) -> str:
        """Inject loop instructions into prompt. Never overwrites existing content."""
        if self.START_MARKER in original_prompt:
            # Replace existing section
            pattern = f"{self.START_MARKER}.*?{self.END_MARKER}"
            return re.sub(pattern, f"{self.START_MARKER}\n{loop_instructions}\n{self.END_MARKER}", original_prompt)
        else:
            # Append new section
            return f"{original_prompt}\n\n{self.START_MARKER}\n{loop_instructions}\n{self.END_MARKER}"

    def restore_original(self, prompt: str) -> str:
        """Remove the injected section, restore original prompt."""
        pattern = f"\n*{self.START_MARKER}.*?{self.END_MARKER}\n*"
        return re.sub(pattern, "", prompt)
```

### Key Principle

> Master NEVER overwrites agent files. It creates a temporary section in the prompt via markers, makes a snapshot before any change, and everything reverts on completion.

## Full Interaction Flow

```
1. loop-engine init --agent opencode
   ├── AgentRegistry.discover_all()
   ├── Find OpenCodeAdapter
   ├── adapter.discover() → get config paths
   └── Create project structure

2. loop-engine validate
   ├── Validate Python DSL (topology, variables, budget, models)
   └── adapter.validate_config() → check agent compatibility

3. loop-engine run --attach
   ├── ConfigManager.snapshot_all()
   ├── PromptManager.inject(loop_instructions)
   ├── Execute loop (runtime interpreter)
   │   ├── Steps execute, context flows
   │   ├── Cost tracking middleware
   │   ├── Metrics collected
   │   └── Checkpoints created
   └── On completion: PromptManager.restore_original()

4. loop-engine detach
   ├── PromptManager.restore_original()
   ├── ConfigManager.rollback() (safety net)
   └── Clean up temp files
```

## Usage from Python DSL

```python
@Loop(name="refactor", agent="opencode", budget="$2.00")
def refactor_loop(ctx):
    Step("analyze", tool="codebase_memory", prompt="Analyze module {module_name}")
    Step("execute", tool="opencode_subagent", prompt="Execute plan: {plan}")
    Step("verify", tool="aislop_scan", prompt="Verify changes")
```

The `agent="opencode"` parameter tells the engine which adapter to use for prompt injection and config management.
