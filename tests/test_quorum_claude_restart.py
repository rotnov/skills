"""A Claude Code restart under the same session ID (#165 stage 2): stop plainly, then one same-chat recover.

The restarted client's session record names its new pipe but never its token, which exists only in the restarted
client's environment. So the old listener never reattaches: it stops at once and names the restart, and the
restarted chat's own `session recover` adopts the new pipe. Real private Unix sockets, the real pipe helper and
synthetic session records in a temporary CLAUDE_CONFIG_DIR. No Claude process, model or service runs.
"""
import hang_guard  # noqa: F401
import json
import os
from pathlib import Path
import socket
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from quorum_test_support import isolated_profiles
from test_quorum_session import without_client_session

SCRIPTS = Path(__file__).resolve().parents[1] / 'skills/quorum/scripts'
sys.path.insert(0, str(SCRIPTS))
SID = '11111111-1111-4111-8111-111111111111'
OTHER = '99999999-9999-4999-8999-999999999999'
FIRST = '22222222-2222-4222-8222-222222222222'
SECOND = '33333333-3333-4333-8333-333333333333'
OLD_TOKEN, NEW_TOKEN = 'synthetic-old-token', 'synthetic-new-token'


class Api:
    """A synthetic service: one inbox, and every other call recorded and accepted."""
    def __init__(self):
        self.inbox, self.calls = [], []

    def call(self, operation, payload, request_id=None):
        if operation == 'session_requirements': return {'protocol_version':2, 'skill_version':'0.3.0'}
        self.calls.append(operation)
        if operation == 'session_inbox': return list(self.inbox)
        if operation == 'session_release_status': return {'target':None, 'agents':[]}
        return {'recorded':True}


def message(message_id):
    return {'id':message_id, 'recipient_id':'agent', 'sender_role':'peer', 'reply_to':None, 'body':'synthetic'}


@unittest.skipUnless(os.name == 'posix' and hasattr(socket, 'AF_UNIX'), 'private Unix socket route')
class ClaudeRestartTests(unittest.TestCase):
    def setUp(self):
        env = patch.dict(os.environ, without_client_session(), clear=True)
        env.start()
        self.addCleanup(env.stop)
        temporary = tempfile.TemporaryDirectory(prefix='qrestart-', dir='/tmp')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        os.chmod(self.root, 0o700)
        self.home = self.root / 'claude'
        (self.home / 'sessions').mkdir(parents=True, mode=0o700)
        os.environ['CLAUDE_CONFIG_DIR'] = str(self.home)
        from quorum_state import LocalLedger
        self.ledger = LocalLedger(self.root / 'sessions' / SID)
        self.addCleanup(self.ledger.db.close)
        self.api, self.frames, self.runs = Api(), {}, []
        self.old_socket, self.new_socket = str(self.root / 'old.sock'), str(self.root / 'new.sock')
        registration = {'engine':'claude', 'session_ref':SID, 'project_ref':'project', 'cwd':str(self.root)}
        self.record = {**registration, 'registration':registration, 'agent_id':'agent', 'device_id':'device',
                       'quorum_id':'quorum', 'role':'claude-test', 'quorum':'team',
                       'messaging_socket':self.old_socket}
        self.ledger.setting('session-agent', self.record)
        from quorum_resume import enable
        enable(self.ledger, self.record)  # The owner's earlier explicit start.
        self.servers = {}
        self.serve(self.old_socket)
        self.session_record(100, self.old_socket)
        self.chat(self.old_socket, OLD_TOKEN)

    # --- the synthetic client ------------------------------------------------------------------------

    def serve(self, path):
        """A private socket that records each connection's auth and user frames, as the client reads them."""
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        server.bind(path)
        os.chmod(path, 0o600)
        server.listen(4)
        server.settimeout(.2)
        self.frames[path], stopped = [], threading.Event()
        def accept():
            while not stopped.is_set():
                try: connection = server.accept()[0]
                except OSError: continue
                with connection, connection.makefile('rb') as stream:
                    connection.settimeout(3)
                    self.frames[path].append([json.loads(stream.readline()) for _ in range(2)])
        thread = threading.Thread(target=accept, daemon=True)
        thread.start()
        def close():
            stopped.set()
            thread.join(timeout=5)
            server.close()
        self.servers[path] = close
        self.addCleanup(close)

    def session_record(self, pid, path, *, session=SID, protocol=1):
        record = {'pid':pid, 'sessionId':session, 'version':'2.1.312', 'peerProtocol':protocol, 'kind':'interactive',
                  'messagingSocketPath':path}
        (self.home / 'sessions' / (str(pid) + '.json')).write_text(json.dumps(record))

    def chat(self, path, token, session=SID):
        """The exports a tool call in this chat inherits."""
        os.environ.update({'CLAUDE_CODE_SESSION_ID':session, 'CLAUDE_CODE_MESSAGING_SOCKET':path,
                           'CLAUDE_CODE_MESSAGING_TOKEN':token})

    def restart(self, *, session=SID, protocol=1):
        """Claude Code restarts the chat: the old process, pipe and record go; a new pipe and record appear."""
        self.servers.pop(self.old_socket)()
        os.unlink(self.old_socket)
        (self.home / 'sessions' / '100.json').unlink()
        self.serve(self.new_socket)
        self.session_record(200, self.new_socket, session=session, protocol=protocol)

    def delivered(self, path):
        return [(auth['token'], user['msg_id']) for auth, user in self.frames[path]]

    # --- the listener, as `session run` wires it ----------------------------------------------------

    def relay(self, ledger, record, *, recovering=False):
        from quorum_resume import require_enabled
        from quorum_session import Relay, check_client_contract, enqueue, listener_route
        options = {'authorize':lambda:require_enabled(ledger, record)} if recovering else {}
        return Relay(self.api, ledger, record, lambda item:enqueue(record, item, self.root),
                     route_check=listener_route(record), contract=lambda:check_client_contract(record), **options)

    def listen(self, run_id='44444444-4444-4444-8444-444444444444', *, sleep=lambda _:time.sleep(.01)):
        from quorum_listener import run_listener
        return run_listener(self.ledger, lambda:self.relay(self.ledger, self.record), run_id, sleep=sleep,
                            timeout=0, retry=True)

    def spawn(self, command, ledger):
        """A recovered `session run`: its own ledger connection, the rebound record and this chat's exports."""
        from quorum_listener import Control, run_listener
        from quorum_state import LocalLedger
        run_id = command[command.index('--run-id') + 1]
        def body():
            own = LocalLedger(ledger.root)
            try:
                record = own.setting('session-agent')
                run_listener(own, lambda:self.relay(own, record, recovering='--recovering' in command), run_id,
                             sleep=lambda _:time.sleep(.01), timeout=0, retry=True)
            finally: own.db.close()
        thread = threading.Thread(target=body, daemon=True)
        thread.start()
        def close():
            if thread.is_alive(): Control(self.ledger).request_stop(run_id)
            thread.join(timeout=10)
        self.runs.append(close)
        self.addCleanup(close)
        return SimpleNamespace(poll=lambda:None if thread.is_alive() else 0, terminate=close, kill=close,
                               wait=lambda **_:close())

    def dispatch(self, action):
        from quorum_session import dispatch
        with patch('quorum_live.api_for', return_value=(self.api, None, None)), \
             patch('quorum_session.Path.cwd', return_value=self.root), \
             patch('quorum_session.spawn_background', side_effect=self.spawn):
            return dispatch(SimpleNamespace(action=action, session_id=SID), SimpleNamespace(root=self.root), None)

    def first_delivered_before_restart(self):
        """FIRST reaches the old chat, which reads it; the service then stops listing it."""
        self.api.inbox = [message(FIRST)]
        self.relay(self.ledger, self.record).run_once()
        self.assertEqual(self.delivered(self.old_socket), [(OLD_TOKEN, FIRST)])
        self.ledger.setting('delivery:' + FIRST, {'state':'acked'})
        self.api.inbox = []

    def lost_listener(self):
        """Mid-restart, the old run checks its route before reading the inbox and stops."""
        self.api.inbox = [message(SECOND)]
        self.api.calls.clear()
        return self.listen()

    # --- tests ---------------------------------------------------------------------------------------

    @isolated_profiles
    def test_same_session_restart_stops_once_and_one_recover_delivers_each_message_once(self):
        from quorum_resume import listener_loss
        self.first_delivered_before_restart()
        self.restart()
        status = self.lost_listener()
        self.assertEqual((status['listener'], status['state'], status['code'], status['reason']),
                         ('offline', 'error', 'claude_pipe_unavailable', 'client_restarted'))
        self.assertNotIn('session_inbox', self.api.calls)
        self.assertIsNone(self.ledger.setting('delivery:' + SECOND))
        self.assertEqual(self.delivered(self.new_socket), [], 'the stale token never reaches the new pipe')

        # The restarted chat's first quorum command says reception is down and gives the one command.
        self.chat(self.new_socket, NEW_TOKEN)
        lost = self.dispatch('status')['listener_lost']
        self.assertEqual((lost['code'], lost['reason']), ('claude_pipe_unavailable', 'client_restarted'))
        step = lost['next_step']
        self.assertTrue(step.startswith('Quorum reception is down in this chat.'))
        self.assertIn('Claude Code restarted this chat under the same session ID', step)
        self.assertIn('cd ' + str(self.root) + ' && uv run --project ', step)
        self.assertIn(' session recover', step)
        for secret in (OLD_TOKEN, NEW_TOKEN): self.assertNotIn(secret, json.dumps(lost))

        recovered = self.dispatch('recover')
        self.assertEqual((recovered['listener'], recovered['messaging_rebound']), ('running', True))
        self.assertEqual(self.ledger.setting('session-agent'), {**self.record, 'messaging_socket':self.new_socket})
        deadline = time.monotonic() + 20
        while not self.delivered(self.new_socket) and time.monotonic() < deadline: time.sleep(.05)
        self.assertEqual(self.delivered(self.new_socket), [(NEW_TOKEN, SECOND)])
        self.assertEqual(self.delivered(self.old_socket), [(OLD_TOKEN, FIRST)], 'nothing is replayed')
        self.assertEqual(self.ledger.setting('delivery:' + SECOND), {'state':'submitted'})
        self.assertIsNone(listener_loss(self.ledger))
        # A repeated recover reuses the live run: no second delivery.
        self.assertEqual(self.dispatch('recover')['run_id'], recovered['run_id'])
        for close in self.runs: close()
        self.assertEqual(self.delivered(self.new_socket), [(NEW_TOKEN, SECOND)])

    @isolated_profiles
    def test_another_session_at_the_new_pipe_is_no_restart_and_cannot_recover(self):
        from quorum_client_contracts import claude_endpoint_rotated
        self.restart(session=OTHER)
        (self.home / 'sessions' / '300.json').write_text('{"sessionId": ')  # Unreadable records are skipped.
        status = self.lost_listener()
        self.assertEqual((status['code'], status.get('reason')), ('claude_pipe_unavailable', None))
        self.assertFalse(claude_endpoint_rotated(SID, self.old_socket))
        self.assertTrue(claude_endpoint_rotated(OTHER, self.old_socket))
        self.chat(self.new_socket, NEW_TOKEN, session=OTHER)
        with self.assertRaisesRegex(ValueError, 'session_binding_denied'): self.dispatch('recover')
        # The notice for a gone pipe with no restart evidence still names the one command for this chat.
        self.chat(self.new_socket, NEW_TOKEN)
        step = self.dispatch('status')['listener_lost']['next_step']
        self.assertIn('messaging socket is gone', step)
        self.assertIn(' session recover', step)
        self.assertEqual(self.ledger.setting('session-agent'), self.record)
        self.assertEqual((self.delivered(self.new_socket), self.runs), ([], []))
        self.assertIsNone(self.ledger.setting('delivery:' + SECOND))

    @isolated_profiles
    def test_a_changed_client_contract_refuses_recovery_before_any_write(self):
        from quorum_client_contracts import CLAUDE_PIPE_CHANGED, ClientContractRefused
        self.restart(protocol=2)
        self.assertEqual(self.lost_listener()['reason'], 'client_restarted')
        self.chat(self.new_socket, NEW_TOKEN)
        with self.assertRaises(ClientContractRefused) as refused: self.dispatch('recover')
        self.assertEqual((refused.exception.code, refused.exception.reason),
                         (CLAUDE_PIPE_CHANGED, 'peer_protocol_changed'))
        self.assertEqual(self.ledger.setting('session-agent'), self.record)
        self.assertEqual((self.delivered(self.new_socket), self.runs), ([], []))
        self.assertIsNone(self.ledger.setting('delivery:' + SECOND))

    @isolated_profiles
    def test_a_gone_route_is_terminal_at_once_with_no_wait_window(self):
        """The new pipe cannot be reattached, so waiting would only delay the chat's own recover."""
        self.restart()
        status = self.listen(sleep=lambda _:self.fail('a gone Claude route never waits'))
        self.assertEqual((status['state'], status['code']), ('error', 'claude_pipe_unavailable'))
        self.assertIsNone(self.ledger.setting('presence'), 'no degraded presence or wait')
        # Even after the route returns, the stopped run stays stopped: only the chat's command restarts reception.
        self.assertEqual(self.dispatch('status')['listener'], 'offline')

    @isolated_profiles
    def test_an_owner_stop_after_the_loss_wins_over_the_notice(self):
        from quorum_resume import listener_loss
        self.restart()
        self.lost_listener()
        self.chat(self.new_socket, NEW_TOKEN)
        self.assertIn('listener_lost', self.dispatch('status'))
        self.dispatch('stop')
        self.assertIsNone(listener_loss(self.ledger))
        self.assertNotIn('listener_lost', self.dispatch('status'))
        with self.assertRaisesRegex(ValueError, 'session_recovery_denied'): self.dispatch('recover')
        self.assertEqual(self.ledger.setting('session-reception'), {'enabled':False})
        self.assertEqual((self.delivered(self.new_socket), self.runs), ([], []))
        self.assertIsNone(self.ledger.setting('delivery:' + SECOND))

    def test_windows_notice_names_the_directory_instead_of_a_cd(self):
        from quorum_resume import claude_restart_notice
        loss = {'quorum':'team', 'role':'claude-test', 'reason':'client_restarted', 'cwd':'C:\\work'}
        step = claude_restart_notice(loss, self.root, windows=True)
        self.assertIn('in the working directory "C:\\\\work"', step)
        self.assertNotIn('cd ', step)
        posix = claude_restart_notice({**loss, 'cwd':'/work dir'}, self.root, windows=False)
        self.assertIn("cd '/work dir' && uv run", posix)


if __name__ == '__main__':
    unittest.main()
