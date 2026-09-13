"""Session lifecycle for the chat REPL.

A *session* is a durable conversation thread. Each turn is appended to
:class:`avo.storage.conversations.ConversationStore` and ordered
by sequence number. The store is append-only, so a session can grow
indefinitely across REPL restarts.

This module is the thin layer the chat REPL talks to. It hides the
SQLite details behind small, REPL-friendly helpers (``open_session``,
``record_turn``, ``list_sessions``) and produces the one-screen
previews the ``/sessions`` picker needs.

Resume strategy
---------------

``AgentRuntime.run`` does not accept prior messages, so a resumed
session cannot be replayed verbatim into the next model call without
modifying the runtime. Instead, the lifecycle layer builds a *preamble*
from past turns and the chat REPL injects it as a system message on
the very next turn (then clears it). This keeps the runtime untouched
while still giving the model the relevant context to continue the
conversation.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from pydantic import JsonValue

from avo.storage.conversations import (
    ConversationStore,
    ConversationTurn,
    HistoryError,
)

SessionError = HistoryError


@dataclass(frozen=True)
class SessionInfo:
    """One row in the ``/sessions`` picker."""

    session_id: str
    turn_count: int
    last_activity: datetime
    first_user_preview: str
    last_user_preview: str

    @property
    def age_seconds(self) -> float:
        return (datetime.now(UTC) - self.last_activity).total_seconds()


class SessionLifecycle:
    """Append-only chat session tied to a SQLite-backed conversation store."""

    __slots__ = ("_store",)

    def __init__(self, store: ConversationStore) -> None:
        self._store = store

    @classmethod
    def open(cls, path: Path | str) -> SessionLifecycle:
        """Open a session backed by the given SQLite path.

        The chat REPL points this at the same file as
        :class:`SQLiteEventStore` so a single database file holds both
        the per-run events and the conversation thread.
        """

        return cls(ConversationStore(path))

    @property
    def store(self) -> ConversationStore:
        return self._store

    @property
    def path(self) -> Path:
        return self._store.path

    def close(self) -> None:
        self._store.close()

    def record_user_turn(
        self,
        session_id: str,
        content: str,
        *,
        metadata: dict[str, JsonValue] | None = None,
    ) -> ConversationTurn:
        """Append a user turn to ``session_id``."""

        return self._store.append(session_id, role="user", content=content, metadata=metadata)

    def record_assistant_turn(
        self,
        session_id: str,
        content: str,
        *,
        run_id: str,
        status: str,
        stop_reason: str,
        metadata: dict[str, JsonValue] | None = None,
    ) -> ConversationTurn:
        """Append an assistant turn with run-level metadata."""

        merged: dict[str, JsonValue] = dict(metadata or {})
        merged["run_id"] = run_id
        merged["status"] = status
        merged["stop_reason"] = stop_reason
        return self._store.append(session_id, role="assistant", content=content, metadata=merged)

    def turns(self, session_id: str) -> tuple[ConversationTurn, ...]:
        """Return the ordered turns for ``session_id``."""

        return self._store.turns(session_id)

    def last_turn(self, session_id: str) -> ConversationTurn | None:
        return self._store.last_turn(session_id)

    def session_exists(self, session_id: str) -> bool:
        return self._store.last_turn(session_id) is not None

    def list_sessions(self, *, limit: int | None = None) -> tuple[SessionInfo, ...]:
        """Return sessions ordered by most-recent activity.

        Each row carries just enough info for the ``/sessions`` picker
        to render a useful one-liner. The full turn history is fetched
        only when the operator actually picks a row.
        """

        infos: list[SessionInfo] = []
        for sid in self._store.sessions():
            turns = self._store.turns(sid)
            if not turns:
                continue
            infos.append(_summarise(sid, turns))
        infos.sort(key=lambda info: info.last_activity, reverse=True)
        if limit is not None:
            return tuple(infos[:limit])
        return tuple(infos)

    def find_resumable(
        self,
        *,
        max_age: timedelta = timedelta(hours=24),
        limit: int = 1,
    ) -> tuple[SessionInfo, ...]:
        """Return up to ``limit`` sessions whose last activity is within ``max_age``."""

        cutoff = datetime.now(UTC) - max_age
        recent = [info for info in self.list_sessions() if info.last_activity >= cutoff]
        return tuple(recent[:limit])

    def build_preamble(
        self,
        session_id: str,
        *,
        max_turns: int = 20,
        max_chars: int = 4000,
    ) -> str:
        """Render past turns as a system preamble for the next model call.

        Caps both the number of turns (newest first tail) and the total
        character count so very long sessions do not blow the context
        window. The preamble is a single user message, not a model
        replay — it tells the model "continue this conversation".
        """

        turns = self._store.turns(session_id)
        if not turns:
            raise SessionError(f"session {session_id!r} has no turns to resume")
        selected = list(turns[-max_turns:])
        lines: list[str] = [
            "You are continuing a previous conversation. The transcript "
            "(oldest first) is below. Continue naturally; do not greet "
            "the user as if this is the first message.",
            "",
        ]
        running = sum(len(line) for line in lines)
        for turn in selected:
            label = "User" if turn.role == "user" else "Assistant"
            snippet = _truncate(turn.content, max_chars - running)
            line = f"{label}: {snippet}"
            running += len(line) + 1
            lines.append(line)
            if running >= max_chars:
                lines.append("…(earlier turns truncated)")
                break
        return "\n".join(lines)

    def export_markdown(
        self,
        session_id: str,
        *,
        title: str | None = None,
    ) -> str:
        """Render the complete conversation for ``session_id`` as Markdown."""
        turns = self._store.turns(session_id)
        header_title = title or f"Avo Chat Session: {session_id}"
        lines: list[str] = [
            f"# {header_title}\n",
            f"- **Session ID:** `{session_id}`",
            f"- **Total Turns:** {len(turns)}",
        ]
        if turns:
            lines.append(
                f"- **Started At:** {turns[0].created_at.strftime('%Y-%m-%d %H:%M:%S UTC')}"
            )
            lines.append(
                f"- **Last Activity:** {turns[-1].created_at.strftime('%Y-%m-%d %H:%M:%S UTC')}"
            )
        lines.extend(["\n---\n"])

        for index, turn in enumerate(turns, start=1):
            role_emoji = "👤" if turn.role == "user" else "🤖"
            role_title = turn.role.capitalize()
            time_str = turn.created_at.strftime("%Y-%m-%d %H:%M:%S UTC")
            lines.append(f"### {role_emoji} {role_title} (Turn {index} • {time_str})\n")

            if turn.metadata:
                meta_items: list[str] = []
                if "run_id" in turn.metadata:
                    meta_items.append(f"Run ID: `{turn.metadata['run_id']}`")
                if "status" in turn.metadata:
                    meta_items.append(f"Status: `{turn.metadata['status']}`")
                if "stop_reason" in turn.metadata:
                    meta_items.append(f"Stop Reason: `{turn.metadata['stop_reason']}`")
                if meta_items:
                    lines.append(f"*{' • '.join(meta_items)}*\n")

            lines.append(turn.content)
            lines.append("\n---\n")

        return "\n".join(lines).strip() + "\n"


def _summarise(session_id: str, turns: Iterable[ConversationTurn]) -> SessionInfo:
    ordered = list(turns)
    user_turns = [t for t in ordered if t.role == "user"]
    first_user = user_turns[0].content if user_turns else ""
    last_user = user_turns[-1].content if user_turns else ""
    last_activity = ordered[-1].created_at
    return SessionInfo(
        session_id=session_id,
        turn_count=len(ordered),
        last_activity=last_activity,
        first_user_preview=first_user,
        last_user_preview=last_user,
    )


def _truncate(text: str, remaining: int) -> str:
    """Trim ``text`` so the running total stays under ``remaining`` chars."""

    if remaining <= 0:
        return ""
    if len(text) <= remaining:
        return text
    if remaining <= 1:
        return "…"
    return text[: remaining - 1] + "…"


def render_session_row(info: SessionInfo) -> str:
    """Render one session row for the ``/sessions`` picker."""

    age = _format_age(info.age_seconds)
    first = _truncate(info.first_user_preview.replace("\n", " "), 60)
    return f"[{info.session_id}]  {info.turn_count:>3} turns  {age:>10}  {first}"


def render_session_picker(infos: tuple[SessionInfo, ...]) -> str:
    """Render the full ``/sessions`` picker output."""

    if not infos:
        return "No previous sessions.\n"
    lines = ["Previous sessions (most recent first):", ""]
    for index, info in enumerate(infos, start=1):
        lines.append(f"  {index}. {render_session_row(info)}")
    lines.append("")
    lines.append("Pick a session: `/resume <id>` or `/resume <number>` (or `/new`).")
    return "\n".join(lines) + "\n"


def _format_age(seconds: float) -> str:
    if seconds < 0:
        return "now"
    if seconds < 60:
        return f"{int(seconds)}s ago"
    if seconds < 3600:
        return f"{int(seconds // 60)}m ago"
    if seconds < 86_400:
        return f"{int(seconds // 3600)}h ago"
    return f"{int(seconds // 86_400)}d ago"


def resolve_session_id(
    raw: str,
    sessions: tuple[SessionInfo, ...],
) -> str | None:
    """Resolve a ``/resume`` argument to a session id.

    Accepts:
      * the full session id,
      * a unique prefix (>= 4 chars),
      * a 1-based row number from ``/sessions``.

    Returns ``None`` when ``raw`` does not unambiguously match anything.
    """

    if not raw:
        return None

    if raw.isdigit():
        index = int(raw)
        if 1 <= index <= len(sessions):
            return sessions[index - 1].session_id
        return None

    by_id = {info.session_id: info for info in sessions}
    if raw in by_id:
        return raw

    matches = [sid for sid in by_id if sid.startswith(raw)]
    if len(matches) == 1 and len(raw) >= 4:
        return matches[0]
    return None


__all__ = [
    "SessionError",
    "SessionInfo",
    "SessionLifecycle",
    "render_session_picker",
    "render_session_row",
    "resolve_session_id",
]
