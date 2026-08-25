from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/validate.yml"


class ValidateWorkflowTests(unittest.TestCase):
    def test_pinned_uv_is_installed_after_python_and_before_unit_tests(self) -> None:
        workflow = WORKFLOW.read_text(encoding="utf-8")
        install_step = (
            '      - name: Install pinned uv\n'
            '        run: python3 -m pip install "uv==0.11.7"'
        )

        self.assertEqual(workflow.count(install_step), 1)
        self.assertLess(
            workflow.index('python-version: "3.12"'),
            workflow.index(install_step),
        )
        self.assertLess(
            workflow.index(install_step),
            workflow.index("python3 -m unittest discover -s tests -v"),
        )


if __name__ == "__main__":
    unittest.main()
