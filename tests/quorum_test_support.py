"""Keep every CLI/profile fixture away from the real OS user's login registry."""
from functools import wraps
import os
from pathlib import Path
import tempfile
from unittest.mock import patch


def isolated_profiles(method):
    @wraps(method)
    def run(*args, **kwargs):
        # macOS's /var alias is intentionally not a trusted profile ancestor.
        canonical_temp = str(Path(tempfile.gettempdir()).resolve())
        with patch.object(tempfile, 'tempdir', canonical_temp), tempfile.TemporaryDirectory() as temporary:
            with patch.dict(os.environ, QUORUM_PROFILE_ROOT=str(Path(temporary) / 'machine-profiles')):
                return method(*args, **kwargs)
    return run
