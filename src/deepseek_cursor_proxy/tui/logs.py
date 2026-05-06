from __future__ import annotations

import os
from pathlib import Path

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import RichLog, Static


class LogsScreen(Vertical):
    """Display recent proxy logs via tailing."""

    _last_size: int = 0
    _log_file: Path | None = None

    def compose(self) -> ComposeResult:
        yield RichLog(id="log-viewer", highlight=True, markup=True)
        yield Static("No log file configured.", id="log-status")

    def on_mount(self) -> None:
        app = self.app
        config = getattr(app, "server_config", None)
        if config is not None:
            log_dir = getattr(config, "log_dir", None)
            if log_dir is not None:
                self._find_latest_log(log_dir)

        if self._log_file is None:
            self.query_one("#log-status", Static).update(
                "No log directory configured. Use --log-dir to enable persistent logs."
            )
            self.query_one("#log-viewer", RichLog).write(
                "No log file found. Use --log-dir to enable persistent logging."
            )
            return

        self.query_one("#log-status", Static).update(
            f"Tailing: {self._log_file}"
        )
        self._tail()
        self.set_interval(2.0, self._tail)

    def _find_latest_log(self, log_dir: str) -> None:
        """Find the most recent log file in the log directory."""
        try:
            dir_path = Path(log_dir)
            if not dir_path.is_dir():
                return
            log_files = sorted(
                dir_path.glob("proxy-*.log"),
                key=os.path.getmtime,
                reverse=True,
            )
            if log_files:
                self._log_file = log_files[0]
                self._last_size = self._log_file.stat().st_size
        except (OSError, PermissionError):
            return

    def _tail(self) -> None:
        """Read new lines from the log file and append to viewer."""
        if self._log_file is None:
            return
        try:
            current_size = self._log_file.stat().st_size
            if current_size <= self._last_size:
                return
            with open(self._log_file, "r", encoding="utf-8", errors="replace") as f:
                f.seek(self._last_size)
                new_content = f.read()
                self._last_size = current_size
            if new_content:
                log_widget = self.query_one("#log-viewer", RichLog)
                for line in new_content.rstrip("\n").split("\n"):
                    log_widget.write(line)
        except (OSError, PermissionError):
            pass
