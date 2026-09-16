"""Unit tests for the deterministic token-saver stages (spec §2, §11)."""

from __future__ import annotations

import copy
from typing import Any

import pytest

from avo.savers.stages import (
    DedupeToolResultsStage,
    ElideVerboseOutputStage,
    JsonMinifyStage,
    PreTrimmerStage,
    SaverStage,
)

Messages = list[dict[str, Any]]


def _tool(content: Any, call_id: str = "c1") -> dict[str, Any]:
    return {"role": "tool", "tool_call_id": call_id, "content": content}


def _sample() -> Messages:
    pretty = '{\n  "a": 1,\n  "b": [1, 2, 3]\n}'
    return [
        {"role": "system", "content": "persona prefix " + pretty},
        {"role": "user", "content": "hello " + pretty},
        _tool(pretty, "t1"),
        _tool(pretty, "t2"),
        _tool(pretty, "t3"),
        {"role": "assistant", "content": "answer " + pretty},
    ]


ALL_STAGES: list[SaverStage] = [
    JsonMinifyStage(),
    DedupeToolResultsStage(),
    ElideVerboseOutputStage(),
    PreTrimmerStage(),
]


@pytest.mark.parametrize("stage", ALL_STAGES, ids=lambda s: s.name)
def test_stage_satisfies_protocol(stage: SaverStage) -> None:
    assert isinstance(stage, SaverStage)
    assert stage.name


@pytest.mark.parametrize("stage", ALL_STAGES, ids=lambda s: s.name)
def test_no_match_returns_identity(stage: SaverStage) -> None:
    messages: Messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "plain text, no json"},
        _tool("plain text, no json", "t1"),
        {"role": "assistant", "content": "short answer"},
    ]
    assert stage.apply(messages) == messages


@pytest.mark.parametrize("stage", ALL_STAGES, ids=lambda s: s.name)
def test_structure_and_roles_preserved(stage: SaverStage) -> None:
    messages = _sample()
    out = stage.apply(messages)
    assert len(out) == len(messages)
    assert [m["role"] for m in out] == [m["role"] for m in messages]
    for original, result in zip(messages, out, strict=True):
        if original["role"] != "tool":
            assert result == original
        else:
            assert result["tool_call_id"] == original["tool_call_id"]


@pytest.mark.parametrize("stage", ALL_STAGES, ids=lambda s: s.name)
def test_input_never_mutated(stage: SaverStage) -> None:
    messages = _sample()
    snapshot = copy.deepcopy(messages)
    stage.apply(messages)
    assert messages == snapshot


@pytest.mark.parametrize("stage", ALL_STAGES, ids=lambda s: s.name)
def test_system_message_never_touched(stage: SaverStage) -> None:
    messages = _sample()
    out = stage.apply(messages)
    assert out[0] is messages[0] or out[0] == messages[0]


def test_json_minify_rewrites_pretty_json() -> None:
    messages: Messages = [_tool('{\n  "a": 1\n}')]
    out = JsonMinifyStage().apply(messages)
    assert out[0]["content"] == '{"a":1}'


def test_json_minify_keeps_non_json() -> None:
    text = "fatal: not a git repository"
    out = JsonMinifyStage().apply([_tool(text)])
    assert out[0]["content"] == text


def test_json_minify_only_when_shorter() -> None:
    compact = '{"a":1}'
    out = JsonMinifyStage().apply([_tool(compact)])
    assert out[0]["content"] == compact


def test_json_minify_handles_list_content_blocks() -> None:
    content = [
        {"type": "text", "text": '{\n  "x": 1\n}'},
        {"type": "image", "source": {"data": "keep"}},
    ]
    out = JsonMinifyStage().apply([_tool(content)])
    result = out[0]["content"]
    assert isinstance(result, list)
    assert result[0] == {"type": "text", "text": '{"x":1}'}
    assert result[1] == content[1]


def test_json_minify_skips_non_tool_roles() -> None:
    messages: Messages = [
        {"role": "system", "content": '{\n  "a": 1\n}'},
        {"role": "user", "content": '{\n  "a": 1\n}'},
    ]
    assert JsonMinifyStage().apply(messages) == messages


def test_dedupe_marks_later_duplicates_with_first_index() -> None:
    messages: Messages = [
        {"role": "system", "content": "sys"},
        _tool("same output", "t1"),
        {"role": "assistant", "content": "thinking"},
        _tool("same output", "t2"),
        _tool("other output", "t3"),
        _tool("same output", "t4"),
    ]
    out = DedupeToolResultsStage().apply(messages)
    assert out[1] == messages[1]
    assert out[3]["content"] == "[same as message #1]"
    assert out[4]["content"] == "other output"
    assert out[5]["content"] == "[same as message #1]"
    assert out[3]["tool_call_id"] == "t2"


def test_dedupe_ignores_non_str_and_non_tool() -> None:
    messages: Messages = [
        {"role": "user", "content": "dup"},
        _tool("dup"),
        _tool(42),
        _tool(None),
    ]
    out = DedupeToolResultsStage().apply(messages)
    assert out[0] == messages[0]
    assert out[1]["content"] == "dup"
    assert out[2]["content"] == 42
    assert out[3]["content"] is None


def _lines(count: int) -> str:
    return "\n".join(f"line {i}" for i in range(count))


def test_elide_replaces_middle_with_marker() -> None:
    stage = ElideVerboseOutputStage(max_lines=10, keep_first=3, keep_last=2)
    out = stage.apply([_tool(_lines(10))])
    assert out[0]["content"] == _lines(10)  # exactly max → identity

    out = stage.apply([_tool(_lines(11))])
    content = str(out[0]["content"])
    assert content.startswith("line 0\nline 1\nline 2\n")
    assert content.endswith("\nline 9\nline 10")
    assert "[... 6 lines elided ...]" in content


def test_elide_keeps_short_output() -> None:
    text = _lines(3)
    out = ElideVerboseOutputStage(max_lines=10).apply([_tool(text)])
    assert out[0]["content"] == text


def test_elide_skips_non_tool_and_non_str() -> None:
    stage = ElideVerboseOutputStage(max_lines=2, keep_first=1, keep_last=1)
    messages: Messages = [
        {"role": "user", "content": _lines(50)},
        _tool(123456),
        _tool(_lines(50)),
    ]
    out = stage.apply(messages)
    assert out[0] == messages[0]
    assert out[1]["content"] == 123456
    assert "[... 48 lines elided ...]" in str(out[2]["content"])


def test_pre_trimmer_identity_below_trigger() -> None:
    stage = PreTrimmerStage(
        trigger_tokens=100, target_tokens=50, keep_recent=2, tool_content_max_chars=200
    )
    messages: Messages = [_tool("x" * 40)]  # 10 est tokens < trigger
    assert stage.apply(messages) == messages


def test_pre_trimmer_shrinks_oldest_first_and_stops_at_target() -> None:
    stage = PreTrimmerStage(
        trigger_tokens=100, target_tokens=120, keep_recent=2, tool_content_max_chars=200
    )
    messages: Messages = [
        {"role": "system", "content": "sys"},
        _tool("a" * 2000, "t1"),  # 500 est tokens — oldest, shrunk first
        _tool("b" * 2000, "t2"),  # untouched (keep_recent window starts here)
        _tool("c" * 2000, "t3"),  # within keep_recent: untouched
    ]
    out = stage.apply(messages)
    assert out[1]["content"] != messages[1]["content"]
    assert "[... 1800 chars elided ...]" in str(out[1]["content"])
    assert out[2]["content"] == messages[2]["content"]
    assert out[3]["content"] == messages[3]["content"]


def test_pre_trimmer_keeps_recent_even_when_target_unreachable() -> None:
    stage = PreTrimmerStage(
        trigger_tokens=100, target_tokens=10, keep_recent=2, tool_content_max_chars=200
    )
    messages: Messages = [
        {"role": "system", "content": "sys"},
        _tool("a" * 2000, "t1"),
        _tool("b" * 2000, "t2"),
        _tool("c" * 2000, "t3"),
        {"role": "user", "content": "newest"},
    ]
    out = stage.apply(messages)
    assert len(out) == len(messages)
    assert "[... 1800 chars elided ...]" in str(out[1]["content"])
    assert "[... 1800 chars elided ...]" in str(out[2]["content"])
    assert out[3]["content"] == messages[3]["content"]  # keep_recent=2 protects t3/user


def test_pre_trimmer_skips_small_and_non_str_contents() -> None:
    stage = PreTrimmerStage(
        trigger_tokens=100, target_tokens=50, keep_recent=1, tool_content_max_chars=200
    )
    big = "z" * 4000
    messages: Messages = [
        _tool("small", "t1"),
        _tool(1234567890, "t2"),
        _tool(big, "t3"),
        {"role": "user", "content": "newest"},
    ]
    out = stage.apply(messages)
    assert out[0]["content"] == "small"
    assert out[1]["content"] == 1234567890
    assert len(str(out[2]["content"])) < len(big)
