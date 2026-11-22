#!/usr/bin/env bash
# Daily Trading Post sync for gw2craft (run by cron). Appends to data/sync.log.
# The tool never prints your API key, so the log is safe to read.
set -u
cd "$(dirname "$0")/.." || exit 1
export PATH="$HOME/.local/bin:$PATH"
{
  echo "=== $(date -Is)"
  uv run gw2craft track sync
} >> data/sync.log 2>&1
