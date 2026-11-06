"""Windows owner/DACL proof over handle-anchored opens, for the native worker inbox routes.

Every component is opened relative to its already verified parent handle (NtCreateFile with a
RootDirectory, the Windows form of openat), as the link itself, and a reparse point is refused.
The owner and DACL are read from the open handle. Nothing here changes the security of an existing
object: only a directory or file this code creates receives an owner-only descriptor at creation,
like POSIX mkdir 0700 or open 0600.

Verified handles carry list/read access without delete sharing, so while one is held nobody can
rename, replace or delete that component; pathname use under a held chain therefore names the
verified objects. Every refusal raises `Unsafe` with no path; callers map it to their fixed codes.
"""
import os
import re
import struct

SYSTEM, ADMINISTRATORS = 'S-1-5-18', 'S-1-5-32-544'
OWNER_RIGHTS = 'S-1-3-4'  # Grants its rights to the object's current owner, which must already be trusted.
TRUSTED_INSTALLER = 'S-1-5-80-956008885-3418522649-1831038044-1853292631-2271478464'

DELETE, READ_CONTROL, WRITE_DAC, WRITE_OWNER, SYNCHRONIZE = 0x10000, 0x20000, 0x40000, 0x80000, 0x100000
GENERIC_ALL, GENERIC_WRITE, GENERIC_READ = 0x10000000, 0x40000000, 0x80000000
FILE_READ_DATA, FILE_WRITE_DATA, FILE_APPEND_DATA, FILE_WRITE_EA = 0x1, 0x2, 0x4, 0x10
FILE_DELETE_CHILD, FILE_READ_ATTRIBUTES, FILE_WRITE_ATTRIBUTES = 0x40, 0x80, 0x100
FILE_ALL_ACCESS = 0x1F01FF
# Rights that remove, rename or re-permission an entry: the Windows form of a non-sticky shared
# directory. Adding new entries alone is like a sticky directory and stays acceptable on ancestors.
REPLACE = DELETE | WRITE_DAC | WRITE_OWNER | FILE_DELETE_CHILD | GENERIC_ALL
# Group/other-writable (POSIX 0o022) for the Codex rollout tree.
WRITE = REPLACE | FILE_WRITE_DATA | FILE_APPEND_DATA | FILE_WRITE_EA | FILE_WRITE_ATTRIBUTES | GENERIC_WRITE

# AppContainer package (S-1-15-2-) and capability (S-1-15-3-) SIDs match only AppContainer or LPAC
# tokens, never an ordinary one. For those tokens Windows grants the intersection of a user/group pass
# and a package/capability pass (a dual access check), so a rule naming one can never grant access the
# token's own user or groups do not already receive from other rules, which are checked anyway:
# same-user, like a sandboxed process of that user.
APP_CONTAINER = ('S-1-15-2-', 'S-1-15-3-')

ALLOWED, DENIED = 0, 1
INHERIT_ONLY, INHERITED = 0x08, 0x10
SE_DACL_PRESENT, SE_DACL_PROTECTED, SE_SELF_RELATIVE = 0x4, 0x1000, 0x8000
ATTRIBUTE_DIRECTORY, ATTRIBUTE_REPARSE = 0x10, 0x400

FILE_OPEN, FILE_CREATE, FILE_OPEN_IF = 1, 2, 3
DIRECTORY_FILE, SYNCHRONOUS, NON_DIRECTORY_FILE = 0x1, 0x20, 0x40
DELETE_ON_CLOSE, OPEN_REPARSE_POINT = 0x1000, 0x200000
SHARE_READ_WRITE, SHARE_ALL = 3, 7
NOT_FOUND = (0xC0000034, 0xC000003A)  # STATUS_OBJECT_NAME/PATH_NOT_FOUND
COLLISION = 0xC0000035  # STATUS_OBJECT_NAME_COLLISION

DRIVE_ROOT = re.compile(r'[A-Za-z]:\\')
RESERVED = re.compile(r'(con|prn|aux|nul|conin\$|conout\$|com[0-9¹²³]|lpt[0-9¹²³])(\..*)?',
                      re.IGNORECASE)


class Unsafe(Exception):
    """Fixed refusal: no path, name or native detail."""


# --- Pure descriptor logic --------------------------------------------------------------------

def sid_text(raw):
    if len(raw) < 8 or raw[0] != 1 or raw[1] > 15 or len(raw) != 8 + 4 * raw[1]: raise Unsafe()
    return 'S-' + '-'.join(map(str, [1, int.from_bytes(raw[2:8], 'big'), *struct.unpack_from('<' + 'I' * raw[1], raw, 8)]))


def _sid_at(data, offset, end):
    if offset + 8 > end: raise Unsafe()
    size = 8 + 4 * data[offset + 1]
    if offset + size > end: raise Unsafe()
    return sid_text(data[offset:offset + size])


def parse(descriptor):
    """(owner SID, DACL protected, [(type, flags, mask, SID)]) from a self-relative security descriptor.

    A missing or NULL DACL (which grants everyone), an unknown ACL revision or any ACE type other than
    plain allow or deny (object, callback, conditional) refuses.
    """
    data = bytes(descriptor)
    if len(data) < 20: raise Unsafe()
    revision, _, control, owner, _, _, dacl = struct.unpack_from('<BBHIIII', data)
    if revision != 1 or not control & SE_SELF_RELATIVE or not owner or not control & SE_DACL_PRESENT or not dacl:
        raise Unsafe()
    if dacl + 8 > len(data): raise Unsafe()
    acl_revision, _, size, count, _ = struct.unpack_from('<BBHHH', data, dacl)
    end = dacl + size
    if acl_revision not in (2, 4) or size < 8 or end > len(data): raise Unsafe()
    aces, offset = [], dacl + 8
    for _ in range(count):
        if offset + 8 > end: raise Unsafe()
        kind, flags, length, mask = struct.unpack_from('<BBHI', data, offset)
        if kind not in (ALLOWED, DENIED) or length < 16 or offset + length > end: raise Unsafe()
        aces.append((kind, flags, mask, _sid_at(data, offset + 8, offset + length)))
        offset += length
    return _sid_at(data, owner, len(data)), bool(control & SE_DACL_PROTECTED), aces


def evaluate(descriptor, kind, policy, user, owners):
    """Refuse unless the descriptor satisfies `kind` under `policy`; never changes anything.

    `user` is the caller's SID; `owners` adds the token's default owner (Administrators in an elevated
    session), so objects this same token creates qualify. Deny ACEs only remove access and are ignored.

    - 'ancestor': owned by the user, SYSTEM, Administrators or TrustedInstaller; no other principal may
      delete, rename or re-permission it (others may only add entries, like a sticky directory).
      AppContainer package/capability rules count as the user's own (see `APP_CONTAINER`).
    - Every kind: an OWNER RIGHTS rule grants only the owner, which each kind has already checked.
    - 'claude' 'private' directory: owned by the user; a protected DACL with no inherited ACE whose allow
      ACEs name only the user, SYSTEM or Administrators (POSIX 0700).
    - 'claude' 'file': owned by the user; allow ACEs name only the user, SYSTEM or Administrators (POSIX
      0600). Inherited ACEs come from the verified protected parent (SQLite journals, lock files).
    - 'codex' 'private'/'file': owned by the user; no other principal holds a write-class right (POSIX
      not group/other writable); read access by others stays acceptable, as on POSIX.
    - 'codex' 'dated' YYYY/MM/DD directory: as 'private', but SYSTEM or Administrators may also own it. An
      elevated Codex run leaves these owned by Administrators, who can already write the whole tree.
    """
    owner, protected, aces = parse(descriptor)
    administrators = {SYSTEM, ADMINISTRATORS}
    if kind == 'ancestor':
        trusted, limit = {user, *owners, *administrators, TRUSTED_INSTALLER}, REPLACE
        if owner not in trusted: raise Unsafe()
    else:
        trusted, limit = {user, *administrators}, WRITE
        relaxed = administrators if kind == 'dated' and policy == 'codex' else ()
        if owner not in {user, *owners, *relaxed}: raise Unsafe()
    # The owner was just required to be trusted, so OWNER RIGHTS reaches no one else. CPython's
    # os.mkdir(mode=0o700) on Windows writes exactly {SYSTEM, Administrators, OWNER RIGHTS}.
    trusted.add(OWNER_RIGHTS)
    strict = policy == 'claude' and kind != 'ancestor'
    for ace_type, flags, mask, sid in aces:
        if ace_type != ALLOWED or sid in trusted: continue
        if kind == 'ancestor' and sid.startswith(APP_CONTAINER): continue
        if strict: raise Unsafe()
        if not flags & INHERIT_ONLY and mask & limit: raise Unsafe()
    if strict and kind == 'private' and (not protected or any(ace[1] & INHERITED for ace in aces)): raise Unsafe()


def private_descriptor(sid, directory):
    """Self-relative descriptor: owner `sid`, protected DACL with one full-control rule for it.

    Used only when this code creates the object, like POSIX mkdir 0700 or open 0600.
    """
    ace = struct.pack('<BBHI', ALLOWED, 3 if directory else 0, 8 + len(sid), FILE_ALL_ACCESS) + sid
    acl = struct.pack('<BBHHH', 2, 0, 8 + len(ace), 1, 0) + ace
    return struct.pack('<BBHIIII', 1, 0, SE_SELF_RELATIVE | SE_DACL_PROTECTED | SE_DACL_PRESENT,
                       20, 0, 0, 20 + len(sid)) + sid + acl


def directory_entries(raw):
    """(name, attributes) from FILE_FULL_DIR_INFO records."""
    entries, offset = [], 0
    while True:
        if offset + 68 > len(raw): raise Unsafe()
        following = struct.unpack_from('<I', raw, offset)[0]
        attributes, length = struct.unpack_from('<II', raw, offset + 56)
        if offset + 68 + length > len(raw) or length % 2: raise Unsafe()
        entries.append((raw[offset + 68:offset + 68 + length].decode('utf-16-le', 'surrogatepass'), attributes))
        if not following: return entries
        offset += following


def component(name):
    """One plain NTFS name that Win32 pathname parsing resolves to the same entry; else refuse."""
    if (not isinstance(name, str) or not name or name in ('.', '..') or len(name) > 255 or name[-1] in '. '
            or any(char in '\\/:*?"<>|' or ord(char) < 32 for char in name) or RESERVED.fullmatch(name)):
        raise Unsafe()
    return name


# --- Native bindings ----------------------------------------------------------------------------

_bindings = None


def _api():
    """ctypes bindings and the caller's token SIDs, built once per process."""
    global _bindings
    if _bindings is not None: return _bindings
    import ctypes
    from ctypes import wintypes as w
    pointer = w.LPVOID
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    security = ctypes.WinDLL('advapi32', use_last_error=True)
    native = ctypes.WinDLL('ntdll')

    class Unicode(ctypes.Structure):
        _fields_ = [('length', w.USHORT), ('maximum', w.USHORT), ('buffer', pointer)]

    class Attributes(ctypes.Structure):
        _fields_ = [('length', w.ULONG), ('root', w.HANDLE), ('name', ctypes.POINTER(Unicode)),
                    ('attributes', w.ULONG), ('descriptor', pointer), ('quality', pointer)]

    class Status(ctypes.Structure):
        _fields_ = [('status', pointer), ('information', ctypes.c_size_t)]

    class Rename(ctypes.Structure):
        _fields_ = [('replace', w.ULONG), ('root', w.HANDLE), ('length', w.ULONG), ('name', w.WCHAR * 1)]

    kernel.GetCurrentProcess.restype = w.HANDLE
    kernel.CloseHandle.argtypes = [w.HANDLE]
    kernel.CreateFileW.argtypes = [w.LPCWSTR, w.DWORD, w.DWORD, pointer, w.DWORD, w.DWORD, w.HANDLE]
    kernel.CreateFileW.restype = w.HANDLE
    kernel.GetFileInformationByHandleEx.argtypes = [w.HANDLE, ctypes.c_int, pointer, w.DWORD]
    kernel.GetDriveTypeW.argtypes = [w.LPCWSTR]
    kernel.GetVolumeInformationByHandleW.argtypes = [w.HANDLE, pointer, w.DWORD, pointer, pointer, pointer,
                                                     w.LPWSTR, w.DWORD]
    security.OpenProcessToken.argtypes = [w.HANDLE, w.DWORD, ctypes.POINTER(w.HANDLE)]
    security.GetTokenInformation.argtypes = [w.HANDLE, ctypes.c_int, pointer, w.DWORD, ctypes.POINTER(w.DWORD)]
    security.GetLengthSid.argtypes, security.GetLengthSid.restype = [pointer], w.DWORD
    security.GetKernelObjectSecurity.argtypes = [w.HANDLE, w.DWORD, pointer, w.DWORD, ctypes.POINTER(w.DWORD)]
    native.NtCreateFile.argtypes = [ctypes.POINTER(w.HANDLE), w.DWORD, ctypes.POINTER(Attributes),
                                    ctypes.POINTER(Status), pointer, w.ULONG, w.ULONG, w.ULONG, w.ULONG,
                                    pointer, w.ULONG]
    native.NtCreateFile.restype = ctypes.c_long
    native.NtSetInformationFile.argtypes = [w.HANDLE, ctypes.POINTER(Status), pointer, w.ULONG, ctypes.c_int]
    native.NtSetInformationFile.restype = ctypes.c_long

    def token_sid(token, kind):
        needed = w.DWORD()
        security.GetTokenInformation(token, kind, None, 0, ctypes.byref(needed))
        if not needed.value: raise Unsafe()
        buffer = ctypes.create_string_buffer(needed.value)
        if not security.GetTokenInformation(token, kind, buffer, needed, ctypes.byref(needed)): raise Unsafe()
        sid = ctypes.cast(buffer, ctypes.POINTER(pointer)).contents  # TOKEN_USER/TOKEN_OWNER first field
        length = security.GetLengthSid(sid)
        if not length: raise Unsafe()
        return ctypes.string_at(sid, length)

    token = w.HANDLE()
    if not security.OpenProcessToken(kernel.GetCurrentProcess(), 8, ctypes.byref(token)): raise Unsafe()  # TOKEN_QUERY
    try: user, owner = token_sid(token, 1), token_sid(token, 4)  # TokenUser, TokenOwner
    finally: kernel.CloseHandle(token)
    _bindings = dict(ctypes=ctypes, w=w, kernel=kernel, security=security, native=native, Unicode=Unicode,
                     Attributes=Attributes, Status=Status, Rename=Rename, user_sid=user, user=sid_text(user),
                     owners=(sid_text(user), sid_text(owner)))
    return _bindings


def available():
    """Whether this Windows host can run the proof; never raises."""
    if os.name != 'nt': return False
    try: _api()
    except Exception: return False
    return True


def _invalid(api, handle):
    return handle is None or handle == api['ctypes'].c_void_p(-1).value


def _to_fd(api, handle, flags):
    import msvcrt
    try: return msvcrt.open_osfhandle(handle, flags)
    except Exception:
        api['kernel'].CloseHandle(handle)
        raise Unsafe() from None


def _handle(fd):
    import msvcrt
    try: return msvcrt.get_osfhandle(fd)
    except OSError: raise Unsafe() from None


def _nt_open(parent, name, access, share, disposition, options, descriptor=None):
    """Open `name` relative to the `parent` handle; None when it does not exist."""
    api = _api()
    ctypes = api['ctypes']
    encoded = component(name).encode('utf-16-le')
    text = ctypes.create_string_buffer(encoded + b'\0\0')
    unicode = api['Unicode'](len(encoded), len(encoded) + 2, ctypes.cast(text, ctypes.c_void_p))
    security = None if descriptor is None else ctypes.create_string_buffer(descriptor)
    attributes = api['Attributes'](ctypes.sizeof(api['Attributes']), parent, ctypes.pointer(unicode), 0x40,  # OBJ_CASE_INSENSITIVE
                                   None if security is None else ctypes.cast(security, ctypes.c_void_p), None)
    handle, status = api['w'].HANDLE(), api['Status']()
    code = api['native'].NtCreateFile(ctypes.byref(handle), access, ctypes.byref(attributes), ctypes.byref(status),
                                      None, 0x80, share, disposition, options, None, 0) & 0xFFFFFFFF
    if code in NOT_FOUND: return None
    if code == COLLISION: raise FileExistsError()
    if code or _invalid(api, handle.value): raise Unsafe()
    return handle.value


def open_root(text):
    """Handle-backed descriptor of a local NTFS/ReFS drive root, opened as itself."""
    api = _api()
    if not isinstance(text, str) or not DRIVE_ROOT.fullmatch(text) or api['kernel'].GetDriveTypeW(text) != 3:
        raise Unsafe()  # DRIVE_FIXED: no UNC, network, removable or device path
    handle = api['kernel'].CreateFileW(text, READ_CONTROL | FILE_READ_ATTRIBUTES | FILE_READ_DATA | SYNCHRONIZE,
                                       SHARE_READ_WRITE, None, 3, 0x02200000, None)  # backup semantics, the link itself
    if _invalid(api, handle): raise Unsafe()
    name = api['ctypes'].create_unicode_buffer(64)
    if (not api['kernel'].GetVolumeInformationByHandleW(handle, None, 0, None, None, None, name, 64)
            or name.value not in ('NTFS', 'ReFS')):
        api['kernel'].CloseHandle(handle)
        raise Unsafe()
    return _to_fd(api, handle, os.O_RDONLY)


def open_at(parent, name, *, directory, create=False, exclusive=False, write=False, share_delete=False):
    """Descriptor of `name` under the verified `parent` descriptor, never following a reparse point.

    `create` opens or creates (exclusive: only creates) with an owner-only protected descriptor. Without
    `share_delete` the held descriptor pins the entry against rename, replacement and deletion.
    """
    api = _api()
    access = READ_CONTROL | FILE_READ_ATTRIBUTES | FILE_READ_DATA | SYNCHRONIZE
    if write: access |= GENERIC_READ | GENERIC_WRITE
    disposition = (FILE_CREATE if exclusive else FILE_OPEN_IF) if create else FILE_OPEN
    options = SYNCHRONOUS | OPEN_REPARSE_POINT | (DIRECTORY_FILE if directory else NON_DIRECTORY_FILE)
    descriptor = private_descriptor(api['user_sid'], directory) if create else None
    handle = _nt_open(_handle(parent), name, access, SHARE_ALL if share_delete else SHARE_READ_WRITE,
                      disposition, options, descriptor)
    if handle is None: return None
    return _to_fd(api, handle, os.O_RDWR if write else os.O_RDONLY)


def _attributes(api, handle):
    ctypes, w = api['ctypes'], api['w']
    tag = (w.DWORD * 2)()
    standard = (ctypes.c_ubyte * 24)()  # FILE_STANDARD_INFO
    if (not api['kernel'].GetFileInformationByHandleEx(handle, 9, tag, ctypes.sizeof(tag))
            or not api['kernel'].GetFileInformationByHandleEx(handle, 1, standard, ctypes.sizeof(standard))):
        raise Unsafe()
    links, pending, is_directory = struct.unpack_from('<IBB', bytes(standard), 16)
    return tag[0], links, bool(pending), bool(is_directory)


def descriptor_of(api, handle):
    """Self-relative owner and DACL of an open handle (READ_CONTROL); the SACL is never requested."""
    ctypes, w = api['ctypes'], api['w']
    needed = w.DWORD()
    api['security'].GetKernelObjectSecurity(handle, 5, None, 0, ctypes.byref(needed))  # OWNER | DACL
    if not needed.value or needed.value > 65536: raise Unsafe()
    buffer = ctypes.create_string_buffer(needed.value)
    if not api['security'].GetKernelObjectSecurity(handle, 5, buffer, needed, ctypes.byref(needed)): raise Unsafe()
    return buffer.raw[:needed.value]


def verify(fd, kind, policy):
    """Refuse a reparse point, a wrong type, a linked or deleting file, or an owner/DACL outside `kind`."""
    api = _api()
    handle = _handle(fd)
    attributes, links, pending, is_directory = _attributes(api, handle)
    if attributes & ATTRIBUTE_REPARSE or pending: raise Unsafe()
    if is_directory != (kind != 'file') or bool(attributes & ATTRIBUTE_DIRECTORY) != is_directory: raise Unsafe()
    if kind == 'file' and links != 1: raise Unsafe()
    evaluate(descriptor_of(api, handle), kind, policy, api['user'], api['owners'])


def open_verified(parent, name, kind, policy, *, create=False, share_delete=False):
    """`open_at` then `verify`; None when absent. A refused descriptor is closed."""
    fd = open_at(parent, name, directory=kind != 'file', create=create and kind != 'file', share_delete=share_delete)
    if fd is None: return None
    try: verify(fd, kind, policy)
    except BaseException:
        os.close(fd)
        raise
    return fd


def children(fd):
    """(name, attributes) of the entries in a verified directory descriptor, without opening any."""
    api = _api()
    ctypes = api['ctypes']
    handle, buffer, entries, restart = _handle(fd), ctypes.create_string_buffer(65536), [], True
    while True:
        # FileFullDirectoryRestartInfo first, then FileFullDirectoryInfo until ERROR_NO_MORE_FILES.
        if not api['kernel'].GetFileInformationByHandleEx(handle, 15 if restart else 14, buffer, len(buffer)):
            if ctypes.get_last_error() == 18: break
            raise Unsafe()
        restart = False
        entries += directory_entries(buffer.raw)
    return [entry for entry in entries if entry[0] not in ('.', '..')]


def rename_at(directory, source, target):
    """Atomically replace `target` with `source`, both named relative to the held `directory`."""
    api = _api()
    ctypes = api['ctypes']
    parent = _handle(directory)
    handle = _nt_open(parent, source, DELETE | SYNCHRONIZE, SHARE_ALL, FILE_OPEN,
                      SYNCHRONOUS | OPEN_REPARSE_POINT | NON_DIRECTORY_FILE)
    if handle is None: raise FileNotFoundError()
    try:
        encoded = component(target).encode('utf-16-le')
        offset = api['Rename'].name.offset
        buffer = ctypes.create_string_buffer(max(ctypes.sizeof(api['Rename']), offset + len(encoded) + 2))
        header = api['Rename'].from_buffer(buffer)
        header.replace, header.root, header.length = 1, parent, len(encoded)
        ctypes.memmove(ctypes.addressof(buffer) + offset, encoded, len(encoded))
        status = api['Status']()
        if api['native'].NtSetInformationFile(handle, ctypes.byref(status), buffer, len(buffer), 10):  # FileRenameInformation
            raise Unsafe()
    finally: api['kernel'].CloseHandle(handle)


def unlink_at(directory, name):
    """Delete the plain file `name` relative to the held `directory`."""
    api = _api()
    handle = _nt_open(_handle(directory), name, DELETE | SYNCHRONIZE, SHARE_ALL, FILE_OPEN,
                      SYNCHRONOUS | OPEN_REPARSE_POINT | NON_DIRECTORY_FILE | DELETE_ON_CLOSE)
    if handle is None: raise FileNotFoundError()
    api['kernel'].CloseHandle(handle)


def path_identity(path):
    """(volume, file ID) of the entry a Win32 pathname names now, opened as the link itself."""
    api = _api()
    handle = api['kernel'].CreateFileW(str(path), FILE_READ_ATTRIBUTES, SHARE_ALL, None, 3, 0x02200000, None)
    if _invalid(api, handle): raise Unsafe()
    fd = _to_fd(api, handle, os.O_RDONLY)
    try:
        info = os.fstat(fd)
        return info.st_dev, info.st_ino
    finally: os.close(fd)
