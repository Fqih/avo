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
from http.server import BaseHTTPRequestHandler
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from avo.web_ui import AvoWebServer

_LOG = logging.getLogger("avo.web_ui")


class WebHttpMixin(BaseHTTPRequestHandler):
    """Transport-level helpers shared by all Avo web route mixins."""

    server: AvoWebServer  # type hint

    def log_message(self, format: str, *args: Any) -> None:
        """Suppress default HTTP server access logs to keep CLI clean."""
        _LOG.debug(format, *args)

    def _send_json(self, data: Any, status: int = 200) -> None:
        raw = json.dumps(data, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(raw)

    def _send_html(self, html: str, status: int = 200) -> None:
        raw = html.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Accept")
        self.end_headers()


__all__ = ["WebHttpMixin"]
