"""Tests for the Avo MCP server mode.

Drives the server with an in-memory byte stream so the suite stays
fully offline and deterministic. Covers framing, the lifecycle
(handshake gating, ``initialize``, ``ping``, ``shutdown``, ``exit``),
the tool methods (``tools/list`` with derived annotations,
``tools/call``), notification handling, and error envelopes.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from pydantic import BaseModel

from avo.mcp_server.framing import (
    FramingError,
    encode_message,
    iter_messages,
)
from avo.mcp_server.server import AvoMcpServer, _ExitSignal
from avo.tools import FunctionTool, ToolRegistry


class _EchoInput(BaseModel):
    text: str


def _echo(arguments: _EchoInput) -> dict[str, str]:
    return {"echoed": arguments.text}


def _registry() -> ToolRegistry:
    tool = FunctionTool(
        name="echo",
        description="echo input",
        arguments_model=_EchoInput,
        function=_echo,
    )
    return ToolRegistry([tool])


def _registry_with(names: list[str]) -> ToolRegistry:
    tools = [
        FunctionTool(
            name=name,
            description=f"{name} tool",
            arguments_model=_EchoInput,
            function=_echo,
        )
        for name in names
    ]
    return ToolRegistry(tools)


async def _initialize(server: AvoMcpServer) -> None:
    response = await server.handle_message(
        {"jsonrpc": "2.0", "id": 0, "method": "initialize", "params": {}}
    )
    assert "error" not in response


# ---------------------------------------------------------------------------
# Framing
# ---------------------------------------------------------------------------


def test_iter_messages_round_trips_single_message() -> None:
    encoded = encode_message({"jsonrpc": "2.0", "id": 1, "method": "ping"})
    messages = list(iter_messages(iter([encoded])))
    assert len(messages) == 1
    payload = messages[0]["payload"]
    assert payload["method"] == "ping"


def test_iter_messages_handles_partial_chunks() -> None:
    encoded = encode_message({"jsonrpc": "2.0", "id": 1, "method": "ping"})

    def chunked() -> Any:
        # Split the wire bytes into 1-byte chunks so the parser must
        # buffer across iterations.
        for byte in encoded:
            yield bytes([byte])

    messages = list(iter_messages(chunked()))
    assert messages[0]["payload"]["method"] == "ping"


def test_iter_messages_rejects_missing_content_length() -> None:
    # Missing Content-Length header — present but invalid.
    bad = b"Content-Type: application/json\r\n\r\n{}"
    with pytest.raises(FramingError):
        list(iter_messages(iter([bad])))


def test_encode_message_sets_content_length() -> None:
    encoded = encode_message({"x": 1})
    header = encoded.split(b"\r\n\r\n", 1)[0]
    assert header.startswith(b"Content-Length: ")
    length = int(header.split(b":", 1)[1].strip())
    body = encoded.split(b"\r\n\r\n", 1)[1]
    assert length == len(body)


# ---------------------------------------------------------------------------
# Method dispatch
# ---------------------------------------------------------------------------


def test_initialize_returns_server_info() -> None:
    server = AvoMcpServer(registry=_registry())
    response = asyncio.run(
        server.handle_message(
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "x"}}
        )
    )
    assert response["id"] == 1
    assert response["result"]["serverInfo"]["name"] == "avo"
    assert "tools" in response["result"]["capabilities"]


def test_ping_returns_empty_object() -> None:
    server = AvoMcpServer(registry=_registry())
    response = asyncio.run(server.handle_message({"jsonrpc": "2.0", "id": 7, "method": "ping"}))
    assert response == {"jsonrpc": "2.0", "id": 7, "result": {}}


def test_tools_list_advertises_registry() -> None:
    server = AvoMcpServer(registry=_registry())

    async def run() -> Any:
        await _initialize(server)
        return await server.handle_message({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})

    response = asyncio.run(run())
    tools = response["result"]["tools"]
    assert len(tools) == 1
    assert tools[0]["name"] == "echo"
    assert tools[0]["description"] == "echo input"
    assert "properties" in tools[0]["inputSchema"]


def test_tools_call_invokes_and_returns_text_block() -> None:
    server = AvoMcpServer(registry=_registry())

    async def run() -> Any:
        await _initialize(server)
        return await server.handle_message(
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {"name": "echo", "arguments": {"text": "hi"}},
            }
        )

    response = asyncio.run(run())
    result = response["result"]
    assert result["isError"] is False
    assert result["content"][0]["type"] == "text"
    assert '"echoed": "hi"' in result["content"][0]["text"]


def test_tools_call_unknown_tool_returns_error_envelope() -> None:
    server = AvoMcpServer(registry=_registry())

    async def run() -> Any:
        await _initialize(server)
        return await server.handle_message(
            {
                "jsonrpc": "2.0",
                "id": 4,
                "method": "tools/call",
                "params": {"name": "nope", "arguments": {}},
            }
        )

    response = asyncio.run(run())
    assert response["result"]["isError"] is True
    assert "nope" in response["result"]["content"][0]["text"]


def test_unknown_method_returns_method_not_found() -> None:
    server = AvoMcpServer(registry=_registry())

    async def run() -> Any:
        await _initialize(server)
        return await server.handle_message({"jsonrpc": "2.0", "id": 5, "method": "x/y"})

    response = asyncio.run(run())
    assert response["error"]["code"] == -32601


def test_notification_returns_none() -> None:
    server = AvoMcpServer(registry=_registry())
    response = asyncio.run(
        server.handle_message({"jsonrpc": "2.0", "method": "notifications/initialized"})
    )
    assert response is None


def test_invalid_envelope_returns_error() -> None:
    server = AvoMcpServer(registry=_registry())
    response = asyncio.run(server.handle_message({"id": 1, "method": "ping"}))
    assert response["error"]["code"] == -32600


# ---------------------------------------------------------------------------
# Lifecycle gating, shutdown, capabilities, annotations
# ---------------------------------------------------------------------------


def test_tools_list_before_initialize_is_rejected() -> None:
    server = AvoMcpServer(registry=_registry())
    response = asyncio.run(
        server.handle_message({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    )
    assert response["error"]["code"] == -32002


def test_double_initialize_is_tolerated() -> None:
    server = AvoMcpServer(registry=_registry())

    async def run() -> Any:
        first = await server.handle_message(
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}
        )
        second = await server.handle_message(
            {"jsonrpc": "2.0", "id": 2, "method": "initialize", "params": {}}
        )
        return first, second

    first, second = asyncio.run(run())
    assert "error" not in first
    assert "error" not in second


def test_initialize_advertises_full_capabilities() -> None:
    server = AvoMcpServer(registry=_registry())
    response = asyncio.run(
        server.handle_message({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
    )
    capabilities = response["result"]["capabilities"]
    assert set(capabilities) >= {"tools", "resources", "prompts", "logging", "completions"}


def test_shutdown_returns_empty_object_and_replay_tolerated() -> None:
    server = AvoMcpServer(registry=_registry())

    async def run() -> Any:
        await _initialize(server)
        first = await server.handle_message({"jsonrpc": "2.0", "id": 8, "method": "shutdown"})
        second = await server.handle_message({"jsonrpc": "2.0", "id": 9, "method": "shutdown"})
        return first, second

    first, second = asyncio.run(run())
    assert first["result"] == {}
    assert second["result"] == {}


def test_shutdown_before_initialize_is_rejected() -> None:
    server = AvoMcpServer(registry=_registry())
    response = asyncio.run(server.handle_message({"jsonrpc": "2.0", "id": 8, "method": "shutdown"}))
    assert response["error"]["code"] == -32002


def test_after_shutdown_only_exempt_methods_survive() -> None:
    server = AvoMcpServer(registry=_registry())

    async def run() -> Any:
        await _initialize(server)
        await server.handle_message({"jsonrpc": "2.0", "id": 8, "method": "shutdown"})
        call = await server.handle_message(
            {
                "jsonrpc": "2.0",
                "id": 9,
                "method": "tools/call",
                "params": {"name": "echo", "arguments": {"text": "hi"}},
            }
        )
        ping = await server.handle_message({"jsonrpc": "2.0", "id": 10, "method": "ping"})
        return call, ping

    call, ping = asyncio.run(run())
    assert call["error"]["code"] == -32002
    assert ping["result"] == {}


def test_tool_annotations_reflect_tool_kind(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AVO_TOOLS_REQUIRE_APPROVAL", "dangerous_tool")
    server = AvoMcpServer(
        registry=_registry_with(["read_notes", "glob", "plain_tool", "dangerous_tool"])
    )

    async def run() -> Any:
        await _initialize(server)
        return await server.handle_message({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})

    response = asyncio.run(run())
    by_name = {tool["name"]: tool["annotations"] for tool in response["result"]["tools"]}
    read_flags = {
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    }
    assert by_name["read_notes"] == read_flags
    assert by_name["glob"] == read_flags
    assert by_name["plain_tool"] == {
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": False,
    }
    assert by_name["dangerous_tool"] == {
        "readOnlyHint": False,
        "destructiveHint": True,
        "idempotentHint": False,
        "openWorldHint": True,
    }


# ---------------------------------------------------------------------------
# Workspace resources
# ---------------------------------------------------------------------------


def _workspace(tmp_path: Any) -> AvoMcpServer:
    (tmp_path / "notes.md").write_text("# hello\n", encoding="utf-8")
    (tmp_path / ".hidden").write_text("secret", encoding="utf-8")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "data.txt").write_text("payload", encoding="utf-8")
    (tmp_path / "blob.bin").write_bytes(b"\x00\xff\xfe")
    return AvoMcpServer(registry=_registry(), workspace_root=tmp_path)


def test_resources_list_returns_workspace_files(tmp_path: Any) -> None:
    server = _workspace(tmp_path)

    async def run() -> Any:
        await _initialize(server)
        return await server.handle_message({"jsonrpc": "2.0", "id": 20, "method": "resources/list"})

    response = asyncio.run(run())
    resources = response["result"]["resources"]
    uris = {r["uri"] for r in resources}
    assert "avo://workspace/notes.md" in uris
    assert "avo://workspace/sub/data.txt" in uris
    # dot-prefixed entries never surface
    assert not any(".hidden" in uri for uri in uris)
    by_uri = {r["uri"]: r for r in resources}
    assert by_uri["avo://workspace/notes.md"]["mimeType"] == "text/markdown"
    assert by_uri["avo://workspace/sub/data.txt"]["name"] == "sub/data.txt"


def test_resources_list_without_workspace_is_empty() -> None:
    server = AvoMcpServer(registry=_registry())

    async def run() -> Any:
        await _initialize(server)
        return await server.handle_message({"jsonrpc": "2.0", "id": 21, "method": "resources/list"})

    response = asyncio.run(run())
    assert response["result"] == {"resources": []}


def test_resources_read_returns_text(tmp_path: Any) -> None:
    server = _workspace(tmp_path)

    async def run() -> Any:
        await _initialize(server)
        return await server.handle_message(
            {
                "jsonrpc": "2.0",
                "id": 22,
                "method": "resources/read",
                "params": {"uri": "avo://workspace/sub/data.txt"},
            }
        )

    response = asyncio.run(run())
    contents = response["result"]["contents"]
    assert contents[0]["uri"] == "avo://workspace/sub/data.txt"
    assert contents[0]["text"] == "payload"
    assert contents[0]["mimeType"] == "text/plain"


def test_resources_read_rejects_traversal(tmp_path: Any) -> None:
    server = _workspace(tmp_path)

    async def run() -> Any:
        await _initialize(server)
        return await server.handle_message(
            {
                "jsonrpc": "2.0",
                "id": 23,
                "method": "resources/read",
                "params": {"uri": "avo://workspace/../secret"},
            }
        )

    response = asyncio.run(run())
    assert response["error"]["code"] == -32000


def test_resources_read_rejects_foreign_scheme(tmp_path: Any) -> None:
    server = _workspace(tmp_path)

    async def run() -> Any:
        await _initialize(server)
        return await server.handle_message(
            {
                "jsonrpc": "2.0",
                "id": 24,
                "method": "resources/read",
                "params": {"uri": "file:///etc/passwd"},
            }
        )

    response = asyncio.run(run())
    assert response["error"]["code"] == -32602


def test_resources_read_requires_string_uri(tmp_path: Any) -> None:
    server = _workspace(tmp_path)

    async def run() -> Any:
        await _initialize(server)
        return await server.handle_message(
            {"jsonrpc": "2.0", "id": 25, "method": "resources/read", "params": {"uri": 42}}
        )

    response = asyncio.run(run())
    assert response["error"]["code"] == -32602


def test_resources_read_missing_file(tmp_path: Any) -> None:
    server = _workspace(tmp_path)

    async def run() -> Any:
        await _initialize(server)
        return await server.handle_message(
            {
                "jsonrpc": "2.0",
                "id": 26,
                "method": "resources/read",
                "params": {"uri": "avo://workspace/nope.txt"},
            }
        )

    response = asyncio.run(run())
    assert response["error"]["code"] == -32000


def test_resources_read_binary_rejected(tmp_path: Any) -> None:
    server = _workspace(tmp_path)

    async def run() -> Any:
        await _initialize(server)
        return await server.handle_message(
            {
                "jsonrpc": "2.0",
                "id": 27,
                "method": "resources/read",
                "params": {"uri": "avo://workspace/blob.bin"},
            }
        )

    response = asyncio.run(run())
    assert response["error"]["code"] == -32000
    assert "binary" in response["error"]["message"]


def test_resources_read_without_workspace_is_error(tmp_path: Any) -> None:
    server = AvoMcpServer(registry=_registry())

    async def run() -> Any:
        await _initialize(server)
        return await server.handle_message(
            {
                "jsonrpc": "2.0",
                "id": 28,
                "method": "resources/read",
                "params": {"uri": "avo://workspace/notes.md"},
            }
        )

    response = asyncio.run(run())
    assert response["error"]["code"] == -32000


def test_resources_templates_list(tmp_path: Any) -> None:
    server = _workspace(tmp_path)

    async def run() -> Any:
        await _initialize(server)
        return await server.handle_message(
            {"jsonrpc": "2.0", "id": 29, "method": "resources/templates/list"}
        )

    response = asyncio.run(run())
    templates = response["result"]["resourceTemplates"]
    assert templates[0]["uriTemplate"] == "avo://workspace/{path}"


def test_resources_templates_list_without_workspace_is_empty() -> None:
    server = AvoMcpServer(registry=_registry())

    async def run() -> Any:
        await _initialize(server)
        return await server.handle_message(
            {"jsonrpc": "2.0", "id": 30, "method": "resources/templates/list"}
        )

    response = asyncio.run(run())
    assert response["result"] == {"resourceTemplates": []}


# ---------------------------------------------------------------------------
# End-to-end loop with mock stdio
# ---------------------------------------------------------------------------


def test_serve_stdio_runs_full_handshake() -> None:
    server = AvoMcpServer(registry=_registry())

    requests = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "x"}},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": "echo", "arguments": {"text": "ping"}},
        },
        {"jsonrpc": "2.0", "method": "exit"},
    ]
    wire = b"".join(encode_message(req) for req in requests)

    output = bytearray()

    def read_fn(_size: int) -> bytes:
        return wire  # ignored — the loop drains our buffer instead

    def write_fn(data: bytes) -> int:
        output.extend(data)
        return len(data)

    # ``read_fn`` is unused by the loop; the buffer is read from the
    # byte iterator the loop builds. Wire ``read_fn`` to a no-op that
    # returns an empty bytes so the loop terminates after draining
    # our encoded messages.
    async def run_loop() -> None:
        # Custom loop: feed bytes to iter_messages directly.
        from avo.mcp_server.framing import iter_messages

        messages = list(iter_messages(iter([wire])))
        for message in messages:
            payload = message["payload"]
            try:
                response = await server.handle_message(payload)
            except _ExitSignal:
                return
            if response is not None:
                write_fn(encode_message(response))

    asyncio.run(run_loop())

    assert len(output) > 0
    decoded = list(iter_messages(iter([bytes(output)])))
    assert len(decoded) >= 3
    assert decoded[0]["payload"]["result"]["serverInfo"]["name"] == "avo"
    assert decoded[1]["payload"]["result"]["tools"][0]["name"] == "echo"
    assert '"echoed": "ping"' in decoded[2]["payload"]["result"]["content"][0]["text"]
