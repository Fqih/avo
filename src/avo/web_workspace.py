"""Workspace and git endpoints for the Avo Web UI.

Holds the ``/api/git*`` and ``/api/workspace/*`` routes plus the
:class:`avo.web_ui.AvoWebServer` helpers they call (git status, workspace
tree scan, safe file read and write).

Re-exported from :mod:`avo.web_ui` for backward compatibility.
"""

from __future__ import annotations

import contextlib
import json
import os
import urllib.parse
from pathlib import Path
from typing import TYPE_CHECKING, Any

from avo.web_http import _LOG, WebHttpMixin


class WebWorkspaceMixin(WebHttpMixin):
    """Git/workspace routes for :class:`avo.web_ui.AvoWebHandler`."""

    def _route_workspace_get(self, parsed: urllib.parse.ParseResult, path: str) -> bool:
        """Handle the git and workspace-tree/file GET routes."""
        if path == "/api/git":
            self._send_json(self.server.get_sync_git_status())
            return True

        if path == "/api/git/stash":
            from avo.workspace.git import GitRepository

            repo = GitRepository(self.server.workspace_root)
            if not repo.is_repository():
                self._send_json({"error": "Not a git repository"}, status=400)
                return True
            self._send_json({"stashes": repo.stash_list()})
            return True

        if path.startswith("/api/git/commit/"):
            from avo.workspace.git import GitError, GitRepository

            commit_hash = path.split("/api/git/commit/", 1)[1].strip()
            if not commit_hash:
                self._send_json({"error": "commit hash is required"}, status=400)
                return True
            repo = GitRepository(self.server.workspace_root)
            if not repo.is_repository():
                self._send_json({"error": "Not a git repository"}, status=400)
                return True
            try:
                commit_data = repo.commit_show(commit_hash)
                self._send_json(commit_data)
            except GitError as exc:
                self._send_json({"error": str(exc)}, status=404)
            return True

        if path == "/api/workspace/tree":
            if not self._authenticate_read():
                return True
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
            return True

        if path == "/api/workspace/file":
            if not self._authenticate_read():
                return True
            file_params = urllib.parse.parse_qs(parsed.query)
            file_path = file_params.get("path", [""])[0].strip()
            if not file_path:
                self._send_json({"error": "Query parameter 'path' is required"}, status=400)
                return True
            try:
                file_info = self.server.get_sync_workspace_file(file_path)
                self._send_json(file_info)
            except Exception as exc:
                self._send_json({"error": str(exc)}, status=400)
            return True

        return False

    def _route_workspace_post(self, parsed: urllib.parse.ParseResult, path: str) -> bool:
        """Handle the git commit/branch/stash and workspace-file-save POST routes."""
        if path == "/api/git/commit":
            from avo.workspace.git import GitRepository, generate_commit_message_heuristic

            content_len = int(self.headers.get("Content-Length", 0))
            body_dict: dict[str, Any] = {}
            if content_len > 0:
                with contextlib.suppress(Exception):
                    body_dict = json.loads(self.rfile.read(content_len).decode("utf-8"))

            if not self._require_confirmation(body_dict):
                return True
            repo = GitRepository(self.server.workspace_root)
            if not repo.is_repository():
                self._send_json({"ok": False, "error": "Not a git repository"}, status=400)
                return True

            status = repo.status()
            if status.is_clean:
                self._send_json(
                    {"ok": False, "error": "Working tree clean, nothing to commit"}, status=400
                )
                return True

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
                return True

            self._send_json(
                {
                    "ok": True,
                    "message": commit_msg,
                    "commit_hash": commit_hash,
                }
            )
            return True

        if path == "/api/git/branch":
            from avo.workspace.git import GitError, GitRepository

            content_len = int(self.headers.get("Content-Length", 0))
            if content_len <= 0:
                self._send_json({"error": "empty body"}, status=400)
                return True
            try:
                data = json.loads(self.rfile.read(content_len).decode("utf-8"))
            except Exception:
                self._send_json({"error": "invalid JSON body"}, status=400)
                return True

            if not self._require_confirmation(data):
                return True
            branch_name = str(data.get("branch", "")).strip()
            create = bool(data.get("create", False))
            if not branch_name:
                self._send_json({"ok": False, "error": "Branch name is required"}, status=400)
                return True

            repo = GitRepository(self.server.workspace_root)
            if not repo.is_repository():
                self._send_json({"ok": False, "error": "Not a git repository"}, status=400)
                return True

            try:
                repo.switch_branch(branch_name, create=create)
                self._send_json({"ok": True, "branch": branch_name, "created": create})
            except GitError as exc:
                self._send_json({"ok": False, "error": str(exc)}, status=400)
            return True

        if path == "/api/git/stash":
            from avo.workspace.git import GitError, GitRepository

            content_len = int(self.headers.get("Content-Length", 0))
            stash_payload: dict[str, Any] = {}
            if content_len > 0:
                with contextlib.suppress(Exception):
                    loaded = json.loads(self.rfile.read(content_len).decode("utf-8"))
                    if isinstance(loaded, dict):
                        stash_payload = loaded

            action = str(stash_payload.get("action", "list")).strip().lower()
            if action != "list" and not self._require_confirmation(stash_payload):
                return True
            repo = GitRepository(self.server.workspace_root)
            if not repo.is_repository():
                self._send_json({"ok": False, "error": "Not a git repository"}, status=400)
                return True
            try:
                if action in ("save", "push"):
                    stash_msg = stash_payload.get("message")
                    save_res = repo.stash_save(str(stash_msg) if stash_msg else None)
                    self._send_json({"ok": True, "result": save_res, "stashes": repo.stash_list()})
                    return True
                if action in ("pop", "apply"):
                    idx = int(stash_payload.get("index", 0))
                    repo.stash_pop(idx)
                    self._send_json({"ok": True, "stashes": repo.stash_list()})
                    return True
                if action in ("drop", "delete"):
                    idx = int(stash_payload.get("index", 0))
                    repo.stash_drop(idx)
                    self._send_json({"ok": True, "stashes": repo.stash_list()})
                    return True
                self._send_json({"ok": True, "stashes": repo.stash_list()})
                return True
            except GitError as exc:
                self._send_json({"ok": False, "error": str(exc)}, status=400)
                return True

        if path == "/api/workspace/file":
            content_len = int(self.headers.get("Content-Length", 0))
            if content_len <= 0:
                self._send_json({"error": "empty body"}, status=400)
                return True
            try:
                data = json.loads(self.rfile.read(content_len).decode("utf-8"))
            except Exception:
                self._send_json({"error": "invalid JSON body"}, status=400)
                return True

            if not self._require_confirmation(data):
                return True
            file_path = str(data.get("path", "")).strip()
            content = data.get("content")
            if not file_path:
                self._send_json({"error": "Field 'path' is required"}, status=400)
                return True
            if content is None or not isinstance(content, str):
                self._send_json({"error": "Field 'content' must be a string"}, status=400)
                return True

            try:
                saved_result = self.server.save_sync_workspace_file(file_path, content)
                self._send_json(saved_result)
            except Exception as exc:
                self._send_json({"error": str(exc)}, status=400)
            return True

        return False


class WorkspaceServerMixin:
    """Git and workspace queries for :class:`avo.web_ui.AvoWebServer`."""

    if TYPE_CHECKING:
        workspace_root: Path

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
        from avo.workspace_write import save_workspace_file

        return save_workspace_file(self.workspace_root, file_path_str, content)


__all__ = ["WebWorkspaceMixin", "WorkspaceServerMixin"]
