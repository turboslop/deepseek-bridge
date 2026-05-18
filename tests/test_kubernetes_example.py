"""Regression tests for the Kubernetes example manifest."""

import re
import unittest
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]


class KubernetesExampleTests(unittest.TestCase):
    """Validate deployment assumptions that are easy to drift in docs."""

    def _deployment(self) -> dict:
        manifest_path = (
            REPO_ROOT / "examples" / "kubernetes" / "deployment.yaml"
        )
        manifest = manifest_path.read_text(encoding="utf-8")
        docs = list(yaml.safe_load_all(manifest))
        for doc in docs:
            if doc.get("kind") == "Deployment":
                return doc
        raise AssertionError("Kubernetes example does not contain a Deployment")

    def _container(self) -> dict:
        containers = self._deployment()["spec"]["template"]["spec"][
            "containers"
        ]
        for container in containers:
            if container.get("name") == "deepseek-bridge":
                return container
        raise AssertionError(
            "Kubernetes example does not contain the app container"
        )

    def test_pod_security_context_matches_dockerfile_user(self) -> None:
        dockerfile = (REPO_ROOT / "Dockerfile").read_text(encoding="utf-8")
        match = re.search(r"^USER\s+(\d+):(\d+)$", dockerfile, re.MULTILINE)
        self.assertIsNotNone(match)
        uid, gid = (int(value) for value in match.groups())

        pod_security_context = self._deployment()["spec"]["template"]["spec"][
            "securityContext"
        ]

        self.assertTrue(pod_security_context["runAsNonRoot"])
        self.assertEqual(pod_security_context["runAsUser"], uid)
        self.assertEqual(pod_security_context["runAsGroup"], gid)
        self.assertEqual(pod_security_context["fsGroup"], gid)

    def test_read_only_root_uses_valkey_without_cache_mount(self) -> None:
        container = self._container()

        self.assertTrue(container["securityContext"]["readOnlyRootFilesystem"])
        self.assertNotIn("--reasoning-content-path", container["args"])
        env = {item["name"]: item for item in container["env"]}
        self.assertEqual(
            env["DEEPSEEK_BRIDGE_STORAGE_BACKEND"]["value"],
            "valkey",
        )
        self.assertEqual(
            env["DEEPSEEK_BRIDGE_VALKEY_URL"]["valueFrom"]["secretKeyRef"],
            {"name": "deepseek-bridge-valkey", "key": "url"},
        )
        self.assertNotIn("volumeMounts", container)

    def test_valkey_example_uses_multiple_replicas(self) -> None:
        deployment = self._deployment()

        self.assertEqual(deployment["spec"]["replicas"], 2)


if __name__ == "__main__":
    unittest.main()
