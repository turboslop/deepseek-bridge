from __future__ import annotations

import contextlib
from http.client import IncompleteRead
from typing import Any
from urllib.parse import urlparse

import orjson

from .._types import _error_body
from ..logging import (
    LOG,
    log_bytes,
    read_response_body,
)
from ..trace import TraceRequest


def _safe_origin_header(origin: str) -> bool:
    if not origin or origin == "*" or "\r" in origin or "\n" in origin:
        return False
    if origin == "null":
        return True
    try:
        parsed = urlparse(origin)
        _ = parsed.port
    except ValueError:
        return False
    return (
        bool(parsed.scheme)
        and bool(parsed.netloc)
        and parsed.path == ""
        and parsed.params == ""
        and parsed.query == ""
        and parsed.fragment == ""
        and parsed.username is None
        and parsed.password is None
    )


def _origin_matches_port_wildcard(origin: str, allowed_origin: str) -> bool:
    if not allowed_origin.endswith(":*"):
        return False
    try:
        request = urlparse(origin)
        allowed = urlparse(allowed_origin[:-2])
    except ValueError:
        return False
    if not request.hostname or not allowed.hostname:
        return False
    return (
        request.scheme.lower() == allowed.scheme.lower()
        and request.hostname.lower() == allowed.hostname.lower()
        and allowed.path == ""
        and allowed.params == ""
        and allowed.query == ""
        and allowed.fragment == ""
    )


def _allowed_origin_entries(
    allowed_origins: tuple[str, ...],
) -> tuple[str, ...]:
    return tuple(origin.strip().rstrip("/") for origin in allowed_origins)


def _origin_is_allowed(origin: str, allowed_origins: tuple[str, ...]) -> bool:
    allowed_entries = _allowed_origin_entries(allowed_origins)
    return (
        "*" in allowed_entries
        or any(
            origin == allowed_origin
            or _origin_matches_exact(origin, allowed_origin)
            for allowed_origin in allowed_entries
        )
        or any(
            _origin_matches_port_wildcard(origin, allowed_origin)
            for allowed_origin in allowed_entries
        )
    )


def _origin_matches_exact(origin: str, allowed_origin: str) -> bool:
    try:
        request = urlparse(origin)
        allowed = urlparse(allowed_origin)
        request_port = request.port
        allowed_port = allowed.port
    except ValueError:
        return False
    return (
        request.scheme.lower() == allowed.scheme.lower()
        and request.hostname is not None
        and allowed.hostname is not None
        and request.hostname.lower() == allowed.hostname.lower()
        and request_port == allowed_port
        and request.path == ""
        and allowed.path == ""
        and request.params == ""
        and allowed.params == ""
        and request.query == ""
        and allowed.query == ""
        and request.fragment == ""
        and allowed.fragment == ""
        and request.username is None
        and allowed.username is None
        and request.password is None
        and allowed.password is None
    )


class HandlerResponse:
    def _send_cors_headers(self) -> None:
        if not self.config.cors:
            return

        headers = getattr(self, "headers", None)
        raw_origin = headers.get("Origin") if headers else None
        origin = raw_origin.strip() if raw_origin else ""
        origin_is_safe = _safe_origin_header(origin)
        origin_is_allowed = origin_is_safe and _origin_is_allowed(
            origin, self.config.cors_allowed_origins
        )
        wildcard_allowed = "*" in _allowed_origin_entries(
            self.config.cors_allowed_origins
        )

        if origin:
            self.send_header("Vary", "Origin")
        if origin_is_allowed:
            if self.config.cors_allow_credentials:
                self.send_header("Access-Control-Allow-Origin", origin)
                self.send_header("Access-Control-Allow-Credentials", "true")
            elif wildcard_allowed:
                self.send_header("Access-Control-Allow-Origin", "*")
            else:
                self.send_header("Access-Control-Allow-Origin", origin)
        elif wildcard_allowed and not self.config.cors_allow_credentials:
            self.send_header("Access-Control-Allow-Origin", "*")

        self.send_header("Access-Control-Allow-Methods", "POST, GET, OPTIONS")
        self.send_header(
            "Access-Control-Allow-Headers",
            "Origin, Content-Type, Accept, Authorization",
        )
        self.send_header("Access-Control-Expose-Headers", "Content-Length")

    def _send_json(
        self,
        status: int,
        payload: dict[str, Any],
        *,
        trace: TraceRequest | None = None,
    ) -> None:
        body = orjson.dumps(payload)
        LOG.debug(
            "handler.response: sending %s, content-length=%s", status, len(body)
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
            LOG.warning(
                "client disconnected while %s: %s", disconnect_context, exc
            )
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
            LOG.warning(
                "client disconnected while %s: %s", disconnect_context, exc
            )
            self.close_connection = True
            return False
        return True

    def _send_upstream_error(
        self,
        response: Any,
        *,
        trace: TraceRequest | None = None,
    ) -> None:
        try:
            body = read_response_body(response)
        except (TimeoutError, OSError, IncompleteRead, ValueError) as exc2:
            LOG.warning("failed to read upstream error body: %s", exc2)
            body = orjson.dumps(
                {"error": {"message": "Upstream error, body unreadable"}}
            )
        finally:
            with contextlib.suppress(Exception):
                response.release_conn()
        if self.config.debug:
            log_bytes("upstream error body", body)
        headers = {
            "Content-Type": response.headers.get(
                "Content-Type", "application/json"
            ),
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

    def _send_sse_error(
        self,
        status: int,
        message: str,
        *,
        trace: TraceRequest | None = None,
    ) -> bool:
        """Send an SSE-formatted error to the client.

        Used when upstream returns 4xx/5xx after streaming headers have
        already been sent (so normal HTTP error response is impossible).
        Sends an SSE data: line with OpenAI-compatible error format.
        """
        error_body = orjson.dumps(
            _error_body(message, "upstream_error", "upstream_error"),
        )
        sse_line = b"data: " + error_body + b"\n\n"
        return self._write_to_client(
            sse_line, "sending SSE error body", flush=True
        )
