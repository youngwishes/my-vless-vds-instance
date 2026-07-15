from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest

from src.api.schemas import SnapshotDTO
from src.storage import SnapshotRecoveryError, SnapshotStore, SnapshotStoreError


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


def test_replace_failure_keeps_last_durable_snapshot_and_cleans_temp_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "snapshot.json"
    store = SnapshotStore(path=path)
    old = _snapshot(revision=7)
    store.save(snapshot=old)

    def fail_replace(source: str | Path, target: str | Path) -> None:
        raise OSError("simulated replace failure containing sensitive paths")

    monkeypatch.setattr(os, "replace", fail_replace)

    with pytest.raises(
        SnapshotStoreError, match="snapshot could not be stored"
    ) as captured:
        store.save(snapshot=_snapshot(revision=8))

    assert str(path) not in str(captured.value)
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
