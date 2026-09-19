"""Data models for code intelligence symbols, definitions, and references."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class SymbolKind(StrEnum):
    """Categorized kind of programming symbol."""

    MODULE = "module"
    CLASS = "class"
    FUNCTION = "function"
    METHOD = "method"
    VARIABLE = "variable"
    IMPORT = "import"


@dataclass(frozen=True)
class SymbolInfo:
    """Metadata describing a declared code symbol."""

    name: str
    kind: SymbolKind
    line: int
    end_line: int
    character: int = 1
    container_name: str | None = None
    docstring: str | None = None


@dataclass(frozen=True)
class DefinitionLocation:
    """Target location where a symbol is declared."""

    path: str
    line: int
    character: int
    name: str
    kind: SymbolKind


@dataclass(frozen=True)
class ReferenceLocation:
    """Location where a symbol is referenced or called."""

    path: str
    line: int
    character: int
    line_text: str = ""
