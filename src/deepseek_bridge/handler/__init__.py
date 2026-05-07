from __future__ import annotations

from http.server import BaseHTTPRequestHandler

from deepseek_bridge import __version__

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
    def config(self):
        return self.server.config

    @property
    def reasoning_store(self):
        return self.server.reasoning_store

    @property
    def trace_writer(self):
        return getattr(self.server, "trace_writer", None)

    @property
    def upstream_pool(self):
        return self.server.upstream_pool

    def log_message(self, fmt, *args):
        pass
