"""Repository-scoped GitHub writes through the gh CLI.

Issue and comment bodies go through --body-file. Search hits count only when
the issue body contains the exact Debug-Finding marker line.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path


GH_COMMANDS = {('auth', 'status'), ('issue', 'list'), ('issue', 'create'), ('issue', 'comment'), ('issue', 'view')}
MARKER_RE = re.compile(r'^[0-9a-f]{64}$')
HASH_RE = re.compile(r'^[0-9a-f]{16}$')


def exact_line(body, label, value):
    if not isinstance(body, str) or not isinstance(value, str) or not value:
        return False
    expected = f'{label}: {value}'
    return any(line.strip() == expected for line in body.splitlines())


def marker_in(body, finding_id):
    return bool(MARKER_RE.fullmatch(finding_id or '')) and exact_line(body, 'Debug-Finding', finding_id)


def evidence_in(body, evidence_hash):
    return bool(HASH_RE.fullmatch(evidence_hash or '')) and exact_line(body, 'Evidence-Hash', evidence_hash)


def line_value(body, label, pattern):
    if not isinstance(body, str):
        return None
    prefix = f'{label}: '
    for line in body.splitlines():
        stripped = line.strip()
        if not stripped.startswith(prefix):
            continue
        value = stripped[len(prefix):]
        if pattern.fullmatch(value):
            return value
    return None


class GhCliPublisher:
    """Callers pass a fake in tests. The default runner is injected by the service."""

    def __init__(self, runner=None, body_dir=None):
        if runner is None:
            raise RuntimeError('publisher runner required')
        self.runner = runner
        self.body_dir = body_dir

    def probe(self):
        if shutil.which('gh') is None:
            return {'available': False, 'tool': 'gh', 'blocker': 'gh_cli_missing'}
        try:
            proc = self.runner(['gh', 'auth', 'status'], timeout=10)
        except (OSError, subprocess.SubprocessError):
            return {'available': False, 'tool': 'gh', 'blocker': 'gh_auth_unavailable'}
        if getattr(proc, 'returncode', 1) != 0:
            return {'available': False, 'tool': 'gh', 'blocker': 'gh_auth_unavailable'}
        return {'available': True, 'tool': 'gh', 'blocker': None}

    def search(self, repo, finding_id):
        # Keep the custom marker label out of GitHub's query syntax. Search
        # results still require the exact marker line when classified.
        proc = self.runner([
            'gh', 'issue', 'list', '--repo', repo, '--state', 'all', '--limit', '5',
            '--json', 'number,body', '--search', f'{finding_id} in:body',
        ], timeout=20)
        if getattr(proc, 'returncode', 1) != 0:
            return {'status': 'uncertain', 'issues': []}
        try:
            data = json.loads(proc.stdout or '[]')
        except json.JSONDecodeError:
            return {'status': 'uncertain', 'issues': []}
        if not isinstance(data, list):
            return {'status': 'uncertain', 'issues': []}
        issues = []
        for item in data:
            if not isinstance(item, dict) or type(item.get('number')) is not int:
                continue
            body = item.get('body') if isinstance(item.get('body'), str) else ''
            issues.append({'number': item['number'], 'body': body})
        return {'status': 'ok', 'issues': issues}

    def read_issue(self, repo, number):
        proc = self.runner([
            'gh', 'issue', 'view', str(number), '--repo', repo, '--json', 'number,body,comments',
        ], timeout=20)
        if getattr(proc, 'returncode', 1) != 0:
            return {'status': 'uncertain'}
        try:
            data = json.loads(proc.stdout or '{}')
        except json.JSONDecodeError:
            return {'status': 'uncertain'}
        if not isinstance(data, dict) or type(data.get('number')) is not int:
            return {'status': 'uncertain'}
        comments = []
        for item in data.get('comments') or []:
            if isinstance(item, dict) and isinstance(item.get('body'), str):
                comments.append({'body': item['body']})
        body = data.get('body') if isinstance(data.get('body'), str) else ''
        return {'status': 'ok', 'number': data['number'], 'body': body, 'comments': comments}

    def create_issue(self, repo, title, body):
        proc = self._run_body(
            ['gh', 'issue', 'create', '--repo', repo, '--title', title], body, timeout=20)
        if getattr(proc, 'returncode', 1) != 0:
            return {'status': 'uncertain'}
        match = re.search(r'https://github.com/([^/\s]+)/([^/\s]+)/issues/(\d+)', proc.stdout or '')
        if not match or f'{match.group(1)}/{match.group(2)}'.casefold() != repo.casefold():
            return {'status': 'uncertain'}
        return {'status': 'created', 'number': int(match.group(3)), 'url': match.group(0)}

    def comment(self, repo, number, body):
        proc = self._run_body(
            ['gh', 'issue', 'comment', str(number), '--repo', repo], body, timeout=20)
        if getattr(proc, 'returncode', 1) != 0:
            return {'status': 'uncertain'}
        return {'status': 'commented'}

    def _run_body(self, args, body, timeout):
        path = self._write_body(body)
        try:
            if '--body' in args:
                raise OSError('inline_body_refused')
            return self.runner(args + ['--body-file', str(path)], timeout=timeout)
        finally:
            try:
                path.unlink()
            except OSError:
                pass

    def _write_body(self, body):
        kwargs = {'prefix': 'quorum-debug-', 'suffix': '.txt'}
        if self.body_dir is not None:
            Path(self.body_dir).mkdir(parents=True, exist_ok=True)
            kwargs['dir'] = self.body_dir
        fd, name = tempfile.mkstemp(**kwargs)
        os.chmod(name, 0o600)
        with os.fdopen(fd, 'w', encoding='utf-8') as handle:
            handle.write(body)
        return Path(name)
