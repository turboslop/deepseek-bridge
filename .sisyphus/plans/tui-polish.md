# TUI Polish: Editable Config, API URLs, Proper Title + Layout

## Problems
1. Title shows "TuiApp" instead of "DeepSeek Cursor Proxy"
2. Dashboard doesn't show API URLs user needs to configure Cursor/Copilot
3. Config tab is read-only — can't edit settings
4. No visual polish (CSS, layout, grouping)

## Changes Needed

### Task 1: Fix Title + Tab Labels
**File**: `src/deepseek_cursor_proxy/tui/app.py`
- Add `TITLE = "DeepSeek Cursor Proxy"` class variable to TuiApp
- Update tab labels: `"Dashboard"`, `"Config"`, `"Logs"` (no emojis)
- Result: Header shows project name, tabs have icons

### Task 2: Store public_url on server
**File**: `src/deepseek_cursor_proxy/server.py`
- After tunnel starts (~line 1923), add: `server.public_url = public_url`
- This makes the ngrok URL accessible to the TUI dashboard

### Task 3: Add API URLs to Dashboard
**File**: `src/deepseek_cursor_proxy/tui/dashboard.py`
- Add 4 fields to `_DashboardSnapshot`: `local_url`, `api_url`, `upstream_url`, `ollama_url`
- In `refresh_stats()`, read from `server.config` and `server.public_url`
- Add "Connection" section below stats:
  - Cursor Base URL (from ngrok or localhost)
  - Upstream (DeepSeek API endpoint)
  - Ollama endpoint

### Task 4: Make Config Editor Editable
**File**: `src/deepseek_cursor_proxy/tui/config.py`
- Replace read-only Static with Input widgets
- Add Apply/Save button
- Group fields by category (Model, Network, Storage)
- On save: use `dataclasses.replace()` to update in-memory config
- Show status message ("Applied" / "Invalid value") without emojis

### Task 5: Add CSS polish
**File**: `src/deepseek_cursor_proxy/tui/app.py`
- Bordered config sections
- Input field margins
- Save button styling

## Delegation Plan

- [x] T1: Fix title + dashboard URLs + server public_url + CSS polish
- [x] T2: Make config editor editable
- [x] T3: Test suite + commit + push

