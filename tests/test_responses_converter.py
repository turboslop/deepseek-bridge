"""Pure-function unit tests for responses_converter.py.

Tests detection of Responses API payloads and conversion to Chat Completions format.
No network calls — purely dict-in/dict-out assertions.
"""

from __future__ import annotations

import unittest

from deepseek_bridge.responses_converter import (
    _convert_input_item,
    _convert_tools,
    _stringify_content,
    convert_responses_to_chat,
    detect_responses_payload,
)


class ResponsesConverterDetectionTests(unittest.TestCase):
    def test_detects_input_field(self) -> None:
        payload = {"input": [], "model": "x"}
        self.assertTrue(detect_responses_payload(payload))

    def test_detects_instructions_field(self) -> None:
        payload = {"instructions": "hi", "model": "x"}
        self.assertTrue(detect_responses_payload(payload))

    def test_rejects_chat_payload(self) -> None:
        payload = {"messages": [{"role": "user", "content": "hi"}], "model": "x"}
        self.assertFalse(detect_responses_payload(payload))

    def test_rejects_empty_dict(self) -> None:
        self.assertFalse(detect_responses_payload({}))

    def test_rejects_non_dict(self) -> None:
        self.assertFalse(detect_responses_payload("not a dict"))
        self.assertFalse(detect_responses_payload(None))
        self.assertFalse(detect_responses_payload([]))

    def test_messages_present_overrides_input(self) -> None:
        payload = {
            "input": [{"role": "user", "content": "hi"}],
            "messages": [{"role": "user", "content": "hi"}],
            "model": "x",
        }
        self.assertFalse(detect_responses_payload(payload))

    def test_messages_present_overrides_instructions(self) -> None:
        payload = {
            "instructions": "You are helpful",
            "messages": [{"role": "user", "content": "hi"}],
            "model": "x",
        }
        self.assertFalse(detect_responses_payload(payload))

    def test_detects_input_and_instructions(self) -> None:
        payload = {"input": [], "instructions": "Be helpful", "model": "x"}
        self.assertTrue(detect_responses_payload(payload))


class ResponsesConverterConversionTests(unittest.TestCase):
    # ── basic input → messages ──────────────────────────────────────────

    def test_input_to_messages_simple(self) -> None:
        payload = {"input": [{"role": "user", "content": "Hello"}], "model": "x"}
        result = convert_responses_to_chat(payload)
        self.assertIn("messages", result)
        self.assertNotIn("input", result)
        self.assertEqual(result["messages"][0]["role"], "user")
        self.assertEqual(result["messages"][0]["content"], "Hello")

    def test_convert_preserves_reasoning_content_in_assistant(self) -> None:
        """Verify reasoning_content is preserved in assistant input items."""
        payload = {
            "input": [
                {
                    "role": "assistant",
                    "content": "Let me think...",
                    "reasoning_content": "I need to calculate...",
                }
            ]
        }
        from deepseek_bridge.responses_converter import convert_responses_to_chat
        result = convert_responses_to_chat(payload)
        messages = result.get("messages", [])
        self.assertEqual(len(messages), 1)
        assistant_msg = messages[0]
        self.assertEqual(assistant_msg["role"], "assistant")
        self.assertEqual(assistant_msg.get("reasoning_content"), "I need to calculate...")

    def test_multiple_input_items(self) -> None:
        payload = {
            "input": [
                {"role": "user", "content": "first"},
                {"role": "assistant", "content": "reply"},
                {"role": "user", "content": "second"},
            ],
            "model": "x",
        }
        result = convert_responses_to_chat(payload)
        self.assertEqual(len(result["messages"]), 3)
        self.assertEqual(result["messages"][0]["role"], "user")
        self.assertEqual(result["messages"][1]["role"], "assistant")
        self.assertEqual(result["messages"][2]["role"], "user")

    def test_empty_input_handled(self) -> None:
        payload = {"input": [], "model": "x"}
        result = convert_responses_to_chat(payload)
        self.assertIn("messages", result)
        self.assertEqual(result["messages"], [])

    def test_input_not_a_list(self) -> None:
        payload = {"input": "not a list", "model": "x"}
        result = convert_responses_to_chat(payload)
        self.assertEqual(result["messages"], [])

    def test_skips_non_dict_input_items(self) -> None:
        payload = {
            "input": [
                {"role": "user", "content": "hi"},
                "string item",
                123,
                None,
                {"role": "assistant", "content": "ok"},
            ],
            "model": "x",
        }
        result = convert_responses_to_chat(payload)
        self.assertEqual(len(result["messages"]), 2)

    # ── function_call_output → tool ────────────────────────────────────

    def test_function_call_output_to_tool(self) -> None:
        payload = {
            "input": [
                {"type": "function_call_output", "call_id": "abc", "output": "result"}
            ],
            "model": "x",
        }
        result = convert_responses_to_chat(payload)
        self.assertEqual(result["messages"][0]["role"], "tool")
        self.assertEqual(result["messages"][0]["tool_call_id"], "abc")
        self.assertEqual(result["messages"][0]["content"], "result")

    def test_function_call_output_missing_call_id(self) -> None:
        payload = {
            "input": [{"type": "function_call_output", "output": "result"}],
            "model": "x",
        }
        result = convert_responses_to_chat(payload)
        self.assertEqual(result["messages"][0]["tool_call_id"], "")

    def test_function_call_output_missing_output(self) -> None:
        payload = {
            "input": [{"type": "function_call_output", "call_id": "abc"}],
            "model": "x",
        }
        result = convert_responses_to_chat(payload)
        self.assertEqual(result["messages"][0]["content"], "")

    # ── instructions → system message ──────────────────────────────────

    def test_instructions_to_system_message(self) -> None:
        payload = {
            "input": [{"role": "user", "content": "Hi"}],
            "instructions": "You are helpful",
            "model": "x",
        }
        result = convert_responses_to_chat(payload)
        self.assertEqual(result["messages"][0]["role"], "system")
        self.assertEqual(result["messages"][0]["content"], "You are helpful")
        self.assertEqual(result["messages"][1]["role"], "user")

    def test_instructions_whitespace_only_skipped(self) -> None:
        payload = {
            "input": [{"role": "user", "content": "Hi"}],
            "instructions": "   ",
            "model": "x",
        }
        result = convert_responses_to_chat(payload)
        roles = [m["role"] for m in result["messages"]]
        self.assertNotIn("system", roles)

    def test_instructions_not_a_string_skipped(self) -> None:
        payload = {
            "input": [{"role": "user", "content": "Hi"}],
            "instructions": 123,
            "model": "x",
        }
        result = convert_responses_to_chat(payload)
        roles = [m["role"] for m in result["messages"]]
        self.assertNotIn("system", roles)

    # ── passthrough fields ─────────────────────────────────────────────

    def test_passthrough_fields(self) -> None:
        payload = {
            "input": [{"role": "user", "content": "Hi"}],
            "model": "v4-pro",
            "stream": True,
            "temperature": 0.7,
            "top_p": 0.9,
            "max_tokens": 100,
            "stream_options": {"include_usage": True},
        }
        result = convert_responses_to_chat(payload)
        self.assertEqual(result["model"], "v4-pro")
        self.assertEqual(result["stream"], True)
        self.assertEqual(result["temperature"], 0.7)
        self.assertEqual(result["top_p"], 0.9)
        self.assertEqual(result["max_tokens"], 100)
        self.assertEqual(result["stream_options"], {"include_usage": True})

    # ── dropped Responses-API-only fields ──────────────────────────────

    def test_drops_responses_only_fields(self) -> None:
        payload = {
            "input": [{"role": "user", "content": "Hi"}],
            "include": ["reasoning"],
            "previous_response_id": "123",
            "store": True,
            "model": "x",
        }
        result = convert_responses_to_chat(payload)
        self.assertNotIn("include", result)
        self.assertNotIn("previous_response_id", result)
        self.assertNotIn("store", result)

    def test_drops_text_field(self) -> None:
        payload = {
            "input": [{"role": "user", "content": "Hi"}],
            "text": {"format": "markdown"},
            "model": "x",
        }
        result = convert_responses_to_chat(payload)
        self.assertNotIn("text", result)

    # ── reasoning → reasoning_effort ───────────────────────────────────

    def test_reasoning_dict_to_reasoning_effort(self) -> None:
        payload = {
            "input": [{"role": "user", "content": "Hi"}],
            "reasoning": {"effort": "max"},
            "model": "x",
        }
        result = convert_responses_to_chat(payload)
        self.assertEqual(result["reasoning_effort"], "max")
        self.assertNotIn("reasoning", result)

    def test_reasoning_without_effort_key(self) -> None:
        payload = {
            "input": [{"role": "user", "content": "Hi"}],
            "reasoning": {"summarize": "auto"},
            "model": "x",
        }
        result = convert_responses_to_chat(payload)
        self.assertNotIn("reasoning_effort", result)

    def test_reasoning_not_a_dict(self) -> None:
        payload = {
            "input": [{"role": "user", "content": "Hi"}],
            "reasoning": "max",
            "model": "x",
        }
        result = convert_responses_to_chat(payload)
        self.assertNotIn("reasoning_effort", result)

    def test_reasoning_effort_not_a_string(self) -> None:
        payload = {
            "input": [{"role": "user", "content": "Hi"}],
            "reasoning": {"effort": 123},
            "model": "x",
        }
        result = convert_responses_to_chat(payload)
        self.assertNotIn("reasoning_effort", result)

    # ── non-Responses payload pass-through ─────────────────────────────

    def test_standard_payload_unchanged(self) -> None:
        payload = {
            "messages": [{"role": "user", "content": "hi"}],
            "model": "x",
            "stream": True,
        }
        result = convert_responses_to_chat(payload)
        self.assertEqual(result, payload)

    def test_non_dict_payload_unchanged(self) -> None:
        self.assertEqual(convert_responses_to_chat("foo"), "foo")
        self.assertEqual(convert_responses_to_chat(None), None)
        self.assertEqual(convert_responses_to_chat([]), [])

    def test_instructions_only_payload_returns_copy(self) -> None:
        payload = {"instructions": "Be brief", "model": "x"}
        result = convert_responses_to_chat(payload)
        self.assertEqual(result, payload)
        self.assertIsNot(result, payload)  # shallow copy

    # ── tools conversion ───────────────────────────────────────────────

    def test_nested_function_tools_passthrough(self) -> None:
        payload = {
            "input": [{"role": "user", "content": "Hi"}],
            "tools": [
                {
                    "type": "function",
                    "function": {
                        "name": "get_weather",
                        "description": "Get weather",
                        "parameters": {"type": "object"},
                    },
                }
            ],
            "model": "x",
        }
        result = convert_responses_to_chat(payload)
        self.assertIn("tools", result)
        self.assertEqual(result["tools"][0]["type"], "function")
        self.assertEqual(result["tools"][0]["function"]["name"], "get_weather")

    def test_flat_function_tools_converted(self) -> None:
        payload = {
            "input": [{"role": "user", "content": "Hi"}],
            "tools": [
                {
                    "type": "function",
                    "name": "get_weather",
                    "description": "Get weather",
                    "parameters": {"type": "object"},
                }
            ],
            "model": "x",
        }
        result = convert_responses_to_chat(payload)
        self.assertEqual(result["tools"][0]["type"], "function")
        self.assertIn("function", result["tools"][0])
        func = result["tools"][0]["function"]
        self.assertEqual(func["name"], "get_weather")
        self.assertEqual(func["description"], "Get weather")

    def test_custom_tool_converted_to_function(self) -> None:
        payload = {
            "input": [{"role": "user", "content": "Hi"}],
            "tools": [
                {
                    "type": "custom",
                    "name": "my_plugin",
                    "description": "A custom plugin",
                    "input_schema": {
                        "type": "object",
                        "properties": {"action": {"type": "string"}},
                    },
                }
            ],
            "model": "x",
        }
        result = convert_responses_to_chat(payload)
        self.assertEqual(result["tools"][0]["type"], "function")
        self.assertEqual(result["tools"][0]["function"]["name"], "my_plugin")
        self.assertEqual(
            result["tools"][0]["function"]["parameters"]["properties"]["action"][
                "type"
            ],
            "string",
        )

    def test_custom_tool_without_input_schema_gets_default(self) -> None:
        payload = {
            "input": [{"role": "user", "content": "Hi"}],
            "tools": [
                {
                    "type": "custom",
                    "name": "my_plugin",
                }
            ],
            "model": "x",
        }
        result = convert_responses_to_chat(payload)
        func = result["tools"][0]["function"]
        self.assertIn("input", func["parameters"]["properties"])

    def test_custom_tool_empty_name_skipped(self) -> None:
        payload = {
            "input": [{"role": "user", "content": "Hi"}],
            "tools": [
                {"type": "custom", "name": ""},
                {"type": "custom", "name": "valid_tool"},
            ],
            "model": "x",
        }
        result = convert_responses_to_chat(payload)
        self.assertEqual(len(result["tools"]), 1)
        self.assertEqual(result["tools"][0]["function"]["name"], "valid_tool")

    def test_unhandled_tool_type_skipped(self) -> None:
        payload = {
            "input": [{"role": "user", "content": "Hi"}],
            "tools": [
                {"type": "web_search", "query": "test"},
                {"type": "function", "function": {"name": "ok"}},
            ],
            "model": "x",
        }
        result = convert_responses_to_chat(payload)
        self.assertEqual(len(result["tools"]), 1)

    def test_non_list_tools_skipped(self) -> None:
        payload = {
            "input": [{"role": "user", "content": "Hi"}],
            "tools": "not a list",
            "model": "x",
        }
        result = convert_responses_to_chat(payload)
        self.assertNotIn("tools", result)

    # ── tool_choice passthrough ────────────────────────────────────────

    def test_tool_choice_passthrough(self) -> None:
        payload = {
            "input": [{"role": "user", "content": "Hi"}],
            "tool_choice": "auto",
            "model": "x",
        }
        result = convert_responses_to_chat(payload)
        self.assertEqual(result["tool_choice"], "auto")

    # ── typed message items ────────────────────────────────────────────

    def test_typed_message_item(self) -> None:
        payload = {
            "input": [
                {"type": "message", "role": "assistant", "content": "Hello, user"}
            ],
            "model": "x",
        }
        result = convert_responses_to_chat(payload)
        self.assertEqual(result["messages"][0]["role"], "assistant")
        self.assertEqual(result["messages"][0]["content"], "Hello, user")

    # ── fallback content extraction ────────────────────────────────────

    def test_fallback_content_on_unknown_type(self) -> None:
        payload = {
            "input": [{"type": "unknown_future_type", "content": "some text"}],
            "model": "x",
        }
        result = convert_responses_to_chat(payload)
        self.assertEqual(len(result["messages"]), 1)
        self.assertEqual(result["messages"][0]["content"], "some text")

    def test_input_item_without_role_or_type_and_no_content(self) -> None:
        payload = {"input": [{"foo": "bar"}], "model": "x"}
        result = convert_responses_to_chat(payload)
        self.assertEqual(result["messages"], [])

    # ── content stringification ────────────────────────────────────────

    def test_stringify_content_none(self) -> None:
        self.assertEqual(_stringify_content(None), "")

    def test_stringify_content_int(self) -> None:
        self.assertEqual(_stringify_content(42), "42")

    def test_stringify_content_list(self) -> None:
        # extract_text_content joins text items with newlines
        self.assertEqual(_stringify_content(["a", "b"]), "a\nb")


class ConvertInputItemTests(unittest.TestCase):
    def test_role_based_system(self) -> None:
        result = _convert_input_item({"role": "system", "content": "You are helpful"})
        assert result is not None
        self.assertEqual(result["role"], "system")
        self.assertEqual(result["content"], "You are helpful")

    def test_role_based_user(self) -> None:
        result = _convert_input_item({"role": "user", "content": "Hello"})
        assert result is not None
        self.assertEqual(result["role"], "user")

    def test_role_based_assistant(self) -> None:
        result = _convert_input_item({"role": "assistant", "content": "Hi there"})
        assert result is not None
        self.assertEqual(result["role"], "assistant")


class ConvertToolsTests(unittest.TestCase):
    def test_skips_non_dict_items(self) -> None:
        result = _convert_tools(["string", None, 123])
        self.assertEqual(result, [])

    def test_mixed_tools(self) -> None:
        result = _convert_tools(
            [
                {"type": "function", "function": {"name": "f1"}},
                "not a dict",
                {"type": "unknown_type", "name": "skip"},
            ]
        )
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["function"]["name"], "f1")
