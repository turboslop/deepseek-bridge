from __future__ import annotations

import importlib
import json
import time
from collections.abc import Iterable
from contextlib import suppress
from typing import Any, Protocol, cast, runtime_checkable

from .logging import INTERNAL_LOG, LOG
from .metrics import METRICS
from .reasoning_store import ReasoningStoreBase, ReasoningStoreStats


@runtime_checkable
class ValkeyClientProtocol(Protocol):
    def set(self, name: str, value: str, ex: int | None = None) -> object:
        raise NotImplementedError

    def get(self, name: str) -> object:
        raise NotImplementedError

    def delete(self, *names: str) -> object:
        raise NotImplementedError

    def scan_iter(
        self, match: str | None = None, count: int | None = None
    ) -> Iterable[object]:
        raise NotImplementedError

    def ping(self) -> object:
        raise NotImplementedError

    def zadd(self, name: str, mapping: dict[str, float]) -> object:
        raise NotImplementedError

    def zcard(self, name: str) -> object:
        raise NotImplementedError

    def zrange(self, name: str, start: int, end: int) -> list[object]:
        raise NotImplementedError

    def zrem(self, name: str, *values: str) -> object:
        raise NotImplementedError

    def zremrangebyscore(
        self, name: str, min_score: float | str, max_score: float | str
    ) -> object:
        raise NotImplementedError

    def close(self) -> object:
        raise NotImplementedError


def _safe_exception_name(exc: Exception) -> str:
    return type(exc).__name__


def _to_key(value: object) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def _to_int(value: object) -> int | None:
    if isinstance(value, int):
        return value
    if isinstance(value, str | bytes | bytearray):
        try:
            return int(value)
        except ValueError:
            return None
    return None


class ValkeyReasoningStore(ReasoningStoreBase):
    backend_name = "valkey"

    def __init__(
        self,
        url: str,
        *,
        key_prefix: str = "deepseek-bridge",
        max_age_seconds: int | None = None,
        max_rows: int | None = None,
        max_connections: int = 10,
        client: ValkeyClientProtocol | None = None,
    ) -> None:
        self.max_age_seconds = max_age_seconds
        self._max_rows = max_rows
        self._closed = False
        self._key_prefix = key_prefix.strip().strip(":")
        if not self._key_prefix:
            raise ValueError("Valkey key prefix must not be empty")
        self._index_key = f"{self._key_prefix}:reasoning:__index__"
        self._client = client or self._create_client(url, max_connections)

    @staticmethod
    def _create_client(url: str, max_connections: int) -> ValkeyClientProtocol:
        if not url.strip():
            raise ValueError("Valkey URL is required for valkey storage")
        try:
            valkey_module: Any = importlib.import_module("valkey")
        except ImportError as exc:
            raise RuntimeError(
                "valkey storage requires the 'valkey' Python package"
            ) from exc

        pool = valkey_module.ConnectionPool.from_url(
            url,
            decode_responses=True,
            health_check_interval=30,
            max_connections=max_connections,
            socket_connect_timeout=2.0,
            socket_keepalive=True,
            socket_timeout=2.0,
        )
        return cast(
            ValkeyClientProtocol, valkey_module.Valkey(connection_pool=pool)
        )

    def _storage_key(self, key: str) -> str:
        return f"{self._key_prefix}:reasoning:{key}"

    def _cache_key_match(self) -> str:
        return f"{self._key_prefix}:reasoning:*"

    def _delete_keys(self, keys: list[str]) -> int:
        if not keys:
            return 0
        deleted = 0
        for index in range(0, len(keys), 500):
            batch = keys[index : index + 500]
            result = self._client.delete(*batch)
            deleted_count = _to_int(result)
            deleted += (
                deleted_count if deleted_count is not None else len(batch)
            )
        return deleted

    def _remove_index_entries(self, keys: list[str]) -> None:
        if not keys:
            return
        for index in range(0, len(keys), 500):
            batch = keys[index : index + 500]
            self._client.zrem(self._index_key, *batch)

    def _prune_index_by_age(self, now: float | None = None) -> int:
        if self.max_age_seconds is None or self.max_age_seconds <= 0:
            return 0
        cutoff = (time.time() if now is None else now) - self.max_age_seconds
        result = self._client.zremrangebyscore(self._index_key, "-inf", cutoff)
        return _to_int(result) or 0

    def _prune_index_by_rows(self) -> int:
        if self._max_rows is None or self._max_rows <= 0:
            return 0
        raw_count = self._client.zcard(self._index_key)
        count = _to_int(raw_count)
        if count is None:
            return 0
        overflow = count - self._max_rows
        if overflow <= 0:
            return 0
        stale_keys = [
            _to_key(key)
            for key in self._client.zrange(self._index_key, 0, overflow - 1)
        ]
        self._delete_keys(stale_keys)
        self._remove_index_entries(stale_keys)
        return len(stale_keys)

    def put(self, key: str, reasoning: str, message: dict[str, Any]) -> None:
        started = time.monotonic()
        try:
            if self._closed or not isinstance(reasoning, str):
                return
            storage_key = self._storage_key(key)
            created_at = time.time()
            value = json.dumps(
                {
                    "reasoning": reasoning,
                    "message_json": json.dumps(
                        message, ensure_ascii=False, sort_keys=True
                    ),
                    "created_at": created_at,
                },
                ensure_ascii=False,
                sort_keys=True,
            )
            ttl = (
                self.max_age_seconds
                if self.max_age_seconds is not None
                and self.max_age_seconds > 0
                else None
            )
            try:
                self._client.set(storage_key, value, ex=ttl)
                self._client.zadd(self._index_key, {storage_key: created_at})
                self._prune_index_by_age(created_at)
                self._prune_index_by_rows()
            except Exception as exc:
                METRICS.record_storage_error(
                    backend=self.backend_name,
                    operation="put",
                )
                LOG.warning(
                    "Valkey write failed for key=%s: %s",
                    key[:32],
                    _safe_exception_name(exc),
                )
        finally:
            METRICS.observe_storage_operation(
                backend=self.backend_name,
                operation="put",
                duration_seconds=time.monotonic() - started,
            )

    def get(self, key: str) -> str | None:
        started = time.monotonic()
        try:
            if self._closed:
                return None
            storage_key = self._storage_key(key)
            try:
                raw_value = self._client.get(storage_key)
            except Exception as exc:
                METRICS.record_storage_error(
                    backend=self.backend_name,
                    operation="get",
                )
                LOG.warning(
                    "Valkey read failed for key=%s: %s",
                    key[:32],
                    _safe_exception_name(exc),
                )
                return None
            if raw_value is None:
                with suppress(Exception):
                    self._client.zrem(self._index_key, storage_key)
                INTERNAL_LOG.debug(
                    "store.cache: key=%s..., hit=False", key[:32]
                )
                return None
            try:
                if isinstance(raw_value, bytes):
                    raw_value = raw_value.decode("utf-8")
                payload = json.loads(str(raw_value))
                reasoning = payload.get("reasoning")
                if isinstance(reasoning, str):
                    INTERNAL_LOG.debug(
                        "store.cache: key=%s..., hit=True", key[:32]
                    )
                    return reasoning
            except Exception as exc:
                METRICS.record_storage_error(
                    backend=self.backend_name,
                    operation="get",
                )
                LOG.warning(
                    "Valkey value decode failed for key=%s: %s",
                    key[:32],
                    _safe_exception_name(exc),
                )
            with suppress(Exception):
                self._client.delete(storage_key)
                self._client.zrem(self._index_key, storage_key)
            INTERNAL_LOG.debug(
                "store.cache: key=%s..., hit=False", key[:32]
            )
            return None
        finally:
            METRICS.observe_storage_operation(
                backend=self.backend_name,
                operation="get",
                duration_seconds=time.monotonic() - started,
            )

    def clear(self) -> int:
        started = time.monotonic()
        try:
            if self._closed:
                return 0
            try:
                keys = [
                    _to_key(key)
                    for key in self._client.scan_iter(
                        match=self._cache_key_match(), count=500
                    )
                ]
                deleted = self._delete_keys(keys)
                if self._index_key not in keys:
                    self._client.delete(self._index_key)
                return max(0, deleted - (1 if self._index_key in keys else 0))
            except Exception as exc:
                METRICS.record_storage_error(
                    backend=self.backend_name,
                    operation="clear",
                )
                LOG.warning(
                    "Valkey clear failed: %s", _safe_exception_name(exc)
                )
                return 0
        finally:
            METRICS.observe_storage_operation(
                backend=self.backend_name,
                operation="clear",
                duration_seconds=time.monotonic() - started,
            )

    def prune(self) -> int:
        started = time.monotonic()
        try:
            if self._closed:
                return 0
            try:
                return self._prune_index_by_age() + self._prune_index_by_rows()
            except Exception as exc:
                METRICS.record_storage_error(
                    backend=self.backend_name,
                    operation="prune",
                )
                LOG.warning(
                    "Valkey prune failed: %s", _safe_exception_name(exc)
                )
                return 0
        finally:
            METRICS.observe_storage_operation(
                backend=self.backend_name,
                operation="prune",
                duration_seconds=time.monotonic() - started,
            )

    def healthcheck(self) -> tuple[bool, str]:
        started = time.monotonic()
        try:
            if self._closed:
                return False, "closed"
            try:
                self._client.ping()
            except Exception as exc:
                METRICS.record_storage_error(
                    backend=self.backend_name,
                    operation="healthcheck",
                )
                LOG.warning(
                    "Valkey health check failed: %s",
                    _safe_exception_name(exc),
                )
                return False, "unavailable"
            return True, "ok"
        finally:
            METRICS.observe_storage_operation(
                backend=self.backend_name,
                operation="healthcheck",
                duration_seconds=time.monotonic() - started,
            )

    def stats(self) -> ReasoningStoreStats:
        started = time.monotonic()
        entries: int | None = None
        try:
            if not self._closed:
                try:
                    self._prune_index_by_age()
                    entries = _to_int(self._client.zcard(self._index_key))
                except Exception as exc:
                    METRICS.record_storage_error(
                        backend=self.backend_name,
                        operation="stats",
                    )
                    LOG.warning(
                        "Valkey stats failed: %s", _safe_exception_name(exc)
                    )
            return ReasoningStoreStats(
                backend=self.backend_name,
                entries=entries,
                max_age_seconds=self.max_age_seconds,
                max_rows=self._max_rows,
            )
        finally:
            METRICS.observe_storage_operation(
                backend=self.backend_name,
                operation="stats",
                duration_seconds=time.monotonic() - started,
            )

    def close(self) -> None:
        self._closed = True
        try:
            self._client.close()
        except Exception as exc:
            LOG.warning("Valkey close failed: %s", _safe_exception_name(exc))
