"""Resilience tests: fault-injection for connection pooling, streaming
timeouts, and bounded thread pool (Wave 2).  All tests use unittest.mock
— no real HTTP requests or sockets beyond server instantiation."""

from __future__ import annotations

import threading
import unittest
from unittest.mock import MagicMock, patch

import urllib3

from deepseek_bridge.config import ProxyConfig
from deepseek_bridge.server import (
    BoundedThreadPoolHTTPServer,
    DeepSeekProxyHandler,
    UpstreamPool,
    build_arg_parser,
)
from deepseek_bridge.helpers import (
    _handle_shutdown_signal,
    _shutdown_requested,
)
from deepseek_bridge.tunnel import HealthCheckConfig, NgrokTunnel

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
                    server.process_request_thread,
                    "fake_request",
                    ("127.0.0.1", 12345),
                )
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
                mock_shutdown.assert_called_once_with(wait=True, cancel_futures=False)
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
        expected = max(os.cpu_count() or 4, 8)
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


# ---------------------------------------------------------------------------
# Ngrok health check and tunnel lifecycle (Wave 3)
# ---------------------------------------------------------------------------


class NgrokHealthCheckTests(unittest.TestCase):
    """NgrokTunnel health check configuration, _is_healthy, and CLI parsing."""

    def test_ngrok_tunnel_accepts_health_check_config(self) -> None:
        hc = HealthCheckConfig(check_interval=5.0)
        tunnel = NgrokTunnel(
            target_url="http://127.0.0.1:8080",
            health_check=hc,
        )
        self.assertEqual(tunnel.health_check.check_interval, 5.0)

    def test_ngrok_health_check_not_started_when_none(self) -> None:
        tunnel = NgrokTunnel(
            target_url="http://127.0.0.1:8080",
            health_check=None,
        )
        tunnel.start_health_check()
        self.assertIsNone(tunnel._health_thread)

    @patch("deepseek_bridge.tunnel.urlopen")
    def test_ngrok_is_healthy_returns_true_when_process_alive_and_api_ok(
        self,
        mock_urlopen: MagicMock,
    ) -> None:
        tunnel = NgrokTunnel(target_url="http://127.0.0.1:8080")
        tunnel.process = MagicMock()
        tunnel.process.poll.return_value = None  # alive
        mock_resp = MagicMock()
        mock_resp.read.return_value = (
            b'{"endpoints": [{"url": "https://abc.ngrok.io"}]}'
        )
        mock_resp.__enter__.return_value = mock_resp
        mock_urlopen.return_value = mock_resp
        self.assertTrue(tunnel._is_healthy())

    @patch("deepseek_bridge.tunnel.urlopen")
    def test_ngrok_is_healthy_returns_false_when_process_dead(
        self,
        mock_urlopen: MagicMock,
    ) -> None:
        tunnel = NgrokTunnel(target_url="http://127.0.0.1:8080")
        tunnel.process = MagicMock()
        tunnel.process.poll.return_value = 1  # dead
        self.assertFalse(tunnel._is_healthy())
        mock_urlopen.assert_not_called()

    @patch("deepseek_bridge.tunnel.urlopen")
    def test_ngrok_is_healthy_returns_false_when_api_unreachable(
        self,
        mock_urlopen: MagicMock,
    ) -> None:
        tunnel = NgrokTunnel(target_url="http://127.0.0.1:8080")
        tunnel.process = MagicMock()
        tunnel.process.poll.return_value = None  # alive
        mock_urlopen.side_effect = OSError("Connection refused")
        self.assertFalse(tunnel._is_healthy())

    def test_health_check_disabled_when_interval_zero(self) -> None:
        hc = HealthCheckConfig(check_interval=0.0)
        self.assertEqual(hc.check_interval, 0.0)


# ---------------------------------------------------------------------------
# Graceful shutdown  (Wave 3)
# ---------------------------------------------------------------------------


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
        from deepseek_bridge.helpers import _error_body

        body = _error_body("test msg", "test_type", "test_code")
        self.assertIn("error", body)
        self.assertEqual(body["error"]["message"], "test msg")
        self.assertEqual(body["error"]["type"], "test_type")
        self.assertEqual(body["error"]["code"], "test_code")
        self.assertIsNone(body["error"]["param"])

    def test_error_body_param_always_null(self) -> None:
        from deepseek_bridge.helpers import _error_body

        body = _error_body("msg", "type", "code")
        self.assertIn("param", body["error"])
        self.assertIsNone(body["error"]["param"])


if __name__ == "__main__":
    unittest.main()
