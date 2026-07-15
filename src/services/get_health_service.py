from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, final

from src.services.snapshot_runtime import AgentRuntimeState, Readiness

if TYPE_CHECKING:
    from src.api.schemas import SnapshotDTO
    from src.services.snapshot_runtime import ExactSetMatches


@final
@dataclass(kw_only=True, slots=True, frozen=True)
class HealthStatus:
    agent_sha: str
    xray_version: str
    xray_image_digest: str
    readiness: Readiness
    snapshot: SnapshotDTO | None


@final
@dataclass(kw_only=True, slots=True, frozen=True)
class GetHealthService:
    state: AgentRuntimeState
    exact_set_matches: ExactSetMatches
    agent_sha: str
    xray_version: str
    xray_image_digest: str

    def __call__(self) -> HealthStatus:
        with self.state.lock:
            status = self.state.read()
            if status.snapshot is not None:
                try:
                    matches = self.exact_set_matches(
                        accesses=status.snapshot.accesses
                    )
                except BaseException:
                    self.state.record_not_ready()
                    raise
                if not matches:
                    self.state.record_not_ready()
                    status = self.state.read()
            return HealthStatus(
                agent_sha=self.agent_sha,
                xray_version=self.xray_version,
                xray_image_digest=self.xray_image_digest,
                readiness=status.readiness,
                snapshot=status.snapshot,
            )


__all__ = ("GetHealthService", "HealthStatus")
