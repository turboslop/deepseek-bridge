from __future__ import annotations

import socket
import subprocess
import sys
import time
import unittest
from http.client import HTTPConnection


def _find_free_port() -> int:
    """Find a free port for the proxy."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class ProxyStartupTests(unittest.TestCase):
    """Verify the proxy can boot in headless mode and respond to requests."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.port = _find_free_port()
        cls.proc = subprocess.Popen(
            [
                sys.executable, "-m", "deepseek_bridge",
                "--headless", "--tunnel", "none",
                "--port", str(cls.port),
                "--no-log",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
        )
        # Wait for proxy to be ready (macOS CI runners can take 30-60s to cold-start uv+Python)
        timeout_s = 60
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            try:
                conn = HTTPConnection("127.0.0.1", cls.port, timeout=1)
                conn.request("GET", "/v1/health")
                resp = conn.getresponse()
                if resp.status == 200:
                    conn.close()
                    break
            except Exception:
                pass
            time.sleep(0.2)
        else:
            cls.proc.kill()
            stderr = cls.proc.stderr.read() if cls.proc.stderr else ""
            raise RuntimeError(
                f"Proxy did not start within {timeout_s}s. stderr:\n{stderr}"
            )

    @classmethod
    def tearDownClass(cls) -> None:
        cls.proc.terminate()
        try:
            cls.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            cls.proc.kill()
            cls.proc.wait()

    def test_health_endpoint(self) -> None:
        conn = HTTPConnection("127.0.0.1", self.port, timeout=2)
        conn.request("GET", "/v1/health")
        resp = conn.getresponse()
        self.assertEqual(resp.status, 200)
        body = resp.read().decode()
        self.assertIn("ok", body)
        conn.close()

    def test_models_endpoint(self) -> None:
        conn = HTTPConnection("127.0.0.1", self.port, timeout=2)
        conn.request("GET", "/v1/models")
        resp = conn.getresponse()
        self.assertEqual(resp.status, 200)
        conn.close()
