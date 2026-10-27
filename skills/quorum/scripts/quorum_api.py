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
    'session_heartbeat session_peers session_requirements').split())


class QuorumError(ValueError):
    """Stable error codes only: never include a response body or credentials."""


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
        code = 'auth_required' if error.code in (401, 403) else 'service_error'
        raise QuorumError(code) from None
    except (URLError, OSError):
        raise QuorumError('service_unavailable') from None
    if len(raw) > 262144:
        raise QuorumError('response_too_large')
    try:
        return json.loads(raw)
    except (ValueError, UnicodeError):
        raise QuorumError('invalid_response') from None


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
        if any(not re.fullmatch('[a-z_]+', key) for key in payload):
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
