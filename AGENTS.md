# AGENTS.md

Guidance for AI coding agents working in this repository.

## Project Overview

DeepSeek Bridge is a Python HTTP proxy that presents OpenAI-compatible endpoints to local AI coding tools and forwards requests to DeepSeek. Its main job is preserving and restoring `reasoning_content` across multi-turn tool-call conversations.

The package entrypoint is:

```sh
deepseek-bridge
```

The console script is defined in `pyproject.toml` and points to `deepseek_bridge.server:main`, which re-exports the CLI implementation from `src/deepseek_bridge/cli.py`.

## Repository Layout

- `src/deepseek_bridge/cli.py` - argument parsing, startup, logging setup, tunnel setup, server lifecycle.
- `src/deepseek_bridge/config.py` - default config, config file loading, typed `ProxyConfig`.
- `src/deepseek_bridge/server_infrastructure.py` - HTTP server, bounded thread pool, upstream connection pool.
- `src/deepseek_bridge/handler/` - request routing, endpoint handlers, upstream forwarding, streaming handling.
- `src/deepseek_bridge/transform/` - request normalization, reasoning repair, response transformation, cache integration.
- `src/deepseek_bridge/streaming/` - SSE parsing, accumulation, reasoning display helpers.
- `src/deepseek_bridge/reasoning_store.py` - current SQLite-backed reasoning cache.
- `src/deepseek_bridge/logging.py` - log formatting, redaction, request summaries.
- `.codex/skills/gof/SKILL.md` - repo-level GoF workflow for taking an issue through branch, PR, CI, fixes, and auto-merge.
- `tests/` - unittest-based test suite.

## Repo-Level Skills

Use `.codex/skills/gof/SKILL.md` when picking up a GitHub issue or implementation task in this repository. It defines the expected local Gang of Four workflow: quorum-routed planning, implementer, reviewer, QA, temp artifacts, local quality gates, PR creation, CI monitoring, fix/review/QA loops, and auto-merge.

## Development Commands

Run the full test suite:

```sh
uv run --extra dev --python 3.14 python -m unittest discover -s tests
```

Run one test module:

```sh
uv run --extra dev --python 3.14 python -m unittest tests.test_reasoning_store
```

Run the server locally without a tunnel:

```sh
uv run --python 3.14 deepseek-bridge --tunnel none --port 9000
```

Run with debug logs and request traces:

```sh
uv run --python 3.14 deepseek-bridge --tunnel none --debug --trace-dir ./dumps
```

Format, lint, and type-check commands documented in the README:

```sh
uv run --extra dev --python 3.14 pre-commit run --all-files
uv run --extra dev --python 3.14 mypy src/deepseek_bridge
```

## Coding Guidelines

- Preserve existing API compatibility unless a task explicitly changes it.
- Keep local CLI behavior working when adding production or Kubernetes behavior.
- Avoid logging API keys, prompts, request bodies, response bodies, or trace payloads in normal logs.
- Keep debug and trace modes explicit because they may write sensitive prompt data.
- Add or update focused tests for behavior changes, especially around streaming, reasoning-cache lookup, and error responses.
- Prefer small, testable changes over large rewrites. This codebase has many compatibility edge cases.

## Reasoning Cache Notes

The current reasoning cache is SQLite-backed and stores reasoning content by scoped cache keys. The core cache semantics are:

- store assistant `reasoning_content` from upstream responses;
- restore missing `reasoning_content` for later tool-call requests;
- isolate cache entries by conversation scope and namespace;
- prune old rows by age and row budget.

When changing storage:

- keep cache-key generation behavior stable;
- keep SQLite as the default local development backend unless the task says otherwise;
- make shared backends implement the same lookup/store semantics;
- make readiness and metrics depend on backend health without breaking local mode.

## Kubernetes-Native Direction

Open issues track the Kubernetes work. The intended production direction is:

- container image runs `deepseek-bridge --tunnel none --host 0.0.0.0 --port 9000`;
- configuration can come from environment variables;
- liveness and readiness endpoints are separate;
- logs go to stdout/stderr, with optional JSON formatting;
- Prometheus metrics are exposed on `/metrics`;
- the Helm chart manages Deployment, Service, probes, ServiceMonitor, autoscaling, and Grafana dashboard resources;
- Valkey is the preferred shared reasoning-cache backend for multi-replica Kubernetes deployments.

For Kubernetes changes, avoid making cloudflared, ngrok, or local file paths mandatory.

## Valkey Backend Expectations

For the planned Valkey backend:

- use `DEEPSEEK_BRIDGE_STORAGE_BACKEND=valkey`;
- read the connection URL from `DEEPSEEK_BRIDGE_VALKEY_URL`;
- support a configurable key prefix such as `DEEPSEEK_BRIDGE_VALKEY_KEY_PREFIX`;
- store entries with TTL based on `reasoning_cache_max_age_seconds`;
- use a connection pool suitable for the existing threaded request model;
- expose backend health for `/readyz`;
- expose cache hit/miss and storage operation metrics.

Suggested key/value model:

- key: `<prefix>:reasoning:<cache-key>`;
- value: JSON object containing `reasoning`, `message_json`, and `created_at`;
- TTL: `reasoning_cache_max_age_seconds`.

## Before Finishing

For code changes, run the narrowest relevant tests first. If the change touches shared request handling, streaming, config, or storage, run the full unittest suite before handing off.

If tests cannot be run, state that clearly in the final response and explain why.
