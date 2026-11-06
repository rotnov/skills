"""Recovery authority for one previously enabled native conversation; never a new owner grant."""
import math
import os
from pathlib import Path
import time
import uuid

# The parent's readiness deadline for an explicit start; a run's unpublished startup authority ends with it.
STARTUP_SECONDS = 90


def binding(record):
    return {key:record.get(key) for key in
            ('engine', 'session_ref', 'agent_id', 'device_id', 'quorum_id', 'project_ref', 'cwd', 'role', 'quorum')}


def enable(ledger, record):
    ledger.setting('session-reception', {'enabled':True, 'binding':binding(record), 'expires_at':None})


def require_enabled(ledger, record):
    grant = ledger.setting('session-reception') or {}
    current = ledger.setting('session-agent')
    if (not isinstance(grant, dict) or not isinstance(record, dict) or not isinstance(current, dict)
            or binding(current) != binding(record)):
        raise ValueError('session_recovery_denied')
    expires = grant.get('expires_at')
    valid_expiry = (expires is None or (type(expires) in (int, float) and math.isfinite(expires)
                                        and expires > time.time()))
    if (grant.get('enabled') is not True or grant.get('binding') != binding(record) or not valid_expiry):
        raise ValueError('session_recovery_denied')


def startup_authority(ledger, record, run_id, *, clock=time.monotonic):
    """Guard for an explicit-start run: bounded startup authority, then only the grant its parent publishes.

    Until the parent replaces the grant observed at launch, the run may work for at most
    STARTUP_SECONDS on that start's own authority. An owner stop of this run, a changed
    binding, or any later grant change ends it; it never creates or extends a grant.
    """
    from quorum_listener import Control
    launched, deadline = ledger.setting('session-reception'), clock() + STARTUP_SECONDS
    def authorize():
        current = ledger.setting('session-agent')
        if (Control(ledger).stopping(run_id) or not isinstance(current, dict) or not isinstance(record, dict)
                or binding(current) != binding(record)):
            raise ValueError('session_recovery_denied')
        if ledger.setting('session-reception') == launched and clock() < deadline: return
        require_enabled(ledger, record)
    return authorize


class ReceptionApi:
    """Keep one listener's reception authority around every token resolution and service dispatch."""
    def __init__(self, api, ledger, record, *, authorize=None):
        self.api, self.ledger, self.record = api, ledger, record
        self.guard = authorize

    def authorize(self):
        if self.guard is not None: return self.guard()
        require_enabled(self.ledger, self.record)

    @property
    def config(self):
        return self.api.config

    def token(self):
        self.authorize()
        token = self.api.token()
        self.authorize()
        return token

    def call(self, operation, payload, request_id=None):
        from quorum_api import guarded_call
        self.authorize()
        # This wrapper owns no RPC attempt state. Queue intent/receipt phases
        # stay in Relay; refusal after token refresh must precede HTTP dispatch.
        return guarded_call(self.api, operation, payload, request_id,
                            before_request=self.authorize, on_dispatch=lambda:None)


def hook_environment(event, client, environment):
    """Use only a reviewed provider SessionStart/resume event, never a recent-session guess.

    Codex documents session_id in its hook payload; Grok additionally injects
    GROK_SESSION_ID itself. Neither the payload nor matching exports grant
    reception: recover still requires the previously enabled exact binding.
    """
    if not isinstance(event, dict) or client not in ('codex', 'grok'):
        raise ValueError('session_recovery_denied')
    sid = event.get('session_id') if client == 'codex' else event.get('sessionId')
    try: canonical = str(uuid.UUID(sid))
    except (ValueError, TypeError, AttributeError): raise ValueError('session_recovery_denied') from None
    if (canonical != sid or event.get('hook_event_name') != 'SessionStart' or event.get('source') != 'resume'
            or not isinstance(event.get('cwd'), str) or not Path(event['cwd']).is_absolute()):
        raise ValueError('session_recovery_denied')
    variable = 'CODEX_THREAD_ID' if client == 'codex' else 'GROK_SESSION_ID'
    competing = ('CLAUDE_CODE_SESSION_ID', 'CLAUDE_CODE_HOST_SESSION_ID',
                 'CLAUDE_CODE_MESSAGING_SOCKET', 'CLAUDE_CODE_MESSAGING_TOKEN',
                 'GROK_SESSION_ID' if client == 'codex' else 'CODEX_THREAD_ID')
    if any(environment.get(name) for name in competing) or environment.get(variable) not in (None, '', sid):
        raise ValueError('session_recovery_denied')
    if client == 'grok' and (environment.get(variable) != sid or not environment.get('GROK_LEADER_SOCKET')):
        raise ValueError('session_recovery_denied')
    return {**environment, variable:sid}


WORKER_SCAN_LIMIT = 256
WORKER_NOTICE_LIMIT = 4


def worker_bindings(state, parent):
    """Enabled Codex tool-inbox bindings whose worker belongs to this parent; read-only, bounded, no ledger created."""
    import json
    import sqlite3
    root = Path(state) / 'sessions'
    try: names = sorted(os.listdir(root))[:WORKER_SCAN_LIMIT]
    except OSError: return []
    found = []
    for name in names:
        try: canonical = str(uuid.UUID(name))
        except ValueError: continue
        path = root / name / 'ledger.sqlite'
        if canonical != name or canonical == parent or path.is_symlink() or not path.is_file(): continue
        try:
            connection = sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=1)
            try:
                rows = dict(connection.execute(
                    "select key,data from settings where key in ('session-agent','tool-inbox-control')").fetchall())
            finally: connection.close()
            record = json.loads(rows.get('session-agent') or 'null')
            control = json.loads(rows.get('tool-inbox-control') or 'null')
        except (sqlite3.Error, ValueError, TypeError): continue
        binding = record.get('worker_binding') if isinstance(record, dict) else None
        if (isinstance(binding, dict) and record.get('transport') == 'tool-inbox' and record.get('session_ref') == name
                and binding.get('parent_thread_id') == parent and isinstance(control, dict)
                and control.get('enabled') is True):
            found.append({key: record.get(key) for key in ('session_ref', 'role', 'quorum')})
    return found


def worker_notices(state, parent):
    """Fixed developer-context lines asking the parent to have its own worker rebind; no path, token or peer text."""
    notices = []
    for item in worker_bindings(state, parent)[:WORKER_NOTICE_LIMIT]:
        if not all(isinstance(item.get(key), str) for key in ('session_ref', 'role', 'quorum')): continue
        notices.append(
            'Quorum: this chat resumed with a tool-inbox worker binding (quorum ' + json_text(item['quorum'])
            + ', role ' + json_text(item['role']) + '). A client restart may have ended its worker. Resume or '
            'spawn this chat\'s quorum service worker and have that worker, from its own shell in the bound '
            'working directory, run: uv run --project <skill-dir> --frozen python <skill-dir>/scripts/quorum.py '
            '--state <private-state> session --session-id ' + item['session_ref'] + ' recover --parent-thread-id '
            + parent + ' ; then continue its receives with --session-id ' + item['session_ref'] + '. This keeps '
            'the same agent, role, delegation and queued messages and needs no invitation. Do not run it from '
            'this parent chat (see references/native-inbox.md, Worker rebind).')
    return notices


def json_text(value):
    import json
    return json.dumps(value)


# --- Re-accept versus resume (#130) ------------------------------------------------------------------

class BindingExists(ValueError):
    """This chat already has a live binding in the invitation's quorum: resume it, the invitation stays unused."""
    code = 'binding_exists_resume'

    def __init__(self, found):
        super().__init__(self.code)
        self.found = found

    def diagnostic(self):
        # IDs, fixed labels and a command only; never a token, path secret or peer text.
        return {**self.found, 'invitation': 'unused',
                'guidance': 'This chat already has a live binding in this quorum. Resume it with next_step; the '
                            'invitation was not used and stays valid until it expires, so its owner may revoke '
                            'or reuse it.'}


def _read_rows(path, keys):
    """Read-only settings of an existing ledger; None when absent, linked or unreadable. Never creates one."""
    import json
    import sqlite3
    if path.is_symlink() or not path.is_file(): return None
    try:
        connection = sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=1)
        try:
            marks = ','.join('?' * len(keys))
            rows = connection.execute(f'select key,data from settings where key in ({marks})', keys).fetchall()
        finally: connection.close()
        return {key: json.loads(data) for key, data in rows}
    except (sqlite3.Error, ValueError, TypeError): return None


def _quorum_command(state, *action):
    import sys
    return [sys.executable, str(Path(__file__).with_name('quorum.py')), '--state', str(state), 'session', *action]


def live_binding(state, quorum, *, sid=None, parent=None):
    """This chat's live binding in `quorum`, read-only, before any login or invitation use; None when absent.

    `sid` is this chat's own session ID (parent route and Claude worker slots); `parent` is the Codex parent
    thread a tool-inbox worker belongs to, whose own parent-route binding counts too: a worker runs with no
    session ID of its own. Only enabled reception counts as live: a stopped binding cannot be resumed
    (`session_recovery_denied`), so it never blocks an acceptance.
    """
    state = Path(os.path.abspath(state))
    # Only canonical session UUIDs name a ledger directory; anything else is never a path component.
    sid, parent = (item if _canonical(item) else None for item in (sid, parent))
    for owner in dict.fromkeys(item for item in (sid, parent) if item):
        rows = _read_rows(state / 'sessions' / owner / 'ledger.sqlite', ('session-agent', 'session-reception'))
        record = (rows or {}).get('session-agent')
        if (isinstance(record, dict) and record.get('session_ref') == owner and record.get('quorum') == quorum
                and record.get('transport', 'native') != 'tool-inbox'):
            try: require_enabled_rows(rows, record)
            except ValueError: continue
            found = {'quorum': quorum, 'route': 'parent', 'role': record.get('role'),
                     'agent_id': record.get('agent_id'), 'next_step': _quorum_command(state, 'recover')}
            # A worker cannot recover its parent's listener; the parent chat runs `session recover` itself.
            if owner != sid: found.update(session_ref=owner, run_in='parent_chat')
            return found
    for owner in dict.fromkeys(item for item in (parent, sid) if item):
        for item in worker_bindings(state, owner):
            if item.get('quorum') == quorum:
                return {'quorum': quorum, 'route': 'codex-worker', 'role': item.get('role'),
                        'session_ref': item['session_ref'],
                        'next_step': _quorum_command(state, '--session-id', item['session_ref'], 'recover',
                                                     '--parent-thread-id', owner)}
    if sid is not None and os.environ.get('CLAUDE_CODE_SESSION_ID') == sid:
        from quorum_claude_worker import live_slots
        for item in live_slots(state, sid, quorum):
            return {'quorum': quorum, 'route': 'claude-worker', 'role': item['role'], 'slot_id': item['slot_id'],
                    'agent_id': item['agent_id'],
                    'next_step': _quorum_command(state, 'worker', 'rebind', item['slot_id'])}
    return None


def _canonical(value):
    try: return isinstance(value, str) and str(uuid.UUID(value)) == value
    except ValueError: return False


def require_enabled_rows(rows, record):
    """`require_enabled` over rows already read read-only."""
    grant = rows.get('session-reception') or {}
    expires = grant.get('expires_at') if isinstance(grant, dict) else None
    valid_expiry = (expires is None or (type(expires) in (int, float) and math.isfinite(expires)
                                        and expires > time.time()))
    if (not isinstance(grant, dict) or grant.get('enabled') is not True or grant.get('binding') != binding(record)
            or not valid_expiry):
        raise ValueError('session_recovery_denied')


def refuse_if_bound(state, quorum, *, sid=None, parent=None):
    found = live_binding(state, quorum, sid=sid, parent=parent)
    if found is not None: raise BindingExists(found)


def main(argv=None):
    """Optional owner-installed startup hook; bounded input and fixed failure diagnostics only."""
    import argparse
    import contextlib
    import io
    import json
    import sys
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state', type=Path, required=True)
    parser.add_argument('--client', choices=('codex', 'grok'), required=True)
    args = parser.parse_args(argv)
    try:
        raw = sys.stdin.buffer.read(8193)
        if len(raw) > 8192: raise ValueError('session_recovery_denied')
        event = json.loads(raw)
        environment = hook_environment(event, args.client, os.environ)
        # The trusted provider event is the hook's identity contract. Shell
        # invocations instead use the actual client's unchanged exports.
        os.environ.update(environment)
        os.chdir(event['cwd'])
        # A Codex parent cannot rebind its worker (the worker proves itself), so it only gets a fixed notice.
        notices = worker_notices(args.state, environment['CODEX_THREAD_ID']) if args.client == 'codex' else []
        if notices and not (args.state / 'sessions' / environment['CODEX_THREAD_ID'] / 'ledger.sqlite').is_file():
            print('\n'.join(notices))
            return 0
        from quorum import main as quorum_main
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            result = quorum_main(['--state', str(args.state), 'session', 'recover'])
        if result:
            # Do not print ledger status, paths, environment or provider payloads.
            print('quorum: reception recovery needs attention; inspect session status in this same chat', file=sys.stderr)
        # Plain stdout of a Codex SessionStart hook becomes developer context in the resumed parent chat.
        if notices: print('\n'.join(notices))
        return result
    except (ValueError, OSError, TypeError):
        print('quorum: session_recovery_denied', file=sys.stderr)
        return 3


if __name__ == '__main__': raise SystemExit(main())
