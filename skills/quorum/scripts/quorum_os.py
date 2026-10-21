"""Host primitives for native Windows and POSIX; no shell or credential service."""
import os
from pathlib import Path


def reject_link(path):
    path = Path(path)
    try: info = path.lstat()
    except FileNotFoundError: return
    if path.is_symlink() or getattr(info, 'st_file_attributes', 0) & 0x400:
        raise ValueError('unsafe_link')


def protect(path, directory=False):
    """Set private permissions. On Windows replace the DACL atomically."""
    path = Path(path)
    reject_link(path)
    if os.name != 'nt':
        os.chmod(path, 0o700 if directory else 0o600)
        return
    import base64
    import subprocess
    # The path is data in an environment variable, never interpolated into code.
    script = r'''
$ErrorActionPreference = 'Stop'
$p = $env:QUORUM_ACL_PATH
$sid = [System.Security.Principal.WindowsIdentity]::GetCurrent().User
if ($env:QUORUM_ACL_DIRECTORY -eq '1') {
  $acl = New-Object System.Security.AccessControl.DirectorySecurity
  $rule = New-Object System.Security.AccessControl.FileSystemAccessRule($sid, 'FullControl', 'ContainerInherit,ObjectInherit', 'None', 'Allow')
} else {
  $acl = New-Object System.Security.AccessControl.FileSecurity
  $rule = New-Object System.Security.AccessControl.FileSystemAccessRule($sid, 'FullControl', 'Allow')
}
$acl.SetOwner($sid)
$acl.SetAccessRuleProtection($true, $false)
$acl.AddAccessRule($rule)
Set-Acl -LiteralPath $p -AclObject $acl
'''
    env = dict(os.environ, QUORUM_ACL_PATH=str(path.resolve()), QUORUM_ACL_DIRECTORY='1' if directory else '0')
    system = Path(os.environ['SystemRoot']) / 'System32/WindowsPowerShell/v1.0/powershell.exe'
    encoded = base64.b64encode(script.encode('utf-16-le')).decode('ascii')
    result = subprocess.run([str(system), '-NoProfile', '-NonInteractive', '-EncodedCommand', encoded],
                            env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15)
    if result.returncode != 0: raise ValueError('private_acl_unavailable')


def open_private(path, flags, mode=0o600):
    """Open the link itself on Windows, then reject reparse points before use."""
    reject_link(path)
    if os.name != 'nt':
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
    def __init__(self, path): self.path = Path(path)
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
