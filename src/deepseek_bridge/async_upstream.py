from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Final

import httpx

from .config import ProxyConfig

RETRYABLE_UPSTREAM_ERRORS: Final = (
    httpx.ConnectError,
    httpx.ConnectTimeout,
    httpx.PoolTimeout,
    httpx.RemoteProtocolError,
    httpx.WriteError,
    httpx.WriteTimeout,
)


class AsyncUpstreamClient:
    """Async HTTP client for DeepSeek upstream requests."""

    def __init__(self, config: ProxyConfig) -> None:
        self._config = config
        self._client = httpx.AsyncClient(
            limits=httpx.Limits(
                max_connections=config.max_pool_connections,
                max_keepalive_connections=config.max_pool_connections,
            ),
            headers={"User-Agent": "DeepSeekBridge"},
        )

    @property
    def is_closed(self) -> bool:
        return self._client.is_closed

    async def post(
        self,
        url: str,
        *,
        body: bytes,
        headers: dict[str, str],
        stream: bool,
    ) -> httpx.Response:
        read_timeout = (
            self._config.stream_read_timeout
            if stream
            else self._config.request_timeout
        )
        timeout = httpx.Timeout(
            connect=self._config.request_timeout,
            read=read_timeout,
            write=self._config.request_timeout,
            pool=self._config.request_timeout,
        )
        request = self._client.build_request(
            "POST",
            url,
            content=body,
            headers=headers,
            timeout=timeout,
        )
        return await self._client.send(request, stream=stream)

    async def aclose(self) -> None:
        await self._client.aclose()


async def iter_response_lines(response: httpx.Response) -> AsyncIterator[bytes]:
    """Yield response bytes as line chunks while preserving line endings."""
    pending = b""
    async for chunk in response.aiter_bytes():
        pending += chunk
        while True:
            newline = pending.find(b"\n")
            if newline < 0:
                break
            line = pending[: newline + 1]
            pending = pending[newline + 1 :]
            yield line
    if pending:
        yield pending
