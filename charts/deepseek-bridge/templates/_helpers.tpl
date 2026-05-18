{{/*
Expand the chart name.
*/}}
{{- define "deepseek-bridge.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{/*
Create a default fully qualified app name.
*/}}
{{- define "deepseek-bridge.fullname" -}}
{{- if .Values.fullnameOverride -}}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- $name := default .Chart.Name .Values.nameOverride -}}
{{- if contains $name .Release.Name -}}
{{- .Release.Name | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" -}}
{{- end -}}
{{- end -}}
{{- end -}}

{{/*
Create chart label value.
*/}}
{{- define "deepseek-bridge.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{/*
Common labels.
*/}}
{{- define "deepseek-bridge.labels" -}}
helm.sh/chart: {{ include "deepseek-bridge.chart" . }}
{{ include "deepseek-bridge.selectorLabels" . }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- with .Values.commonLabels }}
{{ toYaml . }}
{{- end }}
{{- end -}}

{{/*
Selector labels.
*/}}
{{- define "deepseek-bridge.selectorLabels" -}}
app.kubernetes.io/name: {{ include "deepseek-bridge.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}

{{/*
Service account name.
*/}}
{{- define "deepseek-bridge.serviceAccountName" -}}
{{- if .Values.serviceAccount.create -}}
{{- default (include "deepseek-bridge.fullname" .) .Values.serviceAccount.name -}}
{{- else -}}
{{- default "default" .Values.serviceAccount.name -}}
{{- end -}}
{{- end -}}

{{/*
Application image reference.
*/}}
{{- define "deepseek-bridge.image" -}}
{{- $tag := default .Chart.AppVersion .Values.image.tag -}}
{{- printf "%s:%s" .Values.image.repository $tag -}}
{{- end -}}

{{/*
Valkey Secret name for chart-owned URL secrets.
*/}}
{{- define "deepseek-bridge.valkeySecretName" -}}
{{- printf "%s-valkey" (include "deepseek-bridge.fullname" .) -}}
{{- end -}}

{{/*
Bundled Valkey URL.
*/}}
{{- define "deepseek-bridge.internalValkeyUrl" -}}
{{- printf "valkey://%s-valkey:%d/0" (include "deepseek-bridge.fullname" .) (int .Values.valkey.service.port) -}}
{{- end -}}

{{/*
SQLite cache path. Persistence should imply a file-backed cache even when the
base default is the read-only-root-friendly in-memory cache.
*/}}
{{- define "deepseek-bridge.sqlitePath" -}}
{{- if and .Values.storage.sqlite.persistence.enabled (eq .Values.storage.sqlite.path ":memory:") -}}
{{- "/data/reasoning_content.sqlite3" -}}
{{- else -}}
{{- .Values.storage.sqlite.path -}}
{{- end -}}
{{- end -}}

{{/*
Run template-time validations that protect cache correctness and obvious
misconfiguration.
*/}}
{{- define "deepseek-bridge.validate" -}}
{{- $backend := lower .Values.storage.backend -}}
{{- if not (has $backend (list "sqlite" "valkey")) -}}
{{- fail "storage.backend must be either sqlite or valkey" -}}
{{- end -}}
{{- if and (not .Values.autoscaling.enabled) (gt (int .Values.replicaCount) 1) (ne $backend "valkey") (not .Values.storage.sqlite.allowMultiReplica) -}}
{{- fail "replicaCount > 1 requires storage.backend=valkey, or set storage.sqlite.allowMultiReplica=true for a deliberate local/dev exception" -}}
{{- end -}}
{{- if and .Values.autoscaling.enabled (gt (int .Values.autoscaling.maxReplicas) 1) (ne $backend "valkey") (not .Values.storage.sqlite.allowMultiReplica) -}}
{{- fail "autoscaling.maxReplicas > 1 requires storage.backend=valkey, or set storage.sqlite.allowMultiReplica=true for a deliberate local/dev exception" -}}
{{- end -}}
{{- if and (eq $backend "valkey") (not .Values.valkey.enabled) (not .Values.valkey.url) (not .Values.valkey.existingSecret) -}}
{{- fail "storage.backend=valkey requires valkey.enabled=true, valkey.url, or valkey.existingSecret" -}}
{{- end -}}
{{- if and .Values.serviceMonitor.enabled (not .Values.metrics.enabled) -}}
{{- fail "serviceMonitor.enabled requires metrics.enabled=true so /metrics is served" -}}
{{- end -}}
{{- if and .Values.grafanaDashboard.enabled (not .Values.metrics.enabled) -}}
{{- fail "grafanaDashboard.enabled requires metrics.enabled=true so dashboard application panels have metrics" -}}
{{- end -}}
{{- if and .Values.autoscaling.enabled .Values.autoscaling.targetCPUUtilizationPercentage (not .Values.resources.requests.cpu) -}}
{{- fail "autoscaling with targetCPUUtilizationPercentage requires resources.requests.cpu" -}}
{{- end -}}
{{- if and .Values.autoscaling.enabled .Values.autoscaling.targetMemoryUtilizationPercentage (not .Values.resources.requests.memory) -}}
{{- fail "autoscaling with targetMemoryUtilizationPercentage requires resources.requests.memory" -}}
{{- end -}}
{{- end -}}
