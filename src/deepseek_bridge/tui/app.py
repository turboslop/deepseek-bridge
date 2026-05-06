from __future__ import annotations

import time
from dataclasses import replace
from pathlib import Path
from typing import Any

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, VerticalScroll
from textual.widgets import Static


FIELDS = [
    ("thinking", "thinking", "Thinking", ["enabled", "disabled"]),
    (
        "reasoning_effort",
        "reasoning_effort",
        "Effort",
        ["low", "medium", "high", "max", "xhigh"],
    ),
    ("display_reasoning", "display_reasoning", "Show Thinking", ["true", "false"]),
    ("ngrok", "ngrok", "Ngrok", ["true", "false"]),
    ("cors", "cors", "CORS", ["true", "false"]),
    ("ollama", "ollama", "Ollama", ["true", "false"]),
    ("verbose", "verbose", "Verbose", ["true", "false"]),
    ("compact", "compact", "Compact", ["true", "false"]),
    (
        "collapsible_reasoning",
        "collapsible_reasoning",
        "Collapsible",
        ["true", "false"],
    ),
    ("host", "host", "Host", None),
    ("port", "port", "Port", None),
    ("request_timeout", "request_timeout", "Timeout (s)", None),
    ("log_dir", "log_dir", "Log Dir", None),
]

BOOL_FIELDS = {
    "display_reasoning",
    "ngrok",
    "cors",
    "ollama",
    "verbose",
    "compact",
    "collapsible_reasoning",
}

LOG_MAX = 12
_log_lines: list[str] = []


def _add_log(msg: str) -> None:
    _log_lines.append(msg)
    if len(_log_lines) > 100:
        _log_lines[:] = _log_lines[-100:]


class TuiApp(App[None]):

    TITLE = "DeepSeek Bridge"

    CSS = """
    #top-left { height: auto; }
    #bottom-left { height: 1fr; }
    #left-col { width: 2fr; padding: 1 1 1 2; }
    #right-panel { width: 1fr; padding: 1 2; }
    """

    BINDINGS = [
        Binding("up", "cfg_up", "Up", show=False),
        Binding("down", "cfg_down", "Down", show=False),
        Binding("left", "cfg_left", "Cycle left", show=False),
        Binding("right", "cfg_right", "Cycle right", show=False),
        Binding("enter", "cfg_edit", "Edit", show=False),
        Binding("ctrl+s", "save_config", "Save"),
        Binding("p", "toggle_pause", "Pause"),
    ]

    _cfg_cursor: int = 0
    _editing: int | None = None
    _edit_buf: str = ""
    _prev_req: int = 0
    _prev_time: float = 0.0

    def __init__(self, server_config=None, server=None) -> None:
        super().__init__()
        self.server_config = server_config
        self.server = server

    def compose(self) -> ComposeResult:
        with Horizontal():
            with VerticalScroll(id="left-col"):
                with VerticalScroll(id="top-left"):
                    yield Static("", id="stats")
                    yield Static("", id="urls")
                with VerticalScroll(id="bottom-left"):
                    yield Static("", id="logs")
            with VerticalScroll(id="right-panel"):
                yield Static("", id="config")

    def on_mount(self) -> None:
        import sys
        import time

        sys.stdout.write("\x1b]0;DeepSeek Bridge\x07")
        self._prev_time = time.monotonic()
        self.set_interval(1.0, self._refresh)
        _add_log("started")

    def _refresh(self) -> None:
        server = self.server
        config = self.server_config
        now = time.monotonic()

        # --- Stats ---
        req = getattr(server, "request_count", 0)
        elapsed = now - self._prev_time
        rate = (req - self._prev_req) / elapsed if elapsed > 0 else 0
        self._prev_req = req
        self._prev_time = now

        start = getattr(server, "start_time", now)
        uptime = int(max(0, now - start))
        h, m = divmod(uptime // 60, 60)
        uptime_s = f"{h:02d}:{m:02d}"

        exe = getattr(server, "executor", None)
        active = max_workers = queue = "?"
        if exe:
            try:
                active = len(exe._threads)
            except Exception:
                pass
            try:
                max_workers = exe._max_workers
            except Exception:
                pass
            try:
                queue = exe._work_queue.qsize()
            except Exception:
                pass

        store = getattr(server, "reasoning_store", None)
        db_size = "N/A"
        db_rows = "?"
        if store:
            db_path = getattr(store, "reasoning_content_path", None)
            if isinstance(db_path, Path):
                try:
                    db_size = f"{db_path.stat().st_size / (1024 * 1024):.1f}MB"
                except Exception:
                    pass
                try:
                    row = store._conn.execute(
                        "SELECT COUNT(*) FROM reasoning_cache"
                    ).fetchone()
                    db_rows = str(row[0]) if row else "0"
                except Exception:
                    pass

        stats = (
            f"  [bold]DeepSeek Bridge[/]  [dim]uptime {uptime_s}[/]\n"
            f"  requests  {req:,}   ({rate:.1f}/s)\n"
            f"  threads   {active}/{max_workers}   queue {queue}\n"
            f"  db        {db_size}   {db_rows} rows"
        )
        if getattr(self.server, "paused", False):
            stats += "\n  [reverse bold]  PAUSED  [/]"
        self.query_one("#stats", Static).update(stats)

        if config:
            host = config.host or "127.0.0.1"
            port = config.port or 9000
            local = f"http://{host}:{port}/v1"
            ollama = f"http://{host}:{port}"
            public = getattr(server, "public_url", None)
            urls = f"\n  local   {local}"
            if public:
                urls += f"\n  ngrok   {public.rstrip('/')}/v1"
            urls += f"\n  ollama  {ollama}"
            self.query_one("#urls", Static).update(urls)

        visible = _log_lines[-LOG_MAX:]
        log_text = (
            "\n".join(f"  [dim]•[/] {line}" for line in visible) if visible else ""
        )
        self.query_one("#logs", Static).update(log_text)

        # --- Config (right panel) ---
        if config:
            lines = [
                "[bold]Configuration[/]",
                "[dim]arrows nav  enter edit  ctrl+s save[/]",
                "",
            ]
            for i, (_wid, attr, label, choices) in enumerate(FIELDS):
                raw = getattr(config, attr, "")
                if raw is None:
                    val = ""
                elif isinstance(raw, bool):
                    val = "true" if raw else "false"
                else:
                    val = str(raw)

                if i == self._editing:
                    display = f"  {label}: [bold underline]{self._edit_buf}_[/]"
                elif choices:
                    try:
                        idx = choices.index(val)
                    except ValueError:
                        idx = 0
                    parts = []
                    for ci, cv in enumerate(choices):
                        parts.append(f"[reverse]{cv}[/]" if ci == idx else cv)
                    display = f"  {label}: {' '.join(parts)}"
                else:
                    display = f"  {label}: {val}"

                if i == self._cfg_cursor:
                    display = f"[reverse]{display}[/]"

                lines.append(display)

            self.query_one("#config", Static).update("\n".join(lines))
        else:
            self.query_one("#config", Static).update(
                "[bold]Configuration[/]\n\n  (none)"
            )

    # --- Key bindings ---

    def action_cfg_up(self) -> None:
        if self._editing is not None:
            return
        self._cfg_cursor = (self._cfg_cursor - 1) % len(FIELDS)
        self._refresh()

    def action_cfg_down(self) -> None:
        if self._editing is not None:
            return
        self._cfg_cursor = (self._cfg_cursor + 1) % len(FIELDS)
        self._refresh()

    def action_cfg_left(self) -> None:
        if self._editing is not None:
            return
        self._cycle(-1)

    def action_cfg_right(self) -> None:
        if self._editing is not None:
            return
        self._cycle(1)

    def _cycle(self, direction: int) -> None:
        _wid, attr, _label, choices = FIELDS[self._cfg_cursor]
        if not choices:
            return
        config = self.server_config
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
        self._apply(attr, new_val)
        _add_log(f"cfg: {_wid}={new_val}")
        self._refresh()

    def action_cfg_edit(self) -> None:
        _wid, attr, _label, choices = FIELDS[self._cfg_cursor]
        if choices:
            return
        config = self.server_config
        if config is None:
            return
        raw = getattr(config, attr, "")
        if raw is None:
            self._edit_buf = ""
        elif isinstance(raw, bool):
            self._edit_buf = "true" if raw else "false"
        else:
            self._edit_buf = str(raw)
        self._editing = self._cfg_cursor
        self._refresh()

    def action_save_config(self) -> None:
        if self._editing is not None:
            _wid, attr, _label, _choices = FIELDS[self._editing]
            self._apply(attr, self._edit_buf)
            _add_log(f"cfg: {_wid}={self._edit_buf}")
            self._editing = None
            self._edit_buf = ""
            self._refresh()

    def action_toggle_pause(self) -> None:
        if self.server is None:
            return
        self.server.paused = not getattr(self.server, "paused", False)
        state = "paused" if self.server.paused else "resumed"
        _add_log(f"proxy {state}")

    def _apply(self, attr: str, raw: str) -> None:
        config = self.server_config
        if config is None:
            return
        updates: dict[str, Any] = {}
        if raw == "" and attr == "log_dir":
            updates[attr] = None
        elif attr == "port":
            try:
                updates[attr] = int(raw)
            except ValueError:
                return
        elif attr == "request_timeout":
            try:
                updates[attr] = float(raw)
            except ValueError:
                return
        elif attr in BOOL_FIELDS:
            updates[attr] = raw.lower() == "true"
        else:
            updates[attr] = raw
        try:
            self.server_config = replace(config, **updates)
            self.server.config = self.server_config
        except Exception:
            pass

    def on_key(self, event) -> None:
        if self._editing is None:
            return
        if event.name == "escape":
            self._editing = None
            self._edit_buf = ""
            self._refresh()
            return
        if event.name == "enter":
            _wid, attr, _label, _choices = FIELDS[self._editing]
            self._apply(attr, self._edit_buf)
            _add_log(f"cfg: {_wid}={self._edit_buf}")
            self._editing = None
            self._edit_buf = ""
            self._refresh()
            return
        if event.name == "backspace":
            self._edit_buf = self._edit_buf[:-1]
        elif hasattr(event, "key") and event.is_printable:
            self._edit_buf += event.key
        else:
            return
        self._refresh()
