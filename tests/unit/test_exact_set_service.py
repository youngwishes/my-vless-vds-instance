from __future__ import annotations

from dataclasses import FrozenInstanceError
from unittest.mock import Mock, call

import pytest
import grpc

from src.api.schemas import AccessDTO
from src.xray import (
    ApplyExactSetService,
    GrpcXrayClient,
    XrayProtocolError,
    XrayTimeoutError,
    XrayUnavailableError,
    XrayUser,
    VLESS_VISION_FLOW,
    access_email,
)


MANAGED_TAG = "vless-managed"


def _access(access_id: int, uuid: str, *, access_revision: int = 1) -> AccessDTO:
    return AccessDTO(
        access_id=access_id,
        uuid=uuid,
        access_revision=access_revision,
    )


def _service(client: Mock) -> ApplyExactSetService:
    return ApplyExactSetService(client=client, managed_inbound_tag=MANAGED_TAG)


def test_service_is_a_frozen_keyword_only_dataclass() -> None:
    client = Mock()
    service = _service(client)

    with pytest.raises(TypeError):
        ApplyExactSetService(client, MANAGED_TAG)  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        service.managed_inbound_tag = "other"  # type: ignore[misc]


def test_adds_missing_users_in_numeric_access_order() -> None:
    client = Mock()
    client.get_inbound_users.return_value = ()
    service = _service(client)
    first = _access(2, "01890f47-a2d4-7c11-b3e6-89f40d8639f1")
    second = _access(10, "2f1c5a63-7bd6-4ac1-86dc-16b7adf580df")

    service(accesses=(first, second))

    assert client.mock_calls == [
        call.get_inbound_users(tag=MANAGED_TAG),
        call.add_user(
            tag=MANAGED_TAG,
            user=XrayUser(
                email=access_email(2),
                uuid=first.uuid,
                flow=VLESS_VISION_FLOW,
            ),
        ),
        call.add_user(
            tag=MANAGED_TAG,
            user=XrayUser(
                email=access_email(10),
                uuid=second.uuid,
                flow=VLESS_VISION_FLOW,
            ),
        ),
    ]


def test_removes_obsolete_users_in_stable_email_order() -> None:
    client = Mock()
    client.get_inbound_users.return_value = (
        XrayUser(
            email=access_email(10),
            uuid="2f1c5a63-7bd6-4ac1-86dc-16b7adf580df",
            flow=VLESS_VISION_FLOW,
        ),
        XrayUser(
            email=access_email(2),
            uuid="01890f47-a2d4-7c11-b3e6-89f40d8639f1",
            flow=VLESS_VISION_FLOW,
        ),
    )

    _service(client)(accesses=())

    assert client.mock_calls == [
        call.get_inbound_users(tag=MANAGED_TAG),
        call.remove_user(tag=MANAGED_TAG, email=access_email(2)),
        call.remove_user(tag=MANAGED_TAG, email=access_email(10)),
    ]


def test_replaces_changed_uuid_by_removing_before_adding() -> None:
    client = Mock()
    old_uuid = "01890f47-a2d4-7c11-b3e6-89f40d8639f1"
    new_uuid = "2f1c5a63-7bd6-4ac1-86dc-16b7adf580df"
    email = access_email(2)
    client.get_inbound_users.return_value = (
        XrayUser(email=email, uuid=old_uuid, flow=VLESS_VISION_FLOW),
    )

    _service(client)(accesses=(_access(2, new_uuid, access_revision=2),))

    assert client.mock_calls == [
        call.get_inbound_users(tag=MANAGED_TAG),
        call.remove_user(tag=MANAGED_TAG, email=email),
        call.add_user(
            tag=MANAGED_TAG,
            user=XrayUser(email=email, uuid=new_uuid, flow=VLESS_VISION_FLOW),
        ),
    ]


def test_replaces_matching_uuid_when_vless_flow_is_wrong() -> None:
    client = Mock()
    uuid = "01890f47-a2d4-7c11-b3e6-89f40d8639f1"
    email = access_email(2)
    client.get_inbound_users.return_value = (
        XrayUser(email=email, uuid=uuid, flow=""),
    )

    _service(client)(accesses=(_access(2, uuid),))

    assert client.mock_calls == [
        call.get_inbound_users(tag=MANAGED_TAG),
        call.remove_user(tag=MANAGED_TAG, email=email),
        call.add_user(
            tag=MANAGED_TAG,
            user=XrayUser(email=email, uuid=uuid, flow=VLESS_VISION_FLOW),
        ),
    ]


def test_repeated_exact_set_is_a_no_op() -> None:
    client = Mock()
    user = XrayUser(
        email=access_email(2),
        uuid="01890f47-a2d4-7c11-b3e6-89f40d8639f1",
        flow=VLESS_VISION_FLOW,
    )
    client.get_inbound_users.return_value = (user,)

    service = _service(client)
    service(accesses=(_access(2, user.uuid),))
    service(accesses=(_access(2, user.uuid),))

    assert client.mock_calls == [
        call.get_inbound_users(tag=MANAGED_TAG),
        call.get_inbound_users(tag=MANAGED_TAG),
    ]


def test_never_mutates_any_tag_other_than_configured_managed_inbound() -> None:
    client = Mock()
    client.get_inbound_users.return_value = ()

    _service(client)(
        accesses=(_access(2, "01890f47-a2d4-7c11-b3e6-89f40d8639f1"),)
    )

    mutation_calls = [
        item for item in client.mock_calls if item[0] in {"add_user", "remove_user"}
    ]
    assert mutation_calls
    assert all(item.kwargs["tag"] == MANAGED_TAG for item in mutation_calls)


@pytest.mark.parametrize(
    "error",
    [
        XrayTimeoutError("Xray request timed out"),
        XrayUnavailableError("Xray is unavailable"),
        XrayProtocolError("Xray returned an invalid response"),
    ],
)
def test_explicit_xray_failures_propagate_without_access_details(error: Exception) -> None:
    client = Mock()
    client.get_inbound_users.side_effect = error

    with pytest.raises(type(error), match=str(error)) as captured:
        _service(client)(
            accesses=(_access(2, "01890f47-a2d4-7c11-b3e6-89f40d8639f1"),)
        )

    message = str(captured.value)
    assert "01890f47" not in message
    assert access_email(2) not in message


class _RpcError(grpc.RpcError):
    def __init__(self, status: grpc.StatusCode) -> None:
        self._status = status

    def code(self) -> grpc.StatusCode:
        return self._status


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (grpc.StatusCode.DEADLINE_EXCEEDED, XrayTimeoutError),
        (grpc.StatusCode.UNAVAILABLE, XrayUnavailableError),
        (grpc.StatusCode.INVALID_ARGUMENT, XrayProtocolError),
    ],
)
def test_grpc_status_is_mapped_to_safe_explicit_exception(
    status: grpc.StatusCode,
    expected: type[Exception],
) -> None:
    channel = Mock()
    channel.unary_unary.return_value.side_effect = _RpcError(status)
    client = GrpcXrayClient(channel=channel, timeout_seconds=1)

    with pytest.raises(expected) as captured:
        client.get_inbound_users(tag="secret-tag-that-must-not-leak")

    assert "secret-tag-that-must-not-leak" not in str(captured.value)
