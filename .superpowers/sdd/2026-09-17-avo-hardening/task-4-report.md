# Task 4 report: Authenticated web control plane and safe workspace writes

## Status and scope

Reviewed and retained the existing Task 4 implementation and tests. No additional production changes were made in this fresh implementer pass. Only the five web implementation files, the two web test files, and this report are included in the commit `fix: harden local web control plane`, authored by `Fqih <mhmdfkih21@gmail.com>`. Unrelated worktree changes are preserved. No subagents, resets, or pushes were used.

## Implemented behavior

- Every POST is authenticated before routing: a per-server bearer secret or a session cookie with an exact same-origin Origin and CSRF token. Invalid credentials return 401; foreign origins and unrecognized hosts return 403.
- Host validation restricts requests to loopback names at the bound port; duplicate Host/Origin headers and cross-site fetch metadata are rejected. JSON, SSE, and OPTIONS responses no longer enable wildcard CORS.
- The actual `avo ui` CLI dispatches to `web_ui.main`, which calls `run_web_dashboard`. The launcher prints and optionally opens `http://localhost:<bound-port>/#token=<per-process-secret>`. This supplies the token through the local operator flow, including when automatic browser opening is disabled.
- The dashboard bootstrap executes before its other scripts, removes the fragment from history, exchanges the bearer token at POST `/api/session`, receives an HttpOnly, SameSite=Strict cookie and CSRF token, then attaches CSRF to subsequent same-origin POSTs. The bearer secret is not embedded in unauthenticated HTML or API responses.
- Permission mode defaults to `default`. Permission/provider changes, workspace saves, commits, branch changes, and mutating stash operations require literal JSON `confirm: true`; the browser asks for confirmation.
- Workspace writes reject traversal and symlink components, pin parent directories using directory descriptors, write and fsync a same-directory temporary file, and publish with `os.replace`. Existing file mode is retained. A failed replacement preserves the original and removes the temporary file.

## Focused verification

Final commands and results:

```text
.venv/bin/python -m pytest tests/test_web_security.py -q
53 passed in 0.23s (exit 0)

.venv/bin/ruff check src/avo/web_http.py src/avo/web_ui.py src/avo/web_api.py src/avo/web_workspace.py src/avo/web_playground.py tests/test_web_ui.py tests/test_web_security.py
All checks passed! (exit 0)

.venv/bin/ruff format --check src/avo/web_http.py src/avo/web_ui.py src/avo/web_api.py src/avo/web_workspace.py src/avo/web_playground.py tests/test_web_ui.py tests/test_web_security.py
7 files already formatted (exit 0)

git diff --check
exit 0
```

The non-socket suite exercises the real HTTP parser/handler through in-memory transport, real workspace files, the launch URL, and the emitted bootstrap JavaScript in Node. The JavaScript test verifies exchange ordering, bearer delivery, CSRF attachment, cancellation/confirmation, and no CSRF attachment to foreign-origin requests. Its fetch boundary is simulated; this is not a full browser integration run.

Socket attempt:

```text
.venv/bin/python -m pytest tests/test_web_ui.py -q
6 failed, 1 passed, 11 errors in 0.83s (exit 1)
```

All 17 socket-dependent cases were blocked by `PermissionError: [Errno 1] Operation not permitted` during socket creation. These results are environment failures, not evidence of product failures. The required elevated retry was requested but interrupted by the user; it produced no test result. Following the user's instruction to stop after focused verification, no further socket or broad test runs were attempted.

## Limits and remaining verification

- Socket integration and a real browser session remain unverified in this environment.
- The intended bootstrap requires opening the launch URL from the current process. A bare URL in a fresh tab does not grant mutation access. The implementation assumes sessionStorage is available and has no explicit retry or user-facing recovery for a failed session exchange; reopen the printed launch URL to retry. The tested normal bootstrap path passes.
- The descriptor-based write implementation relies on POSIX `O_DIRECTORY`, `O_NOFOLLOW`, and `dir_fd`; Windows compatibility was not verified.
- The implementation and tests predated this pass. This pass observed green focused tests, not the original TDD red phase, and does not claim otherwise.
- Full repository tests/type checking were not run, as the final user instruction limited work to focused verification and report/commit.

`gitleaks git --pre-commit --staged --redact --no-banner` scanned the staged Task 4 diff: no leaks found (exit 0). `git diff --cached --check` also passed. Staging required elevation because the sandbox mounts `.git` read-only.

## Fix round 1

Reviewed `review-task-4.diff`, the findings recorded in `progress.md`, and the existing Task 4 worktree changes. Retained the shared atomic workspace writer, confirmation for persona registration, persistence before updating persona memory, canonical permission API/UI values, database resolver integration, port-specific session cookie names, SSE CORS removal, and literal rendering of filenames and traces. Fixed startup permission normalization by consuming the existing permission parser. Added regression cases for environment/argument legacy aliases and a real headless Chrome XSS check. Ruff formatted two files.

Only Task 4 files are staged, including only the atomic-persistence hunk of `persona.py`. Its unrelated global persona/instruction loading changes remain unstaged (Ruff also normalized their formatting). Other unrelated WIP is preserved. No broad suites, subagents, resets, or pushes were used.

Exact verification commands and results:

```text
.venv/bin/python -m pytest tests/test_web_security.py tests/test_web_dashboard_security.py -q
Initial existing suite: 92 passed in 0.47s (exit 0).
After adding regressions: 3 failed, 92 passed in 1.00s (exit 1).
Two failures reproduced noncanonical startup permission values; Chrome failed
with setsockopt: Operation not permitted under the sandbox. The first elevated
retry was interrupted and produced no result.
Final elevated run after the permission fix: 95 passed in 1.40s (exit 0).
No skips; the Chrome filename-click and trace/JSON XSS checks ran successfully.

.venv/bin/ruff format src/avo/workspace_write.py src/avo/persona.py src/avo/web_api.py src/avo/web_http.py src/avo/web_runs.py src/avo/web_ui.py src/avo/web_workspace.py tests/test_web_security.py tests/test_web_dashboard_security.py
2 files reformatted, 7 files left unchanged (exit 0).

.venv/bin/ruff check src/avo/workspace_write.py src/avo/persona.py src/avo/web_api.py src/avo/web_http.py src/avo/web_runs.py src/avo/web_ui.py src/avo/web_workspace.py tests/test_web_security.py tests/test_web_dashboard_security.py
All checks passed! (exit 0).

.venv/bin/ruff format --check src/avo/workspace_write.py src/avo/persona.py src/avo/web_api.py src/avo/web_http.py src/avo/web_runs.py src/avo/web_ui.py src/avo/web_workspace.py tests/test_web_security.py tests/test_web_dashboard_security.py
9 files already formatted (exit 0).

git diff --check
No output (exit 0).

gitleaks git --pre-commit --staged --redact --no-banner
No leaks found (exit 0), after staging the Task 4 implementation and tests.

git diff --cached --check
No output (exit 0).
```

Verification limits: Windows fallback tests exercise real files with simulated Win32 handles on Linux; this is not native Windows validation. Chrome runs the actual dashboard DOM and inline JavaScript with local test data and no external scripts; this does not claim full browser/server integration. The retained POSIX directory descriptors prevent symlink redirection, but cannot prevent an independently authorized local process from moving an already-open directory outside the workspace. That stronger concurrent-rename containment guarantee remains outside this fix; no redesign was performed.

Commit message: `fix: close web security boundary gaps`; author: `Fqih <mhmdfkih21@gmail.com>`.
