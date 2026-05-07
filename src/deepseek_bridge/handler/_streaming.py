from __future__ import annotations

import json
import time
from http.client import HTTPException
from typing import Any

import urllib3
import urllib3.exceptions

from ..helpers import (
    ProxyResponseResult,
    SYSTEM_FINGERPRINT,
    inject_recovery_notice,
    log_json,
    recovery_notice_chunk,
    sse_data,
)
from ..logging import LOG
from ..reasoning_store import conversation_scope
from ..streaming import CursorReasoningDisplayAdapter, StreamAccumulator
from ..trace import TraceRequest


class HandlerStreaming:
    def _proxy_streaming_response(
        self,
        response: Any,
        original_model: str,
        request_messages: list[dict[str, Any]],
        cache_namespace: str,
        recovery_notice: str | None = None,
        trace: TraceRequest | None = None,
        record_response_scope: str | None = None,
        record_response_messages: list[dict[str, Any]] | None = None,
        record_response_contexts: list[tuple[str, list[dict[str, Any]]]] | None = None,
        include_usage: bool = False,
    ) -> ProxyResponseResult:
        if trace is not None:
            trace.record_upstream_response(
                status=response.status,
                headers=dict(response.headers.items()),
                stream=True,
            )
            trace.record_cursor_response(
                status=response.status,
                headers={
                    "Content-Type": "text/event-stream",
                    "Cache-Control": "no-cache",
                    "Connection": "close",
                },
            )
        sent_headers = self._send_response_headers(
            response.status,
            [
                ("Content-Type", "text/event-stream"),
                ("Cache-Control", "no-cache"),
                ("Connection", "close"),
            ],
            "sending streaming response headers",
        )
        if not sent_headers:
            return ProxyResponseResult(False)
        self.close_connection = True

        accumulator = StreamAccumulator()
        usage: dict[str, Any] | None = None
        display_adapter = (
            CursorReasoningDisplayAdapter(self.config.collapsible_reasoning)
            if self.config.display_reasoning
            else None
        )
        scope = (
            record_response_scope
            if record_response_scope is not None
            else conversation_scope(request_messages, cache_namespace)
        )
        response_prior_messages = (
            record_response_messages
            if record_response_messages is not None
            else request_messages
        )
        response_contexts = (
            record_response_contexts
            if record_response_contexts is not None
            else [(scope, response_prior_messages)]
        )
        finalized = False
        pending_recovery_notice = recovery_notice
        try:
            sse_line_num = 0
            while True:
                if not self._check_client_alive():
                    LOG.debug("handler.disconnect: client disconnected at stage=streaming")
                    if self.config.verbose:
                        LOG.info("client disconnected, stopping upstream read")
                    response.release_conn()
                    return ProxyResponseResult(False, usage)
                try:
                    line = response.readline()
                except (
                    HTTPException,
                    OSError,
                    urllib3.exceptions.ReadTimeoutError,
                ) as exc:
                    if isinstance(exc, urllib3.exceptions.ReadTimeoutError):
                        LOG.warning(
                            "upstream streaming response read timed out after %ss: %s",
                            self.config.stream_read_timeout,
                            exc,
                        )
                    else:
                        LOG.warning("upstream streaming response read failed: %s", exc)
                    response.release_conn()
                    return ProxyResponseResult(False, usage)
                if not line:
                    break
                sse_line_num += 1
                (
                    rewritten,
                    finalized,
                    pending_recovery_notice,
                    chunk_usage,
                ) = self._rewrite_sse_line(
                    line,
                    original_model,
                    accumulator,
                    cache_namespace,
                    response_contexts,
                    display_adapter,
                    pending_recovery_notice,
                    trace,
                    include_usage=include_usage,
                    usage_so_far=usage,
                )
                if chunk_usage is not None:
                    usage = chunk_usage
                if finalized:
                    LOG.debug("handler.sse: line %s, type=done", sse_line_num)
                elif line.lstrip().startswith(b"data:") and b"[DONE]" not in line:
                    LOG.debug("handler.sse: line %s, type=chunk", sse_line_num)
                if trace is not None:
                    trace.record_stream_chunk(line, rewritten)
                if not self._write_to_client(
                    rewritten, "sending streaming response chunk", flush=True
                ):
                    response.release_conn()
                    return ProxyResponseResult(False, usage)
                if finalized:
                    break
        finally:
            if not finalized:
                LOG.debug("handler.disconnect: client disconnected at stage=streaming_finalize")
                if self.config.verbose:
                    log_json(
                        "model streaming assistant messages", accumulator.messages()
                    )
                stored = sum(
                    accumulator.store_reasoning(
                        self.reasoning_store,
                        ctx_scope,
                        cache_namespace,
                        prior_messages,
                    )
                    for ctx_scope, prior_messages in response_contexts
                )
                if self.config.verbose and stored:
                    LOG.info(
                        "stored %s streaming reasoning cache key(s) before exit",
                        stored,
                    )
        return ProxyResponseResult(True, usage)

    def _rewrite_sse_line(
        self,
        line: bytes,
        original_model: str,
        accumulator: StreamAccumulator,
        cache_namespace: str,
        response_contexts: list[tuple[str, list[dict[str, Any]]]],
        display_adapter: CursorReasoningDisplayAdapter | None,
        recovery_notice: str | None = None,
        trace: TraceRequest | None = None,
        include_usage: bool = False,
        usage_so_far: dict[str, Any] | None = None,
    ) -> tuple[bytes, bool, str | None, dict[str, Any] | None]:
        stripped = line.strip()
        if not stripped.startswith(b"data:"):
            return line, False, recovery_notice, None

        data = stripped[len(b"data:") :].strip()
        if data == b"[DONE]":
            if self.config.verbose:
                log_json("model streaming assistant messages", accumulator.messages())
            stored = sum(
                accumulator.store_reasoning(
                    self.reasoning_store,
                    scope,
                    cache_namespace,
                    prior_messages,
                )
                for scope, prior_messages in response_contexts
            )
            scope_preview = (response_contexts[0][0] if response_contexts else "")[:16]
            LOG.debug(
                "handler.sse: stored %s reasoning key(s) for scope=%s...",
                stored,
                scope_preview,
            )
            if self.config.verbose and stored:
                LOG.info("stored %s streaming reasoning cache key(s)", stored)
            prefix = b""
            if include_usage and usage_so_far is None:
                prefix += sse_data(
                    {
                        "id": "chatcmpl-synthesized-usage",
                        "object": "chat.completion.chunk",
                        "created": int(time.time()),
                        "model": original_model,
                        "system_fingerprint": SYSTEM_FINGERPRINT,
                        "choices": [],
                        "usage": {
                            "prompt_tokens": 0,
                            "completion_tokens": 0,
                            "total_tokens": 0,
                        },
                    }
                )
            if display_adapter is None:
                if recovery_notice:
                    prefix += sse_data(
                        recovery_notice_chunk(original_model, recovery_notice)
                    )
                return prefix + b"data: [DONE]\n\n", True, None, None
            closing_chunk = display_adapter.flush_chunk(original_model)
            if closing_chunk is not None:
                prefix += sse_data(closing_chunk)
            if recovery_notice:
                prefix += sse_data(
                    recovery_notice_chunk(original_model, recovery_notice)
                )
            return prefix + b"data: [DONE]\n\n", True, None, None

        try:
            chunk = json.loads(data.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return line, False, recovery_notice, None

        if isinstance(chunk, dict):
            if recovery_notice and inject_recovery_notice(chunk, recovery_notice):
                recovery_notice = None
            accumulator.ingest_chunk(chunk)
            stored = sum(
                accumulator.store_ready_reasoning(
                    self.reasoning_store,
                    scope,
                    cache_namespace,
                    prior_messages,
                )
                for scope, prior_messages in response_contexts
            )
            scope_preview = (response_contexts[0][0] if response_contexts else "")[:16]
            LOG.debug(
                "handler.sse: stored %s reasoning key(s) for scope=%s...",
                stored,
                scope_preview,
            )
            if self.config.verbose and stored:
                LOG.info("stored %s streaming reasoning cache key(s)", stored)
            chunk_usage = chunk.get("usage")
            if trace is not None:
                trace.record_usage(chunk_usage)
            if display_adapter is not None:
                display_adapter.rewrite_chunk(chunk)
            if "model" in chunk:
                chunk["model"] = original_model
            if "system_fingerprint" not in chunk:
                chunk["system_fingerprint"] = SYSTEM_FINGERPRINT
            ending = b"\r\n" if line.endswith(b"\r\n") else b"\n"
            return (
                (
                    b"data: "
                    + json.dumps(
                        chunk, ensure_ascii=False, separators=(",", ":")
                    ).encode("utf-8")
                    + ending
                ),
                False,
                recovery_notice,
                chunk_usage if isinstance(chunk_usage, dict) else None,
            )
        return line, False, recovery_notice, None
