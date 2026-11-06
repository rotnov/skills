"""The Claude worker's background wait (#164): real ledgers, locks and processes, a fake API; no model or service."""
from contextlib import redirect_stdout
from io import StringIO
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import textwrap
import time
import unittest
from unittest.mock import patch

from quorum_test_support import isolated_profiles
from test_quorum_claude_worker import (AGENT, M1, M2, OWNER_PROOF, SID, Api, Bound, StubWaiter, actor,
                                       actor_directory, claude, message)

SCRIPTS = Path(__file__).resolve().parents[1] / 'skills/quorum/scripts'
sys.path.insert(0, str(SCRIPTS))
NEW_HANDLE = 'agent_02 quorum after restart'
HELLO = 'Hello! Listener connected: alpha (project team/project). Transport notice; no reply required.'
# One real `wait` process on the worker's own ledger. Its waiter says ready once the consumer locks are held.
WAIT = textwrap.dedent('''
    import json, sys, time
    from pathlib import Path
    sys.path.insert(0, sys.argv[1])
    import quorum_native_inbox as inbox
    from quorum_release import installed_release
    from quorum_state import LocalLedger
    class Api:
        def call(self, operation, payload, request_id=None):
            if operation == 'session_requirements': return dict(installed_release())
            return [] if operation == 'session_inbox' else {}
    class Waiter:
        def prepare(self, stopping): print('ready', flush=True)
        def pulse(self, stopping=None): pass
        def wait(self, stopping): time.sleep(0.05)
        def close(self): pass
    ledger = LocalLedger(Path(sys.argv[2]))
    result = inbox.wait(Api(), ledger, ledger, ledger.setting('session-agent'), 120, waiter=Waiter())
    print(json.dumps(result), flush=True)
''')
# A parent that dies under the wait: it passes the child's lines on through a file.
PARENT = textwrap.dedent('''
    import subprocess, sys, time
    with open(sys.argv[3], 'wb') as output:
        subprocess.Popen([sys.executable, '-c', sys.argv[4], sys.argv[1], sys.argv[2]], stdout=output)
    time.sleep(120)
''')


class Waiter(StubWaiter):
    """A fake clock's idle wait; `on_wait` runs once per pass."""
    def __init__(self, test, step=60, on_wait=None):
        super().__init__()
        self.test, self.step, self.on_wait, self.waits = test, step, on_wait, 0

    def wait(self, predicate):
        self.waits += 1
        self.test.moment += self.step
        self.test.now += self.step
        if self.on_wait: self.on_wait()
        if predicate(): return


class WaitCase(Bound):
    def setUp(self):
        super().setUp()
        self.moment = 0
        wall = patch('quorum_native_inbox.wall', side_effect=lambda: self.now)
        wall.start()
        self.addCleanup(wall.stop)

    def wait_direct(self, api, seconds=300, waiter=None, **seams):
        from quorum_claude_worker import root_ledger
        import quorum_native_inbox as inbox
        ledger, record, check = self.direct('receive', {'wait_seconds': None})
        auth = root_ledger(self.root)
        self.addCleanup(auth.db.close)
        with claude():
            return inbox.wait(api, auth, ledger, record, seconds, waiter=waiter or Waiter(self),
                              presence=type('Quiet', (), {'tick': lambda *_: None})(),
                              clock=lambda: self.moment, sleep=self.pause, authorize=check, **seams), ledger

    def pause(self, seconds):
        self.moment += seconds

    def fixed(self, result, state, step, proceed=True, awaiting=0, **extra):
        self.assertEqual(result, {'state': state, 'transport': 'tool-inbox', 'continue': proceed, 'next_step': step,
                                  'awaiting_owner': awaiting, **extra})


@OWNER_PROOF
class WaitRouteTests(WaitCase):
    @isolated_profiles
    def test_an_empty_wait_exits_only_at_its_bound_with_a_fixed_rearm(self):
        api = Api()
        self.bound(api)
        waiter = Waiter(self)
        result, ledger = self.wait_direct(api, 300, waiter)
        self.fixed(result, 'rearm', 'wait')
        self.assertEqual(waiter.waits, 5)
        self.assertEqual(ledger.setting('listener')['state'], 'stopped')

    @isolated_profiles
    def test_the_real_waiter_keeps_the_heartbeat_and_the_safety_scan_without_realtime(self):
        from quorum_presence import Presence
        from quorum_realtime import RealtimeWait
        from quorum_state import LocalLedger
        import quorum_native_inbox as inbox
        api = Api()
        self.bound(api)
        ledger, record, check = self.direct('receive', {'wait_seconds': None})
        def sleep(seconds):
            self.moment += seconds
            self.now += seconds
        def refused(*_, **__): raise OSError('realtime unavailable')
        presence = Presence(api, record, ledger, clock=lambda: self.moment, persist_epoch=True, wall=lambda: self.now)
        waiter = RealtimeWait(api, record, ledger, connect=refused, clock=lambda: self.moment, sleep=sleep,
                              keepalive=presence, stop_aware=True)
        auth = LocalLedger(self.root)
        self.addCleanup(auth.db.close)
        mark = len(api.calls)
        with claude():
            result = inbox.wait(api, auth, ledger, record, 300, waiter=waiter, presence=presence,
                                clock=lambda: self.moment, sleep=sleep, authorize=check)
        self.fixed(result, 'rearm', 'wait')
        calls = [call[0] for call in api.calls[mark:]]
        self.assertGreaterEqual(calls.count('session_heartbeat'), 14, 'one every 20 seconds')
        self.assertGreaterEqual(calls.count('session_inbox'), 25, 'one safety scan every 10 seconds without Realtime')
        self.assertNotIn('session_ack', calls)

    @isolated_profiles
    def test_an_offer_ends_the_wait_with_no_body_and_no_receipt_and_receive_gets_it(self):
        api = Api()
        self.bound(api)
        def arrive(): api.inbox.append(message(M1, body='private peer body'))
        result, ledger = self.wait_direct(api, waiter=Waiter(self, on_wait=arrive))
        self.fixed(result, 'offer_available', 'receive')
        self.assertNotIn('private peer body', json.dumps(result))
        self.assertEqual(ledger.setting('delivery:' + M1)['state'], 'offered')
        self.assertNotIn('session_ack', api.ops())
        envelope = self.offer(api)
        self.assertEqual((envelope['state'], envelope['message_id']), ('offered', M1))
        self.assertEqual(envelope['next_step'], 'handle_then_wait')

    @isolated_profiles
    def test_an_unreceipted_offer_after_a_crash_ends_the_next_wait_at_once(self):
        api = Api(inbox=[message(M1)])
        self.bound(api)
        self.offer(api)
        waiter = Waiter(self)
        self.fixed(self.wait_direct(api, waiter=waiter)[0], 'offer_available', 'receive')
        self.assertEqual(waiter.waits, 0)
        self.assertNotIn('session_ack', api.ops())

    @isolated_profiles
    def test_a_transport_notice_is_receipted_with_no_wake(self):
        api = Api(inbox=[message(M1, body=HELLO, notification=True)])
        self.bound(api)
        result, _ = self.wait_direct(api, 120)
        self.fixed(result, 'rearm', 'wait')
        self.assertEqual(api.acked, [M1])

    @isolated_profiles
    def test_an_owner_stop_ends_the_wait_and_keeps_an_unreceipted_offer(self):
        api = Api()
        self.bound(api)
        stop = self.stopper()
        waiter = Waiter(self, on_wait=lambda: stop())
        self.fixed(self.wait_direct(api, waiter=waiter)[0], 'stopped', 'report_stop', False, stop_reason='owner_stop')
        self.assertEqual(waiter.waits, 1)
        self.assertEqual(self.worker(api, 'wait', '--max-seconds', '1')[1]['code'], 'native_worker_slot_terminal')

    @isolated_profiles
    def test_a_capability_deadline_is_a_fatal_code_at_the_next_check(self):
        from quorum_native_inbox import NativeInboxError
        api = Api()
        self.bound(api)
        def expire(): self.now += 100000
        with self.assertRaises(NativeInboxError) as raised:
            self.wait_direct(api, waiter=Waiter(self, on_wait=expire))
        self.assertEqual(raised.exception.code, 'native_worker_slot_terminal')
        self.assertEqual(self.worker_ledger(self.slot).setting('listener')['state'], 'error')

    @isolated_profiles
    def test_a_revoked_membership_is_a_fatal_code(self):
        from quorum_api import QuorumError
        api = Api()
        self.bound(api)
        api.revoked = True
        with self.assertRaises(QuorumError) as raised:
            self.wait_direct(api)
        self.assertEqual(str(raised.exception), 'membership_revoked')

    @isolated_profiles
    def test_a_service_outage_is_waited_out_with_no_exit(self):
        api = Api(inbox=[message(M1)])
        self.bound(api)
        api.fail['session_inbox'] = ['service_unavailable', 'service_unavailable', None]
        result, ledger = self.wait_direct(api)
        self.fixed(result, 'offer_available', 'receive')
        self.assertEqual(self.moment, 30, 'the listener\'s retry delays, on the wait\'s own bound')
        self.assertEqual(ledger.setting('presence')['state'], 'unreachable')

    @isolated_profiles
    def test_an_outage_past_the_bound_still_rearms(self):
        api = Api()
        self.bound(api)
        api.fail['session_inbox'] = ['service_unavailable'] * 20
        self.fixed(self.wait_direct(api, 25)[0], 'rearm', 'wait')

    @isolated_profiles
    def test_a_held_offer_keeps_waiting_then_parks_and_reception_goes_on(self):
        api = Api(inbox=[message(M1)])
        self.bound(api)
        self.offer(api)
        code, _, text = self.worker(api, 'hold', M1, '--seconds', '120')
        self.assertEqual(code, 0, text)
        waiter = Waiter(self)
        result, ledger = self.wait_direct(api, 300, waiter)
        self.fixed(result, 'rearm', 'wait', awaiting=1)
        self.assertEqual(waiter.waits, 5, 'a hold wakes nobody')
        self.assertEqual(ledger.setting('delivery:' + M1)['state'], 'parked')
        self.assertEqual(api.acked, [M1])

    @isolated_profiles
    def test_an_elapsed_offer_parks_and_the_next_message_is_announced_with_the_count(self):
        api = Api(inbox=[message(M1), message(M2)])
        self.bound(api)
        self.offer(api)
        self.now += 601
        result, ledger = self.wait_direct(api)
        self.fixed(result, 'offer_available', 'receive', awaiting=1)
        self.assertEqual(ledger.setting('delivery:' + M2)['state'], 'offered')

    @isolated_profiles
    def test_a_material_alert_wakes_and_stays_for_the_next_receive(self):
        from quorum_delivery_status import WATCH_KEY
        api = Api()
        self.bound(api)
        ledger = self.worker_ledger(self.slot)
        ledger.setting(WATCH_KEY, {'agent_id': AGENT, 'seq': 0, 'more': False, 'read_at': None, 'shown': [],
                                   'pending': [{'message_id': M1, 'recipient_role': 'peer', 'status': 'failed',
                                                'noted_at': 1}]})
        self.fixed(self.wait_direct(api)[0], 'alert_available', 'receive')
        result = self.offer(api)
        self.assertEqual(result['state'], 'timeout')
        self.assertEqual(result['delivery_alerts'][0]['status'], 'failed')

    @isolated_profiles
    def test_authority_lost_at_the_last_step_is_refused_before_the_worker_is_told_anything(self):
        from quorum_native_inbox import NativeInboxError
        api = Api(inbox=[message(M1)])
        self.bound(api)
        with self.assertRaises(NativeInboxError) as raised:
            self.wait_direct(api, waiter=StubWaiter(on_close=self.stopper()))
        self.assertEqual(raised.exception.code, 'native_worker_slot_terminal')

    @isolated_profiles
    def test_an_orphaned_wait_exits_and_reports_nothing_as_a_stop_of_the_owner(self):
        api = Api()
        self.bound(api)
        parent = type('Gone', (), {'gone': lambda _: True})()
        self.fixed(self.wait_direct(api, parent=parent)[0], 'orphaned', 'report_stop', False,
                   stop_reason='fatal:wait_orphaned')

    @isolated_profiles
    def test_the_bound_is_enforced_and_the_cli_prints_one_fixed_line(self):
        import quorum_native_inbox as inbox
        api = Api(inbox=[message(M1, body='private peer body')])
        self.bound(api)
        self.assertEqual(inbox.WAIT_BOUND, 1500)
        with self.assertRaises(ValueError) as raised:
            self.wait_direct(api, inbox.WAIT_BOUND + 1)
        self.assertEqual(str(raised.exception), 'invalid_wait_seconds')
        with patch('quorum_native_inbox.RealtimeWait', lambda *_, **__: StubWaiter()):
            code, result, text = self.worker(api, 'wait', '--max-seconds', '5')
        self.assertEqual(code, 0, text)
        self.assertEqual(result['data']['state'], 'offer_available')
        self.assertEqual(text.count('\n'), 1)
        self.assertNotIn('private peer body', text)
        self.assertNotIn(str(self.root), text)

    @isolated_profiles
    def test_a_scope_without_receive_cannot_wait(self):
        api = Api()
        self.bound(api, self.accept_scope(operations=['accept', 'connect', 'status', 'stop']))
        code, result, _ = self.worker(api, 'wait', '--max-seconds', '1')
        self.assertEqual((code, result['code'], result['data']['reason']),
                         (3, 'native_worker_binding_denied', 'operation_denied'))


@OWNER_PROOF
class BriefTests(WaitCase):
    @isolated_profiles
    def test_a_new_worker_is_briefed_to_wait_and_an_older_loop_keeps_working(self):
        import quorum_native_inbox as inbox
        brief = self.prepare(self.accept_scope())['worker_brief']
        self.assertIn('Run session wait', brief)
        self.assertIn('end your turn', brief)
        self.assertIn('Do not repeat session receive', brief)
        self.assertIn('fatal:<code>', brief)
        api = Api(inbox=[message(M1)])
        self.bound(api)
        envelope = self.offer(api)
        self.assertIn(inbox.WAIT_LOOP, envelope['notice'])
        self.assertEqual(envelope['wait_command'][-1], 'wait')
        self.assertEqual(envelope['wait_command'][-3:-1], ['--worker-capability', str(self.cap)])
        self.assertEqual(self.worker(api, 'received', M1)[0], 0)
        result = self.offer(api)
        self.assertEqual((result['state'], result['continue'], result['next_step']), ('timeout', True, 'wait'))
        self.assertEqual(result['receive_command'][-3:], ['receive', '--wait-seconds', '0'])

    def test_the_codex_route_is_unchanged(self):
        import quorum_native_inbox as inbox
        from test_quorum_native_inbox import CHILD, PARENT as CODEX_PARENT
        record = {'engine': 'codex', 'session_ref': CHILD,
                  'worker_binding': {'parent_thread_id': CODEX_PARENT}}
        auth = type('Auth', (), {'root': Path('state')})()
        self.assertEqual(sorted(inbox.loop_fields(auth, record, 'timeout', 30)),
                         ['continue', 'next_step', 'receive_command'])
        self.assertEqual(inbox.loop_fields(auth, record, 'offered', 30)['next_step'], 'handle_then_receive')

    @isolated_profiles
    def test_a_codex_or_parent_binding_has_no_wait(self):
        import quorum
        with claude(), redirect_stdout(StringIO()) as output:
            code = quorum.main(['--state', str(self.root), 'session', '--session-id', SID, 'wait'])
        self.assertEqual((code, json.loads(output.getvalue())['code']), (3, 'native_inbox_transport_conflict'))

    def test_the_capability_report_stays_conditional_and_honest(self):
        import quorum_capabilities as caps
        report = caps.report('claude-worker')
        entry = report['capabilities']['continuous_background_reception']
        self.assertEqual((entry['status'], entry['reason']), ('conditional', 'client_and_worker_must_stay_alive'))
        for phrase in ('session wait', 'not perpetual service', 'not yet observed', 'dead wait shows offline'):
            self.assertIn(phrase, entry['detail'])
        self.assertEqual(report['capabilities']['unattended_project_execution']['reason'], 'live_agents_disabled')
        self.assertEqual(report['live_agents'], 'disabled')


@OWNER_PROOF
class RebindTests(WaitCase):
    def handed_over(self, api):
        code, result, text = self.cli(api, 'session', 'worker', 'rebind', self.slot)
        self.assertEqual(code, 0, text)
        return result['data']['slot_id']

    def publish(self, ledger, run_id, *, wait):
        from quorum_listener import Control
        from quorum_native_inbox import WAIT_RUN
        Control(ledger).update(run_id, 'waiting', 0)
        if wait: ledger.setting(WAIT_RUN, run_id)

    @isolated_profiles
    def test_an_attest_refused_by_a_running_wait_asks_it_to_exit(self):
        from quorum_os import FileLock
        api = Api()
        self.bound(api, self.accept_scope(lifetime='until-owner-stop'))
        successor = self.handed_over(api)
        ledger = self.worker_ledger(self.slot)
        self.publish(ledger, 'run-1', wait=True)
        with FileLock(ledger.root / 'listener.lock'):
            with self.assertRaises(ValueError) as raised:
                self.attest(successor, NEW_HANDLE)
            self.assertEqual(str(raised.exception), 'worker_already_running')
        self.assertEqual(ledger.setting('listener-stop'), {'run_id': 'run-1'})
        self.assertEqual(self.setting(self.slot)['state'], 'active')
        self.attest(successor, NEW_HANDLE)
        self.assertEqual(self.setting(self.slot)['terminal_reason'], 'rebound')

    @isolated_profiles
    def test_a_running_receive_is_left_alone(self):
        from quorum_os import FileLock
        api = Api()
        self.bound(api, self.accept_scope(lifetime='until-owner-stop'))
        successor = self.handed_over(api)
        ledger = self.worker_ledger(self.slot)
        self.publish(ledger, 'run-2', wait=False)
        with FileLock(ledger.root / 'listener.lock'), self.assertRaises(ValueError):
            self.attest(successor, NEW_HANDLE)
        self.assertIsNone(ledger.setting('listener-stop'))


@OWNER_PROOF
class ProcessTests(Bound):
    """A real wait process: both OSes reach this same code, and only the kill differs."""
    def launch(self):
        self.api = Api()
        self.bound(self.api)
        self.ledger_root = self.root / 'sessions' / actor_directory(actor())
        process = subprocess.Popen([sys.executable, '-c', WAIT, str(SCRIPTS), str(self.ledger_root)],
                                   stdout=subprocess.PIPE, text=True)
        self.addCleanup(process.stdout.close)
        self.addCleanup(process.wait)
        self.addCleanup(process.kill)
        self.assertEqual(process.stdout.readline().strip(), 'ready')
        return process

    def session(self):
        code, result, text = self.worker(self.api, 'status')
        self.assertEqual(code, 0, text)
        return result['data']['session']

    def freed(self, seconds=30):
        from quorum_listener import locked
        deadline = time.monotonic() + seconds
        while locked(self.ledger_root / 'listener.lock') and time.monotonic() < deadline: time.sleep(0.05)
        return not locked(self.ledger_root / 'listener.lock')

    @isolated_profiles
    def test_a_second_wait_is_refused_and_a_killed_wait_shows_offline_with_messages_durable(self):
        process = self.launch()
        self.assertEqual(self.session()['listener'], 'running')
        code, result, _ = self.worker(self.api, 'wait', '--max-seconds', '1')
        self.assertEqual((code, result['code']), (3, 'worker_already_running'))
        process.kill()
        process.wait()
        self.assertTrue(self.freed())
        session = self.session()
        self.assertEqual(session['listener'], 'offline')
        # A Windows venv launcher's child can see its parent die and exit by itself instead.
        self.assertIn(session['state'], ('exited', 'stopped') if os.name == 'nt' else ('exited',))
        self.api.inbox.append(message(M1))
        with patch('quorum_native_inbox.RealtimeWait', lambda *_, **__: StubWaiter()):
            code, result, text = self.worker(self.api, 'wait', '--max-seconds', '5')
        self.assertEqual((code, result['data']['state']), (0, 'offer_available'), text)

    @isolated_profiles
    def test_an_owner_stop_reaches_a_running_process_at_once(self):
        process = self.launch()
        self.stopper()()
        self.assertEqual(json.loads(process.stdout.readline())['state'], 'stopped')
        self.assertEqual(process.wait(timeout=30), 0)

    @isolated_profiles
    @unittest.skipIf(os.name == 'nt', 'a POSIX termination signal')
    def test_a_terminated_wait_also_frees_its_lock(self):
        process = self.launch()
        process.send_signal(signal.SIGTERM)
        process.wait(timeout=30)
        self.assertTrue(self.freed())
        self.assertEqual(self.session()['listener'], 'offline')

    @isolated_profiles
    def test_a_wait_whose_parent_died_exits_and_frees_its_lock(self):
        self.api = Api()
        self.bound(self.api)
        self.ledger_root = self.root / 'sessions' / actor_directory(actor())
        output = self.base / 'wait.out'
        parent = subprocess.Popen([sys.executable, '-c', PARENT, str(SCRIPTS), str(self.ledger_root), str(output), WAIT])
        self.addCleanup(parent.wait)
        self.addCleanup(parent.kill)
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline and not (output.exists() and output.read_text().strip()): time.sleep(0.05)
        self.assertEqual(output.read_text().strip(), 'ready')
        parent.kill()
        parent.wait()
        self.assertTrue(self.freed())
        # A Windows venv launcher's job can end the child before it reports; the lock is the contract.
        if os.name != 'nt': self.assertEqual(json.loads(output.read_text().splitlines()[1])['state'], 'orphaned')


@OWNER_PROOF
class ParentWatchTests(unittest.TestCase):
    @unittest.skipIf(os.name == 'nt', 'POSIX reparenting')
    def test_reparenting_is_a_death(self):
        from quorum_native_inbox import ParentWatch
        watch = ParentWatch()
        self.assertFalse(watch.gone())
        with patch('os.getppid', return_value=watch.parent + 1): self.assertTrue(watch.gone())

    def test_a_windows_parent_it_may_not_query_is_never_read_as_dead(self):
        import quorum_debug  # noqa: F401  Imported before os.name changes below.
        from quorum_native_inbox import ParentWatch
        with patch('quorum_native_inbox.os.name', 'nt'), patch('quorum_debug._windows_pid_alive', return_value=False):
            watch = ParentWatch()
            self.assertFalse(watch.watched)
            self.assertFalse(watch.gone())

    def test_a_windows_parent_that_ends_is_a_death(self):
        import quorum_debug  # noqa: F401  Imported before os.name changes below.
        from quorum_native_inbox import ParentWatch
        alive = [True]
        with patch('quorum_native_inbox.os.name', 'nt'), \
                patch('quorum_debug._windows_pid_alive', side_effect=lambda _: alive[0]):
            watch = ParentWatch()
            self.assertFalse(watch.gone())
            alive[0] = False
            self.assertTrue(watch.gone())


if __name__ == '__main__':
    unittest.main()
