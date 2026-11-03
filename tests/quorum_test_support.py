"""Keep every CLI/profile fixture away from the real OS user's login registry."""
from functools import wraps
import os
from pathlib import Path
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
