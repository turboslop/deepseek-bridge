from __future__ import annotations

import copy
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import valkey

MODEL = "deepseek-v4-pro"
THINKING_1_1 = "Thinking 1.1 - need to look up the date."
THINKING_STREAM = "Streaming thinking - need to call the tool."
ANSWER_1 = "Answer: 2026-04-24 is the date."
CALL_ID_1 = "call_get_date"
CALL_ID_STREAM = "call_stream_date"

TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "get_date",
            "parameters": {"type": "object", "properties": {}},
        },
    }
]

FUNCTIONAL_ENABLED = os.environ.get("RUN_FUNCTIONAL_TESTS") == "1"
FUNCTIONAL_VALKEY_URL = os.environ.get("FUNCTIONAL_VALKEY_URL", "")


def require_functional_valkey() -> str:
    if not FUNCTIONAL_ENABLED:
        raise unittest.SkipTest("set RUN_FUNCTIONAL_TESTS=1 to run")
    if not FUNCTIONAL_VALKEY_URL:
        raise unittest.SkipTest("set FUNCTIONAL_VALKEY_URL to run")
    wait_for_valkey(FUNCTIONAL_VALKEY_URL)
    return FUNCTIONAL_VALKEY_URL


def wait_for_valkey(url: str, timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        client = valkey.Valkey.from_url(
            url,
            decode_responses=True,
            socket_connect_timeout=1.0,
            socket_timeout=1.0,
        )
        try:
            client.ping()
            return
        except Exception as exc:  # pragma: no cover - failure diagnostics
            last_error = exc
            time.sleep(0.1)
        finally:
            client.close()
    raise AssertionError(f"Valkey PING failed: {last_error!r}")


def cleanup_valkey_prefix(url: str, key_prefix: str) -> None:
    client = valkey.Valkey.from_url(
        url,
        decode_responses=True,
        socket_connect_timeout=1.0,
        socket_timeout=1.0,
    )
    try:
        keys = list(
            client.scan_iter(match=f"{key_prefix}:reasoning:*", count=500)
        )
        for offset in range(0, len(keys), 500):
            batch = keys[offset : offset + 500]
            if batch:
                client.delete(*batch)
    finally:
        client.close()


def unique_key_prefix(test_id: str) -> str:
    slug = "".join(ch if ch.isalnum() else "-" for ch in test_id)
    return f"functional-{slug[:48]}-{uuid.uuid4().hex[:12]}"


def unused_port() -> int:
    with socket.create_server(("127.0.0.1", 0)) as server:
        return int(server.getsockname()[1])


def completion(
    *,
    chat_id: str,
    finish_reason: str,
    content: str = "",
    reasoning: str | None = None,
    tool_calls: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    message: dict[str, Any] = {"role": "assistant", "content": content}
    if reasoning is not None:
        message["reasoning_content"] = reasoning
    if tool_calls is not None:
        message["tool_calls"] = tool_calls
    return {
        "id": chat_id,
        "object": "chat.completion",
        "created": 1,
        "model": MODEL,
        "choices": [
            {"index": 0, "finish_reason": finish_reason, "message": message}
        ],
    }


def tool_call(call_id: str = CALL_ID_1) -> dict[str, Any]:
    return {
        "id": call_id,
        "type": "function",
        "function": {"name": "get_date", "arguments": "{}"},
    }


def first_chat_payload(*, stream: bool = False) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "model": MODEL,
        "messages": [{"role": "user", "content": "What's the date tomorrow?"}],
        "tools": TOOLS,
    }
    if stream:
        payload["stream"] = True
    return payload


def stripped_tool_followup(
    assistant_message: dict[str, Any],
    *,
    call_id: str = CALL_ID_1,
) -> dict[str, Any]:
    assistant = copy.deepcopy(assistant_message)
    assistant.pop("reasoning_content", None)
    return {
        "model": MODEL,
        "messages": [
            {"role": "user", "content": "What's the date tomorrow?"},
            assistant,
            {
                "role": "tool",
                "tool_call_id": call_id,
                "content": "2026-04-24",
            },
        ],
        "tools": TOOLS,
    }


@dataclass
class JsonResponse:
    status: int
    body: dict[str, Any]
    text: str


def post_json(
    url: str,
    payload: dict[str, Any],
    *,
    authorization: str = "Bearer sk-functional",
    timeout: float = 10.0,
) -> JsonResponse:
    request = Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers={
            "Authorization": authorization,
            "Content-Type": "application/json",
        },
    )
    return _open_json(request, timeout)


def get_json(url: str, *, timeout: float = 10.0) -> JsonResponse:
    return _open_json(Request(url, method="GET"), timeout)


def post_sse(
    url: str,
    payload: dict[str, Any],
    *,
    authorization: str = "Bearer sk-functional",
    timeout: float = 10.0,
) -> tuple[int, str]:
    request = Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers={
            "Authorization": authorization,
            "Content-Type": "application/json",
        },
    )
    with urlopen(request, timeout=timeout) as response:
        text = response.read().decode("utf-8", errors="replace")
        return response.status, text


def _open_json(request: Request, timeout: float) -> JsonResponse:
    try:
        with urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8", errors="replace")
            return JsonResponse(response.status, json.loads(raw), raw)
    except HTTPError as exc:
        try:
            raw = exc.read().decode("utf-8", errors="replace")
        finally:
            exc.close()
        try:
            body = json.loads(raw)
        except json.JSONDecodeError:
            body = {"raw": raw}
        return JsonResponse(exc.code, body, raw)


@dataclass
class MockRequest:
    method: str
    path: str
    authorization: str
    payload: dict[str, Any]
    status: int | None = None

    def summary(self) -> str:
        messages = self.payload.get("messages")
        roles: list[str] = []
        reasoning: list[bool] = []
        if isinstance(messages, list):
            for message in messages:
                if isinstance(message, dict):
                    roles.append(str(message.get("role")))
                    reasoning.append(
                        isinstance(message.get("reasoning_content"), str)
                    )
        return (
            f"{self.method} {self.path} status={self.status} "
            f"auth={bool(self.authorization)} roles={roles} "
            f"reasoning={reasoning}"
        )


class MockDeepSeekState:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.requests: list[MockRequest] = []

    def append(self, request: MockRequest) -> None:
        with self._lock:
            self.requests.append(request)

    def set_status(self, request: MockRequest, status: int) -> None:
        with self._lock:
            request.status = status

    def snapshot(self) -> list[MockRequest]:
        with self._lock:
            return list(self.requests)

    def chat_requests(self) -> list[MockRequest]:
        return [
            request
            for request in self.snapshot()
            if request.path == "/chat/completions"
        ]

    def diagnostics(self) -> str:
        rows = [request.summary() for request in self.snapshot()[-8:]]
        return "\n".join(rows) if rows else "no mock requests"


class StrictMockDeepSeekServer:
    def __init__(self) -> None:
        self.state = MockDeepSeekState()
        handler = self._make_handler(self.state)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.thread = threading.Thread(
            target=self.server.serve_forever,
            daemon=True,
        )
        self.thread.start()

    @property
    def url(self) -> str:
        host, port = self.server.server_address
        return f"http://{host}:{port}"

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)

    @staticmethod
    def _make_handler(
        state: MockDeepSeekState,
    ) -> type[BaseHTTPRequestHandler]:
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, fmt: str, *args: Any) -> None:
                return

            def do_POST(self) -> None:
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length).decode("utf-8", errors="replace")
                payload = json.loads(raw)
                request = MockRequest(
                    method="POST",
                    path=self.path,
                    authorization=self.headers.get("Authorization", ""),
                    payload=payload,
                )
                state.append(request)
                if self.path == "/chat/completions":
                    status, body, stream = handle_chat(payload)
                    state.set_status(request, status)
                    if stream is not None:
                        self._send_sse(status, stream)
                    else:
                        self._send_json(status, body)
                    return
                if self.path == "/embeddings":
                    body = {
                        "object": "list",
                        "model": str(payload.get("model") or "embedding"),
                        "data": [
                            {
                                "object": "embedding",
                                "index": 0,
                                "embedding": [0.1, 0.2, 0.3],
                            }
                        ],
                    }
                    state.set_status(request, 200)
                    self._send_json(200, body)
                    return
                state.set_status(request, 404)
                self._send_json(404, {"error": {"message": "not found"}})

            def _send_json(self, status: int, body: dict[str, Any]) -> None:
                encoded = json.dumps(body).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(encoded)))
                self.end_headers()
                self.wfile.write(encoded)

            def _send_sse(self, status: int, body: bytes) -> None:
                self.send_response(status)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        return Handler


def handle_chat(
    payload: dict[str, Any],
) -> tuple[int, dict[str, Any], bytes | None]:
    messages = payload.get("messages") or []
    if not isinstance(messages, list):
        return 400, {"error": {"message": "messages must be a list"}}, None
    missing = missing_reasoning_index(messages)
    if missing is not None:
        return (
            400,
            {
                "error": {
                    "message": (
                        "The reasoning_content in the thinking mode must be "
                        "passed back to the API."
                    ),
                    "type": "invalid_request_error",
                    "code": "invalid_request_error",
                    "missing_index": missing,
                }
            },
            None,
        )
    if payload.get("stream"):
        return 200, {}, streaming_tool_call_sse()
    if len(messages) == 1 and messages[0].get("role") == "user":
        return (
            200,
            completion(
                chat_id="chatcmpl-1-1",
                finish_reason="tool_calls",
                reasoning=THINKING_1_1,
                tool_calls=[tool_call()],
            ),
            None,
        )
    last_tool = last_index(messages, "tool")
    if last_tool != -1:
        tool_call_id = messages[last_tool].get("tool_call_id")
        if tool_call_id in {CALL_ID_1, CALL_ID_STREAM}:
            return (
                200,
                completion(
                    chat_id="chatcmpl-1-2",
                    finish_reason="stop",
                    content=ANSWER_1,
                    reasoning="Thinking 1.2 - tool result is enough.",
                ),
                None,
            )
    roles = [message.get("role") for message in messages]
    return 400, {"error": {"message": f"unexpected roles {roles!r}"}}, None


def missing_reasoning_index(messages: list[Any]) -> int | None:
    for index, message in enumerate(messages):
        if not isinstance(message, dict) or message.get("role") != "assistant":
            continue
        if is_tool_turn_assistant(messages, index) and not isinstance(
            message.get("reasoning_content"), str
        ):
            return index
    return None


def is_tool_turn_assistant(messages: list[Any], index: int) -> bool:
    message = messages[index]
    if not isinstance(message, dict):
        return False
    if message.get("tool_calls"):
        return True
    for prior in reversed(messages[:index]):
        if not isinstance(prior, dict):
            continue
        role = prior.get("role")
        if role == "tool":
            return True
        if role in {"user", "system"}:
            return False
    return False


def last_index(messages: list[Any], role: str) -> int:
    for index in range(len(messages) - 1, -1, -1):
        message = messages[index]
        if isinstance(message, dict) and message.get("role") == role:
            return index
    return -1


def streaming_tool_call_sse() -> bytes:
    chunks = [
        {
            "id": "chatcmpl-stream-1",
            "object": "chat.completion.chunk",
            "created": 1,
            "model": MODEL,
            "choices": [
                {
                    "index": 0,
                    "delta": {
                        "role": "assistant",
                        "reasoning_content": THINKING_STREAM,
                        "tool_calls": [
                            {
                                "index": 0,
                                "id": CALL_ID_STREAM,
                                "type": "function",
                                "function": {
                                    "name": "get_date",
                                    "arguments": "{}",
                                },
                            }
                        ],
                    },
                    "finish_reason": None,
                }
            ],
        },
        {
            "id": "chatcmpl-stream-1",
            "object": "chat.completion.chunk",
            "created": 1,
            "model": MODEL,
            "choices": [
                {"index": 0, "delta": {}, "finish_reason": "tool_calls"}
            ],
        },
    ]
    data = b"".join(
        f"data: {json.dumps(chunk)}\n\n".encode() for chunk in chunks
    )
    return data + b"data: [DONE]\n\n"


class BridgeProcess:
    def __init__(
        self,
        *,
        upstream_url: str,
        valkey_url: str,
        key_prefix: str,
        missing_reasoning_strategy: str = "recover",
    ) -> None:
        self.upstream_url = upstream_url
        self.valkey_url = valkey_url
        self.key_prefix = key_prefix
        self.missing_reasoning_strategy = missing_reasoning_strategy
        self.port = unused_port()
        self.base_url = f"http://127.0.0.1:{self.port}"
        self._home = tempfile.TemporaryDirectory()
        self._lines: list[str] = []
        self._reader: threading.Thread | None = None
        self.process: subprocess.Popen[str] | None = None

    def start(self, *, wait_for_ready: bool = True) -> BridgeProcess:
        env = os.environ.copy()
        env.update(
            {
                "DEEPSEEK_BRIDGE_RUNTIME_MODE": "kubernetes",
                "DEEPSEEK_BRIDGE_STORAGE_BACKEND": "valkey",
                "DEEPSEEK_BRIDGE_VALKEY_URL": self.valkey_url,
                "DEEPSEEK_BRIDGE_VALKEY_KEY_PREFIX": self.key_prefix,
                "DEEPSEEK_BRIDGE_MISSING_REASONING_STRATEGY": (
                    self.missing_reasoning_strategy
                ),
                "DEEPSEEK_BRIDGE_DISPLAY_REASONING": "0",
                "DEEPSEEK_BRIDGE_COLLAPSIBLE_REASONING": "0",
                "DEEPSEEK_BRIDGE_CORS": "0",
                "DEEPSEEK_BRIDGE_LOG_FILE_ENABLED": "0",
                "DEEPSEEK_BRIDGE_REQUEST_TIMEOUT": "5",
                "DEEPSEEK_BRIDGE_STREAM_READ_TIMEOUT": "5",
                "DEEPSEEK_BRIDGE_MAX_THREAD_POOL": "4",
                "HOME": self._home.name,
                "PYTHONUNBUFFERED": "1",
            }
        )
        command = [
            sys.executable,
            "-m",
            "deepseek_bridge",
            "--runtime-mode",
            "kubernetes",
            "--host",
            "127.0.0.1",
            "--port",
            str(self.port),
            "--base-url",
            self.upstream_url,
            "--headless",
            "--no-log",
            "--no-display-reasoning",
        ]
        self.process = subprocess.Popen(
            command,
            cwd=Path(__file__).resolve().parents[2],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        self._reader = threading.Thread(target=self._read_output, daemon=True)
        self._reader.start()
        if wait_for_ready:
            self.wait_ready()
        else:
            self.wait_health()
        return self

    def _read_output(self) -> None:
        assert self.process is not None
        assert self.process.stdout is not None
        for line in self.process.stdout:
            self._lines.append(line.rstrip())

    def wait_ready(self, timeout: float = 10.0) -> dict[str, Any]:
        return self._wait_for_json(
            f"{self.base_url}/readyz",
            timeout=timeout,
            predicate=lambda response: (
                response.status == 200 and bool(response.body.get("ok"))
            ),
        ).body

    def wait_health(self, timeout: float = 10.0) -> dict[str, Any]:
        return self._wait_for_json(
            f"{self.base_url}/healthz",
            timeout=timeout,
            predicate=lambda response: response.status == 200,
        ).body

    def _wait_for_json(
        self,
        url: str,
        *,
        timeout: float,
        predicate: Callable[[JsonResponse], bool],
    ) -> JsonResponse:
        deadline = time.monotonic() + timeout
        last_error: Exception | None = None
        last_response: JsonResponse | None = None
        while time.monotonic() < deadline:
            if self.process is not None and self.process.poll() is not None:
                raise AssertionError(
                    "bridge exited during startup\n" + self.diagnostics()
                )
            try:
                response = get_json(url, timeout=1.0)
                last_response = response
                if predicate(response):
                    return response
            except (ConnectionError, TimeoutError, URLError) as exc:
                last_error = exc
            time.sleep(0.1)
        detail = (
            last_response.text
            if last_response is not None
            else repr(last_error)
        )
        raise AssertionError(
            f"bridge did not become ready at {url}: {detail}\n"
            + self.diagnostics()
        )

    def stop(self) -> None:
        process = self.process
        if process is None:
            self._home.cleanup()
            return
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        if self._reader is not None:
            self._reader.join(timeout=2)
        self._home.cleanup()

    def diagnostics(self) -> str:
        return "\n".join(self._lines[-120:]) or "no bridge output"
