from __future__ import annotations

import html
import orjson
import time
from typing import Any

# ── Fingerprint ───────────────────────────────────────────────

SYSTEM_FINGERPRINT = "fp_deepseek_bridge"

# ── Reasoning display blocks ──────────────────────────────────

THINKING_BLOCK_START = "<think>\n"
THINKING_BLOCK_END = "\n</think>\n\n"
COLLAPSIBLE_THINKING_BLOCK_START = "<details>\n<summary>Thinking</summary>\n\n"
COLLAPSIBLE_THINKING_BLOCK_END = "\n</details>\n\n"


def fold_reasoning_into_content(
    response_payload: dict[str, Any],
    collapsible: bool,
) -> None:
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


# ── SSE helpers ───────────────────────────────────────────────


def sse_data(payload: dict[str, Any]) -> bytes:
    return b"data: " + orjson.dumps(payload) + b"\n\n"


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
    notice: str = "",
) -> dict[str, Any]:
    from ..logging import RECOVERY_NOTICE_CONTENT

    used_notice = notice if notice else RECOVERY_NOTICE_CONTENT
    return {
        "id": "chatcmpl-deepseek-bridge-recovery",
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": model,
        "system_fingerprint": SYSTEM_FINGERPRINT,
        "choices": [
            {
                "index": 0,
                "delta": {"content": used_notice},
                "finish_reason": None,
            }
        ],
    }
