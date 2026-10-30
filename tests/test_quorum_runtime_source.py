"""A resident run's source identity is distinct from a fresh helper's files."""
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / 'skills/quorum/scripts'
sys.path.insert(0, str(SCRIPTS))
SOURCE = {'protocol_version': 2, 'skill_version': '0.3.0', 'content_sha256': 'a' * 64}


class RuntimeSourceTests(unittest.TestCase):
    def test_resident_source_snapshot_survives_changed_files(self):
        import quorum_release
        self.assertTrue(callable(getattr(quorum_release, 'installed_source', None)),
                        'a process source snapshot is required')
        with tempfile.TemporaryDirectory() as root:
            skill = Path(root) / 'quorum'
            shutil.copytree(SCRIPTS.parent, skill, ignore=shutil.ignore_patterns('.venv', '__pycache__'))
            script = ("import json; from pathlib import Path; from quorum_release import installed_source; "
                      "first=installed_source(); Path('../new-source.txt').write_text('changed'); "
                      "print(json.dumps([first,installed_source()]))")
            result = subprocess.run([sys.executable, '-B', '-c', script], cwd=skill/'scripts',
                                    capture_output=True, text=True, timeout=20)
            self.assertEqual(result.returncode, 0, result.stderr)
            before, after = json.loads(result.stdout)
            self.assertEqual(before, after)
            self.assertRegex(before['content_sha256'], '^[0-9a-f]{64}$')
            fresh = subprocess.run([sys.executable, '-B', '-c',
                                    'import json; from quorum_release import installed_source; '
                                    'print(json.dumps(installed_source()))'], cwd=skill/'scripts',
                                   capture_output=True, text=True, timeout=20)
            self.assertNotEqual(json.loads(fresh.stdout)['content_sha256'], before['content_sha256'])

    def test_winning_control_publishes_source_and_reports_only_that_run(self):
        from quorum_listener import Control
        from quorum_os import FileLock
        from quorum_state import LocalLedger
        with tempfile.TemporaryDirectory() as root:
            ledger = LocalLedger(Path(root))
            try:
                with patch('quorum_release.installed_source', return_value=SOURCE, create=True), \
                        FileLock(ledger.root/'listener.lock'):
                    control = Control(ledger)
                    control.update('winner', 'waiting', 0, release={'protocol_version':2,'skill_version':'0.3.0'})
                    self.assertEqual(ledger.setting('listener-source'), {**SOURCE, 'run_id':'winner'})
                    active = control.status()['runtime_source']
                    self.assertTrue(active['verified'])
                    self.assertEqual((active['state'], active['run_id'], active['source']),
                                     ('running', 'winner', SOURCE))
                self.assertEqual(control.status()['runtime_source']['state'], 'offline')
            finally: ledger.db.close()

    def test_current_files_never_bless_a_missing_or_foreign_run_marker(self):
        from quorum_listener import Control
        from quorum_os import FileLock
        from quorum_state import LocalLedger
        with tempfile.TemporaryDirectory() as root:
            ledger = LocalLedger(Path(root))
            try:
                ledger.setting('listener', {'run_id':'real', 'state':'waiting'})
                for marker in (None, {**SOURCE, 'run_id':'old'}):
                    if marker is not None: ledger.setting('listener-source', marker)
                    with FileLock(ledger.root/'listener.lock'):
                        evidence = Control(ledger).status().get('runtime_source')
                    self.assertIsNotNone(evidence, 'status must distinguish missing runtime evidence')
                    self.assertFalse(evidence['verified'])
                    self.assertIsNone(evidence['source'])
            finally: ledger.db.close()
