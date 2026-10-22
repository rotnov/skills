"""Explicit continuous Claude listener; no automatic service or login installation."""
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
import uuid
from quorum_os import FileLock, open_private, protect
from quorum_release import ReleaseGuard, ReleaseMismatch


def locked(path):
    if not path.exists(): return False
    try:
        with FileLock(path): return False
    except ValueError as error:
        if str(error) == 'worker_already_running': return True
        raise


class Control:
    def __init__(self, ledger): self.ledger = ledger

    def begin(self, run_id, timeout):
        now = time.time()
        self.ledger.setting('listener', {'run_id': run_id, 'state': 'starting', 'handled': 0,
                                        'timeout': timeout, 'started_at': now, 'updated_at': now})

    def status(self):
        record = self.ledger.setting('listener') or {}
        result = {key: record[key] for key in ('run_id', 'pid', 'state', 'handled', 'code',
                                              'started_at', 'updated_at', 'timeout') if key in record}
        result.setdefault('state', 'stopped')
        result.setdefault('handled', 0)
        result.update(listener='running' if locked(self.ledger.root / 'listener.lock') else 'offline',
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

    def update(self, run_id, state, handled, *, timeout=60, code=None):
        old = self.ledger.setting('listener') or {}
        record = {'run_id': run_id, 'pid': os.getpid(), 'state': state, 'handled': handled,
                  'timeout': timeout, 'updated_at': time.time(),
                  'started_at': old.get('started_at', time.time()) if old.get('run_id') == run_id else time.time()}
        if code is not None: record['code'] = code
        self.ledger.setting('listener', record)

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
        control.update(run_id, 'starting', handled, timeout=timeout)
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
        log_path = ledger.root / 'listener.log'
        fd = None
        try:
            fd = open_private(log_path, os.O_CREAT | os.O_WRONLY | os.O_APPEND)
            protect(log_path)
            flags = {'creationflags': subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == 'nt' else {'start_new_session': True}
            process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=fd, stderr=fd,
                                       cwd=str(ledger.root), close_fds=True, **flags)
        except Exception as error:
            control.update(run_id, 'error', 0, timeout=timeout, code=error_code(error))
            raise QuorumError('listener_start_failed') from None
        finally:
            if fd is not None: os.close(fd)
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
        control.request_stop(run_id)
        diagnostic = ledger.setting('release') or {}
        if diagnostic.get('update_required'):
            control.update(run_id, 'error', 0, timeout=timeout, code='skill_update_required')
            raise ReleaseMismatch(diagnostic)
        raise QuorumError('listener_start_failed')


def stop_listener(ledger):
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
            return Control(ledger).request_stop()
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
        ledger.setting('listener-release', {**release.installed, 'run_id':args.run_id})
        def make_worker():
            return prepare_worker(ledger, api, args.toolchain, args.timeout, args.approve_subscription)
        return run_listener(ledger, make_worker, args.run_id, stop_event=stop_event, timeout=args.timeout)
    raise ValueError('unknown_operation')
