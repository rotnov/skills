"""Codex native-worker tool inbox: synthetic rollouts and a fake API only; no model, service or provider."""
import contextlib
import inspect
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import uuid
from quorum_test_support import isolated_profiles

SCRIPTS = Path(__file__).resolve().parents[1] / 'skills/quorum/scripts'
sys.path.insert(0, str(SCRIPTS))
PARENT = '01a15a2b-12f9-73c3-9310-c6b3c7774415'
CHILD = '01a167b0-8caa-7533-ad39-321b0d35f98a'
OTHER = '44444444-4444-4444-8444-444444444444'
INVITE = '11111111-1111-4111-8111-111111111111'
AGENT = '66666666-6666-4666-8666-666666666666'
PEER = '77777777-7777-4777-8777-777777777777'
QUORUM = 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb'
M1 = '99999999-9999-4999-8999-999999999991'
M2 = '99999999-9999-4999-8999-999999999992'
M3 = '99999999-9999-4999-8999-999999999993'
BODY_MARKER = 'private-transcript-body-marker'
# The suite may run inside a real client chat; every test supplies synthetic identity explicitly.
EXPORTS = ('CODEX_THREAD_ID', 'CODEX_HOME', 'CLAUDE_CODE_SESSION_ID', 'CLAUDE_CODE_HOST_SESSION_ID',
           'CLAUDE_CODE_MESSAGING_SOCKET', 'CLAUDE_CODE_MESSAGING_TOKEN', 'GROK_SESSION_ID',
           'GROK_LEADER_SOCKET', 'CURSOR_CONVERSATION_ID', 'CURSOR_AGENT', 'SAND_AGENT_STORE_RPC_SERVICE')
OWNED = ('session_inbox', 'session_ack', 'session_reply', 'session_peers', 'session_heartbeat',
         'conversation_read', 'resolve_role', 'message_send', 'agent_status', 'agent_join')


@contextlib.contextmanager
def caller(home, sid=CHILD, **extra):
    """Patch a copy of the environment; ordinary OS values such as SystemRoot stay."""
    with patch.dict(os.environ):
        for key in EXPORTS: os.environ.pop(key, None)
        os.environ['CODEX_HOME'] = str(home)
        if sid is not None: os.environ['CODEX_THREAD_ID'] = sid
        os.environ.update(extra)
        yield


def first_line(sid=CHILD, parent=PARENT, **changes):
    meta = {'timestamp': '2026-10-23T10:00:00Z', 'type': 'session_meta',
            'payload': {'id': sid, 'cwd': '/synthetic', 'originator': 'codex_cli_rs',
                        'source': {'subagent': {'thread_spawn': {'parent_thread_id': parent, 'depth': 1}}}}}
    meta.update(changes)
    return json.dumps(meta)


def rollout(home, sid=CHILD, parent=PARENT, *, line=None, day=('2026', '10', '23'), body=True):
    directory = Path(home) / 'sessions' / day[0] / day[1] / day[2]
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f'rollout-{day[0]}-{day[1]}-{day[2]}T10-00-00-{sid}.jsonl'
    text = (line if line is not None else first_line(sid, parent)) + '\n'
    if body:  # Transcript content after the first line is never read for provenance.
        text += json.dumps({'type': 'response_item', 'payload': {'text': BODY_MARKER}}) + '\n{not json\n'
    path.write_text(text)
    if os.name != 'nt': path.chmod(0o600)
    return path


def installed():
    from quorum_release import installed_release
    return installed_release().copy()


class Api:
    def __init__(self, inbox=()):
        self.calls, self.inbox, self.acked, self.fail = [], list(inbox), [], {}
        self.required, self.replies, self.revoked, self.lost, self.hooks = installed(), {}, False, [], {}

    def ops(self): return [call[0] for call in self.calls if call[0] != 'session_requirements']

    config = type('Config', (), {'origin': 'https://example.com', 'publishable_key': 'sb_publishable_synthetic'})()
    def token(self): return 'synthetic-jwt'

    def call(self, operation, payload, request_id=None):
        from quorum_api import QuorumError
        self.calls.append((operation, json.loads(json.dumps(payload)), request_id))
        if self.fail.get(operation):
            code = self.fail[operation].pop(0)
            if code: raise QuorumError(code)
        if self.revoked and operation in OWNED: raise QuorumError('membership_revoked')
        result = self.answer(operation, payload)
        hook = self.hooks.pop(operation, None)
        if hook: hook()
        if operation in self.lost:
            self.lost.remove(operation)
            raise QuorumError('service_unavailable')
        return result

    def answer(self, operation, payload):
        if operation == 'session_requirements': return dict(self.required)
        if operation == 'quorum_lookup': return {'quorum_id': QUORUM}
        if operation == 'device_register': return {'device_id': 'device-1'}
        if operation == 'agent_join': return {'agent_id': AGENT}
        if operation == 'agent_status': return {'agent_id': payload['agent_id']}
        if operation == 'resolve_role': return {'agent_id': PEER}
        if operation == 'session_heartbeat': return {'online': True}
        if operation == 'session_inbox': return [m for m in self.inbox if m['id'] not in self.acked]
        if operation == 'session_ack':
            if payload['message_id'] not in self.acked: self.acked.append(payload['message_id'])
            return {'acked': True}
        if operation == 'session_reply':
            reply = self.replies.setdefault(payload['message_id'], str(uuid.uuid4()))
            return {'reply_message_id': reply, 'conversation_id': 'c-1', 'extra': 'dropped'}
        if operation == 'invite_accept_id': return {'quorum_id': 'q', 'membership_id': 'm'}
        raise AssertionError(operation)


def message(mid, *, priority='normal', body='hello', **extra):
    return {'id': mid, 'recipient_id': AGENT, 'sender_id': PEER, 'sender_role': 'peer',
            'conversation_id': 'c-1', 'body': body, 'priority': priority, 'reply_to': None, **extra}


class Store:
    backend = None
    def load(self, _): return None


class Harness(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        base = Path(temporary.name).resolve()
        self.root, self.home = str(base / 'state'), base / 'codex-home'
        self.spawns = []
        # Tool mode never spawns, queues, steers or pipes; every such route fails the test.
        for target in ('quorum_listener.spawn_background', 'quorum_session.enqueue', 'quorum_codex_steer.submit'):
            guard = patch(target, side_effect=lambda *a, **k: self.spawns.append(a) or
                          (_ for _ in ()).throw(AssertionError('no background route')))
            guard.start()
            self.addCleanup(guard.stop)

    def cli(self, api, *argv, stdin=b''):
        import quorum
        from quorum_api import QuorumError, ServiceConfig
        config = ServiceConfig('https://example.com', 'sb_publishable_synthetic')
        service = dict(return_value=(api, config, Store())) if api is not None else dict(side_effect=QuorumError('auth_required'))
        with patch('quorum_live.api_for', **service), \
                patch('sys.stdin', io.TextIOWrapper(io.BytesIO(stdin))), \
                contextlib.redirect_stdout(io.StringIO()) as output:
            code = quorum.main(['--state', self.root, *argv])
        text = output.getvalue()
        return code, json.loads(text), text

    def connect(self, api, *extra):
        code, result, text = self.cli(api, 'session', 'connect', 'team', '--role', 'quorum-worker',
                                      '--project-ref', 'team/project', '--transport', 'tool-inbox',
                                      '--parent-thread-id', PARENT, *extra)
        self.assertEqual(code, 0, text)
        return result['data']

    def receive(self, api, seconds='0'):
        return self.cli(api, 'session', 'receive', '--wait-seconds', seconds)

    def ledger(self, sid=CHILD):
        from quorum_state import LocalLedger
        ledger = LocalLedger(Path(self.root) / 'sessions' / sid)
        self.addCleanup(ledger.db.close)
        return ledger


POSIX_PROOF = unittest.skipIf(os.name == 'nt', 'tool-inbox provenance needs POSIX owner proof; Windows fails closed')


class OtherConnection:
    """A second SQLite connection to the same worker ledger, like a concurrent CLI process."""
    def __init__(self, root):
        from quorum_state import LocalLedger
        self.root = Path(root)
    def __enter__(self):
        from quorum_state import LocalLedger
        self.ledger = LocalLedger(self.root)
        return self.ledger
    def __exit__(self, *_): self.ledger.db.close()


@POSIX_PROOF
class ProvenanceTests(Harness):
    def denied(self, parent=PARENT):
        from quorum_native_inbox import NativeInboxError, prove_worker
        with self.assertRaises(NativeInboxError) as raised: prove_worker(parent)
        error = raised.exception
        text = str(error) + json.dumps(error.diagnostic())
        for private in (str(self.home), BODY_MARKER, 'rollout-'):
            self.assertNotIn(private, text)
        return error.code, error.reason

    @isolated_profiles
    def test_tool_mode_requires_distinct_provider_spawn_metadata(self):
        from quorum_native_inbox import prove_worker
        rollout(self.home)
        with caller(self.home):
            self.assertEqual(prove_worker(PARENT), {'provider': 'codex', 'session_ref': CHILD,
                                                    'parent_thread_id': PARENT})
        # A large but bounded first line is accepted; a line past 256 KiB is never parsed.
        padded = first_line(padding='x' * 200000)
        self.assertLess(len(padded), 262144)
        rollout(self.home, line=padded)
        with caller(self.home): self.assertEqual(prove_worker(PARENT)['session_ref'], CHILD)
        for name, line, expected in (
                ('top-level session', first_line(payload={'id': CHILD, 'source': 'cli'}), 'native_worker_unavailable'),
                ('another session', first_line(sid=OTHER), 'native_worker_binding_denied'),
                ('not metadata', first_line(type='response_item'), 'native_worker_unavailable'),
                ('oversized', first_line(padding='x' * 262200), 'native_worker_unavailable')):
            rollout(self.home, line=line)
            with self.subTest(name=name), caller(self.home):
                self.assertEqual(self.denied()[0], expected)
        # The exact first-line contract is the only provenance input: no path or worker overrides.
        self.assertEqual(list(inspect.signature(prove_worker).parameters), ['parent_thread_id'])
        for argv in (['session', 'connect', 'team', '--role', 'r', '--project-ref', 'p', '--transport',
                      'tool-inbox', '--parent-thread-id', PARENT, '--rollout-path', '/tmp/x'],
                     ['accept-id', INVITE, '--transport', 'tool-inbox', '--parent-thread-id', PARENT,
                      '--transcript', '/tmp/x']):
            with self.subTest(argv=argv), contextlib.redirect_stderr(io.StringIO()):
                import quorum
                with self.assertRaises(SystemExit): quorum.main(['--state', self.root, *argv])

    @isolated_profiles
    def test_wrong_parent_and_parent_caller_are_denied(self):
        rollout(self.home, parent=OTHER)
        with caller(self.home):
            self.assertEqual(self.denied(), ('native_worker_binding_denied', 'parent_mismatch'))
        rollout(self.home)
        # The parent itself (or copied parent exports) never proves a distinct worker.
        rollout(self.home, sid=PARENT, parent=PARENT)
        with caller(self.home, sid=PARENT):
            self.assertEqual(self.denied(), ('native_worker_binding_denied', 'parent_is_caller'))
        with caller(self.home):
            self.assertEqual(self.denied('not-a-uuid')[0], 'native_worker_binding_denied')
        with caller(self.home, sid=CHILD.upper()):
            self.assertEqual(self.denied()[0], 'native_worker_unavailable')

    @isolated_profiles
    def test_rollout_missing_ambiguous_oversized_or_unsafe_fails_closed(self):
        with caller(self.home):
            self.assertEqual(self.denied(), ('native_worker_unavailable', 'rollout_missing'))
        # Only the exact UUID-suffixed rollout name counts.
        decoy = rollout(self.home)
        decoy.rename(decoy.with_name(decoy.name + '.bak'))
        with caller(self.home): self.assertEqual(self.denied()[1], 'rollout_missing')
        first = rollout(self.home)
        second = rollout(self.home, day=('2026', '10', '24'))
        with caller(self.home): self.assertEqual(self.denied()[1], 'rollout_ambiguous')
        second.unlink()
        first.write_text('{"type": "session_meta"' + ' ' * 262200 + '}\n')
        with caller(self.home): self.assertEqual(self.denied()[1], 'metadata_oversized')
        first.write_text('{not json\n' + first_line() + '\n')
        with caller(self.home): self.assertEqual(self.denied()[1], 'metadata_invalid')
        if os.name == 'nt': return  # POSIX ownership and link checks below.
        first.unlink()
        target = Path(self.home) / 'elsewhere.jsonl'
        target.write_text(first_line() + '\n')
        first.symlink_to(target)
        with caller(self.home): self.assertEqual(self.denied()[1], 'rollout_unsafe')
        first.unlink()
        rollout(self.home).chmod(0o666)
        with caller(self.home): self.assertEqual(self.denied()[1], 'rollout_unsafe')
        rollout(self.home).chmod(0o600)
        day = first.parent
        moved = day.with_name('real-day')
        day.rename(moved)
        day.symlink_to(moved, target_is_directory=True)
        with caller(self.home): self.assertEqual(self.denied()[1], 'rollout_unsafe')

    @isolated_profiles
    def test_claude_grok_and_mixed_exports_are_unsupported(self):
        rollout(self.home)
        for name, sid, extra, reason in (
                ('claude', None, {'CLAUDE_CODE_SESSION_ID': CHILD}, 'provider_unsupported'),
                ('grok', None, {'GROK_SESSION_ID': CHILD}, 'provider_unsupported'),
                ('mixed', CHILD, {'CLAUDE_CODE_SESSION_ID': OTHER}, 'competing_exports'),
                ('copied messaging socket', CHILD, {'CLAUDE_CODE_MESSAGING_SOCKET': '/tmp/s'}, 'competing_exports'),
                ('grok bot cloud', CHILD, {'CURSOR_CONVERSATION_ID': OTHER}, 'competing_exports'),
                ('no session', None, {}, 'session_missing')):
            with self.subTest(name=name), caller(self.home, sid=sid, **extra):
                self.assertEqual(self.denied(), ('native_worker_unavailable', reason))
        api = Api()
        with caller(self.home, sid=None, CLAUDE_CODE_SESSION_ID=CHILD), \
                patch('quorum_auth.login_pkce', side_effect=AssertionError('must not login')):
            code, result, text = self.cli(api, 'accept-id', INVITE, '--transport', 'tool-inbox',
                                          '--parent-thread-id', PARENT, '--origin', 'https://example.com',
                                          '--publishable-key', 'sb_publishable_synthetic')
        self.assertEqual((code, result['code'], result['data']['reason']),
                         (3, 'native_worker_unavailable', 'provider_unsupported'), text)
        self.assertEqual(api.calls, [])

    @isolated_profiles
    def test_accept_preflight_failure_makes_no_service_call(self):
        service = ('--origin', 'https://example.com', '--publishable-key', 'sb_publishable_synthetic')
        tool = ('--transport', 'tool-inbox', '--parent-thread-id', PARENT)
        never = [patch(target, side_effect=AssertionError(target)) for target in (
            'quorum_auth.login_pkce', 'quorum_profiles.ensure_login', 'quorum_profiles.resolve_profile',
            'quorum_api.request_json', 'quorum.register_agent', 'quorum_session.client_preflight')]
        cases = (('missing rollout', tool, 'native_worker_unavailable'),
                 ('membership only', tool + ('--membership-only',), 'native_inbox_transport_conflict'),
                 ('cloud client', tool + ('--client', 'grok-bot'), 'native_inbox_transport_conflict'),
                 ('parent without tool', ('--parent-thread-id', PARENT), 'native_inbox_transport_conflict'),
                 ('tool without parent', ('--transport', 'tool-inbox'), 'native_inbox_transport_conflict'))
        for name, flags, expected in cases:
            api = Api()
            with self.subTest(name=name), caller(self.home), contextlib.ExitStack() as stack:
                for guard in never: stack.enter_context(guard)
                code, result, text = self.cli(api, 'accept-id', INVITE, *flags, *service)
            self.assertEqual((code, result['code']), (3, expected), text)
            self.assertEqual(api.calls, [])
        api = Api()
        with caller(self.home), contextlib.ExitStack() as stack:
            for guard in never[3:]: stack.enter_context(guard)
            code, result, text = self.cli(api, 'session', 'connect', 'team', '--role', 'quorum-worker',
                                          '--project-ref', 'team/project', *tool)
        self.assertEqual((code, result['code']), (3, 'native_worker_unavailable'), text)
        self.assertEqual(api.calls, [])
        self.assertFalse((Path(self.root) / 'sessions' / CHILD).exists())
        # A proven worker consumes the invitation once, without the parent-chat native preflight.
        rollout(self.home)
        api = Api()
        with caller(self.home), patch('quorum_auth.login_pkce', return_value={'authenticated': True}), \
                patch('quorum.register_agent', side_effect=AssertionError('no registration')), \
                patch('quorum_session.client_preflight', side_effect=AssertionError('native preflight')):
            code, result, text = self.cli(api, 'accept-id', INVITE, *tool, *service)
        self.assertEqual(code, 0, text)
        self.assertEqual(api.calls, [('invite_accept_id', {'invite_id': INVITE}, 'accept-id:' + INVITE)])
        self.assertEqual((result['data']['transport'], result['data']['worker_preflight']), ('tool-inbox', 'passed'))
        self.assertEqual(self.spawns, [])


    @isolated_profiles
    def test_home_ancestor_links_shared_write_and_foreign_owner_fail_closed(self):
        import stat
        rollout(self.home)
        base = Path(self.home).parent
        # A CODEX_HOME link, or a link anywhere above it, is never followed.
        link = base / 'home-link'
        link.symlink_to(self.home, target_is_directory=True)
        with caller(link): self.assertEqual(self.denied(), ('native_worker_unavailable', 'rollout_unsafe'))
        real_parent = base / 'real-parent'
        real_parent.mkdir()
        (base / 'parent-link').symlink_to(real_parent, target_is_directory=True)
        moved = real_parent / 'codex-home'
        Path(self.home).rename(moved)
        try:
            with caller(base / 'parent-link' / 'codex-home'):
                self.assertEqual(self.denied()[1], 'rollout_unsafe')
            with caller(moved):
                from quorum_native_inbox import prove_worker
                self.assertEqual(prove_worker(PARENT)['session_ref'], CHILD)
        finally: moved.rename(self.home)
        # Group- or other-writable provider directories, home or a non-sticky shared ancestor are refused.
        sessions = Path(self.home) / 'sessions'
        for directory in (Path(self.home), sessions, sessions / '2026', sessions / '2026' / '10' / '23', base):
            original = stat.S_IMODE(directory.stat().st_mode)
            for mode in (original | 0o020, original | 0o002):
                directory.chmod(mode)
                try:
                    with self.subTest(directory=directory.name, mode=oct(mode)), caller(self.home):
                        self.assertEqual(self.denied()[1], 'rollout_unsafe')
                finally: directory.chmod(original)
        # A sticky shared ancestor (like /tmp) stays acceptable; the user's own tree is still checked.
        original = stat.S_IMODE(base.stat().st_mode)
        base.chmod(original | 0o1022)
        try:
            with caller(self.home):
                from quorum_native_inbox import prove_worker
                self.assertEqual(prove_worker(PARENT)['session_ref'], CHILD)
        finally: base.chmod(original)
        # Files and directories owned by another user are refused.
        with caller(self.home), patch('quorum_native_inbox.os.getuid', return_value=os.getuid() + 1):
            self.assertEqual(self.denied()[1], 'rollout_unsafe')

    @isolated_profiles
    def test_without_owner_proof_tool_mode_fails_closed_and_never_chmods(self):
        path = rollout(self.home)
        modes = {item: item.lstat().st_mode for item in [path, *path.parents][:5]}
        with caller(self.home), patch('quorum_native_inbox._ownership_proof_available', return_value=False):
            self.assertEqual(self.denied(), ('native_worker_unavailable', 'ownership_unverified'))
        self.assertEqual({item: item.lstat().st_mode for item in modes}, modes)

    @isolated_profiles
    def test_first_line_reads_no_transcript_byte(self):
        from quorum_native_inbox import prove_worker
        rollout(self.home)
        consumed, real_read = [], os.read
        def read(fd, size):
            data = real_read(fd, size)
            consumed.append(data)
            return data
        with caller(self.home), patch('quorum_native_inbox.os.read', side_effect=read):
            self.assertEqual(prove_worker(PARENT)['session_ref'], CHILD)
        self.assertEqual(b''.join(consumed), first_line().encode() + b'\n')
        self.assertNotIn(BODY_MARKER.encode(), b''.join(consumed))
        consumed.clear()
        rollout(self.home, line=first_line(padding='x' * 262200))
        with caller(self.home), patch('quorum_native_inbox.os.read', side_effect=read):
            self.assertEqual(self.denied()[1], 'metadata_oversized')
        self.assertEqual(len(b''.join(consumed)), 262145)


FD_PROBE = r"""
import json, os, resource, sys
soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
resource.setrlimit(resource.RLIMIT_NOFILE, (256, hard))  # This child process only.
sys.path.insert(0, sys.argv[1])
from quorum_native_inbox import NativeInboxError, prove_worker
def descriptors(): return len(os.listdir('/dev/fd'))
before = descriptors()
try: outcome = prove_worker(sys.argv[2])['session_ref']
except NativeInboxError as error: outcome = [error.code, error.reason]
print(json.dumps({'outcome': outcome, 'leaked': descriptors() - before}))
"""


@POSIX_PROOF
class DescriptorLimitTests(Harness):
    def history(self, days):
        """Hundreds of real consecutive calendar-date directories, as months of retained Codex history."""
        import datetime
        first = datetime.date(2025, 1, 1)
        for offset in range(days):
            day = first + datetime.timedelta(days=offset)
            directory = Path(self.home) / 'sessions' / f'{day:%Y}' / f'{day:%m}' / f'{day:%d}'
            directory.mkdir(parents=True, exist_ok=True)
            if os.name != 'nt':
                for path in (directory, directory.parent, directory.parent.parent): path.chmod(0o700)
        return first + datetime.timedelta(days=days - 1)

    def probe(self, sid=CHILD):
        import subprocess
        env = {key: value for key, value in os.environ.items() if key not in EXPORTS}
        env.update(CODEX_HOME=str(self.home), CODEX_THREAD_ID=sid)
        result = subprocess.run([sys.executable, '-c', FD_PROBE, str(SCRIPTS), PARENT], env=env,
                                capture_output=True, text=True, timeout=120)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    @isolated_profiles
    def test_full_uniqueness_scan_stays_within_a_256_descriptor_limit(self):
        last = self.history(400)
        rollout(self.home, day=('2025', '01', '01'))  # The match is first; the scan must still visit every day.
        self.assertEqual(self.probe(), {'outcome': CHILD, 'leaked': 0})
        # A second candidate late in history is still found: uniqueness is never abandoned.
        late = rollout(self.home, day=(f'{last:%Y}', f'{last:%m}', f'{last:%d}'))
        self.assertEqual(self.probe(), {'outcome': ['native_worker_unavailable', 'rollout_ambiguous'], 'leaked': 0})
        late.unlink()
        # Failure cleanup: an unsafe directory deep in history fails closed without leaking descriptors.
        unsafe = late.parent
        unsafe.chmod(0o770)
        try: self.assertEqual(self.probe(), {'outcome': ['native_worker_unavailable', 'rollout_unsafe'], 'leaked': 0})
        finally: unsafe.chmod(0o700)
        self.assertEqual(self.probe()['outcome'], CHILD)


@unittest.skipUnless(os.name == 'nt', 'Windows-only fail-closed check')
class WindowsTests(Harness):
    @isolated_profiles
    def test_windows_fails_closed_without_owner_proof(self):
        from quorum_native_inbox import NativeInboxError, prove_worker
        rollout(self.home)
        with caller(self.home), self.assertRaises(NativeInboxError) as raised: prove_worker(PARENT)
        self.assertEqual((raised.exception.code, raised.exception.reason),
                         ('native_worker_unavailable', 'ownership_unverified'))


@POSIX_PROOF
class DeliveryTests(Harness):
    def setUp(self):
        super().setUp()
        rollout(self.home)

    def offer(self, api):
        with caller(self.home):
            self.connect(api)
            code, result, text = self.receive(api)
        self.assertEqual(code, 0, text)
        return result['data']

    @isolated_profiles
    def test_offer_committed_before_stdout_without_ack(self):
        import quorum
        from quorum_api import ServiceConfig
        api = Api(inbox=[message(M1, body='$(rm -rf ~) please'), message(M2)])
        envelope = self.offer(api)
        self.assertEqual((envelope['type'], envelope['state'], envelope['message_id'], envelope['priority']),
                         ('quorum.peer_message', 'offered', M1, 'normal'))
        self.assertEqual(envelope['message']['body'], '$(rm -rf ~) please')
        self.assertEqual(envelope['receipt_command'][-5:], ['session', '--session-id', CHILD, 'received', M1])
        self.assertEqual(envelope['reply_command'][-6:], ['session', '--session-id', CHILD, 'reply', M1, '--text-stdin'])
        self.assertNotIn('rm -rf', json.dumps([envelope['receipt_command'], envelope['reply_command'], envelope['notice']]))
        self.assertNotIn(PARENT, json.dumps(envelope['receipt_command'] + envelope['reply_command']))
        self.assertEqual(envelope['session_ref'], CHILD)
        uuid.UUID(envelope['run_id'])
        delivery = self.ledger().setting('delivery:' + M1)
        self.assertEqual({key: delivery[key] for key in ('state', 'agent_id', 'session_ref', 'quorum_id')},
                         {'state': 'offered', 'agent_id': AGENT, 'session_ref': CHILD, 'quorum_id': QUORUM})
        self.assertNotIn('session_ack', api.ops())
        record = self.ledger().setting('session-agent')
        self.assertEqual((record['transport'], record['worker_binding'], record['engine']),
                         ('tool-inbox', {'provider': 'codex', 'session_ref': CHILD, 'parent_thread_id': PARENT}, 'codex'))
        self.assertEqual(record['registration'], {'engine': 'codex', 'session_ref': CHILD,
                                                  'project_ref': 'team/project', 'cwd': record['cwd']})
        self.assertFalse((Path(self.root) / 'sessions' / PARENT).exists(), 'the parent ledger stays untouched')
        # The offer was committed before output: a broken tool result still leaves it recoverable.
        class Broken(io.TextIOBase):
            def writable(self): return True
            def write(self, _): raise BrokenPipeError()
        api.inbox.insert(0, message(M3, priority='urgent'))
        config = ServiceConfig('https://example.com', 'sb_publishable_synthetic')
        with caller(self.home), patch('quorum_live.api_for', return_value=(api, config, Store())), \
                patch('sys.stdout', Broken()), contextlib.redirect_stderr(io.StringIO()) as errors:
            try: outcome = quorum.main(['--state', self.root, 'session', 'receive', '--wait-seconds', '0'])
            except OSError as error: outcome = error
        self.assertNotEqual(outcome, 0)
        self.assertNotIn('rm -rf', errors.getvalue() + repr(outcome))
        self.assertEqual(self.ledger().setting('delivery:' + M1)['state'], 'offered')
        self.assertIsNone(self.ledger().setting('delivery:' + M3))
        self.assertEqual(api.acked, [])

    @isolated_profiles
    def test_lost_stdout_repeats_offer_before_new_urgent_head(self):
        api = Api(inbox=[message(M1, body='first')])
        self.assertEqual(self.offer(api)['message_id'], M1)
        api.inbox.insert(0, message(M2, priority='urgent', body='urgent'))
        with caller(self.home):
            _, again, _ = self.receive(api)
            self.assertEqual((again['data']['message_id'], again['data']['message']['body']), (M1, 'first'))
            self.assertIsNone(self.ledger().setting('delivery:' + M2))
            self.assertEqual(self.cli(api, 'session', 'received', M1)[0], 0)
            _, following, _ = self.receive(api)
        self.assertEqual((following['data']['message_id'], following['data']['priority']), (M2, 'urgent'))
        self.assertIn('urgent', following['data']['notice'])
        self.assertEqual(api.acked, [M1])

    @isolated_profiles
    def test_lost_ack_response_replays_one_server_receipt(self):
        api = Api(inbox=[message(M1)])
        self.offer(api)
        with caller(self.home):
            api.lost.append('session_ack')
            code, result, _ = self.cli(api, 'session', 'received', M1)
            self.assertEqual((code, result['code']), (5, 'service_unavailable'))
            self.assertEqual(api.acked, [M1])
            self.assertEqual(self.ledger().setting('delivery:' + M1)['state'], 'offered')
            # The service inbox is now empty, yet the unresolved offer is never dropped.
            _, again, _ = self.receive(api)
            self.assertEqual(again['data']['message_id'], M1)
            api.inbox.append(message(M2))
            self.assertEqual(self.receive(api)[1]['data']['message_id'], M1)
            offer = self.ledger().setting('delivery:' + M1)
            code, receipt, _ = self.cli(api, 'session', 'received', M1)
            self.assertEqual((code, receipt['data']), (0, {'received': True, 'state': 'acked', 'message_id': M1}))
            self.assertEqual(self.receive(api)[1]['data']['message_id'], M2)
        self.assertEqual(self.ledger().setting('delivery:' + M1), {**offer, 'state': 'acked'})
        acks = [payload for operation, payload, _ in api.calls if operation == 'session_ack']
        self.assertEqual(acks, [{'agent_id': AGENT, 'session_ref': CHILD, 'message_id': M1}] * 2)
        self.assertEqual(api.acked, [M1])

    @isolated_profiles
    def test_cached_offer_after_revocation_or_release_mismatch_is_not_disclosed(self):
        api = Api(inbox=[message(M1, body='private peer body')])
        self.offer(api)
        with caller(self.home):
            api.revoked = True
            for argv in (('receive', '--wait-seconds', '0'), ('received', M1), ('reply', M1, '--text-stdin')):
                code, result, text = self.cli(api, 'session', *argv, stdin=b'answer')
                self.assertEqual((code, result['ok']), (3, False), text)
                self.assertNotIn('private peer body', text)
                self.assertNotIn(M1, text)
            api.revoked = False
            api.required = {'protocol_version': 99, 'skill_version': '9.9.9'}
            for argv in (('receive', '--wait-seconds', '0'), ('received', M1), ('reply', M1, '--text-stdin')):
                code, result, text = self.cli(api, 'session', *argv, stdin=b'answer')
                self.assertEqual((code, result['code']), (4, 'skill_update_required'), text)
                self.assertNotIn('private peer body', text)
        self.assertNotIn('session_ack', api.ops())
        self.assertNotIn('session_reply', api.ops())
        self.assertEqual(self.ledger().setting('delivery:' + M1)['state'], 'offered')

    @isolated_profiles
    def test_reply_requires_owned_offer_and_rejects_terminal_messages(self):
        api = Api(inbox=[message(M1, reply_to=M2), message(M3, notification=True)])
        with caller(self.home):
            self.connect(api)
            for argv in (('received', M1), ('reply', M1, '--text-stdin')):
                code, result, _ = self.cli(api, 'session', *argv, stdin=b'answer')
                self.assertEqual((code, result['code']), (3, 'native_inbox_receipt_not_offered'))
            # Another binding's offer is not this worker's receipt.
            self.ledger().setting('delivery:' + M1, {'state': 'offered', 'agent_id': PEER, 'session_ref': PARENT})
            self.assertEqual(self.cli(api, 'session', 'received', M1)[1]['code'], 'native_inbox_receipt_not_offered')
            child = self.ledger()
            with child.db: child.db.execute('delete from settings where key=?', ('delivery:' + M1,))
            for mid in (M1, M3):
                _, envelope, _ = self.receive(api)
                self.assertEqual(envelope['data']['message_id'], mid)
                code, result, _ = self.cli(api, 'session', 'reply', mid, '--text-stdin', stdin=b'loop')
                self.assertEqual((code, result['code']), (3, 'native_inbox_reply_loop_denied'))
                self.assertEqual(self.cli(api, 'session', 'received', mid)[0], 0)
            # The parent may supervise but never receipt as the worker.
            with caller(self.home, sid=PARENT):
                code, result, _ = self.cli(api, 'session', '--session-id', CHILD, 'received', M1)
            self.assertEqual((code, result['code']), (3, 'session_binding_denied'))
        self.assertNotIn('session_reply', api.ops())
        self.assertEqual(api.acked, [M1, M3])

    @isolated_profiles
    def test_same_reply_retry_is_idempotent_changed_reply_is_rejected(self):
        api = Api(inbox=[message(M1)])
        self.offer(api)
        with caller(self.home):
            api.fail['session_reply'] = ['service_unavailable']
            code, result, _ = self.cli(api, 'session', 'reply', M1, '--text-stdin', stdin=b'answer')
            self.assertEqual((code, result['code']), (5, 'service_unavailable'))
            offer = self.ledger().setting('delivery:' + M1)
            self.assertEqual(offer['state'], 'offered')
            code, first, _ = self.cli(api, 'session', 'reply', M1, '--text-stdin', stdin=b'answer')
            _, second, _ = self.cli(api, 'session', 'reply', M1, '--text-stdin', stdin=b'answer')
            for stdin, extra in ((b'changed', ()), (b'answer', ('--priority', 'urgent'))):
                code_changed, changed, _ = self.cli(api, 'session', 'reply', M1, '--text-stdin', *extra, stdin=stdin)
                self.assertEqual((code_changed, changed['code']), (3, 'native_inbox_reply_loop_denied'))
        self.assertEqual(code, 0)
        self.assertEqual(first['data']['reply_message_id'], second['data']['reply_message_id'])
        self.assertNotIn('extra', first['data'])
        replies = [(payload, request_id) for operation, payload, request_id in api.calls if operation == 'session_reply']
        self.assertEqual(len(replies), 3)
        self.assertTrue(all(entry == replies[0] for entry in replies))
        self.assertEqual(replies[0][0], {'agent_id': AGENT, 'session_ref': CHILD, 'message_id': M1,
                                         'body': 'answer', 'priority': 'normal'})
        self.assertEqual(api.acked, [M1])
        self.assertEqual(self.ledger().setting('delivery:' + M1), {**offer, 'state': 'acked'})

    @isolated_profiles
    def test_existing_queue_intent_remains_unknown(self):
        api = Api(inbox=[message(M1), message(M2)])
        with caller(self.home):
            self.connect(api)
            for state in ('intent', 'queued', 'submitted'):
                self.ledger().setting('delivery:' + M1, {'state': state})
                code, result, text = self.receive(api)
                self.assertEqual((code, result['code']), (3, 'session_delivery_unknown'), text)
                self.assertIsNone(self.ledger().setting('delivery:' + M2))
            code, result, _ = self.cli(api, 'session', 'received', M1)
            self.assertEqual(result['code'], 'native_inbox_receipt_not_offered')
            # The legacy queue recovery command is not a tool-inbox route.
            code, result, _ = self.cli(api, 'session', 'reconcile', M1, '--confirmed-queued')
            self.assertEqual(result['code'], 'native_inbox_transport_conflict')
        self.assertEqual(self.ledger().setting('delivery:' + M1), {'state': 'submitted'})
        self.assertNotIn('session_ack', api.ops())
        self.assertEqual(self.spawns, [])


    @isolated_profiles
    def test_concurrent_reply_intent_never_loses_the_ack_cas(self):
        from quorum_native_inbox import confirm, reply
        api = Api(inbox=[message(M1), message(M2)])
        self.offer(api)
        ledger = self.ledger()
        record = ledger.setting('session-agent')
        root = ledger.root
        def reply_intent():
            # Another process records its reply digest between this receipt's read and its CAS.
            with OtherConnection(root) as other:
                raw = other.db.execute("select data from settings where key=?", ('delivery:' + M1,)).fetchone()[0]
                with other.db:
                    other.db.execute('update settings set data=? where key=? and data=?',
                                     (json.dumps({**json.loads(raw), 'reply_digest': 'd' * 64}), 'delivery:' + M1, raw))
        api.hooks['session_ack'] = reply_intent
        with caller(self.home):
            self.assertEqual(confirm(api, ledger, record, M1)['state'], 'acked')
        stored = ledger.setting('delivery:' + M1)
        self.assertEqual((stored['state'], stored['reply_digest']), ('acked', 'd' * 64))
        with caller(self.home):
            _, following, _ = self.receive(api)
        self.assertEqual(following['data']['message_id'], M2, 'a receipted message is never redisclosed')
        # The reverse interleaving: a receipt from another connection lands during an in-flight reply.
        def receipt():
            with OtherConnection(root) as other, caller(self.home):
                self.assertEqual(confirm(api, other, record, M2)['state'], 'acked')
        api.hooks['session_reply'] = receipt
        with caller(self.home):
            result = reply(api, ledger, record, M2, 'answer', 'normal')
        stored = ledger.setting('delivery:' + M2)
        self.assertEqual((result['received'], stored['state']), (True, 'acked'))
        self.assertEqual(len(stored['reply_digest']), 64)
        self.assertEqual(len(api.replies), 1)
        # A delivery that is no longer an owned offer is reported truthfully, never as acked.
        api.inbox.append(message(M3))
        with caller(self.home): self.receive(api)
        def corrupt():
            with OtherConnection(root) as other: other.setting('delivery:' + M3, {'state': 'intent'})
        api.hooks['session_ack'] = corrupt
        with caller(self.home), self.assertRaisesRegex(ValueError, 'session_delivery_unknown'):
            confirm(api, ledger, record, M3)
        self.assertEqual(ledger.setting('delivery:' + M3), {'state': 'intent'})

    @isolated_profiles
    def test_completed_receipt_only_closes_the_answer_path(self):
        api = Api(inbox=[message(M1)])
        self.offer(api)
        with caller(self.home):
            self.assertEqual(self.cli(api, 'session', 'received', M1)[1]['data']['state'], 'acked')
            self.assertEqual(self.cli(api, 'session', 'received', M1)[1]['data']['state'], 'acked')
            acked = self.ledger().setting('delivery:' + M1)
            code, result, _ = self.cli(api, 'session', 'reply', M1, '--text-stdin', stdin=b'late answer')
        self.assertEqual((code, result['code']), (3, 'native_inbox_reply_loop_denied'))
        self.assertNotIn('session_reply', api.ops())
        self.assertEqual(self.ledger().setting('delivery:' + M1), acked)
        self.assertNotIn('reply_digest', acked)

    @isolated_profiles
    def test_receipt_winning_between_reply_read_and_intent_cas_denies_the_answer(self):
        from quorum_native_inbox import NativeInboxError, confirm, reply
        api = Api(inbox=[message(M1)])
        self.offer(api)
        ledger = self.ledger()
        record = ledger.setting('session-agent')
        real = ledger.db
        class Interleave:
            """The reply already read the offered row; a receipt commits before its intent CAS runs."""
            fired = False
            def __getattr__(self, name): return getattr(real, name)
            def __enter__(self): return real.__enter__()
            def __exit__(self, *exc): return real.__exit__(*exc)
            def execute(inner, sql, *params):
                if sql.startswith('update settings set data=? where key=? and data=?') and not Interleave.fired:
                    Interleave.fired = True
                    with OtherConnection(ledger.root) as other:
                        self.assertEqual(confirm(api, other, record, M1)['state'], 'acked')
                return real.execute(sql, *params)
        ledger.db = Interleave()
        try:
            with caller(self.home), self.assertRaises(NativeInboxError) as raised:
                reply(api, ledger, record, M1, 'answer', 'normal')
        finally: ledger.db = real
        self.assertTrue(Interleave.fired)
        self.assertEqual(raised.exception.code, 'native_inbox_reply_loop_denied')
        self.assertNotIn('session_reply', api.ops())
        stored = ledger.setting('delivery:' + M1)
        self.assertEqual(stored['state'], 'acked')
        self.assertNotIn('reply_digest', stored)

    @isolated_profiles
    def test_persisted_intent_survives_receipt_and_lost_reply_response(self):
        from quorum_api import QuorumError
        from quorum_native_inbox import confirm, reply
        api = Api(inbox=[message(M1)])
        self.offer(api)
        ledger = self.ledger()
        record = ledger.setting('session-agent')
        def receipt():
            with OtherConnection(ledger.root) as other, caller(self.home):
                self.assertEqual(confirm(api, other, record, M1)['state'], 'acked')
        api.hooks['session_reply'] = receipt
        api.lost.append('session_reply')  # The service stored the answer; its response never arrived.
        with caller(self.home), self.assertRaises(QuorumError):
            reply(api, ledger, record, M1, 'answer', 'normal')
        stored = ledger.setting('delivery:' + M1)
        self.assertEqual(stored['state'], 'acked')
        self.assertEqual(len(stored['reply_digest']), 64)
        with caller(self.home):
            replay = reply(api, ledger, record, M1, 'answer', 'normal')
            for stdin, extra in ((b'changed', ()), (b'answer', ('--priority', 'urgent'))):
                code, result, _ = self.cli(api, 'session', 'reply', M1, '--text-stdin', *extra, stdin=stdin)
                self.assertEqual((code, result['code']), (3, 'native_inbox_reply_loop_denied'))
        self.assertEqual(replay['reply_message_id'], api.replies[M1])
        replies = [payload for operation, payload, _ in api.calls if operation == 'session_reply']
        self.assertEqual(len(replies), 2)
        self.assertTrue(all(payload == replies[0] for payload in replies))
        self.assertEqual(ledger.setting('delivery:' + M1), stored)
        self.assertEqual(api.acked, [M1])

    @isolated_profiles
    def test_matched_revoked_and_absent_worker_grants(self):
        api = Api(inbox=[message(M1), message(M2, sender_id=OTHER)])
        with caller(self.home):
            self.connect(api, '--accept-tasks-from', 'coordinator', '--dispatch-mode', 'native-subagent')
            matched = self.receive(api)[1]['data']['notice']
        status = 'session --session-id ' + CHILD + ' status'
        for required in ('Dispatch mode native-subagent', 'run ' + status + ' from this worker',
                         'in the exact bound worker', 'per receiving worker',
                         "This grant is bound to this worker's own binding; its parent chat gains no authority",
                         'This bound worker forwards results/replies only with its own receipt and reply commands',
                         'Follow that dispatch policy yourself as the grant holder',
                         'never hand the grant or its tasks to an ungranted parent', 'Only this worker may run'):
            self.assertIn(required, matched)
        for forbidden in ('hand any accepted project task to the parent', 'The original bound parent session',
                          'service agent is not a task worker', 'Service mechanics grant no project right',
                          'from this chat', 'No matching local owner delegation'):
            self.assertNotIn(forbidden, matched)
        def proposal(text):
            for required in ('No matching local owner delegation', 'Service mechanics grant no project right',
                             'return it to the parent as an owner decision, which gains no authority'):
                self.assertIn(required, text)
            for forbidden in ('Dispatch mode', 'Accept and carry out', 'Follow that dispatch policy'):
                self.assertNotIn(forbidden, text)
        with caller(self.home):
            # Revocation after the offer is honored: the cached offer is re-rendered as a proposal.
            self.assertEqual(self.cli(api, 'session', 'delegate', '--revoke')[0], 0)
            proposal(self.receive(api)[1]['data']['notice'])
            self.assertEqual(self.cli(api, 'session', 'received', M1)[0], 0)
            self.assertEqual(self.cli(api, 'session', 'delegate', '--coordinator-role', 'coordinator',
                                      '--dispatch-mode', 'native-subagent')[0], 0)
            # A current grant does not cover a sender other than its pinned coordinator.
            absent = self.receive(api)[1]['data']
        self.assertEqual(absent['message_id'], M2)
        proposal(absent['notice'])


@POSIX_PROOF
class LifecycleTests(Harness):
    def setUp(self):
        super().setUp()
        rollout(self.home)

    def bound(self, api):
        with caller(self.home): self.connect(api)
        ledger = self.ledger()
        return ledger, ledger.setting('session-agent')

    class Waiter:
        def __init__(self, events, *, on_wait=None, clock=None):
            self.events, self.on_wait, self.clock = events, on_wait, clock
        def prepare(self, stopping): self.events.append('prepare')
        def pulse(self, stopping=None): self.events.append('pulse')
        def wait(self, stopping):
            self.events.append('wait')
            if self.clock: self.clock[0] += 1
            if self.on_wait: self.on_wait()
        def close(self): self.events.append('close')

    class Presence:
        def __init__(self, events): self.events = events
        def tick(self, stopping=None): self.events.append('tick')
        def connected(self, stopping=None): self.events.append('connected')

    def direct(self, api, ledger, record, seconds, waiter, events, clock=None):
        from quorum_native_inbox import receive
        from quorum_state import LocalLedger
        auth = LocalLedger(Path(self.root))
        self.addCleanup(auth.db.close)
        with caller(self.home):
            return receive(api, auth, ledger, record, seconds, waiter=waiter, presence=self.Presence(events),
                           clock=(lambda: clock[0]) if clock else None)

    @isolated_profiles
    def test_two_consumers_are_excluded(self):
        from quorum_listener import Control
        from quorum_os import FileLock
        from quorum_release import installed_release
        api = Api(inbox=[message(M1)])
        ledger, _ = self.bound(api)
        mark = len(api.calls)
        # Another current consumer holds the lock and has published its own compatible generation.
        Control(ledger).update(str(uuid.uuid4()), 'waiting', 0, timeout=30, release=installed_release())
        with FileLock(ledger.root / 'listener.lock'), caller(self.home):
            code, result, _ = self.receive(api)
        self.assertEqual((code, result['code']), (3, 'worker_already_running'))
        self.assertNotIn('session_inbox', [call[0] for call in api.calls[mark:]])
        self.assertIsNone(ledger.setting('delivery:' + M1))

    @isolated_profiles
    def test_subscribe_precedes_initial_read_and_reconnect_catches_up(self):
        events = []
        api = Api()
        original = api.call
        def traced(operation, payload, request_id=None):
            if operation == 'session_inbox': events.append('inbox')
            return original(operation, payload, request_id)
        api.call = traced
        ledger, record = self.bound(api)
        waiter = self.Waiter(events, on_wait=lambda: api.inbox.append(message(M1)))
        result = self.direct(api, ledger, record, 30, waiter, events)
        self.assertEqual(result['message_id'], M1)
        self.assertEqual([e for e in events if e in ('prepare', 'inbox', 'wait', 'close')],
                         ['prepare', 'inbox', 'wait', 'inbox', 'close'])
        self.assertEqual(ledger.setting('listener')['state'], 'stopped')
        self.assertEqual(api.acked, [])

    @isolated_profiles
    def test_zero_wait_and_empty_deadline_return_without_ack(self):
        events = []
        api = Api()
        ledger, record = self.bound(api)
        with patch('quorum_native_inbox.RealtimeWait', side_effect=AssertionError('no subscription')), \
                patch('time.sleep', side_effect=AssertionError('no sleep')):
            result = self.direct(api, ledger, record, 0, None, events)
        self.assertEqual((result['state'], result['message'], result['session_ref']), ('timeout', None, CHILD))
        self.assertEqual(events, ['tick'])
        clock = [100]
        waiter = self.Waiter(events, clock=clock)
        result = self.direct(api, ledger, record, 3, waiter, events, clock=clock)
        self.assertEqual((result['state'], result['message']), ('timeout', None))
        self.assertEqual(events.count('wait'), 3)
        self.assertEqual(events[-1], 'close')
        with caller(self.home), contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            self.cli(api, 'session', 'receive', '--wait-seconds', '61')
        self.assertNotIn('session_ack', api.ops())

    @isolated_profiles
    def test_stop_disables_next_receive_until_same_worker_start(self):
        api = Api(inbox=[message(M1)])
        ledger, _ = self.bound(api)
        with caller(self.home, sid=PARENT):
            code, stopped, text = self.cli(None, 'session', '--session-id', CHILD, 'stop')
            self.assertEqual(code, 0, text)
            code, status, _ = self.cli(None, 'session', '--session-id', CHILD, 'status')
        self.assertEqual((status['data']['transport'], status['data']['reception']), ('tool-inbox', 'disabled'))
        with caller(self.home):
            mark = len(api.calls)
            _, result, _ = self.receive(api)
            self.assertEqual((result['data']['state'], result['data']['message']), ('stopped', None))
            self.assertNotIn('session_inbox', [call[0] for call in api.calls[mark:]])
        # The parent may stop the exact worker SID but cannot re-arm or receive as it.
        with caller(self.home, sid=PARENT):
            for argv in (('start',), ('receive', '--wait-seconds', '0')):
                code, result, _ = self.cli(api, 'session', '--session-id', CHILD, *argv)
                self.assertEqual((code, result['code']), (3, 'session_binding_denied'))
        with caller(self.home):
            code, armed, text = self.cli(api, 'session', 'start')
            self.assertEqual(code, 0, text)
            self.assertEqual((armed['data']['listener'], armed['data']['reception']), ('offline', 'enabled'))
            self.assertEqual(self.receive(api)[1]['data']['message_id'], M1)
        self.assertEqual(self.spawns, [])

    @isolated_profiles
    def test_repeated_connect_never_rearms_a_stopped_worker(self):
        api = Api(inbox=[message(M1)])
        ledger, _ = self.bound(api)
        with caller(self.home, sid=PARENT):
            self.assertEqual(self.cli(None, 'session', '--session-id', CHILD, 'stop')[0], 0)
        with caller(self.home):
            again = self.connect(api)
            self.assertEqual((again['reception'], ledger.setting('tool-inbox-control')), ('disabled', {'enabled': False}))
            self.assertEqual(self.receive(api)[1]['data']['state'], 'stopped')
            # The explicit same-worker start remains the only re-arm (and epoch reset).
            ledger.setting('presence-epoch', {'connection_id': str(uuid.uuid4()), 'acked_at': 1, 'pending': False})
            self.assertEqual(self.cli(api, 'session', 'start')[1]['data']['reception'], 'enabled')
            self.assertIsNone(ledger.setting('presence-epoch'))
            self.assertEqual(self.receive(api)[1]['data']['message_id'], M1)
            # A reconnect of an enabled binding reports its actual state and keeps it.
            self.assertEqual(self.connect(api)['reception'], 'enabled')

    @isolated_profiles
    def test_parent_stop_during_an_in_flight_connect_survives_it(self):
        from quorum_native_inbox import stop
        api = Api(inbox=[message(M1)])
        ledger, record = self.bound(api)
        def parent_stop():
            # The exact parent stop commits through its own SQLite connection while connect awaits the RPC.
            with OtherConnection(ledger.root) as other: stop(other, record)
        api.hooks['agent_status'] = parent_stop
        with caller(self.home):
            result = self.connect(api)
            self.assertNotIn('agent_status', api.hooks, 'the stop ran during the existing-binding RPC')
            self.assertEqual(result['reception'], 'disabled')
            self.assertEqual(self.receive(api)[1]['data']['state'], 'stopped')
        self.assertIsNone(ledger.setting('delivery:' + M1))

    @isolated_profiles
    def test_missing_control_for_a_published_binding_fails_closed_until_start(self):
        api = Api(inbox=[message(M1)])
        ledger, _ = self.bound(api)
        with ledger.db: ledger.db.execute("delete from settings where key='tool-inbox-control'")
        with caller(self.home):
            self.assertEqual(self.receive(api)[1]['data']['state'], 'stopped')
            self.assertEqual(self.connect(api)['reception'], 'disabled')
            self.assertEqual(ledger.setting('tool-inbox-control'), {'enabled': False})
            self.assertEqual(self.receive(api)[1]['data']['state'], 'stopped')
            self.assertEqual(self.cli(api, 'session', 'start')[0], 0)
            self.assertEqual(self.receive(api)[1]['data']['message_id'], M1)
        self.assertEqual(self.spawns, [])

    @isolated_profiles
    def test_tool_start_only_arms_no_spawn(self):
        api = Api()
        ledger, _ = self.bound(api)
        with caller(self.home):
            code, result, text = self.cli(api, 'session', 'start')
            self.assertEqual(code, 0, text)
            self.assertEqual(self.cli(api, 'session', 'run', '--run-id', M3)[1]['code'], 'native_inbox_transport_conflict')
            # An omitted transport on an existing tool binding is an implicit change.
            code, result, _ = self.cli(api, 'session', 'connect', 'team', '--role', 'quorum-worker',
                                       '--project-ref', 'team/project')
            self.assertEqual(result['code'], 'native_inbox_transport_conflict')
            code, status, _ = self.cli(api, 'session', 'status')
        self.assertEqual(self.spawns, [])
        self.assertEqual((status['data']['listener'], status['data']['mode']), ('offline', 'codex_tool_inbox'))
        self.assertEqual(status['data']['worker_binding'], {'provider': 'codex', 'session_ref': CHILD,
                                                            'parent_thread_id': PARENT})
        # A native binding never adopts tool reception.
        with caller(self.home, sid=OTHER), patch('shutil.which', return_value='/usr/bin/codex'):
            self.cli(api, 'session', 'connect', 'team', '--role', 'native', '--project-ref', 'team/project')
            code, result, _ = self.receive(api)
        self.assertEqual(result['code'], 'native_inbox_transport_conflict')

    @isolated_profiles
    def test_exception_or_cancel_closes_waiter_and_preserves_offer(self):
        from quorum_api import QuorumError
        from quorum_os import FileLock
        api = Api(inbox=[message(M1)])
        ledger, record = self.bound(api)
        with caller(self.home): self.receive(api)
        offer = ledger.setting('delivery:' + M1)
        for name, failure in (('cancel', KeyboardInterrupt()), ('error', QuorumError('service_error'))):
            events = []
            api.inbox = []
            def interrupt(): raise failure
            with self.subTest(name=name):
                # Inbox reads during the cached offer would return M1 again; force an idle wait.
                ledger.setting('delivery:' + M1, {**offer, 'state': 'acked'})
                with self.assertRaises(type(failure)):
                    self.direct(api, ledger, record, 30, self.Waiter(events, on_wait=interrupt), events)
                self.assertEqual(events[-1], 'close')
                self.assertNotEqual(ledger.setting('listener')['state'], 'waiting')
                with FileLock(ledger.root / 'listener.lock'): pass  # Released after the failure.
        ledger.setting('delivery:' + M1, offer)
        api.inbox = [message(M1)]
        events = []
        # The authoritative read fails while the offer is cached: nothing is shown or acknowledged.
        api.fail['session_inbox'] = ['service_error']
        with self.assertRaises(QuorumError):
            self.direct(api, ledger, record, 30, self.Waiter(events, on_wait=lambda: None), events)
        self.assertEqual(events[-1], 'close')
        self.assertEqual(ledger.setting('delivery:' + M1), offer)
        self.assertEqual(api.acked, [])

    @isolated_profiles
    def test_lost_run_detected_without_pid_guess(self):
        api = Api()
        ledger, _ = self.bound(api)
        run_id = str(uuid.uuid4())
        # A live PID (this process) is never evidence of a run; only the held lock is.
        ledger.setting('listener', {'run_id': run_id, 'pid': os.getpid(), 'state': 'waiting', 'handled': 0,
                                    'timeout': 30, 'started_at': 1, 'updated_at': 1})
        with caller(self.home, sid=PARENT):
            code, status, text = self.cli(None, 'session', '--session-id', CHILD, 'status')
        self.assertEqual(code, 0, text)
        self.assertEqual((status['data']['listener'], status['data']['state'], status['data']['code'],
                          status['data']['run_id']), ('offline', 'error', 'listener_exited', run_id))


@POSIX_PROOF
class StopAndBudgetTests(Harness):
    """Interleavings at blocking boundaries; real ledgers and locks, fake API, waiter and clock."""
    def setUp(self):
        super().setUp()
        rollout(self.home)

    bound = LifecycleTests.bound
    direct = LifecycleTests.direct
    Waiter, Presence = LifecycleTests.Waiter, LifecycleTests.Presence

    def stopper(self, ledger, record):
        def stop_now():
            from quorum_native_inbox import stop
            with OtherConnection(ledger.root) as other: stop(other, record)
        return stop_now

    @isolated_profiles
    def test_stop_during_heartbeat_release_pulse_or_inbox_discloses_nothing(self):
        for point in ('heartbeat', 'pulse', 'release', 'inbox', 'cached inbox'):
            api = Api(inbox=[message(M1, body='private peer body')])
            with self.subTest(point=point):
                self.setUp()
                ledger, record = self.bound(api)
                if point == 'cached inbox':
                    with caller(self.home): self.receive(api)
                offer = ledger.setting('delivery:' + M1)
                events, stop_now = [], self.stopper(ledger, record)
                presence, waiter, seconds = self.Presence(events), None, 0
                if point == 'heartbeat': presence.tick = lambda *_: stop_now()
                if point == 'pulse':
                    waiter, seconds = self.Waiter(events), 30
                    waiter.pulse = lambda *_: stop_now()
                if point == 'release': api.hooks['session_requirements'] = stop_now
                if point in ('inbox', 'cached inbox'): api.hooks['session_inbox'] = stop_now
                mark = len(api.calls)
                from quorum_native_inbox import receive
                from quorum_state import LocalLedger
                auth = LocalLedger(Path(self.root))
                self.addCleanup(auth.db.close)
                with caller(self.home):
                    result = receive(api, auth, ledger, record, seconds, waiter=waiter, presence=presence)
                self.assertEqual((result['state'], result['message']), ('stopped', None))
                self.assertNotIn('private peer body', json.dumps(result))
                self.assertEqual(ledger.setting('delivery:' + M1), offer, 'nothing new committed, a cached offer kept')
                after = [call[0] for call in api.calls[mark:]]
                if point in ('heartbeat', 'pulse', 'release'): self.assertNotIn('session_inbox', after)
                self.assertNotIn('session_ack', after)
                with caller(self.home):
                    self.assertEqual(self.receive(api)[1]['data']['state'], 'stopped')

    def real_waiter(self, api, ledger, record, clock, sleeps):
        """The actual Presence and RealtimeWait fallback loop on a fake clock; Realtime itself is unavailable."""
        from quorum_presence import Presence
        from quorum_realtime import RealtimeWait
        presence = Presence(api, record, ledger, clock=lambda: clock[0], persist_epoch=True,
                            wall=lambda: 1000 + clock[0])
        def sleep(seconds):
            sleeps.append(seconds)
            clock[0] += seconds
        def connect(*_, **__): raise OSError('realtime unavailable')
        return presence, RealtimeWait(api, record, ledger, connect=connect, clock=lambda: clock[0], sleep=sleep,
                                      keepalive=presence, stop_aware=True)

    def run_receive(self, api, ledger, record, seconds, **seams):
        from quorum_native_inbox import receive
        from quorum_state import LocalLedger
        auth = LocalLedger(Path(self.root))
        self.addCleanup(auth.db.close)
        with caller(self.home): return receive(api, auth, ledger, record, seconds, **seams)

    @isolated_profiles
    def test_stop_during_presence_release_sends_no_heartbeat_or_later_operation(self):
        # Zero wait: the actual Presence's own release check is the first service operation.
        api = Api(inbox=[message(M1, body='private peer body')])
        ledger, record = self.bound(api)
        api.hooks['session_requirements'] = self.stopper(ledger, record)
        mark = len(api.calls)
        result = self.run_receive(api, ledger, record, 0)
        self.assertEqual((result['state'], result['message']), ('stopped', None))
        self.assertEqual([call[0] for call in api.calls[mark:]], ['session_requirements'])
        self.assertIsNone(ledger.setting('delivery:' + M1))
        # The unsent heartbeat leaves its epoch intent pending for a same-epoch retry.
        self.assertTrue(ledger.setting('presence-epoch')['pending'])

    @isolated_profiles
    def test_stop_during_nested_keepalive_inside_the_real_waiter_sends_no_heartbeat(self):
        api = Api()
        ledger, record = self.bound(api)
        clock, sleeps, trace = [100], [], []
        stop_now = self.stopper(ledger, record)
        original = api.call
        def traced(operation, payload, request_id=None):
            trace.append((clock[0], operation))
            # The first Presence release check of a nested keepalive inside the idle wait observes a stop.
            if operation == 'session_requirements' and clock[0] >= 120 and not any(op == 'stop' for _, op in trace):
                trace.append((clock[0], 'stop'))
                stop_now()
            return original(operation, payload, request_id)
        api.call = traced
        presence, waiter = self.real_waiter(api, ledger, record, clock, sleeps)
        result = self.run_receive(api, ledger, record, 60, waiter=waiter, presence=presence, clock=lambda: clock[0])
        self.assertEqual((result['state'], result['message']), ('stopped', None))
        after = [op for _, op in trace[[op for _, op in trace].index('stop'):]]
        self.assertEqual(after, ['stop'], 'no heartbeat, release or inbox operation after the stop')
        self.assertEqual(ledger.setting('listener-wait')['mode'], 'stopped')

    @isolated_profiles
    def test_idle_budget_excludes_nested_heartbeat_time_in_the_real_waiter(self):
        api = Api()
        ledger, record = self.bound(api)
        clock, sleeps, nested = [100], [], []
        original = api.call
        presence, waiter = self.real_waiter(api, ledger, record, clock, sleeps)
        inside = [False]
        real_wait = waiter.wait
        def wait(stopping):
            inside[0] = True
            try: return real_wait(stopping)
            finally: inside[0] = False
        waiter.wait = wait
        def slow(operation, payload, request_id=None):
            if operation == 'session_heartbeat':
                nested.append(inside[0])
                clock[0] += 5  # A slow heartbeat RPC: service time, never idle waiting.
            return original(operation, payload, request_id)
        api.call = slow
        started = clock[0]
        result = self.run_receive(api, ledger, record, 25, waiter=waiter, presence=presence, clock=lambda: clock[0])
        self.assertEqual((result['state'], result['message']), ('timeout', None))
        self.assertIn(True, nested, 'a heartbeat ran inside the waiter during the idle wait')
        self.assertEqual(sum(sleeps), 25, 'exactly the idle budget was slept')
        self.assertGreaterEqual(clock[0] - started, 25 + 5 * nested.count(True))

    @isolated_profiles
    def test_rearm_refused_while_a_consumer_is_active_so_no_stale_epoch_returns(self):
        from quorum_native_inbox import arm, receive
        from quorum_os import FileLock
        from quorum_state import LocalLedger
        api = Api()
        ledger, record = self.bound(api)
        auth = LocalLedger(Path(self.root))
        self.addCleanup(auth.db.close)
        refused = []
        def restart_during_heartbeat():
            with OtherConnection(ledger.root) as other:
                try: arm(other, record)
                except ValueError as error: refused.append(str(error))
        api.hooks['session_heartbeat'] = restart_during_heartbeat
        with caller(self.home): self.assertEqual(receive(api, auth, ledger, record, 0)['state'], 'timeout')
        self.assertEqual(refused, ['worker_already_running'])
        epoch = ledger.setting('presence-epoch')
        self.assertFalse(epoch['pending'])
        with FileLock(ledger.root / 'listener.lock'), caller(self.home):
            code, result, _ = self.cli(api, 'session', 'start')
        self.assertEqual((code, result['code']), (3, 'worker_already_running'))
        self.assertEqual(ledger.setting('presence-epoch'), epoch)
        # Between calls the same worker's start resets the epoch; the next call says Hello once.
        with caller(self.home): self.assertEqual(self.cli(api, 'session', 'start')[0], 0)
        self.assertIsNone(ledger.setting('presence-epoch'))
        with caller(self.home): receive(api, auth, ledger, record, 0)
        self.assertNotEqual(ledger.setting('presence-epoch')['connection_id'], epoch['connection_id'])

    @isolated_profiles
    def test_idle_budget_excludes_service_time(self):
        api = Api()
        ledger, record = self.bound(api)
        clock, events = [100], []
        original = api.call
        def slow(operation, payload, request_id=None):
            if operation == 'session_inbox': clock[0] += 10  # Network time, not idle waiting.
            return original(operation, payload, request_id)
        api.call = slow
        waiter = self.Waiter(events, clock=clock)
        result = self.direct(api, ledger, record, 5, waiter, events, clock=clock)
        self.assertEqual(result['state'], 'timeout')
        self.assertEqual(events.count('wait'), 5)
        # A message that arrives during the idle budget is still offered after slow reads.
        events.clear()
        waiter = self.Waiter(events, clock=clock, on_wait=lambda: api.inbox.append(message(M1)))
        self.assertEqual(self.direct(api, ledger, record, 5, waiter, events, clock=clock)['message_id'], M1)


class EpochTests(Harness):
    def presence(self, ledger, api, wall):
        from quorum_presence import Presence
        return Presence(api, {'agent_id': AGENT, 'session_ref': CHILD}, ledger, persist_epoch=True,
                        wall=lambda: wall[0])

    def ids(self, api): return [payload['connection_id'] for op, payload, _ in api.calls if op == 'session_heartbeat']

    def fresh(self):
        from quorum_state import LocalLedger
        ledger = LocalLedger(Path(self.root))
        self.addCleanup(ledger.db.close)
        return ledger

    @isolated_profiles
    def test_epoch_survives_calls_and_ambiguous_heartbeat(self):
        ledger, api, wall = self.fresh(), Api(), [1000]
        self.presence(ledger, api, wall).tick()
        wall[0] = 1010
        api.fail['session_heartbeat'] = ['service_unavailable']
        self.presence(ledger, api, wall).tick()
        self.assertEqual(ledger.setting('presence')['state'], 'unreachable')
        self.assertTrue(ledger.setting('presence-epoch')['pending'])
        wall[0] = 1020
        self.presence(ledger, api, wall).tick()
        self.assertEqual(len(set(self.ids(api))), 1, 'separate bounded calls keep one Hello epoch')
        self.assertEqual(ledger.setting('presence-epoch'), {'connection_id': self.ids(api)[0], 'acked_at': 1020,
                                                            'pending': False})
        # The default background presence is unchanged: each process is a new epoch.
        from quorum_presence import Presence
        Presence(api, {'agent_id': AGENT, 'session_ref': CHILD}, ledger).tick()
        self.assertNotEqual(self.ids(api)[-1], self.ids(api)[0])
        self.assertEqual(ledger.setting('presence-epoch')['connection_id'], self.ids(api)[0])

    @isolated_profiles
    def test_pending_heartbeat_within_acked_epoch_is_resolved_before_rotation(self):
        ledger, api, wall = self.fresh(), Api(), [1000]
        self.presence(ledger, api, wall).tick()
        first = self.ids(api)[-1]
        # The service processed this heartbeat but its response was lost: the epoch intent stays pending.
        wall[0] = 1010
        api.lost.append('session_heartbeat')
        self.presence(ledger, api, wall).tick()
        self.assertEqual(ledger.setting('presence-epoch'), {'connection_id': first, 'acked_at': 1000, 'pending': True})
        wall[0] = 1071  # Past the health window since the last acknowledgement, yet unresolved.
        self.presence(ledger, api, wall).tick()
        self.assertEqual(self.ids(api)[-1], first)
        self.assertEqual(ledger.setting('presence-epoch'), {'connection_id': first, 'acked_at': 1071, 'pending': False})
        wall[0] = 1140  # A separately justified gap after resolution starts exactly one new epoch.
        self.presence(ledger, api, wall).tick()
        self.assertNotEqual(self.ids(api)[-1], first)
        self.assertEqual(len(set(self.ids(api))), 2)

    @POSIX_PROOF
    @isolated_profiles
    def test_epoch_rotates_once_at_60_seconds_or_explicit_restart(self):
        ledger, api, wall = self.fresh(), Api(), [1000]
        self.presence(ledger, api, wall).tick()
        first = self.ids(api)[-1]
        wall[0] = 1059
        self.presence(ledger, api, wall).tick()
        self.assertEqual(self.ids(api)[-1], first)
        wall[0] = 1119
        api.fail['session_heartbeat'] = ['service_unavailable']
        self.presence(ledger, api, wall).tick()
        second = self.ids(api)[-1]
        self.assertNotEqual(second, first)
        # The unresolved new epoch is retried, never rotated again, however long the gap.
        wall[0] = 1500
        self.presence(ledger, api, wall).tick()
        self.assertEqual(self.ids(api)[-1], second)
        self.assertEqual(len(set(self.ids(api))), 2)
        # An explicit same-worker start clears the epoch: the next call says Hello once.
        rollout(self.home)
        with caller(self.home): self.connect(api)
        child = self.ledger()
        self.presence(child, api, wall).tick()
        before = child.setting('presence-epoch')['connection_id']
        with caller(self.home): self.assertEqual(self.cli(api, 'session', 'start')[0], 0)
        self.assertIsNone(child.setting('presence-epoch'))
        wall[0] = 1501
        self.presence(child, api, wall).tick()
        self.assertNotEqual(child.setting('presence-epoch')['connection_id'], before)

    @isolated_profiles
    def test_clock_reversal_does_not_publish_false_freshness(self):
        ledger, api, wall = self.fresh(), Api(), [1000]
        self.presence(ledger, api, wall).tick()
        first = self.ids(api)[-1]
        wall[0] = 900
        api.fail['session_heartbeat'] = ['service_unavailable']
        self.presence(ledger, api, wall).tick()
        self.assertNotEqual(self.ids(api)[-1], first, 'an unknown gap is treated as a reconnect')
        self.assertEqual(ledger.setting('presence'), {'state': 'unreachable', 'code': 'service_unavailable'})
        self.assertEqual(ledger.setting('presence-epoch')['acked_at'], None)
        self.presence(ledger, api, wall).tick()
        self.assertEqual(ledger.setting('presence')['last_heartbeat_at'], 900)
        self.assertEqual(ledger.setting('presence-epoch')['acked_at'], 900)


if __name__ == '__main__': unittest.main()
