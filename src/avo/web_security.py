"""Destination validation helpers for model-controlled HTTP requests."""

from __future__ import annotations

import ipaddress
import socket
from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import urlparse

from avo.exceptions import ToolExecutionError

WebSecurityError = ToolExecutionError
HostResolver = Callable[[str, int], tuple[str, ...]]


@dataclass(frozen=True)
class ParsedPublicUrl:
    """A validated HTTP(S) URL and its destination metadata."""

    url: str
    hostname: str
    port: int


def resolve_host(hostname: str, port: int) -> tuple[str, ...]:
    """Resolve a host into unique textual addresses using the system resolver."""

    try:
        infos = socket.getaddrinfo(hostname, port, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise WebSecurityError("web_fetch could not resolve the destination host") from exc
    return tuple(dict.fromkeys(str(info[4][0]) for info in infos))


def _blocked_category(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> str | None:
    mapped = address.ipv4_mapped if isinstance(address, ipaddress.IPv6Address) else None
    checked = mapped or address
    if checked.is_loopback:
        return "loopback"
    if checked.is_private:
        return "private"
    if checked.is_link_local:
        return "link-local"
    if checked.is_multicast:
        return "multicast"
    if checked.is_unspecified:
        return "unspecified"
    if checked.is_reserved:
        return "reserved"
    return None


def validate_public_url(
    url: str,
    *,
    resolver: HostResolver = resolve_host,
) -> ParsedPublicUrl:
    """Validate an HTTP(S) URL against private and local destinations."""

    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise WebSecurityError(f"web_fetch only supports http, https URLs; got {parsed.scheme!r}")
    if not parsed.netloc or not parsed.hostname:
        raise WebSecurityError(f"web_fetch URL is missing a host: {url}")
    if parsed.username is not None or parsed.password is not None:
        raise WebSecurityError("web_fetch URLs cannot contain credentials")
    try:
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
    except ValueError as exc:
        raise WebSecurityError("web_fetch URL contains an invalid port") from exc

    hostname = parsed.hostname.rstrip(".").lower()
    try:
        literal = ipaddress.ip_address(hostname)
    except ValueError:
        addresses = resolver(hostname, port)
    else:
        addresses = (str(literal),)

    for address_text in addresses:
        try:
            address = ipaddress.ip_address(address_text)
        except ValueError as exc:
            raise WebSecurityError("web_fetch resolver returned an invalid address") from exc
        category = _blocked_category(address)
        if category is not None:
            raise WebSecurityError(f"web_fetch blocked {category} destination")

    return ParsedPublicUrl(url=url, hostname=hostname, port=port)


__all__ = [
    "HostResolver",
    "ParsedPublicUrl",
    "WebSecurityError",
    "resolve_host",
    "validate_public_url",
]
