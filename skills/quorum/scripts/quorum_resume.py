"""Recovery authority for one previously enabled native conversation; never a new owner grant."""
import math
import os
from pathlib import Path
import time
import uuid


def binding(record):
    return {key:record.get(key) for key in
            ('engine', 'session_ref', 'agent_id', 'quorum_id', 'project_ref', 'cwd', 'role', 'quorum')}


def enable(ledger, record):
    ledger.setting('session-reception', {'enabled':True, 'binding':binding(record), 'expires_at':None})


def require_enabled(ledger, record):
    grant = ledger.setting('session-reception') or {}
    expires = grant.get('expires_at')
    valid_expiry = (expires is None or (type(expires) in (int, float) and math.isfinite(expires)
                                        and expires > time.time()))
    if (grant.get('enabled') is not True or grant.get('binding') != binding(record) or not valid_expiry):
        raise ValueError('session_recovery_denied')


def hook_environment(event, client, environment):
    """Use only a reviewed provider SessionStart/resume event, never a recent-session guess.

    Codex documents session_id in its hook payload; Grok additionally injects
    GROK_SESSION_ID itself. Neither the payload nor matching exports grant
    reception: recover still requires the previously enabled exact binding.
    """
    if not isinstance(event, dict) or client not in ('codex', 'grok'):
        raise ValueError('session_recovery_denied')
    sid = event.get('session_id') if client == 'codex' else event.get('sessionId')
    try: canonical = str(uuid.UUID(sid))
    except (ValueError, TypeError, AttributeError): raise ValueError('session_recovery_denied') from None
    if (canonical != sid or event.get('hook_event_name') != 'SessionStart' or event.get('source') != 'resume'
            or not isinstance(event.get('cwd'), str) or not Path(event['cwd']).is_absolute()):
        raise ValueError('session_recovery_denied')
    variable = 'CODEX_THREAD_ID' if client == 'codex' else 'GROK_SESSION_ID'
    competing = ('CLAUDE_CODE_SESSION_ID', 'CLAUDE_CODE_HOST_SESSION_ID',
                 'CLAUDE_CODE_MESSAGING_SOCKET', 'CLAUDE_CODE_MESSAGING_TOKEN',
                 'GROK_SESSION_ID' if client == 'codex' else 'CODEX_THREAD_ID')
    if any(environment.get(name) for name in competing) or environment.get(variable) not in (None, '', sid):
        raise ValueError('session_recovery_denied')
    if client == 'grok' and (environment.get(variable) != sid or not environment.get('GROK_LEADER_SOCKET')):
        raise ValueError('session_recovery_denied')
    return {**environment, variable:sid}


def main(argv=None):
    """Optional owner-installed startup hook; bounded input and fixed failure diagnostics only."""
    import argparse
    import contextlib
    import io
    import json
    import sys
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state', type=Path, required=True)
    parser.add_argument('--client', choices=('codex', 'grok'), required=True)
    args = parser.parse_args(argv)
    try:
        raw = sys.stdin.buffer.read(8193)
        if len(raw) > 8192: raise ValueError('session_recovery_denied')
        event = json.loads(raw)
        environment = hook_environment(event, args.client, os.environ)
        # The trusted provider event is the hook's identity contract. Shell
        # invocations instead use the actual client's unchanged exports.
        os.environ.update(environment)
        os.chdir(event['cwd'])
        from quorum import main as quorum_main
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            result = quorum_main(['--state', str(args.state), 'session', 'recover'])
        if result:
            # Do not print ledger status, paths, environment or provider payloads.
            print('quorum: reception recovery needs attention; inspect session status in this same chat', file=sys.stderr)
        return result
    except (ValueError, OSError, TypeError):
        print('quorum: session_recovery_denied', file=sys.stderr)
        return 3


if __name__ == '__main__': raise SystemExit(main())
