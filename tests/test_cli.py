from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import ANY, MagicMock, patch

from deepseek_bridge.config import ProxyConfig
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


# ---------------------------------------------------------------------------
# build_arg_parser – verify every flag parses correctly
# ---------------------------------------------------------------------------


class CliArgParserTests(unittest.TestCase):
    """Verify build_arg_parser() accepts and parses all flags correctly."""

    def test_model_flags(self) -> None:
        from deepseek_bridge.cli import build_arg_parser
        parser = build_arg_parser()
        args = parser.parse_args([
            "--model", "deepseek-v4-flash",
            "--thinking", "disabled",
            "--reasoning-effort", "low",
            "--display-reasoning",
            "--collapsible-reasoning",
        ])
        self.assertEqual(args.model, "deepseek-v4-flash")
        self.assertEqual(args.thinking, "disabled")
        self.assertEqual(args.reasoning_effort, "low")
        self.assertTrue(args.display_reasoning)
        self.assertTrue(args.collapsible_reasoning)

    def test_network_flags(self) -> None:
        from deepseek_bridge.cli import build_arg_parser
        parser = build_arg_parser()
        args = parser.parse_args([
            "--host", "0.0.0.0",
            "--port", "8000",
            "--tunnel", "none",
            "--base-url", "http://api.example.com",
            "--cors",
        ])
        self.assertEqual(args.host, "0.0.0.0")
        self.assertEqual(args.port, 8000)
        self.assertEqual(args.tunnel, "none")
        self.assertEqual(args.base_url, "http://api.example.com")
        self.assertTrue(args.cors)

    def test_tunnel_default_is_cloudflared(self) -> None:
        from deepseek_bridge.cli import build_arg_parser
        parser = build_arg_parser()
        args = parser.parse_args([])
        self.assertEqual(args.tunnel, "cloudflared")

    def test_storage_flags(self) -> None:
        from deepseek_bridge.cli import build_arg_parser
        parser = build_arg_parser()
        args = parser.parse_args([
            "--log-dir", "/tmp/logs",
            "--trace-dir", "/tmp/trace",
            "--no-log",
            "--clear-reasoning-cache",
            "--reasoning-content-path", "/tmp/reasoning.db",
            "--reasoning-cache-max-age-seconds", "3600",
        ])
        self.assertEqual(args.log_dir, Path("/tmp/logs"))
        self.assertEqual(args.trace_dir, Path("/tmp/trace"))
        self.assertTrue(args.no_log)
        self.assertTrue(args.clear_reasoning_cache)
        self.assertEqual(args.reasoning_content_path, Path("/tmp/reasoning.db"))
        self.assertEqual(args.reasoning_cache_max_age_seconds, 3600)

    def test_performance_flags(self) -> None:
        from deepseek_bridge.cli import build_arg_parser
        parser = build_arg_parser()
        args = parser.parse_args([
            "--request-timeout", "120",
            "--stream-read-timeout", "90",
            "--max-pool-connections", "20",
            "--max-thread-pool", "40",
            "--max-request-body-bytes", "1048576",
        ])
        self.assertEqual(args.request_timeout, 120.0)
        self.assertEqual(args.stream_read_timeout, 90.0)
        self.assertEqual(args.max_pool_connections, 20)
        self.assertEqual(args.max_thread_pool, 40)
        self.assertEqual(args.max_request_body_bytes, 1048576)

    def test_headless_debug_compact_flags(self) -> None:
        from deepseek_bridge.cli import build_arg_parser
        parser = build_arg_parser()
        args = parser.parse_args([
            "--headless",
            "--debug",
            "--compact",
            "--missing-reasoning-strategy", "reject",
            "--ollama",
            "--config", "/tmp/config.yaml",
        ])
        self.assertTrue(args.headless)
        self.assertTrue(args.debug)
        self.assertTrue(args.compact)
        self.assertEqual(args.missing_reasoning_strategy, "reject")
        self.assertTrue(args.ollama)
        self.assertEqual(args.config_path, Path("/tmp/config.yaml"))

    def test_negative_boolean_flags(self) -> None:
        """--no-* flags (BooleanOptionalAction) set to False."""
        from deepseek_bridge.cli import build_arg_parser
        parser = build_arg_parser()
        args = parser.parse_args([
            "--no-display-reasoning",
            "--no-ollama",
            "--no-cors",
            "--no-compact",
            "--no-collapsible-reasoning",
        ])
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
# main() – tunnel selection, headless, debug, config errors
# ---------------------------------------------------------------------------


class CliMainTests(unittest.TestCase):
    """Verify main() behaviour without starting a real server."""

    def _mock_config(self) -> ProxyConfig:
        return ProxyConfig(tunnel="none")

    @staticmethod
    def _server_bind_patches():
        """Return context-manager tuple: mock server_bind/activate so no port bind."""
        return (
            patch.object(BoundedThreadPoolHTTPServer, "server_bind", return_value=None),
            patch.object(BoundedThreadPoolHTTPServer, "server_activate",
                         return_value=None),
        )

    # ── tunnel kwarg in create_tunnel helper ──────────────────────

    def _assert_tunnel_kind(self, cli_arg: str, expected_kind: str) -> None:
        srv_bind, srv_activate = self._server_bind_patches()
        with patch("deepseek_bridge.cli.create_tunnel") as mock_create, \
             patch("deepseek_bridge.cli._run_server",
                   side_effect=KeyboardInterrupt), \
             patch("deepseek_bridge.cli.ReasoningStore"), \
             patch("deepseek_bridge.cli.UpstreamPool"), \
             patch("deepseek_bridge.cli.configure_logging"), \
             patch("deepseek_bridge.cli.ProxyConfig") as mock_cfg_cls, \
             srv_bind, srv_activate:
            mock_cfg_cls.from_file.return_value = self._mock_config()
            mock_tunnel = MagicMock()
            mock_tunnel.start.return_value = "https://app.example.com"
            mock_create.return_value = mock_tunnel

            from deepseek_bridge.cli import main
            result = main(["--tunnel", cli_arg, "--headless"])

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
        with patch("deepseek_bridge.cli.create_tunnel") as mock_create, \
             patch("deepseek_bridge.cli._run_server",
                   side_effect=KeyboardInterrupt), \
             patch("deepseek_bridge.cli.ReasoningStore"), \
             patch("deepseek_bridge.cli.UpstreamPool"), \
             patch("deepseek_bridge.cli.configure_logging"), \
             patch("deepseek_bridge.cli.ProxyConfig") as mock_cfg_cls, \
             srv_bind, srv_activate:
            mock_cfg_cls.from_file.return_value = self._mock_config()

            from deepseek_bridge.cli import main
            result = main(["--tunnel", "none", "--headless"])

            self.assertEqual(result, 0)
            mock_create.assert_not_called()

    def test_main_headless_avoids_tui(self) -> None:
        """--headless flag runs server loop instead of TUI."""
        srv_bind, srv_activate = self._server_bind_patches()
        with patch("deepseek_bridge.cli._run_server",
                   side_effect=KeyboardInterrupt) as mock_run, \
             patch("deepseek_bridge.cli.create_tunnel") as mock_create, \
             patch("deepseek_bridge.cli.ReasoningStore"), \
             patch("deepseek_bridge.cli.UpstreamPool"), \
             patch("deepseek_bridge.cli.configure_logging"), \
             patch("deepseek_bridge.cli.ProxyConfig") as mock_cfg_cls, \
             srv_bind, srv_activate:
            mock_cfg_cls.from_file.return_value = self._mock_config()
            mock_tunnel = MagicMock()
            mock_tunnel.start.return_value = "https://app.example.com"
            mock_create.return_value = mock_tunnel

            from deepseek_bridge.cli import main
            result = main(["--headless"])

            self.assertEqual(result, 0)
            mock_run.assert_called_once()

    def test_main_debug_sets_config_debug(self) -> None:
        """--debug flag sets debug=True on config, passed to configure_logging."""
        srv_bind, srv_activate = self._server_bind_patches()
        with patch("deepseek_bridge.cli._run_server",
                   side_effect=KeyboardInterrupt), \
             patch("deepseek_bridge.cli.create_tunnel") as mock_create, \
             patch("deepseek_bridge.cli.ReasoningStore"), \
             patch("deepseek_bridge.cli.UpstreamPool"), \
             patch("deepseek_bridge.cli.configure_logging") as mock_log, \
             patch("deepseek_bridge.cli.ProxyConfig") as mock_cfg_cls, \
             srv_bind, srv_activate:
            mock_cfg_cls.from_file.return_value = self._mock_config()
            mock_tunnel = MagicMock()
            mock_tunnel.start.return_value = "https://app.example.com"
            mock_create.return_value = mock_tunnel

            from deepseek_bridge.cli import main
            result = main(["--headless", "--debug"])

            self.assertEqual(result, 0)
            mock_log.assert_called_once()
            call_kwargs = mock_log.call_args[1]
            self.assertTrue(call_kwargs["debug"],
                            "configure_logging should receive debug=True")

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
