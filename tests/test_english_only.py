"""Guard the English-only requirement (spec section 10).

Any CJK character in a tracked file fails this test.
"""

from __future__ import annotations

import os
import re
import subprocess
import unittest
from typing import List

from tests._helpers import CJK_PATTERN, REPO_ROOT


# Binary-ish files that are never prose.
SKIP_SUFFIXES = (".png", ".jpg", ".jpeg", ".gif", ".ico", ".pdf", ".zip")


def tracked_files() -> List[str]:
    """Return repo-relative paths of every file git tracks."""
    result = subprocess.run(
        ["git", "ls-files"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    return [line for line in result.stdout.splitlines() if line]


class TestEnglishOnly(unittest.TestCase):
    def test_no_cjk_in_tracked_files(self) -> None:
        offenders = []
        for rel in tracked_files():
            if rel.endswith(SKIP_SUFFIXES):
                continue
            abs_path = os.path.join(REPO_ROOT, rel)
            try:
                with open(abs_path, "r", encoding="utf-8") as fh:
                    text = fh.read()
            except (UnicodeDecodeError, OSError):
                continue
            for lineno, line in enumerate(text.splitlines(), start=1):
                if CJK_PATTERN.search(line):
                    offenders.append(f"{rel}:{lineno}: {line.strip()[:80]}")
        self.assertEqual(
            offenders,
            [],
            "CJK characters found in tracked files:\n" + "\n".join(offenders),
        )


if __name__ == "__main__":
    unittest.main()
