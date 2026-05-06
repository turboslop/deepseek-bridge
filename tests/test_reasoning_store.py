from __future__ import annotations

import os
from pathlib import Path
import sqlite3
import stat
from tempfile import TemporaryDirectory
import unittest

from deepseek_cursor_proxy.reasoning_store import ReasoningStore, conversation_scope


class ReasoningStoreTests(unittest.TestCase):
    def test_file_store_creates_private_database_file(self) -> None:
        with TemporaryDirectory() as temp_dir:
            reasoning_content_path = (
                Path(temp_dir) / "nested" / "reasoning_content.sqlite3"
            )

            store = ReasoningStore(reasoning_content_path)
            store.close()

            self.assertTrue(reasoning_content_path.exists())
            self.assertEqual(stat.S_IMODE(reasoning_content_path.stat().st_mode), 0o600)

    def test_store_prunes_to_max_rows_and_can_clear(self) -> None:
        store = ReasoningStore(":memory:", max_rows=2)
        try:
            store.put("a", "reasoning a", {"role": "assistant"})
            store.put("b", "reasoning b", {"role": "assistant"})
            store.put("c", "reasoning c", {"role": "assistant"})

            self.assertIsNone(store.get("a"))
            self.assertEqual(store.get("b"), "reasoning b")
            self.assertEqual(store.get("c"), "reasoning c")
            self.assertEqual(store.clear(), 2)
            self.assertIsNone(store.get("b"))
            self.assertIsNone(store.get("c"))
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
            self.assertEqual(store.get(f"scope:{scope}:tool_call:call_empty"), "")
            self.assertEqual(
                store.lookup_for_message(
                    {"role": "assistant", "content": "", "tool_calls": [tool_call]},
                    scope,
                ),
                "",
            )
        finally:
            store.close()

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

    def test_vacuum_on_close_shrinks_file(self) -> None:
        with TemporaryDirectory() as d:
            p = os.path.join(d, "test.db")
            s = ReasoningStore(p, max_rows=5)
            # Fill with many large rows to grow the DB file,
            # then replace them with tiny rows so freed pages
            # accumulate in the middle where incremental_vacuum
            # cannot reclaim them. The full VACUUM on close
            # should compact everything significantly.
            for i in range(10):
                s.put(f"setup{i}", "x" * 200000, {"role": "assistant"})
            for i in range(30):
                s.put(f"small{i}", "x", {"role": "assistant"})
            before = os.path.getsize(p)
            s.close()
            after = os.path.getsize(p)
            self.assertLess(
                after, before,
                f"DB should shrink after close: {before} -> {after}"
            )

    def test_check_bloat_detects_free_pages(self) -> None:
        with TemporaryDirectory() as d:
            p = os.path.join(d, "test.db")
            s = ReasoningStore(p, max_rows=1000)
            for i in range(60):
                s.put(f"row{i}", "x" * 900000, {"role": "assistant"})
            warn = s.check_bloat()
            self.assertIsNotNone(
                warn, "Should detect bloat: large DB with few rows (>50MB, <2000 rows)"
            )
            s.close()

    def test_check_bloat_healthy_db(self) -> None:
        with TemporaryDirectory() as d:
            p = os.path.join(d, "test.db")
            s = ReasoningStore(p, max_rows=100)
            s.put("k", "small", {"role": "assistant"})
            warn = s.check_bloat()
            self.assertIsNone(warn, "Healthy small DB should not trigger bloat warning")
            s.close()

    def test_check_bloat_memory_db(self) -> None:
        s = ReasoningStore(":memory:")
        s.put("k", "v", {"role": "assistant"})
        warn = s.check_bloat()
        self.assertIsNone(warn, ":memory: DB should always return None")
        s.close()

    def test_vacuum_memory_db_returns_false(self) -> None:
        s = ReasoningStore(":memory:")
        result = s.vacuum()
        self.assertFalse(result, "vacuum on :memory: should return False")
        s.close()


if __name__ == "__main__":
    unittest.main()
