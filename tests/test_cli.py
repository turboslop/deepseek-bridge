from __future__ import annotations

import subprocess
import sys
import unittest


class CliImportTests(unittest.TestCase):
    """Verify the CLI module loads without errors and --help works."""

    def test_module_imports(self) -> None:
        """cli.py can be imported without errors."""
        from deepseek_bridge.cli import build_arg_parser, main
        self.assertIsNotNone(build_arg_parser)
        self.assertIsNotNone(main)

    def test_arg_parser_returns_parser(self) -> None:
        """build_arg_parser returns an ArgumentParser."""
        from deepseek_bridge.cli import build_arg_parser
        parser = build_arg_parser()
        from argparse import ArgumentParser
        self.assertIsInstance(parser, ArgumentParser)

    def test_help_exits_zero(self) -> None:
        """--help returns exit code 0."""
        result = subprocess.run(
            [sys.executable, "-m", "deepseek_bridge", "--help"],
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0)
        self.assertIn("deepseek bridge", result.stdout.lower())
