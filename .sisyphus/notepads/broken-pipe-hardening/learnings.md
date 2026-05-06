# Learnings — broken-pipe-hardening QA

## QA Session: 2026-05-05

### Step 7 (oversized body) note
- Default max_request_body_bytes = 20MB (20971520)
- The test body `{'x'*1000000: 'y'}` produces only ~1MB, well under the limit
- Auth check (line 174) precedes body size check (line 188-190) — correct design: reject unauthorized before processing body
- Body size >20MB correctly yields 413 with JSON error

### Step 9 (resilience tests)
- All 19 tests pass in 0.009s
- Covers: NgrokHealthCheck, PoolRequestTimeout, ShutdownSignal, UpstreamPool

### Step 10 (soak test)
- 5s duration, concurrency=3: 25 total, 12 completed, 13 cancelled, 0 errors, 0 crashes
- RESULT: PASS

## cursor-compat-improvements QA Results

### Startup issues
- `uv run python -m deepseek_cursor_proxy.server` blocks the bash shell; must use explicit backgrounding script
- `setsid` + `disown` approach didn't work; simple `&` inside a script is reliable
- server SIGTERM handling is clean: gracefully drains requests, closes pool, prunes store

### Feature observations
- **CORS**: Disabled by default (`config.cors = False`). Must start with `--cors` to enable.
- **x-request-id**: Present on GET/OPTIONS/POST but NOT on HEAD (returns 501). Pattern: `dcp-<24 hex chars>`.
- **Model timestamps**: Stable across calls - hardcoded `MODEL_CREATED_TIMESTAMPS` dict.
- **/v1/health**: Same as /healthz; maps to same handler via path set `{"/healthz", "/v1/healthz", "/health", "/v1/health"}`.
- **/v1/embeddings**: Returns empty data gracefully when upstream is unreachable (200 with empty list).
- **Error format**: Consistent `{"error": {"message": ..., "type": ..., "code": ..., "param": null}}`.

### Test results
- Resilience: 26/26 pass (~0.02s)
- Full suite: 119/119 pass (~15s), 1 skipped
