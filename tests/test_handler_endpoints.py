"""Unit tests for handler endpoint methods (_send_models, _send_health,
_handle_api_version, _handle_api_tags, _handle_api_show)."""

from __future__ import annotations

import io
import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from deepseek_bridge import __version__
from deepseek_bridge.config import ProxyConfig
from deepseek_bridge.handler import DeepSeekProxyHandler
from deepseek_bridge.reasoning_store import ReasoningStore


def _make_handler_stub(wfile, **config_overrides):
    handler = object.__new__(DeepSeekProxyHandler)
    handler.server = SimpleNamespace(
        config=ProxyConfig(**config_overrides),
        reasoning_store=ReasoningStore(":memory:"),
    )
    handler.wfile = wfile
    handler.close_connection = False
    handler._status = None
    handler.send_response = lambda status: setattr(handler, "_status", status)
    handler.send_header = lambda name, value: None
    handler.end_headers = lambda: None
    handler._request_id = "req-test-123"
    return handler


class SendModelsTests(unittest.TestCase):
    def test_returns_model_list_with_default_model(self) -> None:
        wfile = io.BytesIO()
        handler = _make_handler_stub(wfile, upstream_model="deepseek-v4-pro")
        handler._send_models()
        body = json.loads(wfile.getvalue())
        self.assertEqual(body["object"], "list")
        model_ids = {m["id"] for m in body["data"]}
        self.assertIn("deepseek-v4-pro", model_ids)
        self.assertIn("deepseek-v4-flash", model_ids)
        for m in body["data"]:
            self.assertEqual(m["object"], "model")
            self.assertEqual(m["owned_by"], "deepseek")


class SendHealthTests(unittest.TestCase):
    def test_returns_ok_with_uptime(self) -> None:
        wfile = io.BytesIO()
        handler = _make_handler_stub(wfile)
        handler.server.start_time = 1000.0
        with patch("time.monotonic", return_value=1060.0):
            handler._send_health()
        body = json.loads(wfile.getvalue())
        self.assertTrue(body["ok"])
        self.assertEqual(body["server"], "deepseek-bridge")
        self.assertEqual(body["uptime_seconds"], 60)

    def test_uptime_zero_when_no_start_time(self) -> None:
        wfile = io.BytesIO()
        handler = _make_handler_stub(wfile)
        handler._send_health()
        body = json.loads(wfile.getvalue())
        self.assertTrue(body["ok"])
        self.assertEqual(body["uptime_seconds"], 0)


class SendReadyTests(unittest.TestCase):
    def test_returns_ok_when_all_checks_pass(self) -> None:
        wfile = io.BytesIO()
        handler = _make_handler_stub(wfile)
        handler.server.readiness_checks = lambda: {
            "storage": {"ok": True, "status": "ok"},
            "shutdown": {"ok": True, "status": "ok"},
        }

        handler._send_ready()

        body = json.loads(wfile.getvalue())
        self.assertEqual(handler._status, 200)
        self.assertTrue(body["ok"])
        self.assertEqual(body["checks"]["storage"]["status"], "ok")

    def test_returns_503_when_a_check_fails(self) -> None:
        wfile = io.BytesIO()
        handler = _make_handler_stub(wfile)
        handler.server.readiness_checks = lambda: {
            "storage": {"ok": False, "status": "closed"},
            "shutdown": {"ok": True, "status": "ok"},
        }

        handler._send_ready()

        body = json.loads(wfile.getvalue())
        self.assertEqual(handler._status, 503)
        self.assertFalse(body["ok"])
        self.assertEqual(body["checks"]["storage"]["status"], "closed")

    def test_health_stays_ok_when_readiness_would_fail(self) -> None:
        wfile = io.BytesIO()
        handler = _make_handler_stub(wfile)
        handler.server.readiness_checks = lambda: {
            "storage": {"ok": False, "status": "closed"}
        }

        handler._send_health()

        body = json.loads(wfile.getvalue())
        self.assertEqual(handler._status, 200)
        self.assertTrue(body["ok"])


class HandleApiVersionTests(unittest.TestCase):
    def test_returns_current_version(self) -> None:
        wfile = io.BytesIO()
        handler = _make_handler_stub(wfile)
        handler._handle_api_version()
        body = json.loads(wfile.getvalue())
        self.assertEqual(body["version"], __version__)


class HandleApiTagsTests(unittest.TestCase):
    def test_returns_ollama_tag_list(self) -> None:
        wfile = io.BytesIO()
        handler = _make_handler_stub(wfile, upstream_model="deepseek-v4-pro")
        handler._handle_api_tags()
        body = json.loads(wfile.getvalue())
        self.assertIn("models", body)
        model_names = {m["name"] for m in body["models"]}
        self.assertIn("deepseek-v4-pro", model_names)
        self.assertIn("deepseek-v4-flash", model_names)
        for m in body["models"]:
            self.assertIn("digest", m)
            self.assertTrue(
                m["digest"].startswith("sha256:"), f"bad digest: {m['digest']}"
            )
            self.assertEqual(m["details"]["format"], "gguf")
            self.assertIn("parameter_size", m["details"])


class HandleApiShowTests(unittest.TestCase):
    def test_returns_tool_use_support_for_known_model(self) -> None:
        wfile = io.BytesIO()
        handler = _make_handler_stub(wfile, upstream_model="deepseek-v4-pro")
        body_data = json.dumps({"model": "deepseek-v4-pro"}).encode()
        handler.rfile = io.BytesIO(body_data)
        handler.headers = {"Content-Length": str(len(body_data))}
        handler._handle_api_show()
        body = json.loads(wfile.getvalue())
        self.assertIn("capabilities", body)
        self.assertTrue(body["capabilities"]["supports"]["tool_calls"])

    def test_returns_deepseek_architecture(self) -> None:
        wfile = io.BytesIO()
        handler = _make_handler_stub(wfile, upstream_model="deepseek-v4-pro")
        body_data = json.dumps({"model": "deepseek-v4-pro"}).encode()
        handler.rfile = io.BytesIO(body_data)
        handler.headers = {"Content-Length": str(len(body_data))}
        handler._handle_api_show()
        body = json.loads(wfile.getvalue())
        self.assertEqual(body["details"]["family"], "deepseek")

    def test_invalid_json_returns_400(self) -> None:
        wfile = io.BytesIO()
        handler = _make_handler_stub(wfile)
        handler.rfile = io.BytesIO(b"not json")
        handler.headers = {"Content-Length": "8"}
        handler._handle_api_show()
        body = json.loads(wfile.getvalue())
        self.assertIn("error", body)

    def test_missing_model_uses_configured_fallback(self) -> None:
        wfile = io.BytesIO()
        handler = _make_handler_stub(wfile, upstream_model="deepseek-v4-flash")
        body_data = json.dumps({}).encode()
        handler.rfile = io.BytesIO(body_data)
        handler.headers = {"Content-Length": str(len(body_data))}
        handler._handle_api_show()
        body = json.loads(wfile.getvalue())
        self.assertEqual(body["details"]["family"], "deepseek")
