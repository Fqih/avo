"""Live token printing for the streaming chat REPL.

The runtime's ``stream_callback`` is purely observational; this module
is the chat-side consumer that renders text deltas as they arrive.
Gated by ``AVO_CHAT_STREAM`` (``"0"``/``"false"`` disables; streaming is
on by default whenever the provider supports it).
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import types
from collections.abc import Callable
from typing import TextIO

from avo.providers.streaming import ThinkingStreamParser

STREAM_GATE_ENV = "AVO_CHAT_STREAM"
_INTERRUPT_NOTICE = "⟲ stream interrupted\n"


def chat_stream_enabled(environ: dict[str, str]) -> bool:
    """Return True unless ``AVO_CHAT_STREAM`` is explicitly disabled."""

    raw = environ.get(STREAM_GATE_ENV, "1").strip().lower()
    return raw not in {"0", "false"}


class TerminalSpinner:
    """Async context manager displaying an animated Braille spinner on interactive TTY."""

    FRAMES: tuple[str, ...] = ("⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏")

    def __init__(
        self,
        out: TextIO,
        message: str = "Thinking...",
        interval: float = 0.08,
    ) -> None:
        self._out = out
        self._message = message
        self._interval = interval
        self._running = False
        self._task: asyncio.Task[None] | None = None
        self._enabled = hasattr(out, "isatty") and out.isatty() and not os.environ.get("NO_COLOR")

    @property
    def enabled(self) -> bool:
        return self._enabled

    async def __aenter__(self) -> TerminalSpinner:
        if self._enabled:
            self._running = True
            self._task = asyncio.create_task(self._spin())
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: types.TracebackType | None,
    ) -> None:
        await self.stop()

    async def _spin(self) -> None:
        idx = 0
        try:
            while self._running:
                frame = self.FRAMES[idx % len(self.FRAMES)]
                self._out.write(f"\r\033[36m{frame}\033[0m \033[90m{self._message}\033[0m")
                self._out.flush()
                idx += 1
                await asyncio.sleep(self._interval)
        except asyncio.CancelledError:
            pass

    def stop_sync(self) -> None:
        """Synchronously clear the spinner line when the first content arrives."""

        if not self._running:
            return
        self._running = False
        if self._enabled:
            self._out.write("\r\033[K")
            self._out.flush()

    async def stop(self) -> None:
        """Cancel the background spin task and ensure the line is erased."""

        self.stop_sync()
        if self._task and not self._task.done():
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task


class LiveAnswerPrinter:
    """Render streamed deltas to ``out`` live, answer channel only.

    - ``feed`` receives raw text deltas from the runtime. A
      :class:`ThinkingStreamParser` splits ``<think>`` tags that stream
      mid-content; only the answer channel is printed so the final
      answer is never double-printed against the post-run answer block.
    - The first content delta of a call opens a compact ``• `` line;
      :meth:`finish` closes it with a newline.
    - :meth:`on_interrupt` is called by the runtime when a stream dies
      after already printing deltas; already-printed text cannot be
      unprinted, so the honest behavior is a short notice line, then a
      fresh ``Avo> `` line if deltas resume on a retry. The notice never
      promises a retry — the retry decision is made downstream and may
      fail.
    - ``answered`` reports whether any content was live-printed this
      turn; ``_run_turn`` uses it to suppress the post-run answer block.
    """

    def __init__(
        self,
        out: TextIO,
        on_first_content: Callable[[], None] | None = None,
    ) -> None:
        self._out = out
        self._on_first_content = on_first_content
        self._parser = ThinkingStreamParser()
        self._line_open = False
        self.answered = False

    def feed(self, delta: str) -> None:
        """Route one raw text delta through the thinking filter."""

        for channel, text in self._parser.feed(delta):
            self._emit(channel, text)

    def on_interrupt(self) -> None:
        """Mark where a stream died after live deltas were printed."""

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
            if self._on_first_content is not None:
                self._on_first_content()
                self._on_first_content = None
            self._out.write("• ")
            self._line_open = True
            self.answered = True
        self._out.write(text)
        self._out.flush()


__all__ = ["LiveAnswerPrinter", "TerminalSpinner", "chat_stream_enabled"]
