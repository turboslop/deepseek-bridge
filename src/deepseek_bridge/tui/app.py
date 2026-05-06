from __future__ import annotations

from typing import Any

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.widgets import TabbedContent, TabPane

from .config import ConfigScreen
from .dashboard import DashboardScreen


class TuiApp(App[None]):

    TITLE = "DeepSeek Bridge"

    CSS = """
    TabbedContent { height: 1fr; }
    TabbedContent > ContentSwitcher { height: 1fr; }
    TabPane { height: 1fr; padding: 0; }
    Screen { overflow-y: auto; }
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
        with TabbedContent("Dashboard", "Config"):
            with TabPane("Dashboard"):
                yield DashboardScreen()
            with TabPane("Config"):
                yield ConfigScreen()

    def on_mount(self) -> None:
        import sys
        sys.stdout.write("\x1b]0;DeepSeek Bridge\x07")
