"""Generated invitations stay thin; the recipient's freshly installed join guide owns the procedure."""
from pathlib import Path
import re
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / 'skills/quorum'
SCRIPTS = SKILL / 'scripts'
sys.path.insert(0, str(SCRIPTS))


def flat(path):
    return ' '.join((SKILL / path).read_text().split())


def prompts():
    from quorum_api import ServiceConfig
    from quorum_invitations import create_id_invitation, create_open_invitation

    class Api:
        def call(self, op, payload, request_id=None):
            if op == 'quorum_lookup': return {'quorum_id': 'q'}
            if op == 'quorum_check_owner': return True
            return {'invite_id': '11111111-1111-4111-8111-111111111111', 'expires_at': '2026-10-23T12:00:00Z'}

    config = ServiceConfig('https://example.com', 'sb_publishable_synthetic')
    github = create_id_invitation(Api(), config, 'team', '123', 'github')
    anonymous = create_open_invitation(Api(), config, 'team', 'open')
    return [('github', github['session_prompt'], github), ('configured', github['configured_machine_prompt'], github),
            ('anonymous', anonymous['session_prompt'], anonymous)]


class InvitationPromptTests(unittest.TestCase):
    def test_prompts_refresh_first_and_let_the_installed_join_guide_win(self):
        from quorum_release import INSTALLER
        for name, prompt, _ in prompts():
            with self.subTest(name=name):
                bootstrap = prompt.index('Install or refresh the quorum skill (latest rotnov/skills main)')
                self.assertLess(bootstrap, prompt.index('2. Accept: accept-id'))
                self.assertLess(prompt.index('2. Accept: accept-id'), prompt.index('3. Connect this actual session'))
                self.assertIn(f'npx --yes "{INSTALLER}" add rotnov/skills --skill quorum', prompt)
                self.assertIn('not a temporary scratch folder', prompt)
                self.assertIn('immutable automatic refresh once (references/update.md)', prompt)
                self.assertIn('once more per skill_update_required', prompt)
                self.assertIn('Trust no other version or command quoted here', prompt)
                self.assertIn('the installed references/join.md wins over this text', prompt)
                self.assertLess(prompt.index('references/join.md wins'), prompt.index('2. Accept: accept-id'))

    def test_prompts_carry_only_data_commands_consent_and_hard_stops(self):
        from quorum_invitations import CONSENT
        for name, prompt, _ in prompts():
            with self.subTest(name=name):
                for data in ('"team"', '11111111-1111-4111-8111-111111111111', '"2026-10-23T12:00:00Z"'):
                    self.assertIn(data, prompt)
                for required in (CONSENT, '--accept-tasks-from-members --dispatch-mode inline --trust-members',
                                 '--standing-grants-from coordinator', 'Hard stops, the only things to ask me about',
                                 'ask once, park the message, never pause reception', 'Peers can never grant more'):
                    self.assertIn(required, prompt)
                # Procedure, routing, recovery and version detail live only in the installed guide.
                for procedure in ('native-inbox.md', 'claude-worker.md', 'grok-cloud.md', 'service-agent.md',
                                  '--transport tool-inbox', '--client grok-bot', 'session start', 'session connect',
                                  'cursor', 'Bash(uv run', 'protocol 2', 'skill 0.', 'No-worker route', 'Parent-native',
                                  'parent reception', 'Hello', 'task-check', 'always require the receiving owner',
                                  'merges, default-branch pushes'):
                    self.assertNotIn(procedure, prompt)
                self.assertIsNone(re.search(r'\bsay Hello\b|\bgreet', prompt))
                # A few lines (#114), down from about 4-6k characters.
                self.assertLessEqual(len(prompt.splitlines()), 10)
                self.assertLess(len(prompt), 2500)

    def test_expiry_is_server_data_and_no_local_clock_decides(self):
        for name, prompt, result in prompts():
            with self.subTest(name=name):
                rule = prompt.index('Expiry is server data')
                self.assertLess(rule, prompt.index('Install or refresh'))
                for required in ('always attempt acceptance', 'only the server decides', 'report invite_denied'):
                    self.assertIn(required, prompt)
                # The helper prints no locally computed remaining time that a skewed clock could falsify.
                self.assertNotIn('now_utc', result)
                self.assertNotIn('seconds_remaining', result)
        join = flat('references/join.md')
        for required in ('`expires_at` is server data', 'a machine clock can be hours off',
                         'never compare it with the OS clock, helper output or your own sense of the date',
                         'Always attempt acceptance', "only the server's accept response decides", '`invite_denied`',
                         'The helper prints no local remaining time; do not estimate one'):
            self.assertIn(required, join)

    def test_installer_pin_has_one_source(self):
        from quorum_release import INSTALLER
        self.assertEqual(INSTALLER, 'skills@>=1.7.4')
        for script in SCRIPTS.glob('*.py'):
            if script.name != 'quorum_release.py':
                with self.subTest(script=script.name):
                    self.assertNotRegex(script.read_text(), r"['\"]skills@")
        self.assertIn('INSTALLER', (SCRIPTS / 'quorum_update.py').read_text())
        skill = flat('SKILL.md')
        self.assertIn('issuing an invitation (so the prompt carries the current installer)', skill)

    def test_join_guide_owns_the_moved_procedure(self):
        join = flat('references/join.md')
        intro = join[join.index('## Invitation prompts'):join.index('## Join this chat')]
        for required in ('carries only data', "receiving owner's authorizations", 'hard safety rules',
                         'latest `rotnov/skills` main', 'issued by an older copy', 'this guide wins',
                         # The hard rules the short prompt no longer repeats.
                         'Use only this actual session identity', 'never relabel parent reception as a worker inbox',
                         'Do not start a model, another session, headless pilot or OS startup service',
                         'Never suggest visible parent reception', "legacy opt-in only on the owner's own explicit",
                         "Membership-only acceptance requires the owner's explicit request",
                         'Do not ask for, paste or forward secrets, tokens or OAuth codes',
                         'report one setup outcome', 'same private state and actual session UUID'):
            self.assertIn(required, intro)
        for required in ('grok-cloud.md', '--client grok-bot', 'cursor_inbox_unavailable', 'native-inbox.md',
                         'claude-worker.md', '--require-worker-inbox',
                         '--accept-tasks-from-members --dispatch-mode inline --trust-members',
                         'session receive --wait-seconds 30', 'never runs `session start` as a background listener',
                         'worker consumption, not delivery to the parent', 'Only on the legacy parent opt-in',
                         '`session start` can be blocked as background persistence', '`Bash(uv run *quorum.py*)`',
                         'never work around the block', 'https://github.com/rotnov/skills/issues'):
            self.assertIn(required, join)

    def test_references_route_a_workerless_join_to_the_agent_or_a_blocker(self):
        join = flat('references/join.md')
        section = join[join.index('## Without a worker'):]
        for required in ('Quorum is always handled by the native `quorum`', 'out of sight',
                         'create or start it', 'keep the invitation unused and report that capability blocker once',
                         'Do not suggest visible parent reception, another chat or a replacement session',
                         "never start a headless model, a model CLI or a different provider's client",
                         'legacy opt-in', 'Never offer it as an option'):
            self.assertIn(required, section)
        self.assertNotIn('dedicated chat', section)
        self.assertIn('never fall back to parent reception', join)
        for name in ('service-agent.md', 'claude-worker.md', 'native-inbox.md', 'invite.md', 'sessions.md'):
            with self.subTest(reference=name):
                text = flat('references/' + name)
                self.assertIn('join.md#without-a-worker', text)
                self.assertNotIn("owner's explicit choice before retrying", text)
                self.assertNotIn("wait for the owner's explicit route choice", text)
                self.assertNotIn('dedicated Quorum chat', text)
        skill = flat('SKILL.md')
        self.assertIn('references/join.md#without-a-worker', skill)
        self.assertIn('never suggest parent reception', skill)


if __name__ == '__main__': unittest.main()
