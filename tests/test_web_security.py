"""Exercise the real HTTP handler without requiring a listening socket."""

from __future__ import annotations

import io
import json
import os
import re
import shutil
import subprocess
from http.client import HTTPResponse
from http.server import ThreadingHTTPServer

import pytest

from avo.app_tools.workspace import WorkspacePathError
from avo.web_ui import AvoWebHandler, AvoWebServer, run_web_dashboard


class MemorySocket:
    def __init__(self, data: bytes = b"") -> None:
        self.input = io.BytesIO(data)
        self.output = io.BytesIO()

    def makefile(self, *args, **kwargs):
        return self.input

    def sendall(self, data: bytes) -> None:
        self.output.write(data)


@pytest.fixture
def server(tmp_path, monkeypatch):
    # Only socket allocation is replaced; server initialization and HTTP parsing run normally.
    monkeypatch.setattr(ThreadingHTTPServer, "__init__", lambda *args, **kwargs: None)
    monkeypatch.delenv("AVO_PERMISSION_MODE", raising=False)
    monkeypatch.setenv("AVO_PROVIDER", "ollama")
    srv = AvoWebServer(("127.0.0.1", 43111), tmp_path / "avo.db", workspace_root=tmp_path)
    srv.server_address = ("127.0.0.1", 43111)
    srv.server_port = 43111
    return srv


def request(server, method="POST", path="/api/persona", data=None, headers=None):
    body = json.dumps(data if data is not None else {"persona": "coder"}).encode()
    fields = {"Host": "127.0.0.1:43111", "Content-Length": str(len(body)), **(headers or {})}
    raw = f"{method} {path} HTTP/1.1\r\n"
    raw += "".join(f"{k}: {v}\r\n" for k, v in fields.items()) + "\r\n"
    sock = MemorySocket(raw.encode() + body)
    AvoWebHandler(sock, ("127.0.0.1", 12345), server)
    response = HTTPResponse(MemorySocket(sock.output.getvalue()))
    response.begin()
    return response


def bearer(server):
    return {"Authorization": f"Bearer {server.auth_token}"}


@pytest.mark.parametrize(
    "path",
    [
        "/api/persona",
        "/api/chat",
        "/api/router/probe",
        "/api/permissions",
        "/api/workspace/file",
        "/api/git/stash",
    ],
)
def test_unauthenticated_post_is_rejected(server, path):
    response = request(server, path=path)
    assert response.status == 401
    assert server.persona_manager.active_persona is None


def test_bearer_authenticates_mutation_and_token_is_not_in_reads(server):
    response = request(server, headers=bearer(server))
    assert response.status == 200
    assert server.persona_manager.active_persona == "coder"
    for path in ("/", "/api/permissions"):
        response = request(server, "GET", path)
        assert server.auth_token.encode() not in response.read()
        assert response.getheader("Set-Cookie") is None


def test_server_tokens_rotate_and_permission_defaults_to_default(server, tmp_path):
    other = AvoWebServer(("127.0.0.1", 43111), tmp_path / "second.db")
    assert len(server.auth_token) >= 32
    assert other.auth_token != server.auth_token
    response = request(server, "GET", "/api/permissions")
    assert json.load(response)["mode"] == "default"


@pytest.mark.parametrize("authorization", ["Bearer wrong", "Basic credentials", "Bearer é"])
def test_invalid_bearer_is_rejected(server, authorization):
    assert request(server, headers={"Authorization": authorization}).status == 401


@pytest.mark.parametrize(
    "origin",
    [
        "https://evil.test",
        "null",
        "http://localhost:43111",
        "http://127.0.0.1:43112",
        "http://127.0.0.1:43111/path",
    ],
)
def test_foreign_or_malformed_origin_blocks_bearer_mutation(server, origin):
    response = request(server, headers={**bearer(server), "Origin": origin})
    assert response.status == 403
    assert server.persona_manager.active_persona is None


def test_rebinding_host_is_rejected(server):
    response = request(server, "GET", "/", headers={"Host": "evil.test:43111"})
    assert response.status == 403


@pytest.mark.parametrize("method", ["GET", "OPTIONS"])
def test_same_origin_responses_do_not_enable_cors(server, method):
    response = request(
        server, method, "/api/permissions", headers={"Origin": "http://127.0.0.1:43111"}
    )
    assert response.status == (204 if method == "OPTIONS" else 200)
    assert response.getheader("Access-Control-Allow-Origin") is None
    assert response.getheader("Access-Control-Allow-Credentials") is None


def test_cross_origin_options_is_rejected_without_cors(server):
    response = request(server, "OPTIONS", headers={"Origin": "https://evil.test"})
    assert response.status == 403
    assert response.getheader("Access-Control-Allow-Origin") is None


def test_session_cookie_requires_same_origin_and_csrf(server):
    response = request(server, path="/api/session", headers=bearer(server))
    assert response.status == 200
    cookie = response.getheader("Set-Cookie")
    assert "HttpOnly" in cookie and "SameSite=Strict" in cookie
    assert "Domain=" not in cookie
    csrf = json.load(response)["csrf_token"]
    headers = {"Cookie": cookie.split(";", 1)[0]}
    assert request(server, headers=headers).status == 401
    headers["Origin"] = "http://127.0.0.1:43111"
    assert request(server, headers=headers).status == 401
    headers["X-CSRF-Token"] = csrf
    assert request(server, headers=headers).status == 200
    headers["Origin"] = "https://evil.test"
    assert request(server, headers=headers).status == 403


@pytest.mark.parametrize(
    "path,data",
    [
        ("/api/permissions", {"mode": "bypass"}),
        ("/api/provider", {"provider": "fake"}),
        ("/api/workspace/file", {"path": "new.txt", "content": "new"}),
        ("/api/git/commit", {"message": "commit"}),
        ("/api/git/branch", {"branch": "other"}),
        ("/api/git/stash", {"action": "drop"}),
    ],
)
@pytest.mark.parametrize("confirmation", [None, False, "true", 1])
def test_mutations_require_boolean_confirmation(server, path, data, confirmation):
    response = request(
        server, path=path, data={**data, "confirm": confirmation}, headers=bearer(server)
    )
    assert response.status == 400
    assert "confirm" in json.load(response)["error"]
    assert server.permission_mode == "default"
    assert os.environ["AVO_PROVIDER"] == "ollama"
    assert not (server.workspace_root / "new.txt").exists()


def test_confirmed_workspace_mutation_succeeds(server):
    response = request(
        server,
        path="/api/workspace/file",
        headers=bearer(server),
        data={"path": "nested/new.txt", "content": "new", "confirm": True},
    )
    assert response.status == 200
    assert (server.workspace_root / "nested/new.txt").read_text() == "new"


def test_atomic_save_preserves_old_file_until_replace(server, monkeypatch):
    target = server.workspace_root / "script.sh"
    target.write_text("old")
    target.chmod(0o755)
    original_replace = os.replace
    replacements = []

    def observe_replace(src, dst, **kwargs):
        assert target.read_text() == "old"
        replacements.append((src, dst))
        return original_replace(src, dst, **kwargs)

    monkeypatch.setattr(os, "replace", observe_replace)
    server.save_sync_workspace_file("script.sh", "new")
    assert len(replacements) == 1
    assert target.read_text() == "new"
    assert target.stat().st_mode & 0o777 == 0o755


def test_failed_replace_keeps_original_and_removes_temporary_file(server, monkeypatch):
    target = server.workspace_root / "file.txt"
    target.write_text("old")
    before = set(server.workspace_root.iterdir())

    def fail_replace(*args, **kwargs):
        raise OSError("replacement failed")

    monkeypatch.setattr(os, "replace", fail_replace)
    with pytest.raises(OSError, match="replacement failed"):
        server.save_sync_workspace_file("file.txt", "new")
    assert target.read_text() == "old"
    assert set(server.workspace_root.iterdir()) == before


@pytest.mark.parametrize("path", ["../outside.txt", "link/file.txt", "leaf.txt"])
def test_workspace_save_rejects_escape_and_symlinks(server, tmp_path, path):
    outside = tmp_path.parent / f"{tmp_path.name}-outside"
    outside.mkdir()
    (server.workspace_root / "link").symlink_to(outside, target_is_directory=True)
    (server.workspace_root / "leaf.txt").symlink_to(outside / "file.txt")
    with pytest.raises(WorkspacePathError):
        server.save_sync_workspace_file(path, "escaped")
    assert not (outside / "file.txt").exists()


def test_launch_url_delivers_token_only_in_fragment(server, monkeypatch):
    monkeypatch.setattr("avo.web_ui.AvoWebServer", lambda *args, **kwargs: server)
    monkeypatch.setattr(server, "serve_forever", lambda: None)
    monkeypatch.setattr(server, "server_close", lambda: None)
    opened = []
    monkeypatch.setattr("webbrowser.open", opened.append)
    output = []
    assert run_web_dashboard(output_writer=output.append) == 0
    assert opened == [f"http://localhost:43111/#token={server.auth_token}"]
    assert opened[0] in "".join(output)


def test_dashboard_bootstrap_and_confirmation_behavior(server):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is needed to exercise the dashboard session bootstrap")
    html = request(server, "GET", "/").read().decode()
    script = re.search(r"<script data-avo-session>(.*?)</script>", html, re.S)
    assert script, "dashboard must load its session bootstrap before other scripts"
    harness = r"""
const assert = require('node:assert/strict');
const vm = require('node:vm');
const calls = [];
let allow = false;
let cleared = false;
const saved = new Map();
global.window = global;
global.location = {href: 'http://localhost:43111/#token=launch-secret',
  origin: 'http://localhost:43111', hash: '#token=launch-secret', pathname: '/', search: ''};
global.history = {replaceState: (state, title, url) => {
  assert.equal(url, '/'); cleared = true;
}};
global.sessionStorage = {getItem: k => saved.get(k), setItem: (k,v) => saved.set(k,v)};
global.confirm = () => allow;
global.fetch = async (input, options = {}) => {
  calls.push({input, options});
  return {ok: true, json: async () => ({csrf_token: 'csrf-secret'})};
};
vm.runInThisContext(SCRIPT);
(async () => {
  await fetch('/api/persona', {method: 'POST', body: JSON.stringify({persona: 'coder'})});
  assert.ok(cleared);
  assert.equal(calls[0].input, '/api/session');
  assert.equal(new Headers(calls[0].options.headers).get('Authorization'), 'Bearer launch-secret');
  assert.equal(calls[1].options.headers.get('X-CSRF-Token'), 'csrf-secret');
  assert.equal(calls[1].options.credentials, 'same-origin');
  const body = JSON.stringify({path: 'file.txt', content: 'new'});
  await assert.rejects(fetch('/api/workspace/file', {method: 'POST', body}));
  assert.equal(calls.length, 2);
  allow = true;
  await fetch('/api/workspace/file', {method: 'POST', body});
  assert.equal(JSON.parse(calls[2].options.body).confirm, true);
  await fetch('https://example.org', {method: 'POST', body: '{}'});
  assert.equal(new Headers(calls[3].options.headers).get('X-CSRF-Token'), null);
})().catch(error => {console.error(error); process.exitCode = 1;});
"""
    result = subprocess.run(
        [node, "-e", "const SCRIPT = " + json.dumps(script.group(1)) + ";\n" + harness],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
