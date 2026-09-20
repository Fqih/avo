"""Persistent fact memory store with BM25 keyword ranking."""

from __future__ import annotations

import contextlib
import logging
import math
import os
import re
import secrets
import threading
from collections.abc import Generator
from pathlib import Path

from .models import FactRecord

_LOG = logging.getLogger(__name__)
_TOKEN_RE = re.compile(r"[a-zA-Z0-9_]+")


def _tokenize(text: str) -> list[str]:
    """Tokenize text into lowercase keywords."""
    return [t.lower() for t in _TOKEN_RE.findall(text) if len(t) > 1]


@contextlib.contextmanager
def _file_lock(path: Path) -> Generator[None, None, None]:
    """Acquire an advisory process-level file lock for concurrent write safety."""
    lock_path = path.with_suffix(".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        import fcntl

        with open(lock_path, "a", encoding="utf-8") as f:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)
    except (ImportError, OSError):
        # Fallback on systems without fcntl
        yield


class FactStore:
    """Store and retrieve long-term learned facts using BM25 relevance."""

    def __init__(self, path: str | Path | None = None) -> None:
        if path is None:
            home = Path(os.environ.get("AVO_HOME", Path.home() / ".avo"))
            self.path = home / "memory.jsonl"
        else:
            self.path = Path(path)
        self._lock = threading.Lock()
        self._facts: list[FactRecord] = []
        self._load()

    def _load(self) -> None:
        """Load facts from disk if the file exists."""
        self._facts.clear()
        if not self.path.is_file():
            return
        with self._lock, _file_lock(self.path):
            try:
                lines = self.path.read_text(encoding="utf-8").splitlines()
            except OSError as exc:
                _LOG.warning("Failed to read memory file %s: %s", self.path, exc)
                return

            for lineno, line in enumerate(lines, start=1):
                clean = line.strip()
                if not clean:
                    continue
                try:
                    self._facts.append(FactRecord.model_validate_json(clean))
                except Exception as exc:
                    _LOG.warning(
                        "Malformed memory record in %s:%d: %s (skipping)",
                        self.path,
                        lineno,
                        exc,
                    )

    def _save(self) -> None:
        """Persist all facts to disk atomically using a temp file and file lock."""
        with self._lock, _file_lock(self.path):
            self.path.parent.mkdir(parents=True, exist_ok=True)
            lines = [f.model_dump_json() for f in self._facts]
            content = "\n".join(lines) + ("\n" if lines else "")
            temp_path = self.path.with_suffix(f".tmp.{secrets.token_hex(4)}")
            try:
                temp_path.write_text(content, encoding="utf-8")
                temp_path.replace(self.path)
            finally:
                if temp_path.exists():
                    with contextlib.suppress(OSError):
                        temp_path.unlink()

    def remember(
        self,
        content: str,
        *,
        session_id: str = "global",
        tags: list[str] | None = None,
        category: str = "project",
        source: str = "user",
    ) -> FactRecord:
        """Persist a new learned fact."""
        fact = FactRecord(
            content=content.strip(),
            category=category,
            source=source,
            tags=tags or [],
            session_id=session_id,
        )
        with self._lock:
            self._facts.append(fact)
        self._save()
        return fact

    def recall(self, query: str, *, k: int = 5) -> list[FactRecord]:
        """Retrieve top-k relevant facts using BM25 scoring."""
        query_terms = _tokenize(query)
        if not query_terms:
            return []

        with self._lock:
            facts_snapshot = list(self._facts)

        if not facts_snapshot:
            return []

        doc_tokens = [
            _tokenize(f"{f.content} {' '.join(f.tags)} {f.category}") for f in facts_snapshot
        ]
        doc_lengths = [len(dt) for dt in doc_tokens]
        total_len = sum(doc_lengths)
        num_docs = len(facts_snapshot)
        avg_doc_len = total_len / num_docs if num_docs > 0 else 1.0

        # Calculate document frequency
        df: dict[str, int] = {}
        for dt in doc_tokens:
            for term in set(dt):
                df[term] = df.get(term, 0) + 1

        k1 = 1.5
        b = 0.75
        scored: list[tuple[float, FactRecord]] = []

        for idx, fact in enumerate(facts_snapshot):
            dt = doc_tokens[idx]
            doc_len = doc_lengths[idx]
            score = 0.0

            for q in query_terms:
                raw_tf = dt.count(q)
                if raw_tf == 0:
                    continue

                tf = float(raw_tf)
                term_df = df.get(q, 0)
                idf = math.log((num_docs - term_df + 0.5) / (term_df + 0.5) + 1.0)

                # Boost matches in tags or category
                tag_tokens = _tokenize(" ".join(fact.tags))
                if q in tag_tokens:
                    tf *= 2.0

                num = tf * (k1 + 1)
                den = tf + k1 * (1 - b + b * (doc_len / avg_doc_len))
                score += idf * (num / den)

            if score > 0.0:
                scored.append((score, fact))

        scored.sort(key=lambda item: item[0], reverse=True)
        return [item[1] for item in scored[:k]]

    def delete(self, fact_id: str) -> bool:
        """Delete a fact by ID."""
        with self._lock:
            initial_len = len(self._facts)
            self._facts = [f for f in self._facts if f.id != fact_id]
            changed = len(self._facts) < initial_len
        if changed:
            self._save()
            return True
        return False

    def list_all(self, category: str | None = None) -> list[FactRecord]:
        """Return all stored facts, optionally filtered by category."""
        with self._lock:
            if category:
                return [f for f in self._facts if f.category == category]
            return list(self._facts)
