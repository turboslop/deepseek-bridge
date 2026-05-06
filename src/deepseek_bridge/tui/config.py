from __future__ import annotations

from dataclasses import replace
from typing import Any

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.widgets import Static

CHOICES: dict[str, list[str]] = {
    "thinking": ["enabled", "disabled"],
    "reasoning_effort": ["low", "medium", "high", "max", "xhigh"],
    "display_reasoning": ["true", "false"],
    "collapsible_reasoning": ["true", "false"],
    "ngrok": ["true", "false"],
    "cors": ["true", "false"],
    "ollama": ["true", "false"],
    "verbose": ["true", "false"],
    "compact": ["true", "false"],
}

FIELDS = [
    ("model", "upstream_model", "Model", False),
    ("base_url", "upstream_base_url", "Base URL", False),
    ("thinking", "thinking", "Thinking", True),
    ("reasoning_effort", "reasoning_effort", "Effort", True),
    ("display_reasoning", "display_reasoning", "Show Reasoning", True),
    ("collapsible_reasoning", "collapsible_reasoning", "Collapsible", True),
    ("host", "host", "Host", False),
    ("port", "port", "Port", False),
    ("ngrok", "ngrok", "Ngrok", True),
    ("cors", "cors", "CORS", True),
    ("ollama", "ollama", "Ollama", True),
    ("verbose", "verbose", "Verbose", True),
    ("compact", "compact", "Compact", True),
    ("request_timeout", "request_timeout", "Timeout (s)", False),
    ("log_dir", "log_dir", "Log Dir", False),
]


class ConfigScreen(VerticalScroll, can_focus=True):

    BINDINGS = [
        Binding("up", "cursor_up", "Up", show=False),
        Binding("down", "cursor_down", "Down", show=False),
        Binding("left", "cycle_left", "Cycle", show=False),
        Binding("right", "cycle_right", "Cycle", show=False),
        Binding("enter", "edit_field", "Edit", show=False),
        Binding("escape", "cancel_edit", "Cancel", show=False),
        Binding("ctrl+s", "save_config", "Save"),
    ]

    _cursor: int = 0
    _editing: int | None = None
    _edit_buffer: str = ""

    def compose(self) -> ComposeResult:
        yield Static("", id="config-display")

    def on_mount(self) -> None:
        self._render()

    def _render(self) -> None:
        config = getattr(self.app, "server_config", None)
        if config is None:
            self.query_one("#config-display", Static).update("no config loaded")
            return

        lines: list[str] = []
        lines.append("[bold]Configuration[/]  ([italic]Ctrl+S[/] save, [italic]arrows[/] nav, [italic]enter[/] edit)")
        lines.append("")

        for i, (_wid, attr, label, has_choices) in enumerate(FIELDS):
            raw = getattr(config, attr, "")
            if raw is None:
                val = ""
            elif isinstance(raw, bool):
                val = "true" if raw else "false"
            else:
                val = str(raw)

            cursor = "  " if i != self._cursor else "[reverse]>[/] "
            edit_marker = ""

            if i == self._editing:
                val = f"[bold underline]{self._edit_buffer}_[/]"
            elif has_choices:
                choices = CHOICES.get(_wid, [])
                idx = choices.index(val) if val in choices else -1
                if idx >= 0:
                    arrows = []
                    for ci, cv in enumerate(choices):
                        marker = f" [reverse]{cv}[/]" if ci == idx else f" {cv}"
                        arrows.append(marker)
                    val = "".join(arrows)
                else:
                    val = f" {val}"
            else:
                val = f" {val}"

            lines.append(f"{cursor}{label}:{val}")

        lines.append("")
        lines.append("[dim]saved changes require restart[/]")

        self.query_one("#config-display", Static).update("\n".join(lines))

    def action_cursor_up(self) -> None:
        if self._editing is not None:
            return
        self._cursor = (self._cursor - 1) % len(FIELDS)
        self._render()

    def action_cursor_down(self) -> None:
        if self._editing is not None:
            return
        self._cursor = (self._cursor + 1) % len(FIELDS)
        self._render()

    def action_cycle_left(self) -> None:
        if self._editing is not None:
            return
        self._cycle(-1)

    def action_cycle_right(self) -> None:
        if self._editing is not None:
            return
        self._cycle(1)

    def _cycle(self, direction: int) -> None:
        _wid, attr, _label, has_choices = FIELDS[self._cursor]
        if not has_choices:
            return
        choices = CHOICES.get(_wid, [])
        if not choices:
            return
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
        self._apply_attr(attr, new_val)

    def action_edit_field(self) -> None:
        if self._editing is not None:
            return
        _wid, attr, _label, has_choices = FIELDS[self._cursor]
        if has_choices:
            return
        config = getattr(self.app, "server_config", None)
        if config is None:
            return
        raw = getattr(config, attr, "")
        if raw is None:
            self._edit_buffer = ""
        elif isinstance(raw, bool):
            self._edit_buffer = "true" if raw else "false"
        else:
            self._edit_buffer = str(raw)
        self._editing = self._cursor
        self._render()

    def action_cancel_edit(self) -> None:
        if self._editing is None:
            return
        _wid, attr, _label, _has = FIELDS[self._editing]
        config = getattr(self.app, "server_config", None)
        if config is None:
            return
        raw = getattr(config, attr, "")
        if raw is None:
            self._edit_buffer = ""
        elif isinstance(raw, bool):
            self._edit_buffer = "true" if raw else "false"
        else:
            self._edit_buffer = str(raw)
        self._apply_attr(attr, self._edit_buffer)
        self._editing = None
        self._edit_buffer = ""
        self._render()

    def on_key(self, event) -> None:
        if self._editing is None:
            return
        if event.key == "escape":
            self.action_cancel_edit()
            return
        if event.key == "enter":
            self._apply_attr(FIELDS[self._editing][1], self._edit_buffer)
            self._editing = None
            self._edit_buffer = ""
            self._render()
            return
        if event.key == "backspace":
            self._edit_buffer = self._edit_buffer[:-1]
        elif len(event.key) == 1 and event.key.isprintable():
            self._edit_buffer += event.key
        else:
            return
        self._render()

    def _apply_attr(self, attr: str, raw: str) -> None:
        config = getattr(self.app, "server_config", None)
        if config is None:
            return
        updates: dict[str, Any] = {}
        if raw == "" and attr in ("log_dir",):
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
        elif raw.lower() in ("true", "false", "enabled", "disabled"):
            updates[attr] = raw.lower() in ("true", "enabled")
        else:
            updates[attr] = raw
        try:
            self.app.server_config = replace(config, **updates)  # type: ignore[attr-defined]
        except (TypeError, ValueError):
            pass

    def action_save_config(self) -> None:
        self._render()
