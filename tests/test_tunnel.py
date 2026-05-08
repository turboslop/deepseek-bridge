from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from deepseek_bridge.tunnel import (
    LocalhostRunTunnel,
    NgrokTunnel,
    create_tunnel,
    get_tunnel_choices,
    local_tunnel_target,
    ngrok_agent_urls,
    parse_ngrok_public_url,
)


class TunnelTests(unittest.TestCase):
    def test_local_tunnel_target_uses_loopback_for_wildcard_hosts(self) -> None:
        self.assertEqual(local_tunnel_target("0.0.0.0", 9000), "http://127.0.0.1:9000")
        self.assertEqual(local_tunnel_target("::", 9000), "http://127.0.0.1:9000")

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

    def test_ngrok_agent_urls_use_current_api_then_legacy_fallback(self) -> None:
        self.assertEqual(
            ngrok_agent_urls("http://127.0.0.1:4040/api"),
            [
                "http://127.0.0.1:4040/api/endpoints",
                "http://127.0.0.1:4040/api/tunnels",
            ],
        )


class LocalhostRunTunnelParseTargetTests(unittest.TestCase):
    """_parse_target extracts host/port from target_url."""

    def test_localhost_with_port(self) -> None:
        tunnel = LocalhostRunTunnel(target_url="http://localhost:9000")
        self.assertEqual(tunnel._parse_target(), ("localhost", 9000))

    def test_loopback_with_port(self) -> None:
        tunnel = LocalhostRunTunnel(target_url="http://127.0.0.1:8080")
        self.assertEqual(tunnel._parse_target(), ("127.0.0.1", 8080))

    def test_wildcard_maps_to_loopback(self) -> None:
        tunnel = LocalhostRunTunnel(target_url="http://0.0.0.0:9000")
        self.assertEqual(tunnel._parse_target(), ("127.0.0.1", 9000))

    def test_ipv6_wildcard_maps_to_loopback(self) -> None:
        tunnel = LocalhostRunTunnel(target_url="http://[::]:3000")
        self.assertEqual(tunnel._parse_target(), ("127.0.0.1", 3000))

    def test_no_port_defaults_to_80(self) -> None:
        tunnel = LocalhostRunTunnel(target_url="http://example.com")
        self.assertEqual(tunnel._parse_target(), ("example.com", 80))

    def test_custom_host_with_port(self) -> None:
        tunnel = LocalhostRunTunnel(target_url="http://example.com:1234")
        self.assertEqual(tunnel._parse_target(), ("example.com", 1234))

    def test_no_scheme_defaults(self) -> None:
        tunnel = LocalhostRunTunnel(target_url="127.0.0.1:9000")
        host, port = tunnel._parse_target()
        self.assertEqual(port, 80)


class LocalhostRunTunnelStartTests(unittest.TestCase):
    """start() launches SSH via subprocess and parses the tunnel URL."""

    @patch("deepseek_bridge.tunnel.subprocess.Popen")
    def test_start_parses_url_from_stdout(self, mock_popen: MagicMock) -> None:
        mock_proc = MagicMock()
        mock_proc.stdout = [
            "Connect to http://abc123.localhost.run\n",
            "Tunnel established.\n",
        ]
        mock_popen.return_value = mock_proc

        tunnel = LocalhostRunTunnel(target_url="http://localhost:9000")
        url = tunnel.start()

        self.assertEqual(url, "https://abc123.localhost.run")
        mock_popen.assert_called_once()

    @patch("deepseek_bridge.tunnel.subprocess.Popen")
    def test_start_ssh_command(self, mock_popen: MagicMock) -> None:
        mock_proc = MagicMock()
        mock_proc.stdout = ["https://xyz1.loca.lt\n"]
        mock_popen.return_value = mock_proc

        tunnel = LocalhostRunTunnel(target_url="http://127.0.0.1:8080")
        tunnel.start()

        call_args = mock_popen.call_args[0][0]
        self.assertEqual(
            call_args,
            ["ssh", "-R", "80:127.0.0.1:8080", "nokey@localhost.run"],
        )

    @patch("deepseek_bridge.tunnel.subprocess.Popen")
    def test_start_upgrades_http_to_https(self, mock_popen: MagicMock) -> None:
        mock_proc = MagicMock()
        mock_proc.stdout = ["http://abc.localhost.run\n"]
        mock_popen.return_value = mock_proc

        tunnel = LocalhostRunTunnel(target_url="http://localhost:9000")
        url = tunnel.start()

        self.assertEqual(url, "https://abc.localhost.run")

    @patch("deepseek_bridge.tunnel.subprocess.Popen")
    def test_start_process_exits_before_url_raises(self, mock_popen: MagicMock) -> None:
        mock_proc = MagicMock()
        mock_proc.stdout = []
        mock_popen.return_value = mock_proc

        tunnel = LocalhostRunTunnel(target_url="http://localhost:9000")
        with self.assertRaises(RuntimeError) as ctx:
            tunnel.start()
        self.assertIn("exited before reporting", str(ctx.exception))


class LocalhostRunTunnelStopTests(unittest.TestCase):
    """stop() terminates the SSH process."""

    def test_stop_terminates_running_process(self) -> None:
        from unittest.mock import MagicMock
        mock_proc = MagicMock()
        mock_proc.poll.return_value = None

        tunnel = LocalhostRunTunnel(target_url="http://localhost:9000")
        tunnel.process = mock_proc
        tunnel.stop()

        mock_proc.terminate.assert_called_once()
        mock_proc.wait.assert_called_once_with(timeout=5)

    def test_stop_skips_if_already_exited(self) -> None:
        mock_proc = MagicMock()
        mock_proc.poll.return_value = 0

        tunnel = LocalhostRunTunnel(target_url="http://localhost:9000")
        tunnel.process = mock_proc
        tunnel.stop()

        mock_proc.terminate.assert_not_called()

    def test_stop_skips_if_no_process(self) -> None:
        tunnel = LocalhostRunTunnel(target_url="http://localhost:9000")
        tunnel.stop()


class CreateTunnelTests(unittest.TestCase):
    """create_tunnel factory returns correct implementation."""

    def test_localhostrun(self) -> None:
        tunnel = create_tunnel("localhostrun", "http://localhost:9000")
        self.assertIsInstance(tunnel, LocalhostRunTunnel)
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
        self.assertIn("localhostrun", choices)
        self.assertIn("ngrok", choices)


if __name__ == "__main__":
    unittest.main()
