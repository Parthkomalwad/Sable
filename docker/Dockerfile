# Sable production image
# Base: Ubuntu 24.04 LTS (Python 3.12; pyproject requires >=3.11)
#
# Build:  docker build -t sable -f docker/Dockerfile .
# Run:    docker run -it sable
# SSH:    docker run -it -e ANTHROPIC_API_KEY=... sable
#
# On startup the shell runs the first-run wizard if no config is present.
# Set SABLE_BACKEND, SABLE_MODEL env vars to skip the wizard.

FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# System dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 \
    python3-pip \
    python3-venv \
    bash \
    tmux \
    curl \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python dependencies into a venv.
#
# Ubuntu 24.04 ships Python 3.12, whose system environment is marked
# externally managed (PEP 668), so a bare `pip3 install` refuses outright. A
# venv rather than --break-system-packages, because that is what install.sh
# builds on a real machine: the image and the installer should not disagree
# about where dependencies live.
ENV VIRTUAL_ENV=/opt/venv
ENV PATH="$VIRTUAL_ENV/bin:$PATH"
RUN python3 -m venv "$VIRTUAL_ENV"

COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

# Pre-cache tiktoken encodings at build time (avoids first-run latency)
RUN python -c "import tiktoken; tiktoken.get_encoding('cl100k_base'); tiktoken.get_encoding('o200k_base')"

# Copy project source. `shell/` is deliberately absent: it is the compat shim
# that structure.md §5 step 7 deletes one release out, and an image that
# invokes `shell.main` stops booting the day that happens. Installed machines
# still need the shim, which is why install.sh keeps its `shell.*` strings and
# tests/integration/test_inplace_upgrade.py covers them; a fresh image does not.
COPY sable/ ./sable/
COPY tests/ ./tests/

# Create runtime directories
RUN mkdir -p \
    /root/.config/agentic-shell \
    /root/.local/share/agentic-shell \
    /var/log/agentic-shell \
    && chmod 1777 /var/log/agentic-shell \
    && touch /var/log/agentic-shell/audit.log \
    && chmod 0666 /var/log/agentic-shell/audit.log

# Register sable as a valid login shell
RUN echo "/usr/local/bin/sable" >> /etc/shells || true

# Write a thin wrapper so it can be used as a login shell by path. It names
# the venv interpreter explicitly: a login shell does not inherit this image's
# ENV PATH, so `python3` alone would find the system interpreter, which has
# none of the dependencies.
RUN printf '#!/bin/bash\nexport PYTHONPATH=/app\nexec /opt/venv/bin/python -m sable.app.main "$@"\n' \
    > /usr/local/bin/sable \
    && chmod +x /usr/local/bin/sable

# Optional: pre-seed a minimal config via build args (skips wizard)
ARG SABLE_BACKEND=ollama
ARG SABLE_MODEL=llama3.1
ARG SABLE_API_BASE=http://localhost:11434
RUN python -c "\
import json, pathlib; \
cfg = pathlib.Path('/root/.config/agentic-shell/config.json'); \
cfg.parent.mkdir(parents=True, exist_ok=True); \
cfg.write_text(json.dumps({ \
  'backend': '${SABLE_BACKEND}', \
  'model': '${SABLE_MODEL}', \
  'api_base': '${SABLE_API_BASE}', \
  'routing_mode': 'auto', \
  'daily_token_budget': None, \
  'session_token_budget': None, \
  'privacy_mode': False, \
  'setup_complete': True \
}, indent=2)); \
import os, stat; os.chmod(cfg, stat.S_IRUSR | stat.S_IWUSR) \
"

# Production: launch sable as the login shell
CMD ["/usr/local/bin/sable"]
