"""A release is its content hash and the protocol integer is the only gate (#168, #172)."""
import json
from pathlib import Path
import shutil
import sys
import tempfile
import tomllib
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / 'skills/quorum'
sys.path.insert(0, str(SKILL / 'scripts'))
sys.path.insert(0, str(ROOT / 'tests'))
from quorum_test_support import isolated_profiles

MANIFEST = json.loads((SKILL / 'assets/release.json').read_text(encoding='utf-8'))
# What a hash-only server requires (20261108120000).
SERVER = {'protocol_version':MANIFEST['protocol_version']}
# What a server before it still answered: its number is not this client's concern.
PHASE_ONE = {**SERVER, 'skill_version':'0.3.0'}
RUN = '77777777-7777-4777-8777-777777777777'
OTHER_RUN = '88888888-8888-4888-8888-888888888888'
OPERATION = '99999999-9999-4999-8999-999999999999'
AGENT = '11111111-1111-4111-8111-111111111111'
PEER = '22222222-2222-4222-8222-222222222222'
THIRD = '33333333-3333-4333-8333-333333333333'
FOURTH = '44444444-4444-4444-8444-444444444444'
RUNNING, TARGET, OLDER = 'a' * 64, 'b' * 64, 'c' * 64


class Api:
    def __init__(self, answers=None):
        self.calls, self.answers = [], answers or {}

    def call(self, operation, payload, request_id=None):
        self.calls.append((operation, dict(payload)))
        answer = self.answers.get(operation, {'recorded':True, 'online':True})
        if isinstance(answer, Exception): raise answer
        return answer() if callable(answer) else answer

    def sent(self, operation):
        return [payload for name, payload in self.calls if name == operation]


class NoNumberTests(unittest.TestCase):
    def test_the_manifest_is_the_protocol_alone_and_the_package_version_is_a_placeholder(self):
        self.assertEqual(MANIFEST, {'protocol_version':2})
        project = tomllib.loads((SKILL / 'pyproject.toml').read_text(encoding='utf-8'))
        self.assertEqual(project['project']['version'], '0.0.0')
        lock = (SKILL / 'uv.lock').read_text(encoding='utf-8')
        self.assertIn('name = "quorum-pilot-client"\nversion = "0.0.0"', lock)
        # Nothing reads the placeholder: no script names the package version or the manifest's old key.
        for path in (SKILL / 'scripts').glob('*.py'):
            text = path.read_text(encoding='utf-8')
            self.assertNotIn('skill_version', text, path.name)
            self.assertNotIn("['project']['version']", text, path.name)

    def test_a_manifest_with_an_old_number_is_read_as_its_protocol(self):
        from quorum_release import read_release
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / 'release.json'
            path.write_text(json.dumps(PHASE_ONE), encoding='utf-8')
            self.assertEqual(read_release(path), SERVER)

    def test_this_copy_validates_a_later_copy(self):
        from quorum_update import validate_source
        with tempfile.TemporaryDirectory() as root:
            copy = Path(root) / 'quorum'
            shutil.copytree(SKILL, copy, ignore=shutil.ignore_patterns('.venv', '__pycache__'))
            project = copy / 'pyproject.toml'
            project.write_text(project.read_text(encoding='utf-8').replace('version = "0.0.0"', 'version = "1"'),
                               encoding='utf-8')
            self.assertRegex(validate_source(copy), '^[0-9a-f]{64}$')

    def test_listener_markers_carry_no_number(self):
        from quorum_listener import Control
        from quorum_release import installed_release
        from quorum_state import LocalLedger
        with tempfile.TemporaryDirectory() as root:
            ledger = LocalLedger(Path(root))
            try:
                Control(ledger).update(RUN, 'waiting', 0, release=installed_release())
                self.assertEqual(ledger.setting('listener-release'), {**SERVER, 'run_id':RUN})
                self.assertNotIn('skill_version', ledger.setting('listener-source') or {})
            finally: ledger.db.close()

    def test_a_marker_from_an_older_listener_is_read_without_stopping_it(self):
        from quorum_listener import Control
        from quorum_os import FileLock
        from quorum_release import ReleaseGuard, source_identity
        from quorum_state import LocalLedger
        with tempfile.TemporaryDirectory() as root:
            ledger = LocalLedger(Path(root))
            try:
                Control(ledger).begin(RUN, 60)
                ledger.setting('listener-release', {'run_id':RUN, **PHASE_ONE})
                with FileLock(ledger.root / 'listener.lock'):
                    with patch('quorum_listener.locked', return_value=True):
                        ReleaseGuard(Api({'session_requirements':SERVER}), ledger).running()
                self.assertFalse(Control(ledger).stopping(RUN))
            finally: ledger.db.close()
        self.assertEqual(source_identity({**PHASE_ONE, 'content_sha256':OLDER}), {**SERVER, 'content_sha256':OLDER})

    def test_the_heartbeat_sends_no_number(self):
        from quorum_presence import Presence
        from quorum_state import LocalLedger
        for required in (SERVER, PHASE_ONE):
            with self.subTest(required=required), tempfile.TemporaryDirectory() as root:
                ledger = LocalLedger(Path(root))
                try:
                    api = Api({'session_requirements':required})
                    Presence(api, {'agent_id':AGENT, 'session_ref':'s'}, ledger, run_id=RUN).tick()
                    self.assertNotIn('skill_version', api.sent('session_heartbeat')[0])
                    self.assertEqual(api.sent('session_heartbeat')[0]['protocol_version'], 2)
                finally: ledger.db.close()

    def test_a_server_that_still_requires_the_number_is_reported_unavailable(self):
        """Its heartbeat has no signature without the number: apply the hash-only release first."""
        from quorum_api import QuorumError
        from quorum_presence import Presence
        from quorum_state import LocalLedger
        with tempfile.TemporaryDirectory() as root:
            ledger = LocalLedger(Path(root))
            try:
                api = Api({'session_requirements':PHASE_ONE,
                           'session_heartbeat':QuorumError('service_error', missing=True)})
                with self.assertRaisesRegex(QuorumError, '^coordination_unavailable$'):
                    Presence(api, {'agent_id':AGENT, 'session_ref':'s'}, ledger, run_id=RUN).tick()
                self.assertTrue(api.sent('session_heartbeat'))
                self.assertTrue(all('skill_version' not in sent for sent in api.sent('session_heartbeat')))
            finally: ledger.db.close()


class GateTests(unittest.TestCase):
    def guard(self, required):
        from quorum_release import ReleaseGuard
        from quorum_state import LocalLedger
        root = tempfile.TemporaryDirectory()
        self.addCleanup(root.cleanup)
        ledger = LocalLedger(Path(root.name))
        self.addCleanup(ledger.db.close)
        return ReleaseGuard(Api({'session_requirements':required}), ledger, stop_on_mismatch=False), ledger

    def test_only_the_protocol_integer_refuses(self):
        from quorum_release import ReleaseMismatch, installed_identity
        for required in (SERVER, PHASE_ONE, {'protocol_version':2, 'skill_version':'9.9.9'}):
            with self.subTest(required=required):
                guard, ledger = self.guard(required)
                self.assertEqual(guard.check(), installed_identity())
                self.assertIs(ledger.setting('release')['update_required'], False)
                self.assertEqual(ledger.setting('release')['required'], {'protocol_version':2})
        for required in ({'protocol_version':3}, {**PHASE_ONE, 'protocol_version':3}, {'protocol_version':1}, {}, None):
            with self.subTest(required=required):
                guard, _ = self.guard(required)
                with self.assertRaisesRegex(ReleaseMismatch, '^skill_update_required$'): guard.check()

    def test_the_identity_is_the_content_hash(self):
        from quorum_release import installed_identity, installed_release
        from quorum_update import content_hash
        digest = content_hash(SKILL)
        self.assertEqual(installed_identity(), {'protocol_version':2, 'content_sha256':digest, 'release':digest[:7]})
        self.assertEqual(installed_release()['protocol_version'], 2)

    def test_an_unreadable_source_costs_the_hash_and_not_the_check(self):
        import quorum_release
        from quorum_api import QuorumError
        with patch.object(quorum_release, 'installed_source', side_effect=QuorumError('runtime_source_unavailable')):
            self.assertEqual(quorum_release.installed_identity(),
                             {'protocol_version':2, 'content_sha256':None, 'release':None})


class ReportTests(unittest.TestCase):
    def setUp(self):
        from quorum_state import LocalLedger
        root = tempfile.TemporaryDirectory()
        self.addCleanup(root.cleanup)
        self.ledger = LocalLedger(Path(root.name))
        self.addCleanup(self.ledger.db.close)
        self.clock, self.wall = [0], [1000.0]
        patcher = patch('quorum_presence.installed_source', return_value={**MANIFEST, 'content_sha256':RUNNING})
        self.source = patcher.start()
        self.addCleanup(patcher.stop)

    def presence(self, api, run_id=RUN):
        from quorum_presence import Presence
        return Presence(api, {'agent_id':AGENT, 'session_ref':'s'}, self.ledger, clock=lambda:self.clock[0],
                        wall=lambda:self.wall[0], run_id=run_id)

    def api(self, **answers):
        return Api({'session_requirements':SERVER, **answers})

    def advance(self, seconds):
        self.clock[0] += seconds
        self.wall[0] += seconds

    def test_a_listener_reports_its_hash_and_run_after_an_acknowledged_heartbeat(self):
        api = self.api()
        self.presence(api).tick()
        names = [name for name, _ in api.calls]
        self.assertLess(names.index('session_heartbeat'), names.index('session_release_report'))
        self.assertEqual(api.sent('session_release_report'), [{'agent_id':AGENT, 'session_ref':'s',
                                                               'content_sha256':RUNNING, 'client_run_id':RUN}])

    def test_it_is_not_sent_every_heartbeat_but_is_refreshed(self):
        from quorum_presence import RELEASE_INTERVAL
        api = self.api()
        presence = self.presence(api)
        presence.tick()
        self.advance(20)
        presence.tick()
        self.assertEqual(len(api.sent('session_heartbeat')), 2)
        self.assertEqual(len(api.sent('session_release_report')), 1)
        self.advance(RELEASE_INTERVAL)
        presence.tick()
        self.assertEqual(len(api.sent('session_release_report')), 2)

    def test_a_new_run_or_a_new_hash_reports_at_once(self):
        api = self.api()
        self.presence(api).tick()
        self.advance(1)
        self.presence(api, OTHER_RUN).tick()
        self.assertEqual([sent['client_run_id'] for sent in api.sent('session_release_report')], [RUN, OTHER_RUN])
        self.advance(1)
        self.source.return_value = {**MANIFEST, 'content_sha256':TARGET}
        self.presence(api, OTHER_RUN).tick()
        self.assertEqual(api.sent('session_release_report')[-1]['content_sha256'], TARGET)

    def test_a_run_without_a_valid_id_reports_only_the_hash(self):
        api = self.api()
        self.presence(api, None).tick()
        self.assertNotIn('client_run_id', api.sent('session_release_report')[0])

    def test_no_report_without_an_acknowledged_heartbeat(self):
        from quorum_api import QuorumError
        api = self.api(session_heartbeat=QuorumError('service_unavailable', transient=True))
        self.presence(api).tick()
        self.assertEqual(api.sent('session_release_report'), [])

    def test_a_stop_observed_first_sends_nothing(self):
        answers = iter((False, False, True))
        api = self.api()
        self.presence(api).tick(lambda:next(answers))
        self.assertEqual(len(api.sent('session_heartbeat')), 1)
        self.assertEqual(api.sent('session_release_report'), [])

    def test_an_older_server_stays_quiet_and_is_asked_again_later(self):
        from quorum_api import QuorumError
        from quorum_presence import RELEASE_INTERVAL
        api = self.api(session_release_report=QuorumError('service_error', missing=True))
        presence = self.presence(api)
        presence.tick()
        self.assertEqual(self.ledger.setting('presence')['state'], 'online')
        self.advance(20)
        presence.tick()
        self.assertEqual(len(api.sent('session_release_report')), 1)
        self.advance(RELEASE_INTERVAL)
        presence.tick()
        self.assertEqual(len(api.sent('session_release_report')), 2)

    def test_a_failed_report_never_touches_the_listener_and_is_retried(self):
        from quorum_api import QuorumError
        for failure in (QuorumError('service_unavailable', transient=True), QuorumError('release_report_invalid')):
            with self.subTest(failure=str(failure)):
                self.ledger.setting('release-report', None)
                api = self.api(session_release_report=failure)
                presence = self.presence(api)
                presence.tick()
                self.assertEqual(self.ledger.setting('presence')['state'], 'online')
                self.advance(20)
                presence.tick()
                self.assertEqual(len(api.sent('session_release_report')), 2)

    def test_an_unreadable_source_sends_no_report_and_keeps_the_heartbeat(self):
        from quorum_api import QuorumError
        self.source.side_effect = QuorumError('runtime_source_unavailable')
        api = self.api()
        self.presence(api).tick()
        self.assertEqual(len(api.sent('session_heartbeat')), 1)
        self.assertEqual(api.sent('session_release_report'), [])


def peer(agent):
    return {'agent_id':agent, 'role':'r', 'availability':'online', 'client':dict(SERVER), 'required':dict(SERVER),
            'update_required':False}


class PeersTests(unittest.TestCase):
    SCOPE = {'agent_id':AGENT, 'session_ref':'s'}

    def view(self, status, peers=None):
        from quorum_release import peers_view
        return peers_view(Api({'session_peers':peers or [peer(PEER), peer(THIRD), peer(FOURTH)],
                               'session_release_status':status}), self.SCOPE)

    def test_who_is_behind_the_fleet_target_by_short_hash(self):
        behind, current, unreported = self.view({
            'target':{'protocol_version':2, 'content_sha256':TARGET, 'requested_at':'2026-11-06T10:00:00Z'},
            'agents':[{'agent_id':PEER, 'content_sha256':RUNNING, 'reported_at':'2026-11-06T10:01:00Z',
                       'reported_via':'heartbeat'},
                      {'agent_id':THIRD, 'content_sha256':TARGET, 'reported_at':'2026-11-06T10:02:00Z',
                       'reported_via':'update_report'},
                      {'agent_id':FOURTH, 'content_sha256':None, 'reported_at':None, 'reported_via':None}]})
        self.assertEqual(behind['release'], {'state':'behind', 'running':'a'*7, 'target':'b'*7, 'self_reported':True,
                                             'reported_at':'2026-11-06T10:01:00Z', 'reported_via':'heartbeat'})
        self.assertEqual((current['release']['state'], current['release']['reported_via']), ('on_target', 'update_report'))
        self.assertEqual(unreported['release'], {'state':'unreported', 'running':None, 'target':'b'*7})

    def test_a_report_without_a_target_is_labelled_so(self):
        row = self.view({'target':None, 'agents':[{'agent_id':PEER, 'content_sha256':RUNNING, 'reported_via':'heartbeat'}]},
                        [peer(PEER)])[0]
        self.assertEqual((row['release']['state'], row['release']['running'], row['release']['target']),
                         ('no_target', 'a'*7, None))

    def test_a_server_without_reports_still_answers_peers(self):
        from quorum_api import QuorumError
        for answer in (QuorumError('service_error', missing=True), QuorumError('service_unavailable', transient=True),
                       'nonsense', {'agents':'nonsense'}):
            with self.subTest(answer=str(answer)):
                row = self.view(answer, [peer(PEER)])[0]
                self.assertEqual(row['release'], {'state':'unreported', 'running':None, 'target':None})
                self.assertEqual(row['availability'], 'online')

    def test_update_required_means_the_protocol(self):
        rows = self.view({'target':None, 'agents':[]},
                         [peer(PEER), {**peer(THIRD), 'client':{'protocol_version':1}, 'update_required':True},
                          {**peer(FOURTH), 'client':{'protocol_version':None}, 'update_required':True}])
        self.assertEqual([row['update_required'] for row in rows], [False, True, True])
        self.assertEqual([row['client'] for row in rows],
                         [SERVER, {'protocol_version':1}, {'protocol_version':None}])

    def test_invalid_reports_are_not_shown_as_hashes(self):
        row = self.view({'target':{'content_sha256':'../../secret'},
                         'agents':[{'agent_id':PEER, 'content_sha256':'private-text', 'reported_via':'somewhere'}]},
                        [peer(PEER)])[0]
        self.assertEqual(row['release'], {'state':'unreported', 'running':None, 'target':None})


class UpdateOutputTests(unittest.TestCase):
    RECORD = {'agent_id':AGENT, 'session_ref':'s', 'quorum_id':'q'}
    TARGET = {**SERVER, 'content_sha256':TARGET}

    def status(self, participants):
        return {'operation_id':OPERATION, 'target':dict(self.TARGET), 'participants':participants}

    def fleet(self, status, answer):
        from quorum_updates import with_fleet_release
        return with_fleet_release(Api({'session_release_status':answer}), self.RECORD, status,
                                  {'operation_id':OPERATION}, lambda:None)

    def test_a_page_shows_who_is_behind_and_names_no_number(self):
        page = self.fleet(self.status([
            {'agent_id':PEER, 'state':'refreshed'}, {'agent_id':THIRD, 'state':'active_reported',
             'runtime_report':{**self.TARGET, 'run_id':RUN, 'reported_at':'2026-11-06T10:00:00Z'}},
            {'agent_id':FOURTH, 'state':'queued'}]),
            {'target':{'content_sha256':TARGET},
             'agents':[{'agent_id':PEER, 'content_sha256':RUNNING, 'reported_via':'heartbeat'},
                       {'agent_id':THIRD, 'content_sha256':TARGET, 'reported_via':'update_report'},
                       {'agent_id':FOURTH, 'content_sha256':None}]})
        self.assertEqual(page['fleet_release'], {'target':'b'*7, 'on_target':1, 'unreported':1,
                                                 'behind':[{'agent_id':PEER, 'running':'a'*7}]})
        self.assertEqual([item['release']['state'] for item in page['participants']], ['behind', 'on_target', 'unreported'])
        self.assertEqual(page['target'], {'protocol_version':2, 'content_sha256':TARGET, 'release':'b'*7})
        self.assertEqual([item['state'] for item in page['participants']], ['refreshed', 'active_reported', 'queued'])
        self.assertNotIn('skill_version', json.dumps(page))

    def test_a_newer_target_than_this_operation_is_the_fleet_target(self):
        page = self.fleet(self.status([{'agent_id':PEER, 'state':'active_reported'}]),
                          {'target':{'content_sha256':OLDER},
                           'agents':[{'agent_id':PEER, 'content_sha256':TARGET, 'reported_via':'heartbeat'}]})
        self.assertEqual((page['fleet_release']['target'], page['target']['release']), ('c'*7, 'b'*7))
        self.assertEqual(page['participants'][0]['release']['state'], 'behind')

    def test_the_server_run_check_outranks_an_older_runtime_report(self):
        report = {**self.TARGET, 'run_id':RUN, 'reported_at':'2026-11-06T10:00:00Z'}
        page = self.fleet(self.status([{'agent_id':PEER, 'runtime_report':report}]),
                          {'target':{'content_sha256':TARGET}, 'agents':[{'agent_id':PEER, 'content_sha256':None}]})
        self.assertEqual(page['participants'][0]['release']['state'], 'unreported')

    def test_without_reports_a_runtime_report_is_the_labelled_fallback(self):
        from quorum_api import QuorumError
        report = {**self.TARGET, 'run_id':RUN, 'reported_at':'2026-11-06T10:00:00Z'}
        page = self.fleet(self.status([{'agent_id':PEER, 'runtime_report':report}, {'agent_id':THIRD}]),
                          QuorumError('service_error', missing=True))
        release = page['participants'][0]['release']
        self.assertEqual((release['state'], release['reported_via'], release['self_reported']),
                         ('on_target', 'update_report', True))
        self.assertEqual(page['fleet_release']['target'], 'b'*7)
        self.assertEqual(page['fleet_release']['unreported'], 1)

    def test_a_failed_read_never_promotes_an_older_runtime_report(self):
        """A migrated server's run check could not be read: the previous run's hash must not count as running."""
        from quorum_api import QuorumError
        report = {**self.TARGET, 'run_id':RUN, 'reported_at':'2026-11-06T10:00:00Z'}
        for answer in (QuorumError('service_unavailable', transient=True), QuorumError('service_error', transient=True),
                       QuorumError('service_error'), QuorumError('auth_required'), 'nonsense', {'agents':'nonsense'}):
            with self.subTest(answer=str(answer)):
                page = self.fleet(self.status([{'agent_id':PEER, 'runtime_report':report}]), answer)
                self.assertEqual(page['participants'][0]['release'], {'state':'unreported', 'running':None, 'target':'b'*7})
                self.assertEqual(page['fleet_release'], {'target':'b'*7, 'on_target':0, 'unreported':1, 'behind':[],
                                                         'read_failed':True})
                self.assertEqual(page['participants'][0]['runtime_report'], report)

    def test_a_page_without_participants_needs_no_extra_read(self):
        from quorum_updates import with_fleet_release
        api = Api()
        status = {'operation_id':OPERATION, 'agents':[]}
        self.assertEqual(with_fleet_release(api, self.RECORD, status, {}, lambda:None)['agents'], [])
        self.assertEqual(api.calls, [])

    def test_evidence_is_the_server_target_unchanged(self):
        """The report compares its evidence with the target exactly: protocol and hash."""
        from quorum_release import source_identity
        self.assertEqual(source_identity(self.TARGET), self.TARGET)
        self.assertEqual(source_identity({**self.TARGET, 'skill_version':'0.3.0'}), self.TARGET)


NOTICE = ('Quorum maintenance proposal. Receiving-owner authorization is required for canonical refresh; '
          'no installation, lifecycle or task permission is granted. Target protocol 2, sha256 %s.')


class MessageReadTests(unittest.TestCase):
    def api(self, answer):
        from quorum_api import QuorumApi, ServiceConfig
        return QuorumApi(ServiceConfig('https://example.com', 'key'), lambda:'token',
                         lambda url, headers, body: answer)

    def test_every_read_returns_what_the_service_sent(self):
        """The server names a target by protocol and hash, so no client rewrites a message."""
        marker = {'kind':'quorum.update', 'notice_id':'n', 'target':{**SERVER, 'content_sha256':TARGET}}
        message = {'id':'m', 'body':NOTICE % TARGET, 'maintenance':marker}
        for operation, answer in (('session_inbox', [message]), ('conversation_read', [message]),
                                  ('session_message', message), ('session_update_status', {'target':marker['target']}),
                                  ('session_message', None), ('session_inbox', 'x')):
            with self.subTest(operation=operation):
                self.assertEqual(self.api(answer).call(operation, {}), answer)


class VisibleOutputTests(unittest.TestCase):
    @isolated_profiles
    def test_status_shows_the_hash_of_a_client_with_no_saved_check(self):
        from quorum_release import diagnostic, installed_identity
        from quorum_state import LocalLedger
        with tempfile.TemporaryDirectory() as root:
            ledger = LocalLedger(Path(root))
            try:
                shown = diagnostic(ledger)
                self.assertEqual(shown['installed'], installed_identity())
                self.assertNotIn('skill_version', json.dumps(shown))
            finally: ledger.db.close()

    def test_no_script_or_reference_names_the_number(self):
        # The server guide alone names it, to describe the migration that removed it.
        for path in [*(SKILL / 'scripts').glob('*.py'), *(SKILL / 'references').glob('*.md'), SKILL / 'SKILL.md',
                     SKILL / 'README.md']:
            if path.name == 'server.md': continue
            self.assertNotIn('skill_version', path.read_text(encoding='utf-8'), path.name)


if __name__ == '__main__':
    unittest.main()
