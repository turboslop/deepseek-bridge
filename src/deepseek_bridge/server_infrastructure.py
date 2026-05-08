"""Server infrastructure: connection pool, HTTP server, bounded thread-pool server.

These classes handle the transport layer — upstream HTTP pooling, request
queuing/dispatch, and thread-pool management. The request handler logic
lives in handler.py.
"""

from __future__ import annotations

import contextlib
import json
import time
from concurrent.futures import ThreadPoolExecutor
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any

import urllib3

from .config import ProxyConfig
from .helpers import format_count
from .logging import LOG
from .reasoning_store import ReasoningStore
from .trace import TraceWriter


class UpstreamPool:
    def __init__(self, max_connections: int = 10) -> None:
        self._pool = urllib3.PoolManager(
            maxsize=max_connections,
            block=True,
            retries=urllib3.Retry(connect=1, read=0, redirect=0, status=0),
        )


class DeepSeekProxyServer(ThreadingHTTPServer):
    config: ProxyConfig
    reasoning_store: ReasoningStore
    trace_writer: TraceWriter | None
    upstream_pool: UpstreamPool
    request_count: int = 0
    start_time: float = 0.0


class BoundedThreadPoolHTTPServer(DeepSeekProxyServer):
    """ThreadingHTTPServer variant that uses a fixed-size ThreadPoolExecutor."""

    def __init__(self, *args, max_workers: int = 20, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.executor = ThreadPoolExecutor(
            max_workers=max_workers,
            thread_name_prefix="proxy",
        )
        self.daemon_threads = True

    def server_bind(self) -> None:
        super().server_bind()
        config = getattr(self, "config", None)
        timeout = int(config.request_timeout) if config is not None else 300
        self.socket.settimeout(timeout)

    def process_request(self, request, client_address) -> None:
        if hasattr(request, "settimeout"):
            request.settimeout(
                self.socket.gettimeout()
                if hasattr(self, "socket")
                else int(getattr(getattr(self, "config", None), "request_timeout", 300))
            )
        queue_size = self.executor._work_queue.qsize()
        config = getattr(self, "config", None)
        effective_max_queue = config.max_queue_size if config is not None else 50
        if queue_size > effective_max_queue:
            LOG.warning(
                "rejecting request from %s: queue full (%s queued)",
                client_address,
                queue_size,
            )
            self._reject_connection(request)
            return
        with contextlib.suppress(RuntimeError):
            self.executor.submit(self.process_request_thread, request, client_address)

    @staticmethod
    def _reject_connection(request: Any) -> None:
        import socket

        try:
            if isinstance(request, socket.socket):
                body = json.dumps(
                    {
                        "error": {
                            "message": "Server overloaded — too many queued requests",
                            "type": "server_error",
                            "code": "service_unavailable",
                        }
                    }
                ).encode("utf-8")
                request.sendall(
                    b"HTTP/1.1 503 Service Unavailable\r\n"
                    b"Content-Type: application/json\r\n"
                    b"Content-Length: " + str(len(body)).encode("utf-8") + b"\r\n"
                    b"Connection: close\r\n"
                    b"\r\n" + body
                )
                request.close()
        except Exception as exc:
            LOG.warning("failed to close rejected connection: %s", exc)

    def _log_pool_utilization(self) -> None:
        try:
            active: int | str = len(self.executor._threads)
        except Exception as exc:
            LOG.warning("failed to count active threads: %s", exc)
            active = "?"
        try:
            queue_size: int | str = (
                self.executor._work_queue.qsize()
                if hasattr(self.executor, "_work_queue")
                else "?"
            )
        except Exception as exc:
            LOG.warning("failed to check queue size: %s", exc)
            queue_size = "?"
        LOG.info(
            "thread pool: max_workers=%s active=%s queue=%s",
            self.executor._max_workers,
            active,
            queue_size,
        )

    def _log_db_stats(self) -> None:
        try:
            store = self.reasoning_store
            if not isinstance(store.reasoning_content_path, Path):
                return
            size_mb = store.reasoning_content_path.stat().st_size / (1024 * 1024)
            row = store._conn.execute("SELECT COUNT(*) FROM reasoning_cache").fetchone()
            row_count = int(row[0]) if row else 0
            LOG.info(
                "db stats: %s size=%.1fMB rows=%s",
                store.reasoning_content_path,
                size_mb,
                format_count(row_count),
            )
        except Exception as exc:
            LOG.warning("failed to log DB stats: %s", exc)

    def _log_heartbeat(self) -> None:
        parts = [f"heartbeat: req={format_count(self.request_count)}"]
        try:
            parts.append(
                f"pool={len(self.executor._threads)}/{self.executor._max_workers}"
            )
        except Exception as exc:
            LOG.warning("failed to read pool stats: %s", exc)
            parts.append("pool=?")
        try:
            store = self.reasoning_store
            if isinstance(store.reasoning_content_path, Path):
                size_mb = store.reasoning_content_path.stat().st_size / (1024 * 1024)
                row = store._conn.execute(
                    "SELECT COUNT(*) FROM reasoning_cache"
                ).fetchone()
                row_count = int(row[0]) if row else 0
                parts.append(f"db={size_mb:.0f}MB/{format_count(row_count)}rows")
        except Exception as exc:
            LOG.warning("failed to read db stats: %s", exc)
            parts.append("db=?")
        uptime = int(time.monotonic() - self.start_time)
        parts.append(f"uptime={uptime // 60}m")
        LOG.info(" | ".join(parts))

    def server_close(self):
        self.executor.shutdown(wait=True, cancel_futures=False)
        super().server_close()
