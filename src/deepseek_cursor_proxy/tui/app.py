"""TUI Dashboard application."""
from __future__ import annotations

from typing import Any

from textual.app import App, ComposeResult
from textual.widgets import Footer, Header, TabbedContent, TabPane

from .config import ConfigScreen
from .dashboard import DashboardScreen
from .logs import LogsScreen


class TuiApp(App[None]):
    """Textual TUI dashboard for deepseek-cursor-proxy."""

    CSS = """
    TabbedContent {
        height: 1fr;
    }

    Screen {
        overflow-y: auto;
    }
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
        yield Header()
        with TabbedContent("Dashboard", "Config", "Logs"):
            with TabPane("Dashboard"):
                yield DashboardScreen()
            with TabPane("Config"):
                yield ConfigScreen()
            with TabPane("Logs"):
                yield LogsScreen()
        yield Footer()
