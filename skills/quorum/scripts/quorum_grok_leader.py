"""Experimental existing Grok TUI IPC. Never spawn/resume a provider agent.

Protocol1: four-byte big-endian JSON, Register then session-scoped ACP. Verified
with installed Grok1.0.67 and xai-org/grok-build. Queue admission is not UI receipt.
"""
import json
import ctypes
import shutil
import os
from pathlib import Path
import socket
import stat
import struct
import subprocess
import sys
import time
import uuid


class GrokBindingError(ValueError):
    """Only fixed local diagnostics may cross the JSON CLI boundary."""
    def __init__(self, reason):
        super().__init__('grok_transport_unavailable')
        self.reason = reason

    def diagnostic(self):
        if self.reason == 'leader_socket_missing':
            return {'reason': 'leader_socket_missing', 'next_step':
                    'No leader socket is available for this chat. An already-running standalone '
                    'Grok TUI cannot attach in place. Keep the chat open; its owner may later exit '
                    'it and relaunch the same session with --resume as described in references/grok.md.'}
        return {}


def process_identity(pid):
    """macOS proc_info.h ABI; do not inspect process environment or credentials."""
    if sys.platform != 'darwin': raise OSError('unsupported_platform')
    class BsdInfo(ctypes.Structure):
        _fields_ = [(name, ctypes.c_uint32) for name in (
            'flags','status','xstatus','pid','ppid','uid','gid','ruid','rgid',
            'svuid','svgid','reserved')] + [('comm', ctypes.c_char*16),
            ('name', ctypes.c_char*32)] + [(name, ctypes.c_uint32) for name in (
            'nfiles','pgid','jobc','tdev','tpgid')] + [('nice', ctypes.c_int32),
            ('seconds', ctypes.c_uint64), ('micros', ctypes.c_uint64)]
    lib = ctypes.CDLL('/usr/lib/libproc.dylib', use_errno=True)
    data = BsdInfo()
    if lib.proc_pidinfo(pid, 3, 0, ctypes.byref(data), ctypes.sizeof(data)) != ctypes.sizeof(data):
        raise OSError('process_unavailable')
    path = ctypes.create_string_buffer(4096)
    if lib.proc_pidpath(pid, path, len(path)) <= 0: raise OSError('process_unavailable')
    # ps exposes argv only. sysctl KERN_PROCARGS2 also contains environment;
    # deliberately do not read it. Supported explicit flags precede the prompt.
    result = subprocess.run(['/bin/ps', '-ww', '-p', str(pid), '-o', 'command='],
                            capture_output=True, text=True, timeout=3, check=True)
    if len(result.stdout) > 65536: raise OSError('process_unavailable')
    return {'pid_start': data.seconds*1000000+data.micros, 'uid':data.uid,
            'tty':bool(data.flags&0x40) and data.tdev != 0xffffffff,
            'executable':str(Path(os.fsdecode(path.value)).resolve()),
            'argv':result.stdout.strip().split()}


def verify_process(pid, start, endpoint, sid, cwd):
    try:
        current = process_identity(pid)
        installed = shutil.which('grok')
        if (not installed or not isinstance(start, int) or isinstance(start, bool)
                or current['pid_start'] != start or current['uid'] != os.geteuid()
                or not current['tty'] or current['executable'] != str(Path(installed).resolve())):
            raise ValueError
        args = iter(current['argv'][1:])
        flags = {}
        for arg in args:
            if not arg.startswith('-') or arg in flags: raise ValueError  # No prompt, subcommand or repeat.
            if arg == '--leader': flags[arg] = True
            elif arg in ('--leader-socket','--session-id','--resume','--cwd'): flags[arg] = next(args)
            else: raise ValueError  # Headless/agent/permission/fork flags are outside this route.
        # A new conversation (--session-id) or the owner's relaunch of this exact
        # one (--resume): never both, and never a title or another session.
        session = [flags[name] for name in ('--session-id','--resume') if name in flags]
        if (flags.get('--leader') is not True or session != [sid]
                or flags.get('--leader-socket') != endpoint
                or ('--cwd' in flags and str(Path(flags['--cwd']).resolve()) != cwd)):
            raise ValueError
    except (OSError, ValueError, KeyError, StopIteration, subprocess.SubprocessError):
        raise ValueError('session_binding_denied') from None


def local_binding(session_id, cwd):
    if os.environ.get('GROK_SESSION_ID') != session_id:
        raise ValueError('session_binding_denied')
    if sys.platform != 'darwin' or not hasattr(socket, 'AF_UNIX'):
        raise ValueError('grok_transport_unavailable')
    home = Path(os.environ.get('GROK_HOME') or Path.home()/'.grok')
    # --leader-socket exports GROK_LEADER_SOCKET and the process guard requires
    # that flag, so without the export this chat has no usable leader socket.
    # A stale or foreign default ~/.grok/leader.sock must not be probed.
    endpoint = os.environ.get('GROK_LEADER_SOCKET')
    if not endpoint: raise GrokBindingError('leader_socket_missing')
    from quorum_claude_pipe import unix_endpoint
    try:
        Path(endpoint).lstat()
    except FileNotFoundError:
        raise GrokBindingError('leader_socket_missing') from None
    except OSError:
        raise ValueError('grok_transport_unavailable') from None
    try:
        # Grok creates mode0755 sockets; require owner-only write and a private
        # parent. Claude retains its stricter default0600 requirement.
        unix_endpoint(endpoint, mode_mask=0o022)
        path = home/'active_sessions.json'
        info = path.lstat()
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
                or info.st_mode&0o022 or info.st_size > 1048576):
            raise ValueError
        entries = json.loads(path.read_text())
    except (OSError, ValueError, TypeError):
        raise ValueError('grok_transport_unavailable') from None
    if not isinstance(entries, list): raise ValueError('session_binding_denied')
    own = [entry for entry in entries if isinstance(entry, dict)
           and entry.get('session_id') == session_id]
    if len(own) != 1: raise ValueError('session_binding_denied')
    entry = own[0]
    pid = entry.get('pid')
    if (not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0
            or str(Path(entry.get('cwd', '')).resolve()) != str(Path(cwd).resolve())):
        raise ValueError('session_binding_denied')
    verify_process(pid, entry.get('pid_start'), endpoint, session_id, str(Path(cwd).resolve()))
    return {'leader_socket': endpoint, 'tui_pid': pid, 'tui_start': entry.get('pid_start'),
            'cwd': str(Path(cwd).resolve())}


class Leader:
    def __init__(self, endpoint):
        self.socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.socket.settimeout(3)
        self.ref = 0
        try:
            self.socket.connect(endpoint)
            self.send({'type':'register', 'client_type':'quorum', 'mode':'stdio', 'capabilities':{}})
            registered = self.receive(time.monotonic()+5)
            if (registered.get('type') != 'registered'
                    or registered.get('leader_protocol_version') != 1
                    or registered.get('leader_binary_version') != '1.0.67'
                    or not isinstance(registered.get('ready'), bool)):
                raise ValueError('grok_transport_unavailable')
            if not registered.get('ready', True):
                if self.receive(time.monotonic()+5).get('type') != 'leader_ready':
                    raise ValueError('grok_transport_unavailable')
        except Exception:
            self.socket.close()
            raise

    def send(self, frame):
        raw = json.dumps(frame, ensure_ascii=False).encode()
        if len(raw) > 262144: raise ValueError('invalid_message')
        self.socket.sendall(struct.pack('>I', len(raw))+raw)

    def receive(self, deadline):
        def exact(length):
            raw = b''
            while len(raw) < length:
                remaining = deadline-time.monotonic()
                if remaining <= 0: raise TimeoutError
                self.socket.settimeout(min(remaining, 3))
                chunk = self.socket.recv(length-len(raw))
                if not chunk: raise EOFError
                raw += chunk
            return raw
        length = struct.unpack('>I', exact(4))[0]
        if length > 262144: raise ValueError('grok_transport_unavailable')
        frame = json.loads(exact(length))
        if not isinstance(frame, dict): raise ValueError('grok_transport_unavailable')
        return frame

    def acp(self, method, params):
        self.ref += 1
        request = {'jsonrpc':'2.0', 'id':self.ref, 'method':method, 'params':params}
        self.send({'type':'acp', 'payload':json.dumps(request, ensure_ascii=False)})
        return self.ref

    def metadata(self, method, params):
        ref = self.acp(method, params)
        deadline = time.monotonic()+5
        while True:
            frame = self.receive(deadline)
            if frame.get('type') != 'acp': continue
            result = json.loads(frame['payload'])
            if result.get('id') == ref:
                return result.get('result', {}).get('result', {})

    def verify(self, session_id, cwd):
        info = self.metadata('_x.ai/session/info', {'sessionId':session_id})
        roster = self.metadata('_x.ai/sessions/list', {})
        def own_cwd(value):
            return (isinstance(value, str) and Path(value).is_absolute()
                    and str(Path(value).resolve()) == cwd)
        if (info.get('sessionId') != session_id or not own_cwd(info.get('cwd'))
                or not any(row.get('sessionId') == session_id and own_cwd(row.get('cwd'))
                           and row.get('resident') is True for row in roster.get('sessions', []))):
            raise ValueError('session_binding_denied')

    def prompt(self, session_id, message_id, content):
        prompt_id = 'quorum:'+message_id
        self.acp('session/prompt', {'sessionId':session_id,
            'prompt':[{'type':'text', 'text':content}], '_meta':{'promptId':prompt_id}})
        deadline = time.monotonic()+5
        while True:
            frame = self.receive(deadline)
            if frame.get('type') != 'acp': continue
            event = json.loads(frame['payload'])
            params = event.get('params', {})
            # Gateway notifications may wrap the actual method/params once.
            if isinstance(params.get('params'), dict): params = params['params']
            if (str(event.get('method', '')).lstrip('_') == 'x.ai/queue/changed'
                    and params.get('sessionId') == session_id
                    and (params.get('runningPromptId') == prompt_id
                         or any(row.get('id') == prompt_id for row in params.get('entries', [])))):
                return  # Submitted only; the actual chat must confirm receipt.

    def close(self):
        try: self.send({'type':'disconnect'})
        except Exception: pass
        self.socket.close()


def binding(session_id, cwd):
    result = local_binding(session_id, cwd)
    try:
        leader = Leader(result['leader_socket'])
        try: leader.verify(session_id, result['cwd'])
        finally: leader.close()
    except ValueError: raise
    except Exception: raise ValueError('grok_transport_unavailable') from None
    return result


def submit(record, message_id, content):
    message_id = str(uuid.UUID(message_id))
    if not isinstance(content, str) or not content or len(content.encode()) > 48000:
        raise ValueError('invalid_message')
    payload = json.dumps({'record':record, 'message_id':message_id, 'content':content}, ensure_ascii=False)
    try:
        result = subprocess.run([sys.executable, str(Path(__file__).resolve())], input=payload,
            text=True, encoding='utf-8', capture_output=True, timeout=20)
    except (OSError, subprocess.TimeoutExpired): raise ValueError('session_delivery_unknown') from None
    if result.returncode: raise ValueError('session_delivery_unknown')


def main():
    try:
        raw = sys.stdin.buffer.read(131073)
        if len(raw) > 131072: raise ValueError('invalid_message')
        data = json.loads(raw)
        record = data['record']
        sid = str(uuid.UUID(record['session_ref']))
        transport = local_binding(sid, record['cwd'])
        if transport != record['grok_transport']: raise ValueError('session_binding_denied')
        leader = Leader(transport['leader_socket'])
        try:
            leader.verify(sid, transport['cwd'])
            if local_binding(sid, record['cwd']) != transport: raise ValueError('session_binding_denied')
            leader.prompt(sid, str(uuid.UUID(data['message_id'])), data['content'])
        finally: leader.close()
        return 0
    except Exception:
        return 1  # Never print raw provider responses, environment or prompts.


if __name__ == '__main__': raise SystemExit(main())
