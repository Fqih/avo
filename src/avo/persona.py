"""System persona and custom project instructions management.

Enables the autonomous agent to adapt its behavior to specific engineering
roles (coder, reviewer, architect, terse) and inherit workspace-specific
instructions from ``.avo/instructions.md`` or ``.avo/system.md``.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Final

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


class PersonaManager:
    """Manages active persona and workspace instructions."""

    __slots__ = ("_active_persona", "_custom_instructions", "_workspace_root")

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
        if self._custom_instructions is None:
            self._load_workspace_instructions()

    def _load_workspace_instructions(self) -> None:
        # Check env var first
        env_prompt = os.environ.get("AVO_SYSTEM_PROMPT", "").strip()
        if env_prompt:
            self._custom_instructions = env_prompt
            return

        if self._workspace_root is None:
            return

        # Check .avo/instructions.md or .avo/system.md
        candidates = [
            self._workspace_root / ".avo" / "instructions.md",
            self._workspace_root / ".avo" / "system.md",
        ]
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

    def set_persona(self, name: str | None) -> None:
        if name is not None and name.lower() not in BUILTIN_PERSONAS:
            allowed = ", ".join(BUILTIN_PERSONAS.keys())
            raise ValueError(f"Unknown persona {name!r}. Available personas: {allowed}")
        self._active_persona = name.lower() if name else None

    def set_custom_instructions(self, text: str | None) -> None:
        self._custom_instructions = text.strip() if text else None

    def render_system_prompt(self) -> str | None:
        """Combine active persona prompt and custom workspace instructions."""
        parts: list[str] = []
        if self._active_persona and self._active_persona in BUILTIN_PERSONAS:
            parts.append(BUILTIN_PERSONAS[self._active_persona])
        if self._custom_instructions:
            parts.append(f"Workspace Instructions:\n{self._custom_instructions}")
        if not parts:
            return None
        return "\n\n".join(parts)


__all__ = [
    "BUILTIN_PERSONAS",
    "PersonaManager",
]
