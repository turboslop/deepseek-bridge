from __future__ import annotations

import unittest
from typing import Any

import httpx
from starlette.testclient import TestClient

from deepseek_bridge.asgi import create_app
from deepseek_bridge.config import ProxyConfig
from deepseek_bridge.metrics import METRICS


class _Store:
    def __init__(self) -> None:
        self.messages: list[dict[str, Any]] = []
        self.closed = False

    def health_check(self) -> tuple[bool, str]:
        return True, "ok"

    def store_assistant_message(
        self,
        message: dict[str, Any],
        scope: str,
        cache_namespace: str = "",
        prior_messages: list[dict[str, Any]] | None = None,
    ) -> int:
        self.messages.append(message)
        return 1


class _Upstream:
    def __init__(
        self,
        responses: list[httpx.Response] | None = None,
        failures: list[Exception] | None = None,
    ) -> None:
        self.responses = responses or []
        self.failures = failures or []
        self.calls = 0
        self.closed = False

    @property
    def is_closed(self) -> bool:
        return self.closed

    async def post(
        self,
        url: str,
        *,
        body: bytes,
        headers: dict[str, str],
        stream: bool,
    ) -> httpx.Response:
        self.calls += 1
        if self.failures:
            raise self.failures.pop(0)
        response = self.responses.pop(0)
        response.request = httpx.Request("POST", url)
        return response

    async def aclose(self) -> None:
        self.closed = True


def _chat_response(content: bytes | None = None) -> httpx.Response:
    body = content or (
        b'{"id":"chatcmpl-test","object":"chat.completion",'
        b'"model":"deepseek-v4-pro","choices":[{"index":0,'
        b'"message":{"role":"assistant","content":"ok",'
        b'"reasoning_content":"hidden"},"finish_reason":"stop"}],'
        b'"usage":{"prompt_tokens":1,"completion_tokens":1,"total_tokens":2}}'
    )
    return httpx.Response(
        200,
        content=body,
        headers={"Content-Type": "application/json"},
    )


def _config(**kwargs: Any) -> ProxyConfig:
    return ProxyConfig(
        upstream_retry_initial_delay_seconds=0,
        upstream_retry_max_delay_seconds=0,
        upstream_retry_jitter_seconds=0,
        **kwargs,
    )


class ASGIAppTests(unittest.TestCase):
    def setUp(self) -> None:
        METRICS.reset()

    def tearDown(self) -> None:
        METRICS.reset()

    def test_health_ready_models_cors_and_request_id(self) -> None:
        app = create_app(_config(), _Store(), _Upstream())

        with TestClient(app) as client:
            health = client.get(
                "/healthz", headers={"Origin": "http://localhost:3000"}
            )
            ready = client.get("/readyz")
            models = client.get("/v1/models")

        self.assertEqual(health.status_code, 200)
        self.assertTrue(health.json()["ok"])
        self.assertEqual(
            health.headers["Access-Control-Allow-Origin"],
            "http://localhost:3000",
        )
        self.assertIn("x-request-id", health.headers)
        self.assertEqual(ready.status_code, 200)
        self.assertTrue(ready.json()["checks"]["asgi"]["ok"])
        self.assertEqual(models.status_code, 200)
        self.assertEqual(models.json()["object"], "list")

    def test_chat_validation_errors(self) -> None:
        app = create_app(
            _config(max_request_body_bytes=2), _Store(), _Upstream()
        )

        with TestClient(app) as client:
            missing_auth = client.post("/v1/chat/completions", json={})
            invalid_json = client.post(
                "/v1/chat/completions",
                content=b"{",
                headers={"Authorization": "Bearer test"},
            )
            too_large = client.post(
                "/v1/chat/completions",
                content=b"{}{}",
                headers={"Authorization": "Bearer test"},
            )
            not_found = client.post("/v1/unknown", json={})

        self.assertEqual(missing_auth.status_code, 401)
        self.assertEqual(invalid_json.status_code, 400)
        self.assertEqual(too_large.status_code, 413)
        self.assertEqual(not_found.status_code, 404)

    def test_non_streaming_chat_retries_connect_failures(self) -> None:
        upstream = _Upstream(
            responses=[_chat_response()],
            failures=[
                httpx.ConnectError("connect failed"),
                httpx.ConnectError("connect failed again"),
            ],
        )
        app = create_app(_config(metrics_enabled=True), _Store(), upstream)

        with TestClient(app) as client:
            response = client.post(
                "/v1/chat/completions",
                json={"model": "deepseek-v4-pro", "messages": []},
                headers={"Authorization": "Bearer test"},
            )
            metrics = client.get("/metrics").text

        self.assertEqual(response.status_code, 200)
        self.assertEqual(upstream.calls, 3)
        self.assertEqual(response.json()["model"], "deepseek-v4-pro")
        self.assertIn("deepseek_bridge_upstream_retries_total", metrics)
        self.assertIn('attempt="1"', metrics)
        self.assertIn('attempt="2"', metrics)

    def test_retry_exhaustion_returns_upstream_failure(self) -> None:
        upstream = _Upstream(
            failures=[
                httpx.ConnectError("one"),
                httpx.ConnectError("two"),
                httpx.ConnectError("three"),
            ]
        )
        app = create_app(_config(metrics_enabled=True), _Store(), upstream)

        with TestClient(app) as client:
            response = client.post(
                "/v1/chat/completions",
                json={"model": "deepseek-v4-pro", "messages": []},
                headers={"Authorization": "Bearer test"},
            )
            metrics = client.get("/metrics").text

        self.assertEqual(response.status_code, 500)
        self.assertEqual(upstream.calls, 3)
        self.assertIn("deepseek_bridge_upstream_retry_exhausted_total", metrics)

    def test_streaming_chat_rewrites_sse(self) -> None:
        stream_body = (
            b'data: {"id":"chunk","object":"chat.completion.chunk",'
            b'"created":1,"model":"deepseek-v4-pro","choices":[{"index":0,'
            b'"delta":{"content":"hi"},"finish_reason":null}]}\n\n'
            b"data: [DONE]\n\n"
        )
        upstream = _Upstream(
            responses=[
                httpx.Response(
                    200,
                    content=stream_body,
                    headers={"Content-Type": "text/event-stream"},
                )
            ]
        )
        app = create_app(_config(display_reasoning=False), _Store(), upstream)

        with (
            TestClient(app) as client,
            client.stream(
                "POST",
                "/v1/chat/completions",
                json={
                    "model": "deepseek-v4-flash",
                    "messages": [],
                    "stream": True,
                },
                headers={"Authorization": "Bearer test"},
            ) as response,
        ):
            body = response.read()

        self.assertEqual(response.status_code, 200)
        self.assertIn(b'"model":"deepseek-v4-flash"', body)
        self.assertIn(b'"system_fingerprint":"fp_deepseek_bridge"', body)
        self.assertIn(b"data: [DONE]", body)


if __name__ == "__main__":
    unittest.main()
