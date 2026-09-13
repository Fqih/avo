"""``symbols`` tool: extract classes, methods, and functions using Python AST."""

from __future__ import annotations

import ast
import contextlib
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from avo import FunctionTool as PublicFunctionTool

from .edit_file import EditFileError
from .file_tools import _workspace_stack
from .workspace import Workspace


class SymbolsArguments(BaseModel):
    """Arguments for the ``symbols`` tool."""

    __test__ = False  # prevent pytest from collecting this model as a test suite

    path: str = Field(
        default=".",
        description="Workspace-relative file or directory to scan for code symbols",
    )
    symbol_name: str | None = Field(
        default=None,
        description="Optional filter: only match symbols whose name contains this substring",
    )
    max_symbols: int = Field(default=200, gt=0, le=2000)


def _current_symbols_workspace() -> Workspace:
    if not _workspace_stack:
        raise EditFileError(
            "symbols invoked without an active workspace; wrap the run in "
            "avo.app_tools.file_tools.bind_workspace(...)"
        )
    return _workspace_stack[-1]


def _format_arg(arg: ast.arg) -> str:
    """Format an ast.arg with optional type annotation."""
    name = arg.arg
    if arg.annotation is not None:
        try:
            return f"{name}: {ast.unparse(arg.annotation)}"
        except Exception:
            return name
    return name


def _format_signature(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    """Format parameter signature of a function."""
    args: list[str] = []
    for a in node.args.args:
        args.append(_format_arg(a))
    if node.args.vararg:
        args.append(f"*{_format_arg(node.args.vararg)}")
    for a in node.args.kwonlyargs:
        args.append(_format_arg(a))
    if node.args.kwarg:
        args.append(f"**{_format_arg(node.args.kwarg)}")

    sig = f"({', '.join(args)})"
    if node.returns:
        with contextlib.suppress(Exception):
            sig += f" -> {ast.unparse(node.returns)}"
    return sig


def _extract_symbols_from_ast(tree: ast.AST) -> list[dict[str, Any]]:
    """Extract top-level and class-level symbols from an AST tree."""
    symbols: list[dict[str, Any]] = []

    for node in tree.body if hasattr(tree, "body") else []:
        if isinstance(node, ast.ClassDef):
            bases: list[str] = []
            for b in node.bases:
                with contextlib.suppress(Exception):
                    bases.append(ast.unparse(b))
            doc = ast.get_docstring(node)
            first_line_doc = doc.splitlines()[0] if doc else None

            children: list[dict[str, Any]] = []
            for child in node.body:
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    c_doc = ast.get_docstring(child)
                    c_first_doc = c_doc.splitlines()[0] if c_doc else None
                    children.append(
                        {
                            "name": child.name,
                            "kind": "async_method"
                            if isinstance(child, ast.AsyncFunctionDef)
                            else "method",
                            "line_start": child.lineno,
                            "line_end": getattr(child, "end_lineno", child.lineno),
                            "signature": _format_signature(child),
                            "docstring": c_first_doc,
                        }
                    )

            symbols.append(
                {
                    "name": node.name,
                    "kind": "class",
                    "line_start": node.lineno,
                    "line_end": getattr(node, "end_lineno", node.lineno),
                    "bases": bases,
                    "docstring": first_line_doc,
                    "children": children,
                }
            )

        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            doc = ast.get_docstring(node)
            first_line_doc = doc.splitlines()[0] if doc else None
            symbols.append(
                {
                    "name": node.name,
                    "kind": "async_function"
                    if isinstance(node, ast.AsyncFunctionDef)
                    else "function",
                    "line_start": node.lineno,
                    "line_end": getattr(node, "end_lineno", node.lineno),
                    "signature": _format_signature(node),
                    "docstring": first_line_doc,
                }
            )

    return symbols


def _filter_symbols(
    symbols: list[dict[str, Any]],
    name_filter: str | None,
) -> list[dict[str, Any]]:
    if not name_filter:
        return symbols

    clean_filter = name_filter.lower()
    filtered: list[dict[str, Any]] = []
    for s in symbols:
        name_match = clean_filter in s["name"].lower()
        matched_children = [c for c in s.get("children", []) if clean_filter in c["name"].lower()]
        if name_match:
            filtered.append(s)
        elif matched_children:
            s_copy = dict(s)
            s_copy["children"] = matched_children
            filtered.append(s_copy)
    return filtered


def _scan_py_file(
    path: Path,
    workspace: Workspace,
    name_filter: str | None,
) -> dict[str, Any] | None:
    try:
        content = path.read_text(encoding="utf-8")
    except Exception:
        return None

    try:
        tree = ast.parse(content, filename=path.name)
    except SyntaxError as exc:
        return {
            "file": path.resolve().relative_to(workspace.root).as_posix(),
            "syntax_error": f"Line {exc.lineno}: {exc.msg}",
            "symbols": [],
        }
    except Exception as exc:
        return {
            "file": path.resolve().relative_to(workspace.root).as_posix(),
            "syntax_error": str(exc),
            "symbols": [],
        }

    raw_symbols = _extract_symbols_from_ast(tree)
    symbols = _filter_symbols(raw_symbols, name_filter)
    if not symbols and name_filter:
        return None

    return {
        "file": path.resolve().relative_to(workspace.root).as_posix(),
        "symbols": symbols,
    }


async def _symbols(arguments: SymbolsArguments) -> dict[str, Any]:
    workspace = _current_symbols_workspace()
    target = workspace.validate_path(arguments.path, must_exist=True)

    file_entries: list[dict[str, Any]] = []
    total_symbols = 0
    truncated = False

    if target.is_file():
        if target.suffix == ".py":
            entry = _scan_py_file(target, workspace, arguments.symbol_name)
            if entry:
                file_entries.append(entry)
                total_symbols += len(entry.get("symbols", []))
    elif target.is_dir():
        py_files = sorted(target.rglob("*.py"))
        for f in py_files:
            if not f.is_file():
                continue
            entry = _scan_py_file(f, workspace, arguments.symbol_name)
            if entry:
                file_entries.append(entry)
                total_symbols += len(entry.get("symbols", []))
                if total_symbols >= arguments.max_symbols:
                    truncated = True
                    break

    return {
        "target": arguments.path,
        "files_count": len(file_entries),
        "total_symbols": total_symbols,
        "truncated": truncated,
        "files": file_entries,
    }


def symbols_tool() -> PublicFunctionTool[SymbolsArguments]:
    """Return a :class:`FunctionTool` that extracts code symbols."""
    return PublicFunctionTool(
        name="symbols",
        description=(
            "Extract Python classes, methods, functions, line numbers, and signatures "
            "from a file or directory using AST parsing. Saves context tokens by avoiding "
            "reading entire files."
        ),
        arguments_model=SymbolsArguments,
        function=_symbols,
    )


__all__ = ["SymbolsArguments", "symbols_tool"]
