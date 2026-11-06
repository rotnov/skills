"""A listener lost while its chat stays open (#135): detection, the turn-end prompt and recovery.

Real listener generations and ledgers; the Grok leader and the service are synthetic. No model runs.
"""
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from quorum_test_support import isolated_profiles
from test_quorum_session import Generation, RequirementsApi, SID, MID, without_client_session

SCRIPTS = Path(__file__).resolve().parents[1] / 'skills/quorum/scripts'
sys.path.insert(0, str(SCRIPTS))


class ListenerLossTests(unittest.TestCase):
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
        self.api, self.generations = RequirementsApi(), []
        self.transport = {'leader_socket':'/synthetic/leader.sock', 'tui_pid':123, 'tui_start':456,
                          'cwd':str(self.root)}

    def record(self, engine='grok', **extra):
        registration = {'engine':engine, 'session_ref':SID, 'project_ref':'project', 'cwd':str(self.root)}
        value = {**registration, 'registration':registration, 'agent_id':'agent', 'quorum_id':'quorum',
                 'role':'grok-macos-test', 'quorum':'team', **extra}
        if engine == 'grok': value['grok_transport'] = self.transport
        self.ledger.setting('session-agent', value)
        return value

    def spawn(self, command, ledger):
        child = Generation(self.ledger.root, command[command.index('--run-id') + 1])
        self.generations.append(child)
        self.addCleanup(child.close)
        return child

    def dispatch(self, action, **extra):
        from quorum_session import dispatch
        with patch.dict(os.environ, {'GROK_SESSION_ID':SID}), patch('pathlib.Path.cwd', return_value=self.root), \
             patch('quorum_live.api_for', return_value=(self.api, None, None)), \
             patch('quorum_grok_leader.binding', return_value=self.transport), \
             patch('quorum_session.spawn_background', side_effect=self.spawn):
            return dispatch(SimpleNamespace(action=action, session_id=SID, **extra), SimpleNamespace(root=self.root),
                            None)

    def restore_kills_listener(self):
        """A Grok session restore ends the background run without any stop or report from it."""
        from quorum_listener import Control
        child = self.generations[-1]
        Control(self.ledger).request_stop(child.run_id)
        child.thread.join(timeout=10)
        self.assertFalse(child.thread.is_alive())
        # The process died while waiting: its last record still says so, and nobody revoked reception.
        record = self.ledger.setting('listener')
        self.ledger.setting('listener', {**record, 'state':'waiting'})
        self.ledger.setting('listener-stop', {'run_id':'00000000-0000-4000-8000-000000000000'})
        return child.run_id

    def stop_hook(self, **change):
        from quorum_resume import turn_end_check
        event = {'hook_event_name':'Stop', 'hookEventName':'stop', 'sessionId':SID, 'cwd':str(self.root),
                 'reason':'end_turn', 'stopHookActive':False, 'backgroundTasks':[], **change}
        return turn_end_check(self.root, event, {'GROK_SESSION_ID':SID})

    @isolated_profiles
    def test_live_listener_is_not_reported_lost(self):
        from quorum_resume import listener_loss
        self.record()
        self.dispatch('start')
        self.assertIsNone(listener_loss(self.ledger))
        self.assertIsNone(self.stop_hook())
        self.assertNotIn('listener_lost', self.dispatch('status'))

    @isolated_profiles
    def test_listener_dead_after_restore_is_detected_once_with_the_exact_recovery_step(self):
        from quorum_resume import listener_loss
        self.record()
        self.dispatch('start')
        lost = self.restore_kills_listener()
        loss = listener_loss(self.ledger)
        self.assertEqual((loss['run_id'], loss['state'], loss['code']), (lost, 'error', 'listener_exited'))
        # Detection is the existing supervisor record: one fixed listener.log line, no secrets.
        log = (self.ledger.root / 'listener.log').read_text()
        self.assertIn('"event": "listener_exited"', log)

        status = self.dispatch('status')
        self.assertEqual(status['listener'], 'offline')
        step = status['listener_lost']['next_step']
        self.assertIn('session recover', step)
        self.assertIn('--state ' + str(self.root), step)
        self.assertIn(str(SCRIPTS / 'quorum.py'), step)
        self.assertIn('"grok-macos-test"', step)

        decision = self.stop_hook()
        self.assertEqual(decision['decision'], 'block')
        self.assertIn('session recover', decision['reason'])
        # Once per lost run: the next turn end, a continuation and the session-end fire stay silent.
        self.assertIsNone(self.stop_hook())
        self.ledger.setting('listener-loss-notice', {'run_id':'other', 'state':'error'})
        self.assertIsNone(self.stop_hook(stopHookActive=True))
        self.assertIsNone(self.stop_hook(reason='shutdown'))
        self.assertEqual(self.stop_hook()['decision'], 'block')

    @isolated_profiles
    def test_recovered_listener_clears_the_loss_and_a_later_loss_notifies_again(self):
        from quorum_resume import listener_loss
        original = self.record()
        started = self.dispatch('start')
        grant = self.ledger.setting('session-reception')
        self.restore_kills_listener()
        self.assertEqual(self.stop_hook()['decision'], 'block')
        recovered = self.dispatch('recover')
        self.assertNotEqual(recovered['run_id'], started['run_id'])
        self.assertEqual(self.ledger.setting('session-agent'), original)
        self.assertEqual(self.ledger.setting('session-reception'), grant)
        self.assertIsNone(listener_loss(self.ledger))
        self.assertIsNone(self.stop_hook())
        self.assertNotIn('listener_lost', self.dispatch('status'))
        # The recovered run dies too: a new run is a new notice.
        self.restore_kills_listener()
        self.assertEqual(self.stop_hook()['decision'], 'block')

    @isolated_profiles
    def test_owner_stop_unbound_tool_inbox_or_starting_runs_are_not_lost(self):
        from quorum_listener import Control
        from quorum_resume import listener_loss
        self.assertIsNone(self.stop_hook())  # No binding: silent, and no ledger is created for another chat.
        self.record()
        self.assertIsNone(listener_loss(self.ledger))  # Never started: no reception grant.
        self.dispatch('start')
        self.dispatch('stop')
        self.generations[-1].thread.join(timeout=10)
        self.assertIsNone(listener_loss(self.ledger))
        self.assertIsNone(self.stop_hook())
        self.record(transport='tool-inbox')
        self.assertIsNone(listener_loss(self.ledger))
        # A start that has published its run but whose child does not hold the lock yet.
        self.record()
        from quorum_resume import enable
        enable(self.ledger, self.ledger.setting('session-agent'))
        with __import__('quorum_os').FileLock(self.ledger.root / 'listener-control.lock'):
            Control(self.ledger).begin('11111111-2222-4333-8444-555555555555', 0)
            self.assertIsNone(listener_loss(self.ledger))

    def test_stop_hook_refuses_another_chat_or_event_and_ignores_session_end(self):
        from quorum_resume import stop_session
        event = {'hook_event_name':'Stop', 'sessionId':SID, 'cwd':str(Path.cwd()), 'reason':'end_turn'}
        env = {'GROK_SESSION_ID':SID}
        self.assertEqual(stop_session(event, env), SID)
        self.assertIsNone(stop_session({**event, 'reason':'channel_closed'}, env))
        # Exports inherited from another client's shell do not silence this read-only prompt.
        self.assertEqual(stop_session(event, {**env, 'CLAUDE_CODE_HOST_SESSION_ID':MID}), SID)
        for change, environment in (({'sessionId':MID}, env), ({'hook_event_name':'SessionStart'}, env),
                                    ({'cwd':'relative'}, env), ({}, {}), ({'sessionId':'recent'}, env)):
            with self.subTest(change=change, environment=environment), \
                 self.assertRaisesRegex(ValueError, 'session_recovery_denied'):
                stop_session({**event, **change}, environment)

    @isolated_profiles
    def test_standalone_stop_hook_prints_one_block_decision_and_fails_open(self):
        self.record()
        self.dispatch('start')
        self.restore_kills_listener()
        script = SCRIPTS / 'quorum_resume.py'
        env = {**without_client_session(), 'GROK_SESSION_ID':SID}
        event = {'hook_event_name':'Stop', 'sessionId':SID, 'cwd':str(self.root), 'reason':'end_turn',
                 'stopHookActive':False, 'lastAssistantMessage':'x' * 32768}
        def run(payload, *extra):
            return subprocess.run([sys.executable, str(script), '--state', str(self.root), '--client', 'grok',
                                   '--event', 'stop', *extra], input=payload, text=True, capture_output=True,
                                  env=env, timeout=90 if os.name == 'nt' else 30)
        first = run(json.dumps(event))
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual(json.loads(first.stdout)['decision'], 'block')
        second = run(json.dumps(event))
        self.assertEqual((second.returncode, second.stdout), (0, ''))
        # Malformed, oversized or foreign payloads exit nonzero but not 2, so Grok ends the turn normally.
        for payload in ('{"broken":', 'x' * 262145, json.dumps({**event, 'sessionId':MID})):
            with self.subTest(length=len(payload)):
                result = run(payload)
                self.assertEqual((result.returncode, result.stdout), (3, ''))
                self.assertNotIn(SID, result.stderr)

    def test_windows_grok_recovery_stays_in_one_background_task(self):
        from quorum_resume import recovery_command
        grok = recovery_command(self.root, 'grok', windows=True)
        self.assertIn('background shell task', grok)
        self.assertIn('session recover ; while ($true) { Start-Sleep 60 }', grok)
        self.assertNotIn('background', recovery_command(self.root, 'codex', windows=True))
        self.assertNotIn('background', recovery_command(self.root, 'grok', windows=False))

    def test_startup_hook_prints_the_grok_turn_end_handler(self):
        from quorum_startup_hook import describe
        value = describe(self.root, 'grok', None)
        stop = value['turn_end_handler']
        self.assertIn('--event stop', stop['command'])
        self.assertEqual(value['configuration']['hooks']['Stop'], [{'hooks':[stop]}])
        self.assertFalse(value['install_supported'])

    def test_peers_name_the_grok_recovery_for_an_unavailable_grok_peer(self):
        from quorum_peer_wake import with_wake_diagnostics
        peers = with_wake_diagnostics([
            {'agent_id':MID, 'engine':'grok', 'availability':'unavailable', 'reason':'heartbeat_timeout'},
            {'agent_id':SID, 'engine':'codex', 'availability':'unavailable', 'reason':'heartbeat_timeout'}])
        self.assertIn('session recover', peers[0]['wake']['next_step'])
        self.assertIn('Grok', peers[0]['wake']['next_step'])
        self.assertNotIn('Grok', peers[1]['wake']['next_step'])


@unittest.skipUnless(os.name == 'posix', 'POSIX file modes')
class GrokBuildChangedTests(unittest.TestCase):
    """A Grok self-update repoints `grok` on PATH while the running TUI keeps its own build."""
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.downloads = Path(temporary.name).resolve() / 'downloads'
        self.downloads.mkdir(mode=0o755)
        for version in ('1.0.60', '1.0.70', '1.0.77'):
            path = self.downloads / ('grok-' + version + '-macos-aarch64')
            path.write_bytes(b'')
            path.chmod(0o755)
        self.argv = ['grok', '--leader', '--leader-socket', '/private/leader.sock', '--resume', SID]

    def verify(self, running, *, installed='1.0.77'):
        from quorum_grok_leader import verify_process
        identity = {'pid_start':5, 'uid':os.geteuid(), 'tty':True, 'argv':self.argv,
                    'executable':str(self.downloads / ('grok-' + running + '-macos-aarch64'))
                    if '/' not in running else running}
        with patch('quorum_grok_leader.process_identity', return_value=identity), \
             patch('quorum_grok_leader.shutil.which',
                   return_value=str(self.downloads / ('grok-' + installed + '-macos-aarch64'))):
            return verify_process(1, 5, '/private/leader.sock', SID, '/project')

    def test_running_supported_build_is_verified_against_itself(self):
        self.assertIsNone(self.verify('1.0.77', installed='1.0.77'))
        self.assertEqual(self.verify('1.0.70'), '1.0.70')

    def test_unsupported_running_build_returns_an_actionable_relaunch_step(self):
        from quorum_grok_leader import GrokBuildChanged
        with self.assertRaises(GrokBuildChanged) as caught:
            self.verify('1.0.60')
        data = caught.exception.diagnostic()
        self.assertEqual((caught.exception.code, data['running_build'], data['installed_build']),
                         ('grok_tui_update_pending', '1.0.60', '1.0.77'))
        self.assertIn('--resume', data['next_step'])
        self.assertNotIn(str(self.downloads), json.dumps(data))

    def test_other_images_and_unsafe_siblings_stay_denied(self):
        other = self.downloads.parent / 'elsewhere'
        other.mkdir()
        (other / 'grok-1.0.70-macos-aarch64').write_bytes(b'')
        for running in ('/fixture/python', str(other / 'grok-1.0.70-macos-aarch64'),
                        str(self.downloads / 'grok-1.0.70-macos-x86_64'), str(self.downloads / 'grok-macos-aarch64')):
            with self.subTest(running=running), self.assertRaisesRegex(ValueError, 'session_binding_denied'):
                self.verify(running)
        image = self.downloads / 'grok-1.0.70-macos-aarch64'
        image.chmod(0o775)
        with self.assertRaisesRegex(ValueError, 'session_binding_denied'): self.verify('1.0.70')
        image.chmod(0o755)
        self.downloads.chmod(0o777)
        try:
            with self.assertRaisesRegex(ValueError, 'session_binding_denied'): self.verify('1.0.70')
        finally: self.downloads.chmod(0o755)
        # A foreign session is still refused before any build diagnosis.
        self.argv = ['grok', '--leader', '--leader-socket', '/private/leader.sock', '--resume', MID]
        with self.assertRaisesRegex(ValueError, 'session_binding_denied'): self.verify('1.0.60')

    def test_sibling_build_must_be_confirmed_by_its_leader(self):
        from quorum_grok_leader import confirm_build
        confirm_build(SimpleNamespace(version='1.0.70'), '1.0.70')
        confirm_build(SimpleNamespace(version='1.0.67'), None)
        with self.assertRaisesRegex(ValueError, 'session_binding_denied'):
            confirm_build(SimpleNamespace(version='1.0.67'), '1.0.70')

    @isolated_profiles
    def test_cli_reports_the_update_pending_code(self):
        from quorum import main
        from quorum_grok_leader import GrokBuildChanged
        output = io.StringIO()
        with tempfile.TemporaryDirectory() as temporary, \
             patch.dict(os.environ, {**without_client_session(), 'GROK_SESSION_ID':SID}, clear=True), \
             patch('quorum_session._dispatch', side_effect=GrokBuildChanged('1.0.60', '1.0.77')), \
             contextlib.redirect_stdout(output):
            code = main(['--state', temporary, 'session', 'recover'])
        result = json.loads(output.getvalue())
        self.assertEqual((code, result['code']), (3, 'grok_tui_update_pending'))
        self.assertEqual(result['data']['running_build'], '1.0.60')


if __name__ == '__main__':
    unittest.main()
