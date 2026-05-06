# DeepSeek Bridge — Project Overhaul Plan

## TL;DR

> **Quick Summary**: Comprehensive overhaul of `deepseek-cursor-proxy` → `deepseek-bridge` including rename, architecture split, Cursor Responses API converter, DeepSeek V4 API compliance update, tooling/modernization, CI overhaul, and cleanup of legacy/typo aliases.
>
> **Deliverables**:
> - Renamed project: `deepseek-bridge` (package, CLI, config dir, all references)
> - Split `server.py` (~2013 lines) into `handler.py` + `cli.py` + `helpers.py`
> - Cursor Agent mode Responses API → Chat Completions format converter
> - DeepSeek V4 API compliance: `thinking` object param, `reasoning_effort` mapping, `response_format`, `logprobs`, deprecated param handling, legacy model mapping
> - Clean CLI with no typo aliases (removed `--collasible-reasoning`, `--no-markdown-reasoning`)
> - Updated pre-commit hooks (black, ruff, pre-commit-hooks to latest)
> - `[tui]` optional dependency group for textual
> - CI with mypy, coverage reporting, Windows testing
> - Fixed anti-pattern tests (replace `inspect.getsource`)
>
> **Estimated Effort**: Large
> **Parallel Execution**: YES — 4 waves
> **Critical Path**: Wave 1 (Foundation: rename + scaffolding) → Wave 2 (Core features: converters + DeepSeek update) → Wave 3 (Refactor: server split + tests) → Wave 4 (CI + polish)

---

## Context

### Original Request
[The user said]: "Please check the whole project including readme for cleanup, improvements, getting rid of legacy and backwards compatible stuff, see if arch can be improved, rename it too since its not just for cursor now."

### Interview Summary
**Key Decisions**:
- **Rename**: `deepseek-cursor-proxy` → `deepseek-bridge` (package, CLI, config dir `~/.deepseek-bridge/`)
- **Architecture**: Split `server.py` into `handler.py`, `cli.py`, `helpers.py`
- **Legacy**: No backwards compat — remove typo aliases, deprecated aliases, no migration for old config dir
- **Responses API converter**: Add bidirectional format conversion for Cursor Agent mode
- **DeepSeek API**: Full update including new `thinking` param, `reasoning_effort` mapping, legacy model mapping
- **Ollama endpoints**: Maintain both (Ollama + customOAIModels)
- **Hooks**: Update all pre-commit hooks to latest
- **TUI deps**: Add `[tui]` extra with textual
- **CI**: Full overhaul — mypy (`--check-untyped-defs`), coverage, Windows testing, uv update
- **Test fix**: Replace `inspect.getsource` anti-pattern with behavioral test

**Research Findings**:
- DeepSeek-V4 launched April 24, 2026 with new thinking param format
- Cursor Agent mode sends OpenAI Responses API format to `/v1/chat/completions` (confirmed community bug, no Cursor-side fix)
- Copilot BYOK GA'd April 22, 2026 — CustomOAIModels config is the future
- Legacy models `deepseek-chat`/`deepseek-reasoner` deprecated July 24, 2026
- `holo-q/deepseek-responses-proxy` is a reference implementation for Responses→Chat conversion
- Pre-commit hooks very outdated (ruff v0.3.0, black 24.2.0)
- No mypy, coverage, or Windows testing in CI

### Metis Review
**Identified Gaps** (addressed):
- Config migration: No migration — old `~/.deepseek-cursor-proxy/` is simply abandoned (user's explicit choice)
- Responses API spec: Researched from forum threads and reference implementations
- Mypy strictness: `--check-untyped-defs` (user's choice)
- `--no-markdown-reasoning` alias fate: Removed alongside typo aliases (user's choice)
- Ollama endpoints: Maintained alongside customOAIModels support (user's choice)

---

## Work Objectives

### Core Objective
Transform `deepseek-cursor-proxy` into `deepseek-bridge` — a modern, maintainable, generically-named proxy for DeepSeek reasoning models that works with Cursor, Copilot, Codex, and any OpenAI-compatible client.

### Concrete Deliverables
- Package renamed from `deepseek-cursor-proxy` to `deepseek-bridge`
- Python package renamed from `deepseek_cursor_proxy` to `deepseek_bridge`
- CLI entry point: `deepseek-cursor-proxy` → `deepseek-bridge`
- Config dir: `~/.deepseek-cursor-proxy/` → `~/.deepseek-bridge/`
- `server.py` split into `handler.py`, `cli.py`, `helpers.py`
- New Responses API converter module
- Updated DeepSeek API compliance
- Clean CLI with correct spelling
- Updated pre-commit hooks and CI
- Fixed anti-pattern tests

### Definition of Done
- [x] `grep -r "deepseek.cursor.proxy" src/ tests/ pyproject.toml` returns zero hits
- [x] `grep -r "deepseek_cursor_proxy" src/ tests/` returns zero hits
- [x] `pip install -e ".[tui]"` installs cleanly with textual available
- [x] All 128+ existing tests pass without modification
- [x] New Responses API converter tests pass
- [x] New DeepSeek V4 API compliance tests pass
- [x] `uv run pre-commit run --all-files` passes
- [x] `uv run mypy src/ --check-untyped-defs` passes
- [x] `uv run coverage run -m unittest discover -s tests -v && uv run coverage report` reports ≥90%

### Must Have
- Complete rename: all references from `deepseek-cursor-proxy`/`deepseek_cursor_proxy` to `deepseek-bridge`/`deepseek_bridge`
- No backwards compat: old CLI aliases removed, old config dir not migrated
- Correct DeepSeek V4 API handling: `thinking` object, `reasoning_effort`, `response_format`
- Core proxy functionality preserved: reasoning_content fix, streaming, caching, Ollama endpoints
- All existing tests pass
- Pre-commit hooks updated
- CI passes on all matrix targets (Linux, macOS, Windows)

### Must NOT Have (Guardrails)
- No config migration from old `~/.deepseek-cursor-proxy/` — old dir is simply ignored
- No new features beyond the explicitly listed scope
- No changes to public API behavior (same endpoints, same response shapes where possible)
- No emoji in code or documentation
- No new dependencies beyond what's required

---

## Verification Strategy

> **ZERO HUMAN INTERVENTION** — ALL verification is agent-executed.

### Test Decision
- **Infrastructure exists**: YES (unittest)
- **Automated tests**: Tests-after (new features get test tasks alongside implementation)
- **Framework**: unittest (existing) + coverage.py (new) + mypy (new)
- **Existing test count**: ~128 tests across 11 files

### QA Policy
Every task MUST include agent-executed QA scenarios. Evidence saved to `.sisyphus/evidence/task-{N}-{scenario-slug}.{ext}`.

- **Module/library tests**: Use `python -m unittest` — import, call functions, verify output
- **CLI tests**: Use bash — run `deepseek-bridge --help`, verify version output
- **Renamed import tests**: Use `bash` — `python -c "import deepseek_bridge"` succeeds
- **Build/setup tests**: Use `uv pip install -e ".[tui]"` then verify textual importable

---

## Execution Strategy

### Parallel Execution Waves

```
Wave 1 (Foundation — MAX PARALLEL):
├── Task 1: Rename project (pyproject.toml, package dir, CLI entry, config dir) [quick]
├── Task 2: Update README and all documentation [quick]
├── Task 3: Update all internal imports and references [quick]
├── Task 4: Update pre-commit hooks + config [quick]
├── Task 5: Add [tui] optional dependency [quick]
└── Task 6: Remove typo/deprecated CLI aliases [quick]

Wave 2 (Core Features — MAX PARALLEL):
├── Task 7: Add Responses API → Chat Completions converter [deep]
├── Task 8: Update DeepSeek API compliance [deep]
├── Task 9: Add legacy model name mapping [quick]
├── Task 10: Update Ollama endpoint capabilities [unspecified-high]
└── Task 11: Responses API integration into proxy handler [deep]

Wave 3 (Refactoring — MAX PARALLEL):
├── Task 12: Split server.py into handler.py + cli.py + helpers.py [deep]
├── Task 13: Fix anti-pattern tests (inspect.getsource) [quick]
├── Task 14: Update test imports and add new tests [unspecified-high]
└── Task 15: Add new converter + DeepSeek API tests [unspecified-high]

Wave 4 (CI + Polish):
├── Task 16: Add mypy type checking to CI [unspecified-high]
├── Task 17: Add coverage reporting to CI [quick]
├── Task 18: Add Windows testing to CI [quick]
├── Task 19: Update uv version pin in CI [quick]
└── Task 20: Update .gitignore for new name [quick]

Wave FINAL:
├── Task F1: Plan compliance audit (oracle)
├── Task F2: Code quality and type check (unspecified-high)
├── Task F3: Full test suite execution (unspecified-high)
└── Task F4: Scope fidelity check (deep)

Critical Path: Task 1 → Task 3 → Task 7 → Task 8 → Task 11 → Task 12 → Task 15 → F1-F4 → user okay
Parallel Speedup: ~65% faster than sequential
Max Concurrent: 6 (Wave 1 + Wave 2 overlap after Wave 1 foundation)
```

### Dependency Matrix
- **1-6**: Foundation tasks, all parallel (block 7-20)
- **7**: Depends on 1-3 (block 11, 15)
- **8**: Depends on 1-3 (block 11, 15)
- **9**: Depends on 1-3 (block 8 in parallel with 7)
- **10**: Depends on 1-3
- **11**: Depends on 7, 8 (block 12, 15)
- **12**: Depends on 1-3, 6 (block 13, 14, 15)
- **13-15**: Depends on 12
- **16-20**: Depends on 1, 3

---

## TODOs

---

- [x] 1. Rename project: pyproject.toml, package dir, CLI, config dir

  **What to do**:
  - Rename package in `pyproject.toml` from `deepseek-cursor-proxy` to `deepseek-bridge`
  - Update description, keywords, classifiers
  - Update `[project.scripts]` entry point from `deepseek-cursor-proxy` to `deepseek-bridge`
  - Move `src/deepseek_cursor_proxy/` → `src/deepseek_bridge/`
  - Update `[tool.setuptools.packages.find]` include pattern
  - Rename config dir constant `APP_DIR_NAME` in `config.py` from `.deepseek-cursor-proxy` to `.deepseek-bridge`
  - Update `DEFAULT_CONFIG_HEADER` text to reference `deepseek-bridge`
  - Update `version` in `__init__.py` to `0.3.0` (major rename)
  - Update `server_version` in `server.py` from `DeepSeekPythonProxy/0.1` to `DeepSeekBridge/0.1`
  - Update `__init__.py` docstring
  - Update `SYSTEM_FINGERPRINT` constant
  - Update `RECOVERY_NOTICE_TEXT` and `RECOVERY_SYSTEM_CONTENT` in `transform.py` to reference `deepseek-bridge`
  - Update startup banner in `server.py:main()` to show `DeepSeek Bridge`
  - Update `TuiApp.TITLE` in `tui/app.py` to `DeepSeek Bridge`

  **Must NOT do**:
  - No config migration from old `~/.deepseek-cursor-proxy/`
  - No changes to supported API endpoints or response format

  **Recommended Agent Profile**:
  - **Category**: `quick`
    - Reason: Many small find-and-replace operations across files
  - **Skills**: []
  - **Skills Evaluated but Omitted**: All — this is a straightforward rename

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 1 (with Tasks 2-6)
  - **Blocks**: Tasks 7-20
  - **Blocked By**: None (foundation task)

  **References**:
  - `pyproject.toml:1-57` — All package metadata to rename
  - `src/deepseek_cursor_proxy/__init__.py:1-5` — Module docstring and version
  - `src/deepseek_cursor_proxy/config.py:10-12` — `APP_DIR_NAME` constant
  - `src/deepseek_cursor_proxy/server.py:46` — `SYSTEM_FINGERPRINT` constant
  - `src/deepseek_cursor_proxy/server.py:180` — `server_version` string
  - `src/deepseek_cursor_proxy/transform.py:95-102` — Recovery notice text
  - `src/deepseek_cursor_proxy/tui/app.py:17` — TUI title

  **Acceptance Criteria**:

  **QA Scenarios**:
  ```
  Scenario: Package installs and imports correctly
    Tool: Bash
    Preconditions: uv sync completed
    Steps:
      1. uv run python -c "import deepseek_bridge; print(deepseek_bridge.__version__)"
      2. uv run python -c "from deepseek_bridge.server import main"
      3. uv run python -c "from deepseek_bridge.config import default_app_dir; print(default_app_dir())"
    Expected Result: All imports succeed. `default_app_dir()` returns path ending in `.deepseek-bridge`
    Evidence: .sisyphus/evidence/task-1-imports.txt

  Scenario: CLI entry point works
    Tool: Bash
    Steps:
      1. uv run deepseek-bridge --help
    Expected Result: Shows help text with `deepseek-bridge` as the program name
    Evidence: .sisyphus/evidence/task-1-cli-help.txt

  Scenario: No old name remains in source
    Tool: Bash
    Steps:
      1. grep -r "deepseek.cursor.proxy" src/ || true
      2. grep -r "deepseek_cursor_proxy" src/ || true
    Expected Result: Zero hits for old name patterns
    Evidence: .sisyphus/evidence/task-1-grep-clean.txt
  ```

  **Evidence to Capture**:
  - [ ] `.sisyphus/evidence/task-1-imports.txt`
  - [ ] `.sisyphus/evidence/task-1-cli-help.txt`
  - [ ] `.sisyphus/evidence/task-1-grep-clean.txt`

  **Commit**: YES
  - Message: `chore: rename deepseek-cursor-proxy to deepseek-bridge`
  - Files: `pyproject.toml`, `src/deepseek_cursor_proxy/*`, `src/deepseek_bridge/*`

---

- [x] 2. Update README and all documentation

  **What to do**:
  - Update `README.md` header, description, and all references from `deepseek-cursor-proxy` to `deepseek-bridge`
  - Make README more generic (not Cursor-focused) — lead with "a local proxy that connects AI coding tools to DeepSeek thinking models"
  - Add Copilot `customOAIModels` setup guide (the new VS Code GA'd feature)
  - Update install commands: `uv run deepseek-bridge`, `deepseek-bridge --help`
  - Update config file paths from `~/.deepseek-cursor-proxy/` to `~/.deepseek-bridge/`
  - Add note about Cursor Agent mode Responses API format (known limitation with workaround)
  - Remove `--collasible-reasoning` from CLI reference table
  - Remove `--no-markdown-reasoning` from CLI reference table
  - Add `--headless` flag to CLI reference table
  - Add new `[tui]` install option
  - Update `PR_MESSAGE.md` or leave as historical artifact
  - Update `assets/logo.svg` and `assets/logo.png` if they contain "Cursor" branding — change to "DeepSeek Bridge"

  **Must NOT do**:
  - Don't add overly verbose documentation sections
  - Don't use emoji

  **Recommended Agent Profile**:
  - **Category**: `writing`
    - Reason: Documentation update requiring clear, professional prose
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 1 (with Tasks 1, 3-6)
  - **Blocks**: Nothing directly
  - **Blocked By**: Task 1 (renaming must be decided first)

  **References**:
  - `README.md` — Full file (294 lines)
  - `PR_MESSAGE.md` — Historical artifact
  - `assets/logo.svg`, `assets/logo.png` — Branding assets

  **Acceptance Criteria**:

  **QA Scenarios**:
  ```
  Scenario: README shows new name and install instructions
    Tool: Bash
    Steps:
      1. grep "deepseek-bridge" README.md | head -5
      2. grep -c "deepseek-cursor-proxy" README.md
    Expected Result: README uses `deepseek-bridge` throughout. Zero or minimal old-name references (only in historical context).
    Evidence: .sisyphus/evidence/task-2-readme-check.txt
  ```

  **Evidence to Capture**:
  - [ ] `.sisyphus/evidence/task-2-readme-check.txt`

  **Commit**: YES (groups with Task 1)
  - Message: `chore: rename deepseek-cursor-proxy to deepseek-bridge`
  - Files: `README.md`, `assets/*`

---

- [x] 3. Update all internal imports and references

  **What to do**:
  - Update ALL internal imports across all Python files: `from deepseek_cursor_proxy.` → `from deepseek_bridge.`
  - Files to update:
    - `src/deepseek_bridge/server.py` (all internal imports)
    - `src/deepseek_bridge/config.py` (no internal imports, but check)
    - `src/deepseek_bridge/transform.py` (internal imports)
    - `src/deepseek_bridge/streaming.py` (internal imports)
    - `src/deepseek_bridge/tunnel.py` (internal imports)
    - `src/deepseek_bridge/logging.py` (no internal imports)
    - `src/deepseek_bridge/trace.py` (no internal imports)
    - `src/deepseek_bridge/reasoning_store.py` (no internal imports, but has lazy import)
    - `src/deepseek_bridge/tui/__init__.py` (import)
    - `src/deepseek_bridge/tui/app.py` (internal imports)
    - `src/deepseek_bridge/tui/dashboard.py` (no internal imports)
    - `src/deepseek_bridge/tui/config.py` (no internal imports)
    - `src/deepseek_bridge/tui/logs.py` (no internal imports)
    - `src/deepseek_bridge/__main__.py` (import)
    - `src/deepseek_bridge/__init__.py` (version)
  - Update `setup.py`/`pyproject.toml` package discovery if needed
  - Update `.github/workflows/ci.yml` if it references the package name
  - Update `reasoning_store.py:221` lazy import: `from .config import _auto_cache_max_rows`

  **Must NOT do**:
  - Do NOT change import paths in tests yet (handled in Wave 3)
  - Do NOT change any functionality

  **Recommended Agent Profile**:
  - **Category**: `quick`
    - Reason: Systematic find-and-replace across all source files
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES (if tasks 1 and 3 are done by same agent or coordinated)

  **NOTE**: Task 1 already moves the directory and renames files. This task handles the file content updates.

  **Acceptance Criteria**:

  **QA Scenarios**:
  ```
  Scenario: All internal imports resolve correctly
    Tool: Bash
    Steps:
      1. uv run python -c "import deepseek_bridge; import deepseek_bridge.server; import deepseek_bridge.config; import deepseek_bridge.transform; import deepseek_bridge.streaming; import deepseek_bridge.tunnel; import deepseek_bridge.logging; import deepseek_bridge.trace; import deepseek_bridge.reasoning_store; import deepseek_bridge.tui"
    Expected Result: All imports succeed without ImportError
    Evidence: .sisyphus/evidence/task-3-all-imports.txt

  Scenario: Lazy import in reasoning_store works
    Tool: Bash
    Steps:
      1. uv run python -c "from deepseek_bridge.reasoning_store import ReasoningStore; store = ReasoningStore(':memory:'); print('OK')"
    Expected Result: Store initializes without import errors
    Evidence: .sisyphus/evidence/task-3-lazy-import.txt
  ```

  **Evidence to Capture**:
  - [ ] `.sisyphus/evidence/task-3-all-imports.txt`
  - [ ] `.sisyphus/evidence/task-3-lazy-import.txt`

  **Commit**: YES (groups with Task 1)

---

- [x] 4. Update pre-commit hooks + config

  **What to do**:
  - Update `.pre-commit-config.yaml`:
    - `pre-commit-hooks` from `v4.5.0` to latest (currently `v5.0.0`)
    - `black` from `24.2.0` to latest (currently `25.1.0`)
    - `ruff` from `v0.3.0` to latest (currently `v0.11.x`)
    - Update `language_version` if needed
  - Update `pyproject.toml`:
    - Remove redundant `[tool.black]` section (black now uses pyproject.toml defaults)
    - Add `[tool.ruff]` config section with appropriate rules
    - Consider adding `target-version = "py310"` to ruff config
  - Run `uv sync --dev` to update tooling
  - Run `uv run pre-commit run --all-files`, fix any new issues (ruff may catch more things with the update)
  - Format code as needed for new black version

  **Must NOT do**:
  - Don't add too many new ruff rules — stick with the existing set plus any critical new ones
  - Don't change the overall code style beyond what the updated tools enforce

  **Recommended Agent Profile**:
  - **Category**: `quick`
    - Reason: Straightforward version bumps and config updates
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 1 (with Tasks 1-3, 5-6)
  - **Blocks**: Wave 4 tasks (which rely on updated tooling)
  - **Blocked By**: None

  **References**:
  - `.pre-commit-config.yaml:1-20` — Current pre-commit config
  - `pyproject.toml:51-53` — Current `[tool.black]` section

  **Acceptance Criteria**:

  **QA Scenarios**:
  ```
  Scenario: Pre-commit hooks pass on all files
    Tool: Bash
    Steps:
      1. uv run pre-commit run --all-files
    Expected Result: Exit code 0. All hooks pass (trailing-whitespace, end-of-file-fixer, black, ruff).
    Evidence: .sisyphus/evidence/task-4-pre-commit-pass.txt

  Scenario: ruff config works with new version
    Tool: Bash
    Steps:
      1. uv run ruff check src/ tests/
    Expected Result: No errors (or only pre-existing ones that are acceptable)
    Evidence: .sisyphus/evidence/task-4-ruff-check.txt
  ```

  **Evidence to Capture**:
  - [ ] `.sisyphus/evidence/task-4-pre-commit-pass.txt`
  - [ ] `.sisyphus/evidence/task-4-ruff-check.txt`

  **Commit**: YES
  - Message: `chore: update pre-commit hooks and tooling config`
  - Files: `.pre-commit-config.yaml`, `pyproject.toml`

---

- [x] 5. Add [tui] optional dependency group

  **What to do**:
  - In `pyproject.toml`, add a `[project.optional-dependencies]` group named `tui`:
    ```toml
    [project.optional-dependencies]
    tui = ["textual>=8.2.5"]
    dev = [
        "black==24.2.0",
        "pre-commit>=3.0.0",
        "ruff>=0.3.0",
        "textual>=0.50.0",
    ]
    ```
  - Update the dev group to remove `textual>=0.50.0` (or keep it since dev should include TUI for contributors)
    - Actually keep `textual` in dev too but bump the version to match `>=8.2.5`
  - Clean up the redundant `[dependency-groups] dev` section — either keep it for uv or align it with optional-deps
  - Update `README.md` install section to document `pip install deepseek-bridge[tui]`

  **Must NOT do**:
  - Don't add `textual` as a hard dependency (it's a heavy dependency, optional is correct)
  - Don't break the existing import fallback in `server.py:1977` (try/except for TUI import)

  **Recommended Agent Profile**:
  - **Category**: `quick`
    - Reason: Small, focused config change
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 1 (with Tasks 1-4, 6)
  - **Blocks**: Nothing
  - **Blocked By**: Task 1 (rename must be done first for package name)

  **References**:
  - `pyproject.toml:33-39` — Current optional deps
  - `pyproject.toml:54-56` — Current dependency-groups
  - `src/deepseek_bridge/server.py:1977` — TUI import fallback

  **Acceptance Criteria**:

  **QA Scenarios**:
  ```
  Scenario: TUI extra installs correctly
    Tool: Bash
    Steps:
      1. uv pip install -e ".[tui]" 2>&1 || pip install -e ".[tui]" 2>&1
      2. python -c "import textual; print(textual.__version__)"
    Expected Result: textual is importable and version >= 8.2.5
    Evidence: .sisyphus/evidence/task-5-tui-install.txt
  ```

  **Evidence to Capture**:
  - [ ] `.sisyphus/evidence/task-5-tui-install.txt`

  **Commit**: YES
  - Message: `feat: add [tui] optional dependency group for textual dashboard`
  - Files: `pyproject.toml`

---

- [x] 6. Remove typo/deprecated CLI aliases

  **What to do**:
  - Remove `--collasible-reasoning` alias (lines 1473-1478 in server.py)
  - Remove `--collasible-resoning` alias (double typo)
  - Remove `--no-collasible-reasoning` alias
  - Remove `--no-collasible-resoning` alias
  - Remove `--no-markdown-reasoning` deprecated alias (lines 1489-1494 in server.py)
  - Remove `collasible_reasoning` fallback key handling in config.py:311
  - Update the official `--collapsible-reasoning` flag to be the canonical name (verify it's already correct)
  - Update any tests that reference the old alias names
  - Update README CLI reference table

  **Must NOT do**:
  - Keep `--collapsible-reasoning` (correct spelling) working as the canonical flag
  - Keep `--display-reasoning` as the main toggle

  **Recommended Agent Profile**:
  - **Category**: `quick`
    - Reason: Targeted removals in a few files
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 1 (with Tasks 1-5)
  - **Blocks**: Task 12 (server.py split needs clean CLI)
  - **Blocked By**: Task 1 (file paths must be correct)

  **References**:
  - `server.py:1473-1494` — Typo/deprecated alias definitions
  - `config.py:311` — `collasible_reasoning` fallback key
  - `README.md` — CLI reference table

  **Acceptance Criteria**:

  **QA Scenarios**:
  ```
  Scenario: Old aliases are rejected
    Tool: Bash
    Steps:
      1. uv run deepseek-bridge --no-collasible-reasoning --help 2>&1 || true
      2. uv run deepseek-bridge --no-markdown-reasoning --help 2>&1 || true
    Expected Result: Both return non-zero exit or print "unrecognized arguments" error
    Evidence: .sisyphus/evidence/task-6-old-aliases-rejected.txt

  Scenario: Correct alias still works
    Tool: Bash
    Steps:
      1. uv run deepseek-bridge --help 2>&1 | grep -i "collaps"
    Expected Result: Shows `--collapsible-reasoning` in help output
    Evidence: .sisyphus/evidence/task-6-correct-alias-works.txt
  ```

  **Evidence to Capture**:
  - [ ] `.sisyphus/evidence/task-6-old-aliases-rejected.txt`
  - [ ] `.sisyphus/evidence/task-6-correct-alias-works.txt`

  **Commit**: YES
  - Message: `refactor: remove deprecated CLI aliases --collasible-reasoning and --no-markdown-reasoning`
  - Files: `src/deepseek_bridge/server.py`, `src/deepseek_bridge/config.py`, `README.md`

---

### Wave 2 — Core Features

- [x] 7. Add Responses API → Chat Completions converter

  **What to do**:
  - Create new module `src/deepseek_bridge/responses_converter.py`
  - Implement `detect_responses_payload(payload: dict) -> bool` that checks for Responses API format indicators:
    - Presence of `"input"` field (instead of `"messages"`)
    - Presence of `"instructions"` field
    - Flat tool format `{"type": "function", "name": "..."}` (no nested `"function"` key)
  - Implement `convert_responses_to_chat(payload: dict) -> dict` that converts:
    - `input` array → `messages` array (with role mapping: `{"role": "system"}` items are mapped, `{"type": "message", "role": "user"}` → standard message, `{"type": "function_call_output"}` → tool role message)
    - `instructions` → system message prepended to messages
    - Flat tools `{"type": "function", "name": "X", "input": {...}}` → nested `{"type": "function", "function": {"name": "X", "parameters": {...}}}`
    - Custom/freeform tools (like Codex `apply_patch`) → function tools with `input` string argument
    - `reasoning` dict → `reasoning_effort` string (extract `effort` field)
    - `text.verbosity` → pass through or drop (not a standard Chat Completions field)
    - `include` array → drop (Responses API-only)
    - `previous_response_id`, `store` → drop (Responses API-only)
    - `stream_options` → keep as-is (already standard)
    - `model`, `temperature`, `top_p`, `max_tokens` → pass through unchanged
    - `tools` already in flat format → convert ALL tools
    - Non-standard tool types like `{"type": "custom"}` → convert to function tool with `input` string parameter
  - Implement `convert_chat_to_responses(payload: dict) -> dict` for response conversion:
    - This is for potential future use with Codex Responses API
    - For now, keep minimal: Chat Completions response is returned as-is (Cursor expects Chat Completions output format)
  - Add comprehensive test fixtures with captured Cursor Agent mode payloads
  - Reference: `holo-q/deepseek-responses-proxy` for validation patterns

  > **Fetch test fixtures from**: The Cursor forum thread at `forum.cursor.com/t/override-openai-base-url-sends-responses-api-payload/159298` and the reference implementation at `github.com/holo-q/deepseek-responses-proxy` provide real payload shapes.

  **Must NOT do**:
  - Don't alter non-Responses-API payloads (standard Chat Completions pass through unchanged)
  - Don't implement full Responses API server (this is a converter, not a Responses endpoint)
  - Don't modify response format (Cursor expects Chat Completions SSE format)

  **Recommended Agent Profile**:
  - **Category**: `deep`
    - Reason: Complex format conversion with many edge cases
  - **Skills**: []
    - This is a pure data-transformation task

  **Parallelization**:
  - **Can Run In Parallel**: YES (can start alongside Task 8)
  - **Parallel Group**: Wave 2 (with Tasks 8-10)
  - **Blocks**: Task 11 (integration into handler)
  - **Blocked By**: Tasks 1-3 (rename and import paths)

  **References**:
  - `src/deepseek_bridge/transform.py` — Existing transform patterns to follow
  - `src/deepseek_bridge/server.py:263-268` — Request path routing (where converter will be called)
  - `forum.cursor.com/t/override-openai-base-url-sends-responses-api-payload/159298` — Cursor payload shapes
  - `github.com/holo-q/deepseek-responses-proxy` — Reference implementation
  - OpenAI Responses API docs: `https://platform.openai.com/docs/api-reference/responses`

  **Acceptance Criteria**:

  **QA Scenarios**:
  ```
  Scenario: Standard Chat Completions payload passes through unchanged
    Tool: Bash
    Steps:
      1. uv run python -c "
      from deepseek_bridge.responses_converter import detect_responses_payload, convert_responses_to_chat
      chat_payload = {'model': 'deepseek-v4-pro', 'messages': [{'role': 'user', 'content': 'hi'}], 'stream': True}
      assert not detect_responses_payload(chat_payload)
      result = convert_responses_to_chat(chat_payload)
      assert result == chat_payload
      print('PASS: standard chat payload unchanged')
      "
    Expected Result: Standard payload passes through unchanged
    Evidence: .sisyphus/evidence/task-7-chat-pass-through.txt

  Scenario: Responses API payload with input is converted to messages
    Tool: Bash
    Steps:
      1. uv run python -c "
      from deepseek_bridge.responses_converter import detect_responses_payload, convert_responses_to_chat
      responses_payload = {
        'model': 'deepseek-v4-pro',
        'input': [
          {'role': 'user', 'content': 'Hello'},
          {'type': 'function_call_output', 'call_id': 'call_123', 'output': 'result'}
        ],
        'instructions': 'You are a helpful assistant',
        'stream': True
      }
      assert detect_responses_payload(responses_payload)
      result = convert_responses_to_chat(responses_payload)
      assert 'messages' in result
      assert 'input' not in result
      assert len(result['messages']) == 3  # system + user + tool
      assert result['messages'][0]['role'] == 'system'
      print('PASS: responses payload converted to chat format')
      "
    Expected Result: Responses payload is correctly converted
    Evidence: .sisyphus/evidence/task-7-responses-conversion.txt

  Scenario: Flat tools converted to nested format
    Tool: Bash
    Steps:
      1. [same pattern: test flat tool conversion]
    Expected Result: Flat tools converted to nested function format
    Evidence: .sisyphus/evidence/task-7-tool-conversion.txt

  Scenario: Empty input handled gracefully
    Tool: Bash
    Steps:
      1. [test with empty input list]
    Expected Result: No crash, returns valid messages array
    Evidence: .sisyphus/evidence/task-7-empty-input.txt
  ```

  **Evidence to Capture**:
  - [ ] `.sisyphus/evidence/task-7-chat-pass-through.txt`
  - [ ] `.sisyphus/evidence/task-7-responses-conversion.txt`
  - [ ] `.sisyphus/evidence/task-7-tool-conversion.txt`
  - [ ] `.sisyphus/evidence/task-7-empty-input.txt`

  **Commit**: YES
  - Message: `feat: add Responses API to Chat Completions format converter for Cursor Agent mode`
  - Files: `src/deepseek_bridge/responses_converter.py`

---

- [x] 8. Update DeepSeek API compliance

  **What to do**:

  **Update `SUPPORTED_REQUEST_FIELDS`** in `transform.py`:
  - Add `"thinking"` to supported fields (now expects object `{"type": "enabled"|"disabled"}`)
  - Add `"response_format"` (JSON mode support)
  - Add `"logprobs"` and `"top_logprobs"`
  - Add `"user_id"`
  - Add `"max_completion_tokens"` as an alias for `max_tokens`

  **Update `thinking` param handling** in `transform.py:prepare_upstream_request`:
  - Change from top-level `thinking` string to object format `{"thinking": {"type": config.thinking}}`
  - Keep backward-compatible detection (both string and object accepted)

  **Update `reasoning_effort` mapping** in `transform.py:EFFORT_ALIASES`:
  - Current: `{"low":"high","medium":"high","high":"high","max":"max","xhigh":"max"}`
  - Verify this is still correct per DeepSeek V4 docs
  - Add any new effort levels

  **Handle deprecated params** in `transform.py:prepare_upstream_request`:
  - `frequency_penalty`: If present, log a warning and drop from prepared payload
  - `presence_penalty`: If present, log a warning and drop from prepared payload

  **Add legacy model mapping** in `transform.py:upstream_model_for`:
  - If original model is `deepseek-chat` or `deepseek-reasoner`, silently map to `deepseek-v4-flash`
  - Do NOT log a warning (silent mapping per user's request)
  - Normal model names (starting with `deepseek-`) pass through unchanged

  **Update `SYSTEM_FINGERPRINT`** if needed (already uses generic `fp_deepseek_cursor_proxy` — rename to `fp_deepseek_bridge`)

  **Update `MODEL_CREATED_TIMESTAMPS`** in `server.py`:
  - Add `deepseek-v4-pro` and `deepseek-v4-flash` entries (already present)
  - Update timestamps if needed

  **Update `response_format` passthrough**: Ensure `response_format: {"type": "json_object"}` is forwarded to the upstream.

  **Must NOT do**:
  - Don't remove support for old `thinking` format (keep accepting both string and object)
  - Don't change the `thinking` field that Cursor sends (Cursor may send both formats)
  - No warning when mapping legacy model names (silent mapping)

  **Recommended Agent Profile**:
  - **Category**: `deep`
    - Reason: Requires careful understanding of DeepSeek V4 API changes
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 2 (with Tasks 7, 9, 10)
  - **Blocks**: Task 11 (handler integration)
  - **Blocked By**: Tasks 1-3

  **References**:
  - `src/deepseek_bridge/transform.py:23-48` — `SUPPORTED_REQUEST_FIELDS`
  - `src/deepseek_bridge/transform.py:74-80` — `EFFORT_ALIASES`
  - `src/deepseek_bridge/transform.py:681-689` — `upstream_model_for`
  - `src/deepseek_bridge/transform.py:737-888` — `prepare_upstream_request`
  - `src/deepseek_bridge/server.py:46-51` — Fingerprint and model timestamps
  - DeepSeek API docs: `https://api-docs.deepseek.com/api/create-chat-completion`

  **Acceptance Criteria**:

  **QA Scenarios**:
  ```
  Scenario: Legacy model names mapped silently
    Tool: Bash
    Steps:
      1. uv run python -c "
      from deepseek_bridge.transform import upstream_model_for
      from deepseek_bridge.config import ProxyConfig
      config = ProxyConfig()
      result = upstream_model_for('deepseek-chat', config)
      print(f'mapped deepseek-chat to: {result}')
      assert result == 'deepseek-v4-flash'
      result2 = upstream_model_for('deepseek-reasoner', config)
      print(f'mapped deepseek-reasoner to: {result2}')
      assert result2 == 'deepseek-v4-flash'
      # Normal models pass through
      result3 = upstream_model_for('deepseek-v4-pro', config)
      assert result3 == 'deepseek-v4-pro'
      print('PASS: all mappings correct')
      "
    Expected Result: Legacy models map to v4-flash. Normal models pass through.
    Evidence: .sisyphus/evidence/task-8-legacy-mapping.txt

  Scenario: thinking object format supported
    Tool: Bash
    Steps:
      1. [test that `thinking` as object is accepted and forwarded]
    Expected Result: thinking object passes through to upstream
    Evidence: .sisyphus/evidence/task-8-thinking-param.txt

  Scenario: deprecated params are dropped
    Tool: Bash
    Steps:
      1. [test that frequency_penalty and presence_penalty are dropped with warning]
    Expected Result: Params dropped, no upstream payload contains them
    Evidence: .sisyphus/evidence/task-8-deprecated-params.txt
  ```

  **Evidence to Capture**:
  - [ ] `.sisyphus/evidence/task-8-legacy-mapping.txt`
  - [ ] `.sisyphus/evidence/task-8-thinking-param.txt`
  - [ ] `.sisyphus/evidence/task-8-deprecated-params.txt`

  **Commit**: YES
  - Message: `feat: update DeepSeek API compliance for V4 — thinking object, legacy model mapping, deprecated params`
  - Files: `src/deepseek_bridge/transform.py`, `src/deepseek_bridge/config.py`

---

- [x] 9. Add legacy model name mapping

  **Note**: This task is embedded within Task 8. Task 8 handles the `upstream_model_for` function change.
  This task adds the corresponding configuration updates.

  **What to do**:
  - Update default model in `config.py` from `deepseek-v4-pro` to `deepseek-v4-flash` (optional — discuss if user wants flash as default)
  - Keep `deepseek-v4-pro` references in the codebase as they're the correct current model names
  - Update `DEFAULT_CONFIG_TEXT` in `config.py` to use current model names
  - This is a small addition to Task 8

  **Recommended Agent Profile**:
  - **Category**: `quick`
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 2 (with Tasks 7, 8, 10)
  - **Blocked By**: Tasks 1-3

  **Commit**: Groups with Task 8

---

- [x] 10. Update Ollama endpoint capabilities for Copilot

  **What to do**:
  - Update `/api/show` endpoint handler to advertise richer capabilities:
    - `capabilities.supports.tool_calls: true` (already set)
    - Add `capabilities.supports.vision: true` or `false` based on whether the backend supports it (flag-based)
    - Add `capabilities.limits.max_prompt_tokens` with a reasonable value (default to 128000 for DeepSeek)
    - Add `capabilities.limits.max_output_tokens` (default 384000 for DeepSeek)
  - Update `_handle_api_show` in `server.py` to return these capabilities
  - The VS Code Copilot Ollama provider parses these fields from `/api/show`
  - Keep the `"tools"` capability in the `capabilities` list
  - Add `"vision"` if backend supports (currently DeepSeek does NOT support vision — advertise false unless configured otherwise)

  **Must NOT do**:
  - Don't break existing `/api/show` response shape used by other clients

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
    - Reason: Requires understanding of VS Code's Ollama provider capabilities parsing
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 2 (with Tasks 7-9)
  - **Blocked By**: Tasks 1-3

  **References**:
  - `src/deepseek_bridge/server.py:843-871` — Current `_handle_api_show` implementation
  - VS Code Ollama provider source (PR #3566): `github.com/microsoft/vscode-copilot-chat/pull/3566`
  - Copilot BYOK docs: `https://code.visualstudio.com/docs/copilot/customization/language-models`
  - CustomOAIModels interface: `url`, `name`, `toolCalling`, `vision`, `thinking`, `maxInputTokens`, `maxOutputTokens`, `streaming`, `editTools`

  **Acceptance Criteria**:

  **QA Scenarios**:
  ```
  Scenario: /api/show returns capabilities with tool_calls and limits
    Tool: Bash
    Steps:
      1. uv run deepseek-bridge --no-ngrok --port 9999 &
      2. sleep 1
      3. curl -s -X POST http://localhost:9999/api/show -d '{"model":"deepseek-v4-pro"}' | python -m json.tool
      4. kill %1 2>/dev/null || true
    Expected Result: Response includes `capabilities.supports.tool_calls: true`, `capabilities.limits.max_prompt_tokens`, `capabilities.limits.max_output_tokens`
    Evidence: .sisyphus/evidence/task-10-api-show.txt

  Scenario: /api/tags returns valid model list
    Tool: Bash
    Steps:
      1. curl -s http://localhost:9999/api/tags | python -m json.tool
    Expected Result: Returns models array with valid entries
    Evidence: .sisyphus/evidence/task-10-api-tags.txt
  ```

  **Evidence to Capture**:
  - [ ] `.sisyphus/evidence/task-10-api-show.txt`
  - [ ] `.sisyphus/evidence/task-10-api-tags.txt`

  **Commit**: YES
  - Message: `feat: enhance Ollama endpoint capabilities for Copilot BYOK compatibility`
  - Files: `src/deepseek_bridge/server.py`

---

- [x] 11. Integrate Responses API converter into proxy handler

  **What to do**:
  - In `server.py` (`do_POST` method), add detection + conversion logic:
    - After reading the request body and before calling `prepare_upstream_request`
    - Check if payload is Responses API format using `detect_responses_payload()`
    - If yes, convert it using `convert_responses_to_chat()` and store the original for trace logging
    - Log a debug/info message: "converted Responses API format to Chat Completions"
    - Record the conversion in the trace (if trace is enabled)
  - The response format stays as Chat Completions (no conversion needed for output — Cursor expects Chat Completions SSE)
  - Ensure the trace records both the original and converted payloads

  **Must NOT do**:
  - Don't alter non-Responses-API payloads (they pass through unchanged)
  - Don't add new dependencies

  **Recommended Agent Profile**:
  - **Category**: `deep`
    - Reason: Requires understanding of the full request flow in the handler
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: NO
  - **Sequential**: After Tasks 7 and 8
  - **Blocks**: Task 12 (server.py split should include this integration)
  - **Blocked By**: Tasks 7, 8

  **References**:
  - `src/deepseek_bridge/server.py:234-399` — `do_POST` method
  - `src/deepseek_bridge/server.py:344-351` — Where `prepare_upstream_request` is called
  - `src/deepseek_bridge/responses_converter.py` — The new converter module (Task 7)

  **Acceptance Criteria**:

  **QA Scenarios**:
  ```
  Scenario: Responses API payload handled end-to-end (mock upstream)
    Tool: Bash
    Steps:
      1. [Start proxy with mock upstream, send Responses API payload, verify response]
    Expected Result: Payload is converted, upstream receives Chat Completions format, client receives valid response
    Evidence: .sisyphus/evidence/task-11-e2e-response-converter.txt

  Scenario: Standard Chat Completions payload unchanged end-to-end
    Tool: Bash
    Steps:
      1. [Same setup, send standard payload, verify passthrough]
    Expected Result: Payload is not converted, sent as-is upstream
    Evidence: .sisyphus/evidence/task-11-e2e-chat-passthrough.txt
  ```

  **Evidence to Capture**:
  - [ ] `.sisyphus/evidence/task-11-e2e-response-converter.txt`
  - [ ] `.sisyphus/evidence/task-11-e2e-chat-passthrough.txt`

  **Commit**: YES (groups with Task 7)
  - Message: `feat: integrate Responses API converter into proxy handler`
  - Files: `src/deepseek_bridge/server.py`

---

### Wave 3 — Refactoring

- [x] 12. Split server.py into handler.py, cli.py, helpers.py

  **What to do**:
  Split the ~2013-line `server.py` into three focused modules. Preserve all imports from `deepseek_bridge.server` for backward compatibility during transition (use re-exports).

  **`src/deepseek_bridge/handler.py`** — HTTP request handling:
  - `DeepSeekProxyHandler` class (all `do_*`, `_handle_*`, `_proxy_*`, `_rewrite_*` methods)
  - `DeepSeekProxyServer` class
  - `BoundedThreadPoolHTTPServer` class
  - `UpstreamPool` class
  - `ProxyResponseResult` dataclass
  - `_generate_request_id()`, `_error_body()`, `_shutdown_requested`
  - `_handle_shutdown_signal()`

  **`src/deepseek_bridge/cli.py`** — CLI and server lifecycle:
  - `build_arg_parser()` function
  - `main()` function
  - `_run_server()` function
  - `warn_if_insecure_upstream()` function
  - Signal handling, startup banner, config loading logic

  **`src/deepseek_bridge/helpers.py`** — Utility functions:
  - All `log_*` functions (`log_json`, `log_bytes`, `log_cursor_request`, etc.)
  - `format_count()`, `int_or_zero()`, `elapsed_ms()`, `summarize_chat_payload()`
  - `message_count()`, `tool_count()`, `user_message_count()`, `reasoning_content_count()`
  - `usage_from_body()`, `format_usage_count()`, `reasoning_token_count()`, `cache_hit_rate()`
  - `context_status()`, `log_context_summary()`, `log_send_summary()`, `log_stats_summary()`
  - `_truncate_message_content()`
  - `read_response_body()`
  - `sse_data()`, `inject_recovery_notice()`, `recovery_notice_chunk()`
  - `RequestBoodTooLarge` exception
  - `SYSTEM_FINGERPRINT`, `MODEL_CREATED_TIMESTAMPS` constants

  **`src/deepseek_bridge/server.py`** — Re-exports (backward compat):
  ```python
  from .handler import DeepSeekProxyHandler, DeepSeekProxyServer, etc.
  from .cli import main, build_arg_parser, _run_server
  from .helpers import *
  ```
  - Also keep `_generate_request_id`, `_error_body`, `_shutdown_requested` re-exports

  **Must NOT do**:
  - Don't change any public import paths that tests use (keep `server.py` as a re-export hub)
  - Don't introduce circular imports
  - Don't change any behavior

  **Recommended Agent Profile**:
  - **Category**: `deep`
    - Reason: Large refactoring requiring careful boundary definition
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: NO (blocks everything that touches server.py)
  - **Blocks**: Tasks 13, 14, 15
  - **Blocked By**: Tasks 1-3, 6 (rename, imports, clean CLI)

  **References**:
  - `src/deepseek_bridge/server.py` — Full file (2013 lines)
  - `tests/test_server.py` — Tests that import from server module
  - `tests/test_resilience.py` — Tests that import server classes
  - `tests/test_protocol.py` — End-to-end tests that use server

  **Acceptance Criteria**:

  **QA Scenarios**:
  ```
  Scenario: All imports resolve from new modules
    Tool: Bash
    Steps:
      1. uv run python -c "
      import deepseek_bridge.handler
      import deepseek_bridge.cli
      import deepseek_bridge.helpers
      from deepseek_bridge.handler import DeepSeekProxyHandler
      from deepseek_bridge.cli import main, build_arg_parser
      from deepseek_bridge.helpers import format_count, sse_data
      print('PASS: all new module imports succeed')
      "
    Expected Result: All imports resolve
    Evidence: .sisyphus/evidence/task-12-new-imports.txt

  Scenario: Old imports from server still work (re-exports)
    Tool: Bash
    Steps:
      1. uv run python -c "
      from deepseek_bridge.server import DeepSeekProxyHandler, main, build_arg_parser, format_count, sse_data
      print('PASS: re-exports from server module work')
      "
    Expected Result: All re-exports resolve
    Evidence: .sisyphus/evidence/task-12-re-exports.txt

  Scenario: No circular imports
    Tool: Bash
    Steps:
      1. uv run python -c "
      import sys
      del sys.modules['deepseek_bridge']
      from deepseek_bridge import handler, cli, helpers, server
      print('PASS: no circular imports')
      "
    Expected Result: All modules import without circular dependency errors
    Evidence: .sisyphus/evidence/task-12-no-circular.txt
  ```

  **Evidence to Capture**:
  - [ ] `.sisyphus/evidence/task-12-new-imports.txt`
  - [ ] `.sisyphus/evidence/task-12-re-exports.txt`
  - [ ] `.sisyphus/evidence/task-12-no-circular.txt`

  **Commit**: YES
  - Message: `refactor: split server.py into handler.py, cli.py, helpers.py`
  - Files: `src/deepseek_bridge/server.py`, `src/deepseek_bridge/handler.py`, `src/deepseek_bridge/cli.py`, `src/deepseek_bridge/helpers.py`

---

- [x] 13. Fix anti-pattern tests in test_resilience.py

  **What to do**:
  - Find the `inspect.getsource`-based test in `tests/test_resilience.py`
  - Replace it with a behavioral test that:
    - Creates a `DeepSeekProxyHandler` stub with a broken wfile
    - Calls a method that writes to `wfile` (e.g., `_write_to_client`)
    - Verifies that `self.close_connection` is set to `True` after the broken pipe error
  - The current test uses `inspect.getsource(self.server.__class__)` to check if the source code contains `close_connection = True` — this is fragile and doesn't test actual behavior
  - New test should inject a real `BrokenPipeError` or use a mock wfile that raises `BrokenPipeError`

  **Must NOT do**:
  - Don't remove the test entirely (it tests an important safety behavior)
  - Don't change other parts of `test_resilience.py`

  **Recommended Agent Profile**:
  - **Category**: `quick`
    - Reason: Targeted fix for one test method
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 3 (with Tasks 14, 15)
  - **Blocked By**: Task 12 (server.py split changes the import structure)

  **References**:
  - `tests/test_resilience.py:347-353` — The anti-pattern `inspect.getsource` test
  - `src/deepseek_bridge/handler.py` — The handler that should set `close_connection = True` on write failures

  **Acceptance Criteria**:

  **QA Scenarios**:
  ```
  Scenario: Behavioral test passes for close_connection on write failure
    Tool: Bash
    Steps:
      1. uv run python -m unittest tests.test_resilience.CloseConnectionTests -v
    Expected Result: Test passes — verifies that BrokenPipeError sets close_connection=True (behaviorally, not via source inspection)
    Evidence: .sisyphus/evidence/task-13-behavioral-test.txt
  ```

  **Evidence to Capture**:
  - [ ] `.sisyphus/evidence/task-13-behavioral-test.txt`

  **Commit**: YES (groups with Task 14 or standalone)
  - Message: `test: replace inspect.getsource anti-pattern with behavioral test for close_connection`
  - Files: `tests/test_resilience.py`

---

- [x] 14. Update test imports for renamed package

  **What to do**:
  - Update ALL test imports from `deepseek_cursor_proxy` to `deepseek_bridge`
  - Files to update:
    - `tests/test_config.py`
    - `tests/test_server.py`
    - `tests/test_transform.py`
    - `tests/test_streaming.py`
    - `tests/test_reasoning_store.py`
    - `tests/test_resilience.py`
    - `tests/test_protocol.py`
    - `tests/test_trace.py`
    - `tests/test_tunnel.py`
    - `tests/test_live.py`
    - `tests/test_soak.py`
  - Use `ast_grep_replace` for systematic replacement: `from deepseek_cursor_proxy.` → `from deepseek_bridge.` and `import deepseek_cursor_proxy.` → `import deepseek_bridge.`
  - Run `uv run python -m unittest discover -s tests -v` to verify all tests pass

  **Must NOT do**:
  - Don't change any test logic — only import paths
  - Don't change the `__pycache__/` directories

  **Recommended Agent Profile**:
  - **Category**: `quick`
    - Reason: Systematic find-and-replace across 11 test files
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 3 (with Tasks 13, 15)
  - **Blocked By**: Task 12 (server.py split)

  **References**:
  - All files in `tests/` directory
  - Task 3 for the pattern used in source files

  **Acceptance Criteria**:

  **QA Scenarios**:
  ```
  Scenario: All tests pass with new imports
    Tool: Bash
    Steps:
      1. uv run python -m unittest discover -s tests -v 2>&1 | tail -20
    Expected Result: All ~128 tests pass (same count as before)
    Evidence: .sisyphus/evidence/task-14-all-tests-pass.txt
  ```

  **Evidence to Capture**:
  - [ ] `.sisyphus/evidence/task-14-all-tests-pass.txt`

  **Commit**: YES
  - Message: `test: update imports for deepseek-bridge rename`
  - Files: `tests/*.py`

---

- [x] 15. Add new tests for converter and DeepSeek API compliance

  **What to do**:
  Create new test files for the features added in Wave 2:

  **`tests/test_responses_converter.py`**:
  - Test `detect_responses_payload` with:
    - Valid Responses API payload (detects correctly)
    - Standard Chat Completions payload (returns False)
    - Edge cases: empty dict, missing fields, None
  - Test `convert_responses_to_chat` with:
    - Full Responses API payload with `input`, `instructions`, flat tools
    - Custom/freeform tool conversion
    - Reasoning dict → reasoning_effort string extraction
    - `include` array stripping
    - Empty input array
    - Passthrough of standard fields (`model`, `stream`, `temperature`)
  - Test that non-Responses payloads pass through unchanged

  **`tests/test_deepseek_api_compliance.py`**:
  - Test `upstream_model_for` with legacy model names
  - Test thinking object parameter preservation
  - Test `response_format` passthrough
  - Test `frequency_penalty`/`presence_penalty` removal
  - Test `SUPPORTED_REQUEST_FIELDS` has all new fields
  - Test `EFFORT_ALIASES` mapping

  **Must NOT do**:
  - Don't modify existing test files (use new files)
  - Don't add network-dependent tests (all tests must be hermetic)

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
    - Reason: Thorough test coverage requiring understanding of new features
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 3 (with Tasks 13, 14)
  - **Blocked By**: Tasks 7, 8, 12

  **References**:
  - `tests/test_transform.py` — Existing transform test patterns
  - `tests/test_protocol.py` — End-to-end test patterns
  - `src/deepseek_bridge/responses_converter.py` — New module to test
  - `src/deepseek_bridge/transform.py` — Updated module to test

  **Acceptance Criteria**:

  **QA Scenarios**:
  ```
  Scenario: New test files are discoverable and pass
    Tool: Bash
    Steps:
      1. uv run python -m unittest tests.test_responses_converter -v
      2. uv run python -m unittest tests.test_deepseek_api_compliance -v
    Expected Result: All new tests pass
    Evidence: .sisyphus/evidence/task-15-new-tests.txt

  Scenario: Full test suite still passes
    Tool: Bash
    Steps:
      1. uv run python -m unittest discover -s tests -v 2>&1 | tail -10
    Expected Result: All tests including new ones pass. Total count increases by new test count.
    Evidence: .sisyphus/evidence/task-15-full-suite.txt
  ```

  **Evidence to Capture**:
  - [ ] `.sisyphus/evidence/task-15-new-tests.txt`
  - [ ] `.sisyphus/evidence/task-15-full-suite.txt`

  **Commit**: YES
  - Message: `test: add tests for Responses API converter and DeepSeek API compliance`
  - Files: `tests/test_responses_converter.py`, `tests/test_deepseek_api_compliance.py`

---

### Wave 4 — CI + Polish

- [x] 16. Add mypy type checking to CI

  **What to do**:
  - Add `mypy` to dev dependencies in `pyproject.toml`
  - Create `mypy.ini` or `pyproject.toml [tool.mypy]` section:
    ```toml
    [tool.mypy]
    python_version = "3.10"
    check_untyped_defs = true
    warn_unused_ignores = true
    ignore_missing_imports = true
    strict_optional = true
    ```
  - Run `uv run mypy src/ --check-untyped-defs` and fix all type issues
  - Common fixes needed:
    - Add type annotations to function signatures where missing
    - Use `Optional[X]` or `X | None` for nullable params
    - Add `# type: ignore[xxx]` for third-party imports without stubs
    - Fix any real type bugs discovered
  - Add `uv run mypy src/ --check-untyped-defs` to `.github/workflows/ci.yml` in the lint job
  - Do not add mypy to pre-commit (keep pre-commit for black+ruff only)

  **Must NOT do**:
  - Don't use `--strict` mode (as agreed)
  - Don't add mypy to pre-commit hooks
  - Don't require type annotations for test files

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
    - Reason: Systematic type annotation work across the codebase
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 4 (with Tasks 17-20)
  - **Blocked By**: Tasks 1-3, 12

  **References**:
  - `pyproject.toml` — Add mypy config
  - `.github/workflows/ci.yml` — Add mypy step
  - `src/deepseek_bridge/*.py` — All source files to type-check

  **Acceptance Criteria**:

  **QA Scenarios**:
  ```
  Scenario: mypy passes on src/
    Tool: Bash
    Steps:
      1. uv run mypy src/ --check-untyped-defs
    Expected Result: Exit code 0. Zero type errors.
    Evidence: .sisyphus/evidence/task-16-mypy-pass.txt
  ```

  **Evidence to Capture**:
  - [ ] `.sisyphus/evidence/task-16-mypy-pass.txt`

  **Commit**: YES
  - Message: `ci: add mypy type checking with --check-untyped-defs`
  - Files: `pyproject.toml`, `.github/workflows/ci.yml`

---

- [x] 17. Add coverage reporting to CI

  **What to do**:
  - Add `coverage` to dev dependencies or as `[test]` optional dependency
  - Create `.coveragerc` config:
    ```ini
    [run]
    source = src/deepseek_bridge
    omit = */tui/*

    [report]
    exclude_lines =
        pragma: no cover
        def __repr__
        raise NotImplementedError
        if __name__ == .__main__.:
    ```
  - Add coverage step to `.github/workflows/ci.yml` unit-test job:
    ```yaml
    - name: Run tests with coverage
      run: |
        uv run coverage run -m unittest discover -s tests -v
        uv run coverage report --fail-under=85
    ```
  - Add `coverage` badge or mention to README (optional)

  **Must NOT do**:
  - Don't set `--fail-under` too high (85% is reasonable for a first baseline)
  - Don't add coverage as a pre-commit hook

  **Recommended Agent Profile**:
  - **Category**: `quick`
    - Reason: Standard CI configuration change
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 4 (with Tasks 16, 18-20)
  - **Blocked By**: Tasks 1-3

  **References**:
  - `.github/workflows/ci.yml` — CI workflow file
  - `pyproject.toml` — Add coverage dependency

  **Acceptance Criteria**:

  **QA Scenarios**:
  ```
  Scenario: Coverage runs and reports
    Tool: Bash
    Steps:
      1. uv run coverage run -m unittest discover -s tests -v
      2. uv run coverage report
    Expected Result: Coverage runs, report shows percentage >= 85%
    Evidence: .sisyphus/evidence/task-17-coverage-report.txt
  ```

  **Evidence to Capture**:
  - [ ] `.sisyphus/evidence/task-17-coverage-report.txt`

  **Commit**: YES
  - Message: `ci: add coverage reporting with 85% threshold`
  - Files: `pyproject.toml`, `.github/workflows/ci.yml`, `.coveragerc`

---

- [x] 18. Add Windows testing to CI

  **What to do**:
  - Add `windows-latest` to the CI matrix in `.github/workflows/ci.yml`
  - Add a Windows-specific entry to the matrix:
    ```yaml
    - os: windows-latest
      python-version: "3.13"
    ```
  - Note: `signal.SIGTERM` is not available on Windows — wrap signal handling in try/except that's already present
  - Handle Windows path separators in any test that uses paths
  - Skip tests that rely on Unix-specific features (like `signal.SIGTERM`) on Windows

  **Must NOT do**:
  - Don't break existing Linux/macOS tests
  - Don't add Python 3.14 testing (that's a separate concern)

  **Recommended Agent Profile**:
  - **Category**: `quick`
    - Reason: Small CI config change
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 4 (with Tasks 16, 17, 19, 20)
  - **Blocked By**: Tasks 1-3

  **References**:
  - `.github/workflows/ci.yml:41-77` — Current matrix definition
  - `src/deepseek_bridge/server.py:1966-1970` — Signal handling that may need Windows compat

  **Acceptance Criteria**:

  **QA Scenarios**:
  ```
  Scenario: CI matrix includes Windows entry
    Tool: Bash
    Steps:
      1. grep -c "windows-latest" .github/workflows/ci.yml
    Expected Result: At least 1 match for windows-latest in the matrix
    Evidence: .sisyphus/evidence/task-18-windows-ci.txt
  ```

  **Evidence to Capture**:
  - [ ] `.sisyphus/evidence/task-18-windows-ci.txt`

  **Commit**: YES
  - Message: `ci: add Windows testing to CI matrix`
  - Files: `.github/workflows/ci.yml`

---

- [x] 19. Update uv version pin in CI

  **What to do**:
  - Update `astral-sh/setup-uv@v7` with `version` from `"0.11.7"` to latest stable (check current latest on PyPI/github)
  - Update both occurrences (lint and unit-test jobs)
  - Keep `enable-cache: true`
  - Verify `uv.lock` is still compatible with the new version (run `uv lock` if needed)

  **Must NOT do**:
  - Don't remove the `--locked` flag (ensures reproducible builds)
  - Don't change the action SHA/tag without checking for breaking changes

  **Recommended Agent Profile**:
  - **Category**: `quick`
    - Reason: Single version string update
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 4 (with Tasks 16-18, 20)
  - **Blocked By**: None

  **References**:
  - `.github/workflows/ci.yml:31-33` and `:69-71` — Two uv version pins

  **Acceptance Criteria**:

  **QA Scenarios**:
  ```
  Scenario: CI file has updated uv version
    Tool: Bash
    Steps:
      1. grep "version:" .github/workflows/ci.yml
    Expected Result: uv version is >= 0.12.0 (not 0.11.7)
    Evidence: .sisyphus/evidence/task-19-uv-version.txt
  ```

  **Evidence to Capture**:
  - [ ] `.sisyphus/evidence/task-19-uv-version.txt`

  **Commit**: YES (groups with Task 18)
  - Message: `ci: update uv version in CI workflow`
  - Files: `.github/workflows/ci.yml`

---

- [x] 20. Update .gitignore for new project name

  **What to do**:
  - Update `.gitignore` to remove old project-specific entries:
    - `.deepseek-cursor-proxy/` (if present)
    - `.deepseek_cursor_reasoning.sqlite3*`
  - Add new project-specific entries:
    - `.deepseek-bridge/`
  - Clean up duplicate entries if any
  - Remove the very generic template junk (commented-out sections for unrelated tools like Celery, PyCharm, Django, etc.) to reduce file bloat — keep only what's relevant to this project:
    - `__pycache__/`, `*.py[codz]`
    - `*.egg-info/`, `.eggs/`
    - `.venv`, `env/`, `venv/`, `ENV/`
    - `.ruff_cache/`
    - `.coverage`, `htmlcov/`, `.coverage.*`
    - `.pytest_cache/`
    - `dist/`, `build/`
    - `trace-dumps/`
    - `.cursorignore`, `.cursorindexingignore`
    - `.mypy_cache/`
    - `.claude/`

  **Must NOT do**:
  - Don't remove `.env`, `.envrc` (standard for any Python project)
  - Don't remove `.gitignore` entries that are already committed (they're tracked)

  **Recommended Agent Profile**:
  - **Category**: `quick`
    - Reason: Simple file cleanup
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 4 (with Tasks 16-19)
  - **Blocked By**: Task 1 (to know the new name)

  **References**:
  - `.gitignore` — Full file (215 lines)

  **Acceptance Criteria**:

  **QA Scenarios**:
  ```
  Scenario: New config dir is ignored
    Tool: Bash
    Steps:
      1. grep ".deepseek-bridge" .gitignore
    Expected Result: `.deepseek-bridge/` is listed in gitignore
    Evidence: .sisyphus/evidence/task-20-gitignore.txt
  ```

  **Evidence to Capture**:
  - [ ] `.sisyphus/evidence/task-20-gitignore.txt`

  **Commit**: YES
  - Message: `chore: update .gitignore for deepseek-bridge rename`
  - Files: `.gitignore`

---

## Final Verification Wave (MANDATORY — after ALL implementation tasks)

> 4 review agents run in PARALLEL. ALL must APPROVE. Present consolidated results to user and get explicit "okay" before completing.

- [x] F1. **Plan Compliance Audit** — `oracle`
  Read the plan end-to-end. For each "Must Have": verify implementation exists (read file, run commands). For each "Must NOT Have": search codebase for forbidden patterns. Check evidence files exist in `.sisyphus/evidence/`. Compare deliverables against plan.
  Output: `Must Have [N/N] | Must NOT Have [N/N] | Tasks [N/N] | VERDICT: APPROVE/REJECT`

- [x] F2. **Code Quality and Type Check** — `unspecified-high`
  Run `uv run mypy src/ --check-untyped-defs` + `uv run pre-commit run --all-files` + `uv run python -m unittest discover -s tests -v`. Review changed files for: commented-out code, unused imports, over-abstraction, generic names.
  Output: `Mypy [PASS/FAIL] | Lint [PASS/FAIL] | Tests [N pass/N fail] | Files [N clean/N issues] | VERDICT`

- [x] F3. **Full QA Scenario Execution** — `unspecified-high`
  Execute EVERY QA scenario from EVERY task — follow exact steps, capture evidence. Test cross-task integration. Save to `.sisyphus/evidence/final-qa/`.
  Output: `Scenarios [N/N pass] | Integration [N/N] | Edge Cases [N tested] | VERDICT`

- [x] F4. **Scope Fidelity Check** — `deep`
  For each task: read "What to do", read actual diff. Verify 1:1 — everything in spec was built, nothing beyond spec was built. Check "Must NOT do" compliance. Detect cross-task contamination.
  Output: `Tasks [N/N compliant] | Contamination [CLEAN/N issues] | Unaccounted [CLEAN/N files] | VERDICT`

---

## Commit Strategy

- **1 (+2, 3)**: `chore: rename deepseek-cursor-proxy to deepseek-bridge` — all src/, tests/, pyproject.toml, README, assets/
- **4**: `chore: update pre-commit hooks and tooling config` — .pre-commit-config.yaml, pyproject.toml
- **5**: `feat: add [tui] optional dependency group for textual dashboard` — pyproject.toml
- **6**: `refactor: remove deprecated CLI aliases` — server.py, config.py, README
- **7 (+11)**: `feat: add Responses API to Chat Completions format converter` — responses_converter.py, server.py
- **8 (+9)**: `feat: update DeepSeek API compliance for V4` — transform.py, config.py
- **10**: `feat: enhance Ollama endpoint capabilities for Copilot BYOK` — server.py
- **12**: `refactor: split server.py into handler.py, cli.py, helpers.py` — handler.py, cli.py, helpers.py, server.py
- **13**: `test: replace inspect.getsource anti-pattern with behavioral test` — test_resilience.py
- **14**: `test: update imports for deepseek-bridge rename` — tests/*.py
- **15**: `test: add tests for Responses converter and DeepSeek API compliance` — test_responses_converter.py, test_deepseek_api_compliance.py
- **16**: `ci: add mypy type checking` — pyproject.toml, ci.yml
- **17**: `ci: add coverage reporting` — pyproject.toml, ci.yml, .coveragerc
- **18 (+19)**: `ci: add Windows testing and update uv version` — ci.yml
- **20**: `chore: update .gitignore for deepseek-bridge` — .gitignore

---

## Success Criteria

### Verification Commands
```bash
# Verify rename
grep -r "deepseek.cursor.proxy" src/ && echo "FAIL" || echo "PASS"
grep -r "deepseek_cursor_proxy" src/ && echo "FAIL" || echo "PASS"

# Verify imports
uv run python -c "import deepseek_bridge; import deepseek_bridge.handler; import deepseek_bridge.cli; import deepseek_bridge.helpers"

# Verify all tests pass
uv run python -m unittest discover -s tests -v

# Verify pre-commit
uv run pre-commit run --all-files

# Verify mypy
uv run mypy src/ --check-untyped-defs

# Verify coverage
uv run coverage run -m unittest discover -s tests -v && uv run coverage report --fail-under=85

# Verify CLI
uv run deepseek-bridge --help

# Verify TUI extra install
pip install -e ".[tui]" && python -c "import textual; print(textual.__version__)"

# Verify old aliases gone
uv run deepseek-bridge --no-collasible-reasoning --help 2>&1 | grep -qi "unrecognized" && echo "PASS" || echo "FAIL"
```

### Final Checklist
- [x] All "Must Have" present
- [x] All "Must NOT Have" absent
- [x] All 128+ existing tests pass (no regressions)
- [x] New feature tests pass
- [x] Pre-commit hooks pass
- [x] mypy passes on src/ with --check-untyped-defs
- [x] Coverage ≥ 85%
- [x] grep for old name patterns returns zero in src/ and tests/
- [x] All imports resolve for new module structure
- [x] TUI is installable via `.[tui]` extra
- [x] CLI typo aliases are gone (--collasible-reasoning returns error)
- [x] DeepSeek legacy model mapping works (deepseek-chat → deepseek-v4-flash)
