"""Two-transaction close-versus-open-invite race on real PostgreSQL 17.

Opt-in: QUORUM_RACE_DOCKER=1. Starts one uniquely named disposable container
from a local postgres:17 image with --network none and no published port, and
always removes it. Never reads configuration or credentials and never contacts a
service. Each transaction is a separate psql process and backend connection.

The interleavings are forced with an advisory lock the reviewed core takes after
its access check: a blocker connection holds that key, so the operation pauses
exactly between reading access='open' and writing its row. The script proves the
pause point is reached (pg_stat_activity, pg_blocking_pids) instead of sleeping.

Without the serialization migration the forced interleaving must leave an
anonymous membership (or an open invitation) behind a closed quorum; with it,
close and accept/create serialize and neither survives. Those rereads need READ
COMMITTED, so each operation run at REPEATABLE READ or SERIALIZABLE must fail
at once, while a blocker holds every lock it could take, and change no row.
"""
from pathlib import Path
import json
import os
import secrets
import subprocess
import sys
import time
import unittest
import unittest.mock

ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS = ROOT / 'skills/quorum/supabase/migrations'
IMAGE = os.environ.get('QUORUM_RACE_IMAGE', 'postgres:17-alpine')
SERIALIZATION = '_open_access_close_serialization.sql'
OWNER = '11111111-1111-4111-8111-111111111111'
ANON = '22222222-2222-4222-8222-222222222222'
TIMEOUT = 30
ISOLATION_LEVELS = ('repeatable read', 'serializable')


def docker(*args, stdin=None, check=True):
    result = subprocess.run(['docker', *args], input=stdin, capture_output=True, text=True, timeout=120)
    if check and result.returncode:
        raise RuntimeError(f'docker {args[0]} failed: {result.stderr.strip()}')
    return result


class Cluster:
    READY_SECONDS = 90

    def __init__(self):
        token = secrets.token_hex(6)
        self.name = 'quorum-close-race-' + token
        # A per-run label finds exactly this container even when `docker run`
        # created it and then failed or timed out before reporting its ID.
        self.label = 'io.rotnov.quorum-close-race=' + token
        self.removed = False

    def __enter__(self):
        # __exit__ is not called when __enter__ raises, so clean up here.
        try:
            # Local image only; the container has no network and publishes no port.
            docker('run', '-d', '--pull', 'never', '--network', 'none', '--label', self.label,
                   '--name', self.name, '-e', 'POSTGRES_PASSWORD=' + secrets.token_hex(16), IMAGE)
            deadline = time.monotonic() + self.READY_SECONDS
            while time.monotonic() < deadline:
                # The entrypoint's temporary init server also answers on the socket.
                logs = docker('logs', self.name, check=False)
                if 'init process complete' in logs.stdout + logs.stderr and docker(
                        'exec', self.name, 'pg_isready', '-U', 'postgres', check=False).returncode == 0:
                    version = self.sql('postgres', 'show server_version_num').strip()
                    if not version.startswith('17'):
                        raise RuntimeError('PostgreSQL 17 required, got ' + version)
                    return self
                time.sleep(0.5)
            raise RuntimeError('disposable PostgreSQL did not start')
        except BaseException:
            try:
                self.__exit__()
            except Exception:
                pass  # Keep the startup failure; removal is best effort without Docker.
            raise

    def __exit__(self, *exc):
        owned = docker('ps', '-a', '-q', '--filter', 'label=' + self.label, check=False).stdout.split()
        if owned:
            docker('rm', '-f', '-v', *owned, check=False)
        self.removed = not docker('ps', '-a', '-q', '--filter', 'label=' + self.label).stdout.strip()

    def sql(self, db, script, single=False):
        args = ['exec', '-i', self.name, 'psql', '-X', '-q', '-At', '-v', 'ON_ERROR_STOP=1', '-U', 'postgres', '-d', db]
        if single:
            args.append('-1')
        result = docker(*args, stdin=script, check=False)
        if result.returncode:
            raise RuntimeError(result.stderr.strip())
        return result.stdout

    def session(self, db, label):
        return Session(self, db, label)


class Session:
    """One psql process: one backend connection holding one open transaction."""

    def __init__(self, cluster, db, label):
        self.label = label + '-' + secrets.token_hex(3)
        self.proc = subprocess.Popen(
            ['docker', 'exec', '-i', '-e', 'PGAPPNAME=' + self.label, cluster.name, 'psql', '-X', '-q', '-At',
             '-v', 'ON_ERROR_STOP=1', '-U', 'postgres', '-d', db],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

    def send(self, script):
        try:
            self.proc.stdin.write(script + '\n')
            self.proc.stdin.flush()
        except BrokenPipeError:
            pass  # psql already stopped on an expected error; finish() reports it.

    def finish(self):
        out, err = self.proc.communicate(timeout=TIMEOUT)
        return self.proc.returncode, out, err


def prepared(cluster, db):
    """Committed fixture: GitHub owner, anonymous user, open quorum, open invite."""
    cluster.sql(db, f"""
      insert into auth.users(id,raw_user_meta_data) values('{OWNER}','{{}}'),('{ANON}','{{}}');
      insert into auth.identities(user_id,provider,provider_id,identity_data) values('{OWNER}','github','9000000000071','{{}}');
      insert into quorum_private.instance_creators(github_id) values('9000000000071');
    """)
    out = cluster.sql(db, f"""
      select set_config('request.jwt.claim.sub','{OWNER}',false);
      select public.quorum_create('race','setup-create')->>'quorum_id' as quorum_id \\gset
      select public.quorum_set_access(:'quorum_id','open','setup-open');
      select json_build_object('quorum_id',:'quorum_id',
        'invite_id',public.invite_create_open(:'quorum_id','setup-invite')->>'invite_id');
    """)
    return json.loads(out.strip().splitlines()[-1])


def wait_for(cluster, db, label, predicate, what):
    deadline = time.monotonic() + TIMEOUT
    while time.monotonic() < deadline:
        row = cluster.sql(db, f"""select json_build_object('pid',pid,'state',state,'wait_event_type',wait_event_type,
          'wait_event',wait_event,'xid',backend_xid::text,'blocking',pg_blocking_pids(pid))
          from pg_stat_activity where application_name like '{label}%'""").strip()
        if row and predicate(json.loads(row)):
            return json.loads(row)
        time.sleep(0.1)
    raise AssertionError(f'{label} never reached: {what}')


def advisory_key(actor, op, request_id):
    return f"hashtextextended('{actor}'||'{op}'||'{request_id}',0)"


def race(cluster, db, operation):
    """Force operation (accept or create) to pause after reading access='open',
    run the close against it, then let the operation finish first."""
    fixture = prepared(cluster, db)
    q, invite = fixture['quorum_id'], fixture['invite_id']
    if operation == 'accept':
        actor, op, req = ANON, 'invite_accept_id', 'race-accept'
        call = f"select public.invite_accept_id('{invite}','{req}');"
    else:
        actor, op, req = OWNER, 'invite_create_open', 'race-create'
        call = f"select public.invite_create_open('{q}','{req}');"
    blocker = cluster.session(db, 'race_blocker')
    blocker.send(f'begin; select pg_advisory_xact_lock({advisory_key(actor, op, req)});')
    held = wait_for(cluster, db, blocker.label, lambda r: r['state'] == 'idle in transaction', 'advisory held')
    worker = cluster.session(db, 'race_' + operation)
    worker.send(f"begin; select 'txid', txid_current(), pg_backend_pid();"
                f" select set_config('request.jwt.claim.sub','{actor}',true); {call}")
    paused = wait_for(cluster, db, worker.label,
                      lambda r: r['wait_event'] == 'advisory' and held['pid'] in r['blocking'],
                      'paused on the post-access-check advisory lock')
    closer = cluster.session(db, 'race_close')
    closer.send(f"begin; select 'txid', txid_current(), pg_backend_pid();"
                f" select set_config('request.jwt.claim.sub','{OWNER}',true);"
                f" select public.quorum_set_access('{q}','github','race-close');")
    # Unserialized create never blocks the close; any other close waits on the worker.
    closing = wait_for(cluster, db, closer.label,
                       lambda r: r['state'] == 'idle in transaction'
                       or (r['wait_event_type'] == 'Lock' and paused['pid'] in r['blocking']),
                       'blocked by the paused operation or finished')
    # A blocked close cannot commit before the worker; a finished one commits first.
    closer.send('commit;')
    blocker.send('commit;')
    blocker.finish()
    worker.send('commit;')
    worker_result = worker.finish()
    closer_result = closer.finish()
    state = json.loads(cluster.sql(db, f"""select json_build_object(
      'access',(select access from quorum_private.quorums where id='{q}'),
      'live_anonymous',(select count(*) from quorum_private.memberships where quorum_id='{q}' and user_id='{ANON}' and not revoked),
      'live_open_invites',(select count(*) from quorum_private.id_invitations where quorum_id='{q}' and open and not revoked and accepted_by is null))""").strip())
    return {'worker': worker_result, 'closer': closer_result, 'paused': paused, 'closing': closing,
            'blocker_pid': held['pid'], 'state': state}


def close_first(cluster, db):
    """The close commits its row changes first; the acceptance must be denied."""
    fixture = prepared(cluster, db)
    closer = cluster.session(db, 'first_close')
    closer.send(f"begin; select 'txid', txid_current(), pg_backend_pid();"
                f" select set_config('request.jwt.claim.sub','{OWNER}',true);"
                f" select public.quorum_set_access('{fixture['quorum_id']}','github','first-close');")
    closing = wait_for(cluster, db, closer.label, lambda r: r['state'] == 'idle in transaction', 'close done')
    worker = cluster.session(db, 'first_accept')
    worker.send(f"begin; select 'txid', txid_current(), pg_backend_pid();"
                f" select set_config('request.jwt.claim.sub','{ANON}',true);"
                f" select public.invite_accept_id('{fixture['invite_id']}','first-accept');")
    paused = wait_for(cluster, db, worker.label,
                      lambda r: r['wait_event_type'] == 'Lock' and closing['pid'] in r['blocking'], 'blocked by close')
    closer.send('commit;')
    closer_result = closer.finish()
    worker.send('commit;')
    worker_result = worker.finish()
    live = cluster.sql(db, f"""select count(*) from quorum_private.memberships
      where quorum_id='{fixture['quorum_id']}' and user_id='{ANON}' and not revoked""").strip()
    return {'worker': worker_result, 'closer': closer_result, 'paused': paused, 'closing': closing, 'live': int(live)}


def snapshot(cluster, db, q):
    """Every row the three operations could lock or write for quorum q."""
    return json.loads(cluster.sql(db, f"""select json_build_object(
      'quorum',(select to_jsonb(x) from quorum_private.quorums x where id='{q}'),
      'memberships',(select jsonb_agg(to_jsonb(m) order by m::text) from quorum_private.memberships m where quorum_id='{q}'),
      'invitations',(select jsonb_agg(to_jsonb(i) order by i::text) from quorum_private.id_invitations i where quorum_id='{q}'))""").strip())


def isolation_refused(cluster, db, operation, level):
    """Run one guarded operation in its own backend at a non-READ COMMITTED
    level while a blocker holds every row and advisory lock it could take. A
    call that reached any lock would wait; the guard must fail at once."""
    fixture = prepared(cluster, db)
    q, invite = fixture['quorum_id'], fixture['invite_id']
    actor, op, req, call = {
        'close': (OWNER, 'quorum_set_access', 'iso-close', f"public.quorum_set_access('{q}','github','iso-close')"),
        'create': (OWNER, 'invite_create_open', 'iso-create', f"public.invite_create_open('{q}','iso-create')"),
        'accept': (ANON, 'invite_accept_id', 'iso-accept', f"public.invite_accept_id('{invite}','iso-accept')"),
    }[operation]
    before = snapshot(cluster, db, q)
    blocker = cluster.session(db, 'iso_blocker')
    blocker.send(f"begin; select 1 from quorum_private.quorums where id='{q}' for update;"
                 f" select 1 from quorum_private.id_invitations where id='{invite}' for update;"
                 f" select pg_advisory_xact_lock({advisory_key(actor, op, req)});")
    wait_for(cluster, db, blocker.label, lambda r: r['state'] == 'idle in transaction', 'locks held')
    worker = cluster.session(db, 'iso_' + operation)
    worker.send(f"set session characteristics as transaction isolation level {level};"
                f" begin; select 'level', current_setting('transaction_isolation');"
                f" select set_config('request.jwt.claim.sub','{actor}',true); select {call}; commit;")
    # Finishes while the blocker still holds everything, or times out.
    worker_result = worker.finish()
    blocker.send('rollback;')
    blocker.finish()
    return {'worker': worker_result, 'before': before, 'after': snapshot(cluster, db, q)}


def transaction_ids(output):
    return [line.split('|')[1:] for line in output.splitlines() if line.startswith('txid|')]


def build(cluster):
    """Template databases: every migration through 170000, and that plus the fix."""
    names = sorted(path.name for path in MIGRATIONS.glob('*.sql'))
    fix = next(name for name in names if name.endswith(SERIALIZATION))
    before = [name for name in names if name < fix]
    script = (ROOT / 'tests/quorum_backend_fixture.sql').read_text(encoding='utf-8')
    script += (ROOT / 'skills/quorum/references/server-bootstrap.sql').read_text(encoding='utf-8')
    for name in before:
        script += (MIGRATIONS / name).read_text(encoding='utf-8')
    cluster.sql('postgres', 'create database prefix')
    cluster.sql('prefix', script, single=True)
    cluster.sql('postgres', 'create database fixed template prefix')
    cluster.sql('fixed', (MIGRATIONS / fix).read_text(encoding='utf-8'), single=True)
    return before[-1], fix


def scenario(cluster, template):
    db = 'run_' + secrets.token_hex(4)
    cluster.sql('postgres', f'create database {db} template {template}')
    return db


def run():
    report = {}
    with Cluster() as cluster:
        report['container'] = cluster.name
        report['server_version'] = cluster.sql('postgres', 'show server_version').strip()
        report['previous'], report['migration'] = build(cluster)
        for template in ('prefix', 'fixed'):
            for operation in ('accept', 'create'):
                report[f'{template}_{operation}_first'] = race(cluster, scenario(cluster, template), operation)
            report[f'{template}_close_first'] = close_first(cluster, scenario(cluster, template))
        for level in ISOLATION_LEVELS:
            for operation in ('close', 'create', 'accept'):
                report[f"isolation_{level.replace(' ', '_')}_{operation}"] = isolation_refused(
                    cluster, scenario(cluster, 'fixed'), operation, level)
    report['container_removed'] = cluster.removed
    return report


class FakeDocker:
    """Records docker calls; the created container is listed until removed."""

    def __init__(self, logs='', version='170011', run_error=None):
        self.calls, self.logs, self.version, self.run_error = [], logs, version, run_error
        self.created = False

    def __call__(self, *args, stdin=None, check=True):
        self.calls.append(args)
        out = ''
        if args[0] == 'run':
            self.created = True
            if self.run_error:
                raise self.run_error
        elif args[0] == 'ps':
            out = 'c0ffee\n' if self.created else ''
        elif args[0] == 'rm':
            self.created = False
        elif args[0] == 'logs':
            out = self.logs
        elif args[0] == 'exec' and 'psql' in args:
            out = self.version + '\n'
        return subprocess.CompletedProcess(args, 0, out, '')


class ClusterCleanupTests(unittest.TestCase):
    """Startup failures after the container exists remove exactly that container."""

    def enter(self, fake):
        cluster = Cluster()
        with unittest.mock.patch(__name__ + '.docker', fake), \
                unittest.mock.patch.object(Cluster, 'READY_SECONDS', 0.2), \
                unittest.mock.patch('time.sleep'):
            with self.assertRaises(BaseException) as raised:
                cluster.__enter__()
        return cluster, raised.exception

    def assert_removed(self, cluster, fake):
        run = fake.calls[0]
        self.assertEqual(run[run.index('--network') + 1], 'none')
        self.assertFalse({'-p', '--publish', '-P', '--publish-all'} & set(run))
        self.assertIn(cluster.label, run)
        self.assertIn(('ps', '-a', '-q', '--filter', 'label=' + cluster.label), fake.calls)
        self.assertEqual([call for call in fake.calls if call[0] == 'rm'], [('rm', '-f', '-v', 'c0ffee')])
        self.assertFalse(fake.created)
        self.assertTrue(cluster.removed)

    def test_readiness_timeout_removes_container(self):
        fake = FakeDocker(logs='still initializing')
        cluster, error = self.enter(fake)
        self.assertIn('did not start', str(error))
        self.assert_removed(cluster, fake)

    def test_wrong_server_version_removes_container(self):
        fake = FakeDocker(logs='init process complete', version='160009')
        cluster, error = self.enter(fake)
        self.assertIn('PostgreSQL 17 required', str(error))
        self.assert_removed(cluster, fake)

    def test_failure_after_create_inside_docker_run_removes_container(self):
        fake = FakeDocker(run_error=subprocess.TimeoutExpired(['docker', 'run'], 120))
        cluster, error = self.enter(fake)
        self.assertIsInstance(error, subprocess.TimeoutExpired)
        self.assert_removed(cluster, fake)

    def test_interrupt_during_readiness_removes_container(self):
        fake = FakeDocker()
        with unittest.mock.patch(__name__ + '.docker', fake), \
                unittest.mock.patch('time.sleep', side_effect=KeyboardInterrupt):
            cluster = Cluster()
            with self.assertRaises(KeyboardInterrupt):
                cluster.__enter__()
        self.assert_removed(cluster, fake)

    def test_success_does_not_remove_until_exit(self):
        fake = FakeDocker(logs='init process complete')
        with unittest.mock.patch(__name__ + '.docker', fake):
            with Cluster() as cluster:
                self.assertFalse([call for call in fake.calls if call[0] == 'rm'])
        self.assert_removed(cluster, fake)


class CloseRaceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if os.environ.get('QUORUM_RACE_DOCKER') != '1':
            raise unittest.SkipTest('set QUORUM_RACE_DOCKER=1 to run the disposable PostgreSQL 17 race')
        cls.report = run()

    def independent(self, result, *names):
        pids = {result[name]['pid'] for name in names}
        self.assertEqual(len(pids), len(names), result)
        xids = [transaction_ids(result[key][1]) for key in ('worker', 'closer')]
        self.assertTrue(all(xids), result)
        self.assertNotEqual(xids[0][0][0], xids[1][0][0])

    def test_container_was_disposable_and_removed(self):
        self.assertTrue(self.report['server_version'].startswith('17'))
        self.assertTrue(self.report['container_removed'])

    def test_unserialized_accept_leaves_anonymous_member_after_close(self):
        # Proves the harness forces the reviewed interleaving on the pre-fix chain.
        result = self.report['prefix_accept_first']
        self.assertEqual(result['worker'][0], 0, result)
        self.assertEqual(result['closer'][0], 0, result)
        self.assertIn(result['paused']['pid'], result['closing']['blocking'])
        self.assertEqual(result['state']['access'], 'github')
        self.assertEqual(result['state']['live_anonymous'], 1)

    def test_unserialized_create_leaves_open_invitation_after_close(self):
        result = self.report['prefix_create_first']
        self.assertEqual(result['state']['access'], 'github')
        self.assertEqual(result['state']['live_open_invites'], 1)

    def test_serialized_close_revokes_membership_accepted_first(self):
        result = self.report['fixed_accept_first']
        self.independent(result, 'paused', 'closing')
        self.assertIn(result['paused']['pid'], result['closing']['blocking'])
        self.assertEqual(result['worker'][0], 0, result)
        self.assertIn('membership_id', result['worker'][1])
        self.assertEqual(result['closer'][0], 0, result)
        self.assertEqual(result['state'], {'access': 'github', 'live_anonymous': 0, 'live_open_invites': 0})

    def test_serialized_close_revokes_invitation_created_first(self):
        result = self.report['fixed_create_first']
        self.independent(result, 'paused', 'closing')
        self.assertEqual(result['closing']['wait_event_type'], 'Lock')
        self.assertIn(result['paused']['pid'], result['closing']['blocking'])
        self.assertEqual(result['state'], {'access': 'github', 'live_anonymous': 0, 'live_open_invites': 0})

    def test_acceptance_after_close_is_denied(self):
        for template in ('prefix', 'fixed'):
            result = self.report[f'{template}_close_first']
            self.independent(result, 'paused', 'closing')
            self.assertEqual(result['closer'][0], 0, result)
            self.assertNotEqual(result['worker'][0], 0, result)
            self.assertIn('invite_denied', result['worker'][2])
            self.assertEqual(result['live'], 0)

    def test_non_read_committed_isolation_is_refused_before_any_lock_or_write(self):
        # A stale REPEATABLE READ/SERIALIZABLE snapshot would skip the core's rereads.
        for level in ISOLATION_LEVELS:
            for operation in ('close', 'create', 'accept'):
                with self.subTest(level=level, operation=operation):
                    result = self.report[f"isolation_{level.replace(' ', '_')}_{operation}"]
                    code, out, err = result['worker']
                    self.assertIn('level|' + level, out)
                    self.assertNotEqual(code, 0, result)
                    self.assertIn('open_access_requires_read_committed', err)
                    self.assertEqual(result['after'], result['before'])
                    before = result['before']
                    self.assertEqual(before['quorum']['access'], 'open')
                    self.assertEqual(len(before['invitations']), 1)
                    self.assertIsNone(before['invitations'][0]['accepted_by'])
                    self.assertFalse(before['invitations'][0]['revoked'])


if __name__ == '__main__':
    if '--report' in sys.argv:
        print(json.dumps(run(), indent=1, default=str))
    else:
        unittest.main()
