"""Opt-in suites skip by default, so a suite no CI step enables could never fail."""
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = (ROOT / '.github/workflows/validate.yml').read_text(encoding='utf-8')
GATE = re.compile(r"environ\.get\('(QUORUM_[A-Z]+_DOCKER)'\)")


def steps():
    """Each workflow step's text, split on its `- name:` line."""
    return re.split(r'^\s*- name: ', WORKFLOW, flags=re.M)[1:]


class OptInSuiteTests(unittest.TestCase):
    def test_every_opt_in_docker_suite_runs_in_a_ci_step_that_enables_it(self):
        gated = {path: GATE.findall(path.read_text(encoding='utf-8')) for path in sorted(ROOT.glob('tests/test_quorum_*.py'))
                 if path != Path(__file__).resolve()}
        gated = {path: set(names) for path, names in gated.items() if names}
        self.assertGreaterEqual(len(gated), 8)
        for path, names in gated.items():
            for name in names:
                with self.subTest(suite=path.name, gate=name):
                    self.assertTrue([step for step in steps() if re.search(name + r': "1"', step) and path.stem in step],
                                    'no CI step sets the gate and runs the suite, so it silently never runs')

    def test_the_gate_is_found_in_a_step_only_when_both_are_present(self):
        self.assertTrue(GATE.findall("os.environ.get('QUORUM_DEDUPE_DOCKER') == '1'"))
        self.assertFalse([step for step in steps() if 'QUORUM_NEVER_DOCKER: "1"' in step])


if __name__ == '__main__':
    unittest.main()
