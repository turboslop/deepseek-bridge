from __future__ import annotations

import contextlib
import http.client
import json
import logging as stdlib_logging
import sys
import threading
import types
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from .config import ProxyConfig

LOG = stdlib_logging.getLogger("deepseek_bridge")
PAYLOAD_LOG = stdlib_logging.getLogger("deepseek_bridge.payload")
INTERNAL_LOG = stdlib_logging.getLogger("deepseek_bridge.internal")

DEFAULT_INFO_LOG_FORMAT = "%(message)s"
DEFAULT_WARNING_LOG_FORMAT = "%(levelname)s %(message)s"
VERBOSE_LOG_FORMAT = "%(asctime)s %(levelname)s %(message)s"
JSON_LOG_FIELDS = (
    "request_id",
    "method",
    "path",
    "status",
    "duration_ms",
    "model",
    "upstream_status",
    "cache_hit",
    "storage_backend",
)

RECOVERY_NOTICE_TEXT = "[deepseek-bridge] Refreshed reasoning_content history."
RECOVERY_NOTICE_CONTENT = f"{RECOVERY_NOTICE_TEXT}\n\n"
RECOVERY_SYSTEM_CONTENT = (
    "deepseek-bridge recovered this request because older DeepSeek "
    "thinking-mode tool-call reasoning_content was unavailable. Older "
    "unrecoverable tool-call history was omitted; continue using only the "
    "remaining recovered context."
)


class ConsoleLogFormatter(stdlib_logging.Formatter):
    def __init__(self) -> None:
        super().__init__()
        self._info_formatter = stdlib_logging.Formatter(DEFAULT_INFO_LOG_FORMAT)
        self._warning_formatter = stdlib_logging.Formatter(
            DEFAULT_WARNING_LOG_FORMAT
        )

    def format(self, record: stdlib_logging.LogRecord) -> str:
        if record.levelno <= stdlib_logging.INFO:
            return self._info_formatter.format(record)
        return self._warning_formatter.format(record)


class JsonLogFormatter(stdlib_logging.Formatter):
    def format(self, record: stdlib_logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, UTC).isoformat(
                timespec="milliseconds"
            ),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for field in JSON_LOG_FIELDS:
            value = getattr(record, field, None)
            if value is not None:
                payload[field] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
            default=str,
        )


def _purge_old_logs(
    log_dir: Path, prefix: str = "proxy", keep: int = 5
) -> None:
    """Remove old log files, keeping the most recent *keep* files."""
    log_files = sorted(
        log_dir.glob(f"{prefix}-*.log"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    for stale in log_files[keep:]:
        with contextlib.suppress(OSError):
            stale.unlink()


def configure_logging(
    *,
    debug: bool = False,
    log_dir: str | Path | None = None,
    log_format: str = "text",
) -> str | None:
    log_file_path: str | None = None
    handlers: list[stdlib_logging.Handler] = []
    structured = log_format.strip().lower() == "json"
    console_formatter: stdlib_logging.Formatter
    if structured:
        console_formatter = JsonLogFormatter()
        file_formatter: stdlib_logging.Formatter = JsonLogFormatter()
    else:
        console_formatter = ConsoleLogFormatter()
        file_formatter = stdlib_logging.Formatter(VERBOSE_LOG_FORMAT)
    console_handler = stdlib_logging.StreamHandler()
    console_handler.setFormatter(console_formatter)
    handlers.append(console_handler)
    if log_dir:
        log_path = Path(log_dir).expanduser()
        log_path.mkdir(parents=True, exist_ok=True)
        _purge_old_logs(log_path, prefix="proxy", keep=5)
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        log_file = log_path / f"proxy-{timestamp}.log"
        log_file_path = str(log_file)
        file_handler = stdlib_logging.FileHandler(
            log_file_path, encoding="utf-8"
        )
        file_handler.setFormatter(file_formatter)
        handlers.append(file_handler)
        if debug:
            _purge_old_logs(log_path, prefix="debug", keep=5)
            debug_file = log_path / f"debug-{timestamp}.log"
            debug_handler = stdlib_logging.FileHandler(
                str(debug_file), encoding="utf-8"
            )
            debug_handler.setFormatter(file_formatter)
            INTERNAL_LOG.addHandler(debug_handler)
            INTERNAL_LOG.propagate = False
    level = stdlib_logging.DEBUG if debug else stdlib_logging.INFO
    stdlib_logging.basicConfig(
        level=level,
        handlers=handlers,
        force=True,
    )
    if log_dir:
        LOG.info("log file: %s", log_file_path)

    def _log_unhandled_exception(
        exc_type: type[BaseException],
        exc_value: BaseException,
        exc_traceback: types.TracebackType | None,
    ) -> None:
        LOG.critical(
            "unhandled exception",
            exc_info=(exc_type, exc_value, exc_traceback),
        )

    sys.excepthook = _log_unhandled_exception
    LOG.info("error logging: enabled")
    return log_file_path


class TerminalSpinner:
    hide_cursor = "\x1b[?25l"
    show_cursor = "\x1b[?25h"
    frames = ("⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏")

    def __init__(
        self,
        *,
        enabled: bool,
        text: str,
        stream: Any | None = None,
        interval: float = 0.12,
    ) -> None:
        self.stream = stream if stream is not None else sys.stderr
        self.enabled = enabled and bool(
            getattr(self.stream, "isatty", lambda: False)()
        )
        self.text = text
        self.interval = interval
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._visible = False

    def start(self) -> TerminalSpinner:
        if not self.enabled or self._thread is not None:
            return self
        self.stream.write(self.hide_cursor)
        self.stream.flush()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        if not self.enabled:
            return
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=1)
            self._thread = None
        if self._visible:
            self.stream.write("\r" + (" " * self._clear_width()) + "\r")
            self.stream.flush()
            self._visible = False
        self.stream.write(self.show_cursor)
        self.stream.flush()

    def _run(self) -> None:
        index = 0
        while not self._stop.is_set():
            self.stream.write("\r" + self.text.format(frame=self.frames[index]))
            self.stream.flush()
            self._visible = True
            index = (index + 1) % len(self.frames)
            self._stop.wait(self.interval)

    def _clear_width(self) -> int:
        return max(len(self.text.format(frame=frame)) for frame in self.frames)


# ── Logging helpers ──────────────────────────────────────────


def _truncate_message_content(payload: Any, max_len: int = 200) -> Any:
    if not isinstance(payload, dict):
        return payload
    result = dict(payload)
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
                        fn2["description"] = (
                            desc[:max_len] + f"... [{len(desc)} chars]"
                        )
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
    except json.JSONDecodeError, UnicodeDecodeError:
        LOG.info("%s:\n%s", label, body.decode("utf-8", errors="replace"))
        return
    log_json(label, payload)


def usage_from_body(body: bytes) -> dict[str, Any] | None:
    try:
        payload = json.loads(body.decode("utf-8"))
    except json.JSONDecodeError, UnicodeDecodeError:
        return None
    if isinstance(payload, dict):
        usage = payload.get("usage")
        if isinstance(usage, dict):
            return usage
    return None


def log_cursor_request(
    payload: dict[str, Any],
    config: ProxyConfig,
    *,
    request_id: str | None = None,
    method: str | None = None,
    path: str | None = None,
) -> None:
    model = str(payload.get("model") or config.upstream_model)
    LOG.info(
        "┌ request model=%s effort=%s messages=%s",
        model,
        config.reasoning_effort,
        format_count(message_count(payload)),
        extra={
            "request_id": request_id,
            "method": method,
            "path": path,
            "model": model,
            "storage_backend": config.storage_backend,
        },
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
    *,
    request_id: str | None = None,
    method: str | None = None,
    path: str | None = None,
    status: int | None = None,
    model: str | None = None,
    upstream_status: int | None = None,
    storage_backend: str | None = None,
) -> None:
    elapsed_str = (
        format_count(elapsed_ms) + "ms" if elapsed_ms is not None else "?"
    )
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
        extra={
            "request_id": request_id,
            "method": method,
            "path": path,
            "status": status,
            "duration_ms": elapsed_ms,
            "model": model,
            "upstream_status": upstream_status,
            "cache_hit": cache_hit_rate(usage),
            "storage_backend": storage_backend,
        },
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
    except TypeError, ValueError:
        return str(value)


def int_or_zero(value: Any) -> int:
    try:
        return int(value or 0)
    except TypeError, ValueError:
        return 0


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
    try:
        if hasattr(response, "data") and response.data is not None:
            return cast(bytes, response.data)
        return cast(bytes, response.read())
    except (TimeoutError, OSError, http.client.IncompleteRead) as exc:
        raise ValueError(
            f"failed to read upstream response body: {exc}"
        ) from exc
