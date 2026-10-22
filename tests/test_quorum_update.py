"""Refresh code copies without replacing active installations or private state."""
import contextlib
import importlib
import io
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / 'skills/quorum/scripts'
sys.path.insert(0, str(SCRIPTS))


class UpdateTests(unittest.TestCase):
    def setUp(self):
        self.module = importlib.import_module('quorum_update')
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.current = self.root / 'installed skill'
        shutil.copytree(SCRIPTS.parent, self.current,
                        ignore=shutil.ignore_patterns('__pycache__', '.venv'))
        self.cache = self.root / 'private updates'
        self.source = self.root / 'public source'
        shutil.copytree(self.current, self.source)
        self.state = self.root / 'private state'
        self.state.mkdir()
        (self.state / 'credentials.json').write_text('owner-private-state')
        (self.current / 'active-reader.txt').write_text('old-code-must-remain')

    def runner(self, command, **kwargs):
        if command[1:4] == ['--yes', 'skills@1.5.20', 'add']:
            self.assertEqual(command[4:], ['rotnov/skills', '--skill', 'quorum', '-a',
                                         'claude-code', 'codex', '--copy', '-y'])
            for client in ('.agents', '.claude'):
                shutil.copytree(self.source, Path(kwargs['cwd']) / client / 'skills/quorum')
            return subprocess.CompletedProcess(command, 0, 'installer log', '')
        if command[1:4] == ['sync', '--frozen', '--project']:
            return subprocess.CompletedProcess(command, 0, '', '')
        self.assertEqual(command[1:4], ['run', '--offline', '--frozen'])
        # Exercise the real copied helper; only installer/package resolution is synthetic.
        marker = command.index('python')
        return subprocess.run([sys.executable, *command[marker + 1:]],
                              cwd=kwargs['cwd'], env=kwargs['env'], stdin=kwargs['stdin'],
                              capture_output=True, text=True, timeout=30)

    def refresh(self, engine='codex', runner=None):
        with patch('quorum_update.shutil.which', side_effect=lambda name: name):
            return self.module.refresh(self.current, self.cache, engine,
                                       run=runner or self.runner)

    def test_changed_code_publishes_both_copies_and_preserves_active_tree_and_state(self):
        (self.source / 'new-version.txt').write_text('new-public-code')
        result = self.refresh()
        self.assertEqual(result['status'], 'updated')
        selected = Path(result['skill_dir'])
        self.assertTrue(selected.is_relative_to(self.cache))
        self.assertEqual((selected / 'new-version.txt').read_text(), 'new-public-code')
        self.assertTrue((selected.parents[2] / '.claude/skills/quorum/SKILL.md').is_file())
        self.assertFalse((self.current / 'new-version.txt').exists())
        self.assertEqual((self.current / 'active-reader.txt').read_text(), 'old-code-must-remain')
        self.assertEqual((self.state / 'credentials.json').read_text(), 'owner-private-state')
        self.assertEqual(list(self.state.iterdir()), [self.state / 'credentials.json'])

    def test_identical_version_reuses_persistent_content_path_for_either_client(self):
        one = self.refresh()
        two = self.refresh('claude')
        self.assertEqual(Path(one['skill_dir']).parents[2], Path(two['skill_dir']).parents[2])
        self.assertEqual(Path(two['skill_dir']).parts[-3:], ('.claude', 'skills', 'quorum'))
        self.assertEqual(len([p for p in self.cache.iterdir() if p.is_dir()]), 1)
        self.current = Path(one['skill_dir'])
        self.assertEqual(self.refresh()['status'], 'current')

    def test_grok_uses_the_shared_agents_copy_without_an_unknown_installer_target(self):
        result = self.refresh('grok')
        selected = Path(result['skill_dir'])
        self.assertEqual(selected.parts[-3:], ('.agents', 'skills', 'quorum'))
        self.assertTrue((selected/'SKILL.md').is_file())

    def test_tool_output_survives_a_non_utf_locale_without_decoding_logs(self):
        # UTF-8 for this character contains 0x90, which cp1252 cannot decode.
        script = ('import os\n'
                  'os.write(1, \'{"message":"\\U0001f610"}\'.encode("utf-8"))\n'
                  'os.write(2, bytes([0x90]))\n')
        with patch('subprocess._text_encoding', return_value='cp1252'):
            result = self.module.checked_run(subprocess.run, [sys.executable, '-c', script],
                                             self.root, dict(os.environ), 'update_fetch_failed', 30)
        self.assertEqual(json.loads(result.stdout), {'message': '\U0001f610'})
        self.assertEqual(result.stderr, b'\x90')

    def test_non_utf_validation_json_is_rejected_without_publishing(self):
        def invalid_status(command, **kwargs):
            if command[1] == 'run':
                # Valid JSON if incorrectly decoded as cp1252, invalid UTF-8.
                raw = b'{"ok":true,"data":{"configured":false,"worker":"offline","text":"\xff"}}'
                return subprocess.run([sys.executable, '-c', 'import os; os.write(1, '+repr(raw)+')'],
                                      **kwargs)
            return self.runner(command, **kwargs)
        with patch('subprocess._text_encoding', return_value='cp1252'):
            with self.assertRaisesRegex(ValueError, '^update_validation_failed$'):
                self.refresh(runner=invalid_status)
        self.assertFalse(any(p.is_dir() for p in self.cache.iterdir()))

    def test_fetch_failure_is_a_blocker_without_partial_publish_or_secret_output(self):
        def failing(command, **kwargs):
            return subprocess.CompletedProcess(command, 1, 'secret-token', 'secret-token')
        with self.assertRaisesRegex(ValueError, '^update_fetch_failed$'):
            self.refresh(runner=failing)
        self.assertEqual((self.current / 'active-reader.txt').read_text(), 'old-code-must-remain')
        self.assertFalse(any(p.is_dir() for p in self.cache.iterdir()))

    def test_missing_tool_blocks_before_network_or_current_tree_changes(self):
        with patch('quorum_update.shutil.which', return_value=None):
            with self.assertRaisesRegex(ValueError, '^update_tool_missing$'):
                self.module.refresh(self.current, self.cache, 'codex',
                                    run=lambda *_args, **_kwargs: self.fail('network call'))
        self.assertFalse(self.cache.exists())

    def test_incomplete_or_different_client_copy_is_never_published(self):
        for corruption in ('missing-lock', 'different-client', 'invalid-python', 'symlink'):
            with self.subTest(corruption=corruption):
                def corrupt(command, **kwargs):
                    result = self.runner(command, **kwargs)
                    if command[1:4] == ['--yes', 'skills@1.5.20', 'add']:
                        target = Path(kwargs['cwd']) / '.claude/skills/quorum'
                        if corruption == 'missing-lock': (target / 'uv.lock').unlink()
                        elif corruption == 'different-client': (target / 'extra').write_text('different')
                        elif corruption == 'invalid-python':
                            for client in ('.agents', '.claude'):
                                (Path(kwargs['cwd']) / client / 'skills/quorum/scripts/broken.py').write_text('def (')
                        else:
                            try: (target / 'external').symlink_to(self.state, target_is_directory=True)
                            except OSError: self.skipTest('host does not permit symlink fixtures')
                    return result
                with self.assertRaisesRegex(ValueError, '^update_invalid_copy$'):
                    self.refresh(runner=corrupt)
                self.assertFalse(any(p.is_dir() for p in self.cache.iterdir()))

    def test_dependency_sync_or_offline_validation_failure_keeps_installed_copy(self):
        for fail_sync in (True, False):
            def fail(command, **kwargs):
                if command[1] == ('sync' if fail_sync else 'run'):
                    return subprocess.CompletedProcess(command, 1, 'private failure detail', '')
                return self.runner(command, **kwargs)
            with self.subTest(fail_sync=fail_sync):
                with self.assertRaisesRegex(ValueError, '^update_validation_failed$'):
                    self.refresh(runner=fail)
                self.assertFalse(any(p.is_dir() for p in self.cache.iterdir()))

    def test_cache_with_corrupt_published_content_is_not_reused_or_overwritten(self):
        result = self.refresh()
        published = Path(result['skill_dir'])
        (published / 'SKILL.md').write_text('corrupt')
        with self.assertRaisesRegex(ValueError, '^update_cache_conflict$'):
            self.refresh()
        self.assertEqual((published / 'SKILL.md').read_text(), 'corrupt')

    def test_cache_symlink_cannot_redirect_a_previously_published_version(self):
        result = self.refresh()
        published = Path(result['skill_dir']).parents[2]
        displaced = self.root / 'displaced version'
        published.rename(displaced)
        try: published.symlink_to(displaced, target_is_directory=True)
        except OSError: self.skipTest('host does not permit symlink fixtures')
        with self.assertRaisesRegex(ValueError, '^update_cache_conflict$'):
            self.refresh()

    def test_successful_status_process_with_live_or_configured_state_is_rejected(self):
        def wrong_status(command, **kwargs):
            if command[1] == 'run':
                return subprocess.CompletedProcess(command, 0, json.dumps(
                    {'ok': True, 'data': {'worker': 'running', 'configured': True}}), '')
            return self.runner(command, **kwargs)
        with self.assertRaisesRegex(ValueError, '^update_validation_failed$'):
            self.refresh(runner=wrong_status)
        self.assertFalse(any(p.is_dir() for p in self.cache.iterdir()))

    def test_cache_cannot_be_inside_loaded_skill_and_source_is_not_overridable(self):
        with self.assertRaisesRegex(ValueError, '^update_unsafe_cache$'):
            self.module.refresh(self.current, self.current / 'cache', 'codex')
        with self.assertRaisesRegex(ValueError, '^update_unsafe_cache$'):
            self.module.refresh(self.current, Path('relative-cache'), 'codex')
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as error:
                self.module.main(['--source', 'untrusted/repo'])
        self.assertEqual(error.exception.code, 2)

    def test_update_environment_cannot_reuse_auth_or_redirect_the_public_source(self):
        def isolated(command, **kwargs):
            env = kwargs['env']
            for name in ('GITHUB_TOKEN', 'CLAUDE_CODE_OAUTH_TOKEN', 'SKILLS_DOWNLOAD_URL'):
                self.assertNotIn(name, env)
            self.assertNotEqual(env['HOME'], str(Path.home()))
            self.assertEqual(env['GIT_CONFIG_GLOBAL'], os.devnull)
            return self.runner(command, **kwargs)
        with patch.dict('os.environ', GITHUB_TOKEN='secret', CLAUDE_CODE_OAUTH_TOKEN='secret',
                        SKILLS_DOWNLOAD_URL='https://untrusted.invalid'):
            self.assertEqual(self.refresh(runner=isolated)['status'], 'updated')

    def test_proxy_ca_paths_are_forwarded_without_code_injection_settings(self):
        stage = self.root / 'ca stage'
        stage.mkdir()
        with patch.dict('os.environ', NODE_EXTRA_CA_CERTS='/synthetic/ca.pem', GIT_SSL_CAINFO='/synthetic/ca.pem',
                        NODE_OPTIONS='--require /synthetic/hook.js', GIT_CONFIG_COUNT='1',
                        GIT_CONFIG_KEY_0='http.extraHeader', GIT_CONFIG_VALUE_0='secret',
                        NPM_CONFIG_SCRIPT_SHELL='/synthetic/shell', NPM_CONFIG_REGISTRY='https://untrusted.invalid',
                        NODE_TLS_REJECT_UNAUTHORIZED='0', GIT_SSL_NO_VERIFY='1'):
            env = self.module.child_environment(stage)
        self.assertEqual(env['NODE_EXTRA_CA_CERTS'], '/synthetic/ca.pem')
        self.assertEqual(env['GIT_SSL_CAINFO'], '/synthetic/ca.pem')
        for name in ('NODE_OPTIONS', 'GIT_CONFIG_COUNT', 'GIT_CONFIG_KEY_0', 'GIT_CONFIG_VALUE_0',
                     'NPM_CONFIG_SCRIPT_SHELL', 'NPM_CONFIG_REGISTRY', 'NODE_TLS_REJECT_UNAUTHORIZED',
                     'GIT_SSL_NO_VERIFY'):
            self.assertNotIn(name, env)

    @unittest.skipUnless(shutil.which('npm'), 'npm is required for the real config isolation check')
    def test_npm_ignores_prefix_global_registry_config(self):
        stage = self.root / 'npm stage'
        stage.mkdir()
        prefix = self.root / 'synthetic npm prefix'
        (prefix / 'etc').mkdir(parents=True)
        (prefix / 'etc/npmrc').write_text('registry=https://synthetic.invalid/\n')
        env = self.module.child_environment(stage)
        command = [shutil.which('npm'), '--prefix', str(prefix), 'config', 'get', 'registry']
        control = dict(env)
        control.pop('NPM_CONFIG_GLOBALCONFIG', None)
        exposed = subprocess.run(command, cwd=self.root, env=control, capture_output=True,
                                 text=True, timeout=30)
        self.assertEqual(exposed.returncode, 0, exposed.stderr)
        self.assertEqual(exposed.stdout.strip(), 'https://synthetic.invalid/')
        isolated = subprocess.run(command, cwd=self.root, env=env, capture_output=True,
                                  text=True, timeout=30)
        self.assertEqual(isolated.returncode, 0, isolated.stderr)
        self.assertEqual(isolated.stdout.strip(), 'https://registry.npmjs.org/')

    @unittest.skipUnless(shutil.which('npm'), 'npm is required for the real config isolation check')
    def test_npm_ignores_ancestor_project_registry_config(self):
        project = self.root / 'owner project'
        project.mkdir()
        (project / 'package.json').write_text('{}\n')
        (project / '.npmrc').write_text('registry=https://synthetic-project.invalid/\n')
        stage = project / 'private-cache/.staging-test'
        stage.mkdir(parents=True)
        control_stage = project / 'private-cache/.control'
        control_stage.mkdir()
        env = self.module.child_environment(stage)
        command = [shutil.which('npm'), 'config', 'get', 'registry']
        exposed = subprocess.run(command, cwd=control_stage, env=env, capture_output=True,
                                 text=True, timeout=30)
        self.assertEqual(exposed.returncode, 0, exposed.stderr)
        self.assertEqual(exposed.stdout.strip(), 'https://synthetic-project.invalid/')
        isolated = subprocess.run(command, cwd=stage, env=env, capture_output=True,
                                  text=True, timeout=30)
        self.assertEqual(isolated.returncode, 0, isolated.stderr)
        self.assertEqual(isolated.stdout.strip(), 'https://registry.npmjs.org/')
        self.assertEqual((project / 'package.json').read_text(), '{}\n')
        self.assertEqual((project / '.npmrc').read_text(),
                         'registry=https://synthetic-project.invalid/\n')

    @unittest.skipUnless(shutil.which('git'), 'git is required for the real fallback isolation check')
    def test_git_ssh_fallback_is_denied_even_when_installer_overrides_allowed_protocols(self):
        stage = self.root / 'git stage'
        stage.mkdir()
        marker = self.root / 'synthetic SSH authentication was invoked'
        ssh_fixture = self.root / 'synthetic ssh.py'
        ssh_fixture.write_text('import pathlib,sys\npathlib.Path(sys.argv[1]).touch()\nraise SystemExit(1)\n')
        env = self.module.child_environment(stage)
        # skills@1.5.20 createGitClient overrides GIT_ALLOW_PROTOCOL and uses
        # process.env.GIT_SSH_COMMAND ?? its SSH default after HTTPS auth failure.
        # Replace only that default with a safe marker, never the host SSH binary.
        fixture_command = shlex.join([Path(sys.executable).as_posix(),
                                     ssh_fixture.as_posix(), marker.as_posix()])
        env.update(GIT_ALLOW_PROTOCOL='https:http:ssh:git:file', GIT_SSH_VARIANT='ssh',
                   GIT_SSH_COMMAND=env.get('GIT_SSH_COMMAND', fixture_command))
        result = subprocess.run([shutil.which('git'), 'clone', '--depth', '1',
                                 'git@synthetic.invalid:rotnov/skills.git', str(stage / 'clone')],
                                cwd=self.root, env=env, capture_output=True, text=True, timeout=30)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(marker.exists(), 'fallback reached an SSH authentication command')

    @unittest.skipUnless(shutil.which('git'), 'git is required for the real protocol isolation check')
    def test_direct_non_https_git_transport_is_blocked_before_network(self):
        stage = self.root / 'protocol stage'
        stage.mkdir()
        env = self.module.child_environment(stage)
        result = subprocess.run([shutil.which('git'), 'ls-remote', 'git://synthetic.invalid/skills.git'],
                                cwd=self.root, env=env, capture_output=True, text=True, timeout=30)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("transport 'git' not allowed", result.stderr)

    def test_concurrent_update_does_not_start_a_second_installer(self):
        from quorum_os import FileLock
        self.cache.mkdir()
        with FileLock(self.cache / 'update.lock'):
            with self.assertRaisesRegex(ValueError, '^update_in_progress$'):
                self.refresh(runner=lambda *_args, **_kwargs: self.fail('second installer'))

    def test_validation_that_changes_source_cannot_publish_under_an_old_hash(self):
        def mutate(command, **kwargs):
            result = self.runner(command, **kwargs)
            if command[1] == 'run':
                target = Path(kwargs['cwd']) / '.agents/skills/quorum/new.py'
                target.write_text('print("changed during validation")')
            return result
        with self.assertRaisesRegex(ValueError, '^update_invalid_copy$'):
            self.refresh(runner=mutate)
        self.assertFalse(any(p.is_dir() for p in self.cache.iterdir()))

    def test_cli_reports_only_safe_blocker_codes(self):
        with patch('quorum_update.refresh', side_effect=OSError('private-secret')), \
                contextlib.redirect_stdout(io.StringIO()) as output:
            code = self.module.main(['--engine', 'codex', '--cache-root', str(self.cache)])
        self.assertEqual(code, 3)
        self.assertEqual(json.loads(output.getvalue()),
                         {'ok': False, 'code': 'update_failed', 'data': {}})


if __name__ == '__main__':
    unittest.main()
