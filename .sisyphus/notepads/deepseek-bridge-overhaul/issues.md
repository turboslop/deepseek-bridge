
## F3: Full QA Scenario Execution — Issues Found (2026-05-06)

### Issue 1: Task 1 (Rename) Incomplete
- **Symptom**: `grep -r "deepseek.cursor.proxy" src/` returns hits
- **Root cause**: Old `src/deepseek_cursor_proxy/` directory was never removed. Contains full copies of server.py, config.py, transform.py, etc.
- **Also**: `pyproject.toml` still has `name = "deepseek-cursor-proxy"`, old script entry point, old package include pattern
- **Impact**: CLI entry `deepseek-bridge` doesn't exist; old name `deepseek-cursor-proxy` still works

### Issue 2: Task 6 (Alias Removal) Incomplete
- **Symptom**: `--no-collasible-reasoning` still accepted by CLI
- **Root cause**: Old `src/deepseek_cursor_proxy/server.py` still has typo aliases. Only the new `src/deepseek_bridge/` code was cleaned
- **Impact**: Test #4 falsely passes (grep matches "error" from spawn failure, not from arg rejection)

### Issue 3: Pre-commit Python Version Mismatch
- **Symptom**: `pre-commit run --all-files` fails with python3.10 not found
- **Root cause**: `.pre-commit-config.yaml` specifies `language_version: python3.10` but only python3.12 is available
- **Impact**: Test #8 FAIL (environment, not code quality)

### Issue 4: Stale .egg-info
- **Symptom**: `src/deepseek_cursor_proxy.egg-info/` still exists
- **Root cause**: Old egg-info never cleaned up after rename
- **Impact**: Minor — doesn't affect functionality but shows incomplete cleanup
