# AI-Assisted Development Policy

This repository may contain changes authored with help from AI coding tools.
The presence of `AGENTS.md`, `.codex/skills`, or agent workflow files is not a
quality signal by itself; it is an operating model. The quality bar is the same
for human-authored and AI-assisted changes.

## Requirements

AI-assisted changes require:

- human review before merge;
- focused tests for changed behavior;
- local validation for request handling, streaming, storage, config, or
  Kubernetes changes;
- security review for code paths that touch API keys, prompts, tool arguments,
  `reasoning_content`, trace/debug output, cache storage, metrics, logs, or
  Kubernetes secret handling;
- clear release notes for user-visible behavior or breaking changes;
- no unchecked generated code, vendored blobs, or dependency additions.

## Review Checklist

Reviewers should check:

- whether the change matches existing local patterns;
- whether compatibility is preserved or migration steps are documented;
- whether sensitive data can leak through logs, traces, metrics, exceptions, or
  generated artifacts;
- whether tests cover failure paths and edge cases, not only happy paths;
- whether Kubernetes defaults are safe for local/dev and clearly documented for
  production;
- whether documentation describes the real runtime behavior.

## Sensitive Paths

The following areas need extra scrutiny:

- `src/deepseek_bridge/handler/`
- `src/deepseek_bridge/transform/`
- `src/deepseek_bridge/streaming/`
- `src/deepseek_bridge/trace.py`
- `src/deepseek_bridge/logging.py`
- `src/deepseek_bridge/reasoning_store.py`
- `src/deepseek_bridge/valkey_store.py`
- `charts/deepseek-bridge/`
- `.github/workflows/`

AI assistance may speed up implementation, but it does not replace review,
tests, threat modeling, or release discipline.
