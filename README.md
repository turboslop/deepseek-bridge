<!-- <h1><img src="assets/logo.png" width="120" alt="deepseek-cursor-proxy logo" style="vertical-align: middle;">&nbsp;DeepSeek Cursor Proxy</h1> -->
<h1 align="center"><img src="assets/logo.png" width="150" alt="deepseek-cursor-proxy logo"><br>DeepSeek Cursor Proxy</h1>

A compatibility proxy that connects Cursor to DeepSeek thinking models (`deepseek-v4-pro` and `deepseek-v4-flash`) by properly handling the `reasoning_content` field for DeepSeek tool-call reasoning API requests.

This proxy can also help **other applications and coding agents** beyond Cursor that run into the same missing `reasoning_content` issue with DeepSeek's thinking-mode API. Just point their API base URL at the proxy.

## What It Does

**Core Reasoning Fix:**
- ✅ Injects `reasoning_content` into outgoing tool-call requests since Cursor does not include the field, restoring previously cached reasoning from regular and streamed DeepSeek responses. See [DeepSeek docs](https://api-docs.deepseek.com/guides/thinking_mode#tool-calls) for more details.
- ✅ Displays DeepSeek's thinking tokens in Cursor by forwarding them into Cursor-visible collapsible Markdown `<details><summary>Thinking</summary>...</details>` blocks.

**Connection Resilience:**
- ✅ Connection pooling via `urllib3` (replaces one-shot `urlopen`) with keep-alive and minimal retries.
- ✅ Bounded thread pool prevents thread exhaustion on long-running streaming connections.
- ✅ Configurable SSE read timeout (`--stream-read-timeout`, default 180s) prevents hung threads on silent upstreams.
- ✅ Ngrok tunnel health check with auto-reconnect (`--ngrok-health-check-interval`).
- ✅ Graceful shutdown on SIGTERM — active requests drain, reasoning cache is flushed.
- ✅ GitHub Copilot integration via Ollama-compatible endpoints (enabled by default).

**OpenAI API Compatibility:**
- ✅ `system_fingerprint` in every streaming and non-streaming response.
- ✅ `x-request-id` UUID header on every response.
- ✅ OpenAI-standard error format: `{"error": {"message", "type", "code", "param": null}}`.
- ✅ CORS headers enabled by default.
- ✅ `/v1/embeddings` endpoint for Cursor @Codebase search.
- ✅ `/v1/health` endpoint with uptime tracking.
- ✅ `/v1/completions` legacy endpoint alias (auto-converts `prompt` to `messages`).
- ✅ Multimodal content arrays preserved (no longer flattened to text).
- ✅ Stable `/v1/models` timestamps.

**Logging:**
- ✅ Persistent log files with `--log-dir <path>` — timestamped per-launch, auto-purges old files (keeps last 5).
- ✅ All errors logged at WARNING level without `--verbose`.
- ✅ Heartbeat and pool utilization counters.
- ✅ Full structured request traces with `--trace-dir` (one JSON file per request).

## Why This Exists

This repository fixes the following Cursor + DeepSeek tool-call error with thinking mode enabled:

<img src="assets/error_400.png" width="600" alt="Error 400 - reasoning_content must be passed back">

```txt
⚠️ Connection Error
Provider returned error:
{
  "error": {
    "message": "The reasoning_content in the thinking mode must be passed back to the API.",
    "type": "invalid_request_error",
    "param": null,
    "code": "invalid_request_error"
  }
}
```

## Usage

### Step 1: Set Up ngrok

Cursor blocks non-public API URLs such as `localhost`, so the proxy needs a public HTTPS URL. [ngrok](https://ngrok.com/) can expose the local proxy to Cursor without opening router ports. Alternatively, you may use [Cloudflare Tunnel](https://developers.cloudflare.com/tunnel/setup/). Create an ngrok account and visit [ngrok's dashboard](https://dashboard.ngrok.com). You will find the authtoken and public URL there.

If you're using this proxy with another application that allows localhost API endpoints, you can skip this step entirely by setting `ngrok: false` in `~/.deepseek-cursor-proxy/config.yaml`, or by starting the proxy with `--no-ngrok`.

<img src="assets/ngrok_dashboard.png" width="600" alt="ngrok dashboard">

Then, install and authenticate ngrok once:

```bash
brew install ngrok
ngrok config add-authtoken <your-ngrok-token>
```

### Step 2: Install and Start the Proxy Server

**Run with UV**

```bash
# Install uv if you don't have it
curl -LsSf https://astral.sh/uv/install.sh | sh

# Install and start
# uv installs the program in .venv/ under the repo local folder
git clone https://github.com/yxlao/deepseek-cursor-proxy.git
cd deepseek-cursor-proxy
uv run deepseek-cursor-proxy
```

**Run with Conda**

```bash
# Install conda if you don't have it
# Follow: https://www.anaconda.com/docs/getting-started/miniconda/install/overview

# Install
conda create -n dcp python=3.10 -y
conda activate dcp
git clone https://github.com/yxlao/deepseek-cursor-proxy.git
cd deepseek-cursor-proxy
pip install -e .

# Start
deepseek-cursor-proxy
```

When ngrok is enabled, `deepseek-cursor-proxy` will print the ngrok public URL on start. If it differs from the one in Cursor, update it in Cursor's Base URL field.

On the first run, `deepseek-cursor-proxy` will create:

- `~/.deepseek-cursor-proxy/config.yaml`: the configuration file
- `~/.deepseek-cursor-proxy/reasoning_content.sqlite3`: the reasoning content cache

Persistent settings live in `~/.deepseek-cursor-proxy/config.yaml`. You can also override the config with command-line flags, for example:

```bash
# Hide thinking tokens displaying in Cursor UI
deepseek-cursor-proxy --no-display-reasoning

# Show full incoming and outgoing requests
deepseek-cursor-proxy --verbose

# Run without ngrok (run on localhost directly)
deepseek-cursor-proxy --no-ngrok

# Use a different local port
deepseek-cursor-proxy --port 9000
```

### Step 3: Add Cursor Custom Model

In Cursor, add the DeepSeek custom model and point it at this proxy:

- Model: `deepseek-v4-pro`
- API Key: your DeepSeek API key
- Base URL: your ngrok HTTPS URL with the `/v1` API version path

The proxy respects the DeepSeek model name Cursor sends, such as `deepseek-v4-pro` or `deepseek-v4-flash`. The `model` field in `config.yaml` is used as a fallback only when a request does not include a model.

For example, if ngrok dashboard shows `https://example.ngrok-free.dev`, use:

```text
https://example.ngrok-free.dev/v1
```

<img src="assets/cursor_config.png" width="600" alt="Cursor settings for DeepSeek through the proxy">

Note: you can toggle the custom API on and off with:

- macOS: `Cmd+Shift+0`
- Windows/Linux: `Ctrl+Shift+0`

### Step 4: Chat with DeepSeek in Cursor

Select `deepseek-v4-pro` in Cursor and use chat or agent mode as usual.

<img src="assets/cursor_chat.png" width="480" alt="Chatting with DeepSeek in Cursor">

## How It Works

- **Core fix:** DeepSeek [thinking-mode tool calls](https://api-docs.deepseek.com/guides/thinking_mode#tool-calls) require the complete **multi-round** `reasoning_content` chain to be sent back in later requests. Cursor omits that field, causing a 400 error. The proxy (`Cursor -> ngrok -> proxy -> DeepSeek API`) stores DeepSeek's original `reasoning_content` and patches missing blocks back into outgoing tool-call history.
- **Multi-conversation isolation:** To avoid collisions across concurrent conversations, the proxy scopes cache keys by a SHA-256 hash of the canonical conversation prefix (roles, content, and tool calls, excluding `reasoning_content`) plus the upstream model, configuration, and an API-key hash. Different threads get different scopes, so reused tool-call IDs do not collide. Byte-identical cloned histories produce identical scopes.
- **Context caching compatibility:** The proxy preserves compatibility by never injecting synthetic thread IDs, timestamps, or cache-control messages. It restores `reasoning_content` as the exact original string, so repeated prefixes remain intact for [DeepSeek context cache](https://api-docs.deepseek.com/guides/kv_cache). Cache hit rates are logged in the terminal output.
- **Additional compatibility fixes:** Beyond reasoning repair, the proxy converts legacy `functions`/`function_call` fields to `tools`/`tool_choice`, preserves required and named tool-choice semantics, normalizes `reasoning_effort` aliases, strips mirrored thinking display blocks from assistant content, flattens multi-part content arrays to plain text, and mirrors `reasoning_content` into Cursor-visible Markdown details blocks.

## GitHub Copilot Integration

The proxy acts as an Ollama-compatible server for GitHub Copilot Chat in VS Code. Copilot uses the Ollama-native API for model discovery and the OpenAI-compatible `/v1/chat/completions` for inference — both are supported by default.

### Setup

In VS Code, configure Copilot to use your local proxy:

```json
{
  "github.copilot.chat.byok.ollamaEndpoint": "http://localhost:9000"
}
```

Then open Copilot Chat → "Manage Models" → "Add Models" → your DeepSeek models appear automatically.

### Agent Mode Support

The `/api/show` endpoint advertises `"tools"` capability, enabling full Agent Mode in Copilot. The proxy's reasoning repair pipeline ensures tool-call chains work correctly.

### Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/version` | GET | Ollama version check (returns `0.18.3`) |
| `/api/tags` | GET | Model list in Ollama format |
| `/api/show` | POST | Model capabilities (includes `tools` for Agent Mode) |

Disable Ollama endpoints with `--no-ollama`.

## Known Limitations

### Cursor Sub-Agents

Cursor sub-agents do **not** inherit custom API base URL or API key settings — this is a Cursor-side bug (see [forum thread](https://forum.cursor.com/t/sub-agents-are-not-using-custom-openai-base-urls/152574)). When Cursor spawns a sub-agent (e.g., during multi-file edits), it routes through Cursor's own servers rather than your custom endpoint.

**Workaround**: There is currently no proxy-side fix for sub-agent routing. The proxy ensures perfect OpenAI API compliance so that when sub-agents DO route through the proxy, they work correctly. Use the main agent (Cmd+Shift+0 to toggle) for direct DeepSeek chat, and sub-agents will fall back to Cursor's built-in models.

### Reasoning Display

Cursor's native reasoning UI (brain icon / thought bubble) is only available for Cursor's own models. For BYOK/custom endpoints, DeepSeek's `reasoning_content` is forwarded in SSE chunks (for potential future native support) and mirrored into visible Markdown `<details>` blocks as a real-time workaround.

Use `--no-display-reasoning` (or its alias `--no-markdown-reasoning`) to hide the Markdown mirroring if you prefer to only use the native SSE field.

## Development

Run unit tests:

```bash
uv run python -m unittest discover -s tests
```

Run pre-commit hooks (code formatting and linting):

```bash
uv sync --dev
uv run pre-commit run --all-files
```

## Debugging

Run with verbose output:

```bash
deepseek-cursor-proxy --verbose
```

Run without ngrok for local curl testing:

```bash
deepseek-cursor-proxy --no-ngrok --port 9000 --verbose
```

Capture full structured request traces for debugging:

```bash
deepseek-cursor-proxy --verbose --trace-dir ./trace-dumps
```

Use another config file:

```bash
deepseek-cursor-proxy --config ./dev.config.yaml
```

Clear the local reasoning cache:

```bash
deepseek-cursor-proxy --clear-reasoning-cache
```

Persist logs to a directory for debugging:

```bash
deepseek-cursor-proxy --log-dir ~/proxy-logs
# Each launch creates a timestamped file, auto-purges old logs (keeps last 5)
cat ~/proxy-logs/proxy-*.log | grep -E "WARNING|ERROR|disconnected"
```

Full CLI reference:

```bash
deepseek-cursor-proxy --help
```

Key flags:
| Flag | Default | Description |
|------|---------|-------------|
| `--stream-read-timeout` | 180 | SSE read timeout in seconds |
| `--max-thread-pool` | 20 | Max concurrent request threads |
| `--max-pool-connections` | 10 | Max upstream connections |
| `--ngrok-health-check-interval` | 30 | Tunnel health check interval (0=disable) |
| `--log-dir` | none | Directory for persistent log files |
| `--ollama` | on | Enable Ollama endpoints (/api/version, /api/tags, /api/show) |
| `--no-log` | off | Disable persistent log files |
| `--compact` | off | Compact 1-line-per-request output |
