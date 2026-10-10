#!/usr/bin/env python3
"""Report which LLM settings actually take effect, before the CLI runs.

`sanityops-cli` uses the config file's `model:` section if and only if `provider`,
`api_key` and `model_id` are ALL present and non-empty. Otherwise it discards the
whole section and reads the `LLM_*` environment variables instead. The two sources
are mutually exclusive - nothing is merged - so:

  * a complete `model:` section silently wins over `LLM_API_KEY` and over the
    action's api-key / provider / model-id / base-url settings;
  * a partial `model:` section is discarded entirely, dropping the values it does
    contain.

Neither case announces itself, so this check states which source is in effect and
fails when the config still holds a placeholder key that would be sent to the LLM.

Exit status: 0 = nothing blocking (warnings permitted), 1 = a blocking problem.

Standard library only: the released CLI is a standalone binary that bundles its own
PyYAML, which this interpreter cannot import.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from typing import Any, Dict, List, Mapping, Optional, Tuple

EXIT_OK = 0
EXIT_FAIL = 1

DEFAULT_CONFIG_PATH = ".sanityops/inspect_config.yaml"

REQUIRED_KEYS = ("provider", "api_key", "model_id")

#: Fragments that mark a value as an unfilled placeholder rather than a real key.
PLACEHOLDER_HINTS = (
    "placeholder",
    "changeme",
    "change-me",
    "your-api-key",
    "your_api_key",
    "yourapikey",
    "replace-me",
    "replaceme",
    "example",
    "dummy",
    "fake",
    "todo",
    "xxx",
    "<",
)

_MODEL_LINE = re.compile(r"^model:\s*$")
_MODEL_INLINE = re.compile(r"^model:\s*\S")
_KEY_LINE = re.compile(r"^([A-Za-z_][\w-]*)\s*:\s*(.*)$")


def _strip_comment(line: str) -> str:
    """Drop a trailing comment, respecting quoted values."""
    quote: Optional[str] = None
    for index, char in enumerate(line):
        if quote:
            if char == quote:
                quote = None
        elif char in "\"'":
            quote = char
        elif char == "#" and (index == 0 or line[index - 1].isspace()):
            return line[:index]
    return line


def _unquote(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    return value


def parse_config(text: str) -> Dict[str, Any]:
    """Read the direct keys of the `model:` block.

    Returns {"__unparsed__": True} for inline (flow) style, which this reader does
    not attempt - guessing wrong here would mean reporting the wrong precedence.
    """
    model: Dict[str, Any] = {}
    in_model = False
    model_indent = 0

    for raw in text.splitlines():
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        line = _strip_comment(raw).rstrip()
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip())

        if not in_model:
            if _MODEL_LINE.match(line.strip()):
                in_model = True
                model_indent = indent
            elif _MODEL_INLINE.match(line.strip()):
                return {"__unparsed__": True}
            continue

        if indent <= model_indent:
            break

        match = _KEY_LINE.match(line.strip())
        if match:
            model[match.group(1)] = _unquote(match.group(2))

    return model


def classify(model: Mapping[str, Any]) -> Tuple[str, Dict[str, Any]]:
    """Classify the `model:` section as absent, partial, complete or unparsed."""
    if model.get("__unparsed__"):
        return "unparsed", {}
    if not model:
        return "absent", {}
    if all(model.get(key) for key in REQUIRED_KEYS):
        return "complete", dict(model)
    return "partial", dict(model)


def looks_like_placeholder(value: Any) -> bool:
    """True when a value is empty or an obviously unfilled placeholder."""
    text = _unquote(str(value if value is not None else "").strip())
    if not text:
        return True
    lowered = text.lower()
    return any(hint in lowered for hint in PLACEHOLDER_HINTS)


def _read(path: str) -> Optional[str]:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return fh.read()
    except (OSError, UnicodeDecodeError):
        return None


def check(config_path: str, env: Mapping[str, str]) -> Tuple[int, List[str]]:
    """Return (exit_code, messages) describing which LLM source is in effect."""
    del env  # the LLM_* variables are named in the messages; nothing to compare yet

    text = _read(config_path)
    if text is None:
        return EXIT_OK, [
            f"Note: {config_path} was not found or could not be read, so every LLM "
            "setting comes from the LLM_* environment variables."
        ]

    state, model = classify(parse_config(text))

    if state == "unparsed":
        return EXIT_OK, [
            f"Warning: the model: section in {config_path} uses inline (flow) style, "
            "which this check cannot read. Use block style (one key per line) so it is "
            "clear which settings come from the file and which from the environment."
        ]

    if state == "absent":
        return EXIT_OK, [
            f"No model: section in {config_path}: all LLM settings come from the "
            "LLM_* environment variables (LLM_API_KEY, LLM_PROVIDER, LLM_MODEL_ID, "
            "LLM_BASE_URL). This is the recommended setup."
        ]

    if state == "complete":
        messages = [
            f"{config_path} has a complete model: section, so the file wins over the "
            "environment. The api-key, provider, model-id and base-url settings are "
            f"ignored, and the model is {model.get('model_id')!r} from the file, not "
            "from LLM_API_KEY."
        ]
        if looks_like_placeholder(model.get("api_key")):
            messages.append(
                f"Error: the api_key in {config_path} still looks like a placeholder "
                f"({model.get('api_key')!r}). This action no longer rewrites the config "
                "file, so that value would be sent to the LLM as-is. Remove the api_key "
                "line and let LLM_API_KEY from the secret supply it, or replace it with "
                "your real key."
            )
            return EXIT_FAIL, messages
        return EXIT_OK, messages

    missing = [key for key in REQUIRED_KEYS if not model.get(key)]
    present = ", ".join(sorted(key for key in model if key != "__unparsed__"))
    return EXIT_OK, [
        f"Warning: the model: section in {config_path} is incomplete - missing "
        f"{', '.join(missing)}. The whole section is ignored unless provider, "
        "api_key and model_id are all set, so the values it does contain "
        f"({present}) are discarded and the LLM_* environment variables are used "
        "instead. Supply the missing settings through the action inputs or the LLM_* "
        "variables, or remove the section entirely."
    ]


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Check which LLM settings take effect.")
    parser.add_argument(
        "--config",
        default=os.environ.get("SANITYOPS_ACTION_CONFIG_PATH", DEFAULT_CONFIG_PATH),
        help="Path to inspect_config.yaml.",
    )
    args = parser.parse_args(argv)

    exit_code, messages = check(args.config, os.environ)
    for message in messages:
        print(f"[SanityOps Config] {message}")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
