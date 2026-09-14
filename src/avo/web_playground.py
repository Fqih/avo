"""Provider playground (chat test-prompt) endpoint for the Avo Web UI.

Holds the ``/api/chat`` route — the streaming provider playground the
dashboard uses to send a test prompt through the configured provider —
and the :meth:`avo.web_ui.AvoWebServer.execute_chat_turn` implementation
that streams the reply and persists the turn.

Re-exported from :mod:`avo.web_ui` for backward compatibility.
"""

from __future__ import annotations

import json
import os
import urllib.parse
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from avo.chat_session import SessionLifecycle
from avo.web_http import _LOG, WebHttpMixin

if TYPE_CHECKING:
    from avo.persona import PersonaManager


class WebPlaygroundMixin(WebHttpMixin):
    """Chat playground routes for :class:`avo.web_ui.AvoWebHandler`."""

    def _route_playground_post(self, parsed: urllib.parse.ParseResult, path: str) -> bool:
        """Handle the ``/api/chat`` POST route (JSON or SSE reply)."""
        import asyncio

        if path == "/api/chat":
            content_len = int(self.headers.get("Content-Length", 0))
            if content_len <= 0:
                self._send_json({"error": "empty body"}, status=400)
                return True

            raw_body = self.rfile.read(content_len).decode("utf-8")
            try:
                data = json.loads(raw_body)
            except Exception:
                self._send_json({"error": "invalid JSON body"}, status=400)
                return True

            message = str(data.get("message", "")).strip()
            if not message:
                self._send_json({"error": "message is required"}, status=400)
                return True

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
                return True

            # Non-SSE standard JSON response
            try:
                res = asyncio.run(self.server.execute_chat_turn(session_id, message))
                self._send_json(res)
            except Exception as exc:
                self._send_json({"ok": False, "error": str(exc)}, status=500)
            return True

        return False


class PlaygroundServerMixin:
    """Chat-turn execution for :class:`avo.web_ui.AvoWebServer`."""

    if TYPE_CHECKING:
        database_path: Path
        persona_manager: PersonaManager

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

            system_content = self.persona_manager.render_system_prompt() or (
                "You are Avo, an autonomous and precise software engineering agent. "
                "Provide direct, concise, and technically accurate responses."
            )
            messages: list[dict[str, JsonValue]] = [
                {
                    "role": "system",
                    "content": system_content,
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


__all__ = ["PlaygroundServerMixin", "WebPlaygroundMixin"]
