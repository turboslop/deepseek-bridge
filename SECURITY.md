# Security Policy and Threat Model

DeepSeek Bridge is an HTTP stateful adapter. It forwards OpenAI-compatible
client traffic to DeepSeek and stores enough reasoning state to repair DS4
tool-call conversations. Treat it as security-sensitive infrastructure.

## Data Flow

The adapter can observe or store:

- client `Authorization: Bearer ...` headers containing DeepSeek API keys;
- prompts and multimodal message metadata sent by clients;
- tool definitions, tool call names, tool call arguments, and tool results;
- DeepSeek responses, including `reasoning_content`;
- streaming SSE chunks;
- request/response metadata, health, readiness, and metrics;
- SQLite cache rows containing reasoning text and serialized assistant
  messages;
- Valkey cache entries containing reasoning text, serialized assistant
  messages, timestamps, and key-prefix/index metadata;
- optional trace dumps when trace logging is enabled.

Normal logs are designed to avoid API keys and full request/response bodies.
Trace dumps are different: depending on trace mode, they may contain sensitive
payloads and should be protected as prompt data.

## Trust Boundaries

Typical boundaries:

```text
OpenAI-compatible client
  -> DeepSeek Bridge
  -> DeepSeek API
```

For Kubernetes:

```text
Ingress / Service
  -> DeepSeek Bridge pod(s)
  -> Valkey or SQLite state backend
  -> DeepSeek API
```

The adapter does not authenticate clients by itself beyond requiring a bearer
token for upstream forwarding. If you expose it beyond a trusted local machine
or private cluster network, put authentication and authorization in front of it.

## Sensitive Storage

### SQLite

SQLite is intended for local or single-process use. The cache file contains
`reasoning_content` and serialized assistant messages. It may include sensitive
prompt-derived data.

Recommended controls:

- keep cache directories private to the adapter user;
- avoid mounting the SQLite cache on shared volumes;
- clear caches before handing machines or volumes to another user;
- use ephemeral storage when persistence is not required;
- avoid multi-replica SQLite deployments.

### Valkey

Valkey is the shared-state backend for multi-replica deployments. The Valkey URL
is sensitive because it may contain credentials or point to a private cache.

Recommended controls:

- use Kubernetes Secrets or an external secret manager for
  `DEEPSEEK_BRIDGE_VALKEY_URL`;
- prefer managed Valkey/Redis-compatible services with TLS and authentication;
- restrict network access to the adapter namespace or workload identity;
- use a distinct `DEEPSEEK_BRIDGE_VALKEY_KEY_PREFIX` per environment/tenant;
- set a cache TTL through `reasoning_cache.max_age_seconds`;
- monitor storage errors and readiness failures.

## Trace and Debug Output

Trace logging is for explicit debugging. It can contain prompts, tool arguments,
responses, reasoning state, and stream chunks depending on trace mode.

Recommended controls:

- do not enable trace logging in normal production traffic;
- use `metadata-only` trace mode by default;
- use `redacted` when structural payload debugging is needed without full
  content;
- use `full` only in trusted environments for short-lived debugging;
- protect trace directories with private filesystem permissions;
- delete trace dumps when the investigation is complete;
- never attach full trace dumps to public issues.

Debug logs should also be treated as sensitive if they are collected centrally.
Use JSON logs in Kubernetes and keep prompt/response content out of normal log
pipelines.

## Kubernetes Production Guidance

For production deployments:

- keep the adapter private unless an authenticated gateway sits in front of it;
- terminate TLS at the ingress/gateway or service mesh boundary;
- use Secrets for Valkey URLs and any credentials;
- prefer external managed Valkey with TLS/auth over the chart's built-in Valkey;
- set resource requests and limits for the adapter and Valkey;
- configure NetworkPolicies around the adapter and state backend;
- restrict egress where your CNI supports FQDN or controlled external egress;
- use separate `/healthz` and `/readyz` probes;
- set `terminationGracePeriodSeconds` long enough for active streaming
  responses to drain;
- scrape `/metrics` only from trusted monitoring infrastructure.

The built-in Helm Valkey is a development convenience. Production operators
should treat it as a starting point, not as a managed datastore replacement.

## Public Exposure

Do not expose DeepSeek Bridge directly to the public internet unless you
intentionally trust every client that can send requests through it. A public
adapter endpoint can be abused to forward attacker-supplied bearer tokens and
can leak prompt-derived state through caches or traces if misconfigured.

If public access is required:

- put an authenticating reverse proxy, gateway, or service mesh in front of the
  adapter;
- require TLS;
- scope clients and API keys;
- disable full traces;
- use rate limiting and request body limits;
- monitor 4xx/5xx rates, upstream errors, storage errors, and active streams.

## Reporting Vulnerabilities

Open a private security advisory or contact the maintainers through the
repository's security reporting channel when available. Do not include API keys,
full prompts, trace dumps, or cache files in public reports.
