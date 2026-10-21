"""Live-boundary tests use local subprocess fixtures, never model providers."""
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'skills/quorum/scripts'))
import quorum_acp


class LiveBoundaryTests(unittest.TestCase):
    def test_raw_acp_output_requires_matching_successful_prompt_result(self):
        parser = quorum_acp.PromptOutput()
        parser.feed({'id': 'p', 'method': 'session/prompt', 'params': {'sessionId': 's'}})
        parser.feed({'method': 'session/update', 'params': {'sessionId': 's', 'update': {
            'sessionUpdate': 'agent_message_chunk', 'content': {'type': 'text', 'text': 'pong'}}}})
        parser.feed({'id': 'other', 'result': {'stopReason': 'end_turn'}})
        with self.assertRaises(ValueError): parser.result(0)
        parser.feed({'id': 'p', 'result': {'stopReason': 'end_turn'}})
        self.assertEqual(parser.result(0), 'pong')
        with self.assertRaises(ValueError): parser.result(1)

    def test_tool_use_or_failed_stop_never_publishes_answer(self):
        for update in ({'sessionUpdate': 'tool_call', 'toolCallId': 'x'},
                       {'sessionUpdate': 'agent_message_chunk', 'content': {'type': 'text', 'text': 'x' * 16385}}):
            parser = quorum_acp.PromptOutput()
            with self.assertRaises(ValueError):
                parser.feed({'method': 'session/update', 'params': {'update': update}})

    def test_policy_wrapper_removes_native_tools_hooks_and_other_mcp(self):
        wrapper = Path(__file__).resolve().parents[1] / 'skills/quorum/scripts/claude_pilot_proxy.mjs'
        self.assertTrue(wrapper.is_file(), 'missing enforced adapter boundary')
        with tempfile.TemporaryDirectory() as root:
            adapter = Path(root) / 'adapter.mjs'
            adapter.write_text('process.stdin.pipe(process.stdout);')
            msg = {'jsonrpc': '2.0', 'id': 1, 'method': 'session/new', 'params': {
                'cwd': root, 'mcpServers': [{'name': 'bad'}], '_meta': {'claudeCode': {
                    'options': {'tools': ['Bash'], 'maxTurns': 99, 'settingSources': ['user']}}}}}
            proc = subprocess.run(['node', str(wrapper), str(adapter)], input=json.dumps(msg)+'\n',
                                  text=True, capture_output=True, timeout=5)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            out = json.loads(proc.stdout)['params']
            opts = out['_meta']['claudeCode']['options']
            self.assertEqual(opts['tools'], [])
            self.assertEqual(opts['maxTurns'], 1)
            self.assertEqual(opts['settingSources'], [])
            self.assertTrue(opts['settings']['disableAllHooks'])
            self.assertEqual(opts['settings']['apiKeyHelper'], '')
            self.assertEqual(out['mcpServers'], [])

    def test_subscription_probe_rejects_api_key_even_with_paid_plan(self):
        good = {'loggedIn': True, 'authMethod': 'claude.ai', 'apiProvider': 'firstParty',
                'subscriptionType': 'max'}
        self.assertTrue(quorum_acp.subscription_status(good))
        for change in ({'apiKeySource': 'apiKeyHelper'}, {'apiProvider': 'bedrock'},
                       {'loggedIn': False}, {'authMethod': 'api_key'}):
            self.assertFalse(quorum_acp.subscription_status({**good, **change}))

    def test_worker_resolves_new_conversation_from_registered_recipient(self):
        from quorum_state import LocalLedger
        from quorum_live import bind_delivery
        with tempfile.TemporaryDirectory() as root:
            ledger = LocalLedger(Path(root))
            cwd = Path(root) / 'scratch' / 'test'
            cwd.mkdir(parents=True)
            ledger.setting('pilot-device:test', {'device_id': 'd'})
            ledger.setting('pilot-agent', {'quorum_id': 'q', 'device_id': 'd', 'agent_id': 'a',
                'pilot_device_key': 'pilot-device:test', 'pilot_role': 'test',
                'registration': {'engine': 'claude', 'project_ref': 'quorum-pilot/messaging', 'session_ref': 'main', 'cwd': str(cwd)}})
            binding = bind_delivery(ledger, {'conversation_id': 'c', 'recipient_agent_id': 'a'})
            self.assertEqual(binding.agent_id, 'a')
            self.assertEqual(ledger.binding('c'), binding)
            with self.assertRaises(ValueError):
                bind_delivery(ledger, {'conversation_id': 'c', 'recipient_agent_id': 'other'})

    def test_private_invitation_rejects_public_files_and_never_returns_token_to_cli(self):
        from quorum_live import read_invitation
        import os
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / 'invite.json'
            path.write_text(json.dumps({'origin': 'https://example.supabase.co', 'publishable_key': 'pub',
                                       'quorum': 'amplifier', 'quorum_id': 'q', 'token': 'x' * 64}))
            path.chmod(0o644)
            with self.assertRaises(ValueError): read_invitation(path)
            path.chmod(0o600)
            self.assertEqual(read_invitation(path)['quorum'], 'amplifier')

    def test_revocation_during_heartbeat_stops_process_and_keeps_uncertainty(self):
        from quorum_worker import Worker
        from quorum_state import LocalLedger, SessionBinding
        class Api:
            def call(self, op, data, request_id=None):
                if op == 'inbox_claim':
                    return {'attempt_id': 'x', 'generation': 1,
                            'delivery': {'conversation_id': 'c', 'body': 'ping'}}
                if data.get('event') == 'heartbeat': raise ValueError('revoked')
                if op == 'context_read': return []
                if op == 'delivery_complete': raise AssertionError('must not publish after revocation')
        class Runner:
            cancelled = False
            def start(self, *args): return 'child'
            def events(self, child):
                yield {'type': 'started'}
                yield {'type': 'heartbeat'}
            def cancel(self, child): self.cancelled = True
        with tempfile.TemporaryDirectory() as root:
            ledger = LocalLedger(Path(root))
            ledger.bind_session(SessionBinding('q', 'p', 'd', 'a', 'c', 'claude', root, 'main'))
            runner = Runner()
            worker = Worker(Api(), ledger, runner, 'd', quorum_acp.ExecutionPolicy(1, 60, 'subscription', True, True))
            self.assertEqual(worker.run_once(), 'unknown')
            self.assertTrue(runner.cancelled)
            self.assertEqual(ledger.attempt('x')['state'], 'unknown')

    def test_ping_routes_in_project_and_retries_identical_conversation(self):
        from quorum_live import dispatch
        from quorum_state import LocalLedger
        from quorum_api import QuorumApi, ServiceConfig
        from types import SimpleNamespace
        from unittest.mock import patch
        sent = []
        def transport(url, headers, body):
            payload = json.loads(body)
            sent.append((url, payload))
            if url.endswith('/resolve_role'):
                if set(payload) != {'p_quorum_id', 'p_role', 'p_project_ref'}:
                    raise ValueError('wrong_role_rpc_contract')
                return {'agent_id': 'peer'}
            return {'message_id': 'm'}
        api = QuorumApi(ServiceConfig('https://example.com', 'pub'), lambda: 'secret', transport)
        with tempfile.TemporaryDirectory() as root:
            ledger = LocalLedger(Path(root))
            ledger.setting('pilot-agent', {'agent_id': 'a', 'quorum_id': 'q',
                'registration': {'project_ref': 'quorum-pilot/messaging'}})
            args = SimpleNamespace(command='ping', to_role='zhenya', text='ping', request_id='retry-1', conversation_id=None)
            with patch('quorum_live.api_for', return_value=(api, None, None)):
                first = dispatch(args, ledger, None)
                second = dispatch(args, ledger, None)
            self.assertEqual(first, second)
            self.assertEqual(sent[0][1]['p_project_ref'], 'quorum-pilot/messaging')
            self.assertEqual(sent[1][1], sent[3][1])
    def test_saved_reply_upload_retries_without_claim_or_second_model_run(self):
        from quorum_worker import Worker
        from quorum_state import LocalLedger
        class Api:
            def call(self, op, data, request_id=None):
                if op == 'inbox_claim': raise AssertionError('must finish saved reply first')
                if op == 'delivery_complete': return {'reply_message_id': 'reply'}
                raise AssertionError('unexpected RPC')
        class Runner:
            def start(self, *args): raise AssertionError('must not rerun model')
        with tempfile.TemporaryDirectory() as root:
            ledger = LocalLedger(Path(root))
            ledger.set_attempt('pending', 1, 'reply_ready', 'saved pong')
            worker = Worker(Api(), ledger, Runner(), 'd', quorum_acp.ExecutionPolicy(1, 60, 'subscription', True, True))
            self.assertEqual(worker.run_once(), 'answered')
            self.assertEqual(ledger.attempt('pending')['state'], 'answered')


class PortableHostTests(unittest.TestCase):
    def test_portable_lock_rejects_overlap_and_releases(self):
        from quorum_os import FileLock
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / 'test.lock'
            with FileLock(path):
                with self.assertRaisesRegex(ValueError, 'worker_already_running'):
                    with FileLock(path): self.fail('overlap entered')
            with FileLock(path): pass

    def test_safe_open_denies_symlink_and_preserves_target(self):
        from quorum_os import open_private
        import os
        with tempfile.TemporaryDirectory() as root:
            target, link = Path(root) / 'target', Path(root) / 'link'
            target.write_text('unchanged')
            try: link.symlink_to(target)
            except OSError: self.skipTest('host does not permit test symlinks')
            with self.assertRaises((ValueError, OSError)):
                open_private(link, os.O_WRONLY | os.O_TRUNC)
            self.assertEqual(target.read_text(), 'unchanged')

    def test_chunk_reader_timeout_cancels_process(self):
        from quorum_acp import ClaudePilotRunner
        proc = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(20)'],
                                stdout=subprocess.PIPE, start_new_session=True)
        proc.quorum_timeout = 0.1
        runner = ClaudePilotRunner.__new__(ClaudePilotRunner)
        with self.assertRaisesRegex(ValueError, 'adapter_timeout'):
            list(runner.events(proc))
        self.assertIsNotNone(proc.poll())

    def test_individual_copy_runs_through_its_own_uv_project(self):
        import shutil
        source = Path(__file__).resolve().parents[1] / 'skills/quorum'
        self.assertTrue((source / 'pyproject.toml').is_file())
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            copied = root / 'quorum'
            shutil.copytree(source, copied, ignore=shutil.ignore_patterns('.venv', '__pycache__'))
            hostile = root / 'hostile'
            hostile.mkdir()
            (hostile / 'pyproject.toml').write_text('[project]\nname="hostile"\nversion="1"\nrequires-python=">=99"\n')
            result = subprocess.run(['uv', 'run', '--project', str(copied), '--frozen', 'python',
                                     str(copied / 'scripts/quorum.py'), '--state', str(root / 'state'), 'status'],
                                    cwd=hostile, capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout)['data']['worker'], 'offline')

    def test_output_pipe_does_not_need_select_support(self):
        from types import SimpleNamespace
        messages = [
            {'id': 1, 'method': 'session/prompt', 'params': {'sessionId': 's'}},
            {'method': 'session/update', 'params': {'sessionId': 's', 'update': {
                'sessionUpdate': 'agent_message_chunk', 'content': {'type': 'text', 'text': 'pong'}}}},
            {'id': 1, 'result': {'stopReason': 'end_turn'}}]
        stream = io.BytesIO(('\n'.join(json.dumps(m) for m in messages)+'\n').encode())
        handle = SimpleNamespace(stdout=stream, quorum_timeout=1, wait=lambda **_: 0)
        runner = quorum_acp.ClaudePilotRunner.__new__(quorum_acp.ClaudePilotRunner)
        runner.cancel = lambda _: None
        events = list(runner.events(handle))
        self.assertEqual(events[-1], {'type': 'completed', 'success': True, 'text': 'pong'})

    def test_status_reports_an_active_supervisor_without_starting_a_model(self):
        from quorum_live import LocalSupervisorLock
        from quorum_state import LocalLedger
        helper = Path(__file__).resolve().parents[1] / 'skills/quorum/scripts/quorum.py'
        with tempfile.TemporaryDirectory() as root:
            ledger = LocalLedger(Path(root))
            with LocalSupervisorLock(ledger.root):
                result = subprocess.run([sys.executable, str(helper), '--state', root, 'status'],
                                        capture_output=True, text=True, timeout=10)
                self.assertEqual(json.loads(result.stdout)['data']['worker'], 'running')


if __name__ == '__main__': unittest.main()

class PublicationReviewTests(unittest.TestCase):
    def test_pilot_and_regular_registration_use_distinct_devices(self):
        from quorum import register_agent
        from quorum_state import LocalLedger
        class Api:
            devices = 0
            def call(self, op, payload, request_id=None):
                if op == 'quorum_lookup': return {'quorum_id': 'q'}
                if op == 'device_register':
                    self.devices += 1
                    return {'device_id': 'd' + str(self.devices)}
                if op == 'agent_join': return {'agent_id': payload['device_id'] + '-agent'}
                if op == 'agent_status': return {'registered': True}
                raise AssertionError(op)
        with tempfile.TemporaryDirectory() as root:
            ledger, api = LocalLedger(Path(root)), Api()
            data = {'engine': 'claude', 'project_ref': 'quorum-pilot/messaging', 'session_ref': 'main', 'cwd': root}
            ordinary = register_agent(api, ledger, 'team', 'backend', data)
            pilot = register_agent(api, ledger, 'team', 'backend', data, device_key='pilot-device:test')
            self.assertNotEqual(ordinary['device_id'], pilot['device_id'])
            self.assertNotEqual(ordinary['agent_id'], pilot['agent_id'])

    def test_foreign_checkout_rejected_before_probe_or_configuration_write(self):
        from quorum_state import SessionBinding
        runner = quorum_acp.ClaudePilotRunner.__new__(quorum_acp.ClaudePilotRunner)
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            allowed, foreign = root / 'scratch', root / 'main-checkout'
            allowed.mkdir(); foreign.mkdir()
            config = foreign / '.acpxrc.json'; config.write_text('keep me')
            runner.binding_scope = {'quorum_id': 'q', 'agent_id': 'a', 'device_id': 'd',
                'project_ref': 'quorum-pilot/messaging', 'cwd': str(allowed)}
            runner.preflight = lambda *_: self.fail('must reject before auth probe')
            binding = SessionBinding('q', 'quorum-pilot/messaging', 'd', 'other', 'c', 'claude', str(foreign), 'main')
            with self.assertRaisesRegex(ValueError, 'pilot_binding_denied'):
                runner.start(binding, 'ping', quorum_acp.ExecutionPolicy(1, 60, 'subscription', True, True))
            self.assertEqual(config.read_text(), 'keep me')

    @unittest.skipIf(sys.platform == 'win32', 'POSIX process-group boundary')
    def test_cancel_kills_descendant_when_leader_exits_on_sigterm(self):
        import os,signal,time
        with tempfile.TemporaryDirectory() as root:
            ready = Path(root) / 'child-pid'
            child = "import os,signal,time,sys;signal.signal(signal.SIGTERM,signal.SIG_IGN);open(sys.argv[1],'w').write(str(os.getpid()));time.sleep(30)"
            parent = "import subprocess,sys,time;subprocess.Popen([sys.executable,'-c',sys.argv[1],sys.argv[2]]);time.sleep(30)"
            proc = subprocess.Popen([sys.executable, '-c', parent, child, str(ready)],
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
            try:
                deadline=time.monotonic()+5
                while not ready.exists() and time.monotonic()<deadline: time.sleep(.02)
                self.assertTrue(ready.exists())
                child_pid=int(ready.read_text())
                quorum_acp.ClaudePilotRunner.__new__(quorum_acp.ClaudePilotRunner).cancel(proc)
                status = subprocess.run(['ps','-o','stat=','-p',str(child_pid)],capture_output=True,text=True)
                self.assertTrue(status.returncode != 0 or status.stdout.strip().startswith('Z'), 'descendant still executing')
            finally:
                try: os.killpg(proc.pid,signal.SIGKILL)
                except ProcessLookupError: pass
                proc.wait(timeout=3)
