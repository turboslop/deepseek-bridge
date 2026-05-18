#!/usr/bin/env bash
set -Eeuo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CHART_DIR="${DEEPSEEK_BRIDGE_CHART_DIR:-${REPO_ROOT}/charts/deepseek-bridge}"
PROFILE="${DEEPSEEK_BRIDGE_MINIKUBE_PROFILE:-deepseek-bridge-smoke}"
NAMESPACE="${DEEPSEEK_BRIDGE_K8S_NAMESPACE:-deepseek-bridge-smoke}"
RELEASE="${DEEPSEEK_BRIDGE_HELM_RELEASE:-deepseek-bridge-smoke}"
IMAGE_REPOSITORY="${DEEPSEEK_BRIDGE_SMOKE_IMAGE_REPOSITORY:-deepseek-bridge}"
IMAGE_TAG="${DEEPSEEK_BRIDGE_SMOKE_IMAGE_TAG:-smoke-$(date +%s)}"
IMAGE="${IMAGE_REPOSITORY}:${IMAGE_TAG}"
MINIKUBE_DRIVER="${DEEPSEEK_BRIDGE_MINIKUBE_DRIVER:-docker}"
MINIKUBE_CPUS="${DEEPSEEK_BRIDGE_MINIKUBE_CPUS:-2}"
MINIKUBE_MEMORY="${DEEPSEEK_BRIDGE_MINIKUBE_MEMORY:-4096}"
TIMEOUT="${DEEPSEEK_BRIDGE_K8S_SMOKE_TIMEOUT:-180s}"
LOCAL_PORT="${DEEPSEEK_BRIDGE_K8S_SMOKE_LOCAL_PORT:-19000}"
SKIP_CLEANUP="${DEEPSEEK_BRIDGE_K8S_SMOKE_SKIP_CLEANUP:-0}"
if [[ -n "${DEEPSEEK_BRIDGE_K8S_SMOKE_WORK_DIR:-}" ]]; then
  WORK_DIR="${DEEPSEEK_BRIDGE_K8S_SMOKE_WORK_DIR}"
  CLEAN_WORK_DIR=0
  mkdir -p "${WORK_DIR}"
else
  WORK_DIR="$(mktemp -d)"
  CLEAN_WORK_DIR=1
fi
MANIFEST="${WORK_DIR}/rendered.yaml"
PORT_FORWARD_LOG="${WORK_DIR}/port-forward.log"
PORT_FORWARD_PID=""
SELECTOR="app.kubernetes.io/instance=${RELEASE},app.kubernetes.io/name=deepseek-bridge"

log() {
  printf '\n==> %s\n' "$*"
}

run() {
  printf '+' >&2
  printf ' %q' "$@" >&2
  printf '\n' >&2
  "$@"
}

require_tool() {
  if ! command -v "$1" >/dev/null 2>&1; then
    printf 'missing required tool: %s\n' "$1" >&2
    exit 127
  fi
}

kubectl_smoke() {
  kubectl --context "${PROFILE}" "$@"
}

helm_smoke() {
  helm --kube-context "${PROFILE}" "$@"
}

diagnostics() {
  log "Kubernetes diagnostics"
  kubectl_smoke cluster-info || true
  minikube status -p "${PROFILE}" || true
  minikube logs -p "${PROFILE}" --problems || true
  helm_smoke status "${RELEASE}" --namespace "${NAMESPACE}" || true
  kubectl_smoke get namespace "${NAMESPACE}" -o wide || true
  kubectl_smoke get all,pvc,endpoints -n "${NAMESPACE}" -o wide || true
  kubectl_smoke get endpointslices.discovery.k8s.io \
    -n "${NAMESPACE}" \
    -o wide || true
  kubectl_smoke describe deployment,pod,svc \
    -n "${NAMESPACE}" \
    -l "${SELECTOR}" || true
  kubectl_smoke get events \
    -n "${NAMESPACE}" \
    --sort-by=.lastTimestamp || true
  kubectl_smoke logs \
    -n "${NAMESPACE}" \
    -l "${SELECTOR}" \
    --all-containers=true \
    --tail=200 || true
  if [[ -s "${PORT_FORWARD_LOG}" ]]; then
    log "Port-forward log"
    cat "${PORT_FORWARD_LOG}" || true
  fi
}

cleanup() {
  status=$?
  set +e
  if [[ -n "${PORT_FORWARD_PID}" ]]; then
    kill "${PORT_FORWARD_PID}" >/dev/null 2>&1
    wait "${PORT_FORWARD_PID}" >/dev/null 2>&1
  fi
  if [[ "${status}" -ne 0 ]]; then
    diagnostics
  fi
  if [[ "${SKIP_CLEANUP}" != "1" ]]; then
    log "Cleaning up smoke resources"
    helm_smoke uninstall "${RELEASE}" \
      --namespace "${NAMESPACE}" \
      --ignore-not-found >/dev/null 2>&1 || true
    kubectl_smoke delete namespace "${NAMESPACE}" \
      --ignore-not-found=true \
      --wait=false >/dev/null 2>&1 || true
  fi
  if [[ "${CLEAN_WORK_DIR}" == "1" ]]; then
    rm -rf "${WORK_DIR}"
  fi
  exit "${status}"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

wait_for_minikube() {
  if minikube status -p "${PROFILE}" >/dev/null 2>&1; then
    log "Reusing Minikube profile ${PROFILE}"
    run minikube update-context -p "${PROFILE}"
    return
  fi

  log "Starting Minikube profile ${PROFILE}"
  run minikube start \
    -p "${PROFILE}" \
    --driver="${MINIKUBE_DRIVER}" \
    --cpus="${MINIKUBE_CPUS}" \
    --memory="${MINIKUBE_MEMORY}" \
    --wait=all
}

wait_for_port_forward() {
  local url="http://127.0.0.1:${LOCAL_PORT}/healthz"
  for _ in {1..60}; do
    if curl -fsS "${url}" >/dev/null 2>&1; then
      return
    fi
    if ! kill -0 "${PORT_FORWARD_PID}" >/dev/null 2>&1; then
      printf 'kubectl port-forward exited early\n' >&2
      return 1
    fi
    sleep 1
  done
  printf 'timed out waiting for port-forward to serve %s\n' "${url}" >&2
  return 1
}

jsonpath() {
  kubectl_smoke get "$1" \
    -n "${NAMESPACE}" \
    -l "${SELECTOR}" \
    -o "jsonpath=$2"
}

assert_contains() {
  local value="$1"
  local expected="$2"
  local label="$3"
  if [[ "${value}" != *"${expected}"* ]]; then
    printf '%s does not contain %q: %s\n' "${label}" "${expected}" "${value}" >&2
    return 1
  fi
}

assert_token() {
  local value="$1"
  local expected="$2"
  local label="$3"
  if [[ " ${value} " != *" ${expected} "* ]]; then
    printf '%s does not contain token %q: %s\n' \
      "${label}" \
      "${expected}" \
      "${value}" >&2
    return 1
  fi
}

assert_equal() {
  local actual="$1"
  local expected="$2"
  local label="$3"
  if [[ "${actual}" != "${expected}" ]]; then
    printf '%s mismatch: expected %q, got %q\n' \
      "${label}" \
      "${expected}" \
      "${actual}" >&2
    return 1
  fi
}

require_tool docker
require_tool helm
require_tool kubectl
require_tool minikube
require_tool curl

cd "${REPO_ROOT}"

wait_for_minikube

log "Building local Docker image ${IMAGE}"
run docker build \
  --build-arg PACKAGE_VERSION=0.0.0 \
  -t "${IMAGE}" \
  "${REPO_ROOT}"

log "Loading local image into Minikube"
run minikube image load \
  -p "${PROFILE}" \
  "${IMAGE}"

log "Linting Helm chart"
run helm lint "${CHART_DIR}"

HELM_ARGS=(
  --namespace "${NAMESPACE}"
  --set-string "image.repository=${IMAGE_REPOSITORY}"
  --set-string "image.tag=${IMAGE_TAG}"
  --set "image.pullPolicy=Never"
  --set-string "upstream.baseUrl=https://deepseek-bridge-smoke.invalid"
)

log "Rendering Helm chart"
run helm template "${RELEASE}" "${CHART_DIR}" "${HELM_ARGS[@]}" >"${MANIFEST}"

log "Preparing namespace ${NAMESPACE}"
kubectl_smoke create namespace "${NAMESPACE}" \
  --dry-run=client \
  -o yaml | kubectl_smoke apply -f -

log "Validating rendered manifests with the Kubernetes API"
run kubectl --context "${PROFILE}" apply \
  --namespace "${NAMESPACE}" \
  --dry-run=server \
  --validate=strict \
  -f "${MANIFEST}"

log "Installing Helm release"
run helm_smoke upgrade \
  --install "${RELEASE}" "${CHART_DIR}" \
  "${HELM_ARGS[@]}" \
  --create-namespace \
  --wait \
  --timeout "${TIMEOUT}"

deployment="$(jsonpath deployment '{.items[0].metadata.name}')"
service="$(jsonpath service '{.items[0].metadata.name}')"
pod="$(jsonpath pod '{.items[0].metadata.name}')"

assert_contains "${deployment}" "deepseek-bridge" "deployment name"
assert_contains "${service}" "deepseek-bridge" "service name"
assert_contains "${pod}" "deepseek-bridge" "pod name"

log "Waiting for deployment rollout and pod readiness"
run kubectl_smoke rollout status \
  "deployment/${deployment}" \
  -n "${NAMESPACE}" \
  --timeout "${TIMEOUT}"
run kubectl_smoke wait \
  --for=condition=Ready \
  "pod/${pod}" \
  -n "${NAMESPACE}" \
  --timeout "${TIMEOUT}"

log "Verifying Service endpoints"
endpoints="$(kubectl_smoke get endpoints "${service}" \
  -n "${NAMESPACE}" \
  -o jsonpath='{.subsets[*].addresses[*].ip}')"
if [[ -z "${endpoints}" ]]; then
  printf 'service %s has no ready endpoints\n' "${service}" >&2
  exit 1
fi

log "Verifying workload image and Kubernetes runtime configuration"
actual_image="$(kubectl_smoke get pod "${pod}" \
  -n "${NAMESPACE}" \
  -o jsonpath='{.spec.containers[?(@.name=="deepseek-bridge")].image}')"
pull_policy="$(kubectl_smoke get pod "${pod}" \
  -n "${NAMESPACE}" \
  -o jsonpath='{.spec.containers[?(@.name=="deepseek-bridge")].imagePullPolicy}')"
args="$(kubectl_smoke get pod "${pod}" \
  -n "${NAMESPACE}" \
  -o jsonpath='{.spec.containers[?(@.name=="deepseek-bridge")].args[*]}')"
runtime_env="$(kubectl_smoke get pod "${pod}" \
  -n "${NAMESPACE}" \
  -o jsonpath='{.spec.containers[?(@.name=="deepseek-bridge")].env[?(@.name=="DEEPSEEK_BRIDGE_RUNTIME_MODE")].value}')"
host_env="$(kubectl_smoke get pod "${pod}" \
  -n "${NAMESPACE}" \
  -o jsonpath='{.spec.containers[?(@.name=="deepseek-bridge")].env[?(@.name=="DEEPSEEK_BRIDGE_HOST")].value}')"
port_env="$(kubectl_smoke get pod "${pod}" \
  -n "${NAMESPACE}" \
  -o jsonpath='{.spec.containers[?(@.name=="deepseek-bridge")].env[?(@.name=="DEEPSEEK_BRIDGE_PORT")].value}')"
tunnel_env="$(kubectl_smoke get pod "${pod}" \
  -n "${NAMESPACE}" \
  -o jsonpath='{.spec.containers[?(@.name=="deepseek-bridge")].env[?(@.name=="DEEPSEEK_BRIDGE_TUNNEL_MODE")].value}')"

assert_equal "${actual_image}" "${IMAGE}" "pod image"
assert_equal "${pull_policy}" "Never" "imagePullPolicy"
assert_token "${args}" "--runtime-mode" "container args"
assert_token "${args}" "kubernetes" "container args"
assert_token "${args}" "--host" "container args"
assert_token "${args}" "0.0.0.0" "container args"
assert_token "${args}" "--port" "container args"
assert_token "${args}" "9000" "container args"
assert_token "${args}" "--tunnel" "container args"
assert_token "${args}" "none" "container args"
assert_equal "${runtime_env}" "kubernetes" "runtime env"
assert_equal "${host_env}" "0.0.0.0" "host env"
assert_equal "${port_env}" "9000" "port env"
assert_equal "${tunnel_env}" "none" "tunnel env"

log "Probing /healthz and /readyz through the Kubernetes Service"
kubectl_smoke port-forward \
  "service/${service}" \
  "${LOCAL_PORT}:9000" \
  -n "${NAMESPACE}" >"${PORT_FORWARD_LOG}" 2>&1 &
PORT_FORWARD_PID=$!
wait_for_port_forward
health_body="$(curl -fsS "http://127.0.0.1:${LOCAL_PORT}/healthz")"
ready_body="$(curl -fsS "http://127.0.0.1:${LOCAL_PORT}/readyz")"
printf '%s\n' "${health_body}"
printf '%s\n' "${ready_body}"
if [[ "${health_body}" != *'"ok":true'* ]]; then
  printf '/healthz response did not report ok=true: %s\n' \
    "${health_body}" >&2
  exit 1
fi
if [[ "${ready_body}" != *'"ok":true'* ]]; then
  printf '/readyz response did not report ok=true: %s\n' \
    "${ready_body}" >&2
  exit 1
fi
if [[ "${ready_body}" != *'"storage":{"ok":true,"status":"ok"}'* ]]; then
  printf '/readyz response did not report healthy storage: %s\n' \
    "${ready_body}" >&2
  exit 1
fi

log "Minikube smoke test passed"
