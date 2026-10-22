#!/usr/bin/env python3
"""Refresh public Quorum code into a new persistent copy; never replace readers."""
import argparse
import ast
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib

sys.dont_write_bytecode = True
from quorum_os import FileLock, protect, reject_link

CLIENTS = {'codex': '.agents/skills/quorum', 'claude': '.claude/skills/quorum',
           'grok': '.agents/skills/quorum'}
GENERATED = {'.venv', '__pycache__', '.git', 'node_modules'}
REQUIRED = ('SKILL.md', 'pyproject.toml', 'uv.lock', 'scripts/quorum.py',
            'scripts/quorum_update.py', 'references/update.md', 'references/join.md',
            'references/invitations.md')
ERRORS = {'update_tool_missing', 'update_fetch_failed', 'update_invalid_copy',
          'update_validation_failed', 'update_cache_conflict', 'update_unsafe_cache',
          'update_in_progress'}


def content_hash(root):
    """Hash portable source bytes, rejecting links and special files before use."""
    root = Path(root)
    reject_link(root)
    digest = hashlib.sha256()
    total = 0
    count = 0
    for directory, dirs, files in os.walk(root, followlinks=False):
        for name in dirs + files:
            reject_link(Path(directory) / name)
        dirs[:] = sorted(name for name in dirs if name not in GENERATED)
        for name in sorted(files):
            if name.endswith(('.pyc', '.pyo')): continue
            path = Path(directory) / name
            if not path.is_file(): raise ValueError('update_invalid_copy')
            size = path.stat().st_size
            count += 1
            total += size
            if count > 10000 or total > 100 * 1024 * 1024:
                raise ValueError('update_invalid_copy')
            relative = path.relative_to(root).as_posix().encode()
            digest.update(len(relative).to_bytes(4, 'big'))
            digest.update(relative)
            digest.update(size.to_bytes(8, 'big'))
            digest.update(path.read_bytes())
    return digest.hexdigest()


def validate_source(root):
    try:
        for path in (root, root.parent, root.parent.parent): reject_link(path)
        digest = content_hash(root)
        if any(not (root / name).is_file() for name in REQUIRED):
            raise ValueError('missing_required_file')
        frontmatter = (root / 'SKILL.md').read_text(encoding='utf-8').split('---', 2)
        if len(frontmatter) != 3 or frontmatter[0].strip() or not re.search(
                r'^name:\s*quorum\s*$', frontmatter[1], re.MULTILINE):
            raise ValueError('invalid_skill_name')
        project = tomllib.loads((root / 'pyproject.toml').read_text(encoding='utf-8'))
        lock = tomllib.loads((root / 'uv.lock').read_text(encoding='utf-8'))
        if not project.get('project', {}).get('requires-python') or not lock.get('version'):
            raise ValueError('missing_lock_contract')
        for path in (root / 'scripts').glob('*.py'):
            ast.parse(path.read_text(encoding='utf-8'), filename=path.name)
        return digest
    except (OSError, ValueError, SyntaxError, UnicodeError, TypeError):
        raise ValueError('update_invalid_copy') from None


def child_environment(stage):
    # skills@1.5.20 also writes a global lock on project installs. Isolate it,
    # Git/npm configuration and credentials from all installed client metadata.
    allowed = ('PATH', 'SystemRoot', 'SYSTEMROOT', 'COMSPEC', 'PATHEXT',
               'TEMP', 'TMP', 'TMPDIR', 'HTTPS_PROXY', 'HTTP_PROXY', 'NO_PROXY',
               'https_proxy', 'http_proxy', 'no_proxy', 'SSL_CERT_FILE', 'SSL_CERT_DIR',
               'UV_CACHE_DIR', 'UV_PYTHON_INSTALL_DIR')
    env = {key: os.environ[key] for key in allowed if key in os.environ}
    home = stage / 'tool-home'
    home.mkdir(mode=0o700)
    # Stop npm's project lookup here even when a private cache is nested in an
    # owner's repository. An isolated HOME doesn't isolate ancestor .npmrc files.
    (stage / 'package.json').write_text('{"private":true}\n', encoding='utf-8')
    (stage / '.npmrc').touch(mode=0o600)
    npm_user, npm_global = home / 'npm-user.npmrc', home / 'npm-global.npmrc'
    for path in (npm_user, npm_global): path.touch(mode=0o600)
    env.update(HOME=str(home), USERPROFILE=str(home), XDG_CONFIG_HOME=str(home / '.config'),
               XDG_DATA_HOME=str(home / '.local/share'), XDG_CACHE_HOME=str(home / '.cache'),
               APPDATA=str(home / 'AppData/Roaming'), LOCALAPPDATA=str(home / 'AppData/Local'),
               GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull, GIT_TERMINAL_PROMPT='0',
               NPM_CONFIG_USERCONFIG=str(npm_user), NPM_CONFIG_GLOBALCONFIG=str(npm_global),
               GIT_ALLOW_PROTOCOL='https', GIT_SSH_COMMAND='exit 1',
               DISABLE_TELEMETRY='1', DO_NOT_TRACK='1',
               SKILLS_CLONE_TIMEOUT_MS='90000', UV_NO_PROGRESS='1', UV_PYTHON=sys.executable)
    # The pinned installer widens GIT_ALLOW_PROTOCOL internally. Its SSH fallback
    # preserves this command; Git runs only its shell's exit builtin, never SSH.
    return env


def checked_run(run, command, stage, env, code, timeout):
    try:
        # Keep tool logs as bytes; status JSON is decoded strictly by json.loads.
        result = run(command, cwd=str(stage), env=env, stdin=subprocess.DEVNULL,
                     capture_output=True, timeout=timeout)
        if result.returncode: raise ValueError(code)
        return result
    except (OSError, subprocess.SubprocessError, UnicodeError):
        raise ValueError(code) from None


def refresh(current, cache, engine, *, run=subprocess.run):
    current, cache = Path(current).resolve(), Path(cache)
    if not cache.is_absolute(): raise ValueError('update_unsafe_cache')
    if engine not in CLIENTS: raise ValueError('update_invalid_copy')
    reject_link(cache)
    cache = cache.resolve()
    if cache == current or cache.is_relative_to(current):
        raise ValueError('update_unsafe_cache')
    tools = {name: shutil.which(name) for name in ('npx', 'node', 'git', 'uv')}
    if not all(tools.values()): raise ValueError('update_tool_missing')
    cache.mkdir(mode=0o700, parents=True, exist_ok=True)
    protect(cache, directory=True)
    guard = FileLock(cache / 'update.lock')
    try: guard.__enter__()
    except ValueError: raise ValueError('update_in_progress') from None
    try:
        with tempfile.TemporaryDirectory(prefix='.staging-', dir=cache) as temporary:
            stage = Path(temporary)
            env = child_environment(stage)
            checked_run(run, [tools['npx'], '--yes', 'skills@1.5.20', 'add', 'rotnov/skills',
                              '--skill', 'quorum', '-a', 'claude-code', 'codex', '--copy', '-y'],
                        stage, env, 'update_fetch_failed', 180)
            copies = [stage / relative for relative in dict.fromkeys(CLIENTS.values())]
            digests = [validate_source(copy) for copy in copies]
            if len(set(digests)) != 1: raise ValueError('update_invalid_copy')
            for index, copy in enumerate(copies):
                isolated = dict(env, UV_PROJECT_ENVIRONMENT=str(stage / f'validation-{index}'))
                checked_run(run, [tools['uv'], 'sync', '--frozen', '--project', str(copy)],
                            stage, isolated, 'update_validation_failed', 180)
                result = checked_run(run, [tools['uv'], 'run', '--offline', '--frozen', '--project',
                                          str(copy), 'python', str(copy / 'scripts/quorum.py'),
                                          '--state', str(stage / f'validation-state-{index}'), 'status'],
                                     stage, isolated, 'update_validation_failed', 30)
                try:
                    status = json.loads(result.stdout)
                    if (status.get('ok') is not True or status.get('data', {}).get('configured') is not False
                            or status['data'].get('worker') != 'offline'):
                        raise ValueError('invalid_status')
                except (ValueError, KeyError, TypeError, AttributeError):
                    raise ValueError('update_validation_failed') from None
            digest = digests[0]
            if any(validate_source(copy) != digest for copy in copies):
                raise ValueError('update_invalid_copy')
            destination = cache / digest
            # Only source copies are published. Discard installer metadata,
            # validation state/environments and temporary isolated home first.
            for item in stage.iterdir():
                if item.name not in ('.agents', '.claude'):
                    if item.is_dir(): shutil.rmtree(item)
                    else: item.unlink()
            if destination.exists() or destination.is_symlink():
                try:
                    reject_link(destination)
                    if any(validate_source(destination / relative) != digest
                           for relative in CLIENTS.values()):
                        raise ValueError('hash_mismatch')
                except ValueError:
                    raise ValueError('update_cache_conflict') from None
            else:
                stage.rename(destination)
            return {'status': 'current' if content_hash(current) == digest else 'updated',
                    'skill_dir': str(destination / CLIENTS[engine]), 'content_sha256': digest}
    finally:
        guard.__exit__()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--engine', required=True, choices=CLIENTS)
    parser.add_argument('--cache-root', type=Path,
                        default=Path.home() / '.local/share/quorum/skill-updates')
    args = parser.parse_args(argv)
    try:
        data = refresh(Path(__file__).resolve().parents[1], args.cache_root, args.engine)
        print(json.dumps({'ok': True, 'code': 'ok', 'data': data}))
        return 0
    except (ValueError, OSError, subprocess.SubprocessError) as error:
        code = str(error) if str(error) in ERRORS else 'update_failed'
        print(json.dumps({'ok': False, 'code': code, 'data': {}}))
        return 3


if __name__ == '__main__':
    raise SystemExit(main())
