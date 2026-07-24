from __future__ import annotations

import argparse
import importlib.util
import subprocess
import sys
import unittest
from pathlib import Path


SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "skills"
    / "i-have-an-issue"
    / "scripts"
    / "search_github.py"
)
SKILL_ROOT = SCRIPT.parents[1]
SPEC = importlib.util.spec_from_file_location("search_github", SCRIPT)
assert SPEC and SPEC.loader
search_github = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(search_github)


class SearchGithubTests(unittest.TestCase):
    def test_documented_helper_path_is_skill_relative(self) -> None:
        for path in (
            SKILL_ROOT / "SKILL.md",
            SKILL_ROOT / "references" / "research-playbook.md",
        ):
            text = path.read_text(encoding="utf-8")
            self.assertNotIn("python3 scripts/search_github.py", text)
            self.assertIn(
                'python3 "$SKILL_DIR/scripts/search_github.py"',
                text,
            )

    def test_helper_help_runs_from_repository_root(self) -> None:
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--help"],
            cwd=SKILL_ROOT.parents[1],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Discover GitHub candidates", result.stdout)

    def test_build_query_for_closed_issues(self) -> None:
        args = argparse.Namespace(
            query='"stale cache"',
            repo="owner/project",
            kind="issues",
            item_type="issue",
            state="closed",
        )
        self.assertEqual(
            search_github.build_query(args),
            '"stale cache" repo:owner/project is:issue state:closed',
        )

    def test_normalize_pull_request(self) -> None:
        normalized = search_github.normalize_issue(
            {
                "pull_request": {},
                "repository_url": "https://api.github.com/repos/owner/project",
                "number": 42,
                "title": "Fix stale cache",
                "state": "closed",
                "html_url": "https://github.com/owner/project/pull/42",
                "user": {"login": "maintainer"},
                "labels": [{"name": "regression"}],
                "body": "A   reproducible\nregression",
                "score": 1.0,
            },
            100,
        )
        self.assertEqual(normalized["kind"], "pull_request")
        self.assertEqual(normalized["repository"], "owner/project")
        self.assertEqual(normalized["body_excerpt"], "A reproducible regression")

    def test_compact_text_truncates(self) -> None:
        self.assertEqual(search_github.compact_text("abcdef", 4), "abc…")
        self.assertEqual(search_github.compact_text("abcdef", 0), "")

    def test_search_result_limit_enforces_github_cap(self) -> None:
        self.assertEqual(search_github.search_result_limit("1000"), 1000)
        with self.assertRaises(argparse.ArgumentTypeError):
            search_github.search_result_limit("1001")

    def test_repository_from_enterprise_api_url(self) -> None:
        self.assertEqual(
            search_github.repository_from_api_url(
                "https://github.example/api/v3/repos/owner/project"
            ),
            "owner/project",
        )
        self.assertIsNone(
            search_github.repository_from_api_url("https://example.invalid/no-repo")
        )


if __name__ == "__main__":
    unittest.main()
