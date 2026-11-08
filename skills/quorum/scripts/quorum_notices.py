"""Server transport notices (Hello and roster) are receipted inside reception, never shown as envelopes (#126).

A notice is auto-receipted only when every condition holds:
- structural: the service marks it `notification: true` (a server `session_notices` row, which only the
  server's own Hello and roster writers create; a peer's `message_send` cannot set it) and it has no
  `reply_to`, no maintenance marker and an ordinary priority;
- body: the exact server-generated Hello or roster text. The inbox exposes no notice kind, so the
  body is the fallback discriminator between notice kinds, never sufficient on its own;
- direction: a Hello comes from another agent, a roster from this agent itself.
Anything else, including peer text that imitates a notice, is delivered as before. The owner's
`--show-transport-notices` opt-out (for debugging) delivers these notices as envelopes again.

Maintenance proposals are coalesced in the same place (#191). The first typed `quorum.update` notice while none
is pending reaches the chat once and becomes this binding's pending update. Each later notice while that owner
decision is pending is quiet: it is receipted here, becomes the newest pending target, and never wakes a model or
produces a reply. `session status` shows the newest target as `pending_update`. The owner's refresh to it ends the
pending state, so the next new target reaches the chat once again. The opt-out does not apply to maintenance.
"""
import json
import re
import time
import uuid
from quorum_invitations import PROJECT_REF_LIMIT, ROLE, ROLE_LIMIT

SHOW_KEY = 'show-transport-notices'
LOG_KEY = 'transport-notices'
RECENT_LIMIT = 20
PEER_LIMIT = 128
# Bounds shared with the invitation validator and the server: roles match agents_role_check, and a project
# reference is at most PROJECT_REF_LIMIT characters (role assignment, 20261105140000). The server does not
# restrict a project reference's charset for every registration path, so any single-line text is accepted
# here; a longer or multi-line one is never receipted quietly and simply stays visible.
_ROLE = ROLE.pattern  # One source of truth for the role charset and bound.
_PROJECT = r'[^\r\n]{1,%d}' % PROJECT_REF_LIMIT
_COUNT = r'(?:0|[1-9]\d{0,2})'  # 0..999 covers the server's 128-peer cap
_TAIL = r'\. Transport notice; no reply required\.'
# The roster lists at most 16 roles, then ', ...' when more are available.
_ROLES = _ROLE + r'(?:, ' + _ROLE + r'){0,15}(?:, \.\.\.)?'
HELLO = re.compile(r'Hello! Listener connected: (?P<role>' + _ROLE + r') \(project ' + _PROJECT + r'\)' + _TAIL)
ROSTER = re.compile(r'Quorum roster: (?:no other native session is available in project ' + _PROJECT
                    + r'\. Hello reached ' + _COUNT + r' registered peer\(s\)|' + _COUNT
                    + r' peer\(s\) available in project ' + _PROJECT + r': ' + _ROLES + r')' + _TAIL)
# Longest possible server body: the 16-role roster with a maximal project reference.
BODY_LIMIT = 128 + PROJECT_REF_LIMIT + 16 * (ROLE_LIMIT + 2)


def kind(message, agent_id):
    """'hello', 'roster' or None for one inbox message addressed to `agent_id`."""
    if not isinstance(message, dict) or message.get('notification') is not True: return None
    if message.get('reply_to') or message.get('maintenance'): return None
    if message.get('priority') not in (None, 'normal'): return None
    body, sender, recipient = message.get('body'), message.get('sender_id'), message.get('recipient_id')
    if not isinstance(body, str) or len(body) > BODY_LIMIT or '\n' in body or '\r' in body: return None
    if not isinstance(sender, str) or not sender or recipient != agent_id: return None
    if HELLO.fullmatch(body) and sender != agent_id: return 'hello'
    if ROSTER.fullmatch(body) and sender == agent_id: return 'roster'
    return None


def visible(ledger):
    """The owner's debugging opt-out: deliver transport notices as ordinary envelopes."""
    return ledger.setting(SHOW_KEY) is True


def set_visible(ledger, show):
    ledger.setting(SHOW_KEY, bool(show))


PENDING_KEY = 'pending-update'
_SHA256 = re.compile('[0-9a-f]{64}')


def maintenance(message, agent_id):
    """The typed proposal `{notice_id, protocol_version, content_sha256}` addressed to `agent_id`, else None.

    Only the authenticated inbox field counts; a body that claims to be an update is never a proposal here.
    """
    if not isinstance(message, dict) or message.get('reply_to') or message.get('recipient_id') != agent_id: return None
    marker = message.get('maintenance')
    if not isinstance(marker, dict) or marker.get('kind') != 'quorum.update': return None
    target = marker.get('target') if isinstance(marker.get('target'), dict) else {}
    digest, protocol = target.get('content_sha256'), target.get('protocol_version')
    if not isinstance(digest, str) or not _SHA256.fullmatch(digest): return None
    if protocol is not None and (type(protocol) is not int or not 0 <= protocol < 2**31): return None
    try: notice_id = str(uuid.UUID(str(marker.get('notice_id'))))
    except ValueError: return None
    return {'notice_id': notice_id, 'protocol_version': protocol, 'content_sha256': digest}


def _load(db, agent_id):
    """This binding's pending record, read inside the caller's transaction."""
    row = db.execute('select data from settings where key=?', (PENDING_KEY,)).fetchone()
    saved = json.loads(row[0]) if row else None
    return saved if isinstance(saved, dict) and agent_id and saved.get('agent_id') == agent_id else None


def _store(db, value):
    db.execute('insert into settings values (?,?) on conflict(key) do update set data=excluded.data',
               (PENDING_KEY, json.dumps(value)))


def _quiet_maintenance(ledger, message, agent_id, *, wall=time.time):
    """True when an owner decision is already pending for another proposal to this binding.

    The decision and its record are one IMMEDIATE transaction, which `settle_update` also takes, so an owner's
    refresh can never clear the pending state between them and lose a quiet proposal. A quiet proposal becomes the
    newest pending target here, before the caller's ACK; IDs, hashes and times only, never a body. With nothing
    pending, this proposal is recorded as the one surfaced in the chat, so a repeat offer of that same message (a
    lost receipt) is shown again rather than swallowed.
    """
    proposal = maintenance(message, agent_id)
    if proposal is None: return False
    message_id = message.get('id')
    with ledger.db:
        ledger.db.execute('begin immediate')
        pending = _load(ledger.db, agent_id)
        if pending is None:
            _store(ledger.db, {'agent_id': agent_id, 'message_id': message_id, **proposal,
                               'received_at': int(wall()), 'surfaced_message_id': message_id,
                               'quiet_notices': 0, 'seen': [message_id]})
            return False
        if message_id == pending.get('surfaced_message_id'): return False
        seen = [item for item in pending.get('seen', []) if isinstance(item, str)]
        # The redelivery of an unacknowledged notice is the one repeat: still quiet, recorded once.
        if message_id not in seen:
            _store(ledger.db, {**pending, 'message_id': message_id, **proposal, 'received_at': int(wall()),
                               'quiet_notices': int(pending.get('quiet_notices', 0)) + 1,
                               'seen': (seen + [message_id])[-RECENT_LIMIT:]})
        return True


def pending_update(ledger, agent_id=None):
    """Status view of the newest maintenance target awaiting the owner, or None."""
    from quorum_release import short_hash
    saved = ledger.setting(PENDING_KEY)
    if not isinstance(saved, dict) or not saved.get('agent_id'): return None
    if agent_id is not None and saved.get('agent_id') != agent_id: return None
    return {'release': short_hash(saved.get('content_sha256')), 'content_sha256': saved.get('content_sha256'),
            'protocol_version': saved.get('protocol_version'), 'notice_id': saved.get('notice_id'),
            'received_at': saved.get('received_at'), 'quiet_notices': int(saved.get('quiet_notices', 0))}


def settle_update(ledger, agent_id, content_sha256):
    """The owner refreshed this binding to `content_sha256`, which decides a pending update for that target.

    A pending update for another, newer target stays, so the owner still sees the newest one. This takes the same
    IMMEDIATE transaction as the listener's decision, so a concurrent quiet proposal either lands first (and stays
    pending as the newer target) or after (and finds nothing pending, so it reaches the chat once).
    """
    with ledger.db:
        ledger.db.execute('begin immediate')
        pending = _load(ledger.db, agent_id)
        if pending is not None and pending.get('content_sha256') == content_sha256:
            # The ledger keeps a key once written, so a decided state is a record with no pending binding.
            _store(ledger.db, {'agent_id': None, 'settled_content_sha256': content_sha256})


def quiet(ledger, message, agent_id):
    """The notice kind when this message is receipted quietly here, else None."""
    if _quiet_maintenance(ledger, message, agent_id): return 'maintenance'
    if visible(ledger): return None
    return kind(message, agent_id)


def record(ledger, message, notice_kind, route, *, wall=time.time):
    """Bounded local log and per-sender summary; IDs, roles and times only, never a body.

    Callers record before the service ACK: a crash between the two then keeps the notice in status, and
    the redelivery of an unacknowledged notice is the one repeat, recognised here by its message ID.
    """
    if notice_kind == 'maintenance': return  # Recorded with its quiet decision, in one transaction.
    now = int(wall())
    saved = ledger.setting(LOG_KEY)
    saved = saved if isinstance(saved, dict) else {}
    recent = [item for item in saved.get('recent', []) if isinstance(item, dict)]
    if any(item.get('message_id') == message.get('id') for item in recent): return
    peers = saved.get('peers') if isinstance(saved.get('peers'), dict) else {}
    recent.append({'message_id': message.get('id'), 'kind': notice_kind, 'route': route,
                   'sender_id': message.get('sender_id'), 'sender_role': message.get('sender_role'),
                   'received_at': now})
    if notice_kind == 'hello':
        sender = message['sender_id']
        previous = peers.pop(sender, None) or {}
        peers[sender] = {'role': message.get('sender_role'), 'last_hello_at': now,
                         'hellos': int(previous.get('hellos', 0)) + 1}
        while len(peers) > PEER_LIMIT: peers.pop(next(iter(peers)))
    ledger.setting(LOG_KEY, {'auto_receipted': int(saved.get('auto_receipted', 0)) + 1,
                             'recent': recent[-RECENT_LIMIT:], 'peers': peers})


def summary(ledger, limit=5):
    """Status view: whether notices are shown, how many were receipted quietly and the latest few."""
    saved = ledger.setting(LOG_KEY)
    saved = saved if isinstance(saved, dict) else {}
    return {'shown': visible(ledger), 'auto_receipted': int(saved.get('auto_receipted', 0)),
            'recent': list(saved.get('recent', []))[-limit:]}


def annotate(peers, ledger):
    """Add each peer's last quietly receipted Hello to a peer view; other fields are unchanged."""
    saved = ledger.setting(LOG_KEY) if ledger is not None else None
    known = (saved or {}).get('peers') if isinstance(saved, dict) else None
    if not isinstance(peers, list) or not isinstance(known, dict): return peers
    result = []
    for peer in peers:
        entry = known.get(peer.get('agent_id')) if isinstance(peer, dict) else None
        if isinstance(entry, dict):
            peer = dict(peer, transport_notice={'last_hello_at': entry.get('last_hello_at'),
                                                'hellos': entry.get('hellos', 0)})
        result.append(peer)
    return result
