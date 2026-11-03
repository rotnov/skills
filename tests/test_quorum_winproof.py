"""Windows owner/DACL proof for the native worker inboxes; no credentials, models or hosted calls.

Descriptor decisions and the patched native surface run everywhere; real NTFS cases run in the
Windows CI job and check that a refusal never changes the inspected owner or DACL.
"""
import ctypes
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / 'skills/quorum/scripts'
sys.path.insert(0, str(SCRIPTS))
import quorum_winproof as proof  # noqa: E402
from quorum_test_support import anchor_owner_proof_fixtures  # noqa: E402

anchor_owner_proof_fixtures()

USER = bytes.fromhex('0105000000000005150000000100000002000000030000000a000000')  # S-1-5-21-1-2-3-10
OTHER = bytes.fromhex('0105000000000005150000000100000002000000030000000b000000')  # S-1-5-21-1-2-3-11
SYSTEM = bytes.fromhex('010100000000000512000000')
ADMINS = bytes.fromhex('01020000000000052000000020020000')
USERS = bytes.fromhex('01020000000000052000000021020000')  # S-1-5-32-545
USER_TEXT = proof.sid_text(USER)
OWNERS = (USER_TEXT, USER_TEXT)
FULL, READ, MODIFY = 0x1F01FF, 0x1200A9, 0x1301BF
APPEND_ONLY = 0x4  # FILE_ADD_SUBDIRECTORY: create entries only, like a sticky directory


def sid_bytes(text):
    parts = [int(part) for part in text.split('-')[1:]]
    return bytes([parts[0], len(parts) - 2]) + parts[1].to_bytes(6, 'big') + struct.pack('<' + 'I' * (len(parts) - 2),
                                                                                         *parts[2:])


def descriptor(owner, aces, *, protected=True, dacl=True, revision=2):
    body = b''.join(struct.pack('<BBHI', kind, flags, 8 + len(sid), mask) + sid for kind, flags, mask, sid in aces)
    acl = struct.pack('<BBHHH', revision, 0, 8 + len(body), len(aces), 0) + body
    control = 0x8000 | (0x4 if dacl else 0) | (0x1000 if protected else 0)
    return struct.pack('<BBHIIII', 1, 0, control, 20, 0, 0, 20 + len(owner) if dacl else 0) + owner + (acl if dacl else b'')


def accepted(raw, kind, policy='claude', owners=OWNERS):
    try: proof.evaluate(raw, kind, policy, USER_TEXT, owners)
    except proof.Unsafe: return False
    return True


class DescriptorDecisionTests(unittest.TestCase):
    def test_well_known_sids_round_trip(self):
        for raw in (USER, SYSTEM, ADMINS, USERS):
            self.assertEqual(sid_bytes(proof.sid_text(raw)), raw)
        self.assertEqual(proof.sid_text(sid_bytes(proof.TRUSTED_INSTALLER)), proof.TRUSTED_INSTALLER)
        self.assertEqual((proof.sid_text(SYSTEM), proof.sid_text(ADMINS)), (proof.SYSTEM, proof.ADMINISTRATORS))

    def test_created_private_descriptor_is_the_exact_private_shape(self):
        for directory in (True, False):
            raw = proof.private_descriptor(USER, directory)
            owner, protected, aces = proof.parse(raw)
            self.assertEqual((owner, protected), (USER_TEXT, True))
            self.assertEqual(aces, [(0, 3 if directory else 0, FULL, USER_TEXT)])
            self.assertTrue(accepted(raw, 'private' if directory else 'file'))

    def test_private_directory_requires_user_owner_and_a_protected_explicit_owner_only_dacl(self):
        good = [(0, 3, FULL, USER)]
        self.assertTrue(accepted(descriptor(USER, good), 'private'))
        # SYSTEM and Administrators are root-equivalent and may appear; a deny never widens access.
        self.assertTrue(accepted(descriptor(USER, good + [(0, 3, FULL, SYSTEM), (0, 3, FULL, ADMINS),
                                                           (1, 0, FULL, USERS)]), 'private'))
        for name, raw in {
                'foreign owner': descriptor(OTHER, good),
                'administrators owner outside an elevated token': descriptor(ADMINS, good),
                'extra read ACE': descriptor(USER, good + [(0, 0, READ, USERS)]),
                'extra inherit-only ACE': descriptor(USER, good + [(0, 0x0B, READ, OTHER)]),
                'inherited ACE': descriptor(USER, [(0, 0x13, FULL, USER)]),
                'unprotected DACL': descriptor(USER, good, protected=False),
                'NULL DACL': descriptor(USER, [], dacl=False),
                'object ACE': descriptor(USER, [(5, 0, FULL, USER)]),
                'callback ACE': descriptor(USER, [(9, 0, FULL, USER)])}.items():
            with self.subTest(name):
                self.assertFalse(accepted(raw, 'private'))

    def test_elevated_token_default_owner_is_accepted_for_objects_it_creates(self):
        raw = descriptor(ADMINS, [(0, 3, FULL, USER)])
        self.assertTrue(accepted(raw, 'private', owners=(USER_TEXT, proof.ADMINISTRATORS)))

    def test_private_file_may_inherit_only_trusted_rules_from_its_verified_parent(self):
        self.assertTrue(accepted(descriptor(USER, [(0, 0x10, FULL, USER)], protected=False), 'file'))
        self.assertFalse(accepted(descriptor(USER, [(0, 0x10, FULL, USER), (0, 0x10, READ, USERS)],
                                             protected=False), 'file'))
        self.assertFalse(accepted(descriptor(OTHER, [(0, 0, FULL, USER)]), 'file'))

    def test_ancestor_allows_trusted_owners_and_others_adding_entries_but_never_replacing(self):
        windows_root = [(0, 0, FULL, ADMINS), (0, 0, FULL, SYSTEM), (0, 0, READ, USERS),
                        (0, 0, APPEND_ONLY, USERS), (0, 0x0B, MODIFY, USERS)]  # inherit-only Modify
        for owner in (USER, SYSTEM, ADMINS, sid_bytes(proof.TRUSTED_INSTALLER)):
            with self.subTest(owner=proof.sid_text(owner)):
                self.assertTrue(accepted(descriptor(owner, windows_root, protected=False), 'ancestor'))
        self.assertFalse(accepted(descriptor(OTHER, windows_root), 'ancestor'))
        # An AppContainer capability with full control (seen on real AppData) is the user's own,
        # but only on ancestors: private paths stay owner-only.
        capability = sid_bytes('S-1-15-3-3557520199-3666692283-3112367039-3524159787-2791857073-3163583606-3692855932')
        package = sid_bytes('S-1-15-2-1')
        for sid in (capability, package):
            self.assertTrue(accepted(descriptor(ADMINS, windows_root + [(0, 0, FULL, sid)]), 'ancestor'))
            self.assertFalse(accepted(descriptor(USER, [(0, 3, FULL, USER), (0, 0, FULL, sid)]), 'private'))
            self.assertFalse(accepted(descriptor(USER, [(0, 0, FULL, USER), (0, 0, FULL, sid)]), 'file', 'codex'))
        # Beside a capability with full control, another account's write still refuses.
        self.assertFalse(accepted(descriptor(ADMINS, windows_root + [(0, 0, FULL, capability), (0, 0, MODIFY, OTHER)]),
                                  'ancestor'))
        # OWNER RIGHTS grants only the trusted owner (seen on an elevated runner's profile directories);
        # an untrusted owner still refuses, and private paths never accept it.
        owner_rights = sid_bytes(proof.OWNER_RIGHTS)
        self.assertTrue(accepted(descriptor(ADMINS, [(0, 3, FULL, SYSTEM), (0, 3, FULL, ADMINS), (0, 3, FULL, owner_rights)]),
                                 'ancestor'))
        self.assertFalse(accepted(descriptor(OTHER, [(0, 3, FULL, owner_rights)]), 'ancestor'))
        # CPython's Windows mkdir(mode=0o700): user-owned, protected {SYSTEM, Administrators, OWNER RIGHTS}.
        cpython = [(0, 3, FULL, SYSTEM), (0, 3, FULL, ADMINS), (0, 3, FULL, owner_rights)]
        self.assertTrue(accepted(descriptor(USER, cpython), 'private'))
        self.assertTrue(accepted(descriptor(USER, [(0, 0, FULL, owner_rights)], protected=False), 'file'))
        self.assertTrue(accepted(descriptor(USER, cpython), 'private', 'codex'))
        for kind, policy in (('private', 'claude'), ('file', 'claude'), ('private', 'codex')):
            with self.subTest(kind=kind, policy=policy):
                self.assertFalse(accepted(descriptor(OTHER, cpython), kind, policy))
        self.assertFalse(accepted(descriptor(USER, cpython + [(0, 0, READ, OTHER)]), 'private'))
        # A sandbox user group or another account with Modify (seen on real %TEMP%) stays refused.
        self.assertFalse(accepted(descriptor(USER, windows_root + [(0, 3, MODIFY, OTHER)]), 'ancestor'))
        for right in (0x10000, 0x40000, 0x80000, 0x40, 0x10000000, MODIFY):
            with self.subTest(right=hex(right)):
                self.assertFalse(accepted(descriptor(SYSTEM, windows_root + [(0, 0, right, USERS)]), 'ancestor'))

    def test_codex_tree_mirrors_not_group_or_other_writable(self):
        profile = [(0, 0x10, FULL, SYSTEM), (0, 0x10, FULL, ADMINS), (0, 0x10, FULL, USER)]
        self.assertTrue(accepted(descriptor(USER, profile, protected=False), 'private', 'codex'))
        self.assertTrue(accepted(descriptor(USER, profile + [(0, 0, READ, USERS)], protected=False), 'file', 'codex'))
        for right in (0x2, 0x4, 0x10, 0x40, 0x100, 0x10000, 0x40000, 0x80000, 0x40000000, 0x10000000):
            with self.subTest(right=hex(right)):
                self.assertFalse(accepted(descriptor(USER, profile + [(0, 0, right, USERS)]), 'file', 'codex'))
        self.assertFalse(accepted(descriptor(OTHER, profile), 'file', 'codex'))

    def test_malformed_descriptors_refuse(self):
        good = descriptor(USER, [(0, 3, FULL, USER)])
        bad = [b'', good[:19], b'\x02' + good[1:], good[:2] + struct.pack('<H', 0x1004) + good[4:],
               good[:-1], descriptor(USER, [(0, 3, FULL, USER)], revision=3),
               good[:4] + struct.pack('<I', 0) + good[8:]]
        for index, raw in enumerate(bad):
            with self.subTest(index=index), self.assertRaises(proof.Unsafe): proof.parse(raw)

    def test_component_names_resolve_identically_through_win32_parsing(self):
        for name in ('state', 'RUNNER~1', '.codex', 'claude-workers', "private $data ' é"):
            self.assertEqual(proof.component(name), name)
        for name in ('', '.', '..', 'a\\b', 'a/b', 'a:b', 'trail.', 'trail ', 'CON', 'nul.txt', 'com1', 'x' * 256,
                     'a\x00', 'a*'):
            with self.subTest(name=name), self.assertRaises(proof.Unsafe): proof.component(name)

    def test_directory_entries_are_parsed_without_opening_them(self):
        def record(name, attributes, last=False):
            encoded = name.encode('utf-16-le')
            size = (68 + len(encoded) + 7) & ~7
            head = struct.pack('<II', 0 if last else size, 0) + b'\0' * 48 + struct.pack('<III', attributes, len(encoded), 0)
            return (head + encoded).ljust(size, b'\0')
        raw = record('.', 0x10) + record('2026', 0x10) + record('link', 0x410, last=True)
        self.assertEqual(proof.directory_entries(raw), [('.', 0x10), ('2026', 0x10), ('link', 0x410)])
        with self.assertRaises(proof.Unsafe): proof.directory_entries(raw[:70])


class PatchedNativeTests(unittest.TestCase):
    """Synthetic Windows host: handle facts and descriptors come from patched native calls."""

    def tearDown(self): proof._bindings = None

    def host(self, attributes=0x10, links=1, pending=False, directory=True, security=None):
        from ctypes import wintypes as w
        calls = []
        raw = security if security is not None else proof.private_descriptor(USER, directory)

        def file_info(handle, kind, output, size):
            calls.append(('info', handle, kind))
            data = (struct.pack('<II', attributes, 0) if kind == 9
                    else struct.pack('<qqIBB2x', 0, 0, links, pending, directory))
            ctypes.memmove(output, data, len(data))
            return 1

        def object_security(handle, information, output, size, needed):
            calls.append(('security', handle, information))
            ctypes.cast(needed, ctypes.POINTER(w.DWORD))[0] = len(raw)
            if not output: return 0
            ctypes.memmove(output, raw, len(raw))
            return 1

        kernel = type('Kernel', (), {'GetFileInformationByHandleEx': staticmethod(file_info)})
        advapi = type('Security', (), {'GetKernelObjectSecurity': staticmethod(object_security)})
        proof._bindings = dict(ctypes=ctypes, w=w, kernel=kernel, security=advapi, user=USER_TEXT, owners=OWNERS,
                               user_sid=USER)
        return calls

    def verify(self, kind, policy='claude'):
        with patch.object(proof, '_handle', side_effect=lambda fd: 1000 + fd):
            try: proof.verify(5, kind, policy)
            except proof.Unsafe: return False
        return True

    def test_verified_directory_reads_attributes_then_owner_and_dacl_only(self):
        calls = self.host()
        self.assertTrue(self.verify('private'))
        # FileAttributeTagInfo, FileStandardInfo, then OWNER|DACL; never the SACL, never a write.
        self.assertEqual(calls, [('info', 1005, 9), ('info', 1005, 1), ('security', 1005, 5),
                                 ('security', 1005, 5)])

    def test_reparse_point_wrong_type_link_count_or_pending_delete_refuse_before_security(self):
        for options, kind in (({'attributes': 0x410}, 'private'), ({'attributes': 0x10, 'directory': False}, 'private'),
                              ({'attributes': 0x80, 'directory': False, 'links': 2}, 'file'),
                              ({'attributes': 0x80, 'directory': False, 'pending': True}, 'file'),
                              ({'attributes': 0x10}, 'file')):
            with self.subTest(**options):
                calls = self.host(**options)
                self.assertFalse(self.verify(kind))
                self.assertFalse(any(call[0] == 'security' for call in calls))

    def test_foreign_owner_extra_or_inherited_rule_refuse(self):
        for raw in (descriptor(OTHER, [(0, 3, FULL, USER)]), descriptor(USER, [(0, 3, FULL, USER), (0, 0, READ, USERS)]),
                    descriptor(USER, [(0, 0x13, FULL, USER)], protected=False)):
            with self.subTest(raw=raw.hex()):
                self.host(security=raw)
                self.assertFalse(self.verify('private'))

    def test_unavailable_native_host_fails_closed_for_both_routes(self):
        import quorum_claude_worker as worker
        import quorum_native_inbox as inbox
        from quorum_native_inbox import NativeInboxError
        proof._bindings = None
        with patch.object(proof.os, 'name', 'nt'), \
                patch.object(ctypes, 'WinDLL', side_effect=OSError('no native host'), create=True):
            self.assertFalse(proof.available())
            self.assertFalse(inbox._ownership_proof_available())
            self.assertFalse(worker.proof_available())
            with patch.dict(os.environ, {'CLAUDE_CODE_SESSION_ID': '01a15a2b-12f9-73c3-9310-c6b3c7774415'}), \
                    self.assertRaises(NativeInboxError) as raised:
                worker.actual_session()
        self.assertEqual((raised.exception.code, raised.exception.reason),
                         ('native_worker_unavailable', 'ownership_unverified'))
        self.assertIsNone(proof._bindings)


# --- Real NTFS ----------------------------------------------------------------------------------

def run(*command):
    return subprocess.run(list(command), capture_output=True, text=True, timeout=30)


@unittest.skipUnless(os.name == 'nt', 'Real NTFS owner/DACL proof runs in the Windows CI job')
class NtfsTests(unittest.TestCase):
    def setUp(self):
        from test_quorum_windows_acl import inspect_native_acl
        self.inspect = inspect_native_acl
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(os.path.realpath(temporary.name))
        self.opened = []
        self.addCleanup(lambda: [os.close(fd) for fd in self.opened])

    def keep(self, fd):
        self.opened.append(fd)
        return fd

    def chain(self, path, policy='claude'):
        """The verified ancestor chain of `path`, from its drive root."""
        parent = self.keep(proof.open_root(path.parts[0]))
        self.diagnosed(parent, path.parts[0], policy)
        for depth, name in enumerate(path.parts[1:], 2):
            parent = self.keep(proof.open_at(parent, name, directory=True))
            self.diagnosed(parent, str(Path(*path.parts[:depth])), policy)
        return parent

    def diagnosed(self, fd, where, policy):
        """Verify an ancestor; on refusal name this test's own path and its decoded owner and rules."""
        try: proof.verify(fd, 'ancestor', policy)
        except proof.Unsafe:
            api = proof._api()
            owner, protected, aces = proof.parse(proof.descriptor_of(api, proof._handle(fd)))
            self.fail(f'ancestor refused: {where} owner={owner} protected={protected} '
                      f'aces={[(kind, hex(flags), hex(mask), sid) for kind, flags, mask, sid in aces]} '
                      f'token={api["owners"]}')

    def private(self, name='state'):
        """A directory created by the proof's own owner-only creation, under the verified temp chain."""
        parent = self.chain(self.base)
        fd = self.keep(proof.open_verified(parent, name, 'private', 'claude', create=True))
        return self.base / name, parent, fd

    def refused(self, path, kind, policy='claude', parent=None):
        """`kind` refuses `path` and leaves its owner and DACL exactly as they were."""
        before = self.inspect(path)
        parent = parent if parent is not None else self.chain(path.parent)
        with self.assertRaises(proof.Unsafe):
            fd = proof.open_verified(parent, path.name, kind, policy)
            os.close(fd)
        self.assertEqual(self.inspect(path), before, 'a refusal never changes the owner or DACL')

    def test_proof_is_available_and_the_temp_chain_is_a_safe_ancestor_chain(self):
        self.assertTrue(proof.available())
        self.chain(self.base)
        self.chain(self.base, 'codex')

    def test_owner_only_creation_passes_and_pins_against_rename(self):
        path, parent, fd = self.private()
        acl = self.inspect(path)
        self.assertTrue(acl['protected'])
        self.assertEqual(acl['owner'], acl['current_user'])
        self.assertEqual([rule['sid'] for rule in acl['rules']], [acl['current_user']])
        # Files: one created owner-only, one inheriting from the verified protected parent.
        os.close(proof.open_at(fd, 'ledger.sqlite', directory=False, create=True, exclusive=True, write=True))
        (path / 'listener.lock').write_bytes(b'0')
        for name in ('ledger.sqlite', 'listener.lock'):
            os.close(proof.open_verified(fd, name, 'file', 'claude'))
        # quorum_os.protect output is a private directory too.
        from quorum_os import protect
        protected = self.base / 'protected'
        protected.mkdir()
        protect(protected, directory=True)
        os.close(proof.open_verified(parent, 'protected', 'private', 'claude'))
        # While held, the verified directory cannot be renamed; after release it can.
        with self.assertRaises(OSError): path.rename(self.base / 'moved')
        os.close(self.opened.pop(self.opened.index(fd)))
        path.rename(self.base / 'moved')

    def test_descriptor_relative_rename_unlink_and_identity(self):
        path, _, fd = self.private()
        os.close(proof.open_at(fd, 'capability.json', directory=False, create=True, exclusive=True, write=True))
        temporary = proof.open_at(fd, '.capability-1.tmp', directory=False, create=True, exclusive=True, write=True)
        os.write(temporary, b'{"version": 1}')
        os.close(temporary)
        proof.rename_at(fd, '.capability-1.tmp', 'capability.json')
        self.assertEqual((path / 'capability.json').read_bytes(), b'{"version": 1}')
        self.assertFalse((path / '.capability-1.tmp').exists())
        check = proof.open_verified(fd, 'capability.json', 'file', 'claude')
        try: self.assertEqual(proof.path_identity(path / 'capability.json'), (os.fstat(check).st_dev, os.fstat(check).st_ino))
        finally: os.close(check)
        proof.unlink_at(fd, 'capability.json')
        self.assertFalse((path / 'capability.json').exists())
        with self.assertRaises(FileNotFoundError): proof.unlink_at(fd, 'capability.json')
        os.close(proof.open_at(fd, 'again', directory=True, create=True, exclusive=True))
        with self.assertRaises(FileExistsError):
            os.close(proof.open_at(fd, 'again', directory=True, create=True, exclusive=True))

    def test_extra_rule_refuses_private_but_read_only_rule_stays_codex_acceptable(self):
        path, _, _ = self.private()
        self.assertEqual(run('icacls', str(path), '/grant', '*S-1-5-32-545:(R)').returncode, 0)
        self.refused(path, 'private')
        os.close(proof.open_verified(self.chain(self.base, 'codex'), path.name, 'private', 'codex'))
        self.assertEqual(run('icacls', str(path), '/grant', '*S-1-5-32-545:(M)').returncode, 0)
        self.refused(path, 'private', 'codex')
        self.refused(path, 'ancestor')

    def test_inherited_rules_refuse_a_private_directory(self):
        plain = self.base / 'plain'
        plain.mkdir()  # Inherits the profile's rules; not protected.
        self.refused(plain, 'private')
        path, _, _ = self.private()
        child = path / 'child'
        child.mkdir()  # Inherits only the user's rule, but inherited all the same.
        self.refused(child, 'private')

    def test_foreign_owner_refuses(self):
        path, _, _ = self.private()
        if run('icacls', str(path), '/setowner', '*S-1-5-19').returncode != 0:  # LOCAL SERVICE
            self.skipTest('this runner cannot assign another owner')
        self.refused(path, 'private')
        self.refused(path, 'private', 'codex')
        self.refused(path, 'ancestor')

    def test_reparse_points_and_hard_links_refuse(self):
        path, _, fd = self.private()
        target = path / 'target'
        target.mkdir()
        self.assertEqual(run('cmd', '/c', 'mklink', '/J', str(path / 'junction'), str(target)).returncode, 0)
        self.assertIn(('junction', True), [(name, bool(attributes & proof.ATTRIBUTE_REPARSE))
                                           for name, attributes in proof.children(fd)])
        for kind in ('ancestor', 'private'):
            with self.subTest(kind=kind), self.assertRaises(proof.Unsafe):
                os.close(proof.open_verified(fd, 'junction', kind, 'claude'))
        self.assertTrue(target.exists() and (path / 'junction').is_junction())
        data = path / 'data.json'
        data.write_text('{}')
        try: os.symlink(data, path / 'symlink.json')
        except OSError: pass
        else:
            with self.assertRaises(proof.Unsafe): os.close(proof.open_verified(fd, 'symlink.json', 'file', 'claude'))
        os.link(data, path / 'hardlink.json')
        with self.assertRaises(proof.Unsafe): os.close(proof.open_verified(fd, 'data.json', 'file', 'claude'))

    def test_network_or_unknown_roots_refuse(self):
        for root in ('\\\\server\\share\\', 'C:', 'C:/', 'relative'):
            with self.subTest(root=root), self.assertRaises(proof.Unsafe): proof.open_root(root)


if __name__ == '__main__':
    unittest.main()
