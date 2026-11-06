"""Release identity and fail-closed native-session compatibility checks.

A release is identified by its content SHA-256, shown in its short form. `protocol_version` alone is the
compatibility gate. A listener behind the fleet target is shown, never refused.

`assets/release.json` still carries the frozen `skill_version`, and so does `pyproject.toml`, because installed
copies from before #168 refuse a staged copy without it and the reviewed server functions still take it as a
required argument. Here it is carried to those two places exactly as packaged: it is never compared and never
shown. Removing it is phase two (#172), once every listener reports a hash.
"""
from functools import lru_cache
import json
from pathlib import Path
import re
from quorum_api import QuorumError

# The one installer pin shared by the automatic refresh and every generated invitation.
INSTALLER = 'skills@>=1.7.4'
# The documented minimum Node.js for that installer (README, setup, update reference).
INSTALLER_NODE = (22, 20)
# What owners and agents see; the full digest stays the identity on the wire.
SHORT_HASH = 7
DIGEST = re.compile('[0-9a-f]{64}')
_CARRIED = 'skill_version'
_CARRIED_VALUE = re.compile(r'[0-9]{1,6}\.[0-9]{1,6}\.[0-9]{1,6}')
RELEASE_VIAS = ('heartbeat', 'update_report')


def short_hash(digest):
    """The chat form of a content hash, or None for anything that is not one."""
    return digest[:SHORT_HASH] if isinstance(digest, str) and DIGEST.fullmatch(digest) else None


def versions(value):
    """The compatibility gate from untrusted RPC data: only the bounded protocol integer."""
    if (not isinstance(value, dict) or type(value.get('protocol_version')) is not int
            or not 1 <= value['protocol_version'] <= 2147483647):
        return None
    return {'protocol_version':value['protocol_version']}


def _carried(value):
    """The frozen wire field when present and well formed. Opaque here: see the module note."""
    token = value.get(_CARRIED)
    return {_CARRIED:token} if isinstance(token, str) and _CARRIED_VALUE.fullmatch(token) else {}


def read_release(path):
    """Use the same bounded manifest parser for runtime and staged updates."""
    try:
        with path.open('rb') as source: raw = source.read(1025)
        if len(raw) > 1024: raise ValueError
        manifest = json.loads(raw)
        result = versions(manifest)
        if result is None: raise ValueError
        return {**result, **_carried(manifest)}
    except (OSError, ValueError, TypeError, UnicodeError):
        raise QuorumError('release_manifest_invalid') from None


@lru_cache(maxsize=1)
def installed_release():
    # Snapshot once per Python process. Replacing files never upgrades code that
    # is already loaded by a listener; that process must stop and restart.
    return read_release(Path(__file__).resolve().parents[1] / 'assets/release.json')


def source_identity(value):
    """Only inert, bounded source identity fields may cross maintenance RPCs."""
    release = versions(value)
    digest = value.get('content_sha256') if isinstance(value, dict) else None
    if release is None or not isinstance(digest, str) or not DIGEST.fullmatch(digest):
        return None
    return {**release, **_carried(value), 'content_sha256':digest}


def public_source(value):
    """What owners and agents see of a source identity, or None for an invalid one."""
    source = source_identity(value)
    if source is None: return None
    return {'protocol_version':source['protocol_version'], 'content_sha256':source['content_sha256'],
            'release':short_hash(source['content_sha256'])}


def without_carried(value):
    """A service record as shown: everything but the frozen wire field."""
    return {key:item for key, item in value.items() if key != _CARRIED}


def same_source(left, right):
    """One release: the same protocol and content hash, whatever else a record carries."""
    source = public_source(left)
    return source is not None and source == public_source(right)


@lru_cache(maxsize=1)
def installed_source():
    """Snapshot this process's source at runtime startup, separately from compatibility."""
    from quorum_update import content_hash
    try:
        return {**installed_release(), 'content_sha256':content_hash(Path(__file__).resolve().parents[1])}
    except (OSError, ValueError):
        raise QuorumError('runtime_source_unavailable') from None


def installed_identity():
    """This process's public identity. Unreadable source costs only the hash, never a compatibility check."""
    release = versions(installed_release())
    try: return public_source(installed_source())
    except QuorumError as error:
        if str(error) != 'runtime_source_unavailable': raise
        return {**release, 'content_sha256':None, 'release':None}


def runtime_evidence(ledger):
    """A new helper observes the resident run's marker, never its own source bytes."""
    from quorum_listener import locked
    running = locked(ledger.root/'listener.lock')
    rows = ledger.db.execute("select key,data from settings where key in ('listener','listener-source')")
    saved = {key:json.loads(raw) for key,raw in rows}
    run_id = (saved.get('listener') or {}).get('run_id')
    marker = saved.get('listener-source') or {}
    source = public_source(marker)
    verified = bool(running and run_id and marker.get('run_id') == run_id and source
                    and locked(ledger.root/'listener.lock'))
    return {'state':'running' if running else 'offline', 'run_id':run_id,
            'verified':verified, 'source':source if verified else None}


def public_diagnostic(value):
    """A saved compatibility diagnostic as shown. A run before #168 saved the retired number too."""
    if not isinstance(value, dict): return value
    shown = dict(value)
    for key in ('installed', 'required', 'listener'):
        if isinstance(value.get(key), dict):
            shown[key] = public_source(value[key]) or versions(value[key])
    return shown


def release_view(row, target):
    """One listener against the fleet target. A running hash is always that listener's own self-report."""
    running = row.get('content_sha256') if isinstance(row, dict) else None
    if not short_hash(running): running = None
    state = 'unreported' if running is None else 'no_target' if target is None else (
        'on_target' if running == target else 'behind')
    view = {'state':state, 'running':short_hash(running), 'target':short_hash(target)}
    if running is not None:
        via = row.get('reported_via')
        view.update(self_reported=True, reported_at=row.get('reported_at'),
                    reported_via=via if via in RELEASE_VIAS else None)
    return view


def release_status(api, scope, **page):
    """The fleet target and running self-reports, or None: a server without them shows peers as before."""
    try: value = api.call('session_release_status', {**scope, **page})
    except QuorumError: return None
    if not isinstance(value, dict) or not isinstance(value.get('agents'), list): return None
    target = value.get('target') if isinstance(value.get('target'), dict) else {}
    return {'target':target.get('content_sha256') if short_hash(target.get('content_sha256')) else None,
            'agents':{item['agent_id']:item for item in value['agents']
                      if isinstance(item, dict) and isinstance(item.get('agent_id'), str)}}


def peers_view(api, scope):
    """`session peers`: the service's rows without the retired number, plus each hash against the target."""
    peers = api.call('session_peers', scope)
    if not isinstance(peers, list): return peers
    fleet = release_status(api, scope) or {'target':None, 'agents':{}}
    shown = []
    for peer in peers:
        if isinstance(peer, dict):
            client, required = peer.get('client'), peer.get('required')
            peer = dict(peer, release=release_view(fleet['agents'].get(peer.get('agent_id')), fleet['target']))
            if isinstance(client, dict) and isinstance(required, dict):
                peer.update(client={'protocol_version':client.get('protocol_version')},
                            required={'protocol_version':required.get('protocol_version')},
                            update_required=client.get('protocol_version') != required.get('protocol_version'))
        shown.append(peer)
    return shown


class ReleaseMismatch(QuorumError):
    def __init__(self, diagnostic):
        super().__init__('skill_update_required')
        self.diagnostic = public_diagnostic(diagnostic)


class ReleaseGuard:
    def __init__(self, api, ledger, *, stop_on_mismatch=True):
        self.api, self.ledger = api, ledger
        self.stop_on_mismatch = stop_on_mismatch
        self.installed = installed_identity()

    def check(self):
        required = self.api.call('session_requirements', {})
        return self.accept(required)

    def _listener(self, required):
        # One SQLite snapshot pairs the marker with the current control run even
        # if a new owner publishes between fetching the two rows.
        rows = self.ledger.db.execute("select key,data from settings where key in ('listener','listener-release')")
        records = {key:json.loads(value) for key,value in rows}
        active = records.get('listener-release')
        run_id = (records.get('listener') or {}).get('run_id')
        compatible = (required is not None and versions(active) == required and bool(run_id)
                      and isinstance(active, dict) and active.get('run_id') == run_id)
        return run_id, active, compatible

    def accept(self, required):
        required = versions(required)
        diagnostic = {'installed':self.installed, 'required':required,
                      'update_required':required != versions(self.installed)}
        if diagnostic['update_required']: diagnostic['code'] = 'skill_update_required'
        self.ledger.setting('release', diagnostic)
        if diagnostic['update_required']:
            # A retained old helper still fails, but must not stop a verified
            # upgraded owner. Stale or unverified listeners remain fail-closed.
            from quorum_listener import Control, locked
            if self.stop_on_mismatch:
                run_id, _, compatible = self._listener(required)
                if run_id and not (locked(self.ledger.root/'listener.lock') and compatible):
                    Control(self.ledger).request_stop(run_id)
            raise ReleaseMismatch(diagnostic)
        return dict(self.installed)

    def running(self):
        from quorum_listener import locked
        if locked(self.ledger.root/'listener.lock'):
            # A fresh helper cannot bless an older running Python process.
            from quorum_listener import Control
            run_id, active, compatible = self._listener(versions(self.installed))
            # A stopped run is blessed by nothing: it is leaving, and a start waits it out before spawning.
            if not compatible and not Control(self.ledger).stopping(run_id):
                if run_id: Control(self.ledger).request_stop(run_id)
                diagnostic = self.ledger.setting('release') or {}
                running = public_source(self.ledger.setting('listener-source')) or versions(active)
                diagnostic = {**diagnostic, 'listener':running, 'listener_run_verified':False,
                              'update_required':True, 'code':'skill_update_required'}
                self.ledger.setting('release', diagnostic)
                raise ReleaseMismatch(diagnostic)


def diagnostic(ledger):
    saved = ledger.setting('release')
    if saved: return public_diagnostic(saved)
    return {'installed':installed_identity(), 'required':None, 'update_required':None}
