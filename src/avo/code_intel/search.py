"""Hybrid code search engine for ranking symbols, docstrings, and functions."""

from __future__ import annotations

import contextlib
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from avo import FunctionTool as PublicFunctionTool
from avo.app_tools.file_tools import _current_workspace

from .ast_analyzer import extract_symbols
from .models import SymbolInfo, SymbolKind

_TOKEN_RE = re.compile(r"[a-zA-Z0-9_]+")


def _tokenize(text: str) -> list[str]:
    """Tokenize text into lowercase keywords, splitting snake_case and camelCase."""
    raw_tokens = _TOKEN_RE.findall(text)
    tokens: list[str] = []
    for tok in raw_tokens:
        # Split camelCase
        parts = re.findall(r"[A-Z]?[a-z0-9]+|[A-Z]+(?=[A-Z][a-z0-9]|\b)", tok)
        if parts:
            for p in parts:
                tokens.append(p.lower())
        else:
            tokens.append(tok.lower())
    return [t for t in tokens if len(t) > 1]


@dataclass(frozen=True)
class CodeSearchResult:
    """A scored code search result."""

    path: str
    name: str
    kind: str
    line: int
    container_name: str | None = None
    docstring: str | None = None
    score: float = 0.0


class CodeSearchEngine:
    """In-memory BM25-inspired search engine for workspace code symbols."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).resolve()
        self._docs: list[tuple[SymbolInfo, str, list[str]]] = []  # (symbol, rel_path, tokens)
        self._doc_lengths: list[int] = []
        self._avg_doc_len: float = 1.0
        self._df: dict[str, int] = {}  # document frequency

    def index(self) -> None:
        """Scan workspace and index all Python code symbols."""
        self._docs.clear()
        self._df.clear()
        total_len = 0

        target_files = [p for p in self.root.rglob("*.py") if p.is_file() and ".git" not in p.parts]

        for file_path in target_files:
            try:
                code = file_path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            rel_path = str(file_path.relative_to(self.root))
            symbols = extract_symbols(code, path=rel_path)

            for sym in symbols:
                text_corpus = f"{sym.name} {sym.container_name or ''} {sym.docstring or ''}"
                tokens = _tokenize(text_corpus)
                self._docs.append((sym, rel_path, tokens))
                doc_len = len(tokens)
                self._doc_lengths.append(doc_len)
                total_len += doc_len

                # Update document frequencies
                for term in set(tokens):
                    self._df[term] = self._df.get(term, 0) + 1

        doc_count = len(self._docs)
        self._avg_doc_len = total_len / doc_count if doc_count > 0 else 1.0

    def search(
        self,
        query: str,
        limit: int = 5,
        kind: SymbolKind | None = None,
    ) -> list[CodeSearchResult]:
        """Search indexed code symbols using BM25 relevance ranking."""
        query_terms = _tokenize(query)
        if not query_terms or not self._docs:
            return []

        num_docs = len(self._docs)
        scored: list[tuple[float, SymbolInfo, str]] = []

        k1 = 1.5
        b = 0.75

        for idx, (sym, rel_path, tokens) in enumerate(self._docs):
            if kind is not None and sym.kind != kind:
                continue

            doc_len = self._doc_lengths[idx]
            score = 0.0

            for q in query_terms:
                raw_tf = tokens.count(q)
                if raw_tf == 0:
                    continue

                tf = float(raw_tf)
                df = self._df.get(q, 0)
                # Smoothed IDF
                idf = math.log((num_docs - df + 0.5) / (df + 0.5) + 1.0)

                # Field boost: matches in symbol name get higher score
                name_tokens = _tokenize(sym.name)
                if q in name_tokens:
                    tf *= 2.5

                numerator = tf * (k1 + 1)
                denominator = tf + k1 * (1 - b + b * (doc_len / self._avg_doc_len))
                score += idf * (numerator / denominator)

            if score > 0.0:
                scored.append((score, sym, rel_path))

        scored.sort(key=lambda item: item[0], reverse=True)

        results: list[CodeSearchResult] = []
        for score, sym, rel_path in scored[:limit]:
            results.append(
                CodeSearchResult(
                    path=rel_path,
                    name=sym.name,
                    kind=sym.kind.value,
                    line=sym.line,
                    container_name=sym.container_name,
                    docstring=sym.docstring,
                    score=round(score, 3),
                )
            )

        return results


class CodeSearchArguments(BaseModel):
    """Arguments for searching code symbols in the workspace."""

    query: str = Field(min_length=1, description="Semantic search query or keywords")
    limit: int = Field(default=5, ge=1, le=50, description="Max results to return")
    kind: str | None = Field(
        default=None,
        description="Optional filter by kind (class, function, method)",
    )


async def _code_search(arguments: CodeSearchArguments) -> dict[str, Any]:
    workspace = _current_workspace()
    engine = CodeSearchEngine(root=workspace.root)
    engine.index()

    target_kind: SymbolKind | None = None
    if arguments.kind:
        with contextlib.suppress(ValueError):
            target_kind = SymbolKind(arguments.kind.lower())

    results = engine.search(arguments.query, limit=arguments.limit, kind=target_kind)

    return {
        "query": arguments.query,
        "count": len(results),
        "results": [
            {
                "path": r.path,
                "name": r.name,
                "kind": r.kind,
                "line": r.line,
                "container_name": r.container_name,
                "docstring": r.docstring,
                "score": r.score,
            }
            for r in results
        ],
    }


def code_search_tool() -> PublicFunctionTool[CodeSearchArguments]:
    """Return tool for searching workspace code symbols using BM25 relevance ranking."""
    return PublicFunctionTool(
        name="code_search",
        description=(
            "Search for functions, classes, and methods across workspace by natural language query."
        ),
        arguments_model=CodeSearchArguments,
        function=_code_search,
    )
