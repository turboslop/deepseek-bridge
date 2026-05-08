from __future__ import annotations

import contextlib
import logging as stdlib_logging
import sys
import threading
import types
from datetime import datetime
from pathlib import Path
from typing import Any

LOG = stdlib_logging.getLogger("deepseek_bridge")
PAYLOAD_LOG = stdlib_logging.getLogger("deepseek_bridge.payload")
INTERNAL_LOG = stdlib_logging.getLogger("deepseek_bridge.internal")

DEFAULT_INFO_LOG_FORMAT = "%(message)s"
DEFAULT_WARNING_LOG_FORMAT = "%(levelname)s %(message)s"
VERBOSE_LOG_FORMAT = "%(asctime)s %(levelname)s %(message)s"


class ConsoleLogFormatter(stdlib_logging.Formatter):
    def __init__(self, *, verbose: bool) -> None:
        super().__init__()
        self.verbose = verbose
        self._verbose_formatter = stdlib_logging.Formatter(VERBOSE_LOG_FORMAT)
        self._info_formatter = stdlib_logging.Formatter(DEFAULT_INFO_LOG_FORMAT)
        self._warning_formatter = stdlib_logging.Formatter(DEFAULT_WARNING_LOG_FORMAT)

    def format(self, record: stdlib_logging.LogRecord) -> str:
        if self.verbose:
            return self._verbose_formatter.format(record)
        if record.levelno <= stdlib_logging.INFO:
            return self._info_formatter.format(record)
        return self._warning_formatter.format(record)


def _purge_old_logs(log_dir: Path, prefix: str = "proxy", keep: int = 5) -> None:
    """Remove old log files, keeping the most recent *keep* files."""
    log_files = sorted(
        log_dir.glob(f"{prefix}-*.log"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    for stale in log_files[keep:]:
        with contextlib.suppress(OSError):
            stale.unlink()


def configure_logging(
    *,
    debug: bool = False,
    log_dir: str | Path | None = None,
) -> str | None:
    log_file_path: str | None = None
    handlers: list[stdlib_logging.Handler] = []
    console_handler = stdlib_logging.StreamHandler()
    console_handler.setFormatter(ConsoleLogFormatter(verbose=False))
    handlers.append(console_handler)
    if log_dir:
        log_path = Path(log_dir).expanduser()
        log_path.mkdir(parents=True, exist_ok=True)
        _purge_old_logs(log_path, prefix="proxy", keep=5)
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        log_file = log_path / f"proxy-{timestamp}.log"
        log_file_path = str(log_file)
        file_handler = stdlib_logging.FileHandler(log_file_path, encoding="utf-8")
        file_handler.setFormatter(stdlib_logging.Formatter(VERBOSE_LOG_FORMAT))
        handlers.append(file_handler)
        if debug:
            _purge_old_logs(log_path, prefix="debug", keep=5)
            debug_file = log_path / f"debug-{timestamp}.log"
            debug_handler = stdlib_logging.FileHandler(str(debug_file), encoding="utf-8")
            debug_handler.setFormatter(stdlib_logging.Formatter(VERBOSE_LOG_FORMAT))
            INTERNAL_LOG.addHandler(debug_handler)
            INTERNAL_LOG.propagate = False
    level = stdlib_logging.DEBUG if debug else stdlib_logging.INFO
    stdlib_logging.basicConfig(
        level=level,
        handlers=handlers,
        force=True,
    )
    if log_dir:
        LOG.info("log file: %s", log_file_path)

    def _log_unhandled_exception(
        exc_type: type[BaseException],
        exc_value: BaseException,
        exc_traceback: types.TracebackType | None,
    ) -> None:
        LOG.critical(
            "unhandled exception",
            exc_info=(exc_type, exc_value, exc_traceback),
        )

    import sys as _sys

    _sys.excepthook = _log_unhandled_exception
    LOG.info("error logging: enabled (warnings visible without --verbose)")
    return log_file_path


class TerminalSpinner:
    hide_cursor = "\x1b[?25l"
    show_cursor = "\x1b[?25h"
    frames = ("⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏")

    def __init__(
        self,
        *,
        enabled: bool,
        text: str,
        stream: Any | None = None,
        interval: float = 0.12,
    ) -> None:
        self.stream = stream if stream is not None else sys.stderr
        self.enabled = enabled and bool(getattr(self.stream, "isatty", lambda: False)())
        self.text = text
        self.interval = interval
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._visible = False

    def start(self) -> TerminalSpinner:
        if not self.enabled or self._thread is not None:
            return self
        self.stream.write(self.hide_cursor)
        self.stream.flush()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        if not self.enabled:
            return
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=1)
            self._thread = None
        if self._visible:
            self.stream.write("\r" + (" " * self._clear_width()) + "\r")
            self.stream.flush()
            self._visible = False
        self.stream.write(self.show_cursor)
        self.stream.flush()

    def _run(self) -> None:
        index = 0
        while not self._stop.is_set():
            self.stream.write("\r" + self.text.format(frame=self.frames[index]))
            self.stream.flush()
            self._visible = True
            index = (index + 1) % len(self.frames)
            self._stop.wait(self.interval)

    def _clear_width(self) -> int:
        return max(len(self.text.format(frame=frame)) for frame in self.frames)
