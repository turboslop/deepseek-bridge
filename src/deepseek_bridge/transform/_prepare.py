from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .._normalization import (
    convert_function_call,
    legacy_function_to_tool,
    normalize_reasoning_effort,
    normalize_tool,
    normalize_tool_choice,
)
from ..config import ProxyConfig
from ..logging import INTERNAL_LOG, LOG
from ..reasoning_store import ReasoningStoreProtocol, conversation_scope
from ._cache import (
    reasoning_cache_namespace,
    response_recording_contexts,
    upstream_model_for,
)
from ._normalize import normalize_messages
from ._recovery import (
    _should_show_recovery_notice,
    active_messages_from_recovery_boundary,
    recover_messages_from_missing_reasoning,
    strip_recovery_notice_for_upstream,
)

SUPPORTED_REQUEST_FIELDS = {
    "model",
    "messages",
    "stream",
    "stream_options",
    "max_tokens",
    "response_format",
    "stop",
    "tools",
    "tool_choice",
    "thinking",
    "temperature",
    "top_p",
    "logprobs",
    "top_logprobs",
    "user_id",
    "user",
    "seed",
    "n",
    "logit_bias",
}

RUNTIME_OVERRIDE_FIELDS = {
    "reasoning_effort",
}

_MISSING = object()
_THINKING_TYPES = {"enabled", "disabled"}


@dataclass(frozen=True)
class PreparedRequest:
    payload: dict[str, Any]
    original_model: str
    upstream_model: str
    cache_namespace: str
    patched_reasoning_messages: int
    missing_reasoning_messages: int
    recovered_reasoning_messages: int = 0
    recovery_dropped_messages: int = 0
    recovery_notice: str | None = None
    record_response_scope: str | None = None
    record_response_messages: list[dict[str, Any]] = field(default_factory=list)
    record_response_contexts: list[tuple[str, list[dict[str, Any]]]] = field(
        default_factory=list
    )
    reasoning_diagnostics: list[dict[str, Any]] = field(default_factory=list)
    recovery_steps: list[dict[str, Any]] = field(default_factory=list)
    continued_recovery_boundary: bool = False
    retired_prefix_messages: int = 0


def _normalize_thinking_type(value: Any, fallback: str) -> str:
    if isinstance(value, str):
        thinking_type = value.strip().lower()
        if thinking_type in _THINKING_TYPES:
            return thinking_type
    if fallback in _THINKING_TYPES:
        return fallback
    return "enabled"


def _effective_thinking_payload(
    payload: dict[str, Any], config: ProxyConfig
) -> dict[str, Any]:
    request_thinking = payload.get("thinking", _MISSING)
    request_effort = payload.get("reasoning_effort", _MISSING)

    thinking_type = config.thinking
    reasoning_effort: Any = config.reasoning_effort

    if isinstance(request_thinking, dict):
        thinking_type = _normalize_thinking_type(
            request_thinking.get("type", _MISSING),
            config.thinking,
        )
        nested_effort = request_thinking.get("reasoning_effort", _MISSING)
        if nested_effort is not _MISSING:
            reasoning_effort = nested_effort
        elif request_effort is not _MISSING:
            reasoning_effort = request_effort
    elif request_thinking is not _MISSING:
        thinking_type = _normalize_thinking_type(
            request_thinking,
            config.thinking,
        )
        if request_effort is not _MISSING:
            reasoning_effort = request_effort
    elif request_effort is not _MISSING:
        thinking_type = "enabled"
        reasoning_effort = request_effort

    thinking = {"type": thinking_type}
    if thinking_type == "enabled":
        thinking["reasoning_effort"] = normalize_reasoning_effort(
            reasoning_effort
        )
    return thinking


def prepare_upstream_request(
    payload: dict[str, Any],
    config: ProxyConfig,
    store: ReasoningStoreProtocol | None,
    authorization: str | None = None,
) -> PreparedRequest:
    original_model = str(payload.get("model") or config.upstream_model)
    upstream_model = upstream_model_for(original_model, config)
    messages_raw = payload.get("messages")
    INTERNAL_LOG.debug(
        "transform.prepare: starting request normalization, model=%s, "
        "messages=%s",
        upstream_model,
        len(messages_raw) if isinstance(messages_raw, list) else 0,
    )

    prepared = {
        key: value
        for key, value in payload.items()
        if key in SUPPORTED_REQUEST_FIELDS
    }
    DEPRECATED_PARAMS = {"frequency_penalty", "presence_penalty"}
    dropped_fields = sorted(
        key
        for key in payload
        if key not in SUPPORTED_REQUEST_FIELDS
        and key not in RUNTIME_OVERRIDE_FIELDS
        and key not in {"max_completion_tokens", "functions", "function_call"}
        and key not in DEPRECATED_PARAMS
    )
    if dropped_fields:
        LOG.warning(
            "dropping unsupported request field(s): %s",
            ", ".join(dropped_fields),
        )
    for key in DEPRECATED_PARAMS:
        if key in payload:
            LOG.warning("dropping deprecated parameter: %s", key)
    if "max_tokens" not in prepared and "max_completion_tokens" in payload:
        prepared["max_tokens"] = payload["max_completion_tokens"]

    prepared["model"] = upstream_model

    if "tools" in prepared and isinstance(prepared["tools"], list):
        prepared["tools"] = [normalize_tool(tool) for tool in prepared["tools"]]
    elif isinstance(payload.get("functions"), list):
        prepared["tools"] = [
            legacy_function_to_tool(function)
            for function in payload["functions"]
        ]

    if "tool_choice" in prepared:
        tool_choice = normalize_tool_choice(prepared["tool_choice"])
        if tool_choice is None:
            prepared.pop("tool_choice", None)
        else:
            prepared["tool_choice"] = tool_choice
    elif "function_call" in payload:
        tool_choice = convert_function_call(payload.get("function_call"))
        if tool_choice is not None:
            prepared["tool_choice"] = tool_choice

    prepared["thinking"] = _effective_thinking_payload(payload, config)
    thinking_enabled = prepared["thinking"]["type"] == "enabled"
    thinking_disabled = prepared["thinking"]["type"] == "disabled"

    cache_namespace = reasoning_cache_namespace(
        config,
        upstream_model,
        prepared.get("thinking"),
        prepared.get("thinking", {}).get("reasoning_effort"),
        authorization,
    )
    INTERNAL_LOG.debug(
        "transform.cache: namespace=%s...",
        cache_namespace[:16],
    )
    pre_repair_messages, _, _, _ = normalize_messages(
        payload.get("messages"),
        None,
        cache_namespace,
        repair_reasoning=False,
        keep_reasoning=not thinking_disabled,
    )
    record_response_messages = pre_repair_messages
    record_response_scope = conversation_scope(
        record_response_messages, cache_namespace
    )
    messages_for_repair = pre_repair_messages
    continued_recovery_boundary = False
    retired_prefix_messages = 0
    recovered_count = 0
    recovery_dropped_messages = 0
    recovery_notice = None
    recovery_steps: list[dict[str, Any]] = []

    messages, patched_count, missing_indexes, reasoning_diagnostics = (
        normalize_messages(
            messages_for_repair,
            store,
            cache_namespace,
            repair_reasoning=thinking_enabled,
            keep_reasoning=not thinking_disabled,
        )
    )
    INTERNAL_LOG.debug(
        "transform.prepare: cache lookup found %s patched messages, "
        "%s still missing",
        patched_count,
        len(missing_indexes),
    )
    if (
        missing_indexes
        and thinking_enabled
        and config.missing_reasoning_strategy == "recover"
    ):
        boundary = active_messages_from_recovery_boundary(pre_repair_messages)
        if boundary is not None:
            INTERNAL_LOG.debug(
                "transform.prepare: recovery boundary check - found"
            )
            messages_for_repair, retired_prefix_messages, boundary_step = (
                boundary
            )
            continued_recovery_boundary = True
            recovery_steps.append(boundary_step)
            messages, patched_count, missing_indexes, reasoning_diagnostics = (
                normalize_messages(
                    messages_for_repair,
                    store,
                    cache_namespace,
                    repair_reasoning=thinking_enabled,
                    keep_reasoning=not thinking_disabled,
                )
            )
        else:
            INTERNAL_LOG.debug(
                "transform.prepare: recovery boundary check - not found"
            )
    while missing_indexes and config.missing_reasoning_strategy == "recover":
        recovered_messages, dropped_messages, notice, recovery_step = (
            recover_messages_from_missing_reasoning(messages, missing_indexes)
        )
        recovery_steps.append(recovery_step)
        if not dropped_messages:
            break
        recovered_count += len(missing_indexes)
        recovery_dropped_messages += dropped_messages
        INTERNAL_LOG.debug(
            "transform.prepare: recovery dropped %s prefix messages",
            recovery_dropped_messages,
        )
        if notice and _should_show_recovery_notice(record_response_scope):
            recovery_notice = notice
        (
            messages,
            patched_count,
            missing_indexes,
            latest_diagnostics,
        ) = normalize_messages(
            recovered_messages,
            store,
            cache_namespace,
            repair_reasoning=thinking_enabled,
            keep_reasoning=not thinking_disabled,
        )
        reasoning_diagnostics.extend(latest_diagnostics)
    active_record_response_scope = conversation_scope(messages, cache_namespace)
    record_response_contexts = response_recording_contexts(
        (record_response_scope, record_response_messages),
        (active_record_response_scope, messages),
    )
    prepared["messages"] = strip_recovery_notice_for_upstream(messages)

    return PreparedRequest(
        payload=prepared,
        original_model=original_model,
        upstream_model=upstream_model,
        cache_namespace=cache_namespace,
        patched_reasoning_messages=patched_count,
        missing_reasoning_messages=len(missing_indexes),
        recovered_reasoning_messages=recovered_count,
        recovery_dropped_messages=recovery_dropped_messages,
        recovery_notice=recovery_notice,
        record_response_scope=record_response_scope,
        record_response_messages=record_response_messages,
        record_response_contexts=record_response_contexts,
        reasoning_diagnostics=reasoning_diagnostics,
        recovery_steps=recovery_steps,
        continued_recovery_boundary=continued_recovery_boundary,
        retired_prefix_messages=retired_prefix_messages,
    )
