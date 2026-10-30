"""Read-only fallback guidance; health timeouts prove neither hosting nor sleep."""
from quorum_api import QuorumError


def with_wake_diagnostics(peers):
    if not isinstance(peers, list) or any(not isinstance(peer, dict) for peer in peers):
        raise QuorumError('invalid_response')
    return [dict(peer, wake={
        'status': 'unsupported',
        'provider': 'unknown',
        'suspension': 'unknown',
        'action': 'owner_open_original_chat',
        'next_step': ('Quorum has no verified automatic wake for this binding. Ask its owner to open the '
                      'original chat and recover its existing binding if reception needs recovery. The '
                      'peer view does not prove a message was sent or queued. Leave any existing message '
                      'queued and inspect its delivery status; a timeout does not establish that the computer is asleep.'),
    }) if peer.get('availability') == 'unavailable' else dict(peer) for peer in peers]
