"""A release is its content hash; the protocol integer is the gate; older copies keep working (#168)."""
import json
from pathlib import Path
import re
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
NUMBER = MANIFEST['skill_version']
# What a live server answers until phase two: the frozen requirement is not this client's concern.
SERVER = {'protocol_version':MANIFEST['protocol_version'], 'skill_version':NUMBER}
RUN = '77777777-7777-4777-8777-777777777777'
OTHER_RUN = '88888888-8888-4888-8888-888888888888'
OPERATION = '99999999-9999-4999-8999-999999999999'
AGENT = '11111111-1111-4111-8111-111111111111'
PEER = '22222222-2222-4222-8222-222222222222'
THIRD = '33333333-3333-4333-8333-333333333333'
FOURTH = '44444444-4444-4444-8444-444444444444'
RUNNING, TARGET, OLDER = 'a' * 64, 'b' * 64, 'c' * 64


def old_versions(value):
    """The parser every installed copy before #168 runs: a staged copy that fails it cannot be installed."""
    if (not isinstance(value, dict) or type(value.get('protocol_version')) is not int
            or not 1 <= value['protocol_version'] <= 2147483647
            or not isinstance(value.get('skill_version'), str)
            or not re.fullmatch(r'[0-9]{1,6}\.[0-9]{1,6}\.[0-9]{1,6}', value['skill_version'])):
        return None
    return {key:value[key] for key in ('protocol_version', 'skill_version')}


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


class OldCopiesKeepWorking(unittest.TestCase):
    def test_an_old_copy_can_still_install_this_copy(self):
        self.assertIsNotNone(old_versions(MANIFEST), 'an old validate_source would answer update_invalid_copy')
        project = tomllib.loads((SKILL / 'pyproject.toml').read_text(encoding='utf-8'))
        self.assertEqual(MANIFEST['skill_version'], project['project']['version'])

    def test_this_copy_validates_a_later_copy_without_the_number(self):
        from quorum_update import validate_source
        with tempfile.TemporaryDirectory() as root:
            copy = Path(root) / 'quorum'
            shutil.copytree(SKILL, copy, ignore=shutil.ignore_patterns('.venv', '__pycache__'))
            (copy / 'assets/release.json').write_text('{"protocol_version":2}', encoding='utf-8')
            project = copy / 'pyproject.toml'
            project.write_text(project.read_text(encoding='utf-8').replace(f'version = "{NUMBER}"', 'version = "1"'),
                               encoding='utf-8')
            self.assertRegex(validate_source(copy), '^[0-9a-f]{64}$')

    def test_a_listener_marker_stays_readable_by_a_retained_old_helper(self):
        from quorum_listener import Control
        from quorum_release import installed_release
        from quorum_state import LocalLedger
        with tempfile.TemporaryDirectory() as root:
            ledger = LocalLedger(Path(root))
            try:
                Control(ledger).update(RUN, 'waiting', 0, release=installed_release())
                # Without these the old helper reads this run as stale and stops it.
                self.assertEqual(old_versions(ledger.setting('listener-release')), SERVER)
                self.assertEqual(old_versions(ledger.setting('listener-source')), SERVER)
            finally: ledger.db.close()

    def test_a_marker_from_an_old_listener_is_read_without_stopping_it(self):
        from quorum_listener import Control
        from quorum_os import FileLock
        from quorum_release import ReleaseGuard, source_identity, public_source
        from quorum_state import LocalLedger
        with tempfile.TemporaryDirectory() as root:
            ledger = LocalLedger(Path(root))
            try:
                Control(ledger).begin(RUN, 60)
                ledger.setting('listener-release', {'run_id':RUN, 'protocol_version':2, 'skill_version':'0.1.0'})
                with FileLock(ledger.root / 'listener.lock'):
                    with patch('quorum_listener.locked', return_value=True):
                        ReleaseGuard(Api({'session_requirements':SERVER}), ledger).running()
                self.assertFalse(Control(ledger).stopping(RUN))
            finally: ledger.db.close()
        old = {'protocol_version':2, 'skill_version':'0.1.0', 'content_sha256':OLDER}
        self.assertEqual(source_identity(old)['skill_version'], '0.1.0', 'echoed to the server as it was given')
        self.assertNotIn('skill_version', public_source(old))

    def test_the_heartbeat_sends_the_packaged_value_and_never_echoes_the_server(self):
        from quorum_presence import Presence
        from quorum_state import LocalLedger
        with tempfile.TemporaryDirectory() as root:
            ledger = LocalLedger(Path(root))
            try:
                api = Api({'session_requirements':{'protocol_version':2, 'skill_version':'9.9.9'}})
                Presence(api, {'agent_id':AGENT, 'session_ref':'s'}, ledger, run_id=RUN).tick()
                self.assertEqual(api.sent('session_heartbeat')[0]['skill_version'], NUMBER)
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
        for required in (SERVER, {'protocol_version':2}, {'protocol_version':2, 'skill_version':'9.9.9'}):
            with self.subTest(required=required):
                guard, ledger = self.guard(required)
                self.assertEqual(guard.check(), installed_identity())
                self.assertIs(ledger.setting('release')['update_required'], False)
                self.assertEqual(ledger.setting('release')['required'], {'protocol_version':2})
        for required in ({'protocol_version':3, 'skill_version':NUMBER}, {'protocol_version':1}, {}, None):
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

    def test_a_diagnostic_saved_by_an_older_run_shows_no_number(self):
        from quorum_release import diagnostic
        from quorum_state import LocalLedger
        with tempfile.TemporaryDirectory() as root:
            ledger = LocalLedger(Path(root))
            try:
                ledger.setting('release', {'installed':SERVER, 'required':SERVER, 'listener':SERVER,
                                           'update_required':False})
                shown = diagnostic(ledger)
                self.assertNotIn('skill_version', json.dumps(shown))
                self.assertNotIn(NUMBER, json.dumps(shown))
                self.assertEqual(shown['required'], {'protocol_version':2})
            finally: ledger.db.close()


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

    def test_the_number_is_gone_and_update_required_means_the_protocol(self):
        rows = self.view({'target':None, 'agents':[]},
                         [peer(PEER), {**peer(THIRD), 'client':{'protocol_version':1, 'skill_version':'0.2.0'},
                                       'update_required':True},
                          {**peer(FOURTH), 'client':{'protocol_version':None, 'skill_version':None},
                           'update_required':True},
                          {**peer(AGENT), 'client':{'protocol_version':2, 'skill_version':'0.1.0'},
                           'update_required':True}])
        self.assertNotIn('skill_version', json.dumps(rows))
        self.assertNotIn(NUMBER, json.dumps(rows))
        self.assertEqual([row['update_required'] for row in rows], [False, True, True, False])

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
        self.assertNotIn(NUMBER, json.dumps(page))

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
                self.assertNotIn('skill_version', page['participants'][0]['runtime_report'])

    def test_a_page_without_participants_needs_no_extra_read(self):
        from quorum_updates import with_fleet_release
        api = Api()
        status = {'operation_id':OPERATION, 'agents':[]}
        self.assertEqual(with_fleet_release(api, self.RECORD, status, {}, lambda:None)['agents'], [])
        self.assertEqual(api.calls, [])

    def test_evidence_is_the_server_target_unchanged(self):
        """The reviewed report compares its evidence exactly, so the frozen field is echoed and not shown."""
        from quorum_release import source_identity
        self.assertEqual(source_identity(self.TARGET), self.TARGET)


NOTICE = ('Quorum maintenance proposal. Receiving-owner authorization is required for canonical refresh; '
          'no installation, lifecycle or task permission is granted. Target protocol 2, skill %s, sha256 %s.')


class NoticeDisplayTests(unittest.TestCase):
    def api(self, answer):
        from quorum_api import QuorumApi, ServiceConfig
        return QuorumApi(ServiceConfig('https://example.com', 'key'), lambda:'token',
                         lambda url, headers, body: answer)

    def message(self, **extra):
        return {'id':'m', 'body':NOTICE % (NUMBER, TARGET), **extra}

    def test_every_message_read_shows_the_notice_by_short_hash(self):
        marker = {'kind':'quorum.update', 'notice_id':'n', 'target':{**SERVER, 'content_sha256':TARGET}}
        for operation, answer in (('session_inbox', [self.message(maintenance=marker)]),
                                  ('conversation_read', [self.message()]), ('session_message', self.message())):
            with self.subTest(operation=operation):
                result = self.api(answer).call(operation, {})
                text = json.dumps(result)
                self.assertNotIn(NUMBER, text)
                self.assertNotIn(TARGET, (result[0] if isinstance(result, list) else result)['body'])
                self.assertIn('Target protocol 2, release ' + TARGET[:7] + '.', text)
        shown = self.api([self.message(maintenance=marker)]).call('session_inbox', {})[0]['maintenance']
        self.assertEqual(shown, {'kind':'quorum.update', 'notice_id':'n',
                                 'target':{'protocol_version':2, 'content_sha256':TARGET}})

    def test_other_text_stays_exactly_as_sent(self):
        for body in ('skill 0.3.0, sha256 abc', 'Target protocol 2, skill 0.3.0.', 'ordinary peer text',
                     'p.s. ' + NOTICE.replace('Target protocol 2', 'Target protocol X') % (NUMBER, TARGET)):
            self.assertEqual(self.api([{'body':body}]).call('session_inbox', {}), [{'body':body}])

    def test_other_operations_and_shapes_pass_through(self):
        self.assertEqual(self.api(None).call('session_message', {}), None)
        self.assertEqual(self.api('x').call('session_inbox', {}), 'x')
        answer = {'target':{**SERVER, 'content_sha256':TARGET}}
        self.assertEqual(self.api(answer).call('session_update_status', {}), answer)


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

    def test_no_script_builds_owner_text_from_the_number(self):
        allowed = {'quorum_release.py', 'quorum_api.py'}
        for path in (SKILL / 'scripts').glob('*.py'):
            if path.name in allowed: continue
            self.assertNotIn('skill_version', path.read_text(encoding='utf-8'), path.name)


if __name__ == '__main__':
    unittest.main()
