# DeepSeek Bridge

[![CI](https://github.com/turboslop/deepseek-bridge/actions/workflows/ci.yml/badge.svg)](https://github.com/turboslop/deepseek-bridge/actions/workflows/ci.yml)
[![License](https://img.shields.io/github/license/turboslop/deepseek-bridge)](https://github.com/turboslop/deepseek-bridge/blob/main/LICENSE)

OpenAI-compatible HTTP stateful adapter for the DeepSeek V4 (DS4) reasoning
protocol.

DeepSeek Bridge runs as a local service, sidecar, container, or Kubernetes
workload. OpenAI-compatible clients send ordinary `/v1` HTTP requests to the
adapter; the adapter forwards them to DeepSeek, preserves the reasoning state
required by DS4 tool-call conversations, and repairs follow-up turns when the
client did not keep `reasoning_content`.

This is not a chat UI, TUI, or workflow CLI. The `deepseek-bridge` command is
only the process runner for the HTTP adapter.

```bash
docker run --rm -p 9000:9000 ghcr.io/turboslop/deepseek-bridge:latest
```

Point any OpenAI-compatible client at:

```text
http://127.0.0.1:9000/v1
```

and use your DeepSeek API key as the client's normal bearer token. The adapter
does not need a separate API-key setting for ordinary use; it forwards the
client's `Authorization: Bearer ...` header upstream.

## What It Adapts

DeepSeek thinking/tool-call conversations have a stricter state contract than
the common OpenAI-compatible transcript shape. In multi-turn tool-call flows,
DeepSeek expects assistant messages to be replayed with their full
`reasoning_content`. Many OpenAI-compatible clients keep only the visible
assistant message and tool calls, then drop the hidden reasoning field. DeepSeek
then rejects the next request.

DeepSeek Bridge sits between the client and DeepSeek:

```text
OpenAI-compatible client
  -> DeepSeek Bridge /v1
  -> DeepSeek API
```

The adapter records `reasoning_content` from DeepSeek responses, scopes it to
the conversation and effective model/thinking settings, and restores it into
later tool-call requests before forwarding them upstream.

## HTTP Surface

The primary interface is HTTP, not terminal interaction.

| Client endpoint | Purpose |
| --- | --- |
| `/v1/chat/completions` | Main OpenAI-compatible chat endpoint with DS4 reasoning repair |
| `/v1/completions` | Legacy completions compatibility; prompts are converted to messages |
| `/v1/embeddings` | Embeddings passthrough |
| `/v1/models` | Model metadata for OpenAI-compatible clients |
| `/healthz` | Liveness probe |
| `/readyz` | Readiness probe including storage health |
| `/metrics` | Prometheus metrics when enabled |
| `/api/version`, `/api/tags`, `/api/show` | Ollama-compatible endpoints for clients such as GitHub Copilot BYOK |

Cursor Agent Mode is also supported when it sends OpenAI Responses-style
payloads to the Chat Completions endpoint; the adapter converts those payloads
before forwarding them to DeepSeek.

## Core Capabilities

- Stateful DS4 reasoning repair across streamed and non-streamed tool-call
  conversations.
- SQLite storage for local single-process use.
- Valkey storage for shared multi-replica deployments.
- OpenAI-compatible response shape, errors, request IDs, CORS, model metadata,
  and streaming behavior.
- DeepSeek V4 thinking controls: `thinking`, `reasoning_effort`,
  `response_format`, `logprobs`, and legacy DeepSeek model aliases.
- Optional visible reasoning mirror for clients that cannot render native
  reasoning UI.
- ASGI/Uvicorn runtime, async upstream HTTP transport, SSE read timeouts,
  bounded transport retries, graceful draining, health/readiness endpoints, and
  Prometheus metrics.
- Container and Helm deployment support for Kubernetes-native operation.

## Runtime Architecture

DeepSeek Bridge runs as a Starlette ASGI application served by Uvicorn. Upstream
DeepSeek calls use an async `httpx` client with configured connection limits,
request/stream timeouts, and bounded retries for connection and transport
failures before a usable upstream response exists.

## Trust and Release Posture

- Python 3.14 is the intentionally supported runtime for source, CI, and
  container builds.
- GitHub Releases, GHCR container images, and Helm chart assets are the
  authoritative release channels. PyPI publishing is not part of the current
  release workflow.
- Release history and migration notes live in [CHANGELOG.md](CHANGELOG.md).
- Threat model and production security guidance live in
  [SECURITY.md](SECURITY.md).
- AI-assisted contribution expectations live in
  [AI_ASSISTED_DEVELOPMENT.md](AI_ASSISTED_DEVELOPMENT.md).

## Quick Start

Run the released container locally:

```bash
docker run --rm -p 9000:9000 ghcr.io/turboslop/deepseek-bridge:latest
```

Check that the HTTP service is alive:

```bash
curl -fsS http://127.0.0.1:9000/healthz
```

Send a request through the adapter:

```bash
curl -fsS http://127.0.0.1:9000/v1/chat/completions \
  -H "Authorization: Bearer ${DEEPSEEK_API_KEY}" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "deepseek-v4-pro",
    "messages": [
      {"role": "user", "content": "Plan a small refactor."}
    ],
    "thinking": {"type": "enabled", "reasoning_effort": "max"},
    "stream": true
  }'
```

Use the same base URL and API key in any OpenAI-compatible SDK:

```python
from openai import OpenAI

client = OpenAI(
    api_key="sk-...",
    base_url="http://127.0.0.1:9000/v1",
)

response = client.chat.completions.create(
    model="deepseek-v4-pro",
    messages=[{"role": "user", "content": "Explain this adapter."}],
)
```

## Running From Source

Source runs require Python 3.14:

```bash
git clone https://github.com/breixopd/deepseek-bridge.git
cd deepseek-bridge
uv run --python 3.14 deepseek-bridge --port 9000
```

On first local run, the adapter creates:

- `~/.deepseek-bridge/config.yaml` - structured runtime configuration.
- `~/.deepseek-bridge/reasoning_content.sqlite3` - local reasoning state cache.

## Client Setup

### Generic OpenAI-Compatible Clients

Set the client's base URL to:

```text
http://127.0.0.1:9000/v1
```

If the client runs outside the machine or cluster, use the adapter's reachable
service URL instead, for example:

```text
https://deepseek-bridge.example.com/v1
```

Set the client's API key to your DeepSeek API key. The adapter forwards that
bearer token to DeepSeek and uses it as part of the reasoning-cache namespace,
so separate users or keys do not share reasoning entries.

### Cursor

Add a custom OpenAI-compatible model:

- **Model**: `deepseek-v4-pro` or `deepseek-v4-flash`
- **API Key**: your DeepSeek API key
- **Base URL**: `http://127.0.0.1:9000/v1` if reachable, otherwise your
  reverse-proxy, Ingress, or service URL with `/v1`

### GitHub Copilot BYOK / Ollama Compatibility

For the Ollama-compatible path in VS Code:

```json
{
  "github.copilot.chat.byok.ollamaEndpoint": "http://127.0.0.1:9000"
}
```

For the `customOAIModels` path:

```json
{
  "github.copilot.chat.customOAIModels": {
    "deepseek-v4-pro": {
      "name": "DeepSeek V4 Pro",
      "url": "http://127.0.0.1:9000/v1/chat/completions",
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

### LiteLLM

LiteLLM can route an OpenAI-compatible model to DeepSeek Bridge, which then
adapts the DS4 reasoning protocol before sending traffic to DeepSeek Cloud:

```text
OpenAI-compatible client -> LiteLLM Proxy -> DeepSeek Bridge -> DeepSeek Cloud
```

The repository includes an opt-in live Docker Compose smoke test for this
chain. It builds the local bridge image, starts LiteLLM with `deepseek-v4-pro`
routed to the adapter as an OpenAI-compatible provider, sends a real tool-call
conversation to DeepSeek Cloud, and then sends a follow-up request that
intentionally omits `reasoning_content`. The test passes only if the first
response was cached and the second turn was repaired.

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

This test consumes real DeepSeek API quota and requires Docker Compose. Set
`DEEPSEEK_BRIDGE_LITELLM_E2E_SKIP_CLEANUP=1` to keep containers for debugging.

## Configuration

Configuration precedence:

```text
built-in defaults < YAML config < DEEPSEEK_BRIDGE_* env vars < process flags
```

The default config path is `~/.deepseek-bridge/config.yaml`. Pass
`--config /path/to/deepseek-bridge.yaml` or set `DEEPSEEK_BRIDGE_CONFIG_PATH`
to use another file. Older flat config files still load, but the preferred
schema is structured YAML:

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
  trace_mode: metadata-only
  file:
    enabled: true
    path: null

metrics:
  enabled: false

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
  upstream_retry_attempts: 2
  upstream_retry_initial_delay_seconds: 1
  upstream_retry_max_delay_seconds: 4
  upstream_retry_jitter_seconds: 0.25
  max_request_body_bytes: 20971520
  max_pool_connections: 10
  max_thread_pool: 12
```

YAML paths are relative to the config file. Environment-variable and process
flag paths are relative to the process working directory unless absolute.

`storage.backend` must be `sqlite` or `valkey`. Valkey storage requires
`storage.valkey.url` or `DEEPSEEK_BRIDGE_VALKEY_URL`.

`logging.format` supports `text` and `json`. Use
`DEEPSEEK_BRIDGE_LOG_FORMAT=json` for one-line structured logs in container
logging stacks. Set `metrics.enabled` or `DEEPSEEK_BRIDGE_METRICS_ENABLED=true`
to expose Prometheus metrics on `/metrics`.

`logging.trace_mode` controls how much data is written when `logging.trace_dir`
or `--trace-dir` is enabled:

- `metadata-only` records request/response metadata, byte counts, and safe
  summaries without prompt, tool argument, response, reasoning, or stream chunk
  content.
- `redacted` records structural payload summaries and hashes for debugging
  without full content.
- `full` records full request/response bodies and stream chunks. Use it only
  for deliberate short-lived debugging in trusted environments.

For browser clients served from a custom domain, add that exact origin to
`cors.allowed_origins`. `allowed_origins: ["*"]` is supported, but credentialed
responses echo the request origin instead of combining credentials with wildcard
`*`.

### Environment Variables

| Variable | Config key |
| --- | --- |
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
| `DEEPSEEK_BRIDGE_TRACE_MODE` | `logging.trace_mode` |
| `DEEPSEEK_BRIDGE_LOG_FILE_ENABLED` | `logging.file.enabled` |
| `DEEPSEEK_BRIDGE_LOG_DIR` | `logging.file.path` |
| `DEEPSEEK_BRIDGE_TRACE_DIR` | `logging.trace_dir` |
| `DEEPSEEK_BRIDGE_METRICS_ENABLED` | `metrics.enabled` |
| `DEEPSEEK_BRIDGE_CORS` / `DEEPSEEK_BRIDGE_CORS_ENABLED` | `cors.enabled` |
| `DEEPSEEK_BRIDGE_CORS_ALLOWED_ORIGINS` | `cors.allowed_origins` |
| `DEEPSEEK_BRIDGE_CORS_ALLOW_CREDENTIALS` | `cors.allow_credentials` |
| `DEEPSEEK_BRIDGE_OLLAMA` / `DEEPSEEK_BRIDGE_OLLAMA_ENABLED` | `ollama.enabled` |
| `DEEPSEEK_BRIDGE_REQUEST_TIMEOUT` | `performance.request_timeout` |
| `DEEPSEEK_BRIDGE_STREAM_READ_TIMEOUT` | `performance.stream_read_timeout` |
| `DEEPSEEK_BRIDGE_UPSTREAM_RETRY_ATTEMPTS` | `performance.upstream_retry_attempts` |
| `DEEPSEEK_BRIDGE_UPSTREAM_RETRY_INITIAL_DELAY_SECONDS` | `performance.upstream_retry_initial_delay_seconds` |
| `DEEPSEEK_BRIDGE_UPSTREAM_RETRY_MAX_DELAY_SECONDS` | `performance.upstream_retry_max_delay_seconds` |
| `DEEPSEEK_BRIDGE_UPSTREAM_RETRY_JITTER_SECONDS` | `performance.upstream_retry_jitter_seconds` |
| `DEEPSEEK_BRIDGE_MAX_REQUEST_BODY_BYTES` | `performance.max_request_body_bytes` |
| `DEEPSEEK_BRIDGE_MAX_POOL_CONNECTIONS` | `performance.max_pool_connections` |
| `DEEPSEEK_BRIDGE_MAX_THREAD_POOL` | `performance.max_thread_pool` |

## State Storage

The adapter is stateful because DS4 reasoning repair requires previously
returned `reasoning_content`.

### SQLite

SQLite is the default backend for local development and single-process
deployments:

```yaml
storage:
  backend: sqlite
  sqlite:
    path: reasoning_content.sqlite3
```

The local cache contains reasoning text and serialized assistant messages.
Treat it as sensitive prompt-derived data. The adapter creates local config and
cache directories with private permissions where possible.

### Valkey

Use Valkey when more than one adapter replica must share the same reasoning
state:

```yaml
storage:
  backend: valkey
  valkey:
    url: valkey://valkey.default.svc.cluster.local:6379/0
    key_prefix: deepseek-bridge
```

Entries are stored with TTL based on
`reasoning_cache.max_age_seconds`. The suggested Kubernetes pattern is to put
the Valkey URL in a Secret-backed environment variable:

```bash
DEEPSEEK_BRIDGE_STORAGE_BACKEND=valkey
DEEPSEEK_BRIDGE_VALKEY_URL=valkey://...
```

## Security Notes

- Client bearer tokens pass through the adapter and are forwarded to DeepSeek.
- API keys are not logged in normal logs; trace files summarize authorization
  headers by hash.
- Request traces can contain full prompts, tool arguments, responses, and
  reasoning state when `trace_mode` is `full`. Enable `--trace-dir` only for
  explicit debugging and protect the output directory.
- SQLite and Valkey caches contain `reasoning_content`, which may include
  sensitive prompt-derived information.
- In Kubernetes, prefer Secrets for Valkey URLs, NetworkPolicies around the
  adapter and Valkey, private ingress, and TLS at the edge.
- Do not expose the adapter publicly unless you intentionally trust the clients
  that can send bearer tokens through it.

## Container Image

Build a local image:

```bash
docker build --build-arg PACKAGE_VERSION=0.0.0+local -t deepseek-bridge:local .
```

Run the adapter as an HTTP service:

```bash
docker run --rm -d --name deepseek-bridge \
  -p 9000:9000 \
  deepseek-bridge:local

curl -fsS http://127.0.0.1:9000/healthz
docker stop deepseek-bridge
```

The image runs as UID `10001` and starts:

```bash
deepseek-bridge --config /etc/deepseek-bridge/config.yaml
```

The baked config uses Kubernetes-friendly defaults: `runtime.mode:
kubernetes`, `0.0.0.0:9000`, compact stdout/stderr logs, no file logs, and
SQLite cache path `/data/reasoning_content.sqlite3`. Override with
`DEEPSEEK_BRIDGE_*` env vars or mount your own config at
`/etc/deepseek-bridge/config.yaml`.

For read-only-root deployments, keep `/data` writable by UID/GID `10001` when
using file-backed SQLite, or use Valkey and avoid local cache persistence.

## Kubernetes

Kubernetes mode runs the adapter as a headless HTTP workload:

```bash
deepseek-bridge --runtime-mode kubernetes
```

In Kubernetes mode, the adapter defaults to `0.0.0.0:9000`, logs to
stdout/stderr, skips auto-creating `~/.deepseek-bridge/config.yaml`, and uses an
in-memory SQLite cache unless Valkey or a SQLite file path is configured. For
multiple replicas, set:

```bash
DEEPSEEK_BRIDGE_STORAGE_BACKEND=valkey
DEEPSEEK_BRIDGE_VALKEY_URL=valkey://...
```

Use distinct probes:

- `/healthz` is a lightweight liveness check and returns 200 while the process
  is serving.
- `/readyz` returns 200 only when the process is accepting traffic, storage is
  usable, and shutdown is not draining.

An example Deployment and Service live in
[`examples/kubernetes/deployment.yaml`](examples/kubernetes/deployment.yaml).
The example uses `readOnlyRootFilesystem: true`, binds to `0.0.0.0`, uses
separate liveness/readiness probes, and reads the Valkey URL from a Kubernetes
Secret.

Set `terminationGracePeriodSeconds` longer than your expected request or stream
duration so SIGTERM can drain active work.

### Helm

The chart lives in [`charts/deepseek-bridge`](charts/deepseek-bridge):

```bash
helm install deepseek-bridge ./charts/deepseek-bridge
```

The default chart values run one replica with in-memory SQLite for local/dev
use. Use Valkey for multi-replica installs:

```bash
helm install deepseek-bridge ./charts/deepseek-bridge \
  --set replicaCount=2 \
  --set storage.backend=valkey \
  --set valkey.existingSecret=deepseek-bridge-valkey
```

The chart can deploy a small built-in Valkey instance for development:

```bash
helm install deepseek-bridge ./charts/deepseek-bridge \
  --set storage.backend=valkey \
  --set valkey.enabled=true
```

Enable Prometheus scraping:

```bash
helm upgrade --install deepseek-bridge ./charts/deepseek-bridge \
  --set metrics.enabled=true \
  --set serviceMonitor.enabled=true
```

Run the same Minikube packaging smoke gate used by CI:

```bash
DEEPSEEK_BRIDGE_RUN_K8S_SMOKE=1 \
  python -m unittest tests.test_k8s_minikube_smoke -v
```

## Per-Request Thinking Overrides

Process configuration defines defaults, but a request can override thinking
mode and effort:

```json
{
  "model": "deepseek-v4-pro",
  "messages": [{"role": "user", "content": "Plan this change"}],
  "thinking": {"type": "enabled", "reasoning_effort": "max"}
}
```

OpenAI Responses-style payloads can set `reasoning.effort`. During conversion,
the adapter maps it into DeepSeek's nested `thinking` payload and does not
forward a top-level `reasoning_effort` field.

The effective model, thinking mode, and effort are part of the reasoning-cache
namespace, so incompatible modes do not reuse the same cached reasoning.

## How It Works

1. The client sends an OpenAI-compatible HTTP request to the adapter.
2. The adapter validates the path, request body, auth header, and runtime state.
3. Responses-style payloads are normalized to Chat Completions when needed.
4. The adapter computes a cache namespace from the conversation, upstream
   model, thinking settings, config, and bearer token.
5. Missing assistant `reasoning_content` is looked up in SQLite or Valkey and
   restored before the request is sent upstream.
6. DeepSeek's response is streamed or returned to the client in an
   OpenAI-compatible shape.
7. New `reasoning_content` from the response is stored for later turns.

## Known Limitations

### Reasoning State Is Required

If earlier turns were never seen by the adapter, strict DS4 repair may be
impossible. The default `missing_reasoning_strategy: recover` tries to keep the
conversation moving by dropping unrecoverable older tool-call context. Use
`missing_reasoning_strategy: reject` when debugging correctness.

### Cursor Sub-Agents

Cursor sub-agents may not inherit custom OpenAI base URL or API-key settings.
When that happens, they bypass the adapter and use Cursor's built-in models.
Sub-agents that actually route through the adapter work like ordinary clients.

### Reasoning Display

Some clients cannot render native reasoning UI for custom endpoints. When
`reasoning_display.enabled` is true, the adapter mirrors reasoning into visible
Markdown `<details>` blocks. Disable this with:

```bash
deepseek-bridge --no-display-reasoning
```

## Development

Run the full unittest suite:

```bash
uv run --extra dev --python 3.14 python -m unittest discover -s tests
```

Run opt-in functional HTTP tests against a local Valkey:

```bash
RUN_FUNCTIONAL_TESTS=1 \
FUNCTIONAL_VALKEY_URL=redis://127.0.0.1:6379/0 \
uv run --extra dev --python 3.14 python -m unittest discover -s tests/functional -v
```

Format, lint, type-check, and YAML-check:

```bash
uv run --extra dev --python 3.14 pre-commit run --all-files
uv run --extra dev --python 3.14 mypy src/deepseek_bridge
```

Run with coverage:

```bash
uv run --extra dev --python 3.14 coverage run -m unittest discover -s tests
uv run --extra dev --python 3.14 coverage report
```

## Process Flags

These flags configure the adapter process. They do not define the public
product surface; clients interact with HTTP endpoints.

| Flag | Purpose |
| --- | --- |
| `--config` | YAML config file path |
| `--runtime-mode` / `--runtime` | Runtime profile: `local` or `kubernetes` |
| `--host`, `--port` | HTTP bind address |
| `--base-url` | Upstream DeepSeek API base URL |
| `--model` | Fallback DeepSeek model when a request omits one |
| `--thinking` | Default DeepSeek thinking mode |
| `--reasoning-effort` | Default reasoning effort |
| `--display-reasoning` / `--no-display-reasoning` | Mirror reasoning into visible client content |
| `--collapsible-reasoning` / `--no-collapsible-reasoning` | Use Markdown details for mirrored reasoning |
| `--cors` / `--no-cors` | Send CORS headers |
| `--cors-allowed-origin` | Allowed browser origin; repeat for multiple origins |
| `--cors-allow-credentials` / `--no-cors-allow-credentials` | Credentialed CORS behavior |
| `--request-timeout` | Upstream request timeout |
| `--stream-read-timeout` | SSE read timeout |
| `--upstream-retry-attempts` | Transport retry attempts before returning an upstream error |
| `--upstream-retry-initial-delay-seconds` | Initial transport retry backoff delay |
| `--upstream-retry-max-delay-seconds` | Maximum transport retry backoff delay |
| `--upstream-retry-jitter-seconds` | Random retry jitter budget |
| `--max-thread-pool` | Legacy compatibility and storage pool sizing |
| `--max-pool-connections` | Maximum upstream connection pool size |
| `--max-request-body-bytes` | Maximum accepted request body size |
| `--ollama` / `--no-ollama` | Enable Ollama-compatible endpoints |
| `--metrics` / `--no-metrics` | Expose Prometheus metrics on `/metrics` |
| `--log-dir`, `--no-log` | Persistent log file behavior |
| `--trace-dir` | Write structured request traces using the configured trace mode |
| `--trace-mode` | Trace safety mode: `metadata-only`, `redacted`, or `full` |
| `--debug` | Enable DEBUG-level internal logs |
| `--compact`, `--headless` | Compact service-friendly console output |
| `--reasoning-content-path` | SQLite reasoning cache path |
| `--reasoning-cache-max-age-seconds` | Max age for cached reasoning |
| `--missing-reasoning-strategy` | `recover` or `reject` when required reasoning is unavailable |
| `--clear-reasoning-cache` | Clear configured reasoning state and exit |
| `--version` | Print version and exit |

## License

MIT License

## Acknowledgements

Based on
[yxlao/deepseek-cursor-proxy](https://github.com/yxlao/deepseek-cursor-proxy),
the original project that started this work.
