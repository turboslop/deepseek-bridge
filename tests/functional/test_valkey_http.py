from __future__ import annotations

import unittest
from typing import Any

from tests.functional.fixtures import (
    ANSWER_1,
    CALL_ID_1,
    CALL_ID_STREAM,
    MODEL,
    THINKING_1_1,
    THINKING_STREAM,
    BridgeProcess,
    StrictMockDeepSeekServer,
    cleanup_valkey_prefix,
    first_chat_payload,
    get_json,
    post_json,
    post_sse,
    require_functional_valkey,
    stripped_tool_followup,
    tool_call,
    unique_key_prefix,
    unused_port,
)


class FunctionalValkeyHttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.valkey_url = require_functional_valkey()

    def setUp(self) -> None:
        self.upstream = StrictMockDeepSeekServer()
        self.bridges: list[BridgeProcess] = []
        self.prefixes: list[str] = []

    def tearDown(self) -> None:
        for bridge in reversed(self.bridges):
            bridge.stop()
        for prefix in self.prefixes:
            cleanup_valkey_prefix(self.valkey_url, prefix)
        self.upstream.close()

    def new_prefix(self) -> str:
        prefix = unique_key_prefix(self.id())
        self.prefixes.append(prefix)
        return prefix

    def start_bridge(
        self,
        *,
        prefix: str | None = None,
        valkey_url: str | None = None,
        strategy: str = "recover",
        wait_for_ready: bool = True,
    ) -> BridgeProcess:
        bridge = BridgeProcess(
            upstream_url=self.upstream.url,
            valkey_url=valkey_url or self.valkey_url,
            key_prefix=prefix or self.new_prefix(),
            missing_reasoning_strategy=strategy,
        )
        self.bridges.append(bridge)
        try:
            bridge.start(wait_for_ready=wait_for_ready)
        except AssertionError as exc:
            raise AssertionError(f"{exc}\n{self.diagnostics()}") from exc
        return bridge

    def diagnostics(self) -> str:
        bridge_logs = "\n\n".join(
            bridge.diagnostics() for bridge in self.bridges
        )
        return (
            "\nBridge logs:\n"
            + bridge_logs
            + "\n\nMock requests:\n"
            + self.upstream.state.diagnostics()
        )

    def prime_tool_call(
        self,
        bridge: BridgeProcess,
        *,
        authorization: str = "Bearer sk-functional",
    ) -> dict[str, Any]:
        response = post_json(
            f"{bridge.base_url}/v1/chat/completions",
            first_chat_payload(),
            authorization=authorization,
        )
        self.assertEqual(response.status, 200, self.diagnostics())
        message = response.body["choices"][0]["message"]
        self.assertEqual(message["reasoning_content"], THINKING_1_1)
        self.assertEqual(message["tool_calls"][0]["id"], CALL_ID_1)
        return message

    def assert_restored_reasoning(
        self,
        request_index: int,
        expected_reasoning: str,
    ) -> None:
        chat_requests = self.upstream.state.chat_requests()
        self.assertGreater(
            len(chat_requests),
            request_index,
            self.diagnostics(),
        )
        messages = chat_requests[request_index].payload["messages"]
        self.assertEqual(
            messages[1]["reasoning_content"],
            expected_reasoning,
            self.diagnostics(),
        )

    def test_non_streaming_reasoning_recovery_with_valkey(self) -> None:
        bridge = self.start_bridge()
        assistant = self.prime_tool_call(bridge)

        response = post_json(
            f"{bridge.base_url}/v1/chat/completions",
            stripped_tool_followup(assistant),
        )

        self.assertEqual(response.status, 200, self.diagnostics())
        self.assertEqual(
            response.body["choices"][0]["message"]["content"],
            ANSWER_1,
        )
        self.assert_restored_reasoning(1, THINKING_1_1)

    def test_cross_process_shared_valkey_cache(self) -> None:
        prefix = self.new_prefix()
        bridge_a = self.start_bridge(prefix=prefix)
        assistant = self.prime_tool_call(bridge_a)
        bridge_a.stop()
        self.bridges.remove(bridge_a)

        bridge_b = self.start_bridge(prefix=prefix)
        response = post_json(
            f"{bridge_b.base_url}/v1/chat/completions",
            stripped_tool_followup(assistant),
        )

        self.assertEqual(response.status, 200, self.diagnostics())
        self.assert_restored_reasoning(1, THINKING_1_1)

    def test_streaming_to_non_streaming_recovery(self) -> None:
        bridge = self.start_bridge()
        status, text = post_sse(
            f"{bridge.base_url}/v1/chat/completions",
            first_chat_payload(stream=True),
        )
        self.assertEqual(status, 200, self.diagnostics())
        self.assertIn("data: [DONE]", text)
        assistant = {
            "role": "assistant",
            "content": "",
            "tool_calls": [tool_call(CALL_ID_STREAM)],
        }

        response = post_json(
            f"{bridge.base_url}/v1/chat/completions",
            stripped_tool_followup(assistant, call_id=CALL_ID_STREAM),
        )

        self.assertEqual(response.status, 200, self.diagnostics())
        self.assert_restored_reasoning(1, THINKING_STREAM)

    def test_valkey_prefix_isolation_reject_mode(self) -> None:
        prefix_a = self.new_prefix()
        prefix_b = self.new_prefix()
        bridge_a = self.start_bridge(prefix=prefix_a, strategy="reject")
        assistant = self.prime_tool_call(bridge_a)
        before = len(self.upstream.state.chat_requests())

        bridge_b = self.start_bridge(prefix=prefix_b, strategy="reject")
        response = post_json(
            f"{bridge_b.base_url}/v1/chat/completions",
            stripped_tool_followup(assistant),
        )

        self.assertEqual(response.status, 409, self.diagnostics())
        self.assertEqual(
            response.body["error"]["code"],
            "missing_reasoning_content",
        )
        self.assertEqual(
            len(self.upstream.state.chat_requests()),
            before,
            self.diagnostics(),
        )

    def test_authorization_namespace_isolation_reject_mode(self) -> None:
        bridge = self.start_bridge(strategy="reject")
        assistant = self.prime_tool_call(
            bridge,
            authorization="Bearer sk-user-a",
        )
        before = len(self.upstream.state.chat_requests())

        response = post_json(
            f"{bridge.base_url}/v1/chat/completions",
            stripped_tool_followup(assistant),
            authorization="Bearer sk-user-b",
        )

        self.assertEqual(response.status, 409, self.diagnostics())
        self.assertEqual(
            response.body["error"]["code"],
            "missing_reasoning_content",
        )
        self.assertEqual(
            len(self.upstream.state.chat_requests()),
            before,
            self.diagnostics(),
        )

    def test_readiness_and_liveness_with_valkey(self) -> None:
        bridge = self.start_bridge()

        ready = get_json(f"{bridge.base_url}/readyz")
        self.assertEqual(ready.status, 200, self.diagnostics())
        self.assertTrue(ready.body["checks"]["storage"]["ok"])
        self.assertEqual(ready.body["checks"]["storage"]["status"], "ok")

        health = get_json(f"{bridge.base_url}/healthz")
        self.assertEqual(health.status, 200, self.diagnostics())
        self.assertTrue(health.body["ok"])

        bad_port = unused_port()
        bad_bridge = self.start_bridge(
            valkey_url=f"redis://127.0.0.1:{bad_port}/0",
            wait_for_ready=False,
        )
        bad_ready = get_json(f"{bad_bridge.base_url}/readyz")
        self.assertEqual(bad_ready.status, 503, self.diagnostics())
        self.assertFalse(bad_ready.body["checks"]["storage"]["ok"])
        self.assertEqual(
            bad_ready.body["checks"]["storage"]["status"],
            "unavailable",
        )

        bad_health = get_json(f"{bad_bridge.base_url}/healthz")
        self.assertEqual(bad_health.status, 200, self.diagnostics())
        self.assertTrue(bad_health.body["ok"])

    def test_endpoint_smoke_through_real_bridge(self) -> None:
        bridge = self.start_bridge()

        health = get_json(f"{bridge.base_url}/healthz")
        self.assertEqual(health.status, 200, self.diagnostics())
        self.assertTrue(health.body["ok"])

        ready = get_json(f"{bridge.base_url}/readyz")
        self.assertEqual(ready.status, 200, self.diagnostics())
        self.assertTrue(ready.body["checks"]["storage"]["ok"])

        models = get_json(f"{bridge.base_url}/v1/models")
        self.assertEqual(models.status, 200, self.diagnostics())
        model_ids = [item["id"] for item in models.body["data"]]
        self.assertIn(MODEL, model_ids)

        chat = post_json(
            f"{bridge.base_url}/v1/chat/completions",
            first_chat_payload(),
        )
        self.assertEqual(chat.status, 200, self.diagnostics())

        embeddings = post_json(
            f"{bridge.base_url}/v1/embeddings",
            {"model": "deepseek-embedding", "input": "hello"},
        )
        self.assertEqual(embeddings.status, 200, self.diagnostics())
        self.assertEqual(embeddings.body["object"], "list")


if __name__ == "__main__":
    unittest.main()
