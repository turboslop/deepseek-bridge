# F2: Code Quality and Type Check — Issues

## Issue 1: Pre-commit environment incompatible with system Python
- `.pre-commit-config.yaml` specifies `language_version: python3.10` for black and likely defaults for other hooks
- System only has Python 3.12.3
- pre-commit fails with: `RuntimeError: failed to find interpreter for Builtin discover of python_spec='python3.10'`
- **Fix**: Remove `language_version: python3.10` from `.pre-commit-config.yaml` line 14, or update to `python3.12`

## Issue 2: 5 ruff lint errors
- 4 unused imports in `bridge/cli.py` and `bridge/handler.py`
- 1 unused variable in `proxy/server.py`
- All auto-fixable with `ruff --fix`

## Issue 3: 37 files not formatted with black
- 37 out of 47 files would be reformatted by black
- All auto-fixable with `black src/ tests/`
