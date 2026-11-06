"""#130 / PR #133 review: a join's resume check counts a Claude worker slot exactly when its rebind succeeds.

Each row builds one slot state, then asserts `live_binding(...) is not None` iff `session worker rebind`
succeeds. The two documented non-rebind rows are explicit: a hand-over an interrupted attest can still finish
(rebind says `rebind_pending`, the attest retry succeeds) and a service-side revocation, which no local
read-only check can see (rebind says `agent_revoked`). Fake clocks, local fixtures and a fake API only.
"""
import os
from unittest.mock import patch

from quorum_test_support import isolated_profiles
from test_quorum_claude_worker import OTHER_SID, OWNER_PROOF, SID, OwnerApi, claude
import test_quorum_claude_worker_rebind as rebind_tests
from test_quorum_claude_worker_rebind import NEW_HANDLE, revoked


def interrupted(case, api):
    """Rebind, then an attest that supersedes the predecessor and crashes before the successor activates."""
    import quorum_claude_worker as worker
    old, _ = case.members(api)
    successor = case.rebind(api, old)[1]['data']['slot_id']
    real, calls = worker._actor_binding, []
    def crash(*args):
        calls.append(args)
        if len(calls) == 2: raise OSError('crash before the actor ledger names the new binding')
        return real(*args)
    with patch('quorum_claude_worker._actor_binding', side_effect=crash):
        with case.assertRaises(OSError): case.attest(successor, NEW_HANDLE)
    case.assertEqual(case.setting(old)['terminal_reason'], 'rebound')
    return old, successor


def active(case, api):
    return case.members(api)[0]


def reserved(case, api):
    old = active(case, api)
    case.assertEqual(case.rebind(api, old)[0], 0)  # A prepared, unattested successor.
    return old


def activated(case, api):
    old = active(case, api)
    case.restart(api, old)
    return old


def limbo(case, api):
    return interrupted(case, api)[0]


def lapsed(case, api):
    old, _ = interrupted(case, api)
    case.now += 121  # The unactivated successor's pending window ends.
    return old


def owner_stopped(case, api):
    import quorum_claude_worker as worker
    old = active(case, api)
    with claude(): worker.stop_slot(case.root, old)
    return old


def reception_stopped(case, api):
    old = active(case, api)
    case.assertEqual(case.worker(api, 'stop')[0], 0)
    return old


def permission_expired(case, api):
    old, _ = case.bound(api)  # The default lifetime has a permission deadline.
    case.now += 3601
    return old


def never_attested(case, api):
    return case.prepare(case.accept_scope())['slot_id']


def never_attested_expired(case, api):
    slot = never_attested(case, api)
    case.now += 121
    return slot


def other_directory(case, api):
    old = active(case, api)
    elsewhere = case.base / 'elsewhere'
    elsewhere.mkdir()
    os.chdir(elsewhere)
    return old


def revoked_at_service(case, api):
    old = active(case, api)
    api.answers['agent_status'] = revoked
    return old


# (row, builder, live, rebind succeeds, sid)
ROWS = [
    ('active', active, True, True, SID),
    ('active_with_reserved_successor', reserved, True, True, SID),
    ('activated_successor', activated, True, True, SID),
    ('interrupted_successor_lapsed', lapsed, True, True, SID),
    ('owner_stopped', owner_stopped, False, False, SID),
    ('reception_stopped', reception_stopped, False, False, SID),
    ('permission_expired', permission_expired, False, False, SID),
    ('never_attested', never_attested, False, False, SID),
    ('never_attested_expired', never_attested_expired, False, False, SID),
    ('other_directory', other_directory, False, False, SID),
    ('other_session', active, False, False, OTHER_SID),
]


@OWNER_PROOF
class ResumeTableTests(rebind_tests.RebindTests):
    """live_binding != None iff rebind succeeds, for every slot state."""

    def live(self, sid=SID, quorum='team'):
        from quorum_resume import live_binding
        with claude(sid): return live_binding(self.root, quorum, sid=sid)

    def check(self, builder, live, succeeds, sid):
        api = OwnerApi()
        slot = builder(self, api)
        found = self.live(sid)
        self.assertEqual(found is not None, live, found)
        if found is not None:
            self.assertEqual((found['route'], found['state'], found['next_step'][-4:-1]),
                             ('claude-worker', 'rebindable', ['session', 'worker', 'rebind']))
            # The named slot itself rebinds.
            slot = found['slot_id']
        # Another quorum never counts.
        self.assertIsNone(self.live(sid, 'other-team'))
        code, result, text = self.rebind(api, slot, sid=sid)
        self.assertEqual(code == 0, succeeds, text)

    @isolated_profiles
    def test_interrupted_hand_over_still_finishable_counts_and_its_attest_retry_completes_it(self):
        api = OwnerApi()
        old, successor = interrupted(self, api)
        found = self.live()
        self.assertEqual((found['route'], found['state'], found['slot_id']),
                         ('claude-worker', 'hand_over_pending', successor))
        self.assertEqual(found['next_step'][-5:-2], ['worker', 'attest', successor])
        self.refused(self.rebind(api, old), 'native_worker_binding_denied', 'rebind_pending')
        self.attest(successor, NEW_HANDLE)
        self.assertEqual(self.live()['slot_id'], successor)

    @isolated_profiles
    def test_a_service_revocation_is_the_one_check_left_to_rebind(self):
        api = OwnerApi()
        old = revoked_at_service(self, api)
        self.assertIsNotNone(self.live())
        self.refused(self.rebind(api, old), 'native_worker_binding_denied', 'agent_revoked')


FOREIGN = 300


def crowd(root, count=FOREIGN):
    """Slot-like directories that sort before any real slot and never hold this chat's binding."""
    workers = root / 'claude-workers'
    for index in range(count):
        path = workers / ('00000000-0000-4000-8000-%012d' % index)
        path.mkdir(mode=0o700)


@OWNER_PROOF
class ResumeScanTests(rebind_tests.RebindTests):
    """The resume lookup is keyed by this chat's SID, never truncated; a hit bound refuses instead."""

    def live(self, sid=SID, quorum='team'):
        from quorum_resume import live_binding
        with claude(sid): return live_binding(self.root, quorum, sid=sid)

    def prepare_cli(self):
        import json
        return self.cli(None, 'session', 'worker', 'prepare', '--json-stdin',
                        stdin=json.dumps(self.accept_scope()).encode())

    @isolated_profiles
    def test_three_hundred_foreign_or_expired_slots_never_hide_the_resumable_one(self):
        api = OwnerApi()
        old = active(self, api)
        crowd(self.root)
        # Real foreign and expired slots too: another chat's pending slots and this chat's lapsed ones.
        for _ in range(3): self.prepare(self.accept_scope(quorum_slug='other-team'), sid=OTHER_SID)
        for _ in range(3): self.prepare(self.accept_scope(quorum_slug='other-team'))
        self.now += 121
        self.assertGreater(len(list((self.root / 'claude-workers').iterdir())), 256)
        self.assertEqual(self.live()['slot_id'], old)
        code, result, text = self.prepare_cli()
        self.assertEqual((code, result['code'], result['data']['slot_id']), (3, 'binding_exists_resume', old), text)

    @isolated_profiles
    def test_a_hit_bound_refuses_acceptance_instead_of_assuming_no_binding(self):
        api = OwnerApi()
        active(self, api)
        for limit in ('SESSION_ACTOR_LIMIT', 'INDEX_SCAN_LIMIT'):
            with self.subTest(limit=limit), patch('quorum_claude_worker.' + limit, 0):
                code, result, text = self.prepare_cli()
                self.assertEqual((code, result['code'], result['data']['invitation']),
                                 (3, 'binding_scan_incomplete', 'unused'), text)
                self.assertIn(result['data']['reason'], ('session_limit', 'index_limit'))


def _row_test(builder, live, succeeds, sid):
    @isolated_profiles
    def test(self): self.check(builder, live, succeeds, sid)
    return test


for _name, _builder, _live, _succeeds, _sid in ROWS:
    setattr(ResumeTableTests, 'test_row_' + _name, _row_test(_builder, _live, _succeeds, _sid))

# RebindTests' own tests already run in their module.
for _name in [name for name in vars(rebind_tests.RebindTests) if name.startswith('test_')]:
    setattr(ResumeTableTests, _name, None)
    setattr(ResumeScanTests, _name, None)
