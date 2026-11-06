"""#143: every fixed server and local code is passed through or deliberately mapped, never collapsed."""
import ast
import contextlib
import io
import json
from pathlib import Path
import re
import sys
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

SKILL = Path(__file__).resolve().parents[1] / 'skills/quorum'
sys.path.insert(0, str(SKILL / 'scripts'))

import quorum_api
from quorum_api import (LOCAL_CODES, LOCAL_VALUE_CODES, QuorumError, ROUTED_CODES, SERVER_ERRORS, error_class,
                        safe_code, server_refused)

SQL = [*sorted((SKILL / 'supabase/migrations').glob('*.sql')), SKILL / 'references/server-bootstrap.sql']
RAISE = re.compile(r'\braise\b([^;]*);', re.I | re.S)
LOWER_LEVELS = re.compile(r'(debug|log|info|notice|warning)\b', re.I)
NAME = '[a-z][a-z0-9_]*'
# Raised only while an administrator applies a migration, before any function exists: no client call can see these.
# Review each addition: a name raised by a function belongs in `quorum_api.SERVER_ERRORS` instead.
MIGRATION_GUARDS = frozenset("""
    delivery_status_present hello_dedupe_present hello_dedupe_required hello_extension_present
    hello_extension_required hello_split_present hello_split_required session_message_present session_message_required
    knowledge_default_role_present knowledge_native_binding_required knowledge_vector_schema_required
    knowledge_wiki_required message_unreceipted_present open_access_required open_invite_ttl_present
    owner_update_maintenance_required peer_status_present peer_status_required priority_core_present
    delivery_statuses_required release_reports_present reviewed_dispatcher_changed reviewed_function_changed reviewed_gate_not_unique reviewed_release_changed
    role_assignment_present role_assignment_required serial_core_present task_authority_bound_present
    task_authority_bound_required update_extension_present
""".split())
# ValueError arguments that are not literals, with every value each one can take.
DYNAMIC_VALUES = {
    ('quorum_invitations.py', 'code'): {'invalid_role', 'invalid_coordinator_role', 'invalid_quorum'},
    ('quorum_os.py', 'str(error)'): {'unsafe_link', 'private_acl_access_denied'},
    ('quorum_profiles.py', 'too_large'): {'unsafe_config_file', 'credential_file_too_large'},
    ('quorum_update.py', 'code'): {'update_tool_missing', 'update_fetch_failed', 'update_validation_failed',
                                   'update_invalid_copy', 'python_provisioning_failed'},
}
# Parser sentinels caught where they are raised, so they cannot reach any output.
INTERNAL_VALUES = {'constant', 'duplicate'}


def raises(text):
    """Every (kind, name) a SQL source raises; kind is `denied`, `rejected`, or None for an unreadable statement."""
    text = re.sub(r'/\*.*?\*/', '', text, flags=re.S)
    code = '\n'.join(line.split('--', 1)[0] for line in text.splitlines())
    found = []
    for match in RAISE.finditer(code):
        body = ' '.join(match.group(1).split())
        if not body or LOWER_LEVELS.match(body): continue  # A bare re-raise, or a message that is not an error.
        condition = re.fullmatch(rf'exception \'({NAME})(?:: ?[^\']*)?\'(?:, ?\w+)?(?: using detail=.*)?', body)
        if condition:
            found.append(('rejected', condition.group(1)))
            continue
        privilege = re.fullmatch(rf"insufficient_privilege(?: using message='({NAME})')?", body)
        if privilege:
            found.append(('denied', privilege.group(1) or 'insufficient_privilege'))
            continue
        found.append((None, body))
    return found


def unreviewed(found):
    """Scanned raises no reviewed table covers, or that the table expects at another HTTP status."""
    status = {'denied': 403, 'rejected': 400}
    problems = []
    for kind, name in found:
        if name in MIGRATION_GUARDS: continue
        if kind is None or name not in SERVER_ERRORS or SERVER_ERRORS[name][0] != status[kind]:
            problems.append((kind, name))
    return sorted(set(problems), key=str)


def http_error(status, body):
    return HTTPError('https://example.invalid/rest/v1/rpc/x', status, 'error', {},
                     io.BytesIO(json.dumps(body).encode() if isinstance(body, dict) else body))


def raised(status, body):
    with patch('quorum_api.build_opener') as opener:
        opener.return_value.open.side_effect = http_error(status, body)
        try: quorum_api.request_json('https://example.invalid/rest/v1/rpc/x', {}, b'{}')
        except QuorumError as error: return error
    raise AssertionError('no error')


class ServerNameTests(unittest.TestCase):
    def test_every_scanned_raise_is_passed_through_or_deliberately_mapped(self):
        found = [item for path in SQL for item in raises(path.read_text(encoding='utf-8'))]
        self.assertGreater(len(found), 200)
        self.assertEqual(unreviewed(found), [])

    def test_no_reviewed_entry_is_stale(self):
        scanned = {name for path in SQL for _, name in raises(path.read_text(encoding='utf-8'))}
        self.assertEqual(set(SERVER_ERRORS) - scanned, set())
        self.assertEqual(MIGRATION_GUARDS - scanned, set())
        self.assertEqual(set(SERVER_ERRORS) & MIGRATION_GUARDS, set())

    def test_a_guard_name_never_comes_from_a_function(self):
        for path in SQL:
            pieces = path.read_text(encoding='utf-8').split('$function$')
            bodies = pieces[1::2]
            for body in bodies:
                self.assertEqual({name for _, name in raises(body)} & MIGRATION_GUARDS, set(), path.name)

    def test_an_unknown_new_name_fails_until_it_is_reviewed(self):
        for text, expected in (("raise exception 'brand_new_code';", ('rejected', 'brand_new_code')),
                               ("raise exception 'brand_new_code: %',f;", ('rejected', 'brand_new_code')),
                               ("raise insufficient_privilege using message='brand_new_denial';",
                                ('denied', 'brand_new_denial')),
                               ("raise exception\n  'brand_new_code'\n  using detail=x;", ('rejected', 'brand_new_code')),
                               ("raise sqlstate 'P0002';", (None, "sqlstate 'P0002'")),
                               ("raise exception using message=dynamic;", (None, 'exception using message=dynamic')),
                               ("raise insufficient_privilege using message='agent_denied', hint='x';",
                                (None, "insufficient_privilege using message='agent_denied', hint='x'"))):
            with self.subTest(text=text):
                self.assertEqual(unreviewed(raises(text)), [expected])

    def test_a_reviewed_name_at_the_wrong_status_is_refused(self):
        self.assertEqual(unreviewed(raises("raise exception 'invite_denied';")), [('rejected', 'invite_denied')])

    def test_scanner_ignores_comments_reraise_and_notices(self):
        self.assertEqual(raises("-- raise exception 'in_a_comment';\nraise;\nraise notice 'hello %', x;\n"
                                "/* raise exception 'in_a_block'; */ exception when raise_exception then null;"), [])

    def test_the_issue_names_keep_their_codes(self):
        for status, names in (
                (403, ('invite_denied', 'invite_consumed', 'invite_target_mismatch', 'quorum_not_open', 'membership_required',
                       'message_denied',
                       'session_binding_denied', 'task_message_denied', 'knowledge_denied')),
                (400, ('skill_update_required', 'update_target_invalid', 'request_id_required', 'idempotency_conflict',
                       'update_event_invalid', 'update_evidence_invalid', 'update_requires_read_committed',
                       'invalid_message', 'invalid_priority', 'reply_already_sent',
                       'active_attempt_requires_reconciliation'))):
            for name in names:
                with self.subTest(name=name):
                    error = raised(status, {'code': 'P0001', 'message': name})
                    self.assertEqual((str(error), error.denial, error.transient), (name, None, False))

    def test_login_and_ownership_denials_stay_auth_required(self):
        for name, denial in (('auth_required', None), ('insufficient_privilege', None), ('agent_denied', 'agent_denied'),
                             ('owner_required', 'owner_required')):
            with self.subTest(name=name):
                error = raised(403, {'code': '42501', 'message': name})
                self.assertEqual((str(error), error.denial), ('auth_required', denial))

    def test_a_name_is_kept_only_at_its_own_status(self):
        for status, message, expected in ((400, 'invite_denied', 'service_error'), (403, 'invalid_message', 'auth_required'),
                                          (401, 'invite_denied', 'auth_required'), (400, 'agent_denied', 'service_error'),
                                          (500, 'invalid_message', 'service_error')):
            with self.subTest(status=status, message=message):
                self.assertEqual(str(raised(status, {'message': message})), expected)

    def test_a_migration_guard_or_unknown_body_is_never_echoed(self):
        for name in ('reviewed_dispatcher_changed', 'hello_dedupe_present', 'secret text'):
            with self.subTest(name=name):
                self.assertEqual(str(raised(400, {'message': name})), 'service_error')
                self.assertEqual(str(raised(403, {'message': name})), 'auth_required')

    def test_generic_callers_keep_the_outcome_behind_each_name(self):
        self.assertEqual(error_class(QuorumError('invite_denied')), 'auth_required')
        self.assertEqual(error_class(QuorumError('invalid_payload')), 'service_error')
        self.assertEqual(error_class(QuorumError('update_requires_read_committed')), 'service_error')
        for code in ROUTED_CODES:
            self.assertEqual(error_class(QuorumError(code)), code)
        for code in ('auth_required', 'service_error', 'service_unavailable', 'coordination_unavailable'):
            self.assertEqual(error_class(QuorumError(code)), code)
        self.assertEqual(error_class(ValueError('invite_denied')), 'invite_denied')
        self.assertTrue(server_refused(QuorumError('update_target_invalid')))
        self.assertTrue(server_refused(QuorumError('update_status_denied')))
        self.assertTrue(server_refused(QuorumError('skill_update_required')))
        for error in (QuorumError('service_error'), QuorumError('auth_required'), ValueError('update_target_invalid')):
            self.assertFalse(server_refused(error))


class ConsumerTests(unittest.TestCase):
    def cli_failure(self, error):
        from quorum import main
        output = io.StringIO()
        with tempfile.TemporaryDirectory() as state, contextlib.redirect_stdout(output), \
                patch('quorum.load_config', create=True), \
                patch('quorum_profiles.resolve_profile', side_effect=error):
            code = main(['--state', state, 'login'])
        return code, json.loads(output.getvalue())

    def test_every_server_code_prints_itself_with_its_exit_code(self):
        for _, code, exit_code in SERVER_ERRORS.values():
            with self.subTest(code=code):
                self.assertEqual(self.cli_failure(QuorumError(code)), (exit_code, {'ok': False, 'code': code, 'data': {}}))

    def test_every_raised_quorum_error_prints_itself(self):
        for code in sorted(literals('QuorumError') - {'owner_required'}):
            with self.subTest(code=code):
                self.assertEqual(self.cli_failure(QuorumError(code))[1]['code'], code)

    def test_listener_reports_server_and_local_codes_but_no_other_text(self):
        from quorum_listener import error_code
        for error, expected in ((QuorumError('invite_denied'), 'invite_denied'), (ValueError('invalid_message'), 'invalid_message'),
                                (ValueError('invitation_too_large'), 'invitation_too_large'),
                                (ValueError('secret text'), 'listener_failed'),
                                (RuntimeError('invalid_message'), 'listener_failed'),
                                (OSError('invite_denied'), 'listener_failed')):
            with self.subTest(error=error):
                self.assertEqual(error_code(error), expected)

    def test_a_named_heartbeat_rejection_is_coordination_unavailable_and_a_denial_ends_reception(self):
        from quorum_realtime import TERMINAL
        self.assertIn(error_class(QuorumError('github_identity_required')), TERMINAL)
        self.assertNotIn(error_class(QuorumError('invalid_payload')), TERMINAL)
        self.assertIn(error_class(QuorumError('skill_update_required')), TERMINAL)


def literals(name):
    """String literals passed to `name(...)` across the skill's scripts, and every non-literal argument."""
    found, dynamic = set(), set()
    for path in sorted((SKILL / 'scripts').glob('*.py')):
        for node in ast.walk(ast.parse(path.read_text(encoding='utf-8'))):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == name and node.args):
                continue
            argument = node.args[0]
            values = [leaf.value for leaf in ast.walk(argument)
                      if isinstance(leaf, ast.Constant) and isinstance(leaf.value, str)]
            if isinstance(argument, ast.Constant) or isinstance(argument, ast.IfExp): found.update(values)
            else: dynamic.add((path.name, ast.unparse(argument)))
    literals.dynamic = dynamic
    return found


class LocalCodeTests(unittest.TestCase):
    def test_every_raised_fixed_code_is_in_the_safe_set(self):
        missing = literals('ValueError') - INTERNAL_VALUES - LOCAL_VALUE_CODES - LOCAL_CODES
        self.assertEqual(missing, set())

    def test_every_dynamic_value_error_is_reviewed_and_safe(self):
        literals('ValueError')
        self.assertEqual(literals.dynamic, set(DYNAMIC_VALUES))
        for site, values in DYNAMIC_VALUES.items():
            self.assertEqual(values - LOCAL_VALUE_CODES - LOCAL_CODES, set(), site)

    def test_the_issue_codes_print_as_themselves(self):
        for code in ('github_user_invalid', 'github_response_too_large', 'github_identity_invalid',
                     'invitation_arguments_invalid', 'invitation_file_conflict', 'invitation_token_unavailable',
                     'invalid_service_origin', 'public_service_key_required', 'service_arguments_required',
                     'json_stdin_required', 'invalid_arguments', 'invitation_too_large', 'invalid_invitation',
                     'quorum_mismatch', 'receipt_not_submitted', 'invalid_message', 'invalid_priority',
                     'message_too_large'):
            with self.subTest(code=code):
                self.assertEqual(safe_code(ValueError(code)), code)

    def test_only_a_value_error_carries_a_value_code_and_other_text_stays_hidden(self):
        self.assertEqual(safe_code(ValueError('invite_denied')), 'invite_denied')
        for error in (OSError('private_acl_unavailable'), RuntimeError('invalid_message'), ValueError('secret text'),
                      ValueError(''), TypeError('invalid_message')):
            with self.subTest(error=error):
                self.assertIsNone(safe_code(error))
        self.assertEqual(safe_code(OSError('auth_lock_timeout')), 'auth_lock_timeout')


if __name__ == '__main__':
    unittest.main()
