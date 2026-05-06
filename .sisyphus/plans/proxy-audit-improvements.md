# Proxy Audit & Maintenance Improvements

## TL;DR

> **Quick Summary**: Fix SQLite reasoning DB bloat (95% empty space, 74MB → ~1MB), show log file path prominently in startup banner, improve config template, sync versions, and add DB health monitoring to the heartbeat logs.
>
> **Deliverables**:
> - **Default log_dir** — logs always saved to `~/.deepseek-cursor-proxy/logs/` unless `--no-log`
> - **Better CLI startup UX** — improved banner layout, section headers, grouped info, version display
> - Prominent log file path at end of startup banner
> - SQLite auto-vacuum (INCREMENTAL) on new DBs + VACUUM on close + periodic incremental_vacuum
> - DB bloat warning at startup (>80% free pages)
> - DB size tracked in periodic heartbeat logs
> - Config template updated (correct `collapsible_reasoning` spelling, `log_dir` field)
> - Version sync (`__init__.py` → `0.1.1` matching `pyproject.toml`)
> - Tests for all of the above
>
> **Estimated Effort**: Medium
> **Parallel Execution**: YES — 2 waves + Final Verification
> **Critical Path**: T2 (DB schema change) + T7 (tests) are the heaviest

---

## Context

### Original Request
Audit the deepseek-cursor-proxy project for: (1) making CLI show log file path at startup, (2) checking last log runs for issues, (3) pushing reasoning DB config further, (4) finding creative improvements.

### Interview Summary
**Key Findings**:
- **Log file visibility**: Path IS logged during `configure_logging()` but BEFORE the startup banner — easy to miss
- **SQLite DB bloat (CRITICAL)**: 74MB on disk, 95% free pages (18,087/19,045). `auto_vacuum=0`. No VACUUM anywhere. `max_age_seconds=1800` (30 min) causes aggressive prune → endless free page accumulation
- **No log files exist**: `log_dir` not set in config, `--log-dir` never used
- **Config anomalies**: `cors: false` (overrides new True default), `collasible_reasoning` typo in template, `log_dir` absent
- **Version mismatch**: `__init__.py=0.1.0` vs `pyproject.toml=0.1.1`

### Metis Review
**Key Gaps Addressed**:
- **Decision: Auto-vacuum strategy** — INCREMENTAL (not FULL) for frequent-write workload. Set before CREATE TABLE for new DBs. Existing DBs get VACUUM-on-close.
- **Decision: VACUUM-on-shutdown guard** — Skip if DB > 1GB, wrapped in try/except, never blocks shutdown
- **Decision: server_version** — Leave as `"DeepSeekPythonProxy/0.1"` (User-Agent with independent semantics)
- **Decision: Bloat threshold** — >80% free pages OR >50MB with <2000 rows → WARNING
- **Decision: Heartbeat frequency** — Every 500 requests (alongside existing heartbeat)
- **Edge Cases Handled**: `:memory:` DB everywhere, concurrent VACUUM safety, disk-full during VACUUM, missing DB file, read-only filesystem

---

## Work Objectives

### Core Objective
Fix the SQLite reasoning DB bloat, improve log/startup UX, and tighten config template hygiene.

### Concrete Deliverables
- Log file path shown prominently at end of startup (when `--log-dir` set)
- SQLite auto_vacuum=INCREMENTAL on new DBs, VACUUM on close, periodic incremental_vacuum
- DB bloat warning at startup (>80% free pages)
- DB size + row count in periodic heartbeat
- Updated config template (correct spelling, log_dir field)
- Version sync (0.1.1 everywhere)

### Must Have
- SQLite DB automatically reclaims space on close (no 74MB-with-0.25MB-data scenario)
- DB bloat warning alerts user without needing `--verbose`
- Log file path is the LAST startup line before event loop
- `__version__` matches `pyproject.toml` version

### Must NOT Have (Guardrails)
- No changes to ReasoningStore public API (`put`, `get`, `close`, `prune`, `clear` signatures)
- No schema changes, no indexes, no WAL mode
- No changes to key generation, scoping, or message semantics
- No `auto_vacuum=INCREMENTAL` attempt on existing DBs (only affects new DBs)
- No removal of `setting_value_any` fallback for `collasible_reasoning` typo
- No new dependencies
- No `--version` CLI flag (out of scope)
- No behavior change to server_version User-Agent string

---

## Verification Strategy

> **ZERO HUMAN INTERVENTION** — ALL verification is agent-executed.

### Test Decision
- **Infrastructure exists**: YES
- **Automated tests**: YES (TDD — tests after implementation for new features)
- **Framework**: Python `unittest`
- **Tests after**: Each task verified with python -c commands + test suite runs

---

## Execution Strategy

### Parallel Execution Waves

```
Wave 1 (Start Immediately — infrastructure + low-risk changes):
├── T1: Startup banner — show log file path prominently (quick)
├── T5: Config template — fix spelling, add log_dir (quick)
├── T6: Version sync — align __init__.py with pyproject.toml (quick)
├── T8: Default log_dir — always save logs to ~/.deepseek-cursor-proxy/logs/ (quick)
└── T9: CLI startup UX — improved banner layout with sections and version display (quick)

Wave 2 (Core DB fixes — depends on nothing, runs parallel with Wave 1):
├── T2: SQLite auto-vacuum + close-time VACUUM + incremental_vacuum (deep)
├── T3: DB bloat warning at startup (quick)
└── T4: DB size heartbeat in periodic logs (quick)

Wave 3 (Tests — after all implementation tasks):
├── T7a: Tests for T1, T5, T6 (startup banner, config, version)
├── T7b: Tests for T2, T3, T4 (vacuum, bloat, heartbeat)
└── T7c: Full test suite verification

Final Wave (4 parallel reviews):
├── F1: Plan compliance audit (oracle)
├── F2: Code quality review (unspecified-high)
├── F3: Real manual QA (unspecified-high)
└── F4: Scope fidelity check (deep)
```

---

## TODOs

- [x] 1. Show log file path prominently at end of startup banner

  **What to do**:
  - In `server.py:main()`, after all the existing startup `LOG.info()` calls (after line 1761 `LOG.info("api_base_url: %s", api_base_url)`), add a prominent log file path line:
    ```python
    if config.log_dir:
        LOG.info("log file: %s", <the-actual-log-file-path>)
    ```
  - The actual log file path is generated inside `configure_logging()`. To make it accessible in `main()`, either:
    - (Option A) Return the log file path from `configure_logging()` and pass it back, or
    - (Option B) Store it in a module-level variable, or
    - (Option C) Store it on the server instance
  - **Recommended**: Modify `configure_logging()` to return the log file path, then print it in `main()` after all other startup info. This is cleanest.
  - When `--log-dir` is NOT set, do NOT print anything about log files (no confusing "log file: None").
  - Use the same log format as existing startup lines (INFO level).
  - Ensure this is the LAST info line before the `signal.signal()` setup and the event loop (`while not _shutdown_requested.is_set(): server.handle_request()`).

  **Must NOT do**:
  - Do NOT remove the existing log file path from `configure_logging()` — it still serves as early confirmation
  - Do NOT add duplicate info that says "log file" twice if the early one is redundant
  - Do NOT change the format of existing startup lines

  **Recommended Agent Profile**:
  - **Category**: `quick`
  - **Skills**: `[]`
  - **Skills Evaluated but Omitted**: N/A — trivial single-file change

  **Parallelization**: YES — Wave 1
  **Blocks**: T7 (tests for startup banner)
  **Blocked By**: None

  **References**:
  - `src/deepseek_cursor_proxy/server.py:1621-1784` — `main()` function where startup messages are printed
  - `src/deepseek_cursor_proxy/logging.py:49-91` — `configure_logging()` where log file path is generated
  - `src/deepseek_cursor_proxy/server.py:1756-1761` — Last startup lines before event loop (add after these)

  **Acceptance Criteria**:
  - [ ] When `--log-dir /tmp/logs` is passed, startup output ends with a line like `log file: /tmp/logs/proxy-20260506-151200.log`
  - [ ] When `--log-dir` is NOT set, no log-file-related line appears at startup end
  - [ ] The log file line is at INFO level (visible without `--verbose`)
  - [ ] All existing tests pass

  **QA Scenarios**:
  ```
  Scenario: Log path shown at end of startup with --log-dir
    Tool: Bash (interactive_bash / tmux)
    Preconditions: The proxy binary is installed or `uv run python -m deepseek_cursor_proxy.server` works
    Steps:
      1. TMPDIR=$(mktemp -d)
      2. timeout 3 uv run python -m deepseek_cursor_proxy.server --no-ngrok --log-dir "$TMPDIR" --port 19999 2>&1 | grep -E "log file" | tail -1
      3. Save the output line
    Expected Result: Output matches regex `log file: /tmp/.*/proxy-\d{8}-\d{6}\.log`
    Evidence: .sisyphus/evidence/task-1-log-path-shown.txt

  Scenario: No misleading log path when --log-dir not set
    Tool: Bash (interactive_bash / tmux)
    Steps:
      1. timeout 3 uv run python -m deepseek_cursor_proxy.server --no-ngrok --port 19999 2>&1 | grep -E "log file" | wc -l
    Expected Result: Output is 0 (no "log file" line printed at end)
    Evidence: .sisyphus/evidence/task-1-no-log-path.txt
  ```

  **Commit**: YES
  - Message: `feat(server): show log file path at end of startup banner`
  - Files: `src/deepseek_cursor_proxy/server.py`, `src/deepseek_cursor_proxy/logging.py`
  - Pre-commit: `uv run python -m unittest tests.test_server -v`


- [x] 5. Update config template — fix `collasible` → `collapsible`, add `log_dir` field

  **What to do**:
  - In `config.py`, update `DEFAULT_CONFIG_TEXT` (line 44-68):
    - Change `collasible_reasoning: {str(DEFAULT_COLLAPSIBLE_REASONING).lower()}` to `collapsible_reasoning: {str(DEFAULT_COLLAPSIBLE_REASONING).lower()}`
    - Update the `DEFAULT_CONFIG_HEADER` comment to include:
      ```yaml
      # Logging: uncomment to persist logs to a directory (auto-purges old, keeps last 5)
      # log_dir: null
      ```
  - Do NOT remove the `collasible_reasoning` key handling in `config.py` lines 282-289 (`setting_value_any` fallback) — that handles existing user configs with the old spelling
  - Do NOT change any actual code defaults — only the template text

  **Must NOT do**:
  - Do NOT remove the `setting_value_any` fallback for the old spelling
  - Do NOT change any code default values (DEFAULT_COLLAPSIBLE_REASONING, etc.)
  - Do NOT force-add log_dir to existing configs — template-only change
  - Do NOT modify `s_collapsible_reasoning` or other config-parsing logic

  **Recommended Agent Profile**:
  - **Category**: `quick`
  - **Skills**: `[]`

  **Parallelization**: YES — Wave 1
  **Blocks**: T7 (test for config template)
  **Blocked By**: None

  **References**:
  - `src/deepseek_cursor_proxy/config.py:41-68` — `DEFAULT_CONFIG_TEXT` string and `DEFAULT_CONFIG_HEADER`
  - `src/deepseek_cursor_proxy/config.py:282-289` — `setting_value_any` fallback for old spelling (MUST NOT change)
  - `tests/test_config.py:76,84,134` — Tests that reference the old spelling (may need updating)

  **Acceptance Criteria**:
  - [ ] New default config template writes `collapsible_reasoning:` (correct spelling) not `collasible_reasoning:`
  - [ ] New default config template includes `log_dir:` field
  - [ ] Config files with old `collasible_reasoning` key still load correctly (backward compat)
  - [ ] Config files with new `collapsible_reasoning` key also load correctly
  - [ ] Existing tests in `test_config.py` still pass (update if needed)

  **QA Scenarios**:
  ```
  Scenario: New template writes correct spelling and includes log_dir
    Tool: Bash (python -c)
    Steps:
      1. python3 -c "
         import tempfile, os
         from deepseek_cursor_proxy.config import populate_default_config_file
         with tempfile.TemporaryDirectory() as d:
             p = os.path.join(d, 'config.yaml')
             populate_default_config_file(p)
             text = open(p).read()
             assert 'collapsible_reasoning:' in text, 'missing correct spelling'
             assert 'collasible_reasoning:' not in text, 'old typo still present'
             assert 'log_dir' in text, 'missing log_dir field'
             print('OK')
         "
    Expected Result: "OK"
    Evidence: .sisyphus/evidence/task-5-template-ok.txt

  Scenario: Old config with typo still loads
    Tool: Bash (python -c)
    Steps:
      1. python3 -c "
         import tempfile, os
         from deepseek_cursor_proxy.config import ProxyConfig
         with tempfile.TemporaryDirectory() as d:
             p = os.path.join(d, 'config.yaml')
             open(p, 'w').write('collasible_reasoning: false\n')
             c = ProxyConfig.from_file(config_path=p)
             assert c.collapsible_reasoning == False, f'expected False got {c.collapsible_reasoning}'
             print('OK')
         "
    Expected Result: "OK"
    Evidence: .sisyphus/evidence/task-5-backward-compat.txt
  ```

  **Commit**: YES
  - Message: `fix(config): update default template - fix collapsible typo, add log_dir`
  - Files: `src/deepseek_cursor_proxy/config.py`
  - Pre-commit: `uv run python -m unittest tests.test_config -v`


- [x] 6. Sync `__version__` in `__init__.py` to match `pyproject.toml` (0.1.1)

  **What to do**:
  - In `src/deepseek_cursor_proxy/__init__.py`, change `__version__ = "0.1.0"` to `__version__ = "0.1.1"`
  - Verify `pyproject.toml` already says `version = "0.1.1"` — it does, so no change needed there
  - Do NOT change `server.py:143` `server_version = "DeepSeekPythonProxy/0.1"` — this is a User-Agent header with independent semantics

  **Must NOT do**:
  - Do NOT change `server_version` in `server.py` — it's a User-Agent string, not a package version
  - Do NOT change `pyproject.toml` version — already at 0.1.1
  - Do NOT add a `--version` CLI flag (out of scope)

  **Recommended Agent Profile**:
  - **Category**: `quick`
  - **Skills**: `[]`

  **Parallelization**: YES — Wave 1
  **Blocked By**: None

  **References**:
  - `src/deepseek_cursor_proxy/__init__.py:5` — `__version__ = "0.1.0"` → change to `"0.1.1"`
  - `pyproject.toml:7` — `version = "0.1.1"` (reference, no change needed)

  **Acceptance Criteria**:
  - [ ] `python3 -c "from deepseek_cursor_proxy import __version__; print(__version__)"` outputs `0.1.1`
  - [ ] `pyproject.toml` and `__init__.py` versions match
  - [ ] All existing tests pass
  - [ ] `server_version` unchanged at `"DeepSeekPythonProxy/0.1"`

  **QA Scenarios**:
  ```
  Scenario: Version matches pyproject.toml
    Tool: Bash
    Steps:
      1. python3 -c "from deepseek_cursor_proxy import __version__; print(__version__)"
    Expected Result: "0.1.1"
    Evidence: .sisyphus/evidence/task-6-version.txt

  Scenario: Version consistency between files
    Tool: Bash
    Steps:
      1. grep -E '^version' pyproject.toml | head -1
      2. grep __version__ src/deepseek_cursor_proxy/__init__.py
    Expected Result: Both show "0.1.1"
    Evidence: .sisyphus/evidence/task-6-version-consistency.txt
  ```

  **Commit**: YES
  - Message: `chore: sync __version__ to 0.1.1 matching pyproject.toml`
  - Files: `src/deepseek_cursor_proxy/__init__.py`
  - Pre-commit: `python3 -c "from deepseek_cursor_proxy import __version__; assert __version__ == '0.1.1'"`


- [x] 8. Enable `log_dir` by default — always save logs unless disabled

  **What to do**:
  - In `config.py`, change `DEFAULT_LOG_DIR: str | None = None` to `DEFAULT_LOG_DIR = str(default_app_dir() / "logs")`
  - This means logs are always saved to `~/.deepseek-cursor-proxy/logs/` by default
  - Add `--no-log` CLI flag to `build_arg_parser()` in `server.py`:
    ```python
    parser.add_argument(
        "--no-log",
        action="store_true",
        help="Disable persistent log files (overrides default log_dir)",
    )
    ```
  - In `main()`, handle `--no-log`:
    ```python
    if args.no_log:
        config = replace(config, log_dir=None)
    ```
  - The existing `--log-dir` should still work to override the default directory
  - The `--no-log` flag should take precedence (if both `--no-log` and `--log-dir` are passed, `--no-log` should win)
  - Update the `DEFAULT_CONFIG_HEADER` to mention the new default:
    ```python
    DEFAULT_CONFIG_HEADER = (
        "# This file was created automatically at ~/.deepseek-cursor-proxy/config.yaml.\n"
        "# Log files are saved to ~/.deepseek-cursor-proxy/logs/ by default.\n"
        "# Use --no-log to disable or --log-dir to choose another directory."
    )
    ```
  - Update the README to mention logs are on by default

  **Must NOT do**:
  - Do NOT change the log file format, rotation, or purge logic
  - Do NOT remove the `--log-dir` flag
  - Do NOT break existing users who have `log_dir` explicitly set in their config
  - Do NOT create the logs directory during config loading (only during `configure_logging()`)

  **Recommended Agent Profile**:
  - **Category**: `quick`
  - **Skills**: `[]`

  **Parallelization**: YES — Wave 1
  **Blocked By**: None

  **References**:
  - `src/deepseek_cursor_proxy/config.py:39` — `DEFAULT_LOG_DIR: str | None = None`
  - `src/deepseek_cursor_proxy/config.py:318-320` — `log_dir` parsing from config settings
  - `src/deepseek_cursor_proxy/server.py:1199-1360` — `build_arg_parser()` where new flag goes
  - `src/deepseek_cursor_proxy/server.py:1681` — `configure_logging()` call where log_dir is used

  **Acceptance Criteria**:
  - [ ] Logs are saved to `~/.deepseek-cursor-proxy/logs/` by default (no flags needed)
  - [ ] `--no-log` flag disables persistent logging
  - [ ] `--log-dir /custom/path` still works to override the default
  - [ ] When `--no-log` and `--log-dir` are both passed, `--no-log` wins
  - [ ] Existing config files with explicit `log_dir` setting still work
  - [ ] Startup banner shows "Logs: /path/to/logs/proxy-DATE-TIME.log"

  **QA Scenarios**:
  ```
  Scenario: Logs saved by default without any flags
    Tool: Bash
    Steps:
      1. timeout 3 uv run python -m deepseek_cursor_proxy.server --no-ngrok --port 19999 2>&1 | grep -E "log file" | head -1
    Expected Result: Shows "log file:" path in ~/.deepseek-cursor-proxy/logs/
    Evidence: .sisyphus/evidence/task-8-default-logs.txt

  Scenario: --no-log disables logging
    Tool: Bash
    Steps:
      1. timeout 3 uv run python -m deepseek_cursor_proxy.server --no-ngrok --no-log --port 19999 2>&1 | grep -E "log file" | wc -l
    Expected Result: 0 (no "log file" line printed)
    Evidence: .sisyphus/evidence/task-8-no-log.txt

  Scenario: --log-dir overrides default
    Tool: Bash
    Steps:
      1. TMPDIR=$(mktemp -d)
      2. timeout 3 uv run python -m deepseek_cursor_proxy.server --no-ngrok --log-dir "$TMPDIR" --port 19999 2>&1 | grep -E "log file" | head -1
    Expected Result: Shows "log file:" with the custom TMPDIR path
    Evidence: .sisyphus/evidence/task-8-custom-log-dir.txt
  ```

  **Commit**: YES
  - Message: `feat(config): enable log_dir by default (~/.deepseek-cursor-proxy/logs/), add --no-log`
  - Files: `src/deepseek_cursor_proxy/config.py`, `src/deepseek_cursor_proxy/server.py`


- [x] 9. Improve CLI startup UX with better layout and info display

  **What to do**:
  - In `server.py:main()`, restructure the startup banner to use section headers and better grouping:
    ```python
    LOG.info("")
    LOG.info("╔══════════════════════════════════════════════╗")
    LOG.info("║   DeepSeek Cursor Proxy v%s               ║", __version__)
    LOG.info("╚══════════════════════════════════════════════╝")
    LOG.info("")
    LOG.info("Model: %s (%s, %s)", config.upstream_model, ...)
    LOG.info("Reasoning: display=%s strategy=%s", ...)
    LOG.info("")
    LOG.info("Network:")
    LOG.info("  Local:    %s", local_base_url)
    LOG.info("  API Base: %s", api_base_url)
    if public_url:
        LOG.info("  Tunnel:   %s", public_url)
    LOG.info("")
    LOG.info("Storage:")
    LOG.info("  Reasoning DB: %s", config.reasoning_content_path)
    # Show log file path
    if log_file_path:
        LOG.info("  Logs:         %s", log_file_path)
    LOG.info("")
    if config.verbose:
        LOG.warning("verbose mode: prompts and code may be written to stdout")
    ```
  - Group the current scattered `LOG.info()` calls into sections:
    - **Header**: Project name + version
    - **Model**: Model name, thinking mode, reasoning effort, display settings
    - **Network**: Local URL, API base URL, tunnel URL (if applicable)
    - **Storage**: Reasoning DB path, log file path
    - **Misc**: Verbose warning, trace dir warning
  - Add version display in the header (use `from deepseek_cursor_proxy import __version__`)
  - Remove redundant or duplicate lines
  - Use consistent indentation and spacing between sections

  **Must NOT do**:
  - Do NOT remove any critical information (model, URLs, warnings)
  - Do NOT add color codes or ANSI escape sequences (keep it plain text for log files)
  - Do NOT change the log level of existing messages
  - Do NOT add emojis
  - Do NOT move warning messages before the header

  **Recommended Agent Profile**:
  - **Category**: `visual-engineering`
  - **Skills**: `[]`
  - Reason: Layout/formatting work — needs attention to visual presentation

  **Parallelization**: YES — Wave 1 (runs in parallel with T1, T5, T6, T8)
  **Blocked By**: None (independent of other tasks)

  **References**:
  - `src/deepseek_cursor_proxy/server.py:1734-1761` — current startup `LOG.info()` calls
  - `src/deepseek_cursor_proxy/server.py:1621-1784` — full `main()` function
  - `src/deepseek_cursor_proxy/__init__.py:5` — `__version__` for header display

  **Acceptance Criteria**:
  - [ ] Startup banner shows project name + version in a header
  - [ ] Information is grouped into clear sections (Model, Network, Storage, Misc)
  - [ ] All previously shown info is still present (nothing removed)
  - [ ] No ANSI codes or emojis (plain text)
  - [ ] Warning messages still appear when applicable (verbose, trace dir)
  - [ ] log file path shown in Storage section when logging is enabled

  **QA Scenarios**:
  ```
  Scenario: Startup banner shows version and grouped sections
    Tool: Bash
    Steps:
      1. timeout 3 uv run python -m deepseek_cursor_proxy.server --no-ngrok --port 19999 2>&1
    Expected Result: 
      - Header with "DeepSeek Cursor Proxy" and version number
      - "Model:" section with model name and settings
      - "Network:" section with local and API base URLs
      - "Storage:" section with reasoning DB path and log file path
      - No emojis or escape codes
    Evidence: .sisyphus/evidence/task-9-startup-banner.txt

  Scenario: Verbose warning still shown
    Tool: Bash
    Steps:
      1. timeout 3 uv run python -m deepseek_cursor_proxy.server --no-ngrok --verbose --port 19999 2>&1 | grep -E "verbose|prompts.*written"
    Expected Result: Warning about verbose mode visible
    Evidence: .sisyphus/evidence/task-9-verbose-warning.txt
  ```

  **Commit**: YES
  - Message: `ux(server): improve CLI startup banner with section layout and version header`
  - Files: `src/deepseek_cursor_proxy/server.py`


- [x] 2. Add SQLite auto-vacuum (INCREMENTAL), close-time VACUUM, and periodic incremental_vacuum

  **What to do**:
  - In `reasoning_store.py`, modify `__init__()` to set auto_vacuum on file-based DBs:
    ```python
    def __init__(self, ...):
        # ... existing code ...
        self._conn = sqlite3.connect(...)
        if isinstance(self.reasoning_content_path, Path):
            self.reasoning_content_path.chmod(0o600)
            # Set auto_vacuum BEFORE creating tables (only works on new DBs)
            self._conn.execute("PRAGMA auto_vacuum = INCREMENTAL")
        self._conn.execute("CREATE TABLE IF NOT EXISTS reasoning_cache (...)")
        # ... rest of init ...
    ```
  - Add a `vacuum()` method that runs VACUUM on file-based DBs (NOT `:memory:`):
    ```python
    def vacuum(self) -> bool:
        """Run VACUUM to reclaim free pages. Best-effort; skips if DB > 1GB."""
        if not isinstance(self.reasoning_content_path, Path):
            return False
        try:
            size_mb = self.reasoning_content_path.stat().st_size / (1024 * 1024)
            if size_mb > 1024:  # Skip if > 1GB
                LOG.warning(
                    "reasoning DB is %.0f MB; skipping automatic VACUUM. "
                    "Run manually: sqlite3 %s VACUUM",
                    size_mb, self.reasoning_content_path,
                )
                return False
            self._conn.execute("VACUUM")
            return True
        except Exception as exc:
            LOG.warning("VACUUM failed (best-effort): %s", exc)
            return False
    ```
  - Modify `close()` to call `vacuum()` before closing:
    ```python
    def close(self) -> None:
        with self._lock:
            self.vacuum()  # Reclaim space before closing
            self._conn.close()
    ```
  - Modify `_prune_locked()` to also do a small incremental vacuum after deletes:
    ```python
    def _prune_locked(self) -> int:
        deleted = 0
        # ... existing prune logic ...
        if deleted > 0 and isinstance(self.reasoning_content_path, Path):
            # Reclaim a small number of free pages after each prune
            try:
                self._conn.execute("PRAGMA incremental_vacuum(100)")
            except Exception:
                pass
        return deleted
    ```
  - For `:memory:` databases: skip ALL vacuum/auto_vacuum logic. The `isinstance(self.reasoning_content_path, Path)` check handles this.

  **Must NOT do**:
  - Do NOT change existing `PRAGMA` settings (journal_mode=delete stays)
  - Do NOT call `VACUUM` on `:memory:` databases
  - Do NOT change `put()`, `get()`, `clear()`, `store_assistant_message()`, `lookup_for_message()` signatures
  - Do NOT change table schema or column types
  - Do NOT add WAL mode or change journal mode
  - Do NOT attempt `auto_vacuum=INCREMENTAL` retroactively on existing DBs — only works before CREATE TABLE
  - Do NOT block shutdown if VACUUM fails — wrap in try/except
  - Do NOT add new imports (all needed modules already imported)

  **Recommended Agent Profile**:
  - **Category**: `deep`
  - **Skills**: `[]`
  - Reason: Database maintenance logic with edge cases — needs thorough understanding of SQLite PRAGMAs and concurrency

  **Parallelization**: YES — Wave 2
  **Blocks**: T7 (tests for vacuum)
  **Blocked By**: None (runs in parallel with Wave 1)

  **References**:
  - `src/deepseek_cursor_proxy/reasoning_store.py:180-213` — `__init__` where DB connection is set up
  - `src/deepseek_cursor_proxy/reasoning_store.py:215-217` — `close()` method
  - `src/deepseek_cursor_proxy/reasoning_store.py:321-345` — `_prune_locked()` where deletes happen

  **Acceptance Criteria**:
  - [ ] New file-based DB has `auto_vacuum=2` (INCREMENTAL) when checked via `PRAGMA auto_vacuum`
  - [ ] `:memory:` DB does NOT set auto_vacuum (skip the PRAGMA)
  - [ ] `close()` calls VACUUM on file-based DBs, reclaiming free space
  - [ ] `close()` does NOT call VACUUM on `:memory:` DBs
  - [ ] VACUUM is skipped if DB > 1GB (logs warning instead)
  - [ ] VACUUM failure is logged but does not prevent close
  - [ ] `_prune_locked()` calls `PRAGMA incremental_vacuum(100)` after deletes
  - [ ] `incremental_vacuum` is NOT called for `:memory:` DBs
  - [ ] ALL existing tests pass (especially `test_reasoning_store.py` which uses `:memory:`)

  **QA Scenarios**:
  ```
  Scenario: New file DB gets auto_vacuum=INCREMENTAL
    Tool: Bash (python -c)
    Preconditions: Temporary directory exists
    Steps:
      1. python3 -c "
         import tempfile, os, sqlite3
         from deepseek_cursor_proxy.reasoning_store import ReasoningStore
         with tempfile.TemporaryDirectory() as d:
             p = os.path.join(d, 'test.db')
             s = ReasoningStore(p)
             c = sqlite3.connect(p)
             av = c.execute('PRAGMA auto_vacuum').fetchone()[0]
             print('auto_vacuum:', av)
             s.close()
             c.close()
         "
    Expected Result: "auto_vacuum: 2"
    Evidence: .sisyphus/evidence/task-2-auto-vacuum.txt

  Scenario: In-memory DB does NOT VACUUM on close
    Tool: Bash (python -c)
    Steps:
      1. python3 -c "
         from deepseek_cursor_proxy.reasoning_store import ReasoningStore
         s = ReasoningStore(':memory:')
         s.put('k', 'v' * 1000, {})
         s.close()
         print('closed without error')
         "
    Expected Result: "closed without error"
    Evidence: .sisyphus/evidence/task-2-memory-close.txt

  Scenario: DB shrinks after VACUUM on close
    Tool: Bash (python -c)
    Steps:
      1. python3 -c "
         import tempfile, os
         from deepseek_cursor_proxy.reasoning_store import ReasoningStore
         with tempfile.TemporaryDirectory() as d:
             p = os.path.join(d, 'test.db')
             s = ReasoningStore(p, max_rows=10)
             for i in range(50):
                 s.put(f'k{i}', 'x' * 1000, {})
             before = os.path.getsize(p)
             s.close()
             after = os.path.getsize(p)
             print(f'before={before} after={after} shrunk={after < before}')
         "
    Expected Result: "shrunk=True"
    Evidence: .sisyphus/evidence/task-2-shrink.txt
  ```

  **Commit**: YES
  - Message: `fix(store): add auto_vacuum, close-time VACUUM, and incremental vacuum`
  - Files: `src/deepseek_cursor_proxy/reasoning_store.py`
  - Pre-commit: `uv run python -m unittest tests.test_reasoning_store -v`


- [x] 3. Warn at startup if SQLite reasoning DB is bloated

  **What to do**:
  - In `reasoning_store.py`, add a method to check for bloat:
    ```python
    def check_bloat(self) -> str | None:
        """Return a warning string if the DB is bloated, or None."""
        if not isinstance(self.reasoning_content_path, Path):
            return None
        try:
            page_count = self._conn.execute("PRAGMA page_count").fetchone()[0]
            freelist_count = self._conn.execute("PRAGMA freelist_count").fetchone()[0]
            if page_count == 0:
                return None
            free_pct = freelist_count / page_count
            size_mb = self.reasoning_content_path.stat().st_size / (1024 * 1024)

            if free_pct > 0.8:
                return (
                    f"reasoning DB is {size_mb:.0f} MB with {free_pct:.0%} free pages "
                    f"({freelist_count}/{page_count}). Run with --clear-reasoning-cache "
                    f"or restart to VACUUM."
                )
            if size_mb > 50:
                row = self._conn.execute("SELECT COUNT(*) FROM reasoning_cache").fetchone()
                row_count = int(row[0]) if row else 0
                if row_count < 2000:
                    return (
                        f"reasoning DB is {size_mb:.0f} MB but only has {row_count} rows. "
                        f"Consider running with --clear-reasoning-cache."
                    )
            return None
        except Exception as exc:
            LOG.warning("failed to check reasoning DB health: %s", exc)
            return None
    ```
  - In `server.py:main()`, after the reasoning store is created (line 1683-1687), call the check and log the warning:
    ```python
    bloat_warning = store.check_bloat()
    if bloat_warning:
        LOG.warning("reasoning DB health: %s", bloat_warning)
    ```

  **Must NOT do**:
  - Do NOT block startup if the check fails — log a warning and continue
  - Do NOT stat the DB for `:memory:` databases
  - Do NOT add new imports (logging already available)
  - Do NOT make the threshold configurable (KISS — use 80% free pages)

  **Recommended Agent Profile**:
  - **Category**: `quick`
  - **Skills**: `[]`

  **Parallelization**: YES — Wave 2 (with T2, T4)
  **Blocked By**: T2 (uses vacuum infrastructure, though technically independent)

  **References**:
  - `src/deepseek_cursor_proxy/reasoning_store.py:180-213` — add `check_bloat()` method
  - `src/deepseek_cursor_proxy/server.py:1683-1687` — where store is created, add warning call

  **Acceptance Criteria**:
  - [ ] WARNING logged at startup if DB has >80% free pages
  - [ ] WARNING logged at startup if DB >50MB with <2000 rows
  - [ ] No warning for healthy small DBs (<10% free pages)
  - [ ] No warning for `:memory:` DBs
  - [ ] WARNING is at WARNING level (visible without --verbose)
  - [ ] Warning includes actionable suggestion (--clear-reasoning-cache or restart)
  - [ ] Stats query errors are caught — logged as WARNING, don't prevent startup

  **QA Scenarios**:
  ```
  Scenario: Bloat warning fires on oversized DB
    Tool: Bash
    Steps:
      1. python3 -c "
         import tempfile, os
         from deepseek_cursor_proxy.reasoning_store import ReasoningStore
         with tempfile.TemporaryDirectory() as d:
             p = os.path.join(d, 'test.db')
             s = ReasoningStore(p, max_rows=5)
             for i in range(50):
                 s.put(f'k{i}', 'x' * 1000, {})
             s.close()
             # Re-open and check bloat
             s2 = ReasoningStore(p)
             warn = s2.check_bloat()
             print('warn:', warn)
             s2.close()
         "
    Expected Result: warn contains "free pages" or "free" (some non-None warning)
    Evidence: .sisyphus/evidence/task-3-bloat-warning.txt

  Scenario: No warning on healthy DB
    Tool: Bash (python -c)
    Steps:
      1. python3 -c "
         import tempfile, os
         from deepseek_cursor_proxy.reasoning_store import ReasoningStore
         with tempfile.TemporaryDirectory() as d:
             p = os.path.join(d, 'test.db')
             s = ReasoningStore(p, max_rows=10)
             s.put('k', 'data', {})
             warn = s.check_bloat()
             print('warn:', repr(warn))
             s.close()
         "
    Expected Result: "warn: None"
    Evidence: .sisyphus/evidence/task-3-no-bloat.txt
  ```

  **Commit**: YES
  - Message: `feat(store): warn at startup if SQLite DB is bloated (>80% free pages)`
  - Files: `src/deepseek_cursor_proxy/reasoning_store.py`, `src/deepseek_cursor_proxy/server.py`


- [x] 4. Track DB size and row count in periodic heartbeat logs

  **What to do**:
  - In `server.py`, in `BoundedThreadPoolHTTPServer`, add a method to log DB stats as part of the existing periodic logging.
  - The existing heartbeat runs at lines 194-200:
    ```python
    if self.server.request_count % 500 == 0:
        LOG.info("heartbeat: processed %s requests", self.server.request_count)
    if self.server.request_count % 100 == 0:
        self.server._log_pool_utilization()
    ```
  - Add a third periodic check at every 500 requests (same as heartbeat):
    ```python
    if self.server.request_count % 500 == 0 and hasattr(self.server, 'reasoning_store'):
        self.server._log_db_stats()
    ```
  - Add `_log_db_stats()` method:
    ```python
    def _log_db_stats(self) -> None:
        try:
            store = self.reasoning_store
            if not isinstance(store.reasoning_content_path, Path):
                return  # :memory: DB
            size_mb = store.reasoning_content_path.stat().st_size / (1024 * 1024)
            row = store._conn.execute("SELECT COUNT(*) FROM reasoning_cache").fetchone()
            row_count = int(row[0]) if row else 0
            LOG.info(
                "db stats: %s size=%.1fMB rows=%s",
                store.reasoning_content_path, size_mb, format_count(row_count),
            )
        except Exception as exc:
            LOG.warning("failed to log DB stats: %s", exc)
    ```
  - If the DB file is deleted externally, catch the error gracefully.
  - For `:memory:` databases, skip the stat entirely.

  **Must NOT do**:
  - Do NOT stat the filesystem on every request (only at heartbeat intervals)
  - Do NOT create a new background thread just for this
  - Do NOT log DB stats for `:memory:` databases
  - Do NOT access private `_conn` attribute if avoidable — but since `ReasoningStore` doesn't expose a public row-count method, use it directly (same pattern as existing code)

  **Recommended Agent Profile**:
  - **Category**: `quick`
  - **Skills**: `[]`

  **Parallelization**: YES — Wave 2
  **Blocked By**: None

  **References**:
  - `src/deepseek_cursor_proxy/server.py:123-135` — `_log_pool_utilization()` — existing pattern
  - `src/deepseek_cursor_proxy/server.py:194-200` — `do_POST()` where heartbeat triggers live
  - `src/deepseek_cursor_proxy/reasoning_store.py:189` — `reasoning_content_path` attribute for path detection

  **Acceptance Criteria**:
  - [ ] DB size (in MB) and row count logged every 500 requests alongside existing heartbeat
  - [ ] Stat is on file-based DBs only — skip for `:memory:`
  - [ ] Log is at INFO level
  - [ ] Missing DB file is handled gracefully (log warning, don't crash)
  - [ ] External DB deletion doesn't crash the heartbeat

  **QA Scenarios**:
  ```
  Scenario: DB stats logged in heartbeat
    Tool: Bash
    Steps:
      1. TMPDIR=$(mktemp -d)
      2. timeout 3 uv run python -m deepseek_cursor_proxy.server --no-ngrok --port 19999 2>&1 | grep -E "db stats" | head -5
      (Note: may need 500+ requests to trigger; this is a smoke test of the code path)
    Expected Result: Eventually see "db stats:" lines with size and rows
    Evidence: .sisyphus/evidence/task-4-heartbeat.txt
  ```

  **Commit**: YES
  - Message: `feat(server): track DB size and row count in periodic heartbeat`
  - Files: `src/deepseek_cursor_proxy/server.py`


- [x] 7. Add tests for all new features

  **What to do**:
  - **In `tests/test_reasoning_store.py`**, add:
    - `test_auto_vacuum_on_new_file_db()` — Creates a file-based DB, verifies `PRAGMA auto_vacuum = 2`
    - `test_no_auto_vacuum_on_memory_db()` — Verifies `:memory:` DB doesn't set auto_vacuum
    - `test_vacuum_on_close_shrinks_file()` — Creates DB, inserts 50 rows with max_rows=10, verifies file shrinks after close
    - `test_incremental_vacuum_after_prune()` — Verify incremental_vacuum is called after prune (mock or check freelist_count change)
    - `test_check_bloat_detects_free_pages()` — Creates bloated DB, verify `check_bloat()` returns warning string
    - `test_check_bloat_healthy_db()` — Small DB, verify `check_bloat()` returns None
    - `test_check_bloat_memory_db()` — `:memory:` DB, verify `check_bloat()` returns None
  - **In `tests/test_config.py`**, update existing tests:
    - Find tests that assert `collasible_reasoning` in the template, update to `collapsible_reasoning`
    - Add `test_default_template_includes_log_dir()` — Verify template contains `log_dir`
    - Verify `test_config_parses_both_spellings()` — Already partially covered, ensure both old and new spelling work
  - **In `tests/test_server.py`**, add:
    - `test_startup_banner_includes_log_path()` (or test_startup_log_path_with_log_dir flag)
    - `test_heartbeat_includes_db_stats()` (or test that `_log_db_stats` method exists and works)
    - `test_no_log_path_when_log_dir_not_set()`
  - **Add a new small test** or verification script for version consistency:
    - In `tests/test_config.py` or a new test: verify `__version__` == pyproject.toml version string

  **Must NOT do**:
  - Do NOT add tests for streaming, transform, protocol, ngrok, CORS, etc. — already tested
  - Do NOT make real HTTP connections in tests
  - Do NOT remove or modify existing tests

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
  - **Skills**: `[]`
  - Reason: Multi-file test additions across 3 test files + coordination with implementation tasks

  **Parallelization**: NO (Wave 3 — after all implementation tasks)
  **Blocked By**: T1, T2, T3, T4, T5, T6 (tests verify implementation tasks)

  **References**:
  - `tests/test_reasoning_store.py` — Add vacuum/bloat tests here
  - `tests/test_config.py` — Update collapsible template tests, add log_dir test
  - `tests/test_server.py` — Add startup banner + heartbeat tests
  - `src/deepseek_cursor_proxy/reasoning_store.py` — Methods to test: `vacuum()`, `check_bloat()`, `close()`
  - `src/deepseek_cursor_proxy/server.py` — Features to test: banner log path, heartbeat DB stats
  - `src/deepseek_cursor_proxy/__init__.py` — Version to verify

  **Acceptance Criteria**:
  - [ ] All new tests pass: `uv run python -m unittest tests.test_reasoning_store tests.test_config tests.test_server -v`
  - [ ] All existing tests still pass: `uv run python -m unittest discover -s tests -v`
  - [ ] At least 6 new test cases added across the 3 test files
  - [ ] Version consistency verified in CI-compatible way

  **QA Scenarios**:
  ```
  Scenario: All tests pass
    Tool: Bash
    Steps:
      1. uv run python -m unittest discover -s tests -v 2>&1
    Expected Result: "OK" — zero failures
    Evidence: .sisyphus/evidence/task-7-all-tests-pass.txt
  ```

  **Commit**: YES
  - Message: `test: add tests for startup banner, vacuum, bloat, heartbeat, config, version`
  - Files: `tests/test_reasoning_store.py`, `tests/test_config.py`, `tests/test_server.py`
  - Pre-commit: `uv run python -m unittest discover -s tests -v`

---

## Final Verification Wave (MANDATORY — after ALL implementation tasks)

- [x] F1. **Plan Compliance Audit** — `oracle`
  Verify: auto_vacuum is set on new DBs, VACUUM runs on close, bloat warning fires, log path in banner, version matches, all acceptance criteria met. Check evidence files exist.
  Output: `VERDICT: APPROVE`

- [x] F2. **Code Quality Review** — `unspecified-high`
  Run full test suite, lint, check for `# type: ignore[attr-defined]` proliferation, unused imports, commented-out code.
  Output: `Build/Lint/Tests: PASS`

- [x] F3. **Real Manual QA** — `unspecified-high`
  Start from clean state. Create a synthetic bloated DB, verify warning fires. Start with --log-dir, verify path shown. Import and check version.
  Output: `Scenarios [6/6 pass]`

- [x] F4. **Scope Fidelity Check** — `deep`
  Verify: no schema changes, no API changes, no new deps, server_version unchanged, backward compat maintained.
  Output: `Tasks [8/8 compliant]`

---

## Commit Strategy

- **T1**: `feat(server): show log file path at end of startup banner`
- **T5**: `fix(config): update default template - fix collapsible typo, add log_dir`
- **T6**: `chore: sync __version__ to 0.1.1 matching pyproject.toml`
- **T8**: `feat(config): enable log_dir by default, add --no-log flag`
- **T9**: `ux(server): improve CLI startup banner with section layout and version header`
- **T2**: `fix(store): add auto_vacuum, close-time VACUUM, and incremental vacuum`
- **T3**: `feat(store): warn at startup if SQLite DB is bloated (>80% free pages)`
- **T4**: `feat(server): track DB size and row count in periodic heartbeat`
- **T7**: `test: add tests for startup banner, vacuum, bloat, heartbeat, config, version`

---

## Success Criteria

### Verification Commands
```bash
uv run python -m unittest discover -s tests -v   # All tests pass
python3 -c "from deepseek_cursor_proxy import __version__; print(__version__)"  # → "0.1.1"
```

### Final Checklist
- [ ] Log file path shown at END of startup with --log-dir
- [ ] New file DBs have auto_vacuum=2 (INCREMENTAL)
- [ ] Close-time VACUUM reclaims space (test: 50 inserts → before>after)
- [ ] Bloat warning fires for >80% free pages
- [ ] DB size logged in heartbeat (every 500 requests)
- [ ] Config template writes correct collapsible spelling
- [ ] Config template includes log_dir field
- [ ] Versions match (0.1.1)
- [ ] All existing tests still pass
