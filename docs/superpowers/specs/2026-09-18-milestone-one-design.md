# Milestone One: Provider Catalog, Credentials, and Attachments

**Status:** Proposed
**Date:** 2026-09-18

## Goal

Make provider/model selection account-aware and dynamic where the vendor
supports discovery, store credentials through a safer backend, complete the
Ollama Cloud path, and let users attach images or files from the terminal
prompt by paste or drag-and-drop.

## Scope

This milestone covers:

- one provider-neutral model catalog interface;
- live and cached model discovery for supported providers;
- keyboard model selection in `/model` with clear stale-catalog behavior;
- keyring-backed credentials with a permission-protected file fallback;
- Ollama Cloud health, model discovery, quota/usage metadata when exposed by
  the endpoint, and clear unsupported-operation errors;
- attachment parsing from terminal text, clipboard image data, and dropped
  file paths;
- safe attachment validation and provider-neutral message blocks;
- regression tests, provider documentation, migration notes, and live tests
  that remain opt-in.

This milestone does not implement `@agent`, parallel subagents, or
deterministic provider replay; those remain Milestone 2 work.

## Design

### 1. Dynamic model catalog

Introduce a provider-neutral catalog record with a stable model ID, display
label, provider, source (`live`, `cache`, or `static`), capability hints, and
an optional recommendation reason. A catalog adapter returns a typed result
and never exposes credentials in errors or cache files.

Discovery order:

1. fetch live models from the provider or compatible endpoint;
2. validate and normalize the response;
3. write a bounded, timestamped cache under the resolved Avo user root;
4. use a fresh cache when the provider is temporarily unavailable;
5. use the existing static catalog only as an explicit final fallback.

The `/model` picker consumes the same catalog service for every provider. It
must show the current model, source/staleness, and a readable error when live
discovery is unavailable. Ollama local uses `/api/tags`; Ollama Cloud uses
the configured remote endpoint; OpenAI-compatible providers use `/v1/models`;
other providers use their native model-list endpoint when credentials and the
vendor API expose one. A provider without model discovery keeps a clearly
labelled static catalog rather than pretending it is account-scoped.

### 2. Credential storage

Add a small credential backend interface with `get`, `put`, `delete`, and
`list` operations. The preferred backend is the operating-system keyring via
an optional `keyring` extra. The fallback remains JSON with mode `0600`, a
private parent directory, atomic replacement, and an explicit diagnostic that
it is protected by file permissions rather than encrypted.

Existing credentials migrate lazily and preserve provider/account metadata.
Tokens are never written to model catalogs, model cache files, logs, error
messages, or generated shell startup files. `doctor` reports the selected
backend and migration state without printing secrets.

### 3. Ollama Cloud

Reuse the Ollama provider transport with a remote base URL and bearer API key,
but separate local and cloud configuration in diagnostics. Add health and
model-list operations, normalize remote model IDs, and expose quota/usage only
when the endpoint returns those fields. Pull is local-only; cloud operations
must never download a remote model into the local daemon. Unsupported remote
operations return an actionable message instead of silently doing nothing.

### 4. Attachments

The prompt accepts three input forms:

- a dropped path or `file:///...` URI pasted by the terminal;
- an explicit `@/path/to/file` attachment token;
- a clipboard image action when the terminal supports `wl-paste` or `xclip`.

The parser produces an immutable attachment record and removes only the
attachment token from the user-visible text. Ordinary prose that happens to
contain a path is not read unless the path is an explicit token or a complete
dropped path.

Safety rules:

- paths are resolved and must remain inside the workspace unless the user
  explicitly enables an allowed external attachment directory;
- symlinks are resolved before containment checks;
- file size, image size, and total attachment limits are configurable with
  conservative defaults;
- supported images become `ImageBlock` content blocks;
- UTF-8 text/code/JSON/CSV/Markdown files become labelled text blocks;
- unsupported binary files remain metadata-only and produce a clear message
  explaining that the agent can inspect them through workspace tools instead;
- clipboard data is written to a temporary file only after MIME validation and
  is removed when the turn finishes.

The runtime keeps the canonical message shape. Providers that support image
blocks receive native image payloads. Text-only providers receive the file
contents with an attachment label. Unsupported images are rejected before a
provider request, not silently discarded.

### 5. Error handling and compatibility

Catalog and credential failures are recoverable where a cache or fallback is
safe. Attachment validation failures are user-facing and do not start a model
run. Existing plain-text prompts, `ModelRequest`, provider APIs, and session
transcripts remain backward-compatible. Attachment metadata is persisted with
the user turn without persisting temporary clipboard paths or credentials.

## Testing strategy

- unit tests for catalog normalization, cache freshness, stale fallback, and
  redaction;
- credential backend tests for permissions, atomic writes, migration, and
  missing optional keyring support;
- Ollama Cloud transport tests for auth, `/api/tags`, health, quota metadata,
  and local/cloud operation separation;
- attachment tests for explicit tokens, dropped paths, URI decoding,
  workspace containment, symlink escape, size limits, image blocks, text
  files, unsupported binaries, and clipboard command output;
- provider payload tests for image and text attachment rendering;
- REPL tests proving attachments are shown in the prompt summary and ordinary
  messages remain unchanged;
- live provider tests remain opt-in and never run with repository secrets.

## Acceptance criteria

1. `/model` uses one catalog service and identifies live versus cached versus
   static data.
2. A credential can be stored/retrieved through keyring when available and
   safely through the `0600` fallback otherwise.
3. Ollama Cloud can report health and models without attempting a local pull.
4. A user can paste or drop an image path and the provider receives a native
   image block where supported.
5. A user can attach a text/code file and the model receives labelled content
   without escaping the workspace.
6. Unsupported or unsafe files fail before inference with an actionable error.
7. Existing tests and the full quality gate remain green.
