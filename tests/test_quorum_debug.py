"""Quorum debug runs: local metadata, honest coverage, and mocked GitHub writes."""
import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'skills/quorum/scripts'))

SID = '11111111-1111-4111-8111-111111111111'
OTHER = '99999999-9999-4999-8999-999999999999'
AID = '22222222-2222-4222-8222-222222222222'
MID = '33333333-3333-4333-8333-333333333333'
REPO = 'example/quorum-debug-test'
SECRETS = ('SECRET-BODY-VALUE', 'SECRET-REPLY-VALUE', 'SECRET-NONCE-VALUE', 'SECRET-CWD-VALUE', 'SECRET-FILE-VALUE')


class Clock:
    def __init__(self):
        self.now = datetime(2026, 10, 27, 12, 0, tzinfo=timezone.utc)

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += timedelta(seconds=seconds)


class FakeGitHub:
    def __init__(self):
        self.calls = []
        self.available = True
        self.search_numbers = []
        self.search_issues = None
        self.search_status = 'ok'
        self.create_status = 'created'
        self.comment_status = 'commented'
        self.create_number = 4
        self.create_raises = None
        self.comment_raises = None
        self.before_search = None
        self.before_create = None
        self.before_view = None
        self.view_status = 'ok'
        self.issue_body = ''
        self.issue_comments = []

    def probe(self):
        self.calls.append(('probe',))
        if not self.available:
            return {'available': False, 'tool': 'gh', 'blocker': 'gh_cli_missing'}
        return {'available': True, 'tool': 'gh', 'blocker': None}

    def search(self, repo, finding_id):
        self.calls.append(('search', repo, finding_id))
        if self.before_search:
            self.before_search()
        if self.search_status != 'ok':
            return {'status': 'uncertain', 'issues': []}
        if self.search_issues is not None:
            return {'status': 'ok', 'issues': list(self.search_issues)}
        return {'status': 'ok', 'issues': [{'number': number, 'body': ''} for number in self.search_numbers]}

    def read_issue(self, repo, number):
        self.calls.append(('view', repo, number))
        if self.before_view:
            self.before_view()
        if self.view_status != 'ok':
            return {'status': 'uncertain'}
        return {'status': 'ok', 'number': number, 'body': self.issue_body, 'comments': list(self.issue_comments)}

    def create_issue(self, repo, title, body):
        self.calls.append(('create', repo, title, body))
        if self.before_create:
            self.before_create(repo, title, body)
        if self.create_raises:
            raise self.create_raises
        if self.create_status != 'created':
            return {'status': 'uncertain'}
        return {'status': 'created', 'number': self.create_number,
                'url': f'https://github.com/{repo}/issues/{self.create_number}'}

    def comment(self, repo, number, body):
        self.calls.append(('comment', repo, number, body))
        if self.comment_raises:
            raise self.comment_raises
        if self.comment_status != 'commented':
            return {'status': 'uncertain'}
        return {'status': 'commented'}

    def creates(self):
        return [call for call in self.calls if call[0] == 'create']

    def comments(self):
        return [call for call in self.calls if call[0] == 'comment']


def open_service(root, clock, publisher):
    from quorum_debug import DebugService
    return DebugService(root, clock=clock, publisher=publisher)


def write_session(root, sid=SID, quorum='team', project='proj', transport='native', deliveries=(),
                  listener=None, reception=None, epoch=None, wait=None, reply='SECRET-REPLY-VALUE'):
    from quorum_state import LocalLedger
    ledger = LocalLedger(Path(root) / 'sessions' / sid)
    ledger.setting('session-agent', {
        'quorum': quorum, 'quorum_id': 'qid-1', 'role': 'worker', 'engine': 'codex',
        'project_ref': project, 'agent_id': AID, 'session_ref': sid, 'transport': transport,
        'cwd': '/private/SECRET-CWD-VALUE',
    })
    if reception is not None:
        ledger.setting('session-reception', {'enabled': reception, 'binding': {'cwd': '/private/SECRET-CWD-VALUE'}})
    if listener is not None:
        ledger.setting('listener', listener)
    if epoch is not None:
        ledger.setting('presence-epoch', {'connection_id': epoch, 'acked_at': 1})
    if wait is not None:
        ledger.setting('listener-wait', wait)
    for delivery in deliveries:
        message_id = delivery['message_id']
        ledger.setting('delivery:' + message_id, {key: value for key, value in delivery.items() if key != 'message_id'})
    ledger.setting('nonce', {'nonce': 'SECRET-NONCE-VALUE', 'token': 'SECRET-FILE-VALUE'})
    ledger.set_attempt('attempt-1', 1, 'done', reply)
    ledger.db.close()


def run_text(root):
    return (Path(root) / 'debug' / 'current.json').read_text(encoding='utf-8')


def assert_redacted(test, root):
    text = run_text(root)
    for secret in SECRETS:
        test.assertNotIn(secret, text)


class DebugLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.github = FakeGitHub()
        self.github.available = False
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.service = open_service(self.root, self.clock, self.github)

    def tearDown(self):
        self.tmp.cleanup()

    def test_activation_is_partial_without_github_and_survives_restart(self):
        write_session(self.root, deliveries=[{
            'message_id': MID, 'state': 'submitted',
            'message': {'body': 'SECRET-BODY-VALUE', 'conversation_id': 'conv-1', 'sender_id': 'sender-a'},
        }])
        (self.root / 'credentials.json').write_text('SECRET-FILE-VALUE', encoding='utf-8')
        activated = self.service.activate('team', 'proj', REPO, 'stop')
        self.assertEqual(activated['activation'], 'partial')
        self.assertFalse(activated['complete'])
        self.assertIn('github_unavailable', activated['blockers'])
        self.assertFalse(activated['continuous_observation'])
        self.assertFalse(activated['automatic_runtime_restoration'])
        self.assertTrue(activated['collection']['live_observation'])
        self.assertEqual(activated['coverage']['participants'][0]['binding'], SID)
        self.assertEqual(activated['versions']['skill_version'], '0.3.0')
        assert_redacted(self, self.root)
        restored = open_service(self.root, self.clock, self.github).status()
        self.assertEqual(restored['run_id'], activated['run_id'])
        self.assertEqual(restored['state'], 'active')
        self.clock.advance(10)
        repeated = self.service.activate('team', 'proj', REPO, '2h')
        self.assertTrue(repeated['idempotent'])
        self.assertEqual(repeated['run_id'], activated['run_id'])
        self.assertIsNone(repeated['expires_at'])
        with self.assertRaises(Exception) as caught:
            self.service.activate('team', 'other', REPO, 'stop')
        self.assertEqual(caught.exception.code, 'debug_run_active')

    def test_stop_is_durable_and_a_new_run_does_not_revive_it(self):
        first = self.service.activate('team', 'proj', until='1h')
        stopped = self.service.stop()
        self.assertEqual(stopped['state'], 'stopped')
        self.assertEqual(open_service(self.root, self.clock, self.github).status()['state'], 'stopped')
        self.clock.advance(1)
        second = self.service.activate('team', 'proj', until='1h')
        self.assertNotEqual(second['run_id'], first['run_id'])
        archived = self.service.report(first['run_id'])
        self.assertEqual(archived['state'], 'stopped')
        self.assertFalse(archived['automatic_runtime_restoration'])

    def test_missing_binding_and_expiry_stay_explicit(self):
        activated = self.service.activate('team', 'proj', bindings=(OTHER,), until='1h')
        self.assertIn('bindings_not_observed', activated['blockers'])
        self.assertIn(OTHER, activated['coverage']['unavailable_bindings'])
        self.assertFalse(activated['complete'])
        self.clock.advance(3601)
        status = self.service.status()
        self.assertEqual(status['state'], 'expired')
        self.assertEqual(self.service.publish()['publication'], 'ended')
        self.assertEqual(self.github.creates(), [])


class DebugCollectionTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.github = FakeGitHub()
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.service = open_service(self.root, self.clock, self.github)

    def tearDown(self):
        self.tmp.cleanup()

    def test_local_collection_redacts_and_does_not_invent_loss(self):
        write_session(self.root, deliveries=[{
            'message_id': MID, 'state': 'intent',
            'message': {'body': 'SECRET-BODY-VALUE', 'conversation_id': 'conv-1', 'sender_id': 'sender-a'},
        }])
        write_session(self.root, sid=OTHER, project='other', deliveries=[{
            'message_id': '55555555-5555-4555-8555-555555555555', 'state': 'submitted',
            'message': {'body': 'SECRET-BODY-VALUE'},
        }])
        self.service.activate('team', 'proj')
        self.clock.advance(901)
        self.service.collect()
        report = self.service.report()
        assert_redacted(self, self.root)
        finding = report['findings'][0]
        self.assertEqual(finding['rule'], 'delivery_unknown')
        self.assertFalse(finding['definite'])
        self.assertEqual(finding['task_accepted'], 'unknown')
        self.assertEqual(finding['task_completed'], 'unknown')
        self.assertTrue(finding['live_observation'])
        self.assertTrue(any('Missing telemetry is not definite loss.' in item for item in report['limitations']))
        self.assertNotIn('55555555-5555-4555-8555-555555555555', run_text(self.root))
        self.assertTrue(any(event['stage'] == 'attempt' for event in report['timeline']))
        self.assertNotIn('listener.lock', run_text(self.root))

    def test_stuck_delivery_needs_two_observations_and_is_not_definite(self):
        write_session(self.root, deliveries=[{'message_id': MID, 'state': 'submitted'}])
        self.service.activate('team', 'proj', REPO)
        self.clock.advance(899)
        self.service.collect()
        self.assertEqual(self.service.report()['findings'], [])
        self.clock.advance(2)
        collected = self.service.collect()
        finding = self.service.report()['findings'][0]
        self.assertEqual(finding['rule'], 'stuck_unacknowledged')
        self.assertEqual(finding['delivery_stage'], 'transport_submitted')
        self.assertFalse(finding['definite'])
        self.assertEqual(collected['publication'], 'published')
        self.assertEqual(len(self.github.creates()), 1)
        self.assertEqual(self.github.creates()[0][1], REPO)
        self.assertIn('Debug-Finding:', self.github.creates()[0][3])
        self.assertIn('definite=false', self.github.creates()[0][3])
        for secret in SECRETS:
            self.assertNotIn(secret, self.github.creates()[0][3])
        self.service.collect()
        self.assertEqual(self.github.comments(), [])
        self.assertEqual(len(self.github.creates()), 1)

    def test_stage_change_updates_the_same_issue(self):
        write_session(self.root, deliveries=[{'message_id': MID, 'state': 'submitted'}])
        self.service.activate('team', 'proj', REPO)
        self.clock.advance(901)
        self.service.collect()
        from quorum_state import LocalLedger
        ledger = LocalLedger(self.root / 'sessions' / SID)
        ledger.setting('delivery:' + MID, {'state': 'offered'})
        ledger.db.close()
        self.service.collect()
        self.assertEqual(len(self.github.creates()), 1)
        self.assertEqual(len(self.github.comments()), 1)
        self.assertEqual(self.service.report()['findings'][0]['evidence'][0]['state'], 'offered')

    def test_hello_fallback_and_worker_notifications_are_not_definite_defects(self):
        write_session(self.root, epoch='aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa1',
                      wait={'mode': 'polling_fallback', 'code': 'realtime_unavailable'})
        self.service.activate('team', 'proj', REPO)
        for epoch in ('aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa2', 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa3'):
            from quorum_state import LocalLedger
            ledger = LocalLedger(self.root / 'sessions' / SID)
            ledger.setting('presence-epoch', {'connection_id': epoch})
            ledger.db.close()
            self.service.collect()
        report = self.service.report()
        self.assertTrue(any(item['kind'] == 'repeated_hello_epochs' for item in report['observations']))
        self.assertTrue(any(item['kind'] == 'realtime_fallback' for item in report['observations']))
        self.assertFalse(any(item['definite'] for item in report['findings']))
        self.assertEqual(self.github.creates(), [])
        write_session(self.root, sid=OTHER, transport='tool-inbox', deliveries=[
            {'message_id': f'66666666-6666-4666-8666-66666666666{index}', 'state': 'acked', 'notification': True}
            for index in range(3)
        ])
        self.service.collect()
        self.assertFalse(any(item['rule'] == 'routine_envelopes_reaching_parent' for item in self.service.report()['findings']))

    def test_parent_notifications_reply_burst_empty_waits_and_disabled_reception(self):
        write_session(self.root, deliveries=[
            {'message_id': f'77777777-7777-4777-8777-77777777777{index}', 'state': 'acked', 'notification': True,
             'message': {'conversation_id': 'conv-note'}}
            for index in range(3)
        ])
        self.service.activate('team', 'proj')
        self.service.collect()
        self.assertTrue(any(item['rule'] == 'routine_envelopes_reaching_parent' for item in self.service.report()['findings']))
        from quorum_state import LocalLedger
        for index, sender in enumerate(('sender-a', 'sender-b', 'sender-a', 'sender-b')):
            ledger = LocalLedger(self.root / 'sessions' / SID)
            ledger.setting('delivery:' + f'88888888-8888-4888-8888-88888888888{index}', {
                'state': 'acked', 'reply_to': True,
                'message': {'conversation_id': 'conv-loop', 'sender_id': sender, 'recipient_id': 'sender-b'},
            })
            ledger.db.close()
            self.service.collect()
        self.assertTrue(any(item['rule'] == 'suspected_reply_loop' and item['analysis'] == 'pending'
                            for item in self.service.report()['findings']))
        from quorum_state import LocalLedger
        ledger = LocalLedger(self.root / 'sessions' / SID)
        ledger.setting('listener', {'run_id': 'run-a', 'state': 'waiting', 'handled': 0})
        ledger.setting('session-reception', {'enabled': False})
        ledger.db.close()
        for _ in range(8):
            self.service.collect()
        self.assertTrue(any(item['rule'] == 'excessive_empty_receive' and 'model turns are unavailable' in item['observed']
                            for item in self.service.report()['findings']))
        from quorum_state import LocalLedger
        ledger = LocalLedger(self.root / 'sessions' / SID)
        ledger.setting('listener', {'run_id': 'run-b', 'state': 'waiting', 'handled': 0})
        ledger.db.close()
        self.service.collect()
        self.assertTrue(any(item['rule'] == 'reception_disabled_listener_running' and not item['definite']
                            for item in self.service.report()['findings']))

    def test_import_is_not_live_observation_and_rejects_bodies(self):
        self.service.activate('team', 'proj', REPO, sources=('imported-artifact',))
        artifact = self.root / 'artifact.json'
        artifact.write_text(json.dumps({'events': [
            {'kind': 'side_effect', 'side_effect_key': 'job-1', 'attempt_id': 'attempt-a', 'stage': 'task_completed',
             'live_observation': True, 'observed_at': '2020-01-01T00:00:00Z'},
            {'kind': 'side_effect', 'side_effect_key': 'job-1', 'attempt_id': 'attempt-b', 'stage': 'task_completed'},
        ]}), encoding='utf-8')
        imported = self.service.import_artifact(artifact)
        self.assertFalse(imported['live_observation'])
        report = self.service.report()
        finding = next(item for item in report['findings'] if item['rule'] == 'duplicate_side_effect')
        self.assertTrue(finding['definite'])
        self.assertFalse(finding['live_observation'])
        self.assertEqual(finding['task_completed'], 'claimed_by_import')
        self.assertTrue(any(event['clock'] == 'skew_uncertain' for event in report['timeline']))
        self.assertIn('live_observation=false', self.github.creates()[0][3])
        self.assertFalse(report['collection']['live_observation'])
        artifact.write_text(json.dumps({'events': [{'kind': 'note', 'body': 'SECRET-BODY-VALUE'}]}), encoding='utf-8')
        with self.assertRaises(Exception) as caught:
            self.service.import_artifact(artifact)
        self.assertEqual(caught.exception.code, 'debug_artifact_rejected')
        self.assertNotIn('SECRET-BODY-VALUE', run_text(self.root))

    def test_observation_gap_and_lock_scope(self):
        from quorum_os import FileLock
        self.service.activate('team', 'proj')
        self.clock.advance(901)
        paths = []
        real = FileLock

        class Tracking:
            def __init__(self, path):
                paths.append(str(path))
                self.lock = real(path)

            def __enter__(self):
                return self.lock.__enter__()

            def __exit__(self, *args):
                return self.lock.__exit__(*args)

        with patch('quorum_debug.FileLock', Tracking):
            collected = self.service.collect()
        self.assertTrue(self.service.report()['gaps'])
        self.assertFalse(collected['continuous_observation'])
        self.assertTrue(paths)
        self.assertTrue(all(path.endswith('run.lock') for path in paths))


class DebugPublicationTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.github = FakeGitHub()
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.service = open_service(self.root, self.clock, self.github)
        write_session(self.root, deliveries=[{'message_id': MID, 'state': 'submitted'}])

    def tearDown(self):
        self.tmp.cleanup()

    def test_uncertain_response_does_not_create_a_second_issue(self):
        self.github.create_status = 'uncertain'
        self.service.activate('team', 'proj', REPO)
        self.clock.advance(901)
        self.assertEqual(self.service.collect()['publication'], 'uncertain')
        self.assertEqual(len(self.github.creates()), 1)
        self.assertEqual(self.service.publish()['publication'], 'uncertain')
        self.assertEqual(len(self.github.creates()), 1)
        created = self.github.creates()[0][3]
        self.github.search_issues = [
            {'number': 8, 'body': 'Debug-Finding: ' + created.split('Debug-Finding: ', 1)[1].splitlines()[0] + 'extra'},
            {'number': 9, 'body': created},
        ]
        result = self.service.publish()
        self.assertEqual(len(self.github.creates()), 1)
        self.assertEqual(result['publication'], 'published')
        self.assertEqual(self.service.report()['publication']['ledger'][0]['issue_number'], 9)
        self.assertTrue(self.service.report()['publication']['ledger'][0]['reconciled'])

    def test_stop_and_github_outage_do_not_write(self):
        self.service.activate('team', 'proj', REPO)
        self.clock.advance(901)
        self.service.collect(publish=False)
        self.service.stop()
        self.assertEqual(self.service.publish()['publication'], 'ended')
        self.assertEqual(self.github.creates(), [])
        self.github.available = False
        self.clock.advance(1)
        self.service.activate('team', 'proj', REPO)
        self.clock.advance(901)
        delayed = self.service.collect()
        self.assertEqual(delayed['publication'], 'delayed')
        self.assertTrue(self.service.report()['pending_reports'])
        self.assertEqual(self.github.creates(), [])


class DebugTokenTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.service = open_service(self.root, self.clock, FakeGitHub())

    def tearDown(self):
        self.tmp.cleanup()

    def usage(self, **extra):
        record = {
            'source': 'codex', 'model': 'gpt-test', 'window_start': '2026-10-27T12:00:00Z',
            'window_end': '2026-10-27T13:00:00Z', 'category': 'project', 'quality': 'actual',
            'input_tokens': 8, 'output_tokens': 2,
        }
        record.update(extra)
        return record

    def test_actual_estimated_unavailable_and_shared_parent_windows(self):
        write_session(self.root)
        self.service.activate('team', 'proj')
        before = self.service.add_usage(self.usage())
        self.assertEqual(before['project']['actual'], 10)
        self.assertEqual(before['project']['estimated'], 'unavailable')
        self.assertEqual(before['debugger']['actual'], 'unavailable')
        self.assertGreaterEqual(before['debugger']['invocations'], 1)
        self.assertIsNone(before['overhead_ratio'])
        self.assertIn('codex', before['coverage']['reported_sources'])
        shared = self.service.add_usage(self.usage(shared_parent_sid=True, category='coordination', output_tokens=50))
        self.assertEqual(shared['excluded_double_count'], 1)
        self.assertEqual(shared['project']['actual'], 10)
        self.assertEqual(shared['coordination']['actual'], 'unavailable')
        counted = self.service.add_usage(self.usage(category='coordination', window_start='2026-10-27T13:00:00Z',
                                                    window_end='2026-10-27T14:00:00Z', input_tokens=4, output_tokens=1))
        self.assertEqual(counted['overhead_ratio'], 0.5)
        self.assertIn('counterfactual', counted['overhead_note'])
        mixed = self.service.add_usage(self.usage(category='project', quality='estimated',
                                                  window_start='2026-10-27T14:00:00Z',
                                                  window_end='2026-10-27T15:00:00Z'))
        self.assertEqual(mixed['project']['estimated'], 10)
        self.assertIsNone(mixed['overhead_ratio'])
        self.assertEqual(mixed['mixed_unallocated']['actual'], 'unavailable')
        with self.assertRaises(Exception) as caught:
            self.service.add_usage(self.usage(model='bad model'))
        self.assertEqual(caught.exception.code, 'debug_usage_rejected')

    def test_repeated_reports_are_idempotent_and_conflicts_are_rejected(self):
        self.service.activate('team', 'proj')
        first = self.service.add_usage(self.usage())
        second = self.service.add_usage(self.usage())
        self.assertEqual(second['project']['actual'], 10)
        self.assertEqual(second['excluded_double_count'], 0)
        self.assertEqual(len(second['records']), 1)
        self.assertEqual(first['records'], second['records'])
        with self.assertRaises(Exception) as caught:
            self.service.add_usage(self.usage(input_tokens=9))
        self.assertEqual(caught.exception.code, 'debug_usage_rejected')
        self.assertEqual(self.service.report()['tokens']['project']['actual'], 10)

    def test_cache_semantics_stay_explicit_and_parent_child_windows_do_not_overlap(self):
        self.service.activate('team', 'proj')
        parent = self.service.add_usage(self.usage(source='parent-host'))
        self.assertEqual(parent['project']['actual'], 10)
        self.assertEqual(parent['accounting']['cache'], 'disjoint_added_or_cache_inclusive_not_added')
        child = self.service.add_usage(self.usage(
            source='child-host', shared_parent_sid=True, category='coordination', output_tokens=40))
        self.assertEqual(child['excluded_double_count'], 1)
        self.assertEqual(child['project']['actual'], 10)
        self.assertEqual(child['coordination']['actual'], 'unavailable')
        disjoint = self.service.add_usage(self.usage(
            source='parent-host', cache_semantics='disjoint', cache_read_tokens=4,
            window_start='2026-10-27T13:00:00Z', window_end='2026-10-27T14:00:00Z'))
        self.assertEqual(disjoint['project']['actual'], 24)
        inclusive = self.service.add_usage(self.usage(
            source='parent-host', cache_semantics='cache_inclusive', cache_read_tokens=4, cache_write_tokens=3,
            window_start='2026-10-27T14:00:00Z', window_end='2026-10-27T15:00:00Z'))
        self.assertEqual(inclusive['project']['actual'], 34)
        stored = next(item for item in inclusive['records'] if item['cache_semantics'] == 'cache_inclusive')
        self.assertEqual(stored['tokens'], 10)
        with self.assertRaises(Exception) as caught:
            self.service.add_usage(self.usage(
                cache_read_tokens=2, window_start='2026-10-27T15:00:00Z', window_end='2026-10-27T16:00:00Z'))
        self.assertEqual(caught.exception.code, 'debug_usage_rejected')
        self.assertEqual(self.service.report()['tokens']['project']['actual'], 34)


class DebugCommandTests(unittest.TestCase):
    def test_cli_reports_partial_activation_and_blocks_live_subprocess(self):
        from quorum import main
        from quorum_debug import DebugError, GhCliPublisher, subprocess_run
        fake = FakeGitHub()
        fake.available = False
        with tempfile.TemporaryDirectory() as root, patch('quorum_debug.build_publisher', return_value=fake):
            buffer = io.StringIO()
            with contextlib.redirect_stdout(buffer):
                code = main(['--state', root, 'debug', 'activate', '--quorum', 'team', '--project-ref', 'proj',
                             '--report-repo', REPO, '--until', 'stop'])
            payload = json.loads(buffer.getvalue())
            self.assertEqual(code, 0)
            self.assertFalse(payload['data']['complete'])
            self.assertEqual(payload['data']['collector'], 'bounded_local_watch')
            self.assertEqual(payload['data']['supervision']['mode'], 'handoff')
            self.assertFalse(payload['data']['host_alive'])
            self.assertIn('collector_not_supervising', payload['data']['blockers'])
            buffer = io.StringIO()
            with contextlib.redirect_stdout(buffer):
                code = main(['--state', root, 'debug', 'status'])
            self.assertEqual(code, 0)
            self.assertEqual(json.loads(buffer.getvalue())['data']['state'], 'active')
            buffer = io.StringIO()
            with contextlib.redirect_stdout(buffer):
                code = main(['--state', root, 'debug', 'activate', '--quorum', 'other', '--project-ref', 'proj',
                             '--report-repo', REPO])
            self.assertEqual(code, 3)
            self.assertEqual(json.loads(buffer.getvalue())['code'], 'debug_run_active')
        with self.assertRaises(DebugError):
            subprocess_run(['gh', 'issue', 'delete', '1'])
        with self.assertRaises(AssertionError):
            subprocess_run(['gh', 'auth', 'status'])
        with patch('quorum_debug_publish.shutil.which', return_value=None):
            self.assertFalse(GhCliPublisher(runner=lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError('called'))).probe()['available'])
        with self.assertRaises(DebugError):
            subprocess_run(['gh', 'issue', 'create', '--repo', REPO, '--title', 't', '--body', 'inline'])

    def test_skill_routes_debug_lazily(self):
        root = Path(__file__).resolve().parents[1] / 'skills' / 'quorum'
        skill = (root / 'SKILL.md').read_text(encoding='utf-8')
        guide = (root / 'references' / 'debug.md').read_text(encoding='utf-8')
        self.assertIn('references/debug.md', skill)
        self.assertIn('Ordinary quorum use does not read that file.', skill)
        self.assertNotIn('debug collect', skill)
        self.assertIn('debug collect', guide)
        self.assertIn('live_observation', guide)
        self.assertNotIn('.ievo/', guide)


class DebugReviewTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.github = FakeGitHub()
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.service = open_service(self.root, self.clock, self.github)

    def tearDown(self):
        self.tmp.cleanup()

    def _stuck(self, until='stop'):
        write_session(self.root, deliveries=[{'message_id': MID, 'state': 'submitted'}])
        activated = self.service.activate('team', 'proj', REPO, until=until)
        self.clock.advance(901)
        return activated

    def test_manual_collect_does_not_claim_supervision(self):
        write_session(self.root)
        activated = self.service.activate('team', 'proj', REPO)
        self.assertEqual(activated['supervision']['mode'], 'handoff')
        self.assertFalse(activated['host_alive'])
        self.assertFalse(activated['complete'])
        self.assertIn('collector_not_supervising', activated['blockers'])
        self.assertFalse(activated['continuous_observation'])
        collected = self.service.collect()
        self.assertFalse(collected['continuous_observation'])
        self.assertFalse(self.service.status()['complete'])

    def test_watch_is_alive_only_while_the_host_runs_and_stop_ends_it(self):
        write_session(self.root)
        self.service.activate('team', 'proj', REPO)
        seen = {}

        def on_cycle():
            seen['status'] = self.service.status()

        result = self.service.watch(interval=30, cycles=1, sleeper=lambda _seconds: None, on_cycle=on_cycle)
        self.assertTrue(seen['status']['host_alive'])
        self.assertTrue(seen['status']['continuous_observation'])
        self.assertEqual(seen['status']['supervision']['mode'], 'attached')
        self.assertTrue(seen['status']['complete'])
        self.assertEqual(result['cycles_completed'], 1)
        self.assertFalse(result['host_alive'])
        self.assertFalse(result['continuous_observation'])
        self.assertEqual(result['supervision']['mode'], 'handoff')
        self.assertIn('collector_not_supervising', result['blockers'])

        def sleeper(_seconds):
            self.service.stop()

        self.service.activate('team', 'proj', REPO)
        stopped = self.service.watch(interval=30, cycles=4, sleeper=sleeper)
        self.assertEqual(stopped['state'], 'stopped')
        self.assertEqual(stopped['cycles_completed'], 1)
        self.assertFalse(stopped['host_alive'])

    def test_dead_and_stale_hosts_are_not_reported_alive(self):
        from quorum_debug import iso
        self.service.activate('team', 'proj')
        with self.service._lock():
            run = self.service._load()
            run['host'] = {
                'pid': os.getpid(), 'token': 'abc', 'heartbeat_at': iso(self.clock()),
                'interval_seconds': 30, 'state': 'supervising',
            }
            self.service._save(run)
        self.service.alive_check = lambda _pid: False
        dead = self.service.status()
        self.assertFalse(dead['host_alive'])
        self.assertFalse(dead['continuous_observation'])
        self.assertEqual(dead['supervision']['host_state'], 'dead')
        self.assertEqual([item['reason'] for item in dead['gaps']], ['collector_host_dead'])
        again = self.service.status()
        self.assertEqual(len(again['gaps']), 1)
        with self.service._lock():
            run = self.service._load()
            run['host'] = {
                'pid': 12345, 'token': 'abc', 'heartbeat_at': '2026-10-27T10:00:00Z',
                'interval_seconds': 30, 'state': 'supervising',
            }
            self.service._save(run)
        self.service.alive_check = lambda _pid: True
        stale = self.service.status()
        self.assertFalse(stale['host_alive'])
        self.assertEqual(stale['supervision']['host_state'], 'dead')

    def test_second_watch_is_refused_while_the_recorded_host_is_alive(self):
        from quorum_debug import iso
        self.service.activate('team', 'proj')
        with self.service._lock():
            run = self.service._load()
            run['host'] = {
                'pid': os.getpid(), 'token': 'other', 'heartbeat_at': iso(self.clock()),
                'interval_seconds': 30, 'state': 'supervising',
            }
            self.service._save(run)
        with self.assertRaises(Exception) as caught:
            self.service.watch(cycles=1, sleeper=lambda _seconds: None)
        self.assertEqual(caught.exception.code, 'debug_busy')

    def test_expiry_during_watch_sleep_stops_the_host(self):
        self.service.activate('team', 'proj', until='1h')

        def sleeper(_seconds):
            self.clock.advance(7200)

        result = self.service.watch(interval=5, cycles=3, sleeper=sleeper)
        self.assertEqual(result['state'], 'expired')
        self.assertEqual(result['cycles_completed'], 1)
        self.assertFalse(result['host_alive'])

    def test_intent_is_durable_before_create_and_exceptions_do_not_duplicate(self):
        self._stuck()
        seen = {}

        def before_create(_repo, _title, _body):
            saved = json.loads((self.root / 'debug' / 'current.json').read_text(encoding='utf-8'))
            entry = saved['publication'][0]
            seen['entry'] = entry

        self.github.before_create = before_create
        self.github.create_raises = subprocess.TimeoutExpired(['gh', 'issue', 'create'], 20)
        self.assertEqual(self.service.collect()['publication'], 'uncertain')
        self.assertEqual(seen['entry']['status'], 'intent')
        self.assertEqual(seen['entry']['operation'], 'create')
        self.assertTrue(seen['entry']['submitted'])
        self.assertEqual(len(self.github.creates()), 1)
        self.github.create_raises = None
        self.service.publish()
        self.assertEqual(len(self.github.creates()), 1)
        self.github.create_raises = OSError('spawn failed')
        self.service.publish()
        self.assertEqual(len(self.github.creates()), 1)
        self.github.create_raises = None
        self.github.search_issues = []
        # The saved failure is still the timeout, so a later clear search must not create.
        self.service.publish()
        self.assertEqual(len(self.github.creates()), 1)

    def test_oserror_create_can_be_retried_once_after_a_clear_search(self):
        self._stuck()
        self.github.create_raises = OSError('spawn failed')
        self.assertEqual(self.service.collect()['publication'], 'uncertain')
        self.assertEqual(len(self.github.creates()), 1)
        self.github.create_raises = None
        self.github.search_issues = []
        self.assertEqual(self.service.publish()['publication'], 'published')
        self.assertEqual(len(self.github.creates()), 2)
        self.service.publish()
        self.assertEqual(len(self.github.creates()), 2)

    def test_search_without_the_exact_marker_does_not_create_or_adopt(self):
        self._stuck()
        self.github.search_issues = [{'number': 4, 'body': 'mentioned Debug-Finding but not the marker line'}]
        self.assertEqual(self.service.collect()['publication'], 'uncertain')
        self.assertEqual(self.github.creates(), [])
        self.github.create_status = 'uncertain'
        self.github.search_issues = None
        self.service.publish()
        self.github.search_issues = [{'number': 9, 'body': 'Debug-Finding: ' + 'a' * 64}]
        self.service.publish()
        self.assertEqual(len(self.github.creates()), 1)
        self.assertIsNone(self.service.report()['publication']['ledger'][0].get('issue_number'))

    def test_stop_and_expiry_are_rechecked_after_search_and_before_create(self):
        self._stuck()
        self.github.before_search = self.service.stop
        self.assertEqual(self.service.collect()['publication'], 'ended')
        self.assertEqual(self.github.creates(), [])
        self.assertEqual(self.service.status()['state'], 'stopped')
        self.clock.advance(1)
        self._stuck(until='1h')

        def expire():
            self.clock.advance(4000)

        self.github.before_search = expire
        self.assertEqual(self.service.collect()['publication'], 'ended')
        self.assertEqual(self.github.creates(), [])
        self.assertEqual(self.service.status()['state'], 'expired')

    def test_comment_timeout_is_not_repeated_and_a_visible_marker_reconciles(self):
        self._stuck()
        self.service.collect()
        from quorum_state import LocalLedger
        ledger = LocalLedger(self.root / 'sessions' / SID)
        ledger.setting('delivery:' + MID, {'state': 'offered'})
        ledger.db.close()
        self.github.comment_raises = subprocess.TimeoutExpired(['gh', 'issue', 'comment'], 20)
        self.service.collect()
        self.assertEqual(len(self.github.comments()), 1)
        self.github.comment_raises = None
        self.service.publish()
        self.assertEqual(len(self.github.comments()), 1)
        self.github.issue_comments = [{'body': self.github.comments()[0][3]}]
        reconciled = self.service.publish()
        self.assertEqual(len(self.github.comments()), 1)
        self.assertEqual(reconciled['publication'], 'published')

    def test_comment_oserror_retries_once_after_view_shows_it_is_absent(self):
        self._stuck()
        self.service.collect()
        from quorum_state import LocalLedger
        ledger = LocalLedger(self.root / 'sessions' / SID)
        ledger.setting('delivery:' + MID, {'state': 'offered'})
        ledger.db.close()
        self.github.comment_raises = OSError('spawn failed')
        self.service.collect()
        self.assertEqual(len(self.github.comments()), 1)
        self.github.comment_raises = None
        self.service.publish()
        self.assertEqual(len(self.github.comments()), 2)
        self.service.publish()
        self.assertEqual(len(self.github.comments()), 2)

    def test_expiry_during_view_does_not_comment(self):
        self._stuck(until='1h')
        self.service.collect()
        from quorum_state import LocalLedger
        ledger = LocalLedger(self.root / 'sessions' / SID)
        ledger.setting('delivery:' + MID, {'state': 'offered'})
        ledger.db.close()

        def expire():
            self.clock.advance(4000)

        self.github.before_view = expire
        self.assertEqual(self.service.collect()['publication'], 'ended')
        self.assertEqual(self.github.comments(), [])
        self.assertEqual(self.service.status()['state'], 'expired')

    def test_empty_snapshot_does_not_infer_a_reply_loop_or_model_turns(self):
        replies = []
        for index, sender in enumerate(('sender-a', 'sender-b', 'sender-a', 'sender-b')):
            replies.append({
                'message_id': f'88888888-8888-4888-8888-88888888888{index}', 'state': 'acked', 'reply_to': True,
                'message': {'conversation_id': 'conv-loop', 'sender_id': sender, 'recipient_id': 'sender-b'},
            })
        write_session(self.root, deliveries=replies, listener={'run_id': 'run-a', 'state': 'waiting', 'handled': 0})
        self.service.activate('team', 'proj')
        for _ in range(3):
            self.service.collect()
        report = self.service.report()
        self.assertFalse(any(item['rule'] == 'suspected_reply_loop' for item in report['findings']))
        observation = next(item for item in report['observations'] if item['kind'] == 'historical_reply_count')
        self.assertFalse(observation['reply_loop'])
        self.assertEqual(observation['model_turns'], 'unavailable')
        self.assertFalse(observation['definite'])
        self.assertFalse(any(item.get('rule') == 'model_turn' for item in report['findings']))

    def test_reader_projects_metadata_and_skips_out_of_scope_deliveries(self):
        import sqlite3
        from quorum_debug_ledger import connect_readonly
        from quorum_state import LocalLedger
        write_session(self.root, deliveries=[{
            'message_id': MID, 'state': 'intent',
            'message': {'body': 'SECRET-BODY-VALUE', 'conversation_id': 'conv-1', 'sender_id': 'sender-a'},
        }])
        write_session(self.root, sid=OTHER, project='other', deliveries=[{
            'message_id': '55555555-5555-4555-8555-555555555555', 'state': 'submitted',
            'message': {'body': 'SECRET-BODY-VALUE'},
        }])
        ledger = LocalLedger(self.root / 'sessions' / SID)
        ledger.db.execute(
            'insert into events values (?,?,?,?)',
            ('attempt-1', 'state', json.dumps({'body': 'SECRET-BODY-VALUE', 'state': 'queued', 'generation': 2, 'code': 'ok', 'pid': 4}), 1),
        )
        ledger.db.commit()
        ledger.db.close()
        log = []
        real = connect_readonly

        def tracing(path):
            db = real(path)
            binding = Path(path).parent.name

            class Rows:
                def __init__(self, rows):
                    self.rows = rows

                def fetchone(self):
                    return self.rows[0] if self.rows else None

                def __iter__(self):
                    return iter(self.rows)

            class Proxy:
                def execute(self, sql, params=()):
                    compact = ' '.join(sql.split())
                    log.append((binding, compact))
                    lowered = compact.lower()
                    self_test.assertNotIn('select data', lowered)
                    self_test.assertNotIn('select key, data', lowered)
                    if 'from attempts' in lowered:
                        self_test.assertNotIn('reply', lowered)
                    cursor = db.execute(sql, params)
                    rows = cursor.fetchall()
                    for row in rows:
                        for cell in row:
                            if isinstance(cell, str) and 'SECRET' in cell:
                                raise AssertionError(compact)
                    return Rows(rows)

                def close(self):
                    db.close()

            return Proxy()

        self_test = self
        with patch('quorum_debug_ledger.connect_readonly', tracing):
            self.service.activate('team', 'proj')
        assert_redacted(self, self.root)
        self.assertTrue(any(binding == SID and 'delivery:%' in sql for binding, sql in log))
        self.assertFalse(any(binding == OTHER and 'delivery' in sql for binding, sql in log))
        self.assertFalse(any('select data from settings' in sql.lower() for _binding, sql in log))

    def test_gh_writes_use_body_file_and_drop_it_after_timeout(self):
        from quorum_debug_publish import GhCliPublisher
        bodies = []

        class Completed:
            def __init__(self, code, stdout):
                self.returncode = code
                self.stdout = stdout

        def runner(args, timeout=20):
            self.assertNotIn('--body', args)
            self.assertIn('--body-file', args)
            path = Path(args[args.index('--body-file') + 1])
            self.assertTrue(path.is_file())
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            bodies.append(path.read_text(encoding='utf-8'))
            recorded.append(path)
            if args[1:3] == ['issue', 'create']:
                raise subprocess.TimeoutExpired(args, timeout)
            return Completed(0, '[]')

        recorded = []
        publisher = GhCliPublisher(runner=runner, body_dir=self.root)
        with self.assertRaises(subprocess.TimeoutExpired):
            publisher.create_issue(REPO, 'Quorum debug: stuck', 'Evidence-Hash: abcdefabcdefabcd\nDebug-Finding: ' + 'ab' * 32)
        self.assertEqual(bodies[0].splitlines()[-1], 'Debug-Finding: ' + 'ab' * 32)
        self.assertFalse(recorded[0].exists())
        calls = []

        def listing(args, timeout=20):
            calls.append(args)
            body = 'noise\nDebug-Finding: ' + ('cd' * 32) + '\n'
            exact = 'Debug-Finding: ' + ('ab' * 32)
            return Completed(0, json.dumps([
                {'number': 2, 'body': 'Debug-Finding: ' + ('ab' * 32) + 'extra'},
                {'number': 5, 'body': body if False else 'prefix\n' + exact + '\nEvidence-Hash: abcdefabcdefabcd\n'},
            ]))

        found = GhCliPublisher(runner=listing).search(REPO, 'ab' * 32)
        self.assertEqual([item['number'] for item in found['issues']], [2, 5])
        self.assertIn('--json', calls[0])
        self.assertIn('number,body', calls[0])
        self.assertNotIn('--body', calls[0])


def setUpModule():
    def blocked(*_args, **_kwargs):
        raise AssertionError('live subprocess blocked in quorum debug tests')
    global _SUBPROCESS
    _SUBPROCESS = patch('quorum_debug.subprocess.run', blocked)
    _SUBPROCESS.start()


def tearDownModule():
    _SUBPROCESS.stop()


if __name__ == '__main__':
    unittest.main()
