"""Bounded HTTPS RPC transport. No downloads, login, or implicit retries."""
from dataclasses import dataclass
import json
import re
from urllib.parse import urlsplit
from urllib.request import Request, build_opener, HTTPRedirectHandler
from urllib.error import HTTPError, URLError

OPERATIONS = frozenset(('quorum_create invite_create invite_accept device_register agent_join '
    'message_send inbox_claim attempt_update delivery_complete context_read member_revoke '
    'work_register work_admit work_transition agent_status agent_leave conversation_read '
    'quorum_lookup quorum_check_owner resolve_role session_inbox session_ack session_reply '
    'invite_create_id invite_accept_id invite_revoke_id invite_create_open quorum_set_access '
    'session_heartbeat session_peers session_requirements session_task_authority '
    'session_update_request session_update_status session_update_report '
    'knowledge_admin knowledge_write knowledge_search knowledge_read knowledge_index '
    'knowledge_source_write knowledge_source_read knowledge_catalog agent_retire quorum_agents').split())


# Gateway/overload statuses only; missing RPCs, denials and client errors stay terminal.
TRANSIENT_HTTP = frozenset((408, 429, 500, 502, 503, 504))

# Every fixed exception name a reviewed RPC raises: name -> (HTTP status, client code, CLI exit code).
# PostgREST answers `raise insufficient_privilege` with 403 and `raise exception` with 400. A name is kept only
# at its own status; any other error body stays `auth_required` or `service_error`, without echoing the body.
# tests/test_quorum_error_codes.py scans the SQL, so a new name is refused until it is listed here on review.
def _names(status, exit_code, names):
    return {name: (status, name, exit_code) for name in names.split()}


SERVER_ERRORS = {
    **_names(403, 3, (
        'attempt_denied device_denied github_identity_required independent_verifier_required '
        'instance_creator_required invite_consumed invite_denied invite_target_mismatch knowledge_denied '
        'membership_required message_denied quorum_not_open session_binding_denied task_message_denied '
        'update_notice_denied update_status_denied')),
    **_names(400, 3, (
        'active_attempt_requires_reconciliation already_assigned assignment_closed assignment_missing '
        'binding_conflict cannot_revoke_owner capacity_full completion_unconfirmed connection_id_required '
        'dependency_not_accepted idempotency_conflict invalid_access invalid_attempt_transition '
        'invalid_dependencies invalid_message invalid_payload invalid_peer_status invalid_priority '
        'invalid_project_ref invalid_reply invalid_role invalid_target_github_id invalid_work_transition '
        'knowledge_entry_inactive knowledge_entry_unavailable knowledge_idempotency_conflict '
        'knowledge_indexing_unconfigured knowledge_invalid_link knowledge_invalid_operation '
        'knowledge_invalid_payload knowledge_invalid_vector knowledge_kind_immutable '
        'knowledge_owner_assertion_required knowledge_page_immutable knowledge_request_id_required '
        'knowledge_revision_conflict knowledge_revision_immutable knowledge_source_conflict '
        'knowledge_source_immutable knowledge_stale_revision knowledge_vector_incompatible '
        'knowledge_wiki_evidence_required payload_too_large recipient_ambiguous_or_missing '
        'recipient_unavailable reply_already_sent request_id_required role_assignment_mismatch '
        'role_held_by_other_identity role_held_by_own_agent scratch_repository_required stale_generation '
        'unknown_operation update_event_invalid update_evidence_invalid update_target_conflict '
        'update_target_invalid')),
    # A caller outside READ COMMITTED: the deployment, not the request, is wrong.
    **_names(400, 5, ('knowledge_requires_read_committed open_access_requires_read_committed '
                      'role_exclusivity_requires_read_committed update_requires_read_committed')),
    **_names(400, 4, 'skill_update_required'),
    # Deliberately generic: login state or ownership. `DENIAL_CODES` keeps the two a caller acts on as detail.
    **{name: (403, 'auth_required', 3) for name in ('agent_denied', 'auth_required', 'insufficient_privilege',
                                                    'owner_required')},
}
SERVER_CODES = frozenset(entry[1] for entry in SERVER_ERRORS.values())
# A 403 keeps its `auth_required` code for these; only these server denials are kept as detail.
DENIAL_CODES = frozenset(('agent_denied', 'owner_required'))
# Names a caller routes on by itself; every other reviewed name also reads as its generic outcome (`error_class`).
ROUTED_CODES = frozenset(('recipient_ambiguous_or_missing', 'role_assignment_mismatch', 'role_held_by_other_identity',
                          'role_held_by_own_agent', 'role_exclusivity_requires_read_committed',
                          'skill_update_required'))
# Fixed local codes printed for any handled exception type.
LOCAL_CODES = frozenset((
    'anonymous_state_conflict auth_lock_timeout auth_profile_conflict browser_unavailable '
    'github_identity_required invalid_agent_id invalid_project_ref invalid_quorum invalid_request_id '
    'invalid_role login_timeout machine_profile_unavailable service_config_conflict service_profile_ambiguous').split())
# Fixed local codes printed only for a ValueError. A test keeps every raised literal in this set.
LOCAL_VALUE_CODES = frozenset((
    'adapter_failed adapter_line_too_large adapter_output_too_large adapter_pipe_failed adapter_timeout '
    'agent_not_configured agent_scope_required argument_denied binding_conflict binding_required '
    'cancellation_requested capability_denied claude_pipe_unavailable cloud_relay_running '
    'completion_unconfirmed context_too_large coordinator_changed credential_directory_required '
    'credential_file_too_large credential_store_unavailable dedicated_pilot_profile_required '
    'delegation_scope_changed dispatch_mode_requires_coordinator engine_missing engine_version_mismatch '
    'exact_engine_versions_required execution_policy_denied github_identity_invalid github_response_too_large '
    'github_user_invalid grok_transport_unavailable grok_tui_gone hash_mismatch invalid_argument invalid_arguments '
    'invalid_binding invalid_capability_ttl invalid_coordinator_role invalid_credential_profile '
    'invalid_dispatch_mode invalid_invitation invalid_message invalid_notification invalid_oauth_callback '
    'invalid_operation invalid_priority invalid_profile_registry invalid_registration invalid_release_manifest '
    'invalid_requirement invalid_response invalid_route invalid_scratch_directory invalid_sequence '
    'invalid_service_origin invalid_session invalid_session_ref invalid_session_selector invalid_skill_name '
    'invalid_slot invalid_standing_action invalid_state_directory invalid_status invalid_wait_seconds '
    'invitation_arguments_invalid invitation_file_conflict invitation_token_unavailable invitation_too_large '
    'json_stdin_required listener_start_stopped listener_stop_pending live_agents_disabled mcp_request_too_large '
    'member_intake_requires_inline member_trust_requires_members message_too_large method_not_found '
    'missing_lock_contract missing_required_file not_initialized pilot_agent_required pilot_binding_denied '
    'pilot_claude_one_turn_only private_acl_access_denied private_acl_unavailable '
    'process_isolation_unavailable public_service_key_required publishable_key_required '
    'python_provisioning_failed quorum_mismatch realtime_unavailable receipt_not_submitted reply_too_large '
    'request_id_required request_too_large scope_denied scratch_directory_required service_arguments_required '
    'session_binding_denied session_delivery_unknown session_delivery_unsent session_mismatch '
    'session_not_connected '
    'session_queue_failed session_reconcile_denied session_recovery_denied stale_generation '
    'standing_grants_require_members subscription_required tool_denied tools_disabled truncated_adapter_output '
    'unknown_operation unsafe_config_file unsafe_credential_directory unsafe_credential_file '
    'unsafe_event_fields unsafe_link unsafe_profile_directory unsafe_state_file unsupported_open_flags '
    'unsupported_protocol '
    'update_cache_conflict update_fetch_failed update_in_progress update_invalid_copy update_node_too_old '
    'update_notice_denied update_request_invalid update_target_changed update_tool_missing update_unsafe_cache '
    'update_validation_failed webhook_config_invalid webhook_not_configured worker_already_running').split())
CANONICAL_UUID = re.compile('[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}')
ROLE = re.compile('[a-z][a-z0-9-]{0,47}')
PROJECT_REF = re.compile('[A-Za-z0-9._/:@][A-Za-z0-9._/:@-]{0,499}')


def _detail(code, raw):
    """Only the caller's own data, shape-checked: its other agent's ID, or its assigned role and project."""
    if code == 'role_held_by_own_agent':
        return raw if isinstance(raw, str) and CANONICAL_UUID.fullmatch(raw) else None
    if code == 'role_assignment_mismatch' and isinstance(raw, str) and len(raw) <= 1024:
        try: value = json.loads(raw)
        except ValueError: return None
        if (isinstance(value, dict) and set(value) == {'role', 'project_ref'}
                and (value['role'] is None or (isinstance(value['role'], str) and ROLE.fullmatch(value['role'])))
                and isinstance(value['project_ref'], str) and PROJECT_REF.fullmatch(value['project_ref'])):
            return value
    return None


class QuorumError(ValueError):
    """Stable error codes only: never include a response body or credentials."""
    def __init__(self, code, *, transient=False, denial=None, missing=False, detail=None):
        super().__init__(code)
        self.transient = transient
        # An explicit, allow-listed server privilege denial behind an `auth_required` 403; else None.
        self.denial = denial if denial in DENIAL_CODES else None
        # PostgREST found no function for this name and argument set: an older server.
        self.missing = missing is True
        self.detail = _detail(code, detail)


def error_class(error):
    """The generic outcome behind a reviewed name, for a caller that branches only on denied or rejected."""
    code = str(error)
    if not isinstance(error, QuorumError) or code not in SERVER_ERRORS or code in ROUTED_CODES: return code
    return 'auth_required' if SERVER_ERRORS[code][0] == 403 else 'service_error'


def server_refused(error):
    """A reviewed RPC refused this call by name, so its transaction rolled back and nothing took effect."""
    return isinstance(error, QuorumError) and str(error) in SERVER_CODES and str(error) != 'auth_required'


def safe_code(error):
    """The fixed code of a local or reviewed server failure; None for any other text, which may hold secrets."""
    code = str(error)
    if code in LOCAL_CODES: return code
    return code if isinstance(error, ValueError) and (code in LOCAL_VALUE_CODES or code in SERVER_CODES) else None


def rpc_missing(error):
    """The service has no RPC with these arguments (an older server); never inferred from other failures."""
    return isinstance(error, QuorumError) and str(error) == 'service_error' and error.missing


def is_transient_service(error):
    """A typed outage an opted-in native listener may wait out; the RPC itself is never retried here."""
    return isinstance(error, QuorumError) and (str(error) == 'service_unavailable'
                                               or (str(error) == 'service_error' and error.transient is True))


@dataclass(frozen=True)
class ServiceConfig:
    origin: str
    publishable_key: str

    def __post_init__(self):
        url = urlsplit(self.origin)
        if (url.scheme != 'https' or not url.hostname or url.username or url.password
                or url.path not in ('', '/') or url.query or url.fragment):
            raise ValueError('invalid_service_origin')
        if not self.publishable_key:
            raise ValueError('publishable_key_required')


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *_):
        return None


def request_json(url, headers, body):
    return _open(Request(url, data=body, headers=headers, method='POST'))


def get_json(url, headers):
    return _open(Request(url, headers=headers, method='GET'))


def _open(request):
    try:
        with build_opener(NoRedirect).open(request, timeout=20) as response:
            raw = response.read(262145)
    except HTTPError as error:
        if error.code == 401: raise QuorumError('auth_required') from None
        if error.code == 404:
            raise QuorumError('service_error', missing=_error_body(error).get('code') == 'PGRST202') from None
        body = _error_body(error) if error.code in (400, 403) else {}
        message = body.get('message')
        known = SERVER_ERRORS.get(message) if isinstance(message, str) else None
        if known and known[0] == error.code:
            raise QuorumError(known[1], denial=message, detail=body.get('details')) from None
        if error.code == 403: raise QuorumError('auth_required') from None
        raise QuorumError('service_error', transient=error.code in TRANSIENT_HTTP) from None
    except (URLError, OSError):
        raise QuorumError('service_unavailable', transient=True) from None
    if len(raw) > 262144:
        raise QuorumError('response_too_large')
    try:
        return json.loads(raw)
    except (ValueError, UnicodeError):
        raise QuorumError('invalid_response') from None


def _error_body(error):
    """A bounded JSON error object, or {}; callers keep only allow-listed, shape-checked fields."""
    try:
        raw = error.read(4097)
        body = json.loads(raw) if len(raw) <= 4096 else None
    except Exception:
        return {}
    return body if isinstance(body, dict) else {}


class QuorumApi:
    def __init__(self, config, token, request=request_json):
        self.config, self.token, self.request = config, token, request

    def call_guarded(self, operation, payload, request_id=None, *, before_request, on_dispatch=None):
        """Opt-in local guard after credentials, immediately before transport; never mutate this API.

        `on_dispatch` is an optional non-throwing local marker, not a guard or I/O callback.
        The ordinary call performs the same validation, encoding and token resolution on a scoped clone.
        """
        def request(url, headers, body):
            before_request()
            if on_dispatch is not None: on_dispatch()
            return self.request(url, headers, body)
        return QuorumApi(self.config, self.token, request).call(operation, payload, request_id)

    def call(self, operation, payload, request_id=None):
        if operation not in OPERATIONS or not isinstance(payload, dict):
            raise ValueError('invalid_operation')
        if any(not re.fullmatch('[a-z_][a-z0-9_]*', key) for key in payload):
            raise ValueError('invalid_argument')
        data = {'p_' + key: value for key, value in payload.items()}
        if request_id is not None:
            data['p_request_id'] = request_id
        body = json.dumps(data, ensure_ascii=False).encode()
        if len(body) > 131072:
            raise ValueError('request_too_large')
        return self.request(self.config.origin.rstrip('/') + '/rest/v1/rpc/' + operation,
                            {'Authorization': 'Bearer ' + self.token(),
                             'apikey': self.config.publishable_key,
                             'Content-Type': 'application/json'}, body)


def guarded_call(api, operation, payload, request_id=None, *, before_request, on_dispatch):
    """Use the optional real transport seam; other trusted adapters retain conservative call-entry tracking."""
    scoped = getattr(api, 'call_guarded', None)
    if callable(scoped):
        return scoped(operation, payload, request_id, before_request=before_request, on_dispatch=on_dispatch)
    before_request()
    on_dispatch()
    return api.call(operation, payload, request_id)
