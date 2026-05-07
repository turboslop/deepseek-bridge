from __future__ import annotations

import json
from typing import Any

from ..helpers import RequestBodyTooLargeError
from ..logging import LOG


class HandlerClient:
    def _cursor_authorization(self) -> str | None:
        auth_header = self.headers.get("Authorization", "")
        scheme, separator, token = auth_header.strip().partition(" ")
        if separator != " " or scheme.lower() != "bearer" or not token.strip():
            return None
        return f"Bearer {token.strip()}"

    def _check_client_alive(self) -> bool:
        import socket
        sock = getattr(self, "request", None)
        if sock is not None:
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
        try:
            self.wfile.write(b"")
            return True
        except (ConnectionError, BrokenPipeError, OSError):
            return False

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
