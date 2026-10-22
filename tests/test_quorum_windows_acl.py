"""Real Windows ACL regression; no credentials, models or hosted service calls."""
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import traceback
from types import SimpleNamespace
import unittest
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / 'skills/quorum/scripts'
CHECK_TIMEOUT = 15


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
    script = r'''
$ErrorActionPreference = 'Stop'
$item = Get-Item -LiteralPath $env:QUORUM_TEST_PATH -Force
$acl = $item.GetAccessControl([System.Security.AccessControl.AccessControlSections]'Access,Owner')
$sidType = [System.Security.Principal.SecurityIdentifier]
$rules = @($acl.GetAccessRules($true, $true, $sidType) | ForEach-Object {
  @{sid=$_.IdentityReference.Value; rights=[int]$_.FileSystemRights;
    inherited=$_.IsInherited; inheritance=[int]$_.InheritanceFlags; type=[string]$_.AccessControlType}
})
@{current_user=[System.Security.Principal.WindowsIdentity]::GetCurrent().User.Value;
  owner=$acl.GetOwner($sidType).Value; protected=$acl.AreAccessRulesProtected; rules=$rules} | ConvertTo-Json -Depth 4
'''
    powershell = Path(os.environ['SystemRoot']) / 'System32/WindowsPowerShell/v1.0/powershell.exe'
    results = []
    for path, kind in ((directory, 'directory'), (file, 'file')):
        timed(timings, f'protect_{kind}', lambda: protect(path, directory=kind == 'directory'))
        inspected = timed(timings, f'inspect_{kind}', lambda: subprocess.run(
            [str(powershell), '-NoProfile', '-NonInteractive', '-Command', script],
            env=dict(os.environ, QUORUM_TEST_PATH=str(path)),
            capture_output=True, text=True, check=True, timeout=CHECK_TIMEOUT))
        results.append(json.loads(inspected.stdout))
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
                                           'inherited': False, 'inheritance': inheritance, 'type': 'Allow'}])


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
