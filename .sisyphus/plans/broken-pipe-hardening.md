# Broken Pipe & Connection Hardening Plan

## TL;DR

> **Quick Summary**: Fix recurring BrokenPipeError/connection errors in the deepseek-cursor-proxy by closing 6 unguarded socket touch points, adding connection pooling with urllib3, implementing SSE read timeouts, bounded thread management, ngrok tunnel health monitoring, and graceful shutdown.
>
> **Deliverables**:
> - Guard `_read_json_body()` against client-disconnect mid-upload
> - Guard `read_response_body()` against upstream socket failures
> - Guard `_proxy_regular_response()` and `_send_upstream_error()` against cascading errors
> - Better default error logging (visible without `--verbose`)
> - `urllib3` dependency + `PoolManager` replacing raw `urlopen`
> - Configurable SSE read timeout (`--stream-read-timeout`, default 180s)
> - Bounded thread pool for `ThreadingHTTPServer`
> - Ngrok tunnel health check + auto-reconnect
> - SIGTERM handler for graceful shutdown
> - Resilience test suite (`tests/test_resilience.py`)
>
> **Estimated Effort**: Large
> **Parallel Execution**: YES — 3 waves + 1 final verification
> **Critical Path**: Task 1 → Task 6 → Task 8 → Task 10 → Final

---

## Context

### Original Request
User experiences recurring "broken pipe" errors when running the proxy for extended periods (1-4 hours). The proxy either crashes or becomes unresponsive. No verbose logs were captured during failures.

### Interview Summary
**Key Discussions**:
- 5 unguarded socket touch points identified (read-side failures NOT currently caught)
- `BrokenPipeError` is only one exception type — actual failures include `ConnectionResetError`, `ConnectionAbortedError`, `http.client.IncompleteRead`, `socket.timeout`
- 1-4 hour failure pattern suggests progressive resource exhaustion (thread pileup, socket fd leak, no upstream keepalive)
- User wants all improvements in one plan: error guards, connection pooling, ngrok health, thread limits

**Research Findings**:
- Mitmproxy uses `urllib3` for connection pooling — battle-tested pattern
- Production proxies use three-layer defense: OS-level SIGPIPE ignore, pre-write `is_closing()` checks, catch `BrokenPipeError`/`ConnectionResetError` on every write
- `urllib3` provides built-in read timeouts per-request, which solves the SSE stall problem natively

### Metis Review
**Identified Gaps** (addressed):
- Exception class mismatch: code labels all gaps as "BrokenPipeError" but actual exceptions differ by scenario — addressed with precise per-gap exception handling
- `_send_upstream_error()` re-raises on socket failure — addressed with wrapped `read_response_body()` call
- No thread leak detection in scope — addressed with `ThreadPoolExecutor` replacement
- No soak test criteria — addressed with resilience test suite
- Signal handling missing — addressed with SIGTERM handler

---

## Work Objectives

### Core Objective
Eliminate connection failures in the proxy by guarding every socket touch point and adding resource management for long-running operation.

### Concrete Deliverables
- 6 unguarded socket read/write paths now guarded
- `urllib3` connection pool replacing `urlopen` per-request pattern
- SSE read timeout configurable via CLI/config
- Bounded thread pool (prevents thread exhaustion)
- Ngrok tunnel health check with auto-restart
- SIGTERM graceful shutdown
- Resilience test suite with fault injection

### Definition of Done
- [ ] `python -m unittest discover -s tests` — ALL tests pass (including new resilience tests)
- [ ] Fault-injection tests verify each error path: inject `ConnectionResetError` at each of the 6 unguarded points, verify graceful handling
- [ ] 2-hour soak test: 50 concurrent requests with random mid-request cancels → zero crashes

### Must Have
- Zero handler-thread crashes from socket errors
- Partial reasoning storage on mid-stream disconnect preserved
- All error logging visible at `INFO` level (no `--verbose` required for crash visibility)
- SSE read timeout prevents indefinite thread hangs
- `self.close_connection = True` on every client write failure

### Must NOT Have (Guardrails)
- No API response format changes (byte-identical to current for successful requests)
- No asyncio/aiohttp migration
- No changes to `ReasoningStore` public API
- No changes to `StreamAccumulator` or `CursorReasoningDisplayAdapter` internal logic
- No removal of existing error handling (only add, never remove)

---

## Verification Strategy

> **ZERO HUMAN INTERVENTION** — ALL verification is agent-executed. No exceptions.
> Acceptance criteria requiring "user manually tests/confirms" are FORBIDDEN.

### Test Decision
- **Infrastructure exists**: YES — `python -m unittest discover -s tests`
- **Automated tests**: YES (Tests after implementation)
- **Framework**: Python `unittest` (existing)
- **Tests after**: Each TODO includes a test task after the implementation

### QA Policy
Every task MUST include agent-executed QA scenarios. Evidence saved to `.sisyphus/evidence/task-{N}-{scenario-slug}.{ext}`.

- **API/Server**: Use interactive_bash — start proxy, send curl requests, verify responses
- **Fault injection**: Use Bash with Python unittest — run specific test module with mock assertions
- **Resilience**: Use interactive_bash — run soak test with concurrent curl + timeout commands

---

## Execution Strategy

### Parallel Execution Waves

```
Wave 1 (Foundation — all parallel, no deps):
├── Task 1: Guard _read_json_body() + _record_request_body_for_trace()
├── Task 2: Guard read_response_body() + _proxy_regular_response()
├── Task 3: Guard _send_upstream_error()
└── Task 4: Improve error logging (visible without --verbose)

Wave 2 (Connection layer — depends on 1-4, parallel among themselves):
├── Task 5: Add urllib3 dependency + PoolManager setup
├── Task 6: Refactor do_POST/streaming to use urllib3 PoolManager
├── Task 7: SSE read timeout (urllib3 Timeout.read) + CLI/config
├── Task 8: Bounded thread pool (ThreadPoolExecutor)
└── Task 9: Write Wave 2 tests

Wave 3 (Hardening — depends on 2, parallel among themselves):
├── Task 10: Ngrok tunnel health check + auto-reconnect
├── Task 11: SIGTERM handler + graceful shutdown
├── Task 12: Write Wave 3 tests
└── Task 13: Write 2-hour soak test script

Wave FINAL (After ALL — 4 parallel reviews, then user okay):
├── Task F1: Plan compliance audit (oracle)
├── Task F2: Code quality review (unspecified-high)
├── Task F3: Real manual QA (unspecified-high)
├── Task F4: Scope fidelity check (deep)
└── → Present results → Get explicit user okay

Critical Path: Task 1 → Task 6 → Task 8 → Task 10 → F1-F4 → user okay
Parallel Speedup: ~65% faster than sequential
Max Concurrent: 5 (Wave 1-2)
```

### Dependency Matrix
- **1-4**: None → 5-9, Wave 2
- **5**: 1-4 → 6, 7, Wave 2
- **6**: 5 → 8, Wave 2 and 3
- **7**: 5 → 8, 9, Wave 2
- **8**: 6, 7 → 10-13, Wave 3
- **9**: 1-8 → F1, F2, Wave FINAL
- **10**: 6 → 12, 13, Wave 3
- **11**: 8 → 12, 13, Wave 3
- **13**: 9 → F1, F2, F3, Wave FINAL

### Agent Dispatch Summary
- **Wave 1**: 4 tasks → `quick` (small, focused server.py changes)
- **Wave 2**: 5 tasks → T5-T6 `unspecified-high` (refactoring), T7 `quick`, T8 `quick`, T9 `quick`
- **Wave 3**: 4 tasks → T10 `unspecified-high` (process management), T11 `quick`, T12 `quick`, T13 `unspecified-high` (test infrastructure)
- **Wave FINAL**: 4 reviews → `oracle`, `unspecified-high` (×2), `deep`

---

## TODOs

> Implementation + Test = ONE Task. Never separate.
> EVERY task MUST have: Recommended Agent Profile + Parallelization info + QA Scenarios.
> **A task WITHOUT QA Scenarios is INCOMPLETE. No exceptions.**

- [x] 1. Guard `_read_json_body()` against client disconnect mid-upload

  **What to do**:
  - In `server.py`, wrap `self.rfile.read(length)` in a try/except that catches `(ConnectionError, OSError)` (catches `ConnectionResetError`, `ConnectionAbortedError`, `BrokenPipeError`)
  - On catch: log `LOG.warning("client disconnected while reading request body: %s", exc)`, then raise `ValueError("Client disconnected")` so the existing `except ValueError` block returns a 400
  - In `_record_request_body_for_trace()`, the `self.rfile.read(length)` at line 511 already catches `OSError` — good. No change needed there, but verify the logging level is WARNING not DEBUG.

  **Must NOT do**:
  - Do NOT change the 400 status code that existing ValueError handler sends
  - Do NOT leak raw exception messages to the client response

  **Recommended Agent Profile**:
  - Category: `quick`
    - Reason: Single-file change, well-defined pattern, small surface area
  - Skills: `[]`

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 1 (with Tasks 2, 3, 4)
  - **Blocks**: Wave 2 tasks
  - **Blocked By**: None (can start immediately)

  **References**:
  - `server.py:428-431` - Existing `_send_response_headers()` pattern for catching `BrokenPipeError, ConnectionError`
  - `server.py:471-491` - The `_read_json_body()` method — the `self.rfile.read(length)` call at line 482 is the unguarded point
  - `server.py:132-147` - The existing `except ValueError as exc:` handler that returns 400

  **Acceptance Criteria**:
  - [ ] Fault injection test: mock `self.rfile.read()` to raise `ConnectionResetError`, verify ValueError is raised from `_read_json_body`
  - [ ] Fault injection test: mock `self.rfile.read()` to raise `ConnectionAbortedError`, verify ValueError is raised
  - [ ] Existing test: `test_rejects_oversized_request_body` still passes
  - [ ] Existing test: normal request still works

  **QA Scenarios**:

  ```
  Scenario: Client disconnects mid-upload during JSON body read
    Tool: Bash (curl)
    Preconditions: Proxy running on port 9000, --verbose mode off
    Steps:
      1. Send a POST with a very large Content-Length but kill curl immediately:
         timeout 0.5 curl -X POST http://127.0.0.1:9000/v1/chat/completions \
           -H "Content-Type: application/json" \
           -H "Authorization: Bearer sk-test" \
           -H "Content-Length: 99999999" \
           --data-binary @- <<< "" 2>/dev/null || true
      2. Check proxy stdout/logs for: "client disconnected while reading request body"
      3. Verify proxy still responds to a normal request afterward
    Expected Result: Proxy logs WARNING, returns 400 (or closes conn), continues serving
    Failure Indicators: Proxy crash, unhandled exception traceback
    Evidence: .sisyphus/evidence/task-1-client-disconnect.txt

  Scenario: Normal request still works after fix
    Tool: Bash (curl)
    Preconditions: Proxy running
    Steps:
      1. curl -X POST http://127.0.0.1:9000/v1/chat/completions \
           -H "Content-Type: application/json" \
           -H "Authorization: Bearer sk-test" \
           -d '{"model":"deepseek-v4-pro","messages":[{"role":"user","content":"hi"}],"stream":false}'
      2. Verify HTTP 200 and valid JSON response
    Expected Result: 200 OK with valid response
    Failure Indicators: Non-200 status, connection refused
    Evidence: .sisyphus/evidence/task-1-normal-request.txt
  ```

  **Evidence to Capture**:
  - [ ] task-1-client-disconnect.txt — proxy log output showing graceful handling
  - [ ] task-1-normal-request.txt — curl response showing normal operation

  **Commit**: YES
  - Message: `fix(server): guard _read_json_body against client disconnect mid-upload`
  - Files: `src/deepseek_cursor_proxy/server.py`
  - Pre-commit: `python -m unittest discover -s tests`

- [x] 2. Guard `read_response_body()` and `_proxy_regular_response()` against upstream socket failures

  **What to do**:
  - In `server.py`, wrap `read_response_body(response)` call at line 575 (inside `_proxy_regular_response`) in try/except catching `(OSError, http.client.IncompleteRead, socket.timeout)`
  - On catch: log `LOG.warning("failed to read upstream response body: %s", exc)`, then send 502 JSON error and return `ProxyResponseResult(False, None)`
  - In `read_response_body()` itself (line 1208): wrap `response.read()`, `gzip.decompress()`, `zlib.decompress()` in try/except catching those same exceptions, re-raise as a custom `UpstreamReadError` or just let the caller handle it
  - Better approach: create a simple exception wrapper so `_proxy_regular_response` catches it cleanly

  **Must NOT do**:
  - Do NOT change the `_proxy_streaming_response()` path — it already has its own guard
  - Do NOT change response body on successful reads (no transparency/transformation changes)

  **Recommended Agent Profile**:
  - Category: `quick`
    - Reason: Well-documented single-method change, clear pattern
  - Skills: `[]`

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 1 (with Tasks 1, 3, 4)
  - **Blocks**: Wave 2 tasks
  - **Blocked By**: None

  **References**:
  - `server.py:563-632` - The `_proxy_regular_response()` method — note the `body = read_response_body(response)` at line 575
  - `server.py:1208-1218` - The `read_response_body()` function — note response.read() at line 1209 and gzip/zlib decompress at lines 1212-1217
  - `server.py:274-279` - Existing 502 error response pattern for URLError
  - `server.py:251-266` - Existing `HTTPError` handling pattern

  **Acceptance Criteria**:
  - [ ] Fault injection test: mock `response.read()` to raise `socket.timeout`, verify proxy returns 502
  - [ ] Fault injection test: mock `response.read()` to raise `http.client.IncompleteRead`, verify proxy returns 502
  - [ ] Normal request still returns 200 with correct body

  **QA Scenarios**:

  ```
  Scenario: Upstream response read fails with socket timeout
    Tool: Bash (curl)
    Preconditions: Need test upstream that delays response then closes
    Steps:
      1. Send request to proxy (which will hit a test upstream that fails)
      2. Verify HTTP 502 response
      3. Check proxy logs for "failed to read upstream response body"
    Expected Result: 502, proxy continues serving
    Failure Indicators: 500, crash, unhandled exception
    Evidence: .sisyphus/evidence/task-2-upstream-read-fail.txt
  ```

  **Evidence to Capture**:
  - [ ] task-2-upstream-read-fail.txt

  **Commit**: YES
  - Message: `fix(server): guard read_response_body against upstream socket errors`
  - Files: `src/deepseek_cursor_proxy/server.py`
  - Pre-commit: `python -m unittest discover -s tests`

- [x] 3. Guard `_send_upstream_error()` against cascading socket failure

  **What to do**:
  - In `_send_upstream_error()` at line 538, wrap `read_response_body(exc)` in try/except catching `(OSError, http.client.IncompleteRead, socket.timeout, ValueError)`
  - On catch: log `LOG.warning("failed to read upstream error body: %s", exc)`, use a fallback canned body `json.dumps({"error": {"message": "Upstream error, body unreadable"}}).encode("utf-8")`
  - The canned body must produce valid JSON response with the correct `exc.code` status
  - Ensure `Content-Length` header matches the canned body length

  **Must NOT do**:
  - Do NOT remove or break the existing `exc.headers.get("Content-Type")` logic for the success path
  - Do NOT change behavior when the error body reads fine

  **Recommended Agent Profile**:
  - Category: `quick`
    - Reason: Single error-handling path, minimal code change
  - Skills: `[]`

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 1 (with Tasks 1, 2, 4)
  - **Blocked By**: None

  **References**:
  - `server.py:532-561` - The `_send_upstream_error()` method — note `read_response_body(exc)` at line 538
  - `server.py:141-148` - Pattern for sending JSON error with trace

  **Acceptance Criteria**:
  - [ ] Fault injection test: mock `HTTPError.fp` to raise `OSError` on read, proxy returns 502 with fallback body
  - [ ] Normal upstream error (with readable body) still passes through correctly

  **QA Scenarios**:

  ```
  Scenario: Upstream error body unreadable
    Tool: Bash (curl)
    Preconditions: Upstream returns error but body read fails
    Steps:
      1. Send request that triggers upstream error
      2. Verify proxy returns HTTP 502 with {"error": {"message": "Upstream error, body unreadable"}}
      3. Check proxy logs for "failed to read upstream error body"
    Expected Result: Graceful fallback, proxy continues serving
    Failure Indicators: 500, crash, connection reset
    Evidence: .sisyphus/evidence/task-3-upstream-error-fallback.txt
  ```

  **Evidence to Capture**:
  - [ ] task-3-upstream-error-fallback.txt

  **Commit**: YES
  - Message: `fix(server): guard _send_upstream_error against cascading socket failure`
  - Files: `src/deepseek_cursor_proxy/server.py`
  - Pre-commit: `python -m unittest discover -s tests`

- [x] 4. Improve error logging visibility (crash diagnostics without `--verbose`)

  **What to do**:
  - Ensure all `LOG.warning()` calls at error catch sites in `server.py` use a consistent format: `"client disconnected while <context>: <exception>"` — these already exist but verify
  - Add a startup log message: `LOG.info("error logging: enabled (warnings visible without --verbose)")`
  - In `logging.py`, the `ConsoleLogFormatter` already shows `WARNING` prefix for non-verbose mode (line 27-29). Verify this includes the exception message.
  - Ensure `sys.excepthook` is set to catch any unhandled exceptions on the main thread and log them:
    ```python
    def _log_unhandled_exception(exc_type, exc_value, exc_traceback):
        LOG.critical("unhandled exception", exc_info=(exc_type, exc_value, exc_traceback))
    sys.excepthook = _log_unhandled_exception
    ```
  - Add a heartbeat log line every 500 requests (counter in server) to show the server is alive: `LOG.info("heartbeat: processed 500 requests")`

  **Must NOT do**:
  - Do NOT change existing log message format (keep backward compat for anyone parsing logs)
  - Do NOT log full request/response bodies at WARNING level (privacy concern — keep at DEBUG/verbose)

  **Recommended Agent Profile**:
  - Category: `quick`
    - Reason: Configuration/logging change, small scope
  - Skills: `[]`

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 1 (with Tasks 1, 2, 3)
  - **Blocked By**: None

  **References**:
  - `logging.py:32-39` - `configure_logging()` function — where handler/formatter is set up
  - `logging.py:16-29` - `ConsoleLogFormatter` — note it shows WARNING prefix in non-verbose mode
  - `server.py:428-429` - Existing LOG.warning pattern for disconnected client
  - `server.py:70-71` - `log_message` override that suppresses default logging

  **Acceptance Criteria**:
  - [ ] `sys.excepthook` is set during `configure_logging()`
  - [ ] Error catch sites log with consistent format
  - [ ] ~500th request logs a heartbeat message
  - [ ] Existing test `test_normal_logging_summarizes_without_bodies` still passes

  **QA Scenarios**:

  ```
  Scenario: Unhandled exception on main thread is logged
    Tool: Bash (python)
    Preconditions: Proxy importable
    Steps:
      1. python -c "from deepseek_cursor_proxy.logging import configure_logging; configure_logging(verbose=False); import sys; sys.excepthook(TypeError, TypeError('test'), None)"
      2. Verify CRITICAL log message appears with traceback
    Expected Result: Exception logged at CRITICAL level
    Evidence: .sisyphus/evidence/task-4-excepthook.txt
  ```

  **Evidence to Capture**:
  - [ ] task-4-excepthook.txt

  **Commit**: YES
  - Message: `fix(logging): improve crash visibility without --verbose`
  - Files: `src/deepseek_cursor_proxy/logging.py`, `src/deepseek_cursor_proxy/server.py`
  - Pre-commit: `python -m unittest discover -s tests`

- [x] 5. Add `urllib3` dependency and configure `PoolManager`

  **What to do**:
  - Add `urllib3>=2.0.0` to `dependencies` in `pyproject.toml`
  - In `server.py`, add a module-level `UpstreamPool` class that wraps `urllib3.PoolManager`:
    ```python
    import urllib3
    from urllib3.util import Timeout as Urllib3Timeout

    class UpstreamPool:
        def __init__(self, max_connections: int = 10, max_keepalive: int = 5):
            self._pool = urllib3.PoolManager(
                maxsize=max_connections,
                block=True,
                socket_options=urllib3.util.connection.SOCKET_OPTIONS + [
                    (socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1),
                ],
            )
    ```
  - Add `--max-pool-connections` CLI arg (default 10) and `--max-keepalive` (default 5) to `build_arg_parser()`
  - Add corresponding config fields to `ProxyConfig` class
  - Initialize `UpstreamPool` in `main()` and attach to server
  - Add a property to `DeepSeekProxyHandler` to access the pool

  **Must NOT do**:
  - Do NOT remove `urlopen` yet (that's Task 6)
  - Do NOT set `urlopen.install()` as the default opener — keep both paths temporarily
  - Do NOT add retry logic yet (no duplicate POST risk)

  **Recommended Agent Profile**:
  - Category: `quick`
    - Reason: Dependency addition + simple wrapper class, well-defined boundary
  - Skills: `[]`

  **Parallelization**:
  - **Can Run In Parallel**: NO (depends on Wave 1 completing)
  - **Parallel Group**: Wave 2 (with Tasks 6, 7, 8, 9)
  - **Blocks**: Tasks 6, 7
  - **Blocked By**: Wave 1 completion (Tasks 1-4)

  **References**:
  - `pyproject.toml:28-30` - Existing `dependencies` section
  - `server.py:49-53` - `DeepSeekProxyServer` class attributes (pattern for adding new server-level state)
  - `config.py:187-206` - `ProxyConfig` dataclass (pattern for adding new config fields)
  - `server.py:58-68` - Handler property pattern for accessing server state

  **Acceptance Criteria**:
  - [ ] `pip install -e .` succeeds with urllib3 dependency installed
  - [ ] `UpstreamPool` can be initialized with correct defaults
  - [ ] `--max-pool-connections` CLI arg is accepted
  - [ ] Config file `max_pool_connections` key is respected
  - [ ] `python -m unittest discover -s tests` all pass

  **QA Scenarios**:

  ```
  Scenario: Pool initializes and is accessible from handler
    Tool: Bash (python -c)
    Preconditions: Package installed
    Steps:
      1. python -c "from deepseek_cursor_proxy.server import UpstreamPool; p = UpstreamPool(max_connections=5); print(p)"
      2. Verify it prints without error
    Expected Result: Pool created, no import errors
    Failure Indicators: ImportError, urllib3 not found
    Evidence: .sisyphus/evidence/task-5-pool-init.txt
  ```

  **Evidence to Capture**:
  - [ ] task-5-pool-init.txt

  **Commit**: YES
  - Message: `feat(deps): add urllib3 dependency and configure UpstreamPool`
  - Files: `pyproject.toml`, `src/deepseek_cursor_proxy/server.py`, `src/deepseek_cursor_proxy/config.py`
  - Pre-commit: `python -m unittest discover -s tests`

- [x] 6. Refactor `do_POST` and streaming to use `urllib3` `PoolManager`

  **What to do**:
  - Replace `urlopen(request, timeout=...)` in `do_POST` with `pool.request('POST', url, body=upstream_body, headers=upstream_headers, preload_content=not stream)`
  - For **regular (non-streaming)** responses: use `pool.request()` with `preload_content=True` (default), then access `response.data` and `response.status` / `response.headers`
  - For **streaming** responses: use `pool.request()` with `preload_content=False`, then use `response.readline()` in the existing streaming loop (line 700). urllib3's `HTTPResponse.readline()` works identically to `http.client.HTTPResponse.readline()` for SSE.
  - Replace all references to:
    - `getattr(response, "status", 200)` → `response.status` (urllib3 always has it)
    - `response.headers.get(...)` → `response.headers.get(...)` (urllib3 uses case-insensitive dict — works)
    - `response.read()` → `response.data` (preloaded) or keep `.read()` for non-preloaded
    - `response_headers(response)` helper → `dict(response.headers.items())`
  - Remove the `read_response_body()` encoding handling IF urllib3 auto-decompresses (it does by default). Set `auto_decompress=False` on `PoolManager` init to maintain current behavior of reading raw bytes and decompressing manually. OR better: set `auto_decompress=True` and simplify `read_response_body()` to just `return response.data`.
  - IMPORTANT: `pool.request()` doesn't raise `HTTPError` for non-2xx — it returns a response with `.status` >= 400. Check `response.status` and handle accordingly.
  - For streaming: ensure `response.release_conn()` is called after the streaming loop completes (or use context manager) so the connection returns to the pool.

  **Must NOT do**:
  - Do NOT change the SSE line format (byte-identical chunks)
  - Do NOT add retry logic (no `retries` parameter or minimal `retries=0`)
  - Do NOT change any response transformation logic (transform.py unaffected)
  - Do NOT change the timeout behavior yet (that's Task 7)

  **Recommended Agent Profile**:
  - Category: `unspecified-high`
    - Reason: Core refactoring affecting request/response lifecycle, streaming behavior
  - Skills: `[]`

  **Parallelization**:
  - **Can Run In Parallel**: NO (depends on Task 5)
  - **Parallel Group**: Wave 2 (sequential with Task 7)
  - **Blocks**: Tasks 8, 10
  - **Blocked By**: Task 5

  **References**:
  - `server.py:247-250` - Current `urlopen` call with timeout
  - `server.py:285-337` - Current response handling (`with response:` block)
  - `server.py:563-632` - `_proxy_regular_response()` — uses `response.read()`, `response.status`, `response.headers`
  - `server.py:634-755` - `_proxy_streaming_response()` — uses `response.readline()`, `response.status`
  - `server.py:1208-1218` - `read_response_body()` — gzip/deflate handling
  - urllib3 docs: `https://urllib3.readthedocs.io/en/stable/user-guide.html#streaming-io`

  **Acceptance Criteria**:
  - [ ] Non-streaming request → 200 with correct JSON response body
  - [ ] Streaming request → SSE chunks delivered correctly, ending with `data: [DONE]`
  - [ ] Upstream 4xx/5xx errors are forwarded correctly
  - [ ] All existing unit tests pass
  - [ ] No connection leaks (monitored via pool.pools count)

  **QA Scenarios**:

  ```
  Scenario: Non-streaming request with new pool
    Tool: Bash (curl)
    Preconditions: Proxy running, test upstream responding normally
    Steps:
      1. curl -X POST http://127.0.0.1:9000/v1/chat/completions \
           -H "Content-Type: application/json" \
           -H "Authorization: Bearer sk-test" \
           -d '{"model":"deepseek-v4-pro","messages":[{"role":"user","content":"hi"}],"stream":false}'
      2. Verify HTTP 200, valid JSON with expected structure
    Expected Result: 200, same response structure as before
    Evidence: .sisyphus/evidence/task-6-non-streaming-pool.txt

  Scenario: Streaming request with new pool
    Tool: Bash (curl)
    Steps:
      1. curl -N -X POST http://127.0.0.1:9000/v1/chat/completions \
           -H "Content-Type: application/json" \
           -H "Authorization: Bearer sk-test" \
           -d '{"model":"deepseek-v4-pro","messages":[{"role":"user","content":"hi"}],"stream":true}'
      2. Verify SSE format: "data: {...}\n\n" chunks, ending with "data: [DONE]\n\n"
    Expected Result: SSE stream with correct format
    Evidence: .sisyphus/evidence/task-6-streaming-pool.txt
  ```

  **Evidence to Capture**:
  - [ ] task-6-non-streaming-pool.txt
  - [ ] task-6-streaming-pool.txt

  **Commit**: YES
  - Message: `feat(server): replace urlopen with urllib3 PoolManager`
  - Files: `src/deepseek_cursor_proxy/server.py`
  - Pre-commit: `python -m unittest discover -s tests`

- [x] 7. Add configurable SSE read timeout (`--stream-read-timeout`)

  **What to do**:
  - Add `--stream-read-timeout` CLI arg (float, default 180.0, config key `stream_read_timeout`) to `build_arg_parser()`
  - Add `stream_read_timeout: float = 180.0` field to `ProxyConfig`
  - When building the upstream request for streaming, set `timeout=Urllib3Timeout(connect=config.request_timeout, read=config.stream_read_timeout)`
  - In the streaming loop (line 700), `response.readline()` will now raise `urllib3.exceptions.ReadTimeoutError` if upstream is silent for `stream_read_timeout` seconds
  - Catch `urllib3.exceptions.ReadTimeoutError` alongside existing `(HTTPException, OSError)` at line 701
  - Log the timeout distinctly: `LOG.warning("upstream streaming response read timed out after %ss: %s", config.stream_read_timeout, exc)`
  - For non-streaming requests, use `timeout=Urllib3Timeout(connect=config.request_timeout, read=config.request_timeout)` for backward compat

  **Must NOT do**:
  - Do NOT apply read timeout to non-streaming requests (only SSE)
  - Do NOT change the `request_timeout` config field behavior
  - Do NOT add to default config.yaml (optional field, falls back to default 180s)

  **Recommended Agent Profile**:
  - Category: `quick`
    - Reason: Configuration addition + 1-line timeout change + 1 catch addition
  - Skills: `[]`

  **Parallelization**:
  - **Can Run In Parallel**: YES (with Tasks 5, 6, 8, 9)
  - **Parallel Group**: Wave 2 (with Tasks 5, 6, 8, 9)
  - **Blocked By**: Task 6 (needs urllib3 in place)

  **References**:
  - `config.py:28` - `DEFAULT_REQUEST_TIMEOUT` — existing timeout pattern
  - `config.py:187-206` - `ProxyConfig` dataclass — pattern for adding new fields
  - `server.py:219-221` - Current `upstream_body` construction — where timeout would apply
  - `server.py:247-250` - Current `urlopen(request, timeout=...)` — where timeout is passed
  - `server.py:699-703` - Current streaming read exception handler — where `ReadTimeoutError` should be caught

  **Acceptance Criteria**:
  - [ ] `--stream-read-timeout 60` shortens timeout to 60s
  - [ ] Upstream that pauses > stream_read_timeout seconds triggers ReadTimeoutError
  - [ ] Non-streaming requests time out after `request_timeout`, not `stream_read_timeout`
  - [ ] Existing tests pass

  **QA Scenarios**:

  ```
  Scenario: Streaming read timeout triggers correctly
    Tool: Bash (curl + python)
    Preconditions: Proxy running with --stream-read-timeout 5
    Steps:
      1. Start proxy: uv run python -m deepseek_cursor_proxy.server --no-ngrok --port 9000 --stream-read-timeout 5
      2. Send streaming request to a test upstream that sends 1 chunk then pauses 10s
      3. Verify proxy logs "upstream streaming response read timed out after 5s"
      4. Verify proxy returns with partial response to client
    Expected Result: Read timeout logged, proxy continues serving
    Evidence: .sisyphus/evidence/task-7-stream-read-timeout.txt
  ```

  **Evidence to Capture**:
  - [ ] task-7-stream-read-timeout.txt

  **Commit**: YES
  - Message: `feat(server): add configurable stream-read-timeout`
  - Files: `src/deepseek_cursor_proxy/server.py`, `src/deepseek_cursor_proxy/config.py`
  - Pre-commit: `python -m unittest discover -s tests`

- [x] 8. Add bounded thread pool to `ThreadingHTTPServer`

  **What to do**:
  - Create a new class `BoundedThreadPoolHTTPServer` that inherits from `ThreadingHTTPServer` and uses `concurrent.futures.ThreadPoolExecutor` instead of creating a new thread per request:
    ```python
    from concurrent.futures import ThreadPoolExecutor

    class BoundedThreadPoolHTTPServer(ThreadingHTTPServer):
        def __init__(self, *args, max_workers=20, **kwargs):
            super().__init__(*args, **kwargs)
            self.executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="proxy")

        def process_request(self, request, client_address):
            self.executor.submit(self.process_request_thread, request, client_address)

        def server_close(self):
            self.executor.shutdown(wait=True, cancel_futures=True)
            super().server_close()
    ```
  - Add `--max-thread-pool` CLI arg (int, default 20, config key `max_thread_pool`)
  - Add `max_thread_pool: int = 20` field to `ProxyConfig`
  - In `main()`, replace `DeepSeekProxyServer` with `BoundedThreadPoolHTTPServer`:
    ```python
    server = BoundedThreadPoolHTTPServer(
        (config.host, config.port), DeepSeekProxyHandler,
        max_workers=config.max_thread_pool
    )
    ```
  - Ensure `daemon_threads = True` is set so threads don't block shutdown
  - Handle `RuntimeError: cannot schedule new futures after shutdown` in case the executor is shut down while accepting connections (wrap in try/except at `process_request`)
  - Log pool utilization: `LOG.info("thread pool: max_workers=%s active=%s queue=%s", max_workers, len(executor._threads), executor._work_queue.qsize())` — add a periodic log every 100 requests

  **Must NOT do**:
  - Do NOT remove or change the `ThreadingHTTPServer` inheritance chain (keep `socketserver` compatibility)
  - Do NOT change the handler class interface
  - Do NOT add `asyncio` or other async patterns

  **Recommended Agent Profile**:
  - Category: `quick`
    - Reason: Well-defined class replacement, config wiring, no algorithmic complexity
  - Skills: `[]`

  **Parallelization**:
  - **Can Run In Parallel**: NO (modifies server setup, depends on Wave 1 + Task 6)
  - **Parallel Group**: Wave 2 (with Tasks 5, 6, 7, 9)
  - **Blocked By**: Task 6

  **References**:
  - `server.py:49-53` - `DeepSeekProxyServer` — existing server subclass pattern
  - `server.py:1308-1311` - Current server instantiation in `main()`
  - `config.py:18-19` - `DEFAULT_HOST`, `DEFAULT_PORT` — example defaults
  - Python docs: `https://docs.python.org/3/library/concurrent.futures.html#threadpoolexecutor`

  **Acceptance Criteria**:
  - [ ] Server starts with bounded pool
  - [ ] `--max-thread-pool 5` limits concurrent requests to 5
  - [ ] When pool is full, new connections queue (not rejected)
  - [ ] `server_close()` drains executor before shutdown
  - [ ] All existing tests pass

  **QA Scenarios**:

  ```
  Scenario: Server starts with bounded pool
    Tool: Bash (python -c)
    Steps:
      1. python -c "from deepseek_cursor_proxy.server import BoundedThreadPoolHTTPServer, DeepSeekProxyHandler; s = BoundedThreadPoolHTTPServer(('127.0.0.1', 0), DeepSeekProxyHandler, max_workers=5); print('workers:', s.executor._max_workers); s.server_close()"
      2. Verify output: "workers: 5"
    Expected Result: Server starts with pool of 5 workers
    Evidence: .sisyphus/evidence/task-8-bounded-pool.txt
  ```

  **Evidence to Capture**:
  - [ ] task-8-bounded-pool.txt

  **Commit**: YES
  - Message: `feat(server): bounded thread pool for request handling`
  - Files: `src/deepseek_cursor_proxy/server.py`, `src/deepseek_cursor_proxy/config.py`
  - Pre-commit: `python -m unittest discover -s tests`

- [x] 9. Write Wave 2 resilience tests

  **What to do**:
  - Create `tests/test_resilience.py` with fault-injection tests for Wave 2 changes:
    1. `test_upstream_pool_initialization` — verify `UpstreamPool` creates `PoolManager` with correct params
    2. `test_upstream_pool_request_non_streaming` — mock pool.request, verify correct URL/headers/body
    3. `test_upstream_pool_request_streaming` — mock pool.request with `preload_content=False`, verify readline loop works
    4. `test_streaming_read_timeout_raised` — verify `urllib3.ReadTimeoutError` is caught in streaming loop
    5. `test_streaming_read_timeout_not_raised_normal_request` — verify non-streaming doesn't get read timeout
    6. `test_bounded_thread_pool_limits_concurrency` — submit N > max_workers tasks, verify only max_workers run concurrently
    7. `test_bounded_thread_pool_queues_excess` — submit N > max_workers, verify queue accepts them
    8. `test_pool_connection_reuse` — verify connection count stays bounded after many requests
    9. `test_urllib3_handles_upstream_4xx` — verify pool.request with 4xx response works (returns response, doesn't raise)
    10. `test_urllib3_handles_upstream_5xx` — same for 5xx

  **Must NOT do**:
  - Do NOT make real HTTP requests to DeepSeek API
  - Do NOT add heavy dependencies — use `unittest.mock` for mocking
  - Do NOT test ngrok (that's Wave 3)

  **Recommended Agent Profile**:
  - Category: `quick`
    - Reason: Tests-only task, well-scoped mocking patterns
  - Skills: `[]`

  **Parallelization**:
  - **Can Run In Parallel**: YES (after all Wave 2 implementation tasks)
  - **Parallel Group**: Wave 2 (with Tasks 5, 6, 7, 8)
  - **Blocked By**: Tasks 5, 6, 7, 8

  **References**:
  - `tests/test_server.py:104-115` - `_make_handler_stub` pattern
  - `tests/test_server.py:58-70` - `_FakeStreamingResponse` pattern
  - `tests/test_server.py:73-78` - `_FailingStreamingResponse` pattern
  - `tests/test_streaming.py` - Existing mock patterns for ReasoningStore

  **Acceptance Criteria**:
  - [ ] All 10+ test methods pass
  - [ ] `python -m unittest tests.test_resilience -v` shows all tests listed
  - [ ] Tests do not require network access

  **QA Scenarios**:

  ```
  Scenario: All resilience tests pass
    Tool: Bash (python -m unittest)
    Steps:
      1. python -m unittest tests.test_resilience -v
      2. Verify all tests PASS (OK)
    Expected Result: 10+ tests pass, 0 failures
    Evidence: .sisyphus/evidence/task-9-resilience-tests.txt
  ```

  **Evidence to Capture**:
  - [ ] task-9-resilience-tests.txt

  **Commit**: YES (groups with Task 8)
  - Message: `test: resilience tests for connection pooling and timeouts`
  - Files: `tests/test_resilience.py`
  - Pre-commit: `python -m unittest tests.test_resilience`

- [x] 10. Add ngrok tunnel health check and auto-reconnect

  **What to do**:
  - In `tunnel.py`, add a `HealthCheckConfig` dataclass:
    ```python
    @dataclass
    class HealthCheckConfig:
        check_interval: float = 30.0  # seconds between health checks
        startup_timeout: float = 15.0
        recovery_max_retries: int = 3
        recovery_retry_delay: float = 5.0
    ```
  - Modify `NgrokTunnel` to:
    1. Accept `health_check: HealthCheckConfig | None = None` parameter. If None, no health checks
    2. Add `start_health_check()` method that starts a background `threading.Thread` (daemon) running the loop
    3. The health check loop:
       a. Sleep for `check_interval` seconds
       b. Check if `self.process` is still alive (`self.process.poll() is None`)
       c. If alive, poll the ngrok API (`self.api_url/endpoints`) — if it returns valid data with a public URL, continue
       d. If process is dead OR API check fails → log `LOG.warning("ngrok tunnel health check failed: ...")`
       e. Attempt recovery: terminate stale process → wait → restart → wait for public URL
       f. If recovery succeeds: log `LOG.info("ngrok tunnel recovered, new URL: %s", url)`
       g. If recovery fails after `recovery_max_retries`: log `LOG.critical(...)` but don't exit (proxy still works on localhost)
    4. Update the proxy's public URL when tunnel reconnects (callback or event pattern)
  - Add `--ngrok-health-check-interval` CLI arg (float, default 30.0, 0 = disable)
  - Add corresponding `ngrok_health_check_interval: float = 30.0` to `ProxyConfig`
  - In `main()`, if `config.ngrok` and `config.ngrok_health_check_interval > 0`, start health checks after tunnel is up
  - For the callback pattern: add a `public_url: str | None` mutable field to `NgrokTunnel`, update when tunnel restarts. The server references this field for URL display.

  **Must NOT do**:
  - Do NOT exit the proxy when ngrok health check fails (proxy can still serve localhost)
  - Do NOT check ngrok if `config.ngrok` is False
  - Do NOT add external dependencies — use `threading` and `urllib` (already available)
  - Do NOT modify the existing `start()` method's public interface

  **Recommended Agent Profile**:
  - Category: `unspecified-high`
    - Reason: Process management, background thread lifecycle, recovery logic
  - Skills: `[]`

  **Parallelization**:
  - **Can Run In Parallel**: NO (depends on Wave 2, modifies server startup)
  - **Parallel Group**: Wave 3 (with Tasks 11, 12, 13)
  - **Blocked By**: Task 6

  **References**:
  - `tunnel.py:57-111` - The full `NgrokTunnel` class
  - `tunnel.py:50-54` - `ngrok_agent_urls()` — URLs to check
  - `tunnel.py:84-100` - `wait_for_public_url()` — pattern for polling ngrok API
  - `server.py:1315-1324` - Current ngrok tunnel start and URL capture in `main()`
  - `server.py:1358-1366` - Current `finally` block that stops tunnel

  **Acceptance Criteria**:
  - [ ] Ngrok starts normally (no regression)
  - [ ] Health check thread starts after tunnel is up (verify via log message)
  - [ ] Killing ngrok process triggers health check failure → recovery attempt
  - [ ] Recovery succeeds (process restarted, new URL acquired)
  - [ ] `--ngrok-health-check-interval 0` disables health checks
  - [ ] Local-only mode (`--no-ngrok`) never starts health checks

  **QA Scenarios**:

  ```
  Scenario: Ngrok health check starts and monitors tunnel
    Tool: interactive_bash (tmux)
    Preconditions: ngrok installed, proxy started with --ngrok
    Steps:
      1. Start proxy, wait for "ngrok tunnel: https://..." log message
      2. Verify log contains "ngrok health check: started (interval=30s)"
      3. Kill ngrok process: pkill ngrok
      4. Wait for health check to detect failure (max 35s)
      5. Verify proxy logs: "ngrok tunnel health check failed: ..."
      6. Verify proxy logs: "ngrok tunnel recovered, new URL: https://..."
      7. Verify proxy still responds to requests
    Expected Result: Ngrok detected dead, auto-restarted, new URL acquired
    Failure Indicators: Proxy crashes, doesn't recover, health check thread dies silently
    Evidence: .sisyphus/evidence/task-10-ngrok-healthcheck.txt
  ```

  **Evidence to Capture**:
  - [ ] task-10-ngrok-healthcheck.txt

  **Commit**: YES
  - Message: `feat(tunnel): ngrok health check and auto-reconnect`
  - Files: `src/deepseek_cursor_proxy/tunnel.py`, `src/deepseek_cursor_proxy/server.py`, `src/deepseek_cursor_proxy/config.py`
  - Pre-commit: `python -m unittest discover -s tests`

- [x] 11. Add SIGTERM handler and graceful shutdown

  **What to do**:
  - In `server.py`, add a `GracefulShutdown` context/helper:
    ```python
    import signal

    _shutdown_requested = threading.Event()

    def _handle_sigterm(signum, frame):
        LOG.info("received signal %s, initiating graceful shutdown", signum)
        _shutdown_requested.set()
    ```
  - In `main()`:
    1. Register `signal.signal(signal.SIGTERM, _handle_sigterm)` and `signal.signal(signal.SIGINT, _handle_sigterm)` (SIGINT for Ctrl+C too, but keep existing KeyboardInterrupt handling)
    2. Replace `server.serve_forever()` with:
       ```python
       try:
           while not _shutdown_requested.is_set():
               server.handle_request()  # blocks for up to 0.5s when no request pending
       except KeyboardInterrupt:
           LOG.info("shutting down (keyboard interrupt)")
       ```
       OR keep `server.serve_forever()` but poll the event:
       ```python
       server.timeout = 0.5  # poll interval
       while not _shutdown_requested.is_set():
           server.handle_request()  # handles one request then returns
       ```
  - On shutdown:
    1. Log `LOG.info("graceful shutdown: draining active requests...")`
    2. Set `server._shutdown = True` to reject new connections
    3. If using `BoundedThreadPoolHTTPServer`, call `server.executor.shutdown(wait=True, cancel_futures=False)` — wait for active requests, don't cancel in-flight streaming
    4. In the `finally` block, ensure reasoning store is flushed:
       ```python
       store.prune()  # compact cache
       store.close()
       ```
    5. Stop ngrok tunnel
    6. Log `LOG.info("graceful shutdown: complete")`
  - Handle the case where in-flight streaming threads are still writing to `wfile`:
    - Set `self.close_connection = True` in streaming handler when shutdown is detected
    - The `_write_to_client()` method already catches BrokenPipeError — `connection_reset = True` case is handled

  **Must NOT do**:
  - Do NOT force-kill in-flight streaming requests (let them complete or client disconnect)
  - Do NOT block shutdown for more than `server.timeout * 2` waiting for idle connections
  - Do NOT change the `KeyboardInterrupt` behavior (must still work)

  **Recommended Agent Profile**:
  - Category: `quick`
    - Reason: Signal handling + shutdown orchestration, well-scoped
  - Skills: `[]`

  **Parallelization**:
  - **Can Run In Parallel**: NO (depends on ThreadPool server from Task 8, ngrok from Task 10)
  - **Parallel Group**: Wave 3 (with Tasks 10, 12, 13)
  - **Blocked By**: Tasks 8, 10

  **References**:
  - `server.py:1358-1367` - Current `serve_forever()` and `finally` block
  - `server.py:1362-1366` - Current KeyboardInterrupt handling
  - `server.py:1313-1324` - Ngrok tunnel initialization
  - `server.py:1290-1298` - ReasoningStore initialization
  - Python docs: `https://docs.python.org/3/library/signal.html#example`

  **Acceptance Criteria**:
  - [ ] `kill -TERM <pid>` triggers graceful shutdown log messages
  - [ ] Active streaming request completes before shutdown (not interrupted mid-stream)
  - [ ] No new requests accepted after shutdown signal
  - [ ] Reasoning store is flushed and closed
  - [ ] Ngrok tunnel is stopped
  - [ ] `Ctrl+C` (SIGINT) still works as before

  **QA Scenarios**:

  ```
  Scenario: SIGTERM triggers graceful shutdown
    Tool: interactive_bash (tmux)
    Preconditions: Proxy running in background with PID
    Steps:
      1. Start proxy in background: uv run python -m deepseek_cursor_proxy.server --no-ngrok --port 9000 &
         PID=$!
      2. Send a streaming request that lasts 5+ seconds
      3. Immediately after, send: kill -TERM $PID
      4. Check proxy output for: "received signal 15, initiating graceful shutdown"
      5. Check proxy output for: "graceful shutdown: draining active requests..."
      6. Check proxy output for: "graceful shutdown: complete"
      7. Verify process exits with code 0
    Expected Result: Graceful shutdown with all log messages
    Failure Indicators: Process killed before log messages, non-zero exit code
    Evidence: .sisyphus/evidence/task-11-sigterm.txt
  ```

  **Evidence to Capture**:
  - [ ] task-11-sigterm.txt

  **Commit**: YES
  - Message: `feat(server): SIGTERM handler and graceful shutdown`
  - Files: `src/deepseek_cursor_proxy/server.py`, `src/deepseek_cursor_proxy/reasoning_store.py`
  - Pre-commit: `python -m unittest discover -s tests`

- [x] 12. Write Wave 3 resilience tests

  **What to do**:
  - Add to `tests/test_resilience.py` for Wave 3 changes:
    1. `test_ngrok_health_check_detects_dead_process` — mock `Popen.poll()` to return non-None, verify health check detects it
    2. `test_ngrok_health_check_polls_api` — mock `urlopen` for ngrok API, verify parsing works
    3. `test_ngrok_recovery_restarts_process` — mock health check detects dead ngrok, verify `Popen` is called again
    4. `test_ngrok_health_check_disabled` — verify `check_interval=0` doesn't start thread
    5. `test_sigterm_sets_shutdown_event` — mock signal, verify `_shutdown_requested` is set
    6. `test_serve_forever_polls_shutdown_event` — verify `handle_request` loop exits when event is set
    7. `test_graceful_shutdown_drains_pool` — verify executor shutdown is called
    8. `test_partial_reasoning_flush_on_shutdown` — verify store.prune() is called during shutdown
    9. Test that existing functionality isn't broken (import sanity, basic mock flow)

  **Must NOT do**:
  - Do NOT make real ngrok API calls in tests
  - Do NOT fork actual subprocesses in tests (mock Popen)
  - Do NOT send real signals in tests (mock signal handling)

  **Recommended Agent Profile**:
  - Category: `quick`
    - Reason: Test-only task, mocking patterns already established
  - Skills: `[]`

  **Parallelization**:
  - **Can Run In Parallel**: YES (with other Wave 3 tasks)
  - **Parallel Group**: Wave 3 (with Tasks 10, 11, 13)
  - **Blocked By**: Tasks 10, 11

  **References**:
  - `tests/test_server.py:104-115` - `_make_handler_stub` pattern (use similar for tunnel stubs)
  - `tests/test_tunnel.py` - Check existing tunnel tests for patterns
  - `tunnel.py:57-111` - Full NgrokTunnel class
  - `server.py:1238-1367` - Full main() function

  **Acceptance Criteria**:
  - [ ] All 9+ new test methods pass
  - [ ] `python -m unittest tests.test_resilience -v` shows all Wave 3 tests
  - [ ] Tests do not require network access or real ngrok

  **QA Scenarios**:

  ```
  Scenario: All resilience tests pass
    Tool: Bash (python -m unittest)
    Steps:
      1. python -m unittest tests.test_resilience -v
      2. Verify all tests PASS
    Expected Result: All tests pass
    Evidence: .sisyphus/evidence/task-12-wave3-tests.txt
  ```

  **Evidence to Capture**:
  - [ ] task-12-wave3-tests.txt

  **Commit**: YES (groups with Task 11)
  - Message: `test: resilience tests for ngrok health check and graceful shutdown`
  - Files: `tests/test_resilience.py`
  - Pre-commit: `python -m unittest tests.test_resilience`

- [x] 13. Write 2-hour soak test script

  **What to do**:
  - Create `tests/test_soak.py` — a standalone soak test script (not unittest, a script that runs for 2 hours):
    ```python
    """
    Soak test for deepseek-cursor-proxy.

    Usage:
        python tests/test_soak.py [--duration 7200] [--concurrency 10] [--proxy-url http://127.0.0.1:9000]

    Starts a test upstream, starts a proxy pointed at it, then runs
    concurrent requests with random mid-request cancellations. Reports
    crashes, connection errors, and throughput stats.
    """
    ```
  - The soak test should:
    1. Start a minimal test upstream server (like `_PlainFakeUpstream` from test_server.py)
    2. Start a proxy instance connected to it
    3. Launch `--concurrency N` worker threads
    4. Each worker:
       - Sends a random request (streaming or non-streaming, 50/50)
       - Randomly cancels mid-stream (close the connection after 0.1-2.0s)
       - Reports success/failure
       - Repeats until duration expires
    5. Track metrics: total requests, cancelled requests, errors, crashes
    6. Report: `PASS` if zero unhandled exceptions, `FAIL` otherwise
  - Run continuously for the specified duration
  - Exit code 0 = no crashes detected, 1 = crashes detected

  **Must NOT do**:
  - Do NOT require ngrok (runs on localhost)
  - Do NOT make requests to external APIs
  - Do NOT require --verbose mode (soak test has its own metrics)

  **Recommended Agent Profile**:
  - Category: `unspecified-high`
    - Reason: Multi-threaded test infrastructure, process management, metrics collection
  - Skills: `[]`

  **Parallelization**:
  - **Can Run In Parallel**: YES (with other Wave 3 tasks)
  - **Parallel Group**: Wave 3 (with Tasks 10, 11, 12)
  - **Blocked By**: Wave 2 completion (needs all fixes in place)

  **References**:
  - `tests/test_server.py:352-388` - `_PlainFakeUpstream` — test upstream pattern
  - `tests/test_server.py:413-427` - `_Fixture` — test server lifecycle pattern
  - `tests/test_server.py:430-444` - `_post()` — test request helper
  - `tests/test_server.py:512-537` - `test_streaming_response_closes_after_done` — streaming test pattern
  - Python `concurrent.futures` for worker threads

  **Acceptance Criteria**:
  - [ ] `python tests/test_soak.py --duration 10 --concurrency 5` runs for 10 seconds without error
  - [ ] Reports total requests, cancelled, errors, crashes at end
  - [ ] Exit code 0 when no crashes
  - [ ] Script handles Ctrl+C gracefully

  **QA Scenarios**:

  ```
  Scenario: Soak test runs briefly without errors
    Tool: Bash (python)
    Preconditions: proxy package installed
    Steps:
      1. python tests/test_soak.py --duration 10 --concurrency 5
      2. Verify output contains: "RESULT: PASS" or "RESULT: FAIL"
      3. Verify exit code 0
    Expected Result: Soak test completes with metrics, PASS result
    Failure Indicators: Crash during soak test run
    Evidence: .sisyphus/evidence/task-13-soak-test.txt
  ```

  **Evidence to Capture**:
  - [ ] task-13-soak-test.txt

  **Commit**: YES
  - Message: `test: 2-hour soak test script for connection resilience`
  - Files: `tests/test_soak.py`
  - Pre-commit: `python -m unittest tests.test_resilience`

---

## Final Verification Wave (MANDATORY — after ALL implementation tasks)

- [x] F1. **Plan Compliance Audit** — `oracle`
  Read the plan end-to-end. For each "Must Have": verify implementation exists (read file, curl endpoint, run command). For each "Must NOT Have": search codebase for forbidden patterns — reject with file:line if found. Check evidence files exist in .sisyphus/evidence/. Compare deliverables against plan.
  Output: `Must Have [N/N] | Must NOT Have [N/N] | Tasks [N/N] | VERDICT: APPROVE/REJECT`

- [x] F2. **Code Quality Review** — `unspecified-high`
  Run `python -m unittest discover -s tests` + lint. Review all changed files for: `as any`/`@ts-ignore` equivalents, empty catches, `print()` in prod, commented-out code, unused imports. Check AI slop: excessive comments, over-abstraction, generic names.
  Output: `Build [PASS/FAIL] | Lint [PASS/FAIL] | Tests [N pass/N fail] | Files [N clean/N issues] | VERDICT`

- [x] F3. **Real Manual QA** — `unspecified-high`
  Start proxy, execute EVERY QA scenario from EVERY task. Test cross-task integration. Test edge cases: upstream timeout, client disconnect mid-stream, ngrok restart, SIGTERM while streaming. Save to `.sisyphus/evidence/final-qa/`.
  Output: `Scenarios [N/N pass] | Integration [N/N] | Edge Cases [N tested] | VERDICT`

- [x] F4. **Scope Fidelity Check** — `deep`
  For each task: read "What to do", read actual diff (git log/diff). Verify 1:1 — everything in spec was built (no missing), nothing beyond spec was built (no creep). Check "Must NOT do" compliance. Detect cross-task contamination.
  Output: `Tasks [N/N compliant] | Contamination [CLEAN/N issues] | Unaccounted [CLEAN/N files] | VERDICT`

---

## Commit Strategy

- **1**: `fix(server): guard _read_json_body against client disconnect` — server.py
- **2**: `fix(server): guard read_response_body and _proxy_regular_response` — server.py
- **3**: `fix(server): guard _send_upstream_error body read` — server.py
- **4**: `fix(logging): improve crash visibility without --verbose` — server.py, logging.py
- **5**: `feat(deps): add urllib3 and configure PoolManager` — pyproject.toml, server.py
- **6**: `feat(server): replace urlopen with urllib3 PoolManager` — server.py
- **7**: `feat(server): add configurable SSE read timeout` — server.py, config.py
- **8**: `feat(server): bounded thread pool for HTTPServer` — server.py
- **9**: `test: resilience tests Wave 2` — tests/test_resilience.py
- **10**: `feat(tunnel): ngrok health check and auto-reconnect` — tunnel.py, server.py
- **11**: `feat(server): SIGTERM handler and graceful shutdown` — server.py
- **12**: `test: resilience tests Wave 3` — tests/test_resilience.py
- **13**: `test: 2-hour soak test script` — tests/test_soak.py

---

## Success Criteria

### Verification Commands
```bash
# Unit tests
python -m unittest discover -s tests

# Fault injection tests
python -m unittest tests.test_resilience

# Manual QA: start proxy
uv run python -m deepseek_cursor_proxy.server --no-ngrok --port 9000

# Test normal request
curl -X POST http://127.0.0.1:9000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer sk-test" \
  -d '{"model":"deepseek-v4-pro","messages":[{"role":"user","content":"hi"}],"stream":false}'
# Expected: 200, valid JSON response

# Test client disconnect during streaming (simulate)
curl -X POST http://127.0.0.1:9000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer sk-test" \
  -d '{"model":"deepseek-v4-pro","messages":[{"role":"user","content":"hi"}],"stream":true}' \
  --max-time 1 &
# Expected: proxy logs "client disconnected", continues serving
```

### Final Checklist
- [ ] All 6 unguarded touch points now guarded with try/except
- [ ] All error paths log at WARNING level (visible without --verbose)
- [ ] `urllib3` replaces all `urlopen` calls
- [ ] `--stream-read-timeout` CLI flag present and working
- [ ] Server uses bounded thread pool (no unlimited thread growth)
- [ ] Ngrok tunnel auto-restarts on failure
- [ ] SIGTERM triggers graceful shutdown with partial reasoning flush
- [ ] All existing unit tests pass
- [ ] New resilience tests all pass
- [ ] No API response format changes
