from __future__ import annotations

import contextlib
import json
from http.client import IncompleteRead
from typing import Any

from ..helpers import (
    log_bytes,
    read_response_body,
)
from ..logging import LOG
from ..trace import TraceRequest


class HandlerResponse:
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
        LOG.debug("handler.response: sending %s, content-length=%s", status, len(body))
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
