from __future__ import annotations

import html
from dataclasses import dataclass, field
from typing import Any

from ..logging import LOG
from ..reasoning_store import ReasoningStore


THINKING_BLOCK_START = "<think>\n"
THINKING_BLOCK_END = "\n</think>\n\n"
COLLAPSIBLE_THINKING_BLOCK_START = "<details>\n<summary>Thinking</summary>\n\n"
COLLAPSIBLE_THINKING_BLOCK_END = "\n</details>\n\n"


@dataclass
class StreamingChoice:
    role: str = "assistant"
    content: str = ""
    reasoning_content: str = ""
    has_reasoning_content: bool = False
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    finish_reason: str | None = None

    def to_message(self) -> dict[str, Any]:
        message: dict[str, Any] = {
            "role": self.role,
            "content": self.content,
        }
        if self.has_reasoning_content:
            message["reasoning_content"] = self.reasoning_content
        if self.tool_calls:
            message["tool_calls"] = self.tool_calls
        return message


class StreamAccumulator:
    def __init__(self) -> None:
        self.choices: dict[int, StreamingChoice] = {}
        self._stored_choices: dict[tuple[int, str], str] = {}

    def ingest_chunk(self, chunk: dict[str, Any]) -> None:
        choices = chunk.get("choices")
        if not isinstance(choices, list):
            return

        for raw_choice in choices:
            if not isinstance(raw_choice, dict):
                continue
            index = int(raw_choice.get("index") or 0)
            choice = self.choices.setdefault(index, StreamingChoice())
            finish_reason = raw_choice.get("finish_reason")
            if isinstance(finish_reason, str):
                choice.finish_reason = finish_reason

            delta = raw_choice.get("delta")
            if not isinstance(delta, dict):
                continue

            role = delta.get("role")
            if isinstance(role, str) and role:
                choice.role = role

            content = delta.get("content")
            if isinstance(content, str):
                choice.content += content

            delta_type = "content" if delta.get("content") else ""
            reasoning_content = delta.get("reasoning_content")
            if isinstance(reasoning_content, str):
                choice.has_reasoning_content = True
                choice.reasoning_content += reasoning_content
                delta_type = "reasoning"

            if delta.get("tool_calls"):
                delta_type = "tool_call"

            LOG.debug(
                "streaming.accumulator: chunk[%s], delta_type=%s",
                index,
                delta_type,
            )

            self._merge_tool_call_deltas(choice, delta.get("tool_calls"))

    def store_reasoning(
        self,
        store: ReasoningStore,
        scope: str,
        cache_namespace: str = "",
        prior_messages: list[dict[str, Any]] | None = None,
    ) -> int:
        stored = 0
        for index, choice in self.choices.items():
            stored += self._store_choice(
                index, choice, store, scope, "final", cache_namespace, prior_messages
            )
        return stored

    def store_finished_reasoning(
        self,
        store: ReasoningStore,
        scope: str,
        cache_namespace: str = "",
        prior_messages: list[dict[str, Any]] | None = None,
    ) -> int:
        stored = 0
        for index, choice in self.choices.items():
            if choice.finish_reason is not None:
                stored += self._store_choice(
                    index,
                    choice,
                    store,
                    scope,
                    "final",
                    cache_namespace,
                    prior_messages,
                )
        return stored

    def store_ready_reasoning(
        self,
        store: ReasoningStore,
        scope: str,
        cache_namespace: str = "",
        prior_messages: list[dict[str, Any]] | None = None,
    ) -> int:
        stored = 0
        for index, choice in self.choices.items():
            if choice.finish_reason is not None:
                stored += self._store_choice(
                    index,
                    choice,
                    store,
                    scope,
                    "final",
                    cache_namespace,
                    prior_messages,
                )
            elif self._has_identified_tool_calls(choice):
                stored += self._store_choice(
                    index,
                    choice,
                    store,
                    scope,
                    "tool_call",
                    cache_namespace,
                    prior_messages,
                )
        return stored

    def messages(self) -> list[dict[str, Any]]:
        return [choice.to_message() for _, choice in sorted(self.choices.items())]

    def _merge_tool_call_deltas(self, choice: StreamingChoice, deltas: Any) -> None:
        if not isinstance(deltas, list):
            return

        for raw_delta in deltas:
            if not isinstance(raw_delta, dict):
                continue
            index = raw_delta.get("index")
            if not isinstance(index, int):
                index = len(choice.tool_calls)
            while len(choice.tool_calls) <= index:
                choice.tool_calls.append(
                    {"type": "function", "function": {"name": "", "arguments": ""}}
                )

            tool_call = choice.tool_calls[index]
            if raw_delta.get("id"):
                tool_call["id"] = raw_delta["id"]
            if raw_delta.get("type"):
                tool_call["type"] = raw_delta["type"]
            LOG.debug(
                "streaming.accumulator: tool_call[%s] id=%s, name=%s",
                index,
                tool_call.get("id", ""),
                (raw_delta.get("function") or {}).get("name", ""),
            )

            function_delta = raw_delta.get("function")
            if not isinstance(function_delta, dict):
                continue
            function = tool_call.setdefault("function", {"name": "", "arguments": ""})
            if function_delta.get("name"):
                function["name"] = str(function_delta["name"])
            if (
                "arguments" in function_delta
                and function_delta["arguments"] is not None
            ):
                function["arguments"] = (function.get("arguments") or "") + str(
                    function_delta["arguments"]
                )

    def _store_choice(
        self,
        index: int,
        choice: StreamingChoice,
        store: ReasoningStore,
        scope: str,
        stage: str = "final",
        cache_namespace: str = "",
        prior_messages: list[dict[str, Any]] | None = None,
    ) -> int:
        stage_rank = {"tool_call": 1, "final": 2}
        storage_key = (index, scope)
        previous_stage = self._stored_choices.get(storage_key)
        if stage_rank.get(previous_stage or "", 0) >= stage_rank.get(stage, 0):
            return 0
        if choice.finish_reason is not None:
            LOG.debug(
                "streaming.accumulator: finish_reason=%s, finalizing",
                choice.finish_reason,
            )
        stored = store.store_assistant_message(
            choice.to_message(),
            scope,
            cache_namespace,
            prior_messages,
        )
        if stored:
            self._stored_choices[storage_key] = stage
        return stored

    def _has_identified_tool_calls(self, choice: StreamingChoice) -> bool:
        if not choice.has_reasoning_content or not choice.tool_calls:
            return False
        identified = all(bool(tool_call.get("id")) for tool_call in choice.tool_calls)
        if identified:
            LOG.debug(
                "streaming.accumulator: all tool_call IDs identified, caching reasoning"
            )
        return identified


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
