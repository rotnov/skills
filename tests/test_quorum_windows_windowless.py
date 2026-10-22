"""Background helpers must not allocate consoles; never start a model or peer chat."""
import os
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / 'skills/quorum/scripts'
sys.path.insert(0, str(SCRIPTS))
import quorum_os  # noqa: E402
NO_WINDOW = 0x08000000
SID = '11111111-1111-4111-8111-111111111111'
MID = '22222222-2222-4222-8222-222222222222'
# The detached probe runs protect() then enqueue() sequentially; its parent
# wait covers both owned deadlines plus Python startup/scheduler margin.
ENQUEUE_TIMEOUT = 30
PROBE_TIMEOUT = quorum_os.PRIVATE_ACL_TIMEOUT + ENQUEUE_TIMEOUT + 15


class WindowlessHelpersTests(unittest.TestCase):
    @unittest.skipUnless(os.name == 'nt', 'native Windows console allocation')
    def test_detached_listener_helpers_have_no_native_console(self):
        with tempfile.TemporaryDirectory(prefix='quorum console ') as temporary:
            root = Path(temporary)
            with (root / 'probe.log').open('wb') as log:
                process = subprocess.Popen([sys.executable, __file__, '--probe', str(root)],
                    stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                    creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP)
            try:
                self.assertEqual(process.wait(timeout=PROBE_TIMEOUT), 0, (root / 'probe.log').read_text())
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait(timeout=5)
            result = json.loads((root / 'result.json').read_text())
            self.assertEqual(result, {'listener_console': 0, 'acl_console': 0, 'queue_console': 0})

    def test_windows_private_acl_helper_cannot_allocate_a_console(self):
        import quorum_os
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'private-state'
            path.touch()
            host = SimpleNamespace(name='nt', environ=dict(os.environ, SystemRoot='C:/Windows'))
            with patch.object(quorum_os, 'os', host), \
                 patch.object(subprocess, 'CREATE_NO_WINDOW', NO_WINDOW, create=True), \
                 patch.object(subprocess, 'run', return_value=SimpleNamespace(returncode=0)) as run:
                quorum_os.protect(path)
            self.assertEqual(run.call_args.kwargs.get('creationflags', 0), NO_WINDOW)
            self.assertEqual(run.call_args.kwargs['env']['QUORUM_ACL_PATH'], str(path.resolve()))

    def test_windows_codex_queue_cannot_allocate_a_console(self):
        import quorum_session
        host = SimpleNamespace(name='nt')
        with patch.object(quorum_session, 'os', host), \
             patch.object(subprocess, 'CREATE_NO_WINDOW', NO_WINDOW, create=True), \
             patch.object(quorum_session.shutil, 'which', return_value='synthetic-codex.exe'), \
             patch.object(subprocess, 'run', return_value=SimpleNamespace(returncode=0)) as run:
            quorum_session.enqueue({'session_ref': SID}, {'id': MID}, Path('/synthetic/state'))
        self.assertEqual(run.call_args.kwargs.get('creationflags', 0), NO_WINDOW)
        self.assertEqual(run.call_args.args[0][1:4], ['queue', '--thread', SID])
        self.assertEqual(run.call_args.kwargs['timeout'], ENQUEUE_TIMEOUT)

    def test_windows_claude_io_helper_cannot_allocate_a_console(self):
        import quorum_claude_pipe
        with patch.object(quorum_claude_pipe, 'os', SimpleNamespace(name='nt')), \
             patch.object(subprocess, 'CREATE_NO_WINDOW', NO_WINDOW, create=True), \
             patch.object(subprocess, 'run', return_value=SimpleNamespace(returncode=0)) as run:
            quorum_claude_pipe.submit(SID, MID, 'synthetic message')
        self.assertEqual(run.call_args.kwargs.get('creationflags', 0), NO_WINDOW)
        self.assertEqual(run.call_args.kwargs['timeout'], 10)


def detached_probe(root):
    """Exercise real console allocation from a listener without a parent console."""
    import base64
    import ctypes
    import quorum_os
    import quorum_session
    kernel = ctypes.WinDLL('kernel32')
    kernel.GetConsoleWindow.restype = ctypes.c_void_p
    result = {'listener_console': kernel.GetConsoleWindow() or 0}
    original = subprocess.run

    def execute(command, **kwargs):
        if command[0].lower().endswith('powershell.exe'):
            # Observe this actual ACL child before running its original script.
            script = base64.b64decode(command[-1]).decode('utf-16-le')
            inspect = '''
Add-Type -TypeDefinition 'using System; using System.Runtime.InteropServices; public class ConsoleProbe { [DllImport("kernel32.dll")] public static extern IntPtr GetConsoleWindow(); }'
[System.IO.File]::WriteAllText($env:QUORUM_CONSOLE_PROBE, [ConsoleProbe]::GetConsoleWindow().ToInt64().ToString())
'''
            command = command[:-1] + [base64.b64encode((inspect + script).encode('utf-16-le')).decode()]
        else:
            # Preserve enqueue's launch kwargs, replacing only Codex with a
            # synthetic console application. Never touch a real client chat.
            if command[1:3] != ['queue', '--thread']: raise ValueError('unexpected_command')
            command = [sys.executable, '-c', 'import ctypes; k=ctypes.WinDLL("kernel32"); '
                       'k.GetConsoleWindow.restype=ctypes.c_void_p; print(k.GetConsoleWindow() or 0)']
        completed = original(command, **kwargs)
        if command[0] == sys.executable:
            result['queue_console'] = int(completed.stdout)
        return completed

    target = root / 'synthetic-state'
    target.write_text('synthetic only')
    with patch.dict(os.environ, {'QUORUM_CONSOLE_PROBE': str(root / 'console.txt')}), \
         patch.object(subprocess, 'run', side_effect=execute):
        quorum_os.protect(target)
        result['acl_console'] = int((root / 'console.txt').read_text())
        with patch.object(quorum_session.shutil, 'which', return_value=sys.executable):
            quorum_session.enqueue({'session_ref': SID}, {'id': MID}, root)
    (root / 'result.json').write_text(json.dumps(result))


if __name__ == '__main__':
    if len(sys.argv) == 3 and sys.argv[1] == '--probe':
        detached_probe(Path(sys.argv[2]))
    else:
        unittest.main()
