#!/usr/bin/env bash
#
# SanityOps Defect Inspection - GitHub Actions Docker entrypoint.
#
# Resolves credentials from the inputs and secrets, checks which LLM settings are
# actually in effect, runs sanityops-cli, evaluates the quality gate, and exports
# the action outputs. The project's inspect_config.yaml is used as written and is
# never rewritten.
set -e

# git refuses to operate on the mounted workspace without this.
git config --global --add safe.directory /github/workspace 2>/dev/null || true
git config --global --add safe.directory "*" 2>/dev/null || true

# Pre-create the output directories so artifact upload works even when the run
# produces nothing.
mkdir -p .sanityops/results .sanityops/logs .sanityops/repairs
touch .sanityops/results/.gitkeep .sanityops/logs/.gitkeep

# ---------------------------------------------------------------- credentials
# Resolve the LLM API key from the action input, then from conventional secrets.
if [ -n "$INPUT_API_KEY" ]; then
    export LLM_API_KEY="$INPUT_API_KEY"
elif [ -z "$LLM_API_KEY" ]; then
    export LLM_API_KEY="${OPENAI_API_KEY:-${ANTHROPIC_API_KEY:-}}"
fi

if [ -n "$INPUT_PROVIDER" ]; then
    export LLM_PROVIDER="$INPUT_PROVIDER"
fi
if [ -n "$INPUT_MODEL_ID" ]; then
    export LLM_MODEL_ID="$INPUT_MODEL_ID"
fi
if [ -n "$INPUT_BASE_URL" ]; then
    export LLM_BASE_URL="$INPUT_BASE_URL"
fi

export SANITYOPS_ACTION_CONFIG_PATH="${INPUT_CONFIG_PATH:-.sanityops/inspect_config.yaml}"

# The CLI writes its report to <config-dir>/results/, so the report location
# follows the config path. Deriving it beats searching the repo for inspect-*.md,
# which also matches unrelated files such as checked-in fixtures.
CONFIG_DIR="$(dirname "$SANITYOPS_ACTION_CONFIG_PATH")"
if [ "$CONFIG_DIR" = "." ] || [ -z "$CONFIG_DIR" ]; then
    SANITYOPS_ACTION_RESULTS_DIR="results"
else
    SANITYOPS_ACTION_RESULTS_DIR="${CONFIG_DIR}/results"
fi
export SANITYOPS_ACTION_RESULTS_DIR
mkdir -p "$SANITYOPS_ACTION_RESULTS_DIR"

# The LLM settings reach the CLI through the environment. `sanityops-cli` prefers the
# config file's `model:` section when it is complete, so the config is used as written
# and never rewritten: injecting the secret into it would put a live key into a
# git-tracked file in the workspace.

# Dashboard upload is opt-in. Without a key, nothing is uploaded anywhere.
if [ -n "$INPUT_SERVER_API_KEY" ]; then
    export SANITYOPS_API_KEY="$INPUT_SERVER_API_KEY"
fi
if [ -n "$INPUT_SERVER_BASE_URL" ]; then
    export SANITYOPS_BASE_URL="$INPUT_SERVER_BASE_URL"
fi

# ------------------------------------------------------------------- command
# Only an `inspect` run takes the inspect options and produces a report. Every
# passthrough command (`--version`, `--help`) and every other subcommand
# (`init`, `config`) must be invoked bare: appending --config/--check-level to
# them makes the CLI reject the call with "No such option" and exit 2. The same
# predicate decides whether the quality gate applies, since only `inspect` writes
# a report to gate on.
IS_INSPECT_RUN=false

if [ "$#" -gt 0 ]; then
    # Explicit arguments win. Accept both "sanityops-cli inspect" and bare CLI args.
    if [ "$1" = "sanityops-cli" ]; then
        shift
        CMD=("sanityops-cli" "$@")
    elif [ "$1" = "inspect" ] || [ "$1" = "init" ] || [ "$1" = "config" ] || [[ "$1" == -* ]]; then
        CMD=("sanityops-cli" "$@")
    else
        CMD=("$@")
    fi

    if [ "${CMD[1]:-}" = "inspect" ] && [ "${CMD[2]:-}" != "repair" ]; then
        IS_INSPECT_RUN=true
    fi
else
    SUBCOMMAND="${INPUT_COMMAND:-inspect}"
    CMD=("sanityops-cli" "$SUBCOMMAND")

    if [ "$SUBCOMMAND" = "inspect" ]; then
        IS_INSPECT_RUN=true
    fi

    # These options exist only on `inspect`.
    if [ "$IS_INSPECT_RUN" = "true" ]; then
        if [ -n "$INPUT_CONFIG_PATH" ]; then
            CMD+=("--config" "$INPUT_CONFIG_PATH")
        fi
        if [ -n "$INPUT_CHECK_LEVEL" ]; then
            CMD+=("--check-level" "$INPUT_CHECK_LEVEL")
        fi
        if [ "$INPUT_VERBOSE" = "true" ]; then
            CMD+=("--verbose")
        fi
        if [ "$INPUT_SKIP_DEFECT_CHECK" = "true" ]; then
            CMD+=("--skip-defect-check")
        fi
        if [ -n "$INPUT_TIMEOUT" ]; then
            CMD+=("--timeout" "$INPUT_TIMEOUT")
        fi
        if [ -n "$INPUT_TOKEN_BUDGET" ]; then
            CMD+=("--token-budget" "$INPUT_TOKEN_BUDGET")
        fi
    fi
fi


# Report which LLM settings are actually in effect. A config file carrying a complete
# `model:` section wins over the environment, so a leftover placeholder key there would
# be sent to the LLM; the check fails loudly rather than letting that happen.
if [ "$IS_INSPECT_RUN" = "true" ]; then
    CHECK_SCRIPT="/check_llm_config.py"
    if [ ! -f "$CHECK_SCRIPT" ]; then
        CHECK_SCRIPT="$(dirname "$0")/scripts/check_llm_config.py"
    fi
    python3 "$CHECK_SCRIPT" --config "$SANITYOPS_ACTION_CONFIG_PATH"
fi

echo "Running: ${CMD[*]}"

# Run the CLI without aborting on a non-zero exit; the gate decides the outcome.
set +e
"${CMD[@]}"
CLI_EXIT=$?
set -e
echo "[SanityOps Action] CLI exit code: $CLI_EXIT"

# The CLI logs to ~/.sanityops/logs, outside the workspace. Copy the logs in so
# the action's log-path output and the artifacts can find them.
mkdir -p .sanityops/logs
cp -R "${HOME}/.sanityops/logs/." .sanityops/logs/ 2>/dev/null || true

# ---------------------------------------------------------------------- gate
GATE_SCRIPT="/evaluate_gate.py"
if [ ! -f "$GATE_SCRIPT" ]; then
    GATE_SCRIPT="$(dirname "$0")/scripts/evaluate_gate.py"
fi

# The gate flags are passed explicitly. The action's inputs are named INPUT_*,
# which the gate does not read, so relying on the environment here would leave
# every hard gate silently disabled.
if [ "$IS_INSPECT_RUN" = "true" ]; then
    set +e
    python3 "$GATE_SCRIPT" \
        --exit-code "$CLI_EXIT" \
        --results-dir "$SANITYOPS_ACTION_RESULTS_DIR" \
        --skip-defect-check "${INPUT_SKIP_DEFECT_CHECK:-false}" \
        --block-on-p0 "${INPUT_BLOCK_ON_P0:-false}" \
        --fail-on-defect "${INPUT_FAIL_ON_DEFECT:-false}"
    GATE_EXIT=$?
    set -e
else
    echo "[SanityOps Gate] This command does not produce an inspection report; the quality gate does not apply."
    GATE_EXIT=$CLI_EXIT
fi

# ------------------------------------------------------- outputs and comment
EXPORT_SCRIPT="/export_action_outputs.py"
if [ ! -f "$EXPORT_SCRIPT" ]; then
    EXPORT_SCRIPT="$(dirname "$0")/scripts/export_action_outputs.py"
fi
python3 "$EXPORT_SCRIPT" "$CLI_EXIT" "$GATE_EXIT" || true

PR_COMMENT_SCRIPT="/post_pr_comment.py"
if [ ! -f "$PR_COMMENT_SCRIPT" ]; then
    PR_COMMENT_SCRIPT="$(dirname "$0")/scripts/post_pr_comment.py"
fi
python3 "$PR_COMMENT_SCRIPT" || true

exit $GATE_EXIT
