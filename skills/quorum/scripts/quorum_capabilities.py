"""Offline route capability report: what a Quorum route can and cannot do between owner turns.

Static facts only. It reads at most the client session variable *names* to pick a default route, and
the OS name, never their values, the private state, credentials or the network, and it starts nothing.
"""
import os
import sys

STATES = ('supported', 'conditional', 'unsupported')
CAPABILITIES = ('bounded_receive', 'continuous_background_reception', 'unattended_project_execution')
REQUIREMENTS = {'until-stop-execution': 'unattended_project_execution'}
# OSes with a qualified existing-chat Grok TUI leader; the Grok transport itself refuses all others.
GROK_LEADER_PLATFORMS = ('darwin', 'win32')

# The ACP launcher and default worker stay behind `live_agents_disabled`; no route executes
# project work while nobody drives a turn, so this entry is shared and never route-specific.
_NO_UNATTENDED = ('unsupported', 'live_agents_disabled',
                  'Project tasks run only inside a turn the owner or a finite worker brief drives, with a fresh '
                  'task-check per task; the default worker and Codex runner stay disabled.')

_ROUTES = {
    'codex-tool-inbox': {
        'bounded_receive': ('supported', 'finite_tool_call',
                            'session receive --wait-seconds 0..60 returns one offer, timeout or stopped per call.'),
        'continuous_background_reception': (
            'unsupported', 'no_process_between_calls',
            'Nothing runs between receive calls; session start only re-arms reception. The worker serves a finite '
            'brief and the parent turn ending can suspend it, so an idle parent leaves the binding unavailable '
            '(receipt_timeout); messages stay durable.'),
    },
    'claude-worker': {
        'bounded_receive': ('supported', 'finite_tool_call',
                            'session --worker-capability <file> receive --wait-seconds 0..60 per call; older briefs '
                            'and clients without background commands repeat it.'),
        'continuous_background_reception': (
            'conditional', 'client_and_worker_must_stay_alive',
            'A background worker keeps receiving across idle parent turns only while the Claude Code client and that '
            'worker stay alive. Its brief runs session wait as a background command: no model turn runs during an '
            'empty wait, and the command\'s exit wakes the worker. That wake was observed on macOS for a 90-second '
            'wait; hours-long waits and Windows are not yet observed, so this is not perpetual service. The brief\'s '
            'wait has no own bound (--max-seconds 0) and runs as a background shell command, which an interactive '
            'session never stops, so an idle worker takes no model turn; an unattended session passes up to 7080 '
            'seconds and rearms. A wake reads the command\'s output file, which carries the message, and one '
            'resolve command also starts the next wait: three model turns per message. The worker stops at its '
            'brief\'s limit or the slot deadline (lifetime until-owner-stop has none). Nothing restarts after the '
            'client exits; the resumed chat must rebind. A dead wait shows offline and messages stay durable. Status '
            'is the last observed state, not provider liveness.'),
    },
    'parent-session': {
        'bounded_receive': ('unsupported', 'parent_queue_route',
                            'Parent routes deliver queued cards into the chat; there is no receive call.'),
        'continuous_background_reception': (
            'conditional', 'client_must_stay_available',
            'Grok TUI route on macOS and Windows, and the legacy Codex/Claude parent opt-in: session start delivers '
            'into this visible chat until stopped while the client and host stay available. On Windows Grok, run '
            'session start or recover as one background shell task, because Grok kills an ordinary tool call\'s '
            'children when it returns; reception lasts only while that background shell task stays alive. A Grok session restore or '
            'self-update can end the listener while the chat stays open; that chat then runs session recover. '
            'A Grok listener stops by itself once its TUI or leader stays gone. '
            'Nothing restarts after the client exits, logout or reboot.'),
    },
    'grok-cloud': {
        'bounded_receive': ('unsupported', 'parent_queue_route',
                            'The personal chat reads each message after a webhook wake hint.'),
        'continuous_background_reception': (
            'conditional', 'cloud_computer_must_stay_active',
            'Only while the cloud computer is active; suspended compute is not woken.'),
    },
    'claude-listener-pilot': {
        'bounded_receive': ('unsupported', 'separate_headless_handler', 'The listener claims messages itself.'),
        'continuous_background_reception': (
            'conditional', 'owner_subscription_approval',
            'listen start --approve-subscription after pilot setup; a detached process until stopped, logout or '
            'reboot. It does not connect or notify the main chat.'),
    },
}
ROUTES = tuple(_ROUTES)

# Supported, bounded alternatives the agent offers instead of promising until-stop operation.
ALTERNATIVES = (
    {'route': 'bounded-receive-while-active',
     'summary': 'Repeat bounded session receive calls through empty waits while this chat or worker brief is '
                'active, up to an owner-stated deadline or message count; stop only at that limit, on state '
                'stopped or a fatal error, and report the stop reason.'},
    {'route': 'durable-queue-resume',
     'summary': 'Messages stay durable while nobody receives; the owner resumes this chat or re-briefs the same '
                'worker later to drain them with fresh task-checks.'},
    {'route': 'claude-listener-pilot',
     'summary': 'Separate text-only Claude handler until stopped, with explicit owner subscription approval; '
                'no tools, checkout or project work.'},
)

_CLIENT_ENV = (('codex', 'CODEX_THREAD_ID'), ('claude', 'CLAUDE_CODE_SESSION_ID'), ('grok', 'GROK_SESSION_ID'))
# The quiet default route per client (workflow step 0); Grok has no worker inbox.
_DEFAULT_ROUTE = {'codex': 'codex-tool-inbox', 'claude': 'claude-worker', 'grok': 'parent-session'}


def default_route(environ=None):
    """(route, source) from which client variable names are present; never their values."""
    environ = os.environ if environ is None else environ
    clients = [client for client, name in _CLIENT_ENV if environ.get(name)]
    if len(clients) == 1:
        return _DEFAULT_ROUTE[clients[0]], 'client_env'
    return None, 'ambiguous_client_env' if clients else 'no_client_env'


def _grok_parent_limit(environ, platform):
    """Why this Grok chat cannot hold parent-session reception here; None when it may."""
    if platform not in GROK_LEADER_PLATFORMS:
        return ('unsupported', 'grok_leader_platform_unsupported',
                'The existing-chat Grok TUI route is qualified only on macOS and Windows; session connect, start '
                'and recover refuse here. A Grok Bot personal chat uses the grok-cloud route.')
    if not environ.get('GROK_LEADER_SOCKET'):
        return ('unsupported', 'leader_socket_missing',
                'This Grok chat exports no leader socket, so it cannot receive. Keep it open; its owner may later '
                'relaunch the same session with --leader --leader-socket <path> --resume (references/grok.md).')
    return None


def _entry(value):
    state, reason, detail = value
    return {'status': state, 'reason': reason, 'detail': detail}


def report(route=None, environ=None, platform=None):
    """Capability report for an explicit route, or the current client's default route."""
    environ = os.environ if environ is None else environ
    if route is not None and route not in _ROUTES:
        raise ValueError('invalid_route')
    source = 'explicit'
    if route is None:
        route, source = default_route(environ)
    if route is None:
        unknown = ('unsupported', 'route_unknown', 'Pass --route; no single client session was detected.')
        capabilities = {name: _entry(unknown) for name in CAPABILITIES}
    else:
        capabilities = {name: _entry(value) for name, value in _ROUTES[route].items()}
        # Only a Grok chat's own parent route depends on these; the legacy Codex/Claude opt-in does not.
        if route == 'parent-session' and default_route(environ)[0] == route:
            limit = _grok_parent_limit(environ, sys.platform if platform is None else platform)
            if limit: capabilities['continuous_background_reception'] = _entry(limit)
    capabilities['unattended_project_execution'] = _entry(_NO_UNATTENDED)
    return {'route': route, 'route_source': source, 'capabilities': capabilities,
            'live_agents': 'disabled', 'alternatives': [dict(item) for item in ALTERNATIVES]}


class CapabilityUnsupported(Exception):
    """A required capability is not supported on this route; carries the full fixed report."""
    code = 'background_execution_unsupported'

    def __init__(self, requirement, value):
        super().__init__(self.code)
        capability = REQUIREMENTS[requirement]
        self.data = {'requirement': requirement, 'capability': capability,
                     'reason': value['capabilities'][capability]['reason'], **value}


def check(route=None, require=None, environ=None):
    """The report, or CapabilityUnsupported when a required capability is not plainly supported."""
    value = report(route, environ)
    if require is not None:
        if require not in REQUIREMENTS:
            raise ValueError('invalid_requirement')
        if value['capabilities'][REQUIREMENTS[require]]['status'] != 'supported':
            raise CapabilityUnsupported(require, value)
    return value
