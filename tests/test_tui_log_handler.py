from __future__ import annotations

import logging
import unittest

from deepseek_bridge.tui.log_handler import TuiLogHandler


class TestTuiLogHandler(unittest.TestCase):
    def setUp(self) -> None:
        self.captured: list[str] = []
        self.handler = TuiLogHandler(emit_fn=self.captured.append)

    def make_record(self, level: int, msg: str) -> logging.LogRecord:
        return logging.LogRecord(
            name="test",
            level=level,
            pathname="test.py",
            lineno=1,
            msg=msg,
            args=(),
            exc_info=None,
        )

    def test_emit_info_formats_message(self) -> None:
        self.handler.emit(self.make_record(logging.INFO, "hello world"))
        self.assertEqual(self.captured, ["hello world"])

    def test_emit_warning_includes_level(self) -> None:
        self.handler.emit(self.make_record(logging.WARNING, "danger"))
        self.assertEqual(self.captured, ["WARNING: danger"])

    def test_filter_below_info_is_dropped(self) -> None:
        self.handler.emit(self.make_record(logging.DEBUG, "debug msg"))
        self.assertEqual(self.captured, [])

    def test_handler_does_not_crash_on_format_error(self) -> None:
        # Bad format string with mismatched args
        record = logging.LogRecord(
            name="test", level=logging.INFO, pathname="test.py",
            lineno=1, msg="bad %s %d", args=("only_one",), exc_info=None,
        )
        # Should not raise
        self.handler.emit(record)
        # Callback should still have been called (with format error message)
        self.assertTrue(len(self.captured) > 0)

    def test_close_stops_emission(self) -> None:
        self.handler.close()
        self.handler.emit(self.make_record(logging.INFO, "after close"))
        self.assertEqual(self.captured, [])

    def test_close_is_idempotent(self) -> None:
        self.handler.close()
        self.handler.close()  # Should not raise
