"""The two ends of the server's status facts for one message and its one recipient.

`Reports` holds what this chat reports as a recipient. `StatusWatch` is what this chat learns as a
sender. The contract and the per-route proof are in references/protocol.md#delivery-statuses. A status
is a fact and never a grant: nothing here feeds a permission check, and no local delivery row depends
on one. `session_ack` still alone moves the queue.
"""
import json
import re
import time

from quorum_api import CANONICAL_UUID, ROLE, rpc_missing, server_refused

# A recipient's report waits here until the service has it, so a lost response or a crash after the
# receipt cannot lose it. One day bounds what an unreachable service leaves behind.
MARK_PREFIX = 'delivery-mark:'
MARK_SECONDS = 86400
# A server without the status RPCs is asked again after this delay.
SUPPORT_KEY = 'delivery-status-support'
SUPPORT_RETRY = 600
# Statuses a chat sets itself; `queued`, `read` and `answered` have their own proof.
EXPLICIT_STATUSES = ('in_progress', 'deferred', 'needs_owner', 'declined', 'done', 'failed')
REASON_REQUIRED = ('deferred', 'declined')
REASON_LIMIT = 512
STATUSES = ('sent', 'queued', 'read', 'answered', *EXPLICIT_STATUSES)
# Only these reach the chat. `unread` is a stuck message past the service's limit, not a status.
MATERIAL = ('failed', 'declined', 'needs_owner')
UNREAD = 'unread'
UNREAD_STATUSES = ('sent', 'queued')
# Local sender state; none of it holds a reason or a body. Stuck state is only the service's last answer:
# nothing edits it. The next check is one time, and the feed cursor shares a row with the alerts it read.
CHECK_KEY = 'delivery-check'
STUCK_KEY = 'delivery-stuck'
WATCH_KEY = 'delivery-watch'
# A send's first deadline, the service's limit. Each answer then names the next one.
FIRST_CHECK_SECONDS = 900
# With messages open and none coming due, the stuck list can still age out of the service's window.
OPEN_CHECK_SECONDS = 86400
# How often a pull route catches up, and a parent without Realtime reads the feed, while messages are open.
PULL_DRAIN_SECONDS, POLL_DRAIN_SECONDS = 300, 60
PAGE_LIMIT, ALERT_LIMIT, SHOWN_LIMIT = 16, 32, 5
# The one line a peer request's envelope adds: optional, and never about local policy.
MARK_HINT = ('Optional: tell the sender where this stands with the mark command, one of in_progress, deferred, '
             'needs_owner, declined, done or failed; deferred and declined also need --reason-stdin with a short '
             'reason. The sender sees it as a fact. It grants nothing, and it must not state owner policy. ')


def statuses_supported(ledger, *, wall=time.time):
    saved = ledger.setting(SUPPORT_KEY)
    if not isinstance(saved, dict) or type(saved.get('unsupported_at')) not in (int, float): return True
    return not 0 <= wall() - saved['unsupported_at'] < SUPPORT_RETRY


class Reports:
    """Facts this chat owes the service, kept until it has them."""

    def __init__(self, ledger):
        self.ledger = ledger

    def note(self, message_id, status, *, proof=None, wall=time.time):
        """Keep one fact for the service. Reception never waits on it, and repeating it changes nothing."""
        if not statuses_supported(self.ledger): return
        self.ledger.setting(MARK_PREFIX + message_id + ':' + status,
                            {'message_id': message_id, 'status': status, 'proof': proof, 'noted_at': wall()})

    def flush(self, api, scope, *, wall=time.time):
        """Upload every kept fact, oldest first. Never raises: a report must not cost a delivery.

        A refused fact is dropped, a server without the RPC drops them all, and anything else keeps
        them for the next pass.
        """
        cursor = self.ledger.db.execute("select key,data from settings where key like 'delivery-mark:%' order by rowid")
        for key, raw in cursor.fetchall():
            value = json.loads(raw)
            if not 0 <= wall() - value['noted_at'] < MARK_SECONDS:
                self._drop(key)
                continue
            payload = {**scope, 'message_id': value['message_id'], 'status': value['status']}
            if value.get('proof'): payload['proof'] = value['proof']
            try: api.call('session_delivery_mark', payload)
            except Exception as error:
                if rpc_missing(error):
                    self.ledger.setting(SUPPORT_KEY, {'unsupported_at': wall()})
                    with self.ledger.db:
                        self.ledger.db.execute("delete from settings where key like 'delivery-mark:%'")
                    return
                if not server_refused(error): return
            self._drop(key)

    def report(self, api, scope, message_id, status, *, proof=None):
        self.note(message_id, status, proof=proof)
        self.flush(api, scope)

    def clear(self):
        """Delete this binding's status state inside the caller's transaction."""
        self.ledger.db.execute("delete from settings where key like 'delivery-mark:%'")
        self.ledger.db.executemany('delete from settings where key=?',
                                   [(key,) for key in (CHECK_KEY, STUCK_KEY, WATCH_KEY)])

    def _drop(self, key):
        with self.ledger.db: self.ledger.db.execute('delete from settings where key=?', (key,))


def reportable(delivery):
    """False for a server notice, which carries no status; the service refuses one anyway."""
    message = delivery.get('message')
    return not delivery.get('notification') and not (isinstance(message, dict) and message.get('maintenance'))


def arm_check(ledger, due):
    """Check by `due`, or sooner if already set. A new generation tells a check in flight that it is out of date."""
    saved = ledger.setting(CHECK_KEY) or {}
    if type(saved.get('due')) in (int, float): due = min(due, saved['due'])
    ledger.setting(CHECK_KEY, {'due': due, 'generation': saved.get('generation', 0) + 1})


def note_sent(ledger, *, wall=time.time):
    """This chat has an open outgoing message, so its reception should check on it once it is due.

    Kept on a server without the RPCs too: the message still needs its check after an upgrade.
    """
    arm_check(ledger, wall() + FIRST_CHECK_SECONDS)


def status_row(value):
    """Only fixed-shape fields of one service row; anything else is dropped, never echoed."""
    if (not isinstance(value, dict) or not isinstance(value.get('message_id'), str)
            or not CANONICAL_UUID.fullmatch(value['message_id']) or value.get('status') not in STATUSES):
        return None
    role, moment = value.get('recipient_role'), value.get('status_at')
    return {'message_id': value['message_id'], 'status': value['status'],
            'recipient_role': role if isinstance(role, str) and ROLE.fullmatch(role) else None,
            'status_at': moment if isinstance(moment, str) and re.fullmatch(r'[0-9TZ:.+ -]{10,40}', moment) else None}


def stuck_rows(page):
    rows = page.get('stuck') if isinstance(page, dict) else None
    result = []
    for value in rows if isinstance(rows, list) else []:
        row = status_row(value)
        if row: result.append({**row, 'unread': row['status'] in UNREAD_STATUSES})
    return result


def save_stuck(ledger, page, *, wall=time.time):
    rows = stuck_rows(page)
    total = page.get('stuck_total') if isinstance(page, dict) else None
    saved = {'rows': rows, 'total': total if type(total) is int and total >= 0 else len(rows), 'checked_at': int(wall())}
    ledger.setting(STUCK_KEY, saved)
    return saved


def outgoing_summary(ledger):
    """The local view of this chat's outgoing messages for `status`, which stays offline.

    It is as old as `checked_at`: the listener refreshes it, and `session delivery` reads it live.
    """
    stuck = ledger.setting(STUCK_KEY) or {}
    watch = ledger.setting(WATCH_KEY) or {}
    return {'supported': statuses_supported(ledger), 'stuck': stuck.get('rows', []),
            'stuck_total': stuck.get('total', 0), 'checked_at': stuck.get('checked_at'),
            'alerts': [*watch.get('shown', []), *watch.get('pending', [])][-SHOWN_LIMIT:]}


class StatusWatch:
    """What this chat learns as a sender: event-driven, with no read while nothing is outstanding.

    A `status_changed` hint carries nothing, so `signal` reads this agent's own feed, as a hint does for
    the inbox. Each feed row is one new status. A page moves the cursor and queues its material statuses
    in one write, and a full queue stops the feed, so the cursor never passes an alert. Any change sets
    the next check to now, so the service alone says what is stuck. A routine status starts no turn. An
    alert reaches the chat at most once: `notify` pushes it on a parent route, and a pull route hands it
    over with `take`. `tick` runs once per inbox pass and is local work unless something is due. Unread
    past the limit has no event, so it waits on the deadline `session send` sets. Nothing here raises: a
    failed status read must never stop reception, and the next inbox read reports a real denial.
    """
    def __init__(self, api, ledger, record, *, notify=None, catch_up=True, wall=time.time):
        self.api, self.ledger, self.notify, self.catch_up, self.wall = api, ledger, notify, catch_up, wall
        self.reports = Reports(ledger)
        self.scope = {'agent_id': record['agent_id'], 'session_ref': record['session_ref']}
        # A new subscription may have missed hints; polling receives none.
        self.stale, self.polling = True, False

    def connected(self):
        self.stale, self.polling = True, False

    def fallback(self):
        self.stale, self.polling = True, True

    def signal(self):
        self.stale = True
        self._guarded(self._drain)
        self._guarded(self._check_due)

    def tick(self):
        self.reports.flush(self.api, self.scope)
        self._guarded(self._pass)
        # A failed read, or more alerts than one notice holds, can leave some not shown.
        self._guarded(self._deliver)

    def take(self):
        """Alerts not shown yet, for a route whose own tool result reaches the chat."""
        return self._pending()

    def _guarded(self, step):
        try: step()
        except Exception as error:
            if rpc_missing(error): self.ledger.setting(SUPPORT_KEY, {'unsupported_at': self.wall()})

    def _state(self):
        saved = self.ledger.setting(WATCH_KEY)
        if isinstance(saved, dict) and saved.get('agent_id') == self.scope['agent_id']: return saved
        return {'agent_id': self.scope['agent_id'], 'seq': 0, 'more': False, 'read_at': None, 'pending': [], 'shown': []}

    def _pass(self):
        if not statuses_supported(self.ledger, wall=self.wall): return
        state, is_open = self._state(), self.ledger.setting(CHECK_KEY) is not None
        since = self.wall() - state['read_at'] if state['read_at'] is not None else float('inf')
        # An unfinished catch-up comes first, on any route. An idle pull route or poller reads nothing.
        if state['more'] or (self.stale and (self.catch_up or (is_open and since >= PULL_DRAIN_SECONDS))) or (
                self.polling and is_open and since >= POLL_DRAIN_SECONDS):
            self._drain()
        self._check_due()

    def _drain(self):
        state = self._state()
        for _ in range(PAGE_LIMIT):
            if len(state['pending']) >= ALERT_LIMIT:
                self._deliver()
                state = self._state()
                if len(state['pending']) >= ALERT_LIMIT:
                    state['more'] = True
                    break
            page = self.api.call('session_delivery_changes', {**self.scope, 'after': state['seq']})
            if not isinstance(page, dict) or not isinstance(page.get('rows'), list): return
            if page['rows']: arm_check(self.ledger, self.wall())
            rows = [row for row in map(status_row, page['rows']) if row]
            seq = page.get('cursor')
            state = {**state, 'seq': seq if type(seq) is int and seq >= state['seq'] else state['seq'],
                     'more': page.get('more') is True,
                     'pending': [*state['pending'], *(self._alert(row, row['status']) for row in rows
                                                      if row['status'] in MATERIAL)]}
            self.ledger.setting(WATCH_KEY, state)
            if not state['more']: break
        self.stale = False
        if not state['more']: state['read_at'] = self.wall()
        self.ledger.setting(WATCH_KEY, state)
        self._deliver()

    def _check_due(self):
        raw = self.ledger.db.execute('select data from settings where key=?', (CHECK_KEY,)).fetchone()
        if raw and self.wall() >= json.loads(raw[0])['due']: self._check(raw[0])

    def _check(self, raw):
        page = self.api.call('session_delivery_stuck', self.scope)
        if not isinstance(page, dict) or type(page.get('open')) is not int: return
        # What the last answer already listed needs no new alert.
        known = {row['message_id'] for row in (self.ledger.setting(STUCK_KEY) or {}).get('rows', [])}
        fresh = [self._alert(row, UNREAD) for row in stuck_rows(page) if row['unread'] and row['message_id'] not in known]
        if fresh:
            state = self._state()
            self.ledger.setting(WATCH_KEY, {**state, 'pending': [*state['pending'], *fresh]})
        save_stuck(self.ledger, page, wall=self.wall)
        self._deliver()
        # Compare-and-set: a send or a change during this read keeps its own, newer deadline.
        with self.ledger.db:
            if not page['open']:
                self.ledger.db.execute('delete from settings where key=? and data=?', (CHECK_KEY, raw))
                return
            seconds = page.get('next_check_seconds')
            seconds = seconds if type(seconds) is int and seconds >= 0 else OPEN_CHECK_SECONDS
            self.ledger.db.execute('update settings set data=? where key=? and data=?',
                                   (json.dumps({**json.loads(raw), 'due': self.wall() + seconds}), CHECK_KEY, raw))

    def _alert(self, row, status):
        return {'message_id': row['message_id'], 'recipient_role': row['recipient_role'], 'status': status,
                'noted_at': int(self.wall())}

    def _pending(self):
        """The oldest notice's worth, moved to shown before it is pushed: an uncertain attempt is never repeated."""
        state = self._state()
        pending = state['pending'][:ALERT_LIMIT]
        if not pending: return []
        self.ledger.setting(WATCH_KEY, {**state, 'pending': state['pending'][ALERT_LIMIT:],
                                        'shown': [*state['shown'], *pending][-SHOWN_LIMIT:]})
        return [{key: item[key] for key in ('message_id', 'recipient_role', 'status')} for item in pending]

    def _deliver(self):
        if self.notify is None: return
        pending = self._pending()
        if not pending: return
        # Whatever happens to the push, `session status` still lists these.
        try: self.notify(pending)
        except Exception: pass
