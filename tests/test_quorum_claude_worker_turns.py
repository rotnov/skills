"""The Claude worker's protocol steps per message and per rearm (#164): each worker command is one model turn.

A wake costs the turns between the event and the end of the worker's turn, so these tests count the commands a
worker runs, using only the commands its own results hand it. A fake API, real ledgers and locks; no model,
client or service.
"""
import hang_guard  # noqa: F401
import json
import time
import unittest
from unittest.mock import patch

from quorum_test_support import isolated_profiles
from test_quorum_claude_worker import (AGENT, M1, M2, OWNER_PROOF, PEER, SID, Api, Bound, claude, message)


class Sleepy:
    """The waiter seam with a short real sleep, so a bounded wait reaches its bound without spinning."""
    busy_seconds = 0
    def __init__(self, on_wait=None): self.on_wait = on_wait
    def prepare(self, stopping): pass
    def pulse(self, stopping=None): pass
    def wait(self, predicate):
        if self.on_wait: self.on_wait()
        time.sleep(0.05)
    def close(self): pass


class Steps(Bound):
    """Runs worker commands exactly as handed out and records each one: one entry is one model turn."""
    def setUp(self):
        super().setUp()
        self.steps = []

    def run_step(self, api, argv, stdin=b'', waiter=None):
        self.steps.append(argv[argv.index('--worker-capability') + 2])
        with patch('quorum_native_inbox.RealtimeWait', lambda *_, **__: waiter or Sleepy()):
            code, result, text = self.cli(api, *argv, stdin=stdin)
        self.assertEqual(text.count('\n'), 1, 'one result line: one Monitor event')
        return code, result

    def wait(self, api, seconds='1', waiter=None):
        return self.run_step(api, ['session', '--worker-capability', str(self.cap), 'wait', '--max-seconds', seconds],
                             waiter=waiter)

    def handed(self, command, *extra):
        """A command from a result, minus the interpreter, script and --state that the CLI harness supplies."""
        self.assertEqual(command[2], '--state')
        return [*command[4:], *extra]


@OWNER_PROOF
class MessageTurnTests(Steps):
    @isolated_profiles
    def test_one_message_is_one_wait_and_one_reply_that_waits_again(self):
        api = Api()
        self.bound(api)
        def arrive():
            if not api.inbox: api.inbox.append(message(M1, body='ping'))
        code, woke = self.wait(api, '5', Sleepy(arrive))
        self.assertEqual((code, woke['data']['state']), (0, 'offer_available'))
        envelope = woke['data']['envelope']
        self.assertEqual(envelope['message']['body'], 'ping', 'the wake itself carries the body: no receive')
        code, done = self.run_step(api, self.handed(envelope['reply_command'], '--max-seconds', '1'), stdin=b'pong')
        self.assertEqual(code, 0, done)
        data = done['data']
        self.assertEqual(data['resolved']['message_id'], M1)
        self.assertEqual((data['state'], data['next_step']), ('rearm', 'wait'), 'the same command waited again')
        self.assertEqual(self.steps, ['wait', 'reply'], 'wake, then one act-and-reply command')
        self.assertEqual([call[0] for call in api.calls].count('session_reply'), 1)
        self.assertNotIn('receive', self.steps)
        self.assertEqual(self.worker_ledger(self.slot).setting('listener')['state'], 'stopped')

    @isolated_profiles
    def test_the_next_message_arrives_in_the_reply_command_so_each_costs_one_command(self):
        api = Api(inbox=[message(M1), message(M2, body='second')])
        self.bound(api)
        _, woke = self.wait(api)
        first = woke['data']['envelope']
        _, second = self.run_step(api, self.handed(first['reply_command']), stdin=b'one')
        self.assertEqual((second['data']['state'], second['data']['envelope']['message_id']), ('offer_available', M2))
        _, third = self.run_step(api, self.handed(second['data']['envelope']['reply_command'], '--max-seconds', '1'),
                                 stdin=b'two')
        self.assertEqual(third['data']['state'], 'rearm')
        self.assertEqual(self.steps, ['wait', 'reply', 'reply'])

    @isolated_profiles
    def test_a_terminal_reply_is_receipted_with_one_command_that_waits_again(self):
        api = Api(inbox=[message(M1, reply_to=M2)])
        self.bound(api)
        _, woke = self.wait(api)
        envelope = woke['data']['envelope']
        _, done = self.run_step(api, self.handed(envelope['receipt_command'], '--max-seconds', '1'))
        self.assertEqual(done['data']['resolved'], {'received': True, 'state': 'acked', 'message_id': M1})
        self.assertEqual(done['data']['state'], 'rearm')
        self.assertEqual(api.acked, [M1], 'the receipt is the worker\'s own command, after it read the body')
        self.assertEqual(self.steps, ['wait', 'received'])

    @isolated_profiles
    def test_a_parked_decision_keeps_reception_in_the_same_command(self):
        api = Api(inbox=[message(M1)])
        self.bound(api)
        _, woke = self.wait(api)
        _, done = self.run_step(api, self.handed(woke['data']['envelope']['park_command'], '--max-seconds', '1'))
        self.assertEqual((done['data']['state'], done['data']['awaiting_owner']), ('rearm', 1))
        self.assertEqual(done['data']['resolved']['message_id'], M1)
        self.assertEqual(self.steps, ['wait', 'park'])

    @isolated_profiles
    def test_a_lost_wake_line_offers_the_same_message_again(self):
        api = Api(inbox=[message(M1)])
        self.bound(api)
        self.wait(api)
        _, again = self.wait(api)
        self.assertEqual(again['data']['envelope']['message_id'], M1)
        self.assertEqual(api.acked, [])


@OWNER_PROOF
class RearmTurnTests(Steps):
    @isolated_profiles
    def test_an_empty_wait_needs_no_command_until_its_bound(self):
        api = Api()
        self.bound(api)
        _, rearm = self.wait(api, '1')
        self.assertEqual(rearm['data'], {'state': 'rearm', 'transport': 'tool-inbox', 'continue': True,
                                         'next_step': 'wait', 'awaiting_owner': 0})
        self.assertEqual(self.steps, ['wait'], 'a rearm is one command')

    @isolated_profiles
    def test_the_default_bound_fits_the_host_limits_and_a_longer_one_is_allowed_for_bash(self):
        import quorum_native_inbox as inbox
        api = Api()
        self.bound(api)
        seen = []
        def fake(api, auth, ledger, record, seconds, **_):
            seen.append(seconds)
            return inbox.wait_result(ledger, record, 'rearm')
        with patch('quorum_native_inbox.wait', fake):
            self.assertEqual(self.worker(api, 'wait')[0], 0)
            self.assertEqual(self.worker(api, 'wait', '--max-seconds', str(inbox.WAIT_BOUND))[0], 0)
        self.assertEqual(seen, [inbox.WAIT_DEFAULT, inbox.WAIT_BOUND])
        self.assertLess(inbox.WAIT_DEFAULT, 1800, 'Monitor and the unattended Bash default stop at 30 minutes')
        self.assertLess(inbox.WAIT_BOUND, 7200, 'Claude Code\'s background Bash maximum is 2 hours')
        from contextlib import redirect_stderr
        from io import StringIO
        with redirect_stderr(StringIO()), self.assertRaises(SystemExit) as refused:
            self.worker(api, 'wait', '--max-seconds', str(inbox.WAIT_BOUND + 1))
        self.assertEqual(refused.exception.code, 2)

    @isolated_profiles
    def test_a_then_wait_bound_counts_from_the_command_start(self):
        import quorum_native_inbox as inbox
        api = Api(inbox=[message(M1)])
        self.bound(api)
        self.offer(api)
        seen = []
        def fake(api, auth, ledger, record, seconds, **_):
            seen.append(seconds)
            return inbox.wait_result(ledger, record, 'rearm')
        clock = iter([100.0, 400.0])
        with patch('quorum_native_inbox.wait', fake), \
                patch('quorum_claude_worker.time.monotonic', lambda: next(clock)):
            code, result, text = self.worker(api, 'received', M1, '--then-wait', '--max-seconds', '1000')
        self.assertEqual(code, 0, text)
        self.assertEqual(seen, [700])


@OWNER_PROOF
class SafetyTests(Steps):
    @isolated_profiles
    def test_a_refused_reply_never_starts_the_wait(self):
        api = Api(inbox=[message(M1, notification=True)])
        self.bound(api)
        _, woke = self.wait(api)
        command = woke['data']['envelope']['receipt_command'][:-3] + ['reply', M1, '--text-stdin', '--then-wait']
        code, result = self.run_step(api, self.handed(command), stdin=b'no')
        self.assertNotEqual(code, 0)
        self.assertEqual(result['code'], 'native_inbox_reply_loop_denied')
        self.assertNotIn('session_reply', [call[0] for call in api.calls])

    @isolated_profiles
    def test_a_running_wait_refuses_the_second_wait_but_never_hides_the_sent_reply(self):
        from quorum_os import FileLock
        api = Api(inbox=[message(M1)])
        self.bound(api)
        _, woke = self.wait(api)
        ledger = self.worker_ledger(self.slot)
        with FileLock(ledger.root / 'listener.lock'):
            code, done = self.run_step(api, self.handed(woke['data']['envelope']['reply_command']), stdin=b'pong')
        self.assertEqual(code, 0)
        data = done['data']
        self.assertEqual((data['state'], data['code'], data['next_step'], data['continue']),
                         ('wait_failed', 'worker_already_running', 'end_turn', True))
        self.assertEqual(data['resolved']['message_id'], M1)
        self.assertEqual([call[0] for call in api.calls].count('session_reply'), 1)

    @isolated_profiles
    def test_an_owner_stop_during_the_then_wait_ends_it_as_owner_stop(self):
        api = Api(inbox=[message(M1)])
        self.bound(api)
        _, woke = self.wait(api)
        stop = self.stopper()
        code, done = self.run_step(api, self.handed(woke['data']['envelope']['receipt_command'], '--max-seconds', '30'),
                                   waiter=Sleepy(stop))
        self.assertEqual(code, 0)
        self.assertEqual((done['data']['state'], done['data']['stop_reason']), ('stopped', 'owner_stop'))
        self.assertEqual(done['data']['resolved']['state'], 'acked')

    @isolated_profiles
    def test_a_stopped_slot_refuses_the_then_wait_command_before_its_step(self):
        api = Api(inbox=[message(M1)])
        self.bound(api)
        _, woke = self.wait(api)
        self.stopper()()
        code, result = self.run_step(api, self.handed(woke['data']['envelope']['receipt_command']))
        self.assertEqual((code, result['code']), (3, 'native_worker_slot_terminal'))
        self.assertEqual(api.acked, [])

    @isolated_profiles
    def test_a_scope_without_receive_cannot_then_wait_and_nothing_is_sent(self):
        api = Api(inbox=[message(M1)])
        self.bound(api, self.accept_scope(operations=['accept', 'connect', 'received', 'reply', 'status', 'stop']))
        mark = len(api.calls)
        # Without --then-wait the same reply reaches its own check (no offer); with it the scope refuses first.
        self.assertEqual(self.worker(api, 'reply', M1, '--text-stdin', stdin=b'pong')[1]['code'],
                         'native_inbox_receipt_not_offered')
        code, result, _ = self.worker(api, 'reply', M1, '--text-stdin', '--then-wait', stdin=b'pong')
        self.assertEqual((code, result['code'], result['data']['reason']),
                         (3, 'native_worker_binding_denied', 'operation_denied'))
        self.assertNotIn('session_reply', [call[0] for call in api.calls[mark:]])

    @isolated_profiles
    def test_only_a_claude_worker_may_then_wait(self):
        import quorum
        from contextlib import redirect_stdout
        from io import StringIO
        for argv in (['received', M1, '--then-wait'], ['park', M1, '--then-wait']):
            with claude(), redirect_stdout(StringIO()) as output:
                code = quorum.main(['--state', str(self.root), 'session', '--session-id', SID, *argv])
            self.assertEqual((code, json.loads(output.getvalue())['code']), (3, 'native_inbox_transport_conflict'))

    @isolated_profiles
    def test_the_wake_line_carries_only_this_actors_envelope_and_no_secret(self):
        api = Api(inbox=[message(M1)])
        self.bound(api)
        _, woke = self.wait(api)
        envelope = woke['data']['envelope']
        self.assertEqual((envelope['message']['recipient_id'], envelope['message']['sender_id']), (AGENT, PEER))
        self.assertNotIn(self.token, json.dumps(woke))
        self.assertNotIn('--session-id', envelope['reply_command'])


if __name__ == '__main__':
    unittest.main()
