from __future__ import annotations

import argparse
import importlib.util
import unittest
from pathlib import Path


SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "skills"
    / "i-have-an-issue"
    / "scripts"
    / "search_github.py"
)
SPEC = importlib.util.spec_from_file_location("search_github", SCRIPT)
assert SPEC and SPEC.loader
search_github = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(search_github)


class SearchGithubTests(unittest.TestCase):
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
