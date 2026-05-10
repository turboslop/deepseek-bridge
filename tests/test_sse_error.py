"""Tests for HandlerResponse._send_sse_error.

Covers the error-after-headers-sent code path where an SSE-formatted
error is written to the client instead of a normal HTTP error response.
"""

from __future__ import annotations

import io
import json
import unittest
from types import SimpleNamespace

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
    handler.send_response = lambda status: None
    handler.send_header = lambda name, value: None
    handler.end_headers = lambda: None
    handler._request_id = "req-test-123"
    return handler


class SendSseErrorTests(unittest.TestCase):
    def test_sends_error_sse_to_client(self) -> None:
        """Error SSE is written to wfile and method returns True."""
        wfile = io.BytesIO()
        handler = _make_handler_stub(wfile)
        result = handler._send_sse_error(502, "Upstream returned 502")
        self.assertTrue(result)

    def test_sse_contains_valid_error_json(self) -> None:
        """Written data is a valid SSE line with OpenAI error format."""
        wfile = io.BytesIO()
        handler = _make_handler_stub(wfile)
        handler._send_sse_error(502, "Upstream returned 502")
        raw = wfile.getvalue()
        self.assertTrue(raw.startswith(b"data: "))
        self.assertTrue(raw.endswith(b"\n\n"))
        # Parse the JSON payload after "data: " prefix
        payload = json.loads(raw[len(b"data: "):])
        self.assertIn("error", payload)
        error = payload["error"]
        self.assertEqual(error["message"], "Upstream returned 502")
        self.assertEqual(error["type"], "upstream_error")
        self.assertEqual(error["code"], "upstream_error")
        self.assertIsNone(error["param"])

    def test_sse_error_with_status_code_in_message(self) -> None:
        """Message includes the upstream status code."""
        wfile = io.BytesIO()
        handler = _make_handler_stub(wfile)
        handler._send_sse_error(500, "Upstream returned 500")
        raw = wfile.getvalue()
        payload = json.loads(raw[len(b"data: "):].strip())
        self.assertEqual(payload["error"]["message"], "Upstream returned 500")

    def test_handles_client_disconnect_gracefully(self) -> None:
        """Returns False when client disconnects."""
        class BrokenWfile:
            def write(self, _data: bytes) -> int:
                raise BrokenPipeError("Client disconnected")
            def flush(self) -> None:
                pass

        handler = _make_handler_stub(BrokenWfile())
        result = handler._send_sse_error(502, "Upstream returned 502")
        self.assertFalse(result)

    def test_handles_connection_error_gracefully(self) -> None:
        """Returns False on ConnectionError."""
        class BrokenWfile:
            def write(self, _data: bytes) -> int:
                raise ConnectionError("Connection reset")
            def flush(self) -> None:
                pass

        handler = _make_handler_stub(BrokenWfile())
        result = handler._send_sse_error(503, "Upstream returned 503")
        self.assertFalse(result)


if __name__ == "__main__":
    unittest.main()
