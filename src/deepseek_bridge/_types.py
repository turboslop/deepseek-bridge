from __future__ import annotations

from dataclasses import dataclass
from typing import Any


class RequestBodyTooLargeError(ValueError):
    pass


def _error_body(
    message: str,
    error_type: str,
    code: str | None = None,
) -> dict[str, Any]:
    err: dict[str, Any] = {"message": str(message)}
    err["type"] = error_type
    if code:
        err["code"] = code
    err["param"] = None
    return {"error": err}


@dataclass
class ProxyResponseResult:
    sent: bool
    usage: dict[str, Any] | None = None
