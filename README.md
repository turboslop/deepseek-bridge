# DeepSeek Bridge

[![PyPI version](https://img.shields.io/pypi/v/deepseek-bridge)](https://pypi.org/project/deepseek-bridge/)
[![Python versions](https://img.shields.io/pypi/pyversions/deepseek-bridge)](https://pypi.org/project/deepseek-bridge/)
[![CI](https://github.com/breixopd/deepseek-bridge/actions/workflows/ci.yml/badge.svg)](https://github.com/breixopd/deepseek-bridge/actions/workflows/ci.yml)
[![License](https://img.shields.io/pypi/l/deepseek-bridge)](https://github.com/breixopd/deepseek-bridge/blob/main/LICENSE)

A local proxy that connects AI coding tools (Cursor, GitHub Copilot, Codex, and any OpenAI-compatible client) to DeepSeek's reasoning models by repairing the `reasoning_content` chain that these tools commonly drop from tool-call requests.

```bash
pip install deepseek-bridge
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
- Tunnel support (cloudflared by default, ngrok optional) with health check and automatic reconnection.
- Graceful shutdown on SIGTERM — active requests drain, reasoning cache is flushed.

### API Compatibility
- `system_fingerprint` in every streaming and non-streaming response.
- `x-request-id` UUID header on every response.
- OpenAI-standard error format.
- CORS headers enabled by default.
- `/healthz` liveness, `/readyz` readiness, `/v1/embeddings`, `/v1/health`, and `/v1/models` endpoints.
- `/v1/completions` legacy endpoint (auto-converts `prompt` to `messages`).
- Multimodal content arrays preserved.
- DeepSeek V4 thinking parameter support (`thinking`, `reasoning_effort`, `response_format`, `logprobs`).
- Silent mapping of legacy model names (`deepseek-chat`, `deepseek-reasoner`) to `deepseek-v4-flash`.

### Logging and Observability
- Persistent log files with `--log-dir`.
- Heartbeat and pool utilization counters.
- Full structured request traces with `--trace-dir`.

## Why This Exists

DeepSeek's thinking-mode API enforces a strict contract: every assistant message that participates in a tool-call chain must include the full `reasoning_content` field. Some AI coding tools (including Cursor) drop this field from their chat transcript, causing DeepSeek to reject subsequent tool-call requests.

DeepSeek Bridge stores copies of `reasoning_content` from every response and patches missing entries back into requests before forwarding them upstream.

## Installation

```bash
# From PyPI
pip install deepseek-bridge

# From source
git clone https://github.com/breixopd/deepseek-bridge.git
cd deepseek-bridge
uv run deepseek-bridge
```

### Usage

```bash
# Run the HTTP proxy
deepseek-bridge

# Run without tunnel (localhost only)
deepseek-bridge --tunnel none --port 9000

# Run as a Kubernetes workload
deepseek-bridge --runtime-mode kubernetes --host 0.0.0.0 --port 9000 --tunnel none

# Debug output with trace dumps
deepseek-bridge --debug --trace-dir ./dumps

# Use a custom config file
deepseek-bridge --config ./my-config.yaml

# Clear reasoning cache and exit
deepseek-bridge --clear-reasoning-cache

# Disable thinking display in client UI
deepseek-bridge --no-display-reasoning
```

On first run, DeepSeek Bridge creates:
- `~/.deepseek-bridge/config.yaml` — configuration file
- `~/.deepseek-bridge/reasoning_content.sqlite3` — reasoning cache

### Container Image

Build a local image from the repository root:

```bash
docker build --build-arg PACKAGE_VERSION=0.0.0+local -t deepseek-bridge:local .
```

Run it with Kubernetes-friendly defaults:

```bash
docker run --rm -d --name deepseek-bridge -p 9000:9000 deepseek-bridge:local
curl -fsS http://127.0.0.1:9000/healthz
docker exec deepseek-bridge id -u
docker stop deepseek-bridge
```

The image runs as UID `10001` and starts with a short command:

```bash
deepseek-bridge --config /etc/deepseek-bridge/config.yaml
```

Container defaults are supplied by `/etc/deepseek-bridge/config.yaml`: explicit
`runtime.mode: kubernetes`, no tunnel, `0.0.0.0:9000`, compact logs, no file
logs, and SQLite cache path `/data/reasoning_content.sqlite3`. Override with
`DEEPSEEK_BRIDGE_*` env vars or mount your own config file at that path. Mount
`/data` as an `emptyDir` or persistent volume if the root filesystem is
read-only or cache persistence is required. For read-only-root deployments, make
`/data` writable by UID/GID `10001`, for example with a pod `fsGroup` or volume
ownership setting.

### LiteLLM E2E Smoke Test

Run the live Docker Compose smoke test for this chain:

```text
OpenAI-compatible client -> LiteLLM Proxy -> DeepSeek Bridge -> DeepSeek Cloud
```

The test builds the local bridge image, starts LiteLLM with
`deepseek-v4-pro` routed to the bridge as an OpenAI-compatible provider, then
sends a real tool-call conversation to DeepSeek Cloud. The follow-up request
intentionally omits `reasoning_content`; the bridge runs in strict missing
reasoning mode, so the test only passes if the first response was cached and
the second turn was repaired.

```bash
export DEEPSEEK_API_KEY=sk-...
./scripts/litellm-e2e.sh
```

Or run it through unittest:

```bash
DEEPSEEK_BRIDGE_RUN_LITELLM_E2E=1 \
DEEPSEEK_API_KEY=sk-... \
  python -m unittest tests.test_litellm_e2e_compose -v
```

This is an opt-in live test. It requires Docker Compose, pulls the LiteLLM
container image if needed, and consumes real DeepSeek API quota. Set
`DEEPSEEK_BRIDGE_LITELLM_E2E_SKIP_CLEANUP=1` to keep containers for debugging.
In GitHub Actions, the `LiteLLM E2E (Compose, DeepSeek Cloud)` CI job runs on
pushes and manual workflow dispatches when the repository secret
`DEEPSEEK_API_KEY` is configured. Pull request events skip this live test so
untrusted PR contexts do not receive cloud credentials.

## Configuration

Configuration precedence is:

```text
built-in defaults < YAML config < DEEPSEEK_BRIDGE_* env vars < CLI flags
```

The default config path is `~/.deepseek-bridge/config.yaml`. Pass
`--config /path/to/deepseek-bridge.yaml` or set `DEEPSEEK_BRIDGE_CONFIG_PATH`
to use another file. Existing flat config files continue to work, but the
primary schema is structured YAML:

```yaml
version: 1

runtime:
  mode: local

server:
  host: 127.0.0.1
  port: 9000

upstream:
  base_url: https://api.deepseek.com
  model: deepseek-v4-pro
  thinking:
    mode: enabled
    reasoning_effort: max

storage:
  backend: sqlite
  sqlite:
    path: reasoning_content.sqlite3
  # valkey:
  #   url: valkey://localhost:6379/0
  #   key_prefix: deepseek-bridge

reasoning_cache:
  max_age_seconds: 604800
  max_entries: null
  missing_reasoning_strategy: recover

reasoning_display:
  enabled: true
  collapsible: true

logging:
  level: info
  format: text
  compact: false
  file:
    enabled: true
    path: null

metrics:
  enabled: false

tunnel:
  mode: cloudflared
  # cf_url: https://app.example.com
  # ngrok_url: https://my-tunnel.ngrok.app

cors:
  enabled: true
  allowed_origins:
    - http://localhost
    - http://localhost:*
    - http://127.0.0.1
    - http://127.0.0.1:*
  allow_credentials: true

ollama:
  enabled: true

performance:
  request_timeout: 300
  stream_read_timeout: 180
  max_request_body_bytes: 20971520
  max_pool_connections: 10
  max_thread_pool: 12
```

For browser clients served from a public tunnel or custom domain, add that
exact origin to `cors.allowed_origins`. Setting `allowed_origins: ["*"]` is
supported, but credentialed responses echo the request origin instead of
combining credentials with wildcard `*`.

YAML paths are relative to the config file. Environment-variable and CLI paths
are relative to the process working directory unless absolute.
`storage.backend` must be `sqlite` or `valkey`; Valkey storage requires
`storage.valkey.url` or `DEEPSEEK_BRIDGE_VALKEY_URL`. `logging.format` supports
`text` and `json`; use `DEEPSEEK_BRIDGE_LOG_FORMAT=json` for one-line
structured records in container logging stacks. Set `metrics.enabled` to
`true` to expose Prometheus metrics on `/metrics`.

Supported environment variables:

| Variable | Config key |
|----------|------------|
| `DEEPSEEK_BRIDGE_CONFIG_PATH` | Config file path |
| `DEEPSEEK_BRIDGE_RUNTIME_MODE` / `DEEPSEEK_BRIDGE_RUNTIME` | `runtime.mode` |
| `DEEPSEEK_BRIDGE_HOST` | `server.host` |
| `DEEPSEEK_BRIDGE_PORT` | `server.port` |
| `DEEPSEEK_BRIDGE_BASE_URL` / `DEEPSEEK_BRIDGE_UPSTREAM_BASE_URL` | `upstream.base_url` |
| `DEEPSEEK_BRIDGE_MODEL` / `DEEPSEEK_BRIDGE_UPSTREAM_MODEL` | `upstream.model` |
| `DEEPSEEK_BRIDGE_THINKING` | `upstream.thinking.mode` |
| `DEEPSEEK_BRIDGE_REASONING_EFFORT` | `upstream.thinking.reasoning_effort` |
| `DEEPSEEK_BRIDGE_STORAGE_BACKEND` | `storage.backend` |
| `DEEPSEEK_BRIDGE_REASONING_CONTENT_PATH` / `DEEPSEEK_BRIDGE_SQLITE_PATH` | `storage.sqlite.path` |
| `DEEPSEEK_BRIDGE_VALKEY_URL` | `storage.valkey.url` |
| `DEEPSEEK_BRIDGE_VALKEY_KEY_PREFIX` | `storage.valkey.key_prefix` |
| `DEEPSEEK_BRIDGE_REASONING_CACHE_MAX_AGE_SECONDS` | `reasoning_cache.max_age_seconds` |
| `DEEPSEEK_BRIDGE_REASONING_CACHE_MAX_ENTRIES` | `reasoning_cache.max_entries` |
| `DEEPSEEK_BRIDGE_MISSING_REASONING_STRATEGY` | `reasoning_cache.missing_reasoning_strategy` |
| `DEEPSEEK_BRIDGE_DISPLAY_REASONING` | `reasoning_display.enabled` |
| `DEEPSEEK_BRIDGE_COLLAPSIBLE_REASONING` | `reasoning_display.collapsible` |
| `DEEPSEEK_BRIDGE_LOG_LEVEL` / `DEEPSEEK_BRIDGE_DEBUG` | `logging.level` |
| `DEEPSEEK_BRIDGE_LOG_FORMAT` | `logging.format` |
| `DEEPSEEK_BRIDGE_COMPACT` | `logging.compact` |
| `DEEPSEEK_BRIDGE_LOG_FILE_ENABLED` | `logging.file.enabled` |
| `DEEPSEEK_BRIDGE_LOG_DIR` | `logging.file.path` |
| `DEEPSEEK_BRIDGE_TRACE_DIR` | `logging.trace_dir` |
| `DEEPSEEK_BRIDGE_METRICS_ENABLED` | `metrics.enabled` |
| `DEEPSEEK_BRIDGE_TUNNEL_MODE` / `DEEPSEEK_BRIDGE_TUNNEL` | `tunnel.mode` |
| `DEEPSEEK_BRIDGE_CF_URL` | `tunnel.cf_url` |
| `DEEPSEEK_BRIDGE_CFD_TUNNEL_NAME` | `tunnel.cfd_tunnel_name` |
| `DEEPSEEK_BRIDGE_NGROK_URL` | `tunnel.ngrok_url` |
| `DEEPSEEK_BRIDGE_CORS` / `DEEPSEEK_BRIDGE_CORS_ENABLED` | `cors.enabled` |
| `DEEPSEEK_BRIDGE_CORS_ALLOWED_ORIGINS` | `cors.allowed_origins` |
| `DEEPSEEK_BRIDGE_CORS_ALLOW_CREDENTIALS` | `cors.allow_credentials` |
| `DEEPSEEK_BRIDGE_OLLAMA` / `DEEPSEEK_BRIDGE_OLLAMA_ENABLED` | `ollama.enabled` |
| `DEEPSEEK_BRIDGE_REQUEST_TIMEOUT` | `performance.request_timeout` |
| `DEEPSEEK_BRIDGE_STREAM_READ_TIMEOUT` | `performance.stream_read_timeout` |
| `DEEPSEEK_BRIDGE_MAX_REQUEST_BODY_BYTES` | `performance.max_request_body_bytes` |
| `DEEPSEEK_BRIDGE_MAX_POOL_CONNECTIONS` | `performance.max_pool_connections` |
| `DEEPSEEK_BRIDGE_MAX_THREAD_POOL` | `performance.max_thread_pool` |

Container-only configuration can be expressed without long CLI invocations:

```bash
docker run --rm -p 9000:9000 \
  -e DEEPSEEK_BRIDGE_MODEL=deepseek-v4-pro \
  -e DEEPSEEK_BRIDGE_THINKING=enabled \
  -e DEEPSEEK_BRIDGE_REASONING_EFFORT=max \
  -e DEEPSEEK_BRIDGE_TUNNEL_MODE=none \
  deepseek-bridge:local
```

In Kubernetes, mount the YAML as a ConfigMap and put sensitive backend URLs in
Secrets-backed env vars. Use `storage.backend=valkey` for multi-replica shared
reasoning caches, or keep a writable mount when using SQLite.

## Kubernetes

Use Kubernetes mode for headless container execution:

```bash
deepseek-bridge --runtime-mode kubernetes
```

In Kubernetes mode, the proxy defaults to `0.0.0.0:9000`, disables tunnels,
logs only to stdout/stderr, skips auto-creating `~/.deepseek-bridge/config.yaml`,
and uses an in-memory SQLite reasoning cache unless Valkey or a SQLite file path
is configured. Set `DEEPSEEK_BRIDGE_LOG_FORMAT=json` when your cluster logging
stack expects one-line structured records. That allows a read-only root
filesystem by default. For multiple replicas, set
`DEEPSEEK_BRIDGE_STORAGE_BACKEND=valkey` and `DEEPSEEK_BRIDGE_VALKEY_URL` so all
pods share the same reasoning cache. If you want the SQLite reasoning cache to
survive process restarts, mount a writable directory and set the cache path to a
file inside that mount.

The published container image already sets `runtime.mode: kubernetes` in its
baked config. Workload manifests may still pass `--runtime-mode kubernetes`
explicitly when the deployment should pin the runtime profile regardless of
mounted config or environment overrides.

Use distinct probes:

- `/healthz` is a lightweight liveness check and returns 200 while the process is serving.
- `/readyz` returns 200 only when the server is accepting traffic, storage is usable, and shutdown is not draining.

An example Deployment and Service live in
[`examples/kubernetes/deployment.yaml`](examples/kubernetes/deployment.yaml).
The example sets `readOnlyRootFilesystem: true`, disables tunnels, binds to
`0.0.0.0`, uses separate liveness/readiness probes, and reads the Valkey URL
from a Kubernetes Secret so multiple replicas can share the reasoning cache. Set
`terminationGracePeriodSeconds` longer than your expected request or stream
duration so SIGTERM can drain active work.
The pod security context matches the image runtime user with `runAsUser: 10001`,
`runAsGroup: 10001`, and `fsGroup: 10001`. If your platform requires a different
UID/GID, override all three values together and ensure every writable mount used
by the cache, logs, traces, or temporary files is writable by that identity; keep
the root filesystem read-only.

### Helm

An installable chart lives in
[`charts/deepseek-bridge`](charts/deepseek-bridge):

```bash
helm install deepseek-bridge ./charts/deepseek-bridge
```

Tagged releases publish the Docker image and Helm chart to GHCR. For tag
`v0.1.0`, install the published chart with:

```bash
helm install deepseek-bridge oci://ghcr.io/turboslop/deepseek-bridge \
  --version 0.1.0-chart
```

The default chart values run one replica with an in-memory SQLite reasoning
cache for local/dev use. Use Valkey for multi-replica installs:

```bash
helm install deepseek-bridge ./charts/deepseek-bridge \
  --set replicaCount=2 \
  --set storage.backend=valkey \
  --set valkey.existingSecret=deepseek-bridge-valkey
```

The chart can also deploy a small built-in Valkey instance for development with
`--set valkey.enabled=true`. Enable Prometheus scraping with
`--set metrics.enabled=true --set serviceMonitor.enabled=true`.

Run the same Minikube packaging smoke gate used by CI with:

```bash
DEEPSEEK_BRIDGE_RUN_K8S_SMOKE=1 \
  python -m unittest tests.test_k8s_minikube_smoke -v
```

The smoke gate builds the local Docker image, loads that exact tag into
Minikube, lints and renders the chart, validates the rendered manifests with the
Kubernetes API, installs the release, waits for the app pod, checks Service
endpoints, and probes `/healthz` and `/readyz` through a Service port-forward.
It requires Docker, Helm, kubectl, Minikube, and curl.

## Client Setup

### Cursor

In Cursor, add a custom model with these settings:
- **Model**: `deepseek-v4-pro` (or `deepseek-v4-flash`)
- **API Key**: Your DeepSeek API key
- **Base URL**: Your tunnel HTTPS URL with `/v1` path (e.g., `https://app.example.com/v1`)

> **Note on tunnels**: Cursor blocks non-public URLs such as `localhost`. DeepSeek Bridge uses [Cloudflare Tunnel](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/) by default — a free, persistent HTTPS tunnel with no bandwidth or time limits. Use `--tunnel none` to disable tunneling. Use `--tunnel ngrok` if you prefer [ngrok](https://ngrok.com).

### Cloudflare Tunnel Setup

Cloudflare Named Tunnels are free, persistent, support SSE streaming, and have no bandwidth/time limits. One-time setup:

```bash
# Install cloudflared
brew install cloudflare/cloudflare/cloudflared   # macOS
# Or download from: https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/

# Login and create a tunnel
cloudflared tunnel login
cloudflared tunnel create deepseek-bridge

# Point it at your domain
cloudflared tunnel route dns deepseek-bridge app.example.com
```

Then add your tunnel URL to `~/.deepseek-bridge/config.yaml`:

```yaml
tunnel: cloudflared
cf_url: https://app.example.com
```

Use `--tunnel cloudflared` on the CLI.

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

Any client that speaks the OpenAI `/v1/chat/completions` API can use DeepSeek Bridge. Set the client's base URL to `http://localhost:9000/v1` (or your tunnel URL).

### Per-Request Thinking Overrides

`--thinking` and `--reasoning-effort` are process defaults. A request can
override them with DeepSeek's nested `thinking` payload:

```json
{
  "model": "deepseek-v4-pro",
  "messages": [{"role": "user", "content": "Plan this change"}],
  "thinking": {"type": "enabled", "reasoning_effort": "max"}
}
```

OpenAI Responses API payloads can also set `reasoning.effort`; during
conversion, the proxy maps it into DeepSeek's nested `thinking` payload and
does not forward a top-level `reasoning_effort` field. The effective thinking
mode and effort are included in the reasoning cache namespace, so switching
mode or effort does not reuse incompatible cached reasoning.

## How It Works

1. **Request interception**: The proxy receives a `/v1/chat/completions` request from the client.
2. **Format detection**: If the request uses OpenAI Responses API format (common in Cursor Agent Mode), it is converted to Chat Completions format.
3. **Reasoning repair**: Each assistant message in the conversation is checked. Missing `reasoning_content` fields are looked up in the local SQLite cache and restored.
4. **Cache isolation**: Cache keys are scoped by a SHA-256 hash of the conversation prefix, upstream model, effective thinking settings, configuration, and API key. Different conversations and users never collide.
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
uv run --extra dev --python 3.14 python -m unittest discover -s tests

# Run opt-in functional HTTP tests against a local Valkey
RUN_FUNCTIONAL_TESTS=1 \
FUNCTIONAL_VALKEY_URL=redis://127.0.0.1:6379/0 \
uv run --extra dev --python 3.14 python -m unittest discover -s tests/functional -v

# Format, lint, type-check, and YAML-check
uv run --extra dev --python 3.14 pre-commit run --all-files

# Strict type check for production code
uv run --extra dev --python 3.14 mypy src/deepseek_bridge

# Run with coverage gate
uv run --extra dev --python 3.14 coverage run -m unittest discover -s tests
uv run --extra dev --python 3.14 coverage report
```

## CLI Reference

| Flag | Default | Description |
|------|---------|-------------|
| `--model` | `deepseek-v4-pro` | Fallback model when request omits it |
| `--runtime-mode` | `local` | Runtime profile (`local`, `kubernetes`) |
| `--thinking` | `enabled` | DeepSeek thinking mode |
| `--reasoning-effort` | `max` | Reasoning effort level |
| `--display-reasoning` | on | Show reasoning content in client UI |
| `--collapsible-reasoning` | on | Use collapsible Markdown for reasoning |
| `--host` | `127.0.0.1` | Bind address |
| `--port` | `9000` | Bind port |
| `--tunnel` | `cloudflared` | Tunnel service (none, cloudflared, ngrok) |
| `--cf-url` | none | Cloudflare tunnel public URL |
| `--ngrok-url` | none | Fixed ngrok endpoint URL |
| `--base-url` | `https://api.deepseek.com` | Upstream DeepSeek API URL |
| `--cors` | on | Send CORS headers |
| `--cors-allowed-origin` | loopback origins | Allowed browser Origin; repeat for multiple origins |
| `--cors-allow-credentials` | on | Allow browser credentials for matching CORS origins |
| `--stream-read-timeout` | `180` | SSE read timeout in seconds |
| `--max-thread-pool` | `20` | Max concurrent request threads |
| `--max-pool-connections` | `10` | Max upstream connections |
| `--ollama` / `--no-ollama` | on | Enable/disable Ollama endpoints |
| `--log-dir` | none | Directory for persistent log files |
| `--trace-dir` | none | Directory for request trace dumps |
| `--debug` | off | Enable DEBUG-level log output |
| `--compact` | off | One-line-per-request output |
| `--headless` | off | Disable interactive terminal UI affordances |
| `--config` | ~/.deepseek-bridge/config.yaml | Config file path |
| `--no-log` | off | Disable all log file output |
| `--reasoning-content-path` | ~/.deepseek-bridge/reasoning_content.sqlite3 | Reasoning cache path |
| `--reasoning-cache-max-age-seconds` | 604800 | Max age of cached reasoning (seconds) |
| `--missing-reasoning-strategy` | recover | Strategy for missing reasoning (recover/reject) |
| `--max-request-body-bytes` | 20971520 | Max request body size in bytes |
| `--clear-reasoning-cache` | off | Clear reasoning cache and exit |
| `--version` | - | Print version and exit |

## License

MIT License

## Acknowledgements

Based on [yxlao/deepseek-cursor-proxy](https://github.com/yxlao/deepseek-cursor-proxy), the original project that started this work.
