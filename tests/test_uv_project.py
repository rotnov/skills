from __future__ import annotations

import json
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class UvProjectTests(unittest.TestCase):
    def test_locked_environment_has_dev_tools_without_installing_a_package(self) -> None:
        probe = """
import importlib.metadata
import json
import sys

try:
    importlib.metadata.version("rotnov-skills")
except importlib.metadata.PackageNotFoundError:
    project_installed = False
else:
    project_installed = True

print(json.dumps({
    "python": list(sys.version_info[:2]),
    "pre_commit": importlib.metadata.version("pre-commit"),
    "skills_ref": importlib.metadata.version("skills-ref"),
    "project_installed": project_installed,
}))
"""
        completed = subprocess.run(
            ["uv", "run", "--frozen", "python", "-c", probe],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        python_version = [
            int(part)
            for part in (ROOT / ".python-version").read_text().strip().split(".")
        ]
        result = json.loads(completed.stdout)
        self.assertEqual(result["python"], python_version)
        self.assertTrue(result["pre_commit"])
        self.assertTrue(result["skills_ref"])
        self.assertFalse(result["project_installed"])


if __name__ == "__main__":
    unittest.main()
