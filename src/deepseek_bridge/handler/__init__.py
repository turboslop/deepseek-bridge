from __future__ import annotations

from http.server import BaseHTTPRequestHandler

from .. import __version__

from ..config import ProxyConfig
from ..reasoning_store import ReasoningStore
from ..trace import TraceWriter
from ..server_infrastructure import UpstreamPool

from ._routes import HandlerRoutes
from ._upstream import HandlerUpstream
from ._streaming import HandlerStreaming
from ._response import HandlerResponse
from ._client import HandlerClient
from ._endpoints import HandlerEndpoints
from ._trace import HandlerTrace


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
        return self.server.config

    @property
    def reasoning_store(self) -> ReasoningStore:
        """SQLite-backed reasoning-content cache from the parent server."""
        return self.server.reasoning_store

    @property
    def trace_writer(self) -> TraceWriter | None:
        """Trace writer for request/response logging, or None if disabled."""
        return getattr(self.server, "trace_writer", None)

    @property
    def upstream_pool(self) -> UpstreamPool:
        """urllib3 connection pool for upstream requests."""
        return self.server.upstream_pool

    def log_message(self, fmt, *args) -> None:
        pass  # Intentionally suppress default HTTP server log output
