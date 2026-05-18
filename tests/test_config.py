from __future__ import annotations

import os
import stat
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from deepseek_bridge import __version__
from deepseek_bridge.config import (
    DEFAULT_COLLAPSIBLE_REASONING,
    DEFAULT_CORS_ALLOWED_ORIGINS,
    DEFAULT_MISSING_REASONING_STRATEGY,
    DEFAULT_PORT,
    DEFAULT_REASONING_CACHE_MAX_AGE_SECONDS,
    DEFAULT_RUNTIME_MODE,
    DEFAULT_THINKING,
    DEFAULT_UPSTREAM_MODEL,
    ENV_CONFIG_PATH,
    KUBERNETES_HOST,
    KUBERNETES_REASONING_CONTENT_PATH,
    KUBERNETES_RUNTIME_MODE,
    KUBERNETES_TUNNEL,
    ProxyConfig,
    _auto_cache_max_rows,
    default_config_path,
    default_reasoning_content_path,
)


class ConfigTests(unittest.TestCase):
    def setUp(self) -> None:
        self._env_patch = patch.dict(os.environ, {}, clear=True)
        self._env_patch.start()

    def tearDown(self) -> None:
        self._env_patch.stop()

    def test_default_paths_live_in_visible_user_app_directory(self) -> None:
        home = Path("/tmp/home")

        with patch("deepseek_bridge.config.Path.home", return_value=home):
            self.assertEqual(
                default_config_path(), home / ".deepseek-bridge" / "config.yaml"
            )
            self.assertEqual(
                default_reasoning_content_path(),
                home / ".deepseek-bridge" / "reasoning_content.sqlite3",
            )
            self.assertEqual(
                ProxyConfig().reasoning_content_path,
                home / ".deepseek-bridge" / "reasoning_content.sqlite3",
            )
            self.assertEqual(ProxyConfig().tunnel, "cloudflared")
            self.assertEqual(ProxyConfig().runtime_mode, DEFAULT_RUNTIME_MODE)
            self.assertEqual(
                ProxyConfig().collapsible_reasoning,
                DEFAULT_COLLAPSIBLE_REASONING,
            )
            self.assertIsNone(ProxyConfig().trace_dir)

    def test_missing_default_config_file_is_populated(self) -> None:
        with TemporaryDirectory() as temp_dir:
            home = Path(temp_dir)

            with patch("deepseek_bridge.config.Path.home", return_value=home):
                config = ProxyConfig.from_file(config_path=None)
                config_path = default_config_path()

            config_text = config_path.read_text(encoding="utf-8")

            self.assertTrue(config_path.exists())
            self.assertIn("version: 1", config_text)
            self.assertIn("runtime:", config_text)
            self.assertIn(f"  mode: {DEFAULT_RUNTIME_MODE}", config_text)
            self.assertIn("server:", config_text)
            self.assertIn(f"  model: {DEFAULT_UPSTREAM_MODEL}", config_text)
            self.assertIn(
                (
                    "  missing_reasoning_strategy: "
                    f"{DEFAULT_MISSING_REASONING_STRATEGY}"
                ),
                config_text,
            )
            self.assertIn(
                "  max_age_seconds: "
                f"{DEFAULT_REASONING_CACHE_MAX_AGE_SECONDS}",
                config_text,
            )
            self.assertIn("  mode: cloudflared", config_text)
            self.assertIn(
                "  collapsible: "
                f"{str(DEFAULT_COLLAPSIBLE_REASONING).lower()}",
                config_text,
            )
            self.assertIn("  allowed_origins:", config_text)
            self.assertIn("    - http://localhost:*", config_text)
            self.assertIn("  allow_credentials: true", config_text)
            if os.name != "nt":
                self.assertEqual(
                    stat.S_IMODE(config_path.stat().st_mode), 0o600
                )
            self.assertEqual(config.upstream_model, DEFAULT_UPSTREAM_MODEL)
            self.assertEqual(config.runtime_mode, DEFAULT_RUNTIME_MODE)
            self.assertEqual(
                config.collapsible_reasoning,
                DEFAULT_COLLAPSIBLE_REASONING,
            )
            self.assertEqual(
                config.missing_reasoning_strategy,
                DEFAULT_MISSING_REASONING_STRATEGY,
            )
            self.assertEqual(
                config.reasoning_cache_max_age_seconds,
                DEFAULT_REASONING_CACHE_MAX_AGE_SECONDS,
            )
            self.assertIn(
                "    path: null  # auto: ~/.deepseek-bridge/logs",
                config_text,
            )
            self.assertEqual(config.log_dir, home / ".deepseek-bridge" / "logs")

    def test_missing_explicit_config_file_is_not_populated(self) -> None:
        with TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "missing.yaml"

            config = ProxyConfig.from_file(config_path=config_path)

            self.assertFalse(config_path.exists())
            self.assertEqual(config.upstream_model, DEFAULT_UPSTREAM_MODEL)
            self.assertEqual(
                config.reasoning_cache_max_age_seconds,
                DEFAULT_REASONING_CACHE_MAX_AGE_SECONDS,
            )

    def test_loads_config_from_user_yaml_file(self) -> None:
        with TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.yaml"
            reasoning_content_path = (
                Path(temp_dir) / "reasoning_content.sqlite3"
            )
            config_path.write_text(
                "\n".join(
                    [
                        "base_url: https://example.com/v1/",
                        "model: deepseek-v4-flash",
                        "thinking: disabled",
                        "reasoning_effort: max",
                        "port: 9100",
                        "host: 0.0.0.0",
                        "tunnel: cloudflared",
                        "request_timeout: 123.5",
                        "max_request_body_bytes: 1234",
                        "cors: true",
                        "cors_allowed_origins:",
                        "  - https://app.example.com",
                        "  - http://localhost:*",
                        "cors_allow_credentials: false",
                        "display_reasoning: false",
                        "collapsible_reasoning: false",
                        f"reasoning_content_path: {reasoning_content_path}",
                        "missing_reasoning_strategy: reject",
                        "reasoning_cache_max_age_seconds: 60",
                    ]
                ),
                encoding="utf-8",
            )

            config = ProxyConfig.from_file(config_path=config_path)

        self.assertEqual(config.upstream_base_url, "https://example.com/v1")
        self.assertEqual(config.upstream_model, "deepseek-v4-flash")
        self.assertEqual(config.thinking, "disabled")
        self.assertEqual(config.reasoning_effort, "max")
        self.assertEqual(config.host, "0.0.0.0")
        self.assertEqual(config.port, 9100)
        self.assertEqual(config.tunnel, "cloudflared")
        self.assertEqual(config.request_timeout, 123.5)
        self.assertEqual(config.max_request_body_bytes, 1234)
        self.assertTrue(config.cors)
        self.assertEqual(
            config.cors_allowed_origins,
            ("https://app.example.com", "http://localhost:*"),
        )
        self.assertFalse(config.cors_allow_credentials)
        self.assertFalse(config.display_reasoning)
        self.assertFalse(config.collapsible_reasoning)
        self.assertEqual(config.reasoning_content_path, reasoning_content_path)
        self.assertEqual(config.missing_reasoning_strategy, "reject")
        self.assertEqual(config.reasoning_cache_max_age_seconds, 60)

    def test_loads_structured_config_from_yaml_file(self) -> None:
        with TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.yaml"
            config_path.write_text(
                "\n".join(
                    [
                        "version: 1",
                        "server:",
                        "  host: 0.0.0.0",
                        "  port: 9100",
                        "upstream:",
                        "  base_url: https://example.com/v1/",
                        "  model: deepseek-v4-flash",
                        "  thinking:",
                        "    mode: disabled",
                        "    reasoning_effort: low",
                        "storage:",
                        "  backend: sqlite",
                        "  sqlite:",
                        "    path: data/reasoning.sqlite3",
                        "reasoning_cache:",
                        "  max_age_seconds: 60",
                        "  max_entries: 12345",
                        "  missing_reasoning_strategy: reject",
                        "reasoning_display:",
                        "  enabled: false",
                        "  collapsible: false",
                        "logging:",
                        "  level: debug",
                        "  format: text",
                        "  compact: true",
                        "  trace_dir: traces",
                        "  file:",
                        "    enabled: false",
                        "metrics:",
                        "  enabled: false",
                        "tunnel:",
                        "  mode: none",
                        "cors:",
                        "  enabled: false",
                        "  allowed_origins:",
                        "    - https://app.example.com",
                        "  allow_credentials: false",
                        "ollama:",
                        "  enabled: false",
                        "performance:",
                        "  request_timeout: 123.5",
                        "  stream_read_timeout: 90",
                        "  max_request_body_bytes: 1234",
                        "  max_pool_connections: 7",
                        "  max_thread_pool: 9",
                    ]
                ),
                encoding="utf-8",
            )

            config = ProxyConfig.from_file(config_path=config_path, environ={})

        self.assertEqual(config.host, "0.0.0.0")
        self.assertEqual(config.port, 9100)
        self.assertEqual(config.upstream_base_url, "https://example.com/v1")
        self.assertEqual(config.upstream_model, "deepseek-v4-flash")
        self.assertEqual(config.thinking, "disabled")
        self.assertEqual(config.reasoning_effort, "low")
        self.assertEqual(
            config.reasoning_content_path,
            Path(temp_dir) / "data" / "reasoning.sqlite3",
        )
        self.assertEqual(config.reasoning_cache_max_age_seconds, 60)
        self.assertEqual(config.reasoning_cache_max_entries, 12345)
        self.assertEqual(config.missing_reasoning_strategy, "reject")
        self.assertFalse(config.display_reasoning)
        self.assertFalse(config.collapsible_reasoning)
        self.assertTrue(config.debug)
        self.assertTrue(config.compact)
        self.assertEqual(config.trace_dir, Path(temp_dir) / "traces")
        self.assertIsNone(config.log_dir)
        self.assertFalse(config.cors)
        self.assertEqual(
            config.cors_allowed_origins, ("https://app.example.com",)
        )
        self.assertFalse(config.cors_allow_credentials)
        self.assertFalse(config.ollama)
        self.assertEqual(config.request_timeout, 123.5)
        self.assertEqual(config.stream_read_timeout, 90.0)
        self.assertEqual(config.max_request_body_bytes, 1234)
        self.assertEqual(config.max_pool_connections, 7)
        self.assertEqual(config.max_thread_pool, 9)
        self.assertEqual(config.tunnel, "none")

    def test_invalid_config_values_fall_back_to_defaults(self) -> None:
        with TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.yaml"
            config_path.write_text(
                "\n".join(
                    [
                        "thinking: maybe",
                        "missing_reasoning_strategy: maybe",
                        "port: nope",
                        "collasible_reasoning: maybe",
                    ]
                ),
                encoding="utf-8",
            )

            config = ProxyConfig.from_file(config_path=config_path)

        self.assertEqual(config.thinking, DEFAULT_THINKING)
        self.assertEqual(
            config.missing_reasoning_strategy,
            DEFAULT_MISSING_REASONING_STRATEGY,
        )
        self.assertEqual(config.port, DEFAULT_PORT)
        self.assertEqual(
            config.collapsible_reasoning,
            DEFAULT_COLLAPSIBLE_REASONING,
        )

    def test_relative_reasoning_path_in_config_is_relative_to_config_file(
        self,
    ) -> None:
        with TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.yaml"
            config_path.write_text(
                "\n".join(
                    [
                        "reasoning_content_path: custom.sqlite3",
                    ]
                ),
                encoding="utf-8",
            )

            config = ProxyConfig.from_file(config_path=config_path)

        self.assertEqual(
            config.reasoning_content_path, Path(temp_dir) / "custom.sqlite3"
        )

    def test_relative_structured_sqlite_path_is_relative_to_config_file(
        self,
    ) -> None:
        with TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.yaml"
            config_path.write_text(
                "\n".join(
                    [
                        "storage:",
                        "  sqlite:",
                        "    path: custom.sqlite3",
                    ]
                ),
                encoding="utf-8",
            )

            config = ProxyConfig.from_file(config_path=config_path, environ={})

        self.assertEqual(
            config.reasoning_content_path, Path(temp_dir) / "custom.sqlite3"
        )

    def test_structured_tunnel_url_without_mode_uses_default_mode(self) -> None:
        with TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.yaml"
            config_path.write_text(
                "\n".join(
                    [
                        "tunnel:",
                        "  cf_url: https://app.example.com",
                    ]
                ),
                encoding="utf-8",
            )

            config = ProxyConfig.from_file(config_path=config_path, environ={})

        self.assertEqual(config.tunnel, "cloudflared")
        self.assertEqual(config.cf_url, "https://app.example.com")

    def test_cursor_reasoning_display_can_be_disabled_from_config(self) -> None:
        with TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.yaml"
            config_path.write_text(
                "\n".join(
                    [
                        "display_reasoning: false",
                    ]
                ),
                encoding="utf-8",
            )

            config = ProxyConfig.from_file(config_path=config_path)

        self.assertFalse(config.display_reasoning)

    def test_collapsible_reasoning_can_use_corrected_config_key(self) -> None:
        with TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.yaml"
            config_path.write_text(
                "collapsible_reasoning: false\n", encoding="utf-8"
            )

            config = ProxyConfig.from_file(config_path=config_path)

        self.assertFalse(config.collapsible_reasoning)

    def test_cors_allowed_origins_accepts_csv_string(self) -> None:
        with TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.yaml"
            config_path.write_text(
                "cors_allowed_origins: "
                "https://app.example.com, http://localhost:3000/\n",
                encoding="utf-8",
            )

            config = ProxyConfig.from_file(config_path=config_path)

        self.assertEqual(
            config.cors_allowed_origins,
            ("https://app.example.com", "http://localhost:3000"),
        )

    def test_invalid_cors_allowed_origins_falls_back_to_default(self) -> None:
        with TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.yaml"
            config_path.write_text(
                "cors_allowed_origins: 123\n", encoding="utf-8"
            )

            config = ProxyConfig.from_file(config_path=config_path)

        self.assertEqual(
            config.cors_allowed_origins, DEFAULT_CORS_ALLOWED_ORIGINS
        )

    def test_invalid_yaml_config_raises_value_error(self) -> None:
        with TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.yaml"
            config_path.write_text("- not\n- a\n- mapping\n", encoding="utf-8")

            with self.assertRaises(ValueError):
                ProxyConfig.from_file(config_path=config_path)

    def test_legacy_process_environment_does_not_override_config(self) -> None:
        with TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.yaml"
            config_path.write_text("tunnel: ngrok\n", encoding="utf-8")

            with patch.dict(
                "os.environ",
                {
                    "PROXY_VERBOSE": "true",
                    "DEEPSEEK_CURSOR_PROXY_CONFIG_PATH": "/ignored.yaml",
                },
                clear=False,
            ):
                config = ProxyConfig.from_file(config_path=config_path)
                self.assertEqual(os.environ.get("PROXY_VERBOSE"), "true")
                self.assertEqual(
                    os.environ.get("DEEPSEEK_CURSOR_PROXY_CONFIG_PATH"),
                    "/ignored.yaml",
                )

        self.assertEqual(config.tunnel, "ngrok")

    def test_kubernetes_runtime_defaults_are_read_only_friendly(self) -> None:
        with TemporaryDirectory() as temp_dir:
            home = Path(temp_dir)

            with patch("deepseek_bridge.config.Path.home", return_value=home):
                config = ProxyConfig.from_file(
                    config_path=None,
                    environ={
                        "DEEPSEEK_BRIDGE_RUNTIME_MODE": KUBERNETES_RUNTIME_MODE
                    },
                )
                config_path = default_config_path()

            self.assertFalse(config_path.exists())
            self.assertEqual(config.runtime_mode, KUBERNETES_RUNTIME_MODE)
            self.assertEqual(config.host, KUBERNETES_HOST)
            self.assertEqual(config.port, DEFAULT_PORT)
            self.assertEqual(config.tunnel, KUBERNETES_TUNNEL)
            self.assertEqual(
                config.reasoning_content_path,
                KUBERNETES_REASONING_CONTENT_PATH,
            )
            self.assertIsNone(config.log_dir)

    def test_environment_overrides_config_file(self) -> None:
        with TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.yaml"
            config_path.write_text(
                "\n".join(
                    [
                        "host: 127.0.0.1",
                        "port: 9000",
                        "model: deepseek-v4-pro",
                        "thinking: enabled",
                        "reasoning_effort: max",
                        "tunnel: cloudflared",
                        "reasoning_cache_max_age_seconds: 60",
                    ]
                ),
                encoding="utf-8",
            )

            config = ProxyConfig.from_file(
                config_path=config_path,
                environ={
                    "DEEPSEEK_BRIDGE_HOST": "0.0.0.0",
                    "DEEPSEEK_BRIDGE_PORT": "9100",
                    "DEEPSEEK_BRIDGE_MODEL": "deepseek-v4-flash",
                    "DEEPSEEK_BRIDGE_THINKING": "disabled",
                    "DEEPSEEK_BRIDGE_REASONING_EFFORT": "low",
                    "DEEPSEEK_BRIDGE_TUNNEL_MODE": "none",
                    "DEEPSEEK_BRIDGE_LOG_FILE_ENABLED": "false",
                    "DEEPSEEK_BRIDGE_REASONING_CACHE_MAX_AGE_SECONDS": "120",
                    "DEEPSEEK_BRIDGE_REASONING_CACHE_MAX_ENTRIES": "10001",
                    "DEEPSEEK_BRIDGE_REASONING_CONTENT_PATH": "env.sqlite3",
                },
            )

        self.assertEqual(config.host, "0.0.0.0")
        self.assertEqual(config.port, 9100)
        self.assertEqual(config.upstream_model, "deepseek-v4-flash")
        self.assertEqual(config.thinking, "disabled")
        self.assertEqual(config.reasoning_effort, "low")
        self.assertEqual(config.tunnel, "none")
        self.assertEqual(config.reasoning_cache_max_age_seconds, 120)
        self.assertEqual(config.reasoning_cache_max_entries, 10001)
        self.assertEqual(
            config.reasoning_content_path, Path.cwd() / "env.sqlite3"
        )
        self.assertIsNone(config.log_dir)

    def test_memory_reasoning_path_is_not_resolved_relative_to_config(
        self,
    ) -> None:
        with TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.yaml"
            config_path.write_text(
                'reasoning_content_path: ":memory:"\n',
                encoding="utf-8",
            )

            config = ProxyConfig.from_file(config_path=config_path)

        self.assertEqual(config.reasoning_content_path, ":memory:")

    def test_config_path_can_come_from_environment(self) -> None:
        with TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "env-config.yaml"
            config_path.write_text("port: 9191\n", encoding="utf-8")

            with patch.dict(
                os.environ,
                {ENV_CONFIG_PATH: str(config_path)},
                clear=True,
            ):
                config = ProxyConfig.from_file(config_path=None)

        self.assertEqual(config.port, 9191)

    def test_invalid_environment_value_raises_value_error(self) -> None:
        with TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.yaml"
            config_path.write_text("", encoding="utf-8")
            with (
                patch.dict(
                    os.environ,
                    {"DEEPSEEK_BRIDGE_PORT": "not-a-port"},
                    clear=True,
                ),
                self.assertRaisesRegex(ValueError, "DEEPSEEK_BRIDGE_PORT"),
            ):
                ProxyConfig.from_file(config_path=config_path)

    def test_invalid_structured_config_value_raises_value_error(self) -> None:
        with TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.yaml"
            config_path.write_text(
                "\n".join(["server:", "  port: not-a-port"]),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "server.port"):
                ProxyConfig.from_file(config_path=config_path, environ={})

    def test_structured_port_must_be_in_tcp_port_range(self) -> None:
        with TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.yaml"
            config_path.write_text(
                "\n".join(["server:", "  port: 70000"]),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "server.port"):
                ProxyConfig.from_file(config_path=config_path, environ={})

    def test_environment_port_must_be_in_tcp_port_range(self) -> None:
        with TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.yaml"
            config_path.write_text("", encoding="utf-8")
            with (
                patch.dict(
                    os.environ,
                    {"DEEPSEEK_BRIDGE_PORT": "70000"},
                    clear=True,
                ),
                self.assertRaisesRegex(ValueError, "DEEPSEEK_BRIDGE_PORT"),
            ):
                ProxyConfig.from_file(config_path=config_path)

    def test_invalid_structured_boolean_raises_value_error(self) -> None:
        with TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.yaml"
            config_path.write_text(
                "\n".join(["reasoning_display:", "  enabled: maybe"]),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(
                ValueError, "reasoning_display.enabled"
            ):
                ProxyConfig.from_file(config_path=config_path, environ={})

    def test_invalid_structured_tunnel_mode_raises_value_error(self) -> None:
        with TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.yaml"
            config_path.write_text(
                "\n".join(["tunnel:", "  mode: invalid"]),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "tunnel.mode"):
                ProxyConfig.from_file(config_path=config_path, environ={})

    def test_unsupported_future_config_knobs_raise_value_error(self) -> None:
        cases = [
            ("storage:\n  backend: valkey\n", "storage backend 'valkey'"),
            ("metrics:\n  enabled: true\n", "metrics.enabled"),
            ("logging:\n  format: json\n", "logging format 'json'"),
        ]
        for text, pattern in cases:
            with (
                self.subTest(pattern=pattern),
                TemporaryDirectory() as temp_dir,
            ):
                config_path = Path(temp_dir) / "config.yaml"
                config_path.write_text(text, encoding="utf-8")

                with self.assertRaisesRegex(ValueError, pattern):
                    ProxyConfig.from_file(config_path=config_path, environ={})

    def test_version_is_valid_pep440(self) -> None:
        parts = __version__.split(".")
        self.assertGreaterEqual(len(parts), 3)
        self.assertTrue(parts[0].isdigit() and parts[1].isdigit())
        self.assertNotEqual(__version__, "0.1.0")

    def test_default_log_dir_is_set(self) -> None:
        config = ProxyConfig()
        self.assertIsNotNone(config.log_dir)
        self.assertIn("logs", str(config.log_dir))

    def test_auto_cache_max_rows_returns_reasonable_value(self) -> None:
        """Auto-calc returns a value >= 10000."""
        rows = _auto_cache_max_rows(disk_budget_mb=500)
        self.assertGreaterEqual(rows, 10000)
        rows2 = _auto_cache_max_rows(disk_budget_mb=10)
        self.assertGreaterEqual(rows2, 10000)  # floor

    def test_version_flag_in_arg_parser(self) -> None:
        """--version is available via argparse version action."""
        from deepseek_bridge.server import build_arg_parser

        parser = build_arg_parser()
        try:
            parser.parse_args(["--version"])
        except SystemExit as e:
            self.assertEqual(e.code, 0)


if __name__ == "__main__":
    unittest.main()
