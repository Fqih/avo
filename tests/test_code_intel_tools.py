"""Tests for code intelligence FunctionTools."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from avo.app_tools.file_tools import bind_workspace
from avo.app_tools.workspace import Workspace
from avo.code_intel.tools import (
    FindDefinitionsArguments,
    FindReferencesArguments,
    OutlineSymbolsArguments,
    find_definitions_tool,
    find_references_tool,
    outline_symbols_tool,
)

SAMPLE_CODE = """
class GeometryHelper:
    def area(self, r: float) -> float:
        return 3.14 * r * r

def calculate_shape() -> float:
    helper = GeometryHelper()
    return helper.area(2.0)
"""


async def test_outline_symbols_tool(tmp_path: Path) -> None:
    test_file = tmp_path / "geo.py"
    test_file.write_text(SAMPLE_CODE, encoding="utf-8")

    ws = Workspace(root=tmp_path)
    tool = outline_symbols_tool()

    with bind_workspace(ws):
        res: Any = await tool.invoke(OutlineSymbolsArguments(path="geo.py"))
        assert res["path"] == "geo.py"
        assert res["count"] >= 3
        names = [s["name"] for s in res["symbols"]]
        assert "GeometryHelper" in names
        assert "area" in names
        assert "calculate_shape" in names


async def test_find_definitions_tool(tmp_path: Path) -> None:
    test_file = tmp_path / "geo.py"
    test_file.write_text(SAMPLE_CODE, encoding="utf-8")

    ws = Workspace(root=tmp_path)
    tool = find_definitions_tool()

    with bind_workspace(ws):
        res: Any = await tool.invoke(FindDefinitionsArguments(symbol="GeometryHelper"))
        assert res["count"] == 1
        assert res["definitions"][0]["name"] == "GeometryHelper"
        assert res["definitions"][0]["kind"] == "class"


async def test_find_references_tool(tmp_path: Path) -> None:
    test_file = tmp_path / "geo.py"
    test_file.write_text(SAMPLE_CODE, encoding="utf-8")

    ws = Workspace(root=tmp_path)
    tool = find_references_tool()

    with bind_workspace(ws):
        res: Any = await tool.invoke(FindReferencesArguments(symbol="GeometryHelper"))
        assert res["count"] >= 2
        lines = [r["line"] for r in res["references"]]
        assert len(lines) >= 2
