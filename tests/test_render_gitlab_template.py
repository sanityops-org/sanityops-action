"""Tests for the GitLab template embed generator."""

from __future__ import annotations

import base64
import os
import unittest
from typing import Iterable

from tests._helpers import CJK_PATTERN, REPO_ROOT, load_script

renderer = load_script("render_gitlab_template")

#: Derived from the registry so a newly registered embed cannot be forgotten here.
SOURCES = {name: f"print({index!r})\n" for index, name in enumerate(renderer.EMBEDS)}


def make_template(
    interior: str = "",
    embeds: Iterable[str] = tuple(renderer.EMBEDS),
) -> str:
    """A minimal template containing the requested embed regions."""
    lines = ["variables:\n", "  A: b\n"]
    for name in embeds:
        lines.append(f"      # --- BEGIN EMBED: {name} ---\n")
        lines.append(f"      {interior}\n")
        lines.append(f"      # --- END EMBED: {name} ---\n")
    lines.append("more:\n")
    return "".join(lines)


def encoded(text: str) -> str:
    return base64.b64encode(text.encode("utf-8")).decode("ascii")


def read_template() -> str:
    with open(os.path.join(REPO_ROOT, renderer.TEMPLATE_PATH), encoding="utf-8") as fh:
        return fh.read()


class TestRender(unittest.TestCase):
    def test_replaces_marker_interiors_with_base64(self) -> None:
        rendered = renderer.render(make_template(interior="STALE"), SOURCES)
        self.assertNotIn("STALE", rendered)
        self.assertIn(f'SANITYOPS_EMBED_EVALUATE_GATE="{encoded(SOURCES["evaluate_gate"])}"', rendered)
        self.assertIn(f'SANITYOPS_EMBED_POST_MR_COMMENT="{encoded(SOURCES["post_mr_comment"])}"', rendered)

    def test_end_marker_starts_on_its_own_line(self) -> None:
        # Regression guard: the payload line must not run into the END marker.
        rendered = renderer.render(make_template(), SOURCES)
        for line in rendered.splitlines():
            self.assertFalse("SANITYOPS_EMBED_" in line and "END EMBED" in line, line[:80])

    def test_preserves_content_outside_markers(self) -> None:
        rendered = renderer.render(make_template(), SOURCES)
        self.assertIn("variables:\n  A: b\n", rendered)
        self.assertTrue(rendered.endswith("more:\n"))

    def test_render_is_idempotent(self) -> None:
        once = renderer.render(make_template(), SOURCES)
        self.assertEqual(renderer.render(once, SOURCES), once)

    def test_missing_expected_marker_raises(self) -> None:
        # A deleted marker region must be an error, not a silent no-op.
        with self.assertRaises(ValueError):
            renderer.render(make_template(embeds=("evaluate_gate",)), SOURCES)

    def test_empty_placeholder_is_not_decodable(self) -> None:
        with self.assertRaises(ValueError):
            renderer.decode_embed(make_template(interior=""), "evaluate_gate")


class TestTemplateFile(unittest.TestCase):
    def test_committed_template_is_fresh(self) -> None:
        """The checked-in template must match its current sources."""
        sources = {
            name: open(os.path.join(REPO_ROOT, path), encoding="utf-8").read()
            for name, path in renderer.EMBEDS.items()
        }
        self.assertEqual(renderer.render(read_template(), sources), read_template())

    def test_embedded_gate_compiles(self) -> None:
        compile(renderer.decode_embed(read_template(), "evaluate_gate"), "evaluate_gate.py", "exec")

    def test_embedded_mr_comment_compiles(self) -> None:
        compile(
            renderer.decode_embed(read_template(), "post_mr_comment"),
            "post_mr_comment.py",
            "exec",
        )

    def test_embedded_payloads_match_their_source_files(self) -> None:
        for name, path in renderer.EMBEDS.items():
            with self.subTest(embed=name):
                with open(os.path.join(REPO_ROOT, path), encoding="utf-8") as fh:
                    self.assertEqual(renderer.decode_embed(read_template(), name), fh.read())

    def test_no_cjk_in_template(self) -> None:
        self.assertIsNone(CJK_PATTERN.search(read_template()))

    def test_uses_the_action_variable_namespace(self) -> None:
        text = read_template()
        self.assertIn("SANITYOPS_ACTION_CHECK_LEVEL", text)
        # The LLM endpoint must not be called SANITYOPS_BASE_URL or
        # SANITYOPS_ACTION_BASE_URL: the CLI reserves the former for the dashboard.
        self.assertNotIn("SANITYOPS_ACTION_BASE_URL", text)
        self.assertIn("SANITYOPS_ACTION_LLM_BASE_URL", text)


class TestCheckFlag(unittest.TestCase):
    def test_check_passes_on_fresh_template(self) -> None:
        self.assertEqual(renderer.main(["--check"]), 0)


if __name__ == "__main__":
    unittest.main()
