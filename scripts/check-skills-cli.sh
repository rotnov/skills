#!/bin/sh
set -eu

# Smoke a clean install, not the Codex or Claude Code chat that runs this gate.
unset CODEX_THREAD_ID CLAUDE_CODE_SESSION_ID \
  CLAUDE_CODE_MESSAGING_SOCKET CLAUDE_CODE_MESSAGING_TOKEN

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

  if [ "$skill_name" = "quorum" ]; then
    for client_skill in "$skill_install_root/.claude/skills/quorum" \
      "$skill_install_root/.agents/skills/quorum"; do
      (
        cd "$install_root"
        "$uv_bin" run --no-project --python 3.12 \
          "$client_skill/scripts/quorum.py" --state "$skill_install_root/state" status \
          >"$skill_install_root/quorum-status.json"
        "$uv_bin" run --no-project --python 3.12 python - \
          "$skill_install_root/quorum-status.json" <<'PY'
import json
import pathlib
import sys
result = json.loads(pathlib.Path(sys.argv[1]).read_text())
assert result["ok"] and result["data"]["live_agents"] == "disabled"
assert result["data"]["worker"] == "offline"
PY
        "$uv_bin" run --no-project --python 3.12 \
          "$client_skill/scripts/quorum.py" --state "$skill_install_root/state" listen status \
          >"$skill_install_root/listener-status.json"
        "$uv_bin" run --no-project --python 3.12 python - \
          "$skill_install_root/listener-status.json" <<'PY'
import json
import pathlib
import sys
result = json.loads(pathlib.Path(sys.argv[1]).read_text())
assert result["ok"] and result["data"]["listener"] == "offline"
assert result["data"]["handled"] == 0
PY
        "$uv_bin" run --no-project --python 3.12 \
          "$client_skill/scripts/quorum.py" --state "$skill_install_root/state" \
          session --session-id 11111111-1111-4111-8111-111111111111 status \
          >"$skill_install_root/session-status.json"
        "$uv_bin" run --no-project --python 3.12 python - \
          "$skill_install_root/session-status.json" <<'PY'
import json
import pathlib
import sys
result = json.loads(pathlib.Path(sys.argv[1]).read_text())
assert result["ok"] and result["data"]["listener"] == "offline"
assert result["data"]["mode"] == "native_codex_session"
PY
        if "$uv_bin" run --no-project --python 3.12 \
          "$client_skill/scripts/quorum.py" --state "$skill_install_root/state" \
          join amplifier --role backend >"$skill_install_root/quorum-failure.json"; then
          echo "error: unconfigured quorum join unexpectedly succeeded" >&2
          exit 1
        fi
        "$uv_bin" run --no-project --python 3.12 python - \
          "$skill_install_root/quorum-failure.json" <<'PY'
import json
import pathlib
import sys
result = json.loads(pathlib.Path(sys.argv[1]).read_text())
assert result == {"ok": False, "code": "service_not_configured", "data": {}}
PY
        "$uv_bin" run --no-project --python 3.12 python - \
          "$client_skill" "$skill_install_root/state" <<'PY'
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(sys.argv[1]) / "scripts"))
from quorum_auth import CredentialStore
from quorum_invitations import create_id_invitation
from quorum_api import ServiceConfig
import uuid

assert (pathlib.Path(sys.argv[1]) / "references/invite.md").is_file()
assert (pathlib.Path(sys.argv[1]) / "references/join.md").is_file()
class IdApi:
    def call(self, operation, payload, request_id=None):
        if operation == "quorum_lookup": return {"quorum_id": "synthetic-quorum"}
        if operation == "quorum_check_owner": return True
        assert operation == "invite_create_id"
        return {"invite_id": str(uuid.UUID(int=1)), "expires_at": "synthetic-expiry"}
invitation = create_id_invitation(IdApi(), ServiceConfig("https://example.invalid", "sb_publishable_synthetic"),
                                  "team", "123", "synthetic-request")
assert invitation["invite_id"] in invitation["session_prompt"]
assert "rotnov/skills" in invitation["session_prompt"]
assert "references/join.md" in invitation["session_prompt"]
assert "token" not in invitation

root = pathlib.Path(sys.argv[2]) / "credentials"
store = CredentialStore(root=root)
session = {"access_token": "synthetic-access", "refresh_token": "synthetic-refresh"}
store.save("https://example.invalid", session)
assert CredentialStore(root=root).load("https://example.invalid") == session
assert CredentialStore(root=root).load("https://other.invalid") is None
store.delete("https://example.invalid")
assert store.load("https://example.invalid") is None
store.save("https://example.invalid", session)
next(root.iterdir()).write_text("{broken")
try:
    store.load("https://example.invalid")
except ValueError as error:
    assert str(error) == "credential_store_unavailable"
else:
    raise AssertionError("corrupt installed credential store was accepted")
store.delete("https://example.invalid")
PY
      )
    done
  fi

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

"$uv_bin" run --no-project --python 3.12 python - \
  "$uv_bin" "$claude_recorder" "$claude_fixture" \
  "$codex_recorder" "$codex_fixture" <<'PY'
import json
import subprocess
import sys


def invoke(recorder, fixture, *arguments):
    completed = subprocess.run(
        [sys.argv[1], "run", "--no-project", "--python", "3.12", recorder, *arguments],
        cwd=fixture,
        capture_output=True,
        check=True,
        text=True,
    )
    return json.loads(completed.stdout)


for recorder, fixture in ((sys.argv[2], sys.argv[3]), (sys.argv[4], sys.argv[5])):
    started = invoke(recorder, fixture, "start", "--label", "install", "smoke")
    invoke(recorder, fixture, "note", "--kind", "decision", "--summary", "processed")
    claimed = invoke(recorder, fixture, "prepare-end")
    flushed = invoke(
        recorder, fixture, "flush",
        "--recording-id", claimed["recording_id"],
        "--claim-id", claimed["claim_id"],
        "--revision", str(claimed["revision"]),
    )
    assert flushed["recording_id"] == started["recording_id"], flushed
    assert flushed["label"] == "install smoke", flushed
    assert flushed["phase"] == "open", flushed
    assert flushed["revision"] == 3, flushed
    assert flushed["events"] == [], flushed
    assert flushed["end_claim"] is None, flushed
    invoke(recorder, fixture, "note", "--kind", "outcome", "--summary", "subsequent")
    next_claim = invoke(recorder, fixture, "prepare-end")
    assert [event["summary"] for event in next_claim["events"]] == ["subsequent"]
    invoke(
        recorder, fixture, "close",
        "--recording-id", next_claim["recording_id"],
        "--claim-id", next_claim["claim_id"],
        "--revision", str(next_claim["revision"]),
    )
    assert invoke(recorder, fixture, "status") == {"active": False}
PY

echo "Pinned skills.sh CLI discovery, cross-client installation, and flush lifecycle: valid"
