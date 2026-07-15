from __future__ import annotations

import hashlib
import json
import os
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from src.api.schemas import AccessDTO, SnapshotDTO
from src.domain import InvalidSnapshotError
from src.observability import Observability
from src.services import (
    ApplyCheckpoint,
    ApplySnapshotService,
    RecoveryStatus,
    StartupRestoreService,
)
from src.storage import SnapshotRecoveryError, SnapshotStore


class SimulatedCrash(BaseException):
    pass


class FakeXray:
    def __init__(self) -> None:
        self.accesses: tuple[AccessDTO, ...] = ()
        self.calls: list[tuple[AccessDTO, ...]] = []

    def __call__(self, *, accesses: tuple[AccessDTO, ...]) -> None:
        self.accesses = tuple(accesses)
        self.calls.append(self.accesses)


def _snapshot(revision: int, access_id: int) -> SnapshotDTO:
    payload = {
        "schema_version": "1.0",
        "snapshot_revision": revision,
        "accesses": [
            {
                "access_id": access_id,
                "uuid": f"00000000-0000-4000-8000-{access_id:012x}",
                "access_revision": revision,
            }
        ],
    }
    canonical = json.dumps(
        payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode()
    payload["snapshot_hash"] = hashlib.sha256(canonical).hexdigest()
    return SnapshotDTO.model_validate(payload)


def _crash_at(expected: ApplyCheckpoint):
    def hook(*, checkpoint: ApplyCheckpoint) -> None:
        if checkpoint is expected:
            raise SimulatedCrash

    return hook


def _apply(
    *, store: SnapshotStore, xray: FakeXray, checkpoint_hook=lambda **_: None
) -> ApplySnapshotService:
    return ApplySnapshotService(
        apply_accesses=xray,
        store=store,
        observer=Observability(),
        checkpoint_hook=checkpoint_hook,
    )


def test_crash_before_xray_keeps_old_durable_snapshot_and_runtime(tmp_path: Path) -> None:
    store = SnapshotStore(path=tmp_path / "snapshot.json")
    old = _snapshot(1, 1)
    new = _snapshot(2, 2)
    store.save(snapshot=old)
    xray = FakeXray()
    xray(accesses=old.accesses)

    with pytest.raises(SimulatedCrash):
        _apply(
            store=store,
            xray=xray,
            checkpoint_hook=_crash_at(ApplyCheckpoint.BEFORE_XRAY_APPLY),
        )(snapshot=new)

    assert store.load() == old
    assert xray.accesses == old.accesses


def test_restart_after_xray_before_persist_reapplies_old_and_is_only_recovery_ready(
    tmp_path: Path,
) -> None:
    store = SnapshotStore(path=tmp_path / "snapshot.json")
    old = _snapshot(1, 1)
    new = _snapshot(2, 2)
    store.save(snapshot=old)
    crashed_xray = FakeXray()

    with pytest.raises(SimulatedCrash):
        _apply(
            store=store,
            xray=crashed_xray,
            checkpoint_hook=_crash_at(
                ApplyCheckpoint.AFTER_XRAY_APPLY_BEFORE_PERSISTENCE
            ),
        )(snapshot=new)
    assert crashed_xray.accesses == new.accesses
    assert store.load() == old

    restarted_xray = FakeXray()
    state = StartupRestoreService(apply_accesses=restarted_xray, store=store)()

    assert restarted_xray.accesses == old.accesses
    assert state.status is RecoveryStatus.RECOVERY_READY
    assert state.serving_ready is False
    assert state.snapshot_revision == old.snapshot_revision
    with pytest.raises(FrozenInstanceError):
        state.snapshot_revision = 9  # type: ignore[misc]


def test_restart_after_durable_persist_restores_new_and_retry_is_idempotent(
    tmp_path: Path,
) -> None:
    store = SnapshotStore(path=tmp_path / "snapshot.json")
    old = _snapshot(1, 1)
    new = _snapshot(2, 2)
    store.save(snapshot=old)
    crashed_xray = FakeXray()

    with pytest.raises(SimulatedCrash):
        _apply(
            store=store,
            xray=crashed_xray,
            checkpoint_hook=_crash_at(
                ApplyCheckpoint.AFTER_DURABLE_RENAME_BEFORE_RETURN
            ),
        )(snapshot=new)
    assert store.load() == new

    restarted_xray = FakeXray()
    state = StartupRestoreService(apply_accesses=restarted_xray, store=store)()
    assert state.status is RecoveryStatus.RECOVERY_READY
    assert restarted_xray.accesses == new.accesses

    assert _apply(store=store, xray=restarted_xray)(snapshot=new) == new
    assert store.load() == new
    assert restarted_xray.accesses == new.accesses


def test_no_snapshot_is_not_ready_and_does_not_touch_xray(tmp_path: Path) -> None:
    xray = FakeXray()

    state = StartupRestoreService(
        apply_accesses=xray,
        store=SnapshotStore(path=tmp_path / "snapshot.json"),
    )()

    assert state.status is RecoveryStatus.NO_SNAPSHOT_NOT_READY
    assert state.serving_ready is False
    assert state.snapshot_revision is None
    assert xray.calls == []


@pytest.mark.parametrize("invalid_mode", [None, 0o640])
def test_torn_or_permission_invalid_state_cannot_become_recovery_ready(
    tmp_path: Path, invalid_mode: int | None
) -> None:
    path = tmp_path / "snapshot.json"
    path.write_text("{", encoding="utf-8")
    path.chmod(0o600 if invalid_mode is None else invalid_mode)
    xray = FakeXray()

    with pytest.raises(SnapshotRecoveryError):
        StartupRestoreService(
            apply_accesses=xray, store=SnapshotStore(path=path)
        )()

    assert xray.calls == []


def test_invalid_desired_snapshot_is_rejected_before_checkpoint_or_xray(
    tmp_path: Path,
) -> None:
    valid = _snapshot(1, 1)
    invalid = valid.model_copy(update={"snapshot_hash": "0" * 64})
    checkpoints: list[ApplyCheckpoint] = []
    xray = FakeXray()

    with pytest.raises(InvalidSnapshotError, match="hash"):
        ApplySnapshotService(
            apply_accesses=xray,
            store=SnapshotStore(path=tmp_path / "snapshot.json"),
            observer=Observability(),
            checkpoint_hook=lambda *, checkpoint: checkpoints.append(checkpoint),
        )(snapshot=invalid)

    assert checkpoints == []
    assert xray.calls == []


def test_permission_race_during_startup_load_never_reaches_xray_or_ready_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "snapshot.json"
    store = SnapshotStore(path=path)
    store.save(snapshot=_snapshot(1, 1))
    xray = FakeXray()
    real_read = os.read

    def chmod_during_read(descriptor: int, size: int) -> bytes:
        path.chmod(0o640)
        return real_read(descriptor, size)

    monkeypatch.setattr(os, "read", chmod_during_read)

    with pytest.raises(SnapshotRecoveryError):
        StartupRestoreService(apply_accesses=xray, store=store)()

    assert xray.calls == []


def test_startup_validation_error_has_no_underlying_exception_chain() -> None:
    invalid = _snapshot(1, 1).model_copy(update={"snapshot_hash": "0" * 64})
    xray = FakeXray()

    class InvalidStore:
        def load(self) -> SnapshotDTO:
            return invalid

        def save(self, *, snapshot: SnapshotDTO) -> None:
            del snapshot

    with pytest.raises(SnapshotRecoveryError) as captured:
        StartupRestoreService(apply_accesses=xray, store=InvalidStore())()

    assert captured.value.__cause__ is None
    assert captured.value.__context__ is None
    assert xray.calls == []
