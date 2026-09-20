"""``web_fetch`` tool: HTTP GET a URL and return the response body.

The tool is a thin wrapper around :mod:`httpx`. It enforces a hard
``max_bytes`` cap on the body so a hostile endpoint cannot flood the
context window, and the caller supplies the client so the tests can
inject a fake without monkey-patching the module.

Only ``http://`` and ``https://`` URLs are accepted. ``file://``,
``data:``, and any other scheme is rejected with
:class:`WebFetchError` before the request leaves the process.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urljoin

from pydantic import BaseModel, Field, JsonValue

from avo import FunctionTool as PublicFunctionTool
from avo.exceptions import ToolExecutionError
from avo.web_security import HostResolver, resolve_host, validate_public_url

WebFetchError = ToolExecutionError

_DEFAULT_MAX_BYTES = 16 * 1024  # 16 KiB — keeps model context manageable
_DEFAULT_TIMEOUT_SECONDS = 15.0

_MAX_REDIRECTS = 5


class WebFetchArguments(BaseModel):
    """Arguments for the ``web_fetch`` tool."""

    url: str = Field(min_length=1, description="Absolute http(s) URL to fetch")
    max_bytes: int = Field(default=_DEFAULT_MAX_BYTES, gt=0, le=1_048_576)
    timeout_seconds: float = Field(default=_DEFAULT_TIMEOUT_SECONDS, gt=0, le=120)


def _validate_url(url: str) -> str:
    """Return ``url`` if its scheme is allowed, else raise ``WebFetchError``."""
    validate_public_url(url)
    return url


async def _fetch_with_client(
    client: Any,
    arguments: WebFetchArguments,
    *,
    resolver: HostResolver = resolve_host,
) -> dict[str, JsonValue]:
    current_url = validate_public_url(arguments.url, resolver=resolver).url
    for redirect_count in range(_MAX_REDIRECTS + 1):
        response = await client.get(
            current_url,
            timeout=arguments.timeout_seconds,
            follow_redirects=False,
        )
        if int(response.status_code) in {301, 302, 303, 307, 308}:
            location = response.headers.get("location", "")
            if not location:
                raise WebFetchError("web_fetch redirect response is missing a location")
            if redirect_count >= _MAX_REDIRECTS:
                raise WebFetchError("web_fetch redirect limit exceeded")
            current_url = urljoin(current_url, location)
            validate_public_url(current_url, resolver=resolver)
            continue
        break
    else:  # pragma: no cover - loop always returns or raises
        raise WebFetchError("web_fetch redirect limit exceeded")

    content = bytes(response.content)
    body_bytes = content[: arguments.max_bytes]
    truncated = len(content) > arguments.max_bytes
    try:
        body_text = body_bytes.decode(response.encoding or "utf-8", errors="replace")
    except LookupError:
        body_text = body_bytes.decode("utf-8", errors="replace")
    payload: dict[str, JsonValue] = {
        "url": str(response.url),
        "status_code": int(response.status_code),
        "content_type": response.headers.get("content-type", ""),
        "body": body_text,
        "truncated": truncated,
        "bytes_read": len(body_bytes),
    }
    return payload


# httpx is optional at import time so the runtime never hard-depends on
# the network stack. Tests inject a fake client.
try:
    import httpx as _httpx
except ModuleNotFoundError:  # pragma: no cover - httpx required by providers extra
    _httpx = None  # type: ignore[assignment]


async def _web_fetch(arguments: WebFetchArguments) -> dict[str, JsonValue]:
    validate_public_url(arguments.url)
    if _httpx is None:
        raise WebFetchError(
            "web_fetch requires the `httpx` package; install avo with the [providers] extra."
        )
    async with _httpx.AsyncClient(trust_env=False) as client:
        return await _fetch_with_client(client, arguments)


def web_fetch_tool() -> PublicFunctionTool[WebFetchArguments]:
    """Return a :class:`FunctionTool` that performs HTTP GETs."""

    return PublicFunctionTool(
        name="web_fetch",
        description=(
            "Fetch a URL with HTTP GET and return the body as text. The "
            "response is truncated at ``max_bytes`` (default 16 KiB); only "
            "http and https URLs are accepted."
        ),
        arguments_model=WebFetchArguments,
        function=_web_fetch,
    )


__all__ = ["WebFetchArguments", "WebFetchError", "web_fetch_tool"]


_ = _fetch_with_client  # exported for tests that inject their own client
