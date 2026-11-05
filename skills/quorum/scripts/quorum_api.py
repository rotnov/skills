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

# Fixed server exception names a client may act on. Any other error body stays `service_error`.
SERVER_CODES = frozenset(('recipient_ambiguous_or_missing', 'role_held_by_own_agent', 'role_held_by_other_identity',
                          'role_assignment_mismatch', 'role_exclusivity_requires_read_committed'))
# A 403 keeps its `auth_required` code everywhere; only these allow-listed server denials are kept as detail.
DENIAL_CODES = frozenset(('agent_denied', 'owner_required'))
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
        if error.code == 403: raise QuorumError('auth_required', denial=_server_code(error, DENIAL_CODES)) from None
        if error.code == 401: raise QuorumError('auth_required') from None
        if error.code == 404:
            raise QuorumError('service_error', missing=_error_body(error).get('code') == 'PGRST202') from None
        body = _error_body(error) if error.code == 400 else {}
        message = body.get('message')
        code = message if isinstance(message, str) and message in SERVER_CODES else None
        if code: raise QuorumError(code, detail=body.get('details')) from None
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


def _server_code(error, allowed=SERVER_CODES):
    """Return an allow-listed exception name from a bounded error body; never the body itself."""
    message = _error_body(error).get('message')
    return message if isinstance(message, str) and message in allowed else None


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
