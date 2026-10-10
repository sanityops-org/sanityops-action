"""Truth-table tests for the SanityOps quality gate (spec section 8)."""

from __future__ import annotations

import os
import tempfile
import unittest

from tests._helpers import fixture_path, load_script

gate = load_script("evaluate_gate")

SUMMARY = {"total": 7, "p0": 2, "p1": 3, "p2": 2, "gate": "FAIL"}
CLEAN = {"total": 0, "p0": 0, "p1": 0, "p2": 0, "gate": "PASS"}


def decide(cli_exit=0, skip=False, block_p0=False, fail_all=False, summary=SUMMARY):
    return gate.decide(
        cli_exit=cli_exit,
        skip_defect_check=skip,
        block_on_p0=block_p0,
        fail_on_defect=fail_all,
        summary=summary,
    )


class TestDecideTruthTable(unittest.TestCase):
    def test_row1_cli_failure_always_fails(self) -> None:
        code, message = decide(cli_exit=2)
        self.assertEqual(code, gate.EXIT_FAIL)
        self.assertIn("exit code 2", message)

    def test_row1_cli_failure_fails_even_under_soft_gate(self) -> None:
        code, _ = decide(cli_exit=2, summary=CLEAN)
        self.assertEqual(code, gate.EXIT_FAIL)

    def test_row2_no_report_with_skip_passes(self) -> None:
        code, message = decide(summary=None, skip=True)
        self.assertEqual(code, gate.EXIT_PASS)
        self.assertIn("skip-defect-check", message)

    def test_row3_no_report_without_skip_fails(self) -> None:
        code, message = decide(summary=None, skip=False)
        self.assertEqual(code, gate.EXIT_FAIL)
        self.assertIn("no gate verdict is available", message)

    def test_row4_block_on_p0_with_p0_fails(self) -> None:
        code, message = decide(block_p0=True)
        self.assertEqual(code, gate.EXIT_FAIL)
        self.assertIn("P0", message)

    def test_row5_block_on_p0_without_p0_passes_despite_defects(self) -> None:
        code, _ = decide(
            block_p0=True,
            summary={"total": 5, "p0": 0, "p1": 3, "p2": 2, "gate": "PASS"},
        )
        self.assertEqual(code, gate.EXIT_PASS)

    def test_row6_fail_on_defect_with_defects_fails(self) -> None:
        code, message = decide(fail_all=True)
        self.assertEqual(code, gate.EXIT_FAIL)
        self.assertIn("7 defect", message)

    def test_row6_fail_on_defect_with_no_defects_passes(self) -> None:
        code, _ = decide(fail_all=True, summary=CLEAN)
        self.assertEqual(code, gate.EXIT_PASS)

    def test_row7_soft_gate_passes_with_defects(self) -> None:
        code, message = decide()
        self.assertEqual(code, gate.EXIT_PASS)
        self.assertIn("soft", message)

    def test_row7_soft_gate_passes_with_no_defects(self) -> None:
        code, _ = decide(summary=CLEAN)
        self.assertEqual(code, gate.EXIT_PASS)

    def test_block_on_p0_takes_precedence_over_fail_on_defect(self) -> None:
        # Both gates on, no P0, defects present: block-on-p0 wins and passes.
        code, _ = decide(
            block_p0=True,
            fail_all=True,
            summary={"total": 4, "p0": 0, "p1": 4, "p2": 0, "gate": "PASS"},
        )
        self.assertEqual(code, gate.EXIT_PASS)


class TestParseReport(unittest.TestCase):
    def test_parses_fixture_summary_table(self) -> None:
        with open(fixture_path("inspect-20260101-120000-000000.md"), encoding="utf-8") as fh:
            summary = gate.parse_report(fh.read())
        self.assertEqual(summary["total"], 7)
        self.assertEqual(summary["p0"], 2)
        self.assertEqual(summary["p1"], 3)
        self.assertEqual(summary["p2"], 2)
        self.assertEqual(summary["gate"], "FAIL")

    def test_missing_metrics_default_to_zero(self) -> None:
        summary = gate.parse_report("# Inspect Report\n\nNo table here.\n")
        self.assertEqual(summary["total"], 0)
        self.assertEqual(summary["p0"], 0)
        self.assertEqual(summary["gate"], "UNKNOWN")


class TestFindLatestReport(unittest.TestCase):
    def test_returns_newest_report(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            results = os.path.join(tmp, "results")
            os.makedirs(results)
            names = ("inspect-20260101-000000-000000.md", "inspect-20260102-000000-000000.md")
            for name in names:
                with open(os.path.join(results, name), "w", encoding="utf-8") as fh:
                    fh.write("x")
            oldest = os.path.join(results, names[0])
            os.utime(oldest, (1_600_000_000, 1_600_000_000))
            self.assertTrue(gate.find_latest_report(results).endswith(names[1]))

    def test_returns_none_when_absent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(gate.find_latest_report(tmp))


class TestMain(unittest.TestCase):
    def test_main_exit_code_reflects_verdict(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(
                gate.main(["--exit-code", "0", "--results-dir", tmp, "--skip-defect-check", "true"]),
                0,
            )
            self.assertEqual(
                gate.main(["--exit-code", "0", "--results-dir", tmp, "--skip-defect-check", "false"]),
                1,
            )

    def test_env_fallback_used_when_flags_absent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env = {"SANITYOPS_GATE_CLI_EXIT": "2", "SANITYOPS_ACTION_RESULTS_DIR": tmp}
            original = {k: os.environ.get(k) for k in env}
            os.environ.update(env)
            try:
                self.assertEqual(gate.main([]), 1)
            finally:
                for key, value in original.items():
                    if value is None:
                        os.environ.pop(key, None)
                    else:
                        os.environ[key] = value


if __name__ == "__main__":
    unittest.main()
