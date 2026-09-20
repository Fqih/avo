"""Tests for blackboard FunctionTools."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from avo.blackboard.store import BlackboardStore
from avo.blackboard.tools import (
    BlackboardGetArguments,
    BlackboardListArguments,
    BlackboardSetArguments,
    blackboard_get_tool,
    blackboard_list_tool,
    blackboard_set_tool,
)


async def test_blackboard_tools_flow(tmp_path: Path) -> None:
    store = BlackboardStore(db_path=tmp_path / "bb.sqlite3")
    set_tool = blackboard_set_tool(store, author="researcher")
    get_tool = blackboard_get_tool(store)
    list_tool = blackboard_list_tool(store)

    # 1. Set item
    set_res: Any = await set_tool.invoke(
        BlackboardSetArguments(
            key="findings",
            value={"vuln_count": 0, "verified": True},
            namespace="security",
        )
    )
    assert set_res["status"] == "stored"
    assert set_res["version"] == 1
    assert set_res["author"] == "researcher"

    # 2. Get item
    get_res: Any = await get_tool.invoke(
        BlackboardGetArguments(
            key="findings",
            namespace="security",
        )
    )
    assert get_res["found"] is True
    assert get_res["value"]["vuln_count"] == 0

    # 3. List items
    list_res: Any = await list_tool.invoke(
        BlackboardListArguments(
            namespace="security",
        )
    )
    assert list_res["count"] == 1
    assert list_res["entries"][0]["key"] == "findings"
