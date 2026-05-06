"""TUI Dashboard application."""

from __future__ import annotations

from typing import Any

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.widgets import TabbedContent, TabPane

from .config import ConfigScreen
from .dashboard import DashboardScreen
from .logs import LogsScreen


class TuiApp(App[None]):
    """Textual TUI dashboard for deepseek-bridge."""

    TITLE = "DeepSeek Bridge"

    CSS = """
    TabbedContent { height: 1fr; }
    TabbedContent > ContentSwitcher { height: 1fr; }
    TabPane { height: 1fr; padding: 0; }
    Screen { overflow-y: auto; }
    Label { padding-top: 1; }
    Input { border: none; background: transparent; width: 1fr; }
    #config-title { padding: 1 0; }
    #config-status { height: 1; }
    .config-cat { padding-top: 1; color: $text-muted; }
    DashboardScreen { height: 1fr; }
    #dashboard-left { width: 2fr; padding: 1 2; }
    #dashboard-right { width: 1fr; padding: 1 2; }
    """

    BINDINGS = [
        Binding("ctrl+s", "save_config", "Save config"),
    ]

    def __init__(
        self,
        server_config: Any = None,
        server: Any = None,
    ) -> None:
        super().__init__()
        self.server_config = server_config
        self.server = server

    def compose(self) -> ComposeResult:
        with TabbedContent("Dashboard", "Config", "Logs"):
            with TabPane("Dashboard"):
                yield DashboardScreen()
            with TabPane("Config"):
                yield ConfigScreen()
            with TabPane("Logs"):
                yield LogsScreen()

    def on_mount(self) -> None:
        import sys
        sys.stdout.write("\x1b]0;DeepSeek Bridge\x07")
