"""JSON-RPC 2.0 framing for the MCP stdio transport.

MCP uses the LSP-style ``Content-Length`` framing: each message is a
header section followed by an empty line and a UTF-8 JSON body. The
``mcp`` upstream SDK does the same thing internally; this module
re-implements it so the server has no hard dependency on the optional
``[mcp]`` extra.

Example wire format::

    Content-Length: 142\r\n
    \r\n
    {"jsonrpc":"2.0","id":1,"method":"initialize","params":{...}}

Reference: https://modelcontextprotocol.io/specification/2025-06-18/basic
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any


class FramingError(ValueError):
    """Raised when the wire stream violates the MCP framing rules."""


def encode_message(payload: dict[str, Any]) -> bytes:
    """Encode ``payload`` as a single MCP message."""

    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    header = f"Content-Length: {len(body)}\r\n\r\n".encode("ascii")
    return header + body


def iter_messages(stream: Iterator[bytes]) -> Iterator[dict[str, Any]]:
    """Yield each parsed JSON-RPC message from ``stream``.

    ``stream`` is any iterable yielding bytes — typically a blocking
    ``sys.stdin.buffer.read(N)`` wrapped in a generator. The function
    handles partial reads by buffering across calls until the full
    body arrives.
    """

    buffer = bytearray()
    for chunk in stream:
        if chunk:
            buffer.extend(chunk)
        while True:
            message = _try_parse_one(bytes(buffer))
            if message is None:
                break
            yield message
            # Drop the consumed bytes from the buffer.
            del buffer[: message["_consumed"]]


def _try_parse_one(data: bytes) -> dict[str, Any] | None:
    """Parse one full message from ``data``.

    Returns the message plus a ``_consumed`` hint telling the caller
    how many bytes to drop from the buffer. ``None`` means more bytes
    are needed before the next message can be parsed.
    """

    header_end = data.find(b"\r\n\r\n")
    if header_end == -1:
        return None

    header_block = data[:header_end].decode("ascii", errors="replace")
    content_length = _parse_content_length(header_block)
    if content_length is None:
        raise FramingError(f"missing or invalid Content-Length header: {header_block!r}")

    body_start = header_end + 4
    body_end = body_start + content_length
    if len(data) < body_end:
        return None

    try:
        payload = json.loads(data[body_start:body_end].decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise FramingError(f"invalid JSON body: {exc}") from exc

    return {"_consumed": body_end, "payload": payload}


def _parse_content_length(header_block: str) -> int | None:
    """Return the ``Content-Length`` value from ``header_block`` or ``None``."""

    for line in header_block.split("\r\n"):
        name, sep, value = line.partition(":")
        if not sep:
            continue
        if name.strip().lower() == "content-length":
            try:
                return int(value.strip())
            except ValueError:
                return None
    return None
