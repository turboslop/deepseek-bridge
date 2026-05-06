# Draft: Cursor Compat + CLI UX + Ollama Support

## Research Synthesis

### A. CLI Runtime UX (non-startup)
Current per-request output (4 lines per non-verbose request):
```
┌ request model=deepseek-v4-pro effort=high messages=12
├ context status=ok reasoning_context=3
└ stats   prompt=2,543 output=1,207 reasoning=847 cache_hit=63.2%
```
Plus animated spinner during streaming. Improvements needed:
- Make per-request output more compact (could be 2 lines)
- Add timing info (elapsed time per request)
- Show model name from request (not just fallback)
- Clean up the spinner — show real-time speed info (tokens/sec)
- Heartbeat: show DB size, pool status, uptime in a clean format

### B. Cursor Sub-Agent Support
**CONFIRMED: Cannot be fully fixed.** This is a Cursor-side bug — sub-agents don't inherit custom base URL. The proxy can:
- Document the limitation clearly
- Ensure PERFECT OpenAI compliance so when it DOES route through the proxy, it works flawlessly
- Fix: `/v1/models` should infer models from config

### C. Native Reasoning Display in Cursor
**Current approach**: Mirror `reasoning_content` into `<details><summary>Thinking</summary>...</details>` markdown blocks in the `content` field.
**Goal**: Get reasoning to show in Cursor's NATIVE reasoning UI (the brain icon/thought bubble).

Latest research: Cursor's native reasoning display is for their OWN models (Claude, GPT-5). For BYOK custom models, there is NO native reasoning rendering. The `<details>` markdown approach is the best available workaround.

**BUT**: We can try a new approach — Cursor might now support the standard `reasoning_content` field from the SSE stream. The proxy should:
1. Keep sending `reasoning_content` in SSE chunks (not strip it)
2. Keep the `<details>` markdown mirror as fallback
3. Add `--native-reasoning` mode that strips the `<details>` markdown and only relies on the native field
4. Research what User-Agent or headers Cursor sends to detect if it supports native reasoning

**Updated approach**: The proxy currently BOTH sends `reasoning_content` in the SSE stream AND mirrors it as markdown in `content`. This is the best approach — Cursor can use either. The `--no-display-reasoning` flag strips the markdown but keeps the native field.

### D. GitHub Copilot / Ollama Compatibility
**3 new endpoints needed:**
- `GET /api/version` — Return `"0.18.3"` to pass Copilot's version check
- `GET /api/tags` — List models in Ollama format with `model` field
- `POST /api/show` — Return model capabilities (MUST include `"tools"` for Agent Mode)

**Critical detail from VS Code source**: Copilot uses Ollama-native API for model discovery but OpenAI-compatible `/v1/chat/completions` for actual inference. Our proxy already has the inference part.

### E. Config Simplification
- `reasoning_cache_max_rows`: Auto-calculate based on disk space
- `reasoning_cache_max_age_seconds`: Could be reasonable default (7 days) instead of user-configured
- `cors`: Remove from config, always on
- `display_reasoning`: Always on by default
- `missing_reasoning_strategy`: Always "recover" by default
- Merge `max_pool_connections` and `max_keepalive` into single pool config
- Add `--ollama-mode` flag that enables Copilot-compatible Ollama endpoints

### F. Additional Improvements
- `--help` output cleanup with section grouping
- Per-request output with timing (elapsed_ms)
- Tokens per second in stats line
- Compact mode (`--compact`) for CI/script usage

### Metis Critical Findings (incorporated):
1. **T9 reasoning display is DUPLICATIVE**: `--no-display-reasoning` already does what `--no-markdown-reasoning` would do. Reasoning Content IS already forwarded in SSE. → Merge T9 into T10 docs
2. **T8 Ollama port is overengineered**: Serving Ollama endpoints on the same port under `/api/*` is simpler + avoids second server, tunnel, port conflicts. → Serve on main port
3. **T4 config simplifcation is really 7 sub-tasks**: Split into T4a (dead code), T4b (auto-calc), T4c (template hide), T4d (default changes)
4. **T12 needs exit criteria**: Define specific log patterns to check, thresholds for pass/fail
