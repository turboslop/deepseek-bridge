"""Live stats dashboard screen."""

from __future__ import annotations

import time
from dataclasses import dataclass, replace
from pathlib import Path

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, VerticalScroll
from textual.widgets import Static


def _fmt_hms(seconds: float) -> str:
    s = int(seconds)
    h, m = divmod(s, 3600)
    m, s = divmod(m, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def _fmt_mb(path: Path) -> str:
    try:
        size_mb = path.stat().st_size / (1024 * 1024)
        return f"{size_mb:.1f} MB"
    except (OSError, AttributeError):
        return "N/A"


QUICK_FIELDS = [
    ("thinking", "thinking", "Thinking", ["enabled", "disabled"]),
    ("reasoning_effort", "reasoning_effort", "Effort", ["low", "medium", "high", "max", "xhigh"]),
    ("display_reasoning", "display_reasoning", "Show Thinking", ["true", "false"]),
    ("ngrok", "ngrok", "Ngrok", ["true", "false"]),
]

LOG_MAX = 8
_log_lines: list[str] = []


def _add_log(msg: str) -> None:
    _log_lines.append(msg)
    while len(_log_lines) > LOG_MAX:
        _log_lines.pop(0)


@dataclass
class _DashboardSnapshot:

    req_count: int = 0
    req_rate: float = 0.0
    uptime_seconds: float = 0.0
    active_threads: int = 0
    max_workers: int = 0
    queue_size: int = 0
    db_size: str = ""
    db_rows: int = 0
    local_url: str = ""
    api_url: str = ""
    upstream_url: str = ""
    ollama_url: str = ""


class DashboardScreen(Horizontal, can_focus=True):

    _prev_req_count: int = 0
    _prev_snapshot_time: float = 0.0
    _q_cursor: int = 0

    BINDINGS = [
        Binding("up", "q_up", "Up", show=False),
        Binding("down", "q_down", "Down", show=False),
        Binding("left", "q_left", "Cycle left", show=False),
        Binding("right", "q_right", "Cycle right", show=False),
    ]

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="dashboard-left"):
            yield Static("Connecting...", id="dashboard-stats")
            yield Static("", id="dashboard-urls")
            yield Static("", id="dashboard-logs")
        with VerticalScroll(id="dashboard-right"):
            yield Static("", id="quick-config")

    def on_mount(self) -> None:
        self._prev_snapshot_time = time.monotonic()
        self.set_interval(1.0, self.refresh_stats)

    def action_q_up(self) -> None:
        self._q_cursor = (self._q_cursor - 1) % len(QUICK_FIELDS)

    def action_q_down(self) -> None:
        self._q_cursor = (self._q_cursor + 1) % len(QUICK_FIELDS)

    def action_q_left(self) -> None:
        self._cycle_quick(-1)

    def action_q_right(self) -> None:
        self._cycle_quick(1)

    def _cycle_quick(self, direction: int) -> None:
        _wid, attr, _label, choices = QUICK_FIELDS[self._q_cursor]
        config = getattr(self.app, "server_config", None)
        if config is None:
            return
        raw = getattr(config, attr, "")
        if isinstance(raw, bool):
            current = "true" if raw else "false"
        else:
            current = str(raw)
        try:
            idx = choices.index(current)
        except ValueError:
            idx = 0
        new_val = choices[(idx + direction) % len(choices)]
        updates = {attr: new_val if new_val not in ("true", "false") else new_val == "true"}
        try:
            self.app.server_config = replace(config, **updates)
            _add_log(f"set {_wid}={new_val}")
        except (TypeError, ValueError):
            pass

    def refresh_stats(self) -> None:
        app = self.app
        server = getattr(app, "server", None)
        if server is None:
            return

        snap = _DashboardSnapshot()
        snap.req_count = getattr(server, "request_count", 0)
        now = time.monotonic()
        elapsed = now - self._prev_snapshot_time
        if elapsed > 0:
            snap.req_rate = (snap.req_count - self._prev_req_count) / elapsed
        self._prev_req_count = snap.req_count
        self._prev_snapshot_time = now

        start: float = getattr(server, "start_time", 0.0)
        snap.uptime_seconds = max(0.0, now - start) if start > 0 else 0.0

        executor = getattr(server, "executor", None)
        if executor is not None:
            try:
                snap.active_threads = len(executor._threads)
            except Exception:
                snap.active_threads = -1
            try:
                snap.max_workers = executor._max_workers
            except Exception:
                snap.max_workers = -1
            try:
                snap.queue_size = executor._work_queue.qsize()
            except Exception:
                snap.queue_size = -1
        else:
            snap.active_threads = -1
            snap.max_workers = -1
            snap.queue_size = -1

        store = getattr(server, "reasoning_store", None)
        if store is not None:
            db_path = getattr(store, "reasoning_content_path", None)
            if isinstance(db_path, Path):
                snap.db_size = _fmt_mb(db_path)
                try:
                    row = store._conn.execute("SELECT COUNT(*) FROM reasoning_cache").fetchone()
                    snap.db_rows = row[0] if row else 0
                except Exception:
                    snap.db_rows = -1
            else:
                snap.db_size = "in-memory"
                snap.db_rows = -1
        else:
            snap.db_size = "N/A"
            snap.db_rows = -1

        config = getattr(server, "config", None)
        if config:
            host = config.host or "127.0.0.1"
            port = config.port or 9000
            snap.local_url = f"http://{host}:{port}/v1"
            public_url = getattr(server, "public_url", None)
            snap.api_url = f"{public_url.rstrip('/')}/v1" if public_url else snap.local_url
            snap.upstream_url = f"{config.upstream_base_url}/chat/completions"
            snap.ollama_url = f"http://{host}:{port}"

        stats = [
            f"  [bold]Requests[/]     {snap.req_count:,} total  |  {snap.req_rate:.1f} req/s",
            f"  [bold]Thread Pool[/]  {snap.active_threads}/{snap.max_workers} active  |  queue: {snap.queue_size}",
            f"  [bold]DB[/]           {snap.db_size}  |  {snap.db_rows:,} rows",
            f"  [bold]Uptime[/]       {_fmt_hms(snap.uptime_seconds)}",
        ]
        self.query_one("#dashboard-stats", Static).update("\n".join(stats))

        urls = ""
        if snap.local_url:
            urls = (
                f"  [bold]API[/]   {snap.api_url}\n"
                f"  [bold]Local[/] {snap.local_url}\n"
                f"  [bold]Ollama[/]{snap.ollama_url}"
            )
        self.query_one("#dashboard-urls", Static).update(urls)

        if _log_lines:
            self.query_one("#dashboard-logs", Static).update("\n".join(["[bold dim]Log[/]"] + _log_lines[-LOG_MAX:]))
        else:
            self.query_one("#dashboard-logs", Static).update("")

        if config:
            qc = ["[bold]Quick Config[/] ([italic]arrows[/] select, [italic]left/right[/] cycle)"]
            for i, (_wid, attr, label, choices) in enumerate(QUICK_FIELDS):
                raw = getattr(config, attr, "")
                if isinstance(raw, bool):
                    val = "true" if raw else "false"
                else:
                    val = str(raw)
                marker = "[reverse] >[/] " if i == self._q_cursor else "   "
                try:
                    idx = choices.index(val)
                except ValueError:
                    idx = 0
                cycle = " ".join(f"[reverse]{c}[/]" if j == idx else c for j, c in enumerate(choices))
                qc.append(f"{marker}{label}: {cycle}")
            self.query_one("#quick-config", Static).update("\n".join(qc))
        else:
            self.query_one("#quick-config", Static).update("[bold]Quick Config[/]\n\n  (none)")

    def refresh_stats(self) -> None:
        app = self.app
        server = getattr(app, "server", None)
        if server is None:
            return

        snap = _DashboardSnapshot()
        snap.req_count = getattr(server, "request_count", 0)
        now = time.monotonic()
        elapsed = now - self._prev_snapshot_time
        if elapsed > 0:
            snap.req_rate = (snap.req_count - self._prev_req_count) / elapsed
        self._prev_req_count = snap.req_count
        self._prev_snapshot_time = now

        start: float = getattr(server, "start_time", 0.0)
        snap.uptime_seconds = max(0.0, now - start) if start > 0 else 0.0

        executor = getattr(server, "executor", None)
        if executor is not None:
            try:
                snap.active_threads = len(executor._threads)
            except Exception:
                snap.active_threads = -1
            try:
                snap.max_workers = executor._max_workers
            except Exception:
                snap.max_workers = -1
            try:
                snap.queue_size = executor._work_queue.qsize()
            except Exception:
                snap.queue_size = -1
        else:
            snap.active_threads = -1
            snap.max_workers = -1
            snap.queue_size = -1

        store = getattr(server, "reasoning_store", None)
        if store is not None:
            db_path = getattr(store, "reasoning_content_path", None)
            if isinstance(db_path, Path):
                snap.db_size = _fmt_mb(db_path)
                try:
                    row = store._conn.execute(
                        "SELECT COUNT(*) FROM reasoning_cache"
                    ).fetchone()
                    snap.db_rows = row[0] if row else 0
                except Exception:
                    snap.db_rows = -1
            else:
                snap.db_size = "in-memory"
                snap.db_rows = -1
        else:
            snap.db_size = "N/A"
            snap.db_rows = -1

        config = getattr(server, "config", None)
        if config:
            host = config.host or "127.0.0.1"
            port = config.port or 9000
            snap.local_url = f"http://{host}:{port}/v1"
            public_url = getattr(server, "public_url", None)
            snap.api_url = (
                f"{public_url.rstrip('/')}/v1" if public_url else snap.local_url
            )
            snap.upstream_url = f"{config.upstream_base_url}/chat/completions"
            snap.ollama_url = f"http://{host}:{port}"

        lines: list[str] = []
        lines.append(
            f"  [bold]Requests[/]     {snap.req_count:,} total  |  {snap.req_rate:.1f} req/s"
        )
        lines.append(
            f"  [bold]Thread Pool[/]  {snap.active_threads}/{snap.max_workers}"
            f" active  |  queue: {snap.queue_size}"
        )
        lines.append(
            f"  [bold]DB[/]           {snap.db_size}  |  {snap.db_rows:,} rows"
        )
        lines.append(f"  [bold]Uptime[/]       {_fmt_hms(snap.uptime_seconds)}")

        if snap.local_url:
            lines.append("")
            lines.append("[bold]Connection[/]")
            lines.append(f"  {snap.api_url}")
            lines.append(f"  upstream: {snap.upstream_url}")
            lines.append(f"  ollama:   {snap.ollama_url}")
        self.query_one("#dashboard-text", Static).update("\n".join(lines))

        if config:
            thinking = getattr(config, "thinking", "?")
            effort = getattr(config, "reasoning_effort", "?")
            model = getattr(config, "upstream_model", "?")
            display = "on" if getattr(config, "display_reasoning", True) else "off"
            ngrok = "on" if getattr(config, "ngrok", True) else "off"
            cfg = [
                "[bold]Config[/]",
                "",
                f"  model:        {model}",
                f"  thinking:     {thinking}",
                f"  effort:       {effort}",
                f"  show thinking: {display}",
                f"  ngrok:        {ngrok}",
            ]
            self.query_one("#dashboard-config", Static).update("\n".join(cfg))
        else:
            self.query_one("#dashboard-config", Static).update("[bold]Config[/]\n\n  (none)")
