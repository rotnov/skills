"""A native listener cools down on a server-side fatal error instead of exiting (#186). Synthetic only.

The opted-in native route (`retry=True`) treats a non-transient `service_error` and `coordination_unavailable` as
an outage only the service can end: it reports `degraded` with presence `unavailable`, waits 60, 300, 900 and then
3600 seconds, and reconciles before any further I/O. Hard-terminal codes and an owner stop still end the run.
"""
import hang_guard  # noqa: F401
from collections import Counter
from itertools import groupby
from pathlib import Path
import sys
import tempfile
import unittest
import uuid

from quorum_test_support import isolated_profiles

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'skills/quorum/scripts'))

REQUIREMENTS = {'protocol_version':2, 'skill_version':'0.3.0'}


class Ledger:
    def __enter__(self):
        from quorum_state import LocalLedger
        self.temporary = tempfile.TemporaryDirectory()
        self.ledger = LocalLedger(Path(self.temporary.name))
        return self.ledger

    def __exit__(self, *_):
        self.ledger.db.close()
        self.temporary.cleanup()


class ScriptedWorker:
    """Each step is an outcome string or an exception; an empty script stops the run."""
    def __init__(self, control, steps, reconciles=()):
        self.control, self.steps, self.reconciles = control, list(steps), list(reconciles)
        self.reconciled = self.runs = 0

    def reconcile(self):
        self.reconciled += 1
        if self.reconciles:
            step = self.reconciles.pop(0)
            if isinstance(step, BaseException): raise step

    def run_once(self):
        self.runs += 1
        if not self.steps:
            self.control.request_stop()
            return 'idle'
        step = self.steps.pop(0)
        if isinstance(step, BaseException): raise step
        return step


class Waiter:
    """Stands in for RealtimeWait: `prepare` and `pulse` are where the heartbeat, and so a Hello, goes out."""
    stop_aware = True

    def __init__(self, pulses=(), waits=()):
        self.pulses, self.waits = list(pulses), list(waits)
        self.calls = Counter()

    def prepare(self, stopping): self.calls['prepare'] += 1

    def pulse(self, stopping=None):
        self.calls['pulse'] += 1
        if self.pulses:
            step = self.pulses.pop(0)
            if step is not None: raise step

    def wait(self, stopping):
        self.calls['wait'] += 1
        if self.waits:
            step = self.waits.pop(0)
            if step is not None: raise step

    def close(self): self.calls['close'] += 1


def service_error():
    from quorum_api import QuorumError
    return QuorumError('service_error')


def coordination_unavailable():
    from quorum_api import QuorumError
    return QuorumError('coordination_unavailable')


class CooldownTests(unittest.TestCase):
    def run_scripted(self, ledger, steps, *, reconciles=(), waiter=None, sleep=None, retry=True):
        from quorum_listener import Control, run_listener
        worker, made, run_id = ScriptedWorker(Control(ledger), steps, reconciles), [], str(uuid.uuid4())
        def make():
            made.append(1)
            return worker
        sleeps = []
        def record(seconds):
            sleeps.append(seconds)
            if sleep: sleep(seconds)
        result = run_listener(ledger, make, run_id, sleep=record, waiter=waiter, retry=retry)
        return result, worker, sleeps, made, run_id

    def test_repeated_server_errors_then_ok_resume_the_same_run(self):
        with Ledger() as ledger:
            waiter = Waiter(pulses=[None, None, coordination_unavailable()])
            result, worker, sleeps, made, run_id = self.run_scripted(
                ledger, [service_error(), service_error(), 'delivered'], reconciles=[None, service_error()],
                waiter=waiter)
            self.assertEqual((result['state'], result['run_id'], result['handled']), ('stopped', run_id, 1))
            self.assertEqual(made, [1], 'the same worker and its durable ledger carry across the cooldowns')
            # Inbox, reconcile, inbox, heartbeat: four cooldowns, 60 + 300 + 900 + 3600 one-second steps.
            self.assertEqual(len(sleeps), 60 + 300 + 900 + 3600)
            self.assertEqual(set(sleeps), {1}, 'stop-aware one-second steps')
            # Every resumption subscribes and reconciles again before any further inbox call.
            self.assertEqual((waiter.calls['prepare'], worker.reconciled), (5, 5))

    def test_status_is_degraded_unavailable_with_the_code_and_the_cooldown_is_quiet(self):
        from quorum_listener import Control
        for error, code in ((service_error(), 'service_error'), (coordination_unavailable(), 'coordination_unavailable')):
            with self.subTest(code=code), Ledger() as ledger:
                waiter, seen, snapshots = Waiter(), [], set()
                holder = {}
                def sleep(_):
                    status = Control(ledger).status()
                    seen.append((status['state'], status.get('code'), status['listener'],
                                 tuple(sorted(ledger.setting('presence').items()))))
                    snapshots.add((holder['worker'].runs, holder['worker'].reconciled, tuple(sorted(waiter.calls.items()))))
                from quorum_listener import run_listener
                worker = ScriptedWorker(Control(ledger), [error, 'delivered'])
                holder['worker'] = worker
                result = run_listener(ledger, lambda: worker, str(uuid.uuid4()), sleep=sleep, waiter=waiter, retry=True)
                self.assertEqual(set(seen), {('degraded', code, 'running', (('code', code), ('state', 'unavailable')))})
                # No heartbeat (so no Hello), model turn, inbox call or delivery happens while cooling down.
                self.assertEqual(len(snapshots), 1)
                self.assertEqual((result['state'], result['handled']), ('stopped', 1))

    def test_backoff_steps_then_hourly_and_reset_after_a_successful_drain(self):
        with Ledger() as ledger:
            holder = {}
            per_run = Counter()
            def sleep(_): per_run[holder['worker'].runs] += 1
            from quorum_listener import Control, run_listener
            worker = ScriptedWorker(Control(ledger), [service_error()] * 6 + ['idle', coordination_unavailable()])
            holder['worker'] = worker
            result = run_listener(ledger, lambda: worker, str(uuid.uuid4()), sleep=sleep, retry=True)
            # Six cooldowns, the ordinary ten-second idle poll, then a reset first step.
            self.assertEqual([per_run[run] for run in range(1, 9)], [60, 300, 900, 3600, 3600, 3600, 10, 60])
            self.assertEqual(result['state'], 'stopped')

    def test_transient_outage_keeps_its_own_short_backoff_beside_a_cooldown(self):
        from quorum_api import QuorumError
        with Ledger() as ledger:
            holder, per_run = {}, Counter()
            def sleep(_): per_run[holder['worker'].runs] += 1
            from quorum_listener import Control, run_listener
            worker = ScriptedWorker(Control(ledger), [service_error(), QuorumError('service_unavailable'),
                                                      service_error(), QuorumError('service_error', transient=True)])
            holder['worker'] = worker
            run_listener(ledger, lambda: worker, str(uuid.uuid4()), sleep=sleep, retry=True)
            self.assertEqual([per_run[run] for run in range(1, 5)], [60, 10, 300, 20])

    def test_owner_stop_during_cooldown_ends_the_run_at_once(self):
        from quorum_listener import Control, run_listener, stop_listener
        with Ledger() as ledger:
            sleeps = []
            def sleep(seconds):
                sleeps.append(seconds)
                if len(sleeps) == 5: stop_listener(ledger)
            waiter = Waiter()
            worker = ScriptedWorker(Control(ledger), [coordination_unavailable(), 'delivered'])
            result = run_listener(ledger, lambda: worker, str(uuid.uuid4()), sleep=sleep, waiter=waiter, retry=True)
            self.assertEqual((result['state'], result['listener'], len(sleeps)), ('stopped', 'offline', 5))
            self.assertEqual((worker.runs, worker.reconciled, waiter.calls['prepare']), (1, 1, 1),
                             'no recheck, reconcile or delivery after the stop')

    def test_stop_signal_during_cooldown_ends_the_run_at_once(self):
        import threading
        from quorum_listener import Control, run_listener
        with Ledger() as ledger:
            event, sleeps = threading.Event(), []
            def sleep(seconds):
                sleeps.append(seconds)
                event.set()
            worker = ScriptedWorker(Control(ledger), [service_error(), 'delivered'])
            result = run_listener(ledger, lambda: worker, str(uuid.uuid4()), sleep=sleep, stop_event=event, retry=True)
            self.assertEqual((result['state'], len(sleeps), worker.runs), ('stopped', 1, 1))

    def test_cooldown_from_the_idle_wait_rechecks_after_the_backoff(self):
        with Ledger() as ledger:
            waiter = Waiter(waits=[coordination_unavailable()])
            result, worker, sleeps, _, _ = self.run_scripted(ledger, ['idle', 'delivered'], waiter=waiter)
            self.assertEqual((result['state'], result['handled'], len(sleeps)), ('stopped', 1, 60))
            self.assertEqual((waiter.calls['prepare'], worker.reconciled), (2, 2))

    def test_hard_terminal_codes_still_exit_at_once(self):
        from quorum_api import QuorumError
        terminal = (QuorumError('auth_required'), QuorumError('skill_update_required'),
                    QuorumError('release_manifest_invalid'), QuorumError('session_binding_denied'),
                    ValueError('session_binding_denied'), ValueError('session_not_connected'),
                    ValueError('claude_pipe_unavailable'), ValueError('grok_tui_gone'),
                    ValueError('grok_transport_unavailable'), ValueError('session_recovery_denied'),
                    # A request refused by a reviewed name is not a server fault; nor is an untyped look-alike.
                    QuorumError('invalid_payload'), ValueError('service_error'), ValueError('coordination_unavailable'))
        for error in terminal:
            for place in ('inbox', 'pulse', 'wait'):
                with self.subTest(error=repr(error), place=place), Ledger() as ledger:
                    waiter = Waiter(pulses=[error] if place == 'pulse' else (),
                                    waits=[error] if place == 'wait' else ())
                    steps = [error] if place == 'inbox' else ['idle'] if place == 'wait' else []
                    result, worker, sleeps, _, _ = self.run_scripted(ledger, steps, waiter=waiter)
                    self.assertEqual((result['state'], result['code'], sleeps), ('error', str(error), []))
                    self.assertEqual(worker.reconciled, 1)

    def test_default_listener_still_ends_on_a_server_error(self):
        for error in (service_error(), coordination_unavailable()):
            with self.subTest(code=str(error)), Ledger() as ledger:
                result, worker, sleeps, _, _ = self.run_scripted(ledger, [error], retry=False)
                self.assertEqual((result['state'], result['code'], worker.runs, sleeps), ('error', str(error), 1, []))


class RelayCooldownTests(unittest.TestCase):
    """A real Relay under the cooling listener: one pipe write per message, no message lost or duplicated."""
    @isolated_profiles
    def test_server_errors_around_one_message_neither_lose_nor_repeat_it(self):
        from quorum_api import QuorumError
        from quorum_listener import Control, run_listener
        from quorum_session import Relay
        message_id = str(uuid.uuid4())
        with Ledger() as ledger:
            control, pipes, acks, inbox_calls = Control(ledger), [], [], [0]
            record = {'agent_id':'agent', 'session_ref':'session'}
            class Api:
                def call(self, operation, payload, request_id=None):
                    if operation == 'session_requirements': return REQUIREMENTS
                    if operation == 'session_ack':
                        acks.append(payload['message_id'])
                        if len(acks) == 1: raise QuorumError('service_error')
                        return {}
                    inbox_calls[0] += 1
                    if inbox_calls[0] in (1, 2, 4): raise QuorumError('service_error')
                    if acks[1:]:
                        control.request_stop()
                        return []
                    return [{'id':message_id, 'recipient_id':'agent', 'body':'synthetic'}]
            def pipe(message):
                pipes.append(message['id'])
                return 'submitted'
            seen = []
            def sleep(_):
                seen.append((control.status()['state'], (ledger.setting('presence') or {}).get('state'),
                             inbox_calls[0], len(acks)))
                # The bound chat confirms receipt during the cooldown; its own ACK failed.
                if ledger.setting('delivery:' + message_id) == {'state':'submitted'}:
                    ledger.setting('delivery:' + message_id, {'state':'queued'})
            run_id = str(uuid.uuid4())
            relay = Relay(Api(), ledger, record, pipe)
            result = run_listener(ledger, lambda: relay, run_id, sleep=sleep, retry=True)
            self.assertEqual(pipes, [message_id], 'piped exactly once')
            self.assertEqual(acks, [message_id, message_id])
            self.assertEqual(ledger.setting('delivery:' + message_id), {'state':'acked'})
            self.assertEqual((result['state'], result['run_id']), ('stopped', run_id))
            # Cooldowns only; the ordinary idle poll after the drain is not one.
            self.assertEqual({entry[:2] for entry in seen if entry[0] != 'waiting'}, {('degraded', 'unavailable')})
            # Three inbox failures back off 60, 300 and 900 seconds; the drain that piped the message resets the
            # steps, so the failed ACK after it cools down 60 seconds again.
            # Each cooldown follows one failed call, so the inbox and ACK counts tell consecutive ones apart.
            lengths = [len(list(group)) for key, group in groupby((entry[0], entry[2:]) for entry in seen)
                       if key[0] == 'degraded']
            self.assertEqual(lengths, [60, 300, 900, 60])


if __name__ == '__main__':
    unittest.main()
