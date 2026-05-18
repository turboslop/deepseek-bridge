"""Server boundary, CLI, and operational tests.

Pure helper tests (gzip, summarize) and stub-handler tests (client
disconnect) live near the top. The bottom of the file boots a real proxy +
tiny upstream to exercise things that need the HTTP layer: bearer token
forwarding, oversized body, missing-bearer rejection, logging modes, and
streaming connection close.
"""

from __future__ import annotations

import json
import logging
import threading
import time
import unittest
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO, StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import MagicMock, PropertyMock, patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import urllib3

from deepseek_bridge.config import ProxyConfig
from deepseek_bridge.helpers import _shutdown_requested
from deepseek_bridge.logging import (
    ConsoleLogFormatter,
    JsonLogFormatter,
    TerminalSpinner,
    _truncate_message_content,
    configure_logging,
)
from deepseek_bridge.metrics import METRICS, PROMETHEUS_CONTENT_TYPE
from deepseek_bridge.reasoning_store import ReasoningStore, conversation_scope
from deepseek_bridge.server import (
    BoundedThreadPoolHTTPServer,
    DeepSeekProxyHandler,
    DeepSeekProxyServer,
    UpstreamPool,
    build_arg_parser,
    read_response_body,
)
from deepseek_bridge.transform import normalize_messages
from deepseek_bridge.valkey_store import ValkeyReasoningStore
from tests.test_reasoning_store import _FakeValkeyClient

# ---------------------------------------------------------------------------
# Stubs for fast in-process tests of internal handler methods
# ---------------------------------------------------------------------------


class _FakeResponse:
    def __init__(
        self, body: bytes, encoding: str = "", status: int = 200
    ) -> None:
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
        """Read up to *size* bytes, returning whole buffered lines."""
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
                "--tunnel",
                "cloudflared",
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

    def test_json_logging_format_is_valid_one_line_json(self) -> None:
        formatter = JsonLogFormatter()
        record = logging.LogRecord(
            "deepseek_bridge",
            logging.INFO,
            __file__,
            1,
            "request complete\nmodel=%s",
            ("deepseek-v4-pro",),
            None,
        )
        record.request_id = "req-test-123"
        record.method = "POST"
        record.path = "/v1/chat/completions"
        record.status = 200
        record.duration_ms = 42
        record.model = "deepseek-v4-pro"
        record.upstream_status = 200
        record.cache_hit = "60.0%"
        record.storage_backend = "sqlite"

        formatted = formatter.format(record)
        payload = json.loads(formatted)

        self.assertNotIn("\n", formatted)
        self.assertEqual(payload["level"], "INFO")
        self.assertEqual(payload["logger"], "deepseek_bridge")
        self.assertEqual(
            payload["message"], "request complete\nmodel=deepseek-v4-pro"
        )
        self.assertEqual(payload["request_id"], "req-test-123")
        self.assertEqual(payload["method"], "POST")
        self.assertEqual(payload["path"], "/v1/chat/completions")
        self.assertEqual(payload["status"], 200)
        self.assertEqual(payload["duration_ms"], 42)
        self.assertEqual(payload["model"], "deepseek-v4-pro")
        self.assertEqual(payload["upstream_status"], 200)
        self.assertEqual(payload["cache_hit"], "60.0%")
        self.assertEqual(payload["storage_backend"], "sqlite")
        self.assertIn("timestamp", payload)

    def test_json_logging_omits_unapproved_extra_fields(self) -> None:
        formatter = JsonLogFormatter()
        record = logging.LogRecord(
            "deepseek_bridge",
            logging.INFO,
            __file__,
            1,
            "request summary",
            (),
            None,
        )
        record.authorization = "Bearer sk-secret"
        record.api_key = "sk-secret"
        record.payload = {"messages": [{"content": "secret prompt"}]}
        record.body = "secret response"
        record.trace_payload = {"request": "secret trace"}
        record.messages = ["secret prompt"]

        formatted = formatter.format(record)
        payload = json.loads(formatted)

        self.assertEqual(payload["message"], "request summary")
        self.assertNotIn("authorization", payload)
        self.assertNotIn("api_key", payload)
        self.assertNotIn("payload", payload)
        self.assertNotIn("body", payload)
        self.assertNotIn("trace_payload", payload)
        self.assertNotIn("messages", payload)
        self.assertNotIn("sk-secret", formatted)
        self.assertNotIn("secret prompt", formatted)
        self.assertNotIn("secret response", formatted)
        self.assertNotIn("secret trace", formatted)

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
                result,
                "configure_logging should return path when log_dir is set",
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

    def test_configure_logging_can_install_json_formatter(self) -> None:
        root = logging.getLogger()
        handlers_before = root.handlers[:]
        result = configure_logging(log_format="json")
        try:
            self.assertIsNone(result)
            self.assertIsInstance(root.handlers[0].formatter, JsonLogFormatter)
        finally:
            for handler in root.handlers[:]:
                handler.close()
                root.removeHandler(handler)
            for handler in handlers_before:
                root.addHandler(handler)

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
            "messages": [
                {"role": "user", "content": [{"type": "text", "text": "hi"}]}
            ]
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
            with self.assertLogs(
                "deepseek_bridge", level="WARNING"
            ) as captured:
                result = handler._proxy_regular_response(
                    _FakeResponse(body),
                    "deepseek-v4-pro",
                    [{"role": "user", "content": "hi"}],
                    "ns",
                )
        finally:
            handler.server.reasoning_store.close()
        self.assertFalse(result.sent)
        self.assertIn(
            "sending upstream response body", "\n".join(captured.output)
        )

    def test_streaming_response_stops_on_client_disconnect(self) -> None:
        handler = _make_handler_stub(_BrokenPipeWfile(), debug=True)
        chunk = {
            "id": "stream",
            "model": "deepseek-v4-pro",
            "choices": [
                {"index": 0, "delta": {"role": "assistant", "content": "hi"}}
            ],
        }
        response = _FakeStreamingResponse(
            [
                f"data: {json.dumps(chunk)}\n\n".encode(),
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
            with self.assertLogs(
                "deepseek_bridge", level="WARNING"
            ) as captured:
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
            "upstream streaming response read failed",
            "\n".join(captured.output),
        )

    def test_collapsible_reasoning_no_effect_when_display_disabled(
        self,
    ) -> None:
        wfile = BytesIO()
        handler = _make_handler_stub(
            wfile, display_reasoning=False, collapsible_reasoning=True
        )
        chunk = {
            "id": "stream",
            "model": "deepseek-v4-pro",
            "choices": [
                {"index": 0, "delta": {"reasoning_content": "Need context."}}
            ],
        }
        response = _FakeStreamingResponse(
            [
                f"data: {json.dumps(chunk)}\n\n".encode(),
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
        """Verify _check_client_alive fallback without request."""
        handler = _make_handler_stub(_BrokenPipeWfile())
        # handler.request is not set, so wfile.write raises BrokenPipeError.
        result = handler._check_client_alive()
        self.assertFalse(result)

    def test_pause_rejection_is_logged(self) -> None:
        """Verify paused server rejection is logged at WARNING level."""
        body = json.dumps(
            {
                "model": "deepseek",
                "messages": [{"role": "user", "content": "hi"}],
            }
        )
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
    delay_before_response: float = 0.0
    delay_before_stream_response: float = 0.0
    request_started = threading.Event()
    response_status: int = 200
    response: dict[str, object] = {}

    def log_message(self, fmt: str, *args: object) -> None:
        return

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        payload = json.loads(self.rfile.read(length).decode("utf-8"))
        self.__class__.requests.append(payload)
        self.__class__.auth_headers.append(
            self.headers.get("Authorization", "")
        )
        self.__class__.request_started.set()

        if payload.get("stream"):
            if self.__class__.delay_before_stream_response:
                time.sleep(self.__class__.delay_before_stream_response)
            self.send_response(self.__class__.response_status)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            if self.__class__.response_status >= 400:
                self.wfile.write(
                    json.dumps(self.__class__.response).encode("utf-8")
                )
                return
            self.wfile.write(
                b'data: {"choices":[{"index":0,"delta":{"content":"x"}}]}\n\n'
            )
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()
            if self.__class__.delay_after_done:
                time.sleep(self.__class__.delay_after_done)
            return

        if self.__class__.delay_before_response:
            time.sleep(self.__class__.delay_before_response)
        body = json.dumps(self.__class__.response).encode("utf-8")
        self.send_response(self.__class__.response_status)
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


def _post(
    url: str, payload: dict, api_key: str = "sk-test"
) -> tuple[int, dict]:
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
        try:
            return exc.code, json.loads(exc.read().decode("utf-8"))
        finally:
            exc.close()


def _post_raw_json(
    url: str, body: bytes, api_key: str = "sk-test"
) -> tuple[int, dict]:
    request = Request(
        url,
        data=body,
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
        try:
            return exc.code, json.loads(exc.read().decode("utf-8"))
        finally:
            exc.close()


def _get_text(url: str) -> tuple[int, str, str]:
    try:
        with urlopen(url, timeout=5) as response:
            return (
                response.status,
                response.headers.get("Content-Type", ""),
                response.read().decode("utf-8"),
            )
    except HTTPError as exc:
        try:
            return (
                exc.code,
                exc.headers.get("Content-Type", ""),
                exc.read().decode("utf-8"),
            )
        finally:
            exc.close()


def _assert_prometheus_text(testcase: unittest.TestCase, body: str) -> None:
    testcase.assertIn("# HELP ", body)
    testcase.assertIn("# TYPE ", body)
    sample_pattern = (
        r"^[a-zA-Z_:][a-zA-Z0-9_:]*"
        r"(\{[^{}]*\})? "
        r"[-+]?(?:\d+(?:\.\d+)?|\.\d+)(?:[eE][-+]?\d+)?$"
    )
    for line in body.splitlines():
        if not line or line.startswith("#"):
            continue
        testcase.assertRegex(line, sample_pattern)


class HttpBoundaryTests(unittest.TestCase):
    """Real-HTTP tests that don't fit the protocol suite: things the proxy
    must do at the HTTP boundary regardless of what DeepSeek answers."""

    def setUp(self) -> None:
        _PlainFakeUpstream.requests = []
        _PlainFakeUpstream.auth_headers = []
        _PlainFakeUpstream.delay_after_done = 0.0
        _PlainFakeUpstream.delay_before_response = 0.0
        _PlainFakeUpstream.delay_before_stream_response = 0.0
        _PlainFakeUpstream.request_started = threading.Event()
        _PlainFakeUpstream.response_status = 200
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
        METRICS.reset()

    def tearDown(self) -> None:
        METRICS.reset()
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
        try:
            self.assertEqual(caught.exception.code, 401)
        finally:
            caught.exception.close()
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

    def test_embeddings_reject_oversized_request_body(self) -> None:
        self.proxy.server.config = replace(
            self.proxy.server.config, max_request_body_bytes=10
        )

        for path in ("/embeddings", "/v1/embeddings"):
            with self.subTest(path=path):
                status, payload = _post(
                    f"{self.proxy.url}{path}",
                    {"model": "deepseek-v4-pro", "input": "hello"},
                )
                self.assertEqual(status, 413)
                self.assertIn("too large", payload["error"]["message"])
                self.assertEqual(payload["error"]["code"], "request_too_large")

        self.assertEqual(_PlainFakeUpstream.requests, [])

    def test_embeddings_invalid_json_returns_bad_request(self) -> None:
        for path in ("/embeddings", "/v1/embeddings"):
            with self.subTest(path=path):
                status, payload = _post_raw_json(
                    f"{self.proxy.url}{path}", b"not json"
                )
                self.assertEqual(status, 400)
                self.assertEqual(
                    payload["error"]["code"], "invalid_request_error"
                )

        self.assertEqual(_PlainFakeUpstream.requests, [])

    def test_forwards_bearer_token_to_upstream(self) -> None:
        status, _ = _post(
            f"{self.proxy.url}/v1/chat/completions",
            self._request(),
            api_key="sk-from-cursor",
        )
        self.assertEqual(status, 200)
        self.assertEqual(
            _PlainFakeUpstream.auth_headers[0], "Bearer sk-from-cursor"
        )

    def test_json_stats_log_uses_success_status_from_upstream(self) -> None:
        _PlainFakeUpstream.response_status = 201
        stream = StringIO()
        handler = logging.StreamHandler(stream)
        handler.setFormatter(JsonLogFormatter())
        logger = logging.getLogger("deepseek_bridge")
        old_level = logger.level
        logger.setLevel(logging.INFO)
        logger.addHandler(handler)
        try:
            status, _ = _post(
                f"{self.proxy.url}/v1/chat/completions",
                self._request(),
            )
            deadline = time.monotonic() + 2
            while (
                time.monotonic() < deadline
                and "└ stats" not in stream.getvalue()
            ):
                time.sleep(0.01)
        finally:
            logger.removeHandler(handler)
            logger.setLevel(old_level)

        records = [
            json.loads(line) for line in stream.getvalue().splitlines() if line
        ]
        stats_records = [
            record
            for record in records
            if str(record.get("message", "")).startswith("└ stats")
        ]

        self.assertEqual(status, 201)
        self.assertTrue(stats_records)
        self.assertEqual(stats_records[-1]["status"], 201)
        self.assertEqual(stats_records[-1]["upstream_status"], 201)

    def test_embeddings_forward_upstream_http_error(self) -> None:
        _PlainFakeUpstream.response_status = 401
        _PlainFakeUpstream.response = {
            "error": {
                "message": "bad key",
                "type": "authentication_error",
                "code": "invalid_api_key",
            }
        }

        status, payload = _post(
            f"{self.proxy.url}/v1/embeddings",
            {"model": "deepseek-v4-pro", "input": "hello"},
        )

        self.assertEqual(status, 401)
        self.assertEqual(payload["error"]["code"], "invalid_api_key")
        self.assertNotEqual(payload.get("object"), "list")

    def test_embeddings_transport_failure_returns_error(self) -> None:
        class _FailingUpstreamPool:
            def request(self, *_args: object, **_kwargs: object) -> object:
                raise OSError("connection refused")

        self.proxy.server.config = replace(
            self.proxy.server.config, metrics_enabled=True
        )
        self.proxy.server.upstream_pool = _FailingUpstreamPool()

        status, payload = _post(
            f"{self.proxy.url}/v1/embeddings",
            {"model": "deepseek-v4-pro", "input": "hello"},
        )
        _, _, body = _get_text(f"{self.proxy.url}/metrics")

        self.assertEqual(status, 500)
        self.assertEqual(payload["error"]["code"], "upstream_failure")
        self.assertNotEqual(payload.get("object"), "list")
        self.assertIn(
            'deepseek_bridge_upstream_requests_total{model="deepseek-v4-pro",'
            'status="500"} 1',
            body,
        )

    def test_embeddings_timeout_returns_gateway_timeout_error(self) -> None:
        class _TimeoutUpstreamPool:
            def request(self, *_args: object, **_kwargs: object) -> object:
                raise urllib3.exceptions.TimeoutError("read timed out")

        self.proxy.server.config = replace(
            self.proxy.server.config, metrics_enabled=True
        )
        self.proxy.server.upstream_pool = _TimeoutUpstreamPool()

        status, payload = _post(
            f"{self.proxy.url}/v1/embeddings",
            {"model": "deepseek-v4-pro", "input": "hello"},
        )
        _, _, body = _get_text(f"{self.proxy.url}/metrics")

        self.assertEqual(status, 504)
        self.assertEqual(payload["error"]["code"], "upstream_timeout")
        self.assertNotEqual(payload.get("object"), "list")
        self.assertIn(
            'deepseek_bridge_upstream_requests_total{model="deepseek-v4-pro",'
            'status="504"} 1',
            body,
        )

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

    def test_streaming_upstream_error_logs_without_body_or_key(self) -> None:
        _PlainFakeUpstream.response_status = 502
        _PlainFakeUpstream.response = {
            "error": {"message": "stream-upstream-secret"}
        }
        request = Request(
            f"{self.proxy.url}/v1/chat/completions",
            data=json.dumps(
                {
                    "model": "deepseek-v4-pro",
                    "stream": True,
                    "messages": [
                        {"role": "user", "content": "stream-user-secret"}
                    ],
                }
            ).encode("utf-8"),
            method="POST",
            headers={
                "Authorization": "Bearer sk-stream-secret",
                "Content-Type": "application/json",
            },
        )

        with (
            self.assertLogs("deepseek_bridge", level="INFO") as captured,
            urlopen(request, timeout=2) as response,
        ):
            body = response.read().decode("utf-8")

        output = "\n".join(captured.output)
        self.assertEqual(response.status, 200)
        self.assertIn("Upstream returned 502", body)
        self.assertIn("request failed upstream_status=502", output)
        self.assertNotIn("stream-upstream-secret", output)
        self.assertNotIn("stream-user-secret", output)
        self.assertNotIn("sk-stream-secret", output)

    def test_kubernetes_streaming_runtime_disables_spinner(self) -> None:
        self.proxy.server.config = replace(
            self.proxy.server.config, runtime_mode="kubernetes"
        )
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
        spinner = MagicMock()
        spinner.start.return_value = spinner
        with (
            patch(
                "deepseek_bridge.handler._routes.TerminalSpinner",
                return_value=spinner,
            ) as spinner_cls,
            urlopen(request, timeout=2) as response,
        ):
            body = response.read().decode("utf-8")

        self.assertIn("data: [DONE]", body)
        spinner_cls.assert_called_once()
        self.assertFalse(spinner_cls.call_args.kwargs["enabled"])

    def test_json_logging_disables_streaming_spinner(self) -> None:
        self.proxy.server.config = replace(
            self.proxy.server.config, log_format="json"
        )
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
        spinner = MagicMock()
        spinner.start.return_value = spinner
        with (
            patch(
                "deepseek_bridge.handler._routes.TerminalSpinner",
                return_value=spinner,
            ) as spinner_cls,
            urlopen(request, timeout=2) as response,
        ):
            body = response.read().decode("utf-8")

        self.assertIn("data: [DONE]", body)
        spinner_cls.assert_called_once()
        self.assertFalse(spinner_cls.call_args.kwargs["enabled"])

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
        self.assertIn(
            "┌ request model=deepseek-v4-pro effort=max messages=1", output
        )
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

    def test_normal_json_logging_summarizes_without_bodies_or_keys(
        self,
    ) -> None:
        stream = StringIO()
        handler = logging.StreamHandler(stream)
        handler.setFormatter(JsonLogFormatter())
        logger = logging.getLogger("deepseek_bridge")
        old_level = logger.level
        logger.setLevel(logging.INFO)
        logger.addHandler(handler)
        request_payload = {
            "model": "deepseek-v4-pro",
            "messages": [
                {"role": "user", "content": "super-secret-user-prompt"}
            ],
        }
        _PlainFakeUpstream.response = {
            **_BASE_RESPONSE,
            "choices": [
                {
                    "index": 0,
                    "finish_reason": "stop",
                    "message": {
                        "role": "assistant",
                        "content": "assistant-secret-output",
                    },
                }
            ],
        }
        try:
            status, _ = _post(
                f"{self.proxy.url}/v1/chat/completions",
                request_payload,
                api_key="sk-json-secret",
            )
            deadline = time.monotonic() + 2
            while (
                time.monotonic() < deadline
                and "└ stats" not in stream.getvalue()
            ):
                time.sleep(0.01)
        finally:
            logger.removeHandler(handler)
            logger.setLevel(old_level)

        lines = [line for line in stream.getvalue().splitlines() if line]
        records = [json.loads(line) for line in lines]
        stats_records = [
            record
            for record in records
            if str(record.get("message", "")).startswith("└ stats")
        ]
        serialized = "\n".join(lines)

        self.assertEqual(status, 200)
        self.assertTrue(stats_records)
        self.assertTrue(stats_records[-1]["request_id"].startswith("dcp-"))
        self.assertEqual(stats_records[-1]["method"], "POST")
        self.assertEqual(stats_records[-1]["path"], "/v1/chat/completions")
        self.assertEqual(stats_records[-1]["status"], 200)
        self.assertEqual(stats_records[-1]["model"], "deepseek-v4-pro")
        self.assertEqual(stats_records[-1]["upstream_status"], 200)
        self.assertEqual(stats_records[-1]["storage_backend"], "sqlite")
        self.assertNotIn("sk-json-secret", serialized)
        self.assertNotIn("super-secret-user-prompt", serialized)
        self.assertNotIn("assistant-secret-output", serialized)

    def test_healthz_returns_ok(self) -> None:
        with urlopen(f"{self.proxy.url}/healthz", timeout=2) as response:
            self.assertEqual(response.status, 200)
            self.assertEqual(json.loads(response.read())["ok"], True)

    def test_readyz_returns_ok(self) -> None:
        with urlopen(f"{self.proxy.url}/readyz", timeout=2) as response:
            self.assertEqual(response.status, 200)
            body = json.loads(response.read())
        self.assertTrue(body["ok"])
        self.assertEqual(body["checks"]["storage"]["status"], "ok")

        with urlopen(f"{self.proxy.url}/v1/readyz", timeout=2) as response:
            self.assertEqual(response.status, 200)
            body = json.loads(response.read())
        self.assertTrue(body["ok"])
        self.assertEqual(body["checks"]["storage"]["status"], "ok")

    def test_readyz_returns_503_when_storage_unhealthy(self) -> None:
        class _UnhealthyStore:
            def health_check(self) -> tuple[bool, str]:
                return False, "unavailable"

        self.proxy.server.reasoning_store = _UnhealthyStore()

        with self.assertRaises(HTTPError) as caught:
            urlopen(f"{self.proxy.url}/readyz", timeout=2)
        try:
            self.assertEqual(caught.exception.code, 503)
            body = json.loads(caught.exception.read())
        finally:
            caught.exception.close()
        self.assertFalse(body["ok"])
        self.assertEqual(body["checks"]["storage"]["status"], "unavailable")

        with urlopen(f"{self.proxy.url}/healthz", timeout=2) as response:
            self.assertEqual(response.status, 200)
            self.assertTrue(json.loads(response.read())["ok"])

    def test_readyz_uses_valkey_healthcheck_without_leaking_url(self) -> None:
        client = _FakeValkeyClient()
        client.fail_ops.add("ping")
        self.proxy.server.reasoning_store = ValkeyReasoningStore(
            "valkey://:secret@example.invalid/0",
            key_prefix="tests",
            max_age_seconds=30,
            client=client,
        )

        with (
            self.assertLogs("deepseek_bridge", level="WARNING") as captured,
            self.assertRaises(HTTPError) as caught,
        ):
            urlopen(f"{self.proxy.url}/readyz", timeout=2)
        try:
            self.assertEqual(caught.exception.code, 503)
            body = json.loads(caught.exception.read())
        finally:
            caught.exception.close()
        output = "\n".join(captured.output)
        body_text = json.dumps(body)
        self.assertFalse(body["ok"])
        self.assertEqual(body["checks"]["storage"]["status"], "unavailable")
        self.assertNotIn("secret", output + body_text)
        self.assertNotIn("example.invalid", output + body_text)
        self.assertNotIn("valkey://", output + body_text)

        with urlopen(f"{self.proxy.url}/healthz", timeout=2) as response:
            self.assertEqual(response.status, 200)
            self.assertTrue(json.loads(response.read())["ok"])

    def test_readyz_returns_503_when_shutdown_requested(self) -> None:
        try:
            _shutdown_requested.set()

            with self.assertRaises(HTTPError) as caught:
                urlopen(f"{self.proxy.url}/v1/readyz", timeout=2)
            try:
                self.assertEqual(caught.exception.code, 503)
                body = json.loads(caught.exception.read())
            finally:
                caught.exception.close()
        finally:
            _shutdown_requested.clear()

        self.assertFalse(body["ok"])
        self.assertEqual(body["checks"]["shutdown"]["status"], "draining")

        with urlopen(f"{self.proxy.url}/healthz", timeout=2) as response:
            self.assertEqual(response.status, 200)
            self.assertTrue(json.loads(response.read())["ok"])

    def test_new_post_is_rejected_while_shutting_down(self) -> None:
        try:
            _shutdown_requested.set()

            status, payload = _post(
                f"{self.proxy.url}/v1/chat/completions", self._request()
            )
        finally:
            _shutdown_requested.clear()

        self.assertEqual(status, 503)
        self.assertEqual(payload["error"]["code"], "server_shutting_down")
        self.assertEqual(_PlainFakeUpstream.requests, [])

    def test_shutdown_readiness_fails_but_inflight_request_drains(
        self,
    ) -> None:
        _PlainFakeUpstream.delay_before_response = 0.5
        result: dict[str, tuple[int, dict]] = {}

        def send_inflight_request() -> None:
            result["inflight"] = _post(
                f"{self.proxy.url}/v1/chat/completions",
                self._request(),
            )

        worker = threading.Thread(target=send_inflight_request)
        worker.start()
        self.assertTrue(_PlainFakeUpstream.request_started.wait(timeout=2))

        try:
            _shutdown_requested.set()

            with self.assertRaises(HTTPError) as caught:
                urlopen(f"{self.proxy.url}/readyz", timeout=2)
            try:
                self.assertEqual(caught.exception.code, 503)
                ready_body = json.loads(caught.exception.read())
            finally:
                caught.exception.close()

            with urlopen(f"{self.proxy.url}/healthz", timeout=2) as response:
                self.assertEqual(response.status, 200)
                self.assertTrue(json.loads(response.read())["ok"])

            status, payload = _post(
                f"{self.proxy.url}/v1/chat/completions", self._request()
            )
        finally:
            _shutdown_requested.clear()
            worker.join(timeout=3)

        self.assertFalse(ready_body["ok"])
        self.assertEqual(ready_body["checks"]["shutdown"]["status"], "draining")
        self.assertEqual(status, 503)
        self.assertEqual(payload["error"]["code"], "server_shutting_down")
        self.assertIn("inflight", result)
        self.assertEqual(result["inflight"][0], 200)
        self.assertEqual(len(_PlainFakeUpstream.requests), 1)

    def test_metrics_endpoint_returns_404_when_disabled(self) -> None:
        status, content_type, body = _get_text(f"{self.proxy.url}/metrics")

        self.assertEqual(status, 404)
        self.assertIn("application/json", content_type)
        self.assertEqual(
            json.loads(body)["error"]["code"], "endpoint_not_found"
        )

    def test_metrics_endpoint_reports_http_and_upstream_success(self) -> None:
        self.proxy.server.config = replace(
            self.proxy.server.config, metrics_enabled=True
        )

        status, _ = _post(
            f"{self.proxy.url}/v1/chat/completions", self._request()
        )
        self.assertEqual(status, 200)
        scrape_status, content_type, body = _get_text(
            f"{self.proxy.url}/metrics"
        )

        self.assertEqual(scrape_status, 200)
        self.assertIn(PROMETHEUS_CONTENT_TYPE, content_type)
        _assert_prometheus_text(self, body)
        self.assertIn(
            'deepseek_bridge_http_requests_total{method="POST",'
            'path="/v1/chat/completions",status="200"} 1',
            body,
        )
        self.assertIn(
            'deepseek_bridge_upstream_requests_total{model="deepseek-v4-pro",'
            'status="200"} 1',
            body,
        )
        self.assertIn(
            "deepseek_bridge_http_request_duration_seconds_count",
            body,
        )
        self.assertIn(
            "deepseek_bridge_http_request_duration_seconds_bucket",
            body,
        )
        self.assertIn(
            "deepseek_bridge_upstream_request_duration_seconds_bucket",
            body,
        )
        self.assertNotIn("sk-test", body)

    def test_metrics_normalizes_unknown_paths_and_models(self) -> None:
        self.proxy.server.config = replace(
            self.proxy.server.config, metrics_enabled=True
        )

        status, _, _ = _get_text(
            f"{self.proxy.url}/tenant/request-123?request_id=abc"
        )
        METRICS.record_upstream_request(
            model="deepseek-request-123",
            status=200,
            duration_seconds=0.01,
        )
        METRICS.record_upstream_request(
            model="gpt-request-123",
            status=200,
            duration_seconds=0.01,
        )
        _, _, body = _get_text(f"{self.proxy.url}/metrics")

        self.assertEqual(status, 404)
        self.assertIn(
            'deepseek_bridge_http_requests_total{method="GET",'
            'path="unknown",status="404"} 1',
            body,
        )
        self.assertIn(
            "deepseek_bridge_upstream_requests_total"
            '{model="deepseek-other",status="200"} 1',
            body,
        )
        self.assertIn(
            "deepseek_bridge_upstream_requests_total"
            '{model="custom",status="200"} 1',
            body,
        )
        self.assertNotIn("request-123", body)
        self.assertNotIn("request_id", body)

    def test_metrics_endpoint_reports_http_error_without_upstream(self) -> None:
        self.proxy.server.config = replace(
            self.proxy.server.config, metrics_enabled=True
        )
        request = Request(
            f"{self.proxy.url}/v1/chat/completions",
            data=json.dumps(self._request()).encode("utf-8"),
            method="POST",
            headers={"Content-Type": "application/json"},
        )

        with self.assertRaises(HTTPError) as caught:
            urlopen(request, timeout=5)
        caught.exception.close()
        _, _, body = _get_text(f"{self.proxy.url}/metrics")

        self.assertIn(
            'deepseek_bridge_http_requests_total{method="POST",'
            'path="/v1/chat/completions",status="401"} 1',
            body,
        )
        self.assertNotIn("deepseek_bridge_upstream_requests_total{", body)

    def test_metrics_endpoint_reports_cache_hit_and_miss(self) -> None:
        self.proxy.server.config = replace(
            self.proxy.server.config, metrics_enabled=True
        )
        cache_namespace = "metrics-test"
        user_message = {"role": "user", "content": "find files"}
        tool_call = {
            "id": "call_find",
            "type": "function",
            "function": {"name": "find", "arguments": "{}"},
        }
        assistant_with_reasoning = {
            "role": "assistant",
            "content": "",
            "reasoning_content": "",
            "tool_calls": [tool_call],
        }
        self.store.store_assistant_message(
            assistant_with_reasoning,
            conversation_scope([user_message], cache_namespace),
            cache_namespace,
            [user_message],
        )

        hit_messages, patched_count, missing_indexes, _ = normalize_messages(
            [
                user_message,
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [tool_call],
                },
            ],
            self.store,
            cache_namespace,
            repair_reasoning=True,
            keep_reasoning=True,
        )
        miss_messages, _, miss_indexes, _ = normalize_messages(
            [
                user_message,
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "id": "call_missing",
                            "type": "function",
                            "function": {
                                "name": "missing",
                                "arguments": "{}",
                            },
                        }
                    ],
                },
            ],
            self.store,
            cache_namespace,
            repair_reasoning=True,
            keep_reasoning=True,
        )
        _, _, body = _get_text(f"{self.proxy.url}/metrics")

        self.assertEqual(patched_count, 1)
        self.assertEqual(missing_indexes, [])
        self.assertEqual(hit_messages[1].get("reasoning_content"), "")
        self.assertEqual(miss_indexes, [1])
        self.assertNotIn("reasoning_content", miss_messages[1])
        self.assertIn(
            'deepseek_bridge_cache_hits_total{backend="sqlite"} 1',
            body,
        )
        self.assertIn(
            'deepseek_bridge_cache_misses_total{backend="sqlite"} 1',
            body,
        )
        self.assertIn(
            'deepseek_bridge_cache_hit_ratio{backend="sqlite"} 0.5',
            body,
        )
        self.assertIn(
            "deepseek_bridge_storage_operation_duration_seconds_count"
            '{backend="sqlite",operation="get"}',
            body,
        )

    def test_metrics_endpoint_reports_streaming_gauge_after_close(self) -> None:
        self.proxy.server.config = replace(
            self.proxy.server.config, metrics_enabled=True
        )
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

        with urlopen(request, timeout=2) as response:
            self.assertEqual(response.status, 200)
            self.assertIn("data: [DONE]", response.read().decode("utf-8"))
        _, _, body = _get_text(f"{self.proxy.url}/metrics")
        self.assertIn("deepseek_bridge_streams_active 0", body)

    def test_metrics_endpoint_reports_streaming_gauge_while_open(self) -> None:
        self.proxy.server.config = replace(
            self.proxy.server.config, metrics_enabled=True
        )
        _PlainFakeUpstream.delay_before_stream_response = 0.5
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
        result: dict[str, str] = {}

        def send_stream_request() -> None:
            with urlopen(request, timeout=3) as response:
                result["body"] = response.read().decode("utf-8")

        worker = threading.Thread(target=send_stream_request)
        worker.start()
        self.assertTrue(_PlainFakeUpstream.request_started.wait(timeout=2))

        deadline = time.monotonic() + 2
        active_body = ""
        while time.monotonic() < deadline:
            _, _, active_body = _get_text(f"{self.proxy.url}/metrics")
            if "deepseek_bridge_streams_active 1" in active_body:
                break
            time.sleep(0.01)
        worker.join(timeout=3)
        _, _, closed_body = _get_text(f"{self.proxy.url}/metrics")

        self.assertIn("deepseek_bridge_streams_active 1", active_body)
        self.assertIn("data: [DONE]", result["body"])
        self.assertIn("deepseek_bridge_streams_active 0", closed_body)

    def test_metrics_endpoint_reports_storage_errors(self) -> None:
        class _BrokenConnection:
            def execute(self, *_args: object, **_kwargs: object) -> object:
                raise RuntimeError("database is unavailable")

            def close(self) -> None:
                return

        self.proxy.server.config = replace(
            self.proxy.server.config, metrics_enabled=True
        )
        self.store._conn = _BrokenConnection()

        self.assertIsNone(self.store.get("boom"))
        _, _, body = _get_text(f"{self.proxy.url}/metrics")

        self.assertIn(
            'deepseek_bridge_storage_errors_total{backend="sqlite",'
            'operation="get"} 1',
            body,
        )


class BoundedPoolTests(unittest.TestCase):
    """Tests for BoundedThreadPoolHTTPServer queue rejection."""

    def _saturated_proxy(self) -> tuple[_Fixture, ReasoningStore]:
        store = ReasoningStore(":memory:")
        proxy = BoundedThreadPoolHTTPServer(
            ("127.0.0.1", 0),
            DeepSeekProxyHandler,
            max_workers=1,
        )
        proxy.config = ProxyConfig(
            upstream_base_url="http://127.0.0.1:1",
            upstream_model="deepseek-v4-pro",
            tunnel="none",
            max_queue_size=1,
        )
        proxy.reasoning_store = store
        proxy.upstream_pool = UpstreamPool()
        return _Fixture(proxy), store

    def test_reject_connection_sends_503(self) -> None:
        """Verify _reject_connection sends HTTP 503 with JSON body."""
        import socket

        from deepseek_bridge.server_infrastructure import (
            BoundedThreadPoolHTTPServer,
        )

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

    def test_healthz_returns_ok_when_queue_is_saturated(self) -> None:
        proxy, store = self._saturated_proxy()
        try:
            with (
                patch.object(
                    BoundedThreadPoolHTTPServer,
                    "queue_size",
                    new_callable=PropertyMock,
                    return_value=1,
                ),
                urlopen(f"{proxy.url}/healthz", timeout=2) as response,
            ):
                self.assertEqual(response.status, 200)
                body = json.loads(response.read().decode("utf-8"))
            self.assertTrue(body["ok"])
            self.assertEqual(body["server"], "deepseek-bridge")
        finally:
            proxy.close()
            store.close()

    def test_readyz_returns_503_when_queue_is_saturated(self) -> None:
        proxy, store = self._saturated_proxy()
        try:
            with (
                patch.object(
                    BoundedThreadPoolHTTPServer,
                    "queue_size",
                    new_callable=PropertyMock,
                    return_value=1,
                ),
                self.assertRaises(HTTPError) as caught,
            ):
                urlopen(f"{proxy.url}/readyz", timeout=2)
            try:
                self.assertEqual(caught.exception.code, 503)
                body = json.loads(caught.exception.read().decode("utf-8"))
            finally:
                caught.exception.close()
            self.assertEqual(body["error"]["code"], "service_unavailable")
        finally:
            proxy.close()
            store.close()

    def test_application_request_rejected_when_queue_is_saturated(
        self,
    ) -> None:
        proxy, store = self._saturated_proxy()
        try:
            with patch.object(
                BoundedThreadPoolHTTPServer,
                "queue_size",
                new_callable=PropertyMock,
                return_value=1,
            ):
                status, _, body = _get_text(f"{proxy.url}/v1/models")
            payload = json.loads(body)
            self.assertEqual(status, 503)
            self.assertEqual(payload["error"]["code"], "service_unavailable")
        finally:
            proxy.close()
            store.close()


if __name__ == "__main__":
    unittest.main()
