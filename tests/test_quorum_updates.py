"""Owner refreshes use disposable source copies and an offline RPC boundary."""
import hang_guard  # noqa: F401
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

    def test_owner_fanout_sends_the_complete_target_through_real_transport(self):
        from quorum_api import QuorumApi, ServiceConfig
        sent = []
        def request(url, headers, body):
            sent.append((url, json.loads(body)))
            return {'operation_id':OPID, 'queued':2, 'existing':0}
        self.api = QuorumApi(ServiceConfig('https://synthetic.invalid', 'public'),
                             lambda:'synthetic-token', request=request)
        result = self.invoke()
        self.assertEqual(sent, [('https://synthetic.invalid/rest/v1/rpc/session_update_request',
                                {'p_agent_id':AID, 'p_session_ref':SID, 'p_quorum_id':QID,
                                 'p_protocol_version':2,
                                 'p_content_sha256':result['target']['content_sha256'],
                                 'p_request_id':'owner-1'})])
        self.assertEqual(result['fanout'], {'operation_id':OPID, 'queued':2, 'existing':0})

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
        self.assertEqual(one['target']['protocol_version'], two['target']['protocol_version'])
        self.assertNotEqual(one['target']['content_sha256'], two['target']['content_sha256'])
        self.assertNotEqual(one['target']['release'], two['target']['release'])

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

    def test_old_node_reports_an_allowed_server_blocker_but_keeps_the_local_code(self):
        from quorum_update import NodeTooOld
        self.notice()
        def fail(*args): raise NodeTooOld('20.11.1')
        with self.assertRaisesRegex(ValueError, '^update_node_too_old$') as caught:
            self.module.apply_notice(self.api, self.ledger, self.record, NOTICE, 'blocked',
                                     owner_refresh=True, current=self.source, cache=self.root/'cache', refresh=fail)
        self.assertEqual(caught.exception.data, {'found':'20.11.1', 'required':'22.20'})
        self.assertEqual(self.api.calls[-1][0], 'session_update_report')
        self.assertEqual(self.api.calls[-1][1]['event'], 'blocked')
        # Must stay inside session_update_report's deployed blocked-evidence allowlist.
        self.assertEqual(self.api.calls[-1][1]['evidence'], {'code':'update_tool_missing'})
        sql = (SCRIPTS.parent/'supabase/migrations/20261030140000_owner_update_maintenance.sql').read_text()
        self.assertIn("'update_tool_missing'", sql[sql.index("p_event='blocked'"):])

    def test_python_provisioning_failure_reports_an_allowed_server_blocker(self):
        self.notice()
        def fail(*args): raise ValueError('python_provisioning_failed')
        with self.assertRaisesRegex(ValueError, '^python_provisioning_failed$'):
            self.module.apply_notice(self.api, self.ledger, self.record, NOTICE, 'blocked',
                                     owner_refresh=True, current=self.source, cache=self.root/'cache', refresh=fail)
        self.assertEqual(self.api.calls[-1][0], 'session_update_report')
        self.assertEqual(self.api.calls[-1][1]['event'], 'blocked')
        self.assertEqual(self.api.calls[-1][1]['evidence'], {'code':'update_tool_missing'})

    def test_every_reported_blocker_is_in_the_server_allowlist(self):
        import re
        migrations = sorted((SCRIPTS.parent/'supabase/migrations').glob('*.sql'))
        # The last file that defines the writer; later files may only pin it.
        sql = [path.read_text() for path in migrations
               if 'create function public.session_update_report(' in path.read_text()][-1]
        clause = sql[sql.index("p_event='blocked'"):]
        clause = clause[:clause.index('update_evidence_invalid')]
        allowed = set(re.findall(r"'([a-z_]+)'", clause[clause.index('not in'):]))
        self.assertIn('update_tool_missing', allowed)
        for code in sorted(self.module.BLOCKERS):
            with self.subTest(code=code):
                self.assertIn(self.module.SERVER_BLOCKER.get(code, code), allowed)

    def test_owner_update_cli_preserves_old_node_code_version_and_hint(self):
        from quorum import main
        from quorum_update import NodeTooOld
        with patch('quorum_updates.dispatch', side_effect=NodeTooOld('20.11.1')), \
                contextlib.redirect_stdout(io.StringIO()) as output:
            code = main(['--state', str(self.ledger.root), 'update', 'team', '--request-id', 'owner-1'])
        self.assertEqual(code, 3)
        result = json.loads(output.getvalue())
        self.assertEqual(result['code'], 'update_node_too_old')
        self.assertEqual({key:result['data'][key] for key in ('found', 'required')},
                         {'found':'20.11.1', 'required':'22.20'})
        self.assertIn('22.20', result['data']['hint'])

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

    def test_a_restarted_listener_reports_under_a_new_request_id_and_a_retry_reuses_its_own(self):
        from unittest.mock import patch
        self.notice()
        target = self.module._notice(self.api, self.record, NOTICE, lambda:None)
        def report(run_id):
            active = {'verified':True, 'source':target, 'run_id':run_id}
            with patch.object(self.module, 'runtime_evidence', return_value=active):
                self.assertEqual(self.module.report_runtime(self.api, self.ledger, self.record, NOTICE, 'same')['state'],
                                 'active_reported')
            return [call for call in self.api.calls if call[0] == 'session_update_report'][-1]
        first, retry = report(OPID), report(OPID)
        restarted = report(NOTICE)
        self.assertEqual(first[2], retry[2])
        self.assertNotEqual(first[2], restarted[2])
        self.assertLessEqual(len(restarted[2]), 128)
        self.assertEqual(restarted[1]['evidence']['run_id'], NOTICE)

    def test_a_named_server_refusal_keeps_its_code_and_is_not_an_unknown_fanout(self):
        from quorum_api import QuorumError
        for raised, expected in (('skill_update_required', 'skill_update_required'),
                                 ('update_target_invalid', 'update_target_invalid'),
                                 ('update_requires_read_committed', 'update_requires_read_committed'),
                                 ('idempotency_conflict', 'update_target_changed')):
            class Refused(Api):
                def call(self, *args, **kwargs): raise QuorumError(raised)
            with self.subTest(raised=raised):
                self.api = Refused()
                with self.assertRaises(QuorumError) as caught: self.invoke('refused-' + raised)
                self.assertEqual(str(caught.exception), expected)
                self.assertEqual(caught.exception.diagnostic['fanout_state'], 'unavailable')

    def test_an_unnamed_failure_still_means_the_extension_is_unavailable(self):
        from quorum_api import QuorumError
        for error, expected in ((QuorumError('service_error'), 'update_extension_unavailable'),
                                (QuorumError('service_error', transient=True), 'service_error')):
            with self.subTest(transient=error.transient), self.assertRaisesRegex(QuorumError, f'^{expected}$'):
                self.module._call(type('Failing', (), {'call': lambda *_: (_ for _ in ()).throw(error)})(),
                                  'session_update_report', {}, 'id', lambda:None)


if __name__ == '__main__': unittest.main()
