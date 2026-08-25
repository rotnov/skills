from __future__ import annotations

import contextlib
import hashlib
import importlib.util
import io
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "skills/learning/scripts/recording.py"
SPEC = importlib.util.spec_from_file_location("learning_recording", SCRIPT)
assert SPEC and SPEC.loader
recording = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(recording)


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ("git", *args), cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()


class RecorderTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def init_repo(self, name: str = "repo") -> Path:
        repo = self.root / name
        repo.mkdir()
        git(repo, "init", "-q")
        (repo / "README.md").write_text("# Fixture\n", encoding="utf-8")
        git(repo, "add", "README.md")
        subprocess.run(
            (
                "git",
                "-c",
                "user.name=Recorder Test",
                "-c",
                "user.email=recorder@example.invalid",
                "commit",
                "-q",
                "-m",
                "fixture",
            ),
            cwd=repo,
            check=True,
        )
        return repo

    def state_file(self, repo: Path) -> Path:
        return recording.state_path(repo)

    def read_json(self, path: Path) -> dict[str, object]:
        return json.loads(path.read_text(encoding="utf-8"))

    def run_main(self, repo: Path, *args: str) -> tuple[int, dict[str, object]]:
        output = io.StringIO()
        with mock.patch("pathlib.Path.cwd", return_value=repo):
            with contextlib.redirect_stdout(output):
                result = recording.main(list(args))
        return result, json.loads(output.getvalue())

    def test_start_keeps_state_outside_worktree(self) -> None:
        repo = self.init_repo()

        active = recording.start_recording(repo, None)

        path = recording.state_path(repo)
        git_dir = Path(git(repo, "rev-parse", "--absolute-git-dir")).resolve()
        self.assertEqual(active["phase"], "open")
        self.assertEqual(active["revision"], 0)
        self.assertEqual(active["schema_version"], 2)
        self.assertNotIn("skill_catalog", active)
        self.assertTrue(path.resolve().is_relative_to(git_dir))
        self.assertEqual(git(repo, "status", "--porcelain"), "")

    def test_schema_one_state_normalizes_and_remains_resumable(self) -> None:
        repo = self.init_repo()
        recording.start_recording(repo, None)
        path = self.state_file(repo)
        legacy = self.read_json(path)
        legacy["schema_version"] = 1
        legacy["skill_catalog"] = []
        path.write_text(json.dumps(legacy), encoding="utf-8")

        normalized = recording.read_active(repo)

        self.assertEqual(normalized["schema_version"], 2)
        self.assertNotIn("skill_catalog", normalized)
        recording.add_note(
            repo,
            {"kind": "decision", "summary": "portable routing", "evidence": None},
        )
        persisted = self.read_json(path)
        self.assertEqual(persisted["schema_version"], 2)
        self.assertNotIn("skill_catalog", persisted)

    def test_second_start_preserves_original_recording(self) -> None:
        repo = self.init_repo()
        first = recording.start_recording(repo, "first")

        with self.assertRaisesRegex(recording.RecordingError, "already active"):
            recording.start_recording(repo, "second")

        self.assertEqual(self.read_json(self.state_file(repo))["recording_id"], first["recording_id"])

    def test_safe_tokens_round_trip(self) -> None:
        repo = self.init_repo()
        self.assertEqual(
            self.run_main(repo, "start", "--label", "safe", "label")[0], 0
        )
        self.assertEqual(
            self.run_main(
                repo,
                "note",
                "--kind",
                "outcome",
                "--summary",
                "safe",
                "summary",
                "--evidence",
                "safe/path",
                "evidence",
            )[0],
            0,
        )
        active = recording.read_active(repo)
        self.assertEqual(active["label"], "safe label")
        self.assertEqual(active["events"][0]["summary"], "safe summary")
        self.assertEqual(active["events"][0]["evidence"], "safe/path evidence")

    def test_unsafe_tokens_fail_before_state_mutation(self) -> None:
        repo = self.init_repo()
        for token in ("$(touch-marker)", "quote'", "line\nbreak", "é"):
            with self.subTest(token=token):
                with self.assertRaisesRegex(recording.RecordingError, "safe ASCII token"):
                    recording.safe_text([token], "label", recording.MAX_LABEL_CHARS)
                self.assertEqual(recording.recording_status(repo), {"active": False})

    def test_notes_are_bounded_and_require_open_phase(self) -> None:
        repo = self.init_repo()
        recording.start_recording(repo, None)
        event = {"kind": "action", "summary": "one", "evidence": None}
        with mock.patch.object(recording, "MAX_EVENTS", 1):
            recording.add_note(repo, event)
            with self.assertRaisesRegex(recording.RecordingError, "event limit"):
                recording.add_note(repo, event)
        recording.prepare_end(repo)
        with self.assertRaisesRegex(recording.RecordingError, "end phase"):
            recording.add_note(repo, event)

    @unittest.skipUnless(
        recording.DESCRIPTOR_BACKEND_SUPPORTED,
        "cooperative writer locking requires the descriptor backend",
    )
    def test_cooperating_writer_waits_for_lock_release_before_mutating(self) -> None:
        repo = self.init_repo()
        started = recording.start_recording(repo, None)
        path = self.state_file(repo)
        original_bytes = path.read_bytes()
        original_revision = int(started["revision"])
        event = {
            "kind": "action",
            "summary": "record after lock release",
            "evidence": "test",
        }

        current = recording.repository(repo)
        with recording.locked(repo, current):
            with self.assertRaisesRegex(recording.RecordingError, "state is locked"):
                recording.add_note(repo, event)
            self.assertEqual(path.read_bytes(), original_bytes)
            self.assertEqual(self.read_json(path)["revision"], original_revision)

        mutated = recording.add_note(repo, event)

        self.assertEqual(mutated["revision"], original_revision + 1)
        self.assertEqual(mutated["events"][0]["kind"], event["kind"])
        self.assertEqual(mutated["events"][0]["summary"], event["summary"])
        self.assertEqual(mutated["events"][0]["evidence"], event["evidence"])
        self.assertNotEqual(path.read_bytes(), original_bytes)

    def test_prepare_resume_and_compare_close(self) -> None:
        repo = self.init_repo()
        started = recording.start_recording(repo, None)
        prepared = recording.prepare_end(repo)
        claim = prepared["claim_id"]
        persisted = self.read_json(self.state_file(repo))

        self.assertEqual(
            persisted["end_claim"]["claim_sha256"],
            hashlib.sha256(claim.encode()).hexdigest(),
        )
        self.assertNotIn("claim_id", persisted)
        self.assertNotIn("claim_id", recording.recording_status(repo))
        with self.assertRaisesRegex(recording.RecordingError, "already in end phase"):
            recording.prepare_end(repo)
        self.assertEqual(recording.resume_end(repo, claim)["claim_id"], claim)
        with self.assertRaisesRegex(recording.RecordingError, "claim id"):
            recording.resume_end(repo, "wrong")
        with self.assertRaisesRegex(recording.RecordingError, "recording id"):
            recording.close_recording(repo, "wrong", claim, prepared["revision"])
        with self.assertRaisesRegex(recording.RecordingError, "revision changed"):
            recording.close_recording(repo, started["recording_id"], claim, 0)
        self.assertEqual(
            recording.close_recording(
                repo, started["recording_id"], claim, prepared["revision"]
            ),
            {"active": False, "recording_id": started["recording_id"]},
        )
        self.assertEqual(recording.recording_status(repo), {"active": False})

    def test_lost_claim_requires_authorized_adoption_and_rotates(self) -> None:
        repo = self.init_repo()
        started = recording.start_recording(repo, None)
        prepared = recording.prepare_end(repo)
        old_claim = prepared["claim_id"]
        revision = prepared["revision"]

        with self.assertRaisesRegex(recording.RecordingError, "explicit user authorization"):
            recording.adopt_end(repo, started["recording_id"], revision, False)
        with self.assertRaisesRegex(recording.RecordingError, "recording id"):
            recording.adopt_end(repo, "wrong", revision, True)
        with self.assertRaisesRegex(recording.RecordingError, "revision changed"):
            recording.adopt_end(repo, started["recording_id"], revision - 1, True)

        adopted = recording.adopt_end(repo, started["recording_id"], revision, True)
        new_claim = adopted["claim_id"]
        self.assertNotEqual(new_claim, old_claim)
        self.assertEqual(adopted["revision"], revision + 1)
        self.assertEqual(adopted["end_claim"]["adoption_count"], 1)
        with self.assertRaisesRegex(recording.RecordingError, "claim id"):
            recording.resume_end(repo, old_claim)
        with self.assertRaisesRegex(recording.RecordingError, "claim id"):
            recording.reopen_recording(repo, old_claim)
        with self.assertRaisesRegex(recording.RecordingError, "claim id"):
            recording.close_recording(
                repo, started["recording_id"], old_claim, adopted["revision"]
            )
        self.assertEqual(recording.resume_end(repo, new_claim)["claim_id"], new_claim)

    def test_reopen_preserves_evidence(self) -> None:
        repo = self.init_repo()
        recording.start_recording(repo, None)
        first = recording.prepare_end(repo)
        recording.reopen_recording(repo, first["claim_id"])
        recording.add_note(
            repo,
            {"kind": "correction", "summary": "retain evidence", "evidence": "test"},
        )

        second = recording.prepare_end(repo)

        self.assertNotEqual(first["claim_id"], second["claim_id"])
        self.assertEqual(second["events"][0]["kind"], "correction")
        self.assertEqual(second["events"][0]["summary"], "retain evidence")

    def test_malformed_or_stale_states_fail_closed(self) -> None:
        cases = (
            ("wrong event type", "invalid kind", lambda state: state["events"][0].__setitem__("kind", "wrong"), True),
            ("missing recording id", "missing keys", lambda state: state.pop("recording_id"), False),
            ("unknown phase", "invalid phase", lambda state: state.__setitem__("phase", "unknown"), False),
            ("boolean schema", "unsupported recording schema", lambda state: state.__setitem__("schema_version", True), False),
            ("boolean revision", "revision must be", lambda state: state.__setitem__("revision", True), False),
            ("invalid end claim", "invalid claim", lambda state: state["end_claim"].__setitem__("claim_sha256", "bad"), "ending"),
            ("stale repository root", "different worktree", lambda state: state["repository"].__setitem__("root", "/stale"), False),
        )
        for index, (label, message, mutate, setup) in enumerate(cases):
            with self.subTest(case=label):
                repo = self.init_repo(f"repo-{index}")
                recording.start_recording(repo, None)
                if setup is True:
                    recording.add_note(
                        repo,
                        {"kind": "action", "summary": "valid", "evidence": None},
                    )
                elif setup == "ending":
                    recording.prepare_end(repo)
                path = self.state_file(repo)
                state = self.read_json(path)
                mutate(state)
                path.write_text(json.dumps(state), encoding="utf-8")

                with self.assertRaisesRegex(recording.RecordingError, message):
                    recording.read_active(repo)
                self.assertTrue(path.exists())

    def test_invalid_utf8_and_oversized_states_fail_closed(self) -> None:
        repo = self.init_repo()
        recording.start_recording(repo, None)
        path = self.state_file(repo)
        path.write_bytes(b"\xff")
        with self.assertRaisesRegex(recording.RecordingError, "malformed"):
            recording.read_active(repo)
        path.write_bytes(b" " * (recording.MAX_STATE_BYTES + 1))
        with self.assertRaisesRegex(recording.RecordingError, "exceeds"):
            recording.read_active(repo)

    @unittest.skipIf(os.name == "nt", "descriptor symlink defenses require POSIX")
    def test_state_symlinks_fail_closed(self) -> None:
        for index, dangling in enumerate((False, True)):
            with self.subTest(kind="dangling" if dangling else "regular"):
                repo = self.init_repo(f"state-link-{index}")
                state_dir = Path(git(repo, "rev-parse", "--absolute-git-dir")) / "learning"
                state_dir.mkdir()
                outside = self.root / f"outside-{index}.json"
                if not dangling:
                    outside.write_text("outside\n", encoding="utf-8")
                (state_dir / "active.json").symlink_to(outside)
                with self.assertRaisesRegex(recording.RecordingError, "regular file"):
                    recording.start_recording(repo, None)
                if not dangling:
                    self.assertEqual(outside.read_text(encoding="utf-8"), "outside\n")
                else:
                    self.assertFalse(outside.exists())

        repo = self.init_repo("directory-link")
        git_dir = Path(git(repo, "rev-parse", "--absolute-git-dir"))
        outside_dir = self.root / "outside-directory"
        outside_dir.mkdir()
        (git_dir / "learning").symlink_to(outside_dir, target_is_directory=True)
        with self.assertRaisesRegex(recording.RecordingError, "real directory"):
            recording.start_recording(repo, None)
        self.assertEqual(list(outside_dir.iterdir()), [])

    @unittest.skipIf(os.name == "nt", "descriptor identity defenses require POSIX")
    def test_directory_swap_and_atomic_write_failure_fail_closed(self) -> None:
        repo = self.init_repo("swap-before")
        recording.start_recording(repo, None)
        state_dir = self.state_file(repo).parent
        original_state = (state_dir / "active.json").read_bytes()
        escaped = self.root / "escaped-before"
        replacement_marker = b"replacement unchanged"

        def swap_before_write(payload: dict[str, object]) -> dict[str, object]:
            state_dir.rename(escaped)
            state_dir.mkdir()
            (state_dir / "marker").write_bytes(replacement_marker)
            payload["revision"] = int(payload["revision"]) + 1
            return payload

        with self.assertRaisesRegex(recording.RecordingError, "directory changed"):
            recording.mutate(repo, swap_before_write)
        self.assertEqual((state_dir / "marker").read_bytes(), replacement_marker)
        self.assertEqual((escaped / "active.json").read_bytes(), original_state)

        repo = self.init_repo("swap-during")
        recording.start_recording(repo, None)
        state_dir = self.state_file(repo).parent
        original_state = (state_dir / "active.json").read_bytes()
        escaped = self.root / "escaped-during"
        real_open = os.open
        swapped = False

        def swap_on_child(path: object, flags: int, *args: object, **kwargs: object) -> int:
            nonlocal swapped
            if isinstance(path, str) and path.startswith(".active-") and not swapped:
                swapped = True
                state_dir.rename(escaped)
                state_dir.mkdir()
                (state_dir / "marker").write_bytes(replacement_marker)
            return real_open(path, flags, *args, **kwargs)

        with mock.patch.object(recording.os, "open", side_effect=swap_on_child):
            with self.assertRaisesRegex(recording.RecordingError, "directory changed"):
                recording.add_note(
                    repo,
                    {"kind": "action", "summary": "swap", "evidence": None},
                )
        self.assertEqual((state_dir / "marker").read_bytes(), replacement_marker)
        self.assertEqual((escaped / "active.json").read_bytes(), original_state)

        repo = self.init_repo("write-failure")
        recording.start_recording(repo, None)
        state_dir = self.state_file(repo).parent
        with mock.patch.object(
            recording, "write_all", side_effect=OSError("injected write failure")
        ):
            with self.assertRaisesRegex(recording.RecordingError, "cannot write"):
                recording.add_note(
                    repo,
                    {"kind": "failure", "summary": "injected", "evidence": None},
                )
        self.assertEqual(list(state_dir.glob(".active-*.json")), [])

    @unittest.skipIf(os.name == "nt", "descriptor identity defenses require POSIX")
    def test_directory_swap_during_write_preserves_detached_state(self) -> None:
        repo = self.init_repo("swap-during-write")
        recording.start_recording(repo, None)
        state_dir = self.state_file(repo).parent
        original_state = (state_dir / "active.json").read_bytes()
        escaped = self.root / "escaped-during-write"
        replacement_marker = b"replacement unchanged"
        real_write_all = recording.write_all

        def write_then_swap(descriptor: int, encoded: bytes) -> None:
            real_write_all(descriptor, encoded)
            state_dir.rename(escaped)
            state_dir.mkdir()
            (state_dir / "marker").write_bytes(replacement_marker)

        with mock.patch.object(recording, "write_all", side_effect=write_then_swap):
            with self.assertRaisesRegex(recording.RecordingError, "directory changed"):
                recording.add_note(
                    repo,
                    {"kind": "action", "summary": "swap", "evidence": None},
                )

        self.assertEqual((state_dir / "marker").read_bytes(), replacement_marker)
        self.assertTrue((escaped / "active.json").exists())
        self.assertEqual((escaped / "active.json").read_bytes(), original_state)

    @unittest.skipIf(os.name == "nt", "descriptor identity defenses require POSIX")
    def test_directory_swap_during_claim_check_preserves_detached_state(self) -> None:
        repo = self.init_repo("swap-during-claim-check")
        started = recording.start_recording(repo, None)
        prepared = recording.prepare_end(repo)
        state_dir = self.state_file(repo).parent
        original_state = (state_dir / "active.json").read_bytes()
        escaped = self.root / "escaped-during-claim-check"
        replacement_marker = b"replacement unchanged"
        real_claim_matches = recording.claim_matches

        def check_then_swap(payload: dict[str, object], claim_id: str) -> bool:
            matches = real_claim_matches(payload, claim_id)
            state_dir.rename(escaped)
            state_dir.mkdir()
            (state_dir / "marker").write_bytes(replacement_marker)
            return matches

        with mock.patch.object(
            recording, "claim_matches", side_effect=check_then_swap
        ):
            with self.assertRaisesRegex(recording.RecordingError, "directory changed"):
                recording.close_recording(
                    repo,
                    str(started["recording_id"]),
                    str(prepared["claim_id"]),
                    int(prepared["revision"]),
                )

        self.assertEqual((state_dir / "marker").read_bytes(), replacement_marker)
        self.assertTrue((escaped / "active.json").exists())
        self.assertEqual((escaped / "active.json").read_bytes(), original_state)

    def test_unsupported_descriptor_backend_never_mutates(self) -> None:
        repo = self.init_repo()
        with mock.patch.object(recording, "descriptor_backend_supported", return_value=False):
            self.assertEqual(
                recording.recording_status(repo),
                {
                    "active": False,
                    "available": False,
                    "reason": "safe directory descriptors are unavailable on this platform",
                },
            )
            with self.assertRaisesRegex(recording.RecordingError, "cannot safely write"):
                recording.start_recording(repo, None)
        state_dir = Path(git(repo, "rev-parse", "--absolute-git-dir")) / "learning"
        state_dir.mkdir()
        marker = state_dir / "marker"
        marker.write_text("unchanged", encoding="utf-8")
        with mock.patch.object(recording, "descriptor_backend_supported", return_value=False):
            with self.assertRaisesRegex(recording.RecordingError, "cannot be safely inspected"):
                recording.recording_status(repo)
        self.assertEqual(marker.read_text(encoding="utf-8"), "unchanged")

    def test_linked_worktrees_have_independent_recordings(self) -> None:
        repo = self.init_repo()
        linked = self.root / "linked"
        git(repo, "worktree", "add", "--detach", str(linked), "HEAD")

        first = recording.start_recording(repo, None)
        second = recording.start_recording(linked, None)

        self.assertNotEqual(first["recording_id"], second["recording_id"])
        self.assertNotEqual(recording.state_path(repo), recording.state_path(linked))

    def test_cli_lifecycle_in_real_repository(self) -> None:
        repo = self.init_repo()
        status, started = self.run_main(repo, "start", "--label", "real", "repository")
        self.assertEqual(status, 0)
        status, noted = self.run_main(
            repo,
            "note",
            "--kind",
            "action",
            "--summary",
            "record",
            "evidence",
        )
        self.assertEqual(status, 0)
        self.assertEqual(len(noted["events"]), 1)
        status, prepared = self.run_main(repo, "prepare-end")
        self.assertEqual(status, 0)
        status, resumed = self.run_main(
            repo, "resume-end", "--claim-id", prepared["claim_id"]
        )
        self.assertEqual(status, 0)
        self.assertEqual(resumed["claim_id"], prepared["claim_id"])
        status, closed = self.run_main(
            repo,
            "close",
            "--recording-id",
            started["recording_id"],
            "--claim-id",
            prepared["claim_id"],
            "--revision",
            str(prepared["revision"]),
        )
        self.assertEqual(status, 0)
        self.assertEqual(closed, {"active": False, "recording_id": started["recording_id"]})
        self.assertEqual(recording.recording_status(repo), {"active": False})
        self.assertEqual(git(repo, "status", "--porcelain"), "")

    def test_cli_help_and_outside_git_error(self) -> None:
        help_output = io.StringIO()
        with contextlib.redirect_stdout(help_output):
            with self.assertRaises(SystemExit) as raised:
                recording.main(["prepare-end", "--help"])
        self.assertEqual(raised.exception.code, 0)
        self.assertIn("prepare-end", help_output.getvalue())

        outside = self.root / "outside-git"
        outside.mkdir()
        error = io.StringIO()
        output = io.StringIO()
        with mock.patch("pathlib.Path.cwd", return_value=outside):
            with contextlib.redirect_stdout(output), contextlib.redirect_stderr(error):
                result = recording.main(["status"])
        self.assertEqual(result, 2)
        self.assertEqual(output.getvalue(), "")
        self.assertIn("not inside a Git worktree", error.getvalue())


if __name__ == "__main__":
    unittest.main()
