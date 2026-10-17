# Optional MCP server for Codex

The shell command from the protocol is the default way for Codex to write to
Claude. Use the MCP server only when the user prefers tools, for example because
the sandbox prompts on every shell send.

Offer this `~/.codex/config.toml` snippet to the user; do not edit the file:

```toml
[mcp_servers.agent_bridge]
command = "uv"
args = ["run", "--no-project", "--python", ">=3.10",
        "/absolute/path/to/scripts/agent_bridge_mcp.py", "--side", "codex", "--pair", "TOPIC"]
# Omit to confirm each call in Codex.
default_tools_approval_mode = "approve"
```

Without `uv`, use `command = "python3"` (3.10+) and drop the four `uv`
arguments. Restart Codex afterwards. Tools: `bridge_pairs`, `bridge_send`,
`bridge_inbox`, `bridge_read`. `--pair` restricts the server to listed topics.

Trade-off: Codex runs MCP servers outside its command sandbox, so no shell
approval is needed, but the server cannot see `CODEX_THREAD_ID`. On this route
the bridge checks only the destination topic, not which Codex thread called it,
because `config.toml` applies to every thread. The shell command checks the
calling thread.

The Claude Code side does not need MCP; it runs the bridge directly.
