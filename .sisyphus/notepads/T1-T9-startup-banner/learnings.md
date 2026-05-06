## Learnings - T1+T9: Startup Banner + Log Path

### What changed
- `configure_logging()` now returns `str | None` (log file path or None)
- `server.py:main()` captures return as `log_file_path` and displays in organized sections

### Key decisions
- Box-drawing chars (╔╗╚╝║) are plain Unicode — safe for log files, no ANSI escape codes needed
- `__version__` imported from `deepseek_cursor_proxy` package (same as CLI uses)
- Display reasoning status always shown (not just in verbose mode) since it's fundamental config
- Reasoning DB path always shown in Storage section (was only in verbose before)
- Two separate `if log_dir:` blocks in `configure_logging()` — type checker can't unify them, pre-existing false positive

### Pre-existing diagnostics (not caused by this change)
- `logging.py:77`: `log_file` possibly unbound — two independent `if log_dir:` blocks
- `server.py:178`: Method override parameter name mismatch — unrelated BaseHTTPRequestHandler override

## Learnings - T7: Tests for new features

### What was added
12 new test cases across 3 test files:
- **test_reasoning_store.py** (7 tests): auto_vacuum pragma, vacuum on close shrinkage, bloat detection (healthy/bloated/memory), vacuum on :memory:
- **test_config.py** (2 tests): version format validation, default log_dir verification  
- **test_server.py** (3 tests): configure_logging return value with/without log_dir, _log_db_stats method existence

### Key challenges
- **incremental_vacuum interference**: `_prune_locked()` calls `PRAGMA incremental_vacuum(100)` which aggressively compacts small databases, making it impossible to trigger the `free_pct > 0.8` bloat condition with small data. Solution: used 60 rows × 900KB to grow DB past 50MB threshold.
- **vacuum_on_close_shrinks_file**: Used "large then tiny" row pattern — 10 large rows (200KB) to grow the file, then 30 tiny rows (1 byte) to leave free pages in the middle where incremental_vacuum can't reach. Full VACUUM on close compacts everything.
- **auto_vacuum=INCREMENTAL**: SQLite PRAGMA value 2 (INCREMENTAL) verified via direct `sqlite3.connect()` check — the `ReasoningStore` constructor sets it before table creation.

### Patterns used
- `TemporaryDirectory` for all file-based DB tests (never `:memory:`)
- `os.path.join(d, "test.db")` not `Path(d) / "test.db"` to match import style
- Direct `sqlite3.connect()` for PRAGMA verification (separate from `ReasoningStore`)
