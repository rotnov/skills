"""A client update must stop reception with a fixed code, never deliver blind.

These fixtures record what is known of each client. They never run Codex, Claude Code or a model. The
`turn/steer` server cases live with its fake app server in test_quorum_codex_steer.py.
"""
import hang_guard  # noqa: F401
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / 'skills/quorum/scripts'
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from quorum_test_support import CONTRACT_FIXTURES, codex_child

import quorum_client_contracts as contracts
from quorum_client_contracts import ClientContractRefused, ClientOutputChanged
from quorum_delivery import Deliveries, DeliveryUnsent
from quorum_listener import Control, run_listener
from quorum_session import Relay, check_client_contract, enqueue, shim_command
from quorum_state import LocalLedger

SID = '11111111-1111-4111-8111-111111111111'
MID = '22222222-2222-4222-8222-222222222222'
CONVERSATION = '66666666-6666-4666-8666-666666666666'
SOCKET = '/private/fixture/messaging.sock'
QUEUE = CONTRACT_FIXTURES['codex_queue']
ACCEPTED = 'Queued message 01a1b23b-83f8-74e1-b1df-91544014a099 for thread ' + SID + '.\n'


def done(stdout=b'', stderr=b'', code=0):
    return subprocess.CompletedProcess(['codex'], code, stdout, stderr)


def message():
    return {'id':MID, 'recipient_id':'agent', 'sender_id':'peer-agent', 'sender_role':'peer', 'reply_to':None,
            'priority':'normal', 'conversation_id':CONVERSATION, 'sequence':3, 'body':'peer text'}


def record(engine='codex'):
    return {'engine':engine, 'session_ref':SID, 'agent_id':'agent', 'quorum_id':'q', 'quorum':'team',
            'project_ref':'shared', 'cwd':'/private/fixture', 'role':'test', 'messaging_socket':SOCKET}


class RecordTests(unittest.TestCase):
    def test_every_code_is_safe_to_print_and_explains_the_next_step(self):
        from quorum_api import LOCAL_VALUE_CODES, safe_code
        for code in contracts.CONTRACT_CODES:
            with self.subTest(code):
                self.assertIn(code, LOCAL_VALUE_CODES)
                self.assertEqual(safe_code(ClientContractRefused(code, 'reason')), code)
                self.assertIn('session recover', contracts.NEXT_STEPS[code])

    def test_the_recorded_claude_frames_are_what_the_pipe_writes(self):
        from quorum_claude_pipe import frames
        auth, user = (json.loads(line) for line in frames(SID, MID, 'prompt', 'local-token').splitlines())
        self.assertEqual([auth, user], CONTRACT_FIXTURES['claude_pipe']['frames'])
        self.assertEqual(tuple(auth), contracts.CLAUDE_AUTH_FRAME)
        self.assertEqual(tuple(user), contracts.CLAUDE_USER_FRAME)

    def test_the_recorded_steering_enums_are_the_contract(self):
        recorded = CONTRACT_FIXTURES['turn_steer']
        self.assertEqual(list(contracts.THREAD_STATUSES), recorded['thread_statuses'])
        self.assertEqual(list(contracts.TURN_STATUSES), recorded['turn_statuses'])

    def test_the_recorded_shim_has_the_entry_point_that_is_read(self):
        shim = '\r\n'.join(CONTRACT_FIXTURES['cmd_shim']['lines'])
        self.assertEqual(contracts.SHIM_ENTRY.search(shim)[1], 'node_modules\\@openai\\codex\\bin\\codex.js')

    def test_every_refusal_carries_only_a_fixed_code_reason_and_step(self):
        error = ClientContractRefused(contracts.CODEX_QUEUE_CHANGED, 'queue_arguments_changed')
        self.assertIsInstance(error, DeliveryUnsent)
        self.assertEqual(str(error), contracts.CODEX_QUEUE_CHANGED)
        self.assertEqual(sorted(error.diagnostic()), ['next_step', 'reason'])


class QueueArgumentTests(unittest.TestCase):
    def check(self, result):
        contracts.check_queue(['codex'], lambda *_, **__: result)

    def test_each_recorded_help_passes(self):
        for recorded in QUEUE['help']:
            with self.subTest(recorded['version']):
                self.check(done(recorded['stdout'].encode(), recorded['stderr'].encode(), recorded['returncode']))

    def test_a_newer_client_with_more_options_passes(self):
        self.check(done(b'Usage: codex queue --thread <ID> --message <TEXT> --stdin\n  --priority <P>\n'))

    def test_the_probe_reads_help_through_the_given_prefix_and_never_sends_text(self):
        seen = []
        contracts.check_queue(['node', 'codex.js'], lambda command, **_: seen.append(command) or
                              done(QUEUE['help'][0]['stdout'].encode()))
        self.assertEqual(seen, [['node', 'codex.js', 'queue', '--help']])

    def test_a_recorded_help_without_either_option_is_refused(self):
        for recorded in QUEUE['help']:
            for option, renamed in (('--thread', '--session'), ('--message', '--text')):
                with self.subTest(recorded['version'], option=option), self.assertRaises(ClientContractRefused):
                    self.check(done(recorded['stdout'].replace(option + ' ', renamed + ' ').encode()))

    def test_a_changed_argument_shape_is_refused_as_unsent(self):
        for name, result in (('renamed', done(b'Usage: codex queue --session <ID> --message <TEXT>\n')),
                             ('longer name only', done(b'--thread-id <ID> --message-file <PATH>\n')),
                             ('empty', done()),
                             ('removed subcommand', done(b'', b"error: unrecognized subcommand 'queue'\n", 2))):
            with self.subTest(name), self.assertRaises(ClientContractRefused) as caught: self.check(result)
            self.assertEqual(caught.exception.code, contracts.CODEX_QUEUE_CHANGED)
            self.assertEqual(caught.exception.reason, 'queue_arguments_changed')

    def test_a_probe_that_cannot_finish_is_refused(self):
        for failure in (ValueError('session_delivery_unknown'), DeliveryUnsent()):
            def run(*_, **__): raise failure
            with self.subTest(failure), self.assertRaises(ClientContractRefused) as caught:
                contracts.check_queue(['codex'], run)
            self.assertEqual(caught.exception.reason, 'queue_help_unavailable')


class QueueProbeCacheTests(unittest.TestCase):
    """The help read runs once per listener process and install, not once per message."""

    def prefixes(self, executable, count):
        from quorum_session import codex_queue_prefix
        probes = []
        with patch('quorum_session.run_child', side_effect=codex_child(probes=probes)):
            for _ in range(count): self.assertEqual(codex_queue_prefix(str(executable)), [str(executable)])
        return len(probes)

    def test_one_install_is_read_once_and_again_after_it_changes(self):
        with tempfile.TemporaryDirectory(prefix='qcontract-') as folder:
            executable = Path(folder) / 'codex'
            executable.write_bytes(b'one')
            self.assertEqual(self.prefixes(executable, 3), 1)
            executable.write_bytes(b'updated build')
            self.assertEqual(self.prefixes(executable, 2), 1)

    def test_an_install_whose_identity_cannot_be_read_is_read_every_time(self):
        self.assertEqual(self.prefixes('/synthetic/missing/codex', 2), 2)

    def test_a_refused_install_is_not_remembered(self):
        from quorum_session import codex_queue_prefix
        with tempfile.TemporaryDirectory(prefix='qcontract-') as folder:
            executable = Path(folder) / 'codex'
            executable.write_bytes(b'one')
            with patch('quorum_session.run_child', return_value=done(b'Usage: codex queue --session <ID>\n')):
                for _ in range(2):
                    with self.assertRaises(ClientContractRefused): codex_queue_prefix(str(executable))


class QueueResultTests(unittest.TestCase):
    def check(self, result, thread=SID):
        contracts.check_queue_result(result, thread)

    def test_the_recorded_output_passes(self):
        self.check(done(QUEUE['accepted']['stdout'].encode()), QUEUE['accepted']['thread'])

    def test_either_stream_may_carry_the_line_beside_an_update_note(self):
        self.check(done(b'A new version is available\n' + ACCEPTED.encode()))
        self.check(done(b'', ACCEPTED.encode()))
        self.check(done(ACCEPTED.replace('.\n', '.\r\n').encode(), b'warning: stale config\n'))

    def test_an_exit_0_without_exactly_one_proof_for_this_thread_may_have_queued(self):
        other = ACCEPTED.replace(SID, MID).encode()
        for name, stdout in (('empty', b''), ('old wording', b'queued'), ('truncated', ACCEPTED[:40].encode()),
                             ('no full stop', ACCEPTED.replace('.\n', '\n').encode()),
                             ('other thread', other), ('two proofs', ACCEPTED.encode() * 2),
                             ('embedded', b'note ' + ACCEPTED.encode())):
            with self.subTest(name), self.assertRaises(ClientOutputChanged) as caught: self.check(done(stdout))
            self.assertEqual(str(caught.exception), contracts.CODEX_QUEUE_CHANGED)
            self.assertNotIsInstance(caught.exception, DeliveryUnsent)

    def test_the_recorded_refusal_proves_nothing_was_queued(self):
        refused = QUEUE['refused']
        self.assertEqual(refused['stderr'].strip(), contracts.QUEUE_REFUSED)
        for name, stderr in (('recorded', refused['stderr'].encode()), ('crlf', refused['stderr'].replace('\n', '\r\n').encode())):
            with self.subTest(name), self.assertRaises(ClientContractRefused) as caught:
                self.check(done(refused['stdout'].encode(), stderr, refused['returncode']))
            self.assertEqual(str(caught.exception), contracts.CODEX_THREAD_UNLOADED)

    def test_only_the_exact_recorded_refusal_counts(self):
        line = QUEUE['refused']['stderr'].strip()
        for name, result in (('exit 2', done(b'', line.encode(), 2)), ('stdout too', done(b'x', line.encode(), 1)),
                             ('more stderr', done(b'', (line + '\nnote').encode(), 1)),
                             ('without prefix', done(b'', line.split(': ', 2)[2].encode(), 1)),
                             ('other code', done(b'', line.replace('-32600', '-32601').encode(), 1))):
            with self.subTest(name), self.assertRaisesRegex(ValueError, '^session_queue_failed$'): self.check(result)

    def test_any_other_failure_stays_the_old_unknown(self):
        for result in (done(b'', b'thread/queue/add failed: disk full\n', 1), done(ACCEPTED.encode(), code=1)):
            with self.subTest(result), self.assertRaisesRegex(ValueError, '^session_queue_failed$'): self.check(result)


class ShimTests(unittest.TestCase):
    def test_each_changed_shim_has_the_fixed_code_and_its_own_reason(self):
        lines = CONTRACT_FIXTURES['cmd_shim']['lines']
        for name, text, script, which, reason in (
                ('foreign', '@echo off\r\ncodex.exe %*\r\n', False, 'C:\\node.exe', 'shim_entry_unreadable'),
                ('no entry point', '\r\n'.join(lines), False, 'C:\\node.exe', 'shim_entry_missing'),
                ('no node', '\r\n'.join(lines), True, None, 'shim_node_unusable'),
                ('node shim', '\r\n'.join(lines), True, 'C:\\node.cmd', 'shim_node_unusable')):
            with self.subTest(name), tempfile.TemporaryDirectory(prefix='qcontract-') as folder:
                root = Path(folder)
                (root / 'codex.cmd').write_text(text, encoding='utf-8')
                if script:
                    entry = root / 'node_modules/@openai/codex/bin/codex.js'
                    entry.parent.mkdir(parents=True)
                    entry.touch()
                with patch('quorum_session.shutil.which', return_value=which), \
                     self.assertRaises(ClientContractRefused) as caught:
                    shim_command(str(root / 'codex.cmd'))
                self.assertEqual((caught.exception.code, caught.exception.reason),
                                 (contracts.CODEX_SHIM_CHANGED, reason))


class ClaudeRecordTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory(prefix='qcontract-')
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        (self.root / 'sessions').mkdir()
        environment = patch.dict(os.environ, {'CLAUDE_CONFIG_DIR':folder.name, 'CLAUDE_CODE_EXECPATH':''})
        environment.start()
        self.addCleanup(environment.stop)
        family = patch('quorum_client_contracts.os_family', return_value='posix')
        family.start()
        self.addCleanup(family.stop)

    def write(self, **changed):
        recorded = {**CONTRACT_FIXTURES['claude_pipe']['session_record'], 'messagingSocketPath':SOCKET, **changed}
        recorded = {key: value for key, value in recorded.items() if value is not None}
        (self.root / 'sessions/4242.json').write_text(json.dumps(recorded))

    def check(self):
        contracts.check_claude_session(SID, SOCKET)

    def refusal(self):
        with self.assertRaises(ClientContractRefused) as caught: self.check()
        self.assertEqual(caught.exception.code, contracts.CLAUDE_PIPE_CHANGED)
        return caught.exception.reason

    def test_the_recorded_session_passes(self):
        self.write()
        self.check()

    def test_a_newer_build_that_keeps_the_protocol_passes(self):
        for version in ('2.1.999', '2.2.0', '3.0.0'):
            with self.subTest(version):
                self.write(version=version)
                self.check()

    def test_a_changed_protocol_is_refused_whatever_the_version(self):
        for protocol in (2, 0, '1', True):
            with self.subTest(protocol):
                self.write(peerProtocol=protocol)
                self.assertEqual(self.refusal(), 'peer_protocol_changed')

    def test_a_build_without_a_declared_protocol_is_gated_by_its_version(self):
        self.write(peerProtocol=None, version='2.1.224')
        self.check()
        for version in ('2.2.0', '3.0.0', 'next', None):
            with self.subTest(version):
                self.write(peerProtocol=None, version=version)
                self.assertEqual(self.refusal(), 'version_outside_range')

    def test_the_floor_differs_by_platform(self):
        self.write(version='2.1.223')
        self.assertEqual(self.refusal(), 'version_below_floor')
        self.write(version='2.1.233')
        self.check()
        with patch('quorum_client_contracts.os_family', return_value='nt'):
            self.assertEqual(self.refusal(), 'version_below_floor')
            self.write(version='2.1.234')
            self.check()

    def test_a_build_that_names_no_pipe_is_found_by_session(self):
        self.write(messagingSocketPath=None)
        self.check()

    def test_another_sessions_record_says_nothing_about_this_pipe(self):
        self.write(messagingSocketPath='/private/other.sock', sessionId=MID)
        self.assertEqual(self.refusal(), 'session_record_missing')

    def test_unreadable_entries_are_skipped_without_counting_as_proof(self):
        (self.root / 'sessions/1.json').write_text('{torn')
        (self.root / 'sessions/2.json').write_text('[]')
        (self.root / 'sessions/3.json').write_bytes(b'x' * (contracts.RECORD_LIMIT + 1))
        (self.root / 'sessions/notes.txt').write_text(json.dumps({'messagingSocketPath':SOCKET, 'peerProtocol':1}))
        self.assertEqual(self.refusal(), 'session_record_missing')

    def test_no_record_falls_back_to_the_version_gate_on_the_exported_path(self):
        for path, reason in (('/Users/o/.local/share/claude/versions/2.1.312', None),
                             ('C:\\Users\\o\\.local\\share\\claude\\versions\\2.1.312\\claude.exe', None),
                             ('/Applications/Claude/claude-code/2.1.299/abc/claude.app/Contents/MacOS/claude', None),
                             ('/Users/o/.local/share/claude/versions/2.1.100', 'version_outside_range'),
                             ('/Users/o/.local/share/claude/versions/2.2.0', 'version_outside_range'),
                             ('/usr/local/bin/node', 'session_record_missing')):
            with self.subTest(path), patch.dict(os.environ, {'CLAUDE_CODE_EXECPATH':path}):
                if reason is None: self.check()
                else: self.assertEqual(self.refusal(), reason)

    def test_a_missing_registry_is_refused(self):
        (self.root / 'sessions').rmdir()
        self.assertEqual(self.refusal(), 'session_record_missing')

    def test_a_refused_client_opens_no_pipe_and_removes_nothing_but_the_attempt(self):
        self.write(peerProtocol=2)
        with patch('quorum_session.check_native_route'), \
             patch('quorum_claude_pipe.submit', side_effect=AssertionError('wrote to the pipe')), \
             self.assertRaises(ClientContractRefused):
            enqueue(record('claude'), message(), Path('/private/state'))

    def test_a_matching_client_still_reaches_the_pipe(self):
        self.write()
        with patch('quorum_session.check_native_route'), patch('quorum_claude_pipe.submit') as pipe:
            self.assertEqual(enqueue(record('claude'), message(), Path('/private/state')), 'submitted')
        self.assertEqual(pipe.call_count, 1)


class Api:
    def __init__(self, inbox): self.inbox, self.calls = inbox, []

    def call(self, operation, payload, request_id=None):
        self.calls.append(operation)
        if operation == 'session_requirements': return {'protocol_version':2, 'skill_version':'0.3.0'}
        if operation == 'session_inbox': return self.inbox
        return {}


class ListenerTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory(prefix='qcontract-')
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name).resolve()
        self.ledger = LocalLedger(self.root)
        self.addCleanup(self.ledger.db.close)
        self.ledger.setting('session-reception', {'enabled':True})

    def relay(self, queue, contract=None):
        target = record()
        return Relay(Api([message()]), self.ledger, target, queue, contract=contract)

    def queue(self, child):
        def queued(item):
            with patch('quorum_session.shutil.which', return_value='/synthetic/codex'), \
                 patch('quorum_session.run_child', side_effect=child):
                return enqueue(record(), item, self.root)
        return queued

    def stops(self, relay):
        status = run_listener(self.ledger, lambda: relay, '77777777-7777-4777-8777-777777777777', sleep=lambda _: None)
        self.assertEqual(status['state'], 'error')
        return status

    def test_a_changed_shape_at_listener_start_stops_before_any_message_is_selected(self):
        def changed(): raise ClientContractRefused(contracts.CODEX_QUEUE_CHANGED, 'queue_arguments_changed')
        relay = self.relay(lambda _: self.fail('selected a message'), changed)
        status = self.stops(relay)
        self.assertEqual((status['code'], status['reason']), (contracts.CODEX_QUEUE_CHANGED, 'queue_arguments_changed'))
        self.assertNotIn('session_inbox', relay.api.calls)

    def test_a_shape_that_changes_before_a_delivery_is_unsent_and_sends_no_payload(self):
        seen = []
        def child(command, **_):
            seen.append(command)
            return done(b'Usage: codex queue --session <ID>\n')
        status = self.stops(self.relay(self.queue(child)))
        self.assertEqual(status['code'], contracts.CODEX_QUEUE_CHANGED)
        self.assertEqual(status['reason'], 'queue_arguments_changed')
        self.assertEqual(seen, [['/synthetic/codex', 'queue', '--help']])
        self.assertIsNone(Deliveries(self.ledger).get(MID))  # The next start rereads this message.

    def test_changed_output_stays_an_unknown_attempt_under_its_fixed_code(self):
        status = self.stops(self.relay(self.queue(codex_child(output=b'Message accepted\n'))))
        self.assertEqual((status['code'], status['reason']), (contracts.CODEX_QUEUE_CHANGED, 'queue_output_changed'))
        self.assertEqual(Deliveries(self.ledger).get(MID)['state'], 'intent')

    def test_an_unloaded_thread_is_unsent_and_keeps_the_message(self):
        refused = done(b'', QUEUE['refused']['stderr'].encode(), QUEUE['refused']['returncode'])
        status = self.stops(self.relay(self.queue(codex_child(result=refused))))
        self.assertEqual(status['code'], contracts.CODEX_THREAD_UNLOADED)
        self.assertIsNone(Deliveries(self.ledger).get(MID))

    def test_a_matching_client_delivers_a_pointer(self):
        seen, probes = [], []
        relay = self.relay(self.queue(codex_child(seen, probes=probes)))
        relay.reconcile()
        relay.run_once()
        self.assertEqual(Deliveries(self.ledger).get(MID), {'state':'submitted', 'attempt_id':
                                                             Deliveries(self.ledger).get(MID)['attempt_id']})
        self.assertEqual(([command[1:3] for command in probes], [command[1:3] for command in seen]),
                         ([['queue', '--help']], [['queue', '--thread']]))

    def test_status_names_the_stop(self):
        self.stops(self.relay(lambda _: None, lambda: (_ for _ in ()).throw(
            ClientContractRefused(contracts.CLAUDE_PIPE_CHANGED, 'peer_protocol_changed'))))
        status = Control(self.ledger).status()
        self.assertEqual(contracts.notice(status['code'], status['reason'])['client_contract'], {
            'code':contracts.CLAUDE_PIPE_CHANGED, 'reason':'peer_protocol_changed',
            'next_step':contracts.NEXT_STEPS[contracts.CLAUDE_PIPE_CHANGED]})
        self.assertEqual(contracts.notice('listener_failed'), {})

    def test_a_lost_listener_notice_carries_the_contract_step(self):
        from quorum_resume import loss_notice
        step = loss_notice({'engine':'codex', 'state':'error', 'code':contracts.CODEX_QUEUE_CHANGED}, self.root)['next_step']
        self.assertIn(contracts.NEXT_STEPS[contracts.CODEX_QUEUE_CHANGED], step)
        self.assertNotIn('delivery contract', loss_notice({'engine':'codex', 'state':'error'}, self.root)['next_step'])


class StartCheckTests(unittest.TestCase):
    def test_a_codex_start_reads_the_queue_contract_and_probes_steering(self):
        with patch('quorum_session.shutil.which', return_value='/synthetic/codex'), \
             patch('quorum_session.run_child', side_effect=codex_child()) as child, \
             patch('quorum_codex_steer.probe') as probe:
            check_client_contract(record())
        self.assertEqual(child.call_args.args[0], ['/synthetic/codex', 'queue', '--help'])
        self.assertEqual(probe.call_count, 1)

    def test_a_codex_start_without_the_client_names_it(self):
        with patch('quorum_session.shutil.which', return_value=None), \
             self.assertRaisesRegex(ValueError, '^engine_missing$'):
            check_client_contract(record())

    def test_a_grok_start_keeps_its_own_leader_check(self):
        with patch('quorum_session.shutil.which', side_effect=AssertionError('read a client')):
            check_client_contract(record('grok'))

    def test_the_cli_prints_the_fixed_refusal_and_nothing_else(self):
        import quorum
        def refused(*_): raise ClientContractRefused(contracts.CODEX_STEER_CHANGED, 'method_missing')
        with tempfile.TemporaryDirectory(prefix='qcontract-') as folder, \
             patch('sys.argv', ['quorum.py', '--state', folder, 'session', 'status']), \
             patch('quorum.session_dispatch', side_effect=refused, create=True), \
             patch('quorum_session.dispatch', side_effect=refused), \
             contextlib.redirect_stdout(io.StringIO()) as output:
            code = quorum.main()
        self.assertEqual(code, 3)
        self.assertEqual(json.loads(output.getvalue()), {'ok':False, 'code':contracts.CODEX_STEER_CHANGED, 'data':{
            'reason':'method_missing', 'next_step':contracts.NEXT_STEPS[contracts.CODEX_STEER_CHANGED]}})


class StatusNoticeTests(unittest.TestCase):
    def watch(self, failure):
        from quorum_delivery_status import StatusWatch
        class Ledger:
            def __init__(self): self.rows = {}
            def setting(self, key, value=None):
                if value is not None: self.rows[key] = value
                return self.rows.get(key)
        watch = StatusWatch.__new__(StatusWatch)
        def notify(_): raise failure
        watch.notify = notify
        watch._pending = lambda: [{'message_id':MID}]
        return watch

    def test_a_changed_client_stops_reception_from_a_status_notice_too(self):
        for failure in (ClientContractRefused(contracts.CODEX_QUEUE_CHANGED, 'queue_arguments_changed'),
                        ClientOutputChanged(contracts.CODEX_QUEUE_CHANGED, 'queue_output_changed')):
            with self.subTest(failure), self.assertRaises(type(failure)): self.watch(failure)._deliver()

    def test_any_other_notice_failure_stays_quiet(self):
        self.watch(ValueError('session_queue_failed'))._deliver()

    def test_a_realtime_wait_never_masks_a_refusal(self):
        from quorum_realtime import TERMINAL
        self.assertLessEqual(contracts.CONTRACT_CODES, TERMINAL)


if __name__ == '__main__':
    unittest.main()
