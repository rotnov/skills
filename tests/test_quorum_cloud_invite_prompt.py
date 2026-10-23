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


if __name__ == '__main__': unittest.main()
