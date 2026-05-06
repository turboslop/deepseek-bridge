"""Config editor screen -- view and edit proxy configuration."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from textual.app import ComposeResult
from textual.containers import VerticalScroll
from textual.widgets import Input, Label, Static

FIELDS = [
    ("thinking", "thinking", "Thinking  (enabled / disabled)"),
    ("reasoning_effort", "reasoning_effort", "Effort  (low / medium / high / max / xhigh)"),
    ("display_reasoning", "display_reasoning", "Show Reasoning  (true / false)"),
    ("collapsible_reasoning", "collapsible_reasoning", "Collapsible  (true / false)"),
    ("host", "host", "Host"),
    ("port", "port", "Port"),
    ("ngrok", "ngrok", "Ngrok  (true / false)"),
    ("cors", "cors", "CORS  (true / false)"),
    ("ollama", "ollama", "Ollama  (true / false)"),
    ("verbose", "verbose", "Verbose  (true / false)"),
    ("compact", "compact", "Compact  (true / false)"),
    ("request_timeout", "request_timeout", "Request Timeout (s)"),
    ("log_dir", "log_dir", "Log Dir  (empty to disable)"),
]


class ConfigScreen(VerticalScroll, can_focus=True):

    def compose(self) -> ComposeResult:
        yield Static("[bold]Configuration[/]  ([italic]Ctrl+S[/] to save)", id="config-title")
        yield Static("", id="config-status")
        for widget_id, attr, label in FIELDS:
            yield Label(f" {label}")
            yield Input(placeholder=label, id=f"cfg-{widget_id}")

    def on_mount(self) -> None:
        self._populate()

    def _populate(self) -> None:
        config = getattr(self.app, "server_config", None)
        if config is None:
            return
        for widget_id, attr, _label in FIELDS:
            try:
                widget = self.query_one(f"#cfg-{widget_id}", Input)
            except Exception:
                continue
            raw = getattr(config, attr, "")
            if raw is None:
                widget.value = ""
            elif isinstance(raw, bool):
                widget.value = "true" if raw else "false"
            else:
                widget.value = str(raw)

    def action_save_config(self) -> None:
        config = getattr(self.app, "server_config", None)
        if config is None:
            self._status("no config")
            return
        updates: dict[str, Any] = {}
        for widget_id, attr, _label in FIELDS:
            try:
                widget = self.query_one(f"#cfg-{widget_id}", Input)
            except Exception:
                continue
            raw = widget.value.strip()
            if raw == "" and widget_id == "log_dir":
                updates[attr] = None
                continue
            if widget_id == "port":
                try:
                    updates[attr] = int(raw)
                except ValueError:
                    self._status(f"bad port: {raw}")
                    return
                continue
            if widget_id == "request_timeout":
                try:
                    updates[attr] = float(raw)
                except ValueError:
                    self._status(f"bad timeout: {raw}")
                    return
                continue
            if raw.lower() in ("true", "false", "enabled", "disabled"):
                updates[attr] = raw.lower() in ("true", "enabled")
            else:
                updates[attr] = raw
        try:
            self.app.server_config = replace(config, **updates)  # type: ignore[attr-defined]
            self._status("saved")
        except (TypeError, ValueError) as exc:
            self._status(f"error: {exc}")

    def _status(self, msg: str) -> None:
        try:
            self.query_one("#config-status", Static).update(msg)
        except Exception:
            pass
