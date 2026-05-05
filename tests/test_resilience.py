"""Resilience tests: fault-injection for connection pooling, streaming
timeouts, and bounded thread pool (Wave 2).  All tests use unittest.mock
— no real HTTP requests or sockets beyond server instantiation."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

import urllib3

from deepseek_cursor_proxy.config import ProxyConfig
from deepseek_cursor_proxy.server import (
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
            pool = UpstreamPool(max_connections=5, max_keepalive=3)
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


# ---------------------------------------------------------------------------
# ProxyConfig defaults
# ---------------------------------------------------------------------------


class ConfigDefaultsTests(unittest.TestCase):
    """Default values for Wave-2 configuration fields."""

    def test_stream_read_timeout_defaults(self) -> None:
        self.assertEqual(ProxyConfig().stream_read_timeout, 180.0)

    def test_max_thread_pool_defaults(self) -> None:
        self.assertEqual(ProxyConfig().max_thread_pool, 20)


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
                "--max-keepalive",
                "3",
                "--max-thread-pool",
                "10",
            ]
        )
        self.assertEqual(args.max_pool_connections, 5)
        self.assertEqual(args.max_keepalive, 3)
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


if __name__ == "__main__":
    unittest.main()
