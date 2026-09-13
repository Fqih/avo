"""Persistent conversation history.

A conversation is an ordered list of ``{role, content}`` turns keyed by
``session_id``. The store is append-only — turns are never mutated or
removed, which keeps the audit trail intact across resumes.

The store shares the SQLite file with :class:`SQLiteEventStore` so the
chat REPL can persist conversation history without managing two
connections.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from pydantic import JsonValue

from avo.exceptions import AvoError

HistoryError = AvoError

SCHEMA = """
CREATE TABLE IF NOT EXISTS conversation_turns (
    session_id TEXT NOT NULL,
    sequence INTEGER NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    PRIMARY KEY (session_id, sequence)
);
CREATE INDEX IF NOT EXISTS idx_conv_session_sequence
    ON conversation_turns(session_id, sequence);
"""

_ROLES = frozenset({"user", "assistant", "system", "tool"})


@dataclass(frozen=True)
class ConversationTurn:
    """One turn in a conversation."""

    session_id: str
    sequence: int
    role: str
    content: str
    metadata: dict[str, JsonValue]
    created_at: datetime

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "session_id": self.session_id,
            "sequence": self.sequence,
            "role": self.role,
            "content": self.content,
            "metadata": dict(self.metadata),
            "created_at": self.created_at.isoformat(),
        }


class ConversationStore:
    """SQLite-backed conversation history."""

    __slots__ = ("_closed", "_connection", "_path")

    def __init__(self, path: Path | str) -> None:
        self._path = Path(path)
        try:
            self._connection = sqlite3.connect(self._path, isolation_level=None)
            self._connection.row_factory = sqlite3.Row
            self._connection.executescript(SCHEMA)
        except sqlite3.Error as exc:
            raise HistoryError(f"Could not open conversation store at {self._path}: {exc}") from exc
        self._closed = False

    @property
    def path(self) -> Path:
        return self._path

    def close(self) -> None:
        if self._closed:
            return
        try:
            self._connection.close()
        finally:
            self._closed = True

    def _ensure_open(self) -> None:
        if self._closed:
            raise HistoryError(f"Conversation store at {self._path} is closed.")

    def append(
        self,
        session_id: str,
        *,
        role: str,
        content: str,
        metadata: dict[str, JsonValue] | None = None,
    ) -> ConversationTurn:
        if role not in _ROLES:
            raise HistoryError(f"invalid role: {role!r}")
        self._ensure_open()
        next_seq = self._next_sequence(session_id)
        turn = ConversationTurn(
            session_id=session_id,
            sequence=next_seq,
            role=role,
            content=content,
            metadata=metadata or {},
            created_at=datetime.now(UTC),
        )
        try:
            self._connection.execute(
                "INSERT INTO conversation_turns ("
                "session_id, sequence, role, content, metadata_json, created_at"
                ") VALUES (?, ?, ?, ?, ?, ?)",
                (
                    turn.session_id,
                    turn.sequence,
                    turn.role,
                    turn.content,
                    json.dumps(turn.metadata),
                    turn.created_at.isoformat(),
                ),
            )
        except sqlite3.Error as exc:
            raise HistoryError(f"failed to append turn: {exc}") from exc
        return turn

    def turns(self, session_id: str) -> tuple[ConversationTurn, ...]:
        self._ensure_open()
        try:
            rows = self._connection.execute(
                "SELECT session_id, sequence, role, content, metadata_json, created_at "
                "FROM conversation_turns WHERE session_id = ? ORDER BY sequence",
                (session_id,),
            ).fetchall()
        except sqlite3.Error as exc:
            raise HistoryError(f"failed to read turns: {exc}") from exc
        return tuple(self._row_to_turn(row) for row in rows)

    def sessions(self) -> tuple[str, ...]:
        self._ensure_open()
        try:
            rows = self._connection.execute(
                "SELECT session_id FROM conversation_turns "
                "GROUP BY session_id ORDER BY MAX(sequence)"
            ).fetchall()
        except sqlite3.Error as exc:
            raise HistoryError(f"failed to list sessions: {exc}") from exc
        return tuple(row["session_id"] for row in rows)

    def last_turn(self, session_id: str) -> ConversationTurn | None:
        self._ensure_open()
        try:
            row = self._connection.execute(
                "SELECT session_id, sequence, role, content, metadata_json, created_at "
                "FROM conversation_turns WHERE session_id = ? "
                "ORDER BY sequence DESC LIMIT 1",
                (session_id,),
            ).fetchone()
        except sqlite3.Error as exc:
            raise HistoryError(f"failed to read last turn: {exc}") from exc
        return self._row_to_turn(row) if row is not None else None

    def truncate(self, session_id: str) -> int:
        """Remove every turn for ``session_id``. Returns the row count deleted."""

        self._ensure_open()
        try:
            cursor = self._connection.execute(
                "DELETE FROM conversation_turns WHERE session_id = ?",
                (session_id,),
            )
        except sqlite3.Error as exc:
            raise HistoryError(f"failed to truncate session: {exc}") from exc
        return int(cursor.rowcount)

    def search_turns(
        self,
        query: str,
        *,
        session_id: str | None = None,
        limit: int = 50,
    ) -> tuple[ConversationTurn, ...]:
        """Search conversation turns across sessions or within a specific session."""
        self._ensure_open()
        pattern = f"%{query}%"
        try:
            if session_id is not None:
                rows = self._connection.execute(
                    "SELECT session_id, sequence, role, content, metadata_json, created_at "
                    "FROM conversation_turns "
                    "WHERE session_id = ? AND content LIKE ? "
                    "ORDER BY sequence DESC LIMIT ?",
                    (session_id, pattern, limit),
                ).fetchall()
            else:
                rows = self._connection.execute(
                    "SELECT session_id, sequence, role, content, metadata_json, created_at "
                    "FROM conversation_turns "
                    "WHERE content LIKE ? "
                    "ORDER BY created_at DESC LIMIT ?",
                    (pattern, limit),
                ).fetchall()
        except sqlite3.Error as exc:
            raise HistoryError(f"failed to search turns: {exc}") from exc
        return tuple(self._row_to_turn(row) for row in rows)

    def _next_sequence(self, session_id: str) -> int:
        try:
            row = self._connection.execute(
                "SELECT COALESCE(MAX(sequence), 0) AS max_seq "
                "FROM conversation_turns WHERE session_id = ?",
                (session_id,),
            ).fetchone()
        except sqlite3.Error as exc:
            raise HistoryError(f"failed to read sequence: {exc}") from exc
        return int(row["max_seq"]) + 1

    @staticmethod
    def _row_to_turn(row: sqlite3.Row) -> ConversationTurn:
        raw_meta = row["metadata_json"] or "{}"
        try:
            meta: dict[str, JsonValue] = json.loads(raw_meta)
        except json.JSONDecodeError:
            meta = {}
        return ConversationTurn(
            session_id=row["session_id"],
            sequence=row["sequence"],
            role=row["role"],
            content=row["content"],
            metadata=meta,
            created_at=datetime.fromisoformat(row["created_at"]),
        )


__all__ = [
    "ConversationStore",
    "ConversationTurn",
    "HistoryError",
]
