# Quorum

Quorum connects specific Codex and Claude Code chats, with experimental Grok
TUI support on macOS, so they can exchange messages while you work. A shared Supabase server stores membership and messages;
a listener on each participant's computer delivers them to the connected chat.
Each person signs in with their own GitHub account.

You can join someone else's server with an invitation or set up your own server.
One server can host several independent quorums.

## Install the skill

Give your agent this request:

> Install the quorum skill from
> <https://github.com/rotnov/skills/tree/main/skills/quorum> for this client,
> globally for Claude Code and Codex with the pinned skills@1.5.20 CLI,
> then read its SKILL.md. If already installed, use its immutable automatic refresh.

The pinned `skills@1.5.20` installer needs Node.js >=22.20 and npm. The client
needs uv, Python 3.12, a browser for
GitHub login and outbound HTTPS. The agent should explain any missing
prerequisites. Installation alone does not connect a chat or start reception.

This release uses skill version **0.2.0** and protocol **1**. The server requires
an exact match; a guarded listener stops with `skill_update_required` when they
differ. Your agent can follow the [update and reconnect guide](references/update.md#version-mismatch-recovery)
to refresh once and restart the same authorized chat binding, keeping its state,
login and messages. A current invitation authorizes that lifecycle; older joins
need their owner's grant. Legacy listeners remain reachable for a rollout notice
but are not force-updated. A listener already stopped for mismatch needs your or
the current session's next interaction to recover.

## Set up your own server

Skip this section if someone has sent you an invitation.

Create a Supabase project in your account. Connect the Supabase plugin to your
agent and select that project. You also need a GitHub OAuth application for
sign-in; the agent can guide you through its configuration. Then ask:

> Using the quorum skill, set up a Quorum server in my selected Supabase project.
> Follow references/server.md. Configure GitHub sign-in and allow my GitHub
> account `<github-login>` to create quorums. Report when the server is ready.

The agent prepares the database and local client; you complete any account
configuration or browser login that requires you. The
[server setup guide](references/server.md) contains the administrator steps.

The server administrator chooses who may create quorums. Installing the skill
or signing in with GitHub does not grant that permission. An already configured
service can also be used without a connector, using its explicitly supplied
public connection settings.

## Create a quorum

Once the server is ready, ask:

> Using quorum, create a quorum named my-team on my configured server.

Sign in through GitHub if prompted. The agent returns the quorum name and
confirms that you are its owner. Another quorum uses the same server and
GitHub application.

**Creation does not connect this chat or turn on its listener.** A quorum is
the shared space; each participating chat must be connected separately.

## Invite someone

As the quorum owner, ask:

> Using quorum, invite GitHub user `<github-login>` to my-team.
> Give me a complete prompt that they can paste into their agent.

The agent returns an invitation ID and a ready-to-share prompt. It gets the
public service settings from your selected Supabase connector or explicitly
configured service. No server address or key is bundled in the public skill.

For a first machine, send the **whole bootstrap prompt**, which supplies the public
service locator and global installation command. For an already configured machine,
send the separate configured-machine prompt: the invitation ID is enough to reuse
its selected service and own login, including in a fresh state directory.
Both show the intended GitHub username (when supplied), numeric ID, quorum and expiry.
Invitations last 24 hours and require the matching GitHub account. Sign in through
GitHub via Supabase Auth; no Supabase dashboard registration is needed.

An invitation grants membership in that quorum, without permission to create
others on the server. One member can connect several of their own sessions
without another invitation. See the [owner's invitation guide](references/invite.md).

## Join and connect your chat

Paste the owner's complete invitation prompt into the chat you want to use.
Complete GitHub sign-in with the invited account. The agent follows
[join.md](references/join.md): it accepts membership, connects this exact chat,
starts its native listener, checks status and says **Hello** with its quorum,
role and project reference. No second prompt is needed. An unavailable native
transport is reported as a blocker, rather than silently leaving you joined
without reception. The low-level `accept-id` command alone remains membership-only.

If you are already a member, ask your chat:

> Using quorum, connect this exact chat to my-team with role `<my-role>`
> and project reference `<shared-project>`. Enable its listener so messages
> arrive here. Report which quorum and role this chat is listening as.

The project reference is an agreed label for the work, such as `my-team-app`.
All participating chats use that same label; it is not a Supabase project ID.
Use a distinct role for each chat, such as backend or reviewer. Your own newly
created quorum already gives you membership; you can connect without an invite.

To verify delivery, ask one connected agent to message the other's role.
Confirm it appears in the intended chat and that a reply comes back.
The [session guide](references/sessions.md) contains the technical steps.

## Check whether you are listening

Ask your agent:

> Check this chat's Quorum connection. Which quorum and role is it using?
> Is its listener running?

While enabled, the listener receives server notifications and reads the stored
queue, then passes messages to that chat. If notifications are unavailable it
checks every ten seconds; with a healthy connection it also checks once a minute
to recover a missed notification. A busy chat processes messages when available. The listener
does not start a separate model. Keep the client and computer available for
reception; ask the agent to stop listening when you are done.

The invitation workflow completes membership, connection and listening in order.
The agent reports which steps actually completed. A local Hello confirms the
workflow reached running status; a peer message and reply verify communication.

## Supported clients

| Client | Sign in and join | Receive in the existing chat |
| --- | --- | --- |
| Codex | Supported | Supported |
| Claude Code on Windows | Supported | Experimental; verify delivery in that chat |
| Claude Code on macOS or Linux | Supported | Experimental; verify delivery in that chat |
| Grok Build 1.0.67 on macOS | Supported | Experimental; explicit shared-leader TUI required; see [Grok setup](references/grok.md) |
| Grok Build on Linux or Windows | Membership only | Native reception unsupported |

The [separate headless pilot](references/pilot.md) is an optional different
workflow; it does not connect your existing chat.

One login per OS user and configured service is reused across native sessions and
quorums without reopening a browser on every join. Separate services and ledgers
remain isolated. Existing authenticated states retain their original credential
path, shared through a private machine registry instead of copying rotating tokens.
GitHub login credentials remain on each participant's machine. This pilot stores
them unencrypted with private filesystem permissions; see
[local credential storage](references/setup.md). Invitations never share a
person's login, model subscription credentials or Supabase administrator secrets.

For agent instructions, see [SKILL.md](SKILL.md). Technical commands and recovery
steps live in [setup](references/setup.md), [inviting](references/invite.md),
[joining](references/join.md) and [sessions](references/sessions.md).

## Coordinator tasks and availability

The complete invite prompt lets the receiving owner authorize coordinator tasks
once for the selected project. No repeated assignment approval is needed within
that grant; other project and action permissions stay in force. See
[delegation and revocation](references/delegation.md). Old joins do not gain this
permission automatically.

When the receiving human pastes the generated prompt as their own instruction,
it also explicitly opts into built-in native subagents for matching coordinator
tasks using `--accept-tasks-from <coordinator> --dispatch-mode native-subagent`.
The stored grant keeps existing project/action boundaries. Old grants stay
inline, and revocation removes the mode. The parent chat acknowledges intake,
uses at most two actual supported native workers, returns to the owner's work
and forwards results itself; unavailable capability or slots leave tasks pending.
See [dispatch policy](references/delegation.md#dispatch-matching-tasks).
Subagents do not bypass queued input: immediate active-turn ingestion still
depends on [#35](https://github.com/rotnov/skills/issues/35)'s verified owning-server
steer capability. This opt-in/instruction change neither implements that feature
nor eliminates all blocking or tool-call latency; native UI acceptance is separate.

The server also needs the reviewed native coordination migration listed in
[server setup](references/server.md). Updated listeners send a quiet heartbeat
every 20 seconds. `session peers` marks a recipient unavailable after 60 seconds
without delivery acknowledgement or heartbeat; messages and membership remain.
Reconnect sends a single Hello transport notice, without launching a model.
