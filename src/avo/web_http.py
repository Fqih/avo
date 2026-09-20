"""Shared HTTP transport for the Avo Web UI dashboard.

Base mixin behind every route mixin (:mod:`avo.web_pages`,
:mod:`avo.web_api`, :mod:`avo.web_workspace`, :mod:`avo.web_runs`,
:mod:`avo.web_playground`): response helpers, access-log suppression, and
the CORS pre-flight handler. The ``avo.web_ui`` logger name is kept so
log routing is unchanged after the split.

Re-exported from :mod:`avo.web_ui` for backward compatibility.
"""

from __future__ import annotations

import json
import logging
import secrets
from collections.abc import Mapping
from dataclasses import dataclass
from http.cookies import CookieError, SimpleCookie
from http.server import BaseHTTPRequestHandler
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from avo.web_ui import AvoWebServer

_LOG = logging.getLogger("avo.web_ui")


class WebSecurityError(Exception):
    """Stable, non-secret error raised by the mutation authentication gate."""

    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


@dataclass(frozen=True)
class WebSecurityConfig:
    """Explicit policy for browser-originated dashboard mutations."""

    allowed_origin: str | None = None
    cors_enabled: bool = False
    require_confirmation: bool = True

    def __post_init__(self) -> None:
        if self.allowed_origin == "*":
            raise ValueError("wildcard origins are not allowed")
        if self.cors_enabled and not self.allowed_origin:
            raise ValueError("cors_enabled requires an explicit allowed_origin")


def authenticate_mutation(
    headers: Mapping[str, str],
    *,
    expected_token: str,
    csrf_token: str,
    allowed_origin: str | None,
    confirmation: object | None = None,
) -> None:
    """Validate bearer/origin/CSRF/confirmation before mutation parsing."""

    authorization = headers.get("Authorization", "")
    if not authorization or not secrets.compare_digest(
        authorization.encode(), f"Bearer {expected_token}".encode()
    ):
        raise WebSecurityError(401, "authentication_required", "Authentication required")
    origin = headers.get("Origin", "")
    if origin and (allowed_origin is None or origin != allowed_origin):
        raise WebSecurityError(403, "origin_forbidden", "Forbidden origin")
    if origin:
        supplied_csrf = headers.get("X-CSRF-Token", "")
        if not secrets.compare_digest(supplied_csrf.encode(), csrf_token.encode()):
            raise WebSecurityError(403, "csrf_forbidden", "CSRF validation failed")
    if confirmation is not None and confirmation is not True:
        raise WebSecurityError(400, "confirmation_required", "Explicit confirmation is required")


# The launch secret is delivered in a fragment, never in an HTTP URL/query or
# unauthenticated response. Install before dashboard scripts make requests.
_SESSION_SCRIPT = """<script data-avo-session>
(() => {
  const originalFetch = window.fetch.bind(window);
  let csrf = sessionStorage.getItem('avo_csrf') || '';
  const token = new URLSearchParams(location.hash.slice(1)).get('token');
  if (token) history.replaceState(null, '', location.pathname + location.search);
  const ready = token ? originalFetch('/api/session', {
    method: 'POST', credentials: 'same-origin',
    headers: {'Authorization': 'Bearer ' + token}
  }).then(async response => {
    if (!response.ok) return;
    csrf = (await response.json()).csrf_token;
    sessionStorage.setItem('avo_csrf', csrf);
  }).catch(() => {}) : Promise.resolve();
  const confirmations = {
    '/api/permissions': 'Change tool permissions?',
    '/api/provider': 'Change the active provider?',
    '/api/workspace/file': 'Save changes to this workspace file?',
    '/api/git/commit': 'Commit workspace changes?',
    '/api/git/branch': 'Switch the workspace branch?',
    '/api/git/stash': 'Change the workspace stash?'
  };
  window.fetch = async (input, options = {}) => {
    const url = new URL(typeof input === 'string' ? input : input.url, location.href);
    if (url.origin !== location.origin || (options.method || 'GET').toUpperCase() !== 'POST') {
      return originalFetch(input, options);
    }
    await ready;
    const headers = new Headers(options.headers);
    headers.set('X-CSRF-Token', csrf);
    options = {...options, headers, credentials: 'same-origin'};
    const message = confirmations[url.pathname.replace(/\\/$/, '')];
    if (message) {
      if (!window.confirm(message)) throw new Error('Change cancelled');
      options.body = JSON.stringify({...JSON.parse(options.body || '{}'), confirm: true});
    }
    return originalFetch(input, options);
  };
})();
</script>"""


class WebHttpMixin(BaseHTTPRequestHandler):
    """Transport-level helpers shared by all Avo web route mixins."""

    server: AvoWebServer  # type hint

    def _check_origin(self) -> bool:
        """Reject foreign origins and unrecognized hosts, including DNS rebinding."""
        host = self.headers.get("Host", "")
        port = self.server.server_port
        allowed_hosts = {f"127.0.0.1:{port}", f"localhost:{port}", f"[::1]:{port}"}
        if port == 80:
            allowed_hosts.update({"127.0.0.1", "localhost", "[::1]"})
        origins = self.headers.get_all("Origin", [])
        allowed_origins = {f"http://{host}"}
        configured_origin = self.server.web_security.allowed_origin
        if self.server.web_security.cors_enabled and configured_origin:
            allowed_origins.add(configured_origin)
        if (
            len(self.headers.get_all("Host", [])) != 1
            or host not in allowed_hosts
            or len(origins) > 1
            or (origins and origins[0] not in allowed_origins)
            or self.headers.get("Sec-Fetch-Site") == "cross-site"
        ):
            self._send_error("origin_forbidden", "Forbidden origin or host", status=403)
            return False
        return True

    def _authenticate_mutation(self) -> bool:
        if not self._check_origin():
            return False
        authorization = self.headers.get("Authorization", "")
        if authorization:
            valid = len(self.headers.get_all("Authorization", [])) == 1 and secrets.compare_digest(
                authorization.encode(), f"Bearer {self.server.auth_token}".encode()
            )
        else:
            cookie = SimpleCookie()
            try:
                cookie.load(self.headers.get("Cookie", ""))
            except CookieError:
                cookie = SimpleCookie()
            session = cookie.get(self.server.session_cookie_name)
            valid = bool(
                self.headers.get("Origin")
                and session
                and secrets.compare_digest(
                    session.value.encode(), self.server.session_token.encode()
                )
                and secrets.compare_digest(
                    self.headers.get("X-CSRF-Token", "").encode(), self.server.csrf_token.encode()
                )
            )
        if not valid:
            self._send_error("authentication_required", "Authentication required", status=401)
        return valid

    def _require_confirmation(self, data: Any) -> bool:
        if not isinstance(data, dict) or data.get("confirm") is not True:
            self._send_json({"error": "Explicit confirm: true is required"}, status=400)
            return False
        return True

    def _send_error(self, code: str, message: str, *, status: int) -> None:
        """Send a stable JSON error without request credentials or secrets."""

        self._send_json({"error": {"code": code, "message": message}}, status=status)

    def _send_session(self) -> None:
        raw = json.dumps({"ok": True, "csrf_token": self.server.csrf_token}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.send_header(
            "Set-Cookie",
            f"{self.server.session_cookie_name}={self.server.session_token}; "
            "Path=/; HttpOnly; SameSite=Strict",
        )
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, format: str, *args: Any) -> None:
        """Suppress default HTTP server access logs to keep CLI clean."""
        _LOG.debug(format, *args)

    def _send_json(self, data: Any, status: int = 200) -> None:
        raw = json.dumps(data, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(raw)

    def _send_html(self, html: str, status: int = 200) -> None:
        html = html.replace("<head>", "<head>" + _SESSION_SCRIPT, 1)
        raw = html.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Referrer-Policy", "no-referrer")
        self.end_headers()
        self.wfile.write(raw)

    def do_OPTIONS(self) -> None:
        if not self._check_origin():
            return
        self.send_response(204)
        self.send_header("Allow", "GET, POST, OPTIONS")
        if self.server.web_security.cors_enabled:
            origin = self.headers.get("Origin", "")
            if origin and origin == self.server.web_security.allowed_origin:
                self.send_header("Access-Control-Allow-Origin", origin)
                self.send_header("Access-Control-Allow-Credentials", "true")
                self.send_header(
                    "Access-Control-Allow-Headers",
                    "Authorization, Content-Type, X-CSRF-Token",
                )
                self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
                self.send_header("Vary", "Origin")
        self.end_headers()


__all__ = [
    "WebHttpMixin",
    "WebSecurityConfig",
    "WebSecurityError",
    "authenticate_mutation",
]
