#!/bin/sh
set -eu

repo_root=$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd)
install_root=$(mktemp -d "${TMPDIR:-/tmp}/rotnov-skills-cli.XXXXXX")

cleanup() {
  rm -rf -- "$install_root"
}
trap cleanup EXIT HUP INT TERM

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

echo "Pinned skills.sh CLI discovery and cross-client installation: valid"
