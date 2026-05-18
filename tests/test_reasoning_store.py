from __future__ import annotations

import json
import os
import sqlite3
import stat
import unittest
from fnmatch import fnmatch
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from unittest.mock import patch

from deepseek_bridge.config import _auto_cache_max_rows
from deepseek_bridge.metrics import METRICS
from deepseek_bridge.reasoning_store import (
    ReasoningStore,
    ReasoningStoreBase,
    ReasoningStoreProtocol,
    ReasoningStoreStats,
    conversation_scope,
)
from deepseek_bridge.valkey_store import ValkeyReasoningStore


class _MemoryReasoningStore(ReasoningStoreBase):
    backend_name = "memory"

    def __init__(self) -> None:
        self._items: dict[str, tuple[str, dict[str, object]]] = {}
        self._closed = False

    def put(self, key: str, reasoning: str, message: dict[str, Any]) -> None:
        if self._closed or not isinstance(reasoning, str):
            return
        self._items[key] = (reasoning, dict(message))

    def get(self, key: str) -> str | None:
        if self._closed:
            return None
        item = self._items.get(key)
        if item is None:
            return None
        return item[0]

    def clear(self) -> int:
        count = len(self._items)
        self._items.clear()
        return count

    def prune(self) -> int:
        return 0

    def healthcheck(self) -> tuple[bool, str]:
        return (False, "closed") if self._closed else (True, "ok")

    def stats(self) -> ReasoningStoreStats:
        return ReasoningStoreStats(
            backend=self.backend_name,
            entries=len(self._items),
        )

    def close(self) -> None:
        self._closed = True


class _FakeValkeyClient:
    def __init__(self) -> None:
        self.now = 0.0
        self.values: dict[str, tuple[str, float | None]] = {}
        self.zsets: dict[str, dict[str, float]] = {}
        self.set_calls: list[tuple[str, str, int | None]] = []
        self.fail_ops: set[str] = set()
        self.closed = False

    def advance(self, seconds: float) -> None:
        self.now += seconds

    def _maybe_fail(self, op: str) -> None:
        if op in self.fail_ops or "*" in self.fail_ops:
            raise RuntimeError("valkey://:secret@example.invalid/0")

    def _expire(self, key: str) -> None:
        item = self.values.get(key)
        if item is None:
            return
        _, expires_at = item
        if expires_at is not None and self.now >= expires_at:
            del self.values[key]

    def set(self, name: str, value: str, ex: int | None = None) -> bool:
        self._maybe_fail("set")
        expires_at = self.now + ex if ex is not None else None
        self.values[name] = (value, expires_at)
        self.set_calls.append((name, value, ex))
        return True

    def get(self, name: str) -> str | None:
        self._maybe_fail("get")
        self._expire(name)
        item = self.values.get(name)
        return None if item is None else item[0]

    def delete(self, *names: str) -> int:
        self._maybe_fail("delete")
        deleted = 0
        for name in names:
            if name in self.values:
                del self.values[name]
                deleted += 1
            if name in self.zsets:
                del self.zsets[name]
                deleted += 1
        return deleted

    def scan_iter(
        self, match: str | None = None, count: int | None = None
    ) -> list[str]:
        self._maybe_fail("scan_iter")
        keys = sorted(set(self.values) | set(self.zsets))
        if match is None:
            return keys
        return [key for key in keys if fnmatch(key, match)]

    def ping(self) -> bool:
        self._maybe_fail("ping")
        return True

    def zadd(self, name: str, mapping: dict[str, float]) -> int:
        self._maybe_fail("zadd")
        zset = self.zsets.setdefault(name, {})
        zset.update(mapping)
        return len(mapping)

    def zcard(self, name: str) -> int:
        self._maybe_fail("zcard")
        return len(self.zsets.get(name, {}))

    def zrange(self, name: str, start: int, end: int) -> list[str]:
        self._maybe_fail("zrange")
        items = sorted(
            self.zsets.get(name, {}).items(), key=lambda item: item[1]
        )
        if end == -1:
            end = len(items) - 1
        return [key for key, _ in items[start : end + 1]]

    def zrem(self, name: str, *values: str) -> int:
        self._maybe_fail("zrem")
        zset = self.zsets.setdefault(name, {})
        removed = 0
        for value in values:
            if value in zset:
                del zset[value]
                removed += 1
        return removed

    def zremrangebyscore(
        self, name: str, min: float | str, max: float | str
    ) -> int:
        self._maybe_fail("zremrangebyscore")
        min_value = float("-inf") if min == "-inf" else float(min)
        max_value = float("inf") if max == "+inf" else float(max)
        zset = self.zsets.setdefault(name, {})
        stale = [
            key
            for key, score in zset.items()
            if min_value <= score <= max_value
        ]
        for key in stale:
            del zset[key]
        return len(stale)

    def close(self) -> None:
        self._maybe_fail("close")
        self.closed = True


class ReasoningStoreContractTests(unittest.TestCase):
    def test_fake_store_satisfies_protocol(self) -> None:
        store = _MemoryReasoningStore()

        self.assertIsInstance(store, ReasoningStoreProtocol)

    def test_message_helpers_work_without_sqlite(self) -> None:
        store = _MemoryReasoningStore()
        scope = conversation_scope([{"role": "user", "content": "lookup"}])
        tool_call = {
            "id": "call_empty",
            "type": "function",
            "function": {"name": "lookup", "arguments": "{}"},
        }
        message = {
            "role": "assistant",
            "content": "",
            "reasoning_content": "",
            "tool_calls": [tool_call],
        }

        stored = store.store_assistant_message(message, scope)

        self.assertGreater(stored, 0)
        self.assertEqual(store.get(f"scope:{scope}:tool_call:call_empty"), "")
        self.assertEqual(
            store.lookup_for_message(
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [tool_call],
                },
                scope,
            ),
            "",
        )
        self.assertEqual(
            store.store_assistant_message(
                {"role": "user", "content": "not stored"}, scope
            ),
            0,
        )

    def test_portable_alias_backfill_uses_generic_put_get(self) -> None:
        store = _MemoryReasoningStore()
        prior_messages = [{"role": "user", "content": "find files"}]
        message = {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "call_find",
                    "type": "function",
                    "function": {"name": "find", "arguments": "{}"},
                }
            ],
        }

        stored = store.backfill_portable_aliases(
            message, "portable reasoning", "namespace", prior_messages
        )

        self.assertGreater(stored, 0)
        self.assertEqual(
            store.lookup_for_message(
                message,
                "different-scope",
                "namespace",
                prior_messages,
            ),
            "portable reasoning",
        )

    def test_health_lifecycle_and_stats_are_generic(self) -> None:
        store = _MemoryReasoningStore()
        store.put("k", "v", {"role": "assistant"})

        self.assertEqual(store.healthcheck(), (True, "ok"))
        self.assertEqual(store.health_check(), (True, "ok"))
        self.assertEqual(
            store.stats(),
            ReasoningStoreStats(backend="memory", entries=1),
        )
        self.assertEqual(store.clear(), 1)
        self.assertEqual(store.prune(), 0)

        store.close()

        self.assertEqual(store.healthcheck(), (False, "closed"))
        self.assertIsNone(store.get("k"))


class ReasoningStoreTests(unittest.TestCase):
    def test_file_store_creates_private_database_file(self) -> None:
        with TemporaryDirectory() as temp_dir:
            reasoning_content_path = (
                Path(temp_dir) / "nested" / "reasoning_content.sqlite3"
            )

            store = ReasoningStore(reasoning_content_path)
            store.close()

            self.assertTrue(reasoning_content_path.exists())
            if os.name != "nt":
                self.assertEqual(
                    stat.S_IMODE(reasoning_content_path.stat().st_mode), 0o600
                )

    def test_file_store_sizes_row_budget_from_database_parent(self) -> None:
        with TemporaryDirectory() as temp_dir:
            reasoning_content_path = (
                Path(temp_dir) / "nested" / "reasoning_content.sqlite3"
            )
            with patch(
                "deepseek_bridge.config._auto_cache_max_rows",
                return_value=12345,
            ) as mock_auto_rows:
                store = ReasoningStore(reasoning_content_path)
                store.close()

            mock_auto_rows.assert_called_once_with(
                disk_usage_path=reasoning_content_path.parent
            )

    def test_store_prunes_by_age_and_can_clear(self) -> None:
        store = ReasoningStore(":memory:", max_age_seconds=3600)
        try:
            # Manually set created_at to the past for age-based pruning.
            store.put("a", "reasoning a", {"role": "assistant"})
            store._conn.execute(
                "UPDATE reasoning_cache SET created_at = 0 WHERE key = 'a'"
            )
            store.put("b", "reasoning b", {"role": "assistant"})
            store.prune()
            self.assertIsNone(store.get("a"))
            self.assertEqual(store.get("b"), "reasoning b")
            self.assertEqual(store.clear(), 1)
            self.assertIsNone(store.get("b"))
        finally:
            store.close()

    def test_store_prunes_to_configured_max_rows_on_put(self) -> None:
        store = ReasoningStore(":memory:", max_rows=2)
        try:
            store.put("a", "reasoning a", {"role": "assistant"})
            store.put("b", "reasoning b", {"role": "assistant"})
            store.put("c", "reasoning c", {"role": "assistant"})

            self.assertIsNone(store.get("a"))
            self.assertEqual(store.get("b"), "reasoning b")
            self.assertEqual(store.get("c"), "reasoning c")
            self.assertEqual(store.get_row_count(), 2)
        finally:
            store.close()

    def test_empty_reasoning_content_is_stored_as_present_value(self) -> None:
        store = ReasoningStore(":memory:")
        try:
            scope = conversation_scope([{"role": "user", "content": "lookup"}])
            tool_call = {
                "id": "call_empty",
                "type": "function",
                "function": {"name": "lookup", "arguments": "{}"},
            }
            message = {
                "role": "assistant",
                "content": "",
                "reasoning_content": "",
                "tool_calls": [tool_call],
            }

            self.assertGreater(store.store_assistant_message(message, scope), 0)
            self.assertEqual(
                store.get(f"scope:{scope}:tool_call:call_empty"), ""
            )
            self.assertEqual(
                store.lookup_for_message(
                    {
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [tool_call],
                    },
                    scope,
                ),
                "",
            )
        finally:
            store.close()

    def test_health_check_reports_ok_for_open_store(self) -> None:
        store = ReasoningStore(":memory:")
        try:
            ok, status = store.health_check()
        finally:
            store.close()

        self.assertTrue(ok)
        self.assertEqual(status, "ok")

    def test_health_check_reports_closed_store(self) -> None:
        store = ReasoningStore(":memory:")
        store.close()

        ok, status = store.health_check()

        self.assertFalse(ok)
        self.assertEqual(status, "closed")

    def test_sqlite_stats_expose_generic_storage_fields(self) -> None:
        store = ReasoningStore(":memory:", max_age_seconds=60, max_rows=10)
        try:
            store.put("k", "v", {"role": "assistant"})

            stats = store.stats()
        finally:
            store.close()

        self.assertEqual(stats.backend, "sqlite")
        self.assertEqual(stats.entries, 1)
        self.assertEqual(stats.path, ":memory:")
        self.assertIsNone(stats.size_mb)
        self.assertEqual(stats.max_age_seconds, 60)
        self.assertEqual(stats.max_rows, 10)

    # ── Vacuum / Bloat tests ──────────────────────────────────────

    def test_auto_vacuum_on_new_file_db(self) -> None:
        with TemporaryDirectory() as d:
            p = os.path.join(d, "test.db")
            s = ReasoningStore(p)
            c = sqlite3.connect(p)
            av = c.execute("PRAGMA auto_vacuum").fetchone()[0]
            self.assertEqual(
                av, 2, f"auto_vacuum should be 2 (INCREMENTAL), got {av}"
            )
            s.close()
            c.close()

    def test_no_auto_vacuum_on_memory_db(self) -> None:
        s = ReasoningStore(":memory:")
        s.put("k", "v" * 100, {"role": "assistant"})
        s.close()

    def test_vacuum_works_on_file_db(self) -> None:
        with TemporaryDirectory() as d:
            p = os.path.join(d, "test.db")
            s = ReasoningStore(p)
            for i in range(10):
                s.put(f"large{i}", "x" * 200000, {"role": "assistant"})
            # vacuum must succeed on a file DB with data
            self.assertTrue(s.vacuum())
            # close must not raise
            s.close()
            self.assertTrue(os.path.exists(p))

    def test_check_bloat_detects_free_pages(self) -> None:
        with TemporaryDirectory() as d:
            p = os.path.join(d, "test.db")
            s = ReasoningStore(p)
            for i in range(60):
                s.put(f"row{i}", "x" * 900000, {"role": "assistant"})
            # Flush WAL so page_count / st_size reflect all committed data
            s._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            warn, _ = s.check_bloat()
            self.assertIsNotNone(
                warn,
                "Should detect large DB bloat with few rows",
            )
            s.close()

    def test_check_bloat_healthy_db(self) -> None:
        with TemporaryDirectory() as d:
            p = os.path.join(d, "test.db")
            s = ReasoningStore(p)
            s.put("k", "small", {"role": "assistant"})
            warn, _ = s.check_bloat()
            self.assertIsNone(
                warn, "Healthy small DB should not trigger bloat warning"
            )
            s.close()

    def test_check_bloat_memory_db(self) -> None:
        s = ReasoningStore(":memory:")
        s.put("k", "v", {"role": "assistant"})
        warn, _ = s.check_bloat()
        self.assertIsNone(warn, ":memory: DB should always return None")
        s.close()

    def test_vacuum_memory_db_returns_false(self) -> None:
        s = ReasoningStore(":memory:")
        result = s.vacuum()
        self.assertFalse(result, "vacuum on :memory: should return False")
        s.close()

    def test_wal_mode_and_pragmas_on_new_db(self) -> None:
        """Verify WAL mode, synchronous=NORMAL, and busy_timeout are set."""
        with TemporaryDirectory() as d:
            p = os.path.join(d, "test.db")
            s = ReasoningStore(p)
            c = sqlite3.connect(p)
            jm = c.execute("PRAGMA journal_mode").fetchone()[0]
            self.assertEqual(jm, "wal")
            sync = c.execute("PRAGMA synchronous").fetchone()[0]
            self.assertEqual(sync, 2)  # 2 = NORMAL
            bt = c.execute("PRAGMA busy_timeout").fetchone()[0]
            self.assertEqual(bt, 5000)
            s.close()
            c.close()

    def test_created_at_index_exists(self) -> None:
        """Verify the created_at index is created on new file DBs."""
        with TemporaryDirectory() as d:
            p = os.path.join(d, "test.db")
            s = ReasoningStore(p)
            c = sqlite3.connect(p)
            indexes = [
                r[1]
                for r in c.execute(
                    "SELECT * FROM sqlite_master WHERE type='index'"
                ).fetchall()
            ]
            self.assertIn("idx_reasoning_cache_created_at", indexes)
            s.close()
            c.close()

    def test_get_after_close_returns_none(self) -> None:
        with TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "test.db"
            s = ReasoningStore(db_path)
            s.put("k", "v", {"role": "assistant"})
            s.close()
            result = s.get("k")
            self.assertIsNone(result)

    def test_put_after_close_is_silent(self) -> None:
        with TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "test.db"
            s = ReasoningStore(db_path)
            s.close()
            s.put("k", "v", {"role": "assistant"})


class ValkeyReasoningStoreTests(unittest.TestCase):
    def _store(
        self,
        client: _FakeValkeyClient,
        *,
        max_age_seconds: int | None = 30,
        max_rows: int | None = None,
    ) -> ValkeyReasoningStore:
        return ValkeyReasoningStore(
            "valkey://:secret@example.invalid/0",
            key_prefix="tests:",
            max_age_seconds=max_age_seconds,
            max_rows=max_rows,
            client=client,
        )

    def test_put_get_and_stats_store_json_with_ttl(self) -> None:
        client = _FakeValkeyClient()
        store = self._store(client)

        store.put("k", "reasoning", {"role": "assistant", "content": "ok"})

        self.assertEqual(store.get("k"), "reasoning")
        self.assertEqual(client.set_calls[-1][0], "tests:reasoning:k")
        self.assertEqual(client.set_calls[-1][2], 30)
        payload = json.loads(client.set_calls[-1][1])
        self.assertEqual(payload["reasoning"], "reasoning")
        self.assertEqual(
            json.loads(payload["message_json"]),
            {"content": "ok", "role": "assistant"},
        )
        stats = store.stats()
        self.assertEqual(stats.backend, "valkey")
        self.assertEqual(stats.entries, 1)
        self.assertIsNone(stats.path)
        self.assertEqual(stats.max_age_seconds, 30)

    def test_overwrite_refreshes_value_and_ttl(self) -> None:
        client = _FakeValkeyClient()
        store = self._store(client)

        store.put("k", "first", {"role": "assistant"})
        client.advance(20)
        store.put("k", "second", {"role": "assistant"})
        client.advance(29)

        self.assertEqual(store.get("k"), "second")

        client.advance(2)

        self.assertIsNone(store.get("k"))
        self.assertEqual([call[2] for call in client.set_calls], [30, 30])

    def test_empty_reasoning_is_a_cache_hit(self) -> None:
        client = _FakeValkeyClient()
        store = self._store(client)

        store.put("empty", "", {"role": "assistant"})

        self.assertEqual(store.get("empty"), "")

    def test_clear_deletes_only_selected_prefix(self) -> None:
        client = _FakeValkeyClient()
        store = self._store(client)
        store.put("a", "reasoning a", {"role": "assistant"})
        store.put("b", "reasoning b", {"role": "assistant"})
        client.values["other:reasoning:c"] = ("{}", None)

        deleted = store.clear()

        self.assertEqual(deleted, 2)
        self.assertEqual(client.values, {"other:reasoning:c": ("{}", None)})
        self.assertNotIn("tests:reasoning:__index__", client.zsets)

    def test_max_rows_prunes_oldest_cache_entries(self) -> None:
        client = _FakeValkeyClient()
        store = self._store(client, max_age_seconds=None, max_rows=2)

        with patch(
            "deepseek_bridge.valkey_store.time.time",
            side_effect=[1.0, 2.0, 3.0],
        ):
            store.put("a", "reasoning a", {"role": "assistant"})
            store.put("b", "reasoning b", {"role": "assistant"})
            store.put("c", "reasoning c", {"role": "assistant"})

        self.assertIsNone(store.get("a"))
        self.assertEqual(store.get("b"), "reasoning b")
        self.assertEqual(store.get("c"), "reasoning c")
        self.assertEqual(store.stats().entries, 2)

    def test_multiple_instances_share_cache_by_prefix(self) -> None:
        client = _FakeValkeyClient()
        store_a = self._store(client)
        store_b = self._store(client)
        isolated_store = ValkeyReasoningStore(
            "valkey://example.invalid/0",
            key_prefix="other-prefix",
            max_age_seconds=30,
            client=client,
        )

        store_a.put("shared", "from a", {"role": "assistant"})

        self.assertEqual(store_b.get("shared"), "from a")
        self.assertIsNone(isolated_store.get("shared"))

        store_b.put("shared", "from b", {"role": "assistant"})

        self.assertEqual(store_a.get("shared"), "from b")
        self.assertEqual(store_a.clear(), 1)
        self.assertIsNone(store_b.get("shared"))

    def test_prune_removes_expired_index_entries(self) -> None:
        client = _FakeValkeyClient()
        store = self._store(client, max_age_seconds=10)

        with patch(
            "deepseek_bridge.valkey_store.time.time", return_value=100.0
        ):
            store.put("old", "reasoning", {"role": "assistant"})
        with patch(
            "deepseek_bridge.valkey_store.time.time", return_value=111.0
        ):
            deleted = store.prune()

        self.assertEqual(deleted, 1)
        self.assertEqual(store.stats().entries, 0)

    def test_corrupt_json_is_treated_as_miss_and_deleted(self) -> None:
        client = _FakeValkeyClient()
        store = self._store(client)
        client.values["tests:reasoning:k"] = ("not-json", None)
        client.zsets["tests:reasoning:__index__"] = {"tests:reasoning:k": 1.0}

        with self.assertLogs("deepseek_bridge", level="WARNING"):
            self.assertIsNone(store.get("k"))
        self.assertNotIn("tests:reasoning:k", client.values)
        self.assertNotIn(
            "tests:reasoning:k",
            client.zsets["tests:reasoning:__index__"],
        )

    def test_connection_failures_are_sanitized_and_do_not_raise(self) -> None:
        METRICS.reset()
        self.addCleanup(METRICS.reset)
        client = _FakeValkeyClient()
        store = self._store(client)
        client.fail_ops.update(
            {
                "set",
                "get",
                "ping",
                "scan_iter",
                "zcard",
                "zremrangebyscore",
            }
        )

        with self.assertLogs("deepseek_bridge", level="WARNING") as captured:
            store.put("k", "reasoning", {"role": "assistant"})
            self.assertIsNone(store.get("k"))
            self.assertEqual(store.healthcheck(), (False, "unavailable"))
            self.assertEqual(store.clear(), 0)
            self.assertEqual(store.prune(), 0)
            self.assertEqual(store.stats().entries, None)

        output = "\n".join(captured.output)
        self.assertIn("RuntimeError", output)
        self.assertNotIn("secret", output)
        self.assertNotIn("example.invalid", output)
        self.assertNotIn("valkey://", output)
        metrics = METRICS.render_prometheus()
        self.assertIn(
            'deepseek_bridge_storage_errors_total{backend="valkey",'
            'operation="put"} 1',
            metrics,
        )
        self.assertIn(
            'deepseek_bridge_storage_errors_total{backend="valkey",'
            'operation="stats"} 1',
            metrics,
        )
        self.assertIn(
            "deepseek_bridge_storage_operation_duration_seconds_count"
            '{backend="valkey",operation="get"} 1',
            metrics,
        )

    def test_close_prevents_later_cache_operations(self) -> None:
        client = _FakeValkeyClient()
        store = self._store(client)

        store.close()
        store.put("k", "reasoning", {"role": "assistant"})

        self.assertTrue(client.closed)
        self.assertIsNone(store.get("k"))
        self.assertEqual(store.clear(), 0)
        self.assertEqual(store.prune(), 0)
        self.assertEqual(store.healthcheck(), (False, "closed"))


class AutoCacheMaxRowsTests(unittest.TestCase):
    def test_returns_reasonable_default(self) -> None:
        result = _auto_cache_max_rows(disk_budget_mb=500)
        self.assertGreaterEqual(result, 10000)
        self.assertLess(result, 10_000_000)

    def test_returns_at_least_10000(self) -> None:
        result = _auto_cache_max_rows(disk_budget_mb=1)
        self.assertGreaterEqual(result, 10000)


if __name__ == "__main__":
    unittest.main()
