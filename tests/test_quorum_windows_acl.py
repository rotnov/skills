"""Real Windows ACL regression; no credentials, models or hosted service calls."""
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import struct
import sys
import tempfile
import time
import traceback
from types import SimpleNamespace
import unittest
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / 'skills/quorum/scripts'
CHECK_TIMEOUT = 15


def sid_text(raw):
    if len(raw) < 8 or raw[0] != 1 or raw[1] > 15 or len(raw) != 8 + 4 * raw[1]:
        raise ValueError('invalid_sid')
    parts = [1, int.from_bytes(raw[2:8], 'big'), *struct.unpack_from('<' + 'I' * raw[1], raw, 8)]
    return 'S-' + '-'.join(map(str, parts))


def native_acl_view(owner, current_user, control, raw):
    """Decode actual owner/DACL bytes without filtering away unexpected rules."""
    if not control & 4 or raw is None or len(raw) < 8:
        raise ValueError('missing_dacl')
    revision, _, size, count, _ = struct.unpack_from('<BBHHH', raw)
    if revision not in (2, 4) or size != len(raw):
        raise ValueError('invalid_acl')
    rules, offset = [], 8
    for _ in range(count):
        if offset + 8 > size: raise ValueError('invalid_ace')
        kind, flags, length, rights = struct.unpack_from('<BBHI', raw, offset)
        if kind not in (0, 1) or length < 16 or offset + length > size:
            raise ValueError('invalid_ace')
        rules.append({'sid': sid_text(raw[offset + 8:offset + length]), 'rights': rights,
                      'inherited': bool(flags & 0x10), 'inheritance': flags & 3,
                      'propagation': flags & 0xC, 'type': 'Allow' if kind == 0 else 'Deny'})
        offset += length
    return {'owner': sid_text(owner), 'current_user': sid_text(current_user),
            'protected': bool(control & 0x1000), 'rules': rules}


def inspect_native_acl(path):
    """Independent READ_CONTROL inspection; no PowerShell startup and no SACL request."""
    import ctypes
    from ctypes import wintypes as w
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    security = ctypes.WinDLL('advapi32', use_last_error=True)
    pointer = w.LPVOID
    kernel.GetCurrentProcess.restype = w.HANDLE
    kernel.CloseHandle.argtypes = [w.HANDLE]
    kernel.LocalFree.argtypes, kernel.LocalFree.restype = [pointer], pointer
    security.OpenProcessToken.argtypes = [w.HANDLE, w.DWORD, ctypes.POINTER(w.HANDLE)]
    security.GetTokenInformation.argtypes = [w.HANDLE, ctypes.c_int, pointer, w.DWORD, ctypes.POINTER(w.DWORD)]
    security.GetLengthSid.argtypes, security.GetLengthSid.restype = [pointer], w.DWORD
    security.GetNamedSecurityInfoW.argtypes = [w.LPWSTR, ctypes.c_int, w.DWORD] + [ctypes.POINTER(pointer)] * 5
    security.GetNamedSecurityInfoW.restype = w.DWORD
    security.GetSecurityDescriptorControl.argtypes = [pointer, ctypes.POINTER(w.WORD), ctypes.POINTER(w.DWORD)]

    def sid_bytes(sid):
        if not sid: raise ValueError('missing_sid')
        length = security.GetLengthSid(sid)
        if not length: raise ctypes.WinError(ctypes.get_last_error())
        return ctypes.string_at(sid, length)

    token, needed = w.HANDLE(), w.DWORD()
    if not security.OpenProcessToken(kernel.GetCurrentProcess(), 8, ctypes.byref(token)):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        security.GetTokenInformation(token, 1, None, 0, ctypes.byref(needed))  # TokenUser
        if not needed.value: raise ctypes.WinError(ctypes.get_last_error())
        user = ctypes.create_string_buffer(needed.value)
        if not security.GetTokenInformation(token, 1, user, needed, ctypes.byref(needed)):
            raise ctypes.WinError(ctypes.get_last_error())
        current = sid_bytes(ctypes.cast(user, ctypes.POINTER(pointer)).contents)
    finally:
        kernel.CloseHandle(token)
    owner, dacl, descriptor = pointer(), pointer(), pointer()
    # SE_FILE_OBJECT, OWNER_SECURITY_INFORMATION | DACL_SECURITY_INFORMATION.
    error = security.GetNamedSecurityInfoW(str(path), 1, 5, ctypes.byref(owner), None,
                                          ctypes.byref(dacl), None, ctypes.byref(descriptor))
    if error: raise ctypes.WinError(error)
    try:
        control, revision = w.WORD(), w.DWORD()
        if not security.GetSecurityDescriptorControl(descriptor, ctypes.byref(control), ctypes.byref(revision)):
            raise ctypes.WinError(ctypes.get_last_error())
        raw = None
        if dacl:
            size = struct.unpack_from('<H', ctypes.string_at(dacl, 8), 2)[0]
            raw = ctypes.string_at(dacl, size)
        return native_acl_view(sid_bytes(owner), current, control.value, raw)
    finally:
        kernel.LocalFree(descriptor)


def timed(timings, phase, action):
    # Report only fixed phase names, durations and exception types: no paths or commands.
    started = time.monotonic()
    try: value = action()
    except Exception as error:
        timings.append(f'{phase}={time.monotonic() - started:.1f}s:{type(error).__name__}')
        print('probe_failed ' + ' '.join(timings), file=sys.stderr)
        raise SystemExit(1) from None
    timings.append(f'{phase}={time.monotonic() - started:.1f}s')
    return value


def probe_without_sacl_privilege(root):
    # Change only this disposable subprocess token, never the test runner's token.
    import ctypes
    from ctypes import wintypes as w

    class Luid(ctypes.Structure):
        _fields_ = [('low', w.DWORD), ('high', w.LONG)]

    class Privilege(ctypes.Structure):
        _fields_ = [('luid', Luid), ('attributes', w.DWORD)]

    class TokenPrivileges(ctypes.Structure):
        _fields_ = [('count', w.DWORD), ('privileges', Privilege * 1)]

    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    security = ctypes.WinDLL('advapi32', use_last_error=True)
    kernel.GetCurrentProcess.restype = w.HANDLE
    kernel.CloseHandle.argtypes = [w.HANDLE]
    security.OpenProcessToken.argtypes = [w.HANDLE, w.DWORD, ctypes.POINTER(w.HANDLE)]
    security.LookupPrivilegeValueW.argtypes = [w.LPCWSTR, w.LPCWSTR, ctypes.POINTER(Luid)]
    security.AdjustTokenPrivileges.argtypes = [w.HANDLE, w.BOOL, ctypes.POINTER(TokenPrivileges),
                                             w.DWORD, w.LPVOID, w.LPVOID]
    token = w.HANDLE()
    if not security.OpenProcessToken(kernel.GetCurrentProcess(), 0x28, ctypes.byref(token)):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        update = TokenPrivileges()
        update.count = 1
        if not security.LookupPrivilegeValueW(None, 'SeSecurityPrivilege', ctypes.byref(update.privileges[0].luid)):
            raise ctypes.WinError(ctypes.get_last_error())
        update.privileges[0].attributes = 4  # SE_PRIVILEGE_REMOVED; cannot be re-enabled.
        if not security.AdjustTokenPrivileges(token, False, ctypes.byref(update), 0, None, None):
            raise ctypes.WinError(ctypes.get_last_error())
        if ctypes.get_last_error() not in (0, 1300):  # Already absent is also suitable.
            raise ctypes.WinError(ctypes.get_last_error())
    finally:
        kernel.CloseHandle(token)
    timings = []
    privileges = timed(timings, 'privileges', lambda: subprocess.check_output(
        ['whoami', '/priv'], text=True, timeout=CHECK_TIMEOUT))
    assert 'SeSecurityPrivilege' not in privileges, 'SACL privilege was not removed'

    sys.path.insert(0, str(SCRIPTS))
    from quorum_os import protect
    directory = root / "private $data ' unicode"
    directory.mkdir()
    file = directory / 'invitation.json'
    file.write_text('{}', encoding='utf-8')
    results = []
    for path, kind in ((directory, 'directory'), (file, 'file')):
        timed(timings, f'protect_{kind}', lambda: protect(path, directory=kind == 'directory'))
        results.append(timed(timings, f'inspect_{kind}', lambda: inspect_native_acl(path)))
    print(json.dumps(results))
    print('probe_ok ' + ' '.join(timings), file=sys.stderr)


@unittest.skipUnless(os.name == 'nt', 'Real NTFS ACL check runs in the Windows CI job')
class WindowsAclTests(unittest.TestCase):
    def test_owner_only_directory_and_file_without_sacl_privilege(self):
        sys.path.insert(0, str(SCRIPTS))
        from quorum_os import PRIVATE_ACL_TIMEOUT
        # Exceed every bounded probe operation plus interpreter and token setup.
        deadline = CHECK_TIMEOUT + 2 * (PRIVATE_ACL_TIMEOUT + CHECK_TIMEOUT) + 30
        with tempfile.TemporaryDirectory() as temporary:
            result = subprocess.run([sys.executable, str(Path(__file__).resolve()), '--probe', temporary],
                                    capture_output=True, text=True, timeout=deadline)
        self.assertEqual(result.returncode, 0, result.stderr)
        results = json.loads(result.stdout)
        self.assertEqual(len(results), 2)
        for acl, inheritance in zip(results, (3, 0)):
            self.assertTrue(acl['protected'])
            self.assertEqual(acl['owner'], acl['current_user'])
            self.assertEqual(acl['rules'], [{'sid': acl['current_user'], 'rights': 2032127,
                                           'inherited': False, 'inheritance': inheritance,
                                           'propagation': 0, 'type': 'Allow'}])


class NativeAclViewTests(unittest.TestCase):
    USER = bytes.fromhex('0102000000000005150000002a000000')  # S-1-5-21-42
    OTHER = bytes.fromhex('010100000000000512000000')  # S-1-5-18

    def acl(self, entries):
        aces = b''.join(struct.pack('<BBHI', kind, flags, 8 + len(sid), rights) + sid
                        for kind, flags, rights, sid in entries)
        return struct.pack('<BBHHH', 2, 0, 8 + len(aces), len(entries), 0) + aces

    def test_native_view_preserves_exact_owner_protection_rights_and_inheritance(self):
        raw = self.acl([(0, 3, 2032127, self.USER)])
        self.assertEqual(native_acl_view(self.USER, self.USER, 0x1004, raw), {
            'current_user': 'S-1-5-21-42', 'owner': 'S-1-5-21-42', 'protected': True,
            'rules': [{'sid': 'S-1-5-21-42', 'rights': 2032127, 'inherited': False,
                       'inheritance': 3, 'propagation': 0, 'type': 'Allow'}]})

    def test_native_view_does_not_hide_an_extra_deny_or_inherited_rule(self):
        raw = self.acl([(0, 0, 2032127, self.USER), (1, 0x18, 1, self.OTHER)])
        result = native_acl_view(self.OTHER, self.USER, 4, raw)
        self.assertEqual(result['owner'], 'S-1-5-18')
        self.assertFalse(result['protected'])
        self.assertEqual(len(result['rules']), 2)
        self.assertEqual(result['rules'][1], {'sid': 'S-1-5-18', 'rights': 1, 'inherited': True,
                                            'inheritance': 0, 'propagation': 8, 'type': 'Deny'})

    def test_native_view_fails_closed_for_missing_truncated_or_unknown_acl(self):
        good = self.acl([(0, 0, 2032127, self.USER)])
        for control, raw in ((0x1004, None), (0x1000, good), (0x1004, good[:-1]),
                             (0x1004, self.acl([(5, 0, 2032127, self.USER)]))):
            with self.subTest(control=control, raw=raw), self.assertRaises(ValueError):
                native_acl_view(self.USER, self.USER, control, raw)

    def test_native_inspection_reads_only_owner_dacl_and_releases_its_handles(self):
        import ctypes
        from ctypes import wintypes as w
        user = ctypes.create_string_buffer(self.USER)
        acl = ctypes.create_string_buffer(self.acl([(0, 0, 2032127, self.USER)]))
        calls = []

        def value(output, kind, data):
            ctypes.cast(output, ctypes.POINTER(kind))[0] = data

        def open_token(process, access, output):
            calls.append(('token_access', access))
            value(output, w.HANDLE, 77)
            return 1

        def token_info(token, kind, output, length, needed):
            self.assertEqual(kind, 1)  # TokenUser, never privilege/SACL acquisition.
            value(needed, w.DWORD, ctypes.sizeof(ctypes.c_void_p))
            if output is not None:
                value(output, ctypes.c_void_p, ctypes.addressof(user))
            return int(output is not None)

        def named_info(path, kind, flags, owner, group, dacl, sacl, descriptor):
            calls.append(('security_info', kind, flags, group, sacl))
            value(owner, ctypes.c_void_p, ctypes.addressof(user))
            value(dacl, ctypes.c_void_p, ctypes.addressof(acl))
            value(descriptor, ctypes.c_void_p, 88)
            return 0

        def control(descriptor, flags, revision):
            value(flags, w.WORD, 0x1004)
            value(revision, w.DWORD, 1)
            return 1

        # Plain callables accept the same ctypes signature declarations as DLL exports.
        kernel = SimpleNamespace(GetCurrentProcess=lambda: 1,
                                 CloseHandle=lambda handle: calls.append(('close', handle.value)),
                                 LocalFree=lambda handle: calls.append(('free', handle.value)))
        security = SimpleNamespace(OpenProcessToken=open_token, GetTokenInformation=token_info,
                                   GetLengthSid=lambda sid: len(self.USER), GetNamedSecurityInfoW=named_info,
                                   GetSecurityDescriptorControl=control)
        with patch.object(ctypes, 'WinDLL', side_effect=lambda name, **_: kernel if name == 'kernel32' else security,
                          create=True), patch.object(subprocess, 'run', side_effect=AssertionError('no inspection subprocess')):
            result = inspect_native_acl(Path('/synthetic'))
        self.assertEqual(result, native_acl_view(self.USER, self.USER, 0x1004, bytes(acl.raw[:-1])))
        self.assertEqual(calls, [('token_access', 8), ('close', 77),
                                 ('security_info', 1, 5, None, None), ('free', 88)])


class PrivateAclDeadlineTests(unittest.TestCase):
    """Synthetic Windows host: success, timeout and launch failure stay fail-closed and hidden."""
    def protect(self, run):
        sys.path.insert(0, str(SCRIPTS))
        import quorum_os
        temporary = self.enterContext(tempfile.TemporaryDirectory())
        path = Path(temporary) / "hostile $(x) ' `n; & é"
        path.touch()
        host = SimpleNamespace(name='nt', environ=dict(os.environ, SystemRoot='C:/Windows'))
        with patch.object(quorum_os, 'os', host), \
             patch.object(subprocess, 'CREATE_NO_WINDOW', 0x08000000, create=True), \
             patch.object(subprocess, 'run', side_effect=run) as launched:
            try: quorum_os.protect(path)
            except ValueError as error: failure = error
            else: failure = None
        return quorum_os, path, launched, failure

    def assert_hidden_failure(self, path, failure):
        self.assertIsInstance(failure, ValueError)
        self.assertEqual(str(failure), 'private_acl_unavailable')
        self.assertIsNone(failure.__cause__)
        self.assertTrue(failure.__context__ is None or failure.__suppress_context__)
        rendered = ''.join(traceback.format_exception(failure))
        for leaked in (path.name, str(path), 'EncodedCommand', 'timed out', 'secret'):
            self.assertNotIn(leaked, rendered)

    def test_success_uses_owned_deadline_hidden_window_and_path_as_environment_data(self):
        quorum_os, path, launched, failure = self.protect(lambda *_, **__: SimpleNamespace(returncode=0))
        self.assertIsNone(failure)
        command, options = launched.call_args.args[0], launched.call_args.kwargs
        self.assertEqual(options['timeout'], quorum_os.PRIVATE_ACL_TIMEOUT)
        self.assertEqual(options['creationflags'], 0x08000000)
        self.assertEqual(options['env']['QUORUM_ACL_PATH'], str(path.resolve()))
        self.assertFalse(any(path.name in part for part in command))

    def test_nonzero_exit_reports_fixed_code(self):
        _, path, _, failure = self.protect(lambda *_, **__: SimpleNamespace(returncode=1))
        self.assert_hidden_failure(path, failure)

    def test_timeout_reports_fixed_code_without_command_or_path(self):
        def expire(command, **options):
            raise subprocess.TimeoutExpired(command + [options['env']['QUORUM_ACL_PATH']], options['timeout'])
        _, path, launched, failure = self.protect(expire)
        self.assertEqual(launched.call_count, 1)  # No blind retry after the deadline.
        self.assert_hidden_failure(path, failure)

    def test_launch_failure_reports_fixed_code_without_os_text(self):
        def missing(command, **_):
            raise FileNotFoundError(2, 'secret launcher detail', command[0])
        _, path, launched, failure = self.protect(missing)
        self.assertEqual(launched.call_count, 1)
        self.assert_hidden_failure(path, failure)


class LocalSetupErrorCodeTests(unittest.TestCase):
    def test_fixed_local_setup_codes_are_reported_and_other_text_is_hidden(self):
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'skills/quorum/scripts'))
        import quorum
        for error, expected in ((ValueError('private_acl_unavailable'), 'private_acl_unavailable'),
                                (ValueError('unsafe_link'), 'unsafe_link'),
                                (ValueError('login_timeout'), 'login_timeout'),
                                (OSError('private_acl_unavailable'), 'operation_failed'),
                                (ValueError('C:\\Users\\secret'), 'operation_failed')):
            output = io.StringIO()
            with self.subTest(expected=expected), tempfile.TemporaryDirectory() as temporary, \
                 patch('quorum.LocalLedger', side_effect=error), contextlib.redirect_stdout(output):
                code = quorum.main(['--state', temporary, 'status'])
            self.assertEqual(code, 3)
            self.assertEqual(json.loads(output.getvalue()), {'ok': False, 'code': expected, 'data': {}})


if __name__ == '__main__':
    if len(sys.argv) == 3 and sys.argv[1] == '--probe':
        probe_without_sacl_privilege(Path(sys.argv[2]))
    else:
        unittest.main()
