from __future__ import annotations

import contextlib
import hashlib
import http.client
import json
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from http.client import HTTPException
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import urlparse

import urllib3
import urllib3.exceptions

from deepseek_bridge import __version__
from .config import ProxyConfig
from .helpers import (
    MODEL_CREATED_TIMESTAMPS,
    ProxyResponseResult,
    SYSTEM_FINGERPRINT,
    RequestBodyTooLargeError,
    _error_body,
    _generate_request_id,
    context_status,
    elapsed_ms,
    format_count,
    inject_recovery_notice,
    log_bytes,
    log_context_summary,
    log_cursor_request,
    log_json,
    log_send_summary,
    log_stats_summary,
    message_count,
    read_response_body,
    recovery_notice_chunk,
    sse_data,
    summarize_chat_payload,
    usage_from_body,
)
from .logging import LOG, TerminalSpinner
from .reasoning_store import ReasoningStore, conversation_scope
from .streaming import CursorReasoningDisplayAdapter, StreamAccumulator
from .trace import TraceRequest, TraceWriter
from .transform import (
    prepare_upstream_request,
    rewrite_response_body,
)


class UpstreamPool:
    def __init__(self, max_connections: int = 10):
        self._pool = urllib3.PoolManager(
            maxsize=max_connections,
            block=True,
            retries=urllib3.Retry(connect=1, read=0, redirect=0, status=0),
        )


class DeepSeekProxyServer(ThreadingHTTPServer):
    config: ProxyConfig
    reasoning_store: ReasoningStore
    trace_writer: TraceWriter | None
    upstream_pool: UpstreamPool
    request_count: int = 0
    start_time: float = 0.0


class BoundedThreadPoolHTTPServer(DeepSeekProxyServer):
    """ThreadingHTTPServer variant that uses a fixed-size ThreadPoolExecutor."""

    def __init__(self, *args, max_workers: int = 20, **kwargs):
        super().__init__(*args, **kwargs)
        self.executor = ThreadPoolExecutor(
            max_workers=max_workers,
            thread_name_prefix="proxy",
        )
        self.daemon_threads = True

    def server_bind(self) -> None:
        super().server_bind()
        config = getattr(self, "config", None)
        timeout = int(config.request_timeout) if config is not None else 300
        self.socket.settimeout(timeout)

    def process_request(self, request, client_address) -> None:
        # Apply server socket timeout to accepted client connections
        if hasattr(request, "settimeout"):
            request.settimeout(
                self.socket.gettimeout() if hasattr(self, "socket") else int(getattr(getattr(self, "config", None), "request_timeout", 300))
            )
        # Check queue size before submitting — reject if overloaded
        queue_size = self.executor._work_queue.qsize()
        config = getattr(self, "config", None)
        effective_max_queue = config.max_queue_size if config is not None else 50
        if queue_size > effective_max_queue:
            LOG.warning(
                "rejecting request from %s: queue full (%s queued)",
                client_address, queue_size,
            )
            self._reject_connection(request)
            return
        with contextlib.suppress(RuntimeError):
            self.executor.submit(
                self.process_request_thread, request, client_address
            )

    @staticmethod
    def _reject_connection(request: Any) -> None:
        """Send a 503 and close the connection."""
        import socket
        try:
            if isinstance(request, socket.socket):
                body = json.dumps({
                    "error": {
                        "message": "Server overloaded — too many queued requests",
                        "type": "server_error",
                        "code": "service_unavailable",
                    }
                }).encode("utf-8")
                request.sendall(
                    b"HTTP/1.1 503 Service Unavailable\r\n"
                    b"Content-Type: application/json\r\n"
                    b"Content-Length: " + str(len(body)).encode("utf-8") + b"\r\n"
                    b"Connection: close\r\n"
                    b"\r\n"
                    + body
                )
                request.close()
        except Exception:
            pass

    def _log_pool_utilization(self) -> None:
        try:
            active: int | str = len(self.executor._threads)
        except Exception:
            active = "?"
        try:
            queue_size: int | str = (
                self.executor._work_queue.qsize()
                if hasattr(self.executor, "_work_queue")
                else "?"
            )
        except Exception:
            queue_size = "?"
        LOG.info(
            "thread pool: max_workers=%s active=%s queue=%s",
            self.executor._max_workers,
            active,
            queue_size,
        )

    def _log_db_stats(self) -> None:
        try:
            store = self.reasoning_store
            if not isinstance(store.reasoning_content_path, Path):
                return
            size_mb = store.reasoning_content_path.stat().st_size / (1024 * 1024)
            row = store._conn.execute("SELECT COUNT(*) FROM reasoning_cache").fetchone()
            row_count = int(row[0]) if row else 0
            LOG.info(
                "db stats: %s size=%.1fMB rows=%s",
                store.reasoning_content_path,
                size_mb,
                format_count(row_count),
            )
        except Exception as exc:
            LOG.warning("failed to log DB stats: %s", exc)

    def _log_heartbeat(self) -> None:
        parts = [f"heartbeat: req={format_count(self.request_count)}"]
        try:
            parts.append(
                f"pool={len(self.executor._threads)}/{self.executor._max_workers}"
            )
        except Exception:
            parts.append("pool=?")
        try:
            store = self.reasoning_store
            if isinstance(store.reasoning_content_path, Path):
                size_mb = store.reasoning_content_path.stat().st_size / (1024 * 1024)
                row = store._conn.execute(
                    "SELECT COUNT(*) FROM reasoning_cache"
                ).fetchone()
                row_count = int(row[0]) if row else 0
                parts.append(f"db={size_mb:.0f}MB/{format_count(row_count)}rows")
        except Exception:
            parts.append("db=?")
        uptime = int(time.monotonic() - self.start_time)
        parts.append(f"uptime={uptime // 60}m")
        LOG.info(" | ".join(parts))

    def server_close(self):
        self.executor.shutdown(wait=True, cancel_futures=False)
        super().server_close()


class DeepSeekProxyHandler(BaseHTTPRequestHandler):
    server_version = f"DeepSeekBridge/{__version__}"

    @property
    def config(self) -> ProxyConfig:
        return self.server.config  # type: ignore[attr-defined]

    @property
    def reasoning_store(self) -> ReasoningStore:
        return self.server.reasoning_store  # type: ignore[attr-defined]

    @property
    def trace_writer(self) -> TraceWriter | None:
        return getattr(self.server, "trace_writer", None)

    @property
    def upstream_pool(self) -> UpstreamPool:
        return self.server.upstream_pool  # type: ignore[attr-defined]

    def log_message(self, fmt: str, *args: Any) -> None:
        return

    def do_OPTIONS(self) -> None:
        self._request_id = _generate_request_id()
        request_path = urlparse(self.path).path
        if self.config.verbose:
            LOG.info(
                "incoming OPTIONS %s from %s",
                request_path,
                self.client_address[0],
            )
        self._send_response_headers(204, [], "sending CORS preflight response")

    def do_GET(self) -> None:
        self._request_id = _generate_request_id()
        request_path = urlparse(self.path).path
        if self.config.verbose:
            LOG.info("incoming GET %s from %s", request_path, self.client_address[0])
        if self.config.ollama and request_path == "/api/version":
            self._handle_api_version()
            return
        if self.config.ollama and request_path == "/api/tags":
            self._handle_api_tags()
            return
        if request_path in {"/healthz", "/v1/healthz", "/health", "/v1/health"}:
            self._send_health()
            return
        if request_path in {"/models", "/v1/models"}:
            self._send_models()
            return
        self._send_json(
            404,
            _error_body("Not found", "invalid_request_error", "endpoint_not_found"),
        )

    def do_POST(self) -> None:
        self._request_id = _generate_request_id()
        self.server.request_count += 1  # type: ignore[attr-defined]
        if self.server.request_count % 100 == 0:  # type: ignore[attr-defined]
            self.server._log_heartbeat()  # type: ignore[attr-defined]
        started = time.monotonic()
        request_path = urlparse(self.path).path
        trace = self._start_trace(request_path)
        if self.config.verbose:
            LOG.info(
                "incoming POST %s from %s content_length=%s user_agent=%s",
                request_path,
                self.client_address[0],
                self.headers.get("Content-Length", "0"),
                self.headers.get("User-Agent", ""),
            )
        if self.config.ollama and request_path == "/api/show":
            self._handle_api_show()
            self._finish_trace(trace, "completed")
            return
        if request_path in {"/embeddings", "/v1/embeddings"}:
            if self.config.verbose:
                LOG.info(
                    "incoming embeddings request from %s",
                    self.client_address[0],
                )
            self._handle_embeddings_request()
            self._finish_trace(trace, "completed")
            return
        if request_path not in {
            "/chat/completions",
            "/v1/chat/completions",
            "/completions",
            "/v1/completions",
        }:
            LOG.warning("rejected unsupported POST path=%s status=404", request_path)
            self._record_request_body_for_trace(trace)
            self._send_json(
                404,
                _error_body(
                    "Only /v1/chat/completions and /v1/completions are supported",
                    "invalid_request_error",
                    "endpoint_not_found",
                ),
                trace=trace,
            )
            self._finish_trace(trace, "rejected", http_status=404)
            return
        cursor_authorization = self._cursor_authorization()
        if cursor_authorization is None:
            LOG.warning(
                "rejected request path=%s status=401 reason=missing_bearer_token",
                request_path,
            )
            self._record_request_body_for_trace(trace)
            self._send_json(
                401,
                _error_body(
                    "Missing Authorization bearer token",
                    "authentication_error",
                    "invalid_api_key",
                ),
                trace=trace,
            )
            self._finish_trace(trace, "rejected", http_status=401)
            return

        try:
            payload = self._read_json_body()
        except RequestBodyTooLargeError as exc:
            LOG.warning(
                "rejected request path=%s status=413 reason=%s", request_path, exc
            )
            self._send_json(
                413,
                _error_body(str(exc), "invalid_request_error", "request_too_large"),
                trace=trace,
            )
            self._finish_trace(trace, "rejected", http_status=413, reason=str(exc))
            return
        except ValueError as exc:
            LOG.warning(
                "rejected request path=%s status=400 reason=%s", request_path, exc
            )
            self._send_json(
                400,
                _error_body(str(exc), "invalid_request_error", "invalid_request_error"),
                trace=trace,
            )
            self._finish_trace(trace, "rejected", http_status=400, reason=str(exc))
            return

        if request_path in {"/completions", "/v1/completions"}:
            if "prompt" in payload and "messages" not in payload:
                prompt = payload.pop("prompt")
                if isinstance(prompt, list):
                    payload["messages"] = [
                        {"role": "user", "content": str(p)} for p in prompt
                    ]
                else:
                    payload["messages"] = [{"role": "user", "content": str(prompt)}]
            for legacy_key in ("suffix", "best_of", "echo"):
                payload.pop(legacy_key, None)

        if getattr(self.server, "paused", False):
            LOG.warning("rejecting request from %s: server paused", self.client_address[0])
            self._send_json(
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
            self._finish_trace(trace, "rejected", http_status=503)
            return

        if trace is not None:
            trace.record_cursor_body(payload)

        if self.config.verbose:
            log_json("cursor request body", payload)

        # --- Responses API → Chat Completions conversion ---
        # Cursor Agent mode sends Responses API-shaped payloads to the
        # /chat/completions endpoint.  Detect and convert them inline so the
        # rest of the pipeline (prepare_upstream_request, etc.) sees a standard
        # Chat Completions dict.
        if request_path in {"/chat/completions", "/v1/chat/completions"}:
            try:
                from .responses_converter import (
                    convert_responses_to_chat,
                    detect_responses_payload,
                )

                if detect_responses_payload(payload):
                    payload = convert_responses_to_chat(payload)
                    if self.config.verbose:
                        LOG.info("converted Responses API format to Chat Completions")
                    if trace is not None:
                        trace.record_cursor_body(payload)
            except ImportError:
                pass  # converter module not available (shouldn't happen)

        prepared = prepare_upstream_request(
            payload,
            self.config,
            self.reasoning_store,
            authorization=cursor_authorization,
        )
        if trace is not None:
            trace.record_transform(prepared)

        if self.config.compact:
            LOG.info(
                ">> %s | msg=%s | ctx=%s",
                str(payload.get("model") or self.config.upstream_model),
                format_count(message_count(payload)),
                context_status(prepared),
            )
        else:
            log_cursor_request(payload, self.config)
            log_context_summary(prepared)
        if (
            prepared.missing_reasoning_messages
            and self.config.missing_reasoning_strategy == "reject"
        ):
            LOG.warning(
                (
                    "strict missing-reasoning mode rejected request path=%s "
                    "status=409 reason=missing_reasoning_content count=%s"
                ),
                request_path,
                prepared.missing_reasoning_messages,
            )
            self._send_json(
                409,
                {
                    "error": {
                        "message": (
                            "deepseek-bridge is running in strict "
                            "missing-reasoning mode and cannot automatically "
                            "recover this thinking-mode tool-call history because "
                            "cached DeepSeek reasoning_content is missing for "
                            f"{prepared.missing_reasoning_messages} assistant "
                            "message(s). Restart without "
                            "`--missing-reasoning-strategy reject`, or pass "
                            "`--missing-reasoning-strategy recover`, so the proxy "
                            "can recover from partial chat history automatically."
                        ),
                        "type": "missing_reasoning_content",
                        "code": "missing_reasoning_content",
                        "param": None,
                        "missing_reasoning_messages": prepared.missing_reasoning_messages,
                    }
                },
                trace=trace,
            )
            self._finish_trace(trace, "rejected", http_status=409)
            return

        if self.config.verbose:
            LOG.info(
                (
                    "upstream request metadata: original_model=%s upstream_model=%s "
                    "patched_reasoning=%s missing_reasoning=%s %s"
                ),
                prepared.original_model,
                prepared.upstream_model,
                prepared.patched_reasoning_messages,
                prepared.missing_reasoning_messages,
                summarize_chat_payload(prepared.payload),
            )

        if self.config.verbose:
            log_json("upstream request body", prepared.payload)

        upstream_body = json.dumps(
            prepared.payload, ensure_ascii=False, separators=(",", ":")
        ).encode("utf-8")
        upstream_url = f"{self.config.upstream_base_url}/chat/completions"
        upstream_headers = self._upstream_headers(
            stream=bool(prepared.payload.get("stream")),
            authorization=cursor_authorization,
        )
        if trace is not None:
            trace.record_upstream_request(
                url=upstream_url,
                headers=upstream_headers,
                body_bytes=upstream_body,
            )
        stream = bool(prepared.payload.get("stream"))

        if self.config.verbose and not self.config.compact:
            log_send_summary(prepared)
        spinner = TerminalSpinner(
            enabled=stream and not self.config.verbose and not self.config.compact,
            text="└ {frame}",
        ).start()

        try:
            if self.config.verbose:
                LOG.info("forwarding to %s", upstream_url)
            timeout = urllib3.Timeout(
                connect=self.config.request_timeout,
                read=(
                    self.config.stream_read_timeout
                    if stream
                    else self.config.request_timeout
                ),
            )
            max_retries = 2
            for attempt in range(max_retries + 1):
                try:
                    response = self.upstream_pool._pool.request(
                        "POST",
                        upstream_url,
                        body=upstream_body,
                        headers=upstream_headers,
                        preload_content=not stream,
                        timeout=timeout,
                    )
                    break
                except (
                    http.client.BadStatusLine,
                    ConnectionError,
                    urllib3.exceptions.ProtocolError,
                ) as exc:
                    if attempt < max_retries:
                        sleep_sec = 1 * (2**attempt)
                        LOG.warning(
                            "upstream request failed (%s), retrying in %ss (attempt %d/%d)",
                            exc,
                            sleep_sec,
                            attempt + 1,
                            max_retries,
                        )
                        time.sleep(sleep_sec)
                        continue
                    # After exhausting retries, send a proper error response
                    spinner.stop()
                    LOG.warning(
                        "upstream request failed after %d retries elapsed_ms=%s reason=%s",
                        max_retries,
                        elapsed_ms(started),
                        exc,
                    )
                    self._send_json(
                        500,
                        _error_body(
                            f"Upstream request failed after retries: {exc}",
                            "server_error",
                            "upstream_failure",
                        ),
                        trace=trace,
                    )
                    self._finish_trace(trace, "upstream_error", http_status=500)
                    return
        except urllib3.exceptions.MaxRetryError as exc:
            spinner.stop()
            LOG.warning(
                "upstream request failed elapsed_ms=%s reason=%s",
                elapsed_ms(started),
                exc.reason,
            )
            self._send_json(
                500,
                _error_body(
                    f"Upstream request failed: {exc.reason}",
                    "server_error",
                    "upstream_failure",
                ),
                trace=trace,
            )
            self._finish_trace(trace, "upstream_error", http_status=500)
            return
        except urllib3.exceptions.TimeoutError:
            spinner.stop()
            LOG.warning(
                "upstream request timed out elapsed_ms=%s",
                elapsed_ms(started),
            )
            self._send_json(
                504,
                _error_body(
                    "Upstream request timed out",
                    "server_error",
                    "upstream_timeout",
                ),
                trace=trace,
            )
            self._finish_trace(trace, "upstream_error", http_status=504)
            return
        except urllib3.exceptions.HTTPError as exc:
            spinner.stop()
            LOG.warning(
                "upstream request failed elapsed_ms=%s reason=%s",
                elapsed_ms(started),
                exc,
            )
            self._send_json(
                500,
                _error_body(
                    f"Upstream request failed: {exc}",
                    "server_error",
                    "upstream_failure",
                ),
                trace=trace,
            )
            self._finish_trace(trace, "upstream_error", http_status=500)
            return
        except Exception:
            spinner.stop()
            raise

        try:
            upstream_status = response.status
            if self.config.verbose:
                LOG.info(
                    "upstream response status=%s stream=%s elapsed_ms=%s",
                    upstream_status,
                    stream,
                    elapsed_ms(started),
                )
            if upstream_status >= 400:
                spinner.stop()
                LOG.warning(
                    "request failed upstream_status=%s stream=%s elapsed_ms=%s",
                    upstream_status,
                    stream,
                    elapsed_ms(started),
                )
                self._send_upstream_error(response, trace=trace)
                self._finish_trace(
                    trace,
                    "upstream_error",
                    http_status=upstream_status,
                    stream=stream,
                )
                return
            if stream:
                include_usage = bool(
                    prepared.payload.get("stream_options", {}).get("include_usage")
                )
                sent_response = self._proxy_streaming_response(
                    response,
                    prepared.original_model,
                    prepared.payload["messages"],
                    prepared.cache_namespace,
                    prepared.recovery_notice,
                    trace=trace,
                    record_response_scope=prepared.record_response_scope,
                    record_response_messages=prepared.record_response_messages,
                    record_response_contexts=prepared.record_response_contexts,
                    include_usage=include_usage,
                )
            else:
                sent_response = self._proxy_regular_response(
                    response,
                    prepared.original_model,
                    prepared.payload["messages"],
                    prepared.cache_namespace,
                    prepared.recovery_notice,
                    trace=trace,
                    record_response_scope=prepared.record_response_scope,
                    record_response_messages=prepared.record_response_messages,
                    record_response_contexts=prepared.record_response_contexts,
                )
            if not sent_response.sent:
                spinner.stop()
                self._finish_trace(
                    trace,
                    "client_disconnected",
                    http_status=upstream_status,
                    stream=stream,
                )
                return
            spinner.stop()
            log_stats_summary(sent_response.usage, elapsed_ms=elapsed_ms(started))
            self._finish_trace(
                trace,
                "completed",
                http_status=upstream_status,
                stream=stream,
            )
        finally:
            spinner.stop()
            if "response" in locals():
                response.release_conn()

    def _start_trace(self, request_path: str) -> TraceRequest | None:
        writer = self.trace_writer
        if writer is None:
            return None
        try:
            return writer.start_request(
                method=self.command,
                path=request_path,
                client_address=self.client_address[0],
                headers=dict(self.headers.items()),
            )
        except OSError as exc:
            LOG.warning("failed to start request trace: %s", exc)
            return None

    def _finish_trace(
        self,
        trace: TraceRequest | None,
        status: str,
        **extra: Any,
    ) -> None:
        if trace is None:
            return
        try:
            trace.finish(status, **extra)
        except OSError as exc:
            LOG.warning("failed to write request trace: %s", exc)

    def _cursor_authorization(self) -> str | None:
        auth_header = self.headers.get("Authorization", "")
        scheme, separator, token = auth_header.strip().partition(" ")
        if separator != " " or scheme.lower() != "bearer" or not token.strip():
            return None
        return f"Bearer {token.strip()}"

    def _send_cors_headers(self) -> None:
        if not self.config.cors:
            return
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, GET, OPTIONS")
        self.send_header(
            "Access-Control-Allow-Headers",
            "Origin, Content-Type, Accept, Authorization",
        )
        self.send_header("Access-Control-Expose-Headers", "Content-Length")
        self.send_header("Access-Control-Allow-Credentials", "true")

    def _send_json(
        self,
        status: int,
        payload: dict[str, Any],
        *,
        trace: TraceRequest | None = None,
    ) -> None:
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode(
            "utf-8"
        )
        if trace is not None:
            trace.record_cursor_response(
                status=status,
                headers={
                    "Content-Type": "application/json",
                    "Content-Length": str(len(body)),
                },
                body=body,
            )
        sent_headers = self._send_response_headers(
            status,
            [
                ("Content-Type", "application/json"),
                ("Content-Length", str(len(body))),
            ],
            "sending JSON response headers",
        )
        if sent_headers:
            self._write_to_client(body, "sending JSON response body")

    def _send_response_headers(
        self,
        status: int,
        headers: list[tuple[str, str]],
        disconnect_context: str,
    ) -> bool:
        try:
            self.send_response(status)
            self._send_cors_headers()
            for name, value in headers:
                self.send_header(name, value)
            if hasattr(self, "_request_id"):
                self.send_header("x-request-id", self._request_id)
            self.end_headers()
        except (BrokenPipeError, ConnectionError) as exc:
            LOG.warning("client disconnected while %s: %s", disconnect_context, exc)
            self.close_connection = True
            return False
        return True

    def _write_to_client(
        self,
        body: bytes,
        disconnect_context: str,
        *,
        flush: bool = False,
    ) -> bool:
        try:
            self.wfile.write(body)
            if flush:
                self.wfile.flush()
        except (BrokenPipeError, ConnectionError) as exc:
            LOG.warning("client disconnected while %s: %s", disconnect_context, exc)
            self.close_connection = True
            return False
        return True

    def _check_client_alive(self) -> bool:
        """Check if downstream client is still connected.

        Uses a zero-byte sendall probe directly on the socket to detect
        disconnected clients without consuming any data.
        """
        import socket
        sock = getattr(self, "request", None)
        if sock is not None:
            # macOS: prevent SIGPIPE via socket option (MSG_NOSIGNAL unavailable)
            if hasattr(socket, "SO_NOSIGPIPE"):
                try:
                    sock.setsockopt(socket.SOL_SOCKET, socket.SO_NOSIGPIPE, 1)
                except OSError:
                    pass
            try:
                flags = getattr(socket, "MSG_NOSIGNAL", 0)
                sock.sendall(b"", flags)
                return True
            except (ConnectionError, BrokenPipeError, OSError):
                return False
        # Fallback for test stubs that don't set self.request
        try:
            self.wfile.write(b"")
            return True
        except (ConnectionError, BrokenPipeError, OSError):
            return False

    def _handle_embeddings_request(self) -> None:
        cursor_authorization = self._cursor_authorization()
        if cursor_authorization is None:
            LOG.warning("rejected embeddings request: missing bearer token")
            self._send_json(
                401,
                _error_body(
                    "Missing Authorization bearer token",
                    "authentication_error",
                    "invalid_api_key",
                ),
            )
            return
        try:
            payload = self._read_json_body()
        except (ValueError, RequestBodyTooLargeError) as exc:
            LOG.warning("rejected embeddings request: %s", exc)
            self._send_json(
                400,
                _error_body(str(exc), "invalid_request_error", "invalid_request_error"),
            )
            return

        model = str(payload.get("model") or self.config.upstream_model)
        upstream_body = json.dumps(
            payload, ensure_ascii=False, separators=(",", ":")
        ).encode("utf-8")
        upstream_url = f"{self.config.upstream_base_url}/embeddings"

        try:
            response = self.upstream_pool._pool.request(
                "POST",
                upstream_url,
                body=upstream_body,
                headers=self._upstream_headers(
                    stream=False, authorization=cursor_authorization
                ),
                preload_content=True,
                timeout=urllib3.Timeout(
                    connect=self.config.request_timeout,
                    read=self.config.request_timeout,
                ),
            )
            try:
                if response.status < 400:
                    body = response.data
                    headers = [
                        ("Content-Type", "application/json"),
                        ("Content-Length", str(len(body))),
                    ]
                    self._send_response_headers(
                        response.status, headers, "sending embeddings response"
                    )
                    self._write_to_client(body, "sending embeddings body")
                else:
                    LOG.warning(
                        "embeddings endpoint not supported by upstream status=%s",
                        response.status,
                    )
                    self._send_json(
                        200,
                        {
                            "object": "list",
                            "data": [],
                            "model": model,
                            "usage": {"prompt_tokens": 0, "total_tokens": 0},
                        },
                    )
            finally:
                response.release_conn()
        except Exception as exc:
            LOG.warning("embeddings request failed: %s", exc)
            self._send_json(
                200,
                {
                    "object": "list",
                    "data": [],
                    "model": model,
                    "usage": {"prompt_tokens": 0, "total_tokens": 0},
                },
            )

    def _send_models(self) -> None:
        model_ids = list(
            dict.fromkeys(
                [
                    self.config.upstream_model,
                    "deepseek-v4-pro",
                    "deepseek-v4-flash",
                ]
            )
        )
        models = [
            {
                "id": model_id,
                "object": "model",
                "created": MODEL_CREATED_TIMESTAMPS.get(model_id, 1735689600),
                "owned_by": "deepseek",
            }
            for model_id in model_ids
        ]
        self._send_json(200, {"object": "list", "data": models})

    def _send_health(self) -> None:
        uptime = (
            int(time.monotonic() - self.server.start_time)
            if hasattr(self.server, "start_time")
            else 0
        )
        self._send_json(
            200,
            {
                "ok": True,
                "server": "deepseek-bridge",
                "uptime_seconds": uptime,
            },
        )

    def _handle_api_version(self) -> None:
        self._request_id = _generate_request_id()
        self._send_json(200, {"version": __version__})

    def _handle_api_tags(self) -> None:
        self._request_id = _generate_request_id()
        model_ids = list(
            dict.fromkeys(
                [
                    self.config.upstream_model,
                    "deepseek-v4-pro",
                    "deepseek-v4-flash",
                ]
            )
        )
        models = []
        for model_id in model_ids:
            models.append(
                {
                    "name": model_id,
                    "model": model_id,
                    "modified_at": "2026-01-01T00:00:00.000Z",
                    "size": 4109865159,
                    "digest": f"sha256:{hashlib.sha256(model_id.encode()).hexdigest()}",
                    "details": {
                        "format": "gguf",
                        "family": "deepseek" if "deepseek" in model_id else "custom",
                        "families": (
                            ["deepseek"] if "deepseek" in model_id else ["custom"]
                        ),
                        "parameter_size": "7B",
                        "quantization_level": "Q4_K_M",
                    },
                }
            )
        self._send_json(200, {"models": models})

    def _handle_api_show(self) -> None:
        self._request_id = _generate_request_id()
        try:
            payload = self._read_json_body()
        except (ValueError, RequestBodyTooLargeError):
            self._send_json(400, {"error": "invalid request"})
            return
        model_name = str(payload.get("model") or self.config.upstream_model)
        is_deepseek = "deepseek" in model_name
        architecture = "deepseek" if is_deepseek else "custom"
        response = {
            "modelfile": f"# Modelfile for {model_name}\nFROM {model_name}\n",
            "template": "{{ .Prompt }}",
            "details": {
                "parent_model": "",
                "format": "gguf",
                "family": architecture,
                "families": [architecture],
                "parameter_size": "7B",
                "quantization_level": "Q4_K_M",
            },
            "model_info": {
                f"{architecture}.context_length": 128000,
                f"{architecture}.embedding_length": 2048,
            },
            "capabilities": {
                "supports": {
                    "tool_calls": True,
                    "vision": False,
                },
                "limits": {
                    "max_prompt_tokens": 128000,
                    "max_output_tokens": 384000,
                },
            },
            "modified_at": "2026-01-01T00:00:00.000Z",
        }
        self._send_json(200, response)

    def _read_json_body(self) -> dict[str, Any]:
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError as exc:
            raise ValueError("Invalid Content-Length") from exc
        if length < 0:
            raise ValueError("Invalid Content-Length")
        if length > self.config.max_request_body_bytes:
            raise RequestBodyTooLargeError(
                f"Request body is too large; limit is {self.config.max_request_body_bytes} bytes"
            )
        try:
            raw_body = self.rfile.read(length)
        except (ConnectionError, OSError) as exc:
            LOG.warning("client disconnected while reading request body: %s", exc)
            raise ValueError("Client disconnected") from exc
        if not raw_body:
            raise ValueError("Request body is empty")
        try:
            payload = json.loads(raw_body.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid JSON: {exc}") from exc
        if not isinstance(payload, dict):
            raise ValueError("Request body must be a JSON object")
        return payload

    def _record_request_body_for_trace(self, trace: TraceRequest | None) -> None:
        if trace is None:
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            trace.record_cursor_body_omitted(reason="invalid_content_length")
            return
        if length < 0:
            trace.record_cursor_body_omitted(
                reason="invalid_content_length", body_bytes=length
            )
            return
        if length > self.config.max_request_body_bytes:
            trace.record_cursor_body_omitted(reason="body_too_large", body_bytes=length)
            self.close_connection = True
            return
        try:
            raw_body = self.rfile.read(length)
        except OSError as exc:
            trace.record_cursor_body_omitted(
                reason=f"read_failed:{exc}", body_bytes=length
            )
            return
        trace.record_cursor_body_bytes(raw_body)

    def _upstream_headers(self, stream: bool, authorization: str) -> dict[str, str]:
        headers = {
            "Authorization": authorization,
            "Content-Type": "application/json",
            "Accept": "text/event-stream" if stream else "application/json",
            "Accept-Encoding": "identity",
            "User-Agent": self.server_version,
        }
        accept_language = self.headers.get("Accept-Language")
        if accept_language:
            headers["Accept-Language"] = accept_language
        return headers

    def _send_upstream_error(
        self,
        response: Any,
        *,
        trace: TraceRequest | None = None,
    ) -> None:
        try:
            body = read_response_body(response)
        except (TimeoutError, OSError, http.client.IncompleteRead, ValueError) as exc2:
            LOG.warning("failed to read upstream error body: %s", exc2)
            body = json.dumps(
                {"error": {"message": "Upstream error, body unreadable"}}
            ).encode("utf-8")
        finally:
            with contextlib.suppress(Exception):
                response.release_conn()
        if self.config.verbose:
            log_bytes("upstream error body", body)
        headers = {
            "Content-Type": response.headers.get("Content-Type", "application/json"),
            "Content-Length": str(len(body)),
        }
        if trace is not None:
            trace.record_upstream_response(
                status=response.status,
                headers=dict(response.headers.items()),
                body=body,
            )
            trace.record_cursor_response(
                status=response.status, headers=headers, body=body
            )
        sent_headers = self._send_response_headers(
            response.status,
            [
                ("Content-Type", headers["Content-Type"]),
                ("Content-Length", headers["Content-Length"]),
            ],
            "sending upstream error headers",
        )
        if sent_headers:
            self._write_to_client(body, "sending upstream error body")

    def _proxy_regular_response(
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
    ) -> ProxyResponseResult:
        try:
            body = read_response_body(response)
        except (TimeoutError, OSError, http.client.IncompleteRead, ValueError) as exc:
            LOG.warning("failed to read upstream response body: %s", exc)
            self._send_json(
                500,
                _error_body(
                    f"Failed to read upstream response body: {exc}",
                    "server_error",
                    "response_read_failed",
                ),
                trace=trace,
            )
            return ProxyResponseResult(False, None)
        upstream_body = body
        usage = usage_from_body(upstream_body)
        try:
            body = rewrite_response_body(
                body,
                original_model,
                self.reasoning_store,
                request_messages,
                cache_namespace,
                content_prefix=recovery_notice,
                scope=record_response_scope,
                prior_messages=record_response_messages,
                recording_contexts=record_response_contexts,
                display_reasoning=self.config.display_reasoning,
                collapsible_reasoning=self.config.collapsible_reasoning,
            )
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            LOG.warning("failed to rewrite upstream JSON response: %s", exc)

        if self.config.verbose:
            log_bytes("cursor response body", body)

        headers = {
            "Content-Type": response.headers.get("Content-Type", "application/json"),
            "Content-Length": str(len(body)),
        }
        if trace is not None:
            trace.record_upstream_response(
                status=response.status,
                headers=dict(response.headers.items()),
                body=upstream_body,
                stream=False,
            )
            try:
                upstream_payload = json.loads(upstream_body.decode("utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError):
                upstream_payload = None
            if isinstance(upstream_payload, dict):
                trace.record_usage(upstream_payload.get("usage"))
            trace.record_cursor_response(
                status=response.status,
                headers=headers,
                body=body,
            )

        sent_headers = self._send_response_headers(
            response.status,
            [
                ("Content-Type", headers["Content-Type"]),
                ("Content-Length", headers["Content-Length"]),
            ],
            "sending upstream response headers",
        )
        if not sent_headers:
            return ProxyResponseResult(False, usage)
        sent = self._write_to_client(body, "sending upstream response body")
        return ProxyResponseResult(sent, usage)

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
            while True:
                if not self._check_client_alive():
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
            # Store partial reasoning whenever the stream exits without
            # the upstream's [DONE] terminator (client disconnect, upstream
            # read failure, exception). Without this, a Stop pressed mid-stream
            # would discard any reasoning the proxy received but never cached.
            if not finalized:
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
