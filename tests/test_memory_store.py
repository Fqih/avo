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


def test_fact_store_malformed_lines_handling(tmp_path: Path) -> None:
    store_file = tmp_path / "memory.jsonl"
    line1 = (
        '{"id":"valid1","content":"Valid fact 1","category":"user",'
        '"source":"user","tags":[],"session_id":"s1","created_at":"2026-09-20T00:00:00Z"}'
    )
    line2 = "NOT VALID JSON!!! CORRUPTED LINE"
    line3 = (
        '{"id":"valid2","content":"Valid fact 2","category":"project",'
        '"source":"model","tags":[],"session_id":"s2","created_at":"2026-09-20T00:00:00Z"}'
    )
    store_file.write_text(f"{line1}\n{line2}\n{line3}\n", encoding="utf-8")

    store = FactStore(path=store_file)
    facts = store.list_all()
    assert len(facts) == 2
    assert facts[0].id == "valid1"
    assert facts[1].id == "valid2"


def test_factstore_redacts_credentials(tmp_path: Path) -> None:
    store = FactStore(tmp_path / "memory.jsonl")
    fact = store.remember(
        "API key is sk-1234567890abcdef12345678 and token is Bearer secret_tok_99999"
    )
    assert "sk-1234567890abcdef12345678" not in fact.content
    assert "secret_tok_99999" not in fact.content
    assert "[REDACTED]" in fact.content


def test_factstore_file_permissions_are_0600(tmp_path: Path) -> None:
    import stat

    file_path = tmp_path / "memory.jsonl"
    store = FactStore(file_path)
    store.remember("Important secret preference")
    mode = stat.S_IMODE(file_path.stat().st_mode)
    assert mode == 0o600


def test_factstore_clear_by_session_and_category(tmp_path: Path) -> None:
    store = FactStore(tmp_path / "memory.jsonl")
    store.remember("Fact 1", session_id="s1", category="user")
    store.remember("Fact 2", session_id="s1", category="project")
    store.remember("Fact 3", session_id="s2", category="user")

    # Clear s1 only
    count = store.clear(session_id="s1")
    assert count == 2
    assert len(store.list_all()) == 1
    assert store.list_all()[0].session_id == "s2"

    # Clear category user
    count2 = store.clear(category="user")
    assert count2 == 1
    assert len(store.list_all()) == 0


def test_factstore_concurrent_process_merge(tmp_path: Path) -> None:
    file_path = tmp_path / "memory.jsonl"
    store_a = FactStore(file_path)
    store_b = FactStore(file_path)

    store_a.remember("Fact A from process 1")
    store_b.remember("Fact B from process 2")
    store_a.remember("Fact C from process 1")

    # Reload store_c from disk to inspect final persisted state
    store_c = FactStore(file_path)
    contents = [f.content for f in store_c.list_all()]
    assert "Fact A from process 1" in contents
    assert "Fact B from process 2" in contents
    assert "Fact C from process 1" in contents

