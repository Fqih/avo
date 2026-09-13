"""Modern local Web UI dashboard and visual run inspector for Avo.

Runs an embedded HTTP server providing:
- Overview of provider configuration, health doctor, and token storage.
- Interactive visual trace inspector for recorded runs and events.
- Chat session transcripts and turns explorer.
- Zero external package dependencies (built-in standard library).
"""

from __future__ import annotations

import argparse
import contextlib
import json
import logging
import os
import sys
import urllib.parse
from collections.abc import Sequence
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from avo import __version__ as AVO_VERSION
from avo.auth import load_all_tokens
from avo.chat_session import SessionLifecycle
from avo.doctor import run_doctor
from avo.storage.sqlite import SQLiteEventStore
from avo.tracing import TraceInspector

_LOG = logging.getLogger("avo.web_ui")


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


def _mask_secret(secret: str) -> str:
    """Mask a token or API key for dashboard display."""
    clean = secret.strip()
    if len(clean) <= 8:
        return "•" * len(clean)
    return f"{clean[:4]}••••••••{clean[-4:]}"


class AvoWebHandler(BaseHTTPRequestHandler):
    """HTTP request handler for Avo Web UI."""

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

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path.rstrip("/")

        if path == "" or path == "/index.html":
            self._send_html(_get_dashboard_html())
            return

        if path == "/api/status":
            doc = run_doctor(os.environ)
            stored = load_all_tokens()
            masked_tokens = {k: _mask_secret(v) for k, v in stored.items()}
            router_chain = (
                os.environ.get("AVO_ROUTER_CHAIN", "").strip()
                or os.environ.get("AVO_ROUTER_PROVIDERS", "").strip()
            )
            chain_list = (
                [p.strip() for p in router_chain.split(",") if p.strip()]
                if router_chain
                else ["ollama", "openrouter"]
            )
            self._send_json(
                {
                    "version": AVO_VERSION,
                    "provider": os.environ.get("AVO_PROVIDER", "ollama"),
                    "model": os.environ.get("AVO_MODEL", "auto"),
                    "database": str(self.server.database_path),
                    "tokens": masked_tokens,
                    "doctor": {
                        "ok": doc.ok,
                        "endpoint": doc.endpoint,
                        "missing": doc.missing_vars,
                    },
                    "router": {
                        "active": os.environ.get("AVO_PROVIDER") == "router",
                        "chain": chain_list,
                        "cooldown_seconds": float(
                            os.environ.get("AVO_ROUTER_COOLDOWN_SECONDS", "30.0") or 30.0
                        ),
                    },
                    "env": {
                        "AVO_ROUTER_CHAIN": router_chain,
                        "AVO_ROUTER_PROVIDERS": os.environ.get("AVO_ROUTER_PROVIDERS", ""),
                        "AVO_ROUTER_MODELS": os.environ.get("AVO_ROUTER_MODELS", ""),
                    },
                }
            )
            return

        if path == "/api/runs":
            runs = self.server.get_sync_runs()
            self._send_json({"runs": runs})
            return

        if path.startswith("/api/runs/"):
            run_id = path.split("/api/runs/", 1)[1]
            trace_data = self.server.get_sync_trace(run_id)
            if trace_data is None:
                self._send_json({"error": f"Run {run_id!r} not found"}, status=404)
            else:
                self._send_json(trace_data)
            return

        if path == "/api/sessions":
            sessions = self.server.get_sync_sessions()
            self._send_json({"sessions": sessions})
            return

        self._send_json({"error": "Not Found"}, status=404)


class AvoWebServer(ThreadingHTTPServer):
    """Custom ThreadingHTTPServer holding Avo database connections."""

    def __init__(
        self,
        server_address: tuple[str, int],
        database_path: Path,
    ) -> None:
        super().__init__(server_address, AvoWebHandler)
        self.database_path = database_path

    def get_sync_runs(self) -> list[dict[str, Any]]:
        """Query runs from SQLite synchronously."""
        import asyncio

        async def _fetch() -> list[dict[str, Any]]:
            store = SQLiteEventStore(self.database_path)
            try:
                runs = await store.list_runs()
                return [
                    {
                        "run_id": r.run_id,
                        "state": r.state.value,
                        "stop_reason": r.stop_reason.value if r.stop_reason else None,
                        "steps": r.steps,
                        "created_at": r.created_at.isoformat(),
                    }
                    for r in runs
                ]
            finally:
                await store.close()

        try:
            return asyncio.run(_fetch())
        except Exception as exc:
            _LOG.warning("Could not read runs: %s", exc)
            return []

    def get_sync_trace(self, run_id: str) -> dict[str, Any] | None:
        """Query run trace synchronously."""
        import asyncio

        async def _fetch() -> dict[str, Any] | None:
            store = SQLiteEventStore(self.database_path)
            try:
                trace = await TraceInspector(store).inspect(run_id)
                return {
                    "run_id": trace.run_id,
                    "state": trace.final_state.value,
                    "stop_reason": trace.stop_reason.value if trace.stop_reason else None,
                    "steps": trace.steps,
                    "duration_seconds": trace.duration_seconds,
                    "tokens": trace.token_usage.model_dump(),
                    "text": trace.to_text(),
                }
            except Exception:
                return None
            finally:
                await store.close()

        try:
            return asyncio.run(_fetch())
        except Exception:
            return None

    def get_sync_sessions(self) -> list[dict[str, Any]]:
        """Query chat sessions synchronously."""
        try:
            lifecycle = SessionLifecycle.open(self.database_path)
            infos = lifecycle.list_sessions()
            lifecycle.close()
            return [
                {
                    "session_id": s.session_id,
                    "turn_count": s.turn_count,
                    "last_activity": s.last_activity.isoformat() if s.last_activity else None,
                }
                for s in infos
            ]
        except Exception as exc:
            _LOG.warning("Could not read sessions: %s", exc)
            return []


def run_web_dashboard(
    *,
    port: int = 43111,
    database_path: Path | None = None,
    open_browser: bool = True,
    output_writer: Any = sys.stdout.write,
) -> int:
    """Start the local Web UI server and optionally open the browser."""
    db_path = database_path if database_path is not None else Path("avo.db")
    server = AvoWebServer(("127.0.0.1", port), database_path=db_path)
    url = f"http://localhost:{port}"

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
        default=Path("avo.db"),
        help="Path to the SQLite database (default: avo.db).",
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
        open_browser=not args.no_browser,
    )


__all__ = ["AvoWebHandler", "AvoWebServer", "main", "run_web_dashboard"]
