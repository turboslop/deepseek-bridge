from __future__ import annotations

import http.client
import json
import ssl
import time
from urllib.parse import urlparse

import urllib3
import urllib3.exceptions

from ..helpers import (
    RequestBodyTooLargeError,
    _error_body,
    _generate_request_id,
    context_status,
    elapsed_ms,
    format_count,
    log_context_summary,
    log_cursor_request,
    log_json,
    log_send_summary,
    log_stats_summary,
    message_count,
    summarize_chat_payload,
)
from ..logging import LOG, TerminalSpinner
from ..transform import prepare_upstream_request


class HandlerRoutes:
    def do_OPTIONS(self) -> None:
        self._request_id = _generate_request_id()
        request_path = urlparse(self.path).path
        if self.config.debug:
            LOG.info(
                "incoming OPTIONS %s from %s",
                request_path,
                self.client_address[0],
            )
        self._send_response_headers(204, [], "sending CORS preflight response")

    def do_GET(self) -> None:
        self._request_id = _generate_request_id()
        request_path = urlparse(self.path).path
        if self.config.debug:
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
        self.server.request_count += 1
        if self.server.request_count % 100 == 0:
            self.server._log_heartbeat()
        started = time.monotonic()
        request_path = urlparse(self.path).path
        trace = self._start_trace(request_path)
        LOG.debug(
            "handler.request: POST %s from %s, content-length=%s",
            request_path,
            self.client_address[0],
            self.headers.get("Content-Length", "0"),
        )
        if self.config.debug:
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
            if self.config.debug:
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

        if self.config.debug:
            log_json("cursor request body", payload)

        # --- Responses API → Chat Completions conversion ---
        # Cursor Agent mode sends Responses API-shaped payloads to the
        # /chat/completions endpoint.  Detect and convert them inline so the
        # rest of the pipeline (prepare_upstream_request, etc.) sees a standard
        # Chat Completions dict.
        if request_path in {"/chat/completions", "/v1/chat/completions"}:
            try:
                from ..responses_converter import (
                    convert_responses_to_chat,
                    detect_responses_payload,
                )

                if detect_responses_payload(payload):
                    payload = convert_responses_to_chat(payload)
                    if self.config.debug:
                        LOG.info("converted Responses API format to Chat Completions")
                    if trace is not None:
                        trace.record_cursor_body(payload)
            except ImportError:
                pass  # converter module not available; proceed without conversion

        if not self._check_client_alive():
            LOG.info("client disconnected before message normalization")
            self._finish_trace(trace, "aborted")
            return

        prepared = prepare_upstream_request(
            payload,
            self.config,
            self.reasoning_store,
            authorization=cursor_authorization,
        )
        LOG.debug("handler.request: auth ok, model=%s", prepared.upstream_model)
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

        if self.config.debug:
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

        if self.config.debug:
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

        if self.config.debug and not self.config.compact:
            log_send_summary(prepared)
        spinner = TerminalSpinner(
            enabled=stream and not self.config.debug and not self.config.compact,
            text="└ {frame}",
        ).start()

        if not self._check_client_alive():
            LOG.info("client disconnected before upstream request")
            self._finish_trace(trace, "aborted")
            return

        LOG.debug(
            "handler.upstream: forwarding to %s, stream=%s",
            upstream_url,
            stream,
        )
        try:
            if self.config.debug:
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
                    ssl.SSLError,
                    urllib3.exceptions.SSLError,
                ) as exc:
                    if attempt < max_retries:
                        if not self._check_client_alive():
                            LOG.info("client disconnected before upstream retry")
                            spinner.stop()
                            self._finish_trace(trace, "aborted")
                            return
                        sleep_sec = 1 * (2**attempt)
                        LOG.warning(
                            "upstream request failed (%s), retrying in %ss (attempt %d/%d)",
                            exc,
                            sleep_sec,
                            attempt + 1,
                            max_retries,
                        )
                        LOG.debug(
                            "handler.upstream: retry %s/%s, reason=%s",
                            attempt + 1,
                            max_retries,
                            exc,
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
            raise  # Stop spinner before re-raising unexpected errors

        try:
            upstream_status = response.status
            if self.config.debug:
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
