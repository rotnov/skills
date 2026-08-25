#!/bin/sh
set -eu

repo_root=$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd)
install_root=$(mktemp -d "${TMPDIR:-/tmp}/rotnov skills-cli.XXXXXX")
uv_version="0.11.7"

cleanup() {
  rm -rf -- "$install_root"
}
trap cleanup EXIT HUP INT TERM

uv_bin=""
if command -v uv >/dev/null 2>&1; then
  uv_candidate=$(command -v uv)
  set -- $("$uv_candidate" --version)
  if [ "${1:-}" = "uv" ] && [ "${2:-}" = "$uv_version" ]; then
    uv_bin=$uv_candidate
  fi
fi

if [ -z "$uv_bin" ]; then
  uv_environment="$install_root/uv-$uv_version"
  python3 -m venv "$uv_environment"
  "$uv_environment/bin/python" -m pip install \
    --disable-pip-version-check "uv==$uv_version" >/dev/null
  uv_bin="$uv_environment/bin/uv"
fi

set -- $("$uv_bin" --version)
test "${1:-}" = "uv"
test "${2:-}" = "$uv_version"

(
  cd "$install_root"
  npx --yes skills@1.5.20 add "$repo_root" --list >/dev/null
  npx --yes skills@1.5.20 add "$repo_root" \
    --skill '*' -a claude-code codex --copy -y >/dev/null
)

for skill_dir in "$repo_root"/skills/*; do
  [ -d "$skill_dir" ] || continue
  skill_name=$(basename "$skill_dir")
  test -f "$install_root/.claude/skills/$skill_name/SKILL.md"
  test -f "$install_root/.agents/skills/$skill_name/SKILL.md"
done

claude_recorder="$install_root/.claude/skills/learning/scripts/recording.py"
codex_recorder="$install_root/.agents/skills/learning/scripts/recording.py"
test -f "$claude_recorder"
test -f "$codex_recorder"

claude_fixture="$install_root/hostile projects/claude fixture"
codex_fixture="$install_root/hostile projects/codex fixture"

for fixture in "$claude_fixture" "$codex_fixture"; do
  mkdir -p "$fixture"
  git -C "$fixture" init -q
  git -C "$fixture" -c user.name=skills-cli-smoke \
    -c user.email=skills-cli-smoke@example.invalid \
    commit --allow-empty -qm "Initialize recorder fixture"
  printf '%s\n' \
    '[project]' \
    'name = "hostile-project"' \
    'version = "0.0.0"' \
    'requires-python = ">=99"' >"$fixture/pyproject.toml"
  printf '%s\n' '99.99' >"$fixture/.python-version"
done

shell_output="$install_root/claude-status.json"
(
  cd "$claude_fixture"
  "$uv_bin" run --no-project --python 3.12 "$claude_recorder" status \
    >"$shell_output"
)

python3 - "$shell_output" <<'PY'
import json
import pathlib
import sys

status = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
assert status == {"active": False}, status
PY

(
  cd "$codex_fixture"
  python3 - "$uv_bin" "$codex_recorder" <<'PY'
import json
import subprocess
import sys

completed = subprocess.run(
    [
        sys.argv[1],
        "run",
        "--no-project",
        "--python",
        "3.12",
        sys.argv[2],
        "status",
    ],
    capture_output=True,
    check=True,
    shell=False,
    text=True,
)
status = json.loads(completed.stdout)
assert status == {"active": False}, status
PY
)

echo "Pinned skills.sh CLI discovery and cross-client installation: valid"
