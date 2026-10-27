"""Same-conversation recovery uses real listener generations, never provider models."""
import os
import json
from pathlib import Path
import sys
import subprocess
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from quorum_test_support import isolated_profiles
from test_quorum_session import Generation, MID, RequirementsApi, SID, without_client_session

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'skills/quorum/scripts'))


class ResumeTests(unittest.TestCase):
    def setUp(self):
        env = patch.dict(os.environ, without_client_session(), clear=True)
        env.start()
        self.addCleanup(env.stop)
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        from quorum_state import LocalLedger
        self.root = Path(temporary.name).resolve()
        self.ledger = LocalLedger(self.root / 'sessions' / SID)
        self.addCleanup(self.ledger.db.close)
        self.api, self.children, self.live, self.generations = RequirementsApi(), [], [], []
        self.old = {'leader_socket':'/synthetic/old.sock', 'tui_pid':123, 'tui_start':456,
                    'cwd':str(self.root)}
        self.new = {**self.old, 'leader_socket':'/synthetic/new.sock', 'tui_pid':124, 'tui_start':789}

    def record(self, engine):
        registration = {'engine':engine, 'session_ref':SID, 'project_ref':'project', 'cwd':str(self.root)}
        value = {**registration, 'registration':registration, 'agent_id':'agent', 'quorum_id':'quorum',
                 'role':'worker', 'quorum':'team', 'delegation':{'coordinator_id':'coordinator'}}
        if engine == 'grok': value['grok_transport'] = self.old
        self.ledger.setting('session-agent', value)
        return value

    def generation(self, run_id=None):
        child = Generation(self.ledger.root, run_id)
        self.generations.append(child)
        self.addCleanup(child.close)
        return child

    def spawn(self, command, ledger):
        self.children.append([child.thread.is_alive() for child in self.live])
        return self.generation(command[command.index('--run-id') + 1])

    def dispatch(self, action, *, engine='codex', session=SID, transport=None, **extra):
        from quorum_session import dispatch
        env = {('GROK_SESSION_ID' if engine == 'grok' else 'CODEX_THREAD_ID'):session}
        with patch.dict(os.environ, env), patch('pathlib.Path.cwd', return_value=self.root), \
             patch('quorum_live.api_for', return_value=(self.api, None, None)), \
             patch('quorum_grok_leader.binding', return_value=transport or self.new), \
             patch('quorum_session.spawn_background', side_effect=self.spawn):
            return dispatch(SimpleNamespace(action=action, session_id=SID, **extra), SimpleNamespace(root=self.root), None)

    @isolated_profiles
    def test_grok_same_conversation_start_adopts_new_process_then_recovery_is_idempotent(self):
        original = self.record('grok')
        old = self.generation()
        self.live = [old]
        self.ledger.setting('delivery:' + MID, {'state':'submitted'})
        result = self.dispatch('start', engine='grok')
        self.assertFalse(old.thread.is_alive())
        self.assertEqual(self.children, [[False]])
        self.assertEqual(self.ledger.setting('session-agent'), {**original, 'grok_transport':self.new})
        grant = self.ledger.setting('session-reception')
        self.assertTrue(grant['enabled'])
        self.assertEqual(self.dispatch('recover', engine='grok')['run_id'], result['run_id'])
        self.assertEqual(len(self.children), 1)

        self.assertEqual(self.ledger.setting('session-reception'), grant)
        self.assertEqual(self.ledger.setting('delivery:' + MID), {'state':'submitted'})

    @isolated_profiles
    def test_codex_dead_listener_recovers_without_registration_or_new_grant(self):
        original = self.record('codex')
        started = self.dispatch('start')
        grant = self.ledger.setting('session-reception')
        from quorum_listener import Control
        Control(self.ledger).request_stop(started['run_id'])
        # Simulates the process ending without an owner session-stop command.
        for child in self.children: self.assertEqual(child, [])
        self.generations[-1].thread.join(timeout=10)
        self.assertFalse(self.generations[-1].thread.is_alive())
        recovered = self.dispatch('recover')
        self.assertNotEqual(recovered['run_id'], started['run_id'])
        self.assertEqual(self.ledger.setting('session-agent'), original)
        self.assertEqual(self.ledger.setting('session-reception'), grant)

    @isolated_profiles
    def test_recovery_never_creates_a_grant(self):
        self.record('codex')
        with self.assertRaisesRegex(ValueError, 'session_recovery_denied'): self.dispatch('recover')
        self.assertEqual(self.children, [])

    @isolated_profiles
    def test_owner_stop_is_terminal_for_recovery(self):
        self.record('codex')
        self.dispatch('start')
        self.dispatch('stop')
        before = len(self.children)
        with self.assertRaisesRegex(ValueError, 'session_recovery_denied'): self.dispatch('recover')
        self.assertEqual(len(self.children), before)

    @isolated_profiles
    def test_expired_or_changed_binding_grant_is_denied(self):
        self.record('codex')
        self.dispatch('start')
        grant = self.ledger.setting('session-reception')
        for change in ({'expires_at':1}, {'enabled':False}, {'binding':{}}):
            with self.subTest(change=change):
                self.ledger.setting('session-reception', {**grant, **change})
                with self.assertRaisesRegex(ValueError, 'session_recovery_denied'): self.dispatch('recover')
        self.ledger.setting('session-reception', grant)
        record = self.ledger.setting('session-agent')
        for key in ('agent_id', 'device_id', 'quorum_id', 'role', 'project_ref'):
            with self.subTest(key=key):
                self.ledger.setting('session-agent', {**record, key:'changed'})
                with self.assertRaisesRegex(ValueError, 'session_recovery_denied'): self.dispatch('recover')
        self.ledger.setting('session-agent', record)
        self.assertEqual(len(self.children), 1)

    @isolated_profiles
    def test_wrong_session_or_directory_cannot_recover(self):
        self.record('codex')
        self.dispatch('start')
        with self.assertRaisesRegex(ValueError, 'session_binding_denied'): self.dispatch('recover', session=MID)
        record = self.ledger.setting('session-agent')
        self.ledger.setting('session-agent', {**record, 'cwd':str(self.root / 'elsewhere')})
        with self.assertRaisesRegex(ValueError, 'session_binding_denied'): self.dispatch('recover')
        self.assertEqual(len(self.children), 1)

    @isolated_profiles
    def test_revoked_server_binding_never_restarts_or_stops_listener(self):
        self.record('codex')
        self.dispatch('start')
        def revoked(operation, payload):
            if operation == 'session_requirements': return {'protocol_version':2, 'skill_version':'0.3.0'}
            raise ValueError('session_binding_denied')
        self.api.call = revoked
        with self.assertRaisesRegex(ValueError, 'session_binding_denied'): self.dispatch('recover')
        self.assertEqual(len(self.children), 1)
        from quorum_listener import Control
        self.assertFalse(Control(self.ledger).status()['stop_requested'])

    @isolated_profiles
    def test_uncertain_delivery_prevents_grok_rebind_without_replay(self):
        original = self.record('grok')
        self.dispatch('start', engine='grok', transport=self.old)
        self.ledger.setting('delivery:' + MID, {'state':'intent'})
        with self.assertRaisesRegex(ValueError, 'session_delivery_unknown'): self.dispatch('recover', engine='grok')
        self.assertEqual(self.ledger.setting('session-agent'), original)
        self.assertEqual(self.ledger.setting('delivery:' + MID), {'state':'intent'})
        self.assertEqual(len(self.children), 1)

    @isolated_profiles
    def test_delivery_becoming_uncertain_during_old_listener_stop_blocks_rebind(self):
        from quorum_listener import Control
        original = self.record('grok')
        self.dispatch('start', engine='grok', transport=self.old)
        request_stop = Control.request_stop
        def race(control, run_id=None):
            self.ledger.setting('delivery:' + MID, {'state':'intent'})
            return request_stop(control, run_id)
        with patch.object(Control, 'request_stop', race):
            with self.assertRaisesRegex(ValueError, 'session_delivery_unknown'):
                self.dispatch('recover', engine='grok')
        self.assertEqual(self.ledger.setting('session-agent'), original)
        self.assertEqual(len(self.children), 1)

    def finite_grant(self):
        grant = {**self.ledger.setting('session-reception'), 'expires_at':1}
        self.ledger.setting('session-reception', grant)
        return grant

    def offline_generation(self):
        from quorum_listener import Control
        old = self.generations[-1]
        Control(self.ledger).request_stop(old.run_id)
        old.thread.join(timeout=10)
        self.assertFalse(old.thread.is_alive())

    @isolated_profiles
    def test_expiry_during_service_preflight_never_spawns_a_new_listener(self):
        self.record('codex')
        self.dispatch('start')
        self.offline_generation()
        grant, clock = self.finite_grant(), [0]
        call = self.api.call
        def expire(operation, payload):
            if operation == 'session_inbox': clock[0] = 2
            return call(operation, payload)
        self.api.call = expire
        with patch('quorum_resume.time.time', side_effect=lambda:clock[0]):
            with self.assertRaisesRegex(ValueError, 'session_recovery_denied'): self.dispatch('recover')
        self.assertEqual(len(self.children), 1)
        self.assertEqual(self.ledger.setting('session-reception'), grant)

    @isolated_profiles
    def test_expiry_during_grok_shutdown_does_not_publish_new_transport(self):
        from quorum_listener import Control
        original = self.record('grok')
        self.dispatch('start', engine='grok', transport=self.old)
        grant, clock = self.finite_grant(), [0]
        request_stop = Control.request_stop
        def expire(control, run_id=None):
            clock[0] = 2
            return request_stop(control, run_id)
        with patch('quorum_resume.time.time', side_effect=lambda:clock[0]), \
             patch.object(Control, 'request_stop', expire):
            with self.assertRaisesRegex(ValueError, 'session_recovery_denied'):
                self.dispatch('recover', engine='grok')
        self.assertEqual(self.ledger.setting('session-agent'), original)
        self.assertEqual(len(self.children), 1)
        self.assertEqual(self.ledger.setting('session-reception'), grant)

    @isolated_profiles
    def test_expiry_during_child_startup_stops_only_the_unconfirmed_owned_child(self):
        self.record('codex')
        self.dispatch('start')
        self.offline_generation()
        grant, clock = self.finite_grant(), [0]
        spawn = self.spawn
        def expire(command, ledger):
            child = spawn(command, ledger)
            clock[0] = 2
            return child
        self.spawn = expire
        with patch('quorum_resume.time.time', side_effect=lambda:clock[0]):
            with self.assertRaisesRegex(ValueError, 'session_recovery_denied'): self.dispatch('recover')
        self.assertEqual(len(self.children), 2)
        self.assertFalse(self.generations[-1].thread.is_alive())
        self.assertEqual(self.ledger.setting('session-reception'), grant)

    @isolated_profiles
    def test_real_recovery_child_cannot_submit_after_expiry_in_startup_io(self):
        from quorum_listener import Control, run_listener
        from quorum_resume import require_enabled
        from quorum_session import Relay
        from quorum_state import LocalLedger
        record = self.record('codex')
        self.dispatch('start')
        self.offline_generation()
        grant, clock, submitted = self.finite_grant(), [0], []
        class Api:
            def call(self, operation, payload):
                if operation == 'session_requirements': return {'protocol_version':2, 'skill_version':'0.3.0'}
                if operation == 'session_inbox':
                    clock[0] = 2
                    return [{'id':MID, 'recipient_id':'agent'}]
                raise AssertionError(operation)
        def spawn(command, ledger):
            run_id = command[command.index('--run-id') + 1]
            ready = threading.Event()
            def queue(message):
                submitted.append(message['id'])
                ready.set()
                return 'submitted'
            def run():
                own = LocalLedger(ledger.root)
                def make():
                    relay = Relay(Api(), own, record, queue)
                    if '--recovering' in command:
                        relay.authorize = lambda:require_enabled(own, record)
                    return relay
                try: run_listener(own, make, run_id, timeout=0, sleep=lambda _:time.sleep(.01))
                finally:
                    own.db.close()
                    ready.set()
            thread = threading.Thread(target=run, daemon=True)
            thread.start()
            def close():
                if thread.is_alive(): Control(ledger).request_stop(run_id)
                thread.join(timeout=5)
            self.addCleanup(close)
            self.assertTrue(ready.wait(5))
            return SimpleNamespace(poll=lambda:None if thread.is_alive() else 0,
                                   terminate=close, kill=close, wait=lambda **_:close())
        self.spawn = spawn
        with patch('quorum_resume.time.time', side_effect=lambda:clock[0]):
            with self.assertRaisesRegex(ValueError, 'session_recovery_denied'): self.dispatch('recover')
        self.assertEqual(submitted, [])
        self.assertIsNone(self.ledger.setting('delivery:' + MID))
        self.assertEqual(self.ledger.setting('session-reception'), grant)

    @isolated_profiles
    def test_recovery_relay_pins_its_original_binding_against_current_ledger(self):
        from quorum_resume import require_enabled
        from quorum_session import Relay
        record = self.record('codex')
        self.dispatch('start')
        self.ledger.setting('session-agent', {**record, 'agent_id':'changed'})
        relay = Relay(self.api, self.ledger, record, lambda _:self.fail('must not submit'))
        relay.authorize = lambda:require_enabled(self.ledger, record)
        with self.assertRaisesRegex(ValueError, 'session_recovery_denied'): relay.run_once()

    @isolated_profiles
    def test_internal_recovery_run_wires_current_grant_guard_into_real_relay(self):
        self.record('codex')
        self.dispatch('start')
        self.offline_generation()
        self.finite_grant()
        clock, call = [0], self.api.call
        def expire(operation, payload):
            if operation == 'session_inbox': clock[0] = 2
            return call(operation, payload)
        self.api.call = expire
        def run(ledger, make, run_id, **kwargs):
            make().reconcile()
            self.fail('expired recovery must fail inside the child relay')
        with patch('quorum_resume.time.time', side_effect=lambda:clock[0]), \
             patch('signal.signal'), patch('quorum_realtime.RealtimeWait'), patch('quorum_presence.Presence'), \
             patch('quorum_session.run_listener', side_effect=run):
            with self.assertRaisesRegex(ValueError, 'session_recovery_denied'):
                self.dispatch('run', run_id=SID, recovering=True)

    @isolated_profiles
    def test_expiry_while_persisting_intent_removes_only_this_known_unsent_intent(self):
        from quorum_resume import require_enabled
        from quorum_session import Relay
        record = self.record('codex')
        self.dispatch('start')
        self.finite_grant()
        clock, submitted, call = [0], [], self.api.call
        def inbox(operation, payload):
            if operation == 'session_inbox': return [{'id':MID, 'recipient_id':'agent'}]
            return call(operation, payload)
        self.api.call = inbox
        setting = self.ledger.setting
        def persist(key, value=None):
            result = setting(key, value)
            if key == 'delivery:' + MID and value == {'state':'intent'}: clock[0] = 2
            return result
        relay = Relay(self.api, self.ledger, record, lambda message:submitted.append(message) or 'submitted')
        relay.authorize = lambda:require_enabled(self.ledger, record)
        with patch('quorum_resume.time.time', side_effect=lambda:clock[0]), \
             patch.object(self.ledger, 'setting', side_effect=persist):
            with self.assertRaisesRegex(ValueError, 'session_recovery_denied'): relay.run_once()
        self.assertEqual(submitted, [])
        self.assertIsNone(self.ledger.setting('delivery:' + MID))


class HookTests(unittest.TestCase):
    def test_codex_uses_documented_current_hook_uuid_when_no_shell_export_exists(self):
        from quorum_resume import hook_environment
        event = {'hook_event_name':'SessionStart', 'source':'resume', 'session_id':SID, 'cwd':str(Path.cwd())}
        self.assertEqual(hook_environment(event, 'codex', {}), {'CODEX_THREAD_ID':SID})

    def test_grok_requires_its_runner_export_and_explicit_leader_socket(self):
        from quorum_resume import hook_environment
        event = {'hook_event_name':'SessionStart', 'hookEventName':'session_start', 'source':'resume',
                 'sessionId':SID, 'cwd':str(Path.cwd())}
        env = {'GROK_SESSION_ID':SID, 'GROK_LEADER_SOCKET':'/private/leader.sock'}
        self.assertEqual(hook_environment(event, 'grok', env), env)
        for change in ({'GROK_SESSION_ID':MID}, {'GROK_LEADER_SOCKET':''}):
            with self.subTest(change=change), self.assertRaisesRegex(ValueError, 'session_recovery_denied'):
                hook_environment(event, 'grok', {**env, **change})

    def test_hook_refuses_wrong_event_fork_ambiguous_exports_or_uuid(self):
        from quorum_resume import hook_environment
        event = {'hook_event_name':'SessionStart', 'source':'resume', 'session_id':SID, 'cwd':str(Path.cwd())}
        for change in ({'hook_event_name':'SubagentStart'}, {'source':'fork'}, {'source':'startup'},
                       {'session_id':'recent'}, {'cwd':'relative'}):
            with self.subTest(change=change), self.assertRaisesRegex(ValueError, 'session_recovery_denied'):
                hook_environment({**event, **change}, 'codex', {})
        for env in ({'CODEX_THREAD_ID':MID}, {'GROK_SESSION_ID':SID}, {'CLAUDE_CODE_SESSION_ID':SID}):
            with self.subTest(env=env), self.assertRaisesRegex(ValueError, 'session_recovery_denied'):
                hook_environment(event, 'codex', env)

    @isolated_profiles
    def test_standalone_hook_has_bounded_input_and_cannot_register_an_unbound_chat(self):
        script = Path(__file__).resolve().parents[1] / 'skills/quorum/scripts/quorum_resume.py'
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            env = without_client_session()
            env['QUORUM_PROFILE_ROOT'] = str(root / 'profiles')
            event = {'hook_event_name':'SessionStart', 'source':'resume', 'session_id':SID, 'cwd':str(root)}
            for payload in (json.dumps(event), '{"broken":', 'x' * 8193):
                with self.subTest(length=len(payload)):
                    result = subprocess.run([sys.executable, str(script), '--state', str(root / 'state'),
                                             '--client', 'codex'], input=payload, text=True, capture_output=True,
                                            cwd=root, env=env, timeout=90 if os.name == 'nt' else 10)
                    self.assertEqual(result.returncode, 3)
                    self.assertEqual(result.stdout, '')
                    self.assertIn('quorum:', result.stderr)
                    self.assertNotIn(SID, result.stderr)
                    self.assertFalse((root / 'state/config.json').exists())
                    self.assertFalse((root / 'profiles').exists())
