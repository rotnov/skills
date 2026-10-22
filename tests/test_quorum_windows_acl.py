"""Real Windows ACL regression; no credentials, models or hosted service calls."""
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


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
    privileges = subprocess.check_output(['whoami', '/priv'], text=True)
    assert 'SeSecurityPrivilege' not in privileges, 'SACL privilege was not removed'

    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'skills/quorum/scripts'))
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
    for path, is_directory in ((directory, True), (file, False)):
        protect(path, directory=is_directory)
        inspected = subprocess.run([str(powershell), '-NoProfile', '-NonInteractive', '-Command', script],
                                   env=dict(os.environ, QUORUM_TEST_PATH=str(path)),
                                   capture_output=True, text=True, check=True, timeout=15)
        results.append(json.loads(inspected.stdout))
    print(json.dumps(results))


@unittest.skipUnless(os.name == 'nt', 'Real NTFS ACL check runs in the Windows CI job')
class WindowsAclTests(unittest.TestCase):
    def test_owner_only_directory_and_file_without_sacl_privilege(self):
        with tempfile.TemporaryDirectory() as temporary:
            result = subprocess.run([sys.executable, str(Path(__file__).resolve()), '--probe', temporary],
                                    capture_output=True, text=True, timeout=90)
        self.assertEqual(result.returncode, 0, result.stderr)
        results = json.loads(result.stdout)
        self.assertEqual(len(results), 2)
        for acl, inheritance in zip(results, (3, 0)):
            self.assertTrue(acl['protected'])
            self.assertEqual(acl['owner'], acl['current_user'])
            self.assertEqual(acl['rules'], [{'sid': acl['current_user'], 'rights': 2032127,
                                           'inherited': False, 'inheritance': inheritance, 'type': 'Allow'}])


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
