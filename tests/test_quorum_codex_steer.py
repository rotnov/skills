"""Urgent Codex steering tests. They never start a model, a chat or a server.

A fixture app server speaks the real newline-delimited JSON-RPC framing, so the
request order, the expectedTurnId precondition and the rejection/ambiguity rules
are exercised end to end inside the transport. A fixture is not evidence that an
owner's actual loaded chat was steered.
"""
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import uuid

SCRIPTS = Path(__file__).resolve().parents[1] / 'skills/quorum/scripts'
sys.path.insert(0, str(SCRIPTS))
SID = '11111111-1111-4111-8111-111111111111'
MID = '22222222-2222-4222-8222-222222222222'
TURN = 'turn_0199a000-0000-7000-8000-000000000001'
OTHER_TURN = 'turn_0199a000-0000-7000-8000-000000000002'

FAKE_SERVER = '''
import json, os, sys

SCENARIO = os.environ['FAKE_SCENARIO']
THREAD = os.environ['FAKE_THREAD']
CWD = os.environ['FAKE_CWD']
TURN = os.environ['FAKE_TURN']
OTHER_TURN = os.environ['FAKE_OTHER_TURN']
LOG = os.environ['FAKE_LOG']
steered = False

def emit(frame):
    sys.stdout.write(json.dumps(frame) + '\\n')
    sys.stdout.flush()

if SCENARIO == 'dead':
    raise SystemExit(0)

for line in sys.stdin:
    try: frame = json.loads(line)
    except ValueError: continue
    with open(LOG, 'a', encoding='utf-8') as handle:
        handle.write(json.dumps(frame) + '\\n')
    ref, method, params = frame.get('id'), frame.get('method'), frame.get('params') or {}
    if ref is None: continue
    if method == 'initialize':
        emit({'id': ref, 'result': {'userAgent': 'fixture'}})
    elif method == 'thread/loaded/list':
        listed = [] if SCENARIO == 'not_loaded' else [THREAD]
        emit({'id': ref, 'result': {'data': listed, 'nextCursor': None}})
    elif method == 'thread/read':
        status = {'type': 'idle'} if SCENARIO == 'idle' else {'type': 'active', 'activeFlags': []}
        cwd = os.path.join(CWD, 'elsewhere') if SCENARIO == 'cwd_mismatch' else CWD
        emit({'id': ref, 'result': {'thread': {'id': THREAD, 'cwd': cwd, 'status': status}}})
    elif method == 'thread/turns/list':
        current = OTHER_TURN if (steered and SCENARIO == 'cas_mismatch') else TURN
        emit({'id': ref, 'result': {'data': [{'id': current, 'status': 'inProgress', 'items': []}]}})
    elif method == 'turn/steer':
        steered = True
        if SCENARIO == 'ambiguous': raise SystemExit(0)
        if SCENARIO == 'not_steerable':
            emit({'id': ref, 'error': {'code': -32602, 'message': 'refused',
                                       'data': {'activeTurnNotSteerable': {'turnKind': 'review'}}}})
        elif SCENARIO in ('cas_mismatch', 'opaque_error'):
            emit({'id': ref, 'error': {'code': -32602, 'message': 'refused'}})
        else:
            emit({'id': ref, 'result': {'turnId': params['expectedTurnId']}})
    else:
        emit({'id': ref, 'error': {'code': -32601, 'message': 'unknown'}})
'''


#: The suite may run inside another client's chat; never inherit that binding.
CLIENT_SESSION_VARIABLES = ('CLAUDE_CODE_SESSION_ID', 'CLAUDE_CODE_MESSAGING_SOCKET',
                            'CLAUDE_CODE_MESSAGING_TOKEN', 'GROK_SESSION_ID', 'GROK_LEADER_SOCKET')


class SteerFixture:
    """A fixture daemon plus the environment one existing Codex chat would have."""

    def __init__(self, stack, scenario):
        root = Path(stack.enter_context(tempfile.TemporaryDirectory())).resolve()
        self.cwd, self.log = str(root), root / 'frames.log'
        script = root / 'fake_app_server.py'
        script.write_text(FAKE_SERVER, encoding='utf-8')
        environment = {key: value for key, value in os.environ.items()
                       if key not in CLIENT_SESSION_VARIABLES}
        environment.update({'FAKE_SCENARIO': scenario, 'FAKE_THREAD': SID, 'FAKE_CWD': self.cwd,
                            'FAKE_TURN': TURN, 'FAKE_OTHER_TURN': OTHER_TURN,
                            'FAKE_LOG': str(self.log), 'CODEX_THREAD_ID': SID})
        stack.enter_context(patch.dict(os.environ, environment, clear=True))
        import quorum_codex_steer
        stack.enter_context(patch.object(quorum_codex_steer, 'codex_command',
                                         lambda: [sys.executable, '-I', str(script)]))
        self.record = {'engine': 'codex', 'session_ref': SID, 'cwd': self.cwd, 'quorum': 'team'}

    def frames(self):
        if not self.log.exists(): return []
        return [json.loads(line) for line in self.log.read_text(encoding='utf-8').splitlines() if line]

    def methods(self):
        return [frame.get('method') for frame in self.frames()]

    def steer_params(self):
        return [frame['params'] for frame in self.frames() if frame.get('method') == 'turn/steer']


class CodexSteerTests(unittest.TestCase):
    def fixture(self, scenario):
        import contextlib
        stack = contextlib.ExitStack()
        self.addCleanup(stack.close)
        return SteerFixture(stack, scenario)

    def submit(self, fixture, text='peer prompt'):
        from quorum_codex_steer import submit
        return submit(fixture.record, MID, lambda: text, timeout=20)

    def test_active_turn_is_steered_with_the_expected_turn_precondition(self):
        fixture = self.fixture('steerable')
        self.assertEqual(self.submit(fixture), 'submitted')
        self.assertEqual(fixture.methods()[:4], ['initialize', 'initialized', 'thread/loaded/list', 'thread/read'])
        params = fixture.steer_params()
        self.assertEqual(len(params), 1)
        self.assertEqual(params[0]['threadId'], SID)
        self.assertEqual(params[0]['expectedTurnId'], TURN)
        self.assertEqual(params[0]['input'], [{'type': 'text', 'text': 'peer prompt'}])
        self.assertEqual(params[0]['clientUserMessageId'], 'quorum:' + MID)

    def test_an_idle_chat_is_never_steered(self):
        fixture = self.fixture('idle')
        from quorum_codex_steer import SteerUnavailable
        with self.assertRaises(SteerUnavailable) as caught: self.submit(fixture)
        self.assertEqual(caught.exception.reason, 'no_active_turn')
        self.assertNotIn('turn/steer', fixture.methods())

    def test_a_chat_this_daemon_does_not_serve_is_never_steered(self):
        fixture = self.fixture('not_loaded')
        from quorum_codex_steer import SteerUnavailable
        with self.assertRaises(SteerUnavailable) as caught: self.submit(fixture)
        self.assertEqual(caught.exception.reason, 'thread_not_loaded')
        self.assertNotIn('turn/steer', fixture.methods())

    def test_an_unreachable_daemon_reports_nothing_submitted(self):
        fixture = self.fixture('dead')
        from quorum_codex_steer import SteerUnavailable
        with self.assertRaises(SteerUnavailable) as caught: self.submit(fixture)
        self.assertEqual(caught.exception.reason, 'daemon_unreachable')
        self.assertEqual(fixture.methods(), [])

    def test_a_daemon_view_of_another_directory_refuses_to_steer(self):
        fixture = self.fixture('cwd_mismatch')
        from quorum_codex_steer import SteerUnavailable
        with self.assertRaises(SteerUnavailable) as caught: self.submit(fixture)
        self.assertEqual(caught.exception.reason, 'thread_directory_mismatch')
        self.assertNotIn('turn/steer', fixture.methods())

    def test_a_turn_that_cannot_accept_steering_is_a_rejection(self):
        fixture = self.fixture('not_steerable')
        self.assertEqual(self.submit(fixture), 'rejected')
        self.assertEqual(len(fixture.steer_params()), 1)

    def test_a_lost_precondition_race_is_a_rejection_proved_by_the_running_turn(self):
        fixture = self.fixture('cas_mismatch')
        self.assertEqual(self.submit(fixture), 'rejected')
        self.assertEqual(fixture.methods().count('turn/steer'), 1)
        # The rejection is proved by re-reading the running turn, not by a code.
        self.assertEqual(fixture.methods()[-1], 'thread/turns/list')

    def test_an_opaque_error_while_that_turn_still_runs_stays_unknown(self):
        fixture = self.fixture('opaque_error')
        with self.assertRaisesRegex(ValueError, 'session_delivery_unknown'): self.submit(fixture)
        self.assertEqual(fixture.methods().count('turn/steer'), 1)

    def test_a_lost_steering_response_stays_unknown_without_replaying(self):
        fixture = self.fixture('ambiguous')
        with self.assertRaisesRegex(ValueError, 'session_delivery_unknown'): self.submit(fixture)
        self.assertEqual(fixture.methods().count('turn/steer'), 1)

    def test_another_chats_caller_cannot_steer_this_binding(self):
        fixture = self.fixture('steerable')
        other = str(uuid.uuid4())
        with patch.dict(os.environ, {'CODEX_THREAD_ID': other}):
            from quorum_session import require_caller
            with self.assertRaisesRegex(ValueError, 'session_binding_denied'):
                require_caller(fixture.record['session_ref'], 'codex')

    def test_oversized_steering_text_is_refused_before_any_write(self):
        fixture = self.fixture('steerable')
        with self.assertRaisesRegex(ValueError, 'invalid_message'):
            self.submit(fixture, 'x' * 48001)
        self.assertNotIn('turn/steer', fixture.methods())

    def test_only_the_running_daemon_is_attached_never_a_new_server(self):
        from quorum_codex_steer import codex_command
        with patch('shutil.which', lambda name: 'C:/codex' if name == 'codex' else None):
            self.assertEqual(codex_command(), ['C:/codex', 'app-server', 'proxy'])


if __name__ == '__main__':
    unittest.main()
