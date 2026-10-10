#!/usr/bin/env python3
"""SanityOps CI - post inspection report comments to GitHub pull requests.

This script:
1. Detects whether the current run is associated with a GitHub pull request.
2. Finds the latest inspection report in .sanityops/results/inspect-*.md.
3. Formats an executive summary banner (gate result, P0/P1/P2 defect counts).
4. Looks for an existing SanityOps comment on the pull request:
   - If one is found, updates it in place (keeps the discussion clean).
   - If not, posts a new comment.
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
    """Format the report into a structured pull request comment."""
    summary = parse_report_summary(report_content)
    gate_emoji = "✅" if summary["gate"] == "PASS" else "❌"
    gate_badge = f"{gate_emoji} **{summary['gate']}**"

    # GitHub comments are capped at 65536 characters.
    max_body_len = 60000
    display_content = report_content
    if len(display_content) > max_body_len:
        display_content = (
            display_content[:max_body_len]
            + "\n\n*(⚠️ The report exceeded the GitHub comment size limit and was "
            "truncated. Download the full report from the workflow artifacts.)*"
        )

    time_str = f" ({summary['time']})" if summary["time"] else ""

    comment = f"""{COMMENT_HEADER}

> 🤖 **SanityOps Defect Inspection** ran automated static defect inspection on the AI logic
> artifacts (Prompts / Skills / Tools) touched by this pull request.

### 📊 Inspection Summary
| Gate Result | Total Defects | P0 Blocker | P1 Major | P2 Minor | Duration |
| :---: | :---: | :---: | :---: | :---: | :---: |
| {gate_badge} | **{summary['total']}** | **{summary['p0']}** | **{summary['p1']}** | **{summary['p2']}** | {summary.get('time', 'N/A')} |

<details open>
<summary><b>📋 Full inspection report{time_str}</b></summary>

{display_content}

</details>

---
*💡 This comment is generated automatically by the SanityOps GitHub Action. It is refreshed
on every update to this pull request.*
"""
    return comment


def get_pr_number() -> Optional[int]:
    """Determine the current pull request number from the GitHub event context."""
    event_path = os.environ.get("GITHUB_EVENT_PATH")
    if event_path and os.path.isfile(event_path):
        try:
            with open(event_path, "r", encoding="utf-8") as f:
                event_data = json.load(f)
            pr = (
                event_data.get("pull_request", {}).get("number")
                or event_data.get("number")
                or event_data.get("issue", {}).get("number")
            )
            if pr:
                return int(pr)
        except Exception:
            pass

    ref = os.environ.get("GITHUB_REF", "")
    match = re.match(r"^refs/pull/(\d+)/", ref)
    if match:
        return int(match.group(1))

    return None


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


def get_existing_comment_id(
    api_base: str, repo: str, pr_num: int, headers: Dict[str, str]
) -> Optional[int]:
    """Check whether a SanityOps comment already exists, to avoid duplicate spam."""
    url = f"{api_base}/repos/{repo}/issues/{pr_num}/comments?per_page=100"
    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            comments = json.loads(resp.read().decode("utf-8"))
            for comment in comments:
                if COMMENT_HEADER in comment.get("body", ""):
                    return comment.get("id")
    except Exception as e:
        print(f"ℹ️ Could not query existing PR comments: {e}")
        return None


def post_or_update_comment(
    api_base: str, repo: str, pr_num: int, body: str, token: str
) -> bool:
    """Create or update a pull request comment."""
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "sanityops-action",
        "Content-Type": "application/json",
    }

    existing_id = get_existing_comment_id(api_base, repo, pr_num, headers)
    data = json.dumps({"body": body}).encode("utf-8")

    if existing_id:
        url = f"{api_base}/repos/{repo}/issues/comments/{existing_id}"
        req = urllib.request.Request(url, data=data, headers=headers, method="PATCH")
        action = f"updated existing comment #{existing_id}"
    else:
        url = f"{api_base}/repos/{repo}/issues/{pr_num}/comments"
        req = urllib.request.Request(url, data=data, headers=headers, method="POST")
        action = "posted new comment"

    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            if resp.status in (200, 201):
                print(f"✅ [SanityOps PR Comment] Successfully {action} on PR #{pr_num}!")
                return True
    except urllib.error.HTTPError as e:
        print(f"⚠️ [SanityOps PR Comment] GitHub API error {e.code}: {e.reason}")
        if e.code == 403:
            print("   Tip: ensure the workflow grants 'pull-requests: write' or 'issues: write'.")
        return False
    except Exception as e:
        print(f"⚠️ [SanityOps PR Comment] Network error: {e}")
        return False

    return False


def main() -> None:
    pr_num = get_pr_number()
    if not pr_num:
        print("ℹ️ [SanityOps PR Comment] Not running in a pull request context. Skipping.")
        sys.exit(0)

    repo = os.environ.get("GITHUB_REPOSITORY")
    if not repo:
        print("ℹ️ [SanityOps PR Comment] GITHUB_REPOSITORY is not set. Skipping.")
        sys.exit(0)

    token = os.environ.get("INPUT_GITHUB_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if not token:
        print("⚠️ [SanityOps PR Comment] No GitHub token available. Skipping the PR comment.")
        print("   Tip: pass 'github-token: ${{ secrets.GITHUB_TOKEN }}' to the action step.")
        sys.exit(0)

    api_base = os.environ.get("GITHUB_API_URL", "https://api.github.com").rstrip("/")

    latest_report = find_latest_report()
    if not latest_report:
        print("ℹ️ [SanityOps PR Comment] No inspection report found in .sanityops/results/. Skipping.")
        sys.exit(0)

    with open(latest_report, "r", encoding="utf-8") as f:
        report_content = f.read()

    comment_body = build_comment_body(report_content)
    post_or_update_comment(api_base, repo, pr_num, comment_body, token)


if __name__ == "__main__":
    main()
