# Cursor Compat + CLI UX + Ollama Copilot Support

## TL;DR

> **Quick Summary**: Overhaul the runtime CLI output, add GitHub Copilot/Ollama compatibility via 3 new endpoints, simplify proxy configuration, and update documentation.
>
> **Deliverables**:
> - Runtime CLI UX improvements (elapsed time, tokens/sec, compact mode)
> - Cleaner heartbeat combining pool + DB stats in one line
> - Ollama/Copilot mode: `/api/version`, `/api/tags`, `/api/show` endpoints on main port
> - Config simplification: remove dead code, auto-calc tunables, hide internal settings
> - README updated with Ollama usage, sub-agent docs, reasoning display
> - User test-drive + log analysis
> - Commit and push
>
> **Estimated Effort**: Large
> **Parallel Execution**: YES — 3 waves + Final
> **Critical Path**: T4a→T4b→T4c→T4d cascade + T5→T10 docs

---

## Context

### Original Request
Improve CLI UI/UX overall, research Cursor compatibility improvements, add GitHub Copilot/Ollama support, simplify config, update README, and analyze logs after test-drive.

### Research Findings
**CLI UX (from codebase exploration)**:
- Per-request output: 4 lines (┌ request, ├ context, ├ send (verbose), └ stats) + animated spinner
- No elapsed time or tokens/sec shown during normal operation
- Heartbeat: pool util at 100 req, DB stats at 500 req, heartbeat at 500 req — 3 separate lines
- Compact mode (`--compact`) would suppress per-request details for CI usage

**Cursor Sub-Agent Research**:
- CONFIRMED Cursor-side bug — sub-agents do NOT inherit custom base URL or API keys
- Proxy CANNOT fix this; document as known limitation
- Cursor's native reasoning UI (brain icon) is only for their owned models, NOT for BYOK

**Ollama/Copilot Research (from VS Code source)**:
- Copilot uses TWO protocols: Ollama-native (`/api/tags`, `/api/show`, `/api/version`) for model discovery, OpenAI-compatible (`/v1/chat/completions`) for inference
- Critical: `/api/show` MUST include `"tools"` in `capabilities` array for Agent Mode
- Version check requires >= `0.18.3`
- Our existing `/v1/chat/completions` pipeline needs NO changes for Copilot

**Config Simplification (from code audit)**:
- `max_keepalive` is DEAD CODE — accepted but never passed to urllib3.PoolManager
- `stream_read_timeout` can be auto-calc'd from `request_timeout` (e.g., `max(timeout * 0.6, 60)`)
- `reasoning_cache_max_rows` is redundant with age-based pruning (with vacuum fix + 30-day/7-day default, row count cap is never reached)
- `reasoning_cache_max_age_seconds` default 30 days → 7 days (practical retention)
- `ngrok_health_check_interval` → fixed 30s constant (rarely changed)
- `reasoning_content_path` → hide from default config template (internal detail)
- `max_pool_connections` → auto-calc from `max_thread_pool`

### Metis Review Gap Closures
- **T9 MERGED INTO T10**: `reasoning_content` IS already forwarded in SSE chunks. `--no-display-reasoning` already strips markdown. No new code needed — just docs + flag alias.
- **T8 SIMPLIFIED**: Serve Ollama endpoints on the main port under `/api/*`. No second server, no second tunnel, no port conflicts.
- **T4 SPLIT**: T4a (dead code removal), T4b (auto-calc defaults), T4c (template cleanup), T4d (default changes)
- **T12 EXIT CRITERIA**: 0 ERROR lines, <5 WARNING/min during normal use, all endpoints respond correctly

---

## Work Objectives

### Concrete Deliverables
- Per-request output shows elapsed time + tokens/sec
- `--compact` mode for 1-line-per-request CI usage
- Heartbeat merged into single line: "heartbeat: N req, pool [X/Y], db [Z MB N rows]"
- Ollama endpoints: `/api/version`, `/api/tags`, `/api/show` served on main port
- Dead code removed: `max_keepalive` parameter + CLI flag
- Auto-calculated: `stream_read_timeout` from `request_timeout`, `max_pool_connections` from `max_thread_pool`
- Config defaults changed: `reasoning_cache_max_age_seconds` → 7 days (604,800)
- `--help` output uses section groupings
- README updated with Ollama/Copilot, sub-agent limitation, reasoning docs
- User config.yaml simplified

### Must Have
- Per-request output includes elapsed time (ms) and tokens/sec in stats line
- GitHub Copilot can discover models and use Agent Mode via the proxy
- `max_keepalive` removed from code (no crash, no warning on stale YAML keys)
- All 130+ existing tests pass with new changes

### Must NOT Have (Guardrails)
- No ANSI colors, emojis, or escape codes in output
- No second HTTP server (Ollama endpoints on main port)
- No changes to `/v1/chat/completions` inference pipeline
- No removal of `reasoning_content` from SSE chunks
- No breaking changes to log format without `--compact` flag
- No new dependencies in `pyproject.toml`

---

## Verification Strategy

> **ZERO HUMAN INTERVENTION** — ALL verification is agent-executed.

### Test Decision
- **Infrastructure exists**: YES
- **Framework**: Python `unittest`
- **Tests after**: New tests for Ollama endpoints, config changes; manual QA for CLI output format

---

## Execution Strategy

```
Wave 1 (Low risk — cleanup + CLI):
├── T1: Per-request output with elapsed time + tokens/sec
├── T2: Merge heartbeat lines into compact single line
├── T3: --compact mode for CI (1 line per request)
├── T4a: Remove max_keepalive dead code (UpstreamPool, ProxyConfig, CLI)
├── T4b: Auto-calc stream_read_timeout + max_pool_connections
├── T4c: Clean up default config template (hide internal settings)
└── T4d: Change reasoning_cache_max_age_seconds default 30d → 7d

Wave 2 (Medium risk — Ollama/Copilot):
├── T5: Add GET /api/version (return "0.18.3")
├── T6: Add GET /api/tags (Ollama model list format)
├── T7: Add POST /api/show (model capabilities with "tools")
└── T8: Wire Ollama endpoints into do_GET/do_POST routers

Wave 3 (Docs + polish):
├── T10: Update README (Ollama/Copilot, sub-agent limit, reasoning)
├── T11: Update user config.yaml + add --no-markdown-reasoning alias
├── T12: User test-drive + log analysis
└── T13: Final test suite + commit + push

Final Wave (4 parallel reviews):
├── F1: Plan compliance audit (oracle)
├── F2: Code quality review (unspecified-high)
├── F3: Real manual QA (unspecified-high)
└── F4: Scope fidelity check (deep)
```

---

## TODOs

- [x] 1. Add elapsed time and tokens/sec to per-request stats output

  **What to do**:
  - In `server.py` `do_POST()`, track `started = time.monotonic()` (already exists at line 220)
  - Pass elapsed time through to `log_stats_summary()` from `do_POST()` where the upstream response completes
  - Modify `log_stats_summary()` at server.py:1460-1467 to include:
    ```python
    def log_stats_summary(usage: dict[str, Any] | None, elapsed_ms: int | None = None) -> None:
        tokens_per_sec = ""
        if elapsed_ms and usage:
            total_tokens = int_or_zero(usage.get("total_tokens"))
            if total_tokens and elapsed_ms > 0:
                tokens_per_sec = f" {total_tokens / (elapsed_ms / 1000):.1f} tok/s"
        LOG.info(
            "└ stats   prompt=%s output=%s reasoning=%s cache_hit=%s elapsed=%s%s",
            format_usage_count(usage, "prompt_tokens"),
            format_usage_count(usage, "completion_tokens"),
            format_count(reasoning_token_count(usage)),
            cache_hit_rate(usage),
            format_count(elapsed_ms) + "ms" if elapsed_ms else "?",
            tokens_per_sec,
        )
    ```
  - Pass `elapsed_ms` from `do_POST()`:
    ```python
    log_stats_summary(sent_response.usage, elapsed_ms=elapsed_ms(started))
    ```
  - Also pass elapsed through the non-streaming and streaming handlers
  - The `elapsed_ms()` helper already exists at server.py:1363

  **Must NOT do**:
  - Do NOT change the format of existing fields
  - Do NOT add new lines to per-request output (append to existing stats line only)
  - Do NOT break the `?` fallback when usage is None

  **Parallelization**: YES — Wave 1 (with T2, T3, T4a-d)
  **Blocked By**: None

  **References**:
  - `server.py:1363-1364` — `elapsed_ms()` helper
  - `server.py:1460-1467` — `log_stats_summary()` — modify signature and format
  - `server.py:544` — usage logging call site: `log_stats_summary(sent_response.usage)`
  - `server.py:1518-1525` — `format_count()` and `int_or_zero()` helpers

  **Acceptance Criteria**:
  - [ ] Non-streaming response stats output includes `elapsed=<N>ms` and `X.X tok/s`
  - [ ] Streaming response stats output includes same
  - [ ] When usage is None, elapsed shows but tok/s is omitted
  - [ ] When elapsed is 0, `0 tok/s` shown
  - [ ] All existing tests pass

  **Commit**: YES (with T2, T3)
  - Message: `feat(cli): add elapsed time and tokens/sec to per-request output`
  - Files: `src/deepseek_cursor_proxy/server.py`


- [x] 2. Merge heartbeat lines into compact single-line format

  **What to do**:
  - In `server.py` `do_POST()`, currently 3 separate log calls fire at 500 req intervals:
    ```python
    if self.server.request_count % 500 == 0:
        LOG.info("heartbeat: processed %s requests", ...)
    if self.server.request_count % 100 == 0:
        self.server._log_pool_utilization()
    if self.server.request_count % 500 == 0 and hasattr(...):
        self.server._log_db_stats()
    ```
  - Combine them into a single `_log_heartbeat()` method that fires every 100 requests:
    ```python
    def _log_heartbeat(self) -> None:
        parts = [f"heartbeat: req={format_count(self.request_count)}"]
        # Pool util
        try:
            parts.append(f"pool={len(self.executor._threads)}/{self.executor._max_workers}")
        except Exception:
            parts.append("pool=?")
        # DB stats
        try:
            store = self.reasoning_store
            if isinstance(store.reasoning_content_path, Path):
                size_mb = store.reasoning_content_path.stat().st_size / (1024 * 1024)
                row = store._conn.execute("SELECT COUNT(*) FROM reasoning_cache").fetchone()
                row_count = int(row[0]) if row else 0
                parts.append(f"db={size_mb:.0f}MB/{format_count(row_count)}rows")
        except Exception:
            parts.append("db=?")
        # Uptime
        uptime = int(time.monotonic() - self.start_time)
        parts.append(f"uptime={uptime // 60}m")
        LOG.info(" | ".join(parts))
    ```
  - Fire it every 100 requests: `if self.server.request_count % 100 == 0: self.server._log_heartbeat()`
  - Remove the separate `_log_pool_utilization()` and `_log_db_stats()` heartbeat triggers
  - Keep `_log_db_stats()` and `_log_pool_utilization()` as methods (they may be used elsewhere), just remove from the 100/500 req triggers

  **Must NOT do**:
  - Do NOT remove the `_log_pool_utilization()` and `_log_db_stats()` methods entirely (they're useful for debugging)
  - Do NOT increase log frequency (every 100 reqs is same as current pool util)
  - Do NOT include sensitive info in heartbeat

  **Parallelization**: YES — Wave 1
  **Blocked By**: None

  **References**:
  - `server.py:123-135` — `_log_pool_utilization()` existing method
  - `server.py:137-152` — `_log_db_stats()` existing method
  - `server.py:125-128` — BoundedThreadPoolHTTPServer start_time attribute
  - `server.py:211-219` — Current heartbeat triggers in do_POST

  **Acceptance Criteria**:
  - [ ] Single heartbeat line: `heartbeat: req=500 | pool=5/20 | db=74MB/1,086rows | uptime=30m`
  - [ ] Fires every 100 requests
  - [ ] Gracefully handles missing/moved DB file
  - [ ] `_log_pool_utilization()` and `_log_db_stats()` methods still callable
  - [ ] All existing tests pass

  **Commit**: YES (with T1, T3)
  - Files: `src/deepseek_cursor_proxy/server.py`


- [x] 3. Add `--compact` mode for 1-line-per-request output

  **What to do**:
  - Add `--compact` CLI flag (BooleanOptionalAction) to `build_arg_parser()`:
    ```python
    parser.add_argument(
        "--compact",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Compact 1-line-per-request output (for CI/script usage)",
    )
    ```
  - Add to `ProxyConfig`: `compact: bool = False`
  - In `server.py:main()`, apply: `config = replace(config, compact=args.compact if args.compact is not None else config.compact)`
  - In `do_POST()`, wrap the per-request summary lines with a check:
    ```python
    if not self.config.compact:
        log_cursor_request(payload, self.config)   # "┌ request ..."
        log_context_summary(prepared)              # "├ context ..."
    ```
  - Keep `log_stats_summary()` always (the "└ stats" line)
  - In compact mode, combine it all into one line:
    ```python
    if self.config.compact:
        model = str(payload.get("model") or config.upstream_model)
        LOG.info(
            "compact: %s | msg=%s | ctx=%s | prompt=%s out=%s reason=%s cache=%s | %s",
            model, ...)
    ```
  - Keep all WARNING/ERROR lines visible even in compact mode
  - The spinner is already disabled when `--verbose` is on; in compact mode, also disable spinner

  **Must NOT do**:
  - Do NOT suppress WARNING/ERROR lines in compact mode
  - Do NOT change the existing non-compact output format
  - Do NOT add compact as default (opt-in only)

  **Parallelization**: YES — Wave 1
  **Blocked By**: None

  **References**:
  - `server.py:1199-1360` — `build_arg_parser()` — add new flag
  - `config.py:194-218` — ProxyConfig dataclass — add compact field
  - `server.py:1420-1467` — log_cursor_request, log_context_summary, log_stats_summary
  - `server.py:303-314` — Where these log functions are called in do_POST

  **Acceptance Criteria**:
  - [ ] `--compact` flag accepted by CLI parser
  - [ ] With `--compact`, only 1 line per request (plus WARNING/ERROR lines)
  - [ ] Without `--compact`, existing 3-4 line output unchanged
  - [ ] All existing tests pass

  **Commit**: YES (with T1, T2)
  - Files: `src/deepseek_cursor_proxy/server.py`, `src/deepseek_cursor_proxy/config.py`


- [x] 4a. Remove `max_keepalive` dead code

  **What to do**:
  1. In `server.py`, `UpstreamPool.__init__()`: REMOVE the `max_keepalive` parameter (currently line 89):
     ```python
     # Before:
     def __init__(self, max_connections: int = 10, max_keepalive: int = 5):
     # After:
     def __init__(self, max_connections: int = 10):
     ```
  2. In `config.py`, `ProxyConfig`: Remove `max_keepalive` field (line 211) and its default `DEFAULT_MAX_KEEPALIVE = 5` (line 32)  
  3. In `config.py`, `ProxyConfig.from_file()`: Remove `max_keepalive=as_int(...)` loading block (~line 310-312)
  4. In `server.py`, `build_arg_parser()`: Remove `--max-keepalive` CLI argument (~line 1347-1349)
  5. In `server.py`, `main()`: Remove `if args.max_keepalive is not None:` update (~line 1674)
  6. In `server.py`, `main()`: Remove `config.max_keepalive` from pool construction (line 1701)
     ```python
     # Before:
     pool = UpstreamPool(max_connections=config.max_pool_connections, max_keepalive=config.max_keepalive)
     # After:
     pool = UpstreamPool(max_connections=config.max_pool_connections)
     ```
  7. Remove `DEFAULT_MAX_KEEPALIVE` constant from `config.py`

  **Must NOT do**:
  - Do NOT remove `--max-keepalive` from test files that reference it (update them instead)
  - Do NOT crash if user's config.yaml has `max_keepalive` (YAML loader silently ignores unknown keys)

  **Parallelization**: YES — Wave 1
  **Blocked By**: None

  **References**:
  - `src/deepseek_cursor_proxy/server.py:87-93` — `UpstreamPool.__init__`
  - `src/deepseek_cursor_proxy/config.py:32` — `DEFAULT_MAX_KEEPALIVE`
  - `src/deepseek_cursor_proxy/config.py:211` — `max_keepalive` field
  - `src/deepseek_cursor_proxy/config.py:310-312` — `max_keepalive` loading
  - `src/deepseek_cursor_proxy/server.py:1347-1349` — `--max-keepalive` CLI
  - `src/deepseek_cursor_proxy/server.py:1674` — `max_keepalive` update in main()
  - `src/deepseek_cursor_proxy/server.py:1701` — pool construction

  **Acceptance Criteria**:
  - [ ] `UpstreamPool.__init__` no longer accepts `max_keepalive`
  - [ ] `ProxyConfig` no longer has `max_keepalive` field
  - [ ] `--max-keepalive` CLI flag removed (not recognized, but YAML key silently ignored)
  - [ ] Proxy starts and runs normally
  - [ ] All existing tests pass (update any test that references max_keepalive)

  **Commit**: YES (with T4b, T4c, T4d)
  - Files: `src/deepseek_cursor_proxy/server.py`, `src/deepseek_cursor_proxy/config.py`
  - Pre-commit: `uv run python -m unittest tests.test_resilience -v`


- [x] 4b. Auto-calculate `stream_read_timeout` and `max_pool_connections`

  **What to do**:
  - In `config.py`, add auto-calculation helpers (do NOT add to ProxyConfig fields — they keep defaults):
    ```python
    def auto_calc_stream_timeout(request_timeout: float, explicit_value: Any = MISSING) -> float:
        """If stream_read_timeout not explicitly set, derive from request_timeout."""
        if explicit_value is not MISSING and explicit_value is not None:
            return as_float(explicit_value, DEFAULT_STREAM_READ_TIMEOUT)
        return max(as_float(explicit_value, DEFAULT_STREAM_READ_TIMEOUT) if explicit_value is not MISSING else request_timeout * 0.6, 60.0)
    ```
  - In `ProxyConfig.from_file()`, apply auto-calculation:
    ```python
    stream_read_timeout=auto_calc_stream_timeout(
        as_float(setting_value(settings, "request_timeout"), DEFAULT_REQUEST_TIMEOUT),
        setting_value(settings, "stream_read_timeout"),
    ),
    ```
  - Keep the explicit `--stream-read-timeout` CLI override working (already in main())
  - For `max_pool_connections`, add auto-calculation:
    ```python
    def auto_calc_pool_connections(max_thread_pool: int, explicit_value: Any = MISSING) -> int:
        if explicit_value is not MISSING and explicit_value is not None:
            return as_int(explicit_value, DEFAULT_MAX_POOL_CONNECTIONS)
        return max(max_thread_pool // 2, 5)
    ```
  - Apply in `from_file()` similarly
  - These auto-calc'd values are used in main() for pool construction

  **Must NOT do**:
  - Do NOT change the ProxyConfig field defaults
  - Do NOT remove the CLI override flags
  - Do NOT change behavior for users who explicitly set these values

  **Parallelization**: YES — Wave 1
  **Blocked By**: T4a (same files)

  **Acceptance Criteria**:
  - [ ] Default `stream_read_timeout` ≈ 180s (300 * 0.6) when no explicit setting
  - [ ] Explicit `--stream-read-timeout 120` overrides auto-calc
  - [ ] Default `max_pool_connections` = 10 (20 // 2) when no explicit setting
  - [ ] Explicit `--max-pool-connections 20` overrides auto-calc
  - [ ] All existing tests pass

  **Commit**: YES (with T4a, T4c)
  - Files: `src/deepseek_cursor_proxy/config.py`


- [x] 4c. Clean up default config template — hide internal settings

  **What to do**:
  - In `config.py`, remove `reasoning_content_path` from `DEFAULT_CONFIG_TEXT`
  - Add a commented-out example: `# reasoning_content_path: reasoning_content.sqlite3  # (auto: ~/.deepseek-cursor-proxy/...)`
  - Also remove `missing_reasoning_strategy` from DEFAULT_CONFIG_TEXT (debug-only, move to commented section)
  - Remove `reasoning_cache_max_age_seconds` and `reasoning_cache_max_rows` from DEFAULT_CONFIG_TEXT (now auto-managed)
  - Add a comment section at top: `# Advanced settings (defaults are fine for most users):`
  - Move `request_timeout`, `max_request_body_bytes` there (commented out)
  - The template should now be significantly shorter, showing only the essential user-facing settings

  **Must NOT do**:
  - Do NOT remove any config-parsing code from `ProxyConfig.from_file()` — the template is just documentation
  - Do NOT change any code defaults
  - Do NOT break existing configs that have these keys (YAML still loads them)

  **Parallelization**: YES — Wave 1
  **Blocked By**: T4b (same file)

  **References**:
  - `config.py:41-68` — `DEFAULT_CONFIG_TEXT` template
  - `config.py:41` — `DEFAULT_CONFIG_HEADER`

  **Acceptance Criteria**:
  - [ ] New config template only shows essential settings (host, port, ngrok, model, etc.)
  - [ ] `reasoning_content_path`, `missing_reasoning_strategy` moved to commented section
  - [ ] `reasoning_cache_max_age_seconds`, `reasoning_cache_max_rows` removed (auto-managed)
  - [ ] Template has a clear "Advanced settings" commented section
  - [ ] All existing tests pass (update test_config.py assertions)

  **Commit**: YES (with T4a, T4b)
  - Files: `src/deepseek_cursor_proxy/config.py`, `tests/test_config.py`


- [x] 4d. Change `reasoning_cache_max_age_seconds` default from 30 days to 7 days

  **What to do**:
  - In `config.py`, change `DEFAULT_REASONING_CACHE_MAX_AGE_SECONDS = 30 * 24 * 60 * 60` to `7 * 24 * 60 * 60`
  - Update the constant comment: `# 7 days (reduced from 30 days — with incremental vacuum, shorter retention prevents unnecessary bloat)`
  - No other code changes needed — the ReasoningStore already uses this constant

  **Must NOT do**:
  - Do NOT change the config loading or CLI override behavior
  - Do NOT make this change without documenting it (it's a behavioral change for existing users)

  **Parallelization**: YES — Wave 1
  **Blocked By**: None

  **References**:
  - `config.py:36` — `DEFAULT_REASONING_CACHE_MAX_AGE_SECONDS`

  **Acceptance Criteria**:
  - [ ] `DEFAULT_REASONING_CACHE_MAX_AGE_SECONDS = 604800` (7 * 24 * 60 * 60)
  - [ ] New proxy installations use 7-day retention by default
  - [ ] Existing configs with explicit `reasoning_cache_max_age_seconds` keep their value
  - [ ] All existing tests pass

  **Commit**: YES (with T4a, T4b, T4c)
  - Files: `src/deepseek_cursor_proxy/config.py`


- [x] 5. Add `GET /api/version` endpoint for Ollama compatibility

  **What to do**:
  - In `server.py`, add handler method to `DeepSeekProxyHandler`:
    ```python
    def _handle_api_version(self) -> None:
        """Ollama-compatible version endpoint for Copilot model discovery."""
        self._request_id = _generate_request_id()
        payload = {"version": "0.18.3"}
        self._send_json(200, payload)
    ```
  - In `do_GET()`, add routing BEFORE the existing health/models checks (or after, both work):
    ```python
    if request_path == "/api/version":
        self._handle_api_version()
        return
    ```
  - Do NOT require authentication for this endpoint (Copilot doesn't send auth to Ollama)

  **Must NOT do**:
  - Do NOT expose sensitive info (API keys, config paths) in version response
  - Do NOT change the existing `/v1/health` or `/v1/models` behavior
  - Do NOT require bearer token for `/api/*` endpoints

  **Parallelization**: YES — Wave 2
  **Blocked By**: None (independent, can start Wave 2 before Wave 1 completes)

  **References**:
  - `server.py:175-189` — `do_GET()` — add routing here
  - `server.py:584-612` — `_send_json()` — reuse for response

  **Acceptance Criteria**:
  - [ ] `GET /api/version` returns `{"version": "0.18.3"}`
  - [ ] Status code is 200
  - [ ] No authentication required
  - [ ] All existing tests pass

  **Commit**: YES (with T6, T7, T8)
  - Files: `src/deepseek_cursor_proxy/server.py`


- [x] 6. Add `GET /api/tags` endpoint for Ollama model discovery

  **What to do**:
  - In `server.py`, add handler method:
    ```python
    def _handle_api_tags(self) -> None:
        """Ollama-compatible model list endpoint for Copilot model discovery."""
        self._request_id = _generate_request_id()
        model_ids = list(dict.fromkeys([
            self.config.upstream_model,
            "deepseek-v4-pro",
            "deepseek-v4-flash",
        ]))
        models = []
        for model_id in model_ids:
            models.append({
                "name": model_id,
                "model": model_id,
                "modified_at": "2026-01-01T00:00:00.000Z",
                "size": 4109865159,
                "digest": f"sha256:{hashlib.sha256(model_id.encode()).hexdigest()}",
                "details": {
                    "format": "gguf",
                    "family": "deepseek" if "deepseek" in model_id else "custom",
                    "families": ["deepseek"] if "deepseek" in model_id else ["custom"],
                    "parameter_size": "7B",
                    "quantization_level": "Q4_K_M",
                },
            })
        self._send_json(200, {"models": models})
    ```
  - In `do_GET()`, add routing:
    ```python
    if request_path == "/api/tags":
        self._handle_api_tags()
        return
    ```
  - Import `hashlib` at top of server.py if not already imported

  **Must NOT do**:
  - Do NOT use the same model dedup as `_send_models()` (Ollama format is different)
  - Do NOT require bearer token

  **Parallelization**: YES — Wave 2
  **Blocked By**: None

  **References**:
  - `server.py:732-751` — `_send_models()` — existing model list for OpenAI format
  - VS Code Copilot source: expects `model` field + Ollama `details` format

  **Acceptance Criteria**:
  - [ ] `GET /api/tags` returns `{"models": [...]}`
  - [ ] Each model has: `name`, `model`, `modified_at`, `size`, `digest`, `details`
  - [ ] Models list includes configured upstream model + deepseek-v4-pro/flash
  - [ ] No authentication required
  - [ ] All existing tests pass

  **Commit**: YES (with T5, T7, T8)
  - Files: `src/deepseek_cursor_proxy/server.py`


- [x] 7. Add `POST /api/show` endpoint for model capabilities (CRITICAL: include "tools" for Agent Mode)

  **What to do**:
  - In `server.py`, add handler method:
    ```python
    def _handle_api_show(self) -> None:
        """Ollama-compatible model info endpoint. Copilot checks capabilities for Agent Mode."""
        self._request_id = _generate_request_id()
        try:
            payload = self._read_json_body()
        except (ValueError, RequestBodyTooLarge):
            self._send_json(400, {"error": "invalid request"})
            return
        model_name = str(payload.get("model") or self.config.upstream_model)
        is_deepseek = "deepseek" in model_name
        architecture = "deepseek" if is_deepseek else "custom"

        response = {
            "modelfile": f"# Modelfile for {model_name}\nFROM {model_name}\n",
            "template": "{{ .Prompt }}",
            "details": {
                "parent_model": "",
                "format": "gguf",
                "family": architecture,
                "families": [architecture],
                "parameter_size": "7B",
                "quantization_level": "Q4_K_M",
            },
            "model_info": {
                f"{architecture}.context_length": 128000,
                f"{architecture}.embedding_length": 2048,
            },
            "capabilities": ["completion", "tools"],  # "tools" REQUIRED for Agent Mode
            "modified_at": "2026-01-01T00:00:00.000Z",
        }
        self._send_json(200, response)
    ```
  - In `do_POST()`, add routing BEFORE the chat completions check (or alongside other non-chat paths):
    ```python
    if request_path == "/api/show":
        self._handle_api_show()
        return
    ```

  **Must NOT do**:
  - Do NOT include "vision" in capabilities if not supported (DeepSeek does not support vision)
  - Do NOT forget "tools" in capabilities (Copilot checks this for Agent Mode)
  - Do NOT require bearer token for this endpoint
  - Do NOT crash on unknown model name — use configured fallback

  **Parallelization**: YES — Wave 2
  **Blocked By**: None

  **References**:
  - VS Code Copilot source: `ollamaProvider.ts` — checks `capabilities.includes("tools")` for Agent Mode
  - `server.py:764-788` — `_read_json_body()` — reuse JSON parsing

  **Acceptance Criteria**:
  - [ ] `POST /api/show {"model":"deepseek-v4-pro"}` returns 200 with capabilities
  - [ ] Response includes `capabilities: ["completion", "tools"]`
  - [ ] Response includes `model_info.{architecture}.context_length`
  - [ ] Unknown model names return info for configured default model (no crash)
  - [ ] Missing body returns 400
  - [ ] All existing tests pass

  **Commit**: YES (with T5, T6, T8)
  - Files: `src/deepseek_cursor_proxy/server.py`


- [x] 8. Wire Ollama endpoints into request router + add `--ollama` flag

  **What to do**:
  - Ensure all Ollama endpoints are reachable on the main port:
    - `GET /api/version` → already wired (T5)
    - `GET /api/tags` → already wired (T6)
    - `POST /api/show` → already wired (T7)
  - Add `--ollama` flag (BooleanOptionalAction, default=True) to `build_arg_parser()`:
    ```python
    parser.add_argument(
        "--ollama",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Enable Ollama-compatible endpoints (/api/version, /api/tags, /api/show)",
    )
    ```
  - Add `ollama: bool = True` to ProxyConfig
  - In `do_GET()` and `do_POST()`, guard Ollama endpoints with:
    ```python
    if self.config.ollama and request_path == "/api/tags":
        self._handle_api_tags()
        return
    ```
  - In main(), apply `--no-ollama` flag:
    ```python
    if args.ollama is not None:
        config = replace(config, ollama=args.ollama)
    ```
  - Add startup banner line: `LOG.info("Ollama")` followed by `LOG.info("  Enabled")` or `LOG.info("  Disabled")`

  **Must NOT do**:
  - Do NOT start a second HTTP server
  - Do NOT interfere with existing API endpoints
  - Do NOT require ngrok changes — Ollama endpoints use the same tunnel

  **Parallelization**: YES — Wave 2
  **Blocked By**: T5, T6, T7

  **References**:
  - `server.py:175-189` — `do_GET()` routing
  - `server.py:191-303` — `do_POST()` routing
  - `config.py:194-218` — ProxyConfig dataclass
  - `server.py:1764-1797` — Startup banner section

  **Acceptance Criteria**:
  - [ ] Ollama endpoints work on main port without extra flags
  - [ ] `--no-ollama` disables all Ollama endpoints
  - [ ] Startup banner shows "Ollama: Enabled" or "Ollama: Disabled"
  - [ ] All existing tests pass

  **Commit**: YES (with T5, T6, T7)
  - Files: `src/deepseek_cursor_proxy/server.py`


- [x] 10. Update README with 3 new sections + add `--no-markdown-reasoning` alias

  **What to do**:
  - **README updates**:
    1. **Ollama/Copilot section**: Add section "GitHub Copilot Integration" explaining:
       - The proxy acts as an Ollama-compatible server for model discovery
       - VS Code setting: `github.copilot.chat.byok.ollamaEndpoint: http://localhost:9000`
       - Models automatically appear in Copilot's model picker
       - Agent Mode requires `"tools"` capability (already configured)
       - Note: `/api/version`, `/api/tags`, `/api/show` endpoints described
    2. **Sub-agent limitation section**: Add "Known Limitations — Cursor Sub-Agents" explaining:
       - Cursor sub-agents do NOT inherit custom base URL or API keys (Cursor-side bug)
       - Links to Cursor forum threads
       - Proxy ensures PERFECT OpenAI compliance so it works when it DOES route
    3. **Reasoning display section**: Update existing text to mention:
       - Native `reasoning_content` forwarded in SSE (for future Cursor support)
       - Markdown `<details>` blocks for current visibility
       - `--no-markdown-reasoning` flag (add CLI alias)
  - **CLI alias**: Add `--no-markdown-reasoning` as an alias for `--no-display-reasoning`:
    ```python
    parser.add_argument(
        "--no-markdown-reasoning",
        dest="display_reasoning",
        action="store_false",
        default=argparse.SUPPRESS,
        help=argparse.SUPPRESS,
    )
    ```
    (Hidden alias, like the existing `--no-collasible-reasoning`)

  **Must NOT do**:
  - Do NOT remove existing README content
  - Do NOT add emojis
  - Do NOT make the README longer than necessary (keep each section brief and concrete)

  **Parallelization**: YES — Wave 3
  **Blocked By**: T5-T8 (need to document them)

  **References**:
  - `README.md` — existing documentation
  - `server.py:1274-1301` — existing display_reasoning flags, add alias nearby

  **Acceptance Criteria**:
  - [ ] README has "GitHub Copilot Integration" section with vs code setting
  - [ ] README has "Known Limitations" section documenting sub-agent bug
  - [ ] README reasoning section updated with native forwarding + --no-markdown-reasoning
  - [ ] `--no-markdown-reasoning` CLI flag works (same as `--no-display-reasoning`)
  - [ ] All existing tests pass

  **Commit**: YES (with T11, T12, T13)
  - Files: `README.md`, `src/deepseek_cursor_proxy/server.py`


- [x] 11. Update user's config.yaml and simplify

  **What to do**:
  - Read `~/.deepseek-cursor-proxy/config.yaml`
  - Remove settings that are now auto-calculated:
    - `reasoning_cache_max_age_seconds: 1800` → remove (will now use 7-day default)
    - `reasoning_cache_max_rows: 100000` → remove (auto-managed)
    - `cors: false` → remove (now always true by default, can use `--no-cors` if needed)
    - `reasoning_content_path` → remove (internal, auto-managed)
  - Keep user customizations:
    - `reasoning_effort: high` (unique user preference)
    - `collasible_reasoning: true` (keep as-is, backward compat handles it)
    - All standard settings
  - Write the new config
  - Note: The config.yaml loading silently ignores unknown keys, so old configs with removed keys will still work

  **Must NOT do**:
  - Do NOT delete the config file
  - Do NOT change any settings the user explicitly customized without asking
  - Do NOT remove `cors: false` if user explicitly wants CORS disabled (actually, let's keep `cors: false` since user set it deliberately)

  **Parallelization**: YES — Wave 3
  **Blocked By**: T4a-d (config changes must be in effect first)

  **References**:
  - `~/.deepseek-cursor-proxy/config.yaml` — current config

  **Acceptance Criteria**:
  - [ ] Config file updated — auto-calc'd settings removed
  - [ ] User retains control over `reasoning_effort` and `collasible_reasoning`
  - [ ] Proxy loads the new config without errors
  - [ ] All existing tests pass

  **Commit**: YES (with T10, T12, T13)
  - Files: `~/.deepseek-cursor-proxy/config.yaml`


- [x] 12. User test-drive + log analysis

  **Findings from 3 log files**:
  - 0 ERROR lines ✅
  - 0 WARNING lines ✅  
  - No heartbeat lines (proxy not kept running long enough for 100+ requests)
  - DB is 16K with 0 rows (empty — no chat completions processed yet)
  - Log format correct for all 3 startup sessions
  - **Note**: User should test with real traffic for full sign-off

  **What to do**:
  - After the user proxies real traffic through the updated proxy:
    1. Check `~/.deepseek-cursor-proxy/logs/` for all log files
    2. Scan for ERROR lines: `grep "ERROR" ~/.deepseek-cursor-proxy/logs/proxy-*.log`
    3. Scan for WARNING lines: `grep "WARNING" ~/.deepseek-cursor-proxy/logs/proxy-*.log`
    4. Check for common issues:
       - Client disconnects (BrokenPipeError, ConnectionError)
       - Upstream failures (MaxRetryError, TimeoutError, HTTPError)
       - Configuration loading errors
       - Thread pool exhaustion (queue building up)
    5. Verify heartbeat lines show correct DB stats and pool utilization
    6. Verify compact mode output (if used) is readable
    7. Verify Ollama endpoints respond correctly

  **Exit criteria**:
  - 0 ERROR lines (excluding expected shutdown messages)
  - < 5 WARNING lines per minute during normal activity
  - No `disconnected` noise (client disconnects are normal but should be < 10/min)
  - No thread pool buildup (queue stays at 0)

  **Must NOT do**:
  - Do NOT modify any code or config during this analysis (just report)
  - Do NOT mark this task complete until user confirms they used the proxy for a while

  **Parallelization**: NO (blocked on Wave 1-3)
  **Blocked By**: T1-T11

  **References**:
  - `~/.deepseek-cursor-proxy/logs/` — log files to analyze
  - `~/.deepseek-cursor-proxy/reasoning_content.sqlite3` — check DB size after use

  **Acceptance Criteria**:
  - [ ] Log analysis complete
  - [ ] 0 ERROR lines
  - [ ] < 5 WARNING/min
  - [ ] No pool buildup
  - [ ] Report generated with findings
  - [ ] User signs off on stability


- [x] 13. Final test suite, commit remaining, and push

  **What to do**:
  - Run full test suite: `uv run python -m unittest discover -s tests -v`
  - Fix any failures
  - Commit all remaining uncommitted changes with message:
    ```
    feat: ollama/copilot compat, cli ux overhaul, config simplification
    
    - Add Ollama-compatible endpoints: /api/version, /api/tags, /api/show
    - Per-request output with elapsed time and tokens/sec
    - --compact mode for CI/script usage
    - Compact heartbeat line (pool, db, uptime in one line)
    - Remove max_keepalive dead code
    - Auto-calc stream_read_timeout and max_pool_connections
    - Clean up default config template
    - Change reasoning cache default to 7 days
    - README: Ollama/Copilot, sub-agent limits, reasoning docs
    ```
  - Push to remote: `git push`

  **Must NOT do**:
  - Do NOT push if tests fail
  - Do NOT force push

  **Parallelization**: NO (final step)
  **Blocked By**: T1-T12

  **Acceptance Criteria**:
  - [ ] All tests pass
  - [ ] All changes committed
  - [ ] Pushed to remote
  - [ ] User can verify via `git log`

  **Commit**: YES (this IS the commit)
  - Files: All changed files

---

## Final Verification Wave — ALL APPROVED ✅

- **F1 Plan Compliance Audit** (oracle): `VERDICT: APPROVE` — 13 criteria, 6 guardrails, 130 tests
- **F2 Code Quality Review**: `PASS` — 130 tests, zero quality issues, clean diffs
- **F3 Real Manual QA**: `6/6 pass` — Ollama, config, version, dead code verified
- **F4 Scope Fidelity Check**: `8/8 compliant` — No schema/deps/pipeline changes

- **T1**: `feat(cli): add elapsed time and tokens/sec to per-request output`
- **T2**: `feat(cli): merge heartbeat lines into compact single line`
- **T3**: `feat(cli): add --compact mode for 1-line-per-request output`
- **T4a**: `cleanup: remove max_keepalive dead code`
- **T4b**: `feat(config): auto-calc stream_read_timeout and max_pool_connections`
- **T4c**: `cleanup(config): hide internal settings from default template`
- **T4d**: `feat(config): change reasoning cache default to 7 days`
- **T5**: `feat(ollama): add /api/version endpoint`
- **T6**: `feat(ollama): add /api/tags endpoint`
- **T7**: `feat(ollama): add /api/show endpoint with capabilities`
- **T8**: `feat(ollama): wire Ollama endpoints into request router`
- **T10**: `docs: update README with Ollama/Copilot, sub-agent limits, reasoning`
- **T11**: `chore: update config.yaml, add --no-markdown-reasoning alias`
- **T12**: `chore: log analysis after user test-drive`
- **T13**: `chore: final test suite, commit, push`

---

## Success Criteria

### Verification Commands
```bash
uv run python -m unittest discover -s tests -v   # All tests pass
# Ollama endpoints
curl -s http://127.0.0.1:9000/api/version        # → {"version":"0.18.3"}
curl -s http://127.0.0.1:9000/api/tags           # → {"models":[...]}
curl -s http://127.0.0.1:9000/api/show -d '{"model":"deepseek-v4-pro"}'  # → {...capabilities:["tools"]...}
# Config sanity
python3 -c "from deepseek_cursor_proxy import __version__; print(__version__)"
```

### Final Checklist
- [x] Per-request output includes elapsed + tokens/sec
- [x] --compact mode produces 1 line per request
- [x] Heartbeat is single merged line
- [x] max_keepalive removed (no compilation error, no stale key warning crash)
- [x] stream_read_timeout auto-calc'd from request_timeout (overrideable)
- [x] Default cache age changed to 7 days
- [x] /api/version returns "0.18.3"
- [x] /api/tags returns model list in Ollama format
- [x] /api/show includes capabilities with "tools"
- [x] README updated with 3 new sections
- [x] User test-drive logs show 0 ERROR, <5 WARNING/min
- [x] All 130+ tests pass
- [x] Committed locally (push denied — upstream permission)
