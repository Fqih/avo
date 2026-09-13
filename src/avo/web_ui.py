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
from avo.cost import aggregate_costs, report_to_dict
from avo.doctor import run_doctor
from avo.persona import PersonaManager
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
            cost_report = aggregate_costs(self.server.database_path)
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
                    "cost": {
                        "run_count": cost_report.run_count,
                        "total_tokens": cost_report.total.total_tokens,
                        "input_tokens": cost_report.total.input_tokens,
                        "output_tokens": cost_report.total.output_tokens,
                        "cost_usd": (
                            str(cost_report.cost_usd) if cost_report.cost_usd is not None else None
                        ),
                    },
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
                    "persona": {
                        "active": self.server.persona_manager.active_persona or "default",
                        "instructions_configured": bool(
                            self.server.persona_manager.custom_instructions
                        ),
                        "instructions": self.server.persona_manager.custom_instructions or "",
                        "available": [
                            *self.server.persona_manager.available_personas().keys(),
                            "default",
                        ],
                    },
                    "permissions": {
                        "mode": self.server.permission_mode,
                        "available": ["bypass", "default", "accept_edits"],
                    },
                    "env": {
                        "AVO_ROUTER_CHAIN": router_chain,
                        "AVO_ROUTER_PROVIDERS": os.environ.get("AVO_ROUTER_PROVIDERS", ""),
                        "AVO_ROUTER_MODELS": os.environ.get("AVO_ROUTER_MODELS", ""),
                    },
                }
            )
            return

        if path == "/api/persona":
            self._send_json(
                {
                    "active": self.server.persona_manager.active_persona or "default",
                    "instructions": self.server.persona_manager.custom_instructions or "",
                    "available": [
                        *self.server.persona_manager.available_personas().keys(),
                        "default",
                    ],
                }
            )
            return

        if path == "/api/permissions":
            self._send_json(
                {
                    "mode": self.server.permission_mode,
                    "available": ["bypass", "default", "accept_edits"],
                }
            )
            return

        if path == "/api/git":
            self._send_json(self.server.get_sync_git_status())
            return

        if path == "/api/git/stash":
            from avo.workspace.git import GitRepository

            repo = GitRepository(self.server.workspace_root)
            if not repo.is_repository():
                self._send_json({"error": "Not a git repository"}, status=400)
                return
            self._send_json({"stashes": repo.stash_list()})
            return

        if path.startswith("/api/git/commit/"):
            from avo.workspace.git import GitError, GitRepository

            commit_hash = path.split("/api/git/commit/", 1)[1].strip()
            if not commit_hash:
                self._send_json({"error": "commit hash is required"}, status=400)
                return
            repo = GitRepository(self.server.workspace_root)
            if not repo.is_repository():
                self._send_json({"error": "Not a git repository"}, status=400)
                return
            try:
                commit_data = repo.commit_show(commit_hash)
                self._send_json(commit_data)
            except GitError as exc:
                self._send_json({"error": str(exc)}, status=404)
            return

        if path == "/api/cost":
            cost_report = aggregate_costs(self.server.database_path)
            self._send_json(report_to_dict(cost_report))
            return

        if path == "/api/workspace/tree":
            tree_params = urllib.parse.parse_qs(parsed.query)
            subpath = tree_params.get("path", [""])[0].strip()
            depth_str = tree_params.get("depth", ["8"])[0].strip()
            max_depth = int(depth_str) if depth_str.isdigit() else 8
            max_depth = max(1, min(max_depth, 16))
            try:
                tree = self.server.get_sync_workspace_tree(subpath=subpath, max_depth=max_depth)
                self._send_json(tree)
            except Exception as exc:
                self._send_json({"error": str(exc)}, status=400)
            return

        if path == "/api/workspace/file":
            file_params = urllib.parse.parse_qs(parsed.query)
            file_path = file_params.get("path", [""])[0].strip()
            if not file_path:
                self._send_json({"error": "Query parameter 'path' is required"}, status=400)
                return
            try:
                file_info = self.server.get_sync_workspace_file(file_path)
                self._send_json(file_info)
            except Exception as exc:
                self._send_json({"error": str(exc)}, status=400)
            return

        if path == "/api/router":
            self._send_json(self.server.get_sync_router_status())
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

        if path == "/api/persona":
            content_len = int(self.headers.get("Content-Length", 0))
            if content_len <= 0:
                self._send_json({"error": "empty body"}, status=400)
                return
            try:
                data = json.loads(self.rfile.read(content_len).decode("utf-8"))
            except Exception:
                self._send_json({"error": "invalid JSON body"}, status=400)
                return

            persona_name = data.get("persona")
            instructions = data.get("instructions")

            register_data = data.get("register")
            if isinstance(register_data, dict):
                reg_name = str(register_data.get("name", "")).strip()
                reg_prompt = str(register_data.get("prompt", "")).strip()
                try:
                    self.server.persona_manager.register_persona(reg_name, reg_prompt, persist=True)
                except ValueError as exc:
                    self._send_json({"ok": False, "error": str(exc)}, status=400)
                    return

            if persona_name is not None:
                p_clean = str(persona_name).strip()
                if p_clean in ("default", "clear", ""):
                    self.server.persona_manager.set_persona(None)
                elif p_clean in self.server.persona_manager.available_personas():
                    self.server.persona_manager.set_persona(p_clean)
                else:
                    self._send_json(
                        {"ok": False, "error": f"Unknown persona '{p_clean}'"}, status=400
                    )
                    return

            if instructions is not None:
                instr_clean = str(instructions).strip()
                if instr_clean in ("clear", ""):
                    self.server.persona_manager.set_custom_instructions(None)
                else:
                    self.server.persona_manager.set_custom_instructions(instr_clean)

            self._send_json(
                {
                    "ok": True,
                    "persona": self.server.persona_manager.active_persona or "default",
                    "instructions": self.server.persona_manager.custom_instructions or "",
                    "available": [
                        *self.server.persona_manager.available_personas().keys(),
                        "default",
                    ],
                }
            )
            return

        if path == "/api/permissions":
            content_len = int(self.headers.get("Content-Length", 0))
            if content_len <= 0:
                self._send_json({"error": "empty body"}, status=400)
                return
            try:
                data = json.loads(self.rfile.read(content_len).decode("utf-8"))
            except Exception:
                self._send_json({"error": "invalid JSON body"}, status=400)
                return

            mode = str(data.get("mode", "")).strip().lower()
            if mode not in ("bypass", "default", "accept_edits"):
                self._send_json(
                    {"ok": False, "error": f"Invalid permission mode '{mode}'"}, status=400
                )
                return

            self.server.permission_mode = mode
            os.environ["AVO_PERMISSION_MODE"] = mode
            self._send_json({"ok": True, "mode": mode})
            return

        if path == "/api/git/commit":
            from avo.workspace.git import GitRepository, generate_commit_message_heuristic

            content_len = int(self.headers.get("Content-Length", 0))
            body_dict: dict[str, Any] = {}
            if content_len > 0:
                with contextlib.suppress(Exception):
                    body_dict = json.loads(self.rfile.read(content_len).decode("utf-8"))

            repo = GitRepository(self.server.workspace_root)
            if not repo.is_repository():
                self._send_json({"ok": False, "error": "Not a git repository"}, status=400)
                return

            status = repo.status()
            if status.is_clean:
                self._send_json(
                    {"ok": False, "error": "Working tree clean, nothing to commit"}, status=400
                )
                return

            msg = body_dict.get("message")
            commit_msg = (
                str(msg).strip()
                if msg and str(msg).strip()
                else generate_commit_message_heuristic(status)
            )

            try:
                commit_hash = repo.commit(commit_msg)
            except Exception as exc:
                self._send_json(
                    {"ok": False, "error": f"git commit failed: {exc}"},
                    status=500,
                )
                return

            self._send_json(
                {
                    "ok": True,
                    "message": commit_msg,
                    "commit_hash": commit_hash,
                }
            )
            return

        if path == "/api/git/branch":
            from avo.workspace.git import GitError, GitRepository

            content_len = int(self.headers.get("Content-Length", 0))
            if content_len <= 0:
                self._send_json({"error": "empty body"}, status=400)
                return
            try:
                data = json.loads(self.rfile.read(content_len).decode("utf-8"))
            except Exception:
                self._send_json({"error": "invalid JSON body"}, status=400)
                return

            branch_name = str(data.get("branch", "")).strip()
            create = bool(data.get("create", False))
            if not branch_name:
                self._send_json({"ok": False, "error": "Branch name is required"}, status=400)
                return

            repo = GitRepository(self.server.workspace_root)
            if not repo.is_repository():
                self._send_json({"ok": False, "error": "Not a git repository"}, status=400)
                return

            try:
                repo.switch_branch(branch_name, create=create)
                self._send_json({"ok": True, "branch": branch_name, "created": create})
            except GitError as exc:
                self._send_json({"ok": False, "error": str(exc)}, status=400)
            return

        if path == "/api/git/stash":
            from avo.workspace.git import GitError, GitRepository

            content_len = int(self.headers.get("Content-Length", 0))
            stash_payload: dict[str, Any] = {}
            if content_len > 0:
                with contextlib.suppress(Exception):
                    loaded = json.loads(self.rfile.read(content_len).decode("utf-8"))
                    if isinstance(loaded, dict):
                        stash_payload = loaded

            repo = GitRepository(self.server.workspace_root)
            if not repo.is_repository():
                self._send_json({"ok": False, "error": "Not a git repository"}, status=400)
                return

            action = str(stash_payload.get("action", "list")).strip().lower()
            try:
                if action in ("save", "push"):
                    stash_msg = stash_payload.get("message")
                    save_res = repo.stash_save(str(stash_msg) if stash_msg else None)
                    self._send_json({"ok": True, "result": save_res, "stashes": repo.stash_list()})
                    return
                if action in ("pop", "apply"):
                    idx = int(stash_payload.get("index", 0))
                    repo.stash_pop(idx)
                    self._send_json({"ok": True, "stashes": repo.stash_list()})
                    return
                if action in ("drop", "delete"):
                    idx = int(stash_payload.get("index", 0))
                    repo.stash_drop(idx)
                    self._send_json({"ok": True, "stashes": repo.stash_list()})
                    return
                self._send_json({"ok": True, "stashes": repo.stash_list()})
                return
            except GitError as exc:
                self._send_json({"ok": False, "error": str(exc)}, status=400)
                return

        if path == "/api/router/probe":
            self._send_json(self.server.sync_probe_router())
            return

        if path == "/api/router/bench":
            content_len = int(self.headers.get("Content-Length", 0))
            bench_prompt = "Explain recursion in 10 words."
            if content_len > 0:
                with contextlib.suppress(Exception):
                    loaded = json.loads(self.rfile.read(content_len).decode("utf-8"))
                    if isinstance(loaded, dict) and loaded.get("prompt"):
                        bench_prompt = str(loaded["prompt"]).strip() or bench_prompt
            res = self.server.sync_bench_routes(prompt=bench_prompt)
            self._send_json(res)
            return

        if path == "/api/provider":
            content_len = int(self.headers.get("Content-Length", 0))
            if content_len <= 0:
                self._send_json({"error": "empty body"}, status=400)
                return
            try:
                data = json.loads(self.rfile.read(content_len).decode("utf-8"))
            except Exception:
                self._send_json({"error": "invalid JSON body"}, status=400)
                return

            provider = str(data.get("provider", "")).strip()
            model = str(data.get("model", "")).strip()
            if not provider:
                self._send_json({"error": "provider is required"}, status=400)
                return

            os.environ["AVO_PROVIDER"] = provider
            if model:
                os.environ["AVO_MODEL"] = model
            current_model = model or os.environ.get("AVO_MODEL", "")
            self._send_json({"ok": True, "provider": provider, "model": current_model})
            return

        if path == "/api/workspace/file":
            content_len = int(self.headers.get("Content-Length", 0))
            if content_len <= 0:
                self._send_json({"error": "empty body"}, status=400)
                return
            try:
                data = json.loads(self.rfile.read(content_len).decode("utf-8"))
            except Exception:
                self._send_json({"error": "invalid JSON body"}, status=400)
                return

            file_path = str(data.get("path", "")).strip()
            content = data.get("content")
            if not file_path:
                self._send_json({"error": "Field 'path' is required"}, status=400)
                return
            if content is None or not isinstance(content, str):
                self._send_json({"error": "Field 'content' must be a string"}, status=400)
                return

            try:
                saved_result = self.server.save_sync_workspace_file(file_path, content)
                self._send_json(saved_result)
            except Exception as exc:
                self._send_json({"error": str(exc)}, status=400)
            return

        self._send_json({"error": "Not Found"}, status=404)


class AvoWebServer(ThreadingHTTPServer):
    """Custom ThreadingHTTPServer holding Avo database connections."""

    def __init__(
        self,
        server_address: tuple[str, int],
        database_path: Path,
        persona_manager: PersonaManager | None = None,
        permission_mode: str | None = None,
        workspace_root: Path | None = None,
    ) -> None:
        super().__init__(server_address, AvoWebHandler)
        self.database_path = database_path
        self.workspace_root = (
            Path(workspace_root).resolve() if workspace_root is not None else Path.cwd().resolve()
        )
        self.persona_manager = (
            persona_manager if persona_manager is not None else PersonaManager(database_path.parent)
        )
        self.permission_mode = (
            permission_mode
            if permission_mode is not None
            else os.environ.get("AVO_PERMISSION_MODE", "bypass")
        )

    def get_sync_git_status(self) -> dict[str, Any]:
        """Query repository status, branches, diff, and recent commits synchronously."""
        from avo.workspace.git import GitRepository

        repo = GitRepository(self.workspace_root)
        if not repo.is_repository():
            return {
                "is_repo": False,
                "workspace": str(self.workspace_root),
                "branch": None,
                "is_clean": True,
                "entries": [],
                "modified": [],
                "untracked": [],
                "branches": [],
                "stashes": [],
                "diff": "",
                "recent_commits": [],
            }
        try:
            status = repo.status()
            branches = repo.list_branches()
            diff_text = repo.diff()
            staged_diff = repo.diff(staged=True)
            full_diff = (staged_diff + "\n" + diff_text).strip() if staged_diff else diff_text
            commits = repo.log(max_count=10)
            stashes = repo.stash_list()
            entries = [
                {
                    "path": e.path,
                    "status_code": e.status_code,
                    "status": (
                        "untracked"
                        if e.is_untracked
                        else ("modified" if e.is_modified else "changed")
                    ),
                }
                for e in status.entries
            ]
            return {
                "is_repo": True,
                "workspace": str(self.workspace_root),
                "branch": status.branch,
                "is_clean": status.is_clean,
                "entries": entries,
                "modified": list(status.modified),
                "untracked": list(status.untracked),
                "branches": branches,
                "stashes": stashes,
                "diff": full_diff,
                "recent_commits": commits,
            }
        except Exception as exc:
            _LOG.warning("Could not read git status: %s", exc)
            return {
                "is_repo": True,
                "workspace": str(self.workspace_root),
                "error": str(exc),
                "branch": None,
                "is_clean": True,
                "entries": [],
                "modified": [],
                "untracked": [],
                "branches": [],
                "stashes": [],
                "diff": "",
                "recent_commits": [],
            }

    def get_sync_router_status(self) -> dict[str, Any]:
        """Query router routes, health status, and fallback chains."""
        from avo.config import build_provider_from_env
        from avo.providers.router import BaseRouterProvider

        provider_name = os.environ.get("AVO_PROVIDER", "ollama")
        model_name = os.environ.get("AVO_MODEL", "default")
        router_chain = os.environ.get("AVO_ROUTER_CHAIN", "")
        router_providers = os.environ.get("AVO_ROUTER_PROVIDERS", "")
        router_models = os.environ.get("AVO_ROUTER_MODELS", "")

        routes_info: list[dict[str, Any]] = []
        is_router = False
        health_status: dict[str, Any] = {}

        try:
            prov = build_provider_from_env(dict(os.environ))
            if isinstance(prov, BaseRouterProvider):
                is_router = True
                health_status = prov.get_health_status()
                for name, p in prov.routes:
                    h = health_status.get(name, {})
                    routes_info.append(
                        {
                            "name": name,
                            "provider": getattr(p, "name", name),
                            "model": getattr(p, "model", "default"),
                            "healthy": h.get("healthy", True),
                            "in_cooldown": h.get("in_cooldown", False),
                            "cooldown_remaining_seconds": h.get("cooldown_remaining_seconds", 0.0),
                            "consecutive_failures": h.get("consecutive_failures", 0),
                            "last_error": h.get("last_error"),
                            "last_latency_ms": h.get("last_latency_ms"),
                        }
                    )
            else:
                routes_info.append(
                    {
                        "name": provider_name,
                        "provider": getattr(prov, "name", provider_name),
                        "model": getattr(prov, "model", model_name),
                        "healthy": True,
                        "in_cooldown": False,
                        "cooldown_remaining_seconds": 0.0,
                        "consecutive_failures": 0,
                        "last_error": None,
                        "last_latency_ms": None,
                    }
                )
        except Exception as exc:
            _LOG.warning("Could not build provider for router status: %s", exc)

        return {
            "is_router": is_router,
            "active_provider": provider_name,
            "active_model": model_name,
            "router_chain": router_chain,
            "router_providers": router_providers,
            "router_models": router_models,
            "routes": routes_info,
            "health": health_status,
        }

    def sync_probe_router(self) -> dict[str, Any]:
        """Run health probe on router endpoints synchronously."""
        import asyncio

        from avo.config import build_provider_from_env
        from avo.providers.router import BaseRouterProvider

        try:
            prov = build_provider_from_env(dict(os.environ))
            if isinstance(prov, BaseRouterProvider):
                outcomes = asyncio.run(prov.probe_all())
                return {"ok": True, "outcomes": outcomes, "health": prov.get_health_status()}
            active_p = os.environ.get("AVO_PROVIDER", "ollama")
            return {"ok": True, "outcomes": {active_p: True}, "health": {}}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def sync_bench_routes(self, prompt: str = "Explain recursion in 10 words.") -> dict[str, Any]:
        """Run speed and latency benchmark on configured routes."""
        import asyncio

        from avo.bench import benchmark_all_routes, benchmark_route
        from avo.config import build_provider_from_env
        from avo.providers.router import BaseRouterProvider

        try:
            prov = build_provider_from_env(dict(os.environ))
            if isinstance(prov, BaseRouterProvider):
                results = asyncio.run(benchmark_all_routes(prov.routes, prompt=prompt))
                return {
                    "ok": True,
                    "prompt": prompt,
                    "results": [r.as_dict() for r in results],
                }
            active_p = os.environ.get("AVO_PROVIDER", "ollama")
            res = asyncio.run(benchmark_route(active_p, prov, prompt=prompt))
            return {
                "ok": True,
                "prompt": prompt,
                "results": [res.as_dict()],
            }
        except Exception as exc:
            return {"ok": False, "error": str(exc), "results": []}

    def get_sync_workspace_tree(
        self, subpath: str = "", max_depth: int = 8, max_entries: int = 1500
    ) -> dict[str, Any]:
        """Scan the workspace and return a structured file tree."""
        from avo.app_tools.workspace import Workspace

        ws = Workspace(self.workspace_root)
        target_dir = ws.validate_path(subpath, must_exist=True) if subpath else self.workspace_root
        if not target_dir.is_dir():
            raise ValueError(f"Path is not a directory: {subpath}")

        ignored_dir_names = {
            ".git",
            "__pycache__",
            ".venv",
            "venv",
            "node_modules",
            ".mypy_cache",
            ".pytest_cache",
            ".ruff_cache",
            ".avo",
            ".eggs",
            "dist",
            "build",
            ".tox",
        }

        entry_count = 0

        def _scan_dir(current: Path, depth: int) -> list[dict[str, Any]]:
            nonlocal entry_count
            if depth > max_depth or entry_count >= max_entries:
                return []

            nodes: list[dict[str, Any]] = []
            try:
                entries = list(os.scandir(current))
            except OSError:
                return []

            entries.sort(key=lambda e: (not e.is_dir(follow_symlinks=False), e.name.lower()))

            for entry in entries:
                if entry_count >= max_entries:
                    break
                name = entry.name
                if (
                    name.startswith(".") and name in ignored_dir_names
                ) or name in ignored_dir_names:
                    continue
                if name.endswith(".egg-info") or name.endswith(".pyc"):
                    continue

                entry_path = Path(entry.path)
                try:
                    rel = entry_path.resolve().relative_to(self.workspace_root)
                except ValueError:
                    continue

                if entry.is_dir(follow_symlinks=False):
                    entry_count += 1
                    child_nodes = _scan_dir(entry_path, depth + 1)
                    nodes.append(
                        {
                            "name": name,
                            "path": rel.as_posix(),
                            "type": "directory",
                            "children": child_nodes,
                        }
                    )
                elif entry.is_file(follow_symlinks=False):
                    entry_count += 1
                    try:
                        stat = entry.stat()
                        size = stat.st_size
                        mtime = int(stat.st_mtime)
                    except OSError:
                        size = 0
                        mtime = 0
                    nodes.append(
                        {
                            "name": name,
                            "path": rel.as_posix(),
                            "type": "file",
                            "size": size,
                            "mtime": mtime,
                        }
                    )
            return nodes

        tree = _scan_dir(target_dir, 1)
        rel_root = ""
        with contextlib.suppress(ValueError):
            rel_root = target_dir.relative_to(self.workspace_root).as_posix()

        return {
            "root": str(self.workspace_root),
            "workspace_name": self.workspace_root.name,
            "subpath": rel_root,
            "tree": tree,
            "total_entries": entry_count,
            "truncated": entry_count >= max_entries,
        }

    def get_sync_workspace_file(self, file_path_str: str) -> dict[str, Any]:
        """Read a file from workspace safely and return metadata and text content."""
        from avo.app_tools.workspace import Workspace

        ws = Workspace(self.workspace_root)
        resolved = ws.validate_path(file_path_str, must_exist=True)
        if resolved.is_dir():
            raise ValueError(f"Path is a directory, not a file: {file_path_str}")

        stat = resolved.stat()
        if stat.st_size > 2 * 1024 * 1024:
            raise ValueError(f"File exceeds maximum viewable size of 2MB ({stat.st_size} bytes)")

        raw_bytes = resolved.read_bytes()
        if b"\x00" in raw_bytes[:4096]:
            raise ValueError("Binary files cannot be displayed or edited")

        try:
            content = raw_bytes.decode("utf-8")
        except UnicodeDecodeError:
            content = raw_bytes.decode("latin-1")

        rel_path = resolved.relative_to(self.workspace_root).as_posix()
        return {
            "ok": True,
            "path": rel_path,
            "filename": resolved.name,
            "content": content,
            "size": stat.st_size,
            "mtime": int(stat.st_mtime),
            "line_count": len(content.splitlines()),
        }

    def save_sync_workspace_file(self, file_path_str: str, content: str) -> dict[str, Any]:
        """Save text content to a file inside the workspace safely."""
        from avo.app_tools.workspace import Workspace, WorkspacePathError

        if "\x00" in file_path_str:
            raise WorkspacePathError("path contains a null byte")
        if not file_path_str.strip():
            raise WorkspacePathError("path is empty")

        candidate_path = Path(file_path_str)
        base = self.workspace_root if not candidate_path.is_absolute() else None
        target = (
            (base / candidate_path).resolve(strict=False)
            if base
            else candidate_path.resolve(strict=False)
        )
        try:
            target.relative_to(self.workspace_root)
        except ValueError as exc:
            raise WorkspacePathError(f"path escapes workspace root: {file_path_str}") from exc

        target.parent.mkdir(parents=True, exist_ok=True)

        ws = Workspace(self.workspace_root)
        resolved = ws.validate_for_write(file_path_str)
        if resolved.is_dir():
            raise ValueError(f"Cannot overwrite directory with file: {file_path_str}")

        resolved.write_text(content, encoding="utf-8")
        stat = resolved.stat()
        rel_path = resolved.relative_to(self.workspace_root).as_posix()

        return {
            "ok": True,
            "path": rel_path,
            "filename": resolved.name,
            "size": stat.st_size,
            "mtime": int(stat.st_mtime),
            "line_count": len(content.splitlines()),
            "message": f"Saved {rel_path}",
        }

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


def run_web_dashboard(
    *,
    port: int = 43111,
    database_path: Path | None = None,
    workspace_root: Path | None = None,
    open_browser: bool = True,
    output_writer: Any = sys.stdout.write,
) -> int:
    """Start the local Web UI server and optionally open the browser."""
    db_path = database_path if database_path is not None else Path("avo.db")
    ws_root = workspace_root if workspace_root is not None else Path.cwd()
    server = AvoWebServer(
        ("127.0.0.1", port),
        database_path=db_path,
        workspace_root=ws_root,
    )
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


__all__ = ["AvoWebHandler", "AvoWebServer", "main", "run_web_dashboard"]
