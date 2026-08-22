"""PromptManager — system prompt injection via HTML comment markers.

Uses `<!-- LOOP_ENGINEER:start -->` / `<!-- LOOP_ENGINEER:end -->` markers
to inject loop instructions without overwriting the original prompt.
"""

from __future__ import annotations

import re


class PromptManager:
    """System prompt injection via HTML comment markers."""

    START_MARKER = "<!-- LOOP_ENGINEER:start -->"
    END_MARKER = "<!-- LOOP_ENGINEER:end -->"

    def inject(self, original_prompt: str, loop_instructions: str) -> str:
        """Inject loop instructions into prompt. Never overwrites existing content.

        If markers already exist, replaces the section between them.
        Otherwise, appends a new marked section at the end.
        """
        section = f"{self.START_MARKER}\n{loop_instructions}\n{self.END_MARKER}"

        if self.START_MARKER in original_prompt:
            pattern = re.compile(
                rf"\n*{re.escape(self.START_MARKER)}.*?{re.escape(self.END_MARKER)}\n*",
                re.DOTALL,
            )
            return pattern.sub(f"\n{section}\n", original_prompt)

        return f"{original_prompt}\n\n{section}\n"

    def restore_original(self, prompt: str) -> str:
        """Remove the injected section, restore original prompt."""
        pattern = re.compile(
            rf"\n*{re.escape(self.START_MARKER)}.*?{re.escape(self.END_MARKER)}\n*",
            re.DOTALL,
        )
        result = pattern.sub("", prompt)
        return result.strip() + "\n" if result.strip() else ""

    def has_markers(self, prompt: str) -> bool:
        """Check if prompt already has loop engineer markers."""
        return self.START_MARKER in prompt

    def extract_section(self, prompt: str) -> str | None:
        """Extract the content between markers, if present."""
        match = re.search(
            rf"{re.escape(self.START_MARKER)}\s*(.*?)\s*{re.escape(self.END_MARKER)}",
            prompt,
            re.DOTALL,
        )
        return match.group(1) if match else None
