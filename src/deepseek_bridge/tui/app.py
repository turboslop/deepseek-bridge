"""TUI Dashboard application."""

from __future__ import annotations

from typing import Any

from textual.app import App, ComposeResult
from textual.widgets import TabbedContent, TabPane

from .config import ConfigScreen
from .dashboard import DashboardScreen
from .logs import LogsScreen


class TuiApp(App[None]):
    """Textual TUI dashboard for deepseek-bridge."""

    TITLE = "DeepSeek Bridge"

    CSS = """
    TabbedContent { height: 1fr; }
    TabPane { height: 1fr; }
    Screen { overflow-y: auto; }
    ConfigScreen { height: 1fr; padding: 1 3; }
    ConfigScreen Label { padding-top: 1; }
    ConfigScreen Select { width: 1fr; }
    ConfigScreen Input { width: 1fr; }
    #config-title { padding: 1 0; }
    #config-status { height: 1; }
    #save-btn { margin: 1 0 0 3; min-height: 3; }
    DashboardScreen { height: 1fr; }
    #dashboard-left { width: 2fr; padding: 1 2; }
    #dashboard-right { width: 1fr; padding: 1 2; }
    DashboardScreen Select { width: 1fr; }
    DashboardScreen Input { width: 1fr; }
    DashboardScreen Button { width: 1fr; }
    """

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
        self.title = "DeepSeek Bridge"
