
## F3: QA Execution Decisions (2026-05-06)

### Test #4 False Positive
The original test command `uv run deepseek-bridge --no-collasible-reasoning --help 2>&1 | grep -qi "unrecognized\|error"` produces a false positive: `deepseek-bridge` doesn't exist, so the spawn error matches `grep -qi "error"`. This masks the fact that the old alias is still accepted by the active CLI. A more robust test would verify the CLI entry exists first, then test alias rejection.
