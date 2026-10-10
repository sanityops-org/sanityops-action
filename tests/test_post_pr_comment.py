"""Tests for the GitHub pull request comment."""

from __future__ import annotations

import json
import os
import re
import tempfile
import unittest

from tests._helpers import CJK_PATTERN, fixture_path, load_script

commenter = load_script("post_pr_comment")


def fixture_text() -> str:
    with open(fixture_path("inspect-20260101-120000-000000.md"), encoding="utf-8") as fh:
        return fh.read()


class TestParseReportSummary(unittest.TestCase):
    def test_extracts_counts_from_fixture(self) -> None:
        summary = commenter.parse_report_summary(fixture_text())
        self.assertEqual(summary["total"], "7")
        self.assertEqual(summary["p0"], "2")
        self.assertEqual(summary["p1"], "3")
        self.assertEqual(summary["p2"], "2")
        self.assertEqual(summary["gate"], "FAIL")
        self.assertEqual(summary["time"], "42.10s")

    def test_missing_metrics_are_safe(self) -> None:
        summary = commenter.parse_report_summary("nothing here")
        self.assertEqual(summary["total"], "0")
        self.assertEqual(summary["gate"], "UNKNOWN")


class TestBuildCommentBody(unittest.TestCase):
    def test_body_is_english_and_titled(self) -> None:
        body = commenter.build_comment_body(fixture_text())
        self.assertTrue(body.startswith(commenter.COMMENT_HEADER))
        self.assertEqual(commenter.COMMENT_HEADER, "## 🔍 SanityOps Defect Inspection Report")
        self.assertIn("Inspection Summary", body)
        self.assertIn("Total Defects", body)

    def test_body_includes_counts_and_report(self) -> None:
        body = commenter.build_comment_body(fixture_text())
        self.assertIn("**7**", body)
        self.assertIn("**2**", body)
        self.assertIn("# Inspect Report", body)

    def test_no_cjk_in_body(self) -> None:
        body = commenter.build_comment_body(fixture_text())
        self.assertIsNone(CJK_PATTERN.search(body))

    def test_truncates_oversized_report(self) -> None:
        body = commenter.build_comment_body("x" * 70_000)
        self.assertLess(len(body), 65_536 + 1_000)
        self.assertIn("truncated", body)


class TestGetPrNumber(unittest.TestCase):
    def setUp(self) -> None:
        self._saved = {
            k: os.environ.get(k)
            for k in ("GITHUB_EVENT_PATH", "GITHUB_REF", "INPUT_PR_NUMBER")
        }
        for key in self._saved:
            os.environ.pop(key, None)

    def tearDown(self) -> None:
        for key, value in self._saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def test_reads_event_payload(self) -> None:
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
            json.dump({"pull_request": {"number": 123}}, fh)
            path = fh.name
        try:
            os.environ["GITHUB_EVENT_PATH"] = path
            self.assertEqual(commenter.get_pr_number(), 123)
        finally:
            os.unlink(path)

    def test_falls_back_to_ref(self) -> None:
        os.environ["GITHUB_REF"] = "refs/pull/456/merge"
        self.assertEqual(commenter.get_pr_number(), 456)

    def test_returns_none_outside_pr_context(self) -> None:
        os.environ["GITHUB_REF"] = "refs/heads/main"
        self.assertIsNone(commenter.get_pr_number())


class TestReportDiscovery(unittest.TestCase):
    """The report is looked up where the CLI writes it, not anywhere in the repo."""

    def setUp(self) -> None:
        self._saved = os.environ.get("SANITYOPS_ACTION_RESULTS_DIR")
        os.environ.pop("SANITYOPS_ACTION_RESULTS_DIR", None)

    def tearDown(self) -> None:
        if self._saved is not None:
            os.environ["SANITYOPS_ACTION_RESULTS_DIR"] = self._saved

    def test_patterns_are_bounded(self) -> None:
        # A repo-wide search also matches unrelated files such as checked-in
        # fixtures, which were reported as this run's result.
        patterns = commenter.report_patterns()
        self.assertNotIn("**", " ".join(patterns))
        self.assertIn(".sanityops/results/inspect-*.md", patterns)

    def test_configured_directory_takes_precedence(self) -> None:
        os.environ["SANITYOPS_ACTION_RESULTS_DIR"] = "config/sanityops/results"
        self.assertEqual(
            commenter.report_patterns()[0], "config/sanityops/results/inspect-*.md"
        )


if __name__ == "__main__":
    unittest.main()
