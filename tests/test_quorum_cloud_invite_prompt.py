"""Generated invitations route the real hosted parent before spending the invite."""
from pathlib import Path
import sys
import unittest

SCRIPTS = Path(__file__).resolve().parents[1] / 'skills/quorum/scripts'
sys.path.insert(0, str(SCRIPTS))


class CloudInvitationPromptTests(unittest.TestCase):
    def test_all_bootstrap_prompts_prepare_cloud_transport_before_acceptance(self):
        from quorum_api import ServiceConfig
        from quorum_invitations import create_id_invitation, create_open_invitation

        class Api:
            def call(self, op, payload, request_id=None):
                if op == 'quorum_lookup': return {'quorum_id': 'q'}
                if op == 'quorum_check_owner': return True
                return {'invite_id': '11111111-1111-4111-8111-111111111111',
                        'expires_at': '2026-10-23T12:00:00Z'}

        config = ServiceConfig('https://example.com', 'sb_publishable_synthetic')
        github = create_id_invitation(Api(), config, 'team', '123', 'github')
        anonymous = create_open_invitation(Api(), config, 'team', 'open')
        for name, prompt in [('github', github['session_prompt']),
                             ('configured', github['configured_machine_prompt']),
                             ('anonymous', anonymous['session_prompt'])]:
            with self.subTest(name=name):
                self.assertLess(prompt.index('references/grok-cloud.md'), prompt.index('helper accept-id'))
                self.assertLess(prompt.index('--client grok-bot'), prompt.index('helper accept-id'))
                self.assertIn('cloud connect', prompt)
                self.assertIn('cloud start', prompt)
                self.assertIn('cloud status', prompt)
                self.assertIn('Only the original personal parent', prompt)
                self.assertIn('wake hint', prompt)
                self.assertIn('For native clients', prompt)
                self.assertIn('session connect', prompt)
                self.assertIn('Cursor Cloud is not', prompt)
                self.assertNotIn('api2.cursor.sh/automations/webhook/', prompt)

    def test_all_bootstrap_prompts_select_service_agent_before_setup_without_local_hello(self):
        import re
        from quorum_api import ServiceConfig
        from quorum_invitations import create_id_invitation, create_open_invitation

        class Api:
            def call(self, op, payload, request_id=None):
                if op == 'quorum_lookup': return {'quorum_id': 'q'}
                if op == 'quorum_check_owner': return True
                return {'invite_id': '11111111-1111-4111-8111-111111111111',
                        'expires_at': '2026-10-23T12:00:00Z'}

        config = ServiceConfig('https://example.com', 'sb_publishable_synthetic')
        github = create_id_invitation(Api(), config, 'team', '123', 'github')
        anonymous = create_open_invitation(Api(), config, 'team', 'open')
        for name, prompt in [('github', github['session_prompt']),
                             ('configured', github['configured_machine_prompt']),
                             ('anonymous', anonymous['session_prompt'])]:
            with self.subTest(name=name):
                self.assertIn('references/service-agent.md', prompt)
                self.assertLess(prompt.index('references/service-agent.md'), prompt.index('helper accept-id'))
                # A local greeting is not required; the transport Hello grant is unchanged.
                self.assertIsNone(re.search(r'\bsay Hello\b|\bgreet', prompt))
                self.assertIn('Hello transport notices', prompt)
                self.assertIsNone(re.search(r'\b[Ss]how/receipt\b|receive, show', prompt))

    def test_all_bootstrap_prompts_require_a_worker_or_explicit_visible_parent_choice(self):
        from quorum_api import ServiceConfig
        from quorum_invitations import create_id_invitation, create_open_invitation

        class Api:
            def call(self, op, payload, request_id=None):
                if op == 'quorum_lookup': return {'quorum_id': 'q'}
                if op == 'quorum_check_owner': return True
                return {'invite_id': '11111111-1111-4111-8111-111111111111',
                        'expires_at': '2026-10-23T12:00:00Z'}

        config = ServiceConfig('https://example.com', 'sb_publishable_synthetic')
        github = create_id_invitation(Api(), config, 'team', '123', 'github')
        anonymous = create_open_invitation(Api(), config, 'team', 'open')
        for name, prompt in [('github', github['session_prompt']),
                             ('configured', github['configured_machine_prompt']),
                             ('anonymous', anonymous['session_prompt'])]:
            with self.subTest(name=name):
                # The worker route and its exact parent relationship are chosen before the invitation is spent.
                self.assertLess(prompt.index('references/native-inbox.md'), prompt.index('helper accept-id'))
                self.assertLess(prompt.index('--transport tool-inbox --parent-thread-id'), prompt.index('helper accept-id'))
                self.assertIn('native_worker_unavailable (for example ownership_unverified on Windows), keep the invitation unused', prompt)
                self.assertIn('--require-worker-inbox', prompt)
                # No worker: start the native quorum agent, else one blocker; parent reception is never suggested.
                self.assertIn('follow the no-worker route below', prompt)
                self.assertIn('Quorum is always handled by the native quorum service agent', prompt)
                self.assertIn('out of sight while this chat gets only material results', prompt)
                self.assertIn('create or start it with this client', prompt)
                self.assertIn('keep the invitation unused and report that capability blocker once', prompt)
                self.assertIn('do not suggest visible parent reception, another chat or a replacement session', prompt)
                self.assertIn('legacy opt-in only when I explicitly request it myself for this chat; never offer it', prompt)
                for steering in ('dedicated Quorum session', 'dedicated chat', 'paste this invitation there',
                                 'wait for my explicit choice before any parent fallback',
                                 'only after I explicitly choose'):
                    self.assertNotIn(steering, prompt)
                no_worker = prompt.index('No-worker route')
                self.assertLess(no_worker, prompt.index('helper accept-id'))
                self.assertLess(no_worker, prompt.index('Legacy parent-native opt-in'))
                # Auto mode may block the listener start; the owner approves, nothing works around it.
                self.assertIn('In Claude Code auto mode, session start can be blocked as background persistence', prompt)
                self.assertIn('Bash(uv run *quorum.py*)', prompt)
                self.assertIn('never work around the block', prompt)
                self.assertIn('session receive', prompt)
                self.assertIn('never runs session start as a background listener', prompt)
                self.assertIn('not delivery to this chat', prompt)
                self.assertIn('--accept-tasks-from-members --dispatch-mode inline', prompt)
                # The supported parent fallback stays intact and truthfully labelled.
                self.assertIn('then session start and session status', prompt)
                self.assertIn('Never relabel parent reception as a worker inbox', prompt)
                self.assertIn('Only the original personal parent', prompt)
                # Flags belong only to accept/connect, and the worker and parent branches are exclusive.
                self.assertNotIn('every session command with --transport', prompt)
                self.assertIn('only those two commands take these flags', prompt)
                worker = prompt.index('For a proven Codex tool-inbox worker')
                fallback = prompt.index('Legacy parent-native opt-in, only when I explicitly request visible parent reception for this chat myself')
                self.assertLess(worker, fallback)
                self.assertIn('the parent fallback below then does not apply', prompt[worker:fallback])
                self.assertNotIn('session start', prompt[worker:fallback].replace('never runs session start', ''))


    def test_references_route_a_workerless_join_to_the_agent_or_a_blocker(self):
        root = Path(__file__).resolve().parents[1] / 'skills/quorum'
        join = ' '.join((root / 'references/join.md').read_text().split())
        section = join[join.index('## Without a worker'):]
        for required in ('Quorum is always handled by the native `quorum`', 'out of sight',
                         'create or start it', 'keep the invitation unused and report that capability blocker once',
                         'Do not suggest visible parent reception, another chat or a replacement session',
                         "never start a headless model, a model CLI or a different provider's client",
                         'legacy opt-in', 'Never offer it as an option',
                         'In Claude Code auto mode, `session start` can be blocked as background persistence',
                         '`Bash(uv run *quorum.py*)`', 'never work around the block'):
            self.assertIn(required, section)
        self.assertNotIn('dedicated chat', section)
        self.assertIn('never fall back to parent reception', join)
        for name in ('service-agent.md', 'claude-worker.md', 'native-inbox.md', 'invite.md', 'sessions.md'):
            with self.subTest(reference=name):
                text = ' '.join((root / 'references' / name).read_text().split())
                self.assertIn('join.md#without-a-worker', text)
                self.assertNotIn("owner's explicit choice before retrying", text)
                self.assertNotIn('wait for the owner\'s explicit route choice', text)
                self.assertNotIn('dedicated Quorum chat', text)
        skill = ' '.join((root / 'SKILL.md').read_text().split())
        self.assertIn('references/join.md#without-a-worker', skill)
        self.assertNotIn("requires the owner's explicit choice", skill)
        self.assertIn('never suggest parent reception', skill)


if __name__ == '__main__': unittest.main()
