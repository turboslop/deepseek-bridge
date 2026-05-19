"""Regression tests for release automation."""

from __future__ import annotations

import unittest
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
RELEASE_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "release.yml"


class ReleaseWorkflowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.workflow = yaml.safe_load(RELEASE_WORKFLOW.read_text())

    def _step(self, job: dict[str, Any], name: str) -> dict[str, Any]:
        for step in job["steps"]:
            if step.get("name") == name:
                return step
        raise AssertionError(f"missing step: {name}")

    def test_chart_version_is_plain_release_semver(self) -> None:
        meta_job = self.workflow["jobs"]["release-metadata"]
        meta_step = self._step(meta_job, "Derive versions from tag")

        self.assertIn('echo "chart_version=${version}"', meta_step["run"])
        self.assertNotIn("${version}-chart", meta_step["run"])

    def test_helm_release_updates_shared_repo_index(self) -> None:
        helm_job = self.workflow["jobs"]["helm"]

        token_step = self._step(helm_job, "Create Helm repository app token")
        self.assertEqual(
            token_step["uses"], "actions/create-github-app-token@v3"
        )
        self.assertEqual(token_step["with"]["repositories"], "helm")
        self.assertEqual(token_step["with"]["permission-contents"], "write")

        checkout_step = self._step(helm_job, "Checkout Helm repository")
        self.assertEqual(checkout_step["with"]["repository"], "turboslop/helm")
        self.assertEqual(checkout_step["with"]["ref"], "gh-pages")
        self.assertEqual(checkout_step["with"]["path"], "helm-repo")

        index_step = self._step(
            helm_job, "Publish Helm chart to repository index"
        )
        self.assertEqual(
            index_step["env"]["HELM_REPO_URL"],
            "https://turboslop.github.io/helm",
        )
        self.assertIn('cp -f "${CHART_PACKAGE}"', index_step["run"])
        self.assertIn("helm repo index", index_step["run"])
        self.assertIn("--merge", index_step["run"])

        push_step = self._step(helm_job, "Push Helm repository updates")
        self.assertEqual(push_step["working-directory"], "helm-repo")
        self.assertIn(
            "chore(chart): publish deepseek-bridge ${CHART_VERSION}",
            push_step["run"],
        )
