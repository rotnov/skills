"""Host primitives for native Windows and POSIX; no shell or credential service."""
import os
from pathlib import Path

FILE_ALL_ACCESS = 0x1F01FF


class AnchoredPath(type(Path())):
    """A verified directory whose direct children are reached through its held descriptor.

    Path-based helpers (locks, logs, existence checks) then cannot be redirected by a later rename
    or link of an ancestor. Deeper paths and other operations keep ordinary path semantics.
    """
    def __init__(self, *segments, held=None):
        super().__init__(*segments)
        self.held = held  # (verified directory descriptor, its path string)

    def with_segments(self, *segments):
        return type(self)(*segments, held=self.held)

    def anchored_child(self):
        """(name, directory descriptor) for a direct child of the anchored directory, else None.

        Windows has no dir_fd; there the holder keeps the verified chain pinned against rename and
        deletion (`quorum_winproof`), so ordinary pathname use names the verified directory.
        """
        if os.name == 'nt' or self.held is None or self.name in ('', '.', '..'): return None
        directory, base = self.held
        return (self.name, directory) if str(self.parent) == base else None

    def stat(self, *, follow_symlinks=True):
        child = self.anchored_child()
        if child is None: return super().stat(follow_symlinks=follow_symlinks)
        return os.stat(child[0], dir_fd=child[1], follow_symlinks=follow_symlinks)


def reject_link(path):
    path = path if isinstance(path, Path) else Path(path)
    try: info = path.lstat()
    except FileNotFoundError: return
    if path.is_symlink() or getattr(info, 'st_file_attributes', 0) & 0x400:
        raise ValueError('unsafe_link')


def protect(path, directory=False):
    """Set private permissions. On Windows persist only the owner and DACL."""
    path = Path(path)
    reject_link(path)
    if os.name != 'nt':
        os.chmod(path, 0o700 if directory else 0o600)
        return
    try:
        _protect_windows(path, directory)
    except ValueError as error:
        if str(error) == 'unsafe_link': raise ValueError('unsafe_link') from None
        raise ValueError('private_acl_unavailable') from None
    except Exception:
        raise ValueError('private_acl_unavailable') from None


def _protect_windows(path, directory):
    """Owner and a protected DACL granting only the current user, set in-process.

    advapi32 replaces a PowerShell launch per call: no child process, console or
    deadline. The SACL is never written, so SeSecurityPrivilege is not needed.
    """
    import ctypes
    from ctypes import wintypes as w
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    security = ctypes.WinDLL('advapi32', use_last_error=True)
    pointer = w.LPVOID
    kernel.GetCurrentProcess.restype = w.HANDLE
    kernel.CloseHandle.argtypes = [w.HANDLE]
    kernel.CreateFileW.argtypes = [w.LPCWSTR, w.DWORD, w.DWORD, pointer, w.DWORD, w.DWORD, w.HANDLE]
    kernel.CreateFileW.restype = w.HANDLE
    kernel.GetFileInformationByHandleEx.argtypes = [w.HANDLE, ctypes.c_int, pointer, w.DWORD]
    security.OpenProcessToken.argtypes = [w.HANDLE, w.DWORD, ctypes.POINTER(w.HANDLE)]
    security.GetTokenInformation.argtypes = [w.HANDLE, ctypes.c_int, pointer, w.DWORD, ctypes.POINTER(w.DWORD)]
    security.GetLengthSid.argtypes, security.GetLengthSid.restype = [pointer], w.DWORD
    security.InitializeAcl.argtypes = [pointer, w.DWORD, w.DWORD]
    security.AddAccessAllowedAceEx.argtypes = [pointer, w.DWORD, w.DWORD, w.DWORD, pointer]
    security.SetSecurityInfo.argtypes = [w.HANDLE, ctypes.c_int, w.DWORD, pointer, pointer, pointer, pointer]
    security.SetSecurityInfo.restype = w.DWORD

    token, needed = w.HANDLE(), w.DWORD()
    if not security.OpenProcessToken(kernel.GetCurrentProcess(), 8, ctypes.byref(token)):  # TOKEN_QUERY
        raise OSError('token_unavailable')
    try:
        security.GetTokenInformation(token, 1, None, 0, ctypes.byref(needed))  # TokenUser
        if not needed.value: raise OSError('token_unavailable')
        user = ctypes.create_string_buffer(needed.value)
        if not security.GetTokenInformation(token, 1, user, needed, ctypes.byref(needed)):
            raise OSError('token_unavailable')
    finally:
        kernel.CloseHandle(token)
    sid = ctypes.cast(user, ctypes.POINTER(pointer)).contents  # TOKEN_USER.User.Sid
    length = security.GetLengthSid(sid)
    if not length: raise OSError('token_unavailable')
    size = (8 + 8 + length + 3) & ~3  # ACL header, one ACCESS_ALLOWED_ACE, DWORD aligned.
    acl = ctypes.create_string_buffer(size)
    inheritance = 3 if directory else 0  # OBJECT_INHERIT_ACE | CONTAINER_INHERIT_ACE
    if (not security.InitializeAcl(acl, size, 2)
            or not security.AddAccessAllowedAceEx(acl, 2, inheritance, FILE_ALL_ACCESS, sid)):
        raise OSError('acl_unavailable')
    # READ_CONTROL | WRITE_DAC | WRITE_OWNER on the link itself; directories need backup semantics.
    handle = kernel.CreateFileW(str(path), 0x000E0000, 7, None, 3, 0x02200000, None)
    if handle is None or handle == ctypes.c_void_p(-1).value: raise OSError('private_file_unavailable')
    try:
        class Attributes(ctypes.Structure):
            _fields_ = [('attributes', w.DWORD), ('tag', w.DWORD)]
        attrs = Attributes()
        if not kernel.GetFileInformationByHandleEx(handle, 9, ctypes.byref(attrs), ctypes.sizeof(attrs)):
            raise OSError('private_file_unavailable')
        if attrs.attributes & 0x400: raise ValueError('unsafe_link')
        # OWNER | DACL | PROTECTED_DACL: drop inherited rules, never touch the SACL.
        if security.SetSecurityInfo(handle, 1, 0x80000005, sid, None, acl, None):
            raise OSError('acl_unavailable')
    finally:
        kernel.CloseHandle(handle)


def background_flags(extra=0):
    """Popen options for a console-free child in its own process group.

    CREATE_NO_WINDOW gives the child a hidden console that its descendants
    inherit, including the interpreter a venv/uv Scripts/python.exe launcher
    starts. DETACHED_PROCESS would leave that launcher without a console, so
    its child would allocate a visible window; never combine the two.
    """
    if os.name != 'nt': return {'start_new_session': True}
    import subprocess
    return {'creationflags': subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP | extra}


def open_private(path, flags, mode=0o600):
    """Open the link itself on Windows, then reject reparse points before use."""
    reject_link(path)
    if os.name != 'nt':
        child = path.anchored_child() if isinstance(path, AnchoredPath) else None
        if child is not None: return os.open(child[0], flags | os.O_NOFOLLOW, mode, dir_fd=child[1])
        return os.open(path, flags | os.O_NOFOLLOW, mode)
    import ctypes
    import msvcrt
    from ctypes import wintypes
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                  wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.GetFileInformationByHandleEx.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD]
    access = 0x80000000
    if flags & os.O_RDWR: access |= 0x40000000
    elif flags & os.O_WRONLY: access = 0x40000000
    creation = 1 if flags & os.O_EXCL else (4 if flags & os.O_CREAT else 3)
    handle = kernel.CreateFileW(str(Path(path)), access, 7, None, creation, 0x00200080, None)
    if handle == ctypes.c_void_p(-1).value:
        code = ctypes.get_last_error()
        if code in (2, 3): raise FileNotFoundError(str(path))
        if code in (80, 183): raise FileExistsError(str(path))
        raise OSError('private_file_unavailable')
    class Attributes(ctypes.Structure):
        _fields_ = [('attributes', wintypes.DWORD), ('tag', wintypes.DWORD)]
    attrs = Attributes()
    if not kernel.GetFileInformationByHandleEx(handle, 9, ctypes.byref(attrs), ctypes.sizeof(attrs)) or attrs.attributes & 0x400:
        kernel.CloseHandle(handle)
        raise ValueError('unsafe_link')
    try: fd = msvcrt.open_osfhandle(handle, (flags & (os.O_RDWR | os.O_WRONLY)) | os.O_BINARY)
    except Exception:
        kernel.CloseHandle(handle)
        raise
    try:
        if flags & os.O_TRUNC: os.ftruncate(fd, 0)
        return fd
    except Exception:
        os.close(fd)
        raise


class FileLock:
    def __init__(self, path): self.path = path if isinstance(path, Path) else Path(path)
    def __enter__(self):
        self.fd = open_private(self.path, os.O_CREAT | os.O_RDWR)
        try:
            if os.name == 'nt':
                import msvcrt
                if os.fstat(self.fd).st_size == 0: os.write(self.fd, b'0')
                os.lseek(self.fd, 0, os.SEEK_SET)
                msvcrt.locking(self.fd, msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            os.close(self.fd)
            raise ValueError('worker_already_running') from None
        return self
    def __exit__(self, *_):
        os.close(self.fd)


class WindowsJob:
    """Own only this handler's process tree; closing the job terminates its children."""
    def __init__(self, process):
        import ctypes
        from ctypes import wintypes as w
        class Basic(ctypes.Structure):
            _fields_ = [('process_time', ctypes.c_longlong), ('job_time', ctypes.c_longlong),
                        ('flags', w.DWORD), ('min_ws', ctypes.c_size_t), ('max_ws', ctypes.c_size_t),
                        ('active', w.DWORD), ('affinity', ctypes.c_size_t),
                        ('priority', w.DWORD), ('scheduling', w.DWORD)]
        class Counters(ctypes.Structure):
            _fields_ = [('values', ctypes.c_ulonglong * 6)]
        class Limits(ctypes.Structure):
            _fields_ = [('basic', Basic), ('io', Counters), ('process_memory', ctypes.c_size_t),
                        ('job_memory', ctypes.c_size_t), ('peak_process', ctypes.c_size_t),
                        ('peak_job', ctypes.c_size_t)]
        self.kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        self.kernel.CreateJobObjectW.argtypes = [w.LPVOID, w.LPCWSTR]
        self.kernel.CreateJobObjectW.restype = w.HANDLE
        self.kernel.SetInformationJobObject.argtypes = [w.HANDLE, ctypes.c_int, w.LPVOID, w.DWORD]
        self.kernel.AssignProcessToJobObject.argtypes = [w.HANDLE, w.HANDLE]
        self.kernel.CloseHandle.argtypes = [w.HANDLE]
        self.handle = self.kernel.CreateJobObjectW(None, None)
        if not self.handle: raise ValueError('process_isolation_unavailable')
        limits = Limits()
        limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if (not self.kernel.SetInformationJobObject(self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits))
                or not self.kernel.AssignProcessToJobObject(self.handle, int(process._handle))):
            self.close()
            raise ValueError('process_isolation_unavailable')
    def resume(self, process):
        import ctypes
        from ctypes import wintypes
        native = ctypes.WinDLL('ntdll')
        native.NtResumeProcess.argtypes = [wintypes.HANDLE]
        native.NtResumeProcess.restype = ctypes.c_long
        if native.NtResumeProcess(int(process._handle)) != 0:
            raise ValueError('process_isolation_unavailable')

    def close(self):
        if self.handle:
            self.kernel.CloseHandle(self.handle)
            self.handle = None
