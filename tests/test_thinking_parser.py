"""Tests for reasoning/thinking stream parser and formatting."""

from __future__ import annotations

from avo.providers.streaming import ThinkingStreamParser, split_thinking


def test_split_thinking_standard() -> None:
    content = "<think>\nConsider options A and B.\nOption A is better.\n</think>\nHere is the code."
    thought, answer = split_thinking(content)
    assert thought == "Consider options A and B.\nOption A is better."
    assert answer == "Here is the code."


def test_split_thinking_no_tags() -> None:
    content = "Just a regular response without thinking."
    thought, answer = split_thinking(content)
    assert thought == ""
    assert answer == content


def test_split_thinking_empty() -> None:
    thought, answer = split_thinking("")
    assert thought == ""
    assert answer == ""


def test_split_thinking_unterminated() -> None:
    content = "<think>\nThinking that was cut off..."
    thought, answer = split_thinking(content)
    assert thought == "Thinking that was cut off..."
    assert answer == ""


def test_thinking_stream_parser_split_chunks() -> None:
    parser = ThinkingStreamParser()

    # Chunks with split opening tag
    out1 = parser.feed("Hello ")
    out2 = parser.feed("<th")
    out3 = parser.feed("ink>Let me think ")
    out4 = parser.feed("carefully.</th")
    out5 = parser.feed("ink>Here is ")
    out6 = parser.feed("the answer.")
    out7 = parser.flush()

    all_outputs = out1 + out2 + out3 + out4 + out5 + out6 + out7

    content_text = "".join(text for ch, text in all_outputs if ch == "content")
    thought_text = "".join(text for ch, text in all_outputs if ch == "thought")

    assert content_text == "Hello Here is the answer."
    assert thought_text == "Let me think carefully."


def test_thinking_stream_parser_flush_unterminated() -> None:
    parser = ThinkingStreamParser()
    out1 = parser.feed("<think>Unterminated thought")
    out2 = parser.flush()
    assert ("thought", "Unterminated thought") in (out1 + out2)
