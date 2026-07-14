#!/usr/bin/env bash
# Run this once, by hand, before adding memobot-mcp to an MCP client —
# especially clients with a short startup timeout (e.g. OpenClaw). The
# first-ever `uvx --from git+...` run has to clone and build the package,
# which can take 10-30s; a client that gives up before that finishes will
# see the process die mid-startup (often surfaced as a generic pipe/EPIPE
# error) and report a failure that has nothing to do with memobot-mcp
# itself. This checks/installs prerequisites, then "warms" uv's cache so
# every later launch (by any client) starts near-instantly.
set -euo pipefail

REPO_URL="git+https://github.com/tinhvqbk/memobot-mcp"

if ! command -v git >/dev/null 2>&1; then
  echo "git is required but not found on PATH. Install it first (e.g. apt install git, brew install git)." >&2
  exit 1
fi

if ! command -v uv >/dev/null 2>&1; then
  echo "uv not found — installing via the official installer (https://astral.sh/uv)..."
  curl -fsSL https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
fi

if ! command -v uvx >/dev/null 2>&1; then
  echo "uvx still not on PATH after installing uv — check your shell's PATH and re-run." >&2
  exit 1
fi

echo "Warming the uvx cache for memobot-mcp (first run clones + builds it)..."
echo '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"preflight","version":"1"}}}' \
  | uvx --from "$REPO_URL" memobot >/dev/null

echo "OK — memobot-mcp is installed and warmed up. It's now safe to add to any MCP client."
