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
            # The production listener launch; under `uv run`, sys.executable is a launcher.
            with (root / 'probe.log').open('wb') as log:
                process = subprocess.Popen([sys.executable, __file__, '--probe', str(root)],
                    stdin=subprocess.DEVNULL, stdout=log, stderr=log, **quorum_os.background_flags())
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


NEW_GROUP, DETACHED, SUSPENDED = 0x200, 0x8, 0x4


class BackgroundLaunchFlagsTests(unittest.TestCase):
    def test_windows_background_children_get_a_hidden_console_never_detached(self):
        with patch.object(quorum_os, 'os', SimpleNamespace(name='nt')), \
             patch.object(subprocess, 'CREATE_NO_WINDOW', NO_WINDOW, create=True), \
             patch.object(subprocess, 'CREATE_NEW_PROCESS_GROUP', NEW_GROUP, create=True):
            listener, handler = quorum_os.background_flags(), quorum_os.background_flags(SUSPENDED)
        self.assertEqual(listener, {'creationflags': NO_WINDOW | NEW_GROUP})
        self.assertEqual(handler, {'creationflags': NO_WINDOW | NEW_GROUP | SUSPENDED})
        self.assertFalse((listener['creationflags'] | handler['creationflags']) & DETACHED)
        if os.name != 'nt': self.assertEqual(quorum_os.background_flags(), {'start_new_session': True})

    def test_session_and_pilot_listeners_spawn_through_the_windowless_launcher(self):
        import quorum_listener
        from quorum_state import LocalLedger
        marker = {'creationflags': NO_WINDOW | NEW_GROUP}
        with tempfile.TemporaryDirectory() as temporary:
            ledger = LocalLedger(Path(temporary))
            try:
                with patch.object(quorum_listener, 'background_flags', return_value=marker) as flags, \
                     patch.object(quorum_listener, 'protect'), \
                     patch.object(quorum_listener.subprocess, 'Popen') as popen:
                    quorum_listener.spawn_background(['synthetic-python', 'quorum.py'], ledger)
                flags.assert_called_once_with()
                self.assertEqual(popen.call_args.kwargs['creationflags'], marker['creationflags'])
                self.assertNotIn('start_new_session', popen.call_args.kwargs)
                self.assertEqual(popen.call_args.kwargs['stdin'], subprocess.DEVNULL)
            finally: ledger.db.close()
        import quorum_session
        self.assertIs(quorum_session.spawn_background, quorum_listener.spawn_background)
        source = (SCRIPTS / 'quorum_session.py').read_text() + (SCRIPTS / 'quorum_listener.py').read_text()
        self.assertNotIn('DETACHED_PROCESS', source)

    def test_pilot_handler_keeps_suspended_job_launch_and_adds_no_window(self):
        import quorum_acp
        from quorum_state import SessionBinding
        with tempfile.TemporaryDirectory() as temporary:
            runner = quorum_acp.ClaudePilotRunner.__new__(quorum_acp.ClaudePilotRunner)
            runner.root, runner.node, runner.claude = Path(temporary), 'synthetic-node', 'synthetic-claude'
            runner.adapter, runner.acpx = Path(temporary) / 'adapter.js', Path(temporary) / 'acpx.js'
            runner.binding_scope = {'quorum_id': 'q', 'agent_id': 'a', 'device_id': 'd',
                                    'project_ref': 'quorum-pilot/messaging', 'cwd': temporary}
            runner.preflight = lambda _: None
            binding = SessionBinding('q', 'quorum-pilot/messaging', 'd', 'a', 'c', 'claude', temporary, 'main')
            with patch.object(quorum_os, 'background_flags', return_value={'synthetic': True}) as flags, \
                 patch.object(subprocess, 'Popen', side_effect=OSError('not launched')) as popen:
                with self.assertRaises(OSError):
                    runner.start(binding, 'ping', quorum_acp.ExecutionPolicy(1, 60, 'subscription', True, True))
            flags.assert_called_once_with(SUSPENDED)
            self.assertTrue(popen.call_args.kwargs['synthetic'])


def launcher_python(root):
    """Create a venv whose Scripts/python.exe is a launcher, preferring uv's."""
    import shutil
    target, uv = root / 'launcher-venv', shutil.which('uv')
    env = dict(os.environ, UV_PYTHON_DOWNLOADS='never', UV_OFFLINE='1', UV_NO_CONFIG='1')
    command = ([uv, 'venv', '--quiet', '--python', sys.executable, str(target)] if uv
               else [sys.executable, '-m', 'venv', '--without-pip', str(target)])
    subprocess.run(command, env=env, check=True, capture_output=True, timeout=120, creationflags=NO_WINDOW)
    return target / 'Scripts' / 'python.exe', 'uv' if uv else 'venv'


def wait_for(path, timeout=60):
    import time
    deadline = time.monotonic() + timeout
    while not path.exists():
        if time.monotonic() > deadline: raise AssertionError('probe did not report: ' + path.name)
        time.sleep(.1)
    return json.loads(path.read_text())


def kernel32():
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
    kernel.IsProcessInJob.argtypes = [wintypes.HANDLE, wintypes.HANDLE, ctypes.POINTER(wintypes.BOOL)]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    return kernel


@unittest.skipUnless(os.name == 'nt', 'native Windows launcher and console windows')
class WindowsLauncherConsoleTests(unittest.TestCase):
    def assert_windowless(self, report, launcher):
        same = lambda a, b: os.path.normcase(os.path.realpath(a)) == os.path.normcase(os.path.realpath(b))
        # The venv/uv Scripts/python.exe launcher started a separate interpreter.
        self.assertTrue(same(report['parent_image'], launcher), report)
        self.assertFalse(same(report['image'], launcher), report)
        # No console window handle at all (not merely an unshown one), here and
        # in a descendant started with default flags.
        self.assertEqual((report['console'], report['visible'], report['descendant_console']), (0, 0, 0), report)

    def test_listener_launch_through_uv_launcher_has_no_visible_console_and_outlives_starter(self):
        with tempfile.TemporaryDirectory(prefix='quorum launcher ') as temporary:
            root = Path(temporary)
            launcher, kind = launcher_python(root)
            report, done, stop = root / 'report.json', root / 'done.json', root / 'stop'
            # A short-lived starter process uses the production spawn path, then exits.
            starter = subprocess.run([sys.executable, __file__, '--spawn', str(root), str(launcher)],
                                     capture_output=True, text=True, timeout=120, creationflags=NO_WINDOW)
            self.assertEqual(starter.returncode, 0, starter.stdout + starter.stderr)
            kernel, pid = kernel32(), int(starter.stdout)
            # Exact handle (SYNCHRONIZE | TERMINATE | QUERY_LIMITED): no PID reuse in cleanup.
            handle = kernel.OpenProcess(0x101001, False, pid)
            try:
                result = wait_for(report)
                self.assert_windowless(result, launcher)
                # Still running after its starter exited: the launcher is this
                # interpreter's parent, and it stops only on request.
                self.assertEqual(result['parent_pid'], pid)
                self.assertFalse(done.exists())
                # A failed start terminates its exact Popen child: the launcher.
                # The interpreter it started must not survive as an orphan.
                interpreter = kernel.OpenProcess(0x101000, False, result['pid'])
                self.assertTrue(handle and interpreter)
                try:
                    self.assertTrue(kernel.TerminateProcess(handle, 1))
                    self.assertEqual(kernel.WaitForSingleObject(interpreter, 10000), 0, 'interpreter outlived its launcher')
                finally: kernel.CloseHandle(interpreter)
                self.assertFalse(done.exists())
            finally:
                stop.write_text('stop')
                if handle:
                    if kernel.WaitForSingleObject(handle, 15000) != 0: kernel.TerminateProcess(handle, 1)
                    kernel.CloseHandle(handle)
            print('\nlauncher', kind, launcher, json.dumps(result))

    def test_suspended_pilot_handler_launch_stays_windowless_inside_its_job(self):
        import ctypes
        from ctypes import wintypes
        kernel = kernel32()
        with tempfile.TemporaryDirectory(prefix='quorum handler ') as temporary:
            root = Path(temporary)
            launcher, kind = launcher_python(root)
            report, stop = root / 'report.json', root / 'stop'
            process = subprocess.Popen([str(launcher), __file__, '--probe', str(report), str(stop), str(root / 'done.json')],
                                       stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                       **quorum_os.background_flags(SUSPENDED))
            job = None
            try:
                job = quorum_os.WindowsJob(process)
                job.resume(process)
                result = wait_for(report)
                self.assert_windowless(result, launcher)
                self.assertEqual(result['parent_pid'], process.pid)
                handle = kernel.OpenProcess(0x101000, False, result['pid'])  # SYNCHRONIZE | QUERY_LIMITED
                self.assertTrue(handle)
                try:
                    inside = wintypes.BOOL()
                    self.assertTrue(kernel.IsProcessInJob(handle, job.handle, ctypes.byref(inside)))
                    self.assertTrue(inside.value, 'interpreter escaped the handler job')
                    job.close()
                    self.assertEqual(kernel.WaitForSingleObject(handle, 5000), 0, 'job close left the interpreter running')
                finally: kernel.CloseHandle(handle)
                self.assertIsNotNone(process.wait(timeout=5))
            finally:
                if job: job.close()
                if process.poll() is None:
                    process.kill()
                    process.wait(timeout=5)
            print('\nhandler', kind, launcher, json.dumps(result))

    @unittest.skipUnless(os.environ.get('QUORUM_WINDOWS_NEGATIVE_CONTROL') == '1', 'opt-in: briefly shows a window')
    def test_negative_control_detached_launcher_child_gets_a_console_window(self):
        with tempfile.TemporaryDirectory(prefix='quorum control ') as temporary:
            root = Path(temporary)
            launcher, _ = launcher_python(root)
            report, stop = root / 'report.json', root / 'stop'
            process = subprocess.Popen([str(launcher), __file__, '--probe', str(report), str(stop), str(root / 'done.json')],
                                       stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                       creationflags=DETACHED | NEW_GROUP)
            try: result = wait_for(report)
            finally:
                stop.write_text('stop')
                process.wait(timeout=30)
            print('\ncontrol', json.dumps(result))
            # The old DETACHED_PROCESS launch: the interpreter allocates its own console window.
            self.assertEqual(result['console'], 1, result)


def console_probe(report, stop, done):
    """Report this interpreter's console visibility and launcher, then wait."""
    import ctypes
    import time
    from ctypes import wintypes
    kernel, user = ctypes.WinDLL('kernel32', use_last_error=True), ctypes.WinDLL('user32')
    kernel.GetConsoleWindow.restype = wintypes.HWND
    user.IsWindowVisible.argtypes = [wintypes.HWND]
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
                                                  ctypes.POINTER(wintypes.DWORD)]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    def image(pid):
        handle = kernel.OpenProcess(0x1000, False, pid)
        if not handle: return ''
        try:
            size, buffer = wintypes.DWORD(32768), ctypes.create_unicode_buffer(32768)
            return buffer.value if kernel.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)) else ''
        finally: kernel.CloseHandle(handle)
    window = kernel.GetConsoleWindow()
    # A descendant started with default flags inherits this console.
    child = subprocess.run([sys.executable, '-c', 'import ctypes;from ctypes import wintypes as w;'
                            'k=ctypes.WinDLL("kernel32");u=ctypes.WinDLL("user32");'
                            'k.GetConsoleWindow.restype=w.HWND;u.IsWindowVisible.argtypes=[w.HWND];'
                            'h=k.GetConsoleWindow();print(int(bool(h)),int(bool(h and u.IsWindowVisible(h))))'],
                           capture_output=True, text=True, timeout=60)
    console, visible = (int(value) for value in (child.stdout.split() or ['-1', '-1']))
    result = {'pid': os.getpid(), 'image': image(os.getpid()), 'parent_pid': os.getppid(),
              'parent_image': image(os.getppid()),
              'console': int(bool(window)), 'visible': int(bool(window and user.IsWindowVisible(window))),
              'descendant_console': console, 'descendant_visible': visible}
    temporary = Path(str(report) + '.tmp')
    temporary.write_text(json.dumps(result))
    temporary.replace(report)
    deadline = time.monotonic() + 120
    while not Path(stop).exists() and time.monotonic() < deadline: time.sleep(.1)
    Path(done).write_text(json.dumps({'stopped': Path(stop).exists()}))


def spawn_probe(root, launcher):
    """Starter process: the production listener spawn path, then exit at once."""
    import quorum_listener
    from quorum_state import LocalLedger
    ledger = LocalLedger(root / 'state')
    try:
        child = quorum_listener.spawn_background([str(launcher), __file__, '--probe', str(root / 'report.json'),
                                                  str(root / 'stop'), str(root / 'done.json')], ledger)
    finally: ledger.db.close()
    print(child.pid)


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
    elif len(sys.argv) == 5 and sys.argv[1] == '--probe':
        console_probe(*sys.argv[2:])
    elif len(sys.argv) == 4 and sys.argv[1] == '--spawn':
        spawn_probe(Path(sys.argv[2]), sys.argv[3])
    else:
        unittest.main()
