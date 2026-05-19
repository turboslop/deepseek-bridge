# Changelog

This project follows tag-based releases. Release tags must use
`vMAJOR.MINOR.PATCH` or `vMAJOR.MINOR.PATCH-PRERELEASE` format, for example
`v0.5.0` or `v0.5.0-rc.1`.

## Unreleased

### Changed

- Repositioned the project as an OpenAI-compatible HTTP stateful adapter for
  the DeepSeek V4 (DS4) reasoning protocol. The `deepseek-bridge` executable is
  the adapter process runner, not the product interface.
- Documented security-sensitive state: client bearer tokens, prompts, tool
  arguments, `reasoning_content`, trace dumps, and SQLite/Valkey caches.
- Added explicit release, migration, and AI-assisted development policy
  documentation.
- Documented Helm production posture and added an opt-in chart NetworkPolicy.

### Security

- Added explicit trace safety modes: `metadata-only`, `redacted`, and `full`.
  `metadata-only` is the default. Full traces are intended only for deliberate
  debugging because they may contain prompts, tool arguments, responses, and
  reasoning state.

## Migration Guide: Flat YAML to `version: 1`

Older flat config files still load, but new deployments should use schema v1.
The runtime precedence remains:

```text
built-in defaults < YAML config < DEEPSEEK_BRIDGE_* env vars < process flags
```

### Before

```yaml
host: 127.0.0.1
port: 9000
base_url: https://api.deepseek.com
model: deepseek-v4-pro
thinking: enabled
reasoning_effort: max
reasoning_content_path: reasoning_content.sqlite3
tunnel: none
debug: false
```

### After

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

logging:
  level: info

tunnel:
  mode: none
```

### Key Mapping

| Flat key | Schema v1 key |
| --- | --- |
| `host` | `server.host` |
| `port` | `server.port` |
| `base_url` | `upstream.base_url` |
| `model` | `upstream.model` |
| `thinking` | `upstream.thinking.mode` |
| `reasoning_effort` | `upstream.thinking.reasoning_effort` |
| `reasoning_content_path` | `storage.sqlite.path` |
| `storage_backend` | `storage.backend` |
| `valkey_url` | `storage.valkey.url` |
| `valkey_key_prefix` | `storage.valkey.key_prefix` |
| `reasoning_cache_max_age_seconds` | `reasoning_cache.max_age_seconds` |
| `reasoning_cache_max_entries` | `reasoning_cache.max_entries` |
| `missing_reasoning_strategy` | `reasoning_cache.missing_reasoning_strategy` |
| `display_reasoning` | `reasoning_display.enabled` |
| `collapsible_reasoning` | `reasoning_display.collapsible` |
| `debug` | `logging.level: debug` |
| `compact` | `logging.compact` |
| `log_dir` | `logging.file.path` |
| `trace_dir` | `logging.trace_dir` |
| `metrics_enabled` | `metrics.enabled` |
| `tunnel` | `tunnel.mode` |
| `cf_url` | `tunnel.cf_url` |
| `ngrok_url` | `tunnel.ngrok_url` |
| `cors` | `cors.enabled` |
| `cors_allowed_origins` | `cors.allowed_origins` |
| `cors_allow_credentials` | `cors.allow_credentials` |
| `ollama` | `ollama.enabled` |
| `request_timeout` | `performance.request_timeout` |
| `stream_read_timeout` | `performance.stream_read_timeout` |
| `max_request_body_bytes` | `performance.max_request_body_bytes` |
| `max_pool_connections` | `performance.max_pool_connections` |
| `max_thread_pool` | `performance.max_thread_pool` |

## Release Process

1. Update this changelog with the user-visible changes, migration notes, and
   breaking changes.
2. Ensure the version is represented by a Git tag:

   ```bash
   git tag v0.5.0
   git push origin v0.5.0
   ```

3. The release workflow validates the tag format, runs lint/tests, creates a
   GitHub Release, publishes the Python package, builds and attests the Docker
   image, packages and attests the Helm chart, and uploads chart assets to the
   GitHub Release.
4. GitHub Release notes should call out:
   - breaking config/schema changes;
   - storage backend changes;
   - Kubernetes/Helm changes;
   - security-sensitive behavior such as trace/debug output;
   - migration steps and rollback notes.

Tags and GitHub Releases are intentionally the public source of truth for
released versions. Unreleased branch state should not be described as production
ready.
