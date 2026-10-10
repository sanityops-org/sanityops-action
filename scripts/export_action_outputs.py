#!/usr/bin/env python3
"""Export structured action outputs and a step summary for GitHub Actions.

Invoked as: export_action_outputs.py <raw-cli-exit> <gate-exit>

The raw CLI exit code is reported for diagnostics. `exit-code` and `gate-result`
reflect the *gate* verdict, which is what a consumer branches on.
"""

from __future__ import annotations

import glob
import os
import re
import sys
from typing import Dict, List, Optional

DEFAULT_RESULTS_DIR = ".sanityops/results"

#: Conventional locations, checked after the configured directory.
FALLBACK_RESULTS_DIRS = [".sanityops/results", "results"]

#: Bounded on purpose. The CLI writes its report to <config-dir>/results/, so the
#: location is known once the config path is. A repo-wide `**/inspect-*.md` search
#: would also match unrelated files - a checked-in fixture or sample named
#: inspect-*.md was reported as this run's result, with its fabricated counts.
LOG_PATTERNS = [".sanityops/logs/*.log", "logs/*.log"]


def results_dir() -> str:
    """The directory holding this run's report."""
    return os.environ.get("SANITYOPS_ACTION_RESULTS_DIR") or DEFAULT_RESULTS_DIR


def report_patterns(configured: Optional[str] = None) -> List[str]:
    """Report globs for the configured results directory, then the conventional ones."""
    patterns: List[str] = []
    for candidate in [configured or results_dir()] + FALLBACK_RESULTS_DIRS:
        directory = (candidate or "").rstrip("/")
        if not directory or directory == ".":
            directory = "results"
        pattern = f"{directory}/inspect-*.md"
        if pattern not in patterns:
            patterns.append(pattern)
    return patterns


def find_files(patterns: List[str]) -> List[str]:
    """Find files matching the given globs. The patterns name their directories
    explicitly, so no recursive or hidden-directory search is needed."""
    matched: List[str] = []
    for pattern in patterns:
        matched.extend(glob.glob(pattern))
    seen = set()
    unique = []
    for path in matched:
        if path not in seen:
            seen.add(path)
            unique.append(path)
    return unique


def find_latest(patterns: List[str]) -> str:
    """Newest existing file matching any pattern, or an empty string."""
    files = [path for path in find_files(patterns) if os.path.isfile(path)]
    if not files:
        return ""
    return max(files, key=os.path.getmtime)


def collect_outputs(
    raw_cli_exit: int, gate_exit: int, configured_results: Optional[str] = None
) -> Dict[str, str]:
    """Build the action output map from the workspace and the gate verdict."""
    outputs = {
        "exit-code": str(gate_exit),
        "total-defects": "0",
        "p0-defects": "0",
        "p1-defects": "0",
        "p2-defects": "0",
        "gate-result": "PASS" if gate_exit == 0 else "FAIL",
        "report-path": find_latest(report_patterns(configured_results)),
        "log-path": find_latest(LOG_PATTERNS),
    }

    if not outputs["report-path"]:
        return outputs

    try:
        with open(outputs["report-path"], "r", encoding="utf-8") as fh:
            content = fh.read()
    except OSError as exc:
        print(f"Warning: failed to read report {outputs['report-path']}: {exc}", file=sys.stderr)
        return outputs

    match = re.search(r"\|\s*Total defects\s*\|\s*(\d+)\s*\|", content)
    if match:
        outputs["total-defects"] = match.group(1)

    match = re.search(
        r"\|\s*Severity\s*\|\s*P0:\s*(\d+)\s*/\s*P1:\s*(\d+)\s*/\s*P2:\s*(\d+)\s*\|",
        content,
    )
    if match:
        outputs["p0-defects"] = match.group(1)
        outputs["p1-defects"] = match.group(2)
        outputs["p2-defects"] = match.group(3)

    return outputs


def build_summary(raw_cli_exit: int, gate_exit: int, outputs: Dict[str, str]) -> str:
    """Render the job step summary."""
    verdict = "PASS" if gate_exit == 0 else "FAIL"
    lines = [
        "### SanityOps Inspection",
        "",
        "| Metric | Value |",
        "|---|---|",
        f"| Verdict | **{verdict}** |",
        f"| CLI exit code | `{raw_cli_exit}` |",
        f"| Total defects | {outputs['total-defects']} |",
        f"| Severity | P0: {outputs['p0-defects']} / P1: {outputs['p1-defects']}"
        f" / P2: {outputs['p2-defects']} |",
        f"| Report | `{outputs['report-path'] or 'not generated'}` |",
        f"| Log | `{outputs['log-path'] or 'not generated'}` |",
        "",
    ]

    if not outputs["report-path"]:
        return "\n".join(lines)

    try:
        with open(outputs["report-path"], "r", encoding="utf-8") as fh:
            lines.extend(["", fh.read(), ""])
    except OSError:
        pass
    return "\n".join(lines)


def _write_github_output(outputs: Dict[str, str]) -> None:
    path = os.environ.get("GITHUB_OUTPUT")
    if not path:
        return
    try:
        with open(path, "a", encoding="utf-8") as out:
            for key, value in outputs.items():
                out.write(f"{key}={value}\n")
    except OSError as exc:
        print(f"Warning: failed to write GITHUB_OUTPUT: {exc}", file=sys.stderr)


def _write_step_summary(summary: str) -> None:
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not path:
        return
    try:
        with open(path, "a", encoding="utf-8") as out:
            out.write(summary + "\n")
    except OSError as exc:
        print(f"Warning: failed to write GITHUB_STEP_SUMMARY: {exc}", file=sys.stderr)


def main(argv: Optional[List[str]] = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    raw_cli_exit = int(args[0]) if len(args) > 0 and args[0] else 0
    gate_exit = int(args[1]) if len(args) > 1 and args[1] else 0

    outputs = collect_outputs(raw_cli_exit, gate_exit)
    _write_github_output(outputs)
    _write_step_summary(build_summary(raw_cli_exit, gate_exit, outputs))

    for key, value in outputs.items():
        print(f"{key}={value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
