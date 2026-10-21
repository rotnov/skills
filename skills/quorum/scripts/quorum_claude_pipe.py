"""Experimental Claude Code >=2.1.234 Windows inbox, using only Python stdlib.

Protocol: newline JSON auth then a session-scoped user frame. Verified against
the installed Claude Code transport source; writes are not delivery receipts.
The exported inbox token stays in local process environments, never the ledger.
"""
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import uuid


def binding(session_id):
    if os.environ.get('CLAUDE_CODE_SESSION_ID') != session_id:
        raise ValueError('session_binding_denied')
    endpoint = os.environ.get('CLAUDE_CODE_MESSAGING_SOCKET', '')
    token = os.environ.get('CLAUDE_CODE_MESSAGING_TOKEN', '')
    # Local named pipes only: never a remote UNC path or an ordinary file.
    if not re.fullmatch(r'\\\\\.\\pipe\\[A-Za-z0-9_.-]{1,240}', endpoint):
        raise ValueError('claude_pipe_unavailable')
    if not token or len(token) > 4096: raise ValueError('claude_pipe_unavailable')
    if os.name != 'nt': raise ValueError('claude_pipe_unavailable')
    return endpoint


def frames(session_id, message_id, content, token):
    session_id, message_id = str(uuid.UUID(session_id)), str(uuid.UUID(message_id))
    if not isinstance(content,str) or not content or len(content.encode()) > 48000:
        raise ValueError('invalid_message')
    auth = {'type':'auth', 'token':token}
    event = {'type':'user', 'session_id':session_id, 'from':'quorum', 'msg_id':message_id,
             'message':{'role':'user', 'content':content}}
    return (json.dumps(auth)+'\n'+json.dumps(event,ensure_ascii=False)+'\n').encode()


def submit(session_id, message_id, content):
    # A short-lived Python I/O helper bounds a stuck native WriteFile/open. This
    # process never runs a model, reads provider auth, or receives a token argv.
    payload = json.dumps({'session_id':session_id,'message_id':message_id,'content':content},ensure_ascii=False)
    try:
        result = subprocess.run([sys.executable,str(Path(__file__).resolve())], input=payload,
                                text=True,encoding='utf-8',capture_output=True,timeout=10)
    except (OSError,subprocess.TimeoutExpired):
        raise ValueError('session_delivery_unknown') from None
    if result.returncode: raise ValueError('session_delivery_unknown')


def main():
    try:
        raw = sys.stdin.buffer.read(131073)
        if len(raw)>131072: raise ValueError('invalid_message')
        data = json.loads(raw)
        endpoint = binding(data['session_id'])
        payload = frames(data['session_id'],data['message_id'],data['content'],
                         os.environ['CLAUDE_CODE_MESSAGING_TOKEN'])
        with open(endpoint,'r+b',buffering=0) as pipe:
            view=memoryview(payload)
            while view:
                written=pipe.write(view)
                if not written: raise OSError('pipe_write_failed')
                view=view[written:]
        return 0
    except Exception:
        # No raw exception, input, pipe name or local authentication in output.
        return 1


if __name__=='__main__': raise SystemExit(main())
