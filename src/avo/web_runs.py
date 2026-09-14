"""Run, event, and session inspector endpoints for the Avo Web UI.

Holds the ``/api/runs*``, ``/api/events``, ``/api/sessions*``, and
``/api/history`` routes plus the :class:`avo.web_ui.AvoWebServer` helpers
they call (run listing, trace inspection, recent events, session detail,
and history search).

Re-exported from :mod:`avo.web_ui` for backward compatibility.
"""

from __future__ import annotations

import json
import os
import urllib.parse
from pathlib import Path
from typing import TYPE_CHECKING, Any

from avo.chat_session import SessionLifecycle
from avo.events import AgentEvent
from avo.storage.sqlite import SQLiteEventStore
from avo.tracing import TraceInspector
from avo.web_http import _LOG, WebHttpMixin


class WebRunsMixin(WebHttpMixin):
    """Runs/events/sessions/history GET routes for AvoWebHandler."""

    def _route_runs_get(self, parsed: urllib.parse.ParseResult, path: str) -> bool:
        """Handle the run, event, and session inspector GET routes."""
        if path == "/api/runs":
            runs = self.server.get_sync_runs()
            self._send_json({"runs": runs})
            return True

        if path == "/api/events":
            event_params = urllib.parse.parse_qs(parsed.query)
            run_filter = event_params.get("run_id", [None])[0]
            limit_str = event_params.get("limit", ["50"])[0]
            limit = int(limit_str) if limit_str.isdigit() else 50
            limit = max(1, min(limit, 200))
            is_stream = "stream" in event_params or "text/event-stream" in self.headers.get(
                "Accept", ""
            )

            if is_stream:
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Connection", "close")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.close_connection = True

                events = self.server.get_sync_recent_events(limit=limit, run_id=run_filter)
                events.reverse()
                for ev in events:
                    msg = f"data: {json.dumps(ev)}\n\n"
                    self.wfile.write(msg.encode("utf-8"))
                self.wfile.write(
                    f"data: {json.dumps({'done': True, 'count': len(events)})}\n\n".encode()
                )
                self.wfile.flush()
                return True

            events = self.server.get_sync_recent_events(limit=limit, run_id=run_filter)
            self._send_json({"events": events, "count": len(events)})
            return True

        if path.startswith("/api/runs/"):
            run_id = path.split("/api/runs/", 1)[1]
            trace_data = self.server.get_sync_trace(run_id)
            if trace_data is None:
                self._send_json({"error": f"Run {run_id!r} not found"}, status=404)
            else:
                self._send_json(trace_data)
            return True

        if path == "/api/sessions":
            sessions = self.server.get_sync_sessions()
            self._send_json({"sessions": sessions})
            return True

        if path.startswith("/api/sessions/"):
            sid = path.split("/api/sessions/", 1)[1]
            session_data = self.server.get_sync_session_detail(sid)
            if session_data is None:
                self._send_json({"error": f"Session {sid!r} not found"}, status=404)
            else:
                self._send_json(session_data)
            return True

        if path == "/api/history":
            params = urllib.parse.parse_qs(parsed.query)
            query = params.get("q", [""])[0].strip()
            sid_raw = params.get("session_id", [None])[0]
            filter_sid: str | None = (
                sid_raw.strip() if isinstance(sid_raw, str) and sid_raw.strip() else None
            )
            limit_str = params.get("limit", ["50"])[0]
            limit = int(limit_str) if limit_str.isdigit() else 50
            results = self.server.search_sync_history(query, session_id=filter_sid, limit=limit)
            self._send_json({"query": query, "results": results})
            return True

        return False


class RunsServerMixin:
    """Run/event/session queries for :class:`avo.web_ui.AvoWebServer`."""

    if TYPE_CHECKING:
        database_path: Path

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
                    "entries": [
                        {
                            "sequence": e.sequence,
                            "created_at": e.created_at.isoformat(),
                            "event_type": e.event_type.value,
                            "summary": e.summary,
                            "from_state": e.from_state.value if e.from_state else None,
                            "to_state": e.to_state.value if e.to_state else None,
                            "duration_ms": e.duration_ms,
                            "error": e.error,
                            "policy": e.policy,
                            "payload": e.payload,
                        }
                        for e in trace.entries
                    ],
                }
            except Exception:
                return None
            finally:
                await store.close()

        try:
            return asyncio.run(_fetch())
        except Exception:
            return None

    def get_sync_recent_events(
        self, limit: int = 50, run_id: str | None = None
    ) -> list[dict[str, Any]]:
        """Query recent events across all runs or for a specific run."""
        import sqlite3

        if not self.database_path.exists():
            return []

        try:
            conn = sqlite3.connect(f"file:{self.database_path}?mode=ro", uri=True)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            if run_id:
                rows = cursor.execute(
                    "SELECT event_json FROM events WHERE run_id = ? ORDER BY sequence DESC LIMIT ?",
                    (run_id, limit),
                ).fetchall()
            else:
                rows = cursor.execute(
                    "SELECT event_json FROM events ORDER BY rowid DESC LIMIT ?",
                    (limit,),
                ).fetchall()
            conn.close()

            results: list[dict[str, Any]] = []
            for row in rows:
                raw_json = str(row["event_json"])
                try:
                    event = AgentEvent.model_validate_json(raw_json)
                    entry = TraceInspector._entry(event)
                    results.append(
                        {
                            "event_id": event.event_id,
                            "run_id": event.run_id,
                            "sequence": event.sequence,
                            "created_at": event.created_at.isoformat(),
                            "event_type": event.event_type.value,
                            "summary": entry.summary,
                            "duration_ms": entry.duration_ms,
                            "error": entry.error,
                            "policy": entry.policy,
                            "payload": event.payload,
                        }
                    )
                except Exception:
                    continue
            return results
        except Exception as exc:
            _LOG.warning("Could not read recent events: %s", exc)
            return []

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
            from avo.context_advisor import evaluate_session_context

            model_name = os.environ.get("AVO_MODEL", "auto")
            advisor_report = evaluate_session_context(turns, model_name)
            return {
                "session_id": session_id,
                "turn_count": len(turns),
                "context_advice": advisor_report.to_dict(),
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

    def search_sync_history(
        self,
        query: str,
        *,
        session_id: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Search past conversation turns matching a keyword query."""
        try:
            lifecycle = SessionLifecycle.open(self.database_path)
            turns = lifecycle.search_history(query, session_id=session_id, limit=limit)
            lifecycle.close()
            return [
                {
                    "session_id": t.session_id,
                    "sequence": t.sequence,
                    "role": t.role,
                    "content": t.content,
                    "created_at": t.created_at.isoformat(),
                }
                for t in turns
            ]
        except Exception as exc:
            _LOG.warning("History search error: %s", exc)
            return []


__all__ = ["RunsServerMixin", "WebRunsMixin"]
