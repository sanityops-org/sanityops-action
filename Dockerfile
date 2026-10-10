FROM --platform=linux/amd64 python:3.12-slim

ARG SANITYOPS_INSTALL_URL="https://downloads.sanityops.org/sanityops-cli"
ARG SANITYOPS_CLI_VERSION="latest"

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# Runtime utilities. The installer needs curl and, on some runners, git.
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    git \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Install the CLI through the published installer, which verifies the download
# against checksums.txt. Pin SANITYOPS_CLI_VERSION in production builds.
RUN curl -fsSL "${SANITYOPS_INSTALL_URL}/install.sh" \
    | bash -s -- --system --yes --version "${SANITYOPS_CLI_VERSION}"

# Entrypoint and helpers are flattened to the filesystem root so the entrypoint
# finds them regardless of the mounted working directory.
COPY entrypoint.sh /entrypoint.sh
COPY scripts/export_action_outputs.py /export_action_outputs.py
COPY scripts/evaluate_gate.py /evaluate_gate.py
COPY scripts/check_llm_config.py /check_llm_config.py
COPY scripts/post_pr_comment.py /post_pr_comment.py
RUN chmod +x /entrypoint.sh /export_action_outputs.py /evaluate_gate.py \
    /check_llm_config.py /post_pr_comment.py

# GitHub Actions mounts the caller's repository here.
WORKDIR /github/workspace

ENTRYPOINT ["/entrypoint.sh"]
