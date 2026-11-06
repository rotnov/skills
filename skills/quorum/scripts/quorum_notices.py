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
"""
import re
import time
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


def quiet(ledger, message, agent_id):
    """The notice kind when this message is receipted quietly here, else None."""
    if visible(ledger): return None
    return kind(message, agent_id)


def record(ledger, message, notice_kind, route, *, wall=time.time):
    """Bounded local log and per-sender summary; IDs, roles and times only, never a body.

    Callers record before the service ACK: a crash between the two then keeps the notice in status, and
    the redelivery of an unacknowledged notice is the one repeat, recognised here by its message ID.
    """
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
