"""Config editor screen."""
from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Vertical, Horizontal
from textual.widgets import Label, Static


class ConfigScreen(Vertical):
    """Display current proxy configuration as read-only values."""

    def compose(self) -> ComposeResult:
        with Vertical(id="config-container"):
            yield Static("Loading configuration...", id="config-display")

    def on_mount(self) -> None:
        self._populate()

    def _populate(self) -> None:
        app = self.app
        config = getattr(app, "server_config", None)
        if config is None:
            self.query_one("#config-display", Static).update(
                "No configuration available."
            )
            return

        lines: list[str] = []
        lines.append("[bold]Current Configuration[/]")
        lines.append("")

        attrs = config.__dataclass_fields__ if hasattr(config, "__dataclass_fields__") else {}
        if not attrs:
            for key, value in config.__dict__.items():
                lines.append(f"  {key}: {value}")
        else:
            for key in sorted(attrs.keys()):
                value = getattr(config, key, "?")
                lines.append(f"  [bold]{key}[/]: {value}")

        self.query_one("#config-display", Static).update("\n".join(lines))
