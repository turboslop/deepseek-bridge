# Learnings: Add Signal Handlers for Graceful Shutdown

## Changes Made
- **File**: `src/deepseek_cursor_proxy/server.py`
- Added `import signal` and `import threading` to imports
- Added `_shutdown_requested = threading.Event()` module-level flag
- Added `_handle_shutdown_signal(signum, frame)` callback for SIGTERM/SIGINT
- Registered `signal.signal(signal.SIGTERM, _handle_shutdown_signal)` in main()
- Registered `signal.signal(signal.SIGINT, _handle_shutdown_signal)` with try/except ValueError for non-main thread safety
- Replaced `server.serve_forever()` with polling loop: `server.timeout = 0.5` + `while not _shutdown_requested.is_set(): server.handle_request()`
- Updated finally block: drain executor via `shutdown(wait=True, cancel_futures=False)`, prune store, close store, close server, final log

## Key Decisions
- `cancel_futures=False` to avoid killing in-flight streaming requests
- SIGINT handler wrapped in try/except ValueError to handle non-main thread case
- `store.prune()` called before `store.close()` to clean up stale cache entries
- Final `LOG.info("graceful shutdown: complete")` as last action

## Verification
- 103/103 tests pass (1 skipped)
- Only pre-existing LSP diagnostic (log_message parameter name mismatch)
