"""Structural validation of every YAML file in the repository (spec section 11.3).

A full parse runs when PyYAML is importable. The structural assertions below run
unconditionally so the suite stays dependency-free.
"""

from __future__ import annotations

import os
import re
import unittest

from tests._helpers import REPO_ROOT, load_script

YAML_FILES = [
    "action.yml",
    ".gitlab-ci.yml",
    "templates/inspect.gitlab-ci.yml",
    ".github/workflows/release.yml",
    ".github/workflows/test.yml",
    ".github/workflows/inspect.yml",
]

TEMPLATE = "templates/inspect.gitlab-ci.yml"

#: Taken from the renderer's registry so a newly registered embed is covered here.
EMBED_NAMES = tuple(load_script("render_gitlab_template").EMBEDS)

try:
    import yaml  # type: ignore
except ImportError:  # pragma: no cover - exercised in images without PyYAML
    yaml = None


def read(rel_path: str) -> str:
    with open(os.path.join(REPO_ROOT, rel_path), encoding="utf-8") as fh:
        return fh.read()


class TestYamlFiles(unittest.TestCase):
    def test_every_declared_file_exists(self) -> None:
        for rel_path in YAML_FILES:
            self.assertTrue(os.path.isfile(os.path.join(REPO_ROOT, rel_path)), rel_path)

    def test_parses_with_pyyaml_when_available(self) -> None:
        if yaml is None:
            self.skipTest("PyYAML is not installed")
        for rel_path in YAML_FILES:
            with self.subTest(rel_path=rel_path):
                self.assertIsNotNone(yaml.safe_load(read(rel_path)))

    def test_no_tab_indentation(self) -> None:
        for rel_path in YAML_FILES:
            for lineno, line in enumerate(read(rel_path).splitlines(), start=1):
                self.assertIsNone(
                    re.match(r"^[ ]*\t", line),
                    f"{rel_path}:{lineno} uses a tab for indentation",
                )

    def test_embed_markers_are_balanced(self) -> None:
        text = read(TEMPLATE)
        for name in EMBED_NAMES:
            self.assertEqual(text.count(f"# --- BEGIN EMBED: {name} ---"), 1, name)
            self.assertEqual(text.count(f"# --- END EMBED: {name} ---"), 1, name)

    def test_every_embed_carries_a_payload(self) -> None:
        text = read(TEMPLATE)
        for name in EMBED_NAMES:
            self.assertRegex(
                text,
                rf'SANITYOPS_EMBED_{name.upper()}="[A-Za-z0-9+/=]+"',
                f"{name} has no base64 payload",
            )

    def test_workflow_files_declare_expected_triggers(self) -> None:
        self.assertIn("workflow_call:", read(".github/workflows/inspect.yml"))
        self.assertIn("v*.*.*", read(".github/workflows/release.yml"))
        self.assertIn("unittest discover", read(".github/workflows/test.yml"))


if __name__ == "__main__":
    unittest.main()
