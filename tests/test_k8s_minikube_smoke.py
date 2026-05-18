"""Opt-in Minikube smoke test wrapper."""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "k8s-minikube-smoke.sh"
RUN_SMOKE = os.environ.get("DEEPSEEK_BRIDGE_RUN_K8S_SMOKE") == "1"
REQUIRED_TOOLS = ("docker", "helm", "kubectl", "minikube", "curl")
MISSING_TOOLS = [tool for tool in REQUIRED_TOOLS if shutil.which(tool) is None]


@unittest.skipUnless(
    RUN_SMOKE,
    "set DEEPSEEK_BRIDGE_RUN_K8S_SMOKE=1 to run the Minikube smoke test",
)
class K8sMinikubeSmokeTests(unittest.TestCase):
    def test_minikube_smoke_script(self) -> None:
        if MISSING_TOOLS:
            self.fail("missing required tools: " + ", ".join(MISSING_TOOLS))

        timeout = int(
            os.environ.get("DEEPSEEK_BRIDGE_K8S_SMOKE_TEST_TIMEOUT", "1800")
        )
        process = subprocess.Popen(
            [str(SCRIPT)],
            cwd=REPO_ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
        timed_out = False
        try:
            stdout, stderr = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            os.killpg(process.pid, signal.SIGTERM)
            try:
                stdout, stderr = process.communicate(timeout=60)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                stdout, stderr = process.communicate()
        returncode = process.returncode

        self.assertEqual(
            returncode,
            0,
            (
                f"timed out: {timed_out}\n"
                f"stdout:\n{stdout}\n\nstderr:\n{stderr}"
            ),
        )


if __name__ == "__main__":
    unittest.main()
