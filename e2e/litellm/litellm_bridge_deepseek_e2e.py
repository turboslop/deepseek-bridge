from __future__ import annotations

import json
import os
import socket
import sys
import time
from copy import deepcopy
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

TIMEOUT_SECONDS = float(os.getenv("LITELLM_E2E_TIMEOUT_SECONDS", "240"))
BRIDGE_READY_URL = os.getenv("BRIDGE_READY_URL", "http://bridge:9000/readyz")
LITELLM_BASE_URL = os.getenv("LITELLM_BASE_URL", "http://litellm:4000/v1")
LITELLM_API_KEY = os.getenv("LITELLM_API_KEY", "sk-litellm-e2e")


class E2EFailure(AssertionError):
    pass


def log(message: str) -> None:
    print(f"==> {message}", flush=True)


def wait_for_tcp(url: str, label: str) -> None:
    parsed = urlparse(url)
    host = parsed.hostname
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    deadline = time.monotonic() + TIMEOUT_SECONDS
    last_error: BaseException | None = None
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=2):
                return
        except OSError as exc:
            last_error = exc
            time.sleep(1)
    raise E2EFailure(f"timed out waiting for {label}: {last_error}")


def get_json(url: str, timeout: float = 10) -> tuple[int, dict[str, Any]]:
    request = Request(url, method="GET")
    try:
        with urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8")
            return response.status, json.loads(body)
    except HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


def wait_for_bridge_ready() -> None:
    deadline = time.monotonic() + TIMEOUT_SECONDS
    last_status: int | None = None
    last_body: dict[str, Any] | None = None
    while time.monotonic() < deadline:
        try:
            last_status, last_body = get_json(BRIDGE_READY_URL)
            if last_status == 200 and last_body.get("ok") is True:
                return
        except OSError, ValueError, URLError:
            pass
        time.sleep(1)
    raise E2EFailure(
        f"bridge never became ready: status={last_status} body={last_body}"
    )


def post_litellm(payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    url = LITELLM_BASE_URL.rstrip("/") + "/chat/completions"
    request = Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers={
            "Authorization": f"Bearer {LITELLM_API_KEY}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urlopen(request, timeout=180) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        body = exc.read().decode("utf-8")
        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            payload = {"raw_body": body}
        return exc.code, payload


def first_request() -> dict[str, Any]:
    return {
        "model": "deepseek-v4-pro",
        "messages": [
            {
                "role": "user",
                "content": (
                    "You must call the get_date tool before answering. Do "
                    "not answer from memory. After the tool returns, tell me "
                    "the date it returns."
                ),
            }
        ],
        "tools": [
            {
                "type": "function",
                "function": {
                    "name": "get_date",
                    "description": "Return the current date as YYYY-MM-DD.",
                    "parameters": {"type": "object", "properties": {}},
                },
            }
        ],
        "temperature": 0,
        "user": "litellm-compose-e2e",
    }


def error_message(payload: dict[str, Any]) -> str:
    error = payload.get("error")
    if isinstance(error, dict):
        return str(error.get("message") or error)
    return json.dumps(payload, ensure_ascii=False)


def assert_ok(status: int, payload: dict[str, Any], label: str) -> None:
    if status != 200:
        message = error_message(payload)
        raise E2EFailure(f"{label} failed: status={status} {message}")


def first_choice_message(payload: dict[str, Any]) -> dict[str, Any]:
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        raise E2EFailure(f"response has no choices: {payload}")
    choice = choices[0]
    if not isinstance(choice, dict):
        raise E2EFailure(f"choice is not an object: {choice}")
    message = choice.get("message")
    if not isinstance(message, dict):
        raise E2EFailure(f"choice has no message: {choice}")
    return message


def run_tool_repair_scenario() -> None:
    log("sending first tool-call request through LiteLLM")
    assistant: dict[str, Any] | None = None
    tool_calls: list[Any] | None = None
    first_error: str | None = None
    for attempt in range(1, 4):
        status, first_response = post_litellm(first_request())
        if status != 200:
            first_error = error_message(first_response)
            time.sleep(attempt)
            continue
        assistant = first_choice_message(first_response)
        candidate_tool_calls = assistant.get("tool_calls")
        if isinstance(candidate_tool_calls, list) and candidate_tool_calls:
            tool_calls = candidate_tool_calls
            break
        first_error = f"response had no tool_calls: {assistant}"
        time.sleep(attempt)
    if assistant is None or tool_calls is None:
        raise E2EFailure(f"first LiteLLM request failed: {first_error}")

    cursor_assistant = deepcopy(assistant)
    cursor_assistant.pop("reasoning_content", None)
    tool_messages = [
        {
            "role": "tool",
            "tool_call_id": str(tool_call["id"]),
            "content": "2026-05-19",
        }
        for tool_call in tool_calls
        if isinstance(tool_call, dict) and tool_call.get("id")
    ]
    if len(tool_messages) != len(tool_calls):
        raise E2EFailure(f"not all tool calls had ids: {tool_calls}")

    log("sending follow-up without reasoning_content through LiteLLM")
    followup_payload = {
        "model": "deepseek-v4-pro",
        "messages": [
            first_request()["messages"][0],
            cursor_assistant,
            *tool_messages,
        ],
        "tools": first_request()["tools"],
        "user": "litellm-compose-e2e",
    }
    status, followup_response = post_litellm(followup_payload)
    assert_ok(status, followup_response, "follow-up LiteLLM request")

    final_assistant = first_choice_message(followup_response)
    if not final_assistant.get("content") and not final_assistant.get(
        "tool_calls"
    ):
        raise E2EFailure(
            f"follow-up produced an empty assistant: {final_assistant}"
        )


def main() -> int:
    log("waiting for bridge TCP")
    wait_for_tcp(BRIDGE_READY_URL, "deepseek-bridge")
    log("waiting for LiteLLM TCP")
    wait_for_tcp(LITELLM_BASE_URL, "LiteLLM")
    log("waiting for bridge readiness")
    wait_for_bridge_ready()
    run_tool_repair_scenario()
    log("LiteLLM -> bridge -> DeepSeek Cloud e2e passed")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except E2EFailure as exc:
        print(f"E2E FAILED: {exc}", file=sys.stderr, flush=True)
        raise SystemExit(1) from exc
