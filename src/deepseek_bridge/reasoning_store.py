from __future__ import annotations

import contextlib
import hashlib
import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

from ._normalization import normalize_tool_call
from .logging import INTERNAL_LOG, LOG


def tool_call_signature(tool_call: dict[str, Any]) -> str:
    normalized = normalize_tool_call(tool_call)
    normalized.pop("id", None)
    canonical = json.dumps(
        normalized, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def tool_call_ids(message: dict[str, Any]) -> list[str]:
    return [
        str(tool_call["id"])
        for tool_call in message.get("tool_calls") or []
        if isinstance(tool_call, dict) and tool_call.get("id")
    ]


def tool_call_names(message: dict[str, Any]) -> list[str]:
    names: list[str] = []
    for tool_call in message.get("tool_calls") or []:
        if not isinstance(tool_call, dict):
            continue
        function = tool_call.get("function")
        if isinstance(function, dict) and function.get("name"):
            names.append(str(function["name"]))
    return names


def message_signature(message: dict[str, Any]) -> str:
    tool_calls = [
        normalize_tool_call(tool_call)
        for tool_call in (message.get("tool_calls") or [])
        if isinstance(tool_call, dict)
    ]
    payload = {
        "content": message.get("content") or "",
        "tool_calls": tool_calls,
    }
    canonical = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _sha256_json(payload: Any) -> str:
    canonical = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def canonical_scope_message(message: dict[str, Any]) -> dict[str, Any]:
    canonical: dict[str, Any] = {"role": message.get("role")}
    for key in ("content", "name", "tool_call_id", "prefix"):
        if key in message:
            canonical[key] = message[key]
    if message.get("tool_calls"):
        canonical["tool_calls"] = [
            normalize_tool_call(tool_call)
            for tool_call in message.get("tool_calls") or []
            if isinstance(tool_call, dict)
        ]
    return canonical


def conversation_scope(
    messages: list[dict[str, Any]], namespace: str = ""
) -> str:
    scope_messages = [canonical_scope_message(message) for message in messages]
    payload: Any = scope_messages
    if namespace:
        payload = {"namespace": namespace, "messages": scope_messages}
    return _sha256_json(payload)


def turn_context_signature(prior_messages: list[dict[str, Any]]) -> str:
    last_user_index = next(
        (
            index
            for index in range(len(prior_messages) - 1, -1, -1)
            if prior_messages[index].get("role") == "user"
        ),
        -1,
    )
    start_index = 0
    if last_user_index != -1:
        start_index = last_user_index
        while (
            start_index > 0
            and prior_messages[start_index - 1].get("role") == "user"
        ):
            start_index -= 1

    context_messages = [
        canonical_scope_message(message)
        for message in prior_messages[start_index:]
        if message.get("role") != "system"
    ]
    return _sha256_json(context_messages)


def scoped_reasoning_keys(message: dict[str, Any], scope: str) -> list[str]:
    keys = [f"scope:{scope}:signature:{message_signature(message)}"]
    keys.extend(
        f"scope:{scope}:tool_call:{tool_call_id}"
        for tool_call_id in tool_call_ids(message)
    )
    keys.extend(
        f"scope:{scope}:tool_call_signature:{tool_call_signature(tool_call)}"
        for tool_call in (message.get("tool_calls") or [])
        if isinstance(tool_call, dict)
    )
    # Recovery-of-last-resort key. Catches the case where a streaming response
    # was interrupted (user pressed Stop) before the tool_call.id chunk arrived,
    # so neither tool_call_id nor tool_call_signature (which canonicalizes
    # arguments) survives the round-trip through Cursor's transcript.
    keys.extend(
        f"scope:{scope}:tool_name:{tool_name}"
        for tool_name in tool_call_names(message)
    )
    return keys


def portable_reasoning_keys(
    message: dict[str, Any],
    cache_namespace: str,
    prior_messages: list[dict[str, Any]],
) -> list[str]:
    if not cache_namespace:
        return []

    turn_signature = turn_context_signature(prior_messages)
    keys = [
        f"namespace:{cache_namespace}:turn:{turn_signature}:"
        f"signature:{message_signature(message)}"
    ]
    keys.extend(
        f"namespace:{cache_namespace}:turn:{turn_signature}:"
        f"tool_call:{tool_call_id}"
        for tool_call_id in tool_call_ids(message)
    )
    keys.extend(
        f"namespace:{cache_namespace}:turn:{turn_signature}:"
        f"tool_call_signature:{tool_call_signature(tool_call)}"
        for tool_call in (message.get("tool_calls") or [])
        if isinstance(tool_call, dict)
    )
    keys.extend(
        f"namespace:{cache_namespace}:turn:{turn_signature}:"
        f"tool_name:{tool_name}"
        for tool_name in tool_call_names(message)
    )
    return keys


class ReasoningStore:
    def __init__(
        self,
        reasoning_content_path: str | Path,
        max_age_seconds: int | None = None,
    ) -> None:
        self.max_age_seconds = max_age_seconds
        if str(reasoning_content_path) == ":memory:":
            self.reasoning_content_path: str | Path = ":memory:"
        else:
            self.reasoning_content_path = Path(
                reasoning_content_path
            ).expanduser()
            self.reasoning_content_path.parent.mkdir(
                mode=0o700, parents=True, exist_ok=True
            )
        self._lock = threading.RLock()
        self._closed = False
        self._max_rows: int | None = None
        self._maintenance_thread: threading.Thread | None = None
        self._maintenance_interval: float = 1800.0  # 30 min default
        self._conn = sqlite3.connect(
            self.reasoning_content_path, check_same_thread=False
        )
        if isinstance(self.reasoning_content_path, Path):
            self.reasoning_content_path.chmod(0o600)
            self._conn.execute("PRAGMA auto_vacuum = INCREMENTAL")
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=NORMAL")
            self._conn.execute("PRAGMA busy_timeout=5000")
            self._conn.execute("PRAGMA cache_size = -65536")
            self._conn.execute("PRAGMA mmap_size = 268435456")
            self._conn.execute("PRAGMA temp_store = MEMORY")
            self._conn.execute("PRAGMA wal_autocheckpoint=4000")
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS reasoning_cache (
                key TEXT PRIMARY KEY,
                reasoning TEXT NOT NULL,
                message_json TEXT NOT NULL,
                created_at REAL NOT NULL
            )
            """)
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_reasoning_cache_created_at "
            "ON reasoning_cache(created_at)"
        )
        self._conn.commit()
        if isinstance(self.reasoning_content_path, Path):
            from .config import _auto_cache_max_rows

            self._max_rows = _auto_cache_max_rows()
        else:
            self._max_rows = None
        self.prune()

    def vacuum(self) -> bool:
        if not isinstance(self.reasoning_content_path, Path):
            return False
        try:
            size_mb = self.reasoning_content_path.stat().st_size / (1024 * 1024)
            if size_mb > 1024:
                LOG.warning(
                    "reasoning DB is %.0f MB; skipping automatic VACUUM. "
                    "Run manually.",
                    size_mb,
                )
                return False
            self._conn.execute("VACUUM")
            return True
        except Exception as exc:
            LOG.warning("VACUUM failed: %s", exc)
            return False

    def check_bloat(self) -> tuple[str | None, float | None]:
        """Return (warning, free_pct) or (None, None) if healthy."""
        if not isinstance(self.reasoning_content_path, Path):
            return None, None
        try:
            page_count = self._conn.execute("PRAGMA page_count").fetchone()[0]
            freelist_count = self._conn.execute(
                "PRAGMA freelist_count"
            ).fetchone()[0]
            if page_count == 0:
                return None, None
            free_pct = freelist_count / page_count
            size_mb = self.reasoning_content_path.stat().st_size / (1024 * 1024)
            if free_pct > 0.8:
                return (
                    f"reasoning DB is {size_mb:.0f} MB with "
                    f"{free_pct:.0%} free pages "
                    f"({freelist_count}/{page_count}). Run with "
                    f"--clear-reasoning-cache or restart to reclaim space."
                ), free_pct
            if size_mb > 50:
                row = self._conn.execute(
                    "SELECT COUNT(*) FROM reasoning_cache"
                ).fetchone()
                row_count = int(row[0]) if row else 0
                if row_count < 2000:
                    return (
                        f"reasoning DB is {size_mb:.0f} MB but only has "
                        f"{row_count} rows. "
                        f"Consider running with --clear-reasoning-cache."
                    ), free_pct
            return None, free_pct
        except Exception as exc:
            LOG.warning("check_bloat failed: %s", exc)
            return None, None

    def get_row_count(self) -> int:
        """Return the number of cached reasoning rows."""
        try:
            row = self._conn.execute(
                "SELECT COUNT(*) FROM reasoning_cache"
            ).fetchone()
            return int(row[0]) if row else 0
        except Exception:
            return 0

    def get_db_size_mb(self) -> float:
        """Return the reasoning cache database file size in MB."""
        if not isinstance(self.reasoning_content_path, Path):
            return 0.0
        try:
            return self.reasoning_content_path.stat().st_size / (1024 * 1024)
        except Exception:
            return 0.0

    def close(self) -> None:
        with self._lock:
            self._closed = True
        self._stop_maintenance()
        with self._lock:
            self.vacuum()
            self._conn.close()

    def start_periodic_maintenance(
        self, interval_seconds: float = 1800.0
    ) -> None:
        self._maintenance_interval = interval_seconds
        if self._maintenance_thread is not None:
            return
        self._maintenance_thread = threading.Thread(
            target=self._maintenance_loop,
            daemon=True,
            name="db-maintenance",
        )
        self._maintenance_thread.start()

    def _stop_maintenance(self) -> None:
        if self._maintenance_thread is None:
            return
        self._maintenance_thread = None

    def _maintenance_loop(self) -> None:
        while not self._closed:
            time.sleep(self._maintenance_interval)
            if self._closed:
                break
            try:
                with self._lock:
                    if self._closed:
                        break
                    bloat, free_pct = self.check_bloat()
                    if bloat is not None:
                        LOG.info("periodic DB check: %s", bloat)
                        if free_pct is not None and free_pct > 0.8:
                            LOG.warning(
                                "DB severely bloated (%.0f%% free), "
                                "clearing cache",
                                free_pct * 100,
                            )
                            deleted = self._clear_locked()
                            LOG.info("cleared %s reasoning cache rows", deleted)
                    self._conn.execute("PRAGMA optimize")
            except Exception as exc:
                LOG.warning("periodic DB maintenance failed: %s", exc)

    def _clear_locked(self) -> int:
        row = self._conn.execute(
            "SELECT COUNT(*) FROM reasoning_cache"
        ).fetchone()
        count = int(row[0] if row else 0)
        self._conn.execute("DELETE FROM reasoning_cache")
        self._conn.commit()
        return count

    def put(self, key: str, reasoning: str, message: dict[str, Any]) -> None:
        if not isinstance(reasoning, str):
            return
        message_json = json.dumps(message, ensure_ascii=False, sort_keys=True)
        with self._lock:
            if self._closed:
                return
            try:
                self._conn.execute(
                    "INSERT INTO reasoning_cache("
                    "key, reasoning, message_json, created_at) "
                    "VALUES (?, ?, ?, ?) ON CONFLICT(key) DO UPDATE SET "
                    "reasoning = excluded.reasoning, "
                    "message_json = excluded.message_json, "
                    "created_at = excluded.created_at",
                    (key, reasoning, message_json, time.time()),
                )
                self._conn.commit()
            except Exception as exc:
                LOG.warning("SQLite write failed for key=%s: %s", key[:32], exc)

    def get(self, key: str) -> str | None:
        with self._lock:
            if self._closed:
                return None
            try:
                row = self._conn.execute(
                    "SELECT reasoning FROM reasoning_cache WHERE key = ?",
                    (key,),
                ).fetchone()
            except Exception as exc:
                LOG.warning("SQLite read failed for key=%s: %s", key[:32], exc)
                return None
        if row is None:
            INTERNAL_LOG.debug("store.cache: key=%s..., hit=False", key[:32])
            return None
        INTERNAL_LOG.debug("store.cache: key=%s..., hit=True", key[:32])
        return str(row[0])

    def store_assistant_message(
        self,
        message: dict[str, Any],
        scope: str,
        cache_namespace: str = "",
        prior_messages: list[dict[str, Any]] | None = None,
    ) -> int:
        if message.get("role") != "assistant":
            return 0
        reasoning = message.get("reasoning_content")
        if not isinstance(reasoning, str):
            return 0

        keys = scoped_reasoning_keys(message, scope)
        if prior_messages is not None:
            keys.extend(
                portable_reasoning_keys(
                    message, cache_namespace, prior_messages
                )
            )
        keys = list(dict.fromkeys(keys))
        for key in keys:
            self.put(key, reasoning, message)
        return len(keys)

    def lookup_for_message(
        self,
        message: dict[str, Any],
        scope: str,
        cache_namespace: str = "",
        prior_messages: list[dict[str, Any]] | None = None,
    ) -> str | None:
        keys = scoped_reasoning_keys(message, scope)
        if prior_messages is not None:
            keys.extend(
                portable_reasoning_keys(
                    message, cache_namespace, prior_messages
                )
            )
        for key in keys:
            reasoning = self.get(key)
            if reasoning is not None:
                return reasoning
        return None

    def backfill_portable_aliases(
        self,
        message: dict[str, Any],
        reasoning: str,
        cache_namespace: str,
        prior_messages: list[dict[str, Any]],
    ) -> int:
        if not isinstance(reasoning, str):
            return 0
        keys = portable_reasoning_keys(message, cache_namespace, prior_messages)
        if not keys:
            return 0
        message_with_reasoning = dict(message)
        message_with_reasoning["reasoning_content"] = reasoning
        for key in dict.fromkeys(keys):
            self.put(key, reasoning, message_with_reasoning)
        return len(keys)

    def clear(self) -> int:
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) FROM reasoning_cache"
            ).fetchone()
            count = int(row[0] if row else 0)
            self._conn.execute("DELETE FROM reasoning_cache")
            self._conn.commit()
        return count

    def prune(self) -> int:
        with self._lock:
            deleted = self._prune_locked()
            self._conn.commit()
        return deleted

    def _prune_locked(self) -> int:
        deleted = 0
        if self.max_age_seconds is not None and self.max_age_seconds > 0:
            cutoff = time.time() - self.max_age_seconds
            cursor = self._conn.execute(
                "DELETE FROM reasoning_cache WHERE created_at < ?",
                (cutoff,),
            )
            deleted += cursor.rowcount if cursor.rowcount != -1 else 0

        if self._max_rows is not None and self._max_rows > 0:
            cursor = self._conn.execute(
                """
                DELETE FROM reasoning_cache
                WHERE key NOT IN (
                    SELECT key
                    FROM reasoning_cache
                    ORDER BY created_at DESC
                    LIMIT ?
                )
                """,
                (self._max_rows,),
            )
            deleted += cursor.rowcount if cursor.rowcount != -1 else 0

        if deleted > 0 and isinstance(self.reasoning_content_path, Path):
            with contextlib.suppress(Exception):
                self._conn.execute("PRAGMA incremental_vacuum(100)")
        return deleted
