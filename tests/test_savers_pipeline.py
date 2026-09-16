"""Pipeline ordering, config validation, and applied-stage accounting."""

from __future__ import annotations

import copy
from typing import Any

import pytest
from pydantic import ValidationError

from avo.savers.pipeline import (
    ElideConfig,
    PipelineConfig,
    PreTrimConfig,
    run_pipeline,
)

Messages = list[dict[str, Any]]


def _tool(content: Any, call_id: str = "c1") -> dict[str, Any]:
    return {"role": "tool", "tool_call_id": call_id, "content": content}


def test_defaults_run_minify_dedupe_elide_not_pretrim() -> None:
    pretty = '{\n  "a": 1\n}'
    verbose = "\n".join(f"v line {i}" for i in range(200))
    messages: Messages = [
        {"role": "system", "content": "sys"},
        _tool(pretty, "t1"),
        _tool(pretty, "t2"),
        _tool(verbose, "t3"),
    ]
    result = run_pipeline(messages, PipelineConfig())
    assert result.stages_applied == ("json_minify", "dedupe_tool_results", "elide_verbose_output")
    assert result.messages[2]["content"] == "[same as message #1]"
    assert result.tokens_after < result.tokens_before


def test_no_change_yields_empty_applied_and_equal_estimates() -> None:
    messages: Messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "hi"},
        _tool("short plain output"),
    ]
    result = run_pipeline(messages, PipelineConfig())
    assert result.stages_applied == ()
    assert result.tokens_before == result.tokens_after
    assert result.messages == messages


def test_dedupe_runs_after_minify_so_whitespace_variants_collapse() -> None:
    messages: Messages = [_tool('{\n"a":1}', "t1"), _tool('{ "a" : 1 }', "t2")]
    result = run_pipeline(messages, PipelineConfig())
    assert "json_minify" in result.stages_applied
    assert "dedupe_tool_results" in result.stages_applied
    assert result.messages[1]["content"] == "[same as message #0]"


def test_disabled_stages_never_run() -> None:
    pretty = '{\n  "a": 1\n}'
    verbose = "\n".join(f"v line {i}" for i in range(200))
    messages: Messages = [_tool(pretty, "t1"), _tool(pretty, "t2"), _tool(verbose, "t3")]
    config = PipelineConfig(json_minify=False, dedupe=False, elide=None)
    result = run_pipeline(messages, config)
    assert result.stages_applied == ()
    assert result.messages == messages


def test_pre_trim_only_when_configured() -> None:
    big = "z" * 60_000  # ~15000 est tokens, far over default trigger
    messages: Messages = [_tool(big, "t1"), {"role": "user", "content": "newest"}]
    without = run_pipeline(messages, PipelineConfig())
    assert "pre_trimmer" not in without.stages_applied

    config = PipelineConfig(
        json_minify=False,
        dedupe=False,
        elide=None,
        pre_trim=PreTrimConfig(trigger_tokens=100, target_tokens=50, keep_recent=1),
    )
    with_trim = run_pipeline(messages, config)
    assert with_trim.stages_applied == ("pre_trimmer",)
    assert len(str(with_trim.messages[0]["content"])) < len(big)


def test_input_never_mutated() -> None:
    pretty = '{\n  "a": 1\n}'
    messages: Messages = [_tool(pretty, "t1"), _tool(pretty, "t2")]
    snapshot = copy.deepcopy(messages)
    run_pipeline(messages, PipelineConfig())
    assert messages == snapshot


def test_pretrim_config_rejects_target_above_trigger() -> None:
    with pytest.raises(ValidationError):
        PreTrimConfig(trigger_tokens=100, target_tokens=101)


def test_configs_require_positive_integers() -> None:
    with pytest.raises(ValidationError):
        ElideConfig(max_lines=0)
    with pytest.raises(ValidationError):
        PreTrimConfig(keep_recent=0)
    with pytest.raises(ValidationError):
        PreTrimConfig(tool_content_max_chars=-5)


def test_configs_are_frozen() -> None:
    config = PipelineConfig()
    with pytest.raises(ValidationError):
        config.json_minify = False  # type: ignore[misc]
