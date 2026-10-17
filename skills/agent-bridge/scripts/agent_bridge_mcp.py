#!/usr/bin/env python3
"""Stdio MCP server exposing the agent bridge as tools (standard library only).

One server instance serves one side: `--side codex` inside Codex, `--side claude`
inside Claude Code. `--pair NAME` (repeatable) restricts the instance to those
pairs; without it every pair is visible. Sending always goes to the paired peer
of the named pair, so a session can only reach the session it was paired with.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import agent_bridge as bridge  # noqa: E402

PROTOCOL_FALLBACK = "2025-06-18"


def tool_definitions(side: str) -> list[dict]:
    peer = "Claude Code" if side == "codex" else "Codex"
    pair_property = {"type": "string", "description": "Pair name from bridge_pairs"}
    send_properties = {
        "pair": pair_property,
        "text": {"type": "string", "description": "Message text"},
        "reply_to": {"type": "string", "description": "Message id being answered"},
    }
    if side == "claude":
        send_properties["steer"] = {"type": "boolean", "description":
                                    "Add the message to Codex's active turn now (daemon-hosted "
                                    "threads only). Use only when the user asked to redirect "
                                    "running work."}
    else:
        send_properties["after_turn"] = {"type": "boolean", "description":
                                         "Deliver after Claude's current turn instead of between "
                                         "its tool calls."}
    return [
        {"name": "bridge_pairs",
         "description": "List bridge pairs. Each pair links exactly one Claude Code session "
                        "with exactly one Codex session.",
         "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False}},
        {"name": "bridge_send",
         "description": f"Send a message to the {peer} session paired with you in this pair. "
                        "Keep it short and point to files by path instead of pasting large "
                        "content. The peer treats it as a message from another agent.",
         "inputSchema": {"type": "object", "properties": send_properties,
                         "required": ["pair", "text"], "additionalProperties": False}},
        {"name": "bridge_inbox",
         "description": "List the messages of a pair (ids, direction, status; no text).",
         "inputSchema": {"type": "object", "properties": {"pair": pair_property},
                         "required": ["pair"], "additionalProperties": False}},
        {"name": "bridge_read",
         "description": "Read the text of one message of a pair.",
         "inputSchema": {"type": "object", "properties": {
             "pair": pair_property, "id": {"type": "string"}},
             "required": ["pair", "id"], "additionalProperties": False}},
    ]


class Server:
    def __init__(self, side: str, pairs: list[str]):
        self.side, self.allowed = side, set(pairs)
        self.peer = "claude" if side == "codex" else "codex"

    def _pair(self, name: str) -> str:
        if self.allowed and name not in self.allowed:
            raise bridge.BridgeError(f"pair {name!r} is not enabled for this server")
        bridge.load_pair(name)
        return name

    def call(self, tool: str, arguments: dict):
        if tool == "bridge_pairs":
            # The caller identity flag tells whether sender checks can bind to a thread id.
            identity = bool(os.environ.get("CODEX_THREAD_ID") or os.environ.get("CODEX_SESSION_ID")
                            or os.environ.get("CLAUDE_CODE_SESSION_ID"))
            return {"side": self.side, "caller_identity_visible": identity,
                    "pairs": [{"name": item["name"],
                               "peer_session": item[self.peer]["session_id"],
                               "created": item["created"]}
                              for item in bridge.all_pairs()
                              if not self.allowed or item["name"] in self.allowed]}
        if tool == "bridge_send":
            name = self._pair(arguments["pair"])
            return bridge.send(name, self.peer, arguments["text"], arguments.get("reply_to"),
                               wait=False, steer=bool(arguments.get("steer")),
                               priority="later" if arguments.get("after_turn") else "next",
                               require_identity=False)  # no session identity reaches MCP
        if tool == "bridge_inbox":
            name = self._pair(arguments["pair"])
            mail = bridge.pair_dir(name) / "mail"
            return [{"id": item["id"], "box": path.parent.name, "from": item["from"],
                     "kind": item["kind"], "created": item["created"],
                     "reply_to": item.get("reply_to")}
                    for path in sorted(mail.rglob("*.json"))
                    for item in [bridge.read_json(path)]]
        if tool == "bridge_read":
            name = self._pair(arguments["pair"])
            return bridge.find_message(name, arguments["id"])
        raise bridge.BridgeError(f"unknown tool {tool!r}")

    def handle(self, request) -> dict | None:
        if not isinstance(request, dict):
            return {"jsonrpc": "2.0", "id": None,
                    "error": {"code": -32600, "message": "a request must be a JSON object"}}
        method, identifier = request.get("method"), request.get("id")
        if identifier is None:
            return None  # notification, e.g. notifications/initialized
        params = {} if request.get("params") is None else request["params"]
        arguments = None
        if isinstance(params, dict):
            arguments = {} if params.get("arguments") is None else params["arguments"]
        if not isinstance(params, dict) or not isinstance(arguments, dict):
            return {"jsonrpc": "2.0", "id": identifier,
                    "error": {"code": -32602, "message": "params and arguments must be objects"}}
        if method == "initialize":
            result = {"protocolVersion": params.get("protocolVersion", PROTOCOL_FALLBACK),
                      "capabilities": {"tools": {"listChanged": False}},
                      "serverInfo": {"name": "agent-bridge", "version": bridge.VERSION}}
        elif method == "ping":
            result = {}
        elif method == "tools/list":
            result = {"tools": tool_definitions(self.side)}
        elif method == "tools/call":
            try:
                value = self.call(str(params.get("name") or ""), arguments)
                result = {"content": [{"type": "text",
                                       "text": json.dumps(value, indent=2, ensure_ascii=False)}],
                          "isError": False}
            except Exception as error:  # noqa: BLE001 - one bad call must not stop the server
                result = {"content": [{"type": "text", "text": f"agent-bridge: {error}"}],
                          "isError": True}
        else:
            return {"jsonrpc": "2.0", "id": identifier,
                    "error": {"code": -32601, "message": f"method not found: {method}"}}
        return {"jsonrpc": "2.0", "id": identifier, "result": result}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="agent-bridge MCP server (stdio)")
    parser.add_argument("--side", required=True, choices=bridge.SIDES)
    parser.add_argument("--pair", action="append", default=[])
    args = parser.parse_args(argv)
    server = Server(args.side, args.pair)
    for line in sys.stdin:
        if not line.strip():
            continue
        try:
            request = json.loads(line)
        except json.JSONDecodeError:
            response = {"jsonrpc": "2.0", "id": None,
                        "error": {"code": -32700, "message": "parse error"}}
        else:
            response = server.handle(request)
        if response is not None:
            sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
            sys.stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
