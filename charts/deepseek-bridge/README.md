# deepseek-bridge Helm chart

This chart installs DeepSeek Bridge as a Kubernetes workload.

## Install

```sh
helm install deepseek-bridge ./charts/deepseek-bridge
```

Tagged releases are published to the shared `turboslop` Helm repository:

```sh
helm repo add turboslop https://turboslop.github.io/helm
helm repo update turboslop
helm search repo turboslop/deepseek-bridge --versions
helm upgrade --install deepseek-bridge turboslop/deepseek-bridge
```

Add `--version` with one of the versions returned by `helm search` when you
need a pinned install.

The same chart is also available as a GHCR OCI artifact:

```sh
helm upgrade --install deepseek-bridge oci://ghcr.io/turboslop/deepseek-bridge
```

The default install is a single-replica local/dev profile using an in-memory
SQLite reasoning cache. Multi-replica installs should use Valkey so cache
entries are shared across pods.

## Production posture

The default values are intentionally small and development-friendly. For
production, set an explicit profile instead of relying on empty defaults:

```sh
helm install deepseek-bridge ./charts/deepseek-bridge \
  --set storage.backend=valkey \
  --set valkey.existingSecret=deepseek-bridge-valkey \
  --set resources.requests.cpu=250m \
  --set resources.requests.memory=256Mi \
  --set resources.limits.memory=1Gi \
  --set metrics.enabled=true
```

Recommended production controls:

- use an external managed Valkey/Redis-compatible service with TLS/auth;
- store Valkey URLs and credentials in Kubernetes Secrets;
- keep the built-in Valkey deployment for development or small private
  clusters only;
- configure resource requests and limits for the adapter and Valkey;
- put authenticated ingress, gateway auth, or service mesh policy in front of
  any endpoint reachable outside a trusted network;
- terminate TLS at the ingress/gateway or mesh boundary;
- enable `networkPolicy.enabled=true` and set ingress/egress rules appropriate
  for your namespace and CNI;
- avoid `trace_mode=full` on routine production traffic.

## External Valkey

```sh
helm install deepseek-bridge ./charts/deepseek-bridge \
  --set replicaCount=2 \
  --set storage.backend=valkey \
  --set valkey.existingSecret=deepseek-bridge-valkey \
  --set valkey.existingSecretKey=url
```

The referenced Secret must contain a Valkey URL such as
`valkey://valkey.default.svc.cluster.local:6379/0`.

## Built-in Valkey

```sh
helm install deepseek-bridge ./charts/deepseek-bridge \
  --set replicaCount=2 \
  --set storage.backend=valkey \
  --set valkey.enabled=true
```

The built-in Valkey deployment is intended for small or development clusters.
Use a managed or separately operated Valkey service for production.

## Metrics

Enable the app metrics endpoint and ServiceMonitor together:

```sh
helm install deepseek-bridge ./charts/deepseek-bridge \
  --set metrics.enabled=true \
  --set serviceMonitor.enabled=true
```

Metrics are served on `/metrics` through the `http` service port.

## Grafana dashboard

The chart can package the bundled Kubernetes dashboard as a ConfigMap for
Grafana sidecar importers:

```sh
helm install deepseek-bridge ./charts/deepseek-bridge \
  --set metrics.enabled=true \
  --set grafanaDashboard.enabled=true
```

The dashboard includes namespace, service, and pod variables, Prometheus panels
for application and pod metrics, and a Loki logs panel. The Loki panel is useful
when a Loki datasource exists; Prometheus-only Grafana installs can still import
the dashboard, but that panel will remain empty until Loki is configured.

For Prometheus Operator users, set `serviceMonitor.enabled=true` as well so the
application metrics are scraped by the cluster Prometheus.

## Minikube smoke test

The repository includes a CI-compatible smoke gate that builds the local Docker
image, loads that exact tag into Minikube, validates the rendered chart with the
Kubernetes API, installs the release, waits for rollout, verifies Service
endpoints, and probes `/healthz` and `/readyz` through the Kubernetes Service:

```sh
DEEPSEEK_BRIDGE_RUN_K8S_SMOKE=1 \
  python -m unittest tests.test_k8s_minikube_smoke -v
```

Run `scripts/k8s-minikube-smoke.sh` directly for the same check without the
unittest wrapper. The script requires Docker, Helm, kubectl, Minikube, and curl.
