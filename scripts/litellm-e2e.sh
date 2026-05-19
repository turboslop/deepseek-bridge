#!/usr/bin/env bash
set -Eeuo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMPOSE_FILE="${DEEPSEEK_BRIDGE_LITELLM_E2E_COMPOSE_FILE:-${REPO_ROOT}/docker-compose.litellm-e2e.yml}"
PROJECT_NAME="${DEEPSEEK_BRIDGE_LITELLM_E2E_PROJECT:-deepseek-bridge-litellm-e2e}"
SKIP_CLEANUP="${DEEPSEEK_BRIDGE_LITELLM_E2E_SKIP_CLEANUP:-0}"
DEEPSEEK_API_KEY="${DEEPSEEK_API_KEY:-${LIVE_DEEPSEEK_KEY:-}}"
LITELLM_MASTER_KEY="${LITELLM_MASTER_KEY:-sk-litellm-e2e}"

log() {
  printf '\n==> %s\n' "$*"
}

run() {
  printf '+' >&2
  printf ' %q' "$@" >&2
  printf '\n' >&2
  "$@"
}

compose() {
  if docker compose version >/dev/null 2>&1; then
    docker compose "$@"
    return
  fi
  if command -v docker-compose >/dev/null 2>&1; then
    docker-compose "$@"
    return
  fi
  printf 'missing Docker Compose plugin or docker-compose binary\n' >&2
  exit 127
}

diagnostics() {
  log "Compose diagnostics"
  compose -f "${COMPOSE_FILE}" -p "${PROJECT_NAME}" ps || true
  compose -f "${COMPOSE_FILE}" -p "${PROJECT_NAME}" logs \
    --no-color \
    bridge \
    litellm \
    e2e || true
}

cleanup() {
  status=$?
  set +e
  if [[ "${status}" -ne 0 ]]; then
    diagnostics
  fi
  if [[ "${SKIP_CLEANUP}" != "1" ]]; then
    log "Cleaning up Compose resources"
    compose -f "${COMPOSE_FILE}" -p "${PROJECT_NAME}" down \
      --volumes \
      --remove-orphans >/dev/null 2>&1 || true
  fi
  exit "${status}"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

if [[ -z "${DEEPSEEK_API_KEY}" ]]; then
  printf 'set DEEPSEEK_API_KEY or LIVE_DEEPSEEK_KEY to run the live e2e\n' >&2
  exit 2
fi

export DEEPSEEK_API_KEY
export LITELLM_MASTER_KEY

cd "${REPO_ROOT}"

log "Validating Compose configuration"
run compose -f "${COMPOSE_FILE}" -p "${PROJECT_NAME}" config --quiet

log "Building deepseek-bridge image"
run compose -f "${COMPOSE_FILE}" -p "${PROJECT_NAME}" build bridge

log "Running LiteLLM -> deepseek-bridge -> DeepSeek Cloud e2e"
run compose -f "${COMPOSE_FILE}" -p "${PROJECT_NAME}" up \
  --abort-on-container-exit \
  --exit-code-from e2e \
  e2e
