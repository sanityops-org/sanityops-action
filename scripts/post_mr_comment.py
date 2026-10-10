#!/usr/bin/env python3
"""SanityOps CI - post inspection report comments to GitLab merge requests.

This script:
1. Detects whether the current pipeline is running for a merge request.
2. Finds the latest inspection report in .sanityops/results/inspect-*.md.
3. Formats an executive summary banner (gate result, P0/P1/P2 defect counts).
4. Looks for an existing SanityOps note on the merge request:
   - If one is found, updates it in place (keeps the discussion clean).
   - If not, posts a new note.
5. Handles token authentication and permission errors without failing the build.
"""

from __future__ import annotations

import glob
import json
import os
import re
import sys
import urllib.error
import urllib.request
from typing import Dict, List, Optional

COMMENT_HEADER = "## 🔍 SanityOps Defect Inspection Report"

DEFAULT_RESULTS_DIR = ".sanityops/results"

#: Conventional locations, checked after the configured directory.
FALLBACK_RESULTS_DIRS = [".sanityops/results", "results"]


def results_dir() -> str:
    """The directory holding this run's report."""
    return os.environ.get("SANITYOPS_ACTION_RESULTS_DIR") or DEFAULT_RESULTS_DIR


def report_patterns() -> List[str]:
    """Report globs for the configured results directory, then the conventional ones.

    Bounded on purpose: the CLI writes to <config-dir>/results/, so the location is
    known from the config path. A repo-wide search would also match unrelated files,
    such as a checked-in fixture named inspect-*.md.
    """
    patterns: List[str] = []
    for candidate in [results_dir()] + FALLBACK_RESULTS_DIRS:
        directory = (candidate or "").rstrip("/")
        if not directory or directory == ".":
            directory = "results"
        pattern = f"{directory}/inspect-*.md"
        if pattern not in patterns:
            patterns.append(pattern)
    return patterns


def parse_report_summary(markdown_text: str) -> Dict[str, str]:
    """Extract metrics from the Markdown report summary table."""
    summary = {
        "gate": "UNKNOWN",
        "total": "0",
        "p0": "0",
        "p1": "0",
        "p2": "0",
        "time": "",
    }

    time_match = re.search(r"\*\*Execution time\*\*:\s*([0-9.]+s)", markdown_text, re.IGNORECASE)
    if time_match:
        summary["time"] = time_match.group(1)

    gate_match = re.search(r"\|\s*Gate\s*\|\s*([A-Z]+)\s*\|", markdown_text, re.IGNORECASE)
    if gate_match:
        summary["gate"] = gate_match.group(1).upper()

    total_match = re.search(r"\|\s*Total defects\s*\|\s*(\d+)\s*\|", markdown_text, re.IGNORECASE)
    if total_match:
        summary["total"] = total_match.group(1)

    sev_match = re.search(
        r"\|\s*Severity\s*\|\s*P0:\s*(\d+)\s*/\s*P1:\s*(\d+)\s*/\s*P2:\s*(\d+)\s*\|",
        markdown_text,
        re.IGNORECASE,
    )
    if sev_match:
        summary["p0"] = sev_match.group(1)
        summary["p1"] = sev_match.group(2)
        summary["p2"] = sev_match.group(3)

    return summary


def build_comment_body(report_content: str) -> str:
    """Format the report into a structured merge request comment."""
    summary = parse_report_summary(report_content)
    gate_emoji = "✅" if summary["gate"] == "PASS" else "❌"
    gate_badge = f"{gate_emoji} **{summary['gate']}**"

    # GitLab notes allow more characters than GitHub comments.
    max_body_len = 80000
    display_content = report_content
    if len(display_content) > max_body_len:
        display_content = (
            display_content[:max_body_len]
            + "\n\n*(⚠️ The report exceeded the size limit and was truncated. "
            "Download the full report from the pipeline artifacts.)*"
        )

    time_str = f" ({summary['time']})" if summary["time"] else ""

    comment = f"""{COMMENT_HEADER}

> 🤖 **SanityOps Defect Inspection** ran automated static defect inspection on the AI logic
> artifacts (Prompts / Skills / Tools) touched by this merge request.

### 📊 Inspection Summary
| Gate Result | Total Defects | P0 Blocker | P1 Major | P2 Minor | Duration |
| :---: | :---: | :---: | :---: | :---: | :---: |
| {gate_badge} | **{summary['total']}** | **{summary['p0']}** | **{summary['p1']}** | **{summary['p2']}** | {summary.get('time', 'N/A')} |

<details open>
<summary><b>📋 Full inspection report{time_str}</b></summary>

{display_content}

</details>

---
*💡 This comment is generated automatically by the SanityOps GitLab CI pipeline. It is
refreshed on every update to this merge request.*
"""
    return comment


def find_latest_report() -> str:
    """Newest inspection report on disk, or an empty string."""
    report_files: List[str] = []
    for pattern in report_patterns():
        report_files.extend(glob.glob(pattern))

    report_files = [path for path in report_files if os.path.isfile(path)]
    if not report_files:
        return ""
    report_files.sort(key=os.path.getmtime, reverse=True)
    return report_files[0]


def get_existing_note_id(
    api_base: str, project_id: str, mr_iid: str, headers: Dict[str, str]
) -> Optional[int]:
    """Check whether a SanityOps note already exists, to avoid duplicate spam."""
    url = f"{api_base}/projects/{project_id}/merge_requests/{mr_iid}/notes?per_page=100&sort=asc"
    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            notes = json.loads(resp.read().decode("utf-8"))
            for note in notes:
                body = note.get("body", "")
                if COMMENT_HEADER in body and not note.get("system", False):
                    return note.get("id")
    except Exception as e:
        print(f"ℹ️ Could not query existing MR notes: {e}")
    return None


def post_or_update_comment(
    api_base: str, project_id: str, mr_iid: str, body: str, token_type: str, token_val: str
) -> bool:
    """Create or update a merge request note."""
    headers = {
        "Content-Type": "application/json",
        token_type: token_val,
    }

    existing_id = get_existing_note_id(api_base, project_id, mr_iid, headers)
    data = json.dumps({"body": body}).encode("utf-8")

    if existing_id:
        url = f"{api_base}/projects/{project_id}/merge_requests/{mr_iid}/notes/{existing_id}"
        req = urllib.request.Request(url, data=data, headers=headers, method="PUT")
        action = f"updated existing note #{existing_id}"
    else:
        url = f"{api_base}/projects/{project_id}/merge_requests/{mr_iid}/notes"
        req = urllib.request.Request(url, data=data, headers=headers, method="POST")
        action = "posted new note"

    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            if resp.status in (200, 201):
                print(f"✅ [SanityOps MR Comment] Successfully {action} on MR !{mr_iid}!")
                return True
    except urllib.error.HTTPError as e:
        print(f"⚠️ [SanityOps MR Comment] GitLab API error {e.code}: {e.reason} using {token_type}")
        return False
    except Exception as e:
        print(f"⚠️ [SanityOps MR Comment] Network error: {e}")
        return False

    return False


def main() -> None:
    mr_iid = os.environ.get("CI_MERGE_REQUEST_IID")
    if not mr_iid:
        print("ℹ️ [SanityOps MR Comment] Not a merge request pipeline. Skipping.")
        sys.exit(0)

    project_id = os.environ.get("CI_PROJECT_ID")
    if not project_id:
        print("ℹ️ [SanityOps MR Comment] CI_PROJECT_ID is not set. Skipping.")
        sys.exit(0)

    api_base = os.environ.get("CI_API_V4_URL", "").rstrip("/")
    if not api_base:
        print("ℹ️ [SanityOps MR Comment] CI_API_V4_URL is not set. Skipping.")
        sys.exit(0)

    latest_report = find_latest_report()
    if not latest_report:
        print("ℹ️ [SanityOps MR Comment] No inspection report found in .sanityops/results/. Skipping.")
        sys.exit(0)

    with open(latest_report, "r", encoding="utf-8") as f:
        report_content = f.read()

    comment_body = build_comment_body(report_content)

    # Collect the available credentials in priority order.
    tokens_to_try = []
    if os.environ.get("GITLAB_TOKEN"):
        tokens_to_try.append(("PRIVATE-TOKEN", os.environ["GITLAB_TOKEN"]))
    if os.environ.get("CODEUP_ACCESS_TOKEN"):
        tokens_to_try.append(("PRIVATE-TOKEN", os.environ["CODEUP_ACCESS_TOKEN"]))
    if os.environ.get("CI_JOB_TOKEN"):
        tokens_to_try.append(("JOB-TOKEN", os.environ["CI_JOB_TOKEN"]))

    if not tokens_to_try:
        print("⚠️ [SanityOps MR Comment] Neither GITLAB_TOKEN nor CI_JOB_TOKEN is available.")
        print("   Tip: set GITLAB_TOKEN in Settings -> CI/CD -> Variables to enable MR comments.")
        sys.exit(0)

    success = False
    for token_type, token_val in tokens_to_try:
        if post_or_update_comment(api_base, project_id, mr_iid, comment_body, token_type, token_val):
            success = True
            break

    if not success:
        print("⚠️ [SanityOps MR Comment] Unable to post the MR comment: insufficient permissions.")
        print("   Tip: ensure GITLAB_TOKEN has the 'api' scope.")


if __name__ == "__main__":
    main()
