"""Behavioural tests for the GitLab template's job script.

The template's shell is extracted from the YAML and executed against the stub CLI,
so the GitLab path is covered by the same kind of test as the GitHub entrypoint.
Without this, a defect fixed in entrypoint.sh can silently survive in the template.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest

from tests._helpers import REPO_ROOT, write_stub_cli

TEMPLATE_PATH = os.path.join(REPO_ROOT, "templates", "inspect.gitlab-ci.yml")
JOB_NAME = "sanityops-inspect"

try:
    import yaml  # type: ignore
except ImportError:  # pragma: no cover - exercised in images without PyYAML
    yaml = None


def job_script() -> str:
    """The job's `script:` block, joined into one shell program."""
    if yaml is None:
        raise unittest.SkipTest("PyYAML is not installed")
    with open(TEMPLATE_PATH, encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    entries = data[JOB_NAME]["script"]
    return "\n".join(str(entry) for entry in entries)


def run_job(command: str, results_dir: str = "") -> tuple[int, str]:
    """Execute the template's script block in a throwaway workspace."""
    tmp = tempfile.mkdtemp()
    try:
        home = os.path.join(tmp, "home")
        os.makedirs(home)
        write_stub_cli(os.path.join(tmp, "bin"))
        os.makedirs(os.path.join(tmp, ".sanityops"))
        with open(os.path.join(tmp, ".sanityops", "inspect_config.yaml"), "w") as fh:
            fh.write("project:\n  id: test\n")

        env = {
            "PATH": f"{os.path.join(tmp, 'bin')}:{os.environ.get('PATH', '')}",
            "HOME": home,
            "SANITYOPS_ACTION_COMMAND": command,
            "SANITYOPS_ACTION_CONFIG_PATH": ".sanityops/inspect_config.yaml",
            "SANITYOPS_ACTION_CHECK_LEVEL": "L2",
            "SANITYOPS_ACTION_VERBOSE": "false",
            "SANITYOPS_ACTION_SKIP_DEFECT_CHECK": "false",
            "SANITYOPS_ACTION_TIMEOUT": "",
            "SANITYOPS_ACTION_TOKEN_BUDGET": "",
        }
        if results_dir:
            env["FAKE_REPORT"] = "# Inspect Report\n"
            env["FAKE_RESULTS_DIR"] = results_dir

        result = subprocess.run(
            ["bash", "-c", job_script()],
            cwd=tmp,
            env=env,
            capture_output=True,
            text=True,
        )
        return result.returncode, result.stdout + result.stderr
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def running_line(log: str) -> str:
    for line in log.splitlines():
        if line.startswith("Running SanityOps inspection: "):
            return line
    raise AssertionError(f"no running line in output:\n{log}")


class TestTemplateCommandConstruction(unittest.TestCase):
    def test_inspect_run_receives_its_options(self) -> None:
        _, log = run_job("inspect")
        line = running_line(log)
        self.assertIn("sanityops-cli inspect", line)
        self.assertIn("--config .sanityops/inspect_config.yaml", line)
        self.assertIn("--check-level L2", line)

    def test_version_command_receives_no_inspect_options(self) -> None:
        code, log = run_job("--version")
        self.assertEqual(running_line(log), "Running SanityOps inspection: sanityops-cli --version")
        self.assertEqual(code, 0, "a --version smoke test must not fail")

    def test_help_command_receives_no_inspect_options(self) -> None:
        code, log = run_job("--help")
        self.assertEqual(running_line(log), "Running SanityOps inspection: sanityops-cli --help")
        self.assertEqual(code, 0)

    def test_version_run_reports_that_the_gate_does_not_apply(self) -> None:
        _, log = run_job("--version")
        self.assertIn("the quality gate does not apply", log)


if __name__ == "__main__":
    unittest.main()
