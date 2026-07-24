#!/bin/sh
set -eu

repo_root=$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd)
reference_checkout="38a2ff82958afee88dadf4831509e6f7e9d8ef4e"
validator_env=$(mktemp -d "${TMPDIR:-/tmp}/rotnov-skills-ref.XXXXXX")

cleanup() {
  rm -rf -- "$validator_env"
}
trap cleanup EXIT HUP INT TERM

python3 -m venv "$validator_env"
"$validator_env/bin/python" -m pip install \
  --disable-pip-version-check \
  --quiet \
  "git+https://github.com/agentskills/agentskills.git@$reference_checkout#subdirectory=skills-ref"

"$validator_env/bin/python" "$repo_root/scripts/check_agentskills_spec.py"

echo "Pinned agentskills.io reference validation: valid"
