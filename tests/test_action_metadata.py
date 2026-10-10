"""Verify action.yml declares the contract the entrypoint and docs expect."""

from __future__ import annotations

import os
import re
import unittest
from typing import Dict, Set

from tests._helpers import CJK_PATTERN, REPO_ROOT

ACTION_PATH = os.path.join(REPO_ROOT, "action.yml")
ENTRYPOINT_PATH = os.path.join(REPO_ROOT, "entrypoint.sh")

EXPECTED_INPUTS = {
    "api-key", "config-path", "check-level", "provider", "model-id", "base-url",
    "verbose", "skip-defect-check", "timeout", "token-budget", "github-token",
    "fail-on-defect", "block-on-p0", "command",
    "server-api-key", "server-base-url",
}

EXPECTED_OUTPUTS = {
    "exit-code", "total-defects", "p0-defects", "p1-defects", "p2-defects",
    "gate-result", "report-path", "log-path",
}


def read(path: str) -> str:
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def section_keys(text: str, section: str) -> Dict[str, str]:
    """Two-space indented keys directly inside a top-level section."""
    pattern = re.compile(rf"^{section}:\n((?:  [^\n]*\n|\n)*)", re.M)
    match = pattern.search(text)
    if not match:
        return {}
    return dict(re.findall(r"^  ([a-z][a-z0-9-]*):\s*(.*)$", match.group(1), re.M))


def input_env_names(text: str) -> Set[str]:
    """INPUT_* names wired up in the runs.env block."""
    env_match = re.search(r"^  env:\n((?:    [^\n]*\n|\n)*)", text, re.M)
    if not env_match:
        return set()
    return set(re.findall(r"^\s+(INPUT_[A-Z0-9_]+):", env_match.group(1), re.M))


def consumed_inputs() -> Set[str]:
    """INPUT_* variables the entrypoint or any shipped script actually reads.

    INPUT_GITHUB_TOKEN, for example, is consumed by post_pr_comment.py rather
    than by the entrypoint.
    """
    sources = [read(ENTRYPOINT_PATH)]
    scripts_dir = os.path.join(REPO_ROOT, "scripts")
    for name in sorted(os.listdir(scripts_dir)):
        if name.endswith(".py"):
            sources.append(read(os.path.join(scripts_dir, name)))
    return set(re.findall(r"\b(INPUT_[A-Z0-9_]+)\b", "\n".join(sources)))


class TestActionYml(unittest.TestCase):
    def test_declares_expected_inputs(self) -> None:
        self.assertEqual(set(section_keys(read(ACTION_PATH), "inputs")), EXPECTED_INPUTS)

    def test_declares_expected_outputs(self) -> None:
        self.assertEqual(set(section_keys(read(ACTION_PATH), "outputs")), EXPECTED_OUTPUTS)

    def test_every_input_has_a_description(self) -> None:
        text = read(ACTION_PATH)
        for name in EXPECTED_INPUTS:
            block = re.search(rf"^  {re.escape(name)}:\n((?:    [^\n]*\n)+)", text, re.M)
            self.assertIsNotNone(block, f"{name} has no body")
            self.assertIn("description:", block.group(1), f"{name} has no description")

    def test_check_level_defaults_to_l2(self) -> None:
        block = re.search(r"^  check-level:\n((?:    [^\n]*\n)+)", read(ACTION_PATH), re.M)
        self.assertIn("'L2'", block.group(1))

    def test_config_path_defaults_into_sanityops(self) -> None:
        block = re.search(r"^  config-path:\n((?:    [^\n]*\n)+)", read(ACTION_PATH), re.M)
        self.assertIn(".sanityops/inspect_config.yaml", block.group(1))

    def test_no_cjk_in_action(self) -> None:
        self.assertIsNone(CJK_PATTERN.search(read(ACTION_PATH)))

    def test_uses_docker(self) -> None:
        text = read(ACTION_PATH)
        self.assertIn("using: 'docker'", text)
        self.assertIn("image: 'Dockerfile'", text)


def reusable_workflow_action_inputs() -> Set[str]:
    """Inputs passed to the action step inside .github/workflows/inspect.yml.

    Only the `with:` block of the step that `uses:` this action counts.
    Workflow-only inputs such as artifact-retention-days are configured by the
    reusable workflow for its own steps and are not part of the action contract.
    """
    text = read(os.path.join(REPO_ROOT, ".github", "workflows", "inspect.yml"))
    step = re.search(
        r"uses:\s*[^\n]*sanityops-action@[^\n]*\n\s+with:\n((?:\s+[a-z][a-z0-9-]*:[^\n]*\n)+)",
        text,
    )
    if not step:
        return set()
    return set(re.findall(r"^\s+([a-z][a-z0-9-]*):", step.group(1), re.M))


class TestEntrypointWiring(unittest.TestCase):
    def test_every_input_consumed_is_wired_up(self) -> None:
        missing = sorted(consumed_inputs() - input_env_names(read(ACTION_PATH)))
        self.assertEqual(
            missing,
            [],
            f"the action reads {missing} but action.yml never sets them",
        )

    def test_reusable_workflow_passes_only_real_action_inputs(self) -> None:
        declared = set(section_keys(read(ACTION_PATH), "inputs"))
        passed = reusable_workflow_action_inputs()
        self.assertTrue(passed, "no inputs found in the reusable workflow's action step")
        self.assertEqual(sorted(passed - declared), [])

    def test_reusable_workflow_exposes_the_gate_controls(self) -> None:
        passed = reusable_workflow_action_inputs()
        self.assertIn("block-on-p0", passed)
        self.assertIn("fail-on-defect", passed)

    def test_every_declared_input_is_consumed(self) -> None:
        unused = sorted(input_env_names(read(ACTION_PATH)) - consumed_inputs())
        self.assertEqual(unused, [], f"action.yml declares inputs nothing reads: {unused}")


if __name__ == "__main__":
    unittest.main()
