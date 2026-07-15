from __future__ import annotations

from dataclasses import dataclass, field as dataclass_field
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
    def __call__(self) -> object: ...


@final
@dataclass(kw_only=True, slots=True, frozen=True)
class AgentServices:
    state: AgentRuntimeState
    get_health: GetHealthService
    get_snapshot: GetSnapshotService
    put_snapshot: SnapshotCoordinatorService
    startup_restore: StartupRestore
    observability: Observability = dataclass_field(default_factory=Observability)


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
        observer=observability,
    )
    return AgentServices(
        state=state,
        get_health=GetHealthService(
            state=state,
            exact_set_matches=probe,
            agent_sha=settings.agent_sha,
            xray_version=settings.xray_version,
            xray_image_digest=settings.xray_image_digest,
            observer=observability,
        ),
        get_snapshot=GetSnapshotService(state=state),
        put_snapshot=SnapshotCoordinatorService(
            state=state,
            apply_snapshot=apply_snapshot,
            exact_set_matches=probe,
            observer=observability,
        ),
        startup_restore=InitializeRuntimeService(
            state=state,
            restore=StartupRestoreService(apply_accesses=apply_accesses, store=store),
        ),
        observability=observability,
    )


__all__ = ("AgentServices", "InitializeRuntimeService", "create_agent_services")
