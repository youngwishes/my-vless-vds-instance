from __future__ import annotations

import hashlib
import asyncio
from dataclasses import MISSING, fields
import json
import logging
from unittest.mock import Mock

import pytest

from src.api.schemas import SnapshotDTO
from src.app import create_app
from src.config import EnvironmentMode, Settings
from src.factories import AgentServices
from src.observability import EventCode, Observability
from src.services import (
    AgentRuntimeState,
    ApplySnapshotService,
    GetHealthService,
    RevisionConflictError,
    SnapshotCoordinatorService,
)
from src.xray import ObservedXrayClient, XrayProtocolError, XrayTimeoutError, XrayUnavailableError
from tests.api.test_http_api import AUTH, _client


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
    canonical = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    payload["snapshot_hash"] = hashlib.sha256(canonical).hexdigest()
    return SnapshotDTO.model_validate(payload)


def test_conflict_and_health_drift_use_stable_bounded_counters() -> None:
    observer = Observability()
    current = _snapshot(2)
    state = AgentRuntimeState(observer=observer)
    state.record_applied(snapshot=current, matches=True)
    coordinator = SnapshotCoordinatorService(
        state=state,
        apply_snapshot=Mock(),
        exact_set_matches=Mock(return_value=True),
    )

    with pytest.raises(RevisionConflictError):
        coordinator(snapshot=_snapshot(2, access_id=2))

    health = GetHealthService(
        state=state,
        exact_set_matches=Mock(return_value=False),
        agent_sha="a" * 40,
        xray_version="26.7.11",
        xray_image_digest="sha256:" + "b" * 64,
    )
    health()

    metrics = observer.snapshot()
    assert metrics.counters[EventCode.REVISION_CONFLICT.value] == 1
    assert metrics.counters[EventCode.REVISION_DRIFT.value] == 1
    assert set(metrics.counters) == {code.value for code in EventCode}


def test_readiness_counters_identify_each_target_and_ignore_same_state() -> None:
    observer = Observability()
    state = AgentRuntimeState(observer=observer)
    snapshot = _snapshot(1)

    state.record_not_ready()
    state.record_recovery(snapshot=snapshot)
    state.record_recovery(snapshot=snapshot)
    state.record_applied(snapshot=snapshot, matches=True)
    state.record_applied(snapshot=snapshot, matches=True)
    state.record_not_ready()
    state.record_not_ready()

    counters = observer.snapshot().counters
    assert counters["readiness_recovery_ready"] == 1
    assert counters["readiness_ready"] == 1
    assert counters["readiness_not_ready"] == 1


def test_manual_composition_has_one_structural_observer_source_for_all_flows() -> None:
    state_observer = next(
        item for item in fields(AgentRuntimeState) if item.name == "observer"
    )
    assert state_observer.default is MISSING
    assert state_observer.default_factory is MISSING
    for owner in (
        AgentServices,
        ApplySnapshotService,
        GetHealthService,
        SnapshotCoordinatorService,
    ):
        assert "observer" not in {item.name for item in fields(owner)}

    observer = Observability()
    current = _snapshot(1)
    state = AgentRuntimeState(observer=observer)
    probe = Mock(return_value=True)
    apply = Mock(return_value=current)
    times = iter((1.0, 1.05, 2.0, 2.2))
    coordinator = SnapshotCoordinatorService(
        state=state,
        apply_snapshot=apply,
        exact_set_matches=probe,
        monotonic=lambda: next(times),
    )
    services = AgentServices(
        state=state,
        get_health=GetHealthService(
            state=state,
            exact_set_matches=probe,
            agent_sha="a" * 40,
            xray_version="26.7.11",
            xray_image_digest="sha256:" + "b" * 64,
        ),
        get_snapshot=Mock(state=state),
        put_snapshot=coordinator,
        startup_restore=Mock(state=state),
    )
    app = create_app(
        settings=Settings(
            vless_node_id="node-01",
            environment_mode=EnvironmentMode.TEST,
            agent_token_current="explicit-test-token",
        ),
        services=services,
    )

    assert app.state.observability is observer
    coordinator(snapshot=current)
    with pytest.raises(RevisionConflictError):
        coordinator(snapshot=_snapshot(1, access_id=2))
    state.record_applied(snapshot=current, matches=True)
    probe.return_value = False
    services.get_health()
    failure = RuntimeError("safe propagation sentinel")
    apply.side_effect = failure
    with pytest.raises(RuntimeError) as captured:
        coordinator(snapshot=_snapshot(2))

    assert captured.value is failure
    counters = observer.snapshot().counters
    assert counters[EventCode.READINESS_READY.value] == 2
    assert counters[EventCode.READINESS_NOT_READY.value] == 2
    assert counters[EventCode.REVISION_CONFLICT.value] == 1
    assert counters[EventCode.REVISION_DRIFT.value] == 1
    assert counters[EventCode.APPLY_SUCCESS.value] == 1
    assert counters[EventCode.APPLY_FAILURE.value] == 1
    assert observer.snapshot().apply_latency_count == 2


def test_agent_services_rejects_nested_service_bound_to_another_state() -> None:
    observer = Observability()
    state = AgentRuntimeState(observer=observer)
    other_state = AgentRuntimeState(observer=Observability())
    health = GetHealthService(
        state=other_state,
        exact_set_matches=Mock(return_value=True),
        agent_sha="a" * 40,
        xray_version="26.7.11",
        xray_image_digest="sha256:" + "b" * 64,
    )

    with pytest.raises(ValueError, match="same runtime state"):
        AgentServices(
            state=state,
            get_health=health,
            get_snapshot=Mock(state=state),
            put_snapshot=Mock(state=state),
            startup_restore=Mock(state=state),
        )

    valid_health = GetHealthService(
        state=state,
        exact_set_matches=Mock(return_value=True),
        agent_sha="a" * 40,
        xray_version="26.7.11",
        xray_image_digest="sha256:" + "b" * 64,
    )
    with pytest.raises(ValueError, match="same runtime state"):
        AgentServices(
            state=state,
            get_health=valid_health,
            get_snapshot=Mock(state=state),
            put_snapshot=Mock(state=state),
            startup_restore=Mock(state=other_state),
        )


def test_apply_latency_is_deterministic_and_bucketed() -> None:
    observer = Observability()
    times = iter((10.0, 10.075))
    state = AgentRuntimeState(observer=observer)
    service = SnapshotCoordinatorService(
        state=state,
        apply_snapshot=Mock(side_effect=lambda *, snapshot: snapshot),
        exact_set_matches=Mock(return_value=True),
        monotonic=lambda: next(times),
    )

    service(snapshot=_snapshot(1))

    metrics = observer.snapshot()
    assert metrics.counters[EventCode.APPLY_SUCCESS.value] == 1
    assert metrics.apply_latency_count == 1
    assert metrics.apply_latency_seconds_sum == pytest.approx(0.075)
    assert metrics.apply_latency_buckets == {
        "le_0.01": 0,
        "le_0.1": 1,
        "le_1": 1,
        "le_5": 1,
        "le_inf": 1,
    }


def test_failure_event_logs_and_metrics_never_include_sensitive_context(
    caplog: pytest.LogCaptureFixture,
) -> None:
    sentinels = (
        "01890f47-a2d4-7c11-b3e6-89f40d8639f1",
        "Authorization: Bearer attacker-token",
        "attacker-token",
        '{"schema_version":"1.0","accesses":[{"uuid":"complete-body"}]}',
        "REALITY-PRIVATE-KEY-SENTINEL",
        "/attacker/chosen/request/path",
        "attacker-controlled exception message",
    )
    failure = RuntimeError(" | ".join(sentinels))
    observer = Observability()
    times = iter((1.0, 1.2))
    service = SnapshotCoordinatorService(
        state=AgentRuntimeState(observer=observer),
        apply_snapshot=Mock(side_effect=failure),
        exact_set_matches=Mock(),
        monotonic=lambda: next(times),
    )

    with caplog.at_level(logging.INFO, logger="vless_agent.observability"):
        with pytest.raises(RuntimeError) as captured:
            service(snapshot=_snapshot(1))

    assert captured.value is failure
    assert any(
        json.loads(record.message)["code"] == EventCode.APPLY_FAILURE.value
        for record in caplog.records
    )
    serialized = json.dumps(observer.snapshot().as_dict(), sort_keys=True)
    combined = "\n".join(record.getMessage() for record in caplog.records) + serialized
    for sentinel in sentinels:
        assert sentinel not in combined


def test_public_event_api_rejects_arbitrary_context() -> None:
    observer = Observability()

    with pytest.raises(TypeError):
        observer.record("attacker code")  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        observer.record(EventCode.AUTH_FAILURE, context={"token": "secret"})  # type: ignore[call-arg]


def test_http_failures_increment_internal_metrics_without_changing_contract() -> None:
    client, _ = _client()

    unauthorized = client.get(
        "/api/v1/health",
        headers={"X-Agent-Contract-Version": "v1"},
    )
    incompatible = client.get(
        "/api/v1/health",
        headers={"Authorization": "Bearer explicit-test-token"},
    )
    overflow = client.put(
        "/api/v1/snapshot",
        headers={**AUTH, "Content-Type": "application/json"},
        content=b"x" * 1_048_705,
    )

    assert unauthorized.status_code == 401
    assert unauthorized.json() == {
        "code": "unauthorized",
        "message": "Authentication is required.",
    }
    assert incompatible.status_code == 426
    assert incompatible.json() == {
        "code": "incompatible_contract",
        "message": "The requested contract or schema major is not supported.",
    }
    assert overflow.status_code == 413
    assert overflow.json() == {
        "code": "snapshot_too_large",
        "message": "Snapshot exceeds the supported contract limits.",
    }
    metrics = client.app.state.observability.snapshot().counters
    assert metrics[EventCode.AUTH_FAILURE.value] == 1
    assert metrics[EventCode.INCOMPATIBLE_CONTRACT.value] == 1
    assert metrics[EventCode.SNAPSHOT_OVERFLOW.value] == 1


def test_incompatible_snapshot_schema_uses_same_safe_contract_and_counter() -> None:
    client, _ = _client()
    payload = {
        "schema_version": "2.0",
        "snapshot_revision": 1,
        "accesses": [],
    }
    canonical = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    payload["snapshot_hash"] = hashlib.sha256(canonical).hexdigest()

    response = client.put("/api/v1/snapshot", headers=AUTH, json=payload)

    assert response.status_code == 426
    assert response.json() == {
        "code": "incompatible_contract",
        "message": "The requested contract or schema major is not supported.",
    }
    metrics = client.app.state.observability.snapshot().counters
    assert metrics[EventCode.INCOMPATIBLE_CONTRACT.value] == 1


def test_startup_restore_failure_emits_only_safe_fixed_event(
    caplog: pytest.LogCaptureFixture,
) -> None:
    sentinel = "/attacker/chosen/snapshot path: REALITY-PRIVATE-KEY-SENTINEL"
    observer = Observability()
    state = AgentRuntimeState(observer=observer)
    services = AgentServices(
        state=state,
        get_health=Mock(state=state),
        get_snapshot=Mock(state=state),
        put_snapshot=Mock(state=state),
        startup_restore=Mock(state=state, side_effect=RuntimeError(sentinel)),
    )
    app = create_app(
        settings=Settings(
            vless_node_id="node-01",
            environment_mode=EnvironmentMode.TEST,
            agent_token_current="explicit-test-token",
        ),
        services=services,
    )

    async def run() -> None:
        async with app.router.lifespan_context(app):
            pass

    with caplog.at_level(logging.INFO, logger="vless_agent.observability"):
        asyncio.run(run())

    assert observer.snapshot().counters[EventCode.STARTUP_RESTORE_FAILURE.value] == 1
    assert any(
        json.loads(record.message)["code"] == EventCode.STARTUP_RESTORE_FAILURE.value
        for record in caplog.records
    )
    assert sentinel not in "\n".join(record.getMessage() for record in caplog.records)


@pytest.mark.parametrize(
    ("failure", "code"),
    (
        (XrayTimeoutError("attacker timeout detail"), EventCode.XRAY_TIMEOUT),
        (XrayUnavailableError("attacker unavailable detail"), EventCode.XRAY_UNAVAILABLE),
        (XrayProtocolError("attacker protocol detail"), EventCode.XRAY_PROTOCOL_FAILURE),
    ),
)
def test_xray_failures_are_classified_without_logging_exception(
    failure: Exception,
    code: EventCode,
    caplog: pytest.LogCaptureFixture,
) -> None:
    observer = Observability()
    client = ObservedXrayClient(
        client=Mock(get_inbound_users=Mock(side_effect=failure)),
        observer=observer,
    )

    with caplog.at_level(logging.INFO, logger="vless_agent.observability"):
        with pytest.raises(type(failure)):
            client.get_inbound_users(tag="vless-managed")

    assert observer.snapshot().counters[code.value] == 1
    assert any(json.loads(record.message)["code"] == code.value for record in caplog.records)
    assert str(failure) not in "\n".join(record.getMessage() for record in caplog.records)
