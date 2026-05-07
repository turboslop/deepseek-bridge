from __future__ import annotations

import logging
from collections import deque
from typing import Any, Callable

# Pre-mount buffer: captures log messages before TUI is mounted,
# flushed to RichLog widget on mount.
_pre_mount_buffer: deque[str] = deque(maxlen=200)


class PreMountLogHandler(logging.Handler):
    """Buffers log messages before the TUI mounts, flushed on mount."""

    def __init__(self, buffer: deque) -> None:
        super().__init__(level=logging.INFO)
        self.buffer = buffer

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = record.getMessage()
            self.buffer.append(msg)
        except Exception:
            pass


def install_pre_mount_handler() -> logging.Handler:
    """Install a PreMountLogHandler that buffers into _pre_mount_buffer.
    Returns the handler for later removal."""
    handler = PreMountLogHandler(_pre_mount_buffer)
    logging.getLogger().addHandler(handler)
    return handler


class TuiLogHandler(logging.Handler):
    """Logging handler that delegates formatted messages to a callback.

    Designed for bridging Python's logging framework into a TUI widget
    (e.g., Textual's RichLog). The callback abstracts the TUI so this
    module stays pure Python with no GUI dependencies.

    - Filters DEBUG and below (level=INFO).
    - Formats INFO as plain text, WARNING+ with level prefix.
    - Thread-safe: just passes through; caller handles main-thread dispatch.
    - Shutdown-safe: ``close()`` silences further emissions.
    """

    def __init__(self, emit_fn: Callable[[str], None]) -> None:
        super().__init__(level=logging.INFO)
        self.emit_fn = emit_fn
        self._closed = False

    def emit(self, record: logging.LogRecord) -> None:
        if self._closed:
            return
        if record.levelno < self.level:
            return
        try:
            msg = self.format(record)
            self.emit_fn(msg)
        except Exception:
            try:
                self.emit_fn(str(record.msg))
            except Exception:
                pass  # Never crash on a log formatting error

    def format(self, record: logging.LogRecord) -> str:
        msg = record.getMessage()
        if record.levelno <= logging.INFO:
            return msg
        return f"{record.levelname}: {msg}"

    def close(self) -> None:
        self._closed = True
        super().close()
