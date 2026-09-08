# Agentic Shell Layer production image
# Base: Ubuntu 22.04 LTS (matches target deployment environment)
#
# Build:  docker build -t agentic-shell -f docker/Dockerfile .
# Run:    docker run -it agentic-shell
# SSH:    docker run -it -e ANTHROPIC_API_KEY=... agentic-shell
#
# On startup the shell runs the first-run wizard if no config is present.
# Set AGENTIC_BACKEND, AGENTIC_MODEL env vars to skip the wizard.

FROM ubuntu:22.04

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

# Install Python dependencies
COPY requirements.txt .
RUN pip3 install --no-cache-dir -r requirements.txt

# Pre-cache tiktoken encodings at build time (avoids first-run latency)
RUN python3 -c "import tiktoken; tiktoken.get_encoding('cl100k_base'); tiktoken.get_encoding('o200k_base')"

# Copy project source
COPY shell/ ./shell/
COPY tests/ ./tests/

# Create runtime directories
RUN mkdir -p \
    /root/.config/agentic-shell \
    /root/.local/share/agentic-shell \
    /var/log/agentic-shell \
    && chmod 1777 /var/log/agentic-shell \
    && touch /var/log/agentic-shell/audit.log \
    && chmod 0666 /var/log/agentic-shell/audit.log

# Register agentic-shell as a valid login shell
RUN echo "/usr/bin/python3 -m shell.main" >> /etc/shells || true

# Write a thin wrapper so it can be used as a login shell by path
RUN printf '#!/bin/bash\nexec python3 -m shell.main "$@"\n' > /usr/local/bin/agentic-shell \
    && chmod +x /usr/local/bin/agentic-shell

# Optional: pre-seed a minimal config via build args (skips wizard)
ARG AGENTIC_BACKEND=ollama
ARG AGENTIC_MODEL=llama3.1
ARG AGENTIC_API_BASE=http://localhost:11434
RUN python3 -c "\
import json, pathlib; \
cfg = pathlib.Path('/root/.config/agentic-shell/config.json'); \
cfg.parent.mkdir(parents=True, exist_ok=True); \
cfg.write_text(json.dumps({ \
  'backend': '${AGENTIC_BACKEND}', \
  'model': '${AGENTIC_MODEL}', \
  'api_base': '${AGENTIC_API_BASE}', \
  'routing_mode': 'auto', \
  'daily_token_budget': None, \
  'session_token_budget': None, \
  'privacy_mode': False, \
  'setup_complete': True \
}, indent=2)); \
import os, stat; os.chmod(cfg, stat.S_IRUSR | stat.S_IWUSR) \
"

# Production: launch agentic-shell as the login shell
CMD ["/usr/local/bin/agentic-shell"]
