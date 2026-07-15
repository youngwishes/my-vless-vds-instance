from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from threading import RLock
from typing import TYPE_CHECKING, Protocol, final

from src.domain import validate_snapshot

if TYPE_CHECKING:
    from src.api.schemas import SnapshotDTO


class Readiness(StrEnum):
    READY = "READY"
    NOT_READY = "NOT_READY"
    RECOVERY_READY = "RECOVERY_READY"


class ApplySnapshotResultKind(StrEnum):
    APPLIED = "applied"
    NO_OP = "no_op"


class StaleRevisionError(ValueError):
    """The requested revision is older than the durable revision."""


class RevisionConflictError(ValueError):
    """The requested revision conflicts with the durable revision."""


@final
@dataclass(kw_only=True, slots=True, frozen=True)
class RuntimeStatus:
    readiness: Readiness
    snapshot: SnapshotDTO | None


@final
@dataclass(kw_only=True, slots=True, frozen=True)
class ApplySnapshotResult:
    result: ApplySnapshotResultKind
    snapshot: SnapshotDTO


@final
@dataclass(kw_only=True, slots=True, frozen=True)
class AgentRuntimeState:
    lock: RLock = field(default_factory=RLock)
    _status: list[RuntimeStatus] = field(
        default_factory=lambda: [
            RuntimeStatus(readiness=Readiness.NOT_READY, snapshot=None)
        ]
    )

    def read(self) -> RuntimeStatus:
        with self.lock:
            return self._status[0]

    def record_recovery(self, *, snapshot: SnapshotDTO) -> None:
        with self.lock:
            self._status[0] = RuntimeStatus(
                readiness=Readiness.RECOVERY_READY,
                snapshot=snapshot,
            )

    def record_not_ready(self) -> None:
        with self.lock:
            self._status[0] = RuntimeStatus(
                readiness=Readiness.NOT_READY,
                snapshot=self._status[0].snapshot,
            )

    def record_applied(self, *, snapshot: SnapshotDTO, matches: bool) -> None:
        with self.lock:
            self._status[0] = RuntimeStatus(
                readiness=Readiness.READY if matches else Readiness.NOT_READY,
                snapshot=snapshot,
            )


class ApplySnapshot(Protocol):
    def __call__(self, *, snapshot: SnapshotDTO) -> SnapshotDTO: ...


class ExactSetMatches(Protocol):
    def __call__(self, *, accesses: tuple[object, ...]) -> bool: ...


@final
@dataclass(kw_only=True, slots=True, frozen=True)
class SnapshotCoordinatorService:
    state: AgentRuntimeState
    apply_snapshot: ApplySnapshot
    exact_set_matches: ExactSetMatches

    def __call__(self, *, snapshot: SnapshotDTO) -> ApplySnapshotResult:
        validated = validate_snapshot(snapshot)
        with self.state.lock:
            current = self.state.read().snapshot
            if current is not None:
                if validated.snapshot_revision < current.snapshot_revision:
                    self.state.record_not_ready()
                    raise StaleRevisionError
                if validated.snapshot_revision == current.snapshot_revision:
                    if validated.snapshot_hash != current.snapshot_hash:
                        self.state.record_not_ready()
                        raise RevisionConflictError
                    try:
                        matches = self.exact_set_matches(accesses=current.accesses)
                    except BaseException:
                        self.state.record_not_ready()
                        raise
                    self.state.record_applied(snapshot=current, matches=matches)
                    return ApplySnapshotResult(
                        result=ApplySnapshotResultKind.NO_OP,
                        snapshot=current,
                    )

            try:
                applied = self.apply_snapshot(snapshot=validated)
            except BaseException:
                self.state.record_not_ready()
                raise
            self.state.record_applied(snapshot=applied, matches=False)
            try:
                matches = self.exact_set_matches(accesses=applied.accesses)
            except BaseException:
                raise
            self.state.record_applied(snapshot=applied, matches=matches)
            return ApplySnapshotResult(
                result=ApplySnapshotResultKind.APPLIED,
                snapshot=applied,
            )


__all__ = (
    "AgentRuntimeState",
    "ApplySnapshotResult",
    "ApplySnapshotResultKind",
    "Readiness",
    "RevisionConflictError",
    "RuntimeStatus",
    "SnapshotCoordinatorService",
    "StaleRevisionError",
)
