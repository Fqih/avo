"""Fixed tool-heavy transcripts for honest token-saver measurements."""

from __future__ import annotations

import hashlib
import json
import random
from typing import Any

Messages = list[dict[str, Any]]


def _tool_result(index: int, *, lines: int, duplicate_of: int | None = None) -> dict[str, Any]:
    if duplicate_of is not None:
        content = json.dumps(
            {"command": "git log --oneline", "same_as": duplicate_of},
            indent=2,
        )
    else:
        rng = random.Random(42 + index)
        content = json.dumps(
            {
                "command": "run_shell",
                "exit_code": 0,
                "stdout": [
                    f"{index:02d}-{line:03d} {rng.choice(['alpha', 'beta', 'gamma'])}"
                    for line in range(lines)
                ],
                "metadata": {"cwd": "/workspace", "attempt": 1},
            },
            indent=2,
        )
    return {"role": "tool", "tool_call_id": f"call-{index}", "content": content}


def build_transcripts() -> dict[str, Messages]:
    """Return small, medium, and large deterministic transcripts."""

    return {
        "small": [
            {"role": "system", "content": "You are Avo."},
            {"role": "user", "content": "Inspect the repository."},
            _tool_result(1, lines=4),
            _tool_result(2, lines=4, duplicate_of=1),
        ],
        "medium": [
            {"role": "system", "content": "You are Avo."},
            {"role": "user", "content": "Trace the failing integration."},
            *[
                _tool_result(index, lines=24, duplicate_of=1 if index % 4 == 0 else None)
                for index in range(1, 10)
            ],
        ],
        "large": [
            {"role": "system", "content": "You are Avo."},
            {"role": "user", "content": "Summarize the complete execution history."},
            *[
                _tool_result(index, lines=70, duplicate_of=1 if index % 3 == 0 else None)
                for index in range(1, 25)
            ],
        ],
    }


def transcript_digest(messages: Messages) -> str:
    """Return a stable digest so fixture edits are visible in benchmark output."""

    encoded = json.dumps(messages, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


__all__ = ["Messages", "build_transcripts", "transcript_digest"]
