"""Tests for the LLM configuration check.

`sanityops-cli` uses the config file's `model:` section only when provider, api_key
and model_id are ALL present; otherwise the whole section is discarded and the LLM_*
environment variables are used. The two sources are mutually exclusive, so a partial
section silently drops the settings it does contain. These tests pin the three
states and the wording that tells the user which source wins.
"""

from __future__ import annotations

import os
import shutil
import tempfile
import textwrap
import unittest
from contextlib import contextmanager
from typing import Iterator

from tests._helpers import load_script

checker = load_script("check_llm_config")

ENV_FROM_ACTION = {
    "LLM_API_KEY": "sk-real-from-secret",
    "LLM_PROVIDER": "openai",
    "LLM_MODEL_ID": "model-from-env",
    "LLM_BASE_URL": "https://env.example/v1",
}

ENV_EMPTY: dict = {}

COMPLETE = """\
project:
  id: test
model:
  provider: openai
  api_key: "sk-real-committed"
  model_id: model-from-file
"""

COMPLETE_PLACEHOLDER = """\
project:
  id: test
model:
  provider: openai
  api_key: ENV_PLACEHOLDER
  model_id: model-from-file
"""

PARTIAL = """\
project:
  id: test
model:
  provider: openai
  model_id: model-from-file
"""

ABSENT = """\
project:
  id: test
prompts:
  - file: prompts/system.md
"""


@contextmanager
def config(text: str) -> Iterator[str]:
    tmp = tempfile.mkdtemp()
    try:
        path = os.path.join(tmp, "inspect_config.yaml")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(textwrap.dedent(text))
        yield path
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def run(text: str, env: dict | None = None) -> tuple[int, str]:
    with config(text) as path:
        code, messages = checker.check(path, ENV_FROM_ACTION if env is None else env)
    return code, "\n".join(messages)


class TestClassify(unittest.TestCase):
    def test_absent(self) -> None:
        self.assertEqual(checker.classify(checker.parse_config(ABSENT))[0], "absent")

    def test_partial(self) -> None:
        state, model = checker.classify(checker.parse_config(PARTIAL))
        self.assertEqual(state, "partial")
        self.assertEqual(model.get("provider"), "openai")

    def test_complete(self) -> None:
        state, model = checker.classify(checker.parse_config(COMPLETE))
        self.assertEqual(state, "complete")
        self.assertEqual(model.get("api_key"), "sk-real-committed")

    def test_quoted_values_are_unquoted(self) -> None:
        _, model = checker.classify(checker.parse_config(COMPLETE))
        self.assertEqual(model.get("model_id"), "model-from-file")


class TestPlaceholderDetection(unittest.TestCase):
    def test_recognises_placeholders(self) -> None:
        for value in ("ENV_PLACEHOLDER", "", "  ", "changeme", "your-api-key", "TODO", '""'):
            self.assertTrue(checker.looks_like_placeholder(value), value)

    def test_accepts_real_looking_keys(self) -> None:
        for value in ("sk-ant-api03-abc123XYZ", "abc123def456ghi789", "sk-proj-9f8e7d6c"):
            self.assertFalse(checker.looks_like_placeholder(value), value)


class TestCheck(unittest.TestCase):
    def test_absent_section_names_the_environment_as_the_source(self) -> None:
        code, messages = run(ABSENT)
        self.assertEqual(code, checker.EXIT_OK)
        self.assertIn("LLM_API_KEY", messages)

    def test_partial_section_warns_the_section_is_discarded(self) -> None:
        code, messages = run(PARTIAL)
        self.assertEqual(code, checker.EXIT_OK, "a partial section must warn, not fail")
        self.assertIn("ignored", messages.lower())
        self.assertIn("model_id", messages)

    def test_complete_section_warns_the_inputs_are_ignored(self) -> None:
        code, messages = run(COMPLETE)
        self.assertEqual(code, checker.EXIT_OK)
        self.assertIn("model-from-file", messages)
        self.assertIn("ignored", messages.lower())

    def test_complete_section_says_the_secret_is_not_used(self) -> None:
        _, messages = run(COMPLETE)
        self.assertIn("LLM_API_KEY", messages)

    def test_placeholder_key_fails_with_a_fix(self) -> None:
        code, messages = run(COMPLETE_PLACEHOLDER)
        self.assertEqual(code, checker.EXIT_FAIL)
        self.assertIn("placeholder", messages.lower())
        # The fix must be spelled out: remove the api_key line.
        self.assertIn("api_key", messages)

    def test_placeholder_key_fails_even_without_environment_settings(self) -> None:
        code, _ = run(COMPLETE_PLACEHOLDER, env=ENV_EMPTY)
        self.assertEqual(code, checker.EXIT_FAIL)

    def test_missing_file_is_reported_without_failing(self) -> None:
        code, messages = checker.check("/nonexistent/inspect_config.yaml", ENV_FROM_ACTION)
        self.assertEqual(code, checker.EXIT_OK)
        self.assertIn("not found", "\n".join(messages).lower())

    def test_empty_value_is_treated_as_missing(self) -> None:
        # `api_key: ""` leaves the section incomplete, so the environment wins.
        text = COMPLETE.replace('api_key: "sk-real-committed"', 'api_key: ""')
        state, _ = checker.classify(checker.parse_config(text))
        self.assertEqual(state, "partial")


class TestMain(unittest.TestCase):
    def test_main_returns_the_check_exit_code(self) -> None:
        with config(COMPLETE_PLACEHOLDER) as path:
            self.assertEqual(checker.main(["--config", path]), checker.EXIT_FAIL)

    def test_main_defaults_to_the_sanityops_config_path(self) -> None:
        # No --config and nothing at the default path: reported, but not fatal.
        self.assertEqual(checker.main([]), checker.EXIT_OK)

    def test_main_reads_the_environment(self) -> None:
        saved = {k: os.environ.get(k) for k in ENV_FROM_ACTION}
        os.environ.update(ENV_FROM_ACTION)
        try:
            with config(COMPLETE) as path:
                self.assertEqual(checker.main(["--config", path]), checker.EXIT_OK)
        finally:
            for key, value in saved.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value


if __name__ == "__main__":
    unittest.main()
