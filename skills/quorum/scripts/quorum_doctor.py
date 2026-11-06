"""Route-aware health check: is this participant's actual Quorum route usable?

It reuses the `capabilities` route detection, checks only what that route needs locally (service
configuration and a stored login, never their values), reads state without writing it, makes no
network call and starts nothing.
The default ACP worker and Codex runner gate is reported as informational `pilot_gate` unless the
explicit `acp-worker` route is checked; the gate itself is never relaxed here.
"""
import json
from pathlib import Path
import re

from quorum_api import QuorumError
from quorum_capabilities import ROUTES, default_route

# The default `worker` command and Codex runner: the only route the engine acceptance gate applies to.
GATED_ROUTES = ('acp-worker',)
DOCTOR_ROUTES = ROUTES + GATED_ROUTES
_EXIT = {'auth_required': 3, 'auth_profile_conflict': 3}  # Same exit codes as the CLI's error map.


def gate_state(skill_root=None):
    """`engine_acceptance_pending` until exact engine pins are accepted, then still `live_agents_disabled`."""
    from quorum_acp import validate_manifest
    root = Path(__file__).resolve().parents[1] if skill_root is None else Path(skill_root)
    try:
        validate_manifest(json.loads((root / 'assets/engines.lock.json').read_text()))
    except ValueError:
        return 'engine_acceptance_pending'
    return 'live_agents_disabled'


def _code(error):
    code = str(error)
    return code if re.fullmatch(r'[a-z_]+', code) else 'credential_check_failed'


def diagnose(state, route=None, environ=None):
    """(ok, code, exit_code, data) for the explicit route or this client's default route."""
    source = 'explicit'
    if route is None:
        route, source = default_route(environ)
    elif route not in DOCTOR_ROUTES:
        raise ValueError('invalid_route')
    gate = gate_state()
    data = {'route': route, 'route_source': source, 'checks': {},
            'pilot_gate': {'state': gate, 'applies_to': list(GATED_ROUTES), 'applies': route in GATED_ROUTES},
            'live_agents': 'disabled'}

    def fail(code):
        return False, code, _EXIT.get(code, 4), data

    if route is None:
        data['checks']['route'] = 'unknown'
        return fail('route_unknown')
    data['checks']['route'] = 'ok'
    if route in GATED_ROUTES:
        # The explicit default worker route is the one the gate blocks; report it as the failure.
        return fail(gate)
    # Read-only and offline: never resolve_profile, which may adopt, verify at Auth, refresh or bind.
    from quorum_profiles import inspect_profile
    try:
        profile = inspect_profile(state)
    except (QuorumError, ValueError, TypeError) as error:
        code = _code(error)
        data['checks']['service' if code.startswith('service_') else 'login'] = 'failed'
        return fail(code)
    data['checks'].update(profile)
    if profile['login'] == 'missing':
        return fail('auth_required')
    # A legacy login awaiting its one-time Auth binding is verified by the next real command, not here.
    return True, 'ok', 0, data
