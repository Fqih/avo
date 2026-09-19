"""SQLite-backed multi-agent blackboard store."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .models import BlackboardEntry

_SCHEMA = """
CREATE TABLE IF NOT EXISTS blackboard_entries (
    namespace TEXT NOT NULL,
    key TEXT NOT NULL,
    value_json TEXT NOT NULL,
    author TEXT NOT NULL,
    version INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (namespace, key)
);

CREATE INDEX IF NOT EXISTS idx_blackboard_ns_key
    ON blackboard_entries(namespace, key);
"""


class BlackboardStore:
    """Shared typed memory blackboard backed by SQLite."""

    def __init__(self, db_path: str | Path = ":memory:") -> None:
        self.db_path = Path(db_path) if db_path != ":memory:" else ":memory:"
        self._conn = sqlite3.connect(
            str(self.db_path),
            check_same_thread=False,
            isolation_level=None,
        )
        self._conn.row_factory = sqlite3.Row
        self._init_db()

    def _init_db(self) -> None:
        with self._conn:
            self._conn.executescript(_SCHEMA)

    async def set(
        self,
        key: str,
        value: Any,
        namespace: str = "default",
        author: str = "anonymous",
    ) -> BlackboardEntry:
        """Insert or update a blackboard entry, bumping its version."""
        now = datetime.now(UTC)
        now_str = now.isoformat()
        val_json = json.dumps(value)

        with self._conn:
            cur = self._conn.execute(
                (
                    "SELECT version, created_at FROM blackboard_entries "
                    "WHERE namespace = ? AND key = ?"
                ),
                (namespace, key),
            )
            row = cur.fetchone()
            if row:
                version = int(row["version"]) + 1
                created_at_dt = datetime.fromisoformat(row["created_at"])
                self._conn.execute(
                    """
                    UPDATE blackboard_entries
                    SET value_json = ?, author = ?, version = ?, updated_at = ?
                    WHERE namespace = ? AND key = ?
                    """,
                    (val_json, author, version, now_str, namespace, key),
                )
            else:
                version = 1
                created_at_dt = now
                self._conn.execute(
                    """
                    INSERT INTO blackboard_entries
                    (namespace, key, value_json, author, version, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (namespace, key, val_json, author, version, now_str, now_str),
                )

        return BlackboardEntry(
            key=key,
            value=value,
            namespace=namespace,
            author=author,
            version=version,
            created_at=created_at_dt,
            updated_at=now,
        )

    async def get(self, key: str, namespace: str = "default") -> BlackboardEntry | None:
        """Retrieve an entry by key and namespace."""
        cur = self._conn.execute(
            """
            SELECT namespace, key, value_json, author, version, created_at, updated_at
            FROM blackboard_entries
            WHERE namespace = ? AND key = ?
            """,
            (namespace, key),
        )
        row = cur.fetchone()
        if not row:
            return None

        return BlackboardEntry(
            key=row["key"],
            value=json.loads(row["value_json"]),
            namespace=row["namespace"],
            author=row["author"],
            version=int(row["version"]),
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )

    async def list_entries(
        self,
        namespace: str = "default",
        prefix: str | None = None,
    ) -> list[BlackboardEntry]:
        """List entries under a namespace, optionally filtered by key prefix."""
        if prefix:
            cur = self._conn.execute(
                """
                SELECT namespace, key, value_json, author, version, created_at, updated_at
                FROM blackboard_entries
                WHERE namespace = ? AND key LIKE ?
                ORDER BY key ASC
                """,
                (namespace, f"{prefix}%"),
            )
        else:
            cur = self._conn.execute(
                """
                SELECT namespace, key, value_json, author, version, created_at, updated_at
                FROM blackboard_entries
                WHERE namespace = ?
                ORDER BY key ASC
                """,
                (namespace,),
            )

        entries: list[BlackboardEntry] = []
        for row in cur.fetchall():
            entries.append(
                BlackboardEntry(
                    key=row["key"],
                    value=json.loads(row["value_json"]),
                    namespace=row["namespace"],
                    author=row["author"],
                    version=int(row["version"]),
                    created_at=datetime.fromisoformat(row["created_at"]),
                    updated_at=datetime.fromisoformat(row["updated_at"]),
                )
            )
        return entries

    async def delete(self, key: str, namespace: str = "default") -> bool:
        """Delete an entry. Return True if deleted, False if not found."""
        with self._conn:
            cur = self._conn.execute(
                "DELETE FROM blackboard_entries WHERE namespace = ? AND key = ?",
                (namespace, key),
            )
            return bool(cur.rowcount > 0)

    async def clear(self, namespace: str | None = None) -> int:
        """Clear entries in a namespace, or all entries if namespace is None."""
        with self._conn:
            if namespace:
                cur = self._conn.execute(
                    "DELETE FROM blackboard_entries WHERE namespace = ?",
                    (namespace,),
                )
            else:
                cur = self._conn.execute("DELETE FROM blackboard_entries")
            return cur.rowcount

    def close(self) -> None:
        """Close SQLite database connection."""
        self._conn.close()
