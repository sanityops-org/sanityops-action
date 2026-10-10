"""Tests for the GitLab merge request comment."""

from __future__ import annotations

import os
import unittest

from tests._helpers import CJK_PATTERN, fixture_path, load_script

commenter = load_script("post_mr_comment")


def fixture_text() -> str:
    with open(fixture_path("inspect-20260101-120000-000000.md"), encoding="utf-8") as fh:
        return fh.read()


class TestParseReportSummary(unittest.TestCase):
    def test_extracts_counts_from_fixture(self) -> None:
        summary = commenter.parse_report_summary(fixture_text())
        self.assertEqual(summary["total"], "7")
        self.assertEqual(summary["p0"], "2")
        self.assertEqual(summary["gate"], "FAIL")
        self.assertEqual(summary["time"], "42.10s")


class TestBuildCommentBody(unittest.TestCase):
    def test_header_matches_github_script(self) -> None:
        self.assertEqual(commenter.COMMENT_HEADER, "## 🔍 SanityOps Defect Inspection Report")

    def test_body_uses_merge_request_wording(self) -> None:
        body = commenter.build_comment_body(fixture_text())
        self.assertIn("merge request", body)
        self.assertIn("GitLab CI", body)
        self.assertIn("Inspection Summary", body)

    def test_body_includes_counts_and_report(self) -> None:
        body = commenter.build_comment_body(fixture_text())
        self.assertIn("**7**", body)
        self.assertIn("# Inspect Report", body)

    def test_no_cjk_in_body(self) -> None:
        body = commenter.build_comment_body(fixture_text())
        self.assertIsNone(CJK_PATTERN.search(body))

    def test_truncates_oversized_report(self) -> None:
        body = commenter.build_comment_body("x" * 90_000)
        self.assertIn("truncated", body)
        self.assertLess(len(body), 80_000 + 1_000)


class TestNoHardcodedEndpoint(unittest.TestCase):
    def test_does_not_ship_an_internal_gitlab_host(self) -> None:
        """The reference implementation defaulted to a private IP. It must not ship."""
        path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "scripts", "post_mr_comment.py")
        with open(path, encoding="utf-8") as fh:
            source = fh.read()
        self.assertNotIn("192.168.", source)


class TestMainContextDetection(unittest.TestCase):
    def setUp(self) -> None:
        self._saved = {k: os.environ.get(k) for k in ("CI_MERGE_REQUEST_IID", "CI_PROJECT_ID")}
        for key in self._saved:
            os.environ.pop(key, None)

    def tearDown(self) -> None:
        for key, value in self._saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def test_skips_outside_merge_request_pipeline(self) -> None:
        with self.assertRaises(SystemExit) as ctx:
            commenter.main()
        self.assertEqual(ctx.exception.code, 0)

    def test_skips_when_project_id_missing(self) -> None:
        os.environ["CI_MERGE_REQUEST_IID"] = "7"
        with self.assertRaises(SystemExit) as ctx:
            commenter.main()
        self.assertEqual(ctx.exception.code, 0)


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
