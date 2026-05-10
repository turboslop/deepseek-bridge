from __future__ import annotations

from typing import Any


def detect_responses_payload(payload: dict[str, Any]) -> bool:
    """Return True if the payload looks like an OpenAI Responses API request.

    Detection is conservative to avoid false positives:
    - Must NOT have "messages" field (Chat Completions format)
    - Must have "input" field (Responses API) OR "instructions" field
    """
    if not isinstance(payload, dict):
        return False
    has_input = "input" in payload
    has_instructions = "instructions" in payload
    has_messages = "messages" in payload
    # If it has messages field, it's a Chat Completions payload, not Responses
    if has_messages:
        return False
    return has_input or has_instructions


def convert_responses_to_chat(payload: dict[str, Any]) -> dict[str, Any]:
    """Convert a Responses API payload to Chat Completions format.

    Returns a new dict. Non-Responses payloads (missing ``input`` field)
    are returned unchanged.
    """
    if not isinstance(payload, dict):
        return payload

    # If there is no input field, return a shallow copy unchanged.
    # This handles the edge case where detect_responses_payload returned
    # True due to "instructions" alone but the payload isn't actually
    # a valid Responses API shape.
    if "input" not in payload:
        return dict(payload)

    result: dict[str, Any] = {}

    # --- 1. Convert input items to messages ---
    messages: list[dict[str, Any]] = []
    input_items = payload.get("input", [])
    if isinstance(input_items, list):
        for item in input_items:
            if not isinstance(item, dict):
                continue
            msg = _convert_input_item(item)
            if msg is not None:
                messages.append(msg)
    result["messages"] = messages

    # --- 2. Prepend instructions as system message ---
    instructions = payload.get("instructions")
    if isinstance(instructions, str) and instructions.strip():
        result["messages"].insert(0, {"role": "system", "content": instructions})

    # --- 3. Pass through standard Chat Completions fields ---
    for key in (
        "model",
        "stream",
        "temperature",
        "top_p",
        "max_tokens",
        "stream_options",
    ):
        if key in payload:
            result[key] = payload[key]

    # --- 4. Convert tools ---
    if "tools" in payload and isinstance(payload["tools"], list):
        result["tools"] = _convert_tools(payload["tools"])

    # --- 5. Reasoning dict -> reasoning_effort ---
    reasoning = payload.get("reasoning")
    if isinstance(reasoning, dict):
        effort = reasoning.get("effort")
        if isinstance(effort, str):
            result["reasoning_effort"] = effort

    # Fields that are intentionally NOT copied (Responses-API-only):
    #   "input"        - consumed above, replaced by "messages"
    #   "instructions" - consumed above, prepended as system message
    #   "reasoning"    - consumed above, collapsed to reasoning_effort
    #   "include"      - Responses-API-only, dropped
    #   "previous_response_id" - Responses-API-only, dropped
    #   "store"        - Responses-API-only, dropped
    #   "text"         - Responses-API-specific output formatting, dropped
    #   "tool_choice"  - keep aligned with Chat Completions semantics when present
    if "tool_choice" in payload:
        result["tool_choice"] = payload["tool_choice"]

    return result


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _convert_input_item(item: dict[str, Any]) -> dict[str, Any] | None:
    """Convert a single Responses API ``input`` item to a Chat message dict.

    Returns ``None`` when the item cannot be meaningfully converted.
    """
    item_type = item.get("type")

    # --- function_call_output -> tool message ---
    if item_type == "function_call_output":
        return {
            "role": "tool",
            "tool_call_id": str(item.get("call_id", "")),
            "content": str(item.get("output", "")),
        }

    # --- typed message (type="message", role="X", content="...") ---
    if item_type == "message":
        role = str(item.get("role", "user"))
        content = item.get("content")
        result: dict[str, Any] = {
            "role": role,
            "content": _stringify_content(content),
        }
        if "reasoning_content" in item and isinstance(item["reasoning_content"], str):
            result["reasoning_content"] = item["reasoning_content"]
        return result

    # --- role-based items (simple format) ---
    item_role = item.get("role")
    if item_role in ("system", "user", "assistant"):
        role = str(item_role)
        content = item.get("content")
        result = {
            "role": role,
            "content": _stringify_content(content),
        }
        if role == "assistant" and "reasoning_content" in item and isinstance(item["reasoning_content"], str):
            result["reasoning_content"] = item["reasoning_content"]
        return result

    # --- fallback: try to extract something useful ---
    content = item.get("content")
    if content is not None:
        return {
            "role": str(item.get("role", "user")),
            "content": _stringify_content(content),
        }

    return None


def _stringify_content(content: Any) -> str:
    """Convert content to a string, handling text arrays properly."""
    from ._normalization import extract_text_content

    flattened = extract_text_content(content)
    if flattened is not None:
        return flattened
    return ""


def _convert_tools(tools: list[Any]) -> list[dict[str, Any]]:
    """Convert a list of Responses-API tool definitions to Chat Completions format."""
    converted: list[dict[str, Any]] = []
    for tool in tools:
        if not isinstance(tool, dict):
            continue
        result = _convert_tool(tool)
        if result is not None:
            converted.append(result)
    return converted


def _convert_tool(tool: dict[str, Any]) -> dict[str, Any] | None:
    """Convert a single tool definition. Returns ``None`` if unhandled."""
    tool_type = tool.get("type")

    # --- Standard function tool ---
    if tool_type == "function":
        # Already has nested "function" key -> pass through
        if "function" in tool and isinstance(tool["function"], dict):
            return dict(tool)
        # Flat format: name/description/parameters at top level
        return {
            "type": "function",
            "function": {
                "name": str(tool.get("name", "")),
                "description": str(tool.get("description", "")),
                "parameters": (
                    tool["parameters"]
                    if isinstance(tool.get("parameters"), dict)
                    else {}
                ),
            },
        }

    # --- Custom tool types (e.g. Cursor's "custom" with input_schema) ---
    if tool_type == "custom":
        name = tool.get("name", "")
        if name:
            input_schema = tool.get("input_schema", {})
            parameters: dict[str, Any]
            if isinstance(input_schema, dict) and input_schema:
                parameters = input_schema
            else:
                parameters = {
                    "type": "object",
                    "properties": {"input": {"type": "string"}},
                    "required": ["input"],
                }
            return {
                "type": "function",
                "function": {
                    "name": str(name),
                    "description": str(tool.get("description", f"Custom tool: {name}")),
                    "parameters": parameters,
                },
            }

    # Unhandled tool type -> skip
    return None
