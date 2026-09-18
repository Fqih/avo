"""Determinism checks for the token-saver benchmark fixtures."""

from __future__ import annotations

from benchmark.savers.fixtures import build_transcripts, transcript_digest
from benchmark.savers.run_saver_bench import benchmark_rows


def test_benchmark_fixtures_are_deterministic() -> None:
    first = build_transcripts()
    second = build_transcripts()
    assert first == second
    assert {name: transcript_digest(value) for name, value in first.items()} == {
        name: transcript_digest(value) for name, value in second.items()
    }
    assert all(len(transcript_digest(value)) == 64 for value in first.values())


def test_benchmark_covers_all_presets_and_transcripts() -> None:
    rows = benchmark_rows()
    assert {row["preset"] for row in rows} == {"terse", "yagni", "compact", "full"}
    assert {row["transcript"] for row in rows} == {"small", "medium", "large"}
    assert all(
        row["tokens_before"] >= row["tokens_after"]
        for row in rows
        if row["preset"] in {"compact", "full"}
    )
