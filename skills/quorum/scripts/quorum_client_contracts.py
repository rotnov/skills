"""The one record of each qualified client ingress contract, and the checks that read it.

These clients update themselves, so a version number says little. Each check reads the shape or behaviour
Quorum depends on, and a newer client whose contract still matches keeps working. Only the Claude fallback
below reads a version, because that pipe never answers. references/sessions.md#client-contracts gives the
evidence for each entry and the steps to qualify a change.

A refusal before any payload byte is `ClientContractRefused`, a proved unsent attempt. A changed answer after
the client may have admitted the prompt is `ClientOutputChanged`, which keeps the attempt unknown. Both carry
a fixed code and reason, so no client output, path or token crosses the CLI boundary.
"""
import json
import os
from pathlib import Path
import re
from quorum_delivery import DeliveryUnknown, DeliveryUnsent

CODEX_QUEUE_CHANGED = 'codex_queue_contract_changed'
CODEX_SHIM_CHANGED = 'codex_shim_contract_changed'
CODEX_STEER_CHANGED = 'codex_steer_contract_changed'
CLAUDE_PIPE_CHANGED = 'claude_pipe_contract_changed'
CODEX_THREAD_UNLOADED = 'codex_thread_unloaded'

_UUID = '[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}'
# `codex queue`: its help must still offer these options, and the argv is built here alone.
QUEUE_OPTIONS = ('--thread', '--message')
# Seen on codex-cli 0.166.0 for Windows, exit 0: one line naming the queued item and the exact thread.
QUEUE_ACCEPTED = re.compile('Queued message (' + _UUID + ') for thread (' + _UUID + r')\.')
# Seen on the same build for a suspended native subagent thread: exit 1, no stdout, and only this stderr line.
# It documents that nothing was queued.
QUEUE_REFUSED = ('Error: failed to queue session message: thread/queue/add failed: direct app-server input is '
                 'not allowed for unloaded spawned sub-agents (code -32600)')
# The last line of a shim written by npm's cmd-shim: "<node>" "%dp0%\<entry point>" %*
SHIM_ENTRY = re.compile(r'"%dp0%\\([^"%\r\n]+\.[cm]?js)"\s+%\*')
# `turn/steer` reads a thread and its turns first. Each enum is the server's complete wire set.
THREAD_STATUSES = ('notLoaded', 'idle', 'systemError', 'active')
TURN_STATUSES = ('completed', 'interrupted', 'failed', 'inProgress')
# JSON-RPC `method not found`: this server has no such route, so the request did nothing.
METHOD_NOT_FOUND = -32601
# The Claude pipe only reads, so no frame can be probed. Each session's registry record declares the
# peerProtocol its peers speak on that pipe; these frames are qualified for the listed values.
CLAUDE_AUTH_FRAME = ('type', 'token')
CLAUDE_USER_FRAME = ('type', 'session_id', 'from', 'msg_id', 'message')
CLAUDE_PEER_PROTOCOLS = (1,)
# First builds with cross-session messaging, per OS family.
CLAUDE_FLOORS = {'posix': (2, 1, 224), 'nt': (2, 1, 234)}
# Exclusive top of the version gate for a build that declares no peerProtocol: every 2.1 build seen declares it.
CLAUDE_UNDECLARED_LIMIT = (2, 2, 0)
RECORD_LIMIT = 65536
RECORD_FILES = 2048
VERSION = re.compile(r'(\d{1,4})\.(\d{1,4})\.(\d{1,6})')
PROBE_SECONDS = 30

_REPORT = ('Refresh this skill, because a newer release may qualify the changed client, or report this code '
           'and reason to the owner. Do not retry by hand, start a headless model or substitute another chat. '
           'Then run session recover from this same chat.')
_UNKNOWN = (' If session status lists this message under unknown_attempts, it may already be in the chat: '
            'follow references/sessions.md#failure-and-recovery before recovering.')
NEXT_STEPS = {
    CODEX_QUEUE_CHANGED: 'This Codex no longer matches the qualified `codex queue` arguments or output, so '
                         'reception stopped. ' + _REPORT + _UNKNOWN,
    CODEX_SHIM_CHANGED: 'This Codex install no longer matches the qualified Windows `.cmd` shim, so its prompt '
                        'cannot bypass cmd.exe and reception stopped. Nothing was sent. ' + _REPORT,
    CODEX_STEER_CHANGED: 'The Codex app server no longer matches the qualified `turn/steer` route, so reception '
                         'stopped. ' + _REPORT + _UNKNOWN,
    CLAUDE_PIPE_CHANGED: 'This Claude Code no longer declares a qualified messaging protocol, or its version '
                         'cannot be checked, so reception stopped. Nothing was sent. ' + _REPORT,
    CODEX_THREAD_UNLOADED: 'Codex refused this message because the bound thread is a suspended native subagent. '
                           'Nothing was queued and the message stays durable. Have the owner resume that '
                           'thread, then run session recover from it.',
}
CONTRACT_CODES = frozenset(NEXT_STEPS)


def _diagnostic(code, reason):
    return {'reason': reason, 'next_step': NEXT_STEPS[code]}


class ClientContractRefused(DeliveryUnsent):
    """A qualified shape did not hold before any payload byte, so the attempt is unsent."""

    def __init__(self, code, reason):
        ValueError.__init__(self, code)
        self.code, self.reason = code, reason

    def diagnostic(self):
        return _diagnostic(self.code, self.reason)


class ClientOutputChanged(DeliveryUnknown):
    """The client ran and answered outside its contract, so it may have admitted the prompt."""

    def __init__(self, code, reason):
        ValueError.__init__(self, code)
        self.code, self.reason = code, reason


def notice(code, reason=None):
    """What `session status` adds for a run that stopped on one of these codes."""
    return {'client_contract': {'code': code, **_diagnostic(code, reason)}} if code in CONTRACT_CODES else {}


def _stream(data):
    return [line.strip() for line in (data or b'').decode('utf-8', 'replace').splitlines() if line.strip()]


def _lines(result):
    return _stream(result.stdout) + _stream(result.stderr)


def queue_command(prefix, thread, text):
    return [*prefix, 'queue', '--thread', thread, '--message', text]


def check_queue(prefix, run):
    """Read `codex queue --help`, which starts no model, and refuse argv this route cannot use."""
    command = [*prefix, 'queue', '--help']
    try: result = run(command, timeout=PROBE_SECONDS)
    except ValueError: raise ClientContractRefused(CODEX_QUEUE_CHANGED, 'queue_help_unavailable') from None
    text = '\n'.join(_lines(result))
    if result.returncode or not all(re.search(r'(?<![\w-])' + option + r'(?![\w-])', text) for option in QUEUE_OPTIONS):
        raise ClientContractRefused(CODEX_QUEUE_CHANGED, 'queue_arguments_changed')


def check_queue_result(result, thread):
    """Accept only one proof line for this thread. The stream is not pinned, and other lines may be update notes."""
    if result.returncode:
        if result.returncode == 1 and not _stream(result.stdout) and _stream(result.stderr) == [QUEUE_REFUSED]:
            raise ClientContractRefused(CODEX_THREAD_UNLOADED, 'thread_unloaded')
        raise ValueError('session_queue_failed')
    proofs = [found for found in map(QUEUE_ACCEPTED.fullmatch, _lines(result)) if found]
    if len(proofs) != 1 or proofs[0][2] != thread:
        raise ClientOutputChanged(CODEX_QUEUE_CHANGED, 'queue_output_changed')


def os_family():
    return 'nt' if os.name == 'nt' else 'posix'


def _claude_home():
    configured = os.environ.get('CLAUDE_CONFIG_DIR')
    return Path(configured) if configured else Path.home() / '.claude'


def _records():
    """Readable session records, bounded. Each is one client process's own `sessions/<pid>.json`."""
    try: entries = [item for item in os.scandir(_claude_home() / 'sessions') if re.fullmatch(r'\d+\.json', item.name)]
    except OSError: return
    for entry in sorted(entries, key=lambda item: item.name)[:RECORD_FILES]:
        try:
            with open(entry.path, 'rb') as stream: raw = stream.read(RECORD_LIMIT + 1)
            record = json.loads(raw) if len(raw) <= RECORD_LIMIT else None
        except (OSError, ValueError): continue
        if isinstance(record, dict): yield record


def _version(value):
    found = VERSION.fullmatch(value) if isinstance(value, str) else None
    return tuple(int(part) for part in found.groups()) if found else None


def _executable_version():
    """The version folder in the exported client path, for a client that wrote no readable record."""
    path = os.environ.get('CLAUDE_CODE_EXECPATH', '')
    versions = [version for version in map(_version, re.split(r'[\\/]', path)) if version]
    return versions[-1] if versions else None


def check_claude_session(session_id, endpoint):
    """Check the client serving this exact pipe, which never answers.

    The record with this pipe, or this session for a build that names no pipe, declares the protocol. A
    declared qualified value passes whatever the version. Where no such value exists, only the version gate
    is left, so a build outside the floor and limit is refused.
    """
    floor = CLAUDE_FLOORS[os_family()]
    records = list(_records())
    own = ([record for record in records if record.get('messagingSocketPath') == endpoint]
           or [record for record in records if record.get('sessionId') == session_id])
    def refuse(reason): raise ClientContractRefused(CLAUDE_PIPE_CHANGED, reason)
    if not own:
        version = _executable_version()
        if version is None: refuse('session_record_missing')
        if not floor <= version < CLAUDE_UNDECLARED_LIMIT: refuse('version_outside_range')
    for record in own:
        protocol, version = record.get('peerProtocol'), _version(record.get('version'))
        if version is not None and version < floor: refuse('version_below_floor')
        if protocol is None:
            if version is None or version >= CLAUDE_UNDECLARED_LIMIT: refuse('version_outside_range')
        elif type(protocol) is not int or protocol not in CLAUDE_PEER_PROTOCOLS:
            refuse('peer_protocol_changed')
