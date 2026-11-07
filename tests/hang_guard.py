"""Keep a hung or runaway test from outliving its run or eating the machine (#176).

Every test module imports this: `discover -s tests` makes tests/ the top level, so unittest never imports a
tests/__init__.py, and `-p` or a named module would skip a hook kept in any one test file. test_hang_guard
enforces the import. Nothing starts until a suite or test runs, so a test module run as a probe script stays
unguarded.

Four limits, each reached before the failure of 2026-11-06 could recur:
- Time: no test, class fixture or exit may hold the run for TEST_SECONDS between test boundaries.
- Listener passes: a listener whose fake never reaches its stop condition spins on SQLite while the fake records
  every call. Each listener loop reports through Control.update, so counting there stops any such test, written
  or future, and bounds what its fakes can record. Unlike a limit on time, it stops the loop as a test failure.
- Memory: a run past MEMORY_BYTES of peak resident memory ends. POSIX only.
- The caller: a run ends once any process above it has, past launchers such as `uv run`. Windows included.

A process that ends here also kills the processes below it, POSIX only, so a child cannot outlive it. If a test
holds the GIL no Python thread can do that and the C timer exits with no cleanup, so a watchdog process does it
first, while the parent links still exist. It reads a tick per test boundary from a pipe; end of file means the run
has left and it does nothing.
"""
import faulthandler
from functools import wraps
import importlib.machinery
import importlib.util
import os
from pathlib import Path
import select
import signal
import subprocess
import sys
import threading
import time
import unittest

try: import resource
except ImportError: resource = None

TEST_SECONDS = 300  # Slowest test measured 9 s; each whole CI Docker step took at most 25 s, a Windows step 97 s.
EXIT_GRACE_SECONDS = 30  # The C-level timer outlasts the thread, which cannot run if a test holds the GIL.
CHECK_SECONDS = 2.0
MEMORY_BYTES = 2 * 1024 ** 3  # The full suite peaks at 388 MiB.
LISTENER_PASSES = 1000  # The busiest honest test makes 62.
SCRIPTS = Path(__file__).resolve().parents[1] / 'skills/quorum/scripts'

# Tests patch all of these; a guard that read them late would end a healthy run on a mock.
now, kill, snapshot, send = time.monotonic, os.kill, subprocess.run, os.write
POSIX, RSS_UNIT = os.name != 'nt', 1 if sys.platform == 'darwin' else 1024
started, ticked_at, passes, heartbeat, watchdog_process = False, 0.0, 0, None, None


class RunawayLoop(AssertionError):
    """A test's listener loop ran past LISTENER_PASSES, so its fake never reached a stop condition."""


def counted(update):
    @wraps(update)
    def counting(*args, **kwargs):
        global passes
        if started:
            passes += 1
            if passes > LISTENER_PASSES:
                raise RunawayLoop(f'{LISTENER_PASSES} listener status writes in one test: no stop condition was reached')
        return update(*args, **kwargs)
    return counting


def count_passes(module):
    if not hasattr(module.Control.update, '__wrapped__'): module.Control.update = counted(module.Control.update)


class ListenerLoads:
    """Count passes in whichever quorum_listener a test loads.

    The guard must never import it itself: a copy-install test proves its quorum modules come only from its copy.
    """
    @staticmethod
    def find_spec(name, path=None, target=None):
        if name != 'quorum_listener': return None
        spec = importlib.machinery.PathFinder.find_spec(name, path, target)
        if spec is None: return None
        load = spec.loader.exec_module
        def exec_module(module):
            load(module)
            count_passes(module)
        spec.loader.exec_module = exec_module
        return spec


def kill_descendants(root=None):
    """Only processes below `root`, by a parent table read now; never a match on a command line."""
    if not POSIX: return
    root = root or os.getpid()
    try:
        listing = snapshot(['ps', '-A', '-o', 'pid=,ppid='], capture_output=True, text=True, timeout=5).stdout
        children = {}
        for line in listing.splitlines():
            pid, parent = map(int, line.split())
            children.setdefault(parent, []).append(pid)
    except (OSError, ValueError, subprocess.SubprocessError): return
    pending = list(children.get(root, ()))
    while pending:
        pid = pending.pop()
        pending.extend(children.get(pid, ()))
        if pid == os.getpid(): continue  # The watchdog is below the run it sweeps.
        try: kill(pid, signal.SIGKILL)
        except OSError: pass


def die(reason):
    print(f'hang_guard: ending the test run: {reason}', file=sys.__stderr__, flush=True)
    faulthandler.dump_traceback(file=sys.__stderr__)
    kill_descendants()
    os._exit(1)


def peak_bytes():
    if resource is None: return 0
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * RSS_UNIT


def caller_gone(probes):
    try: return any(probe() for probe in probes)
    except Exception: return False


def watch(probes):
    idle, suspect = threading.Event(), False
    while not idle.wait(CHECK_SECONDS):
        if now() - ticked_at > TEST_SECONDS: die(f'no test boundary for {TEST_SECONDS} s')
        if peak_bytes() > MEMORY_BYTES: die(f'peak memory passed {MEMORY_BYTES} bytes')
        # Two readings apart: a test may mock os.kill or os.getppid, and the probes read those.
        gone = caller_gone(probes)
        if gone and suspect: die('the caller of this run has ended')
        suspect = gone


def ancestor_probes():
    """From the repository's copy, under a private name, for the same reason as ListenerLoads."""
    spec = importlib.util.spec_from_file_location('hang_guard_os', SCRIPTS / 'quorum_os.py')
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module.ancestor_probes()


def watchdog(run, limit):
    """Separate process: sweep below a run that stopped ticking, before its C timer ends it and the links go."""
    last = now()
    while now() - last < limit:
        if select.select([0], [], [], max(0, limit - (now() - last)))[0]:
            if not os.read(0, 4096): return
            last = now()
    kill_descendants(run)


def start_watchdog():
    global heartbeat, watchdog_process
    if not POSIX: return
    reader, heartbeat = os.pipe()
    os.set_blocking(heartbeat, False)
    # Halfway through the grace period; a session of its own keeps it out of any signal sent to the run's group.
    limit = TEST_SECONDS + EXIT_GRACE_SECONDS / 2
    watchdog_process = subprocess.Popen([sys.executable, '-I', '-S', __file__, str(os.getpid()), str(limit)],
                                        stdin=reader, stdout=subprocess.DEVNULL, start_new_session=True)
    os.close(reader)


def start():
    global started
    started = True
    start_watchdog()
    threading.Thread(target=watch, args=(ancestor_probes(),), name='hang-guard', daemon=True).start()


def tick():
    """A test boundary: the time and pass limits both count from here."""
    global ticked_at, passes
    if not started: start()
    ticked_at, passes = now(), 0
    faulthandler.dump_traceback_later(TEST_SECONDS + EXIT_GRACE_SECONDS, exit=True, file=sys.__stderr__)
    if heartbeat is not None:
        try: send(heartbeat, b'.')
        except OSError: pass  # A full pipe already holds unread ticks; a closed one has no watchdog to tell.


def guarded(run):
    @wraps(run)
    def bounded(self, *args, **kwargs):
        tick()
        try: return run(self, *args, **kwargs)
        finally: tick()
    return bounded


unittest.TestCase.run = guarded(unittest.TestCase.run)
unittest.TestSuite.run = guarded(unittest.TestSuite.run)
sys.meta_path.insert(0, ListenerLoads)
if 'quorum_listener' in sys.modules: count_passes(sys.modules['quorum_listener'])

if __name__ == '__main__': watchdog(int(sys.argv[1]), float(sys.argv[2]))
