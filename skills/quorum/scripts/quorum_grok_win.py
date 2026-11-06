"""Windows existing Grok TUI leader: process identity and named-pipe client. Experimental.

Grok Build 1.0.70 and 1.0.77 on Windows serve the leader on \\\\.\\pipe\\grok-leader-<16 hex>. The name
is an undocumented hash of the --leader-socket path, so the pipe is discovered by its server:
the resident `grok.exe agent leader` child of the bound TUI, owned by the current user.
Every check reads OS process metadata (birth time, owner SID, image, parent, command line);
the process environment and provider credentials are never read.
"""
import os
import re
import time

PIPES = '\\\\.\\pipe\\'
PIPE_NAME = re.compile(r'grok-leader-[0-9a-f]{16}')
QUERY_LIMITED, STILL_ACTIVE, TOKEN_QUERY = 0x1000, 259, 0x8
GENERIC_RW, OPEN_EXISTING = 0x80000000 | 0x40000000, 3
# Identification-level client: the leader may learn who we are but can never act as us.
SQOS_IDENTIFICATION = 0x00100000 | 0x00010000
BROKEN = (109, 232, 233)  # ERROR_BROKEN_PIPE, ERROR_NO_DATA, ERROR_PIPE_NOT_CONNECTED
PIPE_BUSY, PIPE_NOWAIT = 231, 1
EPOCH = 116444736000000000  # FILETIME of 1970-01-01, in 100 ns units


def _api():
    import ctypes
    from ctypes import wintypes as w
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    advapi = ctypes.WinDLL('advapi32', use_last_error=True)
    shell = ctypes.WinDLL('shell32', use_last_error=True)
    ntdll = ctypes.WinDLL('ntdll')
    kernel.OpenProcess.restype = w.HANDLE
    kernel.OpenProcess.argtypes = (w.DWORD, w.BOOL, w.DWORD)
    kernel.GetCurrentProcess.restype = w.HANDLE
    kernel.CreateFileW.restype = w.HANDLE
    kernel.CreateFileW.argtypes = (w.LPCWSTR, w.DWORD, w.DWORD, ctypes.c_void_p, w.DWORD, w.DWORD, w.HANDLE)
    kernel.CloseHandle.argtypes = (w.HANDLE,)
    kernel.LocalFree.argtypes = (ctypes.c_void_p,)
    kernel.GetExitCodeProcess.argtypes = (w.HANDLE, ctypes.POINTER(w.DWORD))
    kernel.GetProcessTimes.argtypes = (w.HANDLE,) + (ctypes.POINTER(w.FILETIME),) * 4
    kernel.QueryFullProcessImageNameW.argtypes = (w.HANDLE, w.DWORD, w.LPWSTR, ctypes.POINTER(w.DWORD))
    kernel.GetNamedPipeServerProcessId.argtypes = (w.HANDLE, ctypes.POINTER(w.ULONG))
    kernel.WriteFile.argtypes = (w.HANDLE, ctypes.c_char_p, w.DWORD, ctypes.POINTER(w.DWORD), ctypes.c_void_p)
    kernel.ReadFile.argtypes = (w.HANDLE, ctypes.c_void_p, w.DWORD, ctypes.POINTER(w.DWORD), ctypes.c_void_p)
    kernel.WaitNamedPipeW.argtypes = (w.LPCWSTR, w.DWORD)
    kernel.SetNamedPipeHandleState.argtypes = (w.HANDLE, ctypes.POINTER(w.DWORD), ctypes.c_void_p, ctypes.c_void_p)
    kernel.PeekNamedPipe.argtypes = (w.HANDLE, ctypes.c_void_p, w.DWORD, ctypes.c_void_p,
                                     ctypes.POINTER(w.DWORD), ctypes.c_void_p)
    advapi.OpenProcessToken.argtypes = (w.HANDLE, w.DWORD, ctypes.POINTER(w.HANDLE))
    advapi.GetTokenInformation.argtypes = (w.HANDLE, ctypes.c_int, ctypes.c_void_p, w.DWORD,
                                           ctypes.POINTER(w.DWORD))
    advapi.ConvertSidToStringSidW.argtypes = (ctypes.c_void_p, ctypes.POINTER(w.LPWSTR))
    shell.CommandLineToArgvW.restype = ctypes.POINTER(w.LPWSTR)
    shell.CommandLineToArgvW.argtypes = (w.LPCWSTR, ctypes.POINTER(ctypes.c_int))
    ntdll.NtQueryInformationProcess.argtypes = (w.HANDLE, ctypes.c_ulong, ctypes.c_void_p,
                                                ctypes.c_ulong, ctypes.POINTER(ctypes.c_ulong))
    return ctypes, w, kernel, advapi, shell, ntdll


def _sid(api, process):
    ctypes, w, kernel, advapi = api[:4]
    token = w.HANDLE()
    if not advapi.OpenProcessToken(process, TOKEN_QUERY, ctypes.byref(token)): raise OSError('process_unavailable')
    try:
        needed = w.DWORD()
        advapi.GetTokenInformation(token, 1, None, 0, ctypes.byref(needed))  # TokenUser
        if not 0 < needed.value <= 4096: raise OSError('process_unavailable')
        info = ctypes.create_string_buffer(needed.value)
        if not advapi.GetTokenInformation(token, 1, info, needed, ctypes.byref(needed)):
            raise OSError('process_unavailable')
        text = w.LPWSTR()
        if not advapi.ConvertSidToStringSidW(ctypes.cast(info, ctypes.POINTER(ctypes.c_void_p))[0],
                                             ctypes.byref(text)):
            raise OSError('process_unavailable')
        try: return text.value
        finally: kernel.LocalFree(ctypes.cast(text, ctypes.c_void_p))
    finally: kernel.CloseHandle(token)


def own_sid():
    api = _api()
    return _sid(api, api[2].GetCurrentProcess())


def _command_line(api, process):
    ctypes, w, kernel, _, shell, ntdll = api
    size = ctypes.c_ulong()
    ntdll.NtQueryInformationProcess(process, 60, None, 0, ctypes.byref(size))  # ProcessCommandLineInformation
    if not 0 < size.value <= 131072: raise OSError('process_unavailable')
    buffer = ctypes.create_string_buffer(size.value)
    if ntdll.NtQueryInformationProcess(process, 60, buffer, size, ctypes.byref(size)) != 0:
        raise OSError('process_unavailable')
    class Unicode(ctypes.Structure):
        _fields_ = [('length', w.USHORT), ('maximum', w.USHORT), ('buffer', ctypes.c_void_p)]
    text = Unicode.from_buffer(buffer)
    line = ctypes.wstring_at(text.buffer, text.length // 2)
    count = ctypes.c_int()
    argv = shell.CommandLineToArgvW(line, ctypes.byref(count))
    if not argv: raise OSError('process_unavailable')
    try: return [argv[index] for index in range(count.value)]
    finally: kernel.LocalFree(ctypes.cast(argv, ctypes.c_void_p))


def process_identity(pid):
    """Live process birth (Unix microseconds), owner SID, image, parent PID and argv."""
    api = _api()
    ctypes, w, kernel, _, _, ntdll = api
    if not isinstance(pid, int) or isinstance(pid, bool) or not 0 < pid < 2**32:
        raise OSError('process_unavailable')
    process = kernel.OpenProcess(QUERY_LIMITED, False, pid)
    if not process: raise OSError('process_unavailable')
    try:
        code = w.DWORD()
        if not kernel.GetExitCodeProcess(process, ctypes.byref(code)) or code.value != STILL_ACTIVE:
            raise OSError('process_unavailable')  # An exited process can keep its PID while handles remain.
        times = [w.FILETIME() for _ in range(4)]
        if not kernel.GetProcessTimes(process, *[ctypes.byref(value) for value in times]):
            raise OSError('process_unavailable')
        born = (times[0].dwHighDateTime << 32) | times[0].dwLowDateTime
        image = ctypes.create_unicode_buffer(32768)
        length = w.DWORD(len(image))
        if not kernel.QueryFullProcessImageNameW(process, 0, image, ctypes.byref(length)):
            raise OSError('process_unavailable')
        class Basic(ctypes.Structure):
            _fields_ = [('status', ctypes.c_void_p), ('peb', ctypes.c_void_p), ('affinity', ctypes.c_void_p),
                        ('priority', ctypes.c_void_p), ('pid', ctypes.c_void_p), ('parent', ctypes.c_void_p)]
        basic = Basic()
        if ntdll.NtQueryInformationProcess(process, 0, ctypes.byref(basic), ctypes.sizeof(basic), None) != 0:
            raise OSError('process_unavailable')
        return {'pid_start': (born - EPOCH) // 10, 'uid': _sid(api, process), 'ppid': basic.parent or 0,
                'executable': os.path.normcase(os.path.realpath(image.value)),
                'argv': _command_line(api, process)}
    finally: kernel.CloseHandle(process)


class Busy(OSError):
    """Every instance of the pipe is serving another client."""


def _open(name):
    api = _api()
    ctypes, w, kernel = api[:3]
    for attempt in (0, 1):
        handle = kernel.CreateFileW(PIPES + name, GENERIC_RW, 0, None, OPEN_EXISTING, SQOS_IDENTIFICATION, None)
        if handle not in (None, w.HANDLE(-1).value): return api, handle
        if ctypes.get_last_error() != PIPE_BUSY: raise OSError('pipe_unavailable')
        if not attempt: kernel.WaitNamedPipeW(PIPES + name, 500)
    raise Busy('pipe_busy')


def _server(api, handle):
    """The PID serving this already opened handle, never a name looked up before opening."""
    ctypes, w, kernel = api[:3]
    pid = w.ULONG()
    if not kernel.GetNamedPipeServerProcessId(handle, ctypes.byref(pid)) or not pid.value:
        raise OSError('pipe_unavailable')
    return pid.value


def is_leader(identity, tui, executable):
    """The resident leader Grok spawns for this TUI: same user and image, born after it."""
    return (identity['uid'] == tui['uid'] and identity['executable'] == executable
            and identity['ppid'] == tui['pid'] and identity['pid_start'] >= tui['pid_start']
            and identity['argv'][1:3] == ['agent', 'leader'])


def discover(tui, executable):
    """(pipe name, leader pid, leader birth) for exactly one leader pipe of this TUI, else None.

    Every grok-leader-* pipe is opened briefly at identification level to learn its server;
    nothing is sent, and all are checked so that a second candidate is refused, not ignored.
    """
    try: names = [name for name in os.listdir(PIPES) if PIPE_NAME.fullmatch(name)]
    except OSError: return None
    found, busy = [], False
    for name in names:
        try:
            api, handle = _open(name)
            try: pid = _server(api, handle)
            finally: api[2].CloseHandle(handle)
            identity = process_identity(pid)
        except Busy:
            busy = True
            continue
        except OSError: continue
        if is_leader(identity, tui, executable): found.append((name, pid, identity['pid_start']))
    if len(found) > 1: raise ValueError('session_binding_denied')
    # A busy pipe may be this TUI's leader serving another client: unavailable, not missing.
    if busy and not found: raise ValueError('grok_transport_unavailable')
    return found[0] if found else None


class Pipe:
    """Leader client over a Win32 named pipe with the socket calls `Leader` uses."""
    def __init__(self, pid, start):
        self.pid, self.start, self.timeout, self.handle, self.api = pid, start, 3, None, None

    def settimeout(self, value): self.timeout = value

    def connect(self, name):
        if not PIPE_NAME.fullmatch(name): raise OSError('pipe_unavailable')
        self.api, self.handle = _open(name)
        # Re-check the server on this handle: a replaced leader or reused PID is refused.
        if _server(self.api, self.handle) != self.pid or process_identity(self.pid)['pid_start'] != self.start:
            raise OSError('pipe_unavailable')
        # Non-blocking writes, so a leader that stops reading cannot hang sendall.
        ctypes, w, kernel = self.api[:3]
        if not kernel.SetNamedPipeHandleState(self.handle, ctypes.byref(w.DWORD(PIPE_NOWAIT)), None, None):
            raise OSError('pipe_unavailable')

    def sendall(self, data):
        ctypes, w, kernel = self.api[:3]
        deadline = time.monotonic() + self.timeout
        while data:
            written = w.DWORD()
            if not kernel.WriteFile(self.handle, data, len(data), ctypes.byref(written), None):
                raise OSError('pipe_unavailable')
            data = data[written.value:]
            if data and not written.value:
                if time.monotonic() >= deadline: raise TimeoutError
                time.sleep(.01)

    def recv(self, count):
        ctypes, w, kernel = self.api[:3]
        deadline = time.monotonic() + self.timeout
        while True:
            available = w.DWORD()
            if not kernel.PeekNamedPipe(self.handle, None, 0, None, ctypes.byref(available), None):
                if ctypes.get_last_error() in BROKEN: return b''
                raise OSError('pipe_unavailable')
            if available.value: break
            if time.monotonic() >= deadline: raise TimeoutError
            time.sleep(.01)
        buffer = ctypes.create_string_buffer(min(count, available.value))
        read = w.DWORD()
        if not kernel.ReadFile(self.handle, buffer, len(buffer), ctypes.byref(read), None):
            if ctypes.get_last_error() in BROKEN: return b''
            raise OSError('pipe_unavailable')
        return buffer.raw[:read.value]

    def close(self):
        if self.handle is not None: self.api[2].CloseHandle(self.handle)
        self.handle = None
