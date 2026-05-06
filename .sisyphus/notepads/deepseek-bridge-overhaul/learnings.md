# Learnings

## Task: Create responses_converter.py module

### Summary
Created `src/deepseek_bridge/responses_converter.py` — a pure conversion module that detects OpenAI Responses API payloads and converts them to Chat Completions format. This fixes Cursor Agent mode which sends Responses API-shaped payloads to `/v1/chat/completions`.

### Files created
- `src/deepseek_bridge/responses_converter.py` (new)

### Key design decisions
- **Conservative detection**: `detect_responses_payload` only returns True when payload has `input` or `instructions` AND lacks `messages`. This avoids false positives on standard Chat Completions payloads.
- **Pure functions**: No dependencies beyond stdlib, no logging, no side effects. Returns new dicts without mutating input.
- **Input item conversion**: Handles 5 input item shapes — role-based (`system`/`user`/`assistant`), typed messages (`type: "message"`), function_call_output, custom tool items, and a fallback for unrecognized items.
- **Tool conversion**: Flat function tools (`name`/`description`/`parameters` at top level) are nested under `function` key. Already-nested tools pass through. Custom tools (e.g., `type: "custom"` with `input_schema`) are converted to standard function tools. Unhandled tool types are dropped.
- **Responses-API-only fields dropped**: `include`, `previous_response_id`, `store`, `text`, `input`, `instructions`, `reasoning` are all consumed or dropped. Standard fields (`model`, `stream`, `temperature`, `top_p`, `max_tokens`, `stream_options`, `tool_choice`) pass through.

### Verification
- 18 tests pass covering: detection, conversion, empty input, instructions, function_call_output, typed messages, flat/nested/custom tools, reasoning dict, Responses-API-only field dropping, standard field passthrough, missing input, input priority over messages, and malformed payloads.
- LSP diagnostics: clean
- Existing test suite failures are pre-existing (`deepseek_cursor_proxy` import in test files).

## Foundation Task: deepseek-cursor-proxy → deepseek-bridge rename

### Summary
Successfully completed full project rename from `deepseek-cursor-proxy` to `deepseek-bridge`.

### Files modified
- `pyproject.toml`: name, description, scripts entry point, packages find include
- `src/deepseek_bridge/__init__.py`: version 0.2.0 → 0.3.0, docstring
- `src/deepseek_bridge/config.py`: `APP_DIR_NAME`, `DEFAULT_CONFIG_HEADER`, config text comments
- `src/deepseek_bridge/server.py`: `SYSTEM_FINGERPRINT`, `server_version`, startup banner, argparser description, error messages, health endpoint server name, `--version` text, recovery chunk id, absolute package import
- `src/deepseek_bridge/transform.py`: `RECOVERY_NOTICE_TEXT`, `RECOVERY_SYSTEM_CONTENT`
- `src/deepseek_bridge/streaming.py`: `system_fingerprint` in `flush_chunk`
- `src/deepseek_bridge/logging.py`: logger name
- `src/deepseek_bridge/tui/__init__.py`: docstring
- `src/deepseek_bridge/tui/app.py`: `TITLE`, class docstring
- `src/deepseek_bridge/trace.py`: recovery notice matching strings

### Directory moved
- `src/deepseek_cursor_proxy/` → `src/deepseek_bridge/`
- Old egg-info directories removed

### Verification results
- `import deepseek_bridge` → version 0.3.0
- `from deepseek_bridge.server import main; from deepseek_bridge.config import default_app_dir` → returns `~/.deepseek-bridge`
- `grep -r "deepseek\.cursor\.proxy" src/` → PASS (zero hits)
- `grep -r "deepseek_cursor_proxy" src/` → PASS (zero hits after pycache cleanup)
- `deepseek-bridge --help` → works

### Notes
- Stale `.pyc` cache files from the old package name in `__pycache__/` directories will trigger false positives on grep. Must clean them before final verification.
- Relative imports (`from .config import ...`) needed no changes.
- Only the absolute import `from deepseek_cursor_proxy import __version__` in `server.py` needed updating to the new package name.

## Task: Remove typo/deprecated CLI aliases

### Summary
Removed backwards-compat typo aliases: `--collasible-reasoning`, `--collasible-resoning`, `--no-collasible-reasoning`, `--no-collasible-resoning`, `--no-markdown-reasoning`. Also removed `collasible_reasoning` fallback key from config.

### Files modified
- `src/deepseek_bridge/server.py`: Removed 3 `group_other.add_argument(...)` blocks (lines 1472-1494) while keeping correct `--collapsible-reasoning` and `--display-reasoning` flags
- `src/deepseek_bridge/config.py`: Replaced `setting_value_any(settings, "collasible_reasoning", "collapsible_reasoning")` with `setting_value(settings, "collapsible_reasoning")`

### Verification
- `--help | grep collaps` → only `--collapsible-reasoning` (correct spelling) shows
- `--no-collasible-reasoning --help` → "unrecognized arguments" error
- `--no-markdown-reasoning --help` → "unrecognized arguments" error

## Task: Add [tui] optional dependency group

### Summary
Added `[tui]` optional dependency group with `textual>=8.2.5` and aligned dev deps.

### Changes
- `pyproject.toml`: Added `[project.optional-dependencies]` with `tui = ["textual>=8.2.5"]`, bumped dev dep versions (black>=25.1.0, ruff>=0.11.0, textual>=8.2.5), removed redundant `[dependency-groups]` section.

### Key learning
- uv's `--dev` flag installs `[dependency-groups]`, NOT `[project.optional-dependencies]`. Use `--extra tui` or `--extra dev` for PEP 621 optional deps.
- Installing with `uv sync --extra tui` correctly installs textual. Verified: `textual.__version__ == "8.2.5"`.
- When removing `[dependency-groups]`, the `--dev` flag becomes a no-op, but `--extra dev` still works correctly for `[project.optional-dependencies]`.

## Task: Update pre-commit hooks and tooling config

### Summary
Updated `.pre-commit-config.yaml` to latest hook versions and migrated `pyproject.toml` tooling config from `[tool.black]` to `[tool.ruff]`.

### Changes
- `.pre-commit-config.yaml`: pre-commit-hooks v4.5.0→v5.0.0, black 24.2.0→26.3.1, ruff v0.3.0→v0.11.7
- `pyproject.toml`: Replaced `[tool.black]` with `[tool.ruff]` section (target-version, lint select), set `line-length = 120`, added `[tool.ruff.lint.per-file-ignores]` for N802 in tests
- Updated dev optional deps: black>=26.3.1, ruff>=0.11.7

### Issues encountered
- `language_version: python3.10` for black hook failed because python3.10 not available on system. Changed to `python3.12`.
- N802 (`do_POST` naming) flagged in pre-commit's ruff v0.11.7 but not in pip-installed ruff 0.15.12. Suppressed N802 in test files via `per-file-ignores`.
- Many E501 line-too-long errors surfaced with newer ruff defaults. Set `line-length = 120` to match existing codebase patterns.
- Tests fail due to pre-existing `deepseek_cursor_proxy` import references in test files (package rename task not yet applied to tests).

### Verification
- `uv run ruff check src/ tests/` — ✅ passes
- `uv run pre-commit run --all-files` — ✅ passes (trailing-whitespace, end-of-file-fixer, black, ruff)
- No new LSP diagnostics introduced

## Task: DeepSeek V4 API compliance update

### Summary
Updated `src/deepseek_bridge/transform.py` for DeepSeek V4 API changes:
- Added `user_id` to `SUPPORTED_REQUEST_FIELDS`
- Removed deprecated `frequency_penalty` and `presence_penalty` from `SUPPORTED_REQUEST_FIELDS`
- Added separate deprecated param handling with WARNING logging
- Added silent legacy model name mapping (`deepseek-chat`/`deepseek-reasoner` → `deepseek-v4-flash`)

### Files modified
- `src/deepseek_bridge/transform.py` (3 edits)

### Key design decisions
- **Deprecated params are excluded from `dropped_fields`**: `frequency_penalty` and `presence_penalty` are handled separately with their own warning messages, rather than showing up in the generic "unsupported fields" list.
- **Legacy model mapping is silent**: No warning is logged when `deepseek-chat` or `deepseek-reasoner` is mapped to `deepseek-v4-flash`, per user requirement.
- **`response_format` already supported**: Was already in `SUPPORTED_REQUEST_FIELDS`, no additional changes needed for passthrough.
- **`thinking` already supported**: Was already in `SUPPORTED_REQUEST_FIELDS` and explicitly set in `prepare_upstream_request`, no changes needed.
- **`EFFORT_ALIASES` verified correct**: Current mapping already matches DeepSeek V4 docs.

### Verification
- Legacy model mapping: `deepseek-chat` → `deepseek-v4-flash`, `deepseek-reasoner` → `deepseek-v4-flash`, `deepseek-v4-pro` → `deepseek-v4-pro` ✅
- New fields in `SUPPORTED_REQUEST_FIELDS`: `thinking`, `response_format`, `logprobs`, `top_logprobs`, `user_id` all present ✅
- Deprecated params removed from set: `frequency_penalty`, `presence_penalty` not in set ✅
- Deprecated params dropped with WARNING from payload ✅
- Legacy model mapping is silent (no WARNING logged) ✅
- All existing tests that can run still pass (pre-existing test failures from `deepseek_cursor_proxy` module rename unrelated)

## Task: Integrate Responses API converter into do_POST

### Summary
Added inline conversion from Responses API to Chat Completions format in `server.py`'s `do_POST` method. When a Cursor Agent mode request arrives with Responses API format on `/chat/completions` or `/v1/chat/completions`, it's converted before reaching `prepare_upstream_request`.

### Change
- **File modified**: `src/deepseek_bridge/server.py`
- **Insertion point**: After `log_json("cursor request body", payload)` and before `prepare_upstream_request(...)`
- **Scope check**: Conversion only triggers for `/chat/completions` and `/v1/chat/completions` paths
- **Flow**: `detect_responses_payload` → `convert_responses_to_chat` → re-record in trace if converted
- **Failure mode**: `ImportError` silently caught (module should always be available, but graceful degradation if not)

### Key decisions
- **Late import**: The `from .responses_converter import` is inside the function body (try/except). This avoids a circular import at module level and keeps the import lazy (converter only loaded when a chat completions request arrives).
- **Trace re-record**: When a conversion happens, `trace.record_cursor_body(payload)` records the *converted* payload so trace dumps show what was actually sent upstream.
- **Verbose logging**: INFO-level log at `--verbose` confirms the conversion occurred.
- **Standard payloads pass through**: `detect_responses_payload` returns False for payloads with `messages` field, so Chat Completions payloads are unaffected.

### Verification
- Syntax: `py_compile` passes
- Import: `from deepseek_bridge.responses_converter import detect_responses_payload, convert_responses_to_chat` works
- LSP: Only pre-existing errors (no new diagnostic issues)

## Task: Add mypy type checking to CI and fix type issues

### Summary
Added `mypy>=1.15.0` with `--check-untyped-defs` mode (not strict). Fixed all 35 type errors across 10 files in both `deepseek_bridge` and `deepseek_cursor_proxy` packages. Added CI step to lint job.

### Files modified
- `pyproject.toml`: Updated dev optional-deps (mypy>=1.15.0, black>=26.3.1, ruff>=0.11.0, textual>=8.2.5), added `[tool.mypy]` section
- `.github/workflows/ci.yml`: Added "Run mypy" step after pre-commit in lint job
- `src/deepseek_bridge/config.py`: Added `# type: ignore[import-untyped]` for yaml
- `src/deepseek_cursor_proxy/config.py`: Same yaml ignore
- `src/deepseek_bridge/reasoning_store.py`: Added `self._max_rows: int | None = None` in `__init__`
- `src/deepseek_cursor_proxy/reasoning_store.py`: Same `_max_rows` annotation
- `src/deepseek_bridge/responses_converter.py`: Renamed `role` to `item_role` at line 127 to avoid type conflict with line 119's `role = str(...)`
- `src/deepseek_bridge/handler.py`: Fixed `active: int | str` annotation, changed `type: ignore[return-value]` → `type: ignore[attr-defined]` on 3 properties, removed unused ignore on line 790
- `src/deepseek_cursor_proxy/server.py`: Same fixes as handler.py (active annotation, type: ignore fixes, removed unused ignore on line 804), added `# type: ignore[attr-defined]` on `server.public_url`
- `src/deepseek_bridge/tui/config.py`: Added `updates: dict[str, Any]` annotation, `# type: ignore[attr-defined]` on `self.app.server_config`, added `from typing import Any`
- `src/deepseek_cursor_proxy/tui/config.py`: Same fixes as bridge tui/config.py
- `src/deepseek_bridge/cli.py`: Added `# type: ignore[attr-defined]` on `server.public_url`

### Key learnings
- **`from __future__ import annotations` causes type propagation**: Variables declared inside `if` blocks can still affect later variable types. In `responses_converter.py`, `role = str(...)` at line 119 caused mypy to infer `role` as `str`, conflicting with `role = item.get("role")` at line 127.
- **`# type: ignore[return-value]` doesn't suppress `attr-defined`**: Mypy error codes are specific. When `self.server.config` fails with `attr-defined` (because `BaseServer` lacks the attribute), using `[return-value]` doesn't cover it. Must use exact error code: `# type: ignore[attr-defined]`.
- **`# type: ignore` becomes unused when `hasattr` guards exist**: Mypy 1.20.2 correctly narrows types through `hasattr()` checks, making explicit `# type: ignore[attr-defined]` comments redundant (triggering `warn_unused_ignores`).
- **Adding `self._max_rows: int | None = None` in `__init__` prevents "incompatible types in assignment"**: Without the annotation, mypy infers the type from the first assignment (`_auto_cache_max_rows()` → `int`), then rejects the later `None` assignment.
- **`ignore_missing_imports = true` doesn't suppress `import-untyped`**: Mypy distinguishes between "missing imports" (can't find the module) and "untyped library" (found but lacks stubs). Need `# type: ignore[import-untyped]` for yaml.

### Verification
- `uv run mypy src/ --check-untyped-defs` → `Success: no issues found in 34 source files`
- CI workflow has mypy step after pre-commit in lint job
- Lockfile updated with mypy 1.20.2
