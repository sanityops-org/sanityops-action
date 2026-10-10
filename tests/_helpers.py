"""Shared helpers for the SanityOps Action test suite."""

from __future__ import annotations

import importlib.util
import os
import re
import sys
import textwrap
import types

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS_DIR = os.path.join(REPO_ROOT, "scripts")
FIXTURES_DIR = os.path.join(REPO_ROOT, "tests", "fixtures")

#: Matches CJK ideographs, CJK punctuation, and fullwidth forms.
#: Written with escape sequences so this file stays pure ASCII.
CJK_PATTERN = re.compile(r"[\u3000-\u303f\u4e00-\u9fff\uff00-\uffef]")


def load_script(name: str) -> types.ModuleType:
    """Import scripts/<name>.py as a module, without it being a package member."""
    path = os.path.join(SCRIPTS_DIR, f"{name}.py")
    if not os.path.isfile(path):
        raise FileNotFoundError(f"No such script: {path}")
    spec = importlib.util.spec_from_file_location(f"_script_{name}", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def fixture_path(name: str) -> str:
    """Absolute path to a file under tests/fixtures/."""
    return os.path.join(FIXTURES_DIR, name)


#: A stand-in for `sanityops-cli` that mimics typer's option parsing closely enough
#: to catch a malformed invocation: `--version` / `--help` are global flags that
#: accept no further options, and the inspect-only options are rejected when the
#: subcommand is not `inspect`. A stub that ignored its arguments would let a
#: broken command line pass unnoticed.
#:
#: FAKE_REPORT writes a report, FAKE_RESULTS_DIR relocates it, FAKE_CLI_EXIT sets
#: the exit code.
STUB_CLI = textwrap.dedent(
    """\
    #!/usr/bin/env bash
    INSPECT_ONLY="--config --check-level --skip-defect-check --timeout --token-budget --verbose"
    SUBCOMMAND="${1:-}"

    case "$SUBCOMMAND" in
        --version|-V|--help|-h)
            if [ "$#" -gt 1 ]; then
                echo "Usage: sanityops-cli [OPTIONS] COMMAND [ARGS]..."
                echo "Error: No such option: $2" >&2
                exit 2
            fi
            echo "sanityops-cli v0.1.7"
            exit 0
            ;;
    esac

    if [ "$SUBCOMMAND" != "inspect" ]; then
        for arg in "$@"; do
            case " $INSPECT_ONLY " in
                *" $arg "*) echo "Error: No such option: $arg" >&2; exit 2 ;;
            esac
        done
    fi

    if [ -n "${FAKE_REPORT:-}" ]; then
        results="${FAKE_RESULTS_DIR:-.sanityops/results}"
        mkdir -p "$results"
        printf '%s' "$FAKE_REPORT" > "$results/inspect-20260101-000000-000000.md"
    fi
    exit "${FAKE_CLI_EXIT:-0}"
    """
)


def write_stub_cli(bin_dir: str) -> str:
    """Write the stub CLI into bin_dir and return its path."""
    os.makedirs(bin_dir, exist_ok=True)
    path = os.path.join(bin_dir, "sanityops-cli")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(STUB_CLI)
    os.chmod(path, 0o755)
    return path
