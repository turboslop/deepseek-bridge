"""Live stats dashboard screen."""
from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path

from textual.app import ComposeResult
from textual.containers import Vertical
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
    tokens_per_sec: float = 0.0
    cache_hit_rate: str = ""
    last_model: str = ""
    last_elapsed: str = ""
    last_tokens: str = ""
    local_url: str = ""
    api_url: str = ""
    upstream_url: str = ""
    ollama_url: str = ""


class DashboardScreen(Vertical):
    """Live statistics for the proxy server."""

    _prev_req_count: int = 0
    _prev_snapshot_time: float = 0.0

    def compose(self) -> ComposeResult:
        with Vertical(id="dashboard-stats"):
            yield Static("Connecting...", id="dashboard-text")

    def on_mount(self) -> None:
        self._prev_snapshot_time = time.monotonic()
        self.set_interval(1.0, self.refresh_stats)

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
            snap.api_url = f"{public_url.rstrip('/')}/v1" if public_url else snap.local_url
            snap.upstream_url = f"{config.upstream_base_url}/chat/completions"
            snap.ollama_url = f"http://{host}:{port}"

        lines: list[str] = []
        lines.append(f"  [bold]Requests[/]     {snap.req_count:,} total  |  {snap.req_rate:.1f} req/s")
        pool_line = (
            f"  [bold]Thread Pool[/]  {snap.active_threads}/{snap.max_workers}"
            f" active  |  queue: {snap.queue_size}"
        )
        lines.append(pool_line)
        lines.append(f"  [bold]DB[/]           {snap.db_size}  |  {snap.db_rows:,} rows")
        lines.append(f"  [bold]Uptime[/]       {_fmt_hms(snap.uptime_seconds)}")

        if snap.local_url:
            lines.append("")
            lines.append("[bold]Connection[/]")
            lines.append(f"  Cursor Base URL: {snap.api_url}")
            lines.append(f"  Upstream:        {snap.upstream_url}")
            lines.append(f"  Ollama:          {snap.ollama_url}")

        widget = self.query_one("#dashboard-text", Static)
        widget.update("\n".join(lines))
