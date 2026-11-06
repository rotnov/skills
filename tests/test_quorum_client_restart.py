"""Client restart visibility and the opt-in startup hook (#104): synthetic data, temporary files, no client config."""
import contextlib
from datetime import datetime, timezone
import io
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / 'skills/quorum/scripts'
sys.path.insert(0, str(SCRIPTS))

PARENT = '01a15a2b-12f9-73c3-9310-c6b3c7774415'
WORKER = '01a167b0-8caa-7533-ad39-321b0d35f98a'
OTHER_PARENT = '44444444-4444-4444-8444-444444444444'
AGENT = '66666666-6666-4666-8666-666666666666'
SECOND = '77777777-7777-4777-8777-777777777777'
NOW = datetime(2026, 11, 5, 12, 0, tzinfo=timezone.utc)


def peer(agent=AGENT, availability='unavailable', last_seen='2026-11-05T11:50:00+00:00', reason='heartbeat_timeout'):
    return {'agent_id': agent, 'role': 'velvil-author', 'engine': 'codex', 'availability': availability,
            'reason': reason, 'last_seen': last_seen}


def report(agent=AGENT, refreshed='2026-11-05T11:49:30+00:00', active=None, reported=None):
    value = {'agent_id': agent, 'state': 'refreshed', 'refreshed_at': refreshed, 'active_at': active,
             'runtime_report': {'run_id': 'synthetic', 'reported_at': reported} if reported else None}
    return value


class RestartStateTests(unittest.TestCase):
    def test_refresh_then_stale_heartbeat_without_runtime_report_is_restarted_unrecovered(self):
        from quorum_peer_wake import restart_state
        self.assertEqual(restart_state(peer(), report(), now=NOW), 'client_restarted_unrecovered')

    def test_other_evidence_stays_plain(self):
        from quorum_peer_wake import restart_state
        cases = {
            'online': (peer(availability='online'), report()),
            'no_refresh': (peer(), report(refreshed=None)),
            'active_after_refresh': (peer(), report(active='2026-11-05T11:49:40+00:00')),
            'runtime_after_refresh': (peer(), report(reported='2026-11-05T11:49:40+00:00')),
            'heartbeat_stopped_before_refresh': (peer(last_seen='2026-11-05T11:40:00+00:00'), report()),
            'heartbeat_not_stale': (peer(last_seen='2026-11-05T11:59:30+00:00'), report()),
            'no_heartbeat': (peer(last_seen=None), report()),
            'garbage': (peer(last_seen='yesterday'), {'refreshed_at': 7}),
            'no_report': (peer(), None),
        }
        for name, (value, evidence) in cases.items():
            with self.subTest(name=name):
                self.assertIsNone(restart_state(value, evidence, now=NOW))

    def test_peer_view_keeps_server_availability_and_adds_recovery_guidance(self):
        from quorum_peer_wake import with_wake_diagnostics
        plain = with_wake_diagnostics([peer(), peer(SECOND)], {AGENT: report()}, now=NOW)
        self.assertEqual((plain[0]['availability'], plain[0]['server_availability']),
                         ('client_restarted_unrecovered', 'unavailable'))
        self.assertIn('worker rebind', plain[0]['wake']['next_step'])
        self.assertEqual(plain[1]['availability'], 'unavailable')
        self.assertNotIn('server_availability', plain[1])
        # Without cached reports the existing view is unchanged.
        self.assertEqual(with_wake_diagnostics([peer()])[0]['availability'], 'unavailable')


class Api:
    def __init__(self, peers=None, fail=False):
        self.peers, self.fail, self.calls = peers, fail, []

    def call(self, operation, payload, request_id=None):
        from quorum_api import QuorumError
        self.calls.append((operation, payload))
        if self.fail: raise QuorumError('service_unavailable')
        return self.peers


class UpdateStatusVisibilityTests(unittest.TestCase):
    def setUp(self):
        from quorum_state import LocalLedger
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.ledger = LocalLedger(Path(temporary.name) / 'sessions' / PARENT)
        self.addCleanup(self.ledger.db.close)
        self.record = {'agent_id': '88888888-8888-4888-8888-888888888888', 'session_ref': PARENT}
        self.status = {'operation_id': 'op', 'participants': [report(), report(SECOND, refreshed=None)]}

    def test_status_names_the_restart_and_caches_reports_for_session_peers(self):
        from quorum_peer_wake import REPORTS_KEY, with_wake_diagnostics
        from quorum_updates import with_restart_visibility
        api = Api([peer(last_seen='2026-01-01T00:00:00+00:00'), peer(SECOND)])
        # Refresh just before the last heartbeat, both long ago: stale now.
        self.status['participants'][0]['refreshed_at'] = '2025-12-31T23:59:30+00:00'
        checks = []
        result = with_restart_visibility(api, self.ledger, self.record, self.status, lambda: checks.append(1))
        first, second = result['participants']
        self.assertEqual((first['state'], first['availability']), ('refreshed', 'client_restarted_unrecovered'))
        self.assertEqual(second['availability'], 'unavailable')
        self.assertEqual(api.calls, [('session_peers', self.record)])
        self.assertEqual(len(checks), 2)
        cached = self.ledger.setting(REPORTS_KEY)
        self.assertEqual(cached[AGENT]['refreshed_at'], '2025-12-31T23:59:30+00:00')
        self.assertNotIn('run_id', json.dumps(cached))
        later = with_wake_diagnostics([peer(last_seen='2026-01-01T00:00:00+00:00')], cached)
        self.assertEqual(later[0]['availability'], 'client_restarted_unrecovered')

    def test_unavailable_peer_health_returns_the_service_page_unchanged(self):
        from quorum_updates import with_restart_visibility
        self.assertEqual(with_restart_visibility(Api(fail=True), self.ledger, self.record, self.status, lambda: None),
                         self.status)


class StartupHookTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name).resolve()
        self.project = self.base / 'project'
        self.project.mkdir()
        self.state = self.base / 'state'
        self.hooks = self.project / '.codex' / 'hooks.json'

    def run_hook(self, *argv):
        from quorum_startup_hook import main
        with contextlib.redirect_stdout(io.StringIO()) as output:
            code = main(['--state', str(self.state), *argv])
        return code, json.loads(output.getvalue())

    def test_default_prints_documented_configuration_and_writes_nothing(self):
        code, result = self.run_hook('--client', 'codex', '--project-dir', str(self.project))
        self.assertEqual(code, 0)
        data = result['data']
        group = data['configuration']['hooks']['SessionStart'][0]
        self.assertEqual((group['matcher'], group['hooks'][0]['type'], group['hooks'][0]['timeout']),
                         ('resume', 'command', 180))
        self.assertIn('quorum_resume.py', group['hooks'][0]['command'])
        self.assertIn('--client codex', group['hooks'][0]['command'])
        self.assertTrue(data['owner_consent_required'])
        self.assertFalse((self.project / '.codex').exists())

    def test_install_requires_owner_authorization_and_a_project(self):
        self.assertEqual(self.run_hook('--client', 'codex', '--project-dir', str(self.project), '--install')[1]['code'],
                         'owner_authorization_required')
        self.assertEqual(self.run_hook('--client', 'codex', '--install', '--owner-authorized')[1]['code'],
                         'startup_hook_project_required')
        self.assertEqual(self.run_hook('--client', 'grok', '--project-dir', str(self.project), '--install',
                                       '--owner-authorized')[1]['code'], 'startup_hook_install_unsupported')
        code, result = self.run_hook('--client', 'claude')
        self.assertEqual((code, result['code']), (3, 'startup_hook_unsupported'))
        self.assertFalse((self.project / '.codex').exists())

    def test_install_merges_is_idempotent_and_uninstall_restores_other_hooks(self):
        self.hooks.parent.mkdir()
        original = {'description': 'owner hooks', 'hooks': {
            'SessionStart': [{'matcher': 'startup', 'hooks': [{'type': 'command', 'command': 'echo hi'}]}],
            'Stop': [{'hooks': [{'type': 'command', 'command': 'echo bye'}]}]}}
        self.hooks.write_text(json.dumps(original))
        if os.name != 'nt': self.hooks.chmod(0o640)
        flags = ('--client', 'codex', '--project-dir', str(self.project), '--owner-authorized')
        code, result = self.run_hook(*flags, '--install')
        self.assertEqual((code, result['data']['state']), (0, 'installed'))
        installed = json.loads(self.hooks.read_text())
        self.assertEqual(installed['hooks']['Stop'], original['hooks']['Stop'])
        self.assertEqual(len(installed['hooks']['SessionStart']), 2)
        if os.name != 'nt': self.assertEqual(self.hooks.stat().st_mode & 0o777, 0o640)
        self.assertEqual(self.run_hook(*flags, '--install')[1]['data']['state'], 'unchanged')
        self.assertEqual(json.loads(self.hooks.read_text()), installed)
        self.assertEqual(self.run_hook(*flags, '--uninstall')[1]['data']['state'], 'removed')
        self.assertEqual(json.loads(self.hooks.read_text()), original)
        self.assertEqual(self.run_hook(*flags, '--uninstall')[1]['data']['state'], 'absent')

    def test_install_creates_a_new_file_and_refuses_conflicts_or_invalid_config(self):
        flags = ('--client', 'codex', '--project-dir', str(self.project), '--owner-authorized')
        self.assertEqual(self.run_hook(*flags, '--install')[1]['data']['state'], 'installed')
        self.assertEqual(self.run_hook(*flags, '--uninstall')[1]['data']['state'], 'removed')
        self.assertEqual(json.loads(self.hooks.read_text()), {})
        other = {'hooks': {'SessionStart': [{'matcher': 'resume', 'hooks': [
            {'type': 'command', 'command': 'uv run python /elsewhere/scripts/quorum_resume.py --state /x --client codex'}]}]}}
        self.hooks.write_text(json.dumps(other))
        self.assertEqual(self.run_hook(*flags, '--install')[1]['code'], 'startup_hook_conflict')
        self.assertEqual(json.loads(self.hooks.read_text()), other)
        for broken in ('{"hooks": [', '[]', '{"hooks": {"SessionStart": {}}}'):
            with self.subTest(broken=broken):
                self.hooks.write_text(broken)
                self.assertEqual(self.run_hook(*flags, '--install')[1]['code'], 'startup_hook_config_invalid')
                self.assertEqual(self.hooks.read_text(), broken)

    @unittest.skipIf(os.name == 'nt', 'symlink creation needs privileges on Windows')
    def test_linked_config_is_refused(self):
        target = self.base / 'elsewhere.json'
        target.write_text('{}')
        self.hooks.parent.mkdir()
        self.hooks.symlink_to(target)
        flags = ('--client', 'codex', '--project-dir', str(self.project), '--owner-authorized', '--install')
        self.assertEqual(self.run_hook(*flags)[1]['code'], 'startup_hook_config_unsafe')
        self.assertEqual(target.read_text(), '{}')


def write_ledger(path, record, control):
    path.mkdir(parents=True)
    connection = sqlite3.connect(path / 'ledger.sqlite')
    with connection:
        connection.execute('create table settings(key text primary key, data text)')
        connection.execute('insert into settings values (?,?)', ('session-agent', json.dumps(record)))
        if control is not None:
            connection.execute('insert into settings values (?,?)', ('tool-inbox-control', json.dumps(control)))
    connection.close()


class HookWorkerNoticeTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.state = self.root / 'state'

    def worker(self, sid=WORKER, parent=PARENT, enabled=True):
        record = {'transport': 'tool-inbox', 'engine': 'codex', 'session_ref': sid, 'role': 'velvil-author',
                  'quorum': 'team', 'worker_binding': {'provider': 'codex', 'session_ref': sid,
                                                       'parent_thread_id': parent}}
        write_ledger(self.state / 'sessions' / sid, record, {'enabled': enabled})

    def test_only_enabled_workers_of_this_parent_produce_a_fixed_notice(self):
        from quorum_resume import worker_notices
        self.worker()
        self.worker(SECOND, parent=OTHER_PARENT)
        self.worker('55555555-5555-4555-8555-555555555555', enabled=False)
        before = sorted(path.name for path in (self.state / 'sessions').iterdir())
        notices = worker_notices(self.state, PARENT)
        self.assertEqual(len(notices), 1)
        self.assertIn('--session-id ' + WORKER + ' recover --parent-thread-id ' + PARENT, notices[0])
        self.assertIn('Do not run it from this parent chat', notices[0])
        self.assertNotIn(str(self.state), notices[0])
        self.assertEqual(worker_notices(self.state, OTHER_PARENT)[0].count(SECOND), 2)
        self.assertEqual(worker_notices(self.root / 'missing', PARENT), [])
        self.assertEqual(sorted(path.name for path in (self.state / 'sessions').iterdir()), before)
        self.assertFalse((self.state / 'sessions' / PARENT).exists())

    def test_resume_hook_prints_the_worker_notice_as_context_without_recovering_the_parent(self):
        self.worker()
        script = SCRIPTS / 'quorum_resume.py'
        event = {'hook_event_name': 'SessionStart', 'source': 'resume', 'session_id': PARENT, 'cwd': str(self.root)}
        env = {key: value for key, value in os.environ.items()
               if key not in ('CODEX_THREAD_ID', 'CLAUDE_CODE_SESSION_ID', 'GROK_SESSION_ID',
                              'CLAUDE_CODE_HOST_SESSION_ID', 'CLAUDE_CODE_MESSAGING_SOCKET',
                              'CLAUDE_CODE_MESSAGING_TOKEN', 'GROK_LEADER_SOCKET')}
        env['QUORUM_PROFILE_ROOT'] = str(self.root / 'profiles')
        result = subprocess.run([sys.executable, str(script), '--state', str(self.state), '--client', 'codex'],
                                input=json.dumps(event), text=True, capture_output=True, cwd=self.root, env=env,
                                timeout=90 if os.name == 'nt' else 20)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('recover --parent-thread-id ' + PARENT, result.stdout)
        self.assertEqual(result.stderr, '')
        self.assertFalse((self.state / 'sessions' / PARENT).exists())
        self.assertFalse((self.root / 'profiles').exists())


if __name__ == '__main__':
    unittest.main()
