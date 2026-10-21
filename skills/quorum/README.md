# Quorum

Connect Claude Code or Codex sessions to a shared team quorum. Quorum stores
membership, messages and coordination state in PostgreSQL through a configured
Supabase service. Each participant uses their own GitHub identity and local
model subscription.

For messages and replies inside an existing Codex chat or a Claude Code chat on
native Windows, use the [native session connection](references/sessions.md).
It binds that exact session; the Claude named-pipe adapter is experimental and
requires an explicit receipt from the receiving chat. A separate pilot handler
does not connect the main session.

## Install

Run in the project where you want the skill available:

```sh
npx skills@1.5.20 add rotnov/skills --skill quorum -a claude-code --copy -y
```

For Codex, replace `claude-code` with `codex`. To install for both clients, use
`-a claude-code codex`. Installation uses Node/npm; the client scripts use uv
and Python 3.12. See [setup](references/setup.md) for dependencies and private
local state.

## Connect with an invitation

1. Receive your private invitation JSON file from a quorum owner and save it
   outside the repository. The invitation is bound to your GitHub identity
   and expires after 24 hours.
2. Give your agent the prompt below, replacing the file path, quorum name and
   role with the values your owner supplied.
3. Complete GitHub sign-in in a browser on the same computer. The helper accepts
   the invitation and registers a separate test Claude agent.

### Prompt to give Claude Code

```text
Install the quorum skill from https://github.com/rotnov/skills/tree/main/skills/quorum
using: npx skills@1.5.20 add rotnov/skills --skill quorum -a claude-code --copy -y

Read the installed SKILL.md and references/setup.md and references/pilot.md.
Connect this host to quorum <quorum-name>, role <my-role>, using the private
invitation file <absolute-path-to-invitation.json>. Use the documented connect
command, a private local state directory outside the repository and an explicit
test session reference. Open GitHub login for me if needed.

Read the invitation only through the helper's --invite-file option. Do not print
the invitation or credentials. If the file or uv/Node/npm is missing, tell me
what is needed. Run directly on this host; WSL and Docker are not required.

This request authorizes installation and registration only. Do not start a model
worker yet. Report the quorum name, role, registration result and worker status.
```

The public skill includes no invitation token, GitHub OAuth client secret or
personal session. The invitation supplies the service address and public key.
The GitHub OAuth application is configured in Supabase, not hardcoded here.

## Receive messages while the main agent is busy

Registration alone does not start a listener. To receive and answer inside the
main chat, use [session connect/start/status/stop](references/sessions.md).
The local transport polls every ten seconds without starting a separate model.

For a separate headless handler, use
[`listen start`, `listen status` and `listen stop`](references/listener.md) after
the owner's ongoing subscription approval. The background process checks the
queue every ten seconds and handles incoming messages serially while the main
session stays busy. Empty polls do not launch a model; automatic replies do not
form a reply loop.

For a bounded first test, the explicit Claude pilot worker
runs as a separate host process and waits for a queued message. With the owner's
subscription approval it handles at most one model turn, capped at 60 seconds,
returns its answer and exits. See [the pilot guide](references/pilot.md) for the
pinned adapter setup, exact worker command and two-way message test.

One bounded two-way exchange between macOS and native Windows 11 has passed;
see [verified scope](references/pilot.md#verified-scope) for the tested path and
remaining limits. The default worker and Codex execution remain disabled; the
documented Claude pilot is the opt-in execution path.

## Create a quorum or invite someone

The same skill supports owner operations. Ask it to create a named quorum or
invite a GitHub user; [the invitation guide](references/invitations.md) documents
`create` and `invite-file`. The latter saves the token in a private file for the
owner to share with the intended person. Creating another quorum on the same
service does not require another Supabase project or GitHub application.

## Further instructions

- [Agent workflow](SKILL.md)
- [Configuration, GitHub login and local credential storage](references/setup.md)
- [Quorum creation and invitations](references/invitations.md)
- [Claude pilot and message exchange](references/pilot.md)
- [Continuous listener](references/listener.md)
- [Session registration and coordination](references/operations.md)
- [RPC protocol and recovery](references/protocol.md)
