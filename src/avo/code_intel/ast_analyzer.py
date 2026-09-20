"""Native Python AST-based symbol and reference analyzer."""

from __future__ import annotations

import ast

from .models import DefinitionLocation, ReferenceLocation, SymbolInfo, SymbolKind


def extract_symbols(code: str, path: str = "") -> list[SymbolInfo]:
    """Parse python source and extract all classes, functions, and methods."""
    del path
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return []

    symbols: list[SymbolInfo] = []

    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            symbols.append(
                SymbolInfo(
                    name=node.name,
                    kind=SymbolKind.CLASS,
                    line=node.lineno,
                    end_line=getattr(node, "end_lineno", node.lineno),
                    character=node.col_offset + 1,
                    docstring=ast.get_docstring(node),
                )
            )
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    symbols.append(
                        SymbolInfo(
                            name=item.name,
                            kind=SymbolKind.METHOD,
                            line=item.lineno,
                            end_line=getattr(item, "end_lineno", item.lineno),
                            character=item.col_offset + 1,
                            container_name=node.name,
                            docstring=ast.get_docstring(item),
                        )
                    )
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            symbols.append(
                SymbolInfo(
                    name=node.name,
                    kind=SymbolKind.FUNCTION,
                    line=node.lineno,
                    end_line=getattr(node, "end_lineno", node.lineno),
                    character=node.col_offset + 1,
                    docstring=ast.get_docstring(node),
                )
            )

    return symbols


def find_symbol_definitions(code: str, symbol: str, path: str = "") -> list[DefinitionLocation]:
    """Find all declaration sites for ``symbol`` in the source code."""
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return []

    defs: list[DefinitionLocation] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == symbol:
            defs.append(
                DefinitionLocation(
                    path=path,
                    line=node.lineno,
                    character=node.col_offset + 1,
                    name=node.name,
                    kind=SymbolKind.CLASS,
                )
            )
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == symbol:
            kind = SymbolKind.METHOD if getattr(node, "_is_method", False) else SymbolKind.FUNCTION
            defs.append(
                DefinitionLocation(
                    path=path,
                    line=node.lineno,
                    character=node.col_offset + 1,
                    name=node.name,
                    kind=kind,
                )
            )

    return defs


def find_symbol_references(code: str, symbol: str, path: str = "") -> list[ReferenceLocation]:
    """Find all usage and call sites of ``symbol`` in the source code."""
    lines = code.splitlines()
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return []

    refs: list[ReferenceLocation] = []

    for node in ast.walk(tree):
        is_def = (isinstance(node, ast.ClassDef) and node.name == symbol) or (
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == symbol
        )
        is_usage = (isinstance(node, ast.Name) and node.id == symbol) or (
            isinstance(node, ast.Attribute) and node.attr == symbol
        )
        lineno = getattr(node, "lineno", None)
        col = getattr(node, "col_offset", None)
        if (is_def or is_usage) and isinstance(lineno, int) and isinstance(col, int):
            line_idx = lineno - 1
            line_text = lines[line_idx].strip() if 0 <= line_idx < len(lines) else ""
            refs.append(
                ReferenceLocation(
                    path=path,
                    line=lineno,
                    character=col + 1,
                    line_text=line_text,
                )
            )

    # De-duplicate by (line, character)
    unique_refs: list[ReferenceLocation] = []
    seen: set[tuple[int, int]] = set()
    for ref in sorted(refs, key=lambda r: (r.line, r.character)):
        pos = (ref.line, ref.character)
        if pos not in seen:
            seen.add(pos)
            unique_refs.append(ref)

    return unique_refs
