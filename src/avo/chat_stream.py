"""Live token printing for the streaming chat REPL.

The runtime's ``stream_callback`` is purely observational; this module
is the chat-side consumer that renders text deltas as they arrive.
Gated by ``AVO_CHAT_STREAM`` (``"0"``/``"false"`` disables; streaming is
on by default whenever the provider supports it).
"""

from __future__ import annotations

from typing import TextIO

from avo.providers.streaming import ThinkingStreamParser

STREAM_GATE_ENV = "AVO_CHAT_STREAM"
_INTERRUPT_NOTICE = "⟲ stream interrupted, retrying…\n"


def chat_stream_enabled(environ: dict[str, str]) -> bool:
    """Return True unless ``AVO_CHAT_STREAM`` is explicitly disabled."""

    raw = environ.get(STREAM_GATE_ENV, "1").strip().lower()
    return raw not in {"0", "false"}


class LiveAnswerPrinter:
    """Render streamed deltas to ``out`` live, answer channel only.

    - ``feed`` receives raw text deltas from the runtime. A
      :class:`ThinkingStreamParser` splits ``<think>`` tags that stream
      mid-content; only the answer channel is printed so the final
      answer is never double-printed against the post-run ``Avo>`` block,
      and the 💭 thought section keeps rendering from the persisted
      result exactly as before.
    - The first content delta of a call opens an ``Avo> `` line;
      :meth:`finish` closes it with a newline.
    - :meth:`on_interrupt` is called by the runtime when a stream dies
      after already printing deltas; already-printed text cannot be
      unprinted, so the honest behavior is a short notice line before the
      retry's deltas, then a fresh ``Avo> `` line for the retried answer.
    - ``answered`` reports whether any content was live-printed this
      turn; ``_run_turn`` uses it to suppress the post-run answer block.
    """

    def __init__(self, out: TextIO) -> None:
        self._out = out
        self._parser = ThinkingStreamParser()
        self._line_open = False
        self.answered = False

    def feed(self, delta: str) -> None:
        """Route one raw text delta through the thinking filter."""

        for channel, text in self._parser.feed(delta):
            self._emit(channel, text)

    def on_interrupt(self) -> None:
        """Mark the boundary between interrupted and retried deltas."""

        if self._line_open:
            self._out.write("\n")
            self._line_open = False
        self._out.write(_INTERRUPT_NOTICE)
        self._out.flush()

    def finish(self) -> None:
        """Flush buffered characters and close any open answer line."""

        for channel, text in self._parser.flush():
            self._emit(channel, text)
        if self._line_open:
            self._out.write("\n")
            self._line_open = False
        self._out.flush()

    def _emit(self, channel: str, text: str) -> None:
        if channel != "content" or not text:
            return
        if not self._line_open:
            self._out.write("Avo> ")
            self._line_open = True
            self.answered = True
        self._out.write(text)
        self._out.flush()


__all__ = ["LiveAnswerPrinter", "chat_stream_enabled"]
