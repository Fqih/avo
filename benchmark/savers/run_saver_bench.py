"""Run deterministic token-saver measurements without network access."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from avo.savers.pipeline import run_pipeline
from avo.savers.presets import BUILTIN_PRESETS

from .fixtures import build_transcripts, transcript_digest


def benchmark_rows() -> list[dict[str, Any]]:
    """Return one honest estimate row for every preset/transcript pair."""

    rows: list[dict[str, Any]] = []
    for transcript_name, messages in build_transcripts().items():
        for preset_name, preset in BUILTIN_PRESETS.items():
            if preset.pipeline is None:
                before = after = sum(
                    len(str(message.get("content", ""))) // 4 for message in messages
                )
                stages: tuple[str, ...] = ()
            else:
                result = run_pipeline(messages, preset.pipeline)
                before, after, stages = (
                    result.tokens_before,
                    result.tokens_after,
                    result.stages_applied,
                )
            saved = round((before - after) / before * 100, 1) if before else 0.0
            rows.append(
                {
                    "transcript": transcript_name,
                    "preset": preset_name,
                    "tokens_before": before,
                    "tokens_after": after,
                    "saved_percent": saved,
                    "stages": ", ".join(stages) or "none",
                }
            )
    return rows


def render_results() -> str:
    transcripts = build_transcripts()
    lines = [
        "# Token Saver Benchmark Results",
        "",
        "These estimates use `len(text)//4`, not a provider tokenizer. They are",
        "useful for deterministic comparisons, not billing or context-window claims.",
        "",
        "## Fixture digests",
        "",
        "| Transcript | SHA-256 |",
        "| --- | --- |",
    ]
    lines.extend(
        f"| {name} | `{transcript_digest(value)}` |" for name, value in transcripts.items()
    )
    lines.extend(
        [
            "",
            "## Preset measurements",
            "",
            "| Transcript | Preset | Before | After | Saved | Stages |",
            "| --- | --- | ---: | ---: | ---: | --- |",
        ]
    )
    for row in benchmark_rows():
        lines.append(
            f"| {row['transcript']} | {row['preset']} | {row['tokens_before']} | "
            f"{row['tokens_after']} | {row['saved_percent']}% | {row['stages']} |"
        )
    return "\n".join(lines) + "\n"


def main() -> int:
    target = Path(__file__).with_name("RESULTS.md")
    target.write_text(render_results(), encoding="utf-8")
    print(f"wrote {target}")
    for name, messages in build_transcripts().items():
        print(f"{name}: {transcript_digest(messages)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
