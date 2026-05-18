from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import ANY, MagicMock, patch

from deepseek_bridge.config import ProxyConfig
from deepseek_bridge.reasoning_store import ReasoningStoreStats
from deepseek_bridge.server_infrastructure import BoundedThreadPoolHTTPServer


class CliImportTests(unittest.TestCase):
    """Verify the CLI module loads without errors and --help works."""

    def test_module_imports(self) -> None:
        """cli.py can be imported without errors."""
        from deepseek_bridge.cli import build_arg_parser, main

        self.assertIsNotNone(build_arg_parser)
        self.assertIsNotNone(main)

    def test_arg_parser_returns_parser(self) -> None:
        """build_arg_parser returns an ArgumentParser."""
        from deepseek_bridge.cli import build_arg_parser

        parser = build_arg_parser()
        from argparse import ArgumentParser

        self.assertIsInstance(parser, ArgumentParser)

    def test_help_exits_zero(self) -> None:
        """--help returns exit code 0."""
        result = subprocess.run(
            [sys.executable, "-m", "deepseek_bridge", "--help"],
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0)
        self.assertIn("deepseek bridge", result.stdout.lower())

    def test_storage_startup_logs_pathless_stats(self) -> None:
        class _PathlessStore:
            def stats(self) -> ReasoningStoreStats:
                return ReasoningStoreStats(backend="valkey", entries=2)

        from deepseek_bridge.cli import _log_storage_startup

        with self.assertLogs("deepseek_bridge", level="INFO") as captured:
            _log_storage_startup(
                ProxyConfig(storage_backend="valkey"), _PathlessStore()
            )

        output = "\n".join(captured.output)
        self.assertIn("Backend:      valkey", output)
        self.assertIn("Entries:      2", output)
        self.assertNotIn("Reasoning DB", output)

    def test_create_reasoning_store_supports_valkey_backend(self) -> None:
        from deepseek_bridge.cli import create_reasoning_store

        config = ProxyConfig(
            storage_backend="valkey",
            valkey_url="valkey://example.invalid/0",
            valkey_key_prefix="team-a",
            reasoning_cache_max_age_seconds=60,
            reasoning_cache_max_entries=10,
            max_thread_pool=12,
        )

        with patch(
            "deepseek_bridge.valkey_store.ValkeyReasoningStore"
        ) as mock_store_cls:
            store = create_reasoning_store(config)

        self.assertIs(store, mock_store_cls.return_value)
        mock_store_cls.assert_called_once_with(
            "valkey://example.invalid/0",
            key_prefix="team-a",
            max_age_seconds=60,
            max_rows=10,
            max_connections=12,
        )


# ---------------------------------------------------------------------------
# build_arg_parser – verify every flag parses correctly
# ---------------------------------------------------------------------------


class CliArgParserTests(unittest.TestCase):
    """Verify build_arg_parser() accepts and parses all flags correctly."""

    def test_model_flags(self) -> None:
        from deepseek_bridge.cli import build_arg_parser

        parser = build_arg_parser()
        args = parser.parse_args(
            [
                "--model",
                "deepseek-v4-flash",
                "--thinking",
                "disabled",
                "--reasoning-effort",
                "low",
                "--display-reasoning",
                "--collapsible-reasoning",
            ]
        )
        self.assertEqual(args.model, "deepseek-v4-flash")
        self.assertEqual(args.thinking, "disabled")
        self.assertEqual(args.reasoning_effort, "low")
        self.assertTrue(args.display_reasoning)
        self.assertTrue(args.collapsible_reasoning)

    def test_network_flags(self) -> None:
        from deepseek_bridge.cli import build_arg_parser

        parser = build_arg_parser()
        args = parser.parse_args(
            [
                "--host",
                "0.0.0.0",
                "--port",
                "8000",
                "--tunnel",
                "none",
                "--base-url",
                "http://api.example.com",
                "--cors",
                "--cors-allowed-origin",
                "https://app.example.com",
                "--no-cors-allow-credentials",
            ]
        )
        self.assertEqual(args.host, "0.0.0.0")
        self.assertEqual(args.port, 8000)
        self.assertEqual(args.tunnel, "none")
        self.assertEqual(args.base_url, "http://api.example.com")
        self.assertTrue(args.cors)
        self.assertEqual(args.cors_allowed_origins, ["https://app.example.com"])
        self.assertFalse(args.cors_allow_credentials)

    def test_tunnel_default_is_loaded_from_config(self) -> None:
        from deepseek_bridge.cli import build_arg_parser

        parser = build_arg_parser()
        args = parser.parse_args([])
        self.assertIsNone(args.tunnel)

    def test_storage_flags(self) -> None:
        from deepseek_bridge.cli import build_arg_parser

        parser = build_arg_parser()
        args = parser.parse_args(
            [
                "--log-dir",
                "/tmp/logs",
                "--trace-dir",
                "/tmp/trace",
                "--no-log",
                "--clear-reasoning-cache",
                "--reasoning-content-path",
                "/tmp/reasoning.db",
                "--reasoning-cache-max-age-seconds",
                "3600",
            ]
        )
        self.assertEqual(args.log_dir, Path("/tmp/logs"))
        self.assertEqual(args.trace_dir, Path("/tmp/trace"))
        self.assertTrue(args.no_log)
        self.assertTrue(args.clear_reasoning_cache)
        self.assertEqual(args.reasoning_content_path, Path("/tmp/reasoning.db"))
        self.assertEqual(args.reasoning_cache_max_age_seconds, 3600)

        memory_args = parser.parse_args(
            ["--reasoning-content-path", ":memory:"]
        )
        self.assertEqual(memory_args.reasoning_content_path, Path(":memory:"))

    def test_performance_flags(self) -> None:
        from deepseek_bridge.cli import build_arg_parser

        parser = build_arg_parser()
        args = parser.parse_args(
            [
                "--request-timeout",
                "120",
                "--stream-read-timeout",
                "90",
                "--max-pool-connections",
                "20",
                "--max-thread-pool",
                "40",
                "--max-request-body-bytes",
                "1048576",
            ]
        )
        self.assertEqual(args.request_timeout, 120.0)
        self.assertEqual(args.stream_read_timeout, 90.0)
        self.assertEqual(args.max_pool_connections, 20)
        self.assertEqual(args.max_thread_pool, 40)
        self.assertEqual(args.max_request_body_bytes, 1048576)

    def test_debug_compact_flags(self) -> None:
        from deepseek_bridge.cli import build_arg_parser

        parser = build_arg_parser()
        args = parser.parse_args(
            [
                "--debug",
                "--compact",
                "--headless",
                "--missing-reasoning-strategy",
                "reject",
                "--ollama",
                "--config",
                "/tmp/config.yaml",
                "--runtime-mode",
                "kubernetes",
            ]
        )
        self.assertTrue(args.debug)
        self.assertTrue(args.compact)
        self.assertTrue(args.headless)
        self.assertEqual(args.missing_reasoning_strategy, "reject")
        self.assertTrue(args.ollama)
        self.assertEqual(args.config_path, Path("/tmp/config.yaml"))
        self.assertEqual(args.runtime_mode, "kubernetes")

    def test_negative_boolean_flags(self) -> None:
        """--no-* flags (BooleanOptionalAction) set to False."""
        from deepseek_bridge.cli import build_arg_parser

        parser = build_arg_parser()
        args = parser.parse_args(
            [
                "--no-display-reasoning",
                "--no-ollama",
                "--no-cors",
                "--no-compact",
                "--no-collapsible-reasoning",
            ]
        )
        self.assertFalse(args.display_reasoning)
        self.assertFalse(args.ollama)
        self.assertFalse(args.cors)
        self.assertFalse(args.compact)
        self.assertFalse(args.collapsible_reasoning)

    def test_version_action(self) -> None:
        from deepseek_bridge.cli import build_arg_parser

        parser = build_arg_parser()
        with self.assertRaises(SystemExit):
            parser.parse_args(["--version"])


# ---------------------------------------------------------------------------
# main() - tunnel selection, server loop, debug, config errors
# ---------------------------------------------------------------------------


class CliMainTests(unittest.TestCase):
    """Verify main() behaviour without starting a real server."""

    def setUp(self) -> None:
        from deepseek_bridge.helpers import _shutdown_requested

        _shutdown_requested.clear()

    def tearDown(self) -> None:
        from deepseek_bridge.helpers import _shutdown_requested

        _shutdown_requested.clear()

    def _mock_config(self) -> ProxyConfig:
        return ProxyConfig(tunnel="none")

    @staticmethod
    def _server_bind_patches():
        """Return context-manager tuple with disabled socket binding."""
        return (
            patch.object(
                BoundedThreadPoolHTTPServer, "server_bind", return_value=None
            ),
            patch.object(
                BoundedThreadPoolHTTPServer,
                "server_activate",
                return_value=None,
            ),
        )

    # ── tunnel kwarg in create_tunnel helper ──────────────────────

    def _assert_tunnel_kind(self, cli_arg: str, expected_kind: str) -> None:
        srv_bind, srv_activate = self._server_bind_patches()
        with (
            patch("deepseek_bridge.cli.create_tunnel") as mock_create,
            patch(
                "deepseek_bridge.cli._run_server", side_effect=KeyboardInterrupt
            ),
            patch("deepseek_bridge.cli.ReasoningStore") as mock_store_cls,
            patch("deepseek_bridge.cli.UpstreamPool"),
            patch("deepseek_bridge.cli.configure_logging"),
            patch("deepseek_bridge.cli.ProxyConfig") as mock_cfg_cls,
            srv_bind,
            srv_activate,
        ):
            mock_cfg_cls.from_file.return_value = self._mock_config()
            mock_tunnel = MagicMock()
            mock_tunnel.start.return_value = "https://app.example.com"
            mock_create.return_value = mock_tunnel
            mock_store = MagicMock()
            mock_store.check_bloat.return_value = (None, None)
            mock_store_cls.return_value = mock_store

            from deepseek_bridge.cli import main

            result = main(["--tunnel", cli_arg])

            self.assertEqual(result, 0)
            mock_create.assert_called_once_with(expected_kind, ANY)
            mock_tunnel.start.assert_called_once()

    def test_main_tunnel_cloudflared(self) -> None:
        """--tunnel cloudflared creates a CloudflaredTunnel."""
        self._assert_tunnel_kind("cloudflared", "cloudflared")

    def test_main_tunnel_ngrok(self) -> None:
        """--tunnel ngrok creates an NgrokTunnel."""
        self._assert_tunnel_kind("ngrok", "ngrok")

    def test_main_tunnel_none_skips_tunnel(self) -> None:
        """--tunnel none → create_tunnel NOT called."""
        srv_bind, srv_activate = self._server_bind_patches()
        with (
            patch("deepseek_bridge.cli.create_tunnel") as mock_create,
            patch(
                "deepseek_bridge.cli._run_server", side_effect=KeyboardInterrupt
            ),
            patch("deepseek_bridge.cli.ReasoningStore") as mock_store_cls,
            patch("deepseek_bridge.cli.UpstreamPool"),
            patch("deepseek_bridge.cli.configure_logging"),
            patch("deepseek_bridge.cli.ProxyConfig") as mock_cfg_cls,
            srv_bind,
            srv_activate,
        ):
            mock_cfg_cls.from_file.return_value = self._mock_config()
            mock_store = MagicMock()
            mock_store.check_bloat.return_value = (None, None)
            mock_store_cls.return_value = mock_store

            from deepseek_bridge.cli import main

            result = main(["--tunnel", "none"])

            self.assertEqual(result, 0)
            mock_create.assert_not_called()

    def test_main_kubernetes_runtime_skips_tunnel_and_file_logs(self) -> None:
        """Kubernetes runtime can start without tunnel or persistent logs."""
        srv_bind, srv_activate = self._server_bind_patches()
        with (
            patch("deepseek_bridge.cli.create_tunnel") as mock_create,
            patch(
                "deepseek_bridge.cli._run_server", side_effect=KeyboardInterrupt
            ),
            patch("deepseek_bridge.cli.ReasoningStore") as mock_store_cls,
            patch("deepseek_bridge.cli.UpstreamPool"),
            patch("deepseek_bridge.cli.configure_logging") as mock_log,
            patch("deepseek_bridge.cli.ProxyConfig") as mock_cfg_cls,
            srv_bind,
            srv_activate,
        ):
            mock_cfg_cls.from_file.return_value = ProxyConfig(
                runtime_mode="kubernetes",
                host="0.0.0.0",
                tunnel="none",
                log_dir=None,
                reasoning_content_path=":memory:",
            )
            mock_store = MagicMock()
            mock_store.check_bloat.return_value = (None, None)
            mock_store_cls.return_value = mock_store

            from deepseek_bridge.cli import main

            result = main(["--runtime-mode", "kubernetes"])

            self.assertEqual(result, 0)
            mock_cfg_cls.from_file.assert_called_once_with(
                config_path=None,
                runtime_mode="kubernetes",
            )
            mock_log.assert_called_once()
            self.assertIsNone(mock_log.call_args.kwargs["log_dir"])
            mock_store_cls.assert_called_once_with(
                ":memory:", max_age_seconds=ANY, max_rows=None
            )
            mock_create.assert_not_called()

    def test_main_rejects_tunnel_in_kubernetes_runtime(self) -> None:
        """Kubernetes runtime must not start cloudflared or ngrok."""
        srv_bind, srv_activate = self._server_bind_patches()
        with (
            patch("deepseek_bridge.cli.create_tunnel") as mock_create,
            patch("deepseek_bridge.cli.ReasoningStore") as mock_store_cls,
            patch("deepseek_bridge.cli.UpstreamPool"),
            patch("deepseek_bridge.cli.configure_logging"),
            patch("deepseek_bridge.cli.ProxyConfig") as mock_cfg_cls,
            srv_bind,
            srv_activate,
        ):
            mock_cfg_cls.from_file.return_value = ProxyConfig(
                runtime_mode="kubernetes",
                host="0.0.0.0",
                tunnel="cloudflared",
                log_dir=None,
                reasoning_content_path=":memory:",
            )

            from deepseek_bridge.cli import main

            result = main(["--runtime-mode", "kubernetes"])

            self.assertEqual(result, 2)
            mock_store_cls.assert_not_called()
            mock_create.assert_not_called()

    def test_main_uses_config_tunnel_when_cli_flag_absent(self) -> None:
        srv_bind, srv_activate = self._server_bind_patches()
        with (
            patch("deepseek_bridge.cli.create_tunnel") as mock_create,
            patch(
                "deepseek_bridge.cli._run_server", side_effect=KeyboardInterrupt
            ),
            patch("deepseek_bridge.cli.ReasoningStore") as mock_store_cls,
            patch("deepseek_bridge.cli.UpstreamPool"),
            patch("deepseek_bridge.cli.configure_logging"),
            patch("deepseek_bridge.cli.ProxyConfig") as mock_cfg_cls,
            srv_bind,
            srv_activate,
        ):
            mock_cfg_cls.from_file.return_value = ProxyConfig(tunnel="ngrok")
            mock_tunnel = MagicMock()
            mock_tunnel.start.return_value = "https://app.example.com"
            mock_create.return_value = mock_tunnel
            mock_store = MagicMock()
            mock_store.check_bloat.return_value = (None, None)
            mock_store_cls.return_value = mock_store

            from deepseek_bridge.cli import main

            result = main([])

            self.assertEqual(result, 0)
            mock_create.assert_called_once_with("ngrok", ANY)

    def test_main_cli_flags_override_loaded_config(self) -> None:
        srv_bind, srv_activate = self._server_bind_patches()
        with (
            patch(
                "deepseek_bridge.cli._run_server", side_effect=KeyboardInterrupt
            ) as mock_run,
            patch("deepseek_bridge.cli.create_tunnel"),
            patch("deepseek_bridge.cli.ReasoningStore") as mock_store_cls,
            patch("deepseek_bridge.cli.UpstreamPool"),
            patch("deepseek_bridge.cli.configure_logging"),
            patch("deepseek_bridge.cli.ProxyConfig") as mock_cfg_cls,
            srv_bind,
            srv_activate,
        ):
            mock_cfg_cls.from_file.return_value = ProxyConfig(
                host="127.0.0.1",
                port=9000,
                upstream_model="from-config",
                request_timeout=300,
                stream_read_timeout=180,
                reasoning_cache_max_entries=42,
                max_thread_pool=10,
                max_pool_connections=10,
                tunnel="cloudflared",
            )
            mock_store = MagicMock()
            mock_store.check_bloat.return_value = (None, None)
            mock_store_cls.return_value = mock_store

            from deepseek_bridge.cli import main

            result = main(
                [
                    "--host",
                    "0.0.0.0",
                    "--port",
                    "9100",
                    "--model",
                    "from-cli",
                    "--request-timeout",
                    "120",
                    "--max-thread-pool",
                    "20",
                    "--tunnel",
                    "none",
                ]
            )

            self.assertEqual(result, 0)
            server = mock_run.call_args.args[0]
            self.assertEqual(server.config.host, "0.0.0.0")
            self.assertEqual(server.config.port, 9100)
            self.assertEqual(server.config.upstream_model, "from-cli")
            self.assertEqual(server.config.request_timeout, 120)
            self.assertEqual(server.config.stream_read_timeout, 72)
            self.assertEqual(server.config.max_thread_pool, 20)
            self.assertEqual(server.config.max_pool_connections, 20)
            self.assertEqual(server.config.max_queue_size, 50)
            self.assertEqual(server.config.tunnel, "none")
            self.assertEqual(mock_store_cls.call_args.kwargs["max_rows"], 42)

    def test_main_runs_http_server(self) -> None:
        """main runs the HTTP server loop directly."""
        srv_bind, srv_activate = self._server_bind_patches()
        with (
            patch(
                "deepseek_bridge.cli._run_server", side_effect=KeyboardInterrupt
            ) as mock_run,
            patch("deepseek_bridge.cli.create_tunnel") as mock_create,
            patch("deepseek_bridge.cli.ReasoningStore") as mock_store_cls,
            patch("deepseek_bridge.cli.UpstreamPool"),
            patch("deepseek_bridge.cli.configure_logging"),
            patch("deepseek_bridge.cli.ProxyConfig") as mock_cfg_cls,
            srv_bind,
            srv_activate,
        ):
            mock_cfg_cls.from_file.return_value = self._mock_config()
            mock_tunnel = MagicMock()
            mock_tunnel.start.return_value = "https://app.example.com"
            mock_create.return_value = mock_tunnel
            mock_store = MagicMock()
            mock_store.check_bloat.return_value = (None, None)
            mock_store_cls.return_value = mock_store

            from deepseek_bridge.cli import main

            result = main([])

            self.assertEqual(result, 0)
            mock_run.assert_called_once()

    def test_main_debug_sets_config_debug(self) -> None:
        """--debug sets debug=True on config for configure_logging."""
        srv_bind, srv_activate = self._server_bind_patches()
        with (
            patch(
                "deepseek_bridge.cli._run_server", side_effect=KeyboardInterrupt
            ),
            patch("deepseek_bridge.cli.create_tunnel") as mock_create,
            patch("deepseek_bridge.cli.ReasoningStore") as mock_store_cls,
            patch("deepseek_bridge.cli.UpstreamPool"),
            patch("deepseek_bridge.cli.configure_logging") as mock_log,
            patch("deepseek_bridge.cli.ProxyConfig") as mock_cfg_cls,
            srv_bind,
            srv_activate,
        ):
            mock_cfg_cls.from_file.return_value = self._mock_config()
            mock_tunnel = MagicMock()
            mock_tunnel.start.return_value = "https://app.example.com"
            mock_create.return_value = mock_tunnel
            mock_store = MagicMock()
            mock_store.check_bloat.return_value = (None, None)
            mock_store_cls.return_value = mock_store

            from deepseek_bridge.cli import main

            result = main(["--debug"])

            self.assertEqual(result, 0)
            mock_log.assert_called_once()
            call_kwargs = mock_log.call_args[1]
            self.assertTrue(
                call_kwargs["debug"],
                "configure_logging should receive debug=True",
            )

    def test_main_headless_enables_compact_mode(self) -> None:
        srv_bind, srv_activate = self._server_bind_patches()
        with (
            patch(
                "deepseek_bridge.cli._run_server", side_effect=KeyboardInterrupt
            ) as mock_run,
            patch("deepseek_bridge.cli.create_tunnel"),
            patch("deepseek_bridge.cli.ReasoningStore") as mock_store_cls,
            patch("deepseek_bridge.cli.UpstreamPool"),
            patch("deepseek_bridge.cli.configure_logging"),
            patch("deepseek_bridge.cli.ProxyConfig") as mock_cfg_cls,
            srv_bind,
            srv_activate,
        ):
            mock_cfg_cls.from_file.return_value = self._mock_config()
            mock_store = MagicMock()
            mock_store.check_bloat.return_value = (None, None)
            mock_store_cls.return_value = mock_store

            from deepseek_bridge.cli import main

            result = main(["--headless", "--tunnel", "none"])

            self.assertEqual(result, 0)
            server = mock_run.call_args.args[0]
            self.assertTrue(server.config.compact)

    def test_main_config_loading_error_returns_2(self) -> None:
        """Invalid YAML config file → main returns exit code 2."""
        with patch("deepseek_bridge.cli.configure_logging") as mock_log:
            invalid_yaml = "not: [valid: yaml:"
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".yaml", delete=False
            ) as fp:
                fp.write(invalid_yaml)
                config_path = fp.name
            try:
                from deepseek_bridge.cli import main

                result = main(["--config", config_path])
                self.assertEqual(result, 2)
                mock_log.assert_called_once_with(debug=False)
            finally:
                import os

                os.unlink(config_path)
