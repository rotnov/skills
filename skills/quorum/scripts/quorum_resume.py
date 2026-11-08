"""Recovery authority for one previously enabled native conversation; never a new owner grant."""
import math
import os
from pathlib import Path
import time
import uuid
from quorum_client_contracts import NEXT_STEPS

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


# Session ledgers read per Codex worker lookup. No parent-to-worker index exists, so candidates are filtered
# by name and file first; more remaining ledgers than this fail closed in the resume check.
WORKER_SCAN_LIMIT = 4096
WORKER_NOTICE_LIMIT = 4


def worker_bindings(state, parent, *, strict=False):
    """Enabled Codex tool-inbox bindings whose worker belongs to this parent; read-only, bounded, no ledger created.

    `strict` (the join's resume check) raises `binding_scan_incomplete` instead of reading only part of the
    candidates; the startup notice stays best-effort.
    """
    import json
    import sqlite3
    root = Path(state) / 'sessions'
    try: names = os.listdir(root)
    except FileNotFoundError: return []
    except OSError:
        if strict: raise BindingScanIncomplete('sessions_unreadable') from None
        return []
    candidates = []
    for name in sorted(names):
        try: canonical = str(uuid.UUID(name))
        except ValueError: continue
        path = root / name / 'ledger.sqlite'
        if canonical != name or canonical == parent or path.is_symlink() or not path.is_file(): continue
        candidates.append((name, path))
    if len(candidates) > WORKER_SCAN_LIMIT:
        if strict: raise BindingScanIncomplete('worker_limit')
        candidates = candidates[:WORKER_SCAN_LIMIT]
    found = []
    for name, path in candidates:
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
            found.append({key: record.get(key) for key in ('session_ref', 'role', 'quorum', 'cwd')})
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


class BindingScanIncomplete(BindingExists):
    """The resume check could not see every candidate binding: refuse acceptance rather than assume none."""
    code = 'binding_scan_incomplete'

    def __init__(self, reason):
        ValueError.__init__(self, self.code)
        self.found = {'reason': reason}

    def diagnostic(self):
        # A fixed reason only; never a path, ID list or count.
        return {**self.found, 'invitation': 'unused',
                'guidance': 'Not every local binding of this chat could be checked, so the invitation was not '
                            'used. Inspect session status (or session worker status) and stop or clean up '
                            'unused bindings in this state, then retry.'}


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


def live_binding(state, quorum, *, sid=None, parent=None, cloud=None):
    """This chat's resumable binding in `quorum`, read-only, before any login or invitation use; None when absent.

    A binding counts exactly when its route's own recovery accepts it locally (only the service's agent check
    is not local), so an acceptance never consumes an invitation that a resume could have avoided:

    - parent route (Codex, Grok, Claude native listener): `session recover` — enabled reception, a recoverable
      engine and, for this chat's own binding, this working directory;
    - Codex tool-inbox worker: `session --session-id <ref> recover --parent-thread-id <parent>` — enabled worker
      reception, that parent and this working directory;
    - Claude worker slot: `session worker rebind` — `quorum_claude_worker.rebindable`, the predicate rebind
      itself uses, plus a hand-over an interrupted attest can still finish;
    - Grok cloud parent: `cloud start` — this conversation's verified cloud binding.

    `sid` is this chat's own session ID; `parent` is the Codex parent thread of a tool-inbox worker (which has
    no session ID of its own), whose own parent binding counts too; `cloud` is a verified cloud conversation.
    """
    from quorum_session import RECOVERABLE
    state = Path(os.path.abspath(state))
    cwd = str(Path.cwd().resolve())
    # Only canonical UUIDs name a ledger directory; anything else is never a path component.
    sid, parent, cloud = (item if _canonical(item) else None for item in (sid, parent, cloud))
    for owner in dict.fromkeys(item for item in (sid, parent) if item):
        rows = _read_rows(state / 'sessions' / owner / 'ledger.sqlite', ('session-agent', 'session-reception'))
        record = (rows or {}).get('session-agent')
        if (not isinstance(record, dict) or record.get('session_ref') != owner or record.get('quorum') != quorum
                or record.get('transport', 'native') == 'tool-inbox' or record.get('engine') not in RECOVERABLE
                # `session recover` runs in the bound directory; a worker cannot see its parent chat's.
                or (owner == sid and record.get('cwd') != cwd)):
            continue
        try: require_enabled_rows(rows, record)
        except ValueError: continue
        found = {'quorum': quorum, 'route': 'parent', 'role': record.get('role'),
                 'agent_id': record.get('agent_id'), 'next_step': _quorum_command(state, 'recover')}
        # A worker cannot recover its parent's listener; the parent chat runs `session recover` itself.
        if owner != sid: found.update(session_ref=owner, run_in='parent_chat')
        return found
    for owner in dict.fromkeys(item for item in (parent, sid) if item):
        for item in worker_bindings(state, owner, strict=True):
            if item.get('quorum') == quorum and item.get('cwd') == cwd:
                return {'quorum': quorum, 'route': 'codex-worker', 'role': item.get('role'),
                        'session_ref': item['session_ref'],
                        'next_step': _quorum_command(state, '--session-id', item['session_ref'], 'recover',
                                                     '--parent-thread-id', owner)}
    if sid is not None and os.environ.get('CLAUDE_CODE_SESSION_ID') == sid:
        from quorum_claude_worker import live_slots
        for item in live_slots(state, sid, quorum):
            found = {'quorum': quorum, 'route': 'claude-worker', 'role': item['role'], 'slot_id': item['slot_id'],
                     'agent_id': item['agent_id'], 'state': item['state']}
            if item['state'] == 'hand_over_pending':
                # Repeat the interrupted attest with the same handle; once it expires, rebind the old slot.
                # The value stays out of the command: only the parent's own spawn receipt holds it, and an
                # incomplete command fails instead of binding a pasted placeholder as a new actor.
                found['next_step'] = _quorum_command(state, 'worker', 'attest', item['slot_id'], '--native-handle')
                found['native_handle_source'] = 'spawn_receipt'
            else: found['next_step'] = _quorum_command(state, 'worker', 'rebind', item['slot_id'])
            return found
    if cloud is not None:
        import sys
        from quorum_grok_cloud import CloudError, verify_record
        rows = _read_rows(state / 'cloud' / cloud / 'ledger.sqlite', ('cloud-agent',))
        record = (rows or {}).get('cloud-agent')
        try:
            if isinstance(record, dict) and record.get('quorum') == quorum:
                verify_record(record, cloud)
                return {'quorum': quorum, 'route': 'grok-cloud', 'role': record.get('role'),
                        'agent_id': record.get('agent_id'), 'next_step': [
                            sys.executable, str(Path(__file__).with_name('quorum.py')), '--state', str(state),
                            'cloud', 'start']}
        except CloudError: pass
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


def refuse_if_bound(state, quorum, *, sid=None, parent=None, cloud=None):
    found = live_binding(state, quorum, sid=sid, parent=parent, cloud=cloud)
    if found is not None: raise BindingExists(found)


# --- Lost listener of a live chat (#135) ----------------------------------------------------------

def listener_loss(ledger):
    """This parent binding's enabled reception with no live listener; None when running, stopped or unbound.

    A client can end the listener while the chat itself stays open (a Grok TUI session restore or
    self-update ends its background tasks; a Windows Grok tool-call job kills its children). Liveness is the
    listener lock, as in `session status`, never a possibly reused PID; reading status also records an
    unreported exit as `listener_exited`. An owner stop revokes reception, so it is never reported as lost.
    """
    from quorum_listener import Control
    from quorum_session import RECOVERABLE
    record = ledger.setting('session-agent')
    if (not isinstance(record, dict) or record.get('engine') not in RECOVERABLE
            or record.get('transport', 'native') != 'native'):
        return None
    try: require_enabled(ledger, record)
    except ValueError: return None
    status = Control(ledger).status()
    # `starting` without a lost mark is a start or recovery still in progress, not a loss.
    if status['listener'] == 'running' or status.get('state') == 'starting': return None
    return {'engine': record['engine'], 'quorum': record.get('quorum'), 'role': record.get('role'),
            'run_id': status.get('run_id'), 'state': status.get('state'), 'code': status.get('code'),
            'reason': status.get('reason'), 'cwd': record.get('cwd')}


def recovery_command(state, engine, *, windows=os.name == 'nt', cwd=None):
    """The exact same-chat recovery command for this installed copy; local paths only, never a secret.

    `cwd`: the bound working directory recovery requires, entered first on POSIX shells.
    """
    import shlex
    import subprocess
    skill = Path(__file__).resolve().parents[1]
    argv = ['uv', 'run', '--project', str(skill), '--frozen', 'python', str(skill / 'scripts' / 'quorum.py'),
            '--state', str(Path(state).resolve()), 'session', 'recover']
    if not windows: return ('cd ' + shlex.quote(cwd) + ' && ' if cwd else '') + shlex.join(argv)
    command = subprocess.list2cmdline(argv)
    if engine != 'grok': return command
    # Grok kills a Windows tool call's children when the call returns: keep one background task alive.
    return ('this as one background shell task that stays alive (block_until_ms: 0): ' + command
            + ' ; while ($true) { Start-Sleep 60 }  and then session status in a normal call')


def claude_restart_notice(loss, state, *, windows=None):
    """A Claude chat whose bound pipe is gone: reception is down, why, and the one command that ends it."""
    windows = os.name == 'nt' if windows is None else windows
    cwd = loss.get('cwd') if isinstance(loss.get('cwd'), str) else None
    command = recovery_command(state, 'claude', windows=windows, cwd=cwd)
    where = 'in the working directory ' + json_text(cwd) + ', ' if windows and cwd else ''
    return ('Quorum reception is down in this chat. Its listener for quorum ' + json_text(loss.get('quorum'))
            + ' (role ' + json_text(loss.get('role')) + ') stopped with claude_pipe_unavailable, so peer messages '
            'are not reaching this chat and peers see it as unavailable. '
            + ('Claude Code restarted this chat under the same session ID and gave it a new messaging socket. '
               if loss.get('reason') == 'client_restarted' else
               'This chat\'s messaging socket is gone, which a Claude Code restart causes. ')
            + 'A listener cannot follow a restart on its own: only the restarted chat holds its new socket\'s token. '
            'Run this one command now from this chat\'s shell tool, ' + where + 'without asking: ' + command
            + ' . It reuses the reception the owner already enabled, adopts this chat\'s current socket and keeps '
            'the same agent, role, delegation and queued messages, with no invitation. Then run session status and '
            'confirm listener: running. If recover returns a code, follow references/resume.md; never start another '
            'session or accept a new invitation for this quorum.')


def loss_notice(loss, state):
    """Fixed fields and guidance for a lost listener; no peer text, token or environment."""
    last = str(loss.get('state')) + (', code ' + str(loss['code']) if loss.get('code') else '')
    if loss.get('engine') == 'claude' and loss.get('code') == 'claude_pipe_unavailable':
        return {'quorum': loss.get('quorum'), 'role': loss.get('role'), 'run_id': loss.get('run_id'),
                'state': loss.get('state'), 'code': loss.get('code'), 'reason': loss.get('reason'),
                'next_step': claude_restart_notice(loss, state)}
    return {'quorum': loss.get('quorum'), 'role': loss.get('role'), 'run_id': loss.get('run_id'),
            'state': loss.get('state'), 'code': loss.get('code'), 'next_step': (
                'Quorum: this chat\'s listener for quorum ' + json_text(loss.get('quorum')) + ' (role '
                + json_text(loss.get('role')) + ') is no longer running (last state ' + last + '), so peer '
                'messages are not reaching this chat and peers see it as unavailable. A client session restore, '
                'self-update or restart can end it while the chat stays open. '
                + ('The bound Grok TUI or its leader was gone for several checks: if the chat was closed, its owner '
                   'first relaunches the same conversation with --resume (references/grok.md). '
                   if loss.get('code') == 'grok_tui_gone' else '')
                + ('A client update changed a delivery contract, so reception stopped on purpose: '
                   + NEXT_STEPS[loss['code']] + ' ' if loss.get('code') in NEXT_STEPS else '')
                + 'From this same chat\'s shell tool, '
                'run ' + recovery_command(state, loss.get('engine')) + ' . It keeps the same agent, role, '
                'delegation and queued messages and needs no invitation. Then run session status and confirm '
                'listener: running. If recover returns a code, follow references/resume.md; never start another '
                'session or accept a new invitation for this quorum.')}


NOTICE_KEY = 'listener-loss-notice'
STOP_PAYLOAD_LIMIT = 262144


def stop_session(event, environment):
    """A Grok turn-end (Stop) hook's own chat; None for the session-end fire.

    The runner's own GROK_SESSION_ID must name the payload's session. Unlike resume, other clients' inherited
    exports are not refused: this check only reads and prompts, and a TUI launched from another client's shell
    is the one that most needs the prompt. Its `session recover` still reports `ambiguous_current_session`.
    """
    if not isinstance(event, dict): raise ValueError('session_recovery_denied')
    sid = event.get('sessionId')
    try: canonical = str(uuid.UUID(sid))
    except (ValueError, TypeError, AttributeError): raise ValueError('session_recovery_denied') from None
    if (canonical != sid or event.get('hook_event_name') != 'Stop'
            or not isinstance(event.get('cwd'), str) or not Path(event['cwd']).is_absolute()):
        raise ValueError('session_recovery_denied')
    if environment.get('GROK_SESSION_ID') != sid: raise ValueError('session_recovery_denied')
    # The session-end fire (`channel_closed`, `shutdown`) is no turn to continue.
    if event.get('reason', 'end_turn') != 'end_turn': return None
    return sid


def turn_end_check(state, event, environment):
    """Grok Stop hook: once per lost run, block the turn end with the exact recovery step; else silent.

    Grok feeds a block reason back to the same chat as a user message, so the chat learns at its next turn
    end that its listener is gone. `stopHookActive` (already continuing after a block) and an already
    reported run stay silent, so the hook never loops. Nothing is recovered, sent or registered here.
    """
    sid = stop_session(event, environment)
    if sid is None or event.get('stopHookActive') is True: return None
    path = Path(state) / 'sessions' / sid / 'ledger.sqlite'
    if path.is_symlink() or not path.is_file(): return None  # No binding: never create a ledger.
    from quorum_state import LocalLedger
    ledger = LocalLedger(Path(os.path.abspath(state)) / 'sessions' / sid)
    try:
        loss = listener_loss(ledger)
        if loss is None: return None
        mark = {'run_id': loss['run_id'], 'state': loss['state']}
        if ledger.setting(NOTICE_KEY) == mark: return None
        ledger.setting(NOTICE_KEY, mark)
        return {'decision': 'block', 'reason': loss_notice(loss, state)['next_step']}
    finally: ledger.db.close()


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
    parser.add_argument('--event', choices=('session-start', 'stop'), default='session-start',
                        help='stop: Grok turn-end check for a lost listener (prints a block decision, never recovers)')
    args = parser.parse_args(argv)
    if args.event == 'stop':
        try:
            if args.client != 'grok': raise ValueError('session_recovery_denied')
            # A Stop payload carries the last assistant message (up to 32 KiB) and task lists; still bounded.
            raw = sys.stdin.buffer.read(STOP_PAYLOAD_LIMIT + 1)
            if len(raw) > STOP_PAYLOAD_LIMIT: raise ValueError('session_recovery_denied')
            decision = turn_end_check(args.state, json.loads(raw), os.environ)
            if decision is not None: print(json.dumps(decision))
            return 0
        except (ValueError, OSError, TypeError):
            # A nonzero exit other than 2 fails open in Grok: the turn ends normally.
            print('quorum: session_recovery_denied', file=sys.stderr)
            return 3
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
