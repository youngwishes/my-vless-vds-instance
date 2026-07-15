from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, final

import grpc

from src.config import Settings
from src.observability import Observability
from src.services import (
    AgentRuntimeState,
    ApplySnapshotService,
    GetHealthService,
    GetSnapshotService,
    SnapshotCoordinatorService,
    StartupRestoreService,
)
from src.storage import SnapshotStore
from src.xray import ApplyExactSetService, ExactSetMatchesService, GrpcXrayClient, ObservedXrayClient


class StartupRestore(Protocol):
    state: AgentRuntimeState

    def __call__(self) -> object: ...


@final
@dataclass(kw_only=True, slots=True, frozen=True)
class AgentServices:
    state: AgentRuntimeState
    get_health: GetHealthService
    get_snapshot: GetSnapshotService
    put_snapshot: SnapshotCoordinatorService
    startup_restore: StartupRestore

    def __post_init__(self) -> None:
        if any(
            service.state is not self.state
            for service in (
                self.get_health,
                self.get_snapshot,
                self.put_snapshot,
                self.startup_restore,
            )
        ):
            raise ValueError("all agent services must share the same runtime state")


@final
@dataclass(kw_only=True, slots=True, frozen=True)
class InitializeRuntimeService:
    state: AgentRuntimeState
    restore: StartupRestoreService

    def __call__(self) -> None:
        recovery = self.restore()
        if recovery.snapshot_revision is None:
            self.state.record_not_ready()
            return
        snapshot = recovery.snapshot
        if snapshot is None:
            self.state.record_not_ready()
            return
        self.state.record_recovery(snapshot=snapshot)


def create_agent_services(*, settings: Settings) -> AgentServices:
    observability = Observability()
    channel = grpc.insecure_channel(settings.xray_api_target)
    client = ObservedXrayClient(
        client=GrpcXrayClient(
            channel=channel,
            timeout_seconds=settings.xray_api_timeout_seconds,
        ),
        observer=observability,
    )
    apply_accesses = ApplyExactSetService(
        client=client,
        managed_inbound_tag=settings.xray_managed_inbound_tag,
    )
    probe = ExactSetMatchesService(
        client=client,
        managed_inbound_tag=settings.xray_managed_inbound_tag,
    )
    store = SnapshotStore(path=settings.snapshot_path)
    state = AgentRuntimeState(observer=observability)
    apply_snapshot = ApplySnapshotService(
        apply_accesses=apply_accesses,
        store=store,
    )
    return AgentServices(
        state=state,
        get_health=GetHealthService(
            state=state,
            exact_set_matches=probe,
            agent_sha=settings.agent_sha,
            xray_version=settings.xray_version,
            xray_image_digest=settings.xray_image_digest,
        ),
        get_snapshot=GetSnapshotService(state=state),
        put_snapshot=SnapshotCoordinatorService(
            state=state,
            apply_snapshot=apply_snapshot,
            exact_set_matches=probe,
        ),
        startup_restore=InitializeRuntimeService(
            state=state,
            restore=StartupRestoreService(apply_accesses=apply_accesses, store=store),
        ),
    )


__all__ = ("AgentServices", "InitializeRuntimeService", "create_agent_services")
