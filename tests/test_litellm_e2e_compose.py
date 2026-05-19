"""Opt-in Docker Compose smoke test for LiteLLM -> bridge -> DeepSeek Cloud."""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "litellm-e2e.sh"
RUN_SMOKE = os.environ.get("DEEPSEEK_BRIDGE_RUN_LITELLM_E2E") == "1"
HAS_LIVE_KEY = bool(
    os.environ.get("DEEPSEEK_API_KEY") or os.environ.get("LIVE_DEEPSEEK_KEY")
)
MISSING_TOOLS = [tool for tool in ("docker",) if shutil.which(tool) is None]


@unittest.skipUnless(
    RUN_SMOKE and HAS_LIVE_KEY,
    (
        "set DEEPSEEK_BRIDGE_RUN_LITELLM_E2E=1 and DEEPSEEK_API_KEY "
        "or LIVE_DEEPSEEK_KEY to run the live Compose e2e"
    ),
)
class LiteLLMComposeE2ETests(unittest.TestCase):
    def test_litellm_bridge_deepseek_cloud_e2e(self) -> None:
        if MISSING_TOOLS:
            self.fail("missing required tools: " + ", ".join(MISSING_TOOLS))

        timeout = int(
            os.environ.get("DEEPSEEK_BRIDGE_LITELLM_E2E_TEST_TIMEOUT", "2400")
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
