"""Metadata-only reads of a local session ledger.

SQL projects allowlisted JSON fields. Binding scope is applied before any
delivery, attempt, or event query. Private bodies, replies, and secrets are
not selected.
"""
from __future__ import annotations

import re
import sqlite3
from pathlib import Path


SAFE = re.compile(r'^[A-Za-z0-9_.:@/+-]{1,128}$')
VERSION_RE = re.compile(r'^[0-9]{1,6}\.[0-9]{1,6}\.[0-9]{1,6}$')
BINDING_KEYS = ('session-agent', 'cloud-agent', 'pilot-agent')
IDENTITY_FIELDS = ('quorum', 'quorum_id', 'project_ref', 'role', 'engine', 'agent_id', 'transport')
DELIVERY_STATES = ('intent', 'queued', 'submitted', 'offered', 'acked')
DELIVERY_TEXT = (
    ('conversation_id', '$.conversation_id', '$.message.conversation_id'),
    ('sender_id', '$.sender_id', '$.message.sender_id'),
    ('sender_role', '$.sender_role', '$.message.sender_role'),
    ('recipient_id', '$.recipient_id', '$.message.recipient_id'),
    ('priority', '$.priority', '$.message.priority'),
)


def safe(value):
    if isinstance(value, str) and SAFE.fullmatch(value):
        return value
    return None


def flag(value):
    if value is True or value == 1:
        return True
    if value is False or value == 0:
        return False
    return None


def connect_readonly(path):
    return sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True)


def in_scope(snapshot, scope):
    if snapshot.get('unreadable'):
        return False
    quorum = scope['quorum']
    if snapshot.get('quorum') != quorum and snapshot.get('quorum_id') != quorum:
        return False
    if snapshot.get('project_ref') != scope['project_ref']:
        return False
    return not scope['bindings'] or snapshot.get('binding') in scope['bindings']


def _project(db, key, paths):
    columns = ', '.join(f"json_extract(data, '{path}')" for path in paths)
    return db.execute(f'select {columns} from settings where key=?', (key,)).fetchone()


def _identity(db, binding):
    paths = []
    for field in IDENTITY_FIELDS:
        paths.extend((f'$.{field}', f'$.registration.{field}'))
    for key in BINDING_KEYS:
        if db.execute('select 1 from settings where key=?', (key,)).fetchone() is None:
            continue
        row = _project(db, key, paths)
        picked = {}
        for index, field in enumerate(IDENTITY_FIELDS):
            top, nested = row[index * 2], row[index * 2 + 1]
            chosen = safe(top) if top is not None else safe(nested)
            if chosen:
                picked[field] = chosen
        if not picked:
            return {'binding': binding, 'unreadable': True}
        return {
            'binding': binding, 'unreadable': False, 'quorum': picked.get('quorum'),
            'quorum_id': picked.get('quorum_id'), 'project_ref': picked.get('project_ref'),
            'role': picked.get('role'), 'engine': picked.get('engine'),
            'agent_id': picked.get('agent_id'), 'transport': picked.get('transport') or 'native',
            'reception_enabled': None, 'listener': {}, 'wait': {}, 'presence': {}, 'epoch': None,
            'release': {}, 'deliveries': [], 'attempts': [], 'ledger_events': [],
        }
    return {'binding': binding, 'unreadable': True}


def _prefer_text(message_value, top_value):
    if message_value is not None:
        return safe(message_value)
    return safe(top_value)


def _deliveries(db):
    columns = [
        'substr(key, 10)',
        "json_extract(data, '$.state')",
        "json_extract(data, '$.reply_to')",
        "json_extract(data, '$.notification')",
        "json_extract(data, '$.message.reply_to')",
        "json_extract(data, '$.message.notification')",
    ]
    for _name, top, message in DELIVERY_TEXT:
        columns.extend((f"json_extract(data, '{top}')", f"json_extract(data, '{message}')"))
    columns.extend((
        "json_extract(data, '$.sequence')",
        "json_extract(data, '$.message.sequence')",
    ))
    sql = f"select {', '.join(columns)} from settings where key like 'delivery:%'"
    deliveries = []
    for row in db.execute(sql):
        message_id = safe(row[0])
        state = row[1] if row[1] in DELIVERY_STATES else None
        if message_id is None or state is None:
            continue
        delivery = {
            'message_id': message_id, 'state': state,
            'reply_to': bool(flag(row[2]) or flag(row[4])),
            'notification': bool(flag(row[3]) or flag(row[5])),
        }
        cursor = 6
        for name, _top, _message in DELIVERY_TEXT:
            chosen = _prefer_text(row[cursor + 1], row[cursor])
            cursor += 2
            if chosen:
                delivery[name] = chosen
        sequence = row[cursor + 1]
        if type(sequence) is not int:
            sequence = row[cursor]
        if type(sequence) is int:
            delivery['sequence'] = sequence
        deliveries.append(delivery)
    return deliveries


def _settings_metadata(db, snapshot):
    reception = _project(db, 'session-reception', ('$.enabled',))
    if reception is not None:
        enabled = flag(reception[0])
        if enabled is not None:
            snapshot['reception_enabled'] = enabled
    listener = _project(db, 'listener', ('$.run_id', '$.state', '$.code', '$.handled'))
    if listener is not None:
        for field, value in zip(('run_id', 'state', 'code'), listener[:3]):
            item = safe(value)
            if item:
                snapshot['listener'][field] = item
        if type(listener[3]) is int and listener[3] >= 0:
            snapshot['listener']['handled'] = listener[3]
    waiting = _project(db, 'listener-wait', ('$.mode', '$.code'))
    if waiting is not None:
        for field, value in zip(('mode', 'code'), waiting):
            item = safe(value)
            if item:
                snapshot['wait'][field] = item
    presence = _project(db, 'presence', ('$.state', '$.code'))
    if presence is not None:
        for field, value in zip(('state', 'code'), presence):
            item = safe(value)
            if item:
                snapshot['presence'][field] = item
    epoch = _project(db, 'presence-epoch', ('$.connection_id',))
    if epoch is not None:
        snapshot['epoch'] = safe(epoch[0])
    release_row = db.execute("select json_type(data, '$.installed') from settings where key='release'").fetchone()
    if release_row is not None:
        nested = release_row[0] == 'object'
        if nested:
            paths = ('$.installed.protocol_version', '$.installed.skill_version')
        else:
            paths = ('$.protocol_version', '$.skill_version')
        release = _project(db, 'release', paths)
        protocol, version = release if release is not None else (None, None)
        if type(protocol) is int and isinstance(version, str) and VERSION_RE.fullmatch(version):
            snapshot['release'] = {'protocol_version': protocol, 'skill_version': version}


def _attempts(db, snapshot):
    for attempt_id, generation, state in db.execute('select id, generation, state from attempts'):
        attempt = safe(attempt_id)
        stage = safe(state)
        if attempt and type(generation) is int and stage:
            snapshot['attempts'].append({'attempt_id': attempt, 'generation': generation, 'state': stage})


def _events(db, snapshot):
    sql = (
        'select attempt, event, created, '
        "json_extract(details, '$.generation'), json_extract(details, '$.state'), "
        "json_extract(details, '$.code'), json_extract(details, '$.pid') from events"
    )
    for attempt, event, created, generation, state, code, pid in db.execute(sql):
        item = {'attempt_id': safe(attempt), 'name': safe(event)}
        if not item['attempt_id'] or not item['name']:
            continue
        if type(generation) is int:
            item['generation'] = generation
        for field, value in (('state', state), ('code', code)):
            picked = safe(value)
            if picked:
                item[field] = picked
        if type(pid) is int and pid >= 0:
            item['pid'] = pid
        if type(created) in (int, float):
            item['ledger_clock'] = created
        snapshot['ledger_events'].append(item)


def read_scoped(path, binding, scope):
    """Read one ledger. Out-of-scope sessions stop before delivery fields."""
    db = connect_readonly(path)
    try:
        snapshot = _identity(db, binding)
        if snapshot.get('unreadable'):
            if scope['bindings'] and binding not in scope['bindings']:
                return None
            return snapshot
        if not in_scope(snapshot, scope):
            return None
        _settings_metadata(db, snapshot)
        snapshot['deliveries'] = _deliveries(db)
        _attempts(db, snapshot)
        _events(db, snapshot)
        return snapshot
    finally:
        db.close()


def read_local(root, scope):
    sessions = []
    directory = Path(root) / 'sessions'
    if not directory.is_dir():
        return sessions
    for child in sorted(path for path in directory.iterdir() if path.is_dir()):
        database = child / 'ledger.sqlite'
        if not database.is_file() or database.is_symlink():
            continue
        binding = safe(child.name) or 'unreadable'
        try:
            snapshot = read_scoped(database, binding, scope)
        except (OSError, sqlite3.Error, UnicodeError, ValueError):
            snapshot = {'binding': binding, 'unreadable': True} if not scope['bindings'] or binding in scope['bindings'] else None
        if snapshot is not None:
            sessions.append(snapshot)
    return sessions
