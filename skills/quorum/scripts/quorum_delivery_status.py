"""The two ends of the server's status facts for one message and its one recipient.

`Reports` holds what this chat reports as a recipient. `StatusWatch` is what this chat learns as a
sender. The contract and the per-route proof are in references/protocol.md#delivery-statuses. A status
is a fact and never a grant: nothing here feeds a permission check, and no local delivery row depends
on one. `session_ack` still alone moves the queue.
"""
import json
import re
import time
from datetime import datetime

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
STUCK_STATUSES = (*UNREAD_STATUSES, 'in_progress', 'needs_owner')
# Local sender state; none of it holds a reason or a body.
CHECK_KEY = 'delivery-check'
CURSOR_KEY = 'delivery-cursor'
DRAINED_KEY = 'delivery-drained'
STUCK_KEY = 'delivery-stuck'
ALERTS_KEY = 'delivery-alerts'
# Used only until the service states its own limit in a check.
FIRST_CHECK_SECONDS = 900
# A stuck list with no deadline left still ages: its messages can settle unseen or leave the service's window.
STUCK_REFRESH_SECONDS = 86400
# How often a pull route catches up, and a parent without Realtime reads the feed, while messages are open.
PULL_DRAIN_SECONDS, POLL_DRAIN_SECONDS = 300, 60
PAGE_LIMIT, ALERT_LIMIT, SEEN_LIMIT = 16, 32, 512
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
                                   [(key,) for key in (CHECK_KEY, CURSOR_KEY, DRAINED_KEY, STUCK_KEY, ALERTS_KEY)])

    def _drop(self, key):
        with self.ledger.db: self.ledger.db.execute('delete from settings where key=?', (key,))


def reportable(delivery):
    """False for a server notice, which carries no status; the service refuses one anyway."""
    message = delivery.get('message')
    return not delivery.get('notification') and not (isinstance(message, dict) and message.get('maintenance'))


def stuck_limit(ledger):
    saved = ledger.setting(STUCK_KEY)
    limit = saved.get('limit_seconds') if isinstance(saved, dict) else None
    return limit if type(limit) is int and limit > 0 else FIRST_CHECK_SECONDS


def arm_check(ledger, due):
    """Check by `due`. An earlier deadline stands: the service's answer to it chooses the next one."""
    saved = ledger.setting(CHECK_KEY)
    if isinstance(saved, dict) and type(saved.get('due')) in (int, float): due = min(due, saved['due'])
    ledger.setting(CHECK_KEY, {'due': due})


def note_sent(ledger, *, wall=time.time):
    """This chat has an open outgoing message, so its reception should check on it once it is due.

    Kept on a server without the RPCs too: the message still needs its check after an upgrade.
    """
    arm_check(ledger, wall() + stuck_limit(ledger))


def timestamp(moment):
    """Seconds for one service time, or None. Local clocks only ever choose when to ask the service."""
    try: return datetime.fromisoformat(moment).timestamp()
    except (TypeError, ValueError): return None


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
    limit = page.get('limit_seconds') if isinstance(page, dict) else None
    saved = {'rows': rows, 'total': total if type(total) is int and total >= 0 else len(rows), 'checked_at': int(wall()),
             'limit_seconds': limit if type(limit) is int and limit > 0 else stuck_limit(ledger)}
    ledger.setting(STUCK_KEY, saved)
    return saved


def outgoing_summary(ledger):
    """The local view of this chat's outgoing messages for `status`, which stays offline.

    It is as old as `checked_at`: the listener refreshes it, and `session delivery` reads it live.
    """
    stuck = ledger.setting(STUCK_KEY)
    stuck = stuck if isinstance(stuck, dict) else {}
    alerts = ledger.setting(ALERTS_KEY)
    alerts = alerts if isinstance(alerts, dict) else {}
    return {'supported': statuses_supported(ledger), 'stuck': stuck.get('rows', []),
            'stuck_total': stuck.get('total', 0), 'checked_at': stuck.get('checked_at'),
            'alerts': [{key: item.get(key) for key in ('message_id', 'recipient_role', 'status', 'noted_at')}
                       for item in alerts.get('recent', [])[-5:]]}


class StatusWatch:
    """What this chat learns as a sender: event-driven, with no read while nothing is outstanding.

    A `status_changed` hint carries nothing, so `signal` reads this agent's own feed, as a hint does for
    the inbox. A routine status only updates local state. A material one is kept, then reaches the chat
    at most once: `notify` pushes it on a parent route, and a pull route hands it over with `take`.
    `tick` runs once per inbox pass and is local work unless something is due. Unread past the limit
    has no event, so that check waits on a deadline `session send` sets. Nothing here raises: a failed
    status read must never stop reception, and the next inbox read reports a real denial.
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

    def tick(self):
        self.reports.flush(self.api, self.scope)
        self._guarded(self._pass)
        # A failed read, or more alerts than one notice holds, can leave some kept and not shown.
        self._guarded(self._deliver)

    def take(self):
        """Alerts not shown yet, for a route whose own tool result reaches the chat."""
        return self._pending()

    def _guarded(self, step):
        try: step()
        except Exception as error:
            if rpc_missing(error): self.ledger.setting(SUPPORT_KEY, {'unsupported_at': self.wall()})

    def _pass(self):
        if not statuses_supported(self.ledger, wall=self.wall): return
        raw = self.ledger.db.execute('select data from settings where key=?', (CHECK_KEY,)).fetchone()
        check = json.loads(raw[0]) if raw else None
        now = self.wall()
        drained = self.ledger.setting(DRAINED_KEY)
        since = now - drained if type(drained) in (int, float) else float('inf')
        saved = self.ledger.setting(CURSOR_KEY)
        # A catch-up past its page budget finishes first, on any route and with no check left.
        behind = isinstance(saved, dict) and saved.get('agent_id') == self.scope['agent_id'] and saved.get('more') is True
        if behind or (self.stale and (self.catch_up or (check and since >= PULL_DRAIN_SECONDS))) or (
                self.polling and check and since >= POLL_DRAIN_SECONDS):
            self._drain()
        if check and type(check.get('due')) in (int, float) and now >= check['due']: self._check(raw[0])

    def _drain(self):
        saved = self.ledger.setting(CURSOR_KEY)
        same = isinstance(saved, dict) and saved.get('agent_id') == self.scope['agent_id']
        cursor = saved['seq'] if same and type(saved.get('seq')) is int else 0
        more = False
        for _ in range(PAGE_LIMIT):
            page = self.api.call('session_delivery_changes', {**self.scope, 'after': cursor})
            if not isinstance(page, dict) or not isinstance(page.get('rows'), list): return
            rows = [row for row in map(status_row, page['rows']) if row]
            self._follow(rows)
            for row in rows:
                if row['status'] in MATERIAL: self._alert(row, row['status'])
            if type(page.get('cursor')) is int and page['cursor'] >= cursor: cursor = page['cursor']
            more = page.get('more') is True
            # Saved after the alerts are kept: a crash between them repeats a row, and each alert is once only.
            self.ledger.setting(CURSOR_KEY, {'agent_id': self.scope['agent_id'], 'seq': cursor, 'more': more})
            if not more: break
        self.stale = more
        if not more: self.ledger.setting(DRAINED_KEY, self.wall())
        self._deliver()

    def _check(self, raw):
        page = self.api.call('session_delivery_stuck', self.scope)
        if not isinstance(page, dict) or type(page.get('open')) is not int: return
        saved = save_stuck(self.ledger, page, wall=self.wall)
        for row in saved['rows']:
            if row['unread']: self._alert(row, UNREAD)
        self._deliver()
        # Compare-and-set: a `session send` during this read keeps its own deadline.
        with self.ledger.db:
            if not page.get('open'):
                self.ledger.db.execute('delete from settings where key=? and data=?', (CHECK_KEY, raw))
                return
            seconds = page.get('next_check_seconds')
            if not (type(seconds) is int and seconds >= 0): seconds = STUCK_REFRESH_SECONDS if saved['total'] else None
            value = {'due': None if seconds is None else self.wall() + seconds}
            self.ledger.db.execute('update settings set data=? where key=? and data=?',
                                   (json.dumps(value), CHECK_KEY, raw))

    def _follow(self, rows):
        """Keep local state true between checks, as `session_delivery_stuck` would answer now.

        A changed message stays listed only if its own status is overdue, since a new status starts a new
        window. That window needs a deadline, which no earlier check could have set. A list cut short by
        the service cannot be followed here, so a change sends it back to the service.
        """
        now, limit = self.wall(), stuck_limit(self.ledger)
        windows = []
        for row in rows:
            if row['status'] not in STUCK_STATUSES: continue
            started = timestamp(row['status_at'])
            windows.append(now + limit if started is None else min(started, now) + limit)
        if windows: arm_check(self.ledger, min(windows))
        saved = self.ledger.setting(STUCK_KEY)
        if not isinstance(saved, dict) or not rows: return
        changed = {row['message_id']: row for row in rows}
        cached, kept = saved.get('rows', []), []
        for item in cached:
            row = changed.get(item['message_id'])
            if row is None: kept.append(item)
            elif row['status'] in STUCK_STATUSES and (timestamp(row['status_at']) or now) + limit <= now:
                kept.append({**item, **row, 'unread': row['status'] in UNREAD_STATUSES})
        total = max(0, saved.get('total', 0) - len(cached) + len(kept))
        if kept != cached: self.ledger.setting(STUCK_KEY, {**saved, 'rows': kept, 'total': total})
        if total > len(kept): arm_check(self.ledger, now)

    def _alerts(self):
        saved = self.ledger.setting(ALERTS_KEY)
        saved = saved if isinstance(saved, dict) else {}
        return {'recent': list(saved.get('recent', [])), 'seen': list(saved.get('seen', []))}

    def _alert(self, row, status):
        saved = self._alerts()
        # A status set again later is a new fact; a row read twice is not. Unread stays once per message.
        key = ':'.join((row['message_id'], status, '' if status == UNREAD else row['status_at'] or ''))
        if key in saved['seen']: return
        saved['seen'] = [*saved['seen'], key][-SEEN_LIMIT:]
        recent = [*saved['recent'], {'message_id': row['message_id'], 'recipient_role': row['recipient_role'],
                                     'status': status, 'noted_at': int(self.wall()), 'shown': False}]
        # Only what the chat has seen makes room.
        spare = len(recent) - ALERT_LIMIT
        kept = []
        for item in recent:
            if spare > 0 and item.get('shown'): spare -= 1
            else: kept.append(item)
        saved['recent'] = kept[-SEEN_LIMIT:]
        self.ledger.setting(ALERTS_KEY, saved)

    def _pending(self):
        """The oldest notice's worth, marked shown before it is pushed: an uncertain attempt is never repeated."""
        saved = self._alerts()
        pending = [item for item in saved['recent'] if not item.get('shown')][:ALERT_LIMIT]
        if not pending: return []
        for item in pending: item['shown'] = True
        self.ledger.setting(ALERTS_KEY, saved)
        return [{key: item[key] for key in ('message_id', 'recipient_role', 'status')} for item in pending]

    def _deliver(self):
        if self.notify is None: return
        pending = self._pending()
        if not pending: return
        # Whatever happens to the push, `session status` still lists these.
        try: self.notify(pending)
        except Exception: pass
