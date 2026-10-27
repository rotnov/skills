"""Explicit continuous Claude listener; no automatic service or login installation."""
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
import uuid
from quorum_os import FileLock, background_flags, open_private, protect
from quorum_release import ReleaseGuard, ReleaseMismatch, installed_release

# A run in these states holds listener.lock for as long as its process lives.
LIVE_STATES = ('starting', 'waiting', 'checking')


def locked(path):
    if not path.exists(): return False
    try:
        with FileLock(path): return False
    except ValueError as error:
        if str(error) == 'worker_already_running': return True
        raise


class Control:
    def __init__(self, ledger, *, holding_control=False):
        # holding_control: the caller already owns listener-control.lock.
        self.ledger, self.holding_control = ledger, holding_control

    def begin(self, run_id, timeout):
        now = time.time()
        self.ledger.setting('listener', {'run_id': run_id, 'state': 'starting', 'handled': 0,
                                        'timeout': timeout, 'started_at': now, 'updated_at': now})

    def mark_lost(self, run_id, *, exit_code=None):
        """Supervisor-side record of a run whose process ended without reporting.

        A killed process cannot log its own death. Persist only fixed fields:
        no exception text, environment, arguments or credentials.
        """
        record = self.ledger.setting('listener') or {}
        if record.get('run_id') != run_id or record.get('state') not in LIVE_STATES: return False
        now = time.time()
        self.ledger.setting('listener', {**record, 'state': 'error', 'code': 'listener_exited',
                                        'previous_state': record['state'], 'lost_at': now, 'updated_at': now})
        entry = {'event': 'listener_exited', 'run_id': run_id, 'previous_state': record['state'],
                 'detected_at': now}
        if isinstance(exit_code, int): entry['exit_code'] = exit_code
        path = self.ledger.root / 'listener.log'
        try:
            fd = open_private(path, os.O_CREAT | os.O_WRONLY | os.O_APPEND)
            try: os.write(fd, (json.dumps(entry) + '\n').encode())
            finally: os.close(fd)
        except (OSError, ValueError): pass  # The ledger record remains authoritative.
        return True

    def diagnose(self, record):
        """Mark a live run lost only when no start/stop transition is in progress."""
        def lost():
            current = self.ledger.setting('listener') or {}
            if (current.get('run_id') == record.get('run_id') and current.get('state') in LIVE_STATES
                    and not locked(self.ledger.root / 'listener.lock')):
                self.mark_lost(current['run_id'])
        if self.holding_control: return lost()
        try:
            with FileLock(self.ledger.root / 'listener-control.lock'): lost()
        except ValueError as error:
            if str(error) != 'worker_already_running': raise

    def status(self):
        record = self.ledger.setting('listener') or {}
        running = locked(self.ledger.root / 'listener.lock')
        if not running and record.get('run_id') and record.get('state') in LIVE_STATES:
            # The lock, not a possibly reused PID, proves the run is alive.
            self.diagnose(record)
            record = self.ledger.setting('listener') or {}
        result = {key: record[key] for key in ('run_id', 'pid', 'state', 'handled', 'code',
                                              'started_at', 'updated_at', 'timeout', 'lost_at') if key in record}
        result.setdefault('state', 'stopped')
        result.setdefault('handled', 0)
        result.update(listener='running' if running else 'offline',
                      poll_seconds=10, mode=self.ledger.setting('listener-mode') or 'continuous_claude')
        waiting = self.ledger.setting('listener-wait')
        if waiting:
            active = result['listener'] == 'running'
            result.update(wait_mode=waiting['mode'] if active else 'stopped',
                          poll_seconds=waiting['poll_seconds'] if active else 0,
                          notification_signals=waiting.get('signals', 0))
            for key in ('last_signal_at', 'last_connected_at', 'last_fallback_at', 'last_wake'):
                if key in waiting: result[key] = waiting[key]
            if waiting.get('code'): result['notification_code'] = waiting['code']
        presence = self.ledger.setting('presence')
        if presence: result['presence'] = presence if result['listener'] == 'running' else {'state':'offline'}
        release = self.ledger.setting('release')
        if release: result['release'] = release
        result['stop_requested'] = bool(record.get('run_id') and self.stopping(record['run_id']))
        return result

    def update(self, run_id, state, handled, *, timeout=60, code=None, release=None):
        old = self.ledger.setting('listener') or {}
        record = {'run_id': run_id, 'pid': os.getpid(), 'state': state, 'handled': handled,
                  'timeout': timeout, 'updated_at': time.time(),
                  'started_at': old.get('started_at', time.time()) if old.get('run_id') == run_id else time.time()}
        if code is not None: record['code'] = code
        records = {'listener':record}
        if release is not None: records['listener-release'] = {**release, 'run_id':run_id}
        # Publish a winning run and its marker together; readers must never see
        # a marker from one generation paired with another run's control record.
        with self.ledger.db:
            self.ledger.db.executemany('insert into settings values (?,?) on conflict(key) do update set data=excluded.data',
                                      [(key,json.dumps(value)) for key,value in records.items()])

    def stopping(self, run_id):
        return self.ledger.setting('listener-stop') == {'run_id': run_id}

    def request_stop(self, run_id=None):
        record = self.ledger.setting('listener') or {}
        selected = run_id or record.get('run_id')
        if selected: self.ledger.setting('listener-stop', {'run_id': selected})
        return self.status()


def error_code(error):
    allowed = {'auth_required', 'service_unavailable', 'service_error', 'invalid_response',
               'subscription_required', 'engine_missing', 'engine_version_mismatch',
               'dedicated_pilot_profile_required', 'scratch_directory_required',
               'pilot_agent_required', 'worker_already_running', 'session_delivery_unknown',
               'session_binding_denied', 'session_not_connected', 'claude_pipe_unavailable',
               'grok_transport_unavailable', 'coordination_unavailable', 'skill_update_required', 'release_manifest_invalid'}
    return str(error) if str(error) in allowed else 'listener_failed'


def run_listener(ledger, make_worker, run_id, *, sleep=time.sleep, stop_event=None, timeout=60, waiter=None):
    from quorum_live import LocalSupervisorLock
    control, handled = Control(ledger), 0
    stop_event = stop_event or threading.Event()
    def stopping(): return stop_event.is_set() or control.stopping(run_id)
    # The shared lock also excludes the original one-message pilot worker.
    with LocalSupervisorLock(ledger.root), FileLock(ledger.root / 'listener.lock'):
        control.update(run_id, 'starting', handled, timeout=timeout, release=installed_release())
        try:
            uncertain = ledger.db.execute("select 1 from attempts where state in ('unknown','launch_intent','running') limit 1").fetchone()
            if stopping():
                control.update(run_id, 'stopped', handled, timeout=timeout)
            elif uncertain:
                control.update(run_id, 'unknown', handled, timeout=timeout, code='uncertain_attempt')
            else:
                worker = make_worker()
                if waiter: waiter.prepare(stopping)
                worker.reconcile()
                control.update(run_id, 'waiting', handled, timeout=timeout)
                while not stopping():
                    if waiter and hasattr(waiter, 'pulse'): waiter.pulse()
                    control.update(run_id, 'checking', handled, timeout=timeout)
                    outcome = worker.run_once()
                    if outcome == 'unknown':
                        control.update(run_id, 'unknown', handled, timeout=timeout, code='uncertain_attempt')
                        break
                    if outcome in ('answered', 'delivered'):
                        handled += 1
                        control.update(run_id, 'waiting', handled, timeout=timeout)
                        continue
                    if outcome != 'idle':
                        control.update(run_id, 'error', handled, timeout=timeout, code='listener_failed')
                        break
                    control.update(run_id, 'waiting', handled, timeout=timeout)
                    if waiter:
                        waiter.wait(stopping)
                        continue
                    for _ in range(10):
                        if stopping(): break
                        sleep(1)
                else:
                    control.update(run_id, 'stopped', handled, timeout=timeout)
        except Exception as error:
            # Do not blindly retry an RPC: even a lost claim response is uncertain.
            control.update(run_id, 'error', handled, timeout=timeout, code=error_code(error))
        finally:
            if waiter: waiter.close()
    return control.status()


def abandon_start(control, run_id, child, *, grace=10):
    """Stop this start's exact unconfirmed child and record an unreported exit."""
    control.request_stop(run_id)
    deadline = time.monotonic() + grace
    while child.poll() is None and time.monotonic() < deadline: time.sleep(.1)
    if child.poll() is None:
        # Bounded cleanup of a child that ignored the stop request; this is the
        # owned Popen handle, never a PID looked up later.
        signal_owned(child, child.terminate)
        try: child.wait(timeout=5)
        except subprocess.TimeoutExpired:
            signal_owned(child, child.kill)
            child.wait(timeout=5)
    control.mark_lost(run_id, exit_code=child.poll())


def signal_owned(child, send):
    """Signal the owned child; tolerate only its own exit after the last poll."""
    try: send()
    except OSError:
        if child.poll() is None: raise


def spawn_background(command, ledger):
    """Start a console-free child that appends only to the private listener.log."""
    path = ledger.root / 'listener.log'
    fd = open_private(path, os.O_CREAT | os.O_WRONLY | os.O_APPEND)
    try:
        protect(path)
        return subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=fd, stderr=fd,
                                cwd=str(ledger.root), close_fds=True, **background_flags())
    finally: os.close(fd)


def start_listener(ledger, toolchain, timeout, approved):
    from quorum_acp import ExecutionPolicy
    from quorum_live import supervisor_running
    from quorum_api import QuorumError
    ExecutionPolicy(1, timeout, 'subscription', True, approved).validate()
    control = Control(ledger)
    with FileLock(ledger.root / 'listener-control.lock'):
        from quorum_live import api_for
        api, _, _ = api_for(ledger)
        release = ReleaseGuard(api, ledger)
        release.check()
        release.running()
        if locked(ledger.root / 'listener.lock'): return control.status()
        if supervisor_running(ledger.root): raise ValueError('worker_already_running')
        run_id = str(uuid.uuid4())
        command = [sys.executable, str(Path(__file__).with_name('quorum.py')), '--state', str(ledger.root),
                   'listen', 'run', '--toolchain', str(Path(toolchain).resolve()),
                   '--approve-subscription', '--timeout', str(timeout), '--run-id', run_id]
        # Publish before spawn so a concurrent stop can target this exact run.
        control.begin(run_id, timeout)
        try:
            process = spawn_background(command, ledger)
        except Exception as error:
            control.update(run_id, 'error', 0, timeout=timeout, code=error_code(error))
            raise QuorumError('listener_start_failed') from None
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            status = control.status()
            if status.get('run_id') == run_id:
                if (not status['stop_requested'] and status['listener'] == 'running'
                        and status['state'] in ('waiting', 'checking')):
                    return status
                if status['state'] in ('error', 'unknown', 'stopped'): break
            if process.poll() is not None: break
            time.sleep(.1)
        abandon_start(control, run_id, process)
        diagnostic = ledger.setting('release') or {}
        if diagnostic.get('update_required'):
            control.update(run_id, 'error', 0, timeout=timeout, code='skill_update_required')
            raise ReleaseMismatch(diagnostic)
        raise QuorumError('listener_start_failed')


def stop_listener(ledger, *, revoke_reception=False):
    from quorum_api import QuorumError
    deadline = time.monotonic() + 120
    while True:
        guard = FileLock(ledger.root / 'listener-control.lock')
        try:
            guard.__enter__()
        except ValueError as error:
            if str(error) != 'worker_already_running': raise
            if time.monotonic() >= deadline: raise QuorumError('listener_stop_failed') from None
            time.sleep(.1)
            continue
        try:
            if revoke_reception:
                # Atomic with start/recovery under listener-control.lock. An owner
                # stop wins even if the listener itself has already exited.
                ledger.setting('session-reception', {'enabled':False})
            return Control(ledger, holding_control=True).request_stop()
        finally:
            guard.__exit__()


def add_commands(commands):
    listener = commands.add_parser('listen', help='Explicit continuous background Claude listener')
    actions = listener.add_subparsers(dest='action', required=True)
    for name in ('start', 'run'):
        command = actions.add_parser(name)
        command.add_argument('--toolchain', type=Path, required=True)
        command.add_argument('--approve-subscription', action='store_true', required=True)
        command.add_argument('--timeout', type=int, choices=range(10, 61), default=60)
        if name == 'run': command.add_argument('--run-id', type=lambda value: str(uuid.UUID(value)), required=True)
    actions.add_parser('status')
    actions.add_parser('stop')


def dispatch(args, ledger):
    if args.action == 'status': return Control(ledger).status()
    if args.action == 'stop': return stop_listener(ledger)
    if args.action == 'start': return start_listener(ledger, args.toolchain, args.timeout, args.approve_subscription)
    if args.action == 'run':
        import signal
        from quorum_live import api_for, prepare_worker
        stop_event = threading.Event()
        for name in ('SIGTERM', 'SIGINT', 'SIGBREAK'):
            if hasattr(signal, name): signal.signal(getattr(signal, name), lambda *_: stop_event.set())
        from quorum_acp import ExecutionPolicy
        ExecutionPolicy(1, args.timeout, 'subscription', True, args.approve_subscription).validate()
        api, _, _ = api_for(ledger)
        release = ReleaseGuard(api, ledger)
        release.check()
        release.running()
        def make_worker():
            return prepare_worker(ledger, api, args.toolchain, args.timeout, args.approve_subscription)
        return run_listener(ledger, make_worker, args.run_id, stop_event=stop_event, timeout=args.timeout)
    raise ValueError('unknown_operation')
