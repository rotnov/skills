from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/validate.yml"


class ValidateWorkflowTests(unittest.TestCase):
    def test_ci_uses_the_pinned_uv_environment_for_python_checks(self) -> None:
        workflow = WORKFLOW.read_text(encoding="utf-8")
        install_match = re.search(
            r"(?m)^      - name: Install pinned uv\n"
            r"        uses: astral-sh/setup-uv@([0-9a-f]{40})(?: +#.*)?$",
            workflow,
        )

        self.assertIsNotNone(install_match)
        install_offset = install_match.start() if install_match else -1
        self.assertNotIn('          version: "', workflow)
        self.assertLess(
            install_offset,
            workflow.index("uv python install"),
        )
        self.assertLess(
            workflow.index("uv python install"),
            workflow.index("uv sync --frozen"),
        )
        self.assertLess(
            workflow.index("uv sync --frozen"),
            workflow.index("uv run --frozen python -m unittest discover -s tests -v"),
        )
        self.assertNotIn("python3 scripts/validate_skills.py", workflow)
        self.assertNotIn("python3 -m unittest", workflow)

    def test_ci_runs_pre_commit_from_the_locked_environment(self) -> None:
        workflow = WORKFLOW.read_text(encoding="utf-8")

        self.assertIn(
            "uv run --frozen pre-commit run --all-files",
            workflow,
        )
        self.assertNotIn("python3 -m pip install", workflow)


if __name__ == "__main__":
    unittest.main()
