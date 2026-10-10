#!/usr/bin/env python3
"""Regenerate the base64-embedded payloads in templates/inspect.gitlab-ci.yml.

GitLab's `include:` can only pull in YAML, never scripts, and the template job runs
inside the *consuming* project's checkout, so it cannot reference files from this
repository. The payload scripts are therefore embedded verbatim. This generator keeps
those embeds in sync with their canonical sources, and `--check` makes staleness a CI
failure rather than a silent drift.

Usage:
  python3 scripts/render_gitlab_template.py           # rewrite the template in place
  python3 scripts/render_gitlab_template.py --check   # exit 1 if the template is stale
"""

from __future__ import annotations

import argparse
import base64
import os
import re
import sys
from typing import Dict, List, Optional, Tuple

TEMPLATE_PATH = "templates/inspect.gitlab-ci.yml"

EMBEDS: Dict[str, str] = {
    "evaluate_gate": "scripts/evaluate_gate.py",
    "check_llm_config": "scripts/check_llm_config.py",
    "post_mr_comment": "scripts/post_mr_comment.py",
}


def _markers(name: str) -> Tuple["re.Pattern[str]", "re.Pattern[str]"]:
    begin = re.compile(
        rf"^(?P<indent>[ \t]*)# --- BEGIN EMBED: {re.escape(name)} ---[ \t]*$", re.M
    )
    end = re.compile(rf"^[ \t]*# --- END EMBED: {re.escape(name)} ---[ \t]*$", re.M)
    return begin, end


def render(template_text: str, sources: Dict[str, str]) -> str:
    """Return template_text with every marked region replaced by its base64 payload."""
    result = template_text

    for name in EMBEDS:
        begin, end = _markers(name)

        begin_match = begin.search(result)
        if begin_match is None:
            raise ValueError(f"Missing BEGIN marker for embed '{name}' in {TEMPLATE_PATH}")
        end_match = end.search(result, begin_match.end())
        if end_match is None:
            raise ValueError(f"Missing END marker for embed '{name}' in {TEMPLATE_PATH}")

        if name not in sources:
            raise ValueError(f"No source provided for embed '{name}'")

        encoded = base64.b64encode(sources[name].encode("utf-8")).decode("ascii")
        indent = begin_match.group("indent")
        variable = f"SANITYOPS_EMBED_{name.upper()}"
        # The payload is exported, not merely assigned: the decoding step reads it
        # through os.environ, which cannot see a plain shell variable. The trailing
        # newline matters so the END marker starts on its own line.
        body = f'{indent}export {variable}="{encoded}"\n'

        result = result[: begin_match.end()] + "\n" + body + result[end_match.start() :]

    return result


def decode_embed(template_text: str, name: str) -> str:
    """Return the source text currently embedded under `name`."""
    pattern = re.compile(
        rf'^[ \t]*(?:export[ \t]+)?SANITYOPS_EMBED_{re.escape(name.upper())}='
        r'"([A-Za-z0-9+/=]*)"[ 	]*$',
        re.M,
    )
    match = pattern.search(template_text)
    if match is None:
        raise ValueError(f"No embedded payload named '{name}' found in {TEMPLATE_PATH}")
    return base64.b64decode(match.group(1)).decode("utf-8")


def _repo_root() -> str:
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load_sources() -> Dict[str, str]:
    sources = {}
    for name, rel_path in EMBEDS.items():
        with open(os.path.join(_repo_root(), rel_path), encoding="utf-8") as fh:
            sources[name] = fh.read()
    return sources


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Render embedded payloads into the GitLab template."
    )
    parser.add_argument("--check", action="store_true", help="Exit 1 if the template is stale.")
    args = parser.parse_args(argv)

    template_abs = os.path.join(_repo_root(), TEMPLATE_PATH)
    with open(template_abs, encoding="utf-8") as fh:
        current = fh.read()

    rendered = render(current, _load_sources())

    if args.check:
        if rendered != current:
            print(
                f"{TEMPLATE_PATH} is stale. Run: python3 scripts/render_gitlab_template.py",
                file=sys.stderr,
            )
            return 1
        print(f"{TEMPLATE_PATH} is up to date.")
        return 0

    if rendered == current:
        print(f"{TEMPLATE_PATH} is already up to date.")
        return 0

    with open(template_abs, "w", encoding="utf-8") as fh:
        fh.write(rendered)
    print(f"Updated {TEMPLATE_PATH}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
