from __future__ import annotations

import hashlib
import json
import threading
from unittest.mock import Mock

import pytest

from src.api.schemas import SnapshotDTO
from src.observability import Observability
from src.services.snapshot_runtime import (
    AgentRuntimeState,
    ApplySnapshotResult,
    ApplySnapshotResultKind,
    Readiness,
    RevisionConflictError,
    SnapshotCoordinatorService,
    StaleRevisionError,
)
from src.services.get_health_service import GetHealthService
from src.factories import InitializeRuntimeService
from src.services import RecoveryState, RecoveryStatus


def _snapshot(revision: int, *, access_id: int = 1) -> SnapshotDTO:
    payload = {
        "schema_version": "1.0",
        "snapshot_revision": revision,
        "accesses": [
            {
                "access_id": access_id,
                "uuid": "01890f47-a2d4-7c11-b3e6-89f40d8639f1",
                "access_revision": 1,
            }
        ],
    }
    canonical = json.dumps(
        payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode()
    payload["snapshot_hash"] = hashlib.sha256(canonical).hexdigest()
    return SnapshotDTO.model_validate(payload)


def _service(*, state: AgentRuntimeState, apply: Mock, probe: Mock):
    return SnapshotCoordinatorService(
        state=state,
        apply_snapshot=apply,
        exact_set_matches=probe,
    )


def test_first_snapshot_applies_then_equal_snapshot_is_no_op_and_ready() -> None:
    state = AgentRuntimeState(observer=Observability())
    snapshot = _snapshot(1)
    apply = Mock(return_value=snapshot)
    probe = Mock(return_value=True)
    service = _service(state=state, apply=apply, probe=probe)

    assert service(snapshot=snapshot) == ApplySnapshotResult(
        result=ApplySnapshotResultKind.APPLIED, snapshot=snapshot
    )
    assert service(snapshot=snapshot) == ApplySnapshotResult(
        result=ApplySnapshotResultKind.NO_OP, snapshot=snapshot
    )
    assert state.read().readiness is Readiness.READY
    apply.assert_called_once_with(snapshot=snapshot)


def test_lower_and_conflicting_revision_never_mutate_and_demote_readiness() -> None:
    current = _snapshot(2)
    state = AgentRuntimeState(observer=Observability())
    state.record_recovery(snapshot=current)
    apply = Mock()
    service = _service(state=state, apply=apply, probe=Mock(return_value=True))

    with pytest.raises(StaleRevisionError):
        service(snapshot=_snapshot(1))
    assert state.read().readiness is Readiness.NOT_READY

    with pytest.raises(RevisionConflictError):
        service(snapshot=_snapshot(2, access_id=2))
    assert state.read().readiness is Readiness.NOT_READY
    apply.assert_not_called()


def test_dependency_failure_demotes_before_propagating_without_false_metadata() -> None:
    current = _snapshot(1)
    state = AgentRuntimeState(observer=Observability())
    state.record_recovery(snapshot=current)
    failure = RuntimeError("private path /secret and uuid 01890f47")
    service = _service(
        state=state,
        apply=Mock(side_effect=failure),
        probe=Mock(return_value=True),
    )

    with pytest.raises(RuntimeError) as captured:
        service(snapshot=_snapshot(2))

    assert captured.value is failure
    status = state.read()
    assert status.readiness is Readiness.NOT_READY
    assert status.snapshot == current


def test_probe_failure_after_durable_apply_publishes_new_metadata_not_old() -> None:
    old = _snapshot(1)
    new = _snapshot(2)
    state = AgentRuntimeState(observer=Observability())
    state.record_recovery(snapshot=old)
    service = _service(
        state=state,
        apply=Mock(return_value=new),
        probe=Mock(side_effect=RuntimeError("probe failed")),
    )

    with pytest.raises(RuntimeError):
        service(snapshot=new)

    status = state.read()
    assert status.readiness is Readiness.NOT_READY
    assert status.snapshot == new


def test_compare_apply_persist_is_serialized_across_concurrent_requests() -> None:
    state = AgentRuntimeState(observer=Observability())
    first = _snapshot(1)
    second = _snapshot(2)
    entered = threading.Event()
    release = threading.Event()
    calls: list[int] = []

    def apply(*, snapshot: SnapshotDTO) -> SnapshotDTO:
        calls.append(snapshot.snapshot_revision)
        if snapshot.snapshot_revision == 1:
            entered.set()
            assert release.wait(timeout=2)
        return snapshot

    service = SnapshotCoordinatorService(
        state=state,
        apply_snapshot=apply,
        exact_set_matches=lambda **_: True,
    )
    errors: list[BaseException] = []

    def invoke(snapshot: SnapshotDTO) -> None:
        try:
            service(snapshot=snapshot)
        except BaseException as error:  # pragma: no cover - assertion capture
            errors.append(error)

    one = threading.Thread(target=invoke, args=(first,))
    two = threading.Thread(target=invoke, args=(second,))
    one.start()
    assert entered.wait(timeout=2)
    two.start()
    release.set()
    one.join(timeout=2)
    two.join(timeout=2)

    assert errors == []
    assert calls == [1, 2]
    assert state.read().snapshot == second


def test_health_read_only_probe_demotes_ready_on_runtime_drift() -> None:
    snapshot = _snapshot(1)
    state = AgentRuntimeState(observer=Observability())
    state.record_applied(snapshot=snapshot, matches=True)
    probe = Mock(return_value=False)
    service = GetHealthService(
        state=state,
        exact_set_matches=probe,
        agent_sha="a" * 40,
        xray_version="25.7.1",
        xray_image_digest="sha256:" + "b" * 64,
    )

    health = service()

    assert health.readiness is Readiness.NOT_READY
    assert health.snapshot == snapshot
    probe.assert_called_once_with(accesses=snapshot.accesses)


def test_health_probe_failure_demotes_before_propagating() -> None:
    snapshot = _snapshot(1)
    state = AgentRuntimeState(observer=Observability())
    state.record_applied(snapshot=snapshot, matches=True)
    service = GetHealthService(
        state=state,
        exact_set_matches=Mock(side_effect=RuntimeError("xray private detail")),
        agent_sha="a" * 40,
        xray_version="25.7.1",
        xray_image_digest="sha256:" + "b" * 64,
    )

    with pytest.raises(RuntimeError):
        service()

    assert state.read().readiness is Readiness.NOT_READY


def test_startup_restored_snapshot_is_recovery_ready_until_backend_confirmation() -> None:
    snapshot = _snapshot(4)
    state = AgentRuntimeState(observer=Observability())
    restore = Mock(return_value=RecoveryState(
        status=RecoveryStatus.RECOVERY_READY,
        snapshot_revision=4,
        snapshot_hash=snapshot.snapshot_hash,
        snapshot=snapshot,
    ))

    InitializeRuntimeService(state=state, restore=restore)()

    assert state.read().snapshot == snapshot
    assert state.read().readiness is Readiness.RECOVERY_READY
