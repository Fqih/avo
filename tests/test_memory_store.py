"""Tests for native Epistemic Memory Store and BM25 fact recall."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from avo.memory.store import FactStore
from avo.memory.tools import recall_memory_tool, remember_tool


def test_fact_store_save_and_recall(tmp_path: Path) -> None:
    store_file = tmp_path / "memory.jsonl"
    store = FactStore(path=store_file)

    f1 = store.remember(
        "User prefers pytest over unittest and uses strict typing",
        category="user",
        tags=["python", "testing"],
    )
    assert f1.id is not None
    assert f1.category == "user"

    store.remember(
        "PostgreSQL database credentials are read from DATABASE_URL",
        category="project",
        tags=["db", "postgres"],
    )

    store.remember(
        "Frontend uses Tailwind CSS with dark theme enabled",
        category="project",
        tags=["frontend", "css"],
    )

    # Search for pytest preference
    results = store.recall("How does user want to write unit tests?", k=2)
    assert len(results) > 0
    assert "pytest" in results[0].content

    # Search for database
    db_results = store.recall("What database is used?", k=2)
    assert len(db_results) > 0
    assert "PostgreSQL" in db_results[0].content


def test_fact_store_delete_and_list(tmp_path: Path) -> None:
    store_file = tmp_path / "memory.jsonl"
    store = FactStore(path=store_file)

    f = store.remember("Temporary note to be forgotten")
    assert len(store.list_all()) == 1

    deleted = store.delete(f.id)
    assert deleted is True
    assert len(store.list_all()) == 0


@pytest.mark.asyncio
async def test_memory_function_tools(tmp_path: Path) -> None:
    store_file = tmp_path / "memory.jsonl"
    store = FactStore(path=store_file)

    rem_tool = remember_tool(store)
    rec_tool = recall_memory_tool(store)

    # Model saves memory
    save_res: Any = await rem_tool.invoke(
        {"fact": "Project uses FastAPI framework with async endpoints", "category": "project"}
    )
    assert save_res["status"] == "saved"
    fact_id = save_res["id"]
    assert fact_id is not None

    # Model recalls memory
    recall_res: Any = await rec_tool.invoke({"query": "framework endpoints", "limit": 2})
    assert recall_res["count"] == 1
    assert "FastAPI" in recall_res["results"][0]["content"]
