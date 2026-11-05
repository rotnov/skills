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
