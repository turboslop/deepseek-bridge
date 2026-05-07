from __future__ import annotations

from typing import Any

from ..logging import LOG

# Recovery notice tracking — prevents the cascade loop where the
# recovery notice changes message content → changes conversation_scope
# → next cache lookup fails → recovery fires again → notice again.
# By tracking seen scopes, the visible notice is only injected once
# per conversation. Recovery still fires and repairs reasoning silently
# on subsequent misses.
_recovery_notice_seen: set[str] = set()


def reset_recovery_notice_tracking() -> None:
    _recovery_notice_seen.clear()


def _should_show_recovery_notice(scope: str) -> bool:
    if scope in _recovery_notice_seen:
        return False
    if len(_recovery_notice_seen) > 10_000:
        _recovery_notice_seen.clear()
    _recovery_notice_seen.add(scope)
    return True


RECOVERY_NOTICE_TEXT = "[deepseek-bridge] Refreshed reasoning_content history."
RECOVERY_NOTICE_CONTENT = f"{RECOVERY_NOTICE_TEXT}\n\n"
RECOVERY_SYSTEM_CONTENT = (
    "deepseek-bridge recovered this request because older DeepSeek "
    "thinking-mode tool-call reasoning_content was unavailable. Older "
    "unrecoverable tool-call history was omitted; continue using only the "
    "remaining recovered context."
)


def has_recovery_notice(message: dict[str, Any]) -> bool:
    content = message.get("content")
    return (
        message.get("role") == "assistant"
        and isinstance(content, str)
        and content.startswith(RECOVERY_NOTICE_TEXT)
    )


def strip_recovery_notice_for_upstream(
    messages: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    stripped: list[dict[str, Any]] = []
    for message in messages:
        if message.get("role") != "assistant":
            stripped.append(message)
            continue
        content = message.get("content")
        if not isinstance(content, str) or not content.startswith(RECOVERY_NOTICE_TEXT):
            stripped.append(message)
            continue
        cleaned = dict(message)
        cleaned["content"] = content[len(RECOVERY_NOTICE_TEXT) :].lstrip("\r\n")
        stripped.append(cleaned)
    return stripped


def leading_system_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    leading_messages: list[dict[str, Any]] = []
    for message in messages:
        if message.get("role") == "system":
            leading_messages.append(message)
            continue
        break
    return leading_messages


def active_messages_from_recovery_boundary(
    messages: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], int, dict[str, Any]] | None:
    recovery_boundary_index = next(
        (
            index
            for index in range(len(messages) - 1, -1, -1)
            if has_recovery_notice(messages[index])
        ),
        -1,
    )
    if recovery_boundary_index == -1:
        return None

    context_user_index = next(
        (
            index
            for index in range(recovery_boundary_index - 1, -1, -1)
            if messages[index].get("role") == "user"
        ),
        -1,
    )
    leading = leading_system_messages(messages)
    recovered_tail = []
    if context_user_index != -1:
        recovered_tail.append(messages[context_user_index])
    recovered_tail.extend(messages[recovery_boundary_index:])
    active_messages = [
        *leading,
        {"role": "system", "content": RECOVERY_SYSTEM_CONTENT},
        *recovered_tail,
    ]
    kept_context_messages = 1 if context_user_index != -1 else 0
    retired_messages = (
        recovery_boundary_index - len(leading) - kept_context_messages
    )
    retired_messages = max(retired_messages, 0)
    step = {
        "strategy": "continued_recovery_boundary",
        "recovery_boundary_index": recovery_boundary_index,
        "context_user_index": context_user_index,
        "retired_prefix_messages": retired_messages,
    }
    return active_messages, retired_messages, step


def recover_messages_from_missing_reasoning(
    messages: list[dict[str, Any]],
    missing_indexes: list[int],
) -> tuple[list[dict[str, Any]], int, str | None, dict[str, Any]]:
    recovery_boundary_index = next(
        (
            index
            for index in range(len(messages) - 1, -1, -1)
            if has_recovery_notice(messages[index])
            and any(missing_index < index for missing_index in missing_indexes)
        ),
        -1,
    )
    if recovery_boundary_index != -1:
        context_user_index = next(
            (
                index
                for index in range(recovery_boundary_index - 1, -1, -1)
                if messages[index].get("role") == "user"
            ),
            -1,
        )
        leading = leading_system_messages(messages)
        recovered_tail = []
        if context_user_index != -1:
            recovered_tail.append(messages[context_user_index])
        recovered_tail.extend(messages[recovery_boundary_index:])
        recovered = [
            *leading,
            {"role": "system", "content": RECOVERY_SYSTEM_CONTENT},
            *recovered_tail,
        ]
        kept_context_messages = 1 if context_user_index != -1 else 0
        omitted_messages = (
            recovery_boundary_index - len(leading) - kept_context_messages
        )
        LOG.debug(
            "transform.recovery: strategy=recovery_boundary, boundary_at=%s, dropped=%s",
            recovery_boundary_index,
            omitted_messages,
        )
        return (
            recovered,
            omitted_messages,
            None,
            {
                "strategy": "recovery_boundary",
                "missing_indexes": missing_indexes,
                "recovery_boundary_index": recovery_boundary_index,
                "context_user_index": context_user_index,
                "dropped_messages": omitted_messages,
                "notice": None,
            },
        )

    last_user_index = next(
        (
            index
            for index in range(len(messages) - 1, -1, -1)
            if messages[index].get("role") == "user"
        ),
        -1,
    )
    if last_user_index == -1:
        LOG.debug(
            "transform.recovery: strategy=none, no user messages to recover from",
        )
        return (
            messages,
            0,
            None,
            {
                "strategy": "none",
                "missing_indexes": missing_indexes,
                "last_user_index": None,
                "dropped_messages": 0,
                "notice": None,
            },
        )

    recovered = leading_system_messages(messages)
    omitted_messages = len(messages) - len(recovered) - 1
    recovered.append({"role": "system", "content": RECOVERY_SYSTEM_CONTENT})
    recovered.append(messages[last_user_index])
    LOG.debug(
        "transform.recovery: strategy=latest_user, boundary_at=%s, dropped=%s",
        last_user_index,
        omitted_messages,
    )
    return (
        recovered,
        omitted_messages,
        RECOVERY_NOTICE_CONTENT,
        {
            "strategy": "latest_user",
            "missing_indexes": missing_indexes,
            "last_user_index": last_user_index,
            "dropped_messages": omitted_messages,
            "notice": RECOVERY_NOTICE_CONTENT,
        },
    )
