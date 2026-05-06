# Proxy Audit Improvements - QA Learnings

## F3: Real Manual QA Results (2026-05-06)

### Scenario 1: Default log_dir ✅ PASS
- **Test**: Start proxy with no `--no-log` flag
- **Result**: Log path shown in Storage section as `Logs: /home/breixopd14/.deepseek-cursor-proxy/logs/proxy-DATE-TIME.log`
- **Evidence**: Log files exist in `~/.deepseek-cursor-proxy/logs/` (2 files found)

### Scenario 2: Startup banner structure ✅ PASS
- **Test**: Start proxy, inspect banner layout
- **Result**: Clear section headers visible:
  - Header: `DeepSeek Cursor Proxy v0.1.1`
  - Model section with thinking/reasoning settings
  - Network section with local and API base URLs
  - Storage section with DB path and logs path
- **Note**: Plain text, no emojis or ANSI codes

### Scenario 3: Bloat warning ✅ PASS
- **Test**: Start proxy with a 1MB DB at 96% free pages
- **Result**: Warning fires: `WARNING reasoning DB health: reasoning DB is 1 MB with 99% free pages (252/255). Run with --clear-reasoning-cache or restart to reclaim space.`
- **Test**: Healthy DB (0% free pages) produces NO false warning
- **Note**: Close-time VACUUM automatically reclaimed bloat when using ReasoningStore.open/close

### Scenario 4: Version output ✅ PASS
- **Test**: `from deepseek_cursor_proxy import __version__; print(__version__)`
- **Result**: `0.1.1` - matches `pyproject.toml` version

### Scenario 5: Auto-vacuum on new DB ✅ PASS
- **Test**: Create new file DB via ReasoningStore
- **Result**: `auto_vacuum=2` (INCREMENTAL) confirmed
- **Shrink test**: DB went from 4,145,152 bytes to 40,960 bytes after close (99% reduction)

### Scenario 6: --no-log flag ✅ PASS
- **Test**: Start proxy with `--no-log` flag
- **Result**: "Logs: disabled" shown, zero "log file" lines in output

## Key Observations

1. **VACUUM-on-close is aggressive**: The close-time VACUUM reclamations make bloat testing tricky - need raw sqlite3 to preserve bloat for testing
2. **incremental_vacuum works proactively**: During prune cycles, incremental_vacuum(100) reclaims pages, preventing bloat buildup
3. **Bloat warning threshold is conservative**: Only fires at >80% free pages - well-protected against false positives
4. **Startup banner is clean**: Good visual hierarchy without being flashy
