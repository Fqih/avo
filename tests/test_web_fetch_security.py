"""Offline SSRF and redirect tests for model-controlled web fetching."""

from __future__ import annotations

from typing import Any

import pytest

from avo.app_tools.web_fetch import WebFetchArguments, WebFetchError, _fetch_with_client
from avo.web_security import validate_public_url


class _Response:
    def __init__(
        self,
        url: str,
        *,
        status_code: int = 200,
        content: bytes = b"ok",
        headers: dict[str, str] | None = None,
    ) -> None:
        self.url = url
        self.status_code = status_code
        self.content = content
        self.headers = headers or {"content-type": "text/plain"}
        self.encoding = "utf-8"


class _SequenceClient:
    def __init__(self, responses: list[_Response]) -> None:
        self.responses = responses
        self.calls: list[dict[str, Any]] = []

    async def get(
        self,
        url: str,
        *,
        timeout: float,  # noqa: ASYNC109
        follow_redirects: bool,
    ) -> _Response:
        self.calls.append({"url": url, "timeout": timeout, "follow_redirects": follow_redirects})
        return self.responses.pop(0)


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/",
        "http://10.0.0.1/",
        "http://172.16.0.1/",
        "http://192.168.1.1/",
        "http://169.254.169.254/latest/",
        "http://[::1]/",
        "http://[fc00::1]/",
        "http://[fe80::1]/",
        "http://[ff02::1]/",
        "http://[::]/",
        "http://[::ffff:10.0.0.1]/",
    ],
)
def test_validate_public_url_rejects_local_and_reserved_destinations(url: str) -> None:
    with pytest.raises(WebFetchError, match="blocked") as error:
        validate_public_url(url)

    assert "127.0.0.1" not in str(error.value)
    assert "10.0.0.1" not in str(error.value)


async def test_redirect_to_private_destination_is_rejected_before_second_request() -> None:
    client = _SequenceClient(
        [
            _Response(
                "https://public.example/",
                status_code=302,
                headers={"location": "http://127.0.0.1/secret"},
            )
        ]
    )

    with pytest.raises(WebFetchError, match="blocked"):
        await _fetch_with_client(
            client,
            WebFetchArguments(url="https://public.example/"),
            resolver=lambda _host, _port: ("93.184.216.34",),
        )

    assert len(client.calls) == 1


async def test_redirect_chain_is_capped() -> None:
    responses = [
        _Response(
            f"https://public.example/{index}",
            status_code=302,
            headers={"location": f"https://public.example/{index + 1}"},
        )
        for index in range(6)
    ]
    client = _SequenceClient(responses)

    with pytest.raises(WebFetchError, match="redirect limit"):
        await _fetch_with_client(
            client,
            WebFetchArguments(url="https://public.example/0"),
            resolver=lambda _host, _port: ("93.184.216.34",),
        )

    assert len(client.calls) == 6


async def test_hostname_is_revalidated_when_dns_changes() -> None:
    addresses = iter([("93.184.216.34",), ("10.0.0.1",)])

    def changing_resolver(_host: str, _port: int) -> tuple[str, ...]:
        return next(addresses)

    client = _SequenceClient(
        [
            _Response(
                "https://public.example/",
                status_code=302,
                headers={"location": "https://public.example/private"},
            )
        ]
    )

    with pytest.raises(WebFetchError, match="blocked"):
        await _fetch_with_client(
            client,
            WebFetchArguments(url="https://public.example/"),
            resolver=changing_resolver,
        )

    assert len(client.calls) == 1


async def test_public_response_keeps_bounded_payload_shape() -> None:
    client = _SequenceClient([_Response("https://public.example/", content=b"hello world")])

    result = await _fetch_with_client(
        client,
        WebFetchArguments(url="https://public.example/", max_bytes=5),
        resolver=lambda _host, _port: ("93.184.216.34",),
    )

    assert result["status_code"] == 200
    assert result["body"] == "hello"
    assert result["truncated"] is True
    assert result["bytes_read"] == 5
