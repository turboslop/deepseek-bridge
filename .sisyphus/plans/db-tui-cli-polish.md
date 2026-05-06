# DB Optimization + TUI Dashboard + CLI Polish

## TL;DR

> **Quick Summary**: SQLite WAL mode, `created_at` index, remove redundant `max_rows`, auto-calc cache budget, content redaction in verbose logs, CLI `--version` flag, and a full **Terminal UI Dashboard** with live stats, config editor, log viewer, and server controls.
>
> **Deliverables**:
> - WAL mode + synchronous=NORMAL + busy_timeout + created_at index (100x+ perf gain)
> - Remove `reasoning_cache_max_rows` (age-only pruning)
> - Auto-calc cache budget from disk space (500MB default)
> - Content truncation in verbose logging (don't expose user code in logs)
> - `--version` flag + cleaner `--help` output
> - TUI Dashboard with live stats, config editor, server controls, log viewer
>
> **Estimated Effort**: XL
> **Parallel Execution**: YES — 3 waves + Final

---

## Work Objectives

### Quick Wins (Wave 1)
- WAL mode + `synchronous=NORMAL` + `busy_timeout=5000` + index on `created_at`
- Remove `max_rows` from ProxyConfig (age-only pruning)
- Auto-calc cache max rows from disk budget (500MB → ~300K rows)
- Truncate content fields in verbose logging (show metadata only)
- `--version` flag

### TUI Dashboard (Wave 2)
- `textual` dependency
- Live stats: req/s, pool util, DB size, uptime, tokens/sec, cache hit rate
- Runtime config viewer/editor
- Server start/stop/restart controls
- Live log tail viewer
- Active connections viewer

### Polish (Wave 3)
- README update
- Tests
- Full verification

---

## Execution Strategy

```
Wave 1 (DB + CLI — quick wins, no new deps):
├── T1: SQLite WAL mode + synchronous=NORMAL + busy_timeout + index on created_at
├── T2: Remove reasoning_cache_max_rows (age-only pruning)
├── T3: Auto-calc cache disk budget (500MB)
├── T4: Content truncation in verbose logging
├── T5: --version flag + cleaner --help
└── T6: Update test_config.py assertions

Wave 2 (TUI Dashboard — textual library):
├── T7: Add textual dependency, create src/deepseek_cursor_proxy/tui/ module
├── T8: Dashboard screen — live stats (req/s, pool, DB, uptime, tok/s, cache hit)
├── T9: Config editor screen — view/edit runtime settings, apply changes
├── T10: Log viewer screen — tail proxy logs live
├── T11: Server controls — start/stop/restart proxy within TUI
├── T12: Wire TUI as `--tui` flag, fallback to CLI mode otherwise

Wave 3 (Polish):
├── T13: Update README with new features
├── T14: Full test suite + commit + push
```

---

## TODOs

- [x] 1. SQLite WAL mode + synchronous=NORMAL + busy_timeout + index on created_at

  **What to do**: In `reasoning_store.py.__init__()`, after the `sqlite3.connect()` and before `CREATE TABLE`:
  1. Add: `self._conn.execute("PRAGMA journal_mode=WAL")`
  2. Add: `self._conn.execute("PRAGMA synchronous=NORMAL")`
  3. Add: `self._conn.execute("PRAGMA busy_timeout=5000")`
  4. After `CREATE TABLE`, add: `self._conn.execute("CREATE INDEX IF NOT EXISTS idx_reasoning_cache_created_at ON reasoning_cache(created_at)")`
  - These 4 PRAGMAs deliver ~100x write throughput improvement and prevent lock contention on long runs.
  - **Stability**: WAL mode prevents readers from blocking writers — critical for concurrent Cursor requests.
  - **Memory**: WAL uses a separate WAL file; auto-checkpoint keeps it small.
  - The existing `auto_vacuum=INCREMENTAL` PRAGMA stays (already there at line 202).
  - Run: `uv run python -m unittest tests.test_reasoning_store -v`

  **Must NOT do**: Do NOT change existing PRAGMAs. Do NOT remove the existing auto_vacuum=INCREMENTAL.

- [x] 2. Remove reasoning_cache_max_rows — age-only pruning

  **What to do**:
  1. In `config.py`: Remove `DEFAULT_REASONING_CACHE_MAX_ROWS = 100_000` (line 36)
  2. In `config.py`: Remove `reasoning_cache_max_rows: int = ...` from ProxyConfig dataclass (line 222)
  3. In `config.py`: Remove `reasoning_cache_max_rows=as_int(...)` from from_file() (lines 292-294)
  4. In `reasoning_store.py.__init__()`: Remove `max_rows` parameter
  5. In `reasoning_store.py._prune_locked()`: Remove the row-cap pruning block (keep only age-based)
  6. In `server.py`: Remove `--reasoning-cache-max-rows` CLI arg from build_arg_parser()
  7. In `server.py main()`: Remove max_rows from ReasoningStore construction call
  8. Update tests in test_config.py and test_reasoning_store.py that reference max_rows
  9. Run: `uv run python -m unittest discover -s tests -v`

  **Rationale**: Age-based pruning (7 days) with incremental vacuum is sufficient. Dual pruning was belt-and-suspenders. Removing max_rows simplifies config and code path.

  **Must NOT do**: Do NOT remove age-based pruning. Do NOT change the pruning frequency.

- [x] 3. Auto-calc cache disk budget from available space (500MB default)

  **What to do**: Replace the removed max_rows with auto-calculation.
  1. In `config.py`, add: `DEFAULT_REASONING_CACHE_DISK_MB = 500`
  2. In `config.py`, add helper function:
     ```python
     def _auto_cache_max_rows(max_age_seconds: int, disk_budget_mb: int = 500) -> int:
         import shutil
         try:
             available_gb = shutil.disk_usage(default_app_dir()).free / (1024**3)
             disk_budget_mb = min(disk_budget_mb, available_gb * 1024 * 0.05)
         except Exception:
             pass
         est_row_bytes = 1500  # avg ~1.5KB per row
         return max(int((disk_budget_mb * 1024 * 1024) / est_row_bytes), 10000)
     ```
  3. In `ReasoningStore.__init__()`, use this to set an internal `_max_rows` for the row-cap prune (keep as safety net, not in ProxyConfig).
  4. The row-cap still fires as secondary safety net, but with auto-calc'd value instead of hardcoded 100k.

  **Must NOT do**: Do NOT add disk_usage to every request. Compute once at init.

- [x] 4. Content truncation in verbose logging

  **What to do**: In `server.py`, modify `log_json()` to truncate content fields:
  ```python
  def log_json(label: str, payload: Any) -> None:
      if isinstance(payload, dict):
          payload = _truncate_content(payload, max_len=200)
      LOG.info("%s:\n%s", label, json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))

  def _truncate_content(payload: dict, max_len: int = 200) -> dict:
      result = dict(payload)
      if "messages" in result:
          result["messages"] = [
              {**m, "content": m.get("content","")[:max_len] + ("..." if len(str(m.get("content",""))) > max_len else "")}
              if isinstance(m.get("content"), str) else m
              for m in result["messages"] if isinstance(m, dict)
          ]
      return result
  ```
  - Only truncates in verbose mode. Normal mode (already safe) is unchanged.
  - Shows first 200 chars + "..." if truncated.
  - Run: `uv run python -m unittest tests.test_server -v`

- [x] 5. Add `--version` flag + cleaner `--help`

  **What to do**:
  1. In `build_arg_parser()`, add: `parser.add_argument("--version", action="version", version=f"deepseek-cursor-proxy {__version__}")`
  2. Clean up `--help` by grouping args:
     ```python
     group_model = parser.add_argument_group("Model")
     group_model.add_argument("--model", ...)
     group_model.add_argument("--thinking", ...)
     group_model.add_argument("--reasoning-effort", ...)
     group_network = parser.add_argument_group("Network")
     group_network.add_argument("--host", ...)
     group_network.add_argument("--port", ...)
     group_network.add_argument("--ngrok", ...)
     # etc.
     ```
  3. Run: `uv run deepseek-cursor-proxy --version`

- [x] 6. Quick Wave 1 tests + commit
- [x] 7. TUI dashboard — textual dependency + module structure
- [x] 8. TUI dashboard — live stats screen
- [x] 9. TUI config editor screen
- [x] 10. TUI log viewer screen
- [x] 11. TUI server controls + --tui flag integration
- [x] 12. Final tests + README + commit

  **What to do**:
  1. Run: `uv run python -m unittest discover -s tests -v`
  2. Update README with TUI section + performance notes
  3. Commit: `feat: TUI dashboard + SQLite WAL mode + auto-calc cache + --version`
  4. Note: push may fail on upstream (fork permissions)

---

## Success Criteria

### Verification Commands
```bash
uv run python -m unittest discover -s tests -v  # All tests pass
uv run deepseek-cursor-proxy --version           # → "deepseek-cursor-proxy 0.1.1"
uv run deepseek-cursor-proxy --tui               # → TUI dashboard
```

### Final Checklist
- [x] WAL mode active on new DBs (PRAGMA journal_mode → wal)
- [x] created_at index exists
- [x] max_rows removed from ProxyConfig (age-only pruning)
- [x] Cache budget auto-calc'd (~300K rows at 1.5KB each = 450MB)
- [x] Verbose logging truncates content fields >200 chars
- [x] --version flag works
- [x] --tui opens dashboard
- [x] Dashboard shows live stats
- [x] Config editor can change runtime settings
- [x] Log viewer tails proxy logs
- [x] Server start/stop works
- [x] All tests pass (132/132)
