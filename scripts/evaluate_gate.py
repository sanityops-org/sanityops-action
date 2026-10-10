#!/usr/bin/env python3
"""Evaluate the SanityOps quality gate for CI.

`sanityops-cli inspect` exits 0 whenever the run completes, including when
defects are found, and 2 when the run fails (EXIT_FAILURE). The CLI exit code
therefore carries no gate meaning: the verdict is derived from the markdown
report the CLI writes to <config-dir>/results/inspect-*.md.

The report's own `Gate` row is deliberately NOT used for the decision. That row
is FAIL whenever any defect group holds a P0, which is not the same question as
"were any defects found".

Exit status: 0 = gate passed, 1 = gate failed.
"""

from __future__ import annotations

import argparse
import glob
import os
import re
from typing import Any, Dict, List, Optional, Tuple

RESULTS_DIR = ".sanityops/results"
REPORT_GLOB = "inspect-*.md"

EXIT_PASS = 0
EXIT_FAIL = 1

_RE_TOTAL = re.compile(r"\|\s*Total defects\s*\|\s*(\d+)\s*\|")
_RE_SEVERITY = re.compile(
    r"\|\s*Severity\s*\|\s*P0:\s*(\d+)\s*/\s*P1:\s*(\d+)\s*/\s*P2:\s*(\d+)\s*\|"
)
_RE_GATE = re.compile(r"\|\s*Gate\s*\|\s*(\w+)\s*\|")


def find_latest_report(results_dir: str = RESULTS_DIR) -> Optional[str]:
    """Return the newest inspect report under results_dir, or None."""
    pattern = os.path.join(results_dir, REPORT_GLOB)
    try:
        matches = glob.glob(pattern, recursive=True, include_hidden=True)
    except TypeError:
        # include_hidden was added in Python 3.11.
        matches = glob.glob(pattern, recursive=True)
    reports = [path for path in matches if os.path.isfile(path)]
    if not reports:
        return None
    return max(reports, key=os.path.getmtime)


def parse_report(text: str) -> Dict[str, Any]:
    """Extract the summary metrics from a report's Summary table."""
    summary: Dict[str, Any] = {"total": 0, "p0": 0, "p1": 0, "p2": 0, "gate": "UNKNOWN"}

    match = _RE_TOTAL.search(text)
    if match:
        summary["total"] = int(match.group(1))

    match = _RE_SEVERITY.search(text)
    if match:
        summary["p0"] = int(match.group(1))
        summary["p1"] = int(match.group(2))
        summary["p2"] = int(match.group(3))

    match = _RE_GATE.search(text)
    if match:
        summary["gate"] = match.group(1).upper()

    return summary


def decide(
    *,
    cli_exit: int,
    skip_defect_check: bool,
    block_on_p0: bool,
    fail_on_defect: bool,
    summary: Optional[Dict[str, Any]],
) -> Tuple[int, str]:
    """Return (exit_code, message) for the configured gate. See spec section 8."""
    # Row 1: the run itself failed, so no verdict is trustworthy.
    if cli_exit not in (0, 1):
        return EXIT_FAIL, (
            f"The CLI failed with exit code {cli_exit}; the inspection did not complete, "
            "so no gate verdict is available. Check the job log for the failing step."
        )

    # Rows 2 and 3: no report was written.
    if summary is None:
        if skip_defect_check:
            return EXIT_PASS, (
                "No defect report was produced because skip-defect-check is enabled "
                "(analysis-only run). The gate was not evaluated."
            )
        return EXIT_FAIL, (
            f"No inspection report found under {RESULTS_DIR}/, so no gate verdict is "
            "available. This is a backstop: a run whose artifact list is empty, missing, "
            "unreadable, or syntactically invalid normally fails earlier with a CLI config "
            "error, and --skip-defect-check is the only way to complete a run without a "
            "report. Set skip-defect-check to true for an analysis-only run."
        )

    p0 = int(summary.get("p0", 0))
    total = int(summary.get("total", 0))

    # Rows 4 and 5: block-on-p0 takes precedence over fail-on-defect.
    if block_on_p0:
        if p0 > 0:
            return EXIT_FAIL, f"{p0} P0 (blocker) defect(s) found and block-on-p0 is enabled."
        return EXIT_PASS, (
            f"No P0 defects found ({total} lower-severity defect(s)); "
            "the block-on-p0 gate passed."
        )

    # Row 6: fail on any defect.
    if fail_on_defect and total > 0:
        return EXIT_FAIL, f"{total} defect(s) found and fail-on-defect is enabled."

    # Row 7: soft gate, the default.
    if total > 0:
        return EXIT_PASS, (
            f"{total} defect(s) found, but the gate is soft (block-on-p0 and "
            "fail-on-defect are both false). See the PR/MR comment and job summary."
        )
    return EXIT_PASS, "All inspected artifacts passed."


def _as_bool(value: Any) -> bool:
    return str(value).strip().lower() == "true"


def main(argv: Optional[List[str]] = None) -> int:
    """CLI entry point. Flags default to environment variables for the GitLab template."""
    parser = argparse.ArgumentParser(description="Evaluate the SanityOps quality gate.")
    parser.add_argument(
        "--exit-code",
        type=int,
        default=int(os.environ.get("SANITYOPS_GATE_CLI_EXIT", "0")),
        help="Raw sanityops-cli exit code.",
    )
    parser.add_argument(
        "--skip-defect-check",
        default=os.environ.get("SANITYOPS_ACTION_SKIP_DEFECT_CHECK", "false"),
    )
    parser.add_argument(
        "--block-on-p0",
        default=os.environ.get("SANITYOPS_ACTION_BLOCK_ON_P0", "false"),
    )
    parser.add_argument(
        "--fail-on-defect",
        default=os.environ.get("SANITYOPS_ACTION_FAIL_ON_DEFECT", "false"),
    )
    parser.add_argument(
        "--results-dir",
        default=os.environ.get("SANITYOPS_ACTION_RESULTS_DIR", RESULTS_DIR),
    )
    args = parser.parse_args(argv)

    report_path = find_latest_report(args.results_dir)
    summary: Optional[Dict[str, Any]] = None
    if report_path:
        try:
            with open(report_path, "r", encoding="utf-8") as fh:
                summary = parse_report(fh.read())
        except OSError as exc:
            print(f"[SanityOps Gate] Could not read {report_path}: {exc}")

    exit_code, message = decide(
        cli_exit=args.exit_code,
        skip_defect_check=_as_bool(args.skip_defect_check),
        block_on_p0=_as_bool(args.block_on_p0),
        fail_on_defect=_as_bool(args.fail_on_defect),
        summary=summary,
    )

    if summary is not None:
        print(
            f"[SanityOps Gate] Report: {report_path} | "
            f"total={summary['total']} P0={summary['p0']} "
            f"P1={summary['p1']} P2={summary['p2']} report-gate={summary['gate']}"
        )
    print(f"[SanityOps Gate] {message}")
    print(f"[SanityOps Gate] Verdict: {'PASS' if exit_code == EXIT_PASS else 'FAIL'}")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
