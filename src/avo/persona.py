"""System persona and custom project instructions management.

Enables the autonomous agent to adapt its behavior to specific engineering
roles (coder, reviewer, architect, terse) and inherit workspace-specific
instructions from ``.avo/instructions.md``, ``.avo/system.md``, or
custom role templates in ``.avo/personas/*.md``.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Final

DEFAULT_SYSTEM_PROMPT: Final[str] = """Role: You are Avo, a reliable AI workspace agent.

Help the user understand, modify, test, and explain the current workspace.
Follow these operating rules:
- For greetings, thanks, and simple questions, answer directly and conversationally.
  Do not call tools for them.
- Use tools only when the request needs current files, command output, or a workspace change.
- Treat the active workspace as the file boundary. Use workspace-relative paths or absolute
  paths inside it; never invent paths such as /workspace or access a path outside the workspace.
- Choose an exact registered tool and valid arguments. Never claim a tool ran unless its result
  is available.
- Be concise, state what you did, and ask one focused question when the request is ambiguous.
Do not repeat this introduction on every turn; greet briefly only when the user greets or starts
a new conversation."""

BUILTIN_PERSONAS: Final[dict[str, str]] = {
    "coder": (
        "Role: Expert Software Engineer.\n"
        "Focus: Write clean, type-hinted, modular code. Prefer direct code and diffs over "
        "verbose explanations. Ensure comprehensive error handling."
    ),
    "reviewer": (
        "Role: Senior Code Reviewer & Security Auditor.\n"
        "Focus: Rigorously analyze edge cases, security vulnerabilities (OWASP), race conditions, "
        "backward compatibility, and performance. Categorize issues by severity (P0, P1, P2)."
    ),
    "architect": (
        "Role: Principal Systems Architect.\n"
        "Focus: Evaluate design trade-offs, scalability, module boundaries, data invariants, and "
        "interface contracts before implementation."
    ),
    "terse": (
        "Role: Minimalist Technical Assistant.\n"
        "Focus: Maximum information density. Output only diffs, code, and direct commands. "
        "No conversational filler or pleasantries."
    ),
}

_PERSONA_NAME_REGEX = re.compile(r"^[a-zA-Z0-9_\-]+$")


class PersonaManager:
    """Manages active persona, custom role templates, and workspace instructions."""

    __slots__ = ("_active_persona", "_custom_instructions", "_custom_personas", "_workspace_root")

    def __init__(
        self,
        workspace_root: Path | None = None,
        *,
        active_persona: str | None = None,
        custom_instructions: str | None = None,
    ) -> None:
        self._workspace_root = workspace_root
        self._active_persona = active_persona
        self._custom_instructions = custom_instructions
        self._custom_personas: dict[str, str] = {}
        self._load_workspace_personas()
        if self._custom_instructions is None:
            self._load_workspace_instructions()

    def _load_workspace_personas(self) -> None:
        global_personas_dir = Path.home() / ".avo" / "personas"
        search_dirs = [global_personas_dir]
        if self._workspace_root is not None:
            search_dirs.append(self._workspace_root / ".avo" / "personas")

        for personas_dir in search_dirs:
            if not personas_dir.is_dir():
                continue
            try:
                for item in personas_dir.glob("*.md"):
                    if item.is_file():
                        name = item.stem.lower()
                        try:
                            content = item.read_text(encoding="utf-8").strip()
                            if content:
                                self._custom_personas[name] = content
                        except OSError:
                            pass
            except OSError:
                pass

    def _load_workspace_instructions(self) -> None:
        env_prompt = os.environ.get("AVO_SYSTEM_PROMPT", "").strip()
        if env_prompt:
            self._custom_instructions = env_prompt
            return

        candidates: list[Path] = []
        if self._workspace_root is not None:
            candidates.extend(
                [
                    self._workspace_root / ".avo" / "instructions.md",
                    self._workspace_root / ".avo" / "system.md",
                ]
            )
        candidates.append(Path.home() / ".avo" / "instructions.md")

        for candidate in candidates:
            if candidate.is_file():
                try:
                    text = candidate.read_text(encoding="utf-8").strip()
                    if text:
                        self._custom_instructions = text
                        return
                except OSError:
                    pass

    @property
    def active_persona(self) -> str | None:
        return self._active_persona

    @property
    def custom_instructions(self) -> str | None:
        return self._custom_instructions

    def available_personas(self) -> dict[str, str]:
        """Return combined dictionary of builtin and workspace custom personas."""
        return {**BUILTIN_PERSONAS, **self._custom_personas}

    def register_persona(
        self,
        name: str,
        prompt: str,
        *,
        persist: bool = False,
    ) -> None:
        """Register a custom persona in memory or persist to workspace."""
        clean_name = name.strip().lower()
        if not clean_name or not _PERSONA_NAME_REGEX.match(clean_name):
            raise ValueError(
                f"Invalid persona name {name!r}; must contain only alphanumeric characters, "
                "hyphens, or underscores."
            )
        clean_prompt = prompt.strip()
        if not clean_prompt:
            raise ValueError("Persona prompt cannot be empty.")

        if persist and self._workspace_root is not None:
            from avo.workspace_write import save_workspace_file

            save_workspace_file(
                self._workspace_root, f".avo/personas/{clean_name}.md", clean_prompt
            )

        self._custom_personas[clean_name] = clean_prompt

    def set_persona(self, name: str | None) -> None:
        if name is not None:
            clean = name.lower()
            available = self.available_personas()
            if clean not in available:
                allowed = ", ".join(available.keys())
                raise ValueError(f"Unknown persona {name!r}. Available personas: {allowed}")
            self._active_persona = clean
        else:
            self._active_persona = None

    def set_custom_instructions(self, text: str | None) -> None:
        self._custom_instructions = text.strip() if text else None

    def render_system_prompt(self) -> str:
        """Combine the Avo core role, persona, and workspace instructions."""
        parts: list[str] = [DEFAULT_SYSTEM_PROMPT]
        if self._active_persona:
            available = self.available_personas()
            prompt = available.get(self._active_persona)
            if prompt:
                parts.append(prompt)
        if self._custom_instructions:
            parts.append(f"Workspace Instructions:\n{self._custom_instructions}")
        return "\n\n".join(parts)


__all__ = [
    "BUILTIN_PERSONAS",
    "DEFAULT_SYSTEM_PROMPT",
    "PersonaManager",
]
