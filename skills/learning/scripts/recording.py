#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import re
import stat
import subprocess
import sys
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path


SCHEMA_VERSION = 2
MAX_EVENTS = 200
MAX_LABEL_CHARS = 500
MAX_SUMMARY_CHARS = 2_000
MAX_EVIDENCE_CHARS = 4_000
MAX_STATE_BYTES = 1_048_576
EVENT_KINDS = ("action", "outcome", "failure", "decision", "correction")
PHASES = ("open", "ending")
SAFE_TOKEN = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9._:/-]*\Z")


class RecordingError(RuntimeError):
    """A recoverable recording-state error with an actionable message."""


def git(cwd: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        ("git", *args), cwd=cwd, capture_output=True, text=True, check=False
    )
    if check and result.returncode:
        detail = result.stderr.strip() or result.stdout.strip() or "git failed"
        raise RecordingError(detail)
    return result


def repository(cwd: Path) -> dict[str, str | None]:
    root_result = git(cwd, "rev-parse", "--show-toplevel", check=False)
    if root_result.returncode:
        raise RecordingError(f"not inside a Git worktree: {cwd}")
    root = Path(root_result.stdout.strip()).resolve()
    git_dir = Path(
        git(root, "rev-parse", "--absolute-git-dir").stdout.strip()
    ).resolve()
    branch_result = git(root, "symbolic-ref", "--quiet", "--short", "HEAD", check=False)
    return {
        "root": str(root),
        "git_dir": str(git_dir),
        "head": git(root, "rev-parse", "HEAD").stdout.strip(),
        "branch": branch_result.stdout.strip()
        if branch_result.returncode == 0
        else None,
    }


class RecordingDirectory:
    __slots__ = ("path", "git_descriptor", "descriptor")

    def __init__(self, path: Path, git_descriptor: int, descriptor: int) -> None:
        self.path = path
        self.git_descriptor = git_descriptor
        self.descriptor = descriptor


def detect_descriptor_backend() -> bool:
    required_flags = ("O_DIRECTORY", "O_NOFOLLOW")
    required_dir_fd = (os.open, os.mkdir, os.stat, os.unlink, os.rename)
    return all(hasattr(os, name) for name in required_flags) and all(
        operation in os.supports_dir_fd for operation in required_dir_fd
    )


DESCRIPTOR_BACKEND_SUPPORTED = detect_descriptor_backend()


def descriptor_backend_supported() -> bool:
    return DESCRIPTOR_BACKEND_SUPPORTED


def directory_flags() -> int:
    if not descriptor_backend_supported():
        raise RecordingError(
            "safe directory descriptors are unavailable on this platform"
        )
    return os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)


def verify_directory_identity(directory: RecordingDirectory) -> None:
    verifier = -1
    try:
        verifier = os.open(
            "learning",
            directory_flags(),
            dir_fd=directory.git_descriptor,
        )
        expected = os.fstat(directory.descriptor)
        actual = os.fstat(verifier)
    except OSError as error:
        raise RecordingError(
            f"recording directory changed during operation: {directory.path}"
        ) from error
    finally:
        if verifier >= 0:
            os.close(verifier)
    if (actual.st_dev, actual.st_ino) != (expected.st_dev, expected.st_ino):
        raise RecordingError(
            f"recording directory changed during operation: {directory.path}"
        )


@contextmanager
def opened_state_directory(
    cwd: Path,
    *,
    create: bool = False,
    current: dict[str, str | None] | None = None,
) -> Iterator[RecordingDirectory | None]:
    repository_state = repository(cwd) if current is None else current
    git_dir = Path(str(repository_state["git_dir"]))
    path = git_dir / "learning"
    if not descriptor_backend_supported():
        action = "write" if create else "inspect"
        raise RecordingError(
            f"learning cannot safely {action} recording state on this platform"
        )
    git_descriptor = -1
    descriptor = -1
    try:
        try:
            git_descriptor = os.open(git_dir, directory_flags())
            if create:
                try:
                    os.mkdir("learning", mode=0o700, dir_fd=git_descriptor)
                except FileExistsError:
                    pass
            try:
                descriptor = os.open(
                    "learning", directory_flags(), dir_fd=git_descriptor
                )
            except FileNotFoundError:
                if create:
                    raise RecordingError(
                        f"recording directory disappeared during creation: {path}"
                    )
            except OSError as error:
                raise RecordingError(
                    f"recording directory must be a real directory inside Git metadata: {path}"
                ) from error
        except RecordingError:
            raise
        except OSError as error:
            raise RecordingError(f"cannot open recording directory: {path}") from error
        if descriptor < 0:
            yield None
            return
        directory = RecordingDirectory(path, git_descriptor, descriptor)
        try:
            yield directory
        except BaseException:
            raise
        else:
            verify_directory_identity(directory)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if git_descriptor >= 0:
            os.close(git_descriptor)


def state_path(cwd: Path) -> Path:
    return Path(str(repository(cwd)["git_dir"])) / "learning" / "active.json"


@contextmanager
def locked(
    cwd: Path, current: dict[str, str | None] | None = None
) -> Iterator[RecordingDirectory]:
    with opened_state_directory(cwd, create=True, current=current) as directory:
        assert directory is not None
        lock = directory.path / "lock"
        try:
            descriptor = os.open(
                "lock",
                os.O_CREAT
                | os.O_EXCL
                | os.O_WRONLY
                | os.O_NOFOLLOW
                | getattr(os, "O_CLOEXEC", 0),
                0o600,
                dir_fd=directory.descriptor,
            )
        except FileExistsError as error:
            raise RecordingError(
                f"recording state is locked at {lock}; verify no writer is active before recovery"
            ) from error
        try:
            os.write(descriptor, f"{os.getpid()}\n".encode())
            yield directory
        finally:
            os.close(descriptor)
            try:
                os.unlink("lock", dir_fd=directory.descriptor)
            except FileNotFoundError:
                pass


def timestamp() -> str:
    return datetime.now(UTC).isoformat()


def valid_sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def validate_state(
    payload: object, current: dict[str, str | None], path: Path
) -> dict[str, object]:
    if not isinstance(payload, dict):
        raise RecordingError(f"recording state must be an object at {path}")
    required = {
        "schema_version",
        "recording_id",
        "revision",
        "phase",
        "started_at",
        "label",
        "repository",
        "events",
        "end_claim",
    }
    schema_version = payload.get("schema_version")
    if type(schema_version) is not int or schema_version not in (1, SCHEMA_VERSION):
        raise RecordingError(f"unsupported recording schema at {path}")
    if schema_version == 1:
        required.add("skill_catalog")
    missing = sorted(required - payload.keys())
    if missing:
        raise RecordingError(f"recording state has missing keys at {path}: {missing}")
    extra = sorted(payload.keys() - required)
    if extra:
        raise RecordingError(f"recording state has unknown keys at {path}: {extra}")
    if not isinstance(payload["recording_id"], str) or not payload["recording_id"]:
        raise RecordingError(f"recording_id must be a non-empty string at {path}")
    if type(payload["revision"]) is not int or payload["revision"] < 0:
        raise RecordingError(f"revision must be a non-negative integer at {path}")
    if payload["phase"] not in PHASES:
        raise RecordingError(f"invalid phase at {path}: {payload['phase']!r}")
    if not isinstance(payload["started_at"], str) or not payload["started_at"]:
        raise RecordingError(f"started_at must be a non-empty string at {path}")
    if payload["label"] is not None and (
        not isinstance(payload["label"], str) or len(payload["label"]) > MAX_LABEL_CHARS
    ):
        raise RecordingError(f"label is malformed or too large at {path}")

    stored_repository = payload["repository"]
    if not isinstance(stored_repository, dict):
        raise RecordingError(f"repository must be an object at {path}")
    repository_keys = {"root", "git_dir", "head", "branch"}
    if set(stored_repository) != repository_keys:
        raise RecordingError(f"repository keys are malformed at {path}")
    if not isinstance(stored_repository["head"], str) or not stored_repository["head"]:
        raise RecordingError(f"repository head is malformed at {path}")
    if stored_repository["branch"] is not None and not isinstance(
        stored_repository["branch"], str
    ):
        raise RecordingError(f"repository branch is malformed at {path}")
    if (
        stored_repository.get("root") != current["root"]
        or stored_repository.get("git_dir") != current["git_dir"]
    ):
        raise RecordingError(
            f"recording at {path} belongs to a different worktree; preserve it and recover there"
        )

    events = payload["events"]
    if not isinstance(events, list):
        raise RecordingError(f"events must be a list at {path}")
    if len(events) > MAX_EVENTS:
        raise RecordingError(f"recording event limit exceeded at {path}")
    for index, event in enumerate(events):
        if not isinstance(event, dict):
            raise RecordingError(f"event {index} must be an object at {path}")
        kind = event.get("kind")
        summary = event.get("summary")
        evidence = event.get("evidence")
        recorded_at = event.get("recorded_at")
        if set(event) != {"recorded_at", "kind", "summary", "evidence"}:
            raise RecordingError(f"event {index} keys are malformed at {path}")
        if not isinstance(recorded_at, str) or not recorded_at:
            raise RecordingError(f"event {index} has an invalid timestamp at {path}")
        if kind not in EVENT_KINDS:
            raise RecordingError(f"event {index} has an invalid kind at {path}")
        if (
            not isinstance(summary, str)
            or not summary
            or len(summary) > MAX_SUMMARY_CHARS
        ):
            raise RecordingError(f"event {index} has an invalid summary at {path}")
        if evidence is not None and (
            not isinstance(evidence, str) or len(evidence) > MAX_EVIDENCE_CHARS
        ):
            raise RecordingError(f"event {index} has invalid evidence at {path}")

    claim = payload["end_claim"]
    if payload["phase"] == "open" and claim is not None:
        raise RecordingError(f"open recording has an end claim at {path}")
    if payload["phase"] == "ending" and (
        not isinstance(claim, dict)
        or set(claim) != {"claim_sha256", "claimed_at", "adoption_count"}
        or not valid_sha256(claim.get("claim_sha256"))
        or not isinstance(claim.get("claimed_at"), str)
        or not claim.get("claimed_at")
        or type(claim.get("adoption_count")) is not int
        or claim["adoption_count"] < 0
    ):
        raise RecordingError(f"ending recording has an invalid claim at {path}")

    if schema_version == 1:
        catalog = payload["skill_catalog"]
        if not isinstance(catalog, list):
            raise RecordingError(f"skill_catalog must be a list at {path}")
        for index, entry in enumerate(catalog):
            if not isinstance(entry, dict) or set(entry) != {
                "canonical_path",
                "sha256",
                "surfaces",
            }:
                raise RecordingError(
                    f"skill catalog entry {index} is malformed at {path}"
                )
            if (
                not isinstance(entry["canonical_path"], str)
                or not entry["canonical_path"]
                or not valid_sha256(entry["sha256"])
            ):
                raise RecordingError(
                    f"skill catalog entry {index} paths are malformed at {path}"
                )
            if not isinstance(entry["surfaces"], list) or not all(
                isinstance(surface, str) and surface for surface in entry["surfaces"]
            ):
                raise RecordingError(
                    f"skill catalog entry {index} surfaces are malformed at {path}"
                )
        normalized = dict(payload)
        normalized.pop("skill_catalog")
        normalized["schema_version"] = SCHEMA_VERSION
        return normalized
    return payload


def regular_entry_exists(
    directory: RecordingDirectory, name: str, description: str
) -> bool:
    path = directory.path / name
    try:
        metadata = os.stat(name, dir_fd=directory.descriptor, follow_symlinks=False)
    except FileNotFoundError:
        return False
    except OSError as error:
        raise RecordingError(f"cannot inspect {description} at {path}") from error
    if not stat.S_ISREG(metadata.st_mode):
        raise RecordingError(f"{description} must be a regular file at {path}")
    return True


def read_limited(descriptor: int, limit: int) -> bytes:
    chunks: list[bytes] = []
    size = 0
    while size <= limit:
        chunk = os.read(descriptor, min(65_536, limit + 1 - size))
        if not chunk:
            break
        chunks.append(chunk)
        size += len(chunk)
    return b"".join(chunks)


def read_active_from(
    current: dict[str, str | None], directory: RecordingDirectory
) -> dict[str, object]:
    path = directory.path / "active.json"
    try:
        descriptor = os.open(
            "active.json",
            os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0),
            dir_fd=directory.descriptor,
        )
    except FileNotFoundError as error:
        raise RecordingError(
            "no active recording; run learning start first"
        ) from error
    except OSError as error:
        raise RecordingError(
            f"recording state must be a regular file at {path}"
        ) from error
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise RecordingError(f"recording state must be a regular file at {path}")
        encoded = read_limited(descriptor, MAX_STATE_BYTES)
    finally:
        os.close(descriptor)
    if len(encoded) > MAX_STATE_BYTES:
        raise RecordingError(
            f"recording state exceeds {MAX_STATE_BYTES} bytes at {path}"
        )
    try:
        raw = json.loads(encoded.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise RecordingError(
            f"recording state is malformed at {path}; preserve it and recover manually"
        ) from error
    return validate_state(raw, current, path)


def read_active(cwd: Path) -> dict[str, object]:
    current = repository(cwd)
    with opened_state_directory(cwd, current=current) as directory:
        if directory is None:
            raise RecordingError("no active recording; run learning start first")
        return read_active_from(current, directory)


def write_all(descriptor: int, encoded: bytes) -> None:
    offset = 0
    while offset < len(encoded):
        offset += os.write(descriptor, encoded[offset:])


def write_atomic_to(
    current: dict[str, str | None],
    directory: RecordingDirectory,
    payload: dict[str, object],
) -> None:
    path = directory.path / "active.json"
    validate_state(payload, current, path)
    verify_directory_identity(directory)
    encoded = (
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode()
    if len(encoded) > MAX_STATE_BYTES:
        raise RecordingError(f"recording state would exceed {MAX_STATE_BYTES} bytes")
    temporary_name = f".active-{uuid.uuid4().hex}.json"
    try:
        descriptor = os.open(
            temporary_name,
            os.O_CREAT
            | os.O_EXCL
            | os.O_WRONLY
            | os.O_NOFOLLOW
            | getattr(os, "O_CLOEXEC", 0),
            0o600,
            dir_fd=directory.descriptor,
        )
        try:
            verify_directory_identity(directory)
            write_all(descriptor, encoded)
            os.fchmod(descriptor, 0o600)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        verify_directory_identity(directory)
        os.replace(
            temporary_name,
            "active.json",
            src_dir_fd=directory.descriptor,
            dst_dir_fd=directory.descriptor,
        )
        os.fsync(directory.descriptor)
    except OSError as error:
        raise RecordingError(f"cannot write recording state at {path}") from error
    finally:
        try:
            os.unlink(temporary_name, dir_fd=directory.descriptor)
        except FileNotFoundError:
            pass
        except OSError as error:
            raise RecordingError(
                f"cannot remove temporary recording state at {directory.path}"
            ) from error


def safe_text(tokens: list[str] | None, field: str, limit: int) -> str | None:
    if tokens is None:
        return None
    if not tokens:
        raise RecordingError(f"{field} requires at least one safe ASCII token")
    for token in tokens:
        if not SAFE_TOKEN.fullmatch(token):
            raise RecordingError(
                f"{field} contains an unsafe token; use only agent-authored safe ASCII tokens"
            )
    value = " ".join(tokens)
    if len(value) > limit:
        raise RecordingError(f"{field} exceeds {limit} characters")
    return value


def mutate(
    cwd: Path, callback: Callable[[dict[str, object]], dict[str, object]]
) -> dict[str, object]:
    current = repository(cwd)
    with locked(cwd, current) as directory:
        payload = callback(read_active_from(current, directory))
        write_atomic_to(current, directory, payload)
        return payload


def start_recording(cwd: Path, label: str | None) -> dict[str, object]:
    current = repository(cwd)
    with locked(cwd, current) as directory:
        if regular_entry_exists(directory, "active.json", "recording state"):
            active = read_active_from(current, directory)
            raise RecordingError(
                f"recording {active['recording_id']} is already active"
            )
        payload: dict[str, object] = {
            "schema_version": SCHEMA_VERSION,
            "recording_id": uuid.uuid4().hex,
            "revision": 0,
            "phase": "open",
            "started_at": timestamp(),
            "label": label,
            "repository": current,
            "events": [],
            "end_claim": None,
        }
        write_atomic_to(current, directory, payload)
        return payload


def add_note(cwd: Path, event: dict[str, object]) -> dict[str, object]:
    def append(payload: dict[str, object]) -> dict[str, object]:
        if payload["phase"] != "open":
            raise RecordingError("recording is in end phase; resume or reopen it")
        events = payload["events"]
        assert isinstance(events, list)
        if len(events) >= MAX_EVENTS:
            raise RecordingError(f"recording event limit is {MAX_EVENTS}")
        events.append(
            {
                "recorded_at": timestamp(),
                "kind": event.get("kind"),
                "summary": event.get("summary"),
                "evidence": event.get("evidence"),
            }
        )
        payload["revision"] = int(payload["revision"]) + 1
        return payload

    return mutate(cwd, append)


def recording_status(cwd: Path) -> dict[str, object]:
    current = repository(cwd)
    if not descriptor_backend_supported():
        directory = Path(str(current["git_dir"])) / "learning"
        try:
            directory.lstat()
        except FileNotFoundError:
            return {
                "active": False,
                "available": False,
                "reason": "safe directory descriptors are unavailable on this platform",
            }
        except OSError as error:
            raise RecordingError(
                f"cannot determine whether recording state exists at {directory}"
            ) from error
        raise RecordingError(
            "existing recording state cannot be safely inspected on this platform"
        )
    with opened_state_directory(cwd, current=current) as directory:
        if directory is None or not regular_entry_exists(
            directory, "active.json", "recording state"
        ):
            return {"active": False}
        payload = read_active_from(current, directory)
    events = payload["events"]
    assert isinstance(events, list)
    return {
        "active": True,
        "recording_id": payload["recording_id"],
        "revision": payload["revision"],
        "phase": payload["phase"],
        "started_at": payload["started_at"],
        "label": payload["label"],
        "event_count": len(events),
    }


def claim_digest(claim_id: str) -> str:
    return hashlib.sha256(claim_id.encode()).hexdigest()


def claim_matches(payload: dict[str, object], claim_id: str) -> bool:
    claim = payload["end_claim"]
    return (
        payload["phase"] == "ending"
        and isinstance(claim, dict)
        and hmac.compare_digest(str(claim["claim_sha256"]), claim_digest(claim_id))
    )


def with_claim(payload: dict[str, object], claim_id: str) -> dict[str, object]:
    snapshot = dict(payload)
    snapshot["claim_id"] = claim_id
    return snapshot


def prepare_end(cwd: Path) -> dict[str, object]:
    claim_id = uuid.uuid4().hex

    def claim(payload: dict[str, object]) -> dict[str, object]:
        if payload["phase"] != "open":
            raise RecordingError(
                "recording is already in end phase; use resume-end with its claim"
            )
        payload["phase"] = "ending"
        payload["revision"] = int(payload["revision"]) + 1
        payload["end_claim"] = {
            "claim_sha256": claim_digest(claim_id),
            "claimed_at": timestamp(),
            "adoption_count": 0,
        }
        return payload

    return with_claim(mutate(cwd, claim), claim_id)


def resume_end(cwd: Path, claim_id: str) -> dict[str, object]:
    payload = read_active(cwd)
    if not claim_matches(payload, claim_id):
        raise RecordingError("claim id does not match the active end phase")
    return with_claim(payload, claim_id)


def adopt_end(
    cwd: Path,
    recording_id: str,
    expected_revision: int,
    user_authorized: bool,
) -> dict[str, object]:
    if not user_authorized:
        raise RecordingError("end adoption requires explicit user authorization")
    current = repository(cwd)
    with locked(cwd, current) as directory:
        payload = read_active_from(current, directory)
        claim = payload["end_claim"]
        if payload["recording_id"] != recording_id:
            raise RecordingError("recording id does not match the active recording")
        if payload["phase"] != "ending" or not isinstance(claim, dict):
            raise RecordingError("recording is not in the active end phase")
        if payload["revision"] != expected_revision:
            raise RecordingError(
                "recording revision changed; inspect status before adoption"
            )
        claim_id = uuid.uuid4().hex
        payload["revision"] = int(payload["revision"]) + 1
        payload["end_claim"] = {
            "claim_sha256": claim_digest(claim_id),
            "claimed_at": timestamp(),
            "adoption_count": int(claim["adoption_count"]) + 1,
        }
        write_atomic_to(current, directory, payload)
        return with_claim(payload, claim_id)


def reopen_recording(cwd: Path, claim_id: str) -> dict[str, object]:
    def reopen(payload: dict[str, object]) -> dict[str, object]:
        if not claim_matches(payload, claim_id):
            raise RecordingError("claim id does not match the active end phase")
        payload["phase"] = "open"
        payload["end_claim"] = None
        payload["revision"] = int(payload["revision"]) + 1
        return payload

    return mutate(cwd, reopen)


def close_recording(
    cwd: Path, recording_id: str, claim_id: str, expected_revision: int
) -> dict[str, object]:
    current = repository(cwd)
    with locked(cwd, current) as directory:
        payload = read_active_from(current, directory)
        if payload["recording_id"] != recording_id:
            raise RecordingError("recording id does not match the active recording")
        if not claim_matches(payload, claim_id):
            raise RecordingError("claim id does not match the active end phase")
        if payload["revision"] != expected_revision:
            raise RecordingError(
                "recording revision changed; resume and re-evaluate before closing"
            )
        verify_directory_identity(directory)
        os.unlink("active.json", dir_fd=directory.descriptor)
    return {"active": False, "recording_id": recording_id}


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        description="Manage worktree-local learning state."
    )
    commands = result.add_subparsers(dest="command", required=True)
    start = commands.add_parser("start", help="open a recording")
    start.add_argument(
        "--label", nargs="+", help="agent-authored safe ASCII label tokens"
    )
    note = commands.add_parser("note", help="record a safe-token checkpoint")
    note.add_argument("--kind", choices=EVENT_KINDS, required=True)
    note.add_argument("--summary", nargs="+", required=True)
    note.add_argument("--evidence", nargs="+")
    commands.add_parser("status", help="show recording status")
    commands.add_parser("prepare-end", help="claim a stable end snapshot")
    resume = commands.add_parser("resume-end", help="resume a claimed end")
    resume.add_argument("--claim-id", required=True)
    adopt = commands.add_parser(
        "adopt-end", help="rotate a lost end claim after approval"
    )
    adopt.add_argument("--recording-id", required=True)
    adopt.add_argument("--revision", required=True, type=int)
    adopt.add_argument("--user-authorized", action="store_true")
    reopen = commands.add_parser("reopen", help="return a claimed end to observation")
    reopen.add_argument("--claim-id", required=True)
    close = commands.add_parser("close", help="compare and close a completed recording")
    close.add_argument("--recording-id", required=True)
    close.add_argument("--claim-id", required=True)
    close.add_argument("--revision", required=True, type=int)
    return result


def main(argv: list[str] | None = None) -> int:
    try:
        arguments = parser().parse_args(argv)
        cwd = Path.cwd()
        if arguments.command == "start":
            result = start_recording(
                cwd, safe_text(arguments.label, "label", MAX_LABEL_CHARS)
            )
        elif arguments.command == "note":
            result = add_note(
                cwd,
                {
                    "kind": arguments.kind,
                    "summary": safe_text(
                        arguments.summary, "summary", MAX_SUMMARY_CHARS
                    ),
                    "evidence": safe_text(
                        arguments.evidence, "evidence", MAX_EVIDENCE_CHARS
                    ),
                },
            )
        elif arguments.command == "status":
            result = recording_status(cwd)
        elif arguments.command == "prepare-end":
            result = prepare_end(cwd)
        elif arguments.command == "resume-end":
            result = resume_end(cwd, arguments.claim_id)
        elif arguments.command == "adopt-end":
            result = adopt_end(
                cwd,
                arguments.recording_id,
                arguments.revision,
                arguments.user_authorized,
            )
        elif arguments.command == "reopen":
            result = reopen_recording(cwd, arguments.claim_id)
        else:
            result = close_recording(
                cwd, arguments.recording_id, arguments.claim_id, arguments.revision
            )
    except (RecordingError, OSError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
