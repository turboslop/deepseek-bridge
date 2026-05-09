from __future__ import annotations

import html
import time
from typing import Any

from ._sse import (
    COLLAPSIBLE_THINKING_BLOCK_END,
    COLLAPSIBLE_THINKING_BLOCK_START,
    THINKING_BLOCK_END,
    THINKING_BLOCK_START,
)
from ..logging import INTERNAL_LOG


class CursorReasoningDisplayAdapter:
    def __init__(self, collapsible: bool = True) -> None:
        self._open_choices: set[int] = set()
        self._last_chunk_metadata: dict[str, Any] = {}
        self._block_start = (
            COLLAPSIBLE_THINKING_BLOCK_START if collapsible else THINKING_BLOCK_START
        )
        self._block_end = (
            COLLAPSIBLE_THINKING_BLOCK_END if collapsible else THINKING_BLOCK_END
        )

    def rewrite_chunk(self, chunk: dict[str, Any]) -> None:
        self._remember_chunk_metadata(chunk)
        choices = chunk.get("choices")
        if not isinstance(choices, list):
            return

        for raw_choice in choices:
            if not isinstance(raw_choice, dict):
                continue
            index = int(raw_choice.get("index") or 0)
            delta = raw_choice.get("delta")
            if not isinstance(delta, dict):
                delta = {}
                raw_choice["delta"] = delta

            mirrored_parts: list[str] = []
            reasoning_content = delta.get("reasoning_content")
            if isinstance(reasoning_content, str) and reasoning_content:
                if index not in self._open_choices:
                    INTERNAL_LOG.debug(
                        "streaming.display: opened thinking block for choice[%s]",
                        index,
                    )
                    mirrored_parts.append(self._block_start)
                    self._open_choices.add(index)
                mirrored_parts.append(html.escape(reasoning_content))

            existing_content = delta.get("content")
            should_close = index in self._open_choices and (
                bool(existing_content)
                or bool(delta.get("tool_calls"))
                or raw_choice.get("finish_reason") is not None
            )
            if should_close:
                INTERNAL_LOG.debug(
                    "streaming.display: closed thinking block for choice[%s]",
                    index,
                )
                mirrored_parts.append(self._block_end)
                self._open_choices.discard(index)

            if not mirrored_parts:
                continue
            if isinstance(existing_content, str):
                mirrored_parts.append(existing_content)
            delta["content"] = "".join(mirrored_parts)

    def flush_chunk(self, model: str) -> dict[str, Any] | None:
        if not self._open_choices:
            return None

        choices = [
            {
                "index": index,
                "delta": {"content": self._block_end},
                "finish_reason": None,
            }
            for index in sorted(self._open_choices)
        ]
        self._open_choices.clear()

        chunk: dict[str, Any] = {
            "id": self._last_chunk_metadata.get("id", "chatcmpl-reasoning-close"),
            "object": self._last_chunk_metadata.get("object", "chat.completion.chunk"),
            "created": self._last_chunk_metadata.get("created", int(time.time())),
            "model": model,
            "system_fingerprint": "fp_deepseek_bridge",
            "choices": choices,
        }
        return chunk

    def _remember_chunk_metadata(self, chunk: dict[str, Any]) -> None:
        metadata = {
            key: chunk[key] for key in ("id", "object", "created") if key in chunk
        }
        if metadata:
            self._last_chunk_metadata.update(metadata)
