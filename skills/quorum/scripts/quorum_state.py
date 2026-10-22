"""Local nonsecret ledger, scoped capabilities and exclusive worker lock."""
from contextlib import contextmanager
from dataclasses import asdict, dataclass
import hashlib
import hmac
import json
import os
from pathlib import Path
import secrets
import sqlite3
import time
from quorum_os import FileLock, protect, reject_link


@dataclass(frozen=True)
class SessionBinding:
    quorum_id: str
    project_ref: str
    device_id: str
    agent_id: str
    conversation_id: str
    engine: str
    cwd: str
    main_session_ref: str

    @property
    def name(self):
        return 'quorum-' + hashlib.sha256(json.dumps(asdict(self), sort_keys=True).encode()).hexdigest()[:32]


class LocalLedger:
    def __init__(self, root, clock=time.time):
        self.root, self.clock = Path(root), clock
        if not self.root.is_absolute() or self.root.is_symlink():
            raise ValueError('invalid_state_directory')
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        reject_link(self.root)
        protect(self.root, directory=True)
        path = self.root / 'ledger.sqlite'
        if path.is_symlink():
            raise ValueError('unsafe_state_file')
        self.db = sqlite3.connect(path, timeout=5)
        protect(path)
        self.db.executescript('''
            create table if not exists bindings (conversation text primary key, data text not null);
            create table if not exists capabilities (hash text primary key, agent text, expires real);
            create table if not exists events (attempt text, event text, details text, created real);
            create table if not exists attempts (id text primary key, generation integer, state text, reply text);
            create table if not exists settings (key text primary key, data text not null);
        ''')

    def setting(self, key, value=None):
        if value is not None:
            with self.db:
                self.db.execute('insert into settings values (?,?) on conflict(key) do update set data=excluded.data',
                                (key, json.dumps(value)))
        row = self.db.execute('select data from settings where key=?', (key,)).fetchone()
        return None if row is None else json.loads(row[0])

    def bind_session(self, binding):
        if binding.engine not in ('claude', 'codex', 'grok') or not Path(binding.cwd).is_absolute():
            raise ValueError('invalid_binding')
        data = json.dumps(asdict(binding), sort_keys=True)
        with self.db:
            self.db.execute('insert or ignore into bindings values (?,?)', (binding.conversation_id, data))
            saved = self.db.execute('select data from bindings where conversation=?',
                                    (binding.conversation_id,)).fetchone()[0]
            if saved != data:
                raise ValueError('binding_conflict')

    def binding(self, conversation):
        row = self.db.execute('select data from bindings where conversation=?', (conversation,)).fetchone()
        if row is None:
            raise ValueError('binding_required')
        return SessionBinding(**json.loads(row[0]))

    def issue_capability(self, agent_id, ttl=3600):
        if not 0 < ttl <= 3600:
            raise ValueError('invalid_capability_ttl')
        token = secrets.token_urlsafe(32)
        with self.db:
            self.db.execute('insert into capabilities values (?,?,?)',
                            (hashlib.sha256(token.encode()).hexdigest(), agent_id, self.clock() + ttl))
        return token

    def authorize(self, token):
        if not isinstance(token, str) or len(token) > 256:
            raise ValueError('capability_denied')
        row = self.db.execute('select agent,expires from capabilities where hash=?',
                              (hashlib.sha256(token.encode()).hexdigest(),)).fetchone()
        if row is None or row[1] <= self.clock():
            raise ValueError('capability_denied')
        return row[0]

    def revoke(self, agent):
        with self.db:
            self.db.execute('delete from capabilities where agent=?', (agent,))

    def record_event(self, attempt_id, event, details):
        if set(details) - {'generation', 'session_name', 'pid', 'state', 'code'}:
            raise ValueError('unsafe_event_fields')
        with self.db:
            self.db.execute('insert into events values (?,?,?,?)',
                            (attempt_id, event, json.dumps(details), self.clock()))

    def attempt(self, attempt_id):
        row = self.db.execute('select generation,state,reply from attempts where id=?', (attempt_id,)).fetchone()
        return None if row is None else dict(generation=row[0], state=row[1], reply=row[2])

    def set_attempt(self, attempt_id, generation, state, reply=None):
        with self.db:
            old = self.attempt(attempt_id)
            if old is not None and old['generation'] != generation:
                raise ValueError('stale_generation')
            self.db.execute('insert into attempts values (?,?,?,?) on conflict(id) do update '
                            'set state=excluded.state,reply=excluded.reply',
                            (attempt_id, generation, state, reply))

    @contextmanager
    def worker_lock(self):
        with FileLock(self.root / 'worker.lock'):
            yield
