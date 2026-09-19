"""Model-facing FunctionTools for code intelligence and AST analysis."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from avo import FunctionTool as PublicFunctionTool
from avo.app_tools.file_tools import _current_workspace

from .ast_analyzer import (
    extract_symbols,
    find_symbol_definitions,
    find_symbol_references,
)


class OutlineSymbolsArguments(BaseModel):
    """Arguments for outlining symbols in a file."""

    path: str = Field(min_length=1, description="Path to Python file in workspace")


class FindDefinitionsArguments(BaseModel):
    """Arguments for locating symbol definitions."""

    symbol: str = Field(min_length=1, description="Name of symbol to locate")
    path: str | None = Field(
        default=None,
        description="Optional path to target file; if omitted, scans workspace",
    )


class FindReferencesArguments(BaseModel):
    """Arguments for locating symbol references."""

    symbol: str = Field(min_length=1, description="Name of symbol to locate references for")
    path: str | None = Field(
        default=None,
        description="Optional path to target file; if omitted, scans workspace",
    )


async def _outline_symbols(arguments: OutlineSymbolsArguments) -> dict[str, Any]:
    workspace = _current_workspace()
    resolved = workspace.validate_path(arguments.path, must_exist=True)
    content = resolved.read_text(encoding="utf-8")
    symbols = extract_symbols(content, path=str(resolved.relative_to(workspace.root)))
    return {
        "path": str(resolved.relative_to(workspace.root)),
        "count": len(symbols),
        "symbols": [
            {
                "name": s.name,
                "kind": s.kind.value,
                "line": s.line,
                "end_line": s.end_line,
                "character": s.character,
                "container_name": s.container_name,
                "docstring": s.docstring,
            }
            for s in symbols
        ],
    }


async def _find_definitions(arguments: FindDefinitionsArguments) -> dict[str, Any]:
    workspace = _current_workspace()
    defs: list[dict[str, Any]] = []

    if arguments.path:
        target_files = [workspace.validate_path(arguments.path, must_exist=True)]
    else:
        target_files = [
            p for p in workspace.root.rglob("*.py") if p.is_file() and ".git" not in p.parts
        ]

    for file_path in target_files:
        try:
            content = file_path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        rel_path = str(file_path.relative_to(workspace.root))
        found = find_symbol_definitions(content, arguments.symbol, path=rel_path)
        for d in found:
            defs.append(
                {
                    "path": d.path,
                    "line": d.line,
                    "character": d.character,
                    "name": d.name,
                    "kind": d.kind.value,
                }
            )

    return {
        "symbol": arguments.symbol,
        "count": len(defs),
        "definitions": defs,
    }


async def _find_references(arguments: FindReferencesArguments) -> dict[str, Any]:
    workspace = _current_workspace()
    refs: list[dict[str, Any]] = []

    if arguments.path:
        target_files = [workspace.validate_path(arguments.path, must_exist=True)]
    else:
        target_files = [
            p for p in workspace.root.rglob("*.py") if p.is_file() and ".git" not in p.parts
        ]

    for file_path in target_files:
        try:
            content = file_path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        rel_path = str(file_path.relative_to(workspace.root))
        found = find_symbol_references(content, arguments.symbol, path=rel_path)
        for r in found:
            refs.append(
                {
                    "path": r.path,
                    "line": r.line,
                    "character": r.character,
                    "line_text": r.line_text,
                }
            )

    return {
        "symbol": arguments.symbol,
        "count": len(refs),
        "references": refs,
    }


def outline_symbols_tool() -> PublicFunctionTool[OutlineSymbolsArguments]:
    """Return tool for extracting symbols from a Python file."""
    return PublicFunctionTool(
        name="outline_symbols",
        description=(
            "Extract all classes, functions, and methods with line numbers from a Python file."
        ),
        arguments_model=OutlineSymbolsArguments,
        function=_outline_symbols,
    )


def find_definitions_tool() -> PublicFunctionTool[FindDefinitionsArguments]:
    """Return tool for finding definition sites of a symbol."""
    return PublicFunctionTool(
        name="find_definitions",
        description="Find definition locations of a symbol (class, function, method) in workspace.",
        arguments_model=FindDefinitionsArguments,
        function=_find_definitions,
    )


def find_references_tool() -> PublicFunctionTool[FindReferencesArguments]:
    """Return tool for finding reference and call sites of a symbol."""
    return PublicFunctionTool(
        name="find_references",
        description=(
            "Find all references, usages, and call sites of a symbol "
            "across Python files in workspace."
        ),
        arguments_model=FindReferencesArguments,
        function=_find_references,
    )
