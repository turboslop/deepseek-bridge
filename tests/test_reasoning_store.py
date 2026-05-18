from __future__ import annotations

import os
import sqlite3
import stat
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from deepseek_bridge.config import _auto_cache_max_rows
from deepseek_bridge.reasoning_store import ReasoningStore, conversation_scope


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
