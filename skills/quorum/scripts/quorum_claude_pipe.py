"""Experimental Claude Code local inbox, using only Python stdlib.

Protocol: newline JSON auth then a session-scoped user frame. Verified against
the installed Claude Code transport source; writes are not delivery receipts.
The exported inbox token stays in local process environments, never the ledger.
"""
import errno
import json
import os
from pathlib import Path
import re
import socket
import stat
import subprocess
import sys
import uuid


class DeliveryUnsent(ValueError):
    """No payload byte was written. The message is only this fixed code."""

    def __init__(self):
        super().__init__('session_delivery_unsent')


# Child exit 2 is definite no-write only when both captured streams are empty.
_EXIT_UNSENT = 2
_UNSENT_ERRNO = frozenset((errno.ENOENT, errno.ECONNREFUSED))


def definite_unsent(error):
    """ENOENT or ECONNREFUSED before the payload write. Other OS errors stay unknown."""
    return isinstance(error, OSError) and error.errno in _UNSENT_ERRNO


def binding(session_id):
    if os.environ.get('CLAUDE_CODE_SESSION_ID') != session_id:
        raise ValueError('session_binding_denied')
    endpoint = os.environ.get('CLAUDE_CODE_MESSAGING_SOCKET', '')
    token = os.environ.get('CLAUDE_CODE_MESSAGING_TOKEN', '')
    if not token or len(token) > 4096: raise ValueError('claude_pipe_unavailable')
    if os.name == 'nt':
        # Local named pipes only: never remote UNC paths or ordinary files.
        prefix = '\\\\.\\pipe\\'
        name = endpoint[len(prefix):]
        # Windows' flat pipe namespace also accepts backslash-separated names.
        # Refuse empty components and Win32 normalization aliases, not just UNC.
        if (not endpoint.startswith(prefix) or not 1 <= len(name) <= 240
                or any(not re.fullmatch(r'[A-Za-z0-9_.-]{1,240}', part)
                       or part in ('.','..') or part.endswith('.')
                       for part in name.split('\\'))):
            raise ValueError('claude_pipe_unavailable')
    elif os.name == 'posix' and hasattr(socket,'AF_UNIX'):
        unix_endpoint(endpoint)
    else: raise ValueError('claude_pipe_unavailable')
    return endpoint


def lexical_unix_endpoint(endpoint):
    """Reject a path that must not be treated as a missing local socket."""
    path=Path(endpoint)
    maximum=103 if sys.platform=='darwin' else 107
    if (not path.is_absolute() or endpoint.startswith('//') or '..' in path.parts
            or any(ord(c)<32 or ord(c)==127 for c in endpoint)
            or len(os.fsencode(endpoint))>maximum):
        raise ValueError
    return path


def socket_absent(endpoint):
    """A lexically safe Unix path with no inode. Unsafe paths are not absence."""
    if os.name=='nt' or not hasattr(socket,'AF_UNIX'): return False
    try: lexical_unix_endpoint(endpoint)
    except (OSError,ValueError,TypeError): return False
    try: os.lstat(endpoint)
    except FileNotFoundError: return True
    except OSError: return False
    return False


def unix_endpoint(endpoint, *, mode_mask=0o077):
    """Validate an existing current-user socket before sending its local token."""
    try:
        path=lexical_unix_endpoint(endpoint)
        uid=os.geteuid()
        info=path.lstat()
        if not stat.S_ISSOCK(info.st_mode) or info.st_uid!=uid or info.st_mode&mode_mask:
            raise ValueError
        immediate=path.parent.lstat()
        if not stat.S_ISDIR(immediate.st_mode) or immediate.st_uid!=uid or immediate.st_mode&0o077:
            raise ValueError
        # Shared temporary ancestors require the sticky bit. Reject symlink
        # routing, except macOS's root-owned /tmp and /var system aliases.
        aliases={'/tmp':'/private/tmp','/var':'/private/var'}
        for parent in path.parents:
            info=parent.lstat()
            if stat.S_ISLNK(info.st_mode):
                if (sys.platform!='darwin' or info.st_uid!=0
                        or aliases.get(str(parent))!=os.path.realpath(parent)):
                    raise ValueError
                info=parent.stat()
            if (not stat.S_ISDIR(info.st_mode) or info.st_uid not in (0,uid)
                    or (info.st_mode&0o022 and not info.st_mode&stat.S_ISVTX)):
                raise ValueError
    except (OSError,ValueError,TypeError):
        raise ValueError('claude_pipe_unavailable') from None


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
                                text=True,encoding='utf-8',capture_output=True,timeout=10,
                                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
    except (OSError,subprocess.TimeoutExpired):
        # Spawn and timeout are not socket ENOENT. The helper may already have written.
        raise ValueError('session_delivery_unknown') from None
    if result.returncode==_EXIT_UNSENT and not result.stdout and not result.stderr:
        raise DeliveryUnsent()
    if result.returncode: raise ValueError('session_delivery_unknown')


def write_payload(endpoint, payload):
    """Send payload. Absence is classified only before the first payload byte."""
    if os.name=='nt':
        _write_named_pipe(endpoint, payload)
    elif os.name=='posix' and hasattr(socket,'AF_UNIX'):
        _write_unix(endpoint, payload)
    else: raise ValueError('claude_pipe_unavailable')


def _write_unix(endpoint, payload):
    connection=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM)
    try:
        connection.settimeout(5)
        try: connection.connect(endpoint)
        except OSError as error:
            if definite_unsent(error): raise DeliveryUnsent() from None
            raise ValueError('session_delivery_unknown') from None
        try: connection.sendall(payload)
        except Exception: raise ValueError('session_delivery_unknown') from None
    finally: connection.close()


def _write_named_pipe(endpoint, payload):
    try: pipe=open(endpoint,'r+b',buffering=0)
    except OSError as error:
        if definite_unsent(error): raise DeliveryUnsent() from None
        raise ValueError('session_delivery_unknown') from None
    try:
        view=memoryview(payload)
        while view:
            try: written=pipe.write(view)
            except Exception: raise ValueError('session_delivery_unknown') from None
            if not isinstance(written,int) or isinstance(written,bool) or not 0<written<=len(view):
                raise ValueError('session_delivery_unknown')
            view=view[written:]
    finally: pipe.close()


def main():
    try:
        raw = sys.stdin.buffer.read(131073)
        if len(raw)>131072: raise ValueError('invalid_message')
        data = json.loads(raw)
        session_id=data['session_id']
        # Wrong session or missing token stays a binding failure, even if the path is gone.
        if (os.environ.get('CLAUDE_CODE_SESSION_ID')==session_id
                and os.environ.get('CLAUDE_CODE_MESSAGING_TOKEN')
                and socket_absent(os.environ.get('CLAUDE_CODE_MESSAGING_SOCKET',''))):
            raise DeliveryUnsent()
        endpoint = binding(session_id)
        payload = frames(session_id,data['message_id'],data['content'],
                         os.environ['CLAUDE_CODE_MESSAGING_TOKEN'])
        write_payload(endpoint, payload)
        return 0
    except DeliveryUnsent:
        return _EXIT_UNSENT
    except Exception:
        # No raw exception, input, pipe name or local authentication in output.
        return 1


if __name__=='__main__': raise SystemExit(main())
