"""Modern local Web UI dashboard and visual run inspector for Avo.

Runs an embedded HTTP server providing:
- Overview of provider configuration, health doctor, and token storage.
- Interactive visual trace inspector for recorded runs and events.
- Chat session transcripts and turns explorer.
- Zero external package dependencies (built-in standard library).

The request handler is split by route domain into mixin modules: shared
transport helpers in :mod:`avo.web_http`, the dashboard page in
:mod:`avo.web_pages`, JSON status/cost/router/persona endpoints in
:mod:`avo.web_api`, git and workspace endpoints in
:mod:`avo.web_workspace`, the runs/events/sessions inspector in
:mod:`avo.web_runs`, and the chat playground in :mod:`avo.web_playground`.
Those modules are re-exported here so the public surface
(``from avo.web_ui import AvoWebServer``) stays stable.
"""

from __future__ import annotations

import argparse
import contextlib
import os
import secrets
import sys
import threading
import urllib.parse
from collections.abc import Sequence
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any

from avo import __version__ as AVO_VERSION  # re-export
from avo.config import resolve_database_path
from avo.config_resolver import resolve_security_config
from avo.permissions import permission_policy_from_env
from avo.persona import PersonaManager
from avo.web_api import ApiServerMixin, WebApiMixin, _mask_secret  # re-export
from avo.web_approval import DurableApprovalStore, WebApprovalBridge
from avo.web_http import _LOG, WebHttpMixin, WebSecurityConfig  # re-export
from avo.web_pages import WebPageMixin, _get_dashboard_html  # re-export
from avo.web_playground import PlaygroundServerMixin, WebPlaygroundMixin
from avo.web_runs import RunsServerMixin, WebRunsMixin
from avo.web_workspace import WebWorkspaceMixin, WorkspaceServerMixin


class AvoWebHandler(WebPageMixin, WebApiMixin, WebWorkspaceMixin, WebRunsMixin, WebPlaygroundMixin):
    """HTTP request handler for Avo Web UI."""

    def do_GET(self) -> None:
        if not self._check_origin():
            return
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path.rstrip("/")

        for route in (
            self._route_pages,
            self._route_api_get,
            self._route_workspace_get,
            self._route_runs_get,
        ):
            if route(parsed, path):
                return

        self._send_json({"error": "Not Found"}, status=404)

    def do_POST(self) -> None:
        if not self._authenticate_mutation():
            return
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path.rstrip("/")

        if path == "/api/session":
            self._send_session()
            return

        for route in (
            self._route_playground_post,
            self._route_api_post,
            self._route_workspace_post,
        ):
            if route(parsed, path):
                return

        self._send_json({"error": "Not Found"}, status=404)


class AvoWebServer(
    ThreadingHTTPServer,
    ApiServerMixin,
    WorkspaceServerMixin,
    RunsServerMixin,
    PlaygroundServerMixin,
):
    """Custom ThreadingHTTPServer holding Avo database connections."""

    @property
    def session_cookie_name(self) -> str:
        """Cookies share a host across ports, so namespace each dashboard."""
        return f"avo_session_{self.server_port}"

    def __init__(
        self,
        server_address: tuple[str, int],
        database_path: Path,
        persona_manager: PersonaManager | None = None,
        permission_mode: str | None = None,
        workspace_root: Path | None = None,
        web_security: WebSecurityConfig | None = None,
    ) -> None:
        super().__init__(server_address, AvoWebHandler)
        self.auth_token = secrets.token_urlsafe(32)
        self.session_token = secrets.token_urlsafe(32)
        self.csrf_token = secrets.token_urlsafe(32)
        self.database_path = database_path
        self.workspace_root = (
            Path(workspace_root).resolve() if workspace_root is not None else Path.cwd().resolve()
        )
        if web_security is not None:
            self.web_security = web_security
        else:
            security = resolve_security_config(workspace_root=self.workspace_root)
            self.web_security = WebSecurityConfig(
                allowed_origin=security.web_allowed_origin.value,
                cors_enabled=security.web_cors_enabled.value,
            )
        self.persona_manager = (
            persona_manager if persona_manager is not None else PersonaManager(self.workspace_root)
        )
        self.permission_mode = permission_policy_from_env(
            {"AVO_PERMISSION_MODE": permission_mode} if permission_mode is not None else None
        ).mode.value
        # Per-server config lock: serialises concurrent HTTP mutations to
        # permission_mode / active_provider / active_model so that a torn
        # write from two simultaneous POST /api/* requests cannot cause the
        # runtime to see an inconsistent environment snapshot.
        self._config_lock = threading.Lock()
        self.active_provider: str = os.environ.get("AVO_PROVIDER", "")
        self.active_model: str = os.environ.get("AVO_MODEL", "")
        self.approval_bridge = WebApprovalBridge(store=DurableApprovalStore(self.database_path))


def run_web_dashboard(
    *,
    port: int = 43111,
    database_path: Path | None = None,
    workspace_root: Path | None = None,
    open_browser: bool = True,
    output_writer: Any = sys.stdout.write,
) -> int:
    """Start the local Web UI server and optionally open the browser."""
    db_path = resolve_database_path(database_path)
    ws_root = workspace_root if workspace_root is not None else Path.cwd()
    server = AvoWebServer(
        ("127.0.0.1", port),
        database_path=db_path,
        workspace_root=ws_root,
    )
    url = f"http://localhost:{server.server_port}/#token={server.auth_token}"

    output_writer(
        f"\nAvo Web UI Dashboard running at:\n"
        f"  {url}\n\n"
        f"Database: {db_path.resolve()}\n"
        f"Press Ctrl+C to stop.\n\n"
    )

    if open_browser:
        import webbrowser

        with contextlib.suppress(Exception):
            webbrowser.open(url)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        output_writer("\nStopping Avo Web UI Dashboard...\n")
    finally:
        server.server_close()
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point for `avo ui`."""
    parser = argparse.ArgumentParser(
        prog="avo ui",
        description="Run the local web dashboard to inspect runs, sessions, and router status.",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=43111,
        help="Port to bind the web server (default: 43111).",
    )
    parser.add_argument(
        "--database",
        "-d",
        type=Path,
        default=None,
        help="Path to the SQLite database (default: AVO_DATABASE_PATH or avo.db).",
    )
    parser.add_argument(
        "--workspace",
        "-w",
        type=Path,
        default=None,
        help="Path to the workspace root directory (default: current directory).",
    )
    parser.add_argument(
        "--no-browser",
        action="store_true",
        help="Do not automatically open the dashboard in the default browser.",
    )

    args = parser.parse_args(argv)
    return run_web_dashboard(
        port=args.port,
        database_path=args.database,
        workspace_root=args.workspace,
        open_browser=not args.no_browser,
    )


__all__ = [
    "AVO_VERSION",
    "_LOG",
    "AvoWebHandler",
    "AvoWebServer",
    "WebApiMixin",
    "WebHttpMixin",
    "WebPageMixin",
    "WebPlaygroundMixin",
    "WebRunsMixin",
    "WebWorkspaceMixin",
    "_get_dashboard_html",
    "_mask_secret",
    "main",
    "run_web_dashboard",
]
