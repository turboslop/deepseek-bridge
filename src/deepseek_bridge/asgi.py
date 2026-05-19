from __future__ import annotations

import asyncio
import hashlib
import json
import random
import threading
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass, field
from typing import Any, cast

import httpx
import orjson
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import Response, StreamingResponse
from starlette.routing import Route
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from . import __version__
from ._types import RequestBodyTooLargeError, _error_body
from .async_upstream import (
    RETRYABLE_UPSTREAM_ERRORS,
    AsyncUpstreamClient,
    iter_response_lines,
)
from .config import (
    MODEL_CREATED_TIMESTAMPS,
    OLLAMA_CONTEXT_LENGTH,
    OLLAMA_EMBEDDING_LENGTH,
    OLLAMA_FORMAT,
    OLLAMA_MAX_OUTPUT_TOKENS,
    OLLAMA_MODEL_SIZE,
    OLLAMA_MODIFIED_AT,
    OLLAMA_PARAMETER_SIZE,
    OLLAMA_QUANTIZATION_LEVEL,
    ProxyConfig,
)
from .handler._response import _origin_is_allowed, _safe_origin_header
from .helpers import _generate_request_id, _shutdown_requested, elapsed_ms
from .logging import (
    LOG,
    context_status,
    format_count,
    log_context_summary,
    log_cursor_request,
    log_json,
    log_stats_summary,
    message_count,
    summarize_chat_payload,
    usage_from_body,
)
from .metrics import METRICS, PROMETHEUS_CONTENT_TYPE
from .reasoning_store import ReasoningStoreProtocol
from .responses_converter import (
    convert_responses_to_chat,
    detect_responses_payload,
)
from .streaming import CursorReasoningDisplayAdapter, StreamAccumulator
from .streaming._sse import (
    SYSTEM_FINGERPRINT,
    inject_recovery_notice,
    recovery_notice_chunk,
    sse_data,
)
from .trace import TraceRequest, TraceWriter
from .transform import prepare_upstream_request, rewrite_response_body
from .transform._prepare import PreparedRequest

_CHAT_PATHS = {
    "/chat/completions",
    "/v1/chat/completions",
    "/completions",
    "/v1/completions",
}
_COMPLETIONS_PATHS = {"/completions", "/v1/completions"}
_HEALTH_PATHS = {"/healthz", "/v1/healthz", "/health", "/v1/health"}
_READY_PATHS = {"/readyz", "/v1/readyz"}
_METRICS_PATHS = {"/metrics", "/v1/metrics"}
_MODELS_PATHS = {"/models", "/v1/models"}
_EMBEDDINGS_PATHS = {"/embeddings", "/v1/embeddings"}


@dataclass(slots=True)
class UpstreamFailure:
    status: int
    message: str
    code: str
    reason: str


@dataclass
class BridgeRuntimeState:
    config: ProxyConfig
    reasoning_store: ReasoningStoreProtocol
    upstream_client: AsyncUpstreamClient
    trace_writer: TraceWriter | None = None
    request_count: int = 0
    start_time: float = field(default_factory=time.monotonic)
    prompt_tokens: int = 0
    completion_tokens: int = 0
    reasoning_tokens: int = 0
    cache_hit_tokens: int = 0
    cache_miss_tokens: int = 0
    model_tokens: dict[str, int] = field(default_factory=dict)
    paused: bool = False
    _active_request_count: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock)

    @property
    def active_request_count(self) -> int:
        with self._lock:
            return self._active_request_count

    @property
    def active_worker_count(self) -> int:
        return self.active_request_count

    @property
    def queue_size(self) -> int:
        return 0

    def request_started(self) -> None:
        with self._lock:
            self._active_request_count += 1

    def request_finished(self) -> None:
        with self._lock:
            self._active_request_count = max(0, self._active_request_count - 1)

    def track_usage(self, usage: dict[str, Any] | None, model: str) -> None:
        if not isinstance(usage, dict):
            return
        with self._lock:
            self.prompt_tokens += int(usage.get("prompt_tokens", 0) or 0)
            self.completion_tokens += int(
                usage.get("completion_tokens", 0) or 0
            )
            details = usage.get("completion_tokens_details")
            if isinstance(details, dict):
                self.reasoning_tokens += int(
                    details.get("reasoning_tokens", 0) or 0
                )
            self.cache_hit_tokens += int(
                usage.get("prompt_cache_hit_tokens", 0) or 0
            )
            self.cache_miss_tokens += int(
                usage.get("prompt_cache_miss_tokens", 0) or 0
            )
            total = int(usage.get("total_tokens", 0) or 0)
            if total:
                self.model_tokens[model] = (
                    self.model_tokens.get(model, 0) + total
                )

    def readiness_checks(self) -> dict[str, dict[str, Any]]:
        checks: dict[str, dict[str, Any]] = {
            "shutdown": {
                "ok": not _shutdown_requested.is_set(),
                "status": (
                    "ok" if not _shutdown_requested.is_set() else "draining"
                ),
            },
            "paused": {
                "ok": not self.paused,
                "status": "ok" if not self.paused else "paused",
            },
            "upstream_client": {
                "ok": not self.upstream_client.is_closed,
                "status": (
                    "ok" if not self.upstream_client.is_closed else "closed"
                ),
            },
            "asgi": {
                "ok": True,
                "status": "ok",
                "active_requests": self.active_request_count,
            },
        }

        health_check = getattr(self.reasoning_store, "healthcheck", None)
        if not callable(health_check):
            health_check = getattr(self.reasoning_store, "health_check", None)
        if callable(health_check):
            try:
                result = health_check()
                if isinstance(result, tuple) and len(result) == 2:
                    ok, detail = result
                else:
                    ok = bool(result)
                    detail = "ok" if result else "unavailable"
            except Exception as exc:
                LOG.warning("storage readiness check failed: %s", exc)
                ok, detail = False, "unavailable"
            checks["storage"] = {"ok": bool(ok), "status": str(detail)}
        else:
            checks["storage"] = {
                "ok": self.reasoning_store is not None,
                "status": (
                    "ok" if self.reasoning_store is not None else "missing"
                ),
            }
        return checks

    def is_ready(self) -> bool:
        return all(
            bool(check["ok"]) for check in self.readiness_checks().values()
        )


class MetricsMiddleware:
    def __init__(self, app: ASGIApp, runtime: BridgeRuntimeState) -> None:
        self.app = app
        self.runtime = runtime

    async def __call__(
        self, scope: Scope, receive: Receive, send: Send
    ) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        started = time.monotonic()
        status: int | str = 500
        finished = False
        method = str(scope.get("method") or "UNKNOWN")
        path = str(scope.get("path") or "unknown")
        self.runtime.request_started()
        METRICS.asgi_request_started()

        def finish_once() -> None:
            nonlocal finished
            if finished:
                return
            finished = True
            self.runtime.request_finished()
            METRICS.asgi_request_finished()
            METRICS.record_http_request(
                method=method,
                path=path,
                status=status,
                duration_seconds=time.monotonic() - started,
            )

        async def send_wrapper(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = int(message.get("status", 500))
            await send(message)
            if message["type"] == "http.response.body" and not message.get(
                "more_body", False
            ):
                finish_once()

        try:
            await self.app(scope, receive, send_wrapper)
        except Exception:
            finish_once()
            raise


def create_app(
    config: ProxyConfig,
    store: ReasoningStoreProtocol,
    upstream_client: AsyncUpstreamClient,
    trace_writer: TraceWriter | None = None,
) -> Starlette:
    runtime = BridgeRuntimeState(
        config=config,
        reasoning_store=store,
        upstream_client=upstream_client,
        trace_writer=trace_writer,
    )

    @asynccontextmanager
    async def lifespan(_app: Starlette) -> AsyncIterator[None]:
        try:
            yield
        finally:
            await upstream_client.aclose()

    app = Starlette(
        routes=[
            Route("/{path:path}", _handle_options, methods=["OPTIONS"]),
            Route("/{path:path}", _handle_get, methods=["GET"]),
            Route("/{path:path}", _handle_post, methods=["POST"]),
        ],
        lifespan=lifespan,
    )
    app.state.bridge = runtime
    app.add_middleware(MetricsMiddleware, runtime=runtime)
    return app


def _runtime(request: Request) -> BridgeRuntimeState:
    return cast(BridgeRuntimeState, request.app.state.bridge)


async def _handle_options(request: Request) -> Response:
    runtime = _runtime(request)
    request_id = _generate_request_id()
    response = Response(status_code=204)
    return _with_common_headers(response, runtime, request, request_id)


async def _handle_get(request: Request) -> Response:
    runtime = _runtime(request)
    request_id = _generate_request_id()
    path = request.url.path
    if runtime.config.ollama and path == "/api/version":
        return _json_response(
            runtime, request, request_id, 200, {"version": __version__}
        )
    if runtime.config.ollama and path == "/api/tags":
        return _json_response(
            runtime, request, request_id, 200, _api_tags(runtime)
        )
    if path in _HEALTH_PATHS:
        uptime = int(time.monotonic() - runtime.start_time)
        return _json_response(
            runtime,
            request,
            request_id,
            200,
            {
                "ok": True,
                "server": "deepseek-bridge",
                "uptime_seconds": uptime,
            },
        )
    if path in _READY_PATHS:
        checks = runtime.readiness_checks()
        ready = all(bool(check["ok"]) for check in checks.values())
        return _json_response(
            runtime,
            request,
            request_id,
            200 if ready else 503,
            {"ok": ready, "server": "deepseek-bridge", "checks": checks},
        )
    if path in _METRICS_PATHS:
        if runtime.config.metrics_enabled:
            body = METRICS.render_prometheus(server=runtime).encode("utf-8")
            return _raw_response(
                runtime,
                request,
                request_id,
                200,
                body,
                PROMETHEUS_CONTENT_TYPE,
            )
        return _json_response(
            runtime,
            request,
            request_id,
            404,
            _error_body(
                "Not found", "invalid_request_error", "endpoint_not_found"
            ),
        )
    if path in _MODELS_PATHS:
        return _json_response(
            runtime, request, request_id, 200, _models(runtime)
        )
    return _json_response(
        runtime,
        request,
        request_id,
        404,
        _error_body("Not found", "invalid_request_error", "endpoint_not_found"),
    )


async def _handle_post(request: Request) -> Response:
    runtime = _runtime(request)
    request_id = _generate_request_id()
    runtime.request_count += 1
    path = request.url.path
    LOG.info(
        "incoming POST %s from %s content_length=%s user_agent=%s",
        path,
        _client_host(request),
        request.headers.get("Content-Length", "0"),
        request.headers.get("User-Agent", ""),
        extra={"request_id": request_id, "method": "POST", "path": path},
    )
    if _shutdown_requested.is_set():
        return _json_response(
            runtime,
            request,
            request_id,
            503,
            _error_body(
                "Server is shutting down",
                "server_error",
                "server_shutting_down",
            ),
        )
    if runtime.config.ollama and path == "/api/show":
        return await _handle_api_show(runtime, request, request_id)
    if path in _EMBEDDINGS_PATHS:
        return await _handle_embeddings(runtime, request, request_id)
    return await _handle_chat(runtime, request, request_id)


async def _handle_chat(
    runtime: BridgeRuntimeState, request: Request, request_id: str
) -> Response:
    started = time.monotonic()
    path = request.url.path
    trace = _start_trace(runtime, request, path)
    if path not in _CHAT_PATHS:
        _record_body_omitted_for_trace(runtime, request, trace)
        _finish_trace(trace, "rejected", http_status=404)
        return _json_response(
            runtime,
            request,
            request_id,
            404,
            _error_body(
                "Only /v1/chat/completions and /v1/completions are supported",
                "invalid_request_error",
                "endpoint_not_found",
            ),
            trace=trace,
        )

    cursor_auth = _cursor_authorization(request)
    if cursor_auth is None:
        _record_body_omitted_for_trace(runtime, request, trace)
        _finish_trace(trace, "rejected", http_status=401)
        return _json_response(
            runtime,
            request,
            request_id,
            401,
            _error_body(
                "Missing Authorization bearer token",
                "authentication_error",
                "invalid_api_key",
            ),
            trace=trace,
        )

    try:
        payload = await _read_json_body(runtime, request, trace)
    except RequestBodyTooLargeError as exc:
        _finish_trace(trace, "rejected", http_status=413, reason=str(exc))
        return _json_response(
            runtime,
            request,
            request_id,
            413,
            _error_body(str(exc), "invalid_request_error", "request_too_large"),
            trace=trace,
        )
    except ValueError as exc:
        _finish_trace(trace, "rejected", http_status=400, reason=str(exc))
        return _json_response(
            runtime,
            request,
            request_id,
            400,
            _error_body(
                str(exc), "invalid_request_error", "invalid_request_error"
            ),
            trace=trace,
        )

    if path in _COMPLETIONS_PATHS:
        _normalize_completions_payload(payload)
    if runtime.paused:
        _finish_trace(trace, "rejected", http_status=503)
        return _json_response(
            runtime,
            request,
            request_id,
            503,
            {
                "error": {
                    "message": "Server paused",
                    "type": "server_error",
                    "code": "server_paused",
                    "param": None,
                }
            },
            trace=trace,
        )
    if path in {"/chat/completions", "/v1/chat/completions"} and (
        detect_responses_payload(payload)
    ):
        payload = convert_responses_to_chat(payload)
    if trace is not None:
        trace.record_cursor_body(payload)
    if runtime.config.debug:
        log_json("cursor request body", payload)

    prepared = _prepare_request(runtime, payload, cursor_auth, trace, path)
    if (
        prepared.missing_reasoning_messages
        and runtime.config.missing_reasoning_strategy == "reject"
    ):
        _finish_trace(trace, "rejected", http_status=409)
        return _json_response(
            runtime,
            request,
            request_id,
            409,
            _missing_reasoning_error(prepared),
            trace=trace,
        )

    if runtime.config.debug:
        LOG.info(
            (
                "upstream request metadata: original_model=%s "
                "upstream_model=%s patched_reasoning=%s "
                "missing_reasoning=%s %s"
            ),
            prepared.original_model,
            prepared.upstream_model,
            prepared.patched_reasoning_messages,
            prepared.missing_reasoning_messages,
            summarize_chat_payload(prepared.payload),
        )

    upstream_body = json.dumps(
        prepared.payload, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")
    upstream_url = f"{runtime.config.upstream_base_url}/chat/completions"
    upstream_headers = _upstream_headers(
        request, bool(prepared.payload.get("stream")), cursor_auth
    )
    if trace is not None:
        trace.record_upstream_request(
            url=upstream_url, headers=upstream_headers, body_bytes=upstream_body
        )

    stream = bool(prepared.payload.get("stream"))
    if stream:
        if trace is not None:
            trace.record_cursor_response(
                status=200,
                headers={
                    "Content-Type": "text/event-stream",
                    "Cache-Control": "no-cache",
                    "Connection": "close",
                },
            )
        METRICS.stream_started()
        generator = _stream_chat_response(
            runtime,
            request_id,
            upstream_url,
            upstream_body,
            upstream_headers,
            prepared,
            trace,
            started,
        )
        stream_response = StreamingResponse(
            generator,
            status_code=200,
            headers={
                "Content-Type": "text/event-stream",
                "Cache-Control": "no-cache",
                "Connection": "close",
            },
        )
        return _with_common_headers(
            stream_response, runtime, request, request_id
        )

    upstream_started = time.monotonic()
    response, failure = await _send_upstream_with_retry(
        runtime,
        upstream_url,
        upstream_body,
        upstream_headers,
        stream=False,
        upstream_model=prepared.upstream_model,
    )
    if failure is not None:
        METRICS.record_upstream_request(
            model=prepared.upstream_model,
            status=failure.status,
            duration_seconds=time.monotonic() - upstream_started,
        )
        _finish_trace(trace, "upstream_error", http_status=failure.status)
        return _json_response(
            runtime,
            request,
            request_id,
            failure.status,
            _error_body(failure.message, "server_error", failure.code),
            trace=trace,
        )
    assert response is not None
    try:
        upstream_status = response.status_code
        if upstream_status >= 400:
            body = response.content
            METRICS.record_upstream_request(
                model=prepared.upstream_model,
                status=upstream_status,
                duration_seconds=time.monotonic() - upstream_started,
            )
            _record_upstream_error_trace(trace, response, body)
            _finish_trace(
                trace,
                "upstream_error",
                http_status=upstream_status,
                stream=False,
            )
            return _raw_response(
                runtime,
                request,
                request_id,
                upstream_status,
                body,
                response.headers.get("Content-Type", "application/json"),
            )

        upstream_body_bytes = response.content
        usage = usage_from_body(upstream_body_bytes)
        try:
            body = rewrite_response_body(
                upstream_body_bytes,
                prepared.original_model,
                runtime.reasoning_store,
                prepared.payload["messages"],
                prepared.cache_namespace,
                content_prefix=prepared.recovery_notice,
                scope=prepared.record_response_scope,
                prior_messages=prepared.record_response_messages,
                recording_contexts=prepared.record_response_contexts,
                display_reasoning=runtime.config.display_reasoning,
                collapsible_reasoning=runtime.config.collapsible_reasoning,
            )
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            LOG.warning("failed to rewrite upstream JSON response: %s", exc)
            body = upstream_body_bytes
        if trace is not None:
            trace.record_upstream_response(
                status=upstream_status,
                headers=dict(response.headers.items()),
                body=upstream_body_bytes,
                stream=False,
            )
            with suppress(json.JSONDecodeError, UnicodeDecodeError):
                upstream_payload = json.loads(
                    upstream_body_bytes.decode("utf-8")
                )
                if isinstance(upstream_payload, dict):
                    trace.record_usage(upstream_payload.get("usage"))
            trace.record_cursor_response(
                status=upstream_status,
                headers={
                    "Content-Type": response.headers.get(
                        "Content-Type", "application/json"
                    ),
                    "Content-Length": str(len(body)),
                },
                body=body,
            )
        METRICS.record_upstream_request(
            model=prepared.upstream_model,
            status=upstream_status,
            duration_seconds=time.monotonic() - upstream_started,
        )
        runtime.track_usage(usage, prepared.original_model)
        log_stats_summary(
            usage,
            elapsed_ms=elapsed_ms(started),
            request_id=request_id,
            method="POST",
            path=path,
            status=upstream_status,
            model=prepared.original_model,
            upstream_status=upstream_status,
            storage_backend=runtime.config.storage_backend,
        )
        _finish_trace(
            trace, "completed", http_status=upstream_status, stream=False
        )
        return _raw_response(
            runtime,
            request,
            request_id,
            upstream_status,
            body,
            response.headers.get("Content-Type", "application/json"),
        )
    finally:
        await response.aclose()


async def _stream_chat_response(
    runtime: BridgeRuntimeState,
    request_id: str,
    upstream_url: str,
    upstream_body: bytes,
    upstream_headers: dict[str, str],
    prepared: PreparedRequest,
    trace: TraceRequest | None,
    started: float,
) -> AsyncIterator[bytes]:
    response: httpx.Response | None = None
    finalized = False
    usage: dict[str, Any] | None = None
    upstream_started = time.monotonic()
    response_contexts = prepared.record_response_contexts or [
        (
            prepared.record_response_scope or "",
            prepared.record_response_messages,
        )
    ]
    accumulator = StreamAccumulator()
    display_adapter = (
        CursorReasoningDisplayAdapter(runtime.config.collapsible_reasoning)
        if runtime.config.display_reasoning
        else None
    )
    pending_recovery_notice = prepared.recovery_notice
    try:
        response, failure = await _send_upstream_with_retry(
            runtime,
            upstream_url,
            upstream_body,
            upstream_headers,
            stream=True,
            upstream_model=prepared.upstream_model,
        )
        if failure is not None:
            METRICS.record_upstream_request(
                model=prepared.upstream_model,
                status=failure.status,
                duration_seconds=time.monotonic() - upstream_started,
            )
            _finish_trace(trace, "upstream_error", http_status=failure.status)
            yield _sse_error(failure.message)
            return
        assert response is not None
        upstream_status = response.status_code
        if trace is not None:
            trace.record_upstream_response(
                status=upstream_status,
                headers=dict(response.headers.items()),
                stream=True,
            )
        if upstream_status >= 400:
            METRICS.record_upstream_request(
                model=prepared.upstream_model,
                status=upstream_status,
                duration_seconds=time.monotonic() - upstream_started,
            )
            _finish_trace(
                trace,
                "upstream_error",
                http_status=upstream_status,
                stream=True,
            )
            yield _sse_error(f"Upstream returned {upstream_status}")
            return

        include_usage = bool(
            prepared.payload.get("stream_options", {}).get("include_usage")
        )
        async for line in iter_response_lines(response):
            rewritten, finalized, pending_recovery_notice, chunk_usage = (
                _rewrite_sse_line(
                    runtime,
                    line,
                    prepared.original_model,
                    accumulator,
                    prepared.cache_namespace,
                    response_contexts,
                    display_adapter,
                    pending_recovery_notice,
                    trace,
                    include_usage=include_usage,
                    usage_so_far=usage,
                )
            )
            if chunk_usage is not None:
                usage = chunk_usage
            if trace is not None:
                trace.record_stream_chunk(line, rewritten)
            yield rewritten
            if finalized:
                break
        METRICS.record_upstream_request(
            model=prepared.upstream_model,
            status=upstream_status,
            duration_seconds=time.monotonic() - upstream_started,
        )
        runtime.track_usage(usage, prepared.original_model)
        log_stats_summary(
            usage,
            elapsed_ms=elapsed_ms(started),
            request_id=request_id,
            method="POST",
            path="/v1/chat/completions",
            status=upstream_status,
            model=prepared.original_model,
            upstream_status=upstream_status,
            storage_backend=runtime.config.storage_backend,
        )
        _finish_trace(
            trace, "completed", http_status=upstream_status, stream=True
        )
    except asyncio.CancelledError:
        _finish_trace(trace, "client_disconnected", stream=True)
        raise
    except httpx.ReadTimeout as exc:
        LOG.warning("upstream streaming response read timed out: %s", exc)
        METRICS.record_upstream_transport_error(
            model=prepared.upstream_model, reason=exc.__class__.__name__
        )
        METRICS.record_upstream_request(
            model=prepared.upstream_model,
            status=504,
            duration_seconds=time.monotonic() - upstream_started,
        )
        _finish_trace(trace, "upstream_error", http_status=504, stream=True)
        yield _sse_error("Upstream request timed out")
    except httpx.HTTPError as exc:
        LOG.warning("upstream streaming response failed: %s", exc)
        METRICS.record_upstream_transport_error(
            model=prepared.upstream_model, reason=exc.__class__.__name__
        )
        METRICS.record_upstream_request(
            model=prepared.upstream_model,
            status=500,
            duration_seconds=time.monotonic() - upstream_started,
        )
        _finish_trace(trace, "upstream_error", http_status=500, stream=True)
        yield _sse_error(f"Upstream request failed: {exc}")
    finally:
        if not finalized:
            for ctx_scope, ctx_messages in response_contexts:
                accumulator.store_reasoning(
                    runtime.reasoning_store,
                    ctx_scope,
                    prepared.cache_namespace,
                    ctx_messages,
                )
            accumulator.flush_pending_store(runtime.reasoning_store)
        if response is not None:
            await response.aclose()
        METRICS.stream_finished()


async def _handle_embeddings(
    runtime: BridgeRuntimeState, request: Request, request_id: str
) -> Response:
    cursor_auth = _cursor_authorization(request)
    if cursor_auth is None:
        return _json_response(
            runtime,
            request,
            request_id,
            401,
            _error_body(
                "Missing Authorization bearer token",
                "authentication_error",
                "invalid_api_key",
            ),
        )
    try:
        payload = await _read_json_body(runtime, request, None)
    except RequestBodyTooLargeError as exc:
        return _json_response(
            runtime,
            request,
            request_id,
            413,
            _error_body(str(exc), "invalid_request_error", "request_too_large"),
        )
    except ValueError as exc:
        return _json_response(
            runtime,
            request,
            request_id,
            400,
            _error_body(
                str(exc), "invalid_request_error", "invalid_request_error"
            ),
        )

    upstream_body = json.dumps(
        payload, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")
    upstream_url = f"{runtime.config.upstream_base_url}/embeddings"
    upstream_model = str(payload.get("model") or runtime.config.upstream_model)
    upstream_started = time.monotonic()
    response, failure = await _send_upstream_with_retry(
        runtime,
        upstream_url,
        upstream_body,
        _upstream_headers(request, False, cursor_auth),
        stream=False,
        upstream_model=upstream_model,
    )
    if failure is not None:
        METRICS.record_upstream_request(
            model=upstream_model,
            status=failure.status,
            duration_seconds=time.monotonic() - upstream_started,
        )
        return _json_response(
            runtime,
            request,
            request_id,
            failure.status,
            _error_body(failure.message, "server_error", failure.code),
        )
    assert response is not None
    try:
        body = response.content
        METRICS.record_upstream_request(
            model=upstream_model,
            status=response.status_code,
            duration_seconds=time.monotonic() - upstream_started,
        )
        return _raw_response(
            runtime,
            request,
            request_id,
            response.status_code,
            body,
            response.headers.get("Content-Type", "application/json"),
        )
    finally:
        await response.aclose()


async def _handle_api_show(
    runtime: BridgeRuntimeState, request: Request, request_id: str
) -> Response:
    try:
        payload = await _read_json_body(runtime, request, None)
    except ValueError, RequestBodyTooLargeError:
        return _json_response(
            runtime, request, request_id, 400, {"error": "invalid request"}
        )
    model_name = str(payload.get("model") or runtime.config.upstream_model)
    architecture = "deepseek" if "deepseek" in model_name else "custom"
    return _json_response(
        runtime,
        request,
        request_id,
        200,
        {
            "modelfile": f"# Modelfile for {model_name}\nFROM {model_name}\n",
            "template": "{{ .Prompt }}",
            "details": {
                "parent_model": "",
                "format": OLLAMA_FORMAT,
                "family": architecture,
                "families": [architecture],
                "parameter_size": OLLAMA_PARAMETER_SIZE,
                "quantization_level": OLLAMA_QUANTIZATION_LEVEL,
            },
            "model_info": {
                f"{architecture}.context_length": OLLAMA_CONTEXT_LENGTH,
                f"{architecture}.embedding_length": OLLAMA_EMBEDDING_LENGTH,
            },
            "capabilities": {
                "supports": {"tool_calls": True, "vision": False},
                "limits": {
                    "max_prompt_tokens": OLLAMA_CONTEXT_LENGTH,
                    "max_output_tokens": OLLAMA_MAX_OUTPUT_TOKENS,
                },
            },
            "modified_at": OLLAMA_MODIFIED_AT,
        },
    )


async def _send_upstream_with_retry(
    runtime: BridgeRuntimeState,
    upstream_url: str,
    upstream_body: bytes,
    upstream_headers: dict[str, str],
    *,
    stream: bool,
    upstream_model: str,
) -> tuple[httpx.Response | None, UpstreamFailure | None]:
    max_retries = max(0, runtime.config.upstream_retry_attempts)
    for attempt in range(max_retries + 1):
        try:
            response = await runtime.upstream_client.post(
                upstream_url,
                body=upstream_body,
                headers=upstream_headers,
                stream=stream,
            )
            return response, None
        except RETRYABLE_UPSTREAM_ERRORS as exc:
            reason = exc.__class__.__name__
            METRICS.record_upstream_transport_error(
                model=upstream_model, reason=reason
            )
            if attempt < max_retries:
                retry_number = attempt + 1
                METRICS.record_upstream_retry(
                    model=upstream_model,
                    reason=reason,
                    attempt=retry_number,
                )
                await asyncio.sleep(_retry_delay(runtime.config, attempt))
                continue
            METRICS.record_upstream_retry_exhausted(
                model=upstream_model, reason=reason
            )
            return None, UpstreamFailure(
                500,
                f"Upstream request failed after retries: {exc}",
                "upstream_failure",
                reason,
            )
        except httpx.ReadTimeout as exc:
            reason = exc.__class__.__name__
            METRICS.record_upstream_transport_error(
                model=upstream_model, reason=reason
            )
            return None, UpstreamFailure(
                504, "Upstream request timed out", "upstream_timeout", reason
            )
        except httpx.TimeoutException as exc:
            reason = exc.__class__.__name__
            METRICS.record_upstream_transport_error(
                model=upstream_model, reason=reason
            )
            return None, UpstreamFailure(
                504, "Upstream request timed out", "upstream_timeout", reason
            )
        except httpx.HTTPError as exc:
            reason = exc.__class__.__name__
            METRICS.record_upstream_transport_error(
                model=upstream_model, reason=reason
            )
            return None, UpstreamFailure(
                500,
                f"Upstream request failed: {exc}",
                "upstream_failure",
                reason,
            )
    return None, UpstreamFailure(
        500, "Upstream request failed", "upstream_failure", "unknown"
    )


def _retry_delay(config: ProxyConfig, attempt: int) -> float:
    base = config.upstream_retry_initial_delay_seconds * (2**attempt)
    delay = min(base, config.upstream_retry_max_delay_seconds)
    if config.upstream_retry_jitter_seconds > 0:
        delay += random.uniform(0.0, config.upstream_retry_jitter_seconds)
    return float(delay)


def _rewrite_sse_line(
    runtime: BridgeRuntimeState,
    line: bytes,
    original_model: str,
    accumulator: StreamAccumulator,
    cache_namespace: str,
    response_contexts: list[tuple[str, list[dict[str, Any]]]],
    display_adapter: CursorReasoningDisplayAdapter | None,
    recovery_notice: str | None = None,
    trace: TraceRequest | None = None,
    *,
    include_usage: bool = False,
    usage_so_far: dict[str, Any] | None = None,
) -> tuple[bytes, bool, str | None, dict[str, Any] | None]:
    stripped = line.strip()
    if not stripped.startswith(b"data:"):
        return line, False, recovery_notice, None

    data = stripped[len(b"data:") :].strip()
    if data == b"[DONE]":
        for ctx_scope, ctx_messages in response_contexts:
            accumulator.store_reasoning(
                runtime.reasoning_store,
                ctx_scope,
                cache_namespace,
                ctx_messages,
            )
        accumulator.flush_pending_store(runtime.reasoning_store)
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
        if display_adapter is not None:
            closing_chunk = display_adapter.flush_chunk(original_model)
            if closing_chunk is not None:
                prefix += sse_data(closing_chunk)
        if recovery_notice:
            prefix += sse_data(
                recovery_notice_chunk(original_model, recovery_notice)
            )
        return prefix + b"data: [DONE]\n\n", True, None, None

    if (
        (display_adapter is None or not display_adapter._open_choices)
        and b'"reasoning_content"' not in data
        and b'"tool_calls"' not in data
    ):
        result = data.replace(
            b'"model":"deepseek-v4-pro"', f'"model":"{original_model}"'.encode()
        )
        if b'"system_fingerprint"' not in result:
            insert_point = result.rfind(b"}")
            if insert_point > 0:
                fingerprint = (
                    f',"system_fingerprint":"{SYSTEM_FINGERPRINT}"'.encode()
                )
                result = (
                    result[:insert_point] + fingerprint + result[insert_point:]
                )
        ending = b"\r\n" if line.endswith(b"\r\n") else b"\n"
        return b"data: " + result + ending, False, recovery_notice, None

    try:
        chunk = orjson.loads(data)
    except orjson.JSONDecodeError:
        return line, False, recovery_notice, None

    if isinstance(chunk, dict):
        if recovery_notice and inject_recovery_notice(chunk, recovery_notice):
            recovery_notice = None
        accumulator.ingest_chunk(chunk)
        for scope, prior_messages in response_contexts:
            accumulator.store_ready_reasoning(
                runtime.reasoning_store, scope, cache_namespace, prior_messages
            )
        accumulator.flush_pending_store(runtime.reasoning_store)
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
            b"data: " + orjson.dumps(chunk) + ending,
            False,
            recovery_notice,
            chunk_usage if isinstance(chunk_usage, dict) else None,
        )
    return line, False, recovery_notice, None


def _prepare_request(
    runtime: BridgeRuntimeState,
    payload: dict[str, Any],
    cursor_auth: str,
    trace: TraceRequest | None,
    path: str,
) -> PreparedRequest:
    prepared = prepare_upstream_request(
        payload,
        runtime.config,
        runtime.reasoning_store,
        authorization=cursor_auth,
    )
    if trace is not None:
        trace.record_transform(prepared)
    if runtime.config.compact:
        LOG.info(
            ">> %s | msg=%s | ctx=%s",
            str(payload.get("model") or runtime.config.upstream_model),
            format_count(message_count(payload)),
            context_status(prepared),
            extra={
                "method": "POST",
                "path": path,
                "model": prepared.original_model,
                "storage_backend": runtime.config.storage_backend,
            },
        )
    else:
        log_cursor_request(payload, runtime.config, method="POST", path=path)
        log_context_summary(prepared)
    return prepared


def _missing_reasoning_error(prepared: PreparedRequest) -> dict[str, Any]:
    missing = prepared.missing_reasoning_messages
    return {
        "error": {
            "message": (
                "deepseek-bridge is running in strict missing-reasoning mode "
                "and cannot automatically recover this thinking-mode tool-call "
                "history because cached DeepSeek reasoning_content is missing "
                f"for {missing} assistant message(s). Restart without "
                "`--missing-reasoning-strategy reject`, or pass "
                "`--missing-reasoning-strategy recover`, so the proxy can "
                "recover from partial chat history automatically."
            ),
            "type": "missing_reasoning_content",
            "code": "missing_reasoning_content",
            "param": None,
            "missing_reasoning_messages": missing,
        }
    }


async def _read_json_body(
    runtime: BridgeRuntimeState,
    request: Request,
    trace: TraceRequest | None,
) -> dict[str, Any]:
    content_length = request.headers.get("Content-Length")
    if content_length:
        try:
            parsed_length = int(content_length)
        except ValueError as exc:
            if trace is not None:
                trace.record_cursor_body_omitted(
                    reason="invalid_content_length"
                )
            raise ValueError("Invalid Content-Length") from exc
        if parsed_length < 0:
            if trace is not None:
                trace.record_cursor_body_omitted(
                    reason="invalid_content_length", body_bytes=parsed_length
                )
            raise ValueError("Invalid Content-Length")
        if parsed_length > runtime.config.max_request_body_bytes:
            if trace is not None:
                trace.record_cursor_body_omitted(
                    reason="body_too_large", body_bytes=parsed_length
                )
            message = (
                "Request body is too large; limit is "
                f"{runtime.config.max_request_body_bytes} bytes"
            )
            raise RequestBodyTooLargeError(message)

    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > runtime.config.max_request_body_bytes:
            if trace is not None:
                trace.record_cursor_body_omitted(
                    reason="body_too_large", body_bytes=len(body)
                )
            message = (
                "Request body is too large; limit is "
                f"{runtime.config.max_request_body_bytes} bytes"
            )
            raise RequestBodyTooLargeError(message)
    raw_body = bytes(body)
    if not raw_body:
        raise ValueError("Request body is empty")
    try:
        payload = json.loads(raw_body.decode("utf-8"))
    except json.JSONDecodeError as exc:
        if trace is not None:
            trace.record_cursor_body_bytes(raw_body)
        raise ValueError(f"Invalid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError("Request body must be a JSON object")
    return payload


def _normalize_completions_payload(payload: dict[str, Any]) -> None:
    if "prompt" in payload and "messages" not in payload:
        prompt = payload.pop("prompt")
        if isinstance(prompt, list):
            payload["messages"] = [
                {"role": "user", "content": str(item)} for item in prompt
            ]
        else:
            payload["messages"] = [{"role": "user", "content": str(prompt)}]
    for legacy_key in ("suffix", "best_of", "echo"):
        payload.pop(legacy_key, None)


def _cursor_authorization(request: Request) -> str | None:
    auth_header = request.headers.get("Authorization", "")
    scheme, separator, token = auth_header.strip().partition(" ")
    if separator != " " or scheme.lower() != "bearer" or not token.strip():
        return None
    return f"Bearer {token.strip()}"


def _upstream_headers(
    request: Request, stream: bool, authorization: str
) -> dict[str, str]:
    headers = {
        "Authorization": authorization,
        "Content-Type": "application/json",
        "Accept": "text/event-stream" if stream else "application/json",
        "Accept-Encoding": "gzip, deflate",
        "User-Agent": f"DeepSeekBridge/{__version__}",
    }
    accept_language = request.headers.get("Accept-Language")
    if accept_language:
        headers["Accept-Language"] = accept_language
    return headers


def _json_response(
    runtime: BridgeRuntimeState,
    request: Request,
    request_id: str,
    status: int,
    payload: dict[str, Any],
    *,
    trace: TraceRequest | None = None,
) -> Response:
    body = orjson.dumps(payload)
    if trace is not None:
        trace.record_cursor_response(
            status=status,
            headers={
                "Content-Type": "application/json",
                "Content-Length": str(len(body)),
            },
            body=body,
        )
    response = Response(
        body,
        status_code=status,
        headers={
            "Content-Type": "application/json",
            "Content-Length": str(len(body)),
        },
    )
    return _with_common_headers(response, runtime, request, request_id)


def _raw_response(
    runtime: BridgeRuntimeState,
    request: Request,
    request_id: str,
    status: int,
    body: bytes,
    content_type: str,
) -> Response:
    response = Response(
        body,
        status_code=status,
        headers={
            "Content-Type": content_type,
            "Content-Length": str(len(body)),
        },
    )
    return _with_common_headers(response, runtime, request, request_id)


def _with_common_headers(
    response: Response,
    runtime: BridgeRuntimeState,
    request: Request,
    request_id: str,
) -> Response:
    for name, value in _cors_headers(runtime.config, request).items():
        response.headers[name] = value
    response.headers["x-request-id"] = request_id
    return response


def _cors_headers(config: ProxyConfig, request: Request) -> dict[str, str]:
    if not config.cors:
        return {}
    headers = {
        "Access-Control-Allow-Methods": "POST, GET, OPTIONS",
        "Access-Control-Allow-Headers": (
            "Origin, Content-Type, Accept, Authorization"
        ),
        "Access-Control-Expose-Headers": "Content-Length",
    }
    raw_origin = request.headers.get("Origin")
    origin = raw_origin.strip() if raw_origin else ""
    allowed_entries = tuple(
        entry.strip().rstrip("/") for entry in config.cors_allowed_origins
    )
    wildcard_allowed = "*" in allowed_entries
    origin_is_allowed = (
        bool(origin)
        and _safe_origin_header(origin)
        and _origin_is_allowed(origin, config.cors_allowed_origins)
    )
    if origin:
        headers["Vary"] = "Origin"
    if origin_is_allowed:
        if config.cors_allow_credentials:
            headers["Access-Control-Allow-Origin"] = origin
            headers["Access-Control-Allow-Credentials"] = "true"
        elif wildcard_allowed:
            headers["Access-Control-Allow-Origin"] = "*"
        else:
            headers["Access-Control-Allow-Origin"] = origin
    elif wildcard_allowed and not config.cors_allow_credentials:
        headers["Access-Control-Allow-Origin"] = "*"
    return headers


def _sse_error(message: str) -> bytes:
    return (
        b"data: "
        + orjson.dumps(_error_body(message, "upstream_error", "upstream_error"))
        + b"\n\n"
    )


def _start_trace(
    runtime: BridgeRuntimeState, request: Request, path: str
) -> TraceRequest | None:
    if runtime.trace_writer is None:
        return None
    try:
        return runtime.trace_writer.start_request(
            method=request.method,
            path=path,
            client_address=_client_host(request),
            headers=dict(request.headers.items()),
        )
    except OSError as exc:
        LOG.warning("failed to start request trace: %s", exc)
        return None


def _finish_trace(
    trace: TraceRequest | None, status: str, **extra: Any
) -> None:
    if trace is None:
        return
    try:
        trace.finish(status, **extra)
    except OSError as exc:
        LOG.warning("failed to write request trace: %s", exc)


def _record_body_omitted_for_trace(
    runtime: BridgeRuntimeState, request: Request, trace: TraceRequest | None
) -> None:
    if trace is None:
        return
    content_length = request.headers.get("Content-Length")
    if not content_length:
        trace.record_cursor_body_omitted(reason="not_read")
        return
    try:
        parsed_length = int(content_length)
    except ValueError:
        trace.record_cursor_body_omitted(reason="invalid_content_length")
        return
    if parsed_length > runtime.config.max_request_body_bytes:
        trace.record_cursor_body_omitted(
            reason="body_too_large", body_bytes=parsed_length
        )
        return
    trace.record_cursor_body_omitted(
        reason="not_read", body_bytes=parsed_length
    )


def _record_upstream_error_trace(
    trace: TraceRequest | None, response: httpx.Response, body: bytes
) -> None:
    if trace is None:
        return
    headers = {
        "Content-Type": response.headers.get(
            "Content-Type", "application/json"
        ),
        "Content-Length": str(len(body)),
    }
    trace.record_upstream_response(
        status=response.status_code,
        headers=dict(response.headers.items()),
        body=body,
    )
    trace.record_cursor_response(
        status=response.status_code, headers=headers, body=body
    )


def _client_host(request: Request) -> str:
    return request.client.host if request.client is not None else ""


def _models(runtime: BridgeRuntimeState) -> dict[str, Any]:
    model_ids = list(
        dict.fromkeys(
            [
                runtime.config.upstream_model,
                "deepseek-v4-pro",
                "deepseek-v4-flash",
            ]
        )
    )
    return {
        "object": "list",
        "data": [
            {
                "id": model_id,
                "object": "model",
                "created": MODEL_CREATED_TIMESTAMPS.get(model_id, 1735689600),
                "owned_by": "deepseek",
            }
            for model_id in model_ids
        ],
    }


def _api_tags(runtime: BridgeRuntimeState) -> dict[str, Any]:
    model_ids = list(
        dict.fromkeys(
            [
                runtime.config.upstream_model,
                "deepseek-v4-pro",
                "deepseek-v4-flash",
            ]
        )
    )
    models = []
    for model_id in model_ids:
        digest = hashlib.sha256(model_id.encode()).hexdigest()
        family = "deepseek" if "deepseek" in model_id else "custom"
        models.append(
            {
                "name": model_id,
                "model": model_id,
                "modified_at": OLLAMA_MODIFIED_AT,
                "size": OLLAMA_MODEL_SIZE,
                "digest": f"sha256:{digest}",
                "details": {
                    "format": OLLAMA_FORMAT,
                    "family": family,
                    "families": [family],
                    "parameter_size": OLLAMA_PARAMETER_SIZE,
                    "quantization_level": OLLAMA_QUANTIZATION_LEVEL,
                },
            }
        )
    return {"models": models}
