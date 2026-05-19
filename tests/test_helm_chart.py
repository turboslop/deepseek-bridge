"""Regression tests for the Helm chart."""

from __future__ import annotations

import json
import shutil
import subprocess
import unittest
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
CHART_DIR = REPO_ROOT / "charts" / "deepseek-bridge"
HELM = shutil.which("helm")


def _non_empty_docs(rendered: str) -> list[dict[str, Any]]:
    return [
        doc for doc in yaml.safe_load_all(rendered) if isinstance(doc, dict)
    ]


class GrafanaDashboardTests(unittest.TestCase):
    def setUp(self) -> None:
        dashboard_path = CHART_DIR / "dashboards" / "deepseek-bridge.json"
        self.dashboard = json.loads(dashboard_path.read_text())

    def _target_exprs(self) -> list[str]:
        exprs: list[str] = []
        for panel in self.dashboard["panels"]:
            for target in panel.get("targets", []):
                expr = target.get("expr")
                if isinstance(expr, str):
                    exprs.append(expr)
        return exprs

    def test_dashboard_defines_kubernetes_variables(self) -> None:
        variables = {
            item["name"]: item for item in self.dashboard["templating"]["list"]
        }

        self.assertIn("DS_PROMETHEUS", variables)
        self.assertIn("DS_LOKI", variables)
        self.assertIn("namespace", variables)
        self.assertIn("service", variables)
        self.assertIn("pod", variables)
        self.assertTrue(variables["namespace"]["includeAll"])
        self.assertTrue(variables["service"]["includeAll"])
        self.assertTrue(variables["pod"]["includeAll"])
        self.assertIn(
            "label_values(deepseek_bridge_streams_active, namespace)",
            variables["namespace"]["query"],
        )
        self.assertIn(
            "label_values(deepseek_bridge_streams_active",
            variables["service"]["query"],
        )
        self.assertIn('namespace=~"$namespace"', variables["service"]["query"])
        self.assertIn(
            "deepseek_bridge_streams_active",
            variables["pod"]["query"],
        )
        self.assertIn('service=~"$service"', variables["pod"]["query"])
        self.assertNotIn("allValue", variables["pod"])

    def test_dashboard_covers_issue_observability_panels(self) -> None:
        panel_titles = {panel["title"] for panel in self.dashboard["panels"]}

        self.assertIn("HTTP request rate", panel_titles)
        self.assertIn("HTTP error rate", panel_titles)
        self.assertIn("HTTP latency", panel_titles)
        self.assertIn("Upstream request rate", panel_titles)
        self.assertIn("Upstream latency", panel_titles)
        self.assertIn("Upstream error rate", panel_titles)
        self.assertIn("Active streaming responses", panel_titles)
        self.assertIn("Thread pool workers and queue", panel_titles)
        self.assertIn("Cache hit ratio", panel_titles)
        self.assertIn("Cache hits and misses", panel_titles)
        self.assertIn("Valkey operation latency", panel_titles)
        self.assertIn("Valkey storage errors", panel_titles)
        self.assertIn("Pod CPU usage", panel_titles)
        self.assertIn("Pod memory usage", panel_titles)
        self.assertIn("Pod restarts", panel_titles)
        self.assertIn("Pod logs (Loki)", panel_titles)

    def test_dashboard_uses_exported_metric_names(self) -> None:
        joined_exprs = "\n".join(self._target_exprs())

        self.assertIn("deepseek_bridge_http_requests_total", joined_exprs)
        self.assertIn(
            "deepseek_bridge_http_request_duration_seconds_bucket",
            joined_exprs,
        )
        self.assertIn("deepseek_bridge_upstream_requests_total", joined_exprs)
        self.assertIn(
            "deepseek_bridge_upstream_request_duration_seconds_bucket",
            joined_exprs,
        )
        self.assertIn("deepseek_bridge_streams_active", joined_exprs)
        self.assertIn("deepseek_bridge_thread_pool_active", joined_exprs)
        self.assertIn("deepseek_bridge_thread_pool_queue", joined_exprs)
        self.assertIn("deepseek_bridge_cache_hit_ratio", joined_exprs)
        self.assertIn("deepseek_bridge_cache_hits_total", joined_exprs)
        self.assertIn("deepseek_bridge_cache_misses_total", joined_exprs)
        self.assertIn(
            "deepseek_bridge_storage_operation_duration_seconds_bucket",
            joined_exprs,
        )
        self.assertIn("deepseek_bridge_storage_errors_total", joined_exprs)
        self.assertIn("histogram_quantile(0.95", joined_exprs)
        self.assertIn("$__rate_interval", joined_exprs)
        self.assertIn('namespace=~"$namespace"', joined_exprs)
        self.assertIn('service=~"$service"', joined_exprs)
        self.assertIn('pod=~"$pod"', joined_exprs)


@unittest.skipUnless(HELM, "helm is not installed")
class HelmChartTests(unittest.TestCase):
    def _helm(self, *args: str) -> subprocess.CompletedProcess[str]:
        assert HELM is not None
        return subprocess.run(
            [HELM, *args],
            cwd=REPO_ROOT,
            text=True,
            capture_output=True,
            check=False,
        )

    def _render(self, *args: str) -> list[dict[str, Any]]:
        result = self._helm(
            "template",
            "deepseek-bridge",
            str(CHART_DIR),
            *args,
        )
        if result.returncode != 0:
            self.fail(result.stderr)
        return _non_empty_docs(result.stdout)

    def _find(
        self, docs: list[dict[str, Any]], kind: str, name: str
    ) -> dict[str, Any]:
        for doc in docs:
            if doc.get("kind") == kind and doc["metadata"]["name"] == name:
                return doc
        raise AssertionError(f"missing {kind}/{name}")

    def _container(self, deployment: dict[str, Any]) -> dict[str, Any]:
        containers = deployment["spec"]["template"]["spec"]["containers"]
        for container in containers:
            if container["name"] == "deepseek-bridge":
                return container
        raise AssertionError("missing deepseek-bridge container")

    def _env(self, container: dict[str, Any]) -> dict[str, dict[str, Any]]:
        return {item["name"]: item for item in container["env"]}

    def test_helm_lint_passes(self) -> None:
        result = self._helm("lint", str(CHART_DIR))

        self.assertEqual(result.returncode, 0, result.stderr)

    def test_default_render_uses_sqlite_and_probes(self) -> None:
        docs = self._render()

        deployment = self._find(docs, "Deployment", "deepseek-bridge")
        service = self._find(docs, "Service", "deepseek-bridge")
        config_map = self._find(docs, "ConfigMap", "deepseek-bridge")
        container = self._container(deployment)
        env = self._env(container)
        config = yaml.safe_load(config_map["data"]["config.yaml"])

        self.assertEqual(deployment["spec"]["replicas"], 1)
        self.assertEqual(
            container["image"], "ghcr.io/turboslop/deepseek-bridge:latest"
        )
        self.assertEqual(
            service["spec"]["ports"][0],
            {
                "name": "http",
                "port": 9000,
                "targetPort": "http",
                "protocol": "TCP",
                "appProtocol": "http",
            },
        )
        self.assertIn("--runtime-mode", container["args"])
        self.assertIn("kubernetes", container["args"])
        self.assertEqual(
            container["livenessProbe"]["httpGet"],
            {"path": "/healthz", "port": "http"},
        )
        self.assertEqual(
            container["readinessProbe"]["httpGet"],
            {"path": "/readyz", "port": "http"},
        )
        self.assertEqual(
            env["DEEPSEEK_BRIDGE_STORAGE_BACKEND"]["value"], "sqlite"
        )
        self.assertEqual(
            env["DEEPSEEK_BRIDGE_REASONING_CONTENT_PATH"]["value"],
            ":memory:",
        )
        self.assertEqual(config["storage"]["backend"], "sqlite")
        self.assertFalse(config["metrics"]["enabled"])
        self.assertEqual(
            config["performance"]["max_request_body_bytes"], 20971520
        )
        pod_security = deployment["spec"]["template"]["spec"]["securityContext"]
        self.assertTrue(pod_security["runAsNonRoot"])
        self.assertEqual(pod_security["runAsUser"], 10001)
        self.assertEqual(
            container["securityContext"]["capabilities"]["drop"],
            ["ALL"],
        )

    def test_sqlite_persistence_uses_file_backed_cache_path(self) -> None:
        docs = self._render(
            "--set",
            "storage.sqlite.persistence.enabled=true",
        )

        deployment = self._find(docs, "Deployment", "deepseek-bridge")
        claim = self._find(
            docs, "PersistentVolumeClaim", "deepseek-bridge-data"
        )
        env = self._env(self._container(deployment))

        self.assertEqual(
            env["DEEPSEEK_BRIDGE_REASONING_CONTENT_PATH"]["value"],
            "/data/reasoning_content.sqlite3",
        )
        self.assertEqual(
            claim["spec"]["resources"]["requests"]["storage"], "1Gi"
        )

    def test_external_valkey_uses_existing_secret(self) -> None:
        docs = self._render(
            "--set",
            "replicaCount=2",
            "--set",
            "storage.backend=valkey",
            "--set",
            "valkey.existingSecret=deepseek-bridge-valkey",
            "--set",
            "valkey.existingSecretKey=url",
        )

        deployment = self._find(docs, "Deployment", "deepseek-bridge")
        env = self._env(self._container(deployment))

        self.assertEqual(deployment["spec"]["replicas"], 2)
        self.assertEqual(
            env["DEEPSEEK_BRIDGE_VALKEY_URL"]["valueFrom"]["secretKeyRef"],
            {"name": "deepseek-bridge-valkey", "key": "url"},
        )
        self.assertFalse(
            any(doc.get("kind") == "Secret" for doc in docs),
            "existingSecret mode should not render a chart-owned Secret",
        )

    def test_chart_owned_valkey_secret_renders(self) -> None:
        docs = self._render(
            "--set",
            "storage.backend=valkey",
            "--set",
            "valkey.url=valkey://valkey.default.svc:6379/0",
        )

        secret = self._find(docs, "Secret", "deepseek-bridge-valkey")
        deployment = self._find(docs, "Deployment", "deepseek-bridge")
        env = self._env(self._container(deployment))

        self.assertEqual(
            secret["stringData"]["url"],
            "valkey://valkey.default.svc:6379/0",
        )
        self.assertEqual(
            env["DEEPSEEK_BRIDGE_VALKEY_URL"]["valueFrom"]["secretKeyRef"],
            {"name": "deepseek-bridge-valkey", "key": "url"},
        )

    def test_bundled_valkey_renders_service_and_deployment(self) -> None:
        docs = self._render(
            "--set",
            "replicaCount=2",
            "--set",
            "storage.backend=valkey",
            "--set",
            "valkey.enabled=true",
        )

        app_deployment = self._find(docs, "Deployment", "deepseek-bridge")
        valkey_deployment = self._find(
            docs, "Deployment", "deepseek-bridge-valkey"
        )
        valkey_service = self._find(docs, "Service", "deepseek-bridge-valkey")
        env = self._env(self._container(app_deployment))

        self.assertEqual(valkey_deployment["spec"]["replicas"], 1)
        self.assertEqual(valkey_service["spec"]["ports"][0]["port"], 6379)
        self.assertEqual(
            env["DEEPSEEK_BRIDGE_VALKEY_URL"]["value"],
            "valkey://deepseek-bridge-valkey:6379/0",
        )

    def test_metrics_servicemonitor_and_dashboard_render(self) -> None:
        docs = self._render(
            "--set",
            "metrics.enabled=true",
            "--set",
            "serviceMonitor.enabled=true",
            "--set",
            "grafanaDashboard.enabled=true",
        )

        deployment = self._find(docs, "Deployment", "deepseek-bridge")
        service_monitor = self._find(docs, "ServiceMonitor", "deepseek-bridge")
        dashboard = self._find(docs, "ConfigMap", "deepseek-bridge-dashboard")
        env = self._env(self._container(deployment))

        self.assertEqual(
            env["DEEPSEEK_BRIDGE_METRICS_ENABLED"]["value"], "true"
        )
        self.assertEqual(
            service_monitor["spec"]["endpoints"][0]["path"], "/metrics"
        )
        rendered_dashboard = json.loads(
            dashboard["data"]["deepseek-bridge.json"]
        )
        self.assertEqual(rendered_dashboard["uid"], "deepseek-bridge")
        self.assertEqual(
            rendered_dashboard["title"], "DeepSeek Bridge Kubernetes"
        )
        self.assertTrue(rendered_dashboard["panels"])
        self.assertIn("templating", rendered_dashboard)
        self.assertIn(
            "deepseek_bridge_cache_hit_ratio",
            dashboard["data"]["deepseek-bridge.json"],
        )

    def test_grafana_dashboard_requires_metrics_endpoint(self) -> None:
        result = self._helm(
            "template",
            "deepseek-bridge",
            str(CHART_DIR),
            "--set",
            "grafanaDashboard.enabled=true",
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("grafanaDashboard.enabled requires", result.stderr)

    def test_ingress_and_httproute_render(self) -> None:
        docs = self._render(
            "--set",
            "ingress.enabled=true",
            "--set",
            "ingress.hosts[0].host=bridge.example.com",
            "--set",
            "httpRoute.enabled=true",
            "--set",
            "httpRoute.parentRefs[0].name=public-gateway",
            "--set",
            "httpRoute.hostnames[0]=bridge.example.com",
        )

        ingress = self._find(docs, "Ingress", "deepseek-bridge")
        route = self._find(docs, "HTTPRoute", "deepseek-bridge")

        self.assertEqual(
            ingress["spec"]["rules"][0]["host"], "bridge.example.com"
        )
        self.assertEqual(
            route["spec"]["parentRefs"][0]["name"], "public-gateway"
        )
        self.assertEqual(route["spec"]["hostnames"], ["bridge.example.com"])

    def test_hpa_and_pdb_render(self) -> None:
        docs = self._render(
            "--set",
            "storage.backend=valkey",
            "--set",
            "valkey.enabled=true",
            "--set",
            "autoscaling.enabled=true",
            "--set",
            "autoscaling.maxReplicas=3",
            "--set",
            "resources.requests.cpu=100m",
            "--set",
            "resources.requests.memory=128Mi",
            "--set",
            "podDisruptionBudget.enabled=true",
        )

        deployment = self._find(docs, "Deployment", "deepseek-bridge")
        hpa = self._find(docs, "HorizontalPodAutoscaler", "deepseek-bridge")
        pdb = self._find(docs, "PodDisruptionBudget", "deepseek-bridge")

        self.assertNotIn("replicas", deployment["spec"])
        self.assertEqual(hpa["spec"]["maxReplicas"], 3)
        self.assertEqual(pdb["spec"]["maxUnavailable"], 1)

    def test_multi_replica_sqlite_fails_without_override(self) -> None:
        result = self._helm(
            "template",
            "deepseek-bridge",
            str(CHART_DIR),
            "--set",
            "replicaCount=2",
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("replicaCount > 1 requires", result.stderr)


if __name__ == "__main__":
    unittest.main()
