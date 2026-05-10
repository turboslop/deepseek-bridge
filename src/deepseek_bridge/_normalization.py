"""Pure normalization utilities for DeepSeek API payloads.

This module contains stateless, side-effect-free functions for normalizing
request fields, message content, tool calls, and tool choices. All functions
here operate on plain dicts/values without any external dependencies beyond
the standard library.
"""

from __future__ import annotations

import json
import re
from typing import Any

# ── Field whitelists ───────────────────────────────────────────

MESSAGE_FIELDS: set[str] = {
    "role",
    "content",
    "name",
    "tool_call_id",
    "tool_calls",
    "reasoning_content",
    "prefix",
}

ROLE_MESSAGE_FIELDS: dict[str, set[str]] = {
    "system": {"role", "content", "name"},
    "user": {"role", "content", "name"},
    "assistant": {
        "role",
        "content",
        "name",
        "tool_calls",
        "reasoning_content",
        "prefix",
    },
    "tool": {"role", "content", "tool_call_id"},
}

EFFORT_ALIASES: dict[str, str] = {
    "low": "high",
    "medium": "high",
    "high": "high",
    "max": "max",
    "xhigh": "max",
}

CURSOR_THINKING_BLOCK_RE: re.Pattern[str] = re.compile(
    r"""
    (?:
        <(?:think|thinking)\b[^>]*>[\s\S]*?(?:</(?:think|thinking)>|\Z)
        |
        <details\b[^>]*>\s*
        <summary\b[^>]*>\s*Thinking\s*</summary>
        [\s\S]*?(?:</details>|\Z)
    )\s*
    """,
    re.IGNORECASE | re.VERBOSE,
)


# ── Pure normalization functions ────────────────────────────────


def normalize_reasoning_effort(value: Any) -> str:
    if not isinstance(value, str):
        return "high"
    return EFFORT_ALIASES.get(value.strip().lower(), "high")


def extract_text_content(content: Any) -> str | None:
    if content is None or isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
                continue
            if not isinstance(item, dict):
                parts.append(str(item))
                continue
            item_type = item.get("type")
            text = item.get("text") or item.get("content")
            if (
                item_type in {"text", "input_text"}
                and isinstance(text, str)
                or isinstance(text, str)
            ):
                parts.append(text)
        return "\n".join(part for part in parts if part) or ""
    if isinstance(content, (dict, tuple)):
        return json.dumps(content, ensure_ascii=False, sort_keys=True)
    return str(content)


def strip_cursor_thinking_blocks(content: str) -> str:
    return CURSOR_THINKING_BLOCK_RE.sub("", content).lstrip("\r\n")


def normalize_tool_call(tool_call: Any) -> dict[str, Any]:
    if not isinstance(tool_call, dict):
        tool_call = {}
    function = tool_call.get("function") or {}
    if not isinstance(function, dict):
        function = {}

    arguments = function.get("arguments", "")
    if not isinstance(arguments, str):
        arguments = json.dumps(arguments, ensure_ascii=False, sort_keys=True)

    normalized: dict[str, Any] = {
        "id": str(tool_call.get("id") or ""),
        "type": tool_call.get("type") or "function",
        "function": {
            "name": str(function.get("name") or ""),
            "arguments": arguments,
        },
    }
    if not normalized["id"]:
        normalized.pop("id")
    return normalized


def normalize_tool(tool: Any) -> dict[str, Any]:
    if not isinstance(tool, dict):
        return {
            "type": "function",
            "function": {"name": "", "description": "", "parameters": {}},
        }
    normalized = dict(tool)
    normalized["type"] = normalized.get("type") or "function"
    function = normalized.get("function")
    if isinstance(function, dict):
        normalized["function"] = function
    return normalized


def legacy_function_to_tool(function: Any) -> dict[str, Any]:
    if not isinstance(function, dict):
        function = {}
    return {"type": "function", "function": function}


def convert_function_call(function_call: Any) -> Any:
    if isinstance(function_call, str):
        if function_call in {"auto", "none", "required"}:
            return function_call
        return None
    if isinstance(function_call, dict) and function_call.get("name"):
        return {
            "type": "function",
            "function": {"name": str(function_call["name"])},
        }
    return None


def normalize_tool_choice(tool_choice: Any) -> Any:
    if isinstance(tool_choice, str):
        if tool_choice in {"auto", "none", "required"}:
            return tool_choice
        return None
    if isinstance(tool_choice, dict):
        if tool_choice.get("type") == "function":
            function = tool_choice.get("function")
            if isinstance(function, dict) and function.get("name"):
                return {
                    "type": "function",
                    "function": {"name": str(function["name"])},
                }
            # Handle Responses API format: {"type": "function", "name": "my_func"}
            name = tool_choice.get("name")
            if isinstance(name, str) and name:
                return {
                    "type": "function",
                    "function": {"name": name},
                }
        return tool_choice
    return tool_choice
