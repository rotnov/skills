"""One local delivery ledger for every native Quorum route.

This module owns the `delivery:<message UUID>` row, its compare-and-set transitions, the
`session_ack` call and the unknown and unsent outcomes. The state table per route is
references/protocol.md#when-each-route-acknowledges; the routes differ on purpose, so each transition
below belongs to the route named in its section.

A client supplies only its identity proof and a push or pull function. Nothing here decides who may
act, reads a peer body, or starts a client.
"""
import json
import time
import uuid

PREFIX = 'delivery:'
ABSENT_PREFIX = 'delivery-absent:'
# The rows a parent listener, a pull worker and a cloud parent may hold. A worker refuses any other row.
PUSH_STATES = ('intent', 'submitted', 'queued', 'acked')
PULL_STATES = ('offered', 'parked', 'acked')
CLOUD_STATES = ('offered', 'acked')
# Rows `session received` may confirm, and rows `session reply` also confirms.
RECEIPTABLE = ('submitted', 'queued', 'acked')
UNRECEIPTED = ('submitted', 'queued')
CAS_ATTEMPTS = 16


class DeliveryUnsent(ValueError):
    """No payload byte was written. The message is only this fixed code."""

    def __init__(self): super().__init__('session_delivery_unsent')


def server_ack(api, scope, message_id):
    """The service scopes this to the owned agent and exact session, and repeating it is harmless."""
    api.call('session_ack', {**scope, 'message_id': message_id})


class Deliveries:
    """The delivery rows of one chat's private ledger."""

    def __init__(self, ledger):
        self.ledger = ledger

    # --- Rows ---------------------------------------------------------------------------------------

    def get(self, message_id):
        return self.ledger.setting(PREFIX + message_id)

    def raw(self, message_id):
        """The stored text, which a compare-and-set compares; None when there is no row."""
        row = self.ledger.db.execute('select data from settings where key=?', (PREFIX + message_id,)).fetchone()
        return row[0] if row else None

    def rows(self):
        """(message ID, row) pairs in insertion order."""
        cursor = self.ledger.db.execute("select key,data from settings where key like 'delivery:%' order by rowid")
        return [(key[len(PREFIX):], json.loads(raw)) for key, raw in cursor.fetchall()]

    def states(self):
        return [value.get('state') for _, value in self.rows()]

    def require_states(self, allowed):
        """A row of another route stays uncertain here; it is never converted."""
        if any(state not in allowed for state in self.states()): raise ValueError('session_delivery_unknown')

    def archive(self):
        """Every row by its full key, for a retired binding's summary."""
        return {PREFIX + message_id: value for message_id, value in self.rows()}

    def clear(self):
        """Delete every row inside the caller's transaction."""
        self.ledger.db.execute("delete from settings where key like 'delivery:%'")

    # --- Compare-and-set ----------------------------------------------------------------------------

    def swap(self, message_id, raw, value):
        """Replace exactly the row read as `raw`; False when another writer moved it first."""
        with self.ledger.db:
            cursor = self.ledger.db.execute('update settings set data=? where key=? and data=?',
                                            (json.dumps(value), PREFIX + message_id, raw))
        return cursor.rowcount == 1

    def remove(self, message_id, raw):
        """Delete exactly the row read as `raw`, preserving any concurrent write."""
        with self.ledger.db:
            self.ledger.db.execute('delete from settings where key=? and data=?', (PREFIX + message_id, raw))

    def update(self, message_id, change, *, attempts=CAS_ATTEMPTS, optional=False):
        """Read, `change`, compare-and-set, and retry on a concurrent write.

        `change` returns the new row, the same row to leave it, or None to refuse; it may raise. Returns
        the row that now stands, or None for a refusal or when every attempt lost. Only an `optional`
        transition tolerates a missing row: for any other, json.loads(None) raises a TypeError.
        """
        for _ in range(attempts):
            raw = self.raw(message_id)
            if raw is None and optional: return None
            current = json.loads(raw)
            value = change(current)
            if value is None or value is current: return value
            if self.swap(message_id, raw, value): return value
        return None

    # --- Push routes: parent Claude pipe, Grok leader frame, Codex steer and queue ------------------

    def head_unknown(self, messages):
        """True when the service's current head is itself this ledger's unresolved attempt.

        Only that head blocks: a stuck attempt further back must not hold up a newer urgent message,
        and it blocks again once it is the head.
        """
        if not isinstance(messages, list) or not messages: return False
        delivery = self.get(str(uuid.UUID(messages[0]['id'])))
        return bool(delivery) and delivery['state'] == 'intent'

    def attempt(self, message_id, push, *, recheck):
        """One push: `intent` before any client I/O, then the proved outcome.

        `recheck` runs after the intent is durable, because persisting it may itself have blocked.
        A proved-unsent attempt removes only its own intent and raises. Any other failure keeps the
        intent and raises `session_delivery_unknown`. `push` returns 'pointer' for an accepted body
        pointer, which keeps its attempt UUID so that the owner can attest to its absence.
        """
        intent = {'state': 'intent', 'attempt_id': str(uuid.uuid4())}
        self.ledger.setting(PREFIX + message_id, intent)
        raw = json.dumps(intent)
        try: recheck()
        except ValueError:
            # No queue/IPC call began in this attempt.
            self.remove(message_id, raw)
            raise
        try: result = push()
        except DeliveryUnsent:
            # The helper (or a pre-helper guard) proved no payload write began. Never acknowledge.
            self.remove(message_id, raw)
            raise
        except Exception: raise ValueError('session_delivery_unknown') from None
        # No route has a synchronous delivery acknowledgement: `codex queue` accepting a pointer says
        # nothing about the chat either. Only the receiving chat can confirm.
        state = {'state': 'submitted'}
        if result == 'pointer': state['attempt_id'] = intent['attempt_id']
        # A receipt waits for this transition, but stay a compare-and-set so a later attempt or an
        # owner's reconciliation is never replaced.
        self.swap(message_id, raw, state)

    def settled(self, message_id, *, wait, live):
        """This row once any in-flight push has finished.

        A fast chat can run its receipt while the listener's helper is still returning. Only a `live`
        listener can move an intent on, so a stuck intent returns at once, and an unresolved one is
        never treated as a receipt.
        """
        deadline = time.monotonic() + wait
        while True:
            delivery = self.get(message_id)
            if (not delivery or delivery['state'] != 'intent' or time.monotonic() >= deadline or not live()):
                return delivery
            time.sleep(.1)

    def prove(self, message_id):
        """Move a submitted row to `queued`, the chat's proof of receipt whose ack any later pass may finish."""
        raw = self.raw(message_id)
        if not raw or json.loads(raw)['state'] != 'submitted': return
        # A compare-and-set: an attempt that replaced this row is not what the chat confirmed.
        self.swap(message_id, raw, {'state': 'queued'})

    def mark_acked(self, message_id):
        self.ledger.setting(PREFIX + message_id, {'state': 'acked'})

    def confirm(self, api, scope, message_id):
        """Record the chat's own proof of receipt for a submitted or queued row, then acknowledge it."""
        self.prove(message_id)
        server_ack(api, scope, message_id)
        self.mark_acked(message_id)

    def attest_queued(self, api, scope, message_id):
        """The owner's attestation: the only path that may acknowledge an unresolved attempt."""
        self.ledger.setting(PREFIX + message_id, {'state': 'queued'})
        server_ack(api, scope, message_id)
        self.mark_acked(message_id)

    def attest_absent(self, message_id, attempt_id, binding, *, unreceipted, recheck, exclusive):
        """Clear one exact unknown attempt or unread pointer on the owner's word, with an audit marker.

        The marker is historical, not a retry instruction: a repeated old command returns without
        touching any newer attempt. `unreceipted` is the exact service check, kept outside the short
        transaction, so a local receipt committed during it wins the exact compare-and-delete.
        """
        audit_key = ABSENT_PREFIX + message_id + ':' + attempt_id
        marker = {'state': 'confirmed_absent', 'message_id': message_id, 'attempt_id': attempt_id,
                  'binding': binding}
        if self.ledger.setting(audit_key) == marker: return
        raw = self.raw(message_id)
        if raw is None: raise ValueError('session_reconcile_denied')
        attempt = json.loads(raw)
        # Only an unread `codex queue` pointer keeps an attempt UUID once it is submitted.
        if attempt.get('state') not in ('intent', 'submitted') or attempt.get('attempt_id') != attempt_id:
            raise ValueError('session_reconcile_denied')
        if not unreceipted(): raise ValueError('session_reconcile_denied')
        recheck()
        with exclusive(), self.ledger.db:
            self.ledger.db.execute('begin immediate')
            recheck()
            deleted = self.ledger.db.execute('delete from settings where key=? and data=?',
                                             (PREFIX + message_id, raw))
            if deleted.rowcount != 1: raise ValueError('session_reconcile_denied')
            self.ledger.db.execute('insert into settings values (?,?)', (audit_key, json.dumps(marker)))

    # --- Pull routes: Codex tool inbox, Claude worker, Grok Bot cloud -------------------------------

    def offer(self, message_id, record, head, fields, **extra):
        """Offered locally before any tool result. This is not a receipt, and it is insert-only, so an
        acked or parked row is never downgraded. Only peer envelope data is cached."""
        with self.ledger.db:
            self.ledger.db.execute('insert or ignore into settings values (?,?)', (PREFIX + message_id, json.dumps({
                'state': 'offered', **extra, 'agent_id': record['agent_id'], 'session_ref': record['session_ref'],
                'quorum_id': record['quorum_id'], 'reply_to': bool(head.get('reply_to')),
                'notification': bool(head.get('notification')),
                'message': {key: head[key] for key in fields if key in head}})))

    def receipt_offer(self, api, scope, message_id, owned):
        """Worker receipt: acknowledge, then offered or parked -> acked, preserving every cached field.

        A concurrent reply-intent write changes the row, so re-read and retry rather than claim a
        receipt that was not persisted.
        """
        if self.get(message_id)['state'] == 'acked': return
        server_ack(api, scope, message_id)
        def change(current):
            if current.get('state') == 'acked': return current
            if current.get('state') not in ('offered', 'parked') or not owned(current): return None
            return {**current, 'state': 'acked'}
        if self.update(message_id, change) is None: raise ValueError('session_delivery_unknown')

    def receipt_cloud_offer(self, api, scope, message_id):
        """Cloud receipt: acknowledge, then one offered -> acked attempt.

        A lost response retries safely, and a concurrent receipt or stale read never rewrites the row.
        """
        server_ack(api, scope, message_id)
        def change(current):
            if not isinstance(current, dict) or current.get('state') != 'offered': return None
            return {**current, 'state': 'acked'}
        self.update(message_id, change, attempts=1, optional=True)

    def write_then_ack(self, api, scope, message_id, change, **options):
        """Park or dismiss: the local row first, then the receipt.

        A lost response then leaves the row, and the next pass repeats the ack. Returns the row, or None
        with no ack when `change` refused or every attempt lost.
        """
        value = self.update(message_id, change, **options)
        if value is not None: server_ack(api, scope, message_id)
        return value
