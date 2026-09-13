"""REPL input history persistence, slash-command autocompletion, and draft recovery.

Provides:
- Persistent command-line history saved to ``.avo/history``.
- Auto-save draft capability saved to ``.avo/draft.txt``.
- GNU Readline / libedit tab completion for slash commands.
"""

from __future__ import annotations

import atexit
import contextlib
from collections.abc import Callable, Sequence
from pathlib import Path

_DEFAULT_HISTORY_FILE = "history"
_DEFAULT_DRAFT_FILE = "draft.txt"
_DEFAULT_MAX_ENTRIES = 1000


def make_slash_completer(commands: Sequence[str]) -> Callable[[str, int], str | None]:
    """Return a readline completer function for slash commands.

    Handles both cases where leading slash is retained or stripped by completer delims.
    """

    def completer(text: str, state: int) -> str | None:
        if text.startswith("/"):
            matches = [c for c in commands if c.startswith(text)]
        else:
            matches = [c[1:] for c in commands if c.startswith(f"/{text}")]
        if state < len(matches):
            return matches[state]
        return None

    return completer


class ReplHistoryManager:
    """Manages command history persistence, tab completion, and draft prompts."""

    def __init__(
        self,
        workspace_root: Path,
        *,
        history_file: Path | None = None,
        draft_file: Path | None = None,
        max_entries: int = _DEFAULT_MAX_ENTRIES,
    ) -> None:
        self.workspace_root = workspace_root
        dot_avo = workspace_root / ".avo"
        self.history_file = (
            history_file if history_file is not None else dot_avo / _DEFAULT_HISTORY_FILE
        )
        self.draft_file = draft_file if draft_file is not None else dot_avo / _DEFAULT_DRAFT_FILE
        self.max_entries = max(10, max_entries)
        self._entries: list[str] = []
        self._readline_configured = False

    def setup(self, commands: Sequence[str] | None = None) -> bool:
        """Initialize readline history, autocompletion, and register atexit hook.

        Returns True if readline was successfully configured, False otherwise.
        """
        try:
            import readline
        except ImportError:
            return False

        self.history_file.parent.mkdir(parents=True, exist_ok=True)

        # 1. Load history from file if exists
        if self.history_file.exists():
            with (
                contextlib.suppress(Exception),
                self.history_file.open("r", encoding="utf-8", errors="replace") as f,
            ):
                for line in f:
                    clean = line.strip()
                    if clean:
                        self._entries.append(clean)

        # 2. Configure readline limits and populate with workspace entries
        with contextlib.suppress(Exception):
            readline.clear_history()
            readline.set_history_length(self.max_entries)
            for entry in self._entries:
                readline.add_history(entry)

        # 3. Configure tab autocompletion if commands provided
        if commands:
            with contextlib.suppress(Exception):
                readline.set_completer(make_slash_completer(commands))
                delims = readline.get_completer_delims().replace("/", "")
                readline.set_completer_delims(delims)
                doc = getattr(readline, "__doc__", "") or ""
                if "libedit" in doc.lower():
                    readline.parse_and_bind("bind ^I rl_complete")
                else:
                    readline.parse_and_bind("tab: complete")

        # 4. Register atexit save
        if not self._readline_configured:
            atexit.register(self.save_history)
            self._readline_configured = True

        return True

    def append_history(self, entry: str) -> None:
        """Append an entry to in-memory list, readline buffer, and history file."""
        clean = entry.strip()
        if not clean:
            return

        # Avoid appending duplicate consecutive commands
        if self._entries and self._entries[-1] == clean:
            return

        self._entries.append(clean)
        if len(self._entries) > self.max_entries:
            self._entries = self._entries[-self.max_entries :]

        try:
            import readline

            with contextlib.suppress(Exception):
                readline.add_history(clean)
        except ImportError:
            pass

        # Write/append to history file
        self.history_file.parent.mkdir(parents=True, exist_ok=True)
        try:
            with self.history_file.open("a", encoding="utf-8") as f:
                f.write(f"{clean}\n")
        except OSError:
            pass

    def save_history(self) -> None:
        """Write current history buffer to file, truncating to max_entries."""
        self.history_file.parent.mkdir(parents=True, exist_ok=True)
        try:
            lines = self._entries[-self.max_entries :]
            with self.history_file.open("w", encoding="utf-8") as f:
                for line in lines:
                    f.write(f"{line}\n")
        except OSError:
            pass

    def list_history(self, limit: int = 50) -> list[str]:
        """Return the most recent history entries."""
        if self.history_file.exists() and not self._entries:
            with (
                contextlib.suppress(Exception),
                self.history_file.open("r", encoding="utf-8", errors="replace") as f,
            ):
                self._entries = [line.strip() for line in f if line.strip()]
        return self._entries[-limit:] if limit > 0 else []

    def save_draft(self, text: str) -> None:
        """Persist a draft prompt to the workspace draft file."""
        clean = text.strip()
        self.draft_file.parent.mkdir(parents=True, exist_ok=True)
        if clean:
            self.draft_file.write_text(clean + "\n", encoding="utf-8")
        else:
            self.clear_draft()

    def load_draft(self) -> str | None:
        """Return the current draft content if present and non-empty."""
        if not self.draft_file.exists():
            return None
        try:
            content = self.draft_file.read_text(encoding="utf-8").strip()
            return content if content else None
        except OSError:
            return None

    def clear_draft(self) -> bool:
        """Remove the current draft file. Returns True if a draft was cleared."""
        if self.draft_file.exists():
            try:
                self.draft_file.unlink()
                return True
            except OSError:
                return False
        return False


__all__ = [
    "ReplHistoryManager",
    "make_slash_completer",
]
