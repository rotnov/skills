"""Optional Quorum debug runs. A bounded local watch supervises; no live GitHub in tests."""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from quorum_debug_ledger import UUID_RE, in_scope, read_local
from quorum_debug_publish import GH_COMMANDS, GhCliPublisher, evidence_in, line_value, marker_in
from quorum_os import FileLock, protect, reject_link
from quorum_release import installed_release


class DebugError(Exception):
    """Fixed debug codes only. Data stays a small scalar dict."""

    CODES = frozenset({
        'debug_not_active', 'debug_run_active', 'debug_invalid_scope',
        'debug_grant_ended', 'debug_state_unavailable', 'debug_busy',
        'debug_artifact_rejected', 'debug_usage_rejected', 'debug_finding_unknown',
        'debug_conclusion_rejected', 'debug_publisher_denied', 'debug_failed',
    })

    def __init__(self, code, **data):
        if code not in self.CODES:
            code = 'debug_failed'
        super().__init__(code)
        self.code = code
        self.data = {key: value for key, value in data.items() if isinstance(value, (str, int, bool))}


SAFE = re.compile(r'^[A-Za-z0-9_.:@/+-]{1,128}$')
QUORUM_RE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$')
REPO_RE = re.compile(r'^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$')
RUN_RE = re.compile(r'^dbg-[0-9a-f]{32}$')
UNTIL_RE = re.compile(r'^(\d{1,4})([mhd])$')
ISO_RE = re.compile(r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$')
VERSION_RE = re.compile(r'^[0-9]{1,6}\.[0-9]{1,6}\.[0-9]{1,6}$')
FORBIDDEN_KEYS = frozenset({
    'body', 'text', 'reply', 'prompt', 'token', 'access_token', 'refresh_token',
    'nonce', 'invitation', 'secret', 'password', 'authorization', 'jwt',
    'publishable_key', 'api_key', 'webhook', 'credential', 'reply_digest',
    'private_key', 'invitation_token', 'bearer', 'cwd', 'messaging_socket',
    'grok_transport',
})
STUCK_SECONDS = 900
EMPTY_RECEIVE_COLLECTS = 8
PARENT_NOTIFICATIONS = 3
HELLO_EPOCHS = 3
REPLY_BURST = 4
GAP_SECONDS = 900
MAX_EVENTS = 400
MAX_FINDINGS = 80
MAX_DURATION = timedelta(days=14)
BINDING_KEYS = ('session-agent', 'cloud-agent', 'pilot-agent')
DELIVERY_STATES = ('intent', 'queued', 'submitted', 'offered', 'acked')
STAGE = {
    'intent': 'intent', 'queued': 'queued', 'submitted': 'transport_submitted',
    'offered': 'worker_offered', 'acked': 'receipt_acked',
}
LIMITATIONS = (
    'The bounded local watch observes only while its host process is alive. Activation without that host is a handoff, not complete observation.',
    'A stopped, expired, or dead host is not restored automatically.',
    'Manual collection publishes findings but does not supervise the run.',
    'Local metadata does not prove task acceptance or task completion.',
    'Missing telemetry is not definite loss.',
    'Definite non-delivery is not inferred from the local ledger.',
    'Imported artifacts are not live observation.',
    "Other owners' private state is outside this grant.",
    'Parent-history quietness is not proved by metadata.',
    'Token totals use only authorized client-reported usage; message length is not model usage.',
    'Cache tokens are added only when the report says they are disjoint. Cache-inclusive input is not added again.',
    'The same usage measurement is stored once. A conflicting same-source window is rejected. A later shared-parent window is excluded.',
    'Attributed coordination usage is not a counterfactual cost saving.',
    'Duplicate side effects are not inferred from the local ledger.',
    'No server export or service-role observation is used.',
    'Expected realtime fallback, idempotent retries, and repeated unacknowledged offers are not definite defects.',
    'An uncertain GitHub create or comment is not repeated until reconciliation finds the exact finding marker.',
    'A historical reply count or an empty snapshot is not a model turn or a reply loop. Idle listener samples are not receive invocations or model turns. A reply burst is only one sequence-ordered connected chain; missing linkage is not that defect.',
)
HOST_STALE_PADDING = 75
TOKEN_FIELDS = ('input_tokens', 'output_tokens', 'cache_read_tokens', 'cache_write_tokens')


def utcnow():
    return datetime.now(timezone.utc).replace(microsecond=0)


def iso(value):
    return value.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def parse_iso(value):
    if not isinstance(value, str) or not ISO_RE.fullmatch(value):
        return None
    try:
        return datetime.strptime(value, '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def safe(value):
    if isinstance(value, str) and SAFE.fullmatch(value):
        return value
    return None


def _windows_pid_alive(pid):
    """Query-only liveness. os.kill(pid, 0) terminates a Windows process."""
    if pid > 0xFFFFFFFF:
        return False
    try:
        import ctypes
        from ctypes import wintypes
    except (ImportError, AttributeError, OSError):
        return False
    try:
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    except (AttributeError, OSError):
        return False
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel.GetExitCodeProcess.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    handle = None
    try:
        handle = kernel.OpenProcess(0x1000, False, pid)
        if not handle:
            return False
        code = wintypes.DWORD()
        if not kernel.GetExitCodeProcess(handle, ctypes.byref(code)):
            return False
        return code.value == 259
    except (AttributeError, OSError, TypeError, ValueError):
        return False
    finally:
        if handle:
            try:
                kernel.CloseHandle(handle)
            except (AttributeError, OSError, TypeError, ValueError):
                pass


def digest(payload):
    raw = json.dumps(payload, sort_keys=True, separators=(',', ':')).encode()
    return hashlib.sha256(raw).hexdigest()


def forbid_keys(value):
    if isinstance(value, dict):
        for key, item in value.items():
            if str(key).lower() in FORBIDDEN_KEYS:
                return True
            if forbid_keys(item):
                return True
    elif isinstance(value, list):
        return any(forbid_keys(item) for item in value)
    elif isinstance(value, str) and (len(value) > 128 or not SAFE.fullmatch(value)):
        # Short numeric and boolean leaves are checked by their own types.
        if value != '':
            return True
    return False


def subprocess_run(args, timeout=20):
    if not args or args[0] != 'gh' or tuple(args[1:3]) not in GH_COMMANDS:
        raise DebugError('debug_publisher_denied')
    if '--body' in args:
        raise DebugError('debug_publisher_denied')
    return subprocess.run(args, capture_output=True, text=True, timeout=timeout, check=False)


def build_publisher():
    return GhCliPublisher(subprocess_run)


def target_for(transport):
    return 'worker' if transport == 'tool-inbox' else 'parent'


class DebugService:
    _LOCK_ACQUIRE_SECONDS = 15

    def __init__(self, root, *, clock=None, publisher=None, sleeper=None, alive_check=None, pid=None):
        self.root = Path(root)
        self.clock = clock or utcnow
        self.publisher = publisher if publisher is not None else build_publisher()
        self.sleeper = sleeper or time.sleep
        self.alive_check = alive_check
        self.pid = pid or os.getpid
        self._host_token = None
        if not self.root.is_absolute():
            raise DebugError('debug_state_unavailable')

    def activate(self, quorum, project_ref, report_repository=None, until='stop', bindings=(), sources=()):
        scope = self._scope(quorum, project_ref, report_repository, until, bindings, sources)
        with self._lock():
            current = self._load()
            if current and current.get('state') == 'active' and not self._expired(current):
                if not self._same_scope(current, scope):
                    raise DebugError('debug_run_active')
                self._count(current)
                self._save(current)
                view = self._activation_view(current, idempotent=True)
                return view
            if current:
                self._archive(current)
            run = self._new_run(scope)
            if 'local-ledger' in scope['sources']:
                self._observe(run, baseline=True)
            self._count(run)
            self._save(run)
            return self._activation_view(run, idempotent=False)

    def status(self):
        with self._lock():
            run = self._load()
            if run is None:
                return {
                    'state': 'inactive', 'continuous_observation': False,
                    'automatic_runtime_restoration': False, 'durable': True,
                    'observation_gap': True, 'collector': 'bounded_local_watch',
                    'host_alive': False, 'supervision': self._supervision_view(None),
                }
            self._expire(run)
            self._note_host_failure(run)
            self._count(run)
            self._save(run)
            return self._status_view(run)

    def stop(self):
        with self._lock():
            run = self._require()
            if run['state'] == 'active':
                run['state'] = 'stopped'
                run['stopped_at'] = iso(self.clock())
            self._count(run)
            self._save(run)
            return self._status_view(run)

    def report(self, run_id=None):
        with self._lock():
            run = self._load_id(run_id) if run_id else self._require()
            if run is None:
                raise DebugError('debug_not_active')
            if run_id is None:
                self._expire(run)
                self._note_host_failure(run)
                self._count(run)
                self._save(run)
            return self._report_view(run)

    def collect(self, publish=True):
        with self._lock():
            run = self._require()
            self._expire(run)
            self._note_host_failure(run)
            if run['state'] != 'active':
                self._save(run)
                return {'state': run['state'], 'collected': False, 'publication': 'ended',
                        'live_observation': False, 'continuous_observation': False,
                        'host_alive': self._host_alive(run), 'observation_gap': True}
            before = len(run['events'])
            self._note_gap(run)
            if 'local-ledger' in run['scope']['sources']:
                self._observe(run, baseline=False)
                self._apply_rules(run)
            self._count(run)
            self._save(run)
            events_added = len(run['events']) - before
        publication = self._publish_pending() if publish else 'skipped'
        with self._lock():
            run = self._require()
            return {
                'state': run['state'], 'collected': True,
                'live_observation': run['collection']['live_observation'],
                'events_added': events_added, 'publication': publication,
                'observation_gap': self._gap_open(run), 'continuous_observation': self._continuous(run),
                'host_alive': self._host_alive(run),
                'findings': [item['finding_id'] for item in run['findings'] if item['actionable']],
            }

    def import_artifact(self, path):
        path = Path(path)
        reject_link(path)
        try:
            if not path.is_file() or path.stat().st_size > 131072:
                raise DebugError('debug_artifact_rejected')
            data = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            raise DebugError('debug_artifact_rejected') from error
        if not isinstance(data, dict) or not isinstance(data.get('events'), list) or forbid_keys(data):
            raise DebugError('debug_artifact_rejected')
        with self._lock():
            run = self._require()
            self._expire(run)
            if run['state'] != 'active':
                raise DebugError('debug_grant_ended')
            if 'imported-artifact' not in run['scope']['sources']:
                raise DebugError('debug_artifact_rejected')
            for raw in data['events']:
                if not isinstance(raw, dict):
                    raise DebugError('debug_artifact_rejected')
                self._import_event(run, raw)
            self._apply_import_rules(run)
            self._count(run)
            self._save(run)
            imported = len(data['events'])
        publication = self._publish_pending()
        with self._lock():
            run = self._require()
            return {'imported': imported, 'live_observation': False, 'publication': publication,
                    'state': run['state']}

    def add_usage(self, record):
        cleaned = self._usage_record(record)
        with self._lock():
            run = self._require()
            self._expire(run)
            if run['state'] != 'active':
                raise DebugError('debug_grant_ended')
            self._store_usage(run, cleaned)
            self._count(run)
            self._save(run)
            return self._token_view(run)

    def analyze(self, finding_id, conclusion, confidence):
        if confidence not in ('low', 'medium', 'high') or not isinstance(conclusion, str):
            raise DebugError('debug_conclusion_rejected')
        text = conclusion.strip()
        if not 1 <= len(text) <= 400 or any(ord(char) < 32 and char != '\n' for char in text):
            raise DebugError('debug_conclusion_rejected')
        lowered = text.lower()
        if any(marker in lowered for marker in ('token=', 'bearer', 'nonce', 'secret')):
            raise DebugError('debug_conclusion_rejected')
        with self._lock():
            run = self._require()
            found = next((item for item in run['findings'] if item['finding_id'] == finding_id), None)
            if found is None:
                raise DebugError('debug_finding_unknown')
            found['analysis'] = 'recorded'
            found['conclusion'] = text
            found['confidence'] = confidence
            self._count(run)
            self._save(run)
            return {'finding_id': finding_id, 'analysis': 'recorded'}

    def publish(self):
        with self._lock():
            run = self._require()
            self._expire(run)
            if run['state'] != 'active':
                self._save(run)
                ended = {'publication': 'ended', 'state': run['state'], 'pending': list(run['pending'])}
            else:
                self._count(run)
                self._save(run)
                ended = None
        if ended:
            return ended
        result = self._publish_pending()
        with self._lock():
            run = self._require()
            return {'publication': result, 'pending': list(run['pending']), 'state': run['state']}

    def watch(self, interval=30, cycles=None, sleeper=None, on_cycle=None):
        """Foreground local collector. Stop and expiry end it; the host record is not a liveness claim."""
        if type(interval) is not int or not 1 <= interval <= 120:
            raise DebugError('debug_invalid_scope', field='interval')
        if cycles is not None and (type(cycles) is not int or not 1 <= cycles <= 100000):
            raise DebugError('debug_invalid_scope', field='cycles')
        sleeper = sleeper or self.sleeper
        self._attach_host(interval)
        completed = 0
        try:
            while cycles is None or completed < cycles:
                if not self._heartbeat():
                    break
                if on_cycle is not None:
                    on_cycle()
                if not self._grant_running():
                    break
                self.collect(publish=True)
                completed += 1
                if cycles is not None and completed >= cycles:
                    break
                if not self._sleep_until(interval, sleeper):
                    break
        finally:
            self._detach_host('cycle_limit' if cycles is not None and completed >= (cycles or 0) else 'stopped')
        view = self.status()
        view['cycles_completed'] = completed
        return view

    def _scope(self, quorum, project_ref, report_repository, until, bindings, sources):
        if not isinstance(quorum, str) or not QUORUM_RE.fullmatch(quorum):
            raise DebugError('debug_invalid_scope', field='quorum')
        if not isinstance(project_ref, str) or not SAFE.fullmatch(project_ref) or '..' in project_ref:
            raise DebugError('debug_invalid_scope', field='project_ref')
        if report_repository is not None and (not isinstance(report_repository, str) or not REPO_RE.fullmatch(report_repository)):
            raise DebugError('debug_invalid_scope', field='report_repository')
        selected = list(dict.fromkeys(sources or ('local-ledger',)))
        if not selected or any(item not in ('local-ledger', 'imported-artifact') for item in selected):
            raise DebugError('debug_invalid_scope', field='sources')
        clean_bindings = []
        for binding in bindings or ():
            if not isinstance(binding, str) or not SAFE.fullmatch(binding):
                raise DebugError('debug_invalid_scope', field='bindings')
            if binding not in clean_bindings:
                clean_bindings.append(binding)
        lifetime, expires = self._until(until)
        return {
            'quorum': quorum, 'project_ref': project_ref, 'report_repository': report_repository,
            'sources': selected, 'bindings': clean_bindings, 'lifetime': lifetime, 'expires_at': expires,
        }

    def _until(self, until):
        if until in (None, '', 'stop'):
            return 'until_stop', None
        if not isinstance(until, str):
            raise DebugError('debug_invalid_scope', field='until')
        matched = UNTIL_RE.fullmatch(until)
        if matched:
            amount, unit = int(matched.group(1)), matched.group(2)
            factor = {'m': 60, 'h': 3600, 'd': 86400}[unit]
            delta = timedelta(seconds=amount * factor)
            if amount == 0 or delta > MAX_DURATION:
                raise DebugError('debug_invalid_scope', field='until')
            return 'until_expiry', iso(self.clock() + delta)
        parsed = parse_iso(until)
        if parsed is None or parsed <= self.clock() or parsed - self.clock() > MAX_DURATION:
            raise DebugError('debug_invalid_scope', field='until')
        return 'until_expiry', iso(parsed)

    def _same_scope(self, run, scope):
        current = run['scope']
        # A repeated request must not extend or replace the lifetime already granted.
        return all(current[key] == scope[key] for key in ('quorum', 'project_ref', 'report_repository', 'sources', 'bindings'))

    def _new_run(self, scope):
        versions = installed_release()
        return {
            'record_version': 1, 'run_id': 'dbg-' + uuid.uuid4().hex, 'state': 'active',
            'started_at': iso(self.clock()), 'stopped_at': None, 'expired_at': None,
            'expires_at': scope['expires_at'], 'versions': {
                'protocol_version': versions['protocol_version'], 'skill_version': versions['skill_version'],
            },
            'scope': {key: scope[key] for key in ('quorum', 'project_ref', 'report_repository', 'sources', 'bindings', 'lifetime')},
            'collection': {
                'state': 'not_read', 'live_observation': False, 'last_collected_at': None,
                'collector': 'bounded_local_watch',
            },
            'host': {'pid': None, 'token': None, 'heartbeat_at': None, 'interval_seconds': None, 'state': 'handoff'},
            'coverage': {
                'participants': [], 'unavailable_bindings': [], 'engines': [], 'unreadable': [],
                'metadata_only': True, 'task_acceptance': 'unavailable', 'task_completion': 'unavailable',
                'definite_non_delivery': 'not_inferable_from_local_metadata', 'other_owners': 'unavailable',
            },
            'watch': {'deliveries': {}, 'epochs': {}, 'listeners': {}, 'waits': {}, 'replies': {}, 'side_effects': {}},
            'events': [], 'events_dropped': 0, 'findings': [], 'findings_dropped': 0,
            'observations': [], 'gaps': [], 'publication': [], 'pending': [],
            'usage': {
                'totals': {}, 'reported_sources': [], 'windows': [], 'records': [],
                'excluded': 0, 'debugger_invocations': 0,
            },
        }

    def _observe(self, run, *, baseline):
        now = iso(self.clock())
        snapshots = read_local(self.root, run['scope'])
        participants, unavailable, unreadable, engines = [], [], [], []
        seen, seen_keys = set(), set()
        for snapshot in snapshots:
            binding = snapshot.get('binding')
            if snapshot.get('unreadable'):
                if binding not in unreadable:
                    unreadable.append(binding)
                continue
            if not in_scope(snapshot, run['scope']):
                continue
            seen.add(binding)
            engines.append(snapshot.get('engine') or 'unknown')
            participants.append({
                'binding': binding, 'engine': snapshot.get('engine'), 'role': snapshot.get('role'),
                'transport': snapshot.get('transport'), 'agent_id': snapshot.get('agent_id'),
            })
            self._ingest_snapshot(run, snapshot, now, baseline)
            for delivery in snapshot.get('deliveries') or []:
                message_id = delivery.get('message_id')
                if message_id:
                    seen_keys.add(binding + ':' + message_id)
        for binding in run['scope']['bindings']:
            if binding not in seen and binding not in unreadable:
                unavailable.append(binding)
        self._refresh_source_freshness(run, seen, seen_keys, now)
        run['coverage'].update(participants=participants, unavailable_bindings=unavailable,
                               unreadable=unreadable, engines=sorted(set(engines)))
        if seen:
            collection_state, live = 'attached', True
        elif unreadable:
            collection_state, live = 'unreadable', False
        else:
            collection_state, live = 'no_source', False
        run['collection'].update(state=collection_state, live_observation=live, last_collected_at=now)

    def _refresh_source_freshness(self, run, seen_bindings, seen_keys, now):
        deliveries = run['watch'].get('deliveries') or {}
        grouped = {}
        for key, item in deliveries.items():
            binding = item.get('binding') or key.split(':', 1)[0]
            grouped.setdefault(binding, []).append((key, item))
        for binding, items in grouped.items():
            if binding in seen_bindings:
                for key, item in items:
                    if key in seen_keys and item.get('stale'):
                        item['since'] = now
                        item['stale'] = False
                    elif key not in seen_keys:
                        item['stale'] = True
                if any(key in seen_keys for key, _item in items):
                    self._close_source_gap(run, binding, now)
                continue
            started = next((item.get('since') for _key, item in items if item.get('since')), None)
            for _key, item in items:
                item['stale'] = True
            self._open_source_gap(run, binding, started, now)

    def _open_source_gap(self, run, binding, started, now):
        reason = 'source_not_observed'
        for gap in run['gaps']:
            if gap.get('binding') == binding and gap.get('reason') == reason and not gap.get('closed'):
                gap['to'] = now
                return
        if len(run['gaps']) >= 40:
            return
        run['gaps'].append({
            'from': started or now, 'to': now, 'reason': reason, 'binding': binding, 'closed': False,
        })

    def _close_source_gap(self, run, binding, now):
        for gap in run['gaps']:
            if gap.get('binding') == binding and gap.get('reason') == 'source_not_observed' and not gap.get('closed'):
                gap['to'] = now
                gap['closed'] = True

    def _ingest_snapshot(self, run, snapshot, now, baseline):
        binding = snapshot['binding']
        transport = snapshot.get('transport') or 'native'
        for delivery in snapshot['deliveries']:
            key = binding + ':' + delivery['message_id']
            previous = run['watch']['deliveries'].get(key)
            if previous is None or previous['state'] != delivery['state']:
                delivery = {**delivery, 'since': now, 'binding': binding, 'transport': transport,
                            'role': snapshot.get('role'), 'target': target_for(transport)}
                run['watch']['deliveries'][key] = delivery
                self._event(run, {
                    'source': 'local-ledger', 'live_observation': True, 'baseline': baseline,
                    'binding': binding, 'agent_id': snapshot.get('agent_id'), 'message_id': delivery['message_id'],
                    'conversation_id': delivery.get('conversation_id'), 'stage': STAGE[delivery['state']],
                    'state': delivery['state'], 'target': delivery['target'], 'clock': 'collector',
                    'observed_at': now,
                })
            self._note_reply(run, delivery)
            self._remember_chain_candidate(run, delivery, baseline)
        epoch = snapshot.get('epoch')
        if epoch:
            epochs = run['watch']['epochs'].setdefault(binding, [])
            if not epochs or epochs[-1] != epoch:
                epochs.append(epoch)
                self._event(run, {
                    'source': 'local-ledger', 'live_observation': True, 'baseline': baseline,
                    'binding': binding, 'stage': 'hello', 'epoch': epoch, 'clock': 'collector', 'observed_at': now,
                })
        listener = snapshot.get('listener') or {}
        stored = run['watch']['listeners'].setdefault(binding, {})
        signature = [
            listener.get('run_id'), listener.get('state'), listener.get('handled'),
            snapshot.get('reception_enabled'), listener.get('code'),
        ]
        previous = stored.get('signature')
        if not isinstance(previous, (list, tuple)) or list(previous) != signature:
            self._event(run, {
                'source': 'local-ledger', 'live_observation': True, 'baseline': baseline, 'binding': binding,
                'stage': 'listener', 'run_id': listener.get('run_id'), 'listener_state': listener.get('state'),
                'handled': listener.get('handled'), 'reception_enabled': snapshot.get('reception_enabled'),
                'code': listener.get('code'), 'clock': 'collector', 'observed_at': now,
            })
        ids = sorted(item['message_id'] for item in snapshot['deliveries'])
        empty = stored.get('empty_collects', 0)
        waiting = listener.get('state') in ('waiting', 'checking') and listener.get('handled', 0) == 0
        if not baseline:
            if waiting and ids == stored.get('delivery_ids'):
                empty += 1
            else:
                empty = 0
        stored.update(signature=signature, run_id=listener.get('run_id'), state=listener.get('state'),
                      handled=listener.get('handled'), reception=snapshot.get('reception_enabled'),
                      previous_run_id=stored.get('run_id'), empty_collects=empty, delivery_ids=ids,
                      code=listener.get('code'))
        # previous_run_id was overwritten above; keep the prior value for the rule.
        if 'prior_run_id' not in stored:
            stored['prior_run_id'] = None
        prior = stored.get('seen_run_id')
        stored['prior_run_id'] = prior
        stored['seen_run_id'] = listener.get('run_id')
        waiting_mode = snapshot.get('wait') or {}
        previous_mode = run['watch']['waits'].get(binding)
        if waiting_mode != previous_mode:
            run['watch']['waits'][binding] = waiting_mode
            if waiting_mode.get('mode'):
                self._event(run, {
                    'source': 'local-ledger', 'live_observation': True, 'baseline': baseline, 'binding': binding,
                    'stage': 'realtime', 'mode': waiting_mode.get('mode'), 'code': waiting_mode.get('code'),
                    'clock': 'collector', 'observed_at': now,
                })
        for attempt in snapshot['attempts']:
            self._event(run, {
                'source': 'local-ledger', 'live_observation': True, 'baseline': baseline, 'binding': binding,
                'stage': 'attempt', 'attempt_id': attempt['attempt_id'], 'generation': attempt['generation'],
                'state': attempt['state'], 'clock': 'collector', 'observed_at': now,
            })
        for item in snapshot['ledger_events']:
            event = {
                'source': 'local-ledger', 'live_observation': True, 'baseline': baseline, 'binding': binding,
                'stage': 'ledger', 'clock': 'collector', 'observed_at': now, **item,
            }
            if type(item.get('ledger_clock')) in (int, float):
                skew = abs(item['ledger_clock'] - self.clock().timestamp())
                event['clock'] = 'skew_uncertain' if skew > 120 else 'collector'
            event.pop('ledger_clock', None)
            self._event(run, event)

    def _event(self, run, event):
        event = {key: value for key, value in event.items() if value is not None}
        identity = [event.get('source'), event.get('binding'), event.get('stage'),
                    event.get('message_id'), event.get('state'), event.get('epoch'),
                    event.get('run_id'), event.get('mode'), event.get('attempt_id'),
                    event.get('name')]
        if event.get('stage') == 'listener':
            identity.extend((event.get('listener_state'), event.get('handled'),
                             event.get('code'), event.get('reception_enabled')))
        event['event_id'] = digest(identity)[:32]
        if any(item['event_id'] == event['event_id'] for item in run['events']):
            return
        if len(run['events']) >= MAX_EVENTS:
            run['events'].pop(0)
            run['events_dropped'] += 1
        run['events'].append(event)

    def _apply_rules(self, run):
        now = self.clock()
        for item in run['watch']['deliveries'].values():
            if item.get('stale'):
                continue
            started = parse_iso(item['since'])
            age = (now - started).total_seconds() if started else 0
            state = item['state']
            if item.get('notification') and item['target'] == 'parent':
                self._note_parent(run, item)
            if state == 'acked':
                continue
            rule = {'intent': 'delivery_unknown', 'queued': 'stuck_queue'}.get(state, 'stuck_unacknowledged')
            evidence = [{'message_id': item['message_id'], 'binding': item['binding'], 'state': state,
                         'stage': STAGE[state], 'target': item['target']}]
            draft = self._finding(run, rule, evidence, identity={'message_id': item['message_id'], 'binding': item['binding']},
                                  definite=False, confidence='medium', actionable=True,
                                  live=True, delivery_stage=STAGE[state], analysis='not_required',
                                  expected={
                                      'delivery_unknown': 'Intent is followed by a known queued, submitted, offered, or acknowledged stage.',
                                      'stuck_queue': 'A queued delivery leaves the queue or becomes acknowledged.',
                                      'stuck_unacknowledged': 'A submitted or offered delivery is acknowledged or changes stage.',
                                  }[rule],
                                  observed=f"message {item['message_id']} stayed {state} for {int(age)}s; this is not task acceptance, completion, or definite loss.")
            existing = self._finding_by_id(run, draft['finding_id'])
            if age >= STUCK_SECONDS or (existing and existing['evidence_hash'] != draft['evidence_hash']):
                self._upsert(run, draft)
        for binding, epochs in run['watch']['epochs'].items():
            if len(epochs) >= HELLO_EPOCHS:
                self._observation(run, f'hello:{binding}', 'repeated_hello_epochs',
                                  {'binding': binding, 'epochs': len(epochs), 'definite': False})
        for binding, listener in run['watch']['listeners'].items():
            if listener.get('empty_collects', 0) >= EMPTY_RECEIVE_COLLECTS:
                # Sample count is internal. The producer has no empty-receive invocation counter.
                self._observation(run, f'idle:{binding}', 'healthy_idle_samples', {
                    'binding': binding, 'samples': EMPTY_RECEIVE_COLLECTS, 'definite': False,
                    'invocations': 'unavailable', 'model_turns': 'unavailable',
                })
            prior, current = listener.get('prior_run_id'), listener.get('seen_run_id')
            if listener.get('reception') is False and prior and current and prior != current:
                evidence = [{'binding': binding, 'prior_run_id': prior, 'run_id': current, 'reception_enabled': False}]
                self._upsert(run, self._finding(
                    run, 'reception_disabled_listener_running', evidence, identity={'binding': binding},
                    definite=False, confidence='medium',
                    actionable=True, live=True, delivery_stage='unknown', analysis='not_required',
                    expected='Stopped reception is not followed by a new listener run.',
                    observed='A new listener run id appeared while reception was disabled.'))
            if listener.get('code') == 'native_inbox_reply_loop_denied':
                self._observation(run, f'reply-guard:{binding}', 'reply_loop_guard',
                                  {'binding': binding, 'definite': False})
        for binding, waiting in run['watch']['waits'].items():
            if waiting.get('mode') == 'polling_fallback':
                self._observation(run, f'fallback:{binding}', 'realtime_fallback',
                                  {'binding': binding, 'code': waiting.get('code'), 'definite': False})
            elif waiting.get('mode') and waiting.get('mode') != 'polling_fallback':
                self._observation(run, f'recovered:{binding}', 'realtime_recovered',
                                  {'binding': binding, 'mode': waiting.get('mode'), 'definite': False})
        self._fold_reply_chains(run)
        for conversation, chain in run['watch'].get('reply_chains', {}).items():
            replies = chain.get('replies') or []
            if len(replies) < REPLY_BURST:
                continue
            last = replies[-REPLY_BURST:]
            linked = all(last[index]['target'] == last[index - 1]['id'] for index in range(1, REPLY_BURST))
            senders = [item['sender'] for item in last]
            if linked and senders[0] == senders[2] and senders[1] == senders[3] and senders[0] != senders[1]:
                evidence = [{'conversation_id': conversation, 'message_id': item['id']} for item in last]
                self._upsert(run, self._finding(
                    run, 'suspected_reply_loop', evidence, identity={'conversation_id': conversation},
                    definite=False, confidence='low', actionable=True,
                    live=True, delivery_stage='unknown', analysis='pending',
                    expected='Fresh replies form one connected chain back to the previous message.',
                    observed=f'{REPLY_BURST} linked replies alternated in conversation {conversation}.'))
        for conversation, identifiers in run['watch'].get('reply_ids', {}).items():
            observed = (run['watch'].get('reply_chains', {}).get(conversation) or {}).get('replies') or []
            if len(identifiers) >= REPLY_BURST and len(observed) < REPLY_BURST:
                self._observation(run, f'historical-replies:{conversation}', 'historical_reply_count', {
                    'conversation_id': conversation, 'replies': len(identifiers), 'definite': False,
                    'model_turns': 'unavailable', 'reply_loop': False,
                })
        self._parent_findings(run)

    def _note_reply(self, run, delivery):
        if not (delivery.get('reply_to') and delivery.get('conversation_id') and delivery.get('sender_id')):
            return None
        conversation = delivery['conversation_id']
        identifiers = run['watch'].setdefault('reply_ids', {}).setdefault(conversation, [])
        if delivery['message_id'] in identifiers:
            return None
        identifiers.append(delivery['message_id'])
        return {'id': delivery['message_id'], 'sender': delivery['sender_id'],
                'recipient': delivery.get('recipient_id'), 'conversation_id': conversation}

    def _remember_chain_candidate(self, run, delivery, baseline):
        """Keep ids, the boolean reply flag, and a UUID target. Never a body."""
        message_id = delivery.get('message_id')
        if not isinstance(message_id, str) or not message_id:
            return
        seen = run['watch'].setdefault('seen_delivery_ids', [])
        if message_id in seen:
            return
        seen.append(message_id)
        if baseline:
            return
        conversation = delivery.get('conversation_id')
        if not isinstance(conversation, str) or not conversation:
            return
        sequence = delivery.get('sequence')
        target = delivery.get('reply_target')
        sender = delivery.get('sender_id')
        run['watch'].setdefault('pending_chain', []).append({
            'id': message_id,
            'conversation_id': conversation,
            'sender': sender if isinstance(sender, str) else None,
            'reply_to': bool(delivery.get('reply_to')),
            'reply_target': target if isinstance(target, str) and UUID_RE.fullmatch(target) else None,
            'sequence': sequence if type(sequence) is int else None,
        })

    def _fold_reply_chains(self, run):
        pending = run['watch'].pop('pending_chain', [])
        grouped = {}
        for item in pending:
            grouped.setdefault(item['conversation_id'], []).append(item)
        chains = run['watch'].setdefault('reply_chains', {})
        for conversation, items in grouped.items():
            chain = chains.setdefault(conversation, {'head': None, 'sequence': None, 'replies': []})
            self._extend_reply_chain(chain, items)

    def _extend_reply_chain(self, chain, items):
        sequenced = [item for item in items if type(item.get('sequence')) is int]
        unsequenced = [item for item in items if type(item.get('sequence')) is not int]
        if any(not item.get('reply_to') for item in unsequenced) and sequenced:
            chain['replies'] = []
            chain['head'] = None
            chain['sequence'] = None
            return
        groups = {}
        for item in sequenced:
            groups.setdefault(item['sequence'], []).append(item)
        if any(len(group) > 1 for group in groups.values()):
            tied = [item for group in groups.values() if len(group) > 1 for item in group]
            if any(not item.get('reply_to') for item in tied):
                chain['replies'] = []
                chain['head'] = None
                chain['sequence'] = None
            return
        for item in sorted(sequenced, key=lambda item: item['sequence']):
            self._apply_chain_item(chain, item)
        for item in unsequenced:
            self._apply_chain_item(chain, item)
        chain['replies'] = chain['replies'][-REPLY_BURST:]

    def _apply_chain_item(self, chain, item):
        sequence = item.get('sequence') if type(item.get('sequence')) is int else None
        if not item.get('reply_to'):
            chain['replies'] = []
            if sequence is None:
                chain['head'] = None
                chain['sequence'] = None
            else:
                chain['head'] = item['id']
                chain['sequence'] = sequence
            return
        target = item.get('reply_target')
        sender = item.get('sender')
        if not isinstance(target, str) or sequence is None or not sender:
            return
        head = chain.get('head')
        head_sequence = chain.get('sequence')
        if target == head and type(head_sequence) is int and sequence > head_sequence:
            chain['replies'].append({'id': item['id'], 'sender': sender, 'target': target})
            chain['head'] = item['id']
            chain['sequence'] = sequence
            return
        if target != head:
            chain['replies'] = []
            chain['head'] = item['id'] if sender else None
            chain['sequence'] = sequence if sender else None

    def _note_parent(self, run, item):
        notes = run['watch'].setdefault('parent_notes', {})
        bucket = notes.setdefault(item['binding'], [])
        if item['message_id'] not in bucket:
            bucket.append(item['message_id'])

    def _parent_findings(self, run):
        for binding, messages in run['watch'].get('parent_notes', {}).items():
            if len(messages) >= PARENT_NOTIFICATIONS:
                evidence = [{'binding': binding, 'message_id': message, 'target': 'parent'}
                            for message in messages[:8]]
                self._upsert(run, self._finding(
                    run, 'routine_envelopes_reaching_parent', evidence, identity={'binding': binding},
                    definite=False, confidence='medium',
                    actionable=True, live=True, delivery_stage='unknown', analysis='not_required',
                    expected='Routine notification envelopes do not repeatedly reach the parent.',
                    observed=f'{len(messages)} notification deliveries reached the parent binding.'))

    def _import_event(self, run, raw):
        kind = safe(raw.get('kind')) or 'imported'
        event = {
            'source': 'imported-artifact', 'live_observation': False, 'baseline': False,
            'stage': safe(raw.get('stage')) or kind, 'kind': kind, 'clock': 'imported',
            'observed_at': iso(self.clock()),
        }
        for field in ('message_id', 'binding', 'attempt_id', 'side_effect_key', 'conversation_id', 'state'):
            item = safe(raw.get(field))
            if item:
                event[field] = item
        observed = parse_iso(raw.get('observed_at')) if isinstance(raw.get('observed_at'), str) else None
        if observed is not None and abs((observed - self.clock()).total_seconds()) > 120:
            event['clock'] = 'skew_uncertain'
        self._event(run, event)
        if kind == 'side_effect' and event.get('side_effect_key') and event.get('attempt_id') and event.get('stage') == 'task_completed':
            bucket = run['watch']['side_effects'].setdefault(event['side_effect_key'], [])
            if event['attempt_id'] not in bucket:
                bucket.append(event['attempt_id'])

    def _apply_import_rules(self, run):
        for key, attempts in run['watch']['side_effects'].items():
            if len(attempts) < 2:
                continue
            evidence = [{'side_effect_key': key, 'attempt_id': attempt, 'stage': 'task_completed'} for attempt in attempts[:8]]
            self._upsert(run, self._finding(
                run, 'duplicate_side_effect', evidence, identity={'side_effect_key': key},
                definite=True, confidence='high', actionable=True,
                live=False, delivery_stage='task_completed', analysis='not_required',
                expected='One side effect completes once.',
                observed='An imported artifact reports more than one completed attempt for the same side effect.'))

    def _finding(self, run, rule, evidence, *, identity, definite, confidence, actionable, live, delivery_stage, analysis, expected, observed):
        subject = {'rule': rule, 'quorum': run['scope']['quorum'], 'project_ref': run['scope']['project_ref'],
                   'identity': identity}
        now = iso(self.clock())
        return {
            'finding_id': digest(subject), 'rule': rule, 'severity': 'medium' if definite else 'investigation',
            'confidence': confidence, 'definite': definite, 'actionable': actionable, 'expected': expected,
            'observed': observed, 'versions': dict(run['versions']), 'evidence': evidence,
            'evidence_hash': digest(evidence)[:16], 'delivery_stage': delivery_stage,
            'task_accepted': 'unknown', 'task_completed': 'claimed_by_import' if delivery_stage == 'task_completed' else 'unknown',
            'analysis': analysis, 'live_observation': live, 'created_at': now, 'updated_at': now,
        }

    def _finding_by_id(self, run, finding_id):
        return next((item for item in run['findings'] if item['finding_id'] == finding_id), None)

    def _upsert(self, run, draft):
        existing = self._finding_by_id(run, draft['finding_id'])
        if existing:
            existing['observed'] = draft['observed']
            existing['updated_at'] = draft['updated_at']
            if existing['evidence_hash'] != draft['evidence_hash']:
                existing['evidence'] = draft['evidence']
                existing['evidence_hash'] = draft['evidence_hash']
                existing['delivery_stage'] = draft['delivery_stage']
            return existing
        if len(run['findings']) >= MAX_FINDINGS:
            run['findings_dropped'] += 1
            return None
        run['findings'].append(draft)
        return draft

    def _observation(self, run, key, kind, data):
        if any(item['key'] == key for item in run['observations']):
            return
        if len(run['observations']) >= 80:
            return
        run['observations'].append({'key': key, 'kind': kind, 'actionable': False, **data})

    def _usage_record(self, record):
        if not isinstance(record, dict) or forbid_keys(record):
            raise DebugError('debug_usage_rejected')
        source = safe(record.get('source'))
        model = safe(record.get('model'))
        start, end = parse_iso(record.get('window_start')), parse_iso(record.get('window_end'))
        category, quality = record.get('category'), record.get('quality')
        if (source is None or model is None or start is None or end is None or start >= end
                or category not in ('project', 'coordination', 'debugger', 'mixed')
                or quality not in ('actual', 'estimated')):
            raise DebugError('debug_usage_rejected')
        shared = record.get('shared_parent_sid', False)
        if type(shared) is not bool:
            raise DebugError('debug_usage_rejected', field='shared_parent_sid')
        counts = {}
        for field in TOKEN_FIELDS:
            value = record.get(field, 0)
            if type(value) is not int or not 0 <= value <= 10**12:
                raise DebugError('debug_usage_rejected', field=field)
            counts[field] = value
        semantics = record.get('cache_semantics', 'none')
        if counts['cache_read_tokens'] or counts['cache_write_tokens']:
            if semantics not in ('disjoint', 'cache_inclusive'):
                raise DebugError('debug_usage_rejected', field='cache_semantics')
        elif semantics not in ('none', 'disjoint', 'cache_inclusive'):
            raise DebugError('debug_usage_rejected', field='cache_semantics')
        if semantics == 'disjoint':
            tokens = counts['input_tokens'] + counts['output_tokens'] + counts['cache_read_tokens'] + counts['cache_write_tokens']
        else:
            # none and cache_inclusive: cache is not added on top of input.
            tokens = counts['input_tokens'] + counts['output_tokens']
        return {
            'source': source, 'model': model, 'window_start': iso(start), 'window_end': iso(end),
            'category': category, 'quality': quality, 'shared_parent_sid': shared,
            'cache_semantics': semantics, 'tokens': tokens, **counts,
        }

    def _measurement(self, record):
        return (
            record['source'], record['model'], record['window_start'], record['window_end'],
            record['category'], record['quality'], record['cache_semantics'],
            *(record[field] for field in TOKEN_FIELDS),
        )

    def _store_usage(self, run, record):
        records = run['usage'].setdefault('records', [])
        if any(self._measurement(item) == self._measurement(record) for item in records):
            return
        same_window = [
            item for item in records
            if item['model'] == record['model'] and item['window_start'] == record['window_start']
            and item['window_end'] == record['window_end']
        ]
        if record['shared_parent_sid'] and same_window:
            run['usage']['excluded'] += 1
            return
        if any(item['source'] == record['source'] for item in same_window):
            raise DebugError('debug_usage_rejected', reason='conflicting_window')
        records.append(record)
        window = [record['source'], record['model'], record['window_start'], record['window_end']]
        if window not in run['usage']['windows']:
            run['usage']['windows'].append(window)
        if record['source'] not in run['usage']['reported_sources']:
            run['usage']['reported_sources'].append(record['source'])
        totals = run['usage']['totals'].setdefault(record['category'], {})
        totals[record['quality']] = totals.get(record['quality'], 0) + record['tokens']

    def _publish_pending(self):
        with self._lock():
            run = self._load()
            if run is None:
                return 'ended'
            self._expire(run)
            if run['state'] != 'active':
                self._save(run)
                return 'ended'
            origin_id = run['run_id']
            repo = run['scope'].get('report_repository')
            if not repo:
                self._save(run)
                return 'not_authorized'
            self._save(run)
        try:
            probed = self.publisher.probe()
        except (OSError, subprocess.SubprocessError):
            probed = {'available': False, 'tool': 'gh', 'blocker': 'gh_auth_unavailable'}
        probe_result = self._record_probe(origin_id, repo, probed)
        if probe_result != 'continue':
            return probe_result
        seen = set()
        for _step in range(8 * (MAX_FINDINGS + 1)):
            with self._lock():
                run, place = self._origin(origin_id)
                if run is None or not self._origin_can_mutate(run, place, repo):
                    if run is not None:
                        self._save_origin(run, place)
                    return 'ended'
                step = self._next_step(run, seen)
                self._save_origin(run, place)
                if step is None:
                    return self._publication_result(run)
                step['run_id'] = origin_id
            outcome = self._call_external(step)
            with self._lock():
                run, place = self._origin(origin_id)
                if run is None or run['scope'].get('report_repository') != repo:
                    if run is not None:
                        self._save_origin(run, place)
                    return 'ended'
                if place == 'current':
                    self._touch_host(run)
                self._expire(run)
                self._apply_outcome(run, step, outcome)
                self._save_origin(run, place)
                if not self._origin_can_mutate(run, place, repo):
                    return 'ended'
        return 'uncertain'

    def _record_probe(self, origin_id, repo, probed):
        with self._lock():
            run, place = self._origin(origin_id)
            if run is None or run['scope'].get('report_repository') != repo:
                if run is not None:
                    self._save_origin(run, place)
                return 'ended'
            if place == 'current':
                self._touch_host(run)
            self._expire(run)
            run['github'] = probed
            if not self._origin_can_mutate(run, place, repo):
                self._save_origin(run, place)
                return 'ended'
            if not probed.get('available'):
                for item in self._actionable(run):
                    self._pending(run, item['finding_id'])
                self._save_origin(run, place)
                return 'delayed'
            self._save_origin(run, place)
            return 'continue'

    def _origin_can_mutate(self, run, place, repo):
        return place == 'current' and run.get('state') == 'active' and run['scope'].get('report_repository') == repo

    def _origin(self, run_id):
        current = self._load()
        if current and current.get('run_id') == run_id:
            return current, 'current'
        archived = self._read_json(self.root / 'debug' / 'runs' / f'{run_id}.json')
        if archived and archived.get('run_id') == run_id:
            return archived, 'archive'
        return None, None

    def _save_origin(self, run, place):
        if place == 'archive':
            self._write_json(self.root / 'debug' / 'runs' / f"{run['run_id']}.json", run)
        else:
            self._save(run)

    def _actionable(self, run):
        return [item for item in run['findings'] if item.get('actionable')]

    def _publication_result(self, run):
        if any(item.get('status') in ('uncertain', 'intent') for item in run['publication']):
            return 'uncertain'
        if run['pending']:
            return 'delayed'
        return 'published'

    def _entry_for(self, run, finding, repo):
        entry = next((item for item in run['publication'] if item['finding_id'] == finding['finding_id']), None)
        if entry is None:
            entry = {
                'finding_id': finding['finding_id'], 'repository': repo, 'status': 'intent',
                'operation': None, 'submitted': False, 'issue_number': None, 'evidence_hash': None,
                'failure': None, 'retries': 0, 'reconciled': False, 'create_retry': False, 'comment_retry': False,
            }
            run['publication'].append(entry)
        return entry

    def _next_step(self, run, seen):
        repo = run['scope']['report_repository']
        for finding in self._actionable(run):
            step = self._step_for(run, finding, repo, seen)
            if step is not None:
                seen.add((step['kind'], finding['finding_id']))
                return step
        return None

    def _step_for(self, run, finding, repo, seen):
        entry = next((item for item in run['publication'] if item['finding_id'] == finding['finding_id']), None)
        if entry and entry.get('repository') not in (None, repo):
            self._pending(run, finding['finding_id'])
            return None
        if entry and entry.get('status') == 'published' and entry.get('issue_number') and entry.get('evidence_hash') == finding['evidence_hash']:
            self._clear_pending(run, finding['finding_id'])
            return None
        body = self._body(run, finding)
        title = f"Quorum debug: {finding['rule']} ({run['scope']['quorum']})"[:120]
        if entry and entry.get('create_retry'):
            if ('create', finding['finding_id']) in seen:
                return None
            entry.update(create_retry=False, status='intent', operation='create', submitted=True,
                         evidence_hash=finding['evidence_hash'], failure=None)
            if entry.pop('oserror_retry', False):
                entry['retries'] = entry.get('retries', 0) + 1
            self._pending(run, finding['finding_id'])
            return {'kind': 'create', 'repo': repo, 'title': title, 'body': body,
                    'finding_id': finding['finding_id'], 'evidence_hash': finding['evidence_hash']}
        if entry and entry.get('comment_retry'):
            if ('comment', finding['finding_id']) in seen:
                return None
            entry.update(comment_retry=False, status='intent', operation='comment', submitted=True,
                         evidence_hash=finding['evidence_hash'], failure=None)
            if entry.pop('oserror_retry', False):
                entry['retries'] = entry.get('retries', 0) + 1
            self._pending(run, finding['finding_id'])
            return {'kind': 'comment', 'repo': repo, 'number': entry['issue_number'], 'body': body,
                    'finding_id': finding['finding_id'], 'evidence_hash': finding['evidence_hash']}
        if entry and entry.get('issue_number') and (
                entry.get('evidence_hash') != finding['evidence_hash'] or entry.get('status') != 'published'):
            if ('view', finding['finding_id']) in seen:
                return None
            self._pending(run, finding['finding_id'])
            return {'kind': 'view', 'repo': repo, 'number': entry['issue_number'],
                    'finding_id': finding['finding_id'], 'evidence_hash': finding['evidence_hash']}
        if ('search', finding['finding_id']) in seen:
            return None
        self._pending(run, finding['finding_id'])
        return {'kind': 'search', 'repo': repo, 'finding_id': finding['finding_id'],
                'evidence_hash': finding['evidence_hash']}

    def _call_external(self, step):
        try:
            if step['kind'] == 'search':
                result = self.publisher.search(step['repo'], step['finding_id'])
            elif step['kind'] == 'view':
                result = self.publisher.read_issue(step['repo'], step['number'])
            elif step['kind'] == 'create':
                result = self.publisher.create_issue(step['repo'], step['title'], step['body'])
            elif step['kind'] == 'comment':
                result = self.publisher.comment(step['repo'], step['number'], step['body'])
            else:
                result = {'status': 'uncertain'}
        except subprocess.TimeoutExpired:
            return {'failure': 'timeout', 'result': {'status': 'uncertain'}}
        except OSError:
            return {'failure': 'oserror', 'result': {'status': 'uncertain'}}
        return {'failure': None, 'result': result if isinstance(result, dict) else {'status': 'uncertain'}}

    def _apply_outcome(self, run, step, outcome):
        finding = self._finding_by_id(run, step['finding_id'])
        if finding is None:
            return
        entry = self._entry_for(run, finding, step['repo'])
        failure = outcome.get('failure')
        result = outcome.get('result') or {}
        if step['kind'] == 'search':
            self._apply_search(run, finding, entry, result, failure)
        elif step['kind'] == 'view':
            self._apply_view(run, finding, entry, result, failure)
        elif step['kind'] == 'create':
            self._apply_create(run, finding, entry, step, result, failure)
        elif step['kind'] == 'comment':
            self._apply_comment(run, finding, entry, step, result, failure)

    def _classified_issues(self, result, finding_id):
        if result.get('status') != 'ok':
            return None, True
        matched, rejected = [], False
        issues = result.get('issues')
        if not isinstance(issues, list):
            return None, True
        for item in issues:
            number = item.get('number') if isinstance(item, dict) else None
            body = item.get('body') if isinstance(item, dict) else None
            if type(number) is int and marker_in(body, finding_id):
                matched.append({'number': number, 'body': body})
            elif item:
                rejected = True
        return matched, rejected

    def _apply_search(self, run, finding, entry, result, failure):
        if failure or result.get('status') != 'ok':
            entry.update(status='uncertain', failure=failure or 'rejected', operation=entry.get('operation') or 'create')
            self._pending(run, finding['finding_id'])
            return
        matched, rejected = self._classified_issues(result, finding['finding_id'])
        if matched is None:
            entry.update(status='uncertain', failure=failure or 'rejected')
            self._pending(run, finding['finding_id'])
            return
        if matched:
            chosen = min(matched, key=lambda item: item['number'])
            remote = line_value(chosen['body'], 'Evidence-Hash', re.compile(r'[0-9a-f]{16}'))
            entry.update(issue_number=chosen['number'], reconciled=True, failure=None, submitted=True, operation='create')
            if remote == finding['evidence_hash']:
                entry.update(status='published', evidence_hash=remote)
                self._clear_pending(run, finding['finding_id'])
            else:
                entry.update(status='intent', evidence_hash=remote)
                self._pending(run, finding['finding_id'])
            return
        if rejected or result.get('issues'):
            # A search hit without the exact marker is not proof the issue is absent.
            if result.get('issues') and not matched:
                entry.update(status='uncertain', failure='rejected', operation=entry.get('operation') or 'create')
                self._pending(run, finding['finding_id'])
                return
        if entry.get('submitted') and entry.get('failure') == 'oserror' and entry.get('retries', 0) < 1:
            entry.update(create_retry=True, oserror_retry=True, failure=None, status='intent')
            return
        if entry.get('submitted'):
            entry.update(status='uncertain', operation='create')
            self._pending(run, finding['finding_id'])
            return
        entry.update(create_retry=True, status='intent', operation='create', submitted=False, failure=None)
        self._pending(run, finding['finding_id'])

    def _apply_view(self, run, finding, entry, result, failure):
        if failure or result.get('status') != 'ok':
            entry.update(status='uncertain', failure=failure or 'rejected')
            self._pending(run, finding['finding_id'])
            return
        issue_body = result.get('body') or ''
        if not marker_in(issue_body, finding['finding_id']):
            entry.update(status='uncertain', failure='rejected')
            self._pending(run, finding['finding_id'])
            return
        bodies = [issue_body]
        for comment in result.get('comments') or []:
            if isinstance(comment, dict):
                bodies.append(comment.get('body') or '')
        current_hash = finding['evidence_hash']
        if any(marker_in(body, finding['finding_id']) and evidence_in(body, current_hash) for body in bodies):
            entry.update(status='published', evidence_hash=current_hash, failure=None, reconciled=True, submitted=False)
            self._clear_pending(run, finding['finding_id'])
            return
        submitted_hash = entry.get('evidence_hash')
        unresolved = bool(
            entry.get('operation') == 'comment' and entry.get('submitted') and entry.get('status') != 'published'
            and submitted_hash)
        if unresolved and submitted_hash != current_hash:
            if any(marker_in(body, finding['finding_id']) and evidence_in(body, submitted_hash) for body in bodies):
                entry.update(status='published', evidence_hash=submitted_hash, submitted=False, failure=None,
                             reconciled=True, comment_retry=True, operation='comment')
                self._pending(run, finding['finding_id'])
                return
            entry.update(status='uncertain')
            self._pending(run, finding['finding_id'])
            return
        if unresolved and entry.get('failure') == 'oserror' and entry.get('retries', 0) < 1:
            entry.update(comment_retry=True, oserror_retry=True, failure=None, submitted=False, status='intent')
            return
        if unresolved:
            entry.update(status='uncertain')
            self._pending(run, finding['finding_id'])
            return
        entry.update(comment_retry=True, status='intent', operation='comment', failure=None)

    def _outcome_owns_entry(self, entry, step):
        recorded = entry.get('evidence_hash')
        dispatched = step.get('evidence_hash')
        return not dispatched or recorded in (None, dispatched)

    def _settle_submitted(self, run, finding, submitted_hash):
        if finding['evidence_hash'] == submitted_hash:
            self._clear_pending(run, finding['finding_id'])
        else:
            self._pending(run, finding['finding_id'])

    def _apply_create(self, run, finding, entry, step, result, failure):
        if not self._outcome_owns_entry(entry, step):
            self._pending(run, finding['finding_id'])
            return
        submitted_hash = step.get('evidence_hash')
        if failure or result.get('status') != 'created' or type(result.get('number')) is not int:
            entry.update(status='uncertain', failure=failure or 'rejected', operation='create', submitted=True,
                         evidence_hash=submitted_hash)
            self._pending(run, finding['finding_id'])
            return
        entry.update(status='published', operation='create', submitted=True, issue_number=result['number'],
                     evidence_hash=submitted_hash, failure=None, reconciled=False)
        self._settle_submitted(run, finding, submitted_hash)

    def _apply_comment(self, run, finding, entry, step, result, failure):
        if not self._outcome_owns_entry(entry, step):
            self._pending(run, finding['finding_id'])
            return
        submitted_hash = step.get('evidence_hash')
        if failure or result.get('status') != 'commented':
            entry.update(status='uncertain', failure=failure or 'rejected', operation='comment', submitted=True,
                         evidence_hash=submitted_hash)
            self._pending(run, finding['finding_id'])
            return
        entry.update(status='published', operation='comment', submitted=False, evidence_hash=submitted_hash,
                     failure=None)
        self._settle_submitted(run, finding, submitted_hash)

    def _body(self, run, finding):
        lines = ['## Expected', finding['expected'], '', '## Observed', finding['observed'], '', '## Versions',
                 f"protocol {run['versions']['protocol_version']} skill {run['versions']['skill_version']}",
                 '', '## Evidence']
        for item in finding['evidence'][:8]:
            lines.append('- ' + ' '.join(f'{key}={item[key]}' for key in sorted(item)))
        lines += ['', '## Confidence',
                  f"confidence={finding['confidence']} definite={str(finding['definite']).lower()} live_observation={str(finding['live_observation']).lower()}",
                  '', '## Delivery and task outcome',
                  f"delivery_stage={finding['delivery_stage']}",
                  f"task_accepted={finding['task_accepted']}",
                  f"task_completed={finding['task_completed']}",
                  '', f"Evidence-Hash: {finding['evidence_hash']}",
                  f"Debug-Finding: {finding['finding_id']}"]
        body = '\n'.join(lines)
        marker = f"Evidence-Hash: {finding['evidence_hash']}\nDebug-Finding: {finding['finding_id']}"
        if len(body) > 6000:
            body = body[:5600].rstrip() + '\n\n' + marker
        return body

    def _pending(self, run, finding_id):
        if finding_id not in run['pending'] and len(run['pending']) < 40:
            run['pending'].append(finding_id)

    def _clear_pending(self, run, finding_id):
        run['pending'] = [item for item in run['pending'] if item != finding_id]

    def _note_gap(self, run):
        previous = parse_iso(run['collection'].get('last_collected_at') or '')
        if previous is None:
            return
        if (self.clock() - previous).total_seconds() <= GAP_SECONDS:
            return
        if len(run['gaps']) < 40:
            run['gaps'].append({'from': iso(previous), 'to': iso(self.clock()), 'reason': 'collector_not_running'})

    def _gap_open(self, run):
        if run.get('state') != 'active':
            return True
        if 'local-ledger' in run['scope']['sources'] and not self._host_alive(run):
            return True
        previous = parse_iso(run['collection'].get('last_collected_at') or '')
        if previous is None:
            return True
        return (self.clock() - previous).total_seconds() > GAP_SECONDS

    def _pid_alive(self, pid):
        if type(pid) is not int or pid <= 0:
            return False
        if self.alive_check is not None:
            return bool(self.alive_check(pid))
        if os.name == 'nt':
            return _windows_pid_alive(pid)
        try:
            os.kill(pid, 0)
        except OSError:
            return False
        return True

    def _host_alive(self, run):
        if not run:
            return False
        host = run.get('host') or {}
        if host.get('state') != 'supervising':
            return False
        if not self._pid_alive(host.get('pid')):
            return False
        beat = parse_iso(host.get('heartbeat_at') or '')
        if beat is None:
            return False
        interval = host.get('interval_seconds') if type(host.get('interval_seconds')) is int else 30
        stale = interval * 2 + HOST_STALE_PADDING
        return (self.clock() - beat).total_seconds() <= stale

    def _note_host_failure(self, run):
        host = run.get('host') or {}
        if host.get('state') != 'supervising' or self._host_alive(run):
            return False
        signature = host.get('heartbeat_at')
        host['state'] = 'dead'
        if not any(item.get('reason') == 'collector_host_dead' and item.get('from') == signature for item in run['gaps']):
            if len(run['gaps']) < 40:
                run['gaps'].append({'from': signature, 'to': iso(self.clock()), 'reason': 'collector_host_dead'})
        return True

    def _continuous(self, run):
        return bool(run) and run.get('state') == 'active' and self._host_alive(run)

    def _supervision_view(self, run):
        host = (run or {}).get('host') or {}
        alive = self._host_alive(run) if run else False
        return {
            'mode': 'attached' if alive else 'handoff',
            'host_alive': alive,
            'host_state': host.get('state') or 'handoff',
            'pid': host.get('pid') if type(host.get('pid')) is int else None,
            'interval_seconds': host.get('interval_seconds'),
            'command': 'debug watch',
            'automatic_runtime_restoration': False,
        }

    def _attach_host(self, interval):
        with self._lock():
            run = self._require()
            self._expire(run)
            if run['state'] != 'active':
                self._save(run)
                raise DebugError('debug_grant_ended')
            if self._host_alive(run):
                self._save(run)
                raise DebugError('debug_busy')
            token = uuid.uuid4().hex
            self._host_token = token
            run['host'] = {
                'pid': self.pid(), 'token': token, 'heartbeat_at': iso(self.clock()),
                'interval_seconds': interval, 'state': 'supervising',
            }
            run['collection']['collector'] = 'bounded_local_watch'
            self._save(run)

    def _heartbeat(self):
        with self._lock():
            run = self._load()
            if run is None:
                return False
            self._expire(run)
            host = run.get('host') or {}
            if host.get('token') != self._host_token or run.get('state') != 'active':
                self._save(run)
                return False
            host['heartbeat_at'] = iso(self.clock())
            host['pid'] = self.pid()
            host['state'] = 'supervising'
            self._save(run)
            return True

    def _grant_running(self):
        with self._lock():
            run = self._load()
            if run is None:
                return False
            self._expire(run)
            host = run.get('host') or {}
            running = run.get('state') == 'active' and host.get('token') == self._host_token
            self._save(run)
            return running

    def _sleep_until(self, interval, sleeper):
        remaining = interval
        while remaining > 0:
            if not self._grant_running():
                return False
            step = 1 if remaining >= 1 else remaining
            sleeper(step)
            remaining -= step
        return self._grant_running()

    def _detach_host(self, reason):
        with self._lock():
            run = self._load()
            if not run:
                return
            host = run.setdefault('host', {})
            if host.get('token') != self._host_token:
                return
            if run.get('state') == 'expired':
                reason = 'expired'
            elif run.get('state') == 'stopped':
                reason = 'stopped'
            host.update(state='stopped', pid=None, token=None, stopped_reason=reason,
                        heartbeat_at=iso(self.clock()))
            self._save(run)

    def _touch_host(self, run):
        host = run.get('host') or {}
        if host.get('token') and host.get('token') == self._host_token and run.get('state') == 'active':
            host['heartbeat_at'] = iso(self.clock())
            host['pid'] = self.pid()
            host['state'] = 'supervising'

    def _expired(self, run):
        expires = parse_iso(run.get('expires_at') or '')
        return bool(expires and self.clock() >= expires)

    def _expire(self, run):
        if run.get('state') == 'active' and self._expired(run):
            run['state'] = 'expired'
            run['expired_at'] = iso(self.clock())
            return True
        return False

    def _count(self, run):
        run['usage']['debugger_invocations'] += 1

    def _github_view(self, run):
        repo = run['scope'].get('report_repository')
        if not repo:
            return {'tool': 'gh', 'available': False, 'authorized': False, 'blocker': 'not_authorized',
                    'writes': 'none'}
        probed = run.get('github') or self.publisher.probe()
        run['github'] = probed
        return {'tool': 'gh', 'available': bool(probed.get('available')), 'authorized': True,
                'blocker': probed.get('blocker'), 'repository': repo,
                'writes': 'issue_create_and_material_update' if probed.get('available') else 'pending'}

    def _blockers(self, run):
        blockers = []
        state = run['collection']['state']
        if 'local-ledger' not in run['scope']['sources']:
            blockers.append('no_local_collector')
        elif state == 'no_source':
            blockers.append('no_readable_source')
        elif state == 'unreadable':
            blockers.append('unreadable_source')
        elif state != 'attached':
            blockers.append('local_ledger_unreadable')
        if ('local-ledger' in run['scope']['sources'] and state in ('attached', 'no_source', 'unreadable')
                and not self._host_alive(run)):
            blockers.append('collector_not_supervising')
        if run['coverage']['unavailable_bindings'] or run['coverage']['unreadable']:
            blockers.append('bindings_not_observed')
        github = self._github_view(run)
        if github['authorized'] and not github['available']:
            blockers.append('github_unavailable')
        return blockers, github

    def _activation_view(self, run, *, idempotent):
        blockers, github = self._blockers(run)
        return {
            'run_id': run['run_id'], 'state': run['state'], 'idempotent': idempotent,
            'activation': 'ready' if not blockers else 'partial', 'complete': not blockers, 'blockers': blockers,
            'started_at': run['started_at'], 'expires_at': run['expires_at'], 'lifetime': run['scope']['lifetime'],
            'lifetime_unchanged': idempotent, 'automatic_runtime_restoration': False,
            'continuous_observation': self._continuous(run), 'collector': 'bounded_local_watch',
            'host_alive': self._host_alive(run), 'supervision': self._supervision_view(run),
            'versions': run['versions'],
            'scope': run['scope'], 'collection': {
                'state': run['collection']['state'], 'live_observation': run['collection']['live_observation'],
                'host_alive': self._host_alive(run), 'last_collected_at': run['collection']['last_collected_at'],
                'observation_gap': self._gap_open(run),
            },
            'coverage': self._coverage_view(run), 'github': github, 'limitations': list(LIMITATIONS),
        }

    def _status_view(self, run):
        blockers, github = self._blockers(run)
        return {
            'run_id': run['run_id'], 'state': run['state'], 'activation': 'ready' if run['state'] == 'active' and not blockers else 'partial',
            'complete': run['state'] == 'active' and not blockers, 'blockers': blockers, 'expires_at': run['expires_at'],
            'lifetime': run['scope']['lifetime'], 'stopped_at': run.get('stopped_at'), 'expired_at': run.get('expired_at'),
            'continuous_observation': self._continuous(run), 'automatic_runtime_restoration': False, 'durable': True,
            'collector': 'bounded_local_watch', 'host_alive': self._host_alive(run),
            'supervision': self._supervision_view(run), 'observation_gap': self._gap_open(run),
            'collection': run['collection'], 'coverage': self._coverage_view(run), 'github': github,
            'findings': {'actionable': sum(item['actionable'] for item in run['findings']),
                         'analysis_pending': sum(item['analysis'] == 'pending' for item in run['findings'])},
            'pending_reports': list(run['pending']), 'gaps': list(run['gaps']),
            'tokens': self._token_view(run),
        }

    def _coverage_view(self, run):
        coverage = run['coverage']
        return {
            'metadata_only': True, 'participants': coverage['participants'],
            'unavailable_bindings': coverage['unavailable_bindings'], 'unreadable_bindings': coverage['unreadable'],
            'other_owners': 'unavailable', 'task_acceptance': coverage['task_acceptance'],
            'task_completion': coverage['task_completion'],
            'definite_non_delivery': coverage['definite_non_delivery'],
            'token_usage': self._token_view(run)['coverage'],
        }

    def _token_view(self, run):
        totals = run['usage']['totals']

        def quality(category):
            bucket = totals.get(category, {})
            return {'actual': bucket.get('actual', 'unavailable'), 'estimated': bucket.get('estimated', 'unavailable')}

        reported = sorted(run['usage']['reported_sources'])
        unavailable = sorted(engine for engine in run['coverage']['engines'] if engine not in reported and engine != 'unknown')
        project, coordination = totals.get('project', {}), totals.get('coordination', {})
        ratio = None
        if (type(project.get('actual')) is int and type(coordination.get('actual')) is int and project['actual'] > 0
                and 'estimated' not in project and 'estimated' not in coordination):
            ratio = round(coordination['actual'] / project['actual'], 4)
        debugger = totals.get('debugger', {})
        return {
            'method': 'client_reported', 'coverage': {
                'reported_sources': reported, 'unavailable_clients': unavailable, 'unseen_clients': 'unknown',
                'metadata_only': True,
            },
            'project': quality('project'), 'coordination': quality('coordination'),
            'mixed_unallocated': quality('mixed'), 'debugger': {
                'invocations': run['usage']['debugger_invocations'],
                'actual': debugger.get('actual', 'unavailable'), 'estimated': debugger.get('estimated', 'unavailable'),
            },
            'overhead_ratio': ratio,
            'overhead_note': 'Attributed coordination usage is not a counterfactual cost saving.',
            'excluded_double_count': run['usage']['excluded'],
            'accounting': {
                'repeat': 'idempotent',
                'same_source_window_conflict': 'rejected',
                'parent_child': 'shared_parent_sid_excludes_later_window',
                'cache': 'disjoint_added_or_cache_inclusive_not_added',
            },
            'records': [
                {key: item[key] for key in (
                    'source', 'model', 'window_start', 'window_end', 'category', 'quality', 'cache_semantics', 'tokens',
                )}
                for item in run['usage'].get('records', [])
            ],
        }

    def _report_view(self, run):
        status = self._status_view(run)
        status.update(timeline=run['events'][-100:], events_dropped=run['events_dropped'], findings=run['findings'],
                      observations=run['observations'], publication={'ledger': run['publication'], 'pending': run['pending']},
                      limitations=list(LIMITATIONS), versions=run['versions'], scope=run['scope'])
        return status

    def _require(self):
        run = self._load()
        if run is None:
            raise DebugError('debug_not_active')
        return run

    def _load_id(self, run_id):
        if not isinstance(run_id, str) or not RUN_RE.fullmatch(run_id):
            raise DebugError('debug_invalid_scope', field='run_id')
        current = self._load()
        if current and current.get('run_id') == run_id:
            return current
        return self._read_json(self.root / 'debug' / 'runs' / f'{run_id}.json')

    def _archive(self, run):
        if run.get('run_id'):
            self._write_json(self.root / 'debug' / 'runs' / f"{run['run_id']}.json", run)

    def _load(self):
        return self._read_json(self.root / 'debug' / 'current.json')

    def _save(self, run):
        self._write_json(self.root / 'debug' / 'current.json', run)

    def _read_json(self, path):
        if not path.exists():
            return None
        reject_link(path)
        try:
            data = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            raise DebugError('debug_state_unavailable') from error
        if not isinstance(data, dict):
            raise DebugError('debug_state_unavailable')
        return data

    def _write_json(self, path, data):
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        protect(path.parent, directory=True)
        temporary = path.with_name(path.name + '.tmp')
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, 'w', encoding='utf-8') as handle:
            json.dump(data, handle, sort_keys=True, separators=(',', ':'))
            handle.write('\n')
        protect(temporary)
        os.replace(temporary, path)
        protect(path)

    @contextmanager
    def _lock(self):
        directory = self.root / 'debug'
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        protect(directory, directory=True)
        lock = self._acquire_run_lock(directory / 'run.lock')
        try:
            yield
        finally:
            lock.__exit__(None, None, None)

    def _acquire_run_lock(self, path):
        """Wait only for a busy FileLock enter. The body runs once, outside this loop."""
        deadline = time.monotonic() + self._LOCK_ACQUIRE_SECONDS
        while True:
            candidate = FileLock(path)
            try:
                candidate.__enter__()
            except ValueError as error:
                if str(error) != 'worker_already_running':
                    raise
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise DebugError('debug_busy') from None
                time.sleep(min(0.025, remaining))
                continue
            return candidate


def add_commands(commands):
    debug = commands.add_parser('debug', help='Optional local diagnostic run and repository-scoped findings')
    actions = debug.add_subparsers(dest='action', required=True)
    activate = actions.add_parser('activate')
    activate.add_argument('--quorum', required=True)
    activate.add_argument('--project-ref', required=True)
    activate.add_argument('--report-repo')
    activate.add_argument('--until', default='stop')
    activate.add_argument('--binding', action='append', default=[])
    activate.add_argument('--source', action='append', choices=('local-ledger', 'imported-artifact'), default=[])
    activate.add_argument('--watch', action='store_true')
    activate.add_argument('--interval', type=int, default=30)
    activate.add_argument('--cycles', type=int)
    watch = actions.add_parser('watch')
    watch.add_argument('--interval', type=int, default=30)
    watch.add_argument('--cycles', type=int)
    actions.add_parser('status')
    actions.add_parser('stop')
    report = actions.add_parser('report')
    report.add_argument('--run-id')
    collect = actions.add_parser('collect')
    collect.add_argument('--no-publish', action='store_true')
    imported = actions.add_parser('import')
    imported.add_argument('--file', type=Path, required=True)
    actions.add_parser('usage')
    analyze = actions.add_parser('analyze')
    analyze.add_argument('--finding-id', required=True)
    analyze.add_argument('--conclusion', required=True)
    analyze.add_argument('--confidence', choices=('low', 'medium', 'high'), required=True)
    actions.add_parser('publish')


def dispatch(args, ledger):
    import sys
    service = DebugService(ledger.root)
    action = args.action
    if action == 'activate':
        sources = args.source or ['local-ledger']
        view = service.activate(args.quorum, args.project_ref, args.report_repo, args.until, args.binding, sources)
        if args.watch:
            watched = service.watch(interval=args.interval, cycles=args.cycles)
            watched['activation'] = view.get('activation')
            return watched
        return view
    if action == 'watch':
        return service.watch(interval=args.interval, cycles=args.cycles)
    if action == 'status':
        return service.status()
    if action == 'stop':
        return service.stop()
    if action == 'report':
        return service.report(args.run_id)
    if action == 'collect':
        return service.collect(publish=not args.no_publish)
    if action == 'import':
        return service.import_artifact(args.file)
    if action == 'usage':
        raw = sys.stdin.buffer.read(16385)
        if len(raw) > 16384:
            raise DebugError('debug_usage_rejected')
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as error:
            raise DebugError('debug_usage_rejected') from error
        return service.add_usage(data)
    if action == 'analyze':
        return service.analyze(args.finding_id, args.conclusion, args.confidence)
    if action == 'publish':
        return service.publish()
    raise DebugError('debug_invalid_scope', field='action')
