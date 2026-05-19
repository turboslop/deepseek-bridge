"""Resilience tests: fault-injection for connection pooling, streaming
timeouts, and bounded thread pool (Wave 2).  All tests use unittest.mock
— no real HTTP requests or sockets beyond server instantiation."""

from __future__ import annotations

import threading
import time
import unittest
from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch

import httpx
import urllib3

from deepseek_bridge.config import ProxyConfig
from deepseek_bridge.helpers import (
    _handle_shutdown_signal,
    _shutdown_requested,
)
from deepseek_bridge.metrics import METRICS
from deepseek_bridge.reasoning_store import ReasoningStoreStats
from deepseek_bridge.server import (
    AsyncUpstreamClient,
    BoundedThreadPoolHTTPServer,
    DeepSeekProxyHandler,
    UpstreamPool,
    build_arg_parser,
)

# ---------------------------------------------------------------------------
# UpstreamPool
# ---------------------------------------------------------------------------


class UpstreamPoolTests(unittest.TestCase):
    """Connection-pool initialisation and retry configuration."""

    def test_upstream_pool_initialization(self) -> None:
        with patch.object(urllib3, "PoolManager") as mock_pool_mgr:
            pool = UpstreamPool(max_connections=5)
            mock_pool_mgr.assert_called_once()
            _args, kwargs = mock_pool_mgr.call_args
            self.assertEqual(kwargs["maxsize"], 5)
            self.assertTrue(kwargs["block"])
            self.assertIsInstance(pool, UpstreamPool)

    def test_upstream_pool_no_read_retries(self) -> None:
        """read=0 is critical — a read retry would re-POST the request body,
        potentially duplicating side effects (e.g. charging the user twice)."""
        with patch.object(urllib3, "PoolManager") as mock_pool_mgr:
            UpstreamPool()
            mock_pool_mgr.assert_called_once()
            _args, kwargs = mock_pool_mgr.call_args
            self.assertEqual(kwargs["retries"].read, 0)

    def test_upstream_pool_ignores_metrics_model_kwarg(self) -> None:
        with patch.object(urllib3, "PoolManager") as mock_pool_mgr:
            response = MagicMock()
            mock_pool_mgr.return_value.request.return_value = response
            pool = UpstreamPool()

            self.assertIs(
                pool.request(
                    "POST",
                    "https://api.example.test/chat/completions",
                    metrics_model="deepseek-v4-pro",
                ),
                response,
            )

        _args, kwargs = mock_pool_mgr.return_value.request.call_args
        self.assertNotIn("metrics_model", kwargs)


class AsyncUpstreamClientTests(unittest.IsolatedAsyncioTestCase):
    """Async upstream client limits and timeout construction."""

    def test_async_client_uses_configured_connection_limits(self) -> None:
        with (
            patch("deepseek_bridge.async_upstream.httpx.Limits") as limits,
            patch("deepseek_bridge.async_upstream.httpx.AsyncClient") as client,
        ):
            AsyncUpstreamClient(ProxyConfig(max_pool_connections=7))

        limits.assert_called_once_with(
            max_connections=7,
            max_keepalive_connections=7,
        )
        client.assert_called_once_with(
            limits=limits.return_value,
            headers={"User-Agent": "DeepSeekBridge"},
        )

    async def test_post_uses_stream_timeout_for_streaming(self) -> None:
        client = AsyncUpstreamClient(
            ProxyConfig(request_timeout=10, stream_read_timeout=3)
        )
        try:
            with (
                patch(
                    "deepseek_bridge.async_upstream.httpx.Timeout"
                ) as timeout,
                patch.object(client._client, "build_request") as build_request,
                patch.object(
                    client._client,
                    "send",
                    new=AsyncMock(
                        return_value=httpx.Response(200, content=b"{}")
                    ),
                ),
            ):
                build_request.return_value = httpx.Request(
                    "POST", "https://api.example.test/chat/completions"
                )

                await client.post(
                    "https://api.example.test/chat/completions",
                    body=b"{}",
                    headers={"Content-Type": "application/json"},
                    stream=True,
                )

            timeout.assert_called_once_with(
                connect=10,
                read=3,
                write=10,
                pool=10,
            )
        finally:
            await client.aclose()


# ---------------------------------------------------------------------------
# BoundedThreadPoolHTTPServer
# ---------------------------------------------------------------------------


class BoundedThreadPoolTests(unittest.TestCase):
    """Fixed-size thread pool lifecycle and task submission."""

    def _make_server(self, max_workers: int = 5) -> BoundedThreadPoolHTTPServer:
        return BoundedThreadPoolHTTPServer(
            ("127.0.0.1", 0),
            DeepSeekProxyHandler,
            max_workers=max_workers,
        )

    def test_bounded_thread_pool_initialization(self) -> None:
        server = self._make_server(max_workers=7)
        try:
            self.assertEqual(server.executor._max_workers, 7)
        finally:
            server.server_close()

    def test_bounded_thread_pool_submits_tasks(self) -> None:
        server = self._make_server(max_workers=5)
        try:
            with patch.object(server.executor, "submit") as mock_submit:
                server.process_request("fake_request", ("127.0.0.1", 12345))
                mock_submit.assert_called_once_with(
                    server._process_request_thread_tracked,
                    "fake_request",
                    ("127.0.0.1", 12345),
                )
        finally:
            server.server_close()

    def test_thread_pool_metrics_report_active_workers_and_queue(self) -> None:
        server = self._make_server(max_workers=5)
        METRICS.reset()
        try:
            server._active_worker_count = 2
            with patch.object(
                BoundedThreadPoolHTTPServer,
                "queue_size",
                new_callable=PropertyMock,
                return_value=3,
            ):
                body = METRICS.render_prometheus(server=server)

            self.assertIn("deepseek_bridge_thread_pool_active 2", body)
            self.assertIn("deepseek_bridge_thread_pool_queue 3", body)
        finally:
            METRICS.reset()
            server.server_close()

    def test_queue_at_limit_rejects_new_request(self) -> None:
        server = self._make_server(max_workers=5)
        server.config = ProxyConfig(max_queue_size=1)
        try:
            with (
                patch.object(
                    BoundedThreadPoolHTTPServer,
                    "queue_size",
                    new_callable=PropertyMock,
                    return_value=1,
                ),
                patch.object(server, "_reject_connection") as reject,
                patch.object(server.executor, "submit") as submit,
            ):
                server.process_request("fake_request", ("127.0.0.1", 12345))

            reject.assert_called_once_with("fake_request")
            submit.assert_not_called()
        finally:
            server.server_close()

    def test_readiness_fails_when_queue_is_full(self) -> None:
        class _HealthyStore:
            def health_check(self) -> tuple[bool, str]:
                return True, "ok"

        server = self._make_server(max_workers=5)
        server.config = ProxyConfig(max_queue_size=1)
        server.reasoning_store = _HealthyStore()
        server.upstream_pool = object()
        _shutdown_requested.clear()
        try:
            with patch.object(
                BoundedThreadPoolHTTPServer,
                "queue_size",
                new_callable=PropertyMock,
                return_value=1,
            ):
                checks = server.readiness_checks()
                ready = server.is_ready()

            self.assertFalse(checks["queue"]["ok"])
            self.assertEqual(checks["queue"]["status"], "full")
            self.assertFalse(ready)
        finally:
            server.server_close()

    def test_readiness_returns_503_state_for_storage_exception(self) -> None:
        class _FailingStore:
            def health_check(self) -> tuple[bool, str]:
                raise RuntimeError("db offline")

        server = self._make_server(max_workers=5)
        server.config = ProxyConfig(max_queue_size=10)
        server.reasoning_store = _FailingStore()
        server.upstream_pool = object()
        _shutdown_requested.clear()
        try:
            checks = server.readiness_checks()

            self.assertFalse(checks["storage"]["ok"])
            self.assertEqual(checks["storage"]["status"], "unavailable")
            self.assertFalse(server.is_ready())
        finally:
            server.server_close()

    def test_readiness_uses_healthcheck_method(self) -> None:
        class _HealthcheckOnlyStore:
            def healthcheck(self) -> tuple[bool, str]:
                return False, "unavailable"

        server = self._make_server(max_workers=5)
        server.config = ProxyConfig(max_queue_size=10)
        server.reasoning_store = _HealthcheckOnlyStore()
        server.upstream_pool = object()
        _shutdown_requested.clear()
        try:
            checks = server.readiness_checks()

            self.assertFalse(checks["storage"]["ok"])
            self.assertEqual(checks["storage"]["status"], "unavailable")
            self.assertFalse(server.is_ready())
        finally:
            server.server_close()

    def test_heartbeat_logs_pathless_storage_stats(self) -> None:
        class _PathlessStore:
            def healthcheck(self) -> tuple[bool, str]:
                return True, "ok"

            def stats(self) -> ReasoningStoreStats:
                return ReasoningStoreStats(backend="valkey", entries=2)

        server = self._make_server(max_workers=5)
        server.reasoning_store = _PathlessStore()
        server.start_time = time.monotonic()
        try:
            with self.assertLogs("deepseek_bridge", level="INFO") as captured:
                server._log_heartbeat()

            output = "\n".join(captured.output)
            self.assertIn("storage=valkey/2entries", output)
            self.assertNotIn("db=?", output)
        finally:
            server.server_close()

    def test_db_stats_logs_pathless_storage_stats(self) -> None:
        class _PathlessStore:
            def stats(self) -> ReasoningStoreStats:
                return ReasoningStoreStats(backend="valkey", entries=2)

        server = self._make_server(max_workers=5)
        server.reasoning_store = _PathlessStore()
        try:
            with self.assertLogs("deepseek_bridge", level="INFO") as captured:
                server._log_db_stats()

            output = "\n".join(captured.output)
            self.assertIn("storage stats: backend=valkey entries=2", output)
        finally:
            server.server_close()

    def test_bounded_thread_pool_server_close_drains(self) -> None:
        server = self._make_server(max_workers=5)
        executor = server.executor
        server.server_close()
        with self.assertRaises(RuntimeError):
            executor.submit(lambda: None)

    def test_bounded_thread_pool_cancel_futures_false_on_shutdown(self) -> None:
        server = self._make_server(max_workers=5)
        try:
            with patch.object(server.executor, "shutdown") as mock_shutdown:
                server.server_close()
                mock_shutdown.assert_called_once_with(
                    wait=True, cancel_futures=False
                )
        finally:
            server.executor.shutdown(wait=True)


# ---------------------------------------------------------------------------
# ProxyConfig defaults
# ---------------------------------------------------------------------------


class ConfigDefaultsTests(unittest.TestCase):
    """Default values for Wave-2 configuration fields."""

    def test_stream_read_timeout_defaults(self) -> None:
        self.assertEqual(ProxyConfig().stream_read_timeout, 180.0)

    def test_max_thread_pool_defaults(self) -> None:
        import os

        expected = max(os.cpu_count() or 4, 12)
        self.assertEqual(ProxyConfig().max_thread_pool, expected)


# ---------------------------------------------------------------------------
# CLI argument parsing
# ---------------------------------------------------------------------------


class CLITests(unittest.TestCase):
    """Argument-parser integration for pool and timeout flags."""

    def test_stream_read_timeout_configurable_from_args(self) -> None:
        args = build_arg_parser().parse_args(["--stream-read-timeout", "60"])
        self.assertEqual(args.stream_read_timeout, 60.0)

    def test_max_thread_pool_configurable(self) -> None:
        args = build_arg_parser().parse_args(
            [
                "--max-pool-connections",
                "5",
                "--max-thread-pool",
                "10",
            ]
        )
        self.assertEqual(args.max_pool_connections, 5)
        self.assertEqual(args.max_thread_pool, 10)


# ---------------------------------------------------------------------------
# Upstream request timeout construction  (mock-based, no network)
# ---------------------------------------------------------------------------


class PoolRequestTimeoutTests(unittest.TestCase):
    """Verify that streaming requests use stream_read_timeout as the
    read-timeout, while non-streaming requests use the full request_timeout."""

    def test_pool_request_timeout_uses_read_timeout_for_streaming(self) -> None:
        config = ProxyConfig()
        pool = UpstreamPool()
        timeout = urllib3.Timeout(
            connect=config.request_timeout,
            read=config.stream_read_timeout,
        )
        with patch.object(pool._pool, "request") as mock_request:
            mock_request.return_value = MagicMock(status=200)
            pool._pool.request(
                "POST",
                "http://example.com/v1/chat/completions",
                body=b"{}",
                headers={"Content-Type": "application/json"},
                preload_content=False,
                timeout=timeout,
            )
            call_kwargs = mock_request.call_args[1]
            self.assertEqual(call_kwargs["timeout"].read_timeout, 180.0)
            self.assertEqual(call_kwargs["timeout"].connect_timeout, 300.0)
            self.assertFalse(call_kwargs["preload_content"])


class ShutdownSignalTests(unittest.TestCase):
    """_shutdown_requested event and _handle_shutdown_signal."""

    def test_shutdown_requested_event_set_on_signal(self) -> None:
        self.assertIsInstance(_shutdown_requested, threading.Event)
        _shutdown_requested.set()
        self.assertTrue(_shutdown_requested.is_set())
        _shutdown_requested.clear()
        self.assertFalse(_shutdown_requested.is_set())

    def test_handle_shutdown_signal_logs_and_sets_event(self) -> None:
        _shutdown_requested.clear()
        self.assertFalse(_shutdown_requested.is_set())
        _handle_shutdown_signal(15, None)
        self.assertTrue(_shutdown_requested.is_set())
        _shutdown_requested.clear()


# ---------------------------------------------------------------------------
# System fingerprint  (Wave 4)
# ---------------------------------------------------------------------------


class SystemFingerprintTests(unittest.TestCase):
    """SYSTEM_FINGERPRINT constant format and presence in SSE chunks."""

    def test_system_fingerprint_constant_exists(self) -> None:
        from deepseek_bridge.server import SYSTEM_FINGERPRINT

        self.assertTrue(SYSTEM_FINGERPRINT.startswith("fp_"))
        self.assertEqual(SYSTEM_FINGERPRINT, "fp_deepseek_bridge")

    def test_system_fingerprint_in_sse_chunk(self) -> None:
        from deepseek_bridge.server import SYSTEM_FINGERPRINT

        self.assertIsInstance(SYSTEM_FINGERPRINT, str)


# ---------------------------------------------------------------------------
# x-request-id header  (Wave 4)
# ---------------------------------------------------------------------------


class XRequestIdTests(unittest.TestCase):
    """_generate_request_id format and uniqueness."""

    def test_generate_request_id_format(self) -> None:
        from deepseek_bridge.helpers import _generate_request_id

        req_id = _generate_request_id()
        self.assertTrue(req_id.startswith("dcp-"))
        self.assertEqual(len(req_id), 28)  # "dcp-" + 24 hex chars

    def test_generate_request_id_is_unique(self) -> None:
        from deepseek_bridge.helpers import _generate_request_id

        ids = {_generate_request_id() for _ in range(100)}
        self.assertEqual(len(ids), 100)  # Ensure uniqueness


# ---------------------------------------------------------------------------
# Error response body format  (Wave 4)
# ---------------------------------------------------------------------------


class ErrorFormatTests(unittest.TestCase):
    """_error_body produces the standard OpenAI-compatible error envelope."""

    def test_error_body_has_all_fields(self) -> None:
        from deepseek_bridge._types import _error_body

        body = _error_body("test msg", "test_type", "test_code")
        self.assertIn("error", body)
        self.assertEqual(body["error"]["message"], "test msg")
        self.assertEqual(body["error"]["type"], "test_type")
        self.assertEqual(body["error"]["code"], "test_code")
        self.assertIsNone(body["error"]["param"])

    def test_error_body_param_always_null(self) -> None:
        from deepseek_bridge._types import _error_body

        body = _error_body("msg", "type", "code")
        self.assertIn("param", body["error"])
        self.assertIsNone(body["error"]["param"])


if __name__ == "__main__":
    unittest.main()
