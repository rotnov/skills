#!/bin/sh
set -eu

repo_root=$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd)

if ! command -v uv >/dev/null 2>&1; then
  echo "error: uv is required; install the version declared in pyproject.toml" >&2
  exit 1
fi

cd "$repo_root"
uv run --frozen python scripts/check_agentskills_spec.py

echo "Pinned agentskills.io reference validation: valid"
