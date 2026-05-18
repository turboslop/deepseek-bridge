"""Unit tests for DeepSeek API compliance.

No network calls. Tests pure functions from transform.py and config.py.
"""

from __future__ import annotations

import unittest

from deepseek_bridge.config import ProxyConfig
from deepseek_bridge.transform import (
    EFFORT_ALIASES,
    SUPPORTED_REQUEST_FIELDS,
    normalize_reasoning_effort,
    upstream_model_for,
)


class UpstreamModelRoutingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = ProxyConfig()

    def test_legacy_chat_maps_to_flash(self) -> None:
        result = upstream_model_for("deepseek-chat", self.config)
        self.assertEqual(result, "deepseek-v4-flash")

    def test_legacy_reasoner_maps_to_flash(self) -> None:
        result = upstream_model_for("deepseek-reasoner", self.config)
        self.assertEqual(result, "deepseek-v4-flash")

    def test_v4_pro_passes_through(self) -> None:
        self.assertEqual(
            upstream_model_for("deepseek-v4-pro", self.config),
            "deepseek-v4-pro",
        )

    def test_v4_flash_passes_through(self) -> None:
        self.assertEqual(
            upstream_model_for("deepseek-v4-flash", self.config),
            "deepseek-v4-flash",
        )

    def test_v4_unknown_variant_passes_through(self) -> None:
        self.assertEqual(
            upstream_model_for("deepseek-v4-future-model", self.config),
            "deepseek-v4-future-model",
        )

    def test_non_deepseek_model_falls_back_to_config(self) -> None:
        result = upstream_model_for("gpt-4", self.config)
        self.assertEqual(result, self.config.upstream_model)

    def test_passthrough_any_deepseek_prefix(self) -> None:
        self.assertEqual(
            upstream_model_for("deepseek-some-custom-model", self.config),
            "deepseek-some-custom-model",
        )


class SupportedRequestFieldsTests(unittest.TestCase):
    def test_contains_thinking(self) -> None:
        self.assertIn("thinking", SUPPORTED_REQUEST_FIELDS)

    def test_contains_response_format(self) -> None:
        self.assertIn("response_format", SUPPORTED_REQUEST_FIELDS)

    def test_contains_logprobs(self) -> None:
        self.assertIn("logprobs", SUPPORTED_REQUEST_FIELDS)

    def test_contains_top_logprobs(self) -> None:
        self.assertIn("top_logprobs", SUPPORTED_REQUEST_FIELDS)

    def test_contains_user_id(self) -> None:
        self.assertIn("user_id", SUPPORTED_REQUEST_FIELDS)

    def test_contains_model_and_messages(self) -> None:
        self.assertIn("model", SUPPORTED_REQUEST_FIELDS)
        self.assertIn("messages", SUPPORTED_REQUEST_FIELDS)

    def test_contains_tool_related_fields(self) -> None:
        self.assertIn("tools", SUPPORTED_REQUEST_FIELDS)
        self.assertIn("tool_choice", SUPPORTED_REQUEST_FIELDS)

    def test_contains_reasoning_effort(self) -> None:
        # reasoning_effort is now nested inside "thinking" parameter,
        # not a top-level field — per DeepSeek REST API spec
        self.assertNotIn("reasoning_effort", SUPPORTED_REQUEST_FIELDS)

    def test_contains_stream_and_options(self) -> None:
        self.assertIn("stream", SUPPORTED_REQUEST_FIELDS)
        self.assertIn("stream_options", SUPPORTED_REQUEST_FIELDS)

    def test_contains_standard_completions_fields(self) -> None:
        for field in ("temperature", "max_tokens", "top_p", "stop"):
            self.assertIn(field, SUPPORTED_REQUEST_FIELDS, field)

    def test_is_a_set(self) -> None:
        self.assertIsInstance(SUPPORTED_REQUEST_FIELDS, set)


class EffortAliasesTests(unittest.TestCase):
    def test_aliases_dictionary_correct(self) -> None:
        self.assertEqual(EFFORT_ALIASES["low"], "high")
        self.assertEqual(EFFORT_ALIASES["medium"], "high")
        self.assertEqual(EFFORT_ALIASES["high"], "high")
        self.assertEqual(EFFORT_ALIASES["max"], "max")
        self.assertEqual(EFFORT_ALIASES["xhigh"], "max")

    def test_low_medium_and_high_all_map_to_high(self) -> None:
        for alias in ("low", "medium", "high"):
            self.assertEqual(
                EFFORT_ALIASES[alias],
                "high",
                f"{alias!r} should alias to 'high'",
            )

    def test_max_and_xhigh_map_to_max(self) -> None:
        for alias in ("max", "xhigh"):
            self.assertEqual(
                EFFORT_ALIASES[alias],
                "max",
                f"{alias!r} should alias to 'max'",
            )

    def test_unknown_effort_defaults_to_high(self) -> None:
        result = normalize_reasoning_effort("nonsense")
        self.assertEqual(result, "high")

    def test_non_string_effort_defaults_to_high(self) -> None:
        self.assertEqual(normalize_reasoning_effort(None), "high")
        self.assertEqual(normalize_reasoning_effort(123), "high")
        self.assertEqual(normalize_reasoning_effort([]), "high")

    def test_normalize_effort_uses_aliases(self) -> None:
        self.assertEqual(normalize_reasoning_effort("low"), "high")
        self.assertEqual(normalize_reasoning_effort("max"), "max")

    def test_normalize_effort_case_insensitive(self) -> None:
        self.assertEqual(normalize_reasoning_effort("LOW"), "high")
        self.assertEqual(normalize_reasoning_effort("MAX"), "max")

    def test_normalize_effort_trims_whitespace(self) -> None:
        self.assertEqual(normalize_reasoning_effort("  max  "), "max")
        self.assertEqual(normalize_reasoning_effort(" low "), "high")
