"""End-to-end tests for the built-in MCP servers.

Each server is spawned as a real subprocess via :class:`avo.mcp.MCPServer`,
the client performs the ``initialize`` handshake, lists tools, and calls
each one over real JSON-RPC stdio. No mocking of the transport.

The servers share one private stdio helper (``avo.mcp_servers._stdio``)
which is exercised by the first suite. The rest are smoke tests that
focus on:

* tool discovery matches the documented surface,
* happy-path tool calls return useful payloads,
* safety checks reject out-of-workspace paths and non-read-only SQL.
"""

from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from avo.mcp import MCPClient, MCPServer

PYTHON = sys.executable


def _spawn(module: str, *extra: str) -> MCPServer:
    """Build an MCPServer that launches ``python -m <module> <extra...>``."""

    return MCPServer(command=[PYTHON, "-m", module, *extra])


@pytest.mark.asyncio
async def test_server_that_dies_on_startup_fails_fast() -> None:
    """A crashed server must not leave requests waiting the full timeout.

    Regression: the read loop returned on stdout EOF without failing the
    pending futures, so ``_request`` sat in ``wait_for`` for its whole
    90s budget — on loaded CI runners this turned transient startup
    crashes into suite-wide timeouts.
    """

    import asyncio

    from avo.exceptions import ToolExecutionError

    server = MCPServer(command=[PYTHON, "-c", "import sys; sys.exit(3)"])
    try:
        with pytest.raises(ToolExecutionError):
            await asyncio.wait_for(server.start(), timeout=10.0)
    finally:
        await server.stop()


def _payload(call_result: dict[str, object]) -> dict[str, object]:
    """Decode the MCP ``content[0].text`` JSON envelope into a dict."""

    text = call_result.get("text")
    if not isinstance(text, str):
        raise AssertionError(f"call did not return text payload: {call_result!r}")
    return json.loads(text)


# ---------------------------------------------------------------------------
# _stdio helper — direct JSON-RPC dispatch
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_filesystem_lists_expected_tools_and_reads(tmp_path: Path) -> None:
    (tmp_path / "hi.txt").write_text("hello", encoding="utf-8")
    async with MCPClient(_spawn("avo.mcp_servers.filesystem", "--root", str(tmp_path))) as client:
        names = {t.metadata.name for t in client.tools()}
        assert names == {"read_file", "write_file", "list_directory", "search_files"}
        read = next(t for t in client.tools() if t.metadata.name == "read_file")
        result = await read.invoke({"path": "hi.txt"})
        assert result["is_error"] is False
        assert "hello" in result["text"]


# ---------------------------------------------------------------------------
# filesystem server
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_filesystem_read_write_list_search(tmp_path: Path) -> None:
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "a.txt").write_text("A", encoding="utf-8")
    (tmp_path / "sub" / "b.txt").write_text("B", encoding="utf-8")

    async with MCPClient(_spawn("avo.mcp_servers.filesystem", "--root", str(tmp_path))) as client:
        tools = {t.metadata.name: t for t in client.tools()}

        read = await tools["read_file"].invoke({"path": "sub/a.txt"})
        assert read["is_error"] is False
        assert _payload(read)["content"] == "A"

        write = await tools["write_file"].invoke(
            {"path": "new.txt", "content": "fresh", "create_parents": False}
        )
        assert write["is_error"] is False
        assert (tmp_path / "new.txt").read_text(encoding="utf-8") == "fresh"

        listing = await tools["list_directory"].invoke({"path": "sub"})
        assert listing["is_error"] is False
        names = {entry["name"] for entry in _payload(listing)["entries"]}
        assert names == {"a.txt", "b.txt"}

        search = await tools["search_files"].invoke({"pattern": "**/*.txt"})
        assert search["is_error"] is False
        matches = sorted(_payload(search)["matches"])
        assert matches == ["new.txt", "sub/a.txt", "sub/b.txt"]


@pytest.mark.asyncio
async def test_filesystem_rejects_path_escape(tmp_path: Path) -> None:
    async with MCPClient(_spawn("avo.mcp_servers.filesystem", "--root", str(tmp_path))) as client:
        tools = {t.metadata.name: t for t in client.tools()}
        bad = await tools["read_file"].invoke({"path": "../escape.txt"})
        assert bad["is_error"] is True
        assert "escapes workspace root" in bad["text"]


# ---------------------------------------------------------------------------
# sqlite server
# ---------------------------------------------------------------------------


def _seed_db(path: Path) -> None:
    with sqlite3.connect(path) as connection:
        connection.executescript(
            "CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT NOT NULL);"
            "INSERT INTO users (name) VALUES ('alice'), ('bob');"
        )


@pytest.mark.asyncio
async def test_sqlite_list_tables_describe_query(tmp_path: Path) -> None:
    db = tmp_path / "test.db"
    _seed_db(db)

    async with MCPClient(_spawn("avo.mcp_servers.sqlite", "--db", str(db))) as client:
        tools = {t.metadata.name: t for t in client.tools()}
        assert set(tools) == {"list_tables", "describe_table", "query"}

        listing = await tools["list_tables"].invoke({})
        assert listing["is_error"] is False
        tables = {t["name"]: t for t in _payload(listing)["tables"]}
        assert tables["users"]["row_count"] == 2

        describe = await tools["describe_table"].invoke({"table": "users"})
        assert describe["is_error"] is False
        cols = {col["name"]: col for col in _payload(describe)["columns"]}
        assert cols["name"]["not_null"] is True
        assert cols["id"]["primary_key"] is True

        rows = await tools["query"].invoke({"sql": "SELECT name FROM users ORDER BY name"})
        assert rows["is_error"] is False
        data = _payload(rows)["rows"]
        assert [row["name"] for row in data] == ["alice", "bob"]


@pytest.mark.asyncio
async def test_sqlite_refuses_writes(tmp_path: Path) -> None:
    db = tmp_path / "test.db"
    _seed_db(db)
    async with MCPClient(_spawn("avo.mcp_servers.sqlite", "--db", str(db))) as client:
        tools = {t.metadata.name: t for t in client.tools()}
        bad = await tools["query"].invoke({"sql": "DELETE FROM users"})
        assert bad["is_error"] is True
        assert "only SELECT" in bad["text"]


# ---------------------------------------------------------------------------
# git server
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_git_server_reports_status_and_log(tmp_path: Path) -> None:
    if subprocess.run(["git", "--version"], capture_output=True).returncode != 0:
        pytest.skip("git not installed")
    subprocess.run(["git", "init", "--quiet"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "Tester"], cwd=tmp_path, check=True)
    (tmp_path / "a.txt").write_text("hello", encoding="utf-8")
    subprocess.run(["git", "add", "a.txt"], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "commit", "--quiet", "--no-verify", "-m", "init"],
        cwd=tmp_path,
        check=True,
        env={
            "GIT_AUTHOR_NAME": "T",
            "GIT_AUTHOR_EMAIL": "t@e",
            "GIT_COMMITTER_NAME": "T",
            "GIT_COMMITTER_EMAIL": "t@e",
            "PATH": "/usr/bin:/bin",
        },
    )

    async with MCPClient(_spawn("avo.mcp_servers.git", "--cwd", str(tmp_path))) as client:
        tools = {t.metadata.name: t for t in client.tools()}
        assert set(tools) == {"git_status", "git_diff", "git_log", "git_show"}

        status = await tools["git_status"].invoke({})
        assert status["is_error"] is False

        log = await tools["git_log"].invoke({"max_commits": 5, "oneline": True})
        assert log["is_error"] is False
        assert "init" in log["text"]

        show = await tools["git_show"].invoke({"commit": "HEAD"})
        assert show["is_error"] is False
        assert "init" in show["text"]


@pytest.mark.asyncio
async def test_git_show_rejects_unsafe_commit_ref(tmp_path: Path) -> None:
    if subprocess.run(["git", "--version"], capture_output=True).returncode != 0:
        pytest.skip("git not installed")
    async with MCPClient(_spawn("avo.mcp_servers.git", "--cwd", str(tmp_path))) as client:
        tools = {t.metadata.name: t for t in client.tools()}
        bad = await tools["git_show"].invoke({"commit": "HEAD;rm -rf /"})
        assert bad["is_error"] is True
        assert "unsafe commit ref" in bad["text"]


# ---------------------------------------------------------------------------
# http_fetch server (offline — refuse to hit network in CI)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_http_fetch_rejects_non_http_scheme() -> None:
    async with MCPClient(_spawn("avo.mcp_servers.http_fetch")) as client:
        tools = {t.metadata.name: t for t in client.tools()}
        assert set(tools) == {"fetch_url"}
        bad = await tools["fetch_url"].invoke(
            {"url": "file:///etc/passwd", "max_bytes": 1024, "timeout": 1.0}
        )
        assert bad["is_error"] is True
        assert "unsupported URL scheme" in bad["text"]
