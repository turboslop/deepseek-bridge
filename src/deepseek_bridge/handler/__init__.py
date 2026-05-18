from __future__ import annotations

from http.server import BaseHTTPRequestHandler
from typing import Any, cast

from .. import __version__
from ..config import ProxyConfig
from ..reasoning_store import ReasoningStoreProtocol
from ..server_infrastructure import DeepSeekProxyServer, UpstreamPool
from ..trace import TraceWriter
from ._client import HandlerClient
from ._endpoints import HandlerEndpoints
from ._response import HandlerResponse
from ._routes import HandlerRoutes
from ._streaming import HandlerStreaming
from ._trace import HandlerTrace
from ._upstream import HandlerUpstream


class DeepSeekProxyHandler(
    HandlerRoutes,
    HandlerUpstream,
    HandlerStreaming,
    HandlerResponse,
    HandlerClient,
    HandlerEndpoints,
    HandlerTrace,
    BaseHTTPRequestHandler,
):
    server_version = f"DeepSeekBridge/{__version__}"

    @property
    def config(self) -> ProxyConfig:
        """Proxy configuration from the parent server instance."""
        return cast(DeepSeekProxyServer, self.server).config

    @property
    def reasoning_store(self) -> ReasoningStoreProtocol:
        """Reasoning-content cache from the parent server."""
        return cast(DeepSeekProxyServer, self.server).reasoning_store

    @property
    def trace_writer(self) -> TraceWriter | None:
        """Trace writer for request/response logging, or None if disabled."""
        return cast(
            TraceWriter | None, getattr(self.server, "trace_writer", None)
        )

    @property
    def upstream_pool(self) -> UpstreamPool:
        """urllib3 connection pool for upstream requests."""
        return cast(DeepSeekProxyServer, self.server).upstream_pool

    def log_message(self, fmt: str, *args: Any) -> None:
        pass  # Intentionally suppress default HTTP server log output

    def handle_one_request(self) -> None:
        try:
            super().handle_one_request()
        finally:
            self._record_http_metrics()
            self.close_connection = True
