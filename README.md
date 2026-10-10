# SanityOps Defect Inspection (CI/CD)

Automated static defect inspection for AI logic artifacts — **System Prompts, Skills, and
Tool Schemas** — natively supporting **GitHub Actions** and **GitLab CI**.

The action wraps [`sanityops-cli`](https://sanityops.org), which runs the artifact analysis
and LLM-powered defect check, writes a markdown report, and returns a quality gate verdict
that decides whether the pipeline passes.

- [Quick start: GitHub Actions](#quick-start-github-actions)
- [Quick start: GitLab CI](#quick-start-gitlab-ci)
- [Configuring the artifact list](#configuring-the-artifact-list)
- [Gate modes](#gate-modes)
- [Full parameter reference](#full-parameter-reference)
- [The SANITYOPS_ACTION_ namespace](#the-sanityops_action_-namespace)
- [Data handling](#data-handling)
- [Outputs](#outputs)
- [Reusable workflow](#reusable-workflow)
- [Troubleshooting](#troubleshooting)

---

## Quick start: GitHub Actions

Add `.github/workflows/sanityops.yml` to your repository:

```yaml
name: SanityOps Inspection

on:
  pull_request:
    branches: [ main, master ]
  push:
    branches: [ main, master ]

jobs:
  inspect:
    runs-on: ubuntu-latest
    permissions:
      contents: read
      pull-requests: write   # required to post the inspection comment on the PR

    steps:
      - uses: actions/checkout@v4
      - uses: sanityops-org/sanityops-action@v1
        with:
          api-key: ${{ secrets.LLM_API_KEY }}
          # Every input below already has a sensible default. Uncomment to override.
          #
          # --- Basic configuration ---
          # config-path: '.sanityops/inspect_config.yaml'
          # check-level: 'L2'                 # L1 (fast), L2 (standard), L3 (deep)
          #
          # --- Gate control (see "Gate modes") ---
          # block-on-p0: 'true'               # Fail only when a P0 blocker is found
          # fail-on-defect: 'true'            # Fail when any defect is found
          #
          # --- LLM overrides (otherwise read from inspect_config.yaml) ---
          # provider: 'openai'
          # model-id: 'code-assistant'
          # base-url: 'https://your-llm-endpoint.example.com/v1'
          #
          # --- Run control ---
          # timeout: 1800                     # seconds (default 1800)
          # token-budget: 200000
          # skip-defect-check: 'true'         # Analysis only, no LLM defect check
          # verbose: 'true'
          # command: 'inspect'
```

Create the secret under **Settings → Secrets and variables → Actions**:

- `LLM_API_KEY` — your LLM provider API key.

---

## Quick start: GitLab CI

Add this to your project's `.gitlab-ci.yml`:

```yaml
include:
  - project: 'sanityops-org/sanityops-action'
    ref: 'v1'                                  # or a pinned tag, e.g. 'v1.0.0'
    file: '/templates/inspect.gitlab-ci.yml'

# Soft gate is the default: findings are reported without blocking the merge.
# Every variable below already has a default. Uncomment to override.
variables:
  # --- Basic configuration ---
  # SANITYOPS_ACTION_CHECK_LEVEL: "L2"          # L1 (fast), L2 (standard), L3 (deep)
  # SANITYOPS_ACTION_CONFIG_PATH: ".sanityops/inspect_config.yaml"

  # --- Gate control (see "Gate modes") ---
  # SANITYOPS_ACTION_BLOCK_ON_P0: "true"        # Fail only when a P0 blocker is found
  # SANITYOPS_ACTION_FAIL_ON_DEFECT: "true"     # Fail when any defect is found

  # --- LLM overrides (otherwise read from inspect_config.yaml) ---
  # SANITYOPS_ACTION_PROVIDER: "openai"
  # SANITYOPS_ACTION_MODEL_ID: "code-assistant"
  # SANITYOPS_ACTION_LLM_BASE_URL: "https://your-llm-endpoint.example.com/v1"

  # --- Run control ---
  # SANITYOPS_ACTION_TIMEOUT: "1800"
  # SANITYOPS_ACTION_TOKEN_BUDGET: "200000"
  # SANITYOPS_ACTION_SKIP_DEFECT_CHECK: "true"  # Analysis only, no LLM defect check
  # SANITYOPS_ACTION_VERBOSE: "true"
  # SANITYOPS_ACTION_COMMAND: "inspect"

  # --- Runner and CLI source ---
  # SANITYOPS_ACTION_RUNNER_TAG: "docker"
  # SANITYOPS_ACTION_INSTALL_URL: "https://downloads.sanityops.org/sanityops-cli"
  # SANITYOPS_ACTION_CLI_VERSION: "latest"      # pin this in production

  # --- Dashboard upload (see "Data handling"): off unless an API key is set ---
  # SANITYOPS_ACTION_SERVER_API_KEY: ""         # uploads artifacts off the runner
  # SANITYOPS_ACTION_SERVER_BASE_URL: ""        # default: https://demo.sanityops.org
```

Create these under **Settings → CI/CD → Variables**:

| Variable | Setting | Why |
|---|---|---|
| `LLM_API_KEY` | **Masked**, and **not** Protected | Protected variables are unavailable to merge-request pipelines from non-protected branches, which is where you want the check to run. |
| `GITLAB_TOKEN` | Masked, `api` scope | Optional. Enables the inspection report to be posted as a merge-request comment. Without it the job still runs; only the comment is skipped. |

---

## Configuring the artifact list

The set of artifacts to inspect lives in `.sanityops/inspect_config.yaml`. The action reads
it; it does not generate it.

Create it with the CLI:

```bash
sanityops-cli init
```

That writes `.sanityops/inspect_config.yaml` **and** a `.sanityops/.gitignore` containing
`*`, so the whole directory is ignored by git. A fresh CI checkout would therefore have no
config and the run would inspect nothing.

To version the artifact list while keeping the key out of git, add this line to
`.sanityops/.gitignore` so the config is committed:

```
!inspect_config.yaml
```

Then commit `.sanityops/inspect_config.yaml`. The API key comes from the `LLM_API_KEY`
secret through the environment, so **never write an `api_key` line in this file**:

```yaml
project:
  id: "your-project-id"
  name: "your-project-name"

# Optional. If you want the model settings versioned, keep them here — but omit
# api_key, which would take precedence and override the secret.
# model:
#   provider: openai
#   model_id: "code-assistant"
#   base_url: "https://your-llm-endpoint.example.com/v1"

prompts:
  - file: prompts/system_prompt.md

tools:
  - file: tools/search_tools.json

skills:
  - file: skills/code_review.md
```

### How the LLM settings are resolved

`sanityops-cli` reads the config file's `model:` section **only if `provider`, `api_key`
and `model_id` are all present**. If any one is missing, it discards the whole section and
uses the `LLM_*` environment variables instead. The two sources are never merged, so the
outcome is all-or-nothing:

| `model:` section in the config | What takes effect |
|---|---|
| Absent | `LLM_API_KEY` / `LLM_PROVIDER` / `LLM_MODEL_ID` / `LLM_BASE_URL` — the action's `api-key`, `provider`, `model-id` and `base-url` settings. **Recommended.** |
| Present but incomplete (for example `provider` and `model_id` with no `api_key`) | Still the environment. The section is discarded **along with the values it does contain**, so an incomplete section silently does nothing. |
| Complete (all three keys set) | The **file** wins. The key is whatever the file says, and the action's `api-key` / `provider` / `model-id` / `base-url` settings are all **ignored**. |

The action never rewrites your config file. It reports which source is in effect before each
run, and **fails the step** when the file holds a `model:` section whose `api_key` still looks
like a placeholder — that value would otherwise be sent to the LLM verbatim, producing a
confusing authentication error instead of a clear one.

> **Migrating from a placeholder key.** If your config carries `api_key: "ENV_PLACEHOLDER"`
> (or similar) from an earlier version of this action, delete the `api_key` line. Earlier
> versions replaced it with the secret at run time; that rewriting is gone, because it wrote a
> live credential into a git-tracked file in the workspace.

---

## Gate modes

`sanityops-cli` exits `0` whenever the run completes — **including when defects are found**
— and `2` when the run itself fails. The exit code therefore carries no verdict; the gate is
evaluated from the report's defect counts.

Both platforms support the same three modes:

| Gate mode | Behaviour | GitLab CI variable | GitHub Actions input |
|---|---|---|---|
| **Soft gate** (default) | Report findings, never block the merge | `SANITYOPS_ACTION_BLOCK_ON_P0: "false"` and `SANITYOPS_ACTION_FAIL_ON_DEFECT: "false"` | `block-on-p0: 'false'`, `fail-on-defect: 'false'` |
| **Block on P0** (recommended) | Block only when a P0 blocker is found; P1/P2 pass | `SANITYOPS_ACTION_BLOCK_ON_P0: "true"` | `block-on-p0: 'true'` |
| **Fail on any defect** | Block on any defect (P0/P1/P2) | `SANITYOPS_ACTION_FAIL_ON_DEFECT: "true"` | `fail-on-defect: 'true'` |

`block-on-p0` is evaluated before `fail-on-defect`, so setting both means "block only on P0".

Two situations fail the step regardless of the mode, because neither can produce a
trustworthy verdict:

- **The CLI failed** (exit code other than `0`). A broken run is never reported as a pass.
- **No report was produced** while `skip-defect-check` is disabled. The run inspected
  nothing — an empty artifact list, a wrong `config-path`, or an unreadable config.

---

## Full parameter reference

| Purpose | GitLab CI variable | GitHub Actions input | Default |
|---|---|---|---|
| **LLM API key** | `LLM_API_KEY` | `api-key` | *(from a secret)* |
| **Config file path** | `SANITYOPS_ACTION_CONFIG_PATH` | `config-path` | `.sanityops/inspect_config.yaml` |
| **Inspection depth** | `SANITYOPS_ACTION_CHECK_LEVEL` | `check-level` | `L2` (`L1`/`L2`/`L3`) |
| **Block on P0** | `SANITYOPS_ACTION_BLOCK_ON_P0` | `block-on-p0` | `false` |
| **Fail on any defect** | `SANITYOPS_ACTION_FAIL_ON_DEFECT` | `fail-on-defect` | `false` |
| **LLM provider override** | `SANITYOPS_ACTION_PROVIDER` | `provider` | *(from the config file)* |
| **LLM model override** | `SANITYOPS_ACTION_MODEL_ID` | `model-id` | *(from the config file)* |
| **LLM endpoint override** | `SANITYOPS_ACTION_LLM_BASE_URL` | `base-url` | *(from the config file)* |
| **Timeout (seconds)** | `SANITYOPS_ACTION_TIMEOUT` | `timeout` | `1800` |
| **Token budget** | `SANITYOPS_ACTION_TOKEN_BUDGET` | `token-budget` | `200000` |
| **Analysis only** | `SANITYOPS_ACTION_SKIP_DEFECT_CHECK` | `skip-defect-check` | `false` |
| **Verbose diagnostics** | `SANITYOPS_ACTION_VERBOSE` | `verbose` | `false` |
| **CLI subcommand** | `SANITYOPS_ACTION_COMMAND` | `command` | `inspect` |
| **PR/MR comment token** | `GITLAB_TOKEN` | `github-token` | `${{ github.token }}` |
| **CLI release source** | `SANITYOPS_ACTION_INSTALL_URL` | — | `https://downloads.sanityops.org/sanityops-cli` |
| **CLI version** | `SANITYOPS_ACTION_CLI_VERSION` | — | `latest` |
| **Runner tag** | `SANITYOPS_ACTION_RUNNER_TAG` | — | `docker` |
| **Dashboard API key** | `SANITYOPS_ACTION_SERVER_API_KEY` | `server-api-key` | *(empty — uploads disabled)* |
| **Dashboard base URL** | `SANITYOPS_ACTION_SERVER_BASE_URL` | `server-base-url` | `https://demo.sanityops.org` |

---

## The SANITYOPS_ACTION_ namespace

All action-level settings are prefixed **`SANITYOPS_ACTION_`** on purpose.

`sanityops-cli` reads two environment variables of its own — **`SANITYOPS_API_KEY`** and
**`SANITYOPS_BASE_URL`** — which identify the SanityOps **dashboard** server, not the LLM.
Reusing those names for action settings would redirect dashboard traffic to whatever they
were pointed at. The prefix keeps the two apart.

The LLM endpoint follows the same rule: an unprefixed name for it would sit one word away
from the CLI's own `SANITYOPS_BASE_URL`, which means the dashboard, so here it is
**`SANITYOPS_ACTION_LLM_BASE_URL`** (GitHub input: `base-url`).

The LLM **key** deliberately keeps the CLI's own name, `LLM_API_KEY`, so a secret that
already works with the local CLI works unchanged in CI.

---

## Data handling

What leaves your runner, and when:

- **Nothing is uploaded by default.** Artifact analysis and defect checking run on the
  runner, and artifact content is sent only to the LLM provider you configured. With no
  dashboard API key, nothing is uploaded to SanityOps.
- **Setting a dashboard API key turns on upload.** If `server-api-key` (GitHub) or
  `SANITYOPS_ACTION_SERVER_API_KEY` / `SANITYOPS_API_KEY` (GitLab) is set, `sanityops-cli`
  uploads the scanned System Prompts, Tool Schemas, and Skills to the dashboard at
  `server-base-url` after analysis, so they appear in the web console. The first such run
  also creates the project server-side. Treat this as publishing your artifact sources to
  that service and enable it only if that is intended.
- **Secrets stay local.** API keys are masked in CLI output and redacted from logs, and the
  `.sanityops/` directory is git-ignored apart from the config file you choose to commit.

---

## Outputs

| Output | Meaning |
|---|---|
| `exit-code` | Gate exit code — `0` when the configured gate passed |
| `gate-result` | `PASS` or `FAIL`, the action's verdict |
| `total-defects` | Total defects found across all artifacts |
| `p0-defects` / `p1-defects` / `p2-defects` | Counts per severity |
| `report-path` | Path to the generated markdown report |
| `log-path` | Path to the copied CLI log |

`gate-result` reflects the gate mode you configured, **not** the report's own `Gate` row.
Those differ: the report's `Gate` is `FAIL` whenever any defect group contains a P0, whereas
`gate-result` is `PASS` under a soft gate that found defects. Both are shown in the job
summary so the distinction is visible.

```yaml
      - uses: sanityops-org/sanityops-action@v1
        id: sanityops
        with:
          api-key: ${{ secrets.LLM_API_KEY }}

      - run: echo "Gate: ${{ steps.sanityops.outputs.gate-result }}"
```

Reports and logs are written under `.sanityops/results/` and `.sanityops/logs/`. On GitLab
they are collected as job artifacts automatically; on GitHub, upload them yourself:

```yaml
      - if: always()
        uses: actions/upload-artifact@v4
        with:
          name: sanityops-inspection-report
          path: |
            .sanityops/results/
            .sanityops/logs/
```

The report is read back from the **config file's own directory**: `sanityops-cli` writes it
to `<config-dir>/results/`, so `config-path: 'config/sanityops/inspect_config.yaml'` puts the
report in `config/sanityops/results/`. If you move the config, point the artifact paths (and
the GitLab template's `artifacts.paths`) at the matching directory.

---

## Reusable workflow

Instead of writing the step list yourself, call the bundled reusable workflow. It checks out
your repository, runs the action, prints the outputs, and uploads the report:

```yaml
jobs:
  sanityops:
    uses: sanityops-org/sanityops-action/.github/workflows/inspect.yml@v1
    secrets:
      llm-api-key: ${{ secrets.LLM_API_KEY }}
      # server-api-key: ${{ secrets.SANITYOPS_API_KEY }}   # only for dashboard upload
    with:
      check-level: 'L2'
      block-on-p0: 'true'
```

It accepts the same settings as the action, plus `artifact-retention-days`.

---

## Troubleshooting

**The job is green but defects were found.** That is the soft gate, the default. Choose
*Block on P0* or *Fail on any defect* above if findings should stop the merge.

**"No inspection report found … the run inspected nothing."** The step failed because no
report was written, which means no artifacts were inspected. Check that
`.sanityops/inspect_config.yaml` is committed (see
[Configuring the artifact list](#configuring-the-artifact-list)), that `config-path` points
at it, and that the `prompts` / `tools` / `skills` entries reference files that exist on the
branch being built. If you intended an analysis-only run, set `skip-defect-check: 'true'`
(GitHub) or `SANITYOPS_ACTION_SKIP_DEFECT_CHECK: "true"` (GitLab); the gate is then skipped
rather than failed.

**The LLM key seems to be ignored.** A complete `model:` section in `inspect_config.yaml`
takes precedence over the `LLM_*` environment variables, so `api-key` and the other LLM
settings are not used. Delete the `api_key` line — that leaves the section incomplete, which
makes the environment the source again while the rest of the section is ignored too, so also
move `provider` / `model_id` / `base_url` into the action inputs or the `LLM_*` variables.

**The step fails with "the api_key … still looks like a placeholder".** That is deliberate.
Your config carries a placeholder key, and this action no longer rewrites the config file, so
the placeholder would be sent to the LLM. Delete the `api_key` line and let the `LLM_API_KEY`
secret supply it.

**No merge-request or pull-request comment appears.** Once a report exists, commenting needs
permission: on GitLab, a `GITLAB_TOKEN` with `api` scope; on GitHub,
`permissions: pull-requests: write` on the job. Failures to comment never fail the build.

**The install step cannot reach the CLI.** `SANITYOPS_ACTION_INSTALL_URL` is overridable, so
point it at a mirror or a pinned release host if your runners cannot reach the default.

---

## License

[Apache-2.0](LICENSE)
