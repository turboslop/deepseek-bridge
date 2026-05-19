from __future__ import annotations

import io
import json
import threading
import unittest
from types import SimpleNamespace
from urllib.request import Request, urlopen

from deepseek_bridge.config import ProxyConfig
from deepseek_bridge.handler import DeepSeekProxyHandler
from deepseek_bridge.reasoning_store import ReasoningStore
from deepseek_bridge.server import DeepSeekProxyServer, UpstreamPool


def _make_handler_stub(
    *,
    origin: str | None = "http://localhost:3000",
    **config_overrides: object,
) -> tuple[DeepSeekProxyHandler, list[tuple[str, str]]]:
    handler = object.__new__(DeepSeekProxyHandler)
    handler.server = SimpleNamespace(config=ProxyConfig(**config_overrides))
    handler.headers = {"Origin": origin} if origin is not None else {}
    handler.wfile = io.BytesIO()
    handler.close_connection = False
    handler.path = "/v1/models"
    handler.client_address = ("127.0.0.1", 12345)
    sent_headers: list[tuple[str, str]] = []
    handler.send_response = lambda status: sent_headers.append(
        (":status", str(status))
    )
    handler.send_header = lambda name, value: sent_headers.append((name, value))
    handler.end_headers = lambda: None
    return handler, sent_headers


def _header_values(headers: list[tuple[str, str]], name: str) -> list[str]:
    return [value for header_name, value in headers if header_name == name]


class CorsHeaderTests(unittest.TestCase):
    def test_normal_response_echoes_allowed_origin_with_credentials(
        self,
    ) -> None:
        handler, headers = _make_handler_stub(origin="http://localhost:5173")

        handler._send_json(200, {"ok": True})

        self.assertEqual(
            _header_values(headers, "Access-Control-Allow-Origin"),
            ["http://localhost:5173"],
        )
        self.assertEqual(
            _header_values(headers, "Access-Control-Allow-Credentials"),
            ["true"],
        )
        self.assertEqual(_header_values(headers, "Vary"), ["Origin"])
        self.assertEqual(json.loads(handler.wfile.getvalue()), {"ok": True})

    def test_preflight_uses_same_origin_specific_cors_policy(self) -> None:
        handler, headers = _make_handler_stub(origin="http://127.0.0.1:5173")

        handler.do_OPTIONS()

        self.assertEqual(_header_values(headers, ":status"), ["204"])
        self.assertEqual(
            _header_values(headers, "Access-Control-Allow-Origin"),
            ["http://127.0.0.1:5173"],
        )
        self.assertEqual(
            _header_values(headers, "Access-Control-Allow-Credentials"),
            ["true"],
        )
        self.assertEqual(_header_values(headers, "Vary"), ["Origin"])

    def test_cors_enabled_without_origin_does_not_send_wildcard_credentials(
        self,
    ) -> None:
        handler, headers = _make_handler_stub(origin=None)

        handler._send_json(200, {"ok": True})

        self.assertEqual(
            _header_values(headers, "Access-Control-Allow-Origin"), []
        )
        self.assertEqual(
            _header_values(headers, "Access-Control-Allow-Credentials"), []
        )

    def test_disallowed_origin_gets_no_origin_or_credentials(self) -> None:
        handler, headers = _make_handler_stub(
            origin="https://app.example.com",
            cors_allowed_origins=("https://admin.example.com",),
        )

        handler._send_json(200, {"ok": True})

        self.assertEqual(
            _header_values(headers, "Access-Control-Allow-Origin"), []
        )
        self.assertEqual(
            _header_values(headers, "Access-Control-Allow-Credentials"), []
        )
        self.assertEqual(_header_values(headers, "Vary"), ["Origin"])

    def test_exact_origin_match_ignores_scheme_and_host_case(self) -> None:
        handler, headers = _make_handler_stub(
            origin="https://app.example.com:8443",
            cors_allowed_origins=("HTTPS://App.Example.Com:8443",),
        )

        handler._send_json(200, {"ok": True})

        self.assertEqual(
            _header_values(headers, "Access-Control-Allow-Origin"),
            ["https://app.example.com:8443"],
        )
        self.assertEqual(
            _header_values(headers, "Access-Control-Allow-Credentials"),
            ["true"],
        )

    def test_wildcard_without_credentials_uses_wildcard_origin(self) -> None:
        handler, headers = _make_handler_stub(
            origin="https://app.example.com",
            cors_allowed_origins=("*",),
            cors_allow_credentials=False,
        )

        handler._send_json(200, {"ok": True})

        self.assertEqual(
            _header_values(headers, "Access-Control-Allow-Origin"), ["*"]
        )
        self.assertEqual(
            _header_values(headers, "Access-Control-Allow-Credentials"), []
        )

    def test_wildcard_with_credentials_echoes_origin(self) -> None:
        handler, headers = _make_handler_stub(
            origin="https://app.example.com",
            cors_allowed_origins=("*",),
            cors_allow_credentials=True,
        )

        handler._send_json(200, {"ok": True})

        self.assertEqual(
            _header_values(headers, "Access-Control-Allow-Origin"),
            ["https://app.example.com"],
        )
        self.assertEqual(
            _header_values(headers, "Access-Control-Allow-Credentials"),
            ["true"],
        )

    def test_unsafe_origin_header_is_not_echoed(self) -> None:
        handler, headers = _make_handler_stub(
            origin="https://app.example.com\r\nX-Bad: 1",
            cors_allowed_origins=("*",),
            cors_allow_credentials=True,
        )

        handler._send_json(200, {"ok": True})

        self.assertEqual(
            _header_values(headers, "Access-Control-Allow-Origin"), []
        )
        self.assertEqual(
            _header_values(headers, "Access-Control-Allow-Credentials"), []
        )

    def test_cors_disabled_sends_no_cors_headers(self) -> None:
        handler, headers = _make_handler_stub(cors=False)

        handler._send_json(200, {"ok": True})

        self.assertEqual(
            [name for name, _ in headers if name.startswith("Access-Control-")],
            [],
        )
        self.assertEqual(_header_values(headers, "Vary"), [])


class HttpCorsTests(unittest.TestCase):
    def test_http_preflight_reflects_allowed_origin(self) -> None:
        server = DeepSeekProxyServer(("127.0.0.1", 0), DeepSeekProxyHandler)
        server.config = ProxyConfig()
        server.reasoning_store = ReasoningStore(":memory:")
        server.upstream_pool = UpstreamPool()
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            host, port = server.server_address
            request = Request(
                f"http://{host}:{port}/v1/chat/completions",
                method="OPTIONS",
                headers={
                    "Origin": "http://localhost:3000",
                    "Access-Control-Request-Method": "POST",
                },
            )

            with urlopen(request, timeout=10) as response:
                self.assertEqual(response.status, 204)
                self.assertEqual(
                    response.headers["Access-Control-Allow-Origin"],
                    "http://localhost:3000",
                )
                self.assertEqual(
                    response.headers["Access-Control-Allow-Credentials"],
                    "true",
                )
                self.assertEqual(response.headers["Vary"], "Origin")
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)
            server.reasoning_store.close()
