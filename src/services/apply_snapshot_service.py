from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Protocol, final

from src.domain import SnapshotError, validate_snapshot
from src.storage import SnapshotRecoveryError

if TYPE_CHECKING:
    from collections.abc import Sequence

    from src.api.schemas import AccessDTO, SnapshotDTO


class ApplyAccesses(Protocol):
    def __call__(self, *, accesses: Sequence[AccessDTO]) -> None: ...


class SnapshotStoreContract(Protocol):
    def save(self, *, snapshot: SnapshotDTO) -> None: ...

    def load(self) -> SnapshotDTO | None: ...


class ApplyCheckpoint(StrEnum):
    BEFORE_XRAY_APPLY = "before_xray_apply"
    AFTER_XRAY_APPLY_BEFORE_PERSISTENCE = "after_xray_apply_before_persistence"
    AFTER_DURABLE_RENAME_BEFORE_RETURN = "after_durable_rename_before_return"


class CheckpointHook(Protocol):
    def __call__(self, *, checkpoint: ApplyCheckpoint) -> None: ...


def noop_checkpoint_hook(*, checkpoint: ApplyCheckpoint) -> None:
    del checkpoint


class RecoveryStatus(StrEnum):
    NO_SNAPSHOT_NOT_READY = "no_snapshot_not_ready"
    RECOVERY_READY = "recovery_ready"


@final
@dataclass(kw_only=True, slots=True, frozen=True)
class RecoveryState:
    status: RecoveryStatus
    snapshot_revision: int | None = None
    snapshot_hash: str | None = None
    serving_ready: bool = False


@final
@dataclass(kw_only=True, slots=True, frozen=True)
class ApplySnapshotService:
    apply_accesses: ApplyAccesses
    store: SnapshotStoreContract
    checkpoint_hook: CheckpointHook = noop_checkpoint_hook

    def __call__(self, *, snapshot: SnapshotDTO) -> SnapshotDTO:
        validated = validate_snapshot(snapshot)
        self.checkpoint_hook(checkpoint=ApplyCheckpoint.BEFORE_XRAY_APPLY)
        self.apply_accesses(accesses=validated.accesses)
        self.checkpoint_hook(
            checkpoint=ApplyCheckpoint.AFTER_XRAY_APPLY_BEFORE_PERSISTENCE
        )
        self.store.save(snapshot=validated)
        self.checkpoint_hook(
            checkpoint=ApplyCheckpoint.AFTER_DURABLE_RENAME_BEFORE_RETURN
        )
        return validated


@final
@dataclass(kw_only=True, slots=True, frozen=True)
class StartupRestoreService:
    apply_accesses: ApplyAccesses
    store: SnapshotStoreContract

    def __call__(self) -> RecoveryState:
        snapshot = self.store.load()
        if snapshot is None:
            return RecoveryState(status=RecoveryStatus.NO_SNAPSHOT_NOT_READY)

        try:
            validated = validate_snapshot(snapshot)
        except SnapshotError as error:
            raise SnapshotRecoveryError(
                "durable snapshot cannot be recovered safely"
            ) from error
        self.apply_accesses(accesses=validated.accesses)
        return RecoveryState(
            status=RecoveryStatus.RECOVERY_READY,
            snapshot_revision=validated.snapshot_revision,
            snapshot_hash=validated.snapshot_hash,
        )


__all__ = (
    "ApplyAccesses",
    "ApplyCheckpoint",
    "ApplySnapshotService",
    "CheckpointHook",
    "RecoveryState",
    "RecoveryStatus",
    "SnapshotStoreContract",
    "StartupRestoreService",
    "noop_checkpoint_hook",
)
