<h1 align="center"><img src="assets/logo.png" width="150" alt="deepseek-bridge logo"><br>DeepSeek Bridge</h1>

A local proxy that connects AI coding tools (Cursor, GitHub Copilot, Codex, and any OpenAI-compatible client) to DeepSeek's reasoning models by repairing the `reasoning_content` chain that these tools commonly drop from tool-call requests.

```bash
pip install deepseek-bridge     # minimal
pip install deepseek-bridge[tui] # with Terminal UI dashboard
```

DeepSeek's [thinking-mode API](https://api-docs.deepseek.com/guides/thinking_mode#tool-calls) requires every assistant message in a multi-turn tool-call conversation to carry its complete `reasoning_content` back to the server. When a client omits this field, the API returns a 400 error. DeepSeek Bridge intercepts requests, restores the missing reasoning from a local cache, and forwards them upstream — no client-side changes needed.

## Features

### Reasoning Repair
- Injects `reasoning_content` into outgoing tool-call requests, restoring previously cached reasoning from regular and streamed DeepSeek responses.
- Displays thinking tokens in the client UI using collapsible Markdown `<details>` blocks.
- Cursor Agent Mode support: automatically converts Responses API payloads to Chat Completions format.

### Connection Resilience
- Connection pooling via `urllib3` with keep-alive and minimal retries.
- Bounded thread pool prevents thread exhaustion on long-running streaming connections.
- Configurable SSE read timeout (default 180 seconds) prevents hung threads on silent upstreams.
- Ngrok tunnel health check with automatic reconnection.
- Graceful shutdown on SIGTERM — active requests drain, reasoning cache is flushed.

### API Compatibility
- `system_fingerprint` in every streaming and non-streaming response.
- `x-request-id` UUID header on every response.
- OpenAI-standard error format.
- CORS headers enabled by default.
- `/v1/embeddings`, `/v1/health`, and `/v1/models` endpoints.
- `/v1/completions` legacy endpoint (auto-converts `prompt` to `messages`).
- Multimodal content arrays preserved.
- DeepSeek V4 thinking parameter support (`thinking`, `reasoning_effort`, `response_format`, `logprobs`).
- Silent mapping of legacy model names (`deepseek-chat`, `deepseek-reasoner`) to `deepseek-v4-flash`.

### Logging and Observability
- Persistent log files with `--log-dir`.
- Heartbeat and pool utilization counters.
- Full structured request traces with `--trace-dir`.
- Terminal UI dashboard with real-time metrics, config editing, and log viewing.

## TUI Dashboard

Starting with v0.2.0, DeepSeek Bridge opens a Terminal UI dashboard by default. The dashboard provides live monitoring and configuration:

- **Dashboard tab** — Real-time request metrics, uptime, ngrok status, and pool utilization.
- **Config tab** — Edit proxy settings (model, network, storage) without restarting.
- **Logs tab** — Streaming log viewer with filtering and search.

Use `--headless` to disable the TUI and run in classic CLI mode.

## Why This Exists

DeepSeek's thinking-mode API enforces a strict contract: every assistant message that participates in a tool-call chain must include the full `reasoning_content` field. Some AI coding tools (including Cursor) drop this field from their chat transcript, causing DeepSeek to reject subsequent tool-call requests.

DeepSeek Bridge stores copies of `reasoning_content` from every response and patches missing entries back into requests before forwarding them upstream.

## Installation

Install with `uv` (recommended) or `pip`:

```bash
# From PyPI
pip install deepseek-bridge

# With TUI dashboard
pip install deepseek-bridge[tui]

# From source
git clone https://github.com/breixopd/deepseek-bridge.git
cd deepseek-bridge
uv run deepseek-bridge
```

### Quick Start

```bash
# Run without ngrok for local testing
deepseek-bridge --no-ngrok --port 9000

# With verbose output
deepseek-bridge --verbose

# Disable thinking display in the client UI
deepseek-bridge --no-display-reasoning

# Use a different local port
deepseek-bridge --port 9000
```

On first run, DeepSeek Bridge creates:
- `~/.deepseek-bridge/config.yaml` — configuration file
- `~/.deepseek-bridge/reasoning_content.sqlite3` — reasoning cache

## Configuration

All settings are configurable via `~/.deepseek-bridge/config.yaml` or command-line overrides. Example configuration:

```yaml
model: deepseek-v4-pro
base_url: https://api.deepseek.com
thinking: enabled
reasoning_effort: max
display_reasoning: true
collapsible_reasoning: true

host: 127.0.0.1
port: 9000
ngrok: true
verbose: false
cors: true
request_timeout: 300
```

## Client Setup

### Cursor

In Cursor, add a custom model with these settings:
- **Model**: `deepseek-v4-pro` (or `deepseek-v4-flash`)
- **API Key**: Your DeepSeek API key
- **Base URL**: Your ngrok HTTPS URL with `/v1` path (e.g., `https://example.ngrok-free.dev/v1`)



> **Note on ngrok**: Cursor blocks non-public URLs such as `localhost`. Use [ngrok](https://ngrok.com) or [Cloudflare Tunnel](https://developers.cloudflare.com/tunnel/setup) to expose the proxy. If your client supports localhost endpoints, disable ngrok with `--no-ngrok`.

### GitHub Copilot

Configure the Ollama endpoint in VS Code:

```json
{
  "github.copilot.chat.byok.ollamaEndpoint": "http://localhost:9000"
}
```

Then open Copilot Chat, navigate to "Manage Models", and your DeepSeek models appear automatically.

Agent Mode is supported — the proxy advertises `tool_calls` capability via the Ollama `/api/show` endpoint and handles reasoning repair across tool-call chains.

For the new `customOAIModels` path (VS Code Insiders 1.104+):

```json
{
  "github.copilot.chat.customOAIModels": {
    "deepseek-v4-pro": {
      "name": "DeepSeek V4 Pro",
      "url": "http://localhost:9000/v1/chat/completions",
      "toolCalling": true,
      "vision": false,
      "thinking": true,
      "maxInputTokens": 1000000,
      "maxOutputTokens": 384000,
      "streaming": true,
      "requiresAPIKey": true
    }
  }
}
```

### Other OpenAI-Compatible Clients

Any client that speaks the OpenAI `/v1/chat/completions` API can use DeepSeek Bridge. Set the client's base URL to `http://localhost:9000/v1` (or your ngrok URL).

## How It Works

1. **Request interception**: The proxy receives a `/v1/chat/completions` request from the client.
2. **Format detection**: If the request uses OpenAI Responses API format (common in Cursor Agent Mode), it is converted to Chat Completions format.
3. **Reasoning repair**: Each assistant message in the conversation is checked. Missing `reasoning_content` fields are looked up in the local SQLite cache and restored.
4. **Cache isolation**: Cache keys are scoped by a SHA-256 hash of the conversation prefix, upstream model, configuration, and API key. Different conversations and users never collide.
5. **Response processing**: Reasoning content from the upstream response is cached for future requests. Display adapters mirror reasoning thoughts into Markdown `<details>` blocks visible in the client.

## Known Limitations

### Cursor Sub-Agents

Cursor sub-agents do not inherit custom API base URL or API key settings. This is a Cursor-side bug (see [forum thread](https://forum.cursor.com/t/sub-agents-are-not-using-custom-openai-base-urls/152574)). Use the main agent (`Cmd+Shift+0` to toggle) for direct DeepSeek chat. Sub-agents that route through the proxy will work correctly; those that bypass it fall back to Cursor's built-in models.

### Cursor Agent Mode Responses API Format

Cursor Agent mode sends OpenAI Responses API-format payloads to the Chat Completions endpoint. DeepSeek Bridge detects and converts these automatically.

### Reasoning Display

Cursor's native reasoning UI is available only for Cursor's own models. For custom endpoints, reasoning content is mirrored into visible Markdown details blocks. Use `--no-display-reasoning` to disable this behavior.

## Development

```bash
# Run tests
uv run python -m unittest discover -s tests

# Format and lint
uv run pre-commit run --all-files

# Type check
uv run mypy src/ --check-untyped-defs

# Run with coverage
uv run coverage run -m unittest discover -s tests
uv run coverage report
```

## CLI Reference

| Flag | Default | Description |
|------|---------|-------------|
| `--tui` | on | Terminal UI dashboard |
| `--headless` | off | Run without TUI |
| `--model` | `deepseek-v4-pro` | Fallback model when request omits it |
| `--thinking` | `enabled` | DeepSeek thinking mode |
| `--reasoning-effort` | `max` | Reasoning effort level |
| `--display-reasoning` | on | Show reasoning content in client UI |
| `--collapsible-reasoning` | on | Use collapsible Markdown for reasoning |
| `--host` | `127.0.0.1` | Bind address |
| `--port` | `9000` | Bind port |
| `--ngrok` | on | Start ngrok tunnel |
| `--base-url` | `https://api.deepseek.com` | Upstream DeepSeek API URL |
| `--cors` | on | Send CORS headers |
| `--stream-read-timeout` | `180` | SSE read timeout in seconds |
| `--max-thread-pool` | `20` | Max concurrent request threads |
| `--max-pool-connections` | `10` | Max upstream connections |
| `--ngrok-health-check-interval` | `30` | Tunnel health check interval in seconds |
| `--ollama` / `--no-ollama` | on | Enable/disable Ollama endpoints |
| `--log-dir` | none | Directory for persistent log files |
| `--trace-dir` | none | Directory for request trace dumps |
| `--verbose` | off | Detailed request logging |
| `--compact` | off | One-line-per-request output |
| `--clear-reasoning-cache` | off | Clear reasoning cache and exit |
| `--version` | - | Print version and exit |

## License

MIT License

## Acknowledgements

Based on [yxlao/deepseek-cursor-proxy](https://github.com/yxlao/deepseek-cursor-proxy), the original project that started this work.
