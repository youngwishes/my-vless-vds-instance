from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, final

if TYPE_CHECKING:
    from src.api.schemas import SnapshotDTO
    from src.services.snapshot_runtime import AgentRuntimeState


@final
@dataclass(kw_only=True, slots=True, frozen=True)
class GetSnapshotService:
    state: AgentRuntimeState

    def __call__(self) -> SnapshotDTO | None:
        return self.state.read().snapshot


__all__ = ("GetSnapshotService",)
