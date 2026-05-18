from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from deepseek_bridge.tunnel import (
    CloudflaredTunnel,
    NgrokTunnel,
    create_tunnel,
    get_tunnel_choices,
    local_tunnel_target,
    ngrok_agent_urls,
    parse_ngrok_public_url,
)


class TunnelTests(unittest.TestCase):
    def test_local_tunnel_target_uses_loopback_for_wildcard_hosts(self) -> None:
        self.assertEqual(
            local_tunnel_target("0.0.0.0", 9000), "http://127.0.0.1:9000"
        )
        self.assertEqual(
            local_tunnel_target("::", 9000), "http://127.0.0.1:9000"
        )

    def test_local_tunnel_target_formats_ipv6_hosts(self) -> None:
        self.assertEqual(local_tunnel_target("::1", 9000), "http://[::1]:9000")

    def test_parse_ngrok_public_url_prefers_https(self) -> None:
        payload = {
            "tunnels": [
                {"public_url": "http://example.ngrok-free.app"},
                {"public_url": "https://example.ngrok-free.app"},
            ]
        }

        self.assertEqual(
            parse_ngrok_public_url(payload), "https://example.ngrok-free.app"
        )

    def test_parse_ngrok_public_url_supports_endpoint_api(self) -> None:
        payload = {"endpoints": [{"url": "https://example.ngrok-free.app"}]}

        self.assertEqual(
            parse_ngrok_public_url(payload), "https://example.ngrok-free.app"
        )

    def test_parse_ngrok_public_url_ignores_missing_tunnels(self) -> None:
        self.assertIsNone(parse_ngrok_public_url({"tunnels": []}))
        self.assertIsNone(parse_ngrok_public_url({}))

    def test_ngrok_agent_urls_use_current_api_then_legacy_fallback(
        self,
    ) -> None:
        self.assertEqual(
            ngrok_agent_urls("http://127.0.0.1:4040/api"),
            [
                "http://127.0.0.1:4040/api/endpoints",
                "http://127.0.0.1:4040/api/tunnels",
            ],
        )


class CloudflaredTunnelStartTests(unittest.TestCase):
    """start() validates cf_url and binary availability."""

    def test_cloudflared_start_requires_url(self) -> None:
        tunnel = CloudflaredTunnel(target_url="http://localhost:9000")
        tunnel.cfd_url = ""
        with self.assertRaises(RuntimeError) as ctx:
            tunnel.start()
        self.assertIn("tunnel URL not configured", str(ctx.exception))

    @patch("deepseek_bridge.tunnel.subprocess.Popen")
    @patch(
        "deepseek_bridge.tunnel.shutil.which",
        return_value="/usr/bin/cloudflared",
    )
    @patch("time.sleep", return_value=None)
    def test_cloudflared_start_returns_url(
        self,
        mock_sleep: MagicMock,
        mock_which: MagicMock,
        mock_popen: MagicMock,
    ) -> None:
        mock_proc = MagicMock()
        mock_proc.poll.return_value = None  # Process still running
        mock_popen.return_value = mock_proc
        tunnel = CloudflaredTunnel(target_url="http://localhost:9000")
        tunnel.cfd_url = "https://app.example.com"
        url = tunnel.start()
        self.assertEqual(url, "https://app.example.com")
        mock_popen.assert_called_once()

    @patch("deepseek_bridge.tunnel.shutil.which", return_value=None)
    def test_cloudflared_start_requires_binary(
        self, mock_which: MagicMock
    ) -> None:
        tunnel = CloudflaredTunnel(target_url="http://localhost:9000")
        tunnel.cfd_url = "https://app.example.com"
        with self.assertRaises(RuntimeError) as ctx:
            tunnel.start()
        self.assertIn("cloudflared is not installed", str(ctx.exception))


class CloudflaredTunnelStopTests(unittest.TestCase):
    """stop() terminates the cloudflared process."""

    def test_cloudflared_stop_terminates(self) -> None:
        mock_proc = MagicMock()
        mock_proc.poll.return_value = None

        tunnel = CloudflaredTunnel(target_url="http://localhost:9000")
        tunnel.process = mock_proc
        tunnel.stop()

        mock_proc.terminate.assert_called_once()
        mock_proc.wait.assert_called_once_with(timeout=5)

    def test_stop_skips_if_already_exited(self) -> None:
        mock_proc = MagicMock()
        mock_proc.poll.return_value = 0

        tunnel = CloudflaredTunnel(target_url="http://localhost:9000")
        tunnel.process = mock_proc
        tunnel.stop()

        mock_proc.terminate.assert_not_called()

    def test_stop_skips_if_no_process(self) -> None:
        tunnel = CloudflaredTunnel(target_url="http://localhost:9000")
        tunnel.stop()


class CreateTunnelTests(unittest.TestCase):
    """create_tunnel factory returns correct implementation."""

    def test_cloudflared(self) -> None:
        tunnel = create_tunnel("cloudflared", "http://localhost:9000")
        self.assertIsInstance(tunnel, CloudflaredTunnel)
        self.assertEqual(tunnel.target_url, "http://localhost:9000")

    def test_ngrok(self) -> None:
        tunnel = create_tunnel("ngrok", "http://localhost:9000")
        self.assertIsInstance(tunnel, NgrokTunnel)
        self.assertEqual(tunnel.target_url, "http://localhost:9000")

    def test_invalid_kind_raises_valueerror(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            create_tunnel("nonexistent", "http://localhost:9000")
        self.assertIn("unknown tunnel kind", str(ctx.exception))


class GetTunnelChoicesTests(unittest.TestCase):
    """get_tunnel_choices returns registered tunnel names."""

    def test_returns_registered_kinds(self) -> None:
        choices = get_tunnel_choices()
        self.assertIn("cloudflared", choices)
        self.assertIn("ngrok", choices)


if __name__ == "__main__":
    unittest.main()
