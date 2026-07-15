from __future__ import annotations

import json
import os
import stat
import traceback
from pathlib import Path

import pytest

from src.api.schemas import SnapshotDTO
from src.domain import MAX_CANONICAL_BYTES
from src.storage import (
    MAX_PERSISTED_SNAPSHOT_BYTES,
    SnapshotRecoveryError,
    SnapshotStore,
    SnapshotStoreError,
)


def _snapshot(*, revision: int = 7) -> SnapshotDTO:
    payload = {
        "schema_version": "1.0",
        "snapshot_revision": revision,
        "accesses": [
            {
                "access_id": 2,
                "uuid": "01890f47-a2d4-7c11-b3e6-89f40d8639f1",
                "access_revision": 1,
            },
            {
                "access_id": 10,
                "uuid": "2f1c5a63-7bd6-4ac1-86dc-16b7adf580df",
                "access_revision": 3,
            },
        ],
    }
    canonical = json.dumps(
        payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode()
    import hashlib

    payload["snapshot_hash"] = hashlib.sha256(canonical).hexdigest()
    return SnapshotDTO.model_validate(payload)


def test_missing_snapshot_is_a_clean_first_boot(tmp_path: Path) -> None:
    store = SnapshotStore(path=tmp_path / "private" / "snapshot.json")

    assert store.load() is None


def test_save_is_deterministic_private_and_round_trips_complete_snapshot(
    tmp_path: Path,
) -> None:
    path = tmp_path / "private" / "snapshot.json"
    store = SnapshotStore(path=path)
    snapshot = _snapshot()

    store.save(snapshot=snapshot)
    first_bytes = path.read_bytes()
    store.save(snapshot=snapshot)

    assert path.read_bytes() == first_bytes
    assert first_bytes == (
        b'{"accesses":[{"access_id":2,"access_revision":1,'
        b'"uuid":"01890f47-a2d4-7c11-b3e6-89f40d8639f1"},'
        b'{"access_id":10,"access_revision":3,'
        b'"uuid":"2f1c5a63-7bd6-4ac1-86dc-16b7adf580df"}],'
        b'"schema_version":"1.0","snapshot_hash":"'
        + snapshot.snapshot_hash.encode()
        + b'","snapshot_revision":7}'
    )
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert store.load() == snapshot


def test_new_parent_directory_is_exactly_private_despite_process_umask(
    tmp_path: Path,
) -> None:
    path = tmp_path / "private" / "snapshot.json"
    previous_umask = os.umask(0o777)
    try:
        SnapshotStore(path=path).save(snapshot=_snapshot())
    finally:
        os.umask(previous_umask)

    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_save_syscalls_make_new_directory_and_snapshot_entry_durable_in_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "private" / "snapshot.json"
    events: list[str] = []
    real_fsync = os.fsync
    real_replace = os.replace
    containing_parent = tmp_path.stat()

    def observed_fsync(descriptor: int) -> None:
        metadata = os.fstat(descriptor)
        if stat.S_ISREG(metadata.st_mode):
            assert metadata.st_size > 0
            assert os.pread(descriptor, metadata.st_size, 0).startswith(b'{"accesses"')
            events.append("temp-flushed-and-fsynced")
        elif (metadata.st_dev, metadata.st_ino) == (
            containing_parent.st_dev,
            containing_parent.st_ino,
        ):
            events.append("new-directory-parent-fsynced")
        else:
            events.append("snapshot-directory-fsynced")
        real_fsync(descriptor)

    def observed_replace(
        source: str | Path, target: str | Path, **kwargs: int
    ) -> None:
        events.append("replace")
        real_replace(source, target, **kwargs)

    monkeypatch.setattr(os, "fsync", observed_fsync)
    monkeypatch.setattr(os, "replace", observed_replace)

    SnapshotStore(path=path).save(snapshot=_snapshot())

    replace_index = events.index("replace")
    assert events.index("new-directory-parent-fsynced") < replace_index
    assert events.index("temp-flushed-and-fsynced") < replace_index
    assert "snapshot-directory-fsynced" in events[replace_index + 1 :]


@pytest.mark.parametrize("unsafe_kind", ["symlink", "file"])
def test_save_rejects_unsafe_parent_components_without_following_them(
    tmp_path: Path, unsafe_kind: str
) -> None:
    unsafe_parent = tmp_path / "unsafe"
    if unsafe_kind == "symlink":
        actual_directory = tmp_path / "actual"
        actual_directory.mkdir()
        unsafe_parent.symlink_to(actual_directory)
    else:
        unsafe_parent.write_text("not a directory", encoding="utf-8")
    path = unsafe_parent / "snapshot.json"

    with pytest.raises(SnapshotStoreError, match="stored safely") as captured:
        SnapshotStore(path=path).save(snapshot=_snapshot())

    assert str(path) not in str(captured.value)
    if unsafe_kind == "symlink":
        assert not (actual_directory / "snapshot.json").exists()


def test_replace_failure_keeps_last_durable_snapshot_and_cleans_temp_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "snapshot.json"
    store = SnapshotStore(path=path)
    old = _snapshot(revision=7)
    store.save(snapshot=old)

    def fail_replace(
        source: str | Path, target: str | Path, **kwargs: int
    ) -> None:
        del source, target, kwargs
        raise OSError("simulated replace failure containing sensitive paths")

    monkeypatch.setattr(os, "replace", fail_replace)

    with pytest.raises(
        SnapshotStoreError, match="snapshot could not be stored"
    ) as captured:
        store.save(snapshot=_snapshot(revision=8))

    assert str(path) not in str(captured.value)
    assert captured.value.__cause__ is None
    assert captured.value.__context__ is None
    assert store.load() == old
    assert list(tmp_path.glob(f".{path.name}.*.tmp")) == []


@pytest.mark.parametrize(
    "writer",
    [
        lambda path: path.write_bytes(b'{"schema_version":'),
        lambda path: path.write_text(
            '{"schema_version":"1.0","snapshot_revision":1,'
            '"snapshot_hash":"0000000000000000000000000000000000000000000000000000000000000000",'
            '"accesses":[]}',
            encoding="utf-8",
        ),
        lambda path: path.write_bytes(b"\xff"),
    ],
    ids=("torn-json", "invalid-hash", "invalid-utf8"),
)
def test_invalid_persisted_content_raises_safe_recovery_error(
    tmp_path: Path, writer: object
) -> None:
    path = tmp_path / "snapshot.json"
    writer(path)  # type: ignore[operator]
    path.chmod(0o600)

    with pytest.raises(SnapshotRecoveryError, match="cannot be recovered") as captured:
        SnapshotStore(path=path).load()

    assert "00000000" not in str(captured.value)
    assert str(path) not in str(captured.value)


def test_malformed_dto_traceback_chain_does_not_leak_uuid_payload_or_path(
    tmp_path: Path,
) -> None:
    path = tmp_path / "snapshot.json"
    uuid = "DEADBEEF-DEAD-4BAD-8BAD-DEADBEEFCAFE"
    raw_payload = (
        '{"accesses":[{"access_id":1,"access_revision":1,'
        f'"uuid":"{uuid}"}}],"schema_version":"1.0",'
        '"snapshot_hash":"0000000000000000000000000000000000000000000000000000000000000000",'
        '"snapshot_revision":1}'
    )
    path.write_text(raw_payload, encoding="utf-8")
    path.chmod(0o600)

    with pytest.raises(SnapshotRecoveryError) as captured:
        SnapshotStore(path=path).load()

    formatted_chain = "".join(
        traceback.format_exception(captured.type, captured.value, captured.tb)
    )
    assert uuid not in formatted_chain
    assert raw_payload not in formatted_chain
    assert str(path) not in formatted_chain
    assert captured.value.__cause__ is None
    assert captured.value.__context__ is None


def test_wrong_permissions_are_rejected(tmp_path: Path) -> None:
    path = tmp_path / "snapshot.json"
    path.write_bytes(b"{}")
    path.chmod(0o640)

    with pytest.raises(SnapshotRecoveryError, match="permissions"):
        SnapshotStore(path=path).load()


def test_symlink_and_non_regular_files_are_rejected(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.write_bytes(b"{}")
    target.chmod(0o600)
    symlink = tmp_path / "snapshot.json"
    symlink.symlink_to(target)

    with pytest.raises(SnapshotRecoveryError, match="regular file"):
        SnapshotStore(path=symlink).load()

    symlink.unlink()
    symlink.mkdir()
    with pytest.raises(SnapshotRecoveryError, match="regular file"):
        SnapshotStore(path=symlink).load()


def test_persisted_size_limit_has_bounded_envelope_overhead() -> None:
    assert MAX_PERSISTED_SNAPSHOT_BYTES == MAX_CANONICAL_BYTES + 128


def test_oversized_snapshot_is_rejected_before_any_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "snapshot.json"
    with path.open("wb") as oversized:
        oversized.truncate(MAX_PERSISTED_SNAPSHOT_BYTES + 1)
    path.chmod(0o600)

    def unexpected_read(descriptor: int, size: int) -> bytes:
        raise AssertionError(f"unexpected read of {size} bytes from fd {descriptor}")

    def unexpected_fdopen(descriptor: int, mode: str) -> object:
        raise AssertionError(f"unexpected fdopen of fd {descriptor} in mode {mode}")

    monkeypatch.setattr(os, "read", unexpected_read)
    monkeypatch.setattr(os, "fdopen", unexpected_fdopen)

    with pytest.raises(SnapshotRecoveryError, match="cannot be recovered") as captured:
        SnapshotStore(path=path).load()

    assert str(path) not in str(captured.value)


@pytest.mark.parametrize("race", ["growth", "truncation"])
def test_load_is_bounded_and_rejects_file_size_races(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, race: str
) -> None:
    path = tmp_path / "snapshot.json"
    SnapshotStore(path=path).save(snapshot=_snapshot())
    original_size = path.stat().st_size
    real_read = os.read
    observed_sizes: list[int] = []

    def racing_read(descriptor: int, size: int) -> bytes:
        observed_sizes.append(size)
        if race == "growth":
            with path.open("ab") as persisted:
                persisted.write(b"x")
        else:
            with path.open("r+b") as persisted:
                persisted.truncate(original_size - 1)
        return real_read(descriptor, size)

    monkeypatch.setattr(os, "read", racing_read)

    with pytest.raises(SnapshotRecoveryError, match="cannot be recovered"):
        SnapshotStore(path=path).load()

    assert observed_sizes == [MAX_PERSISTED_SNAPSHOT_BYTES + 1]


def test_load_rejects_permission_change_during_bounded_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "snapshot.json"
    SnapshotStore(path=path).save(snapshot=_snapshot())
    real_read = os.read

    def chmod_during_read(descriptor: int, size: int) -> bytes:
        path.chmod(0o640)
        return real_read(descriptor, size)

    monkeypatch.setattr(os, "read", chmod_during_read)

    with pytest.raises(SnapshotRecoveryError) as captured:
        SnapshotStore(path=path).load()

    assert str(path) not in str(captured.value)
