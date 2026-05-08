from __future__ import annotations

import html
import http.client
import json
import threading
import time
import types
import uuid
from dataclasses import dataclass
from typing import Any

from .config import ProxyConfig
from .logging import LOG

# ── Constants ────────────────────────────────────────────────

SYSTEM_FINGERPRINT = "fp_deepseek_bridge"

MODEL_CREATED_TIMESTAMPS: dict[str, int] = {
    "deepseek-v4-pro": 1735689600,
    "deepseek-v4-flash": 1735689600,
}

# Recovery notice constants (shared between helpers and transform)
RECOVERY_NOTICE_TEXT = "[deepseek-bridge] Refreshed reasoning_content history."
RECOVERY_NOTICE_CONTENT = f"{RECOVERY_NOTICE_TEXT}\n\n"
RECOVERY_SYSTEM_CONTENT = (
    "deepseek-bridge recovered this request because older DeepSeek "
    "thinking-mode tool-call reasoning_content was unavailable. Older "
    "unrecoverable tool-call history was omitted; continue using only the "
    "remaining recovered context."
)


# ── Request ID ───────────────────────────────────────────────


def _generate_request_id() -> str:
    return f"dcp-{uuid.uuid4().hex[:24]}"


# ── Shutdown ─────────────────────────────────────────────────

_shutdown_requested = threading.Event()


def _handle_shutdown_signal(signum: int, frame: types.FrameType | None) -> None:
    LOG.info("received signal %s, initiating graceful shutdown", signum)
    _shutdown_requested.set()


# ── Reasoning display blocks ─────────────────────────────────

THINKING_BLOCK_START = "<think>\n"
THINKING_BLOCK_END = "\n</think>\n\n"
COLLAPSIBLE_THINKING_BLOCK_START = "<details>\n<summary>Thinking</summary>\n\n"
COLLAPSIBLE_THINKING_BLOCK_END = "\n</details>\n\n"


def fold_reasoning_into_content(
    response_payload: dict[str, Any],
    collapsible: bool,
) -> None:
    """Inject reasoning_content into the visible content as HTML/Markdown blocks.

    When ``collapsible`` is True, reasoning is wrapped in a ``<details>`` tag
    that AI coding client UIs can render as a collapsible section.  Otherwise
    a plain ``<think>`` block is emitted.
    """
    block_start = (
        COLLAPSIBLE_THINKING_BLOCK_START if collapsible else THINKING_BLOCK_START
    )
    block_end = COLLAPSIBLE_THINKING_BLOCK_END if collapsible else THINKING_BLOCK_END
    choices = response_payload.get("choices")
    if not isinstance(choices, list):
        return
    for choice in choices:
        if not isinstance(choice, dict):
            continue
        message = choice.get("message")
        if not isinstance(message, dict):
            continue
        reasoning = message.get("reasoning_content")
        if not isinstance(reasoning, str) or not reasoning:
            continue
        content = message.get("content")
        message["content"] = (
            block_start
            + html.escape(reasoning)
            + block_end
            + (content if isinstance(content, str) else "")
        )


# ── Error helpers ────────────────────────────────────────────


class RequestBodyTooLargeError(ValueError):
    pass


def _error_body(
    message: str,
    error_type: str,
    code: str | None = None,
) -> dict[str, Any]:
    err: dict[str, Any] = {"message": str(message)}
    err["type"] = error_type
    if code:
        err["code"] = code
    err["param"] = None
    return {"error": err}


@dataclass
class ProxyResponseResult:
    sent: bool
    usage: dict[str, Any] | None = None


# ── Timing ───────────────────────────────────────────────────


def elapsed_ms(started: float) -> int:
    return round((time.monotonic() - started) * 1000)


# ── Logging helpers ──────────────────────────────────────────


def _truncate_message_content(payload: Any, max_len: int = 200) -> Any:
    """Truncate verbose fields in log payloads: message content, tool descriptions,
    tool call arguments, and any long string values to keep logs readable."""
    if not isinstance(payload, dict):
        return payload
    result = dict(payload)
    # Truncate messages array
    if "messages" in result and isinstance(result["messages"], list):
        truncated = []
        for m in result["messages"]:
            if not isinstance(m, dict):
                truncated.append(m)
                continue
            m2 = dict(m)
            content = m2.get("content")
            if isinstance(content, str) and len(content) > max_len:
                m2["content"] = content[:max_len] + "..."
            elif isinstance(content, list):
                m2["content"] = "[multimodal content array]"
            # Truncate tool_call arguments
            tool_calls = m2.get("tool_calls")
            if isinstance(tool_calls, list):
                tc2 = []
                for tc in tool_calls:
                    if isinstance(tc, dict):
                        t = dict(tc)
                        fn = t.get("function", {})
                        if isinstance(fn, dict):
                            args = fn.get("arguments", "")
                            if isinstance(args, str) and len(args) > max_len:
                                fn = dict(fn)
                                fn["arguments"] = args[:max_len] + "..."
                                t["function"] = fn
                        tc2.append(t)
                    else:
                        tc2.append(tc)
                m2["tool_calls"] = tc2
            truncated.append(m2)
        result["messages"] = truncated
    # Truncate tools array (massive descriptions in function schemas)
    if "tools" in result and isinstance(result["tools"], list):
        tools2 = []
        for tool in result["tools"]:
            if isinstance(tool, dict):
                t = dict(tool)
                fn = t.get("function", {})
                if isinstance(fn, dict):
                    fn2 = dict(fn)
                    desc = fn2.get("description", "")
                    if isinstance(desc, str) and len(desc) > max_len:
                        fn2["description"] = desc[:max_len] + f"... [{len(desc)} chars]"
                    # Truncate long parameter descriptions too
                    params = fn2.get("parameters", {})
                    if isinstance(params, dict) and isinstance(
                        params.get("properties"), dict
                    ):
                        props2 = {}
                        for pk, pv in params["properties"].items():
                            if isinstance(pv, dict):
                                pv2 = dict(pv)
                                pd = pv2.get("description", "")
                                if isinstance(pd, str) and len(pd) > max_len:
                                    pv2["description"] = pd[:max_len] + "..."
                                props2[pk] = pv2
                            else:
                                props2[pk] = pv
                        fn2["parameters"] = {**params, "properties": props2}
                    t["function"] = fn2
                tools2.append(t)
            else:
                tools2.append(tool)
        result["tools"] = tools2
    return result


def log_json(label: str, payload: Any) -> None:
    payload = _truncate_message_content(payload)
    LOG.info(
        "%s:\n%s",
        label,
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
    )


def log_bytes(label: str, body: bytes) -> None:
    try:
        payload = json.loads(body.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        LOG.info("%s:\n%s", label, body.decode("utf-8", errors="replace"))
        return
    log_json(label, payload)


def usage_from_body(body: bytes) -> dict[str, Any] | None:
    try:
        payload = json.loads(body.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None
    if isinstance(payload, dict):
        usage = payload.get("usage")
        if isinstance(usage, dict):
            return usage
    return None


def log_cursor_request(
    payload: dict[str, Any],
    config: ProxyConfig,
) -> None:
    model = str(payload.get("model") or config.upstream_model)
    LOG.info(
        "┌ request model=%s effort=%s messages=%s",
        model,
        config.reasoning_effort,
        format_count(message_count(payload)),
    )


def log_context_summary(prepared: Any) -> None:
    status = context_status(prepared)
    if status == "ok":
        LOG.info(
            "├ context status=ok reasoning_context=%s",
            format_count(prepared.patched_reasoning_messages),
        )
        return
    LOG.info(
        "├ context status=%s missing=%s recovered=%s dropped=%s",
        status,
        format_count(prepared.missing_reasoning_messages),
        format_count(prepared.recovered_reasoning_messages),
        format_count(prepared.recovery_dropped_messages),
    )


def log_send_summary(prepared: Any) -> None:
    LOG.info(
        "├ send    user_msgs=%s messages=%s tools=%s reasoning_content=%s",
        format_count(user_message_count(prepared.payload)),
        format_count(message_count(prepared.payload)),
        format_count(tool_count(prepared.payload)),
        format_count(reasoning_content_count(prepared.payload)),
    )


def log_stats_summary(
    usage: dict[str, Any] | None,
    elapsed_ms: int | None = None,
) -> None:
    elapsed_str = format_count(elapsed_ms) + "ms" if elapsed_ms is not None else "?"
    tokens_per_sec = ""
    if elapsed_ms and isinstance(usage, dict):
        total_tokens = int_or_zero(usage.get("total_tokens"))
        if total_tokens and elapsed_ms > 0:
            tokens_per_sec = f" {total_tokens / (elapsed_ms / 1000):.1f} tok/s"
    LOG.info(
        "└ stats   prompt=%s output=%s reasoning=%s cache_hit=%s elapsed=%s%s",
        format_usage_count(usage, "prompt_tokens"),
        format_usage_count(usage, "completion_tokens"),
        format_count(reasoning_token_count(usage)),
        cache_hit_rate(usage),
        elapsed_str,
        tokens_per_sec,
    )


# ── Request inspection helpers ───────────────────────────────


def context_status(prepared: Any) -> str:
    parts: list[str] = []
    if prepared.patched_reasoning_messages:
        parts.append(f"patched={prepared.patched_reasoning_messages}")
    if prepared.recovered_reasoning_messages:
        parts.append(f"recovered={prepared.recovered_reasoning_messages}")
    if prepared.recovery_dropped_messages:
        parts.append(f"dropped={prepared.recovery_dropped_messages}")
    if prepared.missing_reasoning_messages:
        parts.append(f"missing={prepared.missing_reasoning_messages}")
    if not parts:
        return "ok"
    return " ".join(parts)


def message_count(payload: dict[str, Any]) -> int:
    messages = payload.get("messages")
    return len(messages) if isinstance(messages, list) else 0


def tool_count(payload: dict[str, Any]) -> int:
    tools = payload.get("tools")
    return len(tools) if isinstance(tools, list) else 0


def user_message_count(payload: dict[str, Any]) -> int:
    messages = payload.get("messages")
    if not isinstance(messages, list):
        return 0
    return sum(
        1
        for message in messages
        if isinstance(message, dict) and message.get("role") == "user"
    )


def reasoning_content_count(payload: dict[str, Any]) -> int:
    messages = payload.get("messages")
    if not isinstance(messages, list):
        return 0
    return sum(
        1
        for message in messages
        if isinstance(message, dict)
        and message.get("role") == "assistant"
        and isinstance(message.get("reasoning_content"), str)
    )


def format_usage_count(usage: dict[str, Any] | None, key: str) -> str:
    if not isinstance(usage, dict):
        return "?"
    return format_count(usage.get(key))


def reasoning_token_count(usage: dict[str, Any] | None) -> Any:
    if not isinstance(usage, dict):
        return None
    details = usage.get("completion_tokens_details")
    if not isinstance(details, dict):
        return None
    return details.get("reasoning_tokens")


def cache_hit_rate(usage: dict[str, Any] | None) -> str:
    if not isinstance(usage, dict):
        return "?"
    hit_tokens = usage.get("prompt_cache_hit_tokens")
    miss_tokens = usage.get("prompt_cache_miss_tokens")
    if hit_tokens is None and miss_tokens is None:
        return "?"
    hit = int_or_zero(hit_tokens)
    miss = int_or_zero(miss_tokens)
    total = hit + miss
    if not total:
        return "?"
    return f"{hit / total:.1%}"


def format_count(value: Any) -> str:
    if value is None:
        return "?"
    try:
        return f"{int(value):,}"
    except (TypeError, ValueError):
        return str(value)


def int_or_zero(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


# ── SSE helpers ──────────────────────────────────────────────


def sse_data(payload: dict[str, Any]) -> bytes:
    return (
        b"data: "
        + json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        + b"\n\n"
    )


def inject_recovery_notice(chunk: dict[str, Any], notice: str) -> bool:
    choices = chunk.get("choices")
    if not isinstance(choices, list):
        return False
    for choice in choices:
        if not isinstance(choice, dict):
            continue
        delta = choice.get("delta")
        if not isinstance(delta, dict):
            continue
        if "content" not in delta and not delta.get("tool_calls"):
            continue
        existing_content = delta.get("content")
        delta["content"] = notice + (
            existing_content if isinstance(existing_content, str) else ""
        )
        return True
    return False


def recovery_notice_chunk(
    model: str,
    notice: str = RECOVERY_NOTICE_CONTENT,
) -> dict[str, Any]:
    return {
        "id": "chatcmpl-deepseek-bridge-recovery",
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": model,
        "system_fingerprint": SYSTEM_FINGERPRINT,
        "choices": [
            {
                "index": 0,
                "delta": {"content": notice},
                "finish_reason": None,
            }
        ],
    }


def summarize_chat_payload(payload: dict[str, Any]) -> str:
    messages = payload.get("messages")
    tools = payload.get("tools")
    functions = payload.get("functions")
    return (
        f"model={payload.get('model')!r} "
        f"stream={bool(payload.get('stream'))} "
        f"messages={len(messages) if isinstance(messages, list) else 0} "
        f"tools={len(tools) if isinstance(tools, list) else 0} "
        f"functions={len(functions) if isinstance(functions, list) else 0} "
        f"tool_choice={payload.get('tool_choice')!r}"
    )


def read_response_body(response: Any) -> bytes:
    """Read the full body from a urllib3 response.

    urllib3 auto-decompresses gzip/deflate by default.  For preloaded
    responses (preload_content=True) the cached ``response.data`` is used;
    for streaming responses ``response.read()`` reads from the socket.
    """
    try:
        if hasattr(response, "data") and response.data is not None:
            return response.data
        return response.read()
    except (TimeoutError, OSError, http.client.IncompleteRead) as exc:
        raise ValueError(f"failed to read upstream response body: {exc}") from exc
