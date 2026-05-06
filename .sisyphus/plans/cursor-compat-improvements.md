# Cursor Compatibility & OpenAI Compliance Improvements

## TL;DR

> **Quick Summary**: Make the deepseek-cursor-proxy perfectly compatible with Cursor IDE by fixing OpenAI API compliance gaps, adding missing endpoints, improving error handling, and resolving known technical debt. Addresses sub-agent routing issues, response format mismatches, and missing features that cause Cursor to show errors.
>
> **Deliverables**:
> - Add `system_fingerprint` to every response (streaming and non-streaming)
> - Add `x-request-id` header to all responses
> - Fix error format to OpenAI standard: `{"error": {"message", "type", "code", "param"}}`
> - Enable CORS by default
> - Guarantee usage chunk in streaming responses
> - Add `/v1/embeddings` endpoint (for Cursor @Codebase search)
> - Add `/v1/health` endpoint with server status
> - Preserve multimodal content arrays (don't drop images)
> - Fix `close_connection = True` on all write failure paths
> - Add pool utilization logging
> - Add `/v1/completions` legacy endpoint alias
> - Add remaining resilience tests
>
> **Estimated Effort**: Large
> **Parallel Execution**: YES — 3 waves + 1 final verification
> **Critical Path**: T1 → T5 → T6 → Final

---

## Context

### Original Request
User reports that Cursor sometimes shows errors when using the proxy with sub-agents. Sub-agents appear to use "pro" model instead of the configured model, and when they finish, chat stops with an error in Cursor. User wants full Cursor compatibility.

### Research Findings
**Sub-Agent Issue (Cursor Bug)**:
- Cursor's sub-agents do NOT inherit custom OpenAI base URL settings from the main agent (confirmed by multiple Cursor forum threads: `forum.cursor.com/t/sub-agents-are-not-using-custom-openai-base-urls/152574`)
- Sub-agents fall back to Cursor's built-in "pro" model routing
- When sub-agent finishes, the response routing mismatch causes an error in the main chat
- **This is primarily a Cursor bug** that proxy cannot fully fix, but we can (a) ensure PERFECT OpenAI response format so when sub-agents DO route through proxy it works, and (b) make the `/v1/models` endpoint match what Cursor expects for model whitelisting

**OpenAI API Compliance Gaps Found**:
1. `system_fingerprint` — NEVER emitted in any response (required by OpenAI spec)
2. `x-request-id` — NEVER set on any response header
3. Error format — Missing `type`, `code`, `param` in most error responses
4. CORS — Disabled by default (OpenAI always enables)
5. Usage in streaming — Not guaranteed (only upstream pass-through)
6. `/v1/embeddings` endpoint — Missing (Cursor uses for @Codebase)
7. Multimodal content — Flattened to text, images dropped
8. `/v1/completions` — Missing (legacy fallback)
9. `/v1/models` `created` — Dynamic timestamp (not stable)

**Known Gaps from Previous Hardening Plan**:
- `close_connection = True` not set on non-streaming write failures
- Pool utilization logging not implemented
- Wave 3 tests at 8 (need 9+)

---

## Work Objectives

### Core Objective
Make the proxy indistinguishable from OpenAI's API to Cursor IDE, fixing all compliance gaps and technical debt.

### Concrete Deliverables
- Every response includes `system_fingerprint`, `x-request-id`, proper error format
- CORS enabled by default
- `/v1/embeddings`, `/v1/health`, `/v1/completions` endpoints added
- Multimodal content preserved
- All known hardening gaps fixed

### Definition of Done
- [ ] `python -m unittest discover -s tests` — ALL tests pass
- [ ] curl all endpoints, verify response format matches OpenAI spec
- [ ] 12+ resilience tests in test_resilience.py

### Must Have
- `system_fingerprint` in every SSE chunk and non-streaming response
- `x-request-id` UUID header on every response
- All error responses include `type`, `code` (if applicable), `param`
- CORS enabled without `--cors` flag
- Usage chunk present in streaming when `include_usage: true`
- `/v1/embeddings` returns valid response (not 404)
- Multimodal content arrays are NOT flattened to text

### Must NOT Have (Guardrails)
- No changes to reasoning_content caching logic
- No changes to StreamAccumulator or CursorReasoningDisplayAdapter
- No removal of existing error handling
- No breaking changes to config file format
- `.sisyphus/` files never staged in git commits

---

## Verification Strategy

> **ZERO HUMAN INTERVENTION** — ALL verification is agent-executed.

### Test Decision
- **Infrastructure exists**: YES
- **Automated tests**: YES (Tests after implementation)
- **Framework**: Python `unittest`
- **Tests after**: Each implementation task includes verifying with curl + running test suite

### QA Policy
Every task MUST include agent-executed QA scenarios. Evidence saved to `.sisyphus/evidence/task-{N}-{scenario}.{ext}`.

---

## Execution Strategy

### Parallel Execution Waves

```
Wave 1 (Response Compliance — parallel, can start immediately):
├── T1: Add system_fingerprint to all responses
├── T2: Add x-request-id header to responses
├── T3: Fix error format (type, code, param in all errors)
├── T4: Enable CORS by default
└── T5: Guarantee usage chunk in streaming

Wave 2 (Missing Endpoints + Content — after Wave 1):
├── T6: Add /v1/embeddings endpoint
├── T7: Add /v1/health endpoint
├── T8: Preserve multimodal content arrays
└── T9: Add /v1/completions endpoint alias

Wave 3 (Fix Known Gaps + Polish — after Wave 2):
├── T10: Set close_connection=True on all write fails
├── T11: Pool utilization logging
├── T12: Add remaining resilience tests
└── T13: Stable created timestamps in /v1/models

Wave FINAL (After ALL — 4 parallel reviews):
├── F1: Plan compliance audit (oracle)
├── F2: Code quality review (unspecified-high)
├── F3: Real manual QA (unspecified-high)
└── F4: Scope fidelity check (deep)
```

### Dependency Matrix
- **T1-T5**: None → Wave 2
- **T6-T9**: Wave 1 → Wave 3
- **T10-T13**: Wave 2 → Final
- **T6, T9**: Independent (new endpoints, no deps)

---

## TODOs

> Implementation + Test = ONE Task. Never separate.
> EVERY task MUST include: Recommended Agent Profile + Parallelization info + QA Scenarios.

- [x] 1. Add `system_fingerprint` to all responses (streaming + non-streaming)

  **What to do**:
  - In `server.py`, in `_rewrite_sse_line()` at ~line 931-965 (where chunks are re-serialized), add `chunk["system_fingerprint"] = "fp_deepseek_cursor_proxy"` before serialization
  - In `server.py`, in `rewrite_response_body()` path for non-streaming responses, add `response_payload["system_fingerprint"] = "fp_deepseek_cursor_proxy"` after the model rewrite at ~line 959
  - In `streaming.py`, in `CursorReasoningDisplayAdapter.flush_chunk()` at ~line 280-287, add `"system_fingerprint": "fp_deepseek_cursor_proxy"` to the chunk dict
  - Also add it to `recovery_notice_chunk()` in server.py at ~line 1330
  - Make the fingerprint value a module constant: `SYSTEM_FINGERPRINT = "fp_deepseek_cursor_proxy"` near the top of server.py
  - This helps Cursor properly identify the model config fingerprint

  **Must NOT do**:
  - Do NOT change upstream's `system_fingerprint` if it already has one (check before overriding)
  - Do NOT modify the SSE line format or existing fields

  **Parallelization**: YES — Wave 1 (with T2, T3, T4, T5)
  **Blocked By**: None

  **References**:
  - `server.py:880-966` - `_rewrite_sse_line()` — where streaming chunks are re-serialized
  - `server.py:930-962` - `rewrite_response_body()` — where non-streaming is rewritten
  - `streaming.py:280-287` - `flush_chunk()` — where closing chunks are built
  - `server.py:1330-1340` - `recovery_notice_chunk()` — where recovery notice chunks are built
  - OpenAI spec: `system_fingerprint` field in ChatCompletionChunk

  **Acceptance Criteria**:
  - [ ] Non-streaming response includes `system_fingerprint` field
  - [ ] Each streaming SSE chunk includes `system_fingerprint` field
  - [ ] Flush chunk (closing thinking block) includes `system_fingerprint`
  - [ ] Recovery notice chunk includes `system_fingerprint`
  - [ ] All existing tests pass

  **Commit**: YES
  - Message: `feat(server): add system_fingerprint to all responses`
  - Files: `src/deepseek_cursor_proxy/server.py`, `src/deepseek_cursor_proxy/streaming.py`

- [x] 2. Add `x-request-id` header to all responses

  **What to do**:
  - In `server.py`, add a `_generate_request_id()` helper function that returns a unique ID:
    ```python
    import uuid
    def _generate_request_id() -> str:
        return f"dcp-{uuid.uuid4().hex[:24]}"
    ```
  - Store the request ID on the handler at start of each request: `self._request_id = _generate_request_id()`
  - In `_send_response_headers()` at ~line 510, add `self.send_header("x-request-id", self._request_id)` BEFORE `self.end_headers()`
  - Also add it to `do_OPTIONS` and `do_GET` so healthz/models endpoints also get it
  - This helps Cursor track individual requests across the proxy

  **Must NOT do**:
  - Do NOT change the request ID mid-stream (same ID for entire request/response cycle)
  - Do NOT remove any existing headers

  **Parallelization**: YES — Wave 1 (with T1, T3, T4, T5)
  **Blocked By**: None

  **References**:
  - `server.py:510-520` - `_send_response_headers()` — where headers are sent
  - `server.py:500-510` - `_send_json()` — where JSON is sent
  - `server.py:145-168` - `do_POST()` — where request handling starts

  **Acceptance Criteria**:
  - [ ] `x-request-id` header present on all responses (200, 400, 401, 404, etc.)
  - [ ] Same ID throughout a single streaming session
  - [ ] Different requests get different IDs
  - [ ] OPTIONS and GET responses include `x-request-id`
  - [ ] All existing tests pass

  **Commit**: YES
  - Message: `feat(server): add x-request-id header to responses`
  - Files: `src/deepseek_cursor_proxy/server.py`

- [x] 3. Fix error format to OpenAI standard (`type`, `code`, `param`)

  **What to do**:
  - In `server.py`, all error responses need to include `type`, `code`, and `param` fields:
    - Auth failure (401): `type="authentication_error"`, `code="invalid_api_key"`, `param=null`
    - Missing/invalid body (400): `type="invalid_request_error"`, `code="invalid_request_error"`, `param=null`
    - Body too large (413): `type="invalid_request_error"`, `code="request_too_large"`, `param=null`
    - Unsupported path (404): `type="invalid_request_error"`, `code="endpoint_not_found"`, `param=null`
    - Upstream failure (change from 502 → 500): `type="server_error"`, `code="upstream_failure"`, `param=null`
    - Upstream timeout (change from 502 → 504): `type="server_error"`, `code="upstream_timeout"`, `param=null`
    - Response read failure (502 → 500): `type="server_error"`, `code="response_read_failed"`, `param=null`
  - Update the 409 missing-reasoning error to include `param: null`
  - Use consistent error format: `{"error": {"message": str, "type": str, "code": str | null, "param": null}}`
  - This helps Cursor correctly interpret errors instead of showing generic "Connection Error"

  **Must NOT do**:
  - Do NOT change error format for direct upstream passthrough errors (from DeepSeek API)
  - Do NOT change HTTP status for 413 (correct per OpenAI)

  **Parallelization**: YES — Wave 1 (with T1, T2, T4, T5)
  **Blocked By**: None

  **References**:
  - `server.py:168-184` - Auth error (change format)
  - `server.py:190-205` - Body error (change format)
  - `server.py:320-355` - Upstream error (change status+format)
  - `server.py:680-700` - Response read failure (change status+format)

  **Acceptance Criteria**:
  - [ ] Auth error returns `{"error": {"message": ..., "type": "authentication_error", "code": "invalid_api_key", "param": null}}`
  - [ ] Upstream failure returns 500 (not 502) with `type: "server_error"`
  - [ ] Upstream timeout returns 504 with `type: "server_error"`
  - [ ] Unsupported path returns 404 with `type: "invalid_request_error"`
  - [ ] All existing tests still pass (check test assertions on error responses)
  - [ ] Run fix on test files if assertions check for old error format

  **Commit**: YES
  - Message: `fix(server): normalize error format to OpenAI standard`
  - Files: `src/deepseek_cursor_proxy/server.py`

- [x] 4. Enable CORS by default

  **What to do**:
  - In `config.py`, change `DEFAULT_CORS = False` to `DEFAULT_CORS = True` (~line 30)
  - This makes `--cors` flag unnecessary — CORS headers are sent on every response by default
  - The `--no-cors` flag (BooleanOptionalAction) still allows disabling if needed
  - OpenAI always sends CORS headers — this makes the proxy behave consistently

  **Must NOT do**:
  - Do NOT remove the `--no-cors` CLI flag
  - Do NOT change CORS header values

  **Parallelization**: YES — Wave 1 (with T1, T2, T3, T5)
  **Blocked By**: None

  **References**:
  - `config.py:30` - `DEFAULT_CORS = False` — change to True
  - `server.py:464-474` - `_send_cors_headers()` method
  - `server.py:25` - `DEFAULT_NGROK = True` — same pattern for defaults

  **Acceptance Criteria**:
  - [ ] `curl -s -D- http://127.0.0.1:9000/healthz -X OPTIONS -H "Origin: http://example.com" -H "Access-Control-Request-Method: GET" 2>&1 | grep -i "access-control"` returns CORS headers
  - [ ] `--no-cors` flag still disables CORS
  - [ ] All existing tests pass

  **Commit**: YES
  - Message: `fix(config): enable CORS by default`
  - Files: `src/deepseek_cursor_proxy/config.py`

- [x] 5. Guarantee usage chunk in streaming responses when `include_usage: true`

  **What to do**:
  - In `server.py`, `_proxy_streaming_response()`, track whether a usage chunk was received from upstream
  - After the streaming loop completes (before returning), check if:
    - `stream_options.include_usage` was `true` in the request, AND
    - No usage chunk was received from upstream
  - If both conditions met, synthesize a usage chunk:
    ```python
    {
        "id": last_chunk_id or "chatcmpl-synthesized-usage",
        "object": "chat.completion.chunk",
        "created": created_timestamp,
        "model": original_model,
        "system_fingerprint": SYSTEM_FINGERPRINT,
        "choices": [],  # Empty array per OpenAI spec
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    }
    ```
  - Pass `include_usage` flag from `do_POST` through `_proxy_streaming_response`
  - Send the synthesized chunk via `self._write_to_client(sse_data(usage_chunk), ...)` before `[DONE]`
  - This ensures Cursor always gets a usage chunk, preventing errors when upstream doesn't send one

  **Must NOT do**:
  - Do NOT synthesize usage if upstream already provided it (avoid duplicate)
  - Do NOT change the `[DONE]` terminator or SSE format
  - Do NOT add usage to non-streaming responses (they already have it)

  **Parallelization**: YES — Wave 1 (with T1, T2, T3, T4)
  **Blocked By**: None

  **References**:
  - `server.py:750-878` - `_proxy_streaming_response()` — main streaming handler
  - `server.py:880-966` - `_rewrite_sse_line()` — per-chunk processing
  - OpenAI spec: when `include_usage: true`, final chunk before `[DONE]` has `choices: []` and `usage: {...}`

  **Acceptance Criteria**:
  - [ ] When upstream sends usage, it's forwarded unchanged (no duplicate)
  - [ ] When upstream does NOT send usage but `include_usage: true`, synthesized usage chunk emitted before `[DONE]`
  - [ ] Synthesized chunk has `choices: []` (empty array, per OpenAI spec)
  - [ ] All existing tests pass

  **Commit**: YES
  - Message: `feat(server): guarantee usage chunk in streaming responses`
  - Files: `src/deepseek_cursor_proxy/server.py`

- [x] 6. Add `/v1/embeddings` endpoint for Cursor @Codebase support

  **What to do**:
  - In `server.py`, in `do_POST()`, add support for request paths `/embeddings` and `/v1/embeddings`
  - Detect embeddings requests early (before the chat completions check at ~line 158):
    ```python
    if request_path in {"/embeddings", "/v1/embeddings"}:
        if self.config.verbose:
            LOG.info("incoming embeddings request from %s", self.client_address[0])
        self._handle_embeddings_request()
        return
    ```
  - Create `_handle_embeddings_request()` method:
    - Read JSON body (same as chat completions)
    - Extract `input` (string or array of strings), `model` (string)
    - Forward to upstream: `{self.config.upstream_base_url}/embeddings` with same headers
    - For DeepSeek, the embeddings endpoint may not exist — return a graceful error if upstream fails
    - Return response in OpenAI format: `{"object": "list", "data": [{"object": "embedding", "index": 0, "embedding": [...]}], "model": ..., "usage": {...}}`
  - If DeepSeek doesn't support embeddings, implement a simple fallback: return 501 with proper error format `{"error": {"message": "Embeddings not supported by upstream", "type": "invalid_request_error", "code": "endpoint_not_supported", "param": null}}`
  - This prevents Cursor from showing errors when it tries @Codebase search

  **Must NOT do**:
  - Do NOT break existing chat completions endpoint
  - Do NOT add heavy dependencies (use same urllib3 pool)

  **Parallelization**: YES — Wave 2 (with T7, T8, T9)
  **Blocked By**: Wave 1 completion

  **References**:
  - `server.py:155-168` - Current path checking pattern for do_POST
  - OpenAI embeddings API: POST `/v1/embeddings` with `input` and `model`
  - DeepSeek API docs — check if embeddings endpoint exists

  **Acceptance Criteria**:
  - [ ] POST `/v1/embeddings` returns valid JSON (not 404)
  - [ ] Response has `object: "list"` and `data: [{object: "embedding", ...}]` format
  - [ ] Chat completions still work on `/v1/chat/completions`
  - [ ] All existing tests pass

  **Commit**: YES
  - Message: `feat(server): add /v1/embeddings endpoint for Cursor @Codebase`
  - Files: `src/deepseek_cursor_proxy/server.py`

- [x] 7. Improve `/v1/healthz` and add `/v1/health` endpoint

  **What to do**:
  - In `server.py`, in `do_GET()` at ~line 133, add `/health` and `/v1/health` to the health check paths:
    ```python
    if request_path in {"/healthz", "/v1/healthz", "/health", "/v1/health"}:
    ```
  - Enhance the health response to include more useful info:
    ```python
    {"ok": true, "server": "deepseek-cursor-proxy", "version": "0.1.1", "uptime_seconds": ...}
    ```
  - Track server start time in `DeepSeekProxyServer` class:
    ```python
    start_time: float = field(default_factory=time.monotonic)
    ```
  - Include the x-request-id in health response headers
  - OpenAI doesn't have a health endpoint, but many tools (including Cursor's verification) check for `/v1/health`

  **Must NOT do**:
  - Do NOT expose sensitive info in health check (API keys, tokens, etc.)
  - Do NOT break existing `/healthz` behaviour

  **Parallelization**: YES — Wave 2 (with T6, T8, T9)
  **Blocked By**: Wave 1 completion

  **References**:
  - `server.py:133-143` - Current do_GET with healthz check
  - `server.py:49-53` - DeepSeekProxyServer dataclass

  **Acceptance Criteria**:
  - [ ] GET `/v1/health` returns `{"ok": true}`
  - [ ] GET `/health` also works
  - [ ] Health response includes `uptime_seconds`
  - [ ] Existing `/healthz` still works
  - [ ] All existing tests pass

  **Commit**: YES
  - Message: `feat(server): add /v1/health endpoint with server info`
  - Files: `src/deepseek_cursor_proxy/server.py`

- [x] 8. Preserve multimodal content arrays (don't flatten to text)

  **What to do**:
  - In `transform.py`, modify `extract_text_content()` at ~line 133-156:
    - Currently it flattens content arrays to text and replaces non-text parts with `"[type omitted by DeepSeek text proxy]"`
    - Change it to PRESERVE content arrays when the upstream supports them
    - The function should pass through content as-is if it's already a list/array type
    - Only flatten for compatibility when the upstream doesn't support the content type
  - Update the function signature or add a flag to control flattening:
    ```python
    def extract_text_content(content: Any, preserve_multimodal: bool = False) -> str | None:
        if preserve_multimodal and isinstance(content, list):
            return content  # Pass through unchanged
        # ... existing flattening logic
    ```
  - In `normalize_message()`, pass `preserve_multimodal=True` when the upstream model supports it

  **Must NOT do**:
  - Do NOT break existing text-only content handling
  - Do NOT change function signature for callers that depend on flattened output

  **Parallelization**: YES — Wave 2 (with T6, T7, T9)
  **Blocked By**: Wave 1 completion

  **References**:
  - `transform.py:133-156` - `extract_text_content()` — current flattening logic
  - `transform.py:237-343` - `normalize_message()` — calls extract_text_content
  - DeepSeek API docs — check if multimodal content (images) is supported

  **Acceptance Criteria**:
  - [ ] Content arrays with text parts are preserved as arrays (not flattened)
  - [ ] Text-only strings still work as before
  - [ ] All existing tests pass

  **Commit**: YES
  - Message: `fix(transform): preserve multimodal content arrays`
  - Files: `src/deepseek_cursor_proxy/transform.py`

- [x] 9. Add `/v1/completions` legacy endpoint alias

  **What to do**:
  - In `server.py`, `do_POST()` at ~line 155-158, add `/completions` and `/v1/completions` to the path check:
    ```python
    if request_path not in {
        "/chat/completions", "/v1/chat/completions",
        "/completions", "/v1/completions",
        "/embeddings", "/v1/embeddings",
    }:
    ```
  - For completions requests, convert the old-style `prompt` parameter to a `messages` array:
    ```python
    if request_path in {"/completions", "/v1/completions"}:
        if "prompt" in payload and "messages" not in payload:
            payload["messages"] = [{"role": "user", "content": payload.pop("prompt")}]
    ```
  - Then process through the same chat completions pipeline
  - This prevents clients that send legacy completion requests from getting 404 errors

  **Must NOT do**:
  - Do NOT add a separate completions implementation (just alias to chat)
  - Do NOT break chat completions path

  **Parallelization**: YES — Wave 2 (with T6, T7, T8)
  **Blocked By**: Wave 1 completion

  **References**:
  - `server.py:155-168` - Current POST path checks
  - OpenAI legacy `/v1/completions` API

  **Acceptance Criteria**:
  - [ ] POST `/v1/completions` with `prompt` param works (aliases to chat)
  - [ ] POST `/v1/completions` with `messages` param works directly
  - [ ] Chat completions still work on `/v1/chat/completions`
  - [ ] All existing tests pass

  **Commit**: YES
  - Message: `feat(server): add /v1/completions endpoint alias`
  - Files: `src/deepseek_cursor_proxy/server.py`

- [x] 10. Set `close_connection = True` on all write failure paths

  **What to do**:
  - In `server.py`, audit all paths where `_write_to_client()` returns False or `_send_response_headers()` returns False
  - Ensure `self.close_connection = True` is set in EVERY write-failure path
  - The streaming path already does this (line 787) — verify and extend to:
    - `_send_response_headers()` at ~line 517: set `self.close_connection = True` before returning False
    - `_write_to_client()` at ~line 533: set `self.close_connection = True` before returning False  
    - `_proxy_regular_response()` at ~line 746: confirm close_connection is already True
    - `_send_upstream_error()` at ~line 668: set `self.close_connection = True`
    - `_send_json()` at ~line 500: set `self.close_connection = True` on write failure

  **Must NOT do**:
  - Do NOT change the streaming path (already correct)
  - Do NOT remove existing error handling

  **Parallelization**: YES — Wave 3 (with T11, T12, T13)
  **Blocked By**: Wave 2 completion

  **References**:
  - `server.py:512-517` - `_send_response_headers()` return False path
  - `server.py:531-533` - `_write_to_client()` return False path
  - `server.py:746` - `_proxy_regular_response()` write failure
  - `server.py:668` - `_send_upstream_error()` write failure
  - `server.py:787` - already correct in streaming

  **Acceptance Criteria**:
  - [ ] `_write_to_client()` sets `self.close_connection = True` before returning False
  - [ ] `_send_response_headers()` sets `self.close_connection = True` before returning False
  - [ ] `_send_upstream_error()` sets `self.close_connection = True` on write failure
  - [ ] All existing tests pass

  **Commit**: YES
  - Message: `fix(server): set close_connection=True on all write failures`
  - Files: `src/deepseek_cursor_proxy/server.py`

- [x] 11. Add pool utilization logging

  **What to do**:
  - In `server.py`, in `BoundedThreadPoolHTTPServer`, add periodic pool utilization logging
  - Add a request counter to the server (similar to the heartbeat counter from Wave 1 of hardening)
  - Every 100 requests, log:
    ```python
    LOG.info(
        "thread pool: max_workers=%s active=%s queue=%s",
        self.executor._max_workers,
        len(self.executor._threads),
        self.executor._work_queue.qsize() if hasattr(self.executor, '_work_queue') else '?',
    )
    ```

  **Must NOT do**:
  - Do NOT introspect private attributes in a way that breaks on different Python versions
  - Do NOT log sensitive information

  **Parallelization**: YES — Wave 3 (with T10, T12, T13)
  **Blocked By**: Wave 2 completion

  **References**:
  - `server.py:63-95` - BoundedThreadPoolHTTPServer class
  - Python `concurrent.futures.ThreadPoolExecutor` API

  **Acceptance Criteria**:
  - [ ] Pool utilization logged every 100 requests
  - [ ] Log includes max_workers and active count
  - [ ] All existing tests pass

  **Commit**: YES
  - Message: `feat(server): add pool utilization logging`
  - Files: `src/deepseek_cursor_proxy/server.py`

- [x] 12. Add remaining resilience tests

  **What to do**:
  - Add tests to `tests/test_resilience.py` to bring total to 12+:
    - `test_system_fingerprint_in_streaming` — Verify system_fingerprint is in SSE chunks
    - `test_system_fingerprint_in_non_streaming` — Verify system_fingerprint in response
    - `test_x_request_id_on_all_responses` — Verify header present
    - `test_error_format_includes_type` — Verify error has `type` field
    - `test_error_format_includes_code` — Verify error has `code` field
    - `test_close_connection_set_on_write_failure` — Verify behavior
  - Use `unittest.mock` to simulate error conditions

  **Must NOT do**:
  - Do NOT remove existing resilience tests
  - Do NOT make real HTTP calls

  **Parallelization**: YES — Wave 3 (with T10, T11, T13)
  **Blocked By**: Wave 2 completion

  **References**:
  - `tests/test_resilience.py` — existing resilience test file

  **Acceptance Criteria**:
  - [ ] `python -m unittest tests.test_resilience -v` — 12+ tests pass
  - [ ] `python -m unittest discover -s tests` — all tests pass

  **Commit**: YES
  - Message: `test: add remaining resilience tests`
  - Files: `tests/test_resilience.py`

- [x] 13. Stable `created` timestamps in `/v1/models` endpoint

  **What to do**:
  - In `server.py`, `_send_models()` at ~line 539-559:
    - Currently uses `int(time.time())` which changes every call
    - Replace with fixed timestamps for each model:
    ```python
    MODEL_CREATED_TIMESTAMPS = {
        "deepseek-v4-pro": 1735689600,  # Jan 2025
        "deepseek-v4-flash": 1735689600,  # Jan 2025
    }
    created = MODEL_CREATED_TIMESTAMPS.get(model_id, 1735689600)
    ```
  - This prevents Cursor from thinking model metadata changed between calls

  **Must NOT do**:
  - Do NOT change the `/v1/models` response format
  - Do NOT remove the custom model from config

  **Parallelization**: YES — Wave 3 (with T10, T11, T12)
  **Blocked By**: Wave 2 completion

  **References**:
  - `server.py:539-559` - `_send_models()` method

  **Acceptance Criteria**:
  - [ ] `/v1/models` returns same `created` value on repeated calls
  - [ ] Different models can have different timestamps
  - [ ] All existing tests pass

  **Commit**: YES
  - Message: `fix(server): stable created timestamps in /v1/models`
  - Files: `src/deepseek_cursor_proxy/server.py`

---

## Final Verification Wave (MANDATORY)

- [x] F1. **Plan Compliance Audit** — `oracle`
- [x] F2. **Code Quality Review** — `unspecified-high`
- [x] F3. **Real Manual QA** — `unspecified-high`
- [x] F4. **Scope Fidelity Check** — `deep`

---

## Commit Strategy

- **T1**: `feat(server): add system_fingerprint to all responses`
- **T2**: `feat(server): add x-request-id header to responses`
- **T3**: `fix(server): normalize error format to OpenAI standard`
- **T4**: `fix(config): enable CORS by default`
- **T5**: `feat(server): guarantee usage chunk in streaming responses`
- **T6**: `feat(server): add /v1/embeddings endpoint`
- **T7**: `feat(server): add /v1/health endpoint with server info`
- **T8**: `fix(transform): preserve multimodal content arrays`
- **T9**: `feat(server): add /v1/completions endpoint alias`
- **T10**: `fix(server): set close_connection=True on all write failures`
- **T11**: `feat(server): add pool utilization logging`
- **T12**: `test: add remaining resilience tests`
- **T13**: `fix(server): stable created timestamps in /v1/models`

---

## Success Criteria

### Verification Commands
```bash
# Test system_fingerprint in non-streaming
curl -s -X POST http://127.0.0.1:9000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer sk-test" \
  -d '{"model":"deepseek-v4-pro","messages":[{"role":"user","content":"hi"}],"stream":false}' \
  | python3 -c "import sys,json; d=json.load(sys.stdin); assert 'system_fingerprint' in d, 'missing fingerprint'; print('OK:', d.get('system_fingerprint'))"

# Test x-request-id header
curl -sI -X POST http://127.0.0.1:9000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer sk-test" \
  -d '{"model":"deepseek-v4-pro","messages":[{"role":"user","content":"hi"}],"stream":false}' \
  2>&1 | grep -i x-request-id

# Test error format
curl -s http://127.0.0.1:9000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"test"}' \
  | python3 -m json.tool
```

### Final Checklist
- [ ] system_fingerprint present in all responses
- [ ] x-request-id header on all responses
- [ ] All errors include type, code, param
- [ ] CORS headers present without --cors
- [ ] /v1/embeddings returns 200 or proper error
- [ ] Multimodal content preserved
- [ ] close_connection on all write fails
- [ ] Pool utilization logged
- [ ] All resilience tests pass (12+)
- [ ] All existing tests pass
