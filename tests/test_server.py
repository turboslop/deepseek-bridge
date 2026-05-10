"""Server boundary, CLI, and operational tests.

Pure helper tests (gzip, summarize) and stub-handler tests (client
disconnect) live near the top. The bottom of the file boots a real proxy +
tiny upstream to exercise things that need the HTTP layer: bearer token
forwarding, oversized body, missing-bearer rejection, logging modes, and
streaming connection close.
"""

from __future__ import annotations

from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
import json
import logging
from pathlib import Path
from tempfile import TemporaryDirectory
import threading
import time
from types import SimpleNamespace
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from deepseek_bridge.config import ProxyConfig
from deepseek_bridge.logging import (
    ConsoleLogFormatter,
    TerminalSpinner,
    configure_logging,
)
from deepseek_bridge.reasoning_store import ReasoningStore
from deepseek_bridge.server import (
    BoundedThreadPoolHTTPServer,
    DeepSeekProxyHandler,
    DeepSeekProxyServer,
    UpstreamPool,
    build_arg_parser,
    read_response_body,
)
from deepseek_bridge.logging import _truncate_message_content

# ---------------------------------------------------------------------------
# Stubs for fast in-process tests of internal handler methods
# ---------------------------------------------------------------------------


class _FakeResponse:
    def __init__(self, body: bytes, encoding: str = "", status: int = 200) -> None:
        self._body = BytesIO(body)
        self.headers = {"Content-Encoding": encoding} if encoding else {}
        self.status = status

    def read(self) -> bytes:
        return self._body.read()


class _FakeStreamingResponse:
    status = 200
    headers = {"Content-Type": "text/event-stream"}

    def __init__(self, lines: list[bytes]) -> None:
        self._lines = lines
        self.readline_calls = 0

    def readline(self) -> bytes:
        self.readline_calls += 1
        if not self._lines:
            return b""
        return self._lines.pop(0)

    def read(self, size: int = -1) -> bytes:
        """Read up to *size* bytes, returning whole lines to simulate buffered I/O."""
        if not self._lines:
            return b""
        result = b""
        remaining = size if size > 0 else float("inf")
        while self._lines and remaining > 0:
            line = self._lines.pop(0)
            result += line
            remaining -= len(line)
        return result

    def release_conn(self) -> None:
        pass


class _FailingStreamingResponse:
    status = 200
    headers = {"Content-Type": "text/event-stream"}

    def readline(self) -> bytes:
        raise OSError("record layer failure")

    def read(self, size: int = -1) -> bytes:
        raise OSError("record layer failure")

    def release_conn(self) -> None:
        pass


class _BrokenPipeWfile:
    def write(self, body: bytes) -> None:
        raise BrokenPipeError("test disconnect")

    def flush(self) -> None:
        raise BrokenPipeError("test disconnect")


class _FakeConsole:
    def __init__(self, *, tty: bool) -> None:
        self.tty = tty
        self.writes: list[str] = []

    def isatty(self) -> bool:
        return self.tty

    def write(self, text: str) -> None:
        self.writes.append(text)

    def flush(self) -> None:
        return


def _make_handler_stub(wfile: object, **config: object) -> DeepSeekProxyHandler:
    handler = object.__new__(DeepSeekProxyHandler)
    handler.server = SimpleNamespace(
        config=ProxyConfig(**config),
        reasoning_store=ReasoningStore(":memory:"),
    )
    handler.wfile = wfile
    handler.close_connection = False
    handler.send_response = lambda status: None
    handler.send_header = lambda name, value: None
    handler.end_headers = lambda: None
    return handler


# ---------------------------------------------------------------------------
# CLI / pure helpers
# ---------------------------------------------------------------------------


class CliAndHelperTests(unittest.TestCase):
    def test_cli_boolean_flags_have_on_and_off_forms(self) -> None:
        args = build_arg_parser().parse_args(
            [
                "--no-display-reasoning",
                "--no-collapsible-reasoning",
                "--cors",
                "--tunnel", "cloudflared",
                "--trace-dir",
                "/tmp/dcp-traces",
            ]
        )
        self.assertFalse(args.display_reasoning)
        self.assertFalse(args.collapsible_reasoning)
        self.assertTrue(args.cors)
        self.assertEqual(args.tunnel, "cloudflared")
        self.assertEqual(args.trace_dir, Path("/tmp/dcp-traces"))

    def test_console_logging_hides_info_prefix_and_level(self) -> None:
        formatter = ConsoleLogFormatter()
        info_record = logging.LogRecord(
            "deepseek_bridge",
            logging.INFO,
            __file__,
            1,
            "listening on %s",
            ("http://127.0.0.1:9000/v1",),
            None,
        )
        warning_record = logging.LogRecord(
            "deepseek_bridge",
            logging.WARNING,
            __file__,
            1,
            "trace logging enabled",
            (),
            None,
        )

        self.assertEqual(
            formatter.format(info_record),
            "listening on http://127.0.0.1:9000/v1",
        )
        self.assertEqual(
            formatter.format(warning_record), "WARNING trace logging enabled"
        )

    def test_console_logging_format(self) -> None:
        formatter = ConsoleLogFormatter()
        record = logging.LogRecord(
            "deepseek_bridge",
            logging.INFO,
            __file__,
            1,
            "listening on %s",
            ("http://127.0.0.1:9000/v1",),
            None,
        )

        self.assertEqual(
            formatter.format(record),
            "listening on http://127.0.0.1:9000/v1",
        )

    def test_terminal_spinner_animates_only_for_tty(self) -> None:
        tty = _FakeConsole(tty=True)
        spinner = TerminalSpinner(
            enabled=True, text="└ {frame}", stream=tty, interval=0.001
        ).start()
        deadline = time.monotonic() + 0.2
        while time.monotonic() < deadline and not tty.writes:
            time.sleep(0.001)
        spinner.stop()

        output = "".join(tty.writes)
        self.assertIn(TerminalSpinner.hide_cursor, output)
        self.assertIn("└ ⠋", output)
        self.assertIn(TerminalSpinner.show_cursor, output)
        self.assertTrue(output.endswith(TerminalSpinner.show_cursor))

        non_tty = _FakeConsole(tty=False)
        TerminalSpinner(
            enabled=True, text="└ {frame}", stream=non_tty, interval=0.001
        ).start().stop()
        self.assertEqual(non_tty.writes, [])

    def test_read_response_body_returns_raw_bytes(self) -> None:
        body = b'{"ok":1}'
        self.assertEqual(read_response_body(_FakeResponse(body)), body)

    def test_startup_banner_includes_log_path_when_log_dir_set(self) -> None:
        import logging
        root = logging.getLogger()
        handlers_before = root.handlers[:]
        with TemporaryDirectory() as d:
            result = configure_logging(log_dir=d)
            self.assertIsNotNone(
                result, "configure_logging should return path when log_dir is set"
            )
            self.assertIn(d, result)
            for h in root.handlers[:]:
                if h not in handlers_before:
                    h.close()
                    root.removeHandler(h)

    def test_startup_banner_no_log_path_when_log_dir_not_set(self) -> None:
        result = configure_logging()
        self.assertIsNone(
            result, "configure_logging should return None when no log_dir"
        )

    def test_db_heartbeat_method_exists(self) -> None:
        self.assertTrue(
            hasattr(BoundedThreadPoolHTTPServer, "_log_db_stats"),
            "_log_db_stats method should exist on BoundedThreadPoolHTTPServer",
        )

    def test_version_flag_exists(self) -> None:
        """--version flag is registered."""
        parser = build_arg_parser()
        version_actions = [a for a in parser._actions if hasattr(a, "version")]
        self.assertTrue(len(version_actions) > 0, "No --version flag found")

    def test_headless_flag_defaults_false(self) -> None:
        """--headless flag defaults to False."""
        parser = build_arg_parser()
        args = parser.parse_args([])
        self.assertFalse(args.headless)

    def test_truncate_message_content_basic(self) -> None:
        """Content truncation truncates long strings."""
        payload = {"messages": [{"role": "user", "content": "x" * 500}]}
        result = _truncate_message_content(payload, max_len=50)
        content = result["messages"][0]["content"]
        self.assertEqual(len(content), 53)  # 50 chars + "..."
        self.assertTrue(content.endswith("..."))

    def test_truncate_message_content_short(self) -> None:
        """Content truncation does NOT truncate short strings."""
        payload = {"messages": [{"role": "user", "content": "hello"}]}
        result = _truncate_message_content(payload, max_len=50)
        content = result["messages"][0]["content"]
        self.assertEqual(content, "hello")  # unchanged

    def test_truncate_message_content_multimodal(self) -> None:
        """Content truncation handles multimodal arrays."""
        payload = {
            "messages": [{"role": "user", "content": [{"type": "text", "text": "hi"}]}]
        }
        result = _truncate_message_content(payload, max_len=50)
        content = result["messages"][0]["content"]
        self.assertEqual(content, "[multimodal content array]")

    def test_truncate_message_content_non_dict(self) -> None:
        """Content truncation passes through non-dict payloads."""
        result = _truncate_message_content("hello", max_len=50)
        self.assertEqual(result, "hello")  # unchanged

    def test_ollama_endpoint_routing_in_parser(self) -> None:
        """--ollama flag is accepted by parser."""
        parser = build_arg_parser()
        args = parser.parse_args(["--ollama"])
        self.assertTrue(args.ollama)
        args = parser.parse_args(["--no-ollama"])
        self.assertFalse(args.ollama)


# ---------------------------------------------------------------------------
# Client-disconnect / upstream-failure stubs (no real HTTP needed)
# ---------------------------------------------------------------------------


class HandlerStubTests(unittest.TestCase):
    def test_regular_response_handles_client_disconnect(self) -> None:
        handler = _make_handler_stub(_BrokenPipeWfile())
        body = json.dumps(
            {
                "id": "x",
                "object": "chat.completion",
                "model": "deepseek-v4-pro",
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "stop",
                        "message": {"role": "assistant", "content": "ok"},
                    }
                ],
            }
        ).encode("utf-8")
        try:
            with self.assertLogs("deepseek_bridge", level="WARNING") as captured:
                result = handler._proxy_regular_response(
                    _FakeResponse(body),
                    "deepseek-v4-pro",
                    [{"role": "user", "content": "hi"}],
                    "ns",
                )
        finally:
            handler.server.reasoning_store.close()
        self.assertFalse(result.sent)
        self.assertIn("sending upstream response body", "\n".join(captured.output))

    def test_streaming_response_stops_on_client_disconnect(self) -> None:
        handler = _make_handler_stub(_BrokenPipeWfile(), debug=True)
        chunk = {
            "id": "stream",
            "model": "deepseek-v4-pro",
            "choices": [{"index": 0, "delta": {"role": "assistant", "content": "hi"}}],
        }
        response = _FakeStreamingResponse(
            [
                f"data: {json.dumps(chunk)}\n\n".encode("utf-8"),
                b"data: [DONE]\n\n",
            ]
        )
        try:
            with self.assertLogs("deepseek_bridge", level="INFO") as captured:
                result = handler._proxy_streaming_response(
                    response,
                    "deepseek-v4-pro",
                    [{"role": "user", "content": "hi"}],
                    "ns",
                )
        finally:
            handler.server.reasoning_store.close()
        self.assertFalse(result.sent)
        self.assertEqual(response.readline_calls, 0)
        self.assertIn("client disconnected", "\n".join(captured.output))

    def test_streaming_response_handles_upstream_read_failure(self) -> None:
        handler = _make_handler_stub(BytesIO())
        try:
            with self.assertLogs("deepseek_bridge", level="WARNING") as captured:
                result = handler._proxy_streaming_response(
                    _FailingStreamingResponse(),
                    "deepseek-v4-pro",
                    [{"role": "user", "content": "hi"}],
                    "ns",
                )
        finally:
            handler.server.reasoning_store.close()
        self.assertFalse(result.sent)
        self.assertIn(
            "upstream streaming response read failed", "\n".join(captured.output)
        )

    def test_collapsible_reasoning_no_effect_when_display_disabled(self) -> None:
        wfile = BytesIO()
        handler = _make_handler_stub(
            wfile, display_reasoning=False, collapsible_reasoning=True
        )
        chunk = {
            "id": "stream",
            "model": "deepseek-v4-pro",
            "choices": [{"index": 0, "delta": {"reasoning_content": "Need context."}}],
        }
        response = _FakeStreamingResponse(
            [
                f"data: {json.dumps(chunk)}\n\n".encode("utf-8"),
                b"data: [DONE]\n\n",
            ]
        )
        try:
            handler._proxy_streaming_response(
                response,
                "deepseek-v4-pro",
                [{"role": "user", "content": "hi"}],
                "ns",
            )
        finally:
            handler.server.reasoning_store.close()
        body = wfile.getvalue().decode("utf-8")
        self.assertIn("reasoning_content", body)
        self.assertNotIn("<details>", body)

    def test_check_client_alive_without_request_fallback(self) -> None:
        """Verify _check_client_alive falls back to wfile.write when request is unset."""
        handler = _make_handler_stub(_BrokenPipeWfile())
        # handler.request is not set — should fall back to wfile.write which raises
        # _BrokenPipeWfile
        result = handler._check_client_alive()
        self.assertFalse(result)

    def test_pause_rejection_is_logged(self) -> None:
        """Verify paused server rejection is logged at WARNING level."""
        body = json.dumps({
            "model": "deepseek",
            "messages": [{"role": "user", "content": "hi"}],
        })
        handler = _make_handler_stub(BytesIO())
        handler.server.paused = True
        handler.server.request_count = 0
        handler.command = "POST"
        handler.path = "/v1/chat/completions"
        handler.client_address = ("127.0.0.1", 0)
        handler.headers = {
            "Authorization": "Bearer test-key",
            "Content-Length": str(len(body)),
        }
        handler.rfile = BytesIO(body.encode("utf-8"))

        with self.assertLogs("deepseek_bridge", level="WARNING") as log_ctx:
            handler.do_POST()

        self.assertTrue(
            any("server paused" in msg for msg in log_ctx.output),
            f"Expected 'server paused' in logs, got: {log_ctx.output}",
        )

    def test_cursor_authorization_extracts_bearer_token(self) -> None:
        handler = _make_handler_stub(BytesIO())
        handler.headers = {"Authorization": "Bearer sk-test-key"}
        self.assertEqual(handler._cursor_authorization(), "Bearer sk-test-key")

    def test_cursor_authorization_returns_none_for_missing_header(self) -> None:
        handler = _make_handler_stub(BytesIO())
        handler.headers = {}
        self.assertIsNone(handler._cursor_authorization())

    def test_cursor_authorization_returns_none_for_basic_auth(self) -> None:
        handler = _make_handler_stub(BytesIO())
        handler.headers = {"Authorization": "Basic dXNlcjpwYXNz"}
        self.assertIsNone(handler._cursor_authorization())

    def test_read_json_body_negative_content_length_raises(self) -> None:
        handler = _make_handler_stub(BytesIO())
        handler.headers = {"Content-Length": "-1"}
        with self.assertRaises(ValueError):
            handler._read_json_body()

    def test_read_json_body_empty_body_raises(self) -> None:
        handler = _make_handler_stub(BytesIO())
        handler.headers = {"Content-Length": "0"}
        handler.rfile = BytesIO(b"")
        with self.assertRaises(ValueError):
            handler._read_json_body()

    def test_read_json_body_non_dict_raises(self) -> None:
        handler = _make_handler_stub(BytesIO())
        body = b'"just a string"'
        handler.headers = {"Content-Length": str(len(body))}
        handler.rfile = BytesIO(body)
        with self.assertRaises(ValueError):
            handler._read_json_body()

    def test_read_json_body_invalid_json_raises(self) -> None:
        handler = _make_handler_stub(BytesIO())
        body = b"not json"
        handler.headers = {"Content-Length": str(len(body))}
        handler.rfile = BytesIO(body)
        with self.assertRaises(ValueError):
            handler._read_json_body()

    def test_check_client_alive_via_socket_returns_true(self) -> None:
        handler = _make_handler_stub(BytesIO())
        sock = type("FakeSocket", (), {"sendall": lambda s, d, flags=0: None})()
        if hasattr(type(sock), "setsockopt"):
            pass
        handler.request = sock
        self.assertTrue(handler._check_client_alive())


# ---------------------------------------------------------------------------
# HTTP-level boundary tests: real proxy + tiny upstream
# ---------------------------------------------------------------------------


class _PlainFakeUpstream(BaseHTTPRequestHandler):
    """Returns a fixed plain response and records every request."""

    requests: list[dict[str, object]] = []
    auth_headers: list[str] = []
    delay_after_done: float = 0.0
    response: dict[str, object] = {}

    def log_message(self, fmt: str, *args: object) -> None:
        return

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        payload = json.loads(self.rfile.read(length).decode("utf-8"))
        self.__class__.requests.append(payload)
        self.__class__.auth_headers.append(self.headers.get("Authorization", ""))

        if payload.get("stream"):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            self.wfile.write(
                b'data: {"choices":[{"index":0,"delta":{"content":"x"}}]}\n\n'
            )
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()
            if self.__class__.delay_after_done:
                time.sleep(self.__class__.delay_after_done)
            return

        body = json.dumps(self.__class__.response).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


_BASE_RESPONSE: dict[str, object] = {
    "id": "x",
    "object": "chat.completion",
    "created": 1,
    "model": "deepseek-v4-pro",
    "choices": [
        {
            "index": 0,
            "finish_reason": "stop",
            "message": {"role": "assistant", "content": "ok"},
        }
    ],
    "usage": {
        "prompt_tokens": 20,
        "completion_tokens": 5,
        "total_tokens": 25,
        "prompt_cache_hit_tokens": 12,
        "prompt_cache_miss_tokens": 8,
        "completion_tokens_details": {"reasoning_tokens": 3},
    },
}


class _Fixture:
    def __init__(self, server: ThreadingHTTPServer) -> None:
        self.server = server
        self.thread = threading.Thread(target=server.serve_forever, daemon=True)
        self.thread.start()

    @property
    def url(self) -> str:
        host, port = self.server.server_address
        return f"http://{host}:{port}"

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)


def _post(url: str, payload: dict, api_key: str = "sk-test") -> tuple[int, dict]:
    request = Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urlopen(request, timeout=5) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


class HttpBoundaryTests(unittest.TestCase):
    """Real-HTTP tests that don't fit the protocol suite: things the proxy
    must do at the HTTP boundary regardless of what DeepSeek answers."""

    def setUp(self) -> None:
        _PlainFakeUpstream.requests = []
        _PlainFakeUpstream.auth_headers = []
        _PlainFakeUpstream.delay_after_done = 0.0
        _PlainFakeUpstream.response = dict(_BASE_RESPONSE)
        self.upstream = _Fixture(
            ThreadingHTTPServer(("127.0.0.1", 0), _PlainFakeUpstream)
        )
        self.store = ReasoningStore(":memory:")
        proxy = DeepSeekProxyServer(("127.0.0.1", 0), DeepSeekProxyHandler)
        proxy.config = ProxyConfig(
            upstream_base_url=self.upstream.url,
            upstream_model="deepseek-v4-pro",
            tunnel="none",
        )
        proxy.reasoning_store = self.store
        proxy.upstream_pool = UpstreamPool()
        self.proxy = _Fixture(proxy)

    def tearDown(self) -> None:
        self.proxy.close()
        self.upstream.close()
        self.store.close()

    def _request(self) -> dict:
        return {
            "model": "deepseek-v4-pro",
            "messages": [{"role": "user", "content": "hi"}],
        }

    def test_rejects_missing_bearer_token(self) -> None:
        request = Request(
            f"{self.proxy.url}/v1/chat/completions",
            data=json.dumps(self._request()).encode("utf-8"),
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        with self.assertRaises(HTTPError) as caught:
            urlopen(request, timeout=5)
        self.assertEqual(caught.exception.code, 401)
        self.assertEqual(_PlainFakeUpstream.requests, [])

    def test_rejects_oversized_request_body(self) -> None:
        self.proxy.server.config = replace(
            self.proxy.server.config, max_request_body_bytes=10
        )
        status, payload = _post(
            f"{self.proxy.url}/v1/chat/completions", self._request()
        )
        self.assertEqual(status, 413)
        self.assertIn("too large", payload["error"]["message"])
        self.assertEqual(_PlainFakeUpstream.requests, [])

    def test_forwards_bearer_token_to_upstream(self) -> None:
        status, _ = _post(
            f"{self.proxy.url}/v1/chat/completions",
            self._request(),
            api_key="sk-from-cursor",
        )
        self.assertEqual(status, 200)
        self.assertEqual(_PlainFakeUpstream.auth_headers[0], "Bearer sk-from-cursor")

    def test_streaming_response_closes_after_done_when_upstream_lingers(
        self,
    ) -> None:
        """Cursor relies on the proxy ending the SSE stream at [DONE], even
        if the upstream socket stays open."""
        _PlainFakeUpstream.delay_after_done = 2.0
        request = Request(
            f"{self.proxy.url}/v1/chat/completions",
            data=json.dumps(
                {
                    "model": "deepseek-v4-pro",
                    "stream": True,
                    "messages": [{"role": "user", "content": "stream"}],
                }
            ).encode("utf-8"),
            method="POST",
            headers={
                "Authorization": "Bearer sk-test",
                "Content-Type": "application/json",
            },
        )
        started = time.monotonic()
        with urlopen(request, timeout=1) as response:
            body = response.read().decode("utf-8")
        self.assertLess(time.monotonic() - started, 1.0)
        self.assertIn("data: [DONE]", body)

    def test_normal_logging_summarizes_without_bodies_or_keys(self) -> None:
        with self.assertLogs("deepseek_bridge", level="INFO") as captured:
            status, _ = _post(
                f"{self.proxy.url}/v1/chat/completions",
                self._request(),
                api_key="sk-from-cursor",
            )
            # `└ stats` is emitted on the handler thread *after* the response
            # body hits the socket, so the client may return before it lands.
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline and not any(
                "└ stats" in record for record in captured.output
            ):
                time.sleep(0.01)
        output = "\n".join(captured.output)
        self.assertEqual(status, 200)
        self.assertIn("┌ request model=deepseek-v4-pro effort=max messages=1", output)
        self.assertIn("├ context status=ok reasoning_context=0", output)
        self.assertIn("└ stats", output)
        self.assertNotIn(" tools=", output)
        self.assertNotIn("├ send", output)
        self.assertNotIn("hi", output.split("┌ request")[1].split("\n")[0])
        self.assertNotIn("sk-from-cursor", output)

    def test_debug_logging_includes_bodies_but_redacts_api_key(self) -> None:
        self.proxy.server.config = replace(self.proxy.server.config, debug=True)
        with self.assertLogs("deepseek_bridge", level="INFO") as captured:
            _post(
                f"{self.proxy.url}/v1/chat/completions",
                self._request(),
                api_key="sk-from-cursor",
            )
        output = "\n".join(captured.output)
        self.assertIn("cursor request body", output)
        self.assertIn("upstream request body", output)
        self.assertNotIn("sk-from-cursor", output)

    def test_healthz_returns_ok(self) -> None:
        with urlopen(f"{self.proxy.url}/healthz", timeout=2) as response:
            self.assertEqual(response.status, 200)
            self.assertEqual(json.loads(response.read())["ok"], True)


class BoundedPoolTests(unittest.TestCase):
    """Tests for BoundedThreadPoolHTTPServer queue rejection."""

    def test_reject_connection_sends_503(self) -> None:
        """Verify _reject_connection sends HTTP 503 with JSON body."""
        import socket
        from deepseek_bridge.server_infrastructure import BoundedThreadPoolHTTPServer

        a, b = socket.socketpair()
        try:
            BoundedThreadPoolHTTPServer._reject_connection(a)
            response = b.recv(4096)
            self.assertIn(b"503", response)
            self.assertIn(b"Content-Type: application/json", response)
            self.assertIn(b"service_unavailable", response)
        finally:
            a.close()
            b.close()


if __name__ == "__main__":
    unittest.main()
