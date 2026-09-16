"""Deterministic, no-LLM compression stages (spec §2).

Every stage is a pure function over ``ModelRequest.messages``
(``list[dict[str, JsonValue]]``) with hard invariants:

* deterministic — same input, same output; no clock, no I/O, no LLM;
* immutable — returns a new list; rewritten messages are new dicts;
* structurally safe — length, per-index role, and every non-content
  field (``tool_call_id``, ``tool_calls``) are preserved; only the
  ``content`` of ``role == "tool"`` messages is ever rewritten, which
  keeps provider request shapes valid;
* system-pinned — ``role == "system"`` messages are never modified,
  protecting the persona prefix and ``cache_prefix_messages``
  alignment.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from pydantic import JsonValue

from avo.context_advisor import estimate_text_tokens

Messages = list[dict[str, JsonValue]]


@runtime_checkable
class SaverStage(Protocol):
    """One deterministic pass over a message list."""

    name: str

    def apply(self, messages: Messages) -> Messages: ...


def _render(content: JsonValue) -> str:
    """Best-effort text estimate for a message ``content`` value."""
    if isinstance(content, str):
        return content
    return str(content)


def _estimate(messages: Messages) -> int:
    return sum(estimate_text_tokens(_render(m.get("content"))) for m in messages)


def _shrink_text(text: str, max_chars: int) -> str:
    """Head/tail char elision; the marker adds a small fixed overhead."""
    if len(text) <= max_chars:
        return text
    head_len = max_chars // 2
    tail_len = max_chars - head_len
    elided = len(text) - head_len - tail_len
    marker = f"\n[... {elided} chars elided ...]\n"
    return text[:head_len] + marker + text[-tail_len:]


def _map_tool_contents(messages: Messages, transform: Callable[[str], str]) -> Messages:
    """Apply ``transform`` to str contents and list-block text parts."""

    def one(content: JsonValue) -> JsonValue:
        if isinstance(content, str):
            return transform(content)
        if isinstance(content, list):
            changed = False
            blocks: list[JsonValue] = []
            for block in content:
                if not isinstance(block, dict):
                    blocks.append(block)
                    continue
                text = block.get("text")
                if isinstance(text, str):
                    new_text = transform(text)
                    if new_text != text:
                        nb: dict[str, JsonValue] = dict(block)
                        nb["text"] = new_text
                        blocks.append(nb)
                        changed = True
                        continue
                blocks.append(block)
            return blocks if changed else content
        return content

    out: Messages = []
    for message in messages:
        if message.get("role") != "tool":
            out.append(message)
            continue
        content = message.get("content")
        new_content = one(content)
        if new_content == content:
            out.append(message)
            continue
        rewritten: dict[str, JsonValue] = dict(message)
        rewritten["content"] = new_content
        out.append(rewritten)
    return out


@dataclass(frozen=True)
class JsonMinifyStage:
    """Re-serialize JSON-shaped tool output compactly, only if shorter."""

    name: str = "json_minify"

    def apply(self, messages: Messages) -> Messages:
        return _map_tool_contents(messages, self._minify)

    @staticmethod
    def _minify(text: str) -> str:
        try:
            parsed = json.loads(text)
        except (ValueError, RecursionError):
            return text
        compact = json.dumps(parsed, separators=(",", ":"), ensure_ascii=False)
        return compact if len(compact) < len(text) else text


@dataclass(frozen=True)
class DedupeToolResultsStage:
    """Replace repeated identical tool results with a back-reference."""

    name: str = "dedupe_tool_results"

    def apply(self, messages: Messages) -> Messages:
        seen: dict[str, int] = {}
        out: Messages = []
        for index, message in enumerate(messages):
            content = message.get("content") if message.get("role") == "tool" else None
            if isinstance(content, str) and content:
                digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
                first = seen.get(digest)
                if first is None:
                    seen[digest] = index
                else:
                    rewritten: dict[str, JsonValue] = dict(message)
                    rewritten["content"] = f"[same as message #{first}]"
                    out.append(rewritten)
                    continue
            out.append(message)
        return out


@dataclass(frozen=True)
class ElideVerboseOutputStage:
    """Collapse the middle of over-long tool output to head + tail."""

    max_lines: int = 80
    keep_first: int = 20
    keep_last: int = 20
    name: str = "elide_verbose_output"

    def apply(self, messages: Messages) -> Messages:
        return _map_tool_contents(messages, self._elide)

    def _elide(self, text: str) -> str:
        lines = text.split("\n")
        if len(lines) <= self.max_lines:
            return text
        if self.keep_first + self.keep_last >= len(lines):
            return text
        head = "\n".join(lines[: self.keep_first])
        tail = "\n".join(lines[-self.keep_last :])
        elided = len(lines) - self.keep_first - self.keep_last
        return f"{head}\n[... {elided} lines elided ...]\n{tail}"


@dataclass(frozen=True)
class PreTrimmerStage:
    """Last-resort cheap cut: shrink oldest tool results to reach target.

    Never deletes messages; complements the LLM-based ``compact_messages``
    path rather than replacing it.
    """

    trigger_tokens: int = 6000
    target_tokens: int = 4500
    keep_recent: int = 8
    tool_content_max_chars: int = 1200
    name: str = "pre_trimmer"

    def apply(self, messages: Messages) -> Messages:
        estimate = _estimate(messages)
        if estimate <= self.trigger_tokens:
            return list(messages)
        out = list(messages)
        cutoff = max(0, len(out) - self.keep_recent)
        for index in range(min(cutoff, len(out))):
            if estimate <= self.target_tokens:
                break
            message = out[index]
            if message.get("role") != "tool":
                continue
            content = message.get("content")
            if not isinstance(content, str) or len(content) <= self.tool_content_max_chars:
                continue
            shrunk = _shrink_text(content, self.tool_content_max_chars)
            estimate -= estimate_text_tokens(content)
            estimate += estimate_text_tokens(shrunk)
            rewritten: dict[str, JsonValue] = dict(message)
            rewritten["content"] = shrunk
            out[index] = rewritten
        return out


__all__ = [
    "DedupeToolResultsStage",
    "ElideVerboseOutputStage",
    "JsonMinifyStage",
    "Messages",
    "PreTrimmerStage",
    "SaverStage",
]
