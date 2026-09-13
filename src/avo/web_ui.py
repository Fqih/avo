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
import uuid
from collections.abc import Callable, Sequence
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
                        "strategy": os.environ.get("AVO_ROUTER_STRATEGY", "fallback").lower(),
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

        if path.startswith("/api/sessions/"):
            sid = path.split("/api/sessions/", 1)[1]
            session_data = self.server.get_sync_session_detail(sid)
            if session_data is None:
                self._send_json({"error": f"Session {sid!r} not found"}, status=404)
            else:
                self._send_json(session_data)
            return

        self._send_json({"error": "Not Found"}, status=404)

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Accept")
        self.end_headers()

    def do_POST(self) -> None:
        import asyncio

        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path.rstrip("/")

        if path == "/api/chat":
            content_len = int(self.headers.get("Content-Length", 0))
            if content_len <= 0:
                self._send_json({"error": "empty body"}, status=400)
                return

            raw_body = self.rfile.read(content_len).decode("utf-8")
            try:
                data = json.loads(raw_body)
            except Exception:
                self._send_json({"error": "invalid JSON body"}, status=400)
                return

            message = str(data.get("message", "")).strip()
            if not message:
                self._send_json({"error": "message is required"}, status=400)
                return

            session_id = data.get("session_id")
            accept = self.headers.get("Accept", "")
            query_params = urllib.parse.parse_qs(parsed.query)
            is_sse = "text/event-stream" in accept or "stream" in query_params

            if is_sse:
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Connection", "close")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.close_connection = True

                def _stream_cb(text_delta: str, thought_delta: str) -> None:
                    chunk_obj = {"text": text_delta, "thought": thought_delta}
                    msg = f"data: {json.dumps(chunk_obj)}\n\n"
                    self.wfile.write(msg.encode("utf-8"))
                    self.wfile.flush()

                try:
                    res = asyncio.run(
                        self.server.execute_chat_turn(
                            session_id,
                            message,
                            stream_callback=_stream_cb,
                        )
                    )
                    done_obj = {
                        "done": True,
                        "session_id": res["session_id"],
                        "reply": res.get("reply", ""),
                        "thought": res.get("thought", ""),
                    }
                    self.wfile.write(f"data: {json.dumps(done_obj)}\n\n".encode())
                    self.wfile.flush()
                except Exception as exc:
                    err_obj = {"done": True, "error": str(exc)}
                    self.wfile.write(f"data: {json.dumps(err_obj)}\n\n".encode())
                    self.wfile.flush()
                return

            # Non-SSE standard JSON response
            try:
                res = asyncio.run(self.server.execute_chat_turn(session_id, message))
                self._send_json(res)
            except Exception as exc:
                self._send_json({"ok": False, "error": str(exc)}, status=500)
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

    def get_sync_session_detail(self, session_id: str) -> dict[str, Any] | None:
        """Query details and all turns for a specific chat session."""
        try:
            lifecycle = SessionLifecycle.open(self.database_path)
            if not lifecycle.session_exists(session_id):
                lifecycle.close()
                return None
            turns = lifecycle.turns(session_id)
            lifecycle.close()
            return {
                "session_id": session_id,
                "turn_count": len(turns),
                "turns": [
                    {
                        "sequence": t.sequence,
                        "role": t.role,
                        "content": t.content,
                        "created_at": t.created_at.isoformat(),
                    }
                    for t in turns
                ],
            }
        except Exception as exc:
            _LOG.warning("Could not read session %r: %s", session_id, exc)
            return None

    async def execute_chat_turn(
        self,
        session_id: str | None,
        message: str,
        stream_callback: Callable[[str, str], Any] | None = None,
    ) -> dict[str, Any]:
        """Execute one chat turn against the configured provider and persist in SQLite."""
        from pydantic import JsonValue

        from avo.config import build_provider_from_env
        from avo.models import ModelRequest
        from avo.providers.streaming import split_thinking

        sid = session_id.strip() if session_id and session_id.strip() else uuid.uuid4().hex[:12]
        lifecycle = SessionLifecycle.open(self.database_path)
        try:
            lifecycle.record_user_turn(sid, message)
            past_turns = lifecycle.turns(sid)

            messages: list[dict[str, JsonValue]] = [
                {
                    "role": "system",
                    "content": (
                        "You are Avo, an autonomous and precise software engineering agent. "
                        "Provide direct, concise, and technically accurate responses."
                    ),
                }
            ]
            for t in past_turns[-20:]:
                messages.append({"role": t.role, "content": t.content})

            try:
                provider = build_provider_from_env(os.environ)
            except Exception as exc:
                error_reply = f"Error configuring provider: {exc}"
                lifecycle.record_assistant_turn(
                    sid,
                    error_reply,
                    run_id=f"err-{uuid.uuid4().hex[:8]}",
                    status="FAILED",
                    stop_reason="CONFIG_ERROR",
                )
                return {"ok": False, "session_id": sid, "reply": error_reply, "error": str(exc)}

            run_id = f"web-{uuid.uuid4().hex[:8]}"
            req = ModelRequest(
                run_id=run_id,
                step=1,
                messages=messages,
            )

            full_text = ""
            full_thought = ""

            if stream_callback and hasattr(provider, "stream"):
                try:
                    async for chunk in provider.stream(req):
                        text_delta = getattr(chunk, "text", "") or ""
                        thought_delta = getattr(chunk, "thought", "") or ""
                        full_text += text_delta
                        full_thought += thought_delta
                        if text_delta or thought_delta:
                            stream_callback(text_delta, thought_delta)
                except Exception as exc:
                    _LOG.warning("Streaming failed, falling back to generate: %s", exc)
                    resp = await provider.generate(req)
                    raw_content = resp.content or ""
                    t_thought, t_answer = split_thinking(raw_content)
                    full_thought = t_thought
                    full_text = t_answer
                    stream_callback(full_text, full_thought)
            else:
                resp = await provider.generate(req)
                raw_content = resp.content or ""
                t_thought, t_answer = split_thinking(raw_content)
                full_thought = t_thought
                full_text = t_answer
                if stream_callback:
                    stream_callback(full_text, full_thought)

            extracted_thought, clean_answer = split_thinking(full_text)
            if extracted_thought and not full_thought:
                full_thought = extracted_thought
                full_text = clean_answer

            lifecycle.record_assistant_turn(
                sid,
                full_text,
                run_id=run_id,
                status="COMPLETED",
                stop_reason="FINAL",
            )

            return {
                "ok": True,
                "session_id": sid,
                "reply": full_text,
                "thought": full_thought,
            }
        finally:
            lifecycle.close()


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
