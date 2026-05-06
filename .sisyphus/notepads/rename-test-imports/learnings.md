
## 2026-05-06: Test import rename completed

- Replaced ALL `deepseek_cursor_proxy` → `deepseek_bridge` across 11 test files
- Also replaced hypenated forms `deepseek-cursor-proxy` → `deepseek-bridge` and env var `DEEPSEEK_CURSOR_PROXY_CONFIG_PATH` → `DEEPSEEK_BRIDGE_CONFIG_PATH`
- 189/191 tests pass (1 skipped live test)
- Remaining 2 failures are pre-existing typos unrelated to rename:
  - `test_cli_boolean_flags_have_on_and_off_forms`: uses `--no-collasible-resoning` (typo)
  - `test_loads_config_from_user_yaml_file`: writes `collasible_reasoning:` (typo)
