#!/bin/sh
set -eu

repo_root=$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd)
install_root=$(mktemp -d "${TMPDIR:-/tmp}/rotnov skills-cli.XXXXXX")

cleanup() {
  rm -rf -- "$install_root"
}
trap cleanup EXIT HUP INT TERM

if ! command -v uv >/dev/null 2>&1; then
  echo "error: uv is required; install the version declared in pyproject.toml" >&2
  exit 1
fi

uv_bin=$(command -v uv)
(
  cd "$repo_root"
  "$uv_bin" --version >/dev/null
)

(
  cd "$install_root"
  npx --yes skills@1.5.20 add "$repo_root" --list >/dev/null
)

learning_install_root=""
for skill_dir in "$repo_root"/skills/*; do
  [ -d "$skill_dir" ] || continue
  skill_name=$(basename "$skill_dir")
  skill_install_root="$install_root/$skill_name"
  mkdir -p "$skill_install_root"

  (
    cd "$skill_install_root"
    npx --yes skills@1.5.20 add "$repo_root" \
      --skill "$skill_name" -a claude-code codex --copy -y >/dev/null
  )

  test -f "$skill_install_root/.claude/skills/$skill_name/SKILL.md"
  test -f "$skill_install_root/.agents/skills/$skill_name/SKILL.md"
  test "$(find "$skill_install_root/.claude/skills" \
    -mindepth 1 -maxdepth 1 -type d | wc -l | tr -d ' ')" -eq 1
  test "$(find "$skill_install_root/.agents/skills" \
    -mindepth 1 -maxdepth 1 -type d | wc -l | tr -d ' ')" -eq 1

  if [ "$skill_name" = "learning" ]; then
    learning_install_root=$skill_install_root
  fi
done

test -n "$learning_install_root"

claude_recorder="$learning_install_root/.claude/skills/learning/scripts/recording.py"
codex_recorder="$learning_install_root/.agents/skills/learning/scripts/recording.py"
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

"$uv_bin" run --no-project --python 3.12 python - "$shell_output" <<'PY'
import json
import pathlib
import sys

status = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
assert status == {"active": False}, status
PY

(
  cd "$codex_fixture"
  "$uv_bin" run --no-project --python 3.12 python - \
    "$uv_bin" "$codex_recorder" <<'PY'
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
