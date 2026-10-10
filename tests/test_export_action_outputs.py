"""Tests for GitHub Action output export and step summary."""

from __future__ import annotations

import os
import shutil
import tempfile
import unittest
from contextlib import contextmanager
from typing import Iterator

from tests._helpers import fixture_path, load_script

exporter = load_script("export_action_outputs")


@contextmanager
def workspace() -> Iterator[str]:
    """A temp directory containing a realistic .sanityops/ tree."""
    tmp = tempfile.mkdtemp()
    results = os.path.join(tmp, ".sanityops", "results")
    logs = os.path.join(tmp, ".sanityops", "logs")
    os.makedirs(results)
    os.makedirs(logs)
    shutil.copy(
        fixture_path("inspect-20260101-120000-000000.md"),
        os.path.join(results, "inspect-20260101-120000-000000.md"),
    )
    with open(os.path.join(logs, "sanityops-cli-2026-01-01.log"), "w", encoding="utf-8") as fh:
        fh.write("log line\n")
    previous = os.getcwd()
    os.chdir(tmp)
    try:
        yield tmp
    finally:
        os.chdir(previous)
        shutil.rmtree(tmp, ignore_errors=True)


class TestCollectOutputs(unittest.TestCase):
    def test_reads_defect_counts_from_report(self) -> None:
        with workspace():
            outputs = exporter.collect_outputs(0, 0)
        self.assertEqual(outputs["total-defects"], "7")
        self.assertEqual(outputs["p0-defects"], "2")
        self.assertEqual(outputs["p1-defects"], "3")
        self.assertEqual(outputs["p2-defects"], "2")

    def test_exit_code_and_gate_result_follow_the_gate(self) -> None:
        with workspace():
            self.assertEqual(exporter.collect_outputs(0, 0)["exit-code"], "0")
            self.assertEqual(exporter.collect_outputs(0, 0)["gate-result"], "PASS")
            self.assertEqual(exporter.collect_outputs(0, 1)["exit-code"], "1")
            self.assertEqual(exporter.collect_outputs(0, 1)["gate-result"], "FAIL")

    def test_soft_gate_reports_pass_even_with_defects(self) -> None:
        # The report says Gate FAIL, but the action's soft gate passed.
        with workspace():
            outputs = exporter.collect_outputs(0, 0)
        self.assertEqual(outputs["gate-result"], "PASS")

    def test_paths_point_into_sanityops_tree(self) -> None:
        with workspace():
            outputs = exporter.collect_outputs(0, 0)
        self.assertTrue(outputs["report-path"].startswith(".sanityops/results/inspect-"))
        self.assertTrue(outputs["log-path"].startswith(".sanityops/logs/"))

    def test_missing_report_yields_zero_counts(self) -> None:
        tmp = tempfile.mkdtemp()
        previous = os.getcwd()
        os.chdir(tmp)
        try:
            outputs = exporter.collect_outputs(0, 0)
        finally:
            os.chdir(previous)
            shutil.rmtree(tmp, ignore_errors=True)
        self.assertEqual(outputs["total-defects"], "0")
        self.assertEqual(outputs["report-path"], "")


class TestBuildSummary(unittest.TestCase):
    def test_summary_reports_both_exit_codes(self) -> None:
        with workspace():
            outputs = exporter.collect_outputs(2, 1)
            summary = exporter.build_summary(2, 1, outputs)
        self.assertIn("SanityOps Inspection", summary)
        self.assertIn("CLI exit code", summary)
        self.assertIn("Verdict", summary)

    def test_summary_includes_report_when_present(self) -> None:
        with workspace():
            outputs = exporter.collect_outputs(0, 0)
            summary = exporter.build_summary(0, 0, outputs)
        self.assertIn("# Inspect Report", summary)

    def test_summary_survives_a_missing_report(self) -> None:
        # build_summary resolves report-path relative to the cwd, so a report that
        # has gone away must degrade to metrics only rather than raising.
        metrics = {
            "total-defects": "0",
            "p0-defects": "0",
            "p1-defects": "0",
            "p2-defects": "0",
            "report-path": "does-not-exist/inspect-x.md",
            "log-path": "",
        }
        summary = exporter.build_summary(0, 0, metrics)
        self.assertIn("SanityOps Inspection", summary)
        self.assertNotIn("# Inspect Report", summary)


class TestMain(unittest.TestCase):
    def test_writes_github_output(self) -> None:
        with workspace() as tmp:
            out_path = os.path.join(tmp, "github_output")
            summary_path = os.path.join(tmp, "step_summary")
            os.environ["GITHUB_OUTPUT"] = out_path
            os.environ["GITHUB_STEP_SUMMARY"] = summary_path
            try:
                code = exporter.main(["0", "0"])
            finally:
                os.environ.pop("GITHUB_OUTPUT", None)
                os.environ.pop("GITHUB_STEP_SUMMARY", None)

            self.assertEqual(code, 0)
            with open(out_path, encoding="utf-8") as fh:
                written = fh.read()
            self.assertIn("total-defects=7", written)
            self.assertIn("gate-result=PASS", written)
            with open(summary_path, encoding="utf-8") as fh:
                self.assertIn("SanityOps Inspection", fh.read())


if __name__ == "__main__":
    unittest.main()
