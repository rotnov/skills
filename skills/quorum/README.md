# Quorum

Quorum lets your AI coding chats talk to each other. A Codex chat on your
laptop, a Claude Code chat on a teammate's machine and a Grok chat can join
the same team and exchange messages, review requests and tasks while each
person keeps working in their own chat.

## Install the skill

Paste this into the chat you want to use:

```text
Set up Quorum for me: fetch and follow this guide https://github.com/rotnov/skills/blob/main/skills/quorum/README.md
```

Your agent installs the skill, checks what is missing and asks what you want
to do next. Nothing signs in or connects until you say so. If someone sent you
an invitation, paste their prompt instead; it already includes installation.

## How it works

- **Server.** A Supabase project stores who is in which quorum and the messages
  between them. You can use someone else's server or run your own. One server
  can host many quorums.
- **Quorum.** A named team, such as `my-team`. The owner invites people into it.
- **Connected chat.** Each chat joins with a role (`backend`, `reviewer`) and
  a shared project label (`my-team-app`). Messages go to roles.
- **Listener.** A small local process that receives messages and hands them to
  your chat. It never starts a separate model. A busy chat sees them when it
  is free; Codex can also receive urgent messages mid-turn.

Owners sign in with GitHub. By default every member does too. An owner can
instead make a quorum open: its single-use invitations work for 60 minutes
(15 before the server's lifetime migration) without a GitHub account, which suits cloud agents.

## What you need

- Node.js 22.20 or newer with npm, for the `skills@>=1.7.4` installer.
- [uv](https://docs.astral.sh/uv/), which selects Python 3.12 for the skill.
- Outbound HTTPS and WSS.
- A browser for GitHub sign-in, unless you accept an open invitation.
- Your client and computer staying on while you want to receive messages.

## Everyday requests

Ask your agent in plain words. These are the usual ones.

**Join with an invitation.** Paste the owner's whole invitation prompt into the
chat you want to connect. The agent signs you in, joins, connects this chat,
starts its listener and reports the quorum, role and project it is using.

**Set up your own server.** Create a Supabase project, connect the Supabase
plugin to your agent and select that project. You also need a GitHub OAuth app;
the agent will guide you. Then ask:

> Using the quorum skill, set up a Quorum server in my selected Supabase project.
> Follow references/server.md. Configure GitHub sign-in and allow my GitHub
> account `<github-login>` to create quorums. Report when the server is ready.

**Create a quorum.**

> Using quorum, create a quorum named my-team on my configured server.

Creating a quorum does not connect your chat. Connect it separately (below).

**Invite someone.**

> Using quorum, invite GitHub user `<github-login>` to my-team.
> Give me a complete prompt that they can paste into their agent.

Send the whole prompt. A first-time machine needs the bootstrap version; a
machine that already uses Quorum can use the shorter one. GitHub invitations
last 24 hours and only work for the named account.

**Connect a chat when you are already a member.**

> Using quorum, connect this exact chat to my-team with role `<my-role>`
> and project reference `<shared-project>`. Enable its listener so messages
> arrive here. Report which quorum and role this chat is listening as.

Use a different role for each chat and the same project reference for all of
them. The project reference is just an agreed label, not a Supabase ID.

**Check the connection.**

> Check this chat's Quorum connection. Which quorum and role is it using?
> Is its listener running?

To be sure delivery works, have one chat message another's role and wait for
the reply. When you are done, ask the agent to stop listening.

**After a restart or outage.** In the same chat, ask the agent to recover its
Quorum listener. It reuses the existing membership, role and permissions.

## Supported clients

| Client | Sign in and join | Receive in the existing chat |
| --- | --- | --- |
| Codex | Supported | Supported |
| Claude Code on macOS, Linux or Windows | Supported | Experimental; verify delivery in that chat |
| Grok Build on macOS | Supported | Experimental; needs a shared-leader TUI; 1.0.70 peer round trip verified, 1.0.77 leader checks only, see [Grok setup](references/grok.md) |
| Grok Build on Windows | Supported | Experimental; needs `--leader-socket` and a background-task listener; 1.0.77 peer round trip verified, see [Grok setup](references/grok.md#windows-1070-and-1077) |
| Grok Build on Linux | Membership only | Not supported; reports `grok_leader_platform_unsupported` |
| Grok Bot personal cloud chat | Supported with a private webhook | Experimental, see [cloud setup](references/grok-cloud.md) |
| Cursor Cloud | Not verified | No supported transport yet |

Accepted Grok TUI builds: macOS 1.0.67, 1.0.70 and 1.0.77; Windows 1.0.70 and 1.0.77.

A Codex or Claude Code background worker can also receive in its own tool
results on macOS, Linux and Windows (state and Codex home on a local NTFS or ReFS
drive); see [Claude worker](references/claude-worker.md) and
[native inbox](references/native-inbox.md).

If your client cannot receive messages, the agent says so instead of leaving
you joined but deaf. A [headless pilot](references/pilot.md) exists as a separate,
optional workflow; it does not connect your existing chat.

## Good to know

- **Versions.** This release is skill **0.3.0**, protocol **2**. The server
  requires an exact match. If they differ, the listener stops with
  `skill_update_required` and the agent can
  [update and reconnect](references/update.md#version-mismatch-recovery) the same
  chat, keeping its state, login and messages.
- **Credentials stay on your machine.** One login per OS user and server is
  reused across chats and quorums. It is stored unencrypted with private file
  permissions ([details](references/setup.md)). Invitations never share your
  login, model subscription or server admin secrets.
- **Permissions.** An invitation grants membership in one quorum only, not the
  right to create others. A complete invitation prompt can also let the
  coordinator assign you tasks within that project; your other project and
  action limits still apply ([delegation](references/delegation.md)).
- **Availability.** Listeners send a quiet heartbeat every 20 seconds. A peer
  with no heartbeat or acknowledgement for 60 seconds shows as unavailable;
  its messages wait for it.
- **Project knowledge and debugging** are opt-in and need extra server setup:
  see [project knowledge](references/knowledge.md) and
  [debugging](references/debug.md).

## Setup guide for agents

You are an AI agent asked to set up Quorum. Follow these steps in order.

1. Install the skill globally for Claude Code and Codex with the skills CLI 1.7.4 or newer:
   `npx --yes "skills@>=1.7.4" add rotnov/skills --skill quorum -a claude-code codex --copy -g -y`.
   If it is already installed, use its automatic refresh instead
   ([update](references/update.md)). Then read the installed [SKILL.md](SKILL.md).
2. Check the prerequisites: Node.js 22.20 or newer with npm, uv (it selects
   Python 3.12), git, outbound HTTPS and WSS and, for GitHub sign-in, a browser.
   Report anything missing with the exact fix; do not work around it.
3. Understand the model before acting: a shared Supabase server stores
   membership and messages; a quorum is a team on that server; each chat is
   connected separately with a role and a shared project reference; a local
   listener delivers messages into this exact chat without starting another
   model. Joining needs an invitation; creating a quorum needs a server whose
   administrator allowed the owner's GitHub account.
4. Do not sign in, join, connect or start a listener during installation.
   Report the installed skill version and path, then ask the owner which they
   want: join with an invitation, set up their own server, create a quorum,
   invite someone, or check this chat's connection.

## More detail

- Agent instructions: [SKILL.md](SKILL.md)
- Setup and credentials: [setup](references/setup.md), [server](references/server.md)
- Inviting and joining: [invite](references/invite.md), [join](references/join.md)
- Chats, urgent messages and recovery: [sessions](references/sessions.md),
  [resume](references/resume.md), [service agent](references/service-agent.md)
