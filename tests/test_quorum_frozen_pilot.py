"""The headless handler, ACP runners and work tracker are a frozen legacy fallback (#165, review step 1).

Frozen means fixes only: no new caller, command or state. This file keeps the set of code that reaches the frozen
modules and RPCs exactly what it was, and keeps the docs that say so and name the guarding tests in place.
"""
import hang_guard  # noqa: F401
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / 'skills/quorum'
SCRIPTS = SKILL / 'scripts'
FROZEN_MODULES = ('quorum_worker', 'quorum_acp')
FROZEN_RPCS = ('inbox_claim', 'attempt_update', 'delivery_complete', 'work_register', 'work_admit', 'work_transition')
# Every script outside the frozen modules that names them, and why. A new entry is a new caller: refuse it.
CALLERS = {
    'quorum.py': {'work_register'},                          # the `work` command
    'quorum_api.py': set(FROZEN_RPCS),                        # the named-RPC allowlist
    'quorum_doctor.py': {'quorum_acp'},                       # reports the engine pins as not accepted
    'quorum_live.py': {'quorum_acp', 'quorum_worker'},        # prepare_worker behind pilot-worker and listen
    'quorum_listener.py': {'quorum_acp'},                     # the listen start/run policy check
}
GUARDS = ('test_quorum_worker.py', 'test_quorum_live.py', 'test_quorum_cancel.py', 'test_quorum_windows_windowless.py',
          'test_quorum_release_alternate.py', 'test_quorum_listener.py', 'test_quorum_stop_during_start.py',
          'test_quorum_capabilities.py')


class FrozenPilotTests(unittest.TestCase):
    def test_no_new_code_reaches_the_frozen_modules_or_rpcs(self):
        found = {}
        for path in sorted(SCRIPTS.glob('*.py')):
            if path.stem in FROZEN_MODULES: continue
            text = path.read_text(encoding='utf-8')
            names = {name for name in FROZEN_MODULES + FROZEN_RPCS if re.search(r'\b' + name + r'\b', text)}
            if names: found[path.name] = names
        self.assertEqual(found, CALLERS)

    def test_the_frozen_code_says_so(self):
        for name in FROZEN_MODULES:
            doc = (SCRIPTS / f'{name}.py').read_text(encoding='utf-8').split('"""')[1]
            self.assertIn('FROZEN legacy fallback (references/protocol.md#frozen-historical-pilot)', doc, name)
        listener = (SCRIPTS / 'quorum_listener.py').read_text(encoding='utf-8')
        marker = listener.index('# FROZEN legacy fallback: start_listener, add_commands and dispatch')
        self.assertLess(marker, listener.index('def start_listener('))
        self.assertGreater(listener.index('def run_listener('), 0)
        self.assertLess(listener.index('def run_listener('), marker)
        live = (SCRIPTS / 'quorum_live.py').read_text(encoding='utf-8')
        for anchor in ('def prepare_worker(', "    worker = commands.add_parser('pilot-worker'"):
            before = live[:live.index(anchor)].rstrip().splitlines()[-1]
            self.assertIn('FROZEN legacy fallback', before, anchor)

    def test_the_docs_name_the_fallback_and_every_guarding_test(self):
        protocol = (SKILL / 'references/protocol.md').read_text(encoding='utf-8')
        section = protocol.split('## Frozen historical pilot', 1)[1].split('\n## ', 1)[0]
        for test in GUARDS:
            self.assertIn(f'`{test}`', section)
            self.assertTrue((ROOT / 'tests' / test).is_file(), test)
        self.assertIn('`tests/test_quorum_frozen_pilot.py`', section)
        for name in ('listener.md', 'pilot.md'):
            text = (SKILL / 'references' / name).read_text(encoding='utf-8')
            self.assertIn('Frozen legacy fallback: this path gets fixes only, no new features.', text.split('\n## ', 1)[0])
        skill = (SKILL / 'SKILL.md').read_text(encoding='utf-8')
        self.assertEqual(skill.count('frozen legacy fallback'), 2)


if __name__ == '__main__':
    unittest.main()
