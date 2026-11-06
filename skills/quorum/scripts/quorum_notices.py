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

SHOW_KEY = 'show-transport-notices'
LOG_KEY = 'transport-notices'
RECENT_LIMIT = 20
PEER_LIMIT = 128
BODY_LIMIT = 2048
_ROLE = r'[a-z][a-z0-9-]{0,47}'
_TAIL = r'\. Transport notice; no reply required\.'
HELLO = re.compile(r'Hello! Listener connected: (?P<role>' + _ROLE + r') \(project .{1,256}\)' + _TAIL)
ROSTER = re.compile(r'Quorum roster: (?:no other native session is available in project .{1,256}\. Hello reached '
                    r'\d{1,3} registered peer\(s\)|\d{1,3} peer\(s\) available in project .{1,256}: '
                    r'[a-z0-9 ,.-]{1,1200})' + _TAIL)


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
    """Bounded local log and per-sender summary; IDs, roles and times only, never a body."""
    now = int(wall())
    saved = ledger.setting(LOG_KEY)
    saved = saved if isinstance(saved, dict) else {}
    recent = [item for item in saved.get('recent', []) if isinstance(item, dict)]
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
