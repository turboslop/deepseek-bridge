## Summary

This PR adds three major areas of improvement to the deepseek-cursor-proxy:

### Connection Resilience
- **urllib3 connection pooling** replacing one-shot `urlopen` calls — connection reuse + keep-alive prevents socket exhaustion during extended use
- **Bounded thread pool** (`--max-thread-pool`, default 20) replaces unlimited `ThreadingHTTPServer` threads — prevents thread pileup on long-running streaming connections
- **SSE read timeout** (`--stream-read-timeout`, default 180s) — kills hung upstream connections instead of leaking threads forever
- **Ngrok tunnel health check** (`--ngrok-health-check-interval`, default 30s) — auto-detects dead tunnel and restarts with new URL
- **SIGTERM graceful shutdown** — active requests drain, thread pool flushes, reasoning cache is stored before exit
- **6 unguarded socket touch points now guarded** — `_read_json_body`, `read_response_body`, `_proxy_regular_response`, `_send_upstream_error`, plus `close_connection=True` on all write failure paths

### OpenAI API Compliance (Cursor Compatibility)
- **`system_fingerprint`** injected into every streaming chunk, non-streaming response, recovery notice, and flush chunk
- **`x-request-id` UUID header** on every HTTP response (all methods: GET/POST/OPTIONS)
- **OpenAI-standard error format**: `{"error": {"message", "type", "code", "param": null}}` — proper `authentication_error`, `invalid_request_error`, `server_error` types; upstream failures use 500/504 instead of 502
- **CORS enabled by default** (`--no-cors` flag to disable)
- **Guaranteed usage chunk** in streaming when `include_usage: true` — synthesizes `choices: []` usage chunk before `[DONE]` if upstream doesn't provide one
- **`/v1/embeddings` endpoint** with graceful fallback — Cursor @Codebase search won't error out
- **`/v1/health` endpoint** with `uptime_seconds`
- **`/v1/completions` legacy endpoint** — auto-converts `prompt` to `messages` format
- **Multimodal content arrays preserved** — no longer flattened to text
- **Stable model timestamps** in `/v1/models`

### Logging
- **`--log-dir <path>`** — each launch creates a timestamped log file; auto-purges old files (keeps last 5)
- **All errors logged at WARNING level** — visible without `--verbose`
- **Heartbeat counter** every 500 requests
- **Pool utilization logging** every 100 requests (active threads, queue depth)
- **`sys.excepthook`** catches unhandled exceptions at CRITICAL level

### Tests
- **26 resilience tests** covering pool initialization, streaming timeouts, thread pool lifecycle, ngrok health check, shutdown signals, error format validation, and request ID generation
- **2-hour soak test script** (`tests/test_soak.py`) with concurrent workers, random mid-request cancellations, and PASS/FAIL reporting
- **119 total tests passing** (1 skipped — live DeepSeek API key test)

### Files Changed
| File | Description |
|------|-------------|
| `src/deepseek_cursor_proxy/server.py` | Core changes: urllib3 pool, thread pool, SSE timeout, error format, fingerprint, x-request-id, usage chunk, embeddings, health, completions, graceful shutdown, logging |
| `src/deepseek_cursor_proxy/config.py` | New config fields: max_pool_connections, max_keepalive, max_thread_pool, stream_read_timeout, ngrok_health_check_interval, log_dir, DEFAULT_CORS=True |
| `src/deepseek_cursor_proxy/tunnel.py` | Ngrok health check + auto-reconnect |
| `src/deepseek_cursor_proxy/logging.py` | File handler with timestamped per-launch files, auto-purge, excepthook integration |
| `src/deepseek_cursor_proxy/streaming.py` | system_fingerprint in flush chunks |
| `src/deepseek_cursor_proxy/transform.py` | system_fingerprint in non-streaming, multimodal content preservation |
| `tests/test_resilience.py` | 26 fault-injection resilience tests |
| `tests/test_soak.py` | Standalone soak test script |
| `pyproject.toml` | urllib3 dependency |
| `README.md` | Updated with all new features and CLI reference table |
