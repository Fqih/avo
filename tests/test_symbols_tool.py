"""Tests for the ``symbols`` code intelligence tool."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from avo.app_tools.file_tools import bind_workspace
from avo.app_tools.symbols import SymbolsArguments, symbols_tool
from avo.app_tools.workspace import Workspace


@pytest.fixture
def workspace_with_code(tmp_path: Path) -> Workspace:
    ws_dir = tmp_path / "ws"
    ws_dir.mkdir()

    calc_py = '''"""Calculator module."""

class Calculator:
    """A basic arithmetic calculator."""

    def __init__(self, initial: int = 0) -> None:
        self.value = initial

    def add(self, x: int, y: int) -> int:
        """Add two integers."""
        return x + y

    async def fetch_factor(self, key: str) -> float:
        return 1.5


def standalone_func(msg: str) -> str:
    """Return an uppercase message."""
    return msg.upper()


async def async_worker(task_id: int) -> bool:
    return True
'''
    (ws_dir / "calc.py").write_text(calc_py, encoding="utf-8")

    bad_py = "def broken(:\n    pass\n"
    (ws_dir / "bad.py").write_text(bad_py, encoding="utf-8")

    sub_dir = ws_dir / "sub"
    sub_dir.mkdir()
    sub_py = """class BaseService:
    pass

class AuthHandler(BaseService):
    def authenticate(self, token: str) -> bool:
        return True
"""
    (sub_dir / "service.py").write_text(sub_py, encoding="utf-8")

    return Workspace(ws_dir, create=False)


@pytest.mark.asyncio
async def test_symbols_extracts_classes_and_functions(workspace_with_code: Workspace) -> None:
    tool = symbols_tool()
    with bind_workspace(workspace_with_code):
        res: dict[str, Any] = await tool.invoke(SymbolsArguments(path="calc.py"))  # type: ignore[arg-type]

    assert res["files_count"] == 1
    file_entry = res["files"][0]
    assert file_entry["file"] == "calc.py"
    symbols = file_entry["symbols"]

    # Calculator class
    calc_sym = next(s for s in symbols if s["name"] == "Calculator")
    assert calc_sym["kind"] == "class"
    assert calc_sym["docstring"] == "A basic arithmetic calculator."
    methods = {m["name"]: m for m in calc_sym["children"]}
    assert "add" in methods
    assert methods["add"]["kind"] == "method"
    assert "(self, x: int, y: int) -> int" in methods["add"]["signature"]
    assert methods["fetch_factor"]["kind"] == "async_method"

    # Standalone function
    func_sym = next(s for s in symbols if s["name"] == "standalone_func")
    assert func_sym["kind"] == "function"
    assert "(msg: str) -> str" in func_sym["signature"]

    # Async function
    async_sym = next(s for s in symbols if s["name"] == "async_worker")
    assert async_sym["kind"] == "async_function"


@pytest.mark.asyncio
async def test_symbols_directory_scan_and_syntax_error(
    workspace_with_code: Workspace,
) -> None:
    tool = symbols_tool()
    with bind_workspace(workspace_with_code):
        res: dict[str, Any] = await tool.invoke(SymbolsArguments(path="."))  # type: ignore[arg-type]

    files = {f["file"]: f for f in res["files"]}
    assert "calc.py" in files
    assert "sub/service.py" in files
    assert "bad.py" in files
    assert "syntax_error" in files["bad.py"]

    service_syms = {s["name"]: s for s in files["sub/service.py"]["symbols"]}
    assert "AuthHandler" in service_syms
    assert "BaseService" in service_syms["AuthHandler"]["bases"]


@pytest.mark.asyncio
async def test_symbols_filter_by_name(workspace_with_code: Workspace) -> None:
    tool = symbols_tool()
    with bind_workspace(workspace_with_code):
        res: dict[str, Any] = await tool.invoke(
            SymbolsArguments(path=".", symbol_name="auth")  # type: ignore[arg-type]
        )

    files = [f["file"] for f in res["files"]]
    assert "sub/service.py" in files
    # calc.py should not match filter "auth"
    assert "calc.py" not in files


@pytest.mark.asyncio
async def test_symbols_path_traversal_rejection(workspace_with_code: Workspace) -> None:
    from avo.exceptions import ToolExecutionError

    tool = symbols_tool()
    with (
        bind_workspace(workspace_with_code),
        pytest.raises(ToolExecutionError) as exc_info,
    ):
        await tool.invoke(SymbolsArguments(path="../../etc/passwd"))  # type: ignore[arg-type]
    assert "WorkspacePathError" in str(exc_info.value)


@pytest.mark.asyncio
async def test_symbols_missing_workspace_raises() -> None:
    from avo.exceptions import ToolExecutionError

    tool = symbols_tool()
    with pytest.raises(ToolExecutionError) as exc_info:
        await tool.invoke(SymbolsArguments(path="calc.py"))  # type: ignore[arg-type]
    assert "EditFileError" in str(exc_info.value)
