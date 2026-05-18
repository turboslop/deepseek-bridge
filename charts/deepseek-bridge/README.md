# deepseek-bridge Helm chart

This chart installs DeepSeek Bridge as a Kubernetes workload.

## Install

```sh
helm install deepseek-bridge ./charts/deepseek-bridge
```

The default install is a single-replica local/dev profile using an in-memory
SQLite reasoning cache. Multi-replica installs should use Valkey so cache
entries are shared across pods.

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
