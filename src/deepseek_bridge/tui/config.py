"""Config editor screen -- view and edit proxy configuration."""
from __future__ import annotations

from dataclasses import replace
from typing import Any

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import Button, Input, Label, Static

CONFIG_FIELDS = [
    ("model", "upstream_model", "Model", "Model"),
    ("base_url", "upstream_base_url", "Base URL", "Model"),
    ("thinking", "thinking", "Thinking", "Model"),
    ("reasoning_effort", "reasoning_effort", "Reasoning Effort", "Model"),
    ("display_reasoning", "display_reasoning", "Display Reasoning", "Model"),
    ("host", "host", "Host", "Network"),
    ("port", "port", "Port", "Network"),
    ("ngrok", "ngrok", "Ngrok", "Network"),
    ("cors", "cors", "CORS", "Network"),
    ("ollama", "ollama", "Ollama", "Network"),
    ("log_dir", "log_dir", "Log Dir", "Storage"),
    ("verbose", "verbose", "Verbose", "Storage"),
    ("compact", "compact", "Compact", "Storage"),
    ("request_timeout", "request_timeout", "Request Timeout (s)", "Storage"),
]

BOOLEAN_FIELDS = {"display_reasoning", "ngrok", "cors", "ollama", "verbose", "compact"}


class ConfigScreen(Vertical):
    """View and edit proxy configuration at runtime."""

    def compose(self) -> ComposeResult:
        yield Static("[bold]Configuration[/] -- edit and apply changes", id="config-title")
        yield Static("", id="config-status")

        # Yield categories with their children pre-built — NO .mount() in compose!
        for category in ("Model", "Network", "Storage"):
            with Vertical(classes="config-group") as group:
                group.border_title = category
                for display_key, _dataclass_attr, label, cat in CONFIG_FIELDS:
                    if cat != category:
                        continue
                    yield Label(f"  {label}:")
                    yield Input(id=f"cfg-{display_key}", placeholder=str(display_key))

        yield Button("Apply Changes", id="save-btn", variant="primary")

    def on_mount(self) -> None:
        self._populate()

    def _populate(self) -> None:
        config = getattr(self.app, "server_config", None)
        if config is None:
            return
        for display_key, dataclass_attr, _label, _category in CONFIG_FIELDS:
            input_id = f"cfg-{display_key}"
            try:
                widget = self.query_one(f"#{input_id}", Input)
            except Exception:
                continue
            value = getattr(config, dataclass_attr, "")
            if value is None:
                value = ""
            widget.value = str(value)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id != "save-btn":
            return
        config = getattr(self.app, "server_config", None)
        if config is None:
            self._status("No configuration available")
            return

        updates: dict[str, Any] = {}
        for display_key, dataclass_attr, _label, _category in CONFIG_FIELDS:
            input_id = f"cfg-{display_key}"
            try:
                widget = self.query_one(f"#{input_id}", Input)
            except Exception:
                continue
            raw = widget.value.strip()
            if raw == "" and display_key == "log_dir":
                updates[dataclass_attr] = None
                continue
            if display_key in BOOLEAN_FIELDS:
                lower = raw.lower()
                if lower in ("true", "1", "yes", "on"):
                    updates[dataclass_attr] = True
                elif lower in ("false", "0", "no", "off"):
                    updates[dataclass_attr] = False
                else:
                    self._status(f"Invalid boolean for {display_key}: {raw}")
                    return
                continue
            if display_key == "port":
                try:
                    updates[dataclass_attr] = int(raw)
                except ValueError:
                    self._status(f"Invalid port number: {raw}")
                    return
                continue
            if display_key == "request_timeout":
                try:
                    updates[dataclass_attr] = float(raw)
                except ValueError:
                    self._status(f"Invalid timeout: {raw}")
                    return
                continue
            updates[dataclass_attr] = raw

        try:
            self.app.server_config = replace(config, **updates)  # type: ignore[attr-defined]
            self._status("Applied -- some changes may require restart")
        except (TypeError, ValueError) as exc:
            self._status(f"Error: {exc}")

    def _status(self, msg: str) -> None:
        self.query_one("#config-status", Static).update(msg)
