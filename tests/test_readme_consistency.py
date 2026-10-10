"""Keep the README honest about variable and input names."""

from __future__ import annotations

import os
import re
import unittest

from tests._helpers import CJK_PATTERN, REPO_ROOT

README_PATH = os.path.join(REPO_ROOT, "README.md")
TEMPLATE_PATH = os.path.join(REPO_ROOT, "templates", "inspect.gitlab-ci.yml")

#: Inputs the README must mention by name.
DOCUMENTED_INPUTS = (
    "api-key", "config-path", "check-level", "block-on-p0", "fail-on-defect",
    "server-api-key", "server-base-url", "command", "skip-defect-check", "github-token",
)


def read(path: str) -> str:
    with open(path, encoding="utf-8") as fh:
        return fh.read()


class TestReadmeConsistency(unittest.TestCase):
    def test_readme_exists_and_is_english(self) -> None:
        text = read(README_PATH)
        self.assertGreater(len(text), 2000)
        self.assertIsNone(CJK_PATTERN.search(text))

    def test_documented_gitlab_variables_exist_in_the_template(self) -> None:
        documented = set(re.findall(r"SANITYOPS_ACTION_[A-Z0-9_]+", read(README_PATH)))
        self.assertGreater(len(documented), 10)
        template = read(TEMPLATE_PATH)
        missing = sorted(name for name in documented if name not in template)
        self.assertEqual(
            missing,
            [],
            f"README documents variables the template does not define: {missing}",
        )

    def test_documents_the_action_inputs(self) -> None:
        readme = read(README_PATH)
        action = read(os.path.join(REPO_ROOT, "action.yml"))
        for name in DOCUMENTED_INPUTS:
            self.assertIn(name, action)
            self.assertIn(name, readme, f"README never mentions the '{name}' input")

    def test_documents_the_private_namespace_rule(self) -> None:
        text = read(README_PATH)
        # The README must warn that SANITYOPS_API_KEY is the dashboard key, not the LLM key.
        self.assertIn("SANITYOPS_ACTION_", text)
        self.assertIn("SANITYOPS_API_KEY", text)
        self.assertIn("dashboard", text.lower())

    def test_documents_the_llm_endpoint_variable(self) -> None:
        # The LLM endpoint needs the SANITYOPS_ACTION_ prefix because an unprefixed
        # name sits one word from the CLI's dashboard SANITYOPS_BASE_URL.
        text = read(README_PATH)
        self.assertIn("SANITYOPS_ACTION_LLM_BASE_URL", text)
        self.assertIn("SANITYOPS_BASE_URL", text)

    def test_documents_the_config_gitignore_step(self) -> None:
        self.assertIn("!inspect_config.yaml", read(README_PATH))

    def test_documents_the_data_handling_rule(self) -> None:
        text = read(README_PATH).lower()
        self.assertIn("data handling", text)
        self.assertIn("nothing is uploaded", text)

    def test_documents_the_gate_modes(self) -> None:
        text = read(README_PATH)
        for mode in ("Soft gate", "Block on P0", "Fail on any defect"):
            self.assertIn(mode, text)

    def test_documents_the_skip_defect_check_escape_hatch(self) -> None:
        self.assertIn("skip-defect-check", read(README_PATH))

    def test_documents_the_llm_precedence_rule(self) -> None:
        text = read(README_PATH)
        # The all-or-nothing rule is the single most surprising thing about the config.
        self.assertIn("all present", text)
        self.assertIn("never write an `api_key` line", text)

    def test_documents_the_placeholder_failure(self) -> None:
        text = read(README_PATH)
        self.assertIn("looks like a placeholder", text)
        self.assertIn("ENV_PLACEHOLDER", text)

    def test_documents_both_platforms(self) -> None:
        text = read(README_PATH)
        self.assertIn("GitHub Actions", text)
        self.assertIn("GitLab CI", text)


if __name__ == "__main__":
    unittest.main()
