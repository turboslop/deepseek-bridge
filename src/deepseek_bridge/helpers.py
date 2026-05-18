from __future__ import annotations

import threading
import time
import types
import uuid

from .logging import LOG


def _generate_request_id() -> str:
    return f"dcp-{uuid.uuid4().hex[:24]}"


_shutdown_requested = threading.Event()


def _handle_shutdown_signal(
    signum: int, _frame: types.FrameType | None
) -> None:
    LOG.info("received signal %s, initiating graceful shutdown", signum)
    _shutdown_requested.set()


def elapsed_ms(started: float) -> int:
    return round((time.monotonic() - started) * 1000)
