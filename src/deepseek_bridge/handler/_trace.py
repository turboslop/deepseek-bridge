from __future__ import annotations

from typing import Any

from ..logging import LOG
from ..trace import TraceRequest


class HandlerTrace:
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
