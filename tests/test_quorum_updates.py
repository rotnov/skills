"""Owner refreshes use disposable source copies and an offline RPC boundary."""
import importlib.util
import contextlib
import io
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / 'skills/quorum/scripts'
sys.path.insert(0, str(SCRIPTS))
SID = '11111111-1111-4111-8111-111111111111'
AID = '22222222-2222-4222-8222-222222222222'
QID = '33333333-3333-4333-8333-333333333333'
NOTICE = '44444444-4444-4444-8444-444444444444'
OPID = '55555555-5555-4555-8555-555555555555'


class Api:
    def __init__(self):
        self.calls = []
        self.lose = False
        self.notice = None

    def call(self, operation, payload, request_id=None):
        from quorum_api import QuorumError
        self.calls.append((operation, payload.copy(), request_id))
        if operation == 'session_update_request':
            if self.lose:
                self.lose = False
                raise QuorumError('service_unavailable', transient=True)
            return {'operation_id':OPID, 'queued':2, 'existing':0}
        if operation == 'session_update_status':
            return self.notice
        if operation == 'session_update_report':
            return {'recorded':True}
        raise AssertionError(operation)


class OwnerUpdateTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec('quorum_updates'), 'owner update orchestration is missing')
        import quorum_updates
        from quorum_state import LocalLedger
        self.module = quorum_updates
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.ledger = LocalLedger(self.root/'state')
        self.addCleanup(self.ledger.db.close)
        self.source = self.root/'loaded'
        shutil.copytree(SCRIPTS.parent, self.source, ignore=shutil.ignore_patterns('.venv', '__pycache__'))
        self.record = {'session_ref':SID, 'agent_id':AID, 'quorum_id':QID, 'quorum':'team',
                       'engine':'codex', 'role':'service', 'project_ref':'project', 'device_id':'device',
                       'cwd':str(Path.cwd().resolve())}
        self.ledger.setting('session-agent', self.record)
        self.ledger.setting('session-reception', {'enabled':False, 'expires_at':1})
        self.ledger.setting('delegation-test', {'coordinator':'unchanged'})
        self.refreshed = 0
        self.api = Api()

    def refresh(self, current, cache, engine):
        from quorum_update import validate_source
        self.refreshed += 1
        digest = validate_source(self.source)
        destination = self.root/'cache'/digest/'.agents/skills/quorum'
        if not destination.exists():
            shutil.copytree(self.source, destination)
        return {'status':'current', 'skill_dir':str(destination), 'content_sha256':digest}

    def invoke(self, request='owner-1', **extra):
        return self.module.owner_update(self.api, self.ledger, self.record, request,
                                        current=self.source, cache=self.root/'cache',
                                        refresh=self.refresh, **extra)

    def test_current_initiator_still_requests_all_quorum_fanout_and_preserves_stop(self):
        result = self.invoke()
        self.assertEqual(result['local']['status'], 'current')
        self.assertEqual(result['fanout'], {'operation_id':OPID, 'queued':2, 'existing':0})
        self.assertEqual([call[0] for call in self.api.calls], ['session_update_request'])
        self.assertEqual(self.api.calls[0][1]['quorum_id'], QID)
        self.assertEqual(self.ledger.setting('session-agent'), self.record)
        self.assertEqual(self.ledger.setting('session-reception'), {'enabled':False, 'expires_at':1})
        self.assertEqual(self.ledger.setting('delegation-test'), {'coordinator':'unchanged'})
        self.assertFalse(result['runtime']['verified'])

    def test_lost_send_response_resumes_the_same_target_without_refetch(self):
        from quorum_api import QuorumError
        self.api.lose = True
        with self.assertRaises(QuorumError): self.invoke()
        first = self.api.calls[0]
        (self.source/'later.txt').write_text('new canonical main later')
        result = self.invoke()
        self.assertEqual(self.refreshed, 1)
        self.assertEqual(self.api.calls[1], first)
        self.assertEqual(result['target']['content_sha256'], first[1]['content_sha256'])

    def test_local_refresh_failure_sends_nothing(self):
        def failure(*args): raise ValueError('update_fetch_failed')
        with self.assertRaisesRegex(ValueError, '^update_fetch_failed$'):
            self.module.owner_update(self.api, self.ledger, self.record, 'failed', current=self.source,
                                     cache=self.root/'cache', refresh=failure)
        self.assertEqual(self.api.calls, [])

    def test_changed_source_with_same_package_version_has_a_distinct_target(self):
        one = self.invoke()
        (self.source/'changed.txt').write_text('same release different source')
        two = self.invoke('owner-2')
        self.assertEqual(one['target']['skill_version'], two['target']['skill_version'])
        self.assertNotEqual(one['target']['content_sha256'], two['target']['content_sha256'])

    def test_retry_cannot_reuse_a_changed_cached_copy(self):
        result = self.invoke()
        (Path(result['local']['skill_dir'])/'tampered.txt').write_text('bad')
        self.api.calls.clear()
        with self.assertRaisesRegex(ValueError, '^update_cache_conflict$'): self.invoke()
        self.assertEqual(self.api.calls, [])

    def test_missing_extension_fails_clearly_after_local_journal(self):
        from quorum_api import QuorumError
        class Missing(Api):
            def call(self, *args, **kwargs): raise QuorumError('service_error')
        self.api = Missing()
        with self.assertRaisesRegex(QuorumError, '^update_extension_unavailable$'): self.invoke()
        self.assertEqual(self.refreshed, 1)

    def test_uncertain_fanout_error_still_reports_the_validated_local_copy(self):
        from quorum_api import QuorumError
        self.api.lose = True
        with self.assertRaises(QuorumError) as caught: self.invoke()
        diagnostic = getattr(caught.exception, 'diagnostic', None)
        self.assertIsNotNone(diagnostic, 'local refresh success must not disappear behind a send failure')
        self.assertEqual(diagnostic['local']['status'], 'current')
        self.assertEqual(diagnostic['fanout_state'], 'unknown')
        self.assertEqual(diagnostic['target']['content_sha256'], self.api.calls[0][1]['content_sha256'])

    def notice(self):
        from quorum_update import validate_source
        self.api.notice = {'notice_id':NOTICE, 'recipient_agent_id':AID, 'quorum_id':QID,
                           'target':{'protocol_version':2, 'skill_version':'0.3.0',
                                     'content_sha256':validate_source(self.source)}}

    def test_peer_notice_and_old_reception_grant_do_not_authorize_refresh(self):
        self.notice()
        result = self.module.apply_notice(self.api, self.ledger, self.record, NOTICE, 'apply-1',
                                         owner_refresh=False, current=self.source, cache=self.root/'cache',
                                         refresh=self.refresh)
        self.assertEqual(result['state'], 'owner_action_required')
        self.assertEqual(self.refreshed, 0)
        self.assertNotIn('session_update_request', [call[0] for call in self.api.calls])

    def test_explicit_receiving_owner_refresh_never_rebroadcasts_or_claims_runtime(self):
        self.notice()
        result = self.module.apply_notice(self.api, self.ledger, self.record, NOTICE, 'apply-1',
                                         owner_refresh=True, current=self.source, cache=self.root/'cache',
                                         refresh=self.refresh)
        self.assertEqual(result['state'], 'refreshed')
        self.assertFalse(result['runtime']['verified'])
        self.assertEqual(self.refreshed, 1)
        self.assertEqual([call[0] for call in self.api.calls],
                         ['session_update_status', 'session_update_report'])
        self.assertEqual(self.api.calls[-1][1]['event'], 'refreshed')

    def test_successful_notice_retries_with_new_ids_reuse_source_without_report_flood(self):
        self.notice()
        options = dict(owner_refresh=True, current=self.source, cache=self.root/'cache', refresh=self.refresh)
        first = self.module.apply_notice(self.api, self.ledger, self.record, NOTICE, 'apply-1', **options)
        second = self.module.apply_notice(self.api, self.ledger, self.record, NOTICE.replace('-', ''),
                                          'apply-2', **options)
        self.assertEqual(first['target'], second['target'])
        self.assertEqual(self.refreshed, 1, 'same recipient notice must not refresh again for a new request ID')
        self.assertEqual([call[0] for call in self.api.calls].count('session_update_report'), 1)
        held = self.module.apply_notice(self.api, self.ledger, self.record, NOTICE, 'apply-3',
                                        **{**options, 'owner_refresh': False})
        self.assertEqual(held['state'], 'owner_action_required', 'saved success cannot supply fresh owner consent')

    def test_notice_report_lost_response_reuses_one_source_and_stable_report_id(self):
        from quorum_api import QuorumError
        self.notice()
        base_call = self.api.call
        reports = []
        def lose_once(operation, payload, request_id=None):
            result = base_call(operation, payload, request_id)
            if operation == 'session_update_report':
                reports.append(request_id)
                if len(reports) == 1: raise QuorumError('service_unavailable', transient=True)
            return result
        self.api.call = lose_once
        options = dict(owner_refresh=True, current=self.source, cache=self.root/'cache', refresh=self.refresh)
        with self.assertRaisesRegex(QuorumError, 'service_unavailable'):
            self.module.apply_notice(self.api, self.ledger, self.record, NOTICE, 'lost-1', **options)
        self.module.apply_notice(self.api, self.ledger, self.record, NOTICE, 'lost-2', **options)
        self.module.apply_notice(self.api, self.ledger, self.record, NOTICE, 'lost-3', **options)
        self.assertEqual(self.refreshed, 1)
        self.assertEqual(len(reports), 2)
        self.assertEqual(reports[0], reports[1])

    def test_notice_success_revalidates_cached_source_target_and_binding_on_new_request(self):
        self.notice()
        options = dict(owner_refresh=True, current=self.source, cache=self.root/'cache', refresh=self.refresh)
        first = self.module.apply_notice(self.api, self.ledger, self.record, NOTICE, 'source-1', **options)
        selected = Path(first['local']['skill_dir'])
        (selected/'tampered.txt').write_text('changed after refresh')
        with self.assertRaisesRegex(ValueError, 'update_cache_conflict'):
            self.module.apply_notice(self.api, self.ledger, self.record, NOTICE, 'source-2', **options)
        (selected/'tampered.txt').unlink()
        old = self.api.notice
        self.api.notice = {**old, 'target': {**old['target'], 'content_sha256': 'f'*64}}
        with self.assertRaisesRegex(ValueError, 'update_notice_denied'):
            self.module.apply_notice(self.api, self.ledger, self.record, NOTICE, 'target-2', **options)
        self.api.notice = old
        replacement = {**self.record, 'project_ref': 'different'}
        with self.assertRaisesRegex(ValueError, 'update_notice_denied'):
            self.module.apply_notice(self.api, self.ledger, replacement, NOTICE, 'binding-2', **options)
        self.assertEqual(self.refreshed, 1)

    def test_authority_lost_during_refresh_prevents_report_or_fanout(self):
        allowed = [True]
        def authorize():
            if not allowed[0]: raise ValueError('session_binding_denied')
        def refresh(*args):
            result = self.refresh(*args)
            allowed[0] = False
            return result
        with self.assertRaisesRegex(ValueError, '^session_binding_denied$'):
            self.module.owner_update(self.api, self.ledger, self.record, 'lost', current=self.source,
                                     cache=self.root/'cache', refresh=refresh, authorize=authorize)
        self.assertEqual(self.api.calls, [])

    def test_cli_uses_only_current_binding_and_rejects_missing_request_before_rpc(self):
        from quorum import main
        from quorum_state import LocalLedger
        session = LocalLedger(self.ledger.root/'sessions'/SID)
        session.setting('session-agent', self.record)
        session.db.close()
        with patch.dict(os.environ, {'CODEX_THREAD_ID':SID}, clear=True), \
                patch('quorum_live.api_for', side_effect=AssertionError('RPC boundary')), \
                contextlib.redirect_stdout(io.StringIO()) as output:
            try:
                code = main(['--state', str(self.ledger.root), 'update', 'team'])
            except SystemExit:
                self.fail('the explicit update command must be registered')
        self.assertEqual(code, 3)
        self.assertEqual(json.loads(output.getvalue())['code'], 'update_request_invalid')

    def test_rpc_transport_accepts_only_the_named_maintenance_operations(self):
        from quorum_api import QuorumApi, ServiceConfig
        sent = []
        api = QuorumApi(ServiceConfig('https://example.com', 'public'), lambda:'synthetic',
                        lambda url, headers, body: sent.append((url, json.loads(body))) or {})
        for operation in ('session_update_request', 'session_update_status', 'session_update_report'):
            try: api.call(operation, {'agent_id':AID})
            except ValueError: self.fail('the maintenance RPC allowlist is missing')
        self.assertEqual(sent[-1], ('https://example.com/rest/v1/rpc/session_update_report', {'p_agent_id':AID}))

    def test_partial_fanout_failure_is_visible_in_the_owner_result(self):
        class Partial(Api):
            def call(self, *args, **kwargs):
                return {**super().call(*args, **kwargs), 'failed':1}
        self.api = Partial()
        result = self.invoke()
        self.assertEqual(result['fanout'].get('failed'), 1, 'a partial rollout cannot look fully queued')

    def test_recipient_refresh_failure_records_a_bounded_blocker(self):
        self.notice()
        def fail(*args): raise ValueError('update_tool_missing')
        with self.assertRaisesRegex(ValueError, '^update_tool_missing$'):
            self.module.apply_notice(self.api, self.ledger, self.record, NOTICE, 'blocked',
                                     owner_refresh=True, current=self.source, cache=self.root/'cache', refresh=fail)
        self.assertEqual(self.api.calls[-1][0], 'session_update_report')
        self.assertEqual(self.api.calls[-1][1]['event'], 'blocked')
        self.assertEqual(self.api.calls[-1][1]['evidence'], {'code':'update_tool_missing'})

    def test_same_version_old_source_cannot_be_reported_as_the_target_runtime(self):
        from quorum_os import FileLock
        self.notice()
        self.ledger.setting('listener', {'run_id':OPID, 'state':'waiting'})
        self.ledger.setting('listener-source', {'run_id':OPID,'protocol_version':2,'skill_version':'0.3.0',
                                               'content_sha256':'f'*64})
        with FileLock(self.ledger.root/'listener.lock'):
            result = self.module.report_runtime(self.api, self.ledger, self.record, NOTICE, 'old-source')
        self.assertEqual(result['state'], 'runtime_unverified')
        self.assertTrue(result['runtime']['verified'], 'the old process is real, but is not the update target')
        self.assertEqual([call[0] for call in self.api.calls], ['session_update_status'])


if __name__ == '__main__': unittest.main()
