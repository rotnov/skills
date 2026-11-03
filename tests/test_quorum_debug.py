"""Quorum debug runs: local metadata, honest coverage, and mocked GitHub writes."""
import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
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
        self.before_probe = None
        self.before_search = None
        self.before_create = None
        self.before_view = None
        self.before_comment = None
        self.after_comment_retained = None
        self.view_status = 'ok'
        self.issue_body = ''
        self.issue_comments = []
        self.lose_accepted_comment = False

    def probe(self):
        if self.before_probe:
            hook = self.before_probe
            self.before_probe = None
            hook()
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
        self.issue_body = body
        return {'status': 'created', 'number': self.create_number,
                'url': f'https://github.com/{repo}/issues/{self.create_number}'}

    def comment(self, repo, number, body):
        self.calls.append(('comment', repo, number, body))
        if self.before_comment:
            hook = self.before_comment
            self.before_comment = None
            hook(repo, number, body)
        if self.lose_accepted_comment:
            self.lose_accepted_comment = False
            self.issue_comments.append({'body': body})
            raise subprocess.TimeoutExpired(['gh', 'issue', 'comment'], 20)
        if self.comment_raises:
            raise self.comment_raises
        if self.comment_status != 'commented':
            return {'status': 'uncertain'}
        self.issue_comments.append({'body': body})
        if self.after_comment_retained:
            hook = self.after_comment_retained
            self.after_comment_retained = None
            hook(repo, number, body)
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
        report = self.service.report()
        # Boolean reply flags without a nested target are not a connected chain.
        self.assertFalse(any(item['rule'] == 'suspected_reply_loop' for item in report['findings']))
        historical = [item for item in report['observations'] if item['kind'] == 'historical_reply_count']
        self.assertTrue(historical)
        self.assertTrue(all(not item['reply_loop'] and item['model_turns'] == 'unavailable' and not item['actionable']
                            for item in historical))
        from quorum_state import LocalLedger
        ledger = LocalLedger(self.root / 'sessions' / SID)
        ledger.setting('listener', {'run_id': 'run-a', 'state': 'waiting', 'handled': 0})
        ledger.setting('session-reception', {'enabled': False})
        ledger.db.close()
        for _ in range(8):
            self.service.collect()
        report = self.service.report()
        self.assertFalse(any(item['rule'] == 'excessive_empty_receive' for item in report['findings']))
        idle = [item for item in report['observations'] if item['kind'] == 'healthy_idle_samples']
        self.assertEqual(len(idle), 1)
        self.assertFalse(idle[0]['actionable'])
        self.assertEqual(idle[0]['invocations'], 'unavailable')
        self.assertEqual(idle[0]['model_turns'], 'unavailable')
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

    def test_real_publisher_discovers_accepted_create_by_indexed_digest_without_a_duplicate(self):
        from quorum_debug_publish import GhCliPublisher
        created = []
        comments = []

        class Completed:
            returncode = 0

            def __init__(self, stdout=''):
                self.stdout = stdout

        def runner(args, timeout=20):
            operation = args[1:3]
            if operation == ['auth', 'status']:
                return Completed()
            if operation == ['issue', 'create']:
                body = Path(args[args.index('--body-file') + 1]).read_text(encoding='utf-8')
                created.append({'number': 9, 'body': body})
                return Completed()  # GitHub accepted the issue; its URL response was lost.
            if operation == ['issue', 'list']:
                if not created:
                    return Completed('[]')
                finding = created[0]['body'].split('Debug-Finding: ', 1)[1].splitlines()[0]
                # Observed GitHub search boundary: the digest is indexed, while the
                # unquoted custom "Debug-Finding:<digest>" operand returns no hits.
                if args[args.index('--search') + 1] != finding + ' in:body':
                    return Completed('[]')
                return Completed(json.dumps([
                    {'number': 8, 'body': 'An incidental reference to ' + finding},
                    created[0],
                ]))
            if operation == ['issue', 'comment']:
                comments.append(args)
                return Completed()
            self.fail('unexpected GitHub operation: ' + repr(operation))

        publisher = GhCliPublisher(runner=runner, body_dir=self.root)
        service = open_service(self.root, self.clock, publisher)
        with patch('quorum_debug_publish.shutil.which', return_value='/fake/gh'):
            service.activate('team', 'proj', REPO)
            self.clock.advance(901)
            self.assertEqual(service.collect()['publication'], 'uncertain')
            self.assertEqual(len(created), 1)
            self.assertEqual(service.publish()['publication'], 'published')
            entry = service.report()['publication']['ledger'][0]
            self.assertEqual(entry['issue_number'], 9)
            self.assertTrue(entry['reconciled'])
            self.assertEqual(service.publish()['publication'], 'published')
        self.assertEqual(len(created), 1)
        self.assertEqual(comments, [])

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

    def test_shared_parent_child_usage_is_order_independent_across_restarts(self):
        from quorum_debug import DebugError
        for same_source in (False, True):
            for child_first in (False, True):
                with self.subTest(same_source=same_source, child_first=child_first), tempfile.TemporaryDirectory() as root:
                    service = open_service(root, self.clock, FakeGitHub())
                    service.activate('team', 'proj')
                    parent = self.usage(source='parent-host')
                    child = self.usage(source='parent-host' if same_source else 'child-host',
                                       shared_parent_sid=True, category='coordination', output_tokens=40)
                    for record in ((child, parent) if child_first else (parent, child)):
                        try:
                            service.add_usage(record)
                        except DebugError as error:
                            self.fail(f'parent/shared-child reports must be accepted in either order: {error}')
                        service = open_service(root, self.clock, FakeGitHub())
                    tokens = service.report()['tokens']
                    self.assertEqual(tokens['project']['actual'], 10)
                    self.assertEqual(tokens['coordination']['actual'], 'unavailable')
                    self.assertEqual(tokens['excluded_double_count'], 1)
                    self.assertEqual(len(tokens['records']), 1)
                    self.assertEqual(tokens['accounting'].get('provisional_shared_windows'), 0)
                    self.assertEqual(tokens['coverage']['reported_sources'],
                                     ['parent-host'] if same_source else ['child-host', 'parent-host'])

    def test_child_only_usage_is_provisional_until_a_matching_parent_report(self):
        self.service.activate('team', 'proj')
        for source in ('child-a', 'child-b'):
            tokens = self.service.add_usage(self.usage(source=source, shared_parent_sid=True,
                                                       category='coordination', output_tokens=40))
        self.assertEqual(tokens['coordination']['actual'], 96)
        self.assertEqual(tokens['project']['actual'], 'unavailable')
        self.assertEqual(tokens['excluded_double_count'], 0)
        self.assertEqual(tokens['accounting'].get('provisional_shared_windows'), 1)
        restored = open_service(self.root, self.clock, FakeGitHub())
        reconciled = restored.add_usage(self.usage(source='parent-host'))
        self.assertEqual(reconciled['coordination']['actual'], 'unavailable')
        self.assertEqual(reconciled['project']['actual'], 10)
        self.assertEqual(reconciled['excluded_double_count'], 2)
        self.assertEqual(reconciled['accounting'].get('provisional_shared_windows'), 0)

    def test_shared_attribution_distinguishes_identical_source_and_numeric_reports(self):
        for child_first in (False, True):
            with self.subTest(child_first=child_first), tempfile.TemporaryDirectory() as root:
                service = open_service(root, self.clock, FakeGitHub())
                service.activate('team', 'proj')
                parent, child = self.usage(), self.usage(shared_parent_sid=True)
                for record in ((child, parent) if child_first else (parent, child)):
                    service.add_usage(record)
                restored = open_service(root, self.clock, FakeGitHub())
                restored.add_usage(parent)
                tokens = restored.add_usage(child)
                self.assertEqual(tokens['project']['actual'], 10)
                self.assertEqual(tokens['excluded_double_count'], 1)
                self.assertEqual(len(tokens['records']), 1)

    def test_excluded_shared_usage_replay_is_idempotent_after_restart(self):
        self.service.activate('team', 'proj')
        self.service.add_usage(self.usage(source='parent-host'))
        child = self.usage(source='child-host', shared_parent_sid=True,
                           category='coordination', output_tokens=40)
        self.service.add_usage(child)
        restored = open_service(self.root, self.clock, FakeGitHub())
        for _ in range(2):
            tokens = restored.add_usage(child)
            self.assertEqual(tokens['excluded_double_count'], 1)
            self.assertEqual(tokens['project']['actual'], 10)
            self.assertEqual(tokens['coordination']['actual'], 'unavailable')
            self.assertEqual(len(tokens['records']), 1)

    def test_shared_usage_exclusion_preserves_category_quality_and_cache_semantics(self):
        cases = (
            ({'category': 'project', 'quality': 'actual', 'cache_semantics': 'disjoint', 'cache_read_tokens': 4},
             'project', 'actual', 14),
            ({'category': 'mixed', 'quality': 'estimated', 'cache_semantics': 'cache_inclusive',
              'cache_read_tokens': 4, 'cache_write_tokens': 3}, 'mixed_unallocated', 'estimated', 10),
        )
        for fields, category, quality, expected in cases:
            for child_first in (False, True):
                with self.subTest(category=category, child_first=child_first), tempfile.TemporaryDirectory() as root:
                    service = open_service(root, self.clock, FakeGitHub())
                    service.activate('team', 'proj')
                    parent = self.usage(source='parent-host', **fields)
                    child = self.usage(source='child-host', shared_parent_sid=True,
                                       category='coordination', output_tokens=40)
                    for record in ((child, parent) if child_first else (parent, child)):
                        service.add_usage(record)
                    tokens = service.report()['tokens']
                    self.assertEqual(tokens[category][quality], expected)
                    self.assertEqual(tokens['coordination'], {'actual': 'unavailable', 'estimated': 'unavailable'})
                    self.assertEqual(tokens['excluded_double_count'], 1)
                    self.assertIsNone(tokens['overhead_ratio'])

    def test_legacy_unidentified_exclusion_count_is_preserved_and_reported(self):
        self.service.activate('team', 'proj')
        path = self.root / 'debug' / 'current.json'
        legacy = json.loads(path.read_text())
        legacy['usage'].pop('excluded_records', None)
        legacy['usage']['excluded'] = 3
        path.write_text(json.dumps(legacy))
        self.service.add_usage(self.usage(source='parent-host'))
        child = self.usage(source='child-host', shared_parent_sid=True,
                           category='coordination', output_tokens=40)
        self.service.add_usage(child)
        tokens = open_service(self.root, self.clock, FakeGitHub()).add_usage(child)
        self.assertEqual(tokens['project']['actual'], 10)
        self.assertEqual(tokens['excluded_double_count'], 4)
        self.assertEqual(tokens['accounting'].get('unidentified_legacy_exclusions'), 3)


class DebugGrantCheckpointTests(unittest.TestCase):
    def setUp(self):
        from quorum_debug import DebugService
        self.clock = Clock()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.service = DebugService(self.root, clock=self.clock, publisher=FakeGitHub(),
                                    alive_check=lambda _pid: True, pid=lambda: 1234)
        self.owner = DebugService(self.root, clock=self.clock, publisher=FakeGitHub(),
                                  alive_check=lambda _pid: True, pid=lambda: 1234)
        self.service.activate('team', 'proj', REPO, until='1m')
        self.service._attach_host(30)
        self.state = self.root / 'debug' / 'current.json'

    def _mark_checkpoint(self):
        # A real replace/write would reset this old timestamp, even if JSON stays identical.
        os.utime(self.state, ns=(1_700_000_000_000_000_000, 1_700_000_000_000_000_000))
        return self.state.read_bytes(), self.state.stat().st_mtime_ns

    def test_unchanged_sleep_ticks_leave_the_durable_checkpoint_untouched(self):
        content, modified = self._mark_checkpoint()
        self.assertTrue(self.service._sleep_until(30, self.clock.advance))
        self.assertEqual(self.state.read_bytes(), content)
        self.assertEqual(self.state.stat().st_mtime_ns, modified)

    def test_expiry_during_sleep_is_saved_before_the_watch_returns(self):
        self.clock.advance(59)
        self.assertFalse(self.service._sleep_until(30, self.clock.advance))
        saved = json.loads(self.state.read_text())
        self.assertEqual(saved['state'], 'expired')
        self.assertEqual(saved['expired_at'], '2026-10-27T12:01:00Z')
        self.assertEqual(self.owner.status()['state'], 'expired')

    def test_external_stop_is_reread_without_rewriting_its_checkpoint(self):
        self.owner.stop()
        content, modified = self._mark_checkpoint()
        self.assertFalse(self.service._grant_running())
        self.assertEqual(self.state.read_bytes(), content)
        self.assertEqual(self.state.stat().st_mtime_ns, modified)

    def test_replacement_run_is_not_overwritten_by_the_old_watch(self):
        self.owner.stop()
        replacement = self.owner.activate('team', 'proj')
        content, modified = self._mark_checkpoint()
        self.assertFalse(self.service._grant_running())
        self.assertEqual(json.loads(self.state.read_text())['run_id'], replacement['run_id'])
        self.assertEqual(self.state.read_bytes(), content)
        self.assertEqual(self.state.stat().st_mtime_ns, modified)

    def test_overdue_replacement_is_not_expired_by_stale_or_unattached_watchers(self):
        for watcher in (self.service, self.owner):
            with self.subTest(stale_host=watcher is self.service):
                self.owner.stop()
                replacement = self.owner.activate('team', 'proj', until='1m')
                self.clock.advance(61)
                content, modified = self._mark_checkpoint()
                self.assertFalse(watcher._grant_running())
                self.assertEqual(self.state.read_bytes(), content)
                self.assertEqual(self.state.stat().st_mtime_ns, modified)
                saved = json.loads(self.state.read_text())
                self.assertEqual(saved['run_id'], replacement['run_id'])
                self.assertEqual(saved['state'], 'active')
                self.assertIsNone(saved['expired_at'])

    def test_heartbeat_cannot_expire_or_rewrite_a_foreign_replacement(self):
        for watcher in (self.service, self.owner):
            with self.subTest(stale_host=watcher is self.service):
                self.owner.stop()
                self.owner.activate('team', 'proj', until='1m')
                self.clock.advance(61)
                content, modified = self._mark_checkpoint()
                self.assertFalse(watcher._heartbeat())
                self.assertEqual(self.state.read_bytes(), content)
                self.assertEqual(self.state.stat().st_mtime_ns, modified)

    def test_owned_heartbeat_persists_expiry_before_returning(self):
        self.clock.advance(60)
        self.assertFalse(self.service._heartbeat())
        saved = json.loads(self.state.read_text())
        self.assertEqual(saved['state'], 'expired')
        self.assertEqual(saved['expired_at'], '2026-10-27T12:01:00Z')

    def test_detach_cannot_claim_a_replacement_without_an_owned_host(self):
        for watcher in (self.service, self.owner):
            with self.subTest(stale_host=watcher is self.service):
                self.owner.stop()
                self.owner.activate('team', 'proj')
                content, modified = self._mark_checkpoint()
                watcher._detach_host('stopped')
                self.assertEqual(self.state.read_bytes(), content)
                self.assertEqual(self.state.stat().st_mtime_ns, modified)

    def test_stale_watch_result_does_not_mutate_the_replacement_grant(self):
        self.service._detach_host('fixture')
        original = json.loads(self.state.read_text())['run_id']
        expected = {}

        def replace_run():
            self.owner.stop()
            self.owner.activate('team', 'proj', until='1m')
            self.clock.advance(61)
            expected['content'], expected['modified'] = self._mark_checkpoint()
            expected['publisher_calls'] = list(self.service.publisher.calls)

        result = self.service.watch(cycles=1, sleeper=lambda _: None, on_cycle=replace_run)
        self.assertEqual(result['cycles_completed'], 0)
        self.assertEqual(result['run_id'], original)
        self.assertEqual(result['state'], 'stopped')
        self.assertEqual(self.state.read_bytes(), expected['content'])
        self.assertEqual(self.state.stat().st_mtime_ns, expected['modified'])
        self.assertEqual(self.service.publisher.calls, expected['publisher_calls'])

    def test_replacement_after_grant_check_is_not_collected_or_published(self):
        self.service._detach_host('fixture')
        original = json.loads(self.state.read_text())['run_id']
        expected = {}
        check = self.service._grant_running

        def replace_after_check(*args, **kwargs):
            running = check(*args, **kwargs)
            if running and not expected:
                self.owner.stop()
                self.owner.activate('team', 'proj', REPO, until='1m')
                self.clock.advance(61)
                expected['content'], expected['modified'] = self._mark_checkpoint()
                expected['publisher_calls'] = list(self.service.publisher.calls)
            return running

        with patch.object(self.service, '_grant_running', side_effect=replace_after_check):
            result = self.service.watch(cycles=1, sleeper=lambda _: None)
        self.assertEqual(result['run_id'], original)
        self.assertEqual(result['cycles_completed'], 0)
        self.assertEqual(self.state.read_bytes(), expected['content'])
        self.assertEqual(self.state.stat().st_mtime_ns, expected['modified'])
        self.assertEqual(self.service.publisher.calls, expected['publisher_calls'])

    def test_replacement_before_publication_origin_and_result_reload_is_untouched(self):
        self.service._detach_host('fixture')
        original = json.loads(self.state.read_text())['run_id']
        expected = {}
        publish = self.service._publish_pending

        def replace_before_publish(*args, **kwargs):
            self.owner.stop()
            self.owner.activate('team', 'proj', REPO, until='1m')
            self.clock.advance(61)
            expected['content'], expected['modified'] = self._mark_checkpoint()
            expected['publisher_calls'] = list(self.service.publisher.calls)
            return publish(*args, **kwargs)

        with patch.object(self.service, '_publish_pending', side_effect=replace_before_publish):
            result = self.service.watch(cycles=1, sleeper=lambda _: None)
        self.assertEqual(result['run_id'], original)
        self.assertEqual(result['cycles_completed'], 1)
        self.assertEqual(self.state.read_bytes(), expected['content'])
        self.assertEqual(self.state.stat().st_mtime_ns, expected['modified'])
        self.assertEqual(self.service.publisher.calls, expected['publisher_calls'])

    def test_missing_original_watch_result_fails_without_viewing_the_replacement(self):
        from quorum_debug import DebugError
        self.service._detach_host('fixture')
        original = json.loads(self.state.read_text())['run_id']
        expected = {}

        def remove_original():
            self.owner.stop()
            self.owner.activate('team', 'proj', until='1m')
            (self.root / 'debug' / 'runs' / (original + '.json')).unlink()
            self.clock.advance(61)
            expected['content'], expected['modified'] = self._mark_checkpoint()

        with self.assertRaises(DebugError) as caught:
            self.service.watch(cycles=1, sleeper=lambda _: None, on_cycle=remove_original)
        self.assertEqual(caught.exception.code, 'debug_grant_ended')
        self.assertEqual(self.state.read_bytes(), expected['content'])
        self.assertEqual(self.state.stat().st_mtime_ns, expected['modified'])

    def test_watch_expiry_between_publication_locks_is_durable(self):
        self.service._detach_host('fixture')
        record_probe = self.service._record_probe

        def expire_after_probe(*args, **kwargs):
            result = record_probe(*args, **kwargs)
            self.clock.advance(61)
            return result

        with patch.object(self.service, '_record_probe', side_effect=expire_after_probe):
            result = self.service.watch(cycles=1, sleeper=lambda _: None)
        self.assertEqual(result['state'], 'expired')
        saved = json.loads(self.state.read_text())
        self.assertEqual(saved['state'], 'expired')
        self.assertEqual(saved['host']['stopped_reason'], 'expired')

    def test_inflight_publication_settles_only_its_original_ledger_after_replacement(self):
        self.owner.stop()
        write_session(self.root, deliveries=[{'message_id': MID, 'state': 'submitted'}])
        original = self.service.activate('team', 'proj', REPO)['run_id']
        self.clock.advance(901)
        expected = {}

        def replace_during_create(_repo, _title, _body):
            self.owner.stop()
            self.owner.activate('team', 'proj', REPO, until='1m')
            self.clock.advance(61)
            expected['content'], expected['modified'] = self._mark_checkpoint()

        self.service.publisher.before_create = replace_during_create
        result = self.service.watch(cycles=1, sleeper=lambda _: None)
        self.assertEqual(result['run_id'], original)
        self.assertEqual(result['state'], 'stopped')
        self.assertEqual(self.state.read_bytes(), expected['content'])
        self.assertEqual(self.state.stat().st_mtime_ns, expected['modified'])
        archived = json.loads((self.root / 'debug' / 'runs' / (original + '.json')).read_text())
        self.assertEqual(archived['publication'][0]['status'], 'published')
        self.assertEqual(len(self.service.publisher.creates()), 1)
        self.assertEqual(self.service.publisher.comments(), [])


class DebugAnalyzeGuardTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.service = open_service(self.root, self.clock, FakeGitHub())
        self.run_id = self.service.activate('team', 'proj', until='1h')['run_id']
        self.finding = self._seed_finding(self.service)

    def _seed_finding(self, service):
        with service._lock():
            run = service._require()
            finding = service._finding(
                run, 'suspected_reply_loop', [{'conversation_id': 'synthetic-chain', 'message_id': MID}],
                identity={'conversation_id': 'synthetic-chain'}, definite=False, confidence='low',
                actionable=True, live=False, delivery_stage='unknown', analysis='pending',
                expected='A bounded connected reply chain.', observed='A synthetic metadata fixture.')
            service._upsert(run, finding)
            service._save(run)
        return finding

    def _analyze(self, conclusion='synthetic conclusion'):
        return self.service.analyze(self.finding['finding_id'], conclusion, 'medium', run_id=self.run_id)

    def test_pending_actionable_analysis_records_once_and_preserves_prior_result(self):
        result = self._analyze()
        self.assertEqual(result['analysis'], 'recorded')
        before = run_text(self.root)
        from quorum_debug import DebugError
        with self.assertRaises(DebugError) as caught:
            self._analyze('replacement conclusion')
        self.assertEqual(caught.exception.code, 'debug_conclusion_rejected')
        self.assertEqual(run_text(self.root), before)
        saved = json.loads(before)['findings'][0]
        self.assertEqual(saved['conclusion'], 'synthetic conclusion')
        self.assertEqual(saved['confidence'], 'medium')

    def test_nonactionable_and_not_required_findings_are_rejected(self):
        from quorum_debug import DebugError
        for fields in ({'actionable': False}, {'analysis': 'not_required'}):
            with self.subTest(fields=fields):
                with self.service._lock():
                    run = self.service._require()
                    run['findings'][0].update({'actionable': True, 'analysis': 'pending', **fields})
                    self.service._save(run)
                before = run_text(self.root)
                with self.assertRaises(DebugError) as caught:
                    self._analyze()
                self.assertEqual(caught.exception.code, 'debug_conclusion_rejected')
                self.assertEqual(run_text(self.root), before)

    def test_stopped_analysis_is_refused_without_changing_the_checkpoint(self):
        from quorum_debug import DebugError
        self.service.stop()
        before = run_text(self.root)
        with self.assertRaises(DebugError) as caught:
            self._analyze()
        self.assertEqual(caught.exception.code, 'debug_grant_ended')
        self.assertEqual(run_text(self.root), before)

    def test_analysis_persists_expiry_without_recording_a_conclusion(self):
        from quorum_debug import DebugError
        self.clock.advance(3600)
        with self.assertRaises(DebugError) as caught:
            self._analyze()
        self.assertEqual(caught.exception.code, 'debug_grant_ended')
        saved = json.loads(run_text(self.root))
        self.assertEqual(saved['state'], 'expired')
        self.assertEqual(saved['expired_at'], '2026-10-27T13:00:00Z')
        self.assertEqual(saved['findings'][0]['analysis'], 'pending')
        self.assertNotIn('conclusion', saved['findings'][0])

    def test_waiting_analysis_cannot_mutate_a_concurrent_replacement_run(self):
        from quorum_debug import DebugError
        from quorum_os import FileLock as RealFileLock
        collision, outcome = threading.Event(), {}

        class ObservingLock:
            def __init__(self, path):
                self.real = RealFileLock(path)

            def __enter__(self):
                try:
                    return self.real.__enter__()
                except ValueError:
                    collision.set()
                    raise

            def __exit__(self, *args):
                return self.real.__exit__(*args)

        def analyze():
            try:
                outcome['result'] = self._analyze()
            except Exception as error:
                outcome['error'] = error

        with patch('quorum_debug.FileLock', ObservingLock):
            with self.service._lock():
                thread = threading.Thread(target=analyze, name='stale-debug-analysis', daemon=True)
                thread.start()
                self.assertTrue(collision.wait(2), 'analysis must contend on the real run lock')
                old = self.service._require()
                old['state'] = 'stopped'
                self.service._archive(old)
                scope = self.service._scope('team', 'proj', None, '1h', (), ())
                replacement = self.service._new_run(scope)
                replacement['expires_at'] = '2026-10-27T11:00:00Z'
                replacement['findings'] = [dict(self.finding)]
                self.service._save(replacement)
                before = run_text(self.root)
            thread.join(3)
        self.assertFalse(thread.is_alive())
        self.assertIsInstance(outcome.get('error'), DebugError)
        self.assertEqual(outcome['error'].code, 'debug_invalid_scope')
        self.assertEqual(run_text(self.root), before)
        archived = json.loads((self.root / 'debug' / 'runs' / (self.run_id + '.json')).read_text())
        self.assertEqual(archived['findings'][0]['analysis'], 'pending')

    def test_analyze_cli_requires_the_exact_run_id_before_state_changes(self):
        from quorum import main
        before = run_text(self.root)
        argv = ['--state', str(self.root), 'debug', 'analyze', '--finding-id', self.finding['finding_id'],
                '--conclusion', 'synthetic conclusion', '--confidence', 'medium']
        with contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(SystemExit) as caught:
                main(argv)
        self.assertEqual(caught.exception.code, 2)
        self.assertEqual(run_text(self.root), before)
        output = io.StringIO()
        with contextlib.redirect_stdout(output), patch('quorum_debug.utcnow', self.clock):
            code = main(argv + ['--run-id', self.run_id])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(output.getvalue())['data']['run_id'], self.run_id)
        self.assertEqual(json.loads(run_text(self.root))['findings'][0]['conclusion'], 'synthetic conclusion')


class DebugPublicationUrlTests(unittest.TestCase):
    def test_created_url_accepts_case_only_repository_changes_and_refuses_other_repositories(self):
        from quorum_debug_publish import GhCliPublisher
        with tempfile.TemporaryDirectory() as root:
            for url, expected in (
                ('https://github.com/example/quorum-debug-test/issues/4', 'created'),
                ('https://github.com/EXAMPLE/QUORUM-DEBUG-TEST/issues/4', 'created'),
                ('https://github.com/other/quorum-debug-test/issues/4', 'uncertain'),
                ('https://github.com/example/other/issues/4', 'uncertain'),
            ):
                with self.subTest(url=url):
                    def runner(args, timeout):
                        self.assertEqual(args[:3], ['gh', 'issue', 'create'])
                        path = Path(args[args.index('--body-file') + 1])
                        self.assertEqual(path.read_text(), 'synthetic body')
                        return subprocess.CompletedProcess(args, 0, stdout=url)
                    publisher = GhCliPublisher(runner=runner, body_dir=root)
                    result = publisher.create_issue('Example/Quorum-Debug-Test', 'synthetic title', 'synthetic body')
                    self.assertEqual(result['status'], expected)
                    if expected == 'created':
                        self.assertEqual(result['number'], 4)


class DebugUnreadableScopeTests(unittest.TestCase):
    def setUp(self):
        from quorum_debug import DebugService
        self.clock = Clock()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.service = DebugService(self.root, clock=self.clock, publisher=FakeGitHub(),
                                    alive_check=lambda _pid: True, pid=lambda: 1234)

    def _corrupt(self, sid):
        path = self.root / 'sessions' / sid
        path.mkdir(parents=True)
        (path / 'ledger.sqlite').write_bytes(b'not a database')

    def test_unknown_scope_sources_are_separate_from_selected_missing_bindings(self):
        from quorum_state import LocalLedger
        # A readable database with no binding identity is also unscoped.
        empty = LocalLedger(self.root / 'sessions' / SID)
        empty.db.close()
        self._corrupt(OTHER)
        self.service.activate('team', 'proj')
        status = self.service.status()
        self.assertEqual(status['coverage'].get('unreadable_unscoped'), [SID, OTHER])
        self.assertEqual(status['coverage']['unreadable_bindings'], [])
        self.assertEqual(status['coverage']['unavailable_bindings'], [])
        self.assertIn('unreadable_unscoped', status['blockers'])
        self.assertNotIn('bindings_not_observed', status['blockers'])
        self.assertFalse(status['complete'])

    def test_healthy_and_unknown_sources_keep_honest_partial_coverage(self):
        write_session(self.root)
        self._corrupt(OTHER)
        self.service.activate('team', 'proj')
        observed = {}
        self.service.watch(cycles=1, sleeper=lambda _: None, on_cycle=lambda: observed.update(self.service.status()))
        self.assertTrue(observed['host_alive'])
        self.assertEqual(observed['collection']['state'], 'attached')
        self.assertEqual(observed['coverage']['participants'][0]['binding'], SID)
        self.assertEqual(observed['coverage'].get('unreadable_unscoped'), [OTHER])
        self.assertIn('unreadable_unscoped', observed['blockers'])
        self.assertNotIn('bindings_not_observed', observed['blockers'])
        self.assertFalse(observed['complete'])

    def test_selected_unreadable_binding_remains_a_confirmed_coverage_gap(self):
        self._corrupt(OTHER)
        status = self.service.activate('team', 'proj', bindings=(OTHER,))
        self.assertEqual(status['coverage']['unreadable_bindings'], [OTHER])
        self.assertEqual(status['coverage'].get('unreadable_unscoped'), [])
        self.assertIn('bindings_not_observed', status['blockers'])
        self.assertFalse(status['complete'])

    def test_scope_proved_before_a_metadata_failure_stays_scoped_unreadable(self):
        from quorum_state import LocalLedger
        write_session(self.root)
        ledger = LocalLedger(self.root / 'sessions' / SID)
        ledger.db.execute('drop table attempts')
        ledger.db.commit()
        ledger.db.close()
        status = self.service.activate('team', 'proj')
        self.assertEqual(status['coverage']['unreadable_bindings'], [SID])
        self.assertEqual(status['coverage'].get('unreadable_unscoped'), [])
        self.assertIn('bindings_not_observed', status['blockers'])


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


def evidence_line(body):
    for line in (body or '').splitlines():
        if line.startswith('Evidence-Hash: '):
            return line.split(': ', 1)[1]
    return None


def side_effect_artifact(path, attempt_ids):
    path.write_text(json.dumps({'events': [
        {'kind': 'side_effect', 'side_effect_key': 'job-1', 'attempt_id': attempt, 'stage': 'task_completed'}
        for attempt in attempt_ids
    ]}), encoding='utf-8')


class DebugCorrectionTests(unittest.TestCase):
    """Expected publication, scope, freshness, and coverage behavior."""

    def test_r1_material_versions_comment_once_and_reconcile_submitted_hash(self):
        with self.subTest(case='successive'):
            clock, gh, svc, path = self._importer()
            stages = [self._import(svc, path, ids)['publication'] for ids in (('a', 'b'), ('c',), ('d',))]
            self.assertEqual(stages, ['published', 'published', 'published'])
            self.assertEqual(len(gh.creates()), 1)
            self.assertEqual(len(gh.comments()), 2)
            self.assertEqual({call[1] for call in gh.comments()}, {REPO})
            self.assertEqual({call[2] for call in gh.comments()}, {gh.create_number})
            hashes = [evidence_line(call[3]) for call in gh.comments()]
            self.assertEqual(len(hashes), len(set(hashes)))
            self.assertTrue(all(line.startswith('Debug-Finding: ') for call in gh.comments() for line in call[3].splitlines() if line.startswith('Debug-Finding:')))
            self.assertEqual(svc.publish()['publication'], 'published')
            self.assertEqual(len(gh.comments()), 2)
            self.assertEqual(svc.report()['findings'][0]['evidence_hash'], hashes[-1])
        with self.subTest(case='accepted-but-lost'):
            clock, gh, svc, path = self._importer()
            self.assertEqual(self._import(svc, path, ('a', 'b'))['publication'], 'published')
            gh.lose_accepted_comment = True
            lost = self._import(svc, path, ('c',))
            self.assertEqual(lost['publication'], 'uncertain')
            self.assertEqual(len(gh.issue_comments), 1)
            lost_hash = evidence_line(gh.issue_comments[0]['body'])
            newer = self._import(svc, path, ('d',))
            self.assertEqual(newer['publication'], 'published')
            self.assertEqual(len(gh.issue_comments), 2)
            remote = [evidence_line(item['body']) for item in gh.issue_comments]
            self.assertEqual(remote[0], lost_hash)
            self.assertEqual(len(remote), len(set(remote)))
            self.assertEqual(len(gh.creates()), 1)
            self.assertEqual(svc.publish()['publication'], 'published')
            self.assertEqual(len(gh.issue_comments), 2)
        with self.subTest(case='timeout-absent'):
            clock, gh, svc, path = self._importer()
            self._import(svc, path, ('a', 'b'))
            self._import(svc, path, ('c',))
            accepted = len(gh.issue_comments)
            gh.comment_raises = subprocess.TimeoutExpired(['gh', 'issue', 'comment'], 20)
            blocked = self._import(svc, path, ('d',))
            self.assertEqual(blocked['publication'], 'uncertain')
            self.assertEqual(len(gh.issue_comments), accepted)
            gh.comment_raises = None
            self._import(svc, path, ('e',))
            self.assertEqual(svc.publish()['publication'], 'uncertain')
            self.assertEqual(len(gh.issue_comments), accepted)
            self.assertTrue(svc.report()['pending_reports'])

    def test_r1_inflight_success_certifies_only_the_submitted_version(self):
        with self.subTest(case='create-during-newer-evidence'):
            self._inflight_create_during_newer_evidence()
        with self.subTest(case='comment-unavailable'):
            self._inflight_comment_during_newer_evidence('unavailable')
        with self.subTest(case='comment-uncertain'):
            self._inflight_comment_during_newer_evidence('uncertain')
        with self.subTest(case='older-success-preserves-newer-timeout'):
            self._older_success_preserves_newer_timeout()

    def test_r2_old_run_results_do_not_change_the_replacement_run(self):
        for boundary in ('probe', 'search', 'view', 'create'):
            with self.subTest(boundary=boundary):
                self._old_response_stays_on_origin(boundary)
        with self.subTest(boundary='same-run'):
            self._same_run_updates_its_own_issue()

    def test_r3_stale_sources_stay_historical_until_a_fresh_sample(self):
        with self.subTest(case='fresh-control'):
            report, gh = self._aged_delivery(lambda root: None)
            self.assertEqual(report['findings'][0]['rule'], 'stuck_unacknowledged')
            self.assertTrue(report['findings'][0]['live_observation'])
            self.assertEqual(len(gh.creates()), 1)
        for variant in ('out-of-scope', 'vanished', 'unreadable'):
            with self.subTest(case=variant):
                report, gh = self._aged_delivery(variant)
                self.assertFalse(any(item.get('rule') == 'stuck_unacknowledged' for item in report['findings']))
                self.assertEqual(gh.creates(), [])
                self.assertTrue(any(event.get('message_id') == MID for event in report['timeline']))
                self.assertTrue(any(gap.get('reason') == 'source_not_observed' for gap in report['gaps']))
        with self.subTest(case='returns'):
            clock, gh, svc, root = self._delivery_service()
            self._move_out_of_scope(root)
            clock.advance(901)
            svc.collect()
            self.assertEqual(gh.creates(), [])
            self._restore_scope(root)
            svc.collect()
            self.assertEqual(gh.creates(), [])
            self.assertTrue(any(gap.get('reason') == 'source_not_observed' and gap.get('closed') for gap in svc.report()['gaps']))
            clock.advance(901)
            svc.collect()
            report = svc.report()
            self.assertEqual(len(gh.creates()), 1)
            self.assertTrue(report['findings'][0]['live_observation'])
            self.assertEqual(report['findings'][0]['rule'], 'stuck_unacknowledged')

    def test_r4_watch_without_a_readable_source_stays_partial(self):
        for case in ('absent', 'out-of-scope', 'unreadable'):
            with self.subTest(case=case):
                observed = self._watch_case(case)
                self.assertTrue(observed['host_alive'])
                self.assertFalse(observed['complete'])
                self.assertEqual(observed['activation'], 'partial')
                self.assertEqual(observed['coverage']['participants'], [])
                self.assertFalse(observed['collection']['live_observation'])
                expected = 'unreadable_source' if case == 'unreadable' else 'no_readable_source'
                self.assertIn(expected, observed['blockers'])
        with self.subTest(case='readable'):
            observed = self._watch_case('readable')
            self.assertTrue(observed['host_alive'])
            self.assertTrue(observed['complete'])
            self.assertEqual(observed['activation'], 'ready')
            self.assertEqual(observed['coverage']['participants'][0]['binding'], SID)
            self.assertNotIn('no_readable_source', observed['blockers'])
            self.assertNotIn('unreadable_source', observed['blockers'])

    def _remote_hashes(self, gh):
        hashes = []
        if gh.issue_body:
            hashes.append(evidence_line(gh.issue_body))
        hashes.extend(evidence_line(item['body']) for item in gh.issue_comments)
        return hashes

    def _inflight_create_during_newer_evidence(self):
        clock, gh, svc, path = self._importer()
        nested = {}

        def change(_repo, _title, _body):
            gh.before_create = None
            gh.available = False
            nested['result'] = self._import(svc, path, ('d',))
            gh.available = True

        gh.before_create = change
        result = self._import(svc, path, ('a', 'b'))
        report = svc.report()
        created = evidence_line(gh.creates()[0][3])
        current = report['findings'][0]['evidence_hash']
        self.assertEqual(nested['result']['publication'], 'delayed')
        self.assertEqual(created, '74fae6cbc2b5d119')
        self.assertEqual(current, '738737f5fb6cdaec')
        self.assertEqual(len(gh.creates()), 1)
        self.assertEqual(self._remote_hashes(gh), [created, current])
        self.assertEqual([evidence_line(call[3]) for call in gh.comments()], [current])
        entry = report['publication']['ledger'][0]
        self.assertEqual(entry['status'], 'published')
        self.assertEqual(entry['evidence_hash'], current)
        self.assertEqual(entry['issue_number'], gh.create_number)
        self.assertEqual(entry['repository'], REPO)
        self.assertFalse(report['pending_reports'])
        self.assertEqual(result['publication'], 'published')
        repeated = svc.publish()
        self.assertEqual(repeated['publication'], 'published')
        self.assertEqual(len(gh.creates()), 1)
        self.assertEqual(len(gh.comments()), 1)
        self.assertEqual(self._remote_hashes(gh), [created, current])

    def _inflight_comment_during_newer_evidence(self, nested_mode):
        clock, gh, svc, path = self._importer()
        self.assertEqual(self._import(svc, path, ('a', 'b'))['publication'], 'published')
        nested = {}

        def change(_repo, _number, _body):
            gh.available = nested_mode == 'uncertain'
            nested['result'] = self._import(svc, path, ('d',))
            gh.available = True

        gh.before_comment = change
        result = self._import(svc, path, ('c',))
        report = svc.report()
        current = report['findings'][0]['evidence_hash']
        submitted = 'a0ef388ac08dc45a'
        remote = self._remote_hashes(gh)
        self.assertEqual(nested['result']['publication'], 'delayed' if nested_mode == 'unavailable' else 'uncertain')
        self.assertEqual(current, 'd6476b98f3bc4a69')
        self.assertEqual(remote, ['74fae6cbc2b5d119', submitted])
        self.assertNotIn(current, remote)
        self.assertEqual(len(gh.creates()), 1)
        self.assertEqual(len(gh.comments()), 1)
        entry = report['publication']['ledger'][0]
        self.assertEqual(entry['evidence_hash'], submitted)
        self.assertNotEqual(entry['evidence_hash'], current)
        self.assertTrue(report['pending_reports'])
        self.assertNotEqual(result['publication'], 'published')
        followed = svc.publish()
        self.assertEqual(followed['publication'], 'published')
        self.assertEqual(self._remote_hashes(gh), ['74fae6cbc2b5d119', submitted, current])
        self.assertEqual([evidence_line(call[3]) for call in gh.comments()], [submitted, current])
        published = svc.report()['publication']['ledger'][0]
        self.assertEqual(published['status'], 'published')
        self.assertEqual(published['operation'], 'comment')
        self.assertEqual(published['evidence_hash'], current)
        self.assertFalse(published['submitted'])
        self.assertFalse(svc.report()['pending_reports'])
        self.assertEqual(svc.publish()['publication'], 'published')
        self.assertEqual(len(gh.comments()), 2)
        self.assertEqual(len(gh.creates()), 1)

    def _older_success_preserves_newer_timeout(self):
        clock, gh, svc, path = self._importer()
        self._import(svc, path, ('a', 'b'))
        nested = {}

        def after_visible(_repo, _number, _body):
            gh.comment_raises = subprocess.TimeoutExpired(['gh', 'issue', 'comment'], 20)
            nested['result'] = self._import(svc, path, ('d',))
            nested['ledger'] = svc.report()['publication']['ledger'][0]
            gh.comment_raises = None

        gh.after_comment_retained = after_visible
        result = self._import(svc, path, ('c',))
        report = svc.report()
        current = report['findings'][0]['evidence_hash']
        before = nested['ledger']
        after = report['publication']['ledger'][0]
        self.assertEqual(nested['result']['publication'], 'uncertain')
        self.assertEqual(before['evidence_hash'], 'd6476b98f3bc4a69')
        self.assertEqual(before['status'], 'uncertain')
        self.assertTrue(before['submitted'])
        self.assertEqual(before['failure'], 'timeout')
        self.assertEqual(before['operation'], 'comment')
        self.assertEqual(after['evidence_hash'], current)
        self.assertEqual(after['status'], 'uncertain')
        self.assertTrue(after['submitted'])
        self.assertEqual(after['failure'], 'timeout')
        self.assertEqual(after['operation'], 'comment')
        self.assertEqual(current, 'd6476b98f3bc4a69')
        self.assertEqual(self._remote_hashes(gh), ['74fae6cbc2b5d119', 'a0ef388ac08dc45a'])
        self.assertNotIn(current, self._remote_hashes(gh))
        self.assertEqual(len(gh.creates()), 1)
        self.assertEqual(len(gh.comments()), 2)
        self.assertEqual(len(gh.issue_comments), 1)
        self.assertTrue(report['pending_reports'])
        self.assertEqual(result['publication'], 'uncertain')
        repeated = svc.publish()
        self.assertEqual(repeated['publication'], 'uncertain')
        self.assertEqual(len(gh.comments()), 2)
        self.assertEqual(len(gh.issue_comments), 1)
        self.assertEqual(svc.report()['publication']['ledger'][0]['failure'], 'timeout')
        self.assertTrue(svc.report()['pending_reports'])

    def _importer(self):
        clock, gh = Clock(), FakeGitHub()
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        return clock, gh, open_service(root, clock, gh), root / 'artifact.json'

    def _import(self, svc, path, attempt_ids):
        side_effect_artifact(path, attempt_ids)
        if not getattr(svc, '_correction_activated', False):
            svc.activate('team', 'proj', REPO, sources=('imported-artifact',))
            svc._correction_activated = True
        return svc.import_artifact(path)

    def _old_response_stays_on_origin(self, boundary):
        clock, gh = Clock(), FakeGitHub()
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        svc = open_service(root, clock, gh)
        current_path = root / 'current.json'
        original_path = root / 'original.json'
        side_effect_artifact(original_path, ('a', 'b'))
        side_effect_artifact(current_path, ('a', 'b'))
        started = svc.activate('team', 'proj', 'example/old', sources=('imported-artifact',))

        def switch(*_ignored):
            gh.before_probe = gh.before_search = gh.before_view = gh.before_create = None
            gh.available = False
            switched['stopped'] = svc.stop()['state']
            clock.advance(1)
            switched['new'] = svc.activate('team', 'proj', 'example/new', sources=('imported-artifact',))
            switched['imported'] = svc.import_artifact(original_path)
            gh.available = True

        switched = {}
        if boundary == 'view':
            svc.import_artifact(current_path)
            side_effect_artifact(current_path, ('c',))
            gh.before_view = switch
            svc.import_artifact(current_path)
        elif boundary == 'create':
            gh.before_create = switch
            svc.import_artifact(current_path)
        elif boundary == 'search':
            gh.before_search = switch
            svc.import_artifact(current_path)
        else:
            gh.before_probe = switch
            svc.import_artifact(current_path)
        current = svc.report()
        archived = svc.report(started['run_id'])
        self.assertEqual(switched['stopped'], 'stopped')
        self.assertEqual(current['run_id'], switched['new']['run_id'])
        self.assertNotEqual(current['run_id'], archived['run_id'])
        self.assertEqual(current['scope']['report_repository'], 'example/new')
        self.assertEqual(archived['scope']['report_repository'], 'example/old')
        self.assertEqual(current['findings'][0]['finding_id'], archived['findings'][0]['finding_id'])
        self.assertTrue(current['pending_reports'])
        self.assertFalse(any(item.get('issue_number') for item in current['publication']['ledger']))
        self.assertFalse(any(item.get('repository') == 'example/old' for item in current['publication']['ledger']))
        self.assertFalse(any(call[0] == 'comment' and call[1] == 'example/new' for call in gh.calls))
        self.assertFalse(any(call[0] == 'view' and call[1] == 'example/new' for call in gh.calls))
        if boundary == 'create':
            self.assertEqual([call[1] for call in gh.creates()], ['example/old'])
            origin = archived['publication']['ledger'][0]
            self.assertEqual(origin['repository'], 'example/old')
            self.assertEqual(origin['issue_number'], 4)
            self.assertEqual(origin['status'], 'published')
        elif boundary == 'view':
            self.assertEqual([call[1] for call in gh.creates()], ['example/old'])
            self.assertEqual(gh.comments(), [])
        else:
            self.assertEqual(gh.creates(), [])
        gh.issue_body = 'An unrelated issue in example/new, without a Debug-Finding marker'
        gh.issue_comments = []
        side_effect_artifact(current_path, ('e',))
        svc.import_artifact(current_path)
        self.assertFalse(any(call[0] == 'comment' and call[1:][:2] == ['example/new', 4] for call in gh.calls))
        preserved = svc.report(started['run_id'])
        self.assertEqual(preserved['scope']['report_repository'], 'example/old')
        if boundary == 'create':
            self.assertEqual(preserved['publication']['ledger'][0]['issue_number'], 4)
            self.assertEqual(preserved['publication']['ledger'][0]['repository'], 'example/old')

    def _same_run_updates_its_own_issue(self):
        clock, gh, svc, path = self._importer()
        self.assertEqual(self._import(svc, path, ('a', 'b'))['publication'], 'published')
        self.assertEqual(self._import(svc, path, ('c',))['publication'], 'published')
        self.assertEqual(len(gh.creates()), 1)
        self.assertEqual(len(gh.comments()), 1)
        self.assertEqual(gh.comments()[0][1], REPO)
        self.assertIn('Debug-Finding: ', gh.comments()[0][3])
        self.assertTrue(any(line.startswith('Debug-Finding: ') for line in gh.issue_body.splitlines()))
        gh.issue_body = 'An unrelated issue with a reused number and no Debug-Finding marker'
        gh.issue_comments = []
        blocked = self._import(svc, path, ('d',))
        self.assertEqual(blocked['publication'], 'uncertain')
        self.assertEqual(len(gh.comments()), 1)
        self.assertTrue(svc.report()['pending_reports'])

    def _delivery_service(self):
        clock, gh = Clock(), FakeGitHub()
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        write_session(root, deliveries=[{'message_id': MID, 'state': 'offered'}])
        svc = open_service(root, clock, gh)
        svc.activate('team', 'proj', REPO)
        return clock, gh, svc, root

    def _aged_delivery(self, variant):
        clock, gh, svc, root = self._delivery_service()
        if variant == 'out-of-scope':
            self._move_out_of_scope(root)
        elif variant == 'vanished':
            import shutil
            shutil.rmtree(root / 'sessions' / SID)
        elif variant == 'unreadable':
            (root / 'sessions' / SID / 'ledger.sqlite').write_bytes(b'not a database')
        clock.advance(901)
        svc.collect()
        return svc.report(), gh

    def _move_out_of_scope(self, root):
        from quorum_state import LocalLedger
        ledger = LocalLedger(root / 'sessions' / SID)
        record = ledger.setting('session-agent')
        record['project_ref'] = 'different-project'
        ledger.setting('session-agent', record)
        ledger.db.close()

    def _restore_scope(self, root):
        from quorum_state import LocalLedger
        ledger = LocalLedger(root / 'sessions' / SID)
        record = ledger.setting('session-agent')
        record['project_ref'] = 'proj'
        ledger.setting('session-agent', record)
        ledger.db.close()

    def _watch_case(self, case):
        clock, gh = Clock(), FakeGitHub()
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        if case == 'out-of-scope':
            write_session(root, project='different-project')
        elif case == 'unreadable':
            target = root / 'sessions' / SID
            target.mkdir(parents=True)
            (target / 'ledger.sqlite').write_bytes(b'not a database')
        elif case == 'readable':
            write_session(root)
        svc = open_service(root, clock, gh)
        svc.activate('team', 'proj', REPO)
        observed = {}

        def inspect():
            observed.update(svc.status())

        svc.watch(cycles=1, sleeper=lambda _seconds: None, on_cycle=inspect)
        return observed


class DebugLockContentionTests(unittest.TestCase):
    """Real run.lock collisions. The holder is released on the first failed acquisition."""

    USAGE = {
        'source': 'codex', 'model': 'gpt-test', 'window_start': '2026-10-27T12:00:00Z',
        'window_end': '2026-10-27T13:00:00Z', 'category': 'project', 'quality': 'actual',
        'input_tokens': 8, 'output_tokens': 2,
    }

    def _contend(self, operation, *, until='5m', advance_seconds=0, release_on_collision=True):
        # Release the holder when the watcher first fails the real flock, before
        # joining watch. Holding through watch's return deadlocks a bounded retry.
        from quorum_os import FileLock as RealFileLock
        clock, github = Clock(), FakeGitHub()
        tmp = tempfile.TemporaryDirectory(prefix='quorum-debug-lock-')
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        watcher = open_service(root, clock, github)
        owner = open_service(root, clock, github)
        watcher.activate('team', 'proj', until=until, sources=('imported-artifact',))
        entered, release = threading.Event(), threading.Event()
        collision, holder_done, holder_finished = threading.Event(), threading.Event(), threading.Event()
        saves, collects, failures, holder_errors, control_errors = [], [], [], [], []
        outcome = {'hung': False, 'view': None, 'error': None, 'elapsed': 0}
        worker = {'thread': None}

        original_save = owner._save

        def held_save(run):
            saves.append(1)
            if len(saves) == 1:
                entered.set()
                if not release.wait(5):
                    raise RuntimeError('setup: holder was not released')
            return original_save(run)

        owner._save = held_save
        original_collect = watcher.collect

        def counting_collect(*args, **kwargs):
            collects.append(1)
            return original_collect(*args, **kwargs)

        watcher.collect = counting_collect

        class ObservingLock:
            def __init__(self, path):
                self._real = RealFileLock(path)

            def __enter__(self):
                try:
                    return self._real.__enter__()
                except ValueError as error:
                    if str(error) == 'worker_already_running':
                        failures.append(1)
                        first = not collision.is_set()
                        collision.set()
                        if first and release_on_collision and not holder_done.wait(5):
                            raise RuntimeError('setup: holder was not released after the real collision')
                    raise

            def __exit__(self, *args):
                return self._real.__exit__(*args)

        def invoke():
            try:
                if operation == 'status':
                    owner.status()
                elif operation == 'stop':
                    owner.stop()
                elif operation == 'usage':
                    owner.add_usage(dict(self.USAGE))
                else:
                    raise RuntimeError('setup: unknown operation')
            except Exception as exc:
                holder_errors.append(exc)
            finally:
                holder_finished.set()

        def on_cycle():
            thread = threading.Thread(target=invoke, name='debug-lock-holder', daemon=True)
            worker['thread'] = thread
            thread.start()
            if not entered.wait(3):
                raise RuntimeError('setup: public command did not hold the real lock')

        def control():
            try:
                if not collision.wait(5):
                    control_errors.append('setup: watcher never failed a real lock acquisition')
                    return
                if advance_seconds:
                    clock.advance(advance_seconds)
                release.set()
                if not holder_finished.wait(5):
                    control_errors.append('setup: holder did not finish after release')
            finally:
                release.set()
                holder_done.set()

        controller = None
        if release_on_collision:
            controller = threading.Thread(target=control, name='debug-lock-controller', daemon=True)
            controller.start()

        def execute():
            try:
                with patch('quorum_debug.FileLock', ObservingLock):
                    outcome['view'] = watcher.watch(
                        interval=1, cycles=1, sleeper=lambda _seconds: None, on_cycle=on_cycle)
            except Exception as exc:
                outcome['error'] = exc

        try:
            if release_on_collision:
                execute()
            else:
                started = time.monotonic()
                watch_thread = threading.Thread(target=execute, name='debug-lock-watch', daemon=True)
                watch_thread.start()
                watch_thread.join(2)
                outcome['elapsed'] = time.monotonic() - started
                outcome['hung'] = watch_thread.is_alive()
        finally:
            release.set()
            holder_done.set()
            thread = worker['thread']
            if thread is not None:
                thread.join(5)
            if controller is not None:
                controller.join(5)
        if control_errors:
            raise RuntimeError(control_errors[0])
        if holder_errors:
            raise RuntimeError('setup: holder failed: %s' % holder_errors[0])
        error = outcome.get('error')
        if isinstance(error, RuntimeError) and str(error).startswith('setup:'):
            raise error
        raw = json.loads((root / 'debug' / 'current.json').read_text(encoding='utf-8'))
        outcome.update(
            collision=collision.is_set(), failures=len(failures), saves=len(saves),
            collects=len(collects), error=error, state=raw.get('state'),
            host=raw.get('host') or {}, records=list((raw.get('usage') or {}).get('records') or []),
            publisher_calls=list(github.calls),
        )
        if not release_on_collision and not outcome['hung']:
            outcome['post_status'] = owner.status()
        return outcome

    def test_status_contention_lets_the_watch_detach(self):
        outcome = self._contend('status')
        self.assertTrue(outcome['collision'])
        self.assertGreaterEqual(outcome['failures'], 1)
        self.assertEqual(outcome['saves'], 1)
        self.assertIsNone(outcome['error'])
        self.assertEqual(outcome['collects'], 1)
        self.assertEqual(outcome['view']['cycles_completed'], 1)
        self.assertFalse(outcome['view']['host_alive'])
        self.assertEqual(outcome['state'], 'active')
        self.assertEqual(outcome['host'].get('state'), 'stopped')
        self.assertEqual(outcome['host'].get('stopped_reason'), 'cycle_limit')
        self.assertEqual(outcome['publisher_calls'], [])

    def test_usage_contention_records_once_and_detaches(self):
        outcome = self._contend('usage')
        self.assertTrue(outcome['collision'])
        self.assertGreaterEqual(outcome['failures'], 1)
        self.assertEqual(outcome['saves'], 1)
        self.assertIsNone(outcome['error'])
        self.assertEqual(outcome['collects'], 1)
        self.assertEqual(outcome['view']['cycles_completed'], 1)
        self.assertFalse(outcome['view']['host_alive'])
        self.assertEqual(len(outcome['records']), 1)
        self.assertEqual(outcome['records'][0]['input_tokens'], 8)
        self.assertEqual(outcome['host'].get('state'), 'stopped')
        self.assertEqual(outcome['publisher_calls'], [])

    def test_stop_contention_ends_the_watch_without_collecting(self):
        outcome = self._contend('stop')
        self.assertTrue(outcome['collision'])
        self.assertGreaterEqual(outcome['failures'], 1)
        self.assertEqual(outcome['saves'], 1)
        self.assertIsNone(outcome['error'])
        self.assertEqual(outcome['collects'], 0)
        self.assertEqual(outcome['view']['cycles_completed'], 0)
        self.assertEqual(outcome['view']['state'], 'stopped')
        self.assertFalse(outcome['view']['host_alive'])
        self.assertEqual(outcome['state'], 'stopped')
        self.assertEqual(outcome['host'].get('state'), 'stopped')
        self.assertEqual(outcome['host'].get('stopped_reason'), 'stopped')
        self.assertIsNone(outcome['host'].get('pid'))
        self.assertEqual(outcome['publisher_calls'], [])

    def test_expiry_during_contention_is_reread_before_further_work(self):
        outcome = self._contend('status', until='1m', advance_seconds=120)
        self.assertTrue(outcome['collision'])
        self.assertGreaterEqual(outcome['failures'], 1)
        self.assertEqual(outcome['saves'], 1)
        self.assertIsNone(outcome['error'])
        self.assertEqual(outcome['collects'], 0)
        self.assertEqual(outcome['view']['cycles_completed'], 0)
        self.assertEqual(outcome['view']['state'], 'expired')
        self.assertFalse(outcome['view']['host_alive'])
        self.assertEqual(outcome['state'], 'expired')
        self.assertEqual(outcome['host'].get('state'), 'stopped')
        self.assertEqual(outcome['host'].get('stopped_reason'), 'expired')
        self.assertEqual(outcome['publisher_calls'], [])

    def test_prolonged_contention_fails_closed_without_a_false_detach(self):
        from quorum_debug import DebugService
        with patch.object(DebugService, '_LOCK_ACQUIRE_SECONDS', 0.05, create=True):
            outcome = self._contend('status', release_on_collision=False)
        self.assertFalse(outcome['hung'])
        self.assertLess(outcome['elapsed'], 2)
        self.assertTrue(outcome['collision'])
        self.assertGreaterEqual(outcome['failures'], 1)
        self.assertIsNone(outcome['view'])
        self.assertEqual(getattr(outcome['error'], 'code', None), 'debug_busy')
        self.assertEqual(outcome['collects'], 0)
        self.assertEqual(outcome['host'].get('state'), 'supervising')
        self.assertNotEqual(outcome['host'].get('state'), 'stopped')
        self.assertTrue(outcome['post_status']['host_alive'])
        self.assertEqual(outcome['post_status']['supervision']['host_state'], 'supervising')
        self.assertEqual(outcome['publisher_calls'], [])

    def test_body_valueerror_is_not_treated_as_lock_contention(self):
        from quorum_debug import DebugService
        clock, github = Clock(), FakeGitHub()
        tmp = tempfile.TemporaryDirectory(prefix='quorum-debug-lock-body-')
        self.addCleanup(tmp.cleanup)
        service = open_service(Path(tmp.name), clock, github)
        service.activate('team', 'proj', sources=('imported-artifact',))
        calls = []

        def boom(_run):
            calls.append('save')
            raise ValueError('worker_already_running')

        service._save = boom
        caught = None
        with patch.object(DebugService, '_LOCK_ACQUIRE_SECONDS', 0.05, create=True):
            started = time.monotonic()
            try:
                service.status()
            except Exception as exc:
                caught = exc
            elapsed = time.monotonic() - started
        self.assertEqual(calls, ['save'])
        self.assertIsInstance(caught, ValueError)
        self.assertEqual(str(caught), 'worker_already_running')
        self.assertLess(elapsed, 1)
        self.assertEqual(github.calls, [])

    def test_second_watch_stays_busy_without_waiting_out_the_lock(self):
        from quorum_debug import iso
        clock = Clock()
        tmp = tempfile.TemporaryDirectory(prefix='quorum-debug-lock-second-')
        self.addCleanup(tmp.cleanup)
        service = open_service(Path(tmp.name), clock, FakeGitHub())
        service.activate('team', 'proj', sources=('imported-artifact',))
        with service._lock():
            run = service._load()
            run['host'] = {
                'pid': os.getpid(), 'token': 'other', 'heartbeat_at': iso(clock()),
                'interval_seconds': 30, 'state': 'supervising',
            }
            service._save(run)
        box = {}

        def run_watch():
            try:
                service.watch(cycles=1, sleeper=lambda _seconds: None)
            except Exception as exc:
                box['error'] = exc

        thread = threading.Thread(target=run_watch, daemon=True)
        thread.start()
        thread.join(1)
        self.assertFalse(thread.is_alive())
        self.assertEqual(getattr(box.get('error'), 'code', None), 'debug_busy')


class DebugReviewP2Tests(unittest.TestCase):
    """Reply linkage, idle samples, and local cloud ledger discovery."""

    CONV = 'dddddddd-dddd-4ddd-8ddd-dddddddddddd'
    AA = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'
    BB = 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb'
    CC = 'cccccccc-cccc-4ccc-8ccc-cccccccccccc'

    def _service(self, root):
        clock, github = Clock(), FakeGitHub()
        return clock, github, open_service(root, clock, github)

    def _peer(self, root, sid, agent):
        from quorum_state import LocalLedger
        ledger = LocalLedger(Path(root) / 'sessions' / sid)
        ledger.setting('session-agent', {
            'quorum': 'team', 'quorum_id': 'qid-1', 'project_ref': 'proj', 'role': 'peer',
            'engine': 'codex', 'agent_id': agent, 'session_ref': sid, 'transport': 'tool-inbox',
        })
        self.addCleanup(ledger.db.close)
        return ledger

    def _mid(self, number):
        return f'{number:08x}-0000-4000-8000-000000000000'

    def _message(self, ledger, number, sender, recipient, sequence, reply_to=None):
        message_id = self._mid(number)
        ledger.setting('delivery:' + message_id, {
            'state': 'acked', 'reply_to': bool(reply_to), 'notification': False,
            'message': {
                'id': message_id, 'conversation_id': self.CONV, 'sender_id': sender,
                'recipient_id': recipient, 'sequence': sequence, 'reply_to': reply_to,
                'notification': False, 'body': 'SECRET-BODY-VALUE',
            },
        })
        return message_id

    def _loops(self, report):
        return [item for item in report['findings'] if item['rule'] == 'suspected_reply_loop']

    def test_independent_tasks_do_not_publish_a_reply_loop(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        left, right = self._peer(root, SID, self.AA), self._peer(root, OTHER, self.BB)
        clock, github, service = self._service(root)
        service.activate('team', 'proj', REPO)
        for index, sender in enumerate((self.AA, self.BB, self.AA, self.BB)):
            holder, requester, responder = (right, self.BB, self.AA) if sender == self.AA else (left, self.AA, self.BB)
            task = self._message(holder, 100 + index * 2, requester, responder, 1 + index * 2)
            service.collect()
            clock.advance(1)
            self._message(left if holder is right else right, 101 + index * 2, responder, requester, 2 + index * 2, reply_to=task)
            service.collect()
            clock.advance(3600)
        report = service.report()
        self.assertFalse(self._loops(report))
        self.assertEqual(github.creates(), [])
        assert_redacted(self, root)
        self.assertNotIn('SECRET-BODY-VALUE', json.dumps(report))

    def test_connected_chain_publishes_once_in_sequence_order(self):
        from quorum_debug_ledger import read_scoped
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        first, second = self._peer(root, SID, self.AA), self._peer(root, OTHER, self.BB)
        clock, github, service = self._service(root)
        service.activate('team', 'proj', REPO)
        # Later sequences are inserted first and stored on the lexicographically first binding.
        self._message(second, 1, self.BB, self.AA, 1)
        for number, ledger, sender, recipient in (
                (5, first, self.BB, self.AA), (3, first, self.BB, self.AA),
                (4, second, self.AA, self.BB), (2, second, self.AA, self.BB)):
            self._message(ledger, number, sender, recipient, number, reply_to=self._mid(number - 1))
        snap = read_scoped(first.root / 'ledger.sqlite', SID, {'quorum': 'team', 'project_ref': 'proj', 'bindings': []})
        projected = {item['message_id']: item for item in snap['deliveries']}
        self.assertIs(projected[self._mid(5)]['reply_to'], True)
        self.assertEqual(projected[self._mid(5)].get('reply_target'), self._mid(4))
        self.assertNotIn('body', projected[self._mid(5)])
        self.assertNotIn('SECRET-BODY-VALUE', json.dumps(snap))
        service.collect()
        report = service.report()
        loops = self._loops(report)
        self.assertEqual(len(loops), 1)
        self.assertTrue(loops[0]['actionable'])
        self.assertEqual(len(loops[0]['evidence']), 4)
        self.assertEqual(len(github.creates()), 1)
        self.assertNotIn('SECRET-BODY-VALUE', github.creates()[0][3])
        service.collect()
        self.assertEqual(len(self._loops(service.report())), 1)
        self.assertEqual(len(github.creates()), 1)
        self.assertEqual(github.comments(), [])

    def test_incremental_linked_chain_still_publishes_once(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        left, right = self._peer(root, SID, self.AA), self._peer(root, OTHER, self.BB)
        clock, github, service = self._service(root)
        service.activate('team', 'proj', REPO)
        prior = self._message(left, 40, self.BB, self.AA, 1)
        service.collect()
        for index, sender in enumerate((self.AA, self.BB, self.AA, self.BB)):
            ledger, recipient = (right, self.BB) if sender == self.AA else (left, self.AA)
            prior = self._message(ledger, 41 + index, sender, recipient, 2 + index, reply_to=prior)
            service.collect()
            clock.advance(1)
        self.assertEqual(len(self._loops(service.report())), 1)
        self.assertEqual(len(github.creates()), 1)
        service.collect()
        self.assertEqual(len(github.creates()), 1)

    def test_missing_or_disconnected_reply_metadata_is_not_a_loop(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        ledger = self._peer(root, SID, self.AA)
        _clock, github, service = self._service(root)
        service.activate('team', 'proj', REPO)
        for index, sender in enumerate((self.AA, self.BB, self.AA, self.BB)):
            self._message(ledger, 70 + index, sender, self.BB, None if index % 2 else index + 1, reply_to=True)
            service.collect()
        self.assertFalse(self._loops(service.report()))
        self.assertEqual(github.creates(), [])
        for index, sender in enumerate((self.AA, self.BB, self.AA, self.BB)):
            self._message(ledger, 80 + index, sender, self.BB, 10 + index, reply_to=f'{(90 + index):08x}-0000-4000-8000-000000000000')
            service.collect()
        self.assertFalse(self._loops(service.report()))
        self.assertEqual(github.creates(), [])
        assert_redacted(self, root)

    def test_historical_linked_replies_stay_nonlive(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        ledger = self._peer(root, SID, self.AA)
        prior = self._message(ledger, 1, self.BB, self.AA, 1)
        for index, sender in enumerate((self.AA, self.BB, self.AA, self.BB)):
            prior = self._message(ledger, 2 + index, sender, self.BB, 2 + index, reply_to=prior)
        _clock, github, service = self._service(root)
        service.activate('team', 'proj', REPO)
        service.collect()
        report = service.report()
        self.assertFalse(self._loops(report))
        self.assertEqual(github.creates(), [])
        historical = [item for item in report['observations'] if item['kind'] == 'historical_reply_count']
        self.assertTrue(historical)
        self.assertTrue(all(not item['reply_loop'] and item['model_turns'] == 'unavailable' for item in historical))

    def test_idle_samples_do_not_publish_and_real_work_still_does(self):
        from quorum_listener import Control
        from quorum_state import LocalLedger
        for state in ('waiting', 'checking'):
            with self.subTest(state=state):
                tmp = tempfile.TemporaryDirectory()
                self.addCleanup(tmp.cleanup)
                root = Path(tmp.name)
                write_session(root)
                ledger = LocalLedger(root / 'sessions' / SID)
                Control(ledger).update('healthy-idle-run', state, 0)
                self.assertEqual(ledger.setting('listener')['handled'], 0)
                ledger.db.close()
                clock, github, service = self._service(root)
                service.activate('team', 'proj', REPO)
                for _ in range(8):
                    clock.advance(30)
                    service.collect()
                report = service.report()
                self.assertFalse(any(item['rule'] == 'excessive_empty_receive' for item in report['findings']))
                idle = [item for item in report['observations'] if item['kind'] == 'healthy_idle_samples']
                self.assertEqual(len(idle), 1)
                self.assertFalse(idle[0]['actionable'])
                self.assertEqual(idle[0]['invocations'], 'unavailable')
                self.assertEqual(idle[0]['model_turns'], 'unavailable')
                self.assertEqual(github.creates(), [])
                service.collect()
                self.assertEqual(len([item for item in service.report()['observations'] if item['kind'] == 'healthy_idle_samples']), 1)
                self.assertEqual(github.creates(), [])
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        write_session(root, deliveries=[{'message_id': MID, 'state': 'submitted'}])
        clock, github, service = self._service(root)
        service.activate('team', 'proj', REPO)
        clock.advance(901)
        service.collect()
        self.assertEqual([item['rule'] for item in service.report()['findings']], ['stuck_unacknowledged'])
        self.assertEqual(len(github.creates()), 1)

    def test_waiting_and_checking_changes_are_not_receive_invocations(self):
        from quorum_listener import Control
        from quorum_state import LocalLedger
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        write_session(root)
        ledger = LocalLedger(root / 'sessions' / SID)
        control = Control(ledger)
        control.update('healthy-idle-run', 'waiting', 0)
        ledger.db.close()
        clock, github, service = self._service(root)
        service.activate('team', 'proj', REPO)
        for index in range(8):
            if index == 4:
                ledger = LocalLedger(root / 'sessions' / SID)
                Control(ledger).update('healthy-idle-run', 'checking', 0)
                ledger.db.close()
            clock.advance(30)
            service.collect()
        self.assertEqual(github.creates(), [])
        self.assertFalse(any(item['rule'] == 'excessive_empty_receive' for item in service.report()['findings']))

    def _cloud_record(self, conversation, quorum='team', project='proj'):
        from quorum_grok_cloud import SESSION_PREFIX, TRANSPORT
        session_ref = SESSION_PREFIX + conversation
        registration = {'engine': 'grok', 'session_ref': session_ref, 'project_ref': project, 'cwd': '/synthetic/project'}
        return {
            'quorum': quorum, 'quorum_id': 'qid-1', 'project_ref': project, 'role': 'peer', 'engine': 'grok',
            'agent_id': self.CC, 'session_ref': session_ref, 'registration': registration, 'client': 'grok-bot',
            'provider_conversation_id': conversation, 'transport': TRANSPORT,
        }

    def _cloud(self, root, conversation, quorum='team', project='proj'):
        from quorum_grok_cloud import cloud_ledger, verify_record
        from quorum_state import LocalLedger
        auth = LocalLedger(Path(root))
        cloud = cloud_ledger(auth, conversation)
        record = self._cloud_record(conversation, quorum, project)
        verify_record(record, conversation)
        cloud.setting('cloud-agent', record)
        self.addCleanup(cloud.db.close)
        self.addCleanup(auth.db.close)
        return cloud

    def test_cloud_only_and_mixed_ledgers_keep_native_and_cloud_selectors(self):
        from quorum_debug_ledger import read_scoped
        from quorum_grok_cloud import SESSION_PREFIX
        for mixed in (False, True):
            with self.subTest(mixed=mixed):
                tmp = tempfile.TemporaryDirectory()
                self.addCleanup(tmp.cleanup)
                root = Path(tmp.name)
                cloud = self._cloud(root, SID)
                self._message(cloud, 900, self.AA, self.CC, 1)
                direct = read_scoped(cloud.root / 'ledger.sqlite', SESSION_PREFIX + SID,
                                      {'quorum': 'team', 'project_ref': 'proj', 'bindings': []})
                self.assertEqual(direct['agent_id'], self.CC)
                self.assertEqual(direct['binding'], 'grok-bot:' + SID)
                if mixed:
                    write_session(root)
                _clock, _github, service = self._service(root)
                view = service.activate('team', 'proj')
                participants = service.report()['coverage']['participants']
                self.assertTrue(any(item['binding'] == 'grok-bot:' + SID and item['agent_id'] == self.CC
                                     for item in participants))
                self.assertNotIn('no_readable_source', view['blockers'])
                self.assertNotIn('SECRET-BODY-VALUE', json.dumps(service.report()))
                if mixed:
                    self.assertTrue(any(item['binding'] == SID and item['agent_id'] == AID for item in participants))
        for selector, expected in (( (SID,), [SID] ), ( ('grok-bot:' + SID,), ['grok-bot:' + SID] )):
            chosen = tempfile.TemporaryDirectory()
            self.addCleanup(chosen.cleanup)
            chosen_root = Path(chosen.name)
            self._cloud(chosen_root, SID)
            write_session(chosen_root)
            _clock, _github, selected = self._service(chosen_root)
            selected.activate('team', 'proj', bindings=selector)
            bindings = [item['binding'] for item in selected.report()['coverage']['participants']]
            self.assertEqual(bindings, expected)

    def test_foreign_cloud_is_excluded_before_delivery_queries(self):
        from quorum_debug_ledger import read_local
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        cloud = self._cloud(root, SID, quorum='other', project='other')
        foreign = self._message(cloud, 910, self.AA, self.CC, 1)
        scope = {'quorum': 'team', 'project_ref': 'proj', 'bindings': []}

        def denied(_db):
            raise AssertionError('delivery query before scope check')

        with patch('quorum_debug_ledger._deliveries', denied):
            self.assertEqual(read_local(root, scope), [])
        _clock, _github, service = self._service(root)
        service.activate('team', 'proj')
        report = service.report()
        self.assertEqual(report['coverage']['participants'], [])
        self.assertNotIn(foreign, json.dumps(report))
        self.assertNotIn('SECRET-BODY-VALUE', json.dumps(report))
        self.assertIn('no_readable_source', service.status()['blockers'])

    def test_unreadable_cloud_ledger_and_cloud_symlink_are_not_followed(self):
        from quorum_debug_ledger import read_local
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        broken = root / 'cloud' / SID
        broken.mkdir(parents=True)
        (broken / 'ledger.sqlite').write_bytes(b'not a database')
        _clock, _github, service = self._service(root)
        service.activate('team', 'proj')
        self.assertIn('grok-bot:' + SID, service.report()['coverage'].get('unreadable_unscoped', []))
        self.assertIn('unreadable_source', service.status()['blockers'])
        outside = tempfile.TemporaryDirectory()
        self.addCleanup(outside.cleanup)
        linked_root = Path(tmp.name) / 'linked'
        cloud = self._cloud(outside.name, OTHER)
        self._message(cloud, 920, self.AA, self.CC, 1)
        linked_root.mkdir()
        (linked_root / 'cloud').symlink_to(Path(outside.name) / 'cloud', target_is_directory=True)
        scope = {'quorum': 'team', 'project_ref': 'proj', 'bindings': []}
        self.assertEqual(read_local(linked_root, scope), [])


class DebugListenerEventTests(unittest.TestCase):
    """Same-run listener changes stay on the timeline. Identical samples do not."""

    def _open(self, root):
        from quorum_listener import Control
        from quorum_state import LocalLedger
        ledger = LocalLedger(Path(root) / 'sessions' / SID)
        ledger.setting('session-agent', {
            'quorum': 'team', 'quorum_id': 'qid-1', 'project_ref': 'proj', 'role': 'peer',
            'engine': 'codex', 'agent_id': AID, 'session_ref': SID, 'transport': 'tool-inbox',
        })
        ledger.setting('delivery:' + MID, {'state': 'acked', 'notification': False})
        self.addCleanup(ledger.db.close)
        clock, github = Clock(), FakeGitHub()
        github.available = False
        return clock, github, open_service(root, clock, github), Control(ledger), ledger

    def _listeners(self, service):
        return [item for item in service.report()['timeline'] if item.get('stage') == 'listener']

    def test_same_run_listener_material_changes_remain_visible(self):
        from quorum_debug_ledger import read_scoped
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        clock, github, service, control, ledger = self._open(root)
        scope = {'quorum': 'team', 'project_ref': 'proj', 'bindings': []}
        control.update('one-listener-run', 'waiting', 0)
        service.activate('team', 'proj')
        with self.subTest('waiting to error'):
            control.update('one-listener-run', 'error', 0, code='service_unavailable')
            current = read_scoped(ledger.root / 'ledger.sqlite', SID, scope)
            self.assertEqual(current['listener']['state'], 'error')
            self.assertEqual(current['listener']['code'], 'service_unavailable')
            self.assertEqual(current['listener']['handled'], 0)
            service.collect()
            listeners = self._listeners(service)
            self.assertTrue(any(item.get('listener_state') == 'waiting' and item.get('handled') == 0
                                for item in listeners))
            self.assertTrue(any(
                item.get('listener_state') == 'error' and item.get('code') == 'service_unavailable'
                and item.get('handled') == 0 and item.get('run_id') == 'one-listener-run'
                for item in listeners))
        with self.subTest('identical sample'):
            seen = [item['event_id'] for item in service.report()['timeline']]
            clock.advance(30)
            service.collect()
            self.assertEqual([item['event_id'] for item in service.report()['timeline']], seen)
        with self.subTest('code only'):
            control.update('one-listener-run', 'error', 0, code='coordination_unavailable')
            current = read_scoped(ledger.root / 'ledger.sqlite', SID, scope)
            self.assertEqual(current['listener']['code'], 'coordination_unavailable')
            service.collect()
            codes = [item.get('code') for item in self._listeners(service)]
            self.assertIn('service_unavailable', codes)
            self.assertIn('coordination_unavailable', codes)
        with self.subTest('handled progress'):
            control.update('one-listener-run', 'error', 2, code='coordination_unavailable')
            service.collect()
            self.assertTrue(any(item.get('handled') == 2 and item.get('code') == 'coordination_unavailable'
                                and item.get('run_id') == 'one-listener-run' for item in self._listeners(service)))
        with self.subTest('new run'):
            control.update('next-listener-run', 'waiting', 0)
            service.collect()
            runs = {item.get('run_id') for item in self._listeners(service)}
            self.assertIn('one-listener-run', runs)
            self.assertIn('next-listener-run', runs)
        with self.subTest('reception'):
            ledger.setting('session-reception', {'enabled': False})
            service.collect()
            self.assertTrue(any(item.get('run_id') == 'next-listener-run' and item.get('reception_enabled') is False
                                for item in self._listeners(service)))
            self.assertTrue(any(item.get('run_id') == 'next-listener-run' and 'reception_enabled' not in item
                                for item in self._listeners(service)))
        with self.subTest('non listener dedupe'):
            ledger.setting('presence-epoch', {'connection_id': 'epoch-1'})
            service.collect()
            clock.advance(45)
            service.collect()
            hellos = [item for item in service.report()['timeline'] if item.get('stage') == 'hello']
            deliveries = [item for item in service.report()['timeline'] if item.get('message_id') == MID]
            self.assertEqual(len(hellos), 1)
            self.assertEqual(hellos[0].get('epoch'), 'epoch-1')
            self.assertEqual(len(deliveries), 1)
            self.assertEqual(github.creates(), [])


class _SafeOS:
    """Record kill calls. A Windows name never reaches the real os.kill."""

    def __init__(self, name):
        self.name = name
        self.calls = []
        self.error = None

    def kill(self, pid, sig):
        self.calls.append((pid, sig))
        if self.name == 'nt':
            return None
        if self.error is not None:
            raise self.error
        return None


class _WinCall:
    """ctypes-style callable. Bound methods reject argtypes."""

    def __init__(self, func):
        self._func = func
        self.argtypes = None
        self.restype = None

    def __call__(self, *args):
        return self._func(*args)


class _WinKernel:
    def __init__(self):
        self.handle = 256
        self.open_result = 256
        self.exit_code = 259
        self.query_ok = 1
        self.opened = None
        self.closed = []
        self.OpenProcess = _WinCall(self._open)
        self.GetExitCodeProcess = _WinCall(self._query)
        self.CloseHandle = _WinCall(self._close)

    def _open(self, access, inherit, pid):
        self.opened = (access, inherit, pid)
        return self.open_result

    def _query(self, handle, out):
        target = getattr(out, '_obj', None)
        if target is None:
            target = out.contents
        target.value = self.exit_code
        return self.query_ok

    def _close(self, handle):
        self.closed.append(handle)
        return 1


class DebugWindowsLivenessTests(unittest.TestCase):
    """Direct Windows liveness. No ledger, ACL, or real Windows kill."""

    def _bare(self):
        from quorum_debug import DebugService
        service = DebugService.__new__(DebugService)
        service.alive_check = None
        return service

    def _windows(self, kernel):
        proxy = _SafeOS('nt')
        return proxy, patch('quorum_debug.os', proxy), patch('ctypes.WinDLL', return_value=kernel, create=True)

    def test_windows_still_active_is_a_query_and_closes_the_handle(self):
        import ctypes
        from ctypes import wintypes
        service = self._bare()
        kernel = _WinKernel()
        proxy, os_patch, dll = self._windows(kernel)
        with os_patch, dll:
            alive = service._pid_alive(240)
        self.assertEqual(proxy.calls, [])
        self.assertTrue(alive)
        self.assertEqual(kernel.opened, (0x1000, False, 240))
        self.assertEqual(kernel.closed, [256])
        self.assertIs(kernel.OpenProcess.restype, wintypes.HANDLE)
        self.assertEqual(ctypes.sizeof(kernel.OpenProcess.restype), ctypes.sizeof(ctypes.c_void_p))
        self.assertEqual(list(kernel.OpenProcess.argtypes), [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD])
        self.assertIs(kernel.GetExitCodeProcess.restype, wintypes.BOOL)
        self.assertIs(kernel.GetExitCodeProcess.argtypes[1]._type_, wintypes.DWORD)
        self.assertIs(kernel.CloseHandle.restype, wintypes.BOOL)
        self.assertEqual(list(kernel.CloseHandle.argtypes), [wintypes.HANDLE])

    def test_windows_exited_denied_and_failed_query_are_not_alive(self):
        service = self._bare()
        cases = (
            ('exited', {'exit_code': 0, 'query_ok': 1, 'open_result': 256}, [256]),
            ('open denied', {'exit_code': 259, 'query_ok': 1, 'open_result': 0}, []),
            ('query failed', {'exit_code': 259, 'query_ok': 0, 'open_result': 256}, [256]),
        )
        for label, values, closed in cases:
            with self.subTest(label):
                kernel = _WinKernel()
                kernel.exit_code = values['exit_code']
                kernel.query_ok = values['query_ok']
                kernel.open_result = values['open_result']
                proxy, os_patch, dll = self._windows(kernel)
                with os_patch, dll:
                    alive = service._pid_alive(240)
                self.assertFalse(alive)
                self.assertEqual(proxy.calls, [])
                self.assertEqual(kernel.closed, closed)

    def test_windows_unavailable_api_does_not_fall_back_to_kill(self):
        import builtins
        service = self._bare()
        proxy = _SafeOS('nt')
        real_import = builtins.__import__

        def blocked(name, globals=None, locals=None, fromlist=(), level=0):
            if name == 'ctypes' or name.startswith('ctypes.'):
                raise ImportError('ctypes unavailable')
            return real_import(name, globals, locals, fromlist, level)

        with patch('quorum_debug.os', proxy), patch('builtins.__import__', blocked):
            imported = service._pid_alive(240)
        self.assertFalse(imported)
        self.assertEqual(proxy.calls, [])
        with patch('quorum_debug.os', proxy), patch('ctypes.WinDLL', side_effect=OSError('kernel32 unavailable'), create=True):
            missing = service._pid_alive(240)
        self.assertFalse(missing)
        self.assertEqual(proxy.calls, [])

    def test_windows_pid_outside_dword_is_not_alive(self):
        service = self._bare()
        kernel = _WinKernel()
        proxy, os_patch, dll = self._windows(kernel)
        with os_patch, dll:
            alive = service._pid_alive(2 ** 32)
        self.assertFalse(alive)
        self.assertEqual(proxy.calls, [])
        self.assertIsNone(kernel.opened)

    def test_invalid_pid_and_injected_check_skip_the_platform_probe(self):
        service = self._bare()
        proxy = _SafeOS('nt')
        with patch('quorum_debug.os', proxy):
            for pid in (None, 0, -1, True, '4'):
                self.assertFalse(service._pid_alive(pid))
            service.alive_check = lambda pid: pid == 240
            self.assertTrue(service._pid_alive(240))
            self.assertFalse(service._pid_alive(241))
        self.assertEqual(proxy.calls, [])

    def test_posix_signal_zero_and_oserror_stay_unchanged(self):
        service = self._bare()
        proxy = _SafeOS('posix')
        with patch('quorum_debug.os', proxy):
            self.assertTrue(service._pid_alive(240))
            proxy.error = PermissionError('denied')
            self.assertFalse(service._pid_alive(241))
            proxy.error = OSError('down')
            self.assertFalse(service._pid_alive(242))
        self.assertEqual(proxy.calls, [(240, 0), (241, 0), (242, 0)])

    @unittest.skipUnless(os.name == 'nt', 'native Windows owned-child liveness runs in Windows CI')
    def test_owned_child_prints_the_after_probe_marker_and_exits(self):
        scripts = str(Path(__file__).resolve().parents[1] / 'skills' / 'quorum' / 'scripts')
        child = (
            'import os, sys\n'
            'sys.path.insert(0, sys.argv[1])\n'
            'from quorum_debug import DebugService\n'
            'service = DebugService.__new__(DebugService)\n'
            'service.alive_check = None\n'
            'alive = service._pid_alive(os.getpid())\n'
            'if alive is not True:\n'
            '    raise SystemExit(2)\n'
            'sys.stdout.write("QUORUM_DEBUG_WIN_LIVENESS_AFTER_PROBE\\n")\n'
            'sys.stdout.flush()\n'
        )
        proc = subprocess.Popen(
            [sys.executable, '-c', child, scripts],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        try:
            stdout, stderr = proc.communicate(timeout=20)
        except subprocess.TimeoutExpired:
            proc.kill()
            stdout, stderr = proc.communicate(timeout=5)
            self.fail(f'owned child timed out: {stderr}')
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.communicate(timeout=5)
        self.assertIn('QUORUM_DEBUG_WIN_LIVENESS_AFTER_PROBE', stdout)
        self.assertEqual(proc.returncode, 0)
        self.assertNotIn('QUORUM_DEBUG_WIN_LIVENESS_AFTER_PROBE', stderr)


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
