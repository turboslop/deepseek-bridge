from __future__ import annotations

import json
from http.client import IncompleteRead
from typing import Any

from ..helpers import ProxyResponseResult, _error_body, log_bytes, read_response_body, usage_from_body
from ..logging import LOG
from ..trace import TraceRequest
from ..transform import rewrite_response_body


class HandlerUpstream:
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
        except (TimeoutError, OSError, IncompleteRead, ValueError) as exc:
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
