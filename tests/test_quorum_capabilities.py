"""Route capability report (#110): an upfront, offline unsupported result before promising until-stop work."""
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

SKILL = Path(__file__).resolve().parents[1] / 'skills/quorum'
sys.path.insert(0, str(SKILL / 'scripts'))

import quorum  # noqa: E402
import quorum_acp  # noqa: E402
import quorum_capabilities as caps  # noqa: E402

CLIENT_VARIABLES = ('CODEX_THREAD_ID', 'CLAUDE_CODE_SESSION_ID', 'GROK_SESSION_ID')
SECRET_LIKE = 'session-value-that-must-not-appear'


def run(argv, environ=None):
    environ = {} if environ is None else environ
    clean = {key: value for key, value in os.environ.items() if key not in CLIENT_VARIABLES}
    with patch.dict(os.environ, {**clean, **environ}, clear=True), \
            patch.object(subprocess, 'Popen', side_effect=AssertionError('no process may start')), \
            contextlib.redirect_stdout(io.StringIO()) as output:
        code = quorum.main(argv)
    return code, json.loads(output.getvalue()), output.getvalue()


class ReportTests(unittest.TestCase):
    def setUp(self):
        # These describe a route's own facts; a Grok chat running the suite must not change them.
        clean = {key: value for key, value in os.environ.items() if key not in CLIENT_VARIABLES}
        environment = patch.dict(os.environ, clean, clear=True)
        environment.start()
        self.addCleanup(environment.stop)

    def test_every_route_reports_all_three_capabilities_with_fixed_states(self):
        for route in caps.ROUTES:
            value = caps.report(route)
            self.assertEqual(value['route'], route)
            self.assertEqual(value['route_source'], 'explicit')
            self.assertEqual(set(value['capabilities']), set(caps.CAPABILITIES))
            for entry in value['capabilities'].values():
                self.assertIn(entry['status'], caps.STATES)
                self.assertRegex(entry['reason'], r'^[a-z_]+$')

    def test_no_route_supports_unattended_project_execution_while_the_gate_stands(self):
        with self.assertRaisesRegex(ValueError, 'live_agents_disabled'):
            quorum_acp.AcpRunner().start(None, 'task', None)
        for route in caps.ROUTES:
            entry = caps.report(route)['capabilities']['unattended_project_execution']
            self.assertEqual((entry['status'], entry['reason']), ('unsupported', 'live_agents_disabled'))

    def test_codex_tool_inbox_is_bounded_receive_only(self):
        value = caps.report('codex-tool-inbox')['capabilities']
        self.assertEqual(value['bounded_receive']['status'], 'supported')
        self.assertEqual(value['continuous_background_reception']['status'], 'unsupported')
        self.assertEqual(value['continuous_background_reception']['reason'], 'no_process_between_calls')

    def test_each_route_states_its_fixed_receive_claims(self):
        expected = {
            'codex-tool-inbox': (('supported', 'finite_tool_call'), ('unsupported', 'no_process_between_calls')),
            'claude-worker': (('supported', 'finite_tool_call'),
                              ('conditional', 'client_and_worker_must_stay_alive')),
            'parent-session': (('unsupported', 'parent_queue_route'), ('conditional', 'client_must_stay_available')),
            'grok-cloud': (('unsupported', 'parent_queue_route'),
                           ('conditional', 'cloud_computer_must_stay_active')),
            'claude-listener-pilot': (('unsupported', 'separate_headless_handler'),
                                      ('conditional', 'owner_subscription_approval')),
        }
        self.assertEqual(set(expected), set(caps.ROUTES))
        for route, (bounded, continuous) in expected.items():
            value = caps.report(route)['capabilities']
            with self.subTest(route=route):
                self.assertEqual((value['bounded_receive']['status'], value['bounded_receive']['reason']), bounded)
                entry = value['continuous_background_reception']
                self.assertEqual((entry['status'], entry['reason']), continuous)

    def test_claude_worker_receives_across_idle_turns_only_while_the_client_lives(self):
        detail = caps.report('claude-worker')['capabilities']['continuous_background_reception']['detail']
        for phrase in ('across idle parent turns', 'Claude Code client and that worker stay alive',
                       "brief's limit", 'slot deadline', 'Nothing restarts after the client exits', 'rebind'):
            self.assertIn(phrase, detail)

    def test_codex_worker_may_be_suspended_when_the_parent_turn_ends(self):
        detail = caps.report('codex-tool-inbox')['capabilities']['continuous_background_reception']['detail']
        self.assertIn('parent turn ending can suspend it', detail)
        self.assertIn('receipt_timeout', detail)

    def test_parent_session_names_the_windows_grok_background_task(self):
        detail = caps.report('parent-session')['capabilities']['continuous_background_reception']['detail']
        self.assertIn('Grok TUI route', detail)
        self.assertIn('background shell task', detail)

    def test_listener_pilot_receives_continuously_only_with_owner_approval(self):
        value = caps.report('claude-listener-pilot')['capabilities']
        self.assertEqual(value['continuous_background_reception']['status'], 'conditional')
        self.assertEqual(value['continuous_background_reception']['reason'], 'owner_subscription_approval')
        self.assertEqual(value['unattended_project_execution']['status'], 'unsupported')

    def test_alternatives_are_bounded_and_include_the_listener_pilot(self):
        routes = [item['route'] for item in caps.report('codex-tool-inbox')['alternatives']]
        self.assertEqual(routes, ['bounded-receive-while-active', 'durable-queue-resume', 'claude-listener-pilot'])

    def test_require_until_stop_raises_the_fixed_code_with_reason_and_alternatives(self):
        for route in caps.ROUTES + (None,):
            with self.assertRaises(caps.CapabilityUnsupported) as raised:
                caps.check(route, 'until-stop-execution', environ={})
            self.assertEqual(raised.exception.code, 'background_execution_unsupported')
            data = raised.exception.data
            self.assertEqual(data['requirement'], 'until-stop-execution')
            self.assertEqual(data['capability'], 'unattended_project_execution')
            self.assertEqual(data['reason'], 'live_agents_disabled')
            self.assertTrue(data['alternatives'])

    def test_invalid_route_or_requirement_is_refused(self):
        with self.assertRaisesRegex(ValueError, 'invalid_route'):
            caps.report('daemon')
        with self.assertRaisesRegex(ValueError, 'invalid_requirement'):
            caps.check('codex-tool-inbox', 'forever')


class GrokParentRouteTests(unittest.TestCase):
    """#145: a Grok chat is told what its own platform and leader allow, offline and by variable name only."""
    LEADER = {'GROK_SESSION_ID': SECRET_LIKE, 'GROK_LEADER_SOCKET': '/private/' + SECRET_LIKE}

    def continuous(self, environ, platform, route=None):
        return caps.report(route, environ, platform)['capabilities']['continuous_background_reception']

    def test_a_leader_chat_on_a_qualified_platform_stays_conditional(self):
        for platform in ('darwin', 'win32'):
            with self.subTest(platform=platform):
                entry = self.continuous(self.LEADER, platform)
                self.assertEqual((entry['status'], entry['reason']), ('conditional', 'client_must_stay_available'))

    def test_linux_does_not_advertise_parent_session_reception(self):
        for route in (None, 'parent-session'):
            with self.subTest(route=route):
                entry = self.continuous(self.LEADER, 'linux', route)
                self.assertEqual((entry['status'], entry['reason']),
                                 ('unsupported', 'grok_leader_platform_unsupported'))

    def test_a_chat_without_a_leader_export_is_not_advertised_either(self):
        entry = self.continuous({'GROK_SESSION_ID': SECRET_LIKE}, 'darwin')
        self.assertEqual((entry['status'], entry['reason']), ('unsupported', 'leader_socket_missing'))
        self.assertIn('--resume', entry['detail'])

    def test_the_report_carries_no_variable_value(self):
        self.assertNotIn(SECRET_LIKE, json.dumps(caps.report(None, self.LEADER, 'linux')))

    def test_the_legacy_parent_opt_in_of_other_clients_is_unchanged(self):
        for environ in ({}, {'CODEX_THREAD_ID': 'x'}, {'CLAUDE_CODE_SESSION_ID': 'x'},
                        {'CODEX_THREAD_ID': 'x', 'GROK_SESSION_ID': 'y'}):
            with self.subTest(environ=sorted(environ)):
                entry = self.continuous(environ, 'linux', 'parent-session')
                self.assertEqual(entry['status'], 'conditional')

    def test_the_cli_reports_the_current_platform(self):
        with patch.object(sys, 'platform', 'linux'):
            code, data, text = run(['capabilities'], self.LEADER)
        entry = data['data']['capabilities']['continuous_background_reception'] if 'data' in data else \
            data['capabilities']['continuous_background_reception']
        self.assertEqual((code, entry['reason']), (0, 'grok_leader_platform_unsupported'))
        self.assertNotIn(SECRET_LIKE, text)


class DefaultRouteTests(unittest.TestCase):
    def test_single_client_selects_its_quiet_worker_route(self):
        self.assertEqual(caps.default_route({'CODEX_THREAD_ID': 'x'}), ('codex-tool-inbox', 'client_env'))
        self.assertEqual(caps.default_route({'CLAUDE_CODE_SESSION_ID': 'x'}), ('claude-worker', 'client_env'))
        self.assertEqual(caps.default_route({'GROK_SESSION_ID': 'x'}), ('parent-session', 'client_env'))

    def test_ambiguous_or_missing_client_names_no_route(self):
        self.assertEqual(caps.default_route({}), (None, 'no_client_env'))
        both = {'CODEX_THREAD_ID': 'x', 'CLAUDE_CODE_SESSION_ID': 'y'}
        self.assertEqual(caps.default_route(both), (None, 'ambiguous_client_env'))
        value = caps.report(environ=both)
        self.assertIsNone(value['route'])
        self.assertEqual(value['capabilities']['bounded_receive']['reason'], 'route_unknown')


class CommandTests(unittest.TestCase):
    def test_report_is_offline_and_never_touches_state_or_echoes_session_values(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / 'absent'
            code, payload, raw = run(['--state', str(state), 'capabilities'], {'CODEX_THREAD_ID': SECRET_LIKE})
            self.assertEqual(code, 0)
            self.assertTrue(payload['ok'])
            self.assertEqual(payload['data']['route'], 'codex-tool-inbox')
            self.assertFalse(state.exists())
            self.assertNotIn(SECRET_LIKE, raw)

    def test_require_until_stop_fails_upfront_with_exit_four(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / 'absent'
            code, payload, _ = run(['--state', str(state), 'capabilities', '--route', 'codex-tool-inbox',
                                    '--require', 'until-stop-execution'])
            self.assertEqual(code, 4)
            self.assertFalse(payload['ok'])
            self.assertEqual(payload['code'], 'background_execution_unsupported')
            self.assertEqual(payload['data']['reason'], 'live_agents_disabled')
            self.assertEqual(payload['data']['capabilities']['bounded_receive']['status'], 'supported')
            self.assertFalse(state.exists())

    def test_existing_gates_are_unchanged(self):
        with tempfile.TemporaryDirectory() as directory:
            code, payload, _ = run(['--state', directory, 'worker'])
        self.assertEqual((code, payload['code']), (4, 'live_agents_disabled'))


class InstructionTests(unittest.TestCase):
    def test_skill_routes_until_stop_requests_to_the_upfront_check(self):
        skill = ' '.join((SKILL / 'SKILL.md').read_text(encoding='utf-8').split())
        self.assertIn('capabilities --require until-stop-execution', skill)
        self.assertIn('background_execution_unsupported', skill)
        self.assertIn('native-inbox.md#background-capability', skill)

    def test_reference_documents_every_route_and_the_supported_alternatives(self):
        text = (SKILL / 'references/native-inbox.md').read_text(encoding='utf-8')
        section = text.split('## Background capability', 1)[1].split('\n## ', 1)[0]
        flat = ' '.join(section.split())
        for route in caps.ROUTES:
            self.assertIn('`' + route + '`', flat)
        for phrase in ('background_execution_unsupported', 'live_agents_disabled', 'Bounded receive while active',
                       'fresh `task-check`', 'This is not until-stop', 'cannot do project work'):
            self.assertIn(phrase, flat)
        listener = ' '.join((SKILL / 'references/listener.md').read_text(encoding='utf-8').split())
        self.assertIn('not until-stop project execution', listener)

    def test_references_defer_worker_lifetime_to_the_report(self):
        inbox = ' '.join((SKILL / 'references/native-inbox.md').read_text(encoding='utf-8').split())
        service = ' '.join((SKILL / 'references/service-agent.md').read_text(encoding='utf-8').split())
        # No route-wide claim that every worker ends with the parent turn; the report states each route.
        self.assertNotIn('keeps receiving while the parent turn lasts', inbox)
        self.assertNotIn('or when the parent turn ends', inbox)
        self.assertNotIn("can pause when the parent's turn ends", service)
        self.assertIn('one canonical per-route statement', inbox)
        self.assertIn('`continuous_background_reception`', inbox)
        self.assertIn('native-inbox.md#background-capability', service)


if __name__ == '__main__':
    unittest.main()
