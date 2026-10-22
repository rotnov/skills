"""Fixed release metadata and fail-closed native-session compatibility checks."""
from functools import lru_cache
import json
from pathlib import Path
import re
from quorum_api import QuorumError


def versions(value):
    """Select only bounded, non-executable version fields from untrusted RPC data."""
    if (not isinstance(value, dict) or type(value.get('protocol_version')) is not int
            or not 1 <= value['protocol_version'] <= 2147483647
            or not isinstance(value.get('skill_version'), str)
            or not re.fullmatch(r'[0-9]{1,6}\.[0-9]{1,6}\.[0-9]{1,6}', value['skill_version'])):
        return None
    return {key:value[key] for key in ('protocol_version','skill_version')}


def read_release(path):
    """Use the same bounded manifest parser for runtime and staged updates."""
    try:
        with path.open('rb') as source: raw = source.read(1025)
        if len(raw) > 1024: raise ValueError
        result = versions(json.loads(raw))
        if result is None: raise ValueError
        return result
    except (OSError, ValueError, TypeError, UnicodeError):
        raise QuorumError('release_manifest_invalid') from None


@lru_cache(maxsize=1)
def installed_release():
    # Snapshot once per Python process. Replacing files never upgrades code that
    # is already loaded by a listener; that process must stop and restart.
    return read_release(Path(__file__).resolve().parents[1] / 'assets/release.json')


class ReleaseMismatch(QuorumError):
    def __init__(self, diagnostic):
        super().__init__('skill_update_required')
        self.diagnostic = diagnostic


class ReleaseGuard:
    def __init__(self, api, ledger, *, stop_on_mismatch=True):
        self.api, self.ledger = api, ledger
        self.stop_on_mismatch = stop_on_mismatch
        self.installed = installed_release().copy()

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
                      'update_required':required != self.installed}
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
        return self.installed.copy()

    def running(self):
        from quorum_listener import locked
        if locked(self.ledger.root/'listener.lock'):
            # A fresh helper cannot bless an older running Python process.
            run_id, active, compatible = self._listener(self.installed)
            if not compatible:
                from quorum_listener import Control
                if run_id: Control(self.ledger).request_stop(run_id)
                diagnostic = self.ledger.setting('release') or {}
                diagnostic = {**diagnostic, 'listener':versions(active), 'listener_run_verified':False,
                              'update_required':True, 'code':'skill_update_required'}
                self.ledger.setting('release', diagnostic)
                raise ReleaseMismatch(diagnostic)


def diagnostic(ledger):
    result = ledger.setting('release') or {'installed':installed_release().copy(),
                                         'required':None, 'update_required':None}
    return result
