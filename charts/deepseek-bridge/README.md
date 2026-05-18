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
