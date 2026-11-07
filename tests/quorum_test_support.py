"""Keep every CLI/profile fixture away from the real OS user's login registry."""
from functools import wraps
import json
import os
from pathlib import Path
import subprocess
import tempfile
from unittest.mock import patch


def anchor_owner_proof_fixtures():
    """On Windows, keep temporary fixtures under the user profile, like the real state and Codex homes.

    %TEMP% commonly grants other accounts (sandbox users) Modify, which the ancestor proof rightly
    refuses, just as POSIX refuses a non-sticky shared directory. POSIX keeps the system default.
    """
    if os.name != 'nt': return
    import atexit
    base = Path.home() / '.quorum-test-tmp'
    base.mkdir(exist_ok=True)
    tempfile.tempdir = str(base.resolve())
    def remove():
        try: base.rmdir()  # Only when empty; another concurrent run may still be using it.
        except OSError: pass
    atexit.register(remove)


def isolated_profiles(method):
    @wraps(method)
    def run(*args, **kwargs):
        # macOS's /var alias is intentionally not a trusted profile ancestor.
        canonical_temp = str(Path(tempfile.gettempdir()).resolve())
        with patch.object(tempfile, 'tempdir', canonical_temp), tempfile.TemporaryDirectory() as temporary:
            with patch.dict(os.environ, QUORUM_PROFILE_ROOT=str(Path(temporary) / 'machine-profiles')):
                return method(*args, **kwargs)
    return run


CONTRACT_FIXTURES = json.loads((Path(__file__).parent / 'fixtures/client-contracts.json').read_text(encoding='utf-8'))


QUEUE_HELP = CONTRACT_FIXTURES['codex_queue']['help']['stdout'].encode()


def queue_accepted(thread):
    """The recorded `codex queue` success line for this thread."""
    return ('Queued message 01a1b23b-83f8-74e1-b1df-91544014a099 for thread ' + thread + '.\n').encode()


def codex_child(seen=None, *, probes=None, thread=None, output=None, result=None):
    """A `run_child` stand-in for a qualified Codex: `queue --help`, then a queue that accepts or answers `result`.

    `seen` collects the argv of each queue send and `probes` that of each help read. No client or model runs.
    """
    def run(command, **_):
        if command[-2:] == ['queue', '--help']:
            if probes is not None: probes.append(command)
            return subprocess.CompletedProcess(command, 0, QUEUE_HELP, b'')
        if seen is not None: seen.append(command)
        if result is not None: return result
        accepted = queue_accepted(thread or command[-3])
        return subprocess.CompletedProcess(command, 0, accepted if output is None else output, b'')
    return run
