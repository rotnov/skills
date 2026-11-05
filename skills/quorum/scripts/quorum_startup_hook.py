"""Owner opt-in client startup hook for bounded same-binding recovery; prints by default, writes only on request.

Printing changes nothing. `--install` and `--uninstall` change client configuration, so each needs the
owner's explicit `--owner-authorized`, edits only the selected project's Codex `hooks.json`, merges with
existing hooks, is idempotent, and removes only the exact handler it would install. Nothing here runs
recovery, contacts a service, reads a token or edits global configuration.
"""
import argparse
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile

SKILL_DIR = Path(__file__).resolve().parents[1]
TIMEOUT_SECONDS = 180
MATCHER = 'resume'
CONFIG_LIMIT = 1048576
MARKER = 'quorum_resume.py'
CODEX_DOCS = 'https://learn.chatgpt.com/docs/hooks'
GROK_DOCS = ('https://github.com/xai-org/grok-build/blob/2bdd1d6a6369de0e8c68132ea4539e9abd9e14a8/'
             'crates/codegen/xai-grok-pager/docs/user-guide/10-hooks.md')


class HookError(ValueError):
    """Fixed codes only; never file contents, environment or exception text."""


def hook_command(state, client):
    argv = ['uv', 'run', '--project', str(SKILL_DIR), '--frozen', 'python', str(SKILL_DIR / 'scripts' / MARKER),
            '--state', str(Path(state).resolve()), '--client', client]
    return subprocess.list2cmdline(argv) if os.name == 'nt' else shlex.join(argv)


def handler(state, client):
    return {'type': 'command', 'command': hook_command(state, client), 'timeout': TIMEOUT_SECONDS}


def hooks_file(project_dir):
    if project_dir is None: raise HookError('startup_hook_project_required')
    project = Path(project_dir)
    if not project.is_absolute() or not project.is_dir(): raise HookError('startup_hook_project_required')
    directory = project / '.codex'
    path = directory / 'hooks.json'
    if directory.is_symlink() or path.is_symlink(): raise HookError('startup_hook_config_unsafe')
    if directory.exists() and not directory.is_dir(): raise HookError('startup_hook_config_unsafe')
    if path.exists() and not path.is_file(): raise HookError('startup_hook_config_unsafe')
    return path


def _load(path):
    if not path.exists(): return {}
    with open(path, 'rb') as handle: raw = handle.read(CONFIG_LIMIT + 1)
    if len(raw) > CONFIG_LIMIT: raise HookError('startup_hook_config_invalid')
    try: config = json.loads(raw.decode('utf-8')) if raw.strip() else {}
    except (UnicodeDecodeError, ValueError): raise HookError('startup_hook_config_invalid') from None
    if not isinstance(config, dict): raise HookError('startup_hook_config_invalid')
    hooks = config.get('hooks', {})
    if not isinstance(hooks, dict) or not isinstance(hooks.get('SessionStart', []), list):
        raise HookError('startup_hook_config_invalid')
    for group in hooks.get('SessionStart', []):
        if not isinstance(group, dict) or not isinstance(group.get('hooks', []), list):
            raise HookError('startup_hook_config_invalid')
    return config


def _ours(entry):
    return isinstance(entry, dict) and isinstance(entry.get('command'), str) and MARKER in entry['command']


def _write(path, config):
    """Atomic replace in the same directory; an existing file keeps its permission bits."""
    path.parent.mkdir(mode=0o755, exist_ok=True)
    mode = path.stat().st_mode & 0o777 if path.exists() else 0o644
    descriptor, temporary = tempfile.mkstemp(prefix='.hooks.', suffix='.tmp', dir=path.parent)
    try:
        with os.fdopen(descriptor, 'w', encoding='utf-8') as handle:
            json.dump(config, handle, indent=2)
            handle.write('\n')
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    except BaseException:
        try: os.unlink(temporary)
        except OSError: pass
        raise


def install(path, entry):
    config = _load(path)
    groups = config.setdefault('hooks', {}).setdefault('SessionStart', [])
    for group in groups:
        for existing in group.get('hooks', []):
            if _ours(existing):
                if existing == entry and group.get('matcher') == MATCHER: return 'unchanged'
                # Another skill copy, state or matcher: never silently replace an owner's earlier choice.
                raise HookError('startup_hook_conflict')
    groups.append({'matcher': MATCHER, 'hooks': [entry]})
    _write(path, config)
    return 'installed'


def uninstall(path, entry):
    if not path.exists(): return 'absent'
    config = _load(path)
    hooks = config.get('hooks', {})
    groups, removed, kept = hooks.get('SessionStart', []), False, []
    for group in groups:
        remaining = [item for item in group.get('hooks', []) if item != entry]
        if len(remaining) == len(group.get('hooks', [])):
            kept.append(group)  # Unrelated groups stay exactly as the owner wrote them.
            continue
        removed = True
        if remaining: kept.append({**group, 'hooks': remaining})
    if not removed: return 'absent'
    if kept: hooks['SessionStart'] = kept
    else: hooks.pop('SessionStart', None)
    if not hooks: config.pop('hooks', None)
    _write(path, config)
    return 'removed'


def describe(state, client, project_dir):
    if client == 'claude':
        raise HookError('startup_hook_unsupported')
    entry = handler(state, client)
    value = {'client': client, 'event': 'SessionStart', 'matcher': MATCHER, 'handler': entry,
             'owner_consent_required': True}
    if client == 'codex':
        value.update(install_supported=True, documentation=CODEX_DOCS,
                     configuration={'hooks': {'SessionStart': [{'matcher': MATCHER, 'hooks': [entry]}]}},
                     next_step=('Show this to the owner. Only with their explicit consent, rerun with --project-dir '
                                '<project> --install --owner-authorized, then have the owner review and trust the '
                                'handler in Codex (/hooks). Remove it with --uninstall --owner-authorized.'))
        if project_dir is not None: value['hooks_file'] = str(hooks_file(project_dir))
    else:
        value.update(install_supported=False, documentation=GROK_DOCS,
                     next_step=('Print-only: the owner adds this SessionStart handler (matcher resume) in an '
                                'explicitly selected Grok hooks layer, such as the project .grok/hooks/, and '
                                'trusts it. GROK_LEADER_SOCKET must be exported to the hook runner.'))
    return value


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state', type=Path, required=True, help='The private state the bound chat uses')
    parser.add_argument('--client', choices=('codex', 'grok', 'claude'), required=True)
    parser.add_argument('--project-dir', type=Path, help='Codex project whose .codex/hooks.json is selected')
    action = parser.add_mutually_exclusive_group()
    action.add_argument('--install', action='store_true', help='Write the handler (needs --owner-authorized)')
    action.add_argument('--uninstall', action='store_true', help='Remove exactly this handler (needs --owner-authorized)')
    parser.add_argument('--owner-authorized', action='store_true',
                        help="The owner explicitly consented to this client configuration change")
    args = parser.parse_args(argv)
    try:
        value = describe(args.state, args.client, args.project_dir)
        if args.install or args.uninstall:
            if args.client != 'codex': raise HookError('startup_hook_install_unsupported')
            if not args.owner_authorized: raise HookError('owner_authorization_required')
            path = hooks_file(args.project_dir)
            state = (install if args.install else uninstall)(path, value['handler'])
            value = {'client': args.client, 'state': state, 'hooks_file': str(path), 'handler': value['handler']}
            if state == 'installed':
                value['next_step'] = ('Codex skips new or changed hooks until reviewed: have the owner open /hooks '
                                      'in this project and trust the quorum handler. A resume then runs the bounded '
                                      'recovery; confirm one post-resume message and receipt before relying on it.')
        print(json.dumps({'ok': True, 'code': 'ok', 'data': value}))
        return 0
    except HookError as error:
        guidance = {'startup_hook_unsupported': 'Claude Code: run session recover from the same resumed chat.'}
        print(json.dumps({'ok': False, 'code': str(error),
                          'data': {'next_step': guidance[str(error)]} if str(error) in guidance else {}}))
        return 3
    except OSError:
        print(json.dumps({'ok': False, 'code': 'startup_hook_config_unwritable', 'data': {}}))
        return 3


if __name__ == '__main__': raise SystemExit(main())
