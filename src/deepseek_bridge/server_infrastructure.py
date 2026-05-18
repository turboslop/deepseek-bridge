"""Server infrastructure: connection pool and HTTP server.

These classes handle the transport layer — upstream HTTP pooling, request
queuing/dispatch, and thread-pool management. The request handler logic
lives in handler.py.
"""

from __future__ import annotations

import contextlib
import json
import socket
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from http.server import ThreadingHTTPServer
from typing import Any, cast
from urllib.parse import urlparse

import urllib3

from .config import ProxyConfig
from .helpers import _shutdown_requested
from .logging import LOG, format_count
from .metrics import METRICS
from .reasoning_store import ReasoningStoreProtocol, ReasoningStoreStats
from .trace import TraceWriter

_LIVENESS_PATHS = frozenset(
    {"/healthz", "/v1/healthz", "/health", "/v1/health"}
)
_REQUEST_LINE_PEEK_BYTES = 4096
_LIVENESS_PEEK_TIMEOUT_SECONDS = 0.05


class UpstreamPool:
    def __init__(self, max_connections: int = 10) -> None:
        self._pool = urllib3.PoolManager(
            maxsize=max_connections,
            block=True,
            retries=urllib3.Retry(connect=1, read=0, redirect=0, status=0),
            socket_options=[
                (socket.IPPROTO_TCP, socket.TCP_NODELAY, 1),
                (socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1),
            ],
        )

    def request(
        self,
        method: str,
        url: str,
        **kwargs: Any,
    ) -> urllib3.BaseHTTPResponse:
        """Forward an HTTP request through the upstream connection pool."""
        kwargs.pop("metrics_model", None)
        return self._pool.request(method, url, **kwargs)


class DeepSeekProxyServer(ThreadingHTTPServer):
    config: ProxyConfig
    reasoning_store: ReasoningStoreProtocol
    trace_writer: TraceWriter | None
    upstream_pool: UpstreamPool

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self.request_count = 0
        self.start_time = 0.0
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.reasoning_tokens = 0
        self.cache_hit_tokens = 0
        self.cache_miss_tokens = 0
        self.model_tokens: dict[str, int] = {}
        super().__init__(*args, **kwargs)

    def readiness_checks(self) -> dict[str, dict[str, Any]]:
        """Return cheap local checks used by `/readyz`."""
        checks: dict[str, dict[str, Any]] = {
            "shutdown": {
                "ok": not _shutdown_requested.is_set(),
                "status": (
                    "ok" if not _shutdown_requested.is_set() else "draining"
                ),
            },
            "paused": {
                "ok": not bool(getattr(self, "paused", False)),
                "status": (
                    "ok" if not getattr(self, "paused", False) else "paused"
                ),
            },
            "upstream_pool": {
                "ok": hasattr(self, "upstream_pool")
                and self.upstream_pool is not None,
                "status": (
                    "ok"
                    if hasattr(self, "upstream_pool")
                    and self.upstream_pool is not None
                    else "missing"
                ),
            },
        }

        store = getattr(self, "reasoning_store", None)
        health_check = getattr(store, "healthcheck", None)
        if not callable(health_check):
            health_check = getattr(store, "health_check", None)
        if callable(health_check):
            health_check_fn = cast(Callable[[], object], health_check)
            try:
                result = health_check_fn()  # pylint: disable=not-callable
                if isinstance(result, tuple) and len(result) == 2:
                    ok, detail = result
                else:
                    ok = bool(result)
                    detail = "ok" if result else "unavailable"
            except Exception as exc:
                LOG.warning("storage readiness check failed: %s", exc)
                ok, detail = False, "unavailable"
            checks["storage"] = {"ok": bool(ok), "status": str(detail)}
        else:
            checks["storage"] = {
                "ok": store is not None,
                "status": "ok" if store is not None else "missing",
            }

        executor = getattr(self, "executor", None)
        if executor is not None:
            executor_shutdown = bool(getattr(executor, "_shutdown", False))
            checks["executor"] = {
                "ok": not executor_shutdown,
                "status": "ok" if not executor_shutdown else "shutdown",
            }

        if hasattr(self, "queue_size"):
            queue_size = int(self.queue_size)
            max_queue_size = int(
                getattr(getattr(self, "config", None), "max_queue_size", 50)
            )
            checks["queue"] = {
                "ok": queue_size < max_queue_size,
                "status": "ok" if queue_size < max_queue_size else "full",
                "queued": queue_size,
                "max": max_queue_size,
            }

        return checks

    def is_ready(self) -> bool:
        checks = self.readiness_checks()
        return all(bool(check["ok"]) for check in checks.values())

    def reasoning_store_stats(self) -> ReasoningStoreStats | None:
        store = getattr(self, "reasoning_store", None)
        stats_method = getattr(store, "stats", None)
        if not callable(stats_method):
            return None
        stats_fn = cast(Callable[[], object], stats_method)
        try:
            result = stats_fn()  # pylint: disable=not-callable
        except Exception as exc:
            LOG.warning("failed to read storage stats: %s", exc)
            return None
        if isinstance(result, ReasoningStoreStats):
            return result
        return None


class BoundedThreadPoolHTTPServer(DeepSeekProxyServer):
    """ThreadingHTTPServer variant that uses a fixed-size ThreadPoolExecutor."""

    def __init__(
        self, *args: Any, max_workers: int = 20, **kwargs: Any
    ) -> None:
        super().__init__(*args, **kwargs)
        self._active_worker_count = 0
        self._active_worker_lock = threading.Lock()
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

    def process_request(
        self,
        request: socket.socket | tuple[bytes, socket.socket],
        client_address: Any,
    ) -> None:
        if isinstance(request, socket.socket):
            request.settimeout(
                self.socket.gettimeout()
                if hasattr(self, "socket")
                else int(
                    getattr(
                        getattr(self, "config", None), "request_timeout", 300
                    )
                )
            )
        queue_size = self.queue_size
        config = getattr(self, "config", None)
        effective_max_queue = (
            config.max_queue_size if config is not None else 50
        )
        if queue_size >= effective_max_queue:
            if self._serve_liveness_probe_when_saturated(request):
                return
            LOG.warning(
                "rejecting request from %s: queue full (%s queued)",
                client_address,
                queue_size,
            )
            self._reject_connection(request)
            METRICS.record_http_request(
                method="UNKNOWN",
                path="queue_rejected",
                status=503,
                duration_seconds=0.0,
            )
            return
        with contextlib.suppress(RuntimeError):
            self.executor.submit(
                self._process_request_thread_tracked,
                request,
                client_address,
            )

    def _serve_liveness_probe_when_saturated(self, request: Any) -> bool:
        if not isinstance(request, socket.socket):
            return False

        request_path = self._peek_get_request_path(request)
        if request_path not in _LIVENESS_PATHS:
            return False

        try:
            uptime = (
                int(time.monotonic() - self.start_time)
                if hasattr(self, "start_time")
                else 0
            )
            body = json.dumps(
                {
                    "ok": True,
                    "server": "deepseek-bridge",
                    "uptime_seconds": uptime,
                },
                separators=(",", ":"),
            ).encode("utf-8")
            request.sendall(
                b"HTTP/1.1 200 OK\r\n"
                b"Content-Type: application/json\r\n"
                b"Content-Length: " + str(len(body)).encode("utf-8") + b"\r\n"
                b"Connection: close\r\n"
                b"\r\n" + body
            )
            request.close()
            return True
        except Exception as exc:
            LOG.warning("failed to serve liveness probe: %s", exc)
            return False

    @staticmethod
    def _peek_get_request_path(request: socket.socket) -> str | None:
        original_timeout = request.gettimeout()
        try:
            request.settimeout(_LIVENESS_PEEK_TIMEOUT_SECONDS)
            try:
                data = request.recv(_REQUEST_LINE_PEEK_BYTES, socket.MSG_PEEK)
            finally:
                request.settimeout(original_timeout)
        except BlockingIOError, TimeoutError, OSError:
            return None

        if b"\n" not in data:
            return None
        request_line = data.split(b"\n", 1)[0].rstrip(b"\r")
        parts = request_line.split()
        if len(parts) < 3 or parts[0] != b"GET":
            return None

        target = parts[1].decode("latin-1", errors="replace")
        return urlparse(target).path

    def _process_request_thread_tracked(
        self,
        request: socket.socket | tuple[bytes, socket.socket],
        client_address: Any,
    ) -> None:
        with self._active_worker_lock:
            self._active_worker_count += 1
        try:
            self.process_request_thread(request, client_address)
        finally:
            with self._active_worker_lock:
                self._active_worker_count = max(
                    0, self._active_worker_count - 1
                )

    @staticmethod
    def _reject_connection(request: Any) -> None:
        try:
            if isinstance(request, socket.socket):
                body = json.dumps(
                    {
                        "error": {
                            "message": (
                                "Server overloaded — too many queued requests"
                            ),
                            "type": "server_error",
                            "code": "service_unavailable",
                        }
                    }
                ).encode("utf-8")
                request.sendall(
                    b"HTTP/1.1 503 Service Unavailable\r\n"
                    b"Content-Type: application/json\r\n"
                    b"Content-Length: "
                    + str(len(body)).encode("utf-8")
                    + b"\r\n"
                    b"Connection: close\r\n"
                    b"\r\n" + body
                )
                request.close()
        except Exception as exc:
            LOG.warning("failed to close rejected connection: %s", exc)

    @property
    def active_threads(self) -> int:
        """Current number of active worker threads."""
        try:
            return len(self.executor._threads)
        except Exception:
            return 0

    @property
    def active_worker_count(self) -> int:
        """Current number of workers processing accepted requests."""
        try:
            with self._active_worker_lock:
                return self._active_worker_count
        except Exception:
            return 0

    @property
    def max_workers(self) -> int:
        """Configured maximum worker threads."""
        return int(self.executor._max_workers)

    @property
    def queue_size(self) -> int:
        """Current size of the pending request queue."""
        try:
            return self.executor._work_queue.qsize()
        except Exception:
            return 0

    def _log_pool_utilization(self) -> None:
        try:
            active: int | str = self.active_threads
        except Exception as exc:
            LOG.warning("failed to count active threads: %s", exc)
            active = "?"
        try:
            queue_size: int | str = self.queue_size
        except Exception as exc:
            LOG.warning("failed to check queue size: %s", exc)
            queue_size = "?"
        LOG.info(
            "thread pool: max_workers=%s active=%s queue=%s",
            self.max_workers,
            active,
            queue_size,
        )

    def _log_db_stats(self) -> None:
        stats = self.reasoning_store_stats()
        if stats is None:
            return
        details = [f"backend={stats.backend}"]
        if stats.path:
            details.append(f"path={stats.path}")
        if stats.size_mb is not None:
            details.append(f"size={stats.size_mb:.1f}MB")
        if stats.entries is not None:
            details.append(f"entries={format_count(stats.entries)}")
        if len(details) > 1:
            LOG.info("storage stats: %s", " ".join(details))

    def _log_heartbeat(self) -> None:
        parts = [f"heartbeat: req={format_count(self.request_count)}"]
        try:
            parts.append(f"pool={self.active_threads}/{self.max_workers}")
        except Exception as exc:
            LOG.warning("failed to read pool stats: %s", exc)
            parts.append("pool=?")
        stats = self.reasoning_store_stats()
        if stats is not None:
            storage_parts = [stats.backend]
            if stats.size_mb is not None:
                storage_parts.append(f"{stats.size_mb:.0f}MB")
            if stats.entries is not None:
                storage_parts.append(f"{format_count(stats.entries)}entries")
            parts.append(f"storage={'/'.join(storage_parts)}")
        uptime = int(time.monotonic() - self.start_time)
        parts.append(f"uptime={uptime // 60}m")
        LOG.info(" | ".join(parts))

    def server_close(self) -> None:
        self.executor.shutdown(wait=True, cancel_futures=False)
        super().server_close()
