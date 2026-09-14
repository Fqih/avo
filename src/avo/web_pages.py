"""Dashboard page serving for the Avo Web UI.

Holds the embedded dashboard HTML template loader and the index route.
Re-exported from :mod:`avo.web_ui` for backward compatibility.
"""

from __future__ import annotations

import urllib.parse
from pathlib import Path

from avo import __version__ as AVO_VERSION
from avo.web_http import WebHttpMixin


def _get_dashboard_html() -> str:
    """Read the embedded dashboard HTML template."""
    asset_path = Path(__file__).resolve().parent / "web_assets" / "index.html"
    if asset_path.is_file():
        content = asset_path.read_text(encoding="utf-8")
        return content.replace("{{AVO_VERSION}}", AVO_VERSION)
    return (
        f"<!DOCTYPE html><html><head><title>Avo Dashboard</title></head>"
        f"<body><h1>Avo Dashboard v{AVO_VERSION}</h1><p>Ready.</p></body></html>"
    )


class WebPageMixin(WebHttpMixin):
    """Dashboard page routes for :class:`avo.web_ui.AvoWebHandler`."""

    def _route_pages(self, parsed: urllib.parse.ParseResult, path: str) -> bool:
        """Handle the index/dashboard route. True when the route answered."""
        if path == "" or path == "/index.html":
            self._send_html(_get_dashboard_html())
            return True

        return False


__all__ = ["WebPageMixin", "_get_dashboard_html"]
