"""Unit tests for pure normalization utilities in _normalization.py."""

from __future__ import annotations

import unittest

from deepseek_bridge._normalization import (
    EFFORT_ALIASES,
    MESSAGE_FIELDS,
    ROLE_MESSAGE_FIELDS,
    convert_function_call,
    extract_text_content,
    legacy_function_to_tool,
    normalize_reasoning_effort,
    normalize_tool,
    normalize_tool_call,
    normalize_tool_choice,
    strip_cursor_thinking_blocks,
)


class EffortAliasesTests(unittest.TestCase):
    def test_all_expected_aliases(self) -> None:
        self.assertEqual(EFFORT_ALIASES["low"], "high")
        self.assertEqual(EFFORT_ALIASES["medium"], "high")
        self.assertEqual(EFFORT_ALIASES["high"], "high")
        self.assertEqual(EFFORT_ALIASES["max"], "max")
        self.assertEqual(EFFORT_ALIASES["xhigh"], "max")

    def test_non_string_effort_defaults_to_high(self) -> None:
        self.assertEqual(normalize_reasoning_effort(42), "high")

    def test_case_insensitive(self) -> None:
        self.assertEqual(normalize_reasoning_effort("MAX"), "max")

    def test_whitespace_trimmed(self) -> None:
        self.assertEqual(normalize_reasoning_effort("  high  "), "high")


class ExtractTextContentTests(unittest.TestCase):
    def test_none_returns_none(self) -> None:
        self.assertIsNone(extract_text_content(None))

    def test_plain_string_returns_itself(self) -> None:
        self.assertEqual(extract_text_content("hello"), "hello")

    def test_multimodal_list_extracts_text_parts(self) -> None:
        content = [
            {"type": "text", "text": "hello"},
            {"type": "text", "text": " world"},
        ]
        self.assertEqual(extract_text_content(content), "hello\n world")

    def test_multimodal_list_with_input_text_type(self) -> None:
        content = [{"type": "input_text", "text": "prompt"}]
        self.assertEqual(extract_text_content(content), "prompt")

    def test_multimodal_list_skips_non_text_items(self) -> None:
        content = [
            {"type": "image_url", "image_url": {"url": "http://..."}},
            {"type": "text", "text": "describe"},
        ]
        self.assertEqual(extract_text_content(content), "describe")

    def test_dict_content_returns_json_string(self) -> None:
        result = extract_text_content({"key": "value"})
        self.assertIn("key", result)
        self.assertIn("value", result)

    def test_non_dict_non_str_item_in_list(self) -> None:
        self.assertEqual(extract_text_content([42, "hello"]), "42\nhello")

    def test_empty_list_returns_empty_string(self) -> None:
        self.assertEqual(extract_text_content([]), "")

    def test_content_field_fallback(self) -> None:
        content = [{"type": "text", "content": "fallback"}]
        self.assertEqual(extract_text_content(content), "fallback")


class StripCursorThinkingBlocksTests(unittest.TestCase):
    def test_strips_think_tags(self) -> None:
        result = strip_cursor_thinking_blocks("<think>blah</think>actual")
        self.assertEqual(result, "actual")

    def test_strips_thinking_tags(self) -> None:
        result = strip_cursor_thinking_blocks("<thinking>blah</thinking>reply")
        self.assertEqual(result, "reply")

    def test_strips_details_summary_blocks(self) -> None:
        result = strip_cursor_thinking_blocks(
            "<details>\n<summary>\nThinking</summary>hidden</details>visible"
        )
        self.assertEqual(result, "visible")

    def test_strips_multiple_blocks(self) -> None:
        result = strip_cursor_thinking_blocks("<think>a</think><think>b</think>final")
        self.assertEqual(result, "final")

    def test_no_blocks_returns_unchanged(self) -> None:
        self.assertEqual(strip_cursor_thinking_blocks("plain text"), "plain text")

    def test_leading_newlines_stripped(self) -> None:
        result = strip_cursor_thinking_blocks("<think>x</think>\n\n\ntext")
        self.assertEqual(result, "text")


class NormalizeToolCallTests(unittest.TestCase):
    def test_valid_tool_call(self) -> None:
        tc = {
            "id": "call_123",
            "type": "function",
            "function": {"name": "get_weather", "arguments": '{"loc": "SF"}'},
        }
        result = normalize_tool_call(tc)
        self.assertEqual(result["id"], "call_123")
        self.assertEqual(result["type"], "function")
        self.assertEqual(result["function"]["name"], "get_weather")
        self.assertEqual(result["function"]["arguments"], '{"loc": "SF"}')

    def test_dict_arguments_serialized(self) -> None:
        tc = {
            "id": "c1",
            "function": {"name": "f", "arguments": {"a": 1}},
        }
        result = normalize_tool_call(tc)
        self.assertIn('"a"', result["function"]["arguments"])
        self.assertIn("1", result["function"]["arguments"])

    def test_missing_id_removed(self) -> None:
        tc = {"function": {"name": "f", "arguments": "{}"}}
        result = normalize_tool_call(tc)
        self.assertNotIn("id", result)

    def test_empty_id_removed(self) -> None:
        tc = {"id": "", "function": {"name": "f", "arguments": "{}"}}
        result = normalize_tool_call(tc)
        self.assertNotIn("id", result)

    def test_non_dict_input_defaults(self) -> None:
        result = normalize_tool_call(None)
        self.assertEqual(result["type"], "function")
        self.assertEqual(result["function"]["name"], "")

    def test_type_defaults_to_function(self) -> None:
        tc = {"id": "x", "function": {"name": "f", "arguments": "{}"}}
        result = normalize_tool_call(tc)
        self.assertEqual(result["type"], "function")


class NormalizeToolTests(unittest.TestCase):
    def test_valid_tool_preserved(self) -> None:
        tool = {"type": "function", "function": {"name": "get_time"}}
        result = normalize_tool(tool)
        self.assertEqual(result["type"], "function")
        self.assertEqual(result["function"]["name"], "get_time")

    def test_missing_type_defaults_to_function(self) -> None:
        tool = {"function": {"name": "test"}}
        result = normalize_tool(tool)
        self.assertEqual(result["type"], "function")

    def test_non_dict_input_returns_template(self) -> None:
        result = normalize_tool("not_a_dict")
        self.assertEqual(result["type"], "function")
        self.assertIn("function", result)

    def test_function_key_is_dict(self) -> None:
        tool = {"type": "function", "function": {"name": "ok"}}
        result = normalize_tool(tool)
        self.assertIsInstance(result["function"], dict)


class LegacyFunctionToToolTests(unittest.TestCase):
    def test_wraps_function_in_tool(self) -> None:
        fn = {"name": "do_stuff", "description": "does stuff"}
        result = legacy_function_to_tool(fn)
        self.assertEqual(result["type"], "function")
        self.assertEqual(result["function"]["name"], "do_stuff")

    def test_non_dict_input(self) -> None:
        result = legacy_function_to_tool(None)
        self.assertEqual(result["type"], "function")
        self.assertIn("function", result)


class ConvertFunctionCallTests(unittest.TestCase):
    def test_auto_string_passes_through(self) -> None:
        self.assertEqual(convert_function_call("auto"), "auto")
        self.assertEqual(convert_function_call("none"), "none")

    def test_unknown_string_returns_none(self) -> None:
        self.assertIsNone(convert_function_call("invalid"))

    def test_dict_with_name_converts(self) -> None:
        result = convert_function_call({"name": "my_func"})
        self.assertEqual(result["type"], "function")
        self.assertEqual(result["function"]["name"], "my_func")

    def test_dict_without_name_returns_none(self) -> None:
        self.assertIsNone(convert_function_call({}))


class NormalizeToolChoiceTests(unittest.TestCase):
    def test_string_auto_passes_through(self) -> None:
        self.assertEqual(normalize_tool_choice("auto"), "auto")
        self.assertEqual(normalize_tool_choice("required"), "required")

    def test_invalid_string_returns_none(self) -> None:
        self.assertIsNone(normalize_tool_choice("bad"))

    def test_dict_with_function_type(self) -> None:
        tc = {"type": "function", "function": {"name": "my_func"}}
        result = normalize_tool_choice(tc)
        self.assertEqual(result["type"], "function")
        self.assertEqual(result["function"]["name"], "my_func")

    def test_dict_without_function_type_passes_through(self) -> None:
        tc = {"type": "unknown"}
        result = normalize_tool_choice(tc)
        self.assertEqual(result, tc)

    def test_non_dict_non_str_passes_through(self) -> None:
        self.assertEqual(normalize_tool_choice(42), 42)


class MessageFieldsTests(unittest.TestCase):
    def test_expected_message_fields(self) -> None:
        for field in (
            "role",
            "content",
            "name",
            "tool_call_id",
            "tool_calls",
            "reasoning_content",
            "prefix",
        ):
            self.assertIn(field, MESSAGE_FIELDS)

    def test_role_fields_have_expected_keys(self) -> None:
        self.assertIn("tool_calls", ROLE_MESSAGE_FIELDS["assistant"])
        self.assertIn("reasoning_content", ROLE_MESSAGE_FIELDS["assistant"])
        self.assertIn("tool_call_id", ROLE_MESSAGE_FIELDS["tool"])
