from __future__ import annotations

import logging
from collections import deque
import threading
from typing import Callable

from ..logging import LOG

# Pre-mount buffer: captures log messages before TUI is mounted,
# flushed to RichLog widget on mount.
_pre_mount_buffer: deque[str] = deque(maxlen=200)
_pre_mount_lock = threading.Lock()


class PreMountLogHandler(logging.Handler):
    """Buffers log messages before the TUI mounts, flushed on mount."""

    def __init__(self, buffer: deque) -> None:
        super().__init__(level=logging.INFO)
        self.buffer = buffer

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = record.getMessage()
            with _pre_mount_lock:
                self.buffer.append(msg)
        except Exception as exc:
            LOG.warning("pre-mount log handler emit failed: %s", exc)


def install_pre_mount_handler() -> logging.Handler:
    """Install a PreMountLogHandler that buffers into _pre_mount_buffer.
    Returns the handler for later removal."""
    handler = PreMountLogHandler(_pre_mount_buffer)
    logging.getLogger().addHandler(handler)
    LOG.info("installed pre-mount log handler (buffer: %s)", _pre_mount_buffer.maxlen)
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
        except Exception as exc:
            try:
                self.emit_fn(str(record.msg))
            except Exception as exc2:
                LOG.warning(
                    "log formatting failed (original: %s, fallback: %s)", exc, exc2
                )

    def format(self, record: logging.LogRecord) -> str:
        msg = record.getMessage()
        if record.levelno <= logging.INFO:
            return msg
        return f"{record.levelname}: {msg}"

    def close(self) -> None:
        self._closed = True
        super().close()
