# Fix TUI Config MountError + Update README + Version Bump

## Issue 1: TUI MountError
**Bug**: `cat_container.mount(Label(...))` called during `compose()` before the container is mounted. Textual only allows mounting on widgets that have already been added to the DOM.

**Fix**: Instead of using `.mount()` in compose, restructure to yield all children using Textual's compose pattern. Create helper method that yields child widgets from within the container context.

## Issue 2: README updates
Add sections for:
- TUI Dashboard usage
- Ollama/Copilot integration
- New CLI flags: --version, --headless, --compact, --ollama
- Known limitations

## Issue 3: Version bump
Current: 0.1.1 → Proposed: 0.2.0 (significant feature release)
Files to update: `__init__.py`, `pyproject.toml`
