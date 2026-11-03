"""Claude native-worker foreground inbox: fake clocks, local fixtures and a fake API only.

No provider, model, transcript, Quorum service, auth or network call is made here.
"""
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import unittest
from unittest.mock import patch
import uuid
from quorum_test_support import isolated_profiles

SCRIPTS = Path(__file__).resolve().parents[1] / 'skills/quorum/scripts'
sys.path.insert(0, str(SCRIPTS))
SID = '5b0c1c39-6c2e-4a4f-9a51-2f8d3c1e7a10'
OTHER_SID = '0e9d6f1a-3b7c-4d2e-8f10-1a2b3c4d5e6f'
HANDLE = 'agent_01 quorum · ünicode'
TOOL_USE = 'toolu_01ABCdef'
INVITE = '11111111-1111-4111-8111-111111111111'
AGENT = '66666666-6666-4666-8666-666666666666'
PEER = '77777777-7777-4777-8777-777777777777'
QUORUM = 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb'
OTHER_QUORUM = 'cccccccc-cccc-4ccc-8ccc-cccccccccccc'
M1 = '99999999-9999-4999-8999-999999999991'
M2 = '99999999-9999-4999-8999-999999999992'
M3 = '99999999-9999-4999-8999-999999999993'
ORIGIN = 'https://example.com'
KEY = 'sb_publishable_synthetic'
T0 = 1_800_000_000.0
EXPORTS = ('CODEX_THREAD_ID', 'CODEX_HOME', 'CLAUDE_CODE_SESSION_ID', 'CLAUDE_CODE_HOST_SESSION_ID',
           'CLAUDE_CODE_MESSAGING_SOCKET', 'CLAUDE_CODE_MESSAGING_TOKEN', 'GROK_SESSION_ID',
           'GROK_LEADER_SOCKET', 'CURSOR_CONVERSATION_ID', 'CURSOR_AGENT', 'SAND_AGENT_STORE_RPC_SERVICE')
POSIX_PROOF = unittest.skipIf(os.name == 'nt', 'Claude worker slots need POSIX owner proof; Windows fails closed')


@contextlib.contextmanager
def claude(sid=SID, **extra):
    """A synthetic Claude export on a copy of the environment; ordinary OS values such as SystemRoot stay."""
    with patch.dict(os.environ):
        for key in EXPORTS: os.environ.pop(key, None)
        if sid is not None: os.environ['CLAUDE_CODE_SESSION_ID'] = sid
        os.environ.update(extra)
        yield


def sql_signature(name):
    """Argument names of the checked-in public SQL function: the contract the RPC body must match."""
    import re
    sql = (Path(__file__).resolve().parents[1] / 'skills/quorum/references/server-bootstrap.sql').read_text()
    found = re.findall(r'CREATE OR REPLACE FUNCTION public\.' + name + r'\(([^)]*)\)', sql)
    assert len(found) == 1, name
    return {part.split()[0] for part in found[0].split(',')}


def installed():
    from quorum_release import installed_release
    return installed_release().copy()


class Api:
    """A fake service: records calls, never contacts a network."""
    def __init__(self, inbox=()):
        self.calls, self.inbox, self.acked, self.fail = [], list(inbox), [], {}
        self.required, self.replies, self.revoked, self.lost, self.hooks = installed(), {}, False, [], {}
        self.lookup = QUORUM
        # quorum_create accepts only the checked SQL arguments (named JSON keys become function arguments).
        self.create_arguments = {name[2:] for name in sql_signature('quorum_create')} - {'request_id'}

    config = type('Config', (), {'origin': ORIGIN, 'publishable_key': KEY})()
    def token(self): return 'synthetic-jwt'
    def ops(self): return [call[0] for call in self.calls if call[0] != 'session_requirements']

    def call(self, operation, payload, request_id=None):
        from quorum_api import QuorumError
        self.calls.append((operation, json.loads(json.dumps(payload)), request_id))
        if self.fail.get(operation):
            code = self.fail[operation].pop(0)
            if code: raise QuorumError(code)
        if self.revoked and operation.startswith('session_'): raise QuorumError('membership_revoked')
        if operation == 'quorum_create' and self.create_arguments is not None and set(payload) - self.create_arguments:
            raise QuorumError('service_error')
        result = self.answer(operation, payload)
        hook = self.hooks.pop(operation, None)
        if hook: hook()
        if operation in self.lost:
            self.lost.remove(operation)
            raise QuorumError('service_unavailable')
        return result

    def answer(self, operation, payload):
        if operation == 'session_requirements': return dict(self.required)
        if operation == 'quorum_lookup': return {'quorum_id': self.lookup}
        if operation == 'quorum_create': return {'quorum_id': QUORUM, 'slug': payload['slug']}
        if operation == 'quorum_set_access': return {'access': payload['access']}
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
            return {'reply_message_id': reply, 'conversation_id': 'c-1'}
        if operation == 'invite_accept_id': return {'quorum_id': QUORUM, 'membership_id': 'membership-1'}
        raise AssertionError(operation)


def message(mid, *, priority='normal', body='hello', **extra):
    return {'id': mid, 'recipient_id': AGENT, 'sender_id': PEER, 'sender_role': 'peer',
            'conversation_id': 'c-1', 'body': body, 'priority': priority, 'reply_to': None, **extra}


class Store:
    backend = None
    def load(self, _): return None


def forbidden(name):
    def fail(*_, **__): raise AssertionError(name + ' must not run')
    return fail


ADAPTER = 'quorum_claude_worker'
ADAPTER_INTERFACE = ('prepare', 'attest', 'await_binding', 'slot_status', 'stop_slot', 'authorize', 'guard',
                     'pin_target', 'create_worker')


def require_adapter(test):
    """Name the expected missing feature before any fixture patches the adapter's attributes.

    Only an absent module or interface is reported as the missing adapter; a module that exists but
    fails to import (a broken dependency or fixture) propagates as an ordinary error.
    """
    import importlib
    import importlib.util
    if importlib.util.find_spec(ADAPTER) is None:
        test.fail('missing_claude_worker_adapter: module ' + ADAPTER + ' is absent')
    module = importlib.import_module(ADAPTER)
    missing = [name for name in ADAPTER_INTERFACE if not callable(getattr(module, name, None))]
    if missing: test.fail('missing_claude_worker_adapter_interface: ' + ', '.join(missing))


class Harness(unittest.TestCase):
    def setUp(self):
        require_adapter(self)
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name).resolve()
        self.root = self.base / 'state'
        self.project = self.base / 'project'
        self.project.mkdir()
        previous = os.getcwd()
        os.chdir(self.project)
        self.addCleanup(os.chdir, previous)
        self.now = T0
        clock = patch('quorum_claude_worker.now', side_effect=lambda: self.now)
        clock.start()
        self.addCleanup(clock.stop)
        self.sleeps = []
        sleeper = patch('quorum_claude_worker.sleep', side_effect=self.sleep)
        sleeper.start()
        self.addCleanup(sleeper.stop)
        self.on_sleep = None
        # The adapter never spawns, queues, steers or pipes; every such route fails the test.
        for target in ('quorum_listener.spawn_background', 'quorum_session.enqueue', 'quorum_codex_steer.submit',
                       'quorum_claude_pipe.submit', 'quorum_claude_pipe.binding'):
            guard = patch(target, side_effect=forbidden(target))
            guard.start()
            self.addCleanup(guard.stop)

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds
        if self.on_sleep: self.on_sleep()

    def scope(self, **changes):
        value = {'mode': 'create', 'service': {'origin': ORIGIN,
                                               'publishable_key_sha256': hashlib.sha256(KEY.encode()).hexdigest()},
                 'quorum_slug': 'team', 'access': 'github', 'role': 'service-worker',
                 'project_ref': 'team/project', 'cwd': str(self.project)}
        value.update(changes)
        return {key: item for key, item in value.items() if item is not None}

    def accept_scope(self, **changes):
        value = self.scope(mode='accept', invite_id=INVITE)
        value.pop('access')
        value.update(changes)
        return {key: item for key, item in value.items() if item is not None}

    def prepare(self, scope=None, sid=SID):
        import quorum_claude_worker as worker
        with claude(sid):
            return worker.prepare(self.root, self.scope() if scope is None else scope)

    def attest(self, slot, handle=HANDLE, sid=SID, tool=None):
        import quorum_claude_worker as worker
        with claude(sid):
            return worker.attest(self.root, slot, handle, tool)

    def active(self, scope=None, handle=HANDLE):
        slot = self.prepare(scope)['slot_id']
        self.attest(slot, handle)
        return slot, self.capability(slot)

    def capability(self, slot):
        return self.root / 'claude-workers' / slot / 'capability.json'

    def authorize(self, cap, operation, arguments=None, sid=SID):
        import quorum_claude_worker as worker
        with claude(sid):
            return worker.authorize(self.root, cap, operation, arguments or {})

    def denied(self, code, function, *args, **kwargs):
        from quorum_native_inbox import NativeInboxError
        with self.assertRaises(NativeInboxError) as raised:
            function(*args, **kwargs)
        self.assertEqual(raised.exception.code, code)
        return raised.exception

    def setting(self, slot, key='claude-worker-slot'):
        from quorum_state import LocalLedger
        ledger = LocalLedger(self.root / 'claude-workers' / slot)
        try: return ledger.setting(key)
        finally: ledger.db.close()

    def write_setting(self, slot, value, key='claude-worker-slot'):
        from quorum_state import LocalLedger
        ledger = LocalLedger(self.root / 'claude-workers' / slot)
        try: ledger.setting(key, value)
        finally: ledger.db.close()

    def cli(self, api, *argv, stdin=b'', sid=SID, extra=None):
        import quorum
        from quorum_api import QuorumError, ServiceConfig
        config = ServiceConfig(ORIGIN, KEY)
        service = (dict(return_value=(api, config, Store())) if api is not None
                   else dict(side_effect=QuorumError('auth_required')))
        with claude(sid, **(extra or {})), patch('quorum_live.api_for', **service), \
                patch('quorum_profiles.ensure_login', return_value={'authenticated': True, 'reused': True}), \
                patch('sys.stdin', io.TextIOWrapper(io.BytesIO(stdin))), \
                contextlib.redirect_stdout(io.StringIO()) as output:
            code = quorum.main(['--state', str(self.root), *argv])
        text = output.getvalue()
        return code, json.loads(text), text


    def configure(self, origin=ORIGIN, key=KEY):
        """This state's existing nonsecret service config, as the parent's earlier setup wrote it."""
        path = self.root / 'config.json'
        path.write_text(json.dumps({'origin': origin, 'publishable_key': key}))
        path.chmod(0o600)

    def joined(self, api, scope=None, connect=True, extra=(), handle=HANDLE):
        """Accept-mode slot: owner prepare/attest, then the child's own accept-id and connect."""
        slot, cap = self.active(scope or self.accept_scope(), handle=handle)
        self.configure()
        code, result, text = self.cli(api, 'accept-id', INVITE, '--transport', 'tool-inbox',
                                      '--worker-capability', str(cap))
        self.assertEqual(code, 0, text)
        if connect:
            code, result, text = self.connect(api, cap, *extra)
            self.assertEqual(code, 0, text)
        return slot, cap, result

    def connect(self, api, cap, *extra):
        return self.cli(api, 'session', '--worker-capability', str(cap), 'connect', 'team', '--role', 'service-worker',
                        '--project-ref', 'team/project', '--transport', 'tool-inbox', *extra)

    def worker_ledger(self, slot):
        from quorum_state import LocalLedger
        session_ref = self.setting(slot)['binding']['session_ref']
        ledger = LocalLedger(self.root / 'sessions' / session_ref)
        self.addCleanup(ledger.db.close)
        return ledger


@POSIX_PROOF
class SlotTests(Harness):
    def test_prepare_creates_multiple_missing_private_state_ancestors(self):
        import quorum_claude_worker as worker
        from quorum_native_inbox import NativeInboxError
        home = self.base / 'home'
        home.mkdir(mode=0o755)
        self.root = home / '.local' / 'share' / 'quorum'
        with claude():
            self.denied('native_worker_unavailable', worker.slot_status, self.root, str(uuid.uuid4()))
        self.assertFalse((home / '.local').exists(), 'read-only status must not bootstrap state')
        try: prepared = self.prepare()
        except NativeInboxError as error: self.fail('fresh nested state must prepare: ' + error.code)
        self.assertEqual(prepared['state'], 'pending')
        for directory in (home / '.local', home / '.local/share', self.root):
            self.assertEqual(stat.S_IMODE(directory.stat().st_mode), 0o700)
            self.assertEqual(directory.stat().st_uid, os.getuid())
        self.assertEqual(stat.S_IMODE(home.stat().st_mode), 0o755, 'existing ancestors are unchanged')

    def test_prepare_refuses_existing_unsafe_state_ancestors_unchanged(self):
        for kind in ('symlink', 'writable'):
            with self.subTest(kind=kind):
                parent = self.base / kind
                outside = self.base / (kind + '-outside')
                outside.mkdir(mode=0o700)
                if kind == 'symlink': parent.symlink_to(outside, target_is_directory=True)
                else:
                    parent.mkdir(mode=0o700)
                    parent.chmod(0o777)
                self.root = parent / 'missing' / 'state'
                self.denied('native_worker_unavailable', self.prepare)
                self.assertFalse((parent / 'missing').exists())
                self.assertEqual(list(outside.iterdir()), [])
                if kind == 'symlink': self.assertTrue(parent.is_symlink())
                else: self.assertEqual(stat.S_IMODE(parent.stat().st_mode), 0o777)

    def test_prepare_is_pending_with_no_profile_auth_or_api(self):
        with patch('quorum_profiles.resolve_profile', side_effect=forbidden('profile')), \
                patch('quorum_live.api_for', side_effect=forbidden('api')), \
                patch('quorum_api.QuorumApi.call', side_effect=forbidden('call')), \
                patch('quorum_auth.token_provider', side_effect=forbidden('token')):
            result = self.prepare()
            code, cli, text = self.cli(None, 'session', 'worker', 'prepare', '--json-stdin',
                                       stdin=json.dumps(self.scope()).encode())
        self.assertEqual(code, 0, text)
        for value in (result, cli['data']):
            self.assertEqual(set(value), {'slot_id', 'state', 'lifetime', 'pending_expires_at',
                                          'permission_expires_at', 'bootstrap_command'})
            self.assertEqual((value['state'], value['lifetime']), ('pending', 'bounded'))
            self.assertEqual((value['pending_expires_at'], value['permission_expires_at']), (T0 + 120, T0 + 900))
            self.assertEqual(str(uuid.UUID(value['slot_id'])), value['slot_id'])
            self.assertEqual(value['bootstrap_command'][-8:-6], ['--state', str(self.root)])
            self.assertEqual(value['bootstrap_command'][-6:], ['session', 'worker', 'await-binding',
                                                              value['slot_id'], '--wait-seconds', '60'])
        slot = self.root / 'claude-workers' / result['slot_id']
        self.assertEqual(stat.S_IMODE(slot.stat().st_mode), 0o700)
        self.assertFalse((slot / 'capability.json').exists(), 'pending has no capability')
        record = self.setting(result['slot_id'])
        self.assertEqual((record['state'], record['generation'], record['provider_session_id']), ('pending', 1, SID))
        uuid.UUID(record['create_request_id'])
        self.assertFalse((self.root / 'sessions').exists())
        # Bounds: TTL 1..3600, and a shorter bounded permission shortens the pending window.
        self.assertEqual(self.prepare(self.scope(ttl_seconds=60))['pending_expires_at'], T0 + 60)
        self.assertEqual(self.prepare(self.scope(ttl_seconds=3600))['permission_expires_at'], T0 + 3600)
        for ttl in (0, 3601, True, '900', 1.5):
            self.denied('native_worker_binding_denied', self.prepare, self.scope(ttl_seconds=ttl))
        self.assertNotIn('create_request_id', json.dumps(result))

    def test_attest_without_tool_use_id_succeeds_without_history_access(self):
        home = self.base / 'home'
        transcript = home / '.claude' / 'projects' / 'p' / (SID + '.jsonl')
        transcript.parent.mkdir(parents=True)
        transcript.write_text('{"marker":"private-transcript-body"}\n')
        slot = self.prepare()['slot_id']
        opened = []
        real_open = os.open
        def tracking(path, *args, **kwargs):
            opened.append(str(path))
            return real_open(path, *args, **kwargs)
        with patch.dict(os.environ, HOME=str(home), CLAUDE_CONFIG_DIR=str(home / '.claude')), \
                patch('quorum_claude_worker.proof_available', return_value=True), \
                patch('os.open', side_effect=tracking), patch('builtins.open', side_effect=forbidden('open')):
            binding = self.attest(slot)
        self.assertFalse([path for path in opened if str(home) in path])
        self.assertNotIn('spawn_tool_use_id', binding)
        token = json.loads(self.capability(slot).read_text())['token']
        self.assertNotIn(token, json.dumps(binding))
        self.assertNotIn('token', binding)
        self.assertEqual(stat.S_IMODE(self.capability(slot).stat().st_mode), 0o600)
        self.assertEqual(binding['state'], 'active')

    def test_actual_handle_sid_scope_and_uuid5_are_pinned(self):
        slot = self.prepare()['slot_id']
        binding = self.attest(slot, tool=TOOL_USE)
        actor = str(uuid.uuid5(uuid.UUID(SID), HANDLE))
        self.assertEqual({key: binding[key] for key in ('provider', 'binding_version', 'provider_session_id',
                                                         'native_worker_handle', 'logical_actor_id', 'session_ref',
                                                         'slot_id', 'generation', 'attestation', 'spawn_tool_use_id')},
                         {'provider': 'claude', 'binding_version': 1, 'provider_session_id': SID,
                          'native_worker_handle': HANDLE, 'logical_actor_id': actor,
                          'session_ref': 'claude-worker:' + actor, 'slot_id': slot, 'generation': 1,
                          'attestation': 'owner-parent-native-receipt', 'spawn_tool_use_id': TOOL_USE})
        self.assertRegex(binding['authority_hash'], '^[0-9a-f]{64}$')
        self.assertEqual(self.attest(slot, tool=TOOL_USE), binding, 'an identical attest is stable')
        record = self.setting(slot)
        self.assertEqual(record['scope']['quorum_slug'], 'team')
        self.assertEqual(record['binding']['authority_hash'], binding['authority_hash'])
        import quorum_claude_worker as worker
        with claude():
            awaited = worker.await_binding(self.root, slot, 1)
        self.assertEqual(awaited['binding'], binding)
        self.assertEqual(awaited['capability_file'], str(self.capability(slot)))

    def test_changed_attestation_and_wrong_sid_fail(self):
        import quorum_claude_worker as worker
        slot = self.prepare()['slot_id']
        for handle in ('', 'a\nb', 'tab\there', 'x' * 257, 'é' * 129, None, 7):
            self.denied('native_worker_binding_denied', self.attest, slot, handle)
        self.denied('native_worker_binding_denied', self.attest, slot, HANDLE, tool='bad\x00id')
        self.assertEqual(self.setting(slot)['state'], 'pending')
        self.denied('native_worker_binding_denied', self.attest, slot, HANDLE, sid=OTHER_SID)
        self.attest(slot)
        self.denied('native_worker_binding_denied', self.attest, slot, HANDLE + ' changed')
        self.denied('native_worker_binding_denied', self.attest, slot, HANDLE, tool=TOOL_USE)
        for function, args in ((worker.await_binding, (self.root, slot, 1)), (worker.slot_status, (self.root, slot)),
                               (worker.stop_slot, (self.root, slot))):
            with claude(OTHER_SID): self.denied('native_worker_binding_denied', function, *args)
        with claude(None, CODEX_THREAD_ID=OTHER_SID):
            self.denied('native_worker_unavailable', worker.attest, self.root, slot, HANDLE)
        with claude(SID, CODEX_THREAD_ID=OTHER_SID):
            self.denied('native_worker_unavailable', worker.slot_status, self.root, slot)
        with claude(SID.upper()):
            self.denied('native_worker_unavailable', worker.slot_status, self.root, slot)
        self.assertEqual(self.setting(slot)['state'], 'active')

    def test_actor_index_prevents_cross_slot_same_handle_regrant(self):
        import quorum_claude_worker as worker
        first = self.prepare()['slot_id']
        self.attest(first)
        second = self.prepare()['slot_id']
        self.denied('native_worker_binding_denied', self.attest, second, HANDLE)
        self.assertEqual(self.setting(second)['state'], 'pending')
        with claude(): worker.stop_slot(self.root, first)
        third = self.prepare()['slot_id']
        self.denied('native_worker_binding_denied', self.attest, third, HANDLE)
        from quorum_state import LocalLedger
        root = LocalLedger(self.root)
        try:
            actor = str(uuid.uuid5(uuid.UUID(SID), HANDLE))
            self.assertEqual(root.setting('claude-worker-actor:claude-worker:' + actor),
                             {'slot_id': first, 'provider_session_id': SID})
        finally: root.db.close()
        # A different handle is a different actor with its own fresh slot.
        self.assertEqual(self.attest(third, HANDLE + '-2')['slot_id'], third)

    def test_pending_timeout_attest_race_is_serialized(self):
        import quorum_claude_worker as worker
        slot = self.prepare()['slot_id']
        with claude():
            self.denied('native_worker_slot_terminal', worker.await_binding, self.root, slot, 2)
        self.assertEqual((self.setting(slot)['state'], self.setting(slot)['terminal_reason']),
                         ('terminal', 'await_timeout'))
        self.denied('native_worker_slot_terminal', self.attest, slot)
        self.assertFalse(self.capability(slot).exists())
        # An attest that wins under the slot lock before the timeout is returned as active.
        racing = self.prepare()['slot_id']
        self.on_sleep = lambda: (setattr(self, 'on_sleep', None), self.attest(racing))
        with claude():
            awaited = worker.await_binding(self.root, racing, 60)
        self.assertEqual((awaited['state'], awaited['binding']['slot_id']), ('active', racing))
        for wait in (0, 61, True, '60'):
            with claude(): self.denied('native_worker_binding_denied', worker.await_binding, self.root, racing, wait)

    def test_pending_stop_expiry_are_terminal(self):
        import quorum_claude_worker as worker
        stopped = self.prepare()['slot_id']
        with claude():
            result = worker.stop_slot(self.root, stopped)
            self.assertEqual((result['state'], result['terminal_reason']), ('terminal', 'stopped'))
            self.assertEqual(worker.stop_slot(self.root, stopped)['state'], 'terminal')
        self.denied('native_worker_slot_terminal', self.attest, stopped)
        expired = self.prepare()['slot_id']
        self.now = T0 + 120
        self.denied('native_worker_slot_terminal', self.attest, expired)
        with claude():
            status = worker.slot_status(self.root, expired)
        self.assertEqual((status['state'], status['terminal_reason']), ('terminal', 'pending_expired'))
        self.assertNotIn('token', json.dumps(status))
        with claude(): self.denied('native_worker_slot_terminal', worker.await_binding, self.root, expired, 5)

    def test_private_path_symlink_owner_swap_and_oversize_fail_without_chmod(self):
        slot, cap = self.active()
        self.assertTrue(self.authorize(cap, 'status'))
        original = cap.read_bytes()
        # Group-readable capability: refused and never chmodded.
        cap.chmod(0o640)
        self.denied('native_worker_unavailable', self.authorize, cap, 'status')
        self.assertEqual(stat.S_IMODE(cap.stat().st_mode), 0o640)
        cap.chmod(0o600)
        # A symlinked capability or slot directory is refused.
        cap.rename(cap.with_name('moved.json'))
        cap.symlink_to(cap.with_name('moved.json'))
        self.denied('native_worker_unavailable', self.authorize, cap, 'status')
        cap.unlink()
        cap.with_name('moved.json').rename(cap)
        slot_dir = cap.parent
        slot_dir.rename(slot_dir.with_name('real'))
        slot_dir.symlink_to(slot_dir.with_name('real'))
        self.denied('native_worker_unavailable', self.authorize, cap, 'status')
        slot_dir.unlink()
        slot_dir.with_name('real').rename(slot_dir)
        # Oversized or hardlinked capability files are refused.
        cap.write_bytes(b' ' * 16385 + original)
        cap.chmod(0o600)
        self.denied('native_worker_unavailable', self.authorize, cap, 'status')
        cap.write_bytes(original)
        os.link(cap, self.base / 'hardlink.json')
        self.denied('native_worker_unavailable', self.authorize, cap, 'status')
        (self.base / 'hardlink.json').unlink()
        # A copied capability outside the generated path is denied even with a valid token.
        outside = self.base / 'copy.json'
        outside.write_bytes(original)
        outside.chmod(0o600)
        self.denied('native_worker_binding_denied', self.authorize, outside, 'status')
        # A world-writable state root is refused without chmod.
        self.root.chmod(0o777)
        self.denied('native_worker_unavailable', self.authorize, cap, 'status')
        self.assertEqual(stat.S_IMODE(self.root.stat().st_mode), 0o777)
        self.root.chmod(0o700)
        self.assertTrue(self.authorize(cap, 'status'))

    def test_capability_generation_operation_cwd_scope_and_expiry_deny(self):
        pending = self.prepare()['slot_id']
        self.denied('native_worker_binding_denied', self.authorize, self.capability(pending), 'status')
        slot, cap = self.active(self.scope(operations=['create', 'connect', 'status', 'stop']))
        self.denied('native_worker_binding_denied', self.authorize, cap, 'receive', {'wait_seconds': 0})
        self.denied('native_worker_binding_denied', self.authorize, cap, 'run')
        connect = {'quorum': 'team', 'role': 'service-worker', 'project_ref': 'team/project',
                   'transport': 'tool-inbox', 'accept_tasks_from': None, 'dispatch_mode': 'inline'}
        for change in ({'quorum': 'other'}, {'role': 'admin'}, {'project_ref': 'x'}, {'transport': None},
                       {'accept_tasks_from': 'coordinator'}, {'dispatch_mode': 'native-subagent'}):
            self.denied('native_worker_binding_denied', self.authorize, cap, 'connect', {**connect, **change})
        self.denied('native_worker_binding_denied', self.authorize, cap, 'create',
                    {'quorum': 'team', 'access': 'open'})
        self.assertEqual(self.authorize(cap, 'create', {'quorum': 'team', 'access': 'github'})['operation'], 'create')
        other = self.base / 'elsewhere'
        other.mkdir()
        os.chdir(other)
        self.denied('native_worker_binding_denied', self.authorize, cap, 'status')
        os.chdir(self.project)
        self.denied('native_worker_binding_denied', self.authorize, cap, 'status', sid=OTHER_SID)
        record = self.setting(slot)
        self.write_setting(slot, {**record, 'generation': 2})
        self.denied('native_worker_binding_denied', self.authorize, cap, 'status')
        self.write_setting(slot, record)
        data = json.loads(cap.read_text())
        cap.write_text(json.dumps({**data, 'token': data['token'][:-2] + 'xx'}))
        self.denied('native_worker_binding_denied', self.authorize, cap, 'status')
        cap.write_text(json.dumps(data))
        self.now = T0 + 900
        self.denied('native_worker_slot_terminal', self.authorize, cap, 'status')

    def test_same_user_capability_possession_is_delegated_authority(self):
        slot, cap = self.active()
        context = self.authorize(cap, 'status')
        self.assertEqual(context['authority'], 'same-user-capability')
        self.assertEqual(context['binding']['session_ref'],
                         'claude-worker:' + str(uuid.uuid5(uuid.UUID(SID), HANDLE)))
        self.assertEqual(context['capability_file'], str(cap))
        token = json.loads(cap.read_text())['token']
        self.assertNotIn(token, json.dumps(context))
        self.assertNotIn('token', context)
        # The parent shares the actual SID and OS user: possession delegates, it does not authenticate a child.
        self.assertEqual(self.authorize(cap, 'status')['binding'], context['binding'])

    def test_permission_lifetime_schema_rejects_conflicting_ttl(self):
        import quorum_claude_worker as worker
        bad = [self.scope(lifetime='until-owner-stop', ttl_seconds=900), self.scope(lifetime='forever'),
               self.scope(extra=1), self.scope(access='private'), self.scope(role='Bad Role'),
               self.scope(quorum_slug='-x'), self.scope(cwd='relative'), self.scope(cwd=str(self.base)),
               self.scope(service={'origin': 'http://example.com', 'publishable_key_sha256': '0' * 64}),
               self.scope(service={'origin': ORIGIN, 'publishable_key_sha256': 'A' * 64}),
               self.scope(service={'origin': ORIGIN, 'publishable_key_sha256': '0' * 64, 'key': KEY}),
               self.scope(dispatch_mode='native-subagent'), self.scope(operations=['create', 'send']),
               self.scope(operations=['connect', 'connect']), self.scope(operations=[]),
               self.scope(operations=['accept']), self.scope(mode='accept'), self.scope(invite_id=INVITE),
               self.accept_scope(invite_id='not-a-uuid'), self.scope(coordinator_role=7), [], 'scope']
        for scope in bad:
            self.denied('native_worker_binding_denied', self.prepare, scope)
        self.assertFalse((self.root / 'claude-workers').exists())
        for raw in (b'{"mode":"create","mode":"accept"}', b'[1]', b'{' + b' ' * 16384 + b'}', b'\xff',
                    json.dumps({**self.scope(), 'lifetime': 'until-owner-stop', 'ttl_seconds': 60}).encode()):
            code, result, text = self.cli(None, 'session', 'worker', 'prepare', '--json-stdin', stdin=raw)
            self.assertEqual((code, result['code']), (3, 'native_worker_binding_denied'), text)
        self.assertFalse((self.root / 'claude-workers').exists())
        service = self.prepare(self.scope(lifetime='until-owner-stop'))
        self.assertEqual((service['lifetime'], service['permission_expires_at'], service['pending_expires_at']),
                         ('until-owner-stop', None, T0 + 120))
        accept = self.prepare(self.accept_scope(coordinator_role='coordinator', dispatch_mode='native-subagent',
                                                operations=['accept', 'connect', 'receive', 'received', 'reply']))
        self.assertEqual(self.setting(accept['slot_id'])['scope']['operations'],
                         ['accept', 'connect', 'receive', 'received', 'reply'])
        self.assertNotIn('create_request_id', self.setting(accept['slot_id']))
        with claude(None):
            self.denied('native_worker_unavailable', worker.prepare, self.root, self.scope())

    def test_service_permission_survives_qa_ttl_until_terminal_stop(self):
        import quorum_claude_worker as worker
        slot, cap = self.active(self.scope(lifetime='until-owner-stop'))
        self.now = T0 + 10 * 3600
        context = self.authorize(cap, 'status')
        self.assertIsNone(context['binding']['permission_expires_at'])
        self.assertEqual(context['binding']['lifetime'], 'until-owner-stop')
        with claude(): worker.stop_slot(self.root, slot)
        self.denied('native_worker_slot_terminal', self.authorize, cap, 'status')
        self.denied('native_worker_slot_terminal', self.attest, slot)

    def test_activation_preserves_permission_deadline_and_retires_pending_deadline(self):
        slot = self.prepare()['slot_id']
        self.now = T0 + 100
        binding = self.attest(slot)
        self.assertEqual((binding['permission_expires_at'], binding['pending_expires_at']), (T0 + 900, T0 + 120))
        self.now = T0 + 200
        self.assertEqual(self.authorize(self.capability(slot), 'status')['binding']['state'], 'active')
        self.now = T0 + 899
        self.authorize(self.capability(slot), 'status')
        self.now = T0 + 900
        self.denied('native_worker_slot_terminal', self.authorize, self.capability(slot), 'status')


def actor(handle=HANDLE):
    return 'claude-worker:' + str(uuid.uuid5(uuid.UUID(SID), handle))


@POSIX_PROOF
class CliTests(Harness):
    @isolated_profiles
    def test_required_worker_preserves_attested_capability_scope(self):
        api = Api()
        slot, cap = self.active(self.accept_scope())
        self.configure()
        code, result, text = self.cli(api, 'accept-id', INVITE, '--transport', 'tool-inbox',
                                      '--require-worker-inbox', '--worker-capability', str(cap))
        self.assertEqual(code, 0, text)
        code, result, text = self.connect(api, cap, '--require-worker-inbox')
        self.assertEqual(code, 0, text)
        self.assertEqual(result['data']['transport'], 'tool-inbox')
        self.assertEqual(self.worker_ledger(slot).setting('session-agent')['session_ref'], actor())
        before = len(api.calls)
        code, result, text = self.connect(api, cap, '--require-worker-inbox', '--project-ref', 'other/project')
        self.assertEqual((code, result['code']), (3, 'native_worker_binding_denied'), text)
        self.assertEqual(len(api.calls), before)

    def test_cli_worker_management_and_capability_grammar(self):
        code, prepared, text = self.cli(None, 'session', 'worker', 'prepare', '--json-stdin',
                                        stdin=json.dumps(self.scope()).encode())
        self.assertEqual(code, 0, text)
        slot = prepared['data']['slot_id']
        code, attested, text = self.cli(None, 'session', 'worker', 'attest', slot, '--native-handle', HANDLE,
                                        '--spawn-tool-use-id', TOOL_USE)
        self.assertEqual((code, attested['data']['session_ref']), (0, actor()), text)
        code, awaited, text = self.cli(None, 'session', 'worker', 'await-binding', slot, '--wait-seconds', '1')
        self.assertEqual((code, awaited['data']['capability_file']), (0, str(self.capability(slot))), text)
        cap = str(self.capability(slot))
        for argv in (('session', 'worker', 'status', slot), ('session', '--worker-capability', cap, 'status'),
                     ('session', '--session-id', actor(), 'status')):
            code, status, text = self.cli(None, *argv)
            self.assertEqual((code, status['data']['state'], status['data']['slot_id']), (0, 'active', slot), text)
            self.assertNotIn(json.loads(self.capability(slot).read_text())['token'], text)
        # Typed selectors and bounds are parse errors, never a guessed actor or path.
        for argv in (('session', 'worker', 'await-binding', slot, '--wait-seconds', '0'),
                     ('session', 'worker', 'await-binding', slot, '--wait-seconds', '61'),
                     ('session', 'worker', 'attest', 'not-a-slot', '--native-handle', HANDLE),
                     ('session', 'worker', 'status', slot.upper()),
                     ('session', '--session-id', 'claude-worker:' + actor()[14:].upper(), 'status'),
                     ('session', '--session-id', 'claude-worker:../../etc', 'status'),
                     ('session', '--session-id', 'claude-worker:', 'status')):
            with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
                self.cli(None, *argv)
        # A capability form derives its actor; an explicit selector conflicts even when equal.
        code, result, _ = self.cli(None, 'session', '--session-id', actor(), '--worker-capability', cap, 'status')
        self.assertEqual((code, result['code']), (3, 'native_worker_binding_denied'))
        code, result, _ = self.cli(None, 'session', '--session-id', SID, '--worker-capability', cap, 'status')
        self.assertEqual((code, result['code']), (3, 'native_worker_binding_denied'))
        for action in (('start',), ('run', '--run-id', SID), ('reconcile', M1, '--confirmed-queued'),
                       ('send', '--to-role', 'peer', '--request-id', 'r', '--text-stdin'), ('peers',),
                       ('thread', M1), ('delegate', '--revoke')):
            code, result, text = self.cli(None, 'session', '--worker-capability', cap, *action, stdin=b'x')
            self.assertEqual((code, result['code']), (3, 'native_inbox_transport_conflict'), text)
        code, stopped, text = self.cli(None, 'session', '--session-id', actor(), 'stop')
        self.assertEqual((code, stopped['data']['state'], stopped['data']['terminal_reason']), (0, 'terminal', 'stopped'))
        code, result, _ = self.cli(None, 'session', '--worker-capability', cap, 'status')
        self.assertEqual((code, result['code']), (3, 'native_worker_slot_terminal'))

    def test_logical_selector_is_typed_and_management_only(self):
        slot, cap = self.active()
        for action in (('receive', '--wait-seconds', '0'), ('received', M1), ('reply', M1, '--text-stdin'),
                       ('connect', 'team', '--role', 'service-worker', '--project-ref', 'team/project'), ('start',)):
            code, result, text = self.cli(Api(), 'session', '--session-id', actor(), *action, stdin=b'x')
            self.assertEqual((code, result['code']), (3, 'native_worker_binding_denied'), text)
        # Another actual SID, or an actor nobody attested, resolves nothing.
        for sid, selector in ((OTHER_SID, actor()), (SID, actor('unattested'))):
            code, result, _ = self.cli(None, 'session', '--session-id', selector, 'status', sid=sid)
            self.assertEqual((code, result['code']), (3, 'native_worker_binding_denied'))
        code, result, _ = self.cli(None, 'session', '--session-id', actor(), 'stop', sid=OTHER_SID)
        self.assertEqual(self.setting(slot)['state'], 'active')
        self.assertFalse((self.root / 'sessions').exists())

    @isolated_profiles
    def test_actor_selector_service_and_target_cannot_cross_bind(self):
        api = Api()
        slot, cap = self.active(self.accept_scope())
        # A conflicting public service flag or state config denies before profile resolution.
        with patch('quorum_profiles.resolve_profile', side_effect=forbidden('profile')):
            code, result, _ = self.cli(api, 'accept-id', INVITE, '--transport', 'tool-inbox', '--worker-capability',
                                       str(cap), '--origin', 'https://other.example', '--publishable-key', KEY)
            self.assertEqual((code, result['code']), (3, 'native_worker_binding_denied'))
            self.configure(key='sb_publishable_other')
            code, result, _ = self.cli(api, 'accept-id', INVITE, '--transport', 'tool-inbox',
                                       '--worker-capability', str(cap))
            self.assertEqual((code, result['code']), (3, 'native_worker_binding_denied'))
            (self.root / 'config.json').unlink()
            code, result, _ = self.cli(api, 'accept-id', INVITE, '--transport', 'tool-inbox',
                                       '--worker-capability', str(cap))
            self.assertEqual((code, result['code']), (3, 'native_worker_unavailable'))
        self.assertEqual(api.calls, [])
        self.configure()
        self.assertEqual(self.cli(api, 'accept-id', INVITE, '--transport', 'tool-inbox',
                                  '--worker-capability', str(cap))[0], 0)
        self.assertEqual(self.setting(slot)['target'], {'quorum_id': QUORUM, 'membership_id': 'membership-1'})
        # A slug lookup resolving to another quorum is refused before any device or agent effect.
        api.lookup = OTHER_QUORUM
        code, result, _ = self.connect(api, cap)
        self.assertEqual((code, result['code']), (3, 'native_worker_binding_denied'))
        self.assertNotIn('device_register', api.ops())
        self.assertNotIn('agent_join', api.ops())
        # Scope arguments cannot be redirected to another quorum, role or coordinator.
        api.lookup = QUORUM
        for extra in (('--accept-tasks-from', 'coordinator'), ('--dispatch-mode', 'native-subagent')):
            code, result, _ = self.connect(api, cap, *extra)
            self.assertEqual((code, result['code']), (3, 'native_worker_binding_denied'))
        code, result, _ = self.cli(api, 'session', '--worker-capability', str(cap), 'connect', 'other', '--role',
                                   'service-worker', '--project-ref', 'team/project', '--transport', 'tool-inbox')
        self.assertEqual((code, result['code']), (3, 'native_worker_binding_denied'))
        code, result, _ = self.cli(api, 'session', '--worker-capability', str(cap), 'connect', 'team', '--role',
                                   'service-worker', '--project-ref', 'team/project')
        self.assertEqual((code, result['code']), (3, 'native_worker_binding_denied'))
        self.assertEqual(self.connect(api, cap)[0], 0)
        self.assertEqual(self.setting(slot)['target'], {'quorum_id': QUORUM, 'membership_id': 'membership-1',
                                                         'agent_id': AGENT})

    @isolated_profiles
    def test_worker_preflight_precedes_profile_auth_and_invitation_use(self):
        api = Api()
        pending = self.prepare(self.accept_scope())['slot_id']
        slot, cap = self.active(self.accept_scope(), handle='second')
        self.configure()
        cases = [(self.capability(pending), ()), (self.base / 'copy.json', ()), (cap, ('--membership-only',)),
                 (cap, ('--anonymous',)), (cap, ('--client', 'grok-bot')), (cap, ('--parent-thread-id', OTHER_SID))]
        (self.base / 'copy.json').write_bytes(cap.read_bytes())
        with patch('quorum_profiles.resolve_profile', side_effect=forbidden('profile')), \
                patch('quorum.LocalLedger', side_effect=forbidden('root ledger')), \
                patch('quorum_session.client_preflight', side_effect=forbidden('client preflight')):
            for path, extra in cases:
                code, result, text = self.cli(api, 'accept-id', INVITE, '--transport', 'tool-inbox',
                                              '--worker-capability', str(path), *extra)
                self.assertEqual(code, 3, text)
                self.assertIn(result['code'], ('native_worker_binding_denied', 'native_inbox_transport_conflict'))
            code, result, _ = self.cli(api, 'accept-id', OTHER_SID, '--transport', 'tool-inbox',
                                       '--worker-capability', str(cap))
            self.assertEqual((code, result['code']), (3, 'native_worker_binding_denied'))
            code, result, _ = self.cli(api, 'session', '--worker-capability', str(cap), 'receive')
            self.assertEqual((code, result['code']), (3, 'native_worker_binding_denied'), 'connect not pinned yet')
        self.assertEqual(api.calls, [], 'the invitation stays unused')
        # A capability under a state root that does not exist creates nothing.
        missing = self.base / 'missing-state'
        code, result, _ = self.cli(api, '--state', str(missing), 'accept-id', INVITE, '--transport', 'tool-inbox',
                                   '--worker-capability', str(missing / 'claude-workers' / slot / 'capability.json'))
        self.assertEqual(code, 3)
        self.assertFalse(missing.exists())

    @isolated_profiles
    def test_claude_tool_connect_uses_logical_ledger_without_pipe_or_env_mutation(self):
        api = Api()
        before = dict(os.environ)
        grant = ('--accept-tasks-from', 'coordinator', '--dispatch-mode', 'native-subagent')
        slot, cap, connected = self.joined(api, self.accept_scope(coordinator_role='coordinator',
                                                                  dispatch_mode='native-subagent'), extra=grant)
        self.assertEqual(dict(os.environ), before)
        self.assertEqual((connected['data']['session_ref'], connected['data']['transport'],
                          connected['data']['reception'], connected['data']['listener']),
                         (actor(), 'tool-inbox', 'enabled', 'offline'))
        record = self.worker_ledger(slot).setting('session-agent')
        self.assertEqual(record['registration'], {'engine': 'claude', 'session_ref': actor(),
                                                  'project_ref': 'team/project', 'cwd': str(self.project)})
        self.assertEqual((record['engine'], record['transport'], record['quorum_id'], record['agent_id']),
                         ('claude', 'tool-inbox', QUORUM, AGENT))
        self.assertEqual(record['worker_binding']['provider'], 'claude')
        self.assertEqual(record['worker_binding']['capability_file'], str(cap))
        self.assertNotIn('messaging_socket', record)
        self.assertEqual(record['delegation']['coordinator_id'], PEER)
        join = [payload for operation, payload, _ in api.calls if operation == 'agent_join'][0]
        self.assertEqual((join['session_ref'], join['engine']), (actor(), 'claude'))
        # The parent's own ledger and device are untouched; the actor has its own device key.
        self.assertFalse((self.root / 'sessions' / SID).exists())
        from quorum_state import LocalLedger
        root = LocalLedger(self.root)
        try:
            self.assertIsNone(root.setting('device'))
            self.assertEqual(root.setting('session-device:' + actor()), {'device_id': 'device-1'})
        finally: root.db.close()
        # Reconnect is stable and copies no other binding's grant.
        code, again, _ = self.connect(api, cap, '--accept-tasks-from', 'coordinator', '--dispatch-mode',
                                      'native-subagent')
        self.assertEqual((code, again['data']['agent_id']), (0, AGENT))

    def test_worker_capability_rejected_on_unsupported_legacy_commands(self):
        slot, cap = self.active()
        for argv in (('invite', 'team', '--worker-capability', str(cap)), ('join', 'team', '--worker-capability', str(cap)),
                     ('accept', '--worker-capability', str(cap)), ('send', '--worker-capability', str(cap)),
                     ('connect', '--invite-file', 'x', '--role', 'r', '--session-ref', 's', '--worker-capability', str(cap)),
                     ('cloud', '--worker-capability', str(cap), 'status'), ('listen', '--worker-capability', str(cap))):
            with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
                self.cli(None, *argv)
        code, result, _ = self.cli(None, 'create', 'team', '--access', 'github', '--json-stdin',
                                   '--request-id', 'override', '--worker-capability', str(cap), stdin=b'{}')
        self.assertEqual((code, result['code']), (3, 'native_worker_binding_denied'))

    @isolated_profiles
    def test_codex_native_claude_grok_and_cloud_routes_unchanged(self):
        from quorum_session import session_selector
        upper = SID.upper()
        self.assertEqual(session_selector(upper), SID)
        self.assertEqual(session_selector('{' + SID + '}'), SID)
        # An ordinary Claude parent without a capability keeps its existing session route.
        with patch('quorum_claude_worker.manage', side_effect=forbidden('worker route')):
            code, result, text = self.cli(Api(), 'session', 'status')
        self.assertEqual(code, 0, text)
        self.assertEqual(result['data']['session_id'], SID)
        self.assertNotIn('claude-workers', os.listdir(self.root))
        import inspect, quorum
        self.assertIsNone(inspect.signature(quorum.register_agent).parameters['expected_quorum_id'].default)
        from quorum_native_inbox import NativeInboxError, prove_worker, require_worker
        self.assertEqual(list(inspect.signature(prove_worker).parameters), ['parent_thread_id'])
        self.assertEqual(list(inspect.signature(require_worker).parameters), ['record'])
        self.assertLessEqual({'native_worker_unavailable', 'native_worker_binding_denied',
                              'native_inbox_transport_conflict', 'native_inbox_receipt_not_offered',
                              'native_inbox_reply_loop_denied'}, NativeInboxError.CODES)


CREATE_ATTEMPTS = r"""
import contextlib, json, sys, time
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, sys.argv[1])
import test_quorum_claude_worker as fixture
import quorum_claude_worker as worker

directory, name, dispatched, reason = Path(sys.argv[2]), sys.argv[3], sys.argv[4] == 'dispatched', sys.argv[5]
data = json.loads((directory / 'context.json').read_text())
context = data['context']
import os
os.chdir(data['project'])

def wait(path):
    deadline = time.monotonic() + 5
    while not path.exists():
        if time.monotonic() >= deadline: raise RuntimeError('local ordering timeout: ' + path.name)
        time.sleep(.01)

def terminal_time():
    return fixture.T0 + 900 if reason == 'permission_expired' and (directory / 'release').exists() else fixture.T0

api = fixture.Api()
original_set, original_restore, original_lock = worker._set_create, worker._restore_unsent_create, worker._lock

@contextlib.contextmanager
def lock(root, names, lock_name, **kwargs):
    # Observe an attempt waiting for serialization; do not replace the real lock.
    if name == 'B' and lock_name == 'create.lock': (directory / 'B-waiting').touch()
    with original_lock(root, names, lock_name, **kwargs): yield

def persisted(local, value):
    result = original_set(local, value)
    (directory / (name + '-persisted')).touch()
    if name == 'B' or not dispatched: wait(directory / 'release')
    return result

def restored(local, previous, attempt):
    # Force the rejected ordering: A declines cleanup, then B restores its prior A intent.
    if name == 'B': wait(directory / 'A-finished')
    return original_restore(local, previous, attempt)

if name == 'A' and dispatched:
    def blocked():
        (directory / 'A-dispatched').touch()
        wait(directory / 'release')
    api.hooks['quorum_create'] = blocked
    api.lost.append('quorum_create')

with fixture.claude(), patch.object(worker, 'now', side_effect=terminal_time), \
        patch.object(worker, '_lock', side_effect=lock), \
        patch.object(worker, '_set_create', side_effect=persisted), \
        patch.object(worker, '_restore_unsent_create', side_effect=restored):
    try:
        worker._send(context, api, 'quorum_create', {'slug': 'team'}, data['request'], 'create')
    except Exception as error:
        result = {'code': getattr(error, 'code', None), 'reason': getattr(error, 'reason', None),
                  'remote_effect': getattr(error, 'remote_effect', None), 'type': type(error).__name__}
    else: result = {'unexpected': 'completed'}
result['api_calls'] = api.calls
(directory / (name + '-result.json')).write_text(json.dumps(result))
(directory / (name + '-finished')).touch()
"""


@POSIX_PROOF
class CreateTests(Harness):
    def create(self, api, cap, *extra, body=b'{"slug": "team"}', access='github', resolve=None):
        """The existing create branch with a fake profile seam and fake API; no credential is read."""
        from quorum_api import ServiceConfig
        self.resolved = getattr(self, 'resolved', [])
        def resolved(root, *_, **__):
            self.resolved.append(root)
            return ServiceConfig(ORIGIN, KEY), Store()
        with patch('quorum_profiles.resolve_profile', side_effect=resolve or resolved), \
                patch('quorum.QuorumApi', return_value=api), patch('quorum.token_provider', return_value=lambda: 'jwt'):
            return self.cli(api, 'create', 'team', '--access', access, '--json-stdin', '--worker-capability', str(cap),
                            *extra, stdin=body)

    def created(self, api, scope=None, access='github'):
        slot, cap = self.active(scope or self.scope(access=access))
        self.configure()
        code, result, text = self.create(api, cap, access=access)
        self.assertEqual(code, 0, text)
        return slot, cap, result['data']

    @isolated_profiles
    def test_worker_create_denies_pending_expired_revoked_and_wrong_scope_before_auth(self):
        import quorum_claude_worker as worker
        api = Api()
        pending = self.prepare()['slot_id']
        stopped, stopped_cap = self.active(handle='stopped')
        with claude(): worker.stop_slot(self.root, stopped)
        slot, cap = self.active()
        self.configure()
        cases = [(self.capability(pending), 'github', b'{"slug": "team"}'), (stopped_cap, 'github', b'{}'),
                 (cap, 'open', b'{}'), (cap, 'github', b'{"slug": "other"}'), (cap, 'github', b'{"role": "admin"}')]
        with patch('quorum.LocalLedger', side_effect=forbidden('root ledger')):
            for path, access, body in cases:
                code, result, text = self.create(api, path, access=access, body=body,
                                                 resolve=forbidden('profile'))
                self.assertEqual(code, 3, text)
                self.assertIn(result['code'], ('native_worker_binding_denied', 'native_worker_slot_terminal'))
            self.now = T0 + 900
            code, result, _ = self.create(api, cap, resolve=forbidden('profile'))
            self.assertEqual((code, result['code']), (3, 'native_worker_slot_terminal'))
        self.assertEqual(api.calls, [])

    @isolated_profiles
    def test_worker_create_exact_slug_access_service_and_role_only(self):
        api = Api()
        slot, cap = self.active()
        self.configure(key='sb_publishable_other')
        code, result, _ = self.create(api, cap, resolve=forbidden('profile'))
        self.assertEqual((code, result['code']), (3, 'native_worker_binding_denied'), 'service mismatch before profile')
        self.configure()
        code, result, text = self.create(api, cap, '--role', 'service-worker',
                                         body=b'{"slug": "team", "role": "service-worker"}')
        self.assertEqual(code, 0, text)
        record = self.setting(slot)
        self.assertEqual(api.calls, [('quorum_create', {'slug': 'team'},
                                      record['create_request_id'])])
        self.assertEqual(result['data'], {'quorum_id': QUORUM, 'slug': 'team', 'access': 'github'})
        self.assertNotIn(json.loads(cap.read_text())['token'], text)
        self.assertEqual(self.resolved, [self.root])
        self.assertEqual((record['create'], record['target']), ({'state': 'done', 'quorum_id': QUORUM},
                                                                 {'quorum_id': QUORUM}))

    @isolated_profiles
    def test_worker_create_then_connect_pins_target_once(self):
        import quorum_claude_worker as worker
        api = Api()
        slot, cap, _ = self.created(api)
        code, again, _ = self.create(api, cap)
        self.assertEqual((code, again['data']['quorum_id']), (0, QUORUM))
        self.assertEqual([call[0] for call in api.calls], ['quorum_create'], 'a completed create is not resent')
        code, connected, text = self.connect(api, cap)
        self.assertEqual((code, connected['data']['agent_id']), (0, AGENT), text)
        self.assertEqual(self.setting(slot)['target'], {'quorum_id': QUORUM, 'agent_id': AGENT})
        with claude(): context = worker.authorize(self.root, cap, 'status', {})
        self.denied('native_worker_binding_denied', worker.pin_target, context, OTHER_QUORUM)
        self.denied('native_worker_binding_denied', worker.pin_target, context, QUORUM, PEER)
        self.assertEqual(self.setting(slot)['target'], {'quorum_id': QUORUM, 'agent_id': AGENT})

    @isolated_profiles
    def test_create_lost_response_reuses_private_request_id(self):
        api = Api()
        slot, cap = self.active()
        self.configure()
        api.lost.append('quorum_create')
        code, result, _ = self.create(api, cap)
        self.assertEqual((code, result['code']), (5, 'service_unavailable'))
        self.assertEqual(self.setting(slot)['create'], {'state': 'intent', 'step': 'create'})
        code, connect, _ = self.connect(api, cap)
        self.assertEqual((code, connect['code']), (3, 'native_worker_binding_denied'), 'no connect before a known create')
        self.assertEqual(self.create(api, cap)[0], 0)
        requests = [request for operation, _, request in api.calls if operation == 'quorum_create']
        self.assertEqual(requests, [self.setting(slot)['create_request_id']] * 2)

    @isolated_profiles
    def test_fresh_create_terminal_before_dispatch_has_no_ambiguity(self):
        import quorum_claude_worker as worker
        from quorum_api import ServiceConfig
        for boundary in ('profile', 'intent'):
            for reason in ('stopped', 'permission_expired'):
                with self.subTest(boundary=boundary, reason=reason):
                    self.now = T0
                    api = Api()
                    slot, cap = self.active(handle=boundary + reason)
                    self.configure()
                    def terminate():
                        if reason == 'stopped':
                            with claude(): worker.stop_slot(self.root, slot)
                        else: self.now = T0 + 900
                    def resolve(*_, **__):
                        if boundary == 'profile': terminate()
                        return ServiceConfig(ORIGIN, KEY), Store()
                    real = worker._set_create
                    def persisted(context, value):
                        result = real(context, value)
                        if boundary == 'intent' and value and value.get('state') == 'intent': terminate()
                        return result
                    with patch('quorum_claude_worker._set_create', side_effect=persisted):
                        code, result, text = self.create(api, cap, resolve=resolve)
                    self.assertEqual((code, result['code'], result['data']['reason']),
                                     (3, 'native_worker_slot_terminal', reason), text)
                    self.assertNotIn('remote_effect', result['data'])
                    self.assertEqual(api.calls, [])
                    with claude(): status = worker.slot_status(self.root, slot)
                    self.assertEqual((status['state'], status['create'], status['target']),
                                     ('terminal', None, {}), 'no request was dispatched')
                    self.assertIsNone(self.setting(slot)['create'])

    @isolated_profiles
    def test_lost_create_intent_survives_terminal_retry_before_dispatch(self):
        import quorum_claude_worker as worker
        from quorum_api import ServiceConfig
        for boundary in ('profile', 'intent'):
            for reason in ('stopped', 'permission_expired'):
                with self.subTest(boundary=boundary, reason=reason):
                    self.now = T0
                    api = Api()
                    slot, cap = self.active(handle=boundary + reason)
                    self.configure()
                    api.lost.append('quorum_create')
                    code, result, _ = self.create(api, cap)
                    self.assertEqual((code, result['code']), (5, 'service_unavailable'))
                    original = self.setting(slot)
                    def terminate():
                        if reason == 'stopped':
                            with claude(): worker.stop_slot(self.root, slot)
                        else: self.now = T0 + 900
                    def resolve(*_, **__):
                        if boundary == 'profile': terminate()
                        return ServiceConfig(ORIGIN, KEY), Store()
                    real = worker._set_create
                    def persisted(context, value):
                        result = real(context, value)
                        if boundary == 'intent' and value and value.get('state') == 'intent': terminate()
                        return result
                    with patch('quorum_claude_worker._set_create', side_effect=persisted):
                        code, result, text = self.create(api, cap, resolve=resolve)
                    self.assertEqual((code, result['code'], result['data']['reason']),
                                     (3, 'native_worker_slot_terminal', reason), text)
                    self.assertEqual(api.calls, [('quorum_create', {'slug': 'team'}, original['create_request_id'])])
                    with claude(): status = worker.slot_status(self.root, slot)
                    self.assertEqual((status['create'], status['target']), ('ambiguous', {}))
                    record = self.setting(slot)
                    self.assertEqual(record['create'], {'state': 'intent', 'step': 'create'})
                    self.assertEqual(record['create_request_id'], original['create_request_id'])

    @isolated_profiles
    def test_unsent_create_cleanup_preserves_newer_completed_evidence(self):
        import quorum_claude_worker as worker
        api = Api()
        slot, cap = self.active()
        self.configure()
        real = worker._set_create
        def completed_elsewhere(context, value):
            result = real(context, value)
            if value and value.get('state') == 'intent':
                worker.pin_target(context, QUORUM)
                real(context, {'state': 'done', 'quorum_id': QUORUM})
                with claude(): worker.stop_slot(self.root, slot)
            return result
        with patch('quorum_claude_worker._set_create', side_effect=completed_elsewhere):
            code, result, text = self.create(api, cap)
        self.assertEqual((code, result['code']), (3, 'native_worker_slot_terminal'), text)
        self.assertEqual(api.calls, [])
        with claude(): status = worker.slot_status(self.root, slot)
        self.assertEqual((status['create'], status['target']), ('done', {'quorum_id': QUORUM}))

    @isolated_profiles
    def test_unsent_create_cleanup_preserves_concurrent_dispatched_intent(self):
        import quorum_claude_worker as worker
        for reason in ('stopped', 'permission_expired'):
            with self.subTest(reason=reason):
                slot, results = self.concurrent_creates(dispatched=True, reason=reason)
                self.assertEqual((results['A']['code'], results['A']['reason']),
                                 ('native_worker_create_ambiguous', 'create_unresolved'))
                self.assertEqual(results['A']['remote_effect'],
                                 {'state': 'uncertain', 'operation': 'quorum_create'})
                self.assertEqual(results['A']['api_calls'],
                                 [['quorum_create', {'slug': 'team'}, self.setting(slot)['create_request_id']]])
                self.assertEqual((results['B']['code'], results['B']['reason'], results['B']['api_calls']),
                                 ('native_worker_slot_terminal', reason, []))
                with claude(): status = worker.slot_status(self.root, slot)
                self.assertEqual((status['create'], status['target']), ('ambiguous', {}))
                self.assertEqual(self.setting(slot)['create'], {'state': 'intent', 'step': 'create'})

    @isolated_profiles
    def test_two_unsent_creates_cannot_restore_cancelled_intent(self):
        import quorum_claude_worker as worker
        for reason in ('stopped', 'permission_expired'):
            with self.subTest(reason=reason):
                slot, results = self.concurrent_creates(reason=reason)
                for result in results.values():
                    self.assertEqual((result['code'], result['reason'], result['remote_effect'], result['api_calls']),
                                     ('native_worker_slot_terminal', reason, None, []))
                with claude(): status = worker.slot_status(self.root, slot)
                self.assertEqual((status['state'], status['create'], status['target']), ('terminal', None, {}))
                self.assertIsNone(self.setting(slot)['create'])

    def concurrent_creates(self, *, dispatched=False, reason='stopped'):
        """Two real local processes share only private slot state and explicit ordering files."""
        import subprocess
        import time
        import quorum_claude_worker as worker
        self.now = T0
        slot, cap = self.active(handle=str(uuid.uuid4()))
        context = self.authorize(cap, 'create', {'quorum': 'team', 'access': 'github', 'role': None})
        directory = self.base / ('ordering-' + slot)
        directory.mkdir(mode=0o700)
        (directory / 'context.json').write_text(json.dumps({'context': context, 'project': str(self.project),
                                                          'request': self.setting(slot)['create_request_id']}))
        (directory / 'context.json').chmod(0o600)
        processes = []
        def start(name):
            command = [sys.executable, '-B', '-c', CREATE_ATTEMPTS, str(Path(__file__).parent), str(directory),
                       name, 'dispatched' if dispatched else 'unsent', reason]
            processes.append(subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True))
        def wait(*names):
            deadline = time.monotonic() + 5
            while not any((directory / name).exists() for name in names):
                if time.monotonic() >= deadline: self.fail('local ordering timeout: ' + repr(names))
                time.sleep(.01)
        try:
            start('A')
            wait('A-dispatched' if dispatched else 'A-persisted')
            start('B')
            # On the old code B writes another intent; with serialization it waits without writing.
            wait('B-persisted', 'B-waiting')
            if reason == 'stopped':
                with claude(): worker.stop_slot(self.root, slot)
            else: self.now = T0 + 900
            (directory / 'release').touch()
            for process in processes:
                stdout, stderr = process.communicate(timeout=10)
                self.assertEqual((process.returncode, stdout, stderr), (0, '', ''))
            return slot, {name: json.loads((directory / (name + '-result.json')).read_text()) for name in ('A', 'B')}
        finally:
            for process in processes:
                if process.poll() is None: process.kill()
                process.communicate()

    @isolated_profiles
    def test_open_access_retry_preserves_target_and_blocks_early_registration(self):
        api = Api()
        slot, cap = self.active(self.scope(access='open'))
        self.configure()
        api.fail['quorum_set_access'] = ['service_unavailable']
        code, result, _ = self.create(api, cap, access='open')
        self.assertEqual((code, result['code']), (5, 'service_unavailable'))
        self.assertEqual(self.setting(slot)['target'], {'quorum_id': QUORUM})
        code, result, _ = self.connect(api, cap)
        self.assertEqual((code, result['code']), (3, 'native_worker_binding_denied'))
        self.assertNotIn('agent_join', api.ops())
        code, result, text = self.create(api, cap, access='open')
        self.assertEqual((code, result['data']['access']), (0, 'open'), text)
        self.assertEqual([call for call in api.calls if call[0] == 'quorum_set_access'],
                         [('quorum_set_access', {'quorum_id': QUORUM, 'access': 'open'}, 'access:' + QUORUM)] * 2)
        self.assertEqual(api.ops().count('quorum_create'), 1)
        self.assertEqual(self.connect(api, cap)[0], 0)

    @isolated_profiles
    def test_create_ambiguous_stop_retains_intent_and_blocks_followups(self):
        import quorum_claude_worker as worker
        api = Api()
        slot, cap = self.active()
        self.configure()
        def stop():
            with claude(): worker.stop_slot(self.root, slot)
        api.hooks['quorum_create'] = stop
        api.lost.append('quorum_create')
        code, result, text = self.create(api, cap)
        self.assertEqual((code, result['code']), (3, 'native_worker_create_ambiguous'), text)
        with claude(): status = worker.slot_status(self.root, slot)
        self.assertEqual((status['state'], status['create'], status['target']), ('terminal', 'ambiguous', {}))
        self.assertEqual(self.setting(slot)['create'], {'state': 'intent', 'step': 'create'})
        for code, result, _ in (self.create(api, cap), self.connect(api, cap)):
            self.assertEqual((code, result['code']), (3, 'native_worker_slot_terminal'))
        self.assertEqual(api.ops(), ['quorum_create'])
        # A create that completed remotely after the stop is recorded truthfully, never rolled back or redone.
        other, other_cap = self.active(handle='second')
        def stop_other():
            with claude(): worker.stop_slot(self.root, other)
        api.hooks['quorum_create'] = stop_other
        code, result, _ = self.create(api, other_cap)
        self.assertEqual((code, result['code']), (3, 'native_worker_slot_terminal'))
        with claude(): status = worker.slot_status(self.root, other)
        self.assertEqual((status['create'], status['target']), ('completed_after_stop', {'quorum_id': QUORUM}))
        self.assertEqual(api.ops(), ['quorum_create', 'quorum_create'])

    @isolated_profiles
    def test_worker_create_rejects_request_override_and_unknown_json_fields(self):
        api = Api()
        slot, cap = self.active()
        self.configure()
        for extra, body in ((('--request-id', 'override'), b'{}'), ((), b'{"slug": "team", "visibility": "x"}'),
                            ((), b'{"slug": "team", "slug": "team"}'), ((), b'[]'), ((), b'{"request_id": "x"}'),
                            (('--role', 'admin'), b'{}')):
            code, result, text = self.create(api, cap, *extra, body=body, resolve=forbidden('profile'))
            self.assertEqual((code, result['code']), (3, 'native_worker_binding_denied'), text)
        self.assertEqual(api.calls, [])
        self.assertIsNone(self.setting(slot)['create'])

    @isolated_profiles
    def test_parent_create_behavior_is_unchanged(self):
        from quorum_api import ServiceConfig
        api = Api()
        # Legacy parent create is outside the worker adapter correction and is pinned exactly as it was.
        api.create_arguments = None
        with patch('quorum_profiles.resolve_profile', return_value=(ServiceConfig(ORIGIN, KEY), Store())), \
                patch('quorum.QuorumApi', return_value=api), patch('quorum.token_provider', return_value=lambda: 'jwt'), \
                patch('quorum_claude_worker.preflight', side_effect=forbidden('worker preflight')):
            code, result, text = self.cli(api, 'create', 'team', '--access', 'open', '--role', 'lead', '--json-stdin',
                                          stdin=b'{"description": "kept"}')
        self.assertEqual(code, 0, text)
        (create, payload, request), access = api.calls
        self.assertEqual((create, payload), ('quorum_create', {'description': 'kept', 'slug': 'team', 'role': 'lead'}))
        uuid.UUID(request)
        self.assertEqual(access, ('quorum_set_access', {'quorum_id': QUORUM, 'access': 'open'}, 'access:' + QUORUM))


class OtherConnection:
    """A second SQLite connection to the same worker ledger, like a concurrent CLI process."""
    def __init__(self, root): self.root = Path(root)
    def __enter__(self):
        from quorum_state import LocalLedger
        self.ledger = LocalLedger(self.root)
        return self.ledger
    def __exit__(self, *_): self.ledger.db.close()


class Bound(Harness):
    """Shared helpers for a bound logical actor; defines no tests of its own."""
    def bound(self, api, scope=None, extra=(), connect=True, handle=HANDLE):
        self.slot, self.cap, _ = self.joined(api, scope, connect=connect, extra=extra, handle=handle)
        self.token = json.loads(self.cap.read_text())['token']
        return self.slot, self.cap

    def worker(self, api, *argv, stdin=b''):
        return self.cli(api, 'session', '--worker-capability', str(self.cap), *argv, stdin=stdin)

    def receive(self, api, seconds='0'):
        return self.worker(api, 'receive', '--wait-seconds', seconds)

    def offer(self, api):
        code, result, text = self.receive(api)
        self.assertEqual(code, 0, text)
        return result['data']

    def direct(self, operation, arguments=None):
        """The shared inbox functions with this actor's record and its capability guard."""
        import quorum_claude_worker as worker
        with claude(): context = worker.authorize(self.root, self.cap, operation, arguments or {})
        def check():
            with claude(): return worker.guard(context)
        ledger = self.worker_ledger(self.slot)
        return ledger, ledger.setting('session-agent'), check

    def stopper(self):
        import quorum_claude_worker as worker
        def stop():
            with claude(): worker.stop_slot(self.root, self.slot)
        return stop


@POSIX_PROOF
class DeliveryTests(Bound):
    @isolated_profiles
    def test_claude_offer_precedes_stdout_and_explicit_receipt_only(self):
        import quorum
        from quorum_api import ServiceConfig
        api = Api(inbox=[message(M1, body='$(rm -rf ~) please'), message(M2)])
        self.bound(api)
        envelope = self.offer(api)
        self.assertEqual((envelope['type'], envelope['state'], envelope['message_id'], envelope['session_ref']),
                         ('quorum.peer_message', 'offered', M1, actor()))
        self.assertEqual(envelope['receipt_command'][-5:], ['session', '--worker-capability', str(self.cap),
                                                            'received', M1])
        self.assertEqual(envelope['reply_command'][-6:], ['session', '--worker-capability', str(self.cap),
                                                          'reply', M1, '--text-stdin'])
        self.assertNotIn('--session-id', envelope['receipt_command'] + envelope['reply_command'])
        self.assertIn('references/claude-worker.md', envelope['notice'])
        self.assertNotIn(self.token, json.dumps(envelope))
        self.assertNotIn('rm -rf', json.dumps([envelope['receipt_command'], envelope['reply_command'],
                                               envelope['notice']]))
        self.assertNotIn('session_ack', api.ops())
        self.assertEqual(self.worker_ledger(self.slot).setting('delivery:' + M1)['state'], 'offered')
        class Broken(io.TextIOBase):
            def writable(self): return True
            def write(self, _): raise BrokenPipeError()
        config = ServiceConfig(ORIGIN, KEY)
        with claude(), patch('quorum_live.api_for', return_value=(api, config, Store())), \
                patch('sys.stdout', Broken()), contextlib.redirect_stderr(io.StringIO()) as errors:
            try: outcome = quorum.main(['--state', str(self.root), 'session', '--worker-capability', str(self.cap),
                                        'receive', '--wait-seconds', '0'])
            except OSError as error: outcome = error
        self.assertNotEqual(outcome, 0)
        self.assertNotIn('rm -rf', errors.getvalue())
        self.assertEqual(api.acked, [])
        code, receipt, _ = self.worker(api, 'received', M1)
        self.assertEqual((code, receipt['data']), (0, {'received': True, 'state': 'acked', 'message_id': M1}))
        self.assertEqual(api.acked, [M1])
        self.assertEqual(self.offer(api)['message_id'], M2)

    @isolated_profiles
    def test_claude_lost_output_ack_reply_reuses_owned_offer(self):
        api = Api(inbox=[message(M1, body='first'), message(M2)])
        self.bound(api)
        self.assertEqual(self.offer(api)['message_id'], M1)
        api.inbox.insert(0, message(M3, priority='urgent'))
        self.assertEqual(self.offer(api)['message_id'], M1, 'a lost tool result repeats the same offer')
        api.lost.append('session_ack')
        code, result, _ = self.worker(api, 'received', M1)
        self.assertEqual((code, result['code']), (5, 'service_unavailable'))
        self.assertEqual(self.offer(api)['message_id'], M1)
        self.assertEqual(self.worker(api, 'received', M1)[0], 0)
        self.assertEqual(self.offer(api)['message_id'], M3)
        api.lost.append('session_reply')
        code, result, _ = self.worker(api, 'reply', M3, '--text-stdin', stdin=b'answer')
        self.assertEqual((code, result['code']), (5, 'service_unavailable'))
        code, first, _ = self.worker(api, 'reply', M3, '--text-stdin', stdin=b'answer')
        code2, second, _ = self.worker(api, 'reply', M3, '--text-stdin', stdin=b'answer')
        self.assertEqual((code, code2), (0, 0))
        self.assertEqual(first['data']['reply_message_id'], second['data']['reply_message_id'])
        code, changed, _ = self.worker(api, 'reply', M3, '--text-stdin', stdin=b'changed')
        self.assertEqual((code, changed['code']), (3, 'native_inbox_reply_loop_denied'))
        self.assertEqual(api.acked, [M1, M3])

    @isolated_profiles
    def test_claude_stop_or_expiry_during_blocked_api_suppresses_disclosure(self):
        api = Api(inbox=[message(M1, body='secret peer body')])
        self.bound(api)
        api.hooks['session_inbox'] = self.stopper()
        code, result, text = self.receive(api)
        self.assertEqual((code, result['code']), (3, 'native_worker_slot_terminal'), text)
        self.assertNotIn('secret peer body', text)
        self.assertIsNone(self.worker_ledger(self.slot).setting('delivery:' + M1))
        # A bounded permission that expires while a call is blocked discloses nothing either.
        api2 = Api(inbox=[message(M2, body='second secret')])
        other, other_cap = self.active(self.accept_scope(), handle='expiring')
        self.cap, self.slot = other_cap, other
        self.assertEqual(self.cli(api2, 'accept-id', INVITE, '--transport', 'tool-inbox',
                                  '--worker-capability', str(other_cap))[0], 0)
        self.assertEqual(self.connect(api2, other_cap)[0], 0)
        def expire(): self.now = T0 + 900
        api2.hooks['session_inbox'] = expire
        code, result, text = self.receive(api2)
        self.assertEqual((code, result['code']), (3, 'native_worker_slot_terminal'), text)
        self.assertNotIn('second secret', text)
        self.assertNotIn('session_ack', api2.ops())

    @isolated_profiles
    def test_service_stop_during_blocked_api_suppresses_disclosure(self):
        api = Api(inbox=[message(M1, body='service secret')])
        self.bound(api, self.accept_scope(lifetime='until-owner-stop'))
        self.now = T0 + 7 * 24 * 3600
        self.assertEqual(self.offer(api)['message_id'], M1, 'service permission has no elapsed-time expiry')
        def stop():
            code, result, _ = self.worker(api, 'stop')
            self.assertEqual(result['data']['state'], 'terminal')
        api.hooks['session_inbox'] = stop
        code, result, text = self.receive(api)
        self.assertEqual((code, result['code']), (3, 'native_worker_slot_terminal'), text)
        self.assertNotIn('service secret', text)
        self.assertEqual(self.worker_ledger(self.slot).setting('delivery:' + M1)['state'], 'offered',
                         'an unresolved offer is preserved, never acknowledged automatically')
        self.assertEqual(api.acked, [])

    @isolated_profiles
    def test_claude_inflight_side_effect_is_not_claimed_rolled_back(self):
        import quorum_claude_worker as worker
        api = Api(inbox=[message(M1)])
        self.bound(api)
        self.offer(api)
        api.hooks['session_reply'] = self.stopper()
        code, result, text = self.worker(api, 'reply', M1, '--text-stdin', stdin=b'answer')
        self.assertEqual((code, result['code']), (3, 'native_worker_slot_terminal'), text)
        self.assertNotIn('rolled', text)
        self.assertIn(M1, api.replies, 'the reply already sent completed remotely')
        stored = self.worker_ledger(self.slot).setting('delivery:' + M1)
        self.assertEqual((stored['state'], len(stored['reply_digest'])), ('offered', 64))
        self.assertNotIn('session_ack', api.ops())
        with claude(): status = worker.slot_status(self.root, self.slot)
        self.assertEqual((status['state'], status['session']['pending_receipts']), ('terminal', 1))

    @isolated_profiles
    def test_claude_stop_terminal_no_start_or_regrant(self):
        import quorum_claude_worker as worker
        api = Api(inbox=[message(M1)])
        self.bound(api)
        self.offer(api)
        code, stopped, _ = self.cli(None, 'session', '--session-id', actor(), 'stop')
        self.assertEqual((code, stopped['data']['state'], stopped['data']['session']['reception']),
                         (0, 'terminal', 'disabled'))
        for argv in (('receive', '--wait-seconds', '0'), ('received', M1), ('status',)):
            code, result, _ = self.worker(api, *argv)
            self.assertEqual((code, result['code']), (3, 'native_worker_slot_terminal'))
        code, result, _ = self.worker(api, 'start')
        self.assertEqual((code, result['code']), (3, 'native_inbox_transport_conflict'))
        again = self.prepare(self.accept_scope())['slot_id']
        self.denied('native_worker_binding_denied', self.attest, again)
        with claude():
            self.assertEqual(worker.slot_status(self.root, self.slot)['session']['pending_receipts'], 1)
        self.assertEqual(api.acked, [])

    @isolated_profiles
    def test_claude_grant_release_membership_and_terminal_reply_rules(self):
        grant = ('--accept-tasks-from', 'coordinator', '--dispatch-mode', 'native-subagent')
        api = Api(inbox=[message(M1, body='private'), message(M2, sender_id=PEER, reply_to=M1),
                         message(M3, notification=True)])
        self.bound(api, self.accept_scope(coordinator_role='coordinator', dispatch_mode='native-subagent'), grant)
        envelope = self.offer(api)
        for required in ('Dispatch mode native-subagent', 'Follow that dispatch policy yourself as the grant holder',
                         'Only this worker may run', 'run session --worker-capability ' + str(self.cap) + ' status'):
            self.assertIn(required, envelope['notice'])
        self.assertNotIn('--session-id', envelope['notice'])
        api.revoked = True
        code, result, text = self.receive(api)
        self.assertEqual(code, 3)
        self.assertNotIn('private', text)
        api.revoked = False
        api.required = {'protocol_version': 99, 'skill_version': '9.9.9'}
        for argv in (('receive', '--wait-seconds', '0'), ('received', M1), ('reply', M1, '--text-stdin')):
            code, result, text = self.worker(api, *argv, stdin=b'x')
            self.assertEqual((code, result['code']), (4, 'skill_update_required'), text)
            self.assertNotIn('private', text)
        api.required = installed()
        self.assertEqual(self.worker(api, 'received', M1)[0], 0)
        for mid in (M2, M3):
            self.assertEqual(self.offer(api)['message_id'], mid)
            code, result, _ = self.worker(api, 'reply', mid, '--text-stdin', stdin=b'loop')
            self.assertEqual((code, result['code']), (3, 'native_inbox_reply_loop_denied'))
            self.assertEqual(self.worker(api, 'received', mid)[0], 0)
        self.assertNotIn('session_reply', api.ops())

    @isolated_profiles
    def test_worker_commands_and_public_summaries_never_emit_bearer_or_body(self):
        import glob, sqlite3
        api = Api(inbox=[message(M1, body='unique-body-marker')])
        outputs = []
        def run(*argv, **kwargs):
            code, result, text = self.cli(*argv, **kwargs)
            outputs.append((argv, text))
            return code, result, text
        slot, cap = self.active(self.accept_scope())
        self.configure()
        self.slot, self.cap = slot, cap
        token = json.loads(cap.read_text())['token']
        run(api, 'accept-id', INVITE, '--transport', 'tool-inbox', '--worker-capability', str(cap))
        run(api, 'session', '--worker-capability', str(cap), 'connect', 'team', '--role', 'service-worker',
            '--project-ref', 'team/project', '--transport', 'tool-inbox')
        run(api, 'session', '--worker-capability', str(cap), 'receive', '--wait-seconds', '0')
        run(None, 'session', '--worker-capability', str(cap), 'status')
        run(None, 'session', '--session-id', actor(), 'status')
        run(None, 'session', 'worker', 'status', slot)
        run(api, 'session', '--worker-capability', str(cap), 'reply', M1, '--text-stdin', stdin=b'answer')
        run(None, 'session', '--worker-capability', str(cap), 'stop')
        for argv, text in outputs:
            self.assertNotIn(token, text, argv)
            if 'receive' not in argv: self.assertNotIn('unique-body-marker', text, argv)
        self.assertIn('unique-body-marker', outputs[2][1])
        # The raw bearer exists only in the capability file; private ledgers hold its hash.
        for path in glob.glob(str(self.root / '**' / '*.sqlite'), recursive=True):
            db = sqlite3.connect(path)
            try:
                for table, in db.execute("select name from sqlite_master where type='table'"):
                    for row in db.execute('select * from ' + table):
                        self.assertNotIn(token, json.dumps(row, default=str), path)
            finally: db.close()
        self.assertEqual(hashlib.sha256(token.encode()).hexdigest(), self.setting(slot)['token_hash'])

    @isolated_profiles
    def test_concurrent_reply_intent_never_loses_the_ack_cas(self):
        from quorum_native_inbox import confirm, reply
        api = Api(inbox=[message(M1), message(M2)])
        self.bound(api)
        self.offer(api)
        ledger, record, check = self.direct('received', {'message_id': M1})
        def reply_intent():
            with OtherConnection(ledger.root) as other:
                raw = other.db.execute('select data from settings where key=?', ('delivery:' + M1,)).fetchone()[0]
                with other.db:
                    other.db.execute('update settings set data=? where key=? and data=?',
                                     (json.dumps({**json.loads(raw), 'reply_digest': 'd' * 64}), 'delivery:' + M1, raw))
        api.hooks['session_ack'] = reply_intent
        with claude(): self.assertEqual(confirm(api, ledger, record, M1, authorize=check)['state'], 'acked')
        stored = ledger.setting('delivery:' + M1)
        self.assertEqual((stored['state'], stored['reply_digest']), ('acked', 'd' * 64))
        self.assertEqual(self.offer(api)['message_id'], M2)
        def receipt():
            with OtherConnection(ledger.root) as other, claude():
                self.assertEqual(confirm(api, other, record, M2, authorize=check)['state'], 'acked')
        api.hooks['session_reply'] = receipt
        with claude(): result = reply(api, ledger, record, M2, 'answer', 'normal', authorize=check)
        self.assertEqual((result['received'], ledger.setting('delivery:' + M2)['state']), (True, 'acked'))
        self.assertEqual(len(api.replies), 1)

    @isolated_profiles
    def test_completed_receipt_only_closes_the_answer_path(self):
        api = Api(inbox=[message(M1)])
        self.bound(api)
        self.offer(api)
        self.assertEqual(self.worker(api, 'received', M1)[1]['data']['state'], 'acked')
        self.assertEqual(self.worker(api, 'received', M1)[1]['data']['state'], 'acked')
        acked = self.worker_ledger(self.slot).setting('delivery:' + M1)
        code, result, _ = self.worker(api, 'reply', M1, '--text-stdin', stdin=b'late answer')
        self.assertEqual((code, result['code']), (3, 'native_inbox_reply_loop_denied'))
        self.assertNotIn('session_reply', api.ops())
        self.assertEqual(self.worker_ledger(self.slot).setting('delivery:' + M1), acked)
        self.assertNotIn('reply_digest', acked)

    @isolated_profiles
    def test_receipt_winning_between_reply_read_and_intent_cas_denies_the_answer(self):
        from quorum_native_inbox import NativeInboxError, confirm, reply
        api = Api(inbox=[message(M1)])
        self.bound(api)
        self.offer(api)
        ledger, record, check = self.direct('reply', {'message_id': M1, 'priority': 'normal'})
        real = ledger.db
        test = self
        class Interleave:
            """The reply already read the offered row; a receipt commits before its intent CAS runs."""
            fired = False
            def __getattr__(self, name): return getattr(real, name)
            def __enter__(self): return real.__enter__()
            def __exit__(self, *exc): return real.__exit__(*exc)
            def execute(self, sql, *params):
                if sql.startswith('update settings set data=? where key=? and data=?') and not Interleave.fired:
                    Interleave.fired = True
                    with OtherConnection(ledger.root) as other:
                        test.assertEqual(confirm(api, other, record, M1, authorize=check)['state'], 'acked')
                return real.execute(sql, *params)
        ledger.db = Interleave()
        try:
            with claude(), self.assertRaises(NativeInboxError) as raised:
                reply(api, ledger, record, M1, 'answer', 'normal', authorize=check)
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
        self.bound(api)
        self.offer(api)
        ledger, record, check = self.direct('reply', {'message_id': M1, 'priority': 'normal'})
        def receipt():
            with OtherConnection(ledger.root) as other, claude():
                self.assertEqual(confirm(api, other, record, M1, authorize=check)['state'], 'acked')
        api.hooks['session_reply'] = receipt
        api.lost.append('session_reply')
        with claude(), self.assertRaises(QuorumError):
            reply(api, ledger, record, M1, 'answer', 'normal', authorize=check)
        stored = ledger.setting('delivery:' + M1)
        self.assertEqual((stored['state'], len(stored['reply_digest'])), ('acked', 64))
        with claude(): replay = reply(api, ledger, record, M1, 'answer', 'normal', authorize=check)
        for stdin, extra in ((b'changed', ()), (b'answer', ('--priority', 'urgent'))):
            code, result, _ = self.worker(api, 'reply', M1, '--text-stdin', *extra, stdin=stdin)
            self.assertEqual((code, result['code']), (3, 'native_inbox_reply_loop_denied'))
        self.assertEqual(replay['reply_message_id'], api.replies[M1])
        replies = [payload for operation, payload, _ in api.calls if operation == 'session_reply']
        self.assertEqual(len(replies), 2)
        self.assertTrue(all(payload == replies[0] for payload in replies))
        self.assertEqual(ledger.setting('delivery:' + M1), stored)
        self.assertEqual(api.acked, [M1])



@POSIX_PROOF
class ActorPathTests(Bound):
    """The logical actor's own ledger keeps the private-path contract (review fix I1) and
    post-dispatch failures report the remote effect truthfully (review fix I2)."""

    def actor_dir(self):
        return self.root / 'sessions' / actor()

    def snapshot(self, path):
        info = path.stat()
        return stat.S_IMODE(info.st_mode), path.read_bytes()

    @isolated_profiles
    def test_symlinked_sessions_parent_is_refused_before_profile_or_api(self):
        api = Api()
        self.bound(api, connect=False)
        outside = self.base / 'outside'
        outside.mkdir(mode=0o700)
        (self.root / 'sessions').symlink_to(outside)
        before = len(api.calls)
        # api_for raises auth_required if reached: an unavailable code proves no profile or API work.
        code, result, text = self.connect(None, self.cap)
        self.assertEqual((code, result['code']), (3, 'native_worker_unavailable'), text)
        self.assertEqual(os.listdir(outside), [], 'nothing is created outside the pinned state root')
        self.assertEqual(len(api.calls), before)
        self.assertEqual(self.setting(self.slot)['target'], {'quorum_id': QUORUM, 'membership_id': 'membership-1'})

    @isolated_profiles
    def test_loose_actor_ledger_or_directory_is_refused_without_chmod_including_metadata(self):
        import quorum_claude_worker as worker
        api = Api(inbox=[message(M1)])
        self.bound(api)
        ledger_path = self.actor_dir() / 'ledger.sqlite'
        for target, loose in ((ledger_path, 0o644), (self.actor_dir(), 0o755)):
            original = stat.S_IMODE(target.stat().st_mode)
            target.chmod(loose)
            before = len(api.calls)
            for argv in (('status',), ('receive', '--wait-seconds', '0')):
                code, result, text = self.worker(api, *argv)
                self.assertEqual((code, result['code']), (3, 'native_worker_unavailable'), text)
            with claude(): self.denied('native_worker_unavailable', worker.slot_status, self.root, self.slot)
            self.assertEqual(stat.S_IMODE(target.stat().st_mode), loose, 'never chmodded')
            self.assertEqual(len(api.calls), before, 'no profile or API work')
            target.chmod(original)
        self.assertEqual(self.offer(api)['message_id'], M1)

    @isolated_profiles
    def test_metadata_status_is_read_only(self):
        import quorum_claude_worker as worker
        api = Api()
        self.bound(api)
        ledger = self.worker_ledger(self.slot)
        # A recorded live run without its lock would be diagnosed (written) by the mutable status path.
        ledger.setting('listener', {'run_id': str(uuid.uuid4()), 'state': 'waiting', 'handled': 0})
        ledger.db.close()
        path = self.actor_dir() / 'ledger.sqlite'
        before = self.snapshot(path), sorted(os.listdir(self.actor_dir()))
        with claude(): status = worker.slot_status(self.root, self.slot)
        code, capability, _ = self.worker(None, 'status')
        self.assertEqual((status['session']['listener'], code), ('offline', 0))
        self.assertEqual(capability['data']['session'], status['session'])
        self.assertEqual((self.snapshot(path), sorted(os.listdir(self.actor_dir()))), before)

    @isolated_profiles
    def test_actor_ledger_replacement_between_verification_and_open_is_refused(self):
        import shutil, sqlite3
        api = Api(inbox=[message(M1)])
        self.bound(api)
        outside = self.base / 'outside'
        shutil.copytree(self.root / 'sessions', outside)
        decoy = outside / actor() / 'ledger.sqlite'
        before = self.snapshot(decoy)
        real = sqlite3.connect
        swapped = []
        def connect(target, *args, **kwargs):
            if not swapped and '/sessions/' in str(target) and 'mode=ro' not in str(target):
                swapped.append(target)
                (self.root / 'sessions').rename(self.root / 'sessions-verified')
                (self.root / 'sessions').symlink_to(outside)
            return real(target, *args, **kwargs)
        calls = len(api.calls)
        with patch('quorum_claude_worker.sqlite3.connect', side_effect=connect):
            code, result, text = self.receive(api)
        self.assertTrue(swapped)
        self.assertEqual((code, result['code']), (3, 'native_worker_unavailable'), text)
        self.assertEqual(self.snapshot(decoy), before, 'the replacement was neither written nor chmodded')
        self.assertEqual(len(api.calls), calls)
        (self.root / 'sessions').unlink()
        (self.root / 'sessions-verified').rename(self.root / 'sessions')
        self.assertEqual(self.offer(api)['message_id'], M1)

    @isolated_profiles
    def test_post_accept_capability_failure_reports_accepted_membership(self):
        import quorum_claude_worker as worker
        api = Api()
        slot, cap = self.active(self.accept_scope())
        self.configure()
        # A refusal before dispatch still promises an unused invitation.
        cap.chmod(0o640)
        code, result, _ = self.cli(api, 'accept-id', INVITE, '--transport', 'tool-inbox', '--worker-capability', str(cap))
        self.assertEqual((code, result['code']), (3, 'native_worker_unavailable'))
        self.assertIn('was not accepted', result['data']['next_step'])
        self.assertNotIn('remote_effect', result['data'])
        cap.chmod(0o600)
        self.assertEqual(api.calls, [])
        # The capability becomes unsafe while invite_accept_id is in flight: the membership already exists.
        api.hooks['invite_accept_id'] = lambda: cap.chmod(0o640)
        code, result, text = self.cli(api, 'accept-id', INVITE, '--transport', 'tool-inbox', '--worker-capability', str(cap))
        self.assertEqual((code, result['code'], result['data']['reason']), (3, 'native_worker_unavailable', 'path_unsafe'))
        self.assertEqual(result['data']['remote_effect'], {'state': 'accepted', 'operation': 'invite_accept_id',
                                                           'quorum_id': QUORUM, 'membership_id': 'membership-1'})
        for false_claim in ('not accepted', 'still unused', 'nothing was registered'):
            self.assertNotIn(false_claim, text)
        self.assertIn('already accepted', result['data']['next_step'])
        self.assertIn('Do not retry', result['data']['next_step'])
        self.assertEqual(self.setting(slot)['target'], {'quorum_id': QUORUM, 'membership_id': 'membership-1'})
        cap.chmod(0o600)
        # No response observed and the authority then fails: explicitly uncertain, never unused.
        other, other_cap = self.active(self.accept_scope(), handle='uncertain')
        def lost_then_stop():
            with claude(): worker.stop_slot(self.root, other)
        api.hooks['invite_accept_id'] = lost_then_stop
        api.lost.append('invite_accept_id')
        code, result, text = self.cli(api, 'accept-id', INVITE, '--transport', 'tool-inbox',
                                      '--worker-capability', str(other_cap))
        self.assertEqual((code, result['code']), (3, 'native_worker_slot_terminal'), text)
        self.assertEqual(result['data']['remote_effect'], {'state': 'uncertain', 'operation': 'invite_accept_id'})
        self.assertIn('may already be consumed', result['data']['next_step'])
        self.assertNotIn('not accepted', text)

    @isolated_profiles
    def test_post_dispatch_delivery_failure_reports_completed_operation(self):
        api = Api(inbox=[message(M1, body='private body')])
        self.bound(api)
        api.hooks['session_inbox'] = lambda: self.cap.chmod(0o640)
        code, result, text = self.receive(api)
        self.cap.chmod(0o600)
        self.assertEqual((code, result['code']), (3, 'native_worker_unavailable'), text)
        # The latest state-changing operation (the Hello heartbeat) is reported ahead of the later inbox read.
        self.assertEqual(result['data']['remote_effect'], {'state': 'completed', 'operation': 'session_heartbeat'})
        self.assertNotIn('not accepted', text)
        self.assertNotIn('private body', text)


@POSIX_PROOF
class ConnectionIdentityTests(Bound):
    """Review fix round 2: the actual SQLite connection, schema, locks and sidecars stay bound to the
    verified actor directory (I1); every post-dispatch local failure keeps its remote effect (I2)."""

    def outside_copy(self):
        import shutil
        outside = self.base / 'outside'
        shutil.copytree(self.root / 'sessions', outside)
        return outside

    def settings(self, path):
        import sqlite3
        db = sqlite3.connect(path)
        try: return dict(db.execute('select key, data from settings'))
        finally: db.close()

    @isolated_profiles
    def test_swap_open_restore_never_returns_an_outside_connection(self):
        import sqlite3
        import quorum_claude_worker as worker
        api = Api(inbox=[message(M1)])
        self.bound(api)
        outside = self.outside_copy()
        decoy, original = outside / actor() / 'ledger.sqlite', self.actor_dir() / 'ledger.sqlite'
        decoy_before, original_before = self.settings(decoy), self.settings(original)
        real_connect, switched = sqlite3.connect, []
        def redirected(target, *args, **kwargs):
            # Real renames and a real SQLite open: A -> outside B during connect -> A restored before return.
            if not switched and '/sessions/' in str(target) and 'mode=rw' in str(target):
                switched.append(True)
                (self.root / 'sessions').rename(self.root / 'sessions-verified')
                (self.root / 'sessions').symlink_to(outside)
                try: return real_connect(target, *args, **kwargs)
                finally:
                    (self.root / 'sessions').unlink()
                    (self.root / 'sessions-verified').rename(self.root / 'sessions')
            return real_connect(target, *args, **kwargs)
        ledger = None
        with patch('quorum_claude_worker.sqlite3.connect', side_effect=redirected):
            try: ledger = worker._actor_ledger(self.root, actor())
            except Exception as error: refused = error
            else: refused = None
        if ledger is not None:
            try: ledger.setting('review-identity-marker', {'fixture': True})
            finally: ledger.db.close()
        self.assertTrue(switched)
        self.assertIsNone(ledger, 'a connection opened on the outside database must never be returned')
        self.assertEqual(getattr(refused, 'code', None), 'native_worker_unavailable')
        self.assertEqual(self.settings(decoy), decoy_before)
        self.assertEqual(self.settings(original), original_before)
        # The same interleaving through the CLI refuses before any profile or API work.
        switched.clear()
        calls = len(api.calls)
        with patch('quorum_claude_worker.sqlite3.connect', side_effect=redirected):
            code, result, text = self.receive(api)
        self.assertEqual((code, result['code']), (3, 'native_worker_unavailable'), text)
        self.assertEqual(len(api.calls), calls)
        self.assertEqual(self.settings(decoy), decoy_before)
        self.assertEqual(self.offer(api)['message_id'], M1)

    @isolated_profiles
    def test_helper_locks_and_logs_stay_in_the_verified_directory_after_open(self):
        import quorum_claude_worker as worker
        from quorum_live import LocalSupervisorLock
        from quorum_listener import locked
        from quorum_os import FileLock, open_private
        api = Api()
        self.bound(api)
        ledger = worker._actor_ledger(self.root, actor())
        try:
            ledger.setting('review-write', {'fixture': True})
            outside = self.base / 'outside'
            outside.mkdir(mode=0o700)
            (outside / actor()).mkdir(mode=0o700)
            # Direct helper locks/logs after an ancestor swap. SQLite's own native I/O is outside this check:
            # the amended contract requires stable directories while a connection is in use.
            (self.root / 'sessions').rename(self.root / 'sessions-verified')
            (self.root / 'sessions').symlink_to(outside)
            try:
                with FileLock(ledger.root / 'listener-control.lock'), LocalSupervisorLock(ledger.root):
                    self.assertFalse(locked(ledger.root / 'listener.lock'))
                    with FileLock(ledger.root / 'listener.lock'):
                        self.assertTrue(locked(ledger.root / 'listener.lock'))
                os.close(open_private(ledger.root / 'listener.log', os.O_CREAT | os.O_WRONLY | os.O_APPEND))
            finally:
                (self.root / 'sessions').unlink()
                (self.root / 'sessions-verified').rename(self.root / 'sessions')
        finally:
            ledger.db.close()
        self.assertEqual(os.listdir(outside / actor()), [], 'no helper lock or log outside')
        self.assertTrue({'listener-control.lock', 'supervisor.lock', 'listener.lock', 'listener.log'}
                        <= set(os.listdir(self.actor_dir())))
        self.assertIn('review-write', self.settings(self.actor_dir() / 'ledger.sqlite'))

    @isolated_profiles
    def test_connection_identity_fails_closed_without_descriptor_proof(self):
        import quorum_claude_worker as worker
        api = Api()
        self.bound(api)
        before = self.snapshot(self.actor_dir() / 'ledger.sqlite')
        with patch('quorum_claude_worker.DESCRIPTOR_LISTING', str(self.base / 'no-descriptor-listing')):
            with self.assertRaises(Exception) as raised:
                worker._actor_ledger(self.root, actor())
        self.assertEqual(getattr(raised.exception, 'code', None), 'native_worker_unavailable')
        self.assertEqual(getattr(raised.exception, 'reason', None), 'ownership_unverified')
        self.assertEqual(self.snapshot(self.actor_dir() / 'ledger.sqlite'), before)

    def snapshot(self, path):
        info = path.stat()
        return stat.S_IMODE(info.st_mode), path.read_bytes()

    def actor_dir(self):
        return self.root / 'sessions' / actor()

    def create(self, api, cap):
        from quorum_api import ServiceConfig
        with patch('quorum_profiles.resolve_profile', return_value=(ServiceConfig(ORIGIN, KEY), Store())), \
                patch('quorum.QuorumApi', return_value=api), patch('quorum.token_provider', return_value=lambda: 'jwt'):
            return self.cli(api, 'create', 'team', '--access', 'github', '--json-stdin', '--worker-capability', str(cap),
                            stdin=b'{"slug": "team"}')

    def assert_dispatched(self, text, result, effect):
        self.assertEqual(result['data'].get('remote_effect'), effect, text)
        for false_claim in ('sent nothing', 'made no change', 'not accepted', 'stays unused', 'nothing was registered'):
            self.assertNotIn(false_claim, text)
        self.assertIn('Do not retry', result['data']['next_step'])

    @isolated_profiles
    def test_post_create_target_pin_refusal_reports_completed_create(self):
        api = Api()
        slot, cap = self.active()
        self.configure()
        api.hooks['quorum_create'] = lambda: cap.parent.chmod(0o755)
        code, result, text = self.create(api, cap)
        cap.parent.chmod(0o700)
        self.assertEqual((code, result['code'], result['data']['reason']), (3, 'native_worker_unavailable', 'path_unsafe'))
        self.assert_dispatched(text, result, {'state': 'completed', 'operation': 'quorum_create', 'quorum_id': QUORUM})
        self.assertEqual(api.ops(), ['quorum_create'])
        # Response unknown and the slot then unsafe: explicitly uncertain, never a no-dispatch claim.
        other, other_cap = self.active(handle='uncertain')
        api.hooks['quorum_create'] = lambda: other_cap.parent.chmod(0o755)
        api.lost.append('quorum_create')
        code, result, text = self.create(api, other_cap)
        other_cap.parent.chmod(0o700)
        self.assertEqual((code, result['code']), (3, 'native_worker_create_ambiguous'), text)
        self.assert_dispatched(text, result, {'state': 'uncertain', 'operation': 'quorum_create'})

    @isolated_profiles
    def test_post_create_evidence_persistence_failure_keeps_remote_effect(self):
        import sqlite3
        import quorum_claude_worker as worker
        api = Api()
        slot, cap = self.active()
        self.configure()
        def stop():
            with claude(): worker.stop_slot(self.root, slot)
        api.hooks['quorum_create'] = stop
        real = worker._set_create
        def failing(context, value):
            # Fault injection only: the completed-after-stop evidence write itself fails.
            if value.get('state') == 'completed_after_stop': raise sqlite3.OperationalError('disk I/O error')
            return real(context, value)
        with patch('quorum_claude_worker._set_create', side_effect=failing):
            code, result, text = self.create(api, cap)
        self.assertEqual((code, result['code']), (3, 'native_worker_slot_terminal'), text)
        self.assert_dispatched(text, result, {'state': 'completed', 'operation': 'quorum_create', 'quorum_id': QUORUM})
        self.assertNotIn('disk I/O', text)
        # A local write failure after a completed create keeps the effect even with no authority refusal.
        other, other_cap = self.active(handle='write-failure')
        api.hooks.pop('quorum_create', None)
        def failing_pin(*_, **__): raise sqlite3.OperationalError('database is locked')
        with patch('quorum_claude_worker.pin_target', side_effect=failing_pin):
            code, result, text = self.create(api, other_cap)
        self.assertEqual(code, 3, text)
        self.assertEqual(result['data'].get('reason'), 'evidence_unpersisted')
        self.assert_dispatched(text, result, {'state': 'completed', 'operation': 'quorum_create', 'quorum_id': QUORUM})
        self.assertNotIn('database is locked', text)

    @isolated_profiles
    def test_post_accept_slot_refusal_reports_accepted_membership(self):
        api = Api()
        slot, cap = self.active(self.accept_scope())
        self.configure()
        api.hooks['invite_accept_id'] = lambda: cap.parent.chmod(0o755)
        code, result, text = self.cli(api, 'accept-id', INVITE, '--transport', 'tool-inbox', '--worker-capability', str(cap))
        cap.parent.chmod(0o700)
        self.assertEqual((code, result['code']), (3, 'native_worker_unavailable'), text)
        self.assert_dispatched(text, result, {'state': 'accepted', 'operation': 'invite_accept_id', 'quorum_id': QUORUM,
                                              'membership_id': 'membership-1'})


CRASH = r"""
import json, os, sqlite3, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
route, root, ref = sys.argv[2], Path(sys.argv[3]), sys.argv[4]
if route == 'product':
    import quorum_claude_worker as worker
    db = worker._actor_ledger(root, ref).db
else:
    # An earlier process using SQLite's ordinary disk rollback journal.
    db = sqlite3.connect(str(root / 'sessions' / ref / 'ledger.sqlite'), isolation_level=None)
db.execute('pragma cache_size=3')
db.execute('pragma cache_spill=3')
db.execute('begin immediate')
for index in range(80):
    db.execute('update settings set data=? where key=?', (json.dumps('U' * 4096), 'fill:%d' % index))
os._exit(73)
"""


@POSIX_PROOF
class DurableStorageTests(Bound):
    """Amended storage contract (review fix round 3): ordinary disk journaling and crash recovery, legitimate
    private recovery journals allowed, unsafe sidecars refused unchanged."""

    def actor_dir(self):
        return self.root / 'sessions' / actor()

    def fill(self):
        """80 committed 4096-byte rows, written through the product's verified actor ledger."""
        import quorum_claude_worker as worker
        ledger = worker._actor_ledger(self.root, actor())
        try:
            with ledger.db:
                ledger.db.executemany('insert into settings values (?,?)',
                                      [('fill:%d' % index, json.dumps('O' * 4096)) for index in range(80)])
        finally: ledger.db.close()

    def crash(self, route):
        """An owned short subprocess exits during an uncommitted transaction after cache spill."""
        import subprocess
        done = subprocess.run([sys.executable, '-B', '-c', CRASH, str(SCRIPTS), route, str(self.root), actor()],
                              capture_output=True, timeout=60)
        self.assertEqual(done.returncode, 73, done.stderr.decode(errors='replace'))

    def uncommitted(self):
        import sqlite3
        db = sqlite3.connect('file:' + str(self.actor_dir() / 'ledger.sqlite') + '?mode=ro', uri=True)
        try:
            return (db.execute("select count(*) from settings where key like 'fill:%' and data like '%U%'").fetchone()[0],
                    db.execute('pragma integrity_check').fetchone()[0])
        finally: db.close()

    @isolated_profiles
    def test_crash_mid_transaction_rolls_back_uncommitted_existing_row_updates(self):
        import quorum_claude_worker as worker
        api = Api()
        self.bound(api)
        self.fill()
        self.crash('product')
        # Reopen through the product route; SQLite's recovery must undo every uncommitted update.
        worker._actor_ledger(self.root, actor()).db.close()
        self.assertEqual(self.uncommitted(), (0, 'ok'), 'logical rollback, not only integrity')

    @isolated_profiles
    def test_legitimate_hot_journal_recovers_through_capability_receive(self):
        import quorum_claude_worker as worker
        api = Api(inbox=[message(M1)])
        self.bound(api)
        self.fill()
        self.crash('raw')
        journal = self.actor_dir() / 'ledger.sqlite-journal'
        self.assertTrue(journal.is_file(), 'the crash left a hot rollback journal')
        self.assertEqual(stat.S_IMODE(journal.stat().st_mode), 0o600)
        # Read-only metadata never removes the recovery journal SQLite needs.
        with claude():
            try: worker.slot_status(self.root, self.slot)
            except Exception: pass
        self.assertTrue(journal.is_file(), 'a legitimate recovery journal is kept for SQLite')
        envelope = self.offer(api)
        self.assertEqual(envelope['message_id'], M1)
        self.assertEqual(self.uncommitted(), (0, 'ok'))
        self.assertFalse(journal.exists(), 'SQLite completed recovery through the capability route')

    @isolated_profiles
    def test_unsafe_sidecars_fail_closed_without_mutation(self):
        api = Api(inbox=[message(M1)])
        self.bound(api)
        journal = self.actor_dir() / 'ledger.sqlite-journal'
        target = self.base / 'outside-journal'
        def snapshot(path):
            return stat.S_IMODE(path.lstat().st_mode), path.read_bytes() if path.is_file() else None
        cases = []
        def symlinked():
            target.write_bytes(b'outside bytes')
            target.chmod(0o600)
            journal.symlink_to(target)
        def loose():
            journal.write_bytes(b'')
            journal.chmod(0o644)
        def hardlinked():
            journal.write_bytes(b'')
            journal.chmod(0o600)
            os.link(journal, self.base / 'journal-link')
        def directory():
            journal.mkdir(mode=0o700)
        for make in (symlinked, loose, hardlinked, directory):
            make()
            database = snapshot(self.actor_dir() / 'ledger.sqlite')
            outside = snapshot(target) if target.exists() else None
            before = (journal.lstat().st_mode, journal.lstat().st_ino)
            calls = len(api.calls)
            code, result, text = self.receive(api)
            self.assertEqual((code, result['code']), (3, 'native_worker_unavailable'), make.__name__ + text)
            self.assertEqual((journal.lstat().st_mode, journal.lstat().st_ino), before, 'sidecar left unchanged')
            self.assertEqual(snapshot(self.actor_dir() / 'ledger.sqlite'), database)
            if outside: self.assertEqual(snapshot(target), outside)
            self.assertEqual(len(api.calls), calls, 'refused before profile or API work')
            if journal.is_dir() and not journal.is_symlink(): journal.rmdir()
            else: journal.unlink()
            (self.base / 'journal-link').unlink(missing_ok=True)
        self.assertEqual(self.offer(api)['message_id'], M1)


CRASH_DB = r"""
import json, os, sqlite3, sys
db = sqlite3.connect(sys.argv[1], isolation_level=None)
db.execute('pragma cache_size=3')
db.execute('pragma cache_spill=3')
db.execute('begin immediate')
for index in range(80):
    db.execute('update settings set data=? where key=?', (json.dumps('U' * 4096), 'fill:%d' % index))
os._exit(73)
"""


class StubWaiter:
    """Only the waiter seam: no socket. `on_close` runs at the real cleanup boundary."""
    busy_seconds = 0
    def __init__(self, on_close=None): self.on_close = on_close
    def prepare(self, stopping): pass
    def pulse(self, stopping): pass
    def wait(self, predicate): pass
    def close(self):
        if self.on_close: self.on_close()


@POSIX_PROOF
class FinalWaveTests(Bound):
    """Final whole-branch wave: W1 root/lock files, W2 post-cleanup disclosure, W3 slot recovery, M1 wording."""

    def mode(self, path): return stat.S_IMODE(path.lstat().st_mode)

    def hot_slot(self, slot):
        """A legitimate private hot journal in the slot authority database (owned crash subprocess)."""
        import sqlite3, subprocess
        path = self.root / 'claude-workers' / slot / 'ledger.sqlite'
        db = sqlite3.connect(path)
        try:
            with db: db.executemany('insert or replace into settings values (?,?)',
                                    [('fill:%d' % index, json.dumps('O' * 4096)) for index in range(80)])
        finally: db.close()
        done = subprocess.run([sys.executable, '-B', '-c', CRASH_DB, str(path)], capture_output=True, timeout=60)
        self.assertEqual(done.returncode, 73, done.stderr.decode(errors='replace'))
        journal = path.with_name('ledger.sqlite-journal')
        self.assertTrue(journal.is_file())
        return path, journal

    def uncommitted(self, path):
        import sqlite3
        db = sqlite3.connect('file:' + str(path) + '?mode=ro', uri=True)
        try: return db.execute("select count(*) from settings where key like 'fill:%' and data like '%U%'").fetchone()[0]
        finally: db.close()

    def slot_record(self, path):
        import sqlite3
        db = sqlite3.connect('file:' + str(path) + '?mode=ro', uri=True)
        try: return db.execute("select data from settings where key='claude-worker-slot'").fetchone()[0]
        finally: db.close()

    @isolated_profiles
    def test_unsafe_root_ledger_refused_unchanged_on_every_claude_entry(self):
        api = Api()
        slot, cap = self.active(self.accept_scope())
        pending = self.prepare(self.accept_scope())['slot_id']
        self.configure()
        root_db = self.root / 'ledger.sqlite'
        outside = self.base / 'outside.sqlite'
        def entries():
            return [('capability status', lambda: self.cli(api, 'session', '--worker-capability', str(cap), 'status')),
                    ('capability accept', lambda: self.cli(api, 'accept-id', INVITE, '--transport', 'tool-inbox',
                                                           '--worker-capability', str(cap))),
                    ('prepare', lambda: self.cli(None, 'session', 'worker', 'prepare', '--json-stdin',
                                                 stdin=json.dumps(self.accept_scope()).encode())),
                    ('attest', lambda: self.cli(None, 'session', 'worker', 'attest', pending, '--native-handle', 'other')),
                    ('logical status', lambda: self.cli(None, 'session', '--session-id', actor(), 'status'))]
        root_db.chmod(0o644)
        for name, run in entries():
            code, result, text = run()
            self.assertEqual((code, result['code']), (3, 'native_worker_unavailable'), name + text)
            self.assertEqual(self.mode(root_db), 0o644, name + ': never chmodded')
        root_db.chmod(0o600)
        os.link(root_db, outside)
        before = outside.read_bytes()
        for name, run in entries():
            code, result, text = run()
            self.assertEqual((code, result['code']), (3, 'native_worker_unavailable'), name + text)
        self.assertEqual((outside.read_bytes(), outside.stat().st_nlink), (before, 2), 'nothing written through the link')
        self.assertEqual(api.calls, [], 'no profile, auth or API work')
        self.assertEqual(self.setting(pending)['state'], 'pending')
        outside.unlink()
        self.assertEqual(self.cli(api, 'accept-id', INVITE, '--transport', 'tool-inbox', '--worker-capability', str(cap))[0], 0)

    @isolated_profiles
    def test_consumed_unsafe_lock_files_refused_unchanged(self):
        import quorum_claude_worker as worker
        api = Api()
        self.bound(api)
        pending = self.prepare(self.accept_scope())['slot_id']
        slot_lock = self.root / 'claude-workers' / self.slot / 'slot.lock'
        index_lock = self.root / 'claude-workers' / 'index.lock'
        self.assertTrue(slot_lock.exists() and index_lock.exists())
        slot_lock.chmod(0o666)
        with claude(): self.denied('native_worker_unavailable', worker.stop_slot, self.root, self.slot)
        self.assertEqual((self.mode(slot_lock), self.setting(self.slot)['state']), (0o666, 'active'))
        slot_lock.chmod(0o600)
        index_lock.chmod(0o666)
        self.denied('native_worker_unavailable', self.attest, pending, 'other-handle')
        self.assertEqual((self.mode(index_lock), self.setting(pending)['state']), (0o666, 'pending'))
        index_lock.chmod(0o600)

    @isolated_profiles
    def test_stop_or_expiry_during_cleanup_discloses_no_body(self):
        from quorum_listener import Control
        cases = (('stop at waiter close', 'stop', 'close'), ('expiry at waiter close', 'expire', 'close'),
                 ('stop at final status write', 'stop', 'status'))
        for index, (name, event, boundary) in enumerate(cases):
            api = Api(inbox=[message(M1, body='cleanup-boundary-secret')])
            self.bound(api, handle='cleanup-%d' % index)
            self.now = T0
            act = self.stopper() if event == 'stop' else (lambda: setattr(self, 'now', T0 + 900))
            waiter = StubWaiter(act if boundary == 'close' else None)
            real_update = Control.update
            def update(control, run_id, state, handled, **kwargs):
                if boundary == 'status' and handled == 1: act()
                return real_update(control, run_id, state, handled, **kwargs)
            with patch('quorum_native_inbox.RealtimeWait', return_value=waiter), \
                    patch('quorum_listener.Control.update', update):
                code, result, text = self.receive(api, seconds='1')
            self.assertEqual((code, result['code']), (3, 'native_worker_slot_terminal'), name + text)
            self.assertNotIn('cleanup-boundary-secret', text, name)
            self.assertEqual(self.worker_ledger(self.slot).setting('delivery:' + M1)['state'], 'offered', name)
            self.assertIn('remote_effect', result['data'], name)
            self.assertEqual(api.acked, [], name)

    @isolated_profiles
    def test_hot_slot_journal_recovers_through_supported_routes_preserving_state(self):
        api = Api(inbox=[message(M1)])
        self.bound(api)
        path, journal = self.hot_slot(self.slot)
        # Metadata stays read-only and names the supported recovery route.
        for argv in (('session', 'worker', 'status', self.slot), ('session', '--worker-capability', str(self.cap), 'status')):
            code, result, text = self.cli(None, *argv)
            self.assertEqual((code, result['code'], result['data'].get('reason')),
                             (3, 'native_worker_unavailable', 'recovery_needed'), text)
            self.assertIn('await-binding', result['data']['next_step'])
            self.assertTrue(journal.is_file(), 'read-only metadata never recovers or removes the journal')
        # Owner lifecycle route: await-binding recovers and returns the unchanged active binding.
        code, awaited, text = self.cli(None, 'session', 'worker', 'await-binding', self.slot, '--wait-seconds', '1')
        self.assertEqual((code, awaited['data']['state']), (0, 'active'), text)
        self.assertFalse(journal.exists())
        self.assertEqual(self.uncommitted(path), 0)
        record = self.slot_record(path)
        # Capability route: the worker's next receive recovers too.
        self.hot_slot(self.slot)
        self.assertEqual(self.offer(api)['message_id'], M1)
        self.assertFalse(journal.exists())
        self.assertEqual(self.slot_record(path), record, 'recovery changes no authority record')
        # A terminal slot recovers through its own lifecycle route and stays terminal.
        code, stopped, _ = self.cli(None, 'session', 'worker', 'stop', self.slot)
        terminal = self.slot_record(path)
        self.hot_slot(self.slot)
        code, again, text = self.cli(None, 'session', 'worker', 'stop', self.slot)
        self.assertEqual((code, again['data']['state'], again['data']['terminal_reason']), (0, 'terminal', 'stopped'), text)
        self.assertFalse(journal.exists())
        self.assertEqual((self.uncommitted(path), self.slot_record(path)), (0, terminal))

    @isolated_profiles
    def test_slot_recovery_denies_malformed_and_unsafe_requests_first(self):
        api = Api()
        self.bound(api)
        path, journal = self.hot_slot(self.slot)
        copy = self.base / 'copy.json'
        copy.write_bytes(self.cap.read_bytes())
        copy.chmod(0o600)
        for argv, extra in ((('session', '--worker-capability', str(copy), 'receive', '--wait-seconds', '0'), {}),
                            (('session', 'worker', 'await-binding', self.slot, '--wait-seconds', '1'),
                             {'sid': None, 'extra': {'CODEX_THREAD_ID': OTHER_SID}}),
                            (('session', 'worker', 'await-binding', self.slot, '--wait-seconds', '1'),
                             {'sid': SID.upper()})):
            code, result, text = self.cli(api, *argv, **extra)
            self.assertEqual(code, 3, text)
            self.assertTrue(journal.is_file(), 'no recovery before caller, syntax and path checks')
        # An unsafe journal is refused unchanged on the recovery route.
        journal.chmod(0o644)
        code, result, text = self.cli(None, 'session', 'worker', 'await-binding', self.slot, '--wait-seconds', '1')
        self.assertEqual((code, result['code'], result['data'].get('reason')), (3, 'native_worker_unavailable', 'path_unsafe'))
        self.assertEqual(self.mode(journal), 0o644)
        journal.chmod(0o600)
        self.assertEqual(self.cli(None, 'session', 'worker', 'await-binding', self.slot, '--wait-seconds', '1')[0], 0)

    def test_recovery_wording_is_limited_to_uncommitted_local_changes(self):
        text = ' '.join((SKILL / 'references/claude-worker.md').read_text().split())
        for required in ('uncommitted', 'completed remote', 'committed local', 'session worker await-binding <slot-uuid>',
                         'recovery_needed'):
            self.assertIn(required, text)
        self.assertNotIn('an interrupted command rolls back', text)


@POSIX_PROOF
class ResidualCorrectionTests(Bound):
    """Bounded residual correction: F1 deny-first before slot recovery; M1 ledger-specific recovery advice."""
    hot_slot = FinalWaveTests.hot_slot

    def raw(self, slot):
        """Raw bytes only: never opens SQLite, so a check cannot itself recover the journal."""
        directory = self.root / 'claude-workers' / slot
        journal = directory / 'ledger.sqlite-journal'
        return (directory / 'ledger.sqlite').read_bytes(), journal.read_bytes() if journal.exists() else None

    def assert_denied_first(self, api, slot, run, codes=('native_worker_binding_denied',)):
        before, calls = self.raw(slot), len(api.calls)
        code, result, text = run()
        self.assertEqual(code, 3, text)
        self.assertIn(result['code'], codes, text)
        self.assertEqual(self.raw(slot), before, 'hot journal and database bytes untouched: ' + text)
        self.assertEqual(len(api.calls), calls)
        return result

    @isolated_profiles
    def test_independently_decidable_denials_precede_slot_recovery(self):
        api = Api(inbox=[message(M1)])
        self.bound(api)
        creator, creator_cap = self.active(self.scope(), handle='creator')
        self.hot_slot(self.slot)
        self.hot_slot(creator)
        setup_calls = len(api.calls)
        cap, original = self.cap, self.cap.read_bytes()
        receive = lambda: self.cli(api, 'session', '--worker-capability', str(cap), 'receive', '--wait-seconds', '0')
        document = json.loads(original)
        # Generated capability contents: bounded strict JSON and full structural schema, before recovery.
        for content in (b'{broken', b'[]', b' ' * 16385,
                        b'{"version": 1, "version": 1, "slot_id": "x", "state_root": "x", "token": "x"}',
                        json.dumps({**document, 'extra': True}).encode(),
                        json.dumps({**document, 'version': '1'}).encode(),
                        json.dumps({**document, 'token': 7}).encode(),
                        json.dumps({**document, 'token': ''}).encode(),
                        json.dumps({**document, 'slot_id': creator}).encode(),
                        json.dumps({**document, 'state_root': str(self.base)}).encode()):
            cap.write_bytes(content)
            cap.chmod(0o600)
            self.assert_denied_first(api, self.slot, receive,
                                     ('native_worker_binding_denied', 'native_worker_unavailable'))
        cap.write_bytes(original)
        cap.chmod(0o600)
        # Locally decidable accept/connect argument conflicts.
        accept = lambda *extra: (lambda: self.cli(api, 'accept-id', INVITE, '--worker-capability', str(cap), *extra))
        for extra in (('--transport', 'native'), ('--transport', 'tool-inbox', '--membership-only'),
                      ('--transport', 'tool-inbox', '--anonymous'), ('--transport', 'tool-inbox', '--origin', ORIGIN),
                      ('--transport', 'tool-inbox', '--client', 'grok-bot'), ()):
            self.assert_denied_first(api, self.slot, accept(*extra))
        self.assert_denied_first(api, self.slot, lambda: self.cli(
            api, 'session', '--worker-capability', str(cap), 'connect', 'team', '--role', 'service-worker',
            '--project-ref', 'team/project'))
        # Worker create: request override and malformed or conflicting create input, stdin read once.
        create = lambda *extra, body=b'{"slug": "team"}': (lambda: self.cli(
            api, 'create', 'team', '--access', 'github', '--worker-capability', str(creator_cap), *extra, stdin=body))
        result = self.assert_denied_first(api, creator, create('--json-stdin', '--request-id', 'forbidden'))
        self.assertEqual(result['data'].get('reason'), 'request_override')
        for extra, body in ((('--json-stdin',), b'{broken'), (('--json-stdin',), b'[]'), ((), b'{}'),
                            (('--json-stdin',), b'{"slug": "team", "visibility": "x"}'),
                            (('--json-stdin',), b'{"slug": "team", "slug": "team"}'),
                            (('--json-stdin',), b'{"slug": "other"}'),
                            (('--json-stdin', '--role', 'lead'), b'{"role": "other"}'),
                            (('--json-stdin',), b' ' * 16385)):
            self.assert_denied_first(api, creator, create(*extra, body=body))
        # Retained exception: a structurally valid request denied by its recovered record may recover first.
        code, result, text = self.cli(api, 'create', 'team', '--access', 'open', '--json-stdin',
                                      '--worker-capability', str(creator_cap), stdin=b'{"slug": "team"}')
        self.assertEqual((code, result['data'].get('reason')), (3, 'scope_mismatch'), text)
        self.assertFalse((self.root / 'claude-workers' / creator / 'ledger.sqlite-journal').exists())
        self.assertEqual(len(api.calls), setup_calls)

    @isolated_profiles
    def test_actor_recovery_advice_names_a_route_that_recovers_the_actor_ledger(self):
        import sqlite3, subprocess
        import quorum_claude_worker as worker
        api = Api(inbox=[message(M1)])
        self.bound(api)
        ledger = worker._actor_ledger(self.root, actor())
        try:
            with ledger.db: ledger.db.executemany('insert or replace into settings values (?,?)',
                                                  [('fill:%d' % index, json.dumps('O' * 4096)) for index in range(80)])
        finally: ledger.db.close()
        path = self.root / 'sessions' / actor() / 'ledger.sqlite'
        done = subprocess.run([sys.executable, '-B', '-c', CRASH_DB, str(path)], capture_output=True, timeout=60)
        self.assertEqual(done.returncode, 73, done.stderr.decode(errors='replace'))
        journal = path.with_name('ledger.sqlite-journal')
        self.assertTrue(journal.is_file())
        def status():
            return self.cli(None, 'session', '--worker-capability', str(self.cap), 'status')
        code, result, text = status()
        self.assertEqual((code, result['data'].get('reason')), (3, 'recovery_needed'), text)
        advice = result['data']['next_step']
        # The owner's slot route does not open the actor ledger, so the advice must not promise it.
        self.assertNotIn('await-binding', advice)
        self.assertEqual(self.cli(None, 'session', 'worker', 'await-binding', self.slot, '--wait-seconds', '1')[0], 0)
        self.assertTrue(journal.is_file(), 'owner await-binding leaves the actor journal')
        self.assertIn('session --worker-capability <capability-file> receive', advice)
        # Follow the advice: the worker's own receive recovers its ledger and works.
        self.assertEqual(self.offer(api)['message_id'], M1)
        self.assertFalse(journal.exists())
        db = sqlite3.connect('file:' + str(path) + '?mode=ro', uri=True)
        try: self.assertEqual(db.execute("select count(*) from settings where key like 'fill:%' and data like '%U%'")
                              .fetchone()[0], 0)
        finally: db.close()
        self.assertEqual(status()[0], 0)


@POSIX_PROOF
class CreateSchemaTests(Bound):
    """Existing F1 completion: every create-input field outside the scope name domain denies before recovery."""
    hot_slot = FinalWaveTests.hot_slot
    raw = ResidualCorrectionTests.raw
    assert_denied_first = ResidualCorrectionTests.assert_denied_first

    def creator(self, handle):
        slot, cap = self.active(self.scope(), handle=handle)
        if not (self.root / 'config.json').exists(): self.configure()
        self.hot_slot(slot)
        return slot, cap

    def create(self, api, cap, *argv, body=b'{}'):
        from quorum_api import ServiceConfig
        with patch('quorum_profiles.resolve_profile', return_value=(ServiceConfig(ORIGIN, KEY), Store())), \
                patch('quorum.QuorumApi', return_value=api), patch('quorum.token_provider', return_value=lambda: 'jwt'):
            return self.cli(api, 'create', *argv, '--access', 'github', '--json-stdin', '--worker-capability', str(cap),
                            stdin=body)

    def denied_first(self, api, slot, cap, *argv, body=b'{}'):
        self.assertIsNotNone(self.raw(slot)[1], 'a hot journal is present before the case')
        result = self.assert_denied_first(api, slot, lambda: self.create(api, cap, *argv, body=body))
        self.assertIsNotNone(self.raw(slot)[1])
        return result

    @isolated_profiles
    def test_exact_reviewer_probes_deny_before_recovery(self):
        api = Api()
        for index, (argv, body) in enumerate(((('team',), b'{"role": 7}'), (('team',), b'{"role": null}'),
                                               (('team',), b'{"role": "Bad Role"}'), ((), b'{}'))):
            slot, cap = self.creator('probe-%d' % index)
            self.denied_first(api, slot, cap, *argv, body=body)
        self.assertEqual(api.calls, [])

    @isolated_profiles
    def test_create_input_domain_matrix_denies_before_recovery(self):
        api = Api()
        slot, cap = self.creator('matrix')
        invalid = (None, True, False, 7, 1.5, [], ['team'], {}, {'a': 'b'}, '', ' ', ' team', 'team ', 'Bad Role',
                   'Team', 'ünicode', '-team', '7team', 'team_x', 'x' * 49, 'team\n')
        for value in invalid:
            for field in ('role', 'slug'):
                self.denied_first(api, slot, cap, 'team', body=json.dumps({field: value}).encode())
        # A leading '-' is an argparse option, refused before the CLI runs; '7team' covers the bad first character.
        for positional in ('', 'Team', '7team', 'x' * 49, 'bad slug'):
            self.denied_first(api, slot, cap, positional, body=b'{}')
        self.denied_first(api, slot, cap, body=b'{"slug": "team"}')
        for role in ('', 'Bad Role', 'Team', 'x' * 49):
            self.denied_first(api, slot, cap, 'team', '--role', role, body=b'{}')
        self.assertEqual(api.calls, [])

    @isolated_profiles
    def test_valid_create_inputs_reach_record_checks_and_succeed(self):
        import quorum_claude_worker as worker
        api = Api()
        # A valid but different approved value is record-dependent: recovery may precede its denial.
        for index, (argv, body) in enumerate(((('team',), b'{"role": "other-role"}'), (('other',), b'{}'),
                                               (('team', '--role', 'other-role'), b'{}'))):
            slot, cap = self.creator('different-%d' % index)
            code, result, text = self.create(api, cap, *argv, body=body)
            self.assertEqual((code, result['data'].get('reason')), (3, 'scope_mismatch'), text)
            self.assertIsNone(self.raw(slot)[1], 'recovered before the record-dependent denial')
        self.assertEqual(api.calls, [])
        # Omitted fields and a valid role without --role succeed; stdin is parsed exactly once.
        real = worker.create_input
        for index, body in enumerate((b'{}', b'{"role": "service-worker"}', b'{"slug": "team", "role": "service-worker"}')):
            slot, cap = self.creator('valid-%d' % index)
            with patch('quorum_claude_worker.create_input', side_effect=real) as parsed:
                code, result, text = self.create(api, cap, 'team', body=body)
            self.assertEqual((code, result['data']['quorum_id']), (0, QUORUM), text)
            self.assertEqual(parsed.call_count, 1)
        self.assertEqual([operation for operation, *_ in api.calls], ['quorum_create'] * 3)


@POSIX_PROOF
class RpcCreateContractTests(Bound):
    """Worker create encodes the checked public.quorum_create(p_slug, p_request_id) contract on the real API."""

    @isolated_profiles
    def test_worker_create_encodes_only_the_checked_sql_signature(self):
        import quorum_api
        from quorum_api import QuorumError, ServiceConfig
        sent, lost = [], ['once']
        def capture(url, headers, body):
            # The real QuorumApi encoding, intercepted locally: no network.
            sent.append((url, json.loads(body)))
            if url.endswith('/rpc/session_requirements'): return installed()
            if lost: lost.pop(); raise QuorumError('service_unavailable')
            return {'quorum_id': QUORUM, 'slug': 'team'}
        real = lambda config, token: quorum_api.QuorumApi(config, token, request=capture)
        slot, cap = self.active(self.scope())
        self.configure()
        def create():
            with patch('quorum_profiles.resolve_profile', return_value=(ServiceConfig(ORIGIN, KEY), Store())), \
                    patch('quorum.QuorumApi', side_effect=real), patch('quorum.token_provider', return_value=lambda: 'jwt'):
                return self.cli(None, 'create', 'team', '--access', 'github', '--json-stdin', '--worker-capability',
                                str(cap), stdin=b'{"slug": "team", "role": "service-worker"}')
        code, result, _ = create()
        self.assertEqual((code, result['code']), (5, 'service_unavailable'))
        code, result, text = create()
        self.assertEqual((code, result['data']['quorum_id']), (0, QUORUM), text)
        creates = [body for url, body in sent if url == ORIGIN + '/rest/v1/rpc/quorum_create']
        self.assertEqual(len(creates), 2)
        request_id = self.setting(slot)['create_request_id']
        for body in creates:
            self.assertEqual(set(body), sql_signature('quorum_create'), 'exactly the checked SQL arguments')
            self.assertEqual(body, {'p_slug': 'team', 'p_request_id': request_id})
            self.assertNotIn('p_role', body)
        # The approved role stays local scope and is applied at connect/agent registration.
        api = Api()
        code, connected, text = self.connect(api, cap)
        self.assertEqual(code, 0, text)
        join = [payload for operation, payload, _ in api.calls if operation == 'agent_join']
        self.assertEqual([entry['role'] for entry in join], ['service-worker'])

    def test_strict_fake_rejects_unsupported_create_arguments(self):
        from quorum_api import QuorumError
        api = Api()
        with self.assertRaises(QuorumError):
            api.call('quorum_create', {'slug': 'team', 'role': 'service-worker'}, 'r')
        self.assertEqual(api.call('quorum_create', {'slug': 'team'}, 'r')['quorum_id'], QUORUM)

SKILL = Path(__file__).resolve().parents[1] / 'skills/quorum'


def prompts():
    from quorum_api import ServiceConfig
    from quorum_invitations import create_id_invitation, create_open_invitation
    class Invitations:
        def call(self, operation, payload, request_id=None):
            if operation == 'quorum_lookup': return {'quorum_id': 'q'}
            if operation == 'quorum_check_owner': return True
            return {'invite_id': INVITE, 'expires_at': '2026-10-23T12:00:00Z'}
    config = ServiceConfig(ORIGIN, KEY)
    github = create_id_invitation(Invitations(), config, 'team', '123', 'github')
    anonymous = create_open_invitation(Invitations(), config, 'team', 'open')
    return [('github', github['session_prompt']), ('configured', github['configured_machine_prompt']),
            ('anonymous', anonymous['session_prompt'])]


class PromptTests(unittest.TestCase):
    def reference(self, raw=False):
        path = SKILL / 'references/claude-worker.md'
        self.assertTrue(path.is_file(), 'missing_claude_worker_reference: references/claude-worker.md is absent')
        text = path.read_text()
        return text if raw else ' '.join(text.split())

    def test_claude_prompt_pending_background_attest_is_exclusive_with_parent_fallback(self):
        for name, prompt in prompts():
            with self.subTest(name=name):
                self.assertIn('references/claude-worker.md', prompt, 'missing_claude_worker_prompt_route')
                self.assertLess(prompt.index('references/claude-worker.md'), prompt.index('helper accept-id'))
                self.assertIn('For a Claude Code parent with a native background worker', prompt)
                self.assertIn('exactly one actual native background', prompt)
                self.assertIn('attests', prompt)
                self.assertIn('session --worker-capability <capability-file> connect', prompt)
                self.assertIn('the parent fallback below then does not apply', prompt)
                self.assertIn('Parent-native fallback, only after I explicitly choose visible parent reception', prompt)
                self.assertIn('--require-worker-inbox', prompt)
                self.assertNotIn('Claude and other clients have no worker inbox', prompt)
        text = self.reference()
        for required in ('session worker prepare --json-stdin', 'session worker attest <slot-uuid> --native-handle',
                         'session worker await-binding <slot-uuid>', 'exactly one actual native background',
                         'Pending has no server, membership or receive right', '## Parent fallback',
                         'session --session-id claude-worker:<actor-uuid> status',
                         'exclusive'):
            self.assertIn(required, text)

    def test_claude_create_and_join_commands_use_capability_file_not_token(self):
        text = self.reference()
        for required in ('create <slug> --access github --json-stdin --worker-capability <capability-file>',
                         'accept-id <invite-id> --transport tool-inbox --worker-capability <capability-file>',
                         'session --worker-capability <capability-file> connect <slug>',
                         'session --worker-capability <capability-file> receive --wait-seconds 30',
                         'never in arguments, output or chat'):
            self.assertIn(required, text)
        commands = [line for line in self.reference(raw=True).splitlines() if line.startswith('Q ') or 'quorum.py' in line]
        self.assertTrue(commands)
        for line in commands:
            self.assertNotIn('token', line.lower())
            self.assertNotIn('--request-id', line)
        for required in ('`lifetime: "bounded"`', '900', 'until-owner-stop', 'not evidence that the provider keeps',
                         'no renewal', 'regrant', 'same OS user'):
            self.assertIn(required, text)
        for forbidden_claim in ('perpetual', 'guaranteed', 'instantly revoke'):
            self.assertNotIn(forbidden_claim, text.lower())

    def test_missing_background_support_reports_once_without_replacement(self):
        text = self.reference()
        for required in ('report that once', 'no replacement model, session or headless handler',
                         'native_worker_unavailable', 'never as a worker inbox'):
            self.assertIn(required, text)
        evals = json.loads((SKILL / 'evals/evals.json').read_text())['evals']
        claude_eval = [item for item in evals if 'In Claude Code' in item['prompt']]
        self.assertEqual(len(claude_eval), 1)
        self.assertIn('claude-worker.md', claude_eval[0]['expected_output'])
        self.assertIn('parent', claude_eval[0]['expected_output'])
        for reference in ('SKILL.md', 'references/service-agent.md', 'references/native-inbox.md'):
            body = (SKILL / reference).read_text()
            self.assertIn('claude-worker.md', body, reference)
            self.assertNotIn('Claude and other clients have no worker inbox yet', body, reference)
        description = (SKILL / 'SKILL.md').read_text().split('---')[1]
        self.assertNotIn('worker-capability', description)


if __name__ == '__main__':
    unittest.main()
