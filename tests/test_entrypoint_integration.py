"""End-to-end tests for entrypoint.sh, using a stubbed sanityops-cli.

These exercise the real shell script: argument construction, the log copy, the
gate invocation, and the exported outputs. A stub CLI stands in for the real
binary so no network or LLM key is needed.

The stub mimics typer's option parsing closely enough to catch a malformed
invocation: `--version` / `--help` are global flags that accept no further
options, and the inspect-only options are rejected when the subcommand is not
`inspect`. A stub that ignored its arguments would let a broken command line
pass unnoticed.

The gate flags are passed to the gate script explicitly. If that wiring ever
breaks, a hard gate stops firing and every pipeline goes green, so it is tested
behaviourally rather than by grepping the script.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest

from tests._helpers import REPO_ROOT, write_stub_cli

ENTRYPOINT = os.path.join(REPO_ROOT, "entrypoint.sh")

REPORT_TEMPLATE = """# Inspect Report

- **Generated**: 2026-01-01 12:00:00
- **Check level**: L2
- **Status**: completed
- **Execution time**: 1.00s

## Summary

| Metric | Value |
|---|---|
| Total defects | {total} |
| Severity | P0: {p0} / P1: 0 / P2: 0 |
| Gate | {gate} |
"""

#: action.yml supplies these two inputs with defaults, so the real runner always
#: has them set. Omitting them would hide exactly the bug this suite guards.
ACTION_DEFAULT_INPUTS = {
    "INPUT_CONFIG_PATH": ".sanityops/inspect_config.yaml",
    "INPUT_CHECK_LEVEL": "L2",
}


class EntrypointHarness:
    """Runs entrypoint.sh in a throwaway workspace with a stub CLI on PATH."""

    def __init__(self, report: str = "", cli_exit: int = 0, extra_files: dict | None = None) -> None:
        self.report = report
        self.cli_exit = cli_exit
        self.extra_files = extra_files or {}

    def __enter__(self) -> "EntrypointHarness":
        self.tmp = tempfile.mkdtemp()
        self.home = os.path.join(self.tmp, "home")
        self.bin = os.path.join(self.tmp, "bin")
        os.makedirs(self.home)
        os.makedirs(self.bin)

        write_stub_cli(self.bin)

        os.makedirs(os.path.join(self.tmp, ".sanityops"))
        with open(os.path.join(self.tmp, ".sanityops", "inspect_config.yaml"), "w") as fh:
            fh.write("project:\n  id: test\n")

        for rel_path, content in self.extra_files.items():
            target = os.path.join(self.tmp, rel_path)
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with open(target, "w", encoding="utf-8") as fh:
                fh.write(content)
        return self

    def __exit__(self, *exc: object) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run(self, **inputs: str) -> tuple[int, dict, str]:
        env = {
            "PATH": f"{self.bin}:{os.environ.get('PATH', '')}",
            # Isolate git config and the ~/.sanityops log copy inside the temp dir.
            "HOME": self.home,
            "FAKE_CLI_EXIT": str(self.cli_exit),
            "GITHUB_OUTPUT": os.path.join(self.tmp, "github_output"),
        }
        if self.report:
            env["FAKE_REPORT"] = self.report
        env.update(ACTION_DEFAULT_INPUTS)
        for key, value in inputs.items():
            env[key.upper().replace("-", "_")] = value

        result = subprocess.run(
            ["bash", ENTRYPOINT],
            cwd=self.tmp,
            env=env,
            capture_output=True,
            text=True,
        )

        outputs = {}
        try:
            with open(env["GITHUB_OUTPUT"], encoding="utf-8") as fh:
                for line in fh:
                    if "=" in line:
                        key, _, value = line.partition("=")
                        outputs[key.strip()] = value.strip()
        except OSError:
            pass
        return result.returncode, outputs, result.stdout + result.stderr


def report(total: int, p0: int) -> str:
    return REPORT_TEMPLATE.format(total=total, p0=p0, gate="FAIL" if p0 else "PASS")


def running_line(log: str) -> str:
    for line in log.splitlines():
        if line.startswith("Running: "):
            return line
    raise AssertionError(f"no 'Running:' line in output:\n{log}")


class TestGateWiring(unittest.TestCase):
    def test_soft_gate_passes_with_defects(self) -> None:
        with EntrypointHarness(report=report(7, 2)) as h:
            code, outputs, _ = h.run()
        self.assertEqual(code, 0)
        self.assertEqual(outputs.get("gate-result"), "PASS")
        self.assertEqual(outputs.get("total-defects"), "7")

    def test_fail_on_defect_blocks(self) -> None:
        with EntrypointHarness(report=report(7, 2)) as h:
            code, outputs, _ = h.run(input_fail_on_defect="true")
        self.assertEqual(code, 1, "fail-on-defect input did not reach the gate")
        self.assertEqual(outputs.get("gate-result"), "FAIL")

    def test_block_on_p0_blocks_on_p0(self) -> None:
        with EntrypointHarness(report=report(3, 1)) as h:
            code, _, _ = h.run(input_block_on_p0="true")
        self.assertEqual(code, 1, "block-on-p0 input did not reach the gate")

    def test_block_on_p0_allows_non_p0_defects(self) -> None:
        with EntrypointHarness(report=report(4, 0)) as h:
            code, outputs, _ = h.run(input_block_on_p0="true")
        self.assertEqual(code, 0)
        self.assertEqual(outputs.get("gate-result"), "PASS")

    def test_cli_failure_blocks_even_under_soft_gate(self) -> None:
        with EntrypointHarness(report=report(0, 0), cli_exit=2) as h:
            code, outputs, _ = h.run()
        self.assertEqual(code, 1, "a failed CLI run must not pass")
        self.assertEqual(outputs.get("exit-code"), "1")

    def test_no_report_with_skip_defect_check_passes(self) -> None:
        with EntrypointHarness(report="") as h:
            code, _, _ = h.run(input_skip_defect_check="true")
        self.assertEqual(code, 0)

    def test_no_report_without_skip_fails(self) -> None:
        with EntrypointHarness(report="") as h:
            code, _, _ = h.run()
        self.assertEqual(code, 1, "inspecting nothing must not pass")

    def test_skips_the_pr_comment_outside_a_pull_request(self) -> None:
        with EntrypointHarness(report=report(0, 0)) as h:
            _, _, log = h.run()
        self.assertIn("Not running in a pull request context", log)


class TestPassthroughCommands(unittest.TestCase):
    """A non-inspect command must receive no inspect-only options.

    `action.yml` gives config-path and check-level defaults, so those INPUT_*
    variables are always set. Appending them to every command produced
    `sanityops-cli --version --config ... --check-level L2`, which the CLI rejects
    with "No such option: --config" and exit 2.
    """

    def test_version_command_receives_no_inspect_options(self) -> None:
        with EntrypointHarness() as h:
            _, _, log = h.run(input_command="--version")
        self.assertEqual(running_line(log), "Running: sanityops-cli --version")

    def test_help_command_receives_no_inspect_options(self) -> None:
        with EntrypointHarness() as h:
            _, _, log = h.run(input_command="--help")
        self.assertEqual(running_line(log), "Running: sanityops-cli --help")

    def test_version_command_succeeds(self) -> None:
        with EntrypointHarness() as h:
            code, outputs, _ = h.run(input_command="--version")
        self.assertEqual(code, 0, "a --version smoke test must not fail")
        self.assertEqual(outputs.get("gate-result"), "PASS")

    def test_inspect_command_still_receives_its_options(self) -> None:
        with EntrypointHarness(report=report(0, 0)) as h:
            _, _, log = h.run(input_config_path=".sanityops/inspect_config.yaml")
        line = running_line(log)
        self.assertIn("--config .sanityops/inspect_config.yaml", line)
        self.assertIn("--check-level L2", line)


class TestReportDiscovery(unittest.TestCase):
    """The report is looked up where the CLI writes it, not anywhere in the repo."""

    def test_unrelated_inspect_file_is_not_reported(self) -> None:
        # A fixture named inspect-*.md somewhere in the repo must not be mistaken
        # for this run's report.
        with EntrypointHarness(report="", extra_files={"tests/fixtures/inspect-19700101-000000-000000.md": report(7, 2)}) as h:
            code, outputs, _ = h.run()
        self.assertEqual(outputs.get("report-path"), "")
        self.assertEqual(outputs.get("total-defects"), "0")
        self.assertEqual(code, 1, "no report was produced, so the gate must fail")

    def test_report_is_found_under_a_custom_config_directory(self) -> None:
        # The CLI writes to <config-dir>/results/, so a custom config path moves it.
        # The gate is enabled with no defects present: it passes only if it actually
        # read the report, because a missing report fails the gate by design.
        with EntrypointHarness() as h:
            os.makedirs(os.path.join(h.tmp, "config", "sanityops"), exist_ok=True)
            with open(os.path.join(h.tmp, "config", "sanityops", "inspect_config.yaml"), "w") as fh:
                fh.write("project:\n  id: test\n")
            code, outputs, log = h.run(
                input_config_path="config/sanityops/inspect_config.yaml",
                input_fail_on_defect="true",
                FAKE_REPORT=report(0, 0),
                FAKE_RESULTS_DIR="config/sanityops/results",
            )
        self.assertTrue(outputs.get("report-path", "").endswith("inspect-20260101-000000-000000.md"))
        self.assertEqual(outputs.get("total-defects"), "0")
        self.assertEqual(code, 0, "the gate did not find the report in the derived directory")
        self.assertIn("config/sanityops/results", log)


class TestArgumentConstruction(unittest.TestCase):
    def test_builds_the_expected_cli_invocation(self) -> None:
        with EntrypointHarness(report=report(0, 0)) as h:
            _, _, log = h.run(input_check_level="L3", input_verbose="true")
        line = running_line(log)
        self.assertIn("sanityops-cli inspect", line)
        self.assertIn("--check-level L3", line)
        self.assertIn("--config .sanityops/inspect_config.yaml", line)
        self.assertIn("--verbose", line)

    def test_skip_defect_check_is_forwarded_to_the_cli(self) -> None:
        with EntrypointHarness(report="") as h:
            _, _, log = h.run(input_skip_defect_check="true")
        self.assertIn("--skip-defect-check", running_line(log))


SECRET_SENTINEL = "sk-sentinel-must-not-leak-9f8e7d6c"

PARTIAL_MODEL_CONFIG = """project:
  id: test
model:
  provider: openai
  model_id: model-from-file
"""

PLACEHOLDER_CONFIG = """project:
  id: test
model:
  provider: openai
  api_key: ENV_PLACEHOLDER
  model_id: model-from-file
"""


class TestNoSecretLeak(unittest.TestCase):
    """The action must never write the LLM secret into the workspace.

    An earlier version rewrote inspect_config.yaml to inject the key, which put a
    live credential into a git-tracked file on disk.
    """

    def _config_path(self, harness: EntrypointHarness) -> str:
        return os.path.join(harness.tmp, ".sanityops", "inspect_config.yaml")

    def test_config_file_is_byte_identical_after_a_run(self) -> None:
        with EntrypointHarness(
            report=report(0, 0), extra_files={".sanityops/inspect_config.yaml": PARTIAL_MODEL_CONFIG}
        ) as h:
            before = open(self._config_path(h), "rb").read()
            h.run(input_api_key=SECRET_SENTINEL)
            after = open(self._config_path(h), "rb").read()
        self.assertEqual(after, before, "the run modified the committed config file")

    def test_no_file_in_the_workspace_contains_the_secret(self) -> None:
        with EntrypointHarness(
            report=report(0, 0), extra_files={".sanityops/inspect_config.yaml": PARTIAL_MODEL_CONFIG}
        ) as h:
            h.run(input_api_key=SECRET_SENTINEL)
            offenders = []
            for root, _dirs, files in os.walk(h.tmp):
                for name in files:
                    path = os.path.join(root, name)
                    try:
                        with open(path, "rb") as fh:
                            if SECRET_SENTINEL.encode() in fh.read():
                                offenders.append(os.path.relpath(path, h.tmp))
                    except OSError:
                        continue
        self.assertEqual(offenders, [], f"the secret was written to: {offenders}")


class TestPlaceholderConfig(unittest.TestCase):
    def test_placeholder_key_fails_before_the_cli_runs(self) -> None:
        with EntrypointHarness(
            report=report(0, 0), extra_files={".sanityops/inspect_config.yaml": PLACEHOLDER_CONFIG}
        ) as h:
            code, _, log = h.run(input_api_key=SECRET_SENTINEL)
        self.assertEqual(code, 1, "a placeholder key must fail the step")
        self.assertIn("placeholder", log.lower())
        self.assertNotIn("Running: ", log, "the CLI ran despite a placeholder key")

    def test_partial_config_still_runs_and_warns(self) -> None:
        with EntrypointHarness(
            report=report(0, 0), extra_files={".sanityops/inspect_config.yaml": PARTIAL_MODEL_CONFIG}
        ) as h:
            code, _, log = h.run(input_api_key=SECRET_SENTINEL)
        self.assertEqual(code, 0)
        self.assertIn("is incomplete", log)
        self.assertIn("Running: ", log)

    def test_absent_model_section_is_the_quiet_path(self) -> None:
        with EntrypointHarness(report=report(0, 0)) as h:
            code, _, log = h.run(input_api_key=SECRET_SENTINEL)
        self.assertEqual(code, 0)
        self.assertIn("recommended setup", log)


if __name__ == "__main__":
    unittest.main()
