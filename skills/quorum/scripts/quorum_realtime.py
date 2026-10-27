"""Receive-only Supabase wake hints; durable inbox RPCs remain authoritative."""
import json
import logging
import time
from urllib.parse import urlencode
import uuid
from quorum_api import QuorumError

# Frames inspected for an overdue heartbeat reply; exceeds the socket's max_queue.
DRAIN_LIMIT = 32


def connect_socket(uri, **options):
    from websockets.sync.client import reconnect
    class NoRedirect(reconnect):
        def process_redirect(self, error): return error
    # A successful redirect could otherwise receive the JWT in our later join
    # frame, even if the dependency stripped credentials from HTTP headers.
    # Enter the documented connection context; RealtimeWait owns close() across
    # multiple listener iterations rather than one lexical `with` block.
    return NoRedirect(uri, **options).connect().__enter__()


class RealtimeWait:
    """One native agent's private channel, with bounded polling recovery.

    Supabase protocol 1.0: https://supabase.com/docs/guides/realtime/protocol
    No message body or delivery acknowledgement is carried by this transport.
    """
    def __init__(self, api, record, ledger, *, connect=None, clock=time.monotonic,
                 sleep=time.sleep, keepalive=None, stop_aware=False, authorize=None):
        self.api, self.ledger, self.clock, self.sleep = api, ledger, clock, sleep
        self.authorize = authorize or (lambda:None)
        self.keepalive = keepalive
        # Opt-in: pass `stopping` into keepalive calls (the legacy listener keeps calling them bare).
        self.stop_aware = stop_aware
        # Time spent in nested keepalive, token and reconnect work, so callers can budget idle waits only.
        self.busy_seconds, self._busy_depth = 0.0, 0
        self.topic = 'realtime:quorum:agent:' + str(uuid.UUID(record['agent_id']))
        self.connect = connect
        self.socket, self.jwt, self.ref = None, None, 0
        self.retry_at, self.backoff = 0, 10
        self.heartbeat_at, self.pending_heartbeat, self.drain_left = 0, None, 0
        self.diagnostics = {'mode': 'polling_fallback', 'poll_seconds': 10, 'signals': 0}

    def _busy(self, operation, *args):
        """Run a nested service operation, counting its time once even when nested."""
        self._busy_depth += 1
        began = self.clock()
        try: return operation(*args)
        finally:
            self._busy_depth -= 1
            if not self._busy_depth: self.busy_seconds += max(0, self.clock() - began)

    def _keep(self, name, stopping=None):
        if not self.keepalive: return
        method = getattr(self.keepalive, name)
        self._busy(lambda: method(stopping) if self.stop_aware else method())

    def _status(self, **data):
        self.diagnostics.update(data)
        self.ledger.setting('listener-wait', self.diagnostics)

    def _send(self, event, payload, *, topic=None):
        self.ref += 1
        ref = str(self.ref)
        frame = json.dumps({'topic': topic or self.topic, 'event': event,
            'payload': payload, 'ref': ref, 'join_ref': None if topic == 'phoenix' else self.join_ref})
        self.authorize()  # Connection/token work may have outlived a recovery grant.
        self.socket.send(frame)
        return ref

    def _receive(self, timeout):
        raw = self.socket.recv(timeout=timeout)
        if not isinstance(raw, str) or len(raw.encode()) > 65536:
            raise ValueError('invalid_notification')
        frame = json.loads(raw)
        if not isinstance(frame, dict) or not isinstance(frame.get('payload'), dict):
            raise ValueError('invalid_notification')
        return frame

    def _close_socket(self):
        socket, self.socket = self.socket, None
        if socket:
            try: socket.close()
            except Exception: pass
        self.pending_heartbeat = None

    def _fallback(self):
        self._close_socket()
        self.retry_at = self.clock() + self.backoff
        self.backoff = min(self.backoff * 2, 60)
        self._status(mode='polling_fallback', poll_seconds=10,
                     code='realtime_unavailable', last_fallback_at=time.time())

    def _join(self, stopping):
        # Suppress dependency debug frames/handshake logging, which contain JWTs
        # and the public API key. Only our stable diagnostics leave this helper.
        logger = logging.getLogger('quorum.realtime.transport')
        logger.disabled = True
        self.jwt = self.api.token()
        url = (self.api.config.origin.rstrip('/').replace('https://', 'wss://', 1)
               + '/realtime/v1/websocket?' + urlencode({
                   'apikey': self.api.config.publishable_key, 'vsn': '1.0.0'}))
        self.socket = (self.connect or connect_socket)(url, open_timeout=5, close_timeout=1,
            ping_interval=None, compression=None, max_size=65536, max_queue=16,
            logger=logger)
        self.join_ref = str(self.ref + 1)
        joined = self._send('phx_join', {'access_token': self.jwt, 'config': {
            'private': True, 'broadcast': {'ack': False, 'self': False},
            'presence': {'enabled': False}, 'postgres_changes': []}})
        deadline = self.clock() + 5
        while not stopping() and self.clock() < deadline:
            try: frame = self._receive(min(1, deadline - self.clock()))
            except TimeoutError: continue
            if frame.get('topic') == self.topic and frame.get('ref') == joined:
                if frame.get('event') != 'phx_reply' or frame['payload'].get('status') != 'ok':
                    raise ValueError('realtime_unavailable')
                self.heartbeat_at = self.clock() + 20
                self.backoff = 10
                self._status(mode='realtime', poll_seconds=60, code=None,
                             last_connected_at=time.time(), last_wake='subscribed')
                self._keep('connected', stopping)
                return
        raise ValueError('realtime_unavailable')

    def prepare(self, stopping):
        """Subscribe before the listener's initial authoritative inbox drain."""
        if stopping(): return
        try: self._join(stopping)
        except QuorumError as error:
            if str(error) in ('auth_required', 'coordination_unavailable', 'skill_update_required', 'release_manifest_invalid'):
                self._close_socket()
                raise
            self._fallback()
        except Exception:
            self._fallback()

    def pulse(self, stopping=None):
        """Keep health fresh during an active inbox drain as well as idle waits."""
        self._keep('tick', stopping)

    def _poll(self, stopping):
        for _ in range(10):
            if stopping(): return
            self._keep('tick', stopping)
            if stopping(): return
            self.sleep(1)
        self._status(last_wake='fallback_poll')

    def wait(self, stopping):
        """Wake on a scoped signal, reconnect, safety scan, or requested stop."""
        if not self.socket:
            if self.clock() >= self.retry_at:
                self._busy(self.prepare, stopping)
                if self.socket: return  # Catch up even if no new signal arrives.
            self._poll(stopping)
            return
        deadline = self.clock() + 60
        try:
            while not stopping() and self.clock() < deadline:
                now = self.clock()
                self._keep('tick', stopping)
                if stopping(): break
                if now >= self.heartbeat_at and not self.pending_heartbeat:
                    token = self._busy(self.api.token)  # Existing private store refreshes an expiring JWT.
                    if token != self.jwt:
                        self._send('access_token', {'access_token': token})
                        self.jwt = token
                    ref = self._send('heartbeat', {}, topic='phoenix')
                    self.pending_heartbeat = (ref, now, now + 10)
                    self.drain_left = DRAIN_LIMIT
                    self.heartbeat_at = now + 20
                pending = self.pending_heartbeat
                if pending and not pending[1] <= now < pending[2]:
                    # Overdue (or the clock is inconsistent): only a reply already
                    # buffered, e.g. while a delivery ran outside wait(), may still
                    # count, and only within a fixed frame budget so a busy channel
                    # cannot defer the absolute deadline.
                    if self.drain_left <= 0: raise ValueError('realtime_unavailable')
                    self.drain_left -= 1
                    try: frame = self._receive(0)
                    except TimeoutError: raise ValueError('realtime_unavailable') from None
                else:
                    until = min(deadline, pending[2]) if pending else deadline
                    try: frame = self._receive(max(0, min(1, until - self.clock())))
                    except TimeoutError: continue
                topic, event = frame.get('topic'), frame.get('event')
                if (self.pending_heartbeat and topic == 'phoenix' and event == 'phx_reply'
                        and frame.get('ref') == self.pending_heartbeat[0]
                        and frame['payload'].get('status') == 'ok'):
                    self.pending_heartbeat = None
                if topic != self.topic: continue
                if event in ('phx_error', 'phx_close') or (event == 'system'
                        and frame['payload'].get('status') == 'error'):
                    raise ValueError('realtime_unavailable')
                if event == 'broadcast' and frame['payload'].get('event') == 'inbox_changed':
                    self._status(signals=self.diagnostics['signals'] + 1,
                                 last_signal_at=time.time(), last_wake='signal')
                    return
            if not stopping(): self._status(last_wake='safety_poll')
        except QuorumError as error:
            if str(error) in ('auth_required', 'coordination_unavailable', 'skill_update_required', 'release_manifest_invalid'):
                self._close_socket()
                raise
            self._fallback()
            self._poll(stopping)
        except Exception:
            self._fallback()
            self._poll(stopping)

    def close(self):
        self._close_socket()
        self._status(mode='stopped', poll_seconds=0)
