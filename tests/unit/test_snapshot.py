from __future__ import annotations

import hashlib
import json
from typing import Any

import pytest
from pydantic import ValidationError

from src.api.schemas import AccessDTO, SafeErrorDTO, SnapshotDTO
from src.domain import (
    MAX_CANONICAL_BYTES,
    MAX_SNAPSHOT_ACCESSES,
    IncompatibleSchemaVersionError,
    InvalidSnapshotError,
    SnapshotTooLargeError,
    canonical_snapshot_bytes,
    snapshot_hash,
    validate_snapshot,
)


def _access(
    access_id: object = 2,
    *,
    uuid: object = "01890f47-a2d4-7c11-b3e6-89f40d8639f1",
    access_revision: object = 1,
) -> dict[str, object]:
    return {
        "access_id": access_id,
        "uuid": uuid,
        "access_revision": access_revision,
    }


def _snapshot_payload(
    accesses: list[dict[str, object]] | None = None,
    *,
    schema_version: str = "1.0",
    snapshot_revision: object = 1,
    snapshot_hash_value: str | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schema_version": schema_version,
        "snapshot_revision": snapshot_revision,
        "accesses": accesses or [],
    }
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    payload["snapshot_hash"] = snapshot_hash_value or hashlib.sha256(canonical).hexdigest()
    return payload


def test_empty_snapshot_has_exact_canonical_bytes_and_hash() -> None:
    snapshot = SnapshotDTO.model_validate(_snapshot_payload())

    canonical = canonical_snapshot_bytes(snapshot)

    assert canonical == b'{"accesses":[],"schema_version":"1.0","snapshot_revision":1}'
    assert snapshot_hash(snapshot) == (
        "9d7b77084497930cf0439a2d2326e60a435c8b606e1785d06c56f09b25fb90e1"
    )
    assert validate_snapshot(snapshot) is snapshot


def test_two_access_snapshot_uses_recursive_key_sort_and_numeric_input_order() -> None:
    snapshot = SnapshotDTO.model_validate(
        _snapshot_payload(
            [
                _access(2),
                _access(
                    10,
                    uuid="2f1c5a63-7bd6-4ac1-86dc-16b7adf580df",
                    access_revision=3,
                ),
            ],
            snapshot_revision=7,
        )
    )

    canonical = canonical_snapshot_bytes(snapshot)

    assert canonical == (
        b'{"accesses":[{"access_id":2,"access_revision":1,'
        b'"uuid":"01890f47-a2d4-7c11-b3e6-89f40d8639f1"},'
        b'{"access_id":10,"access_revision":3,'
        b'"uuid":"2f1c5a63-7bd6-4ac1-86dc-16b7adf580df"}],'
        b'"schema_version":"1.0","snapshot_revision":7}'
    )
    assert snapshot_hash(snapshot) == (
        "3c351495cf810ed9c06e06ad1436f1e1093413923cb7a2872df7a6243f1fa72f"
    )
    assert validate_snapshot(snapshot) is snapshot


@pytest.mark.parametrize("value", [True, "2", 0, -1])
def test_access_id_is_a_strict_positive_integer(value: object) -> None:
    with pytest.raises(ValidationError):
        AccessDTO.model_validate(_access(value))


@pytest.mark.parametrize("value", [True, "1", 0, -1])
def test_access_revision_is_a_strict_positive_integer(value: object) -> None:
    with pytest.raises(ValidationError):
        AccessDTO.model_validate(_access(access_revision=value))


@pytest.mark.parametrize("value", [True, "1", 0, -1])
def test_snapshot_revision_is_a_strict_positive_integer(value: object) -> None:
    with pytest.raises(ValidationError):
        SnapshotDTO.model_validate(_snapshot_payload(snapshot_revision=value))


@pytest.mark.parametrize(
    "value",
    [
        "not-a-uuid",
        "01890F47-A2D4-7C11-B3E6-89F40D8639F1",
        "01890f47a2d47c11b3e689f40d8639f1",
        "01890f47-a2d4-7c11-33e6-89f40d8639f1",
    ],
)
def test_uuid_must_be_canonical_lowercase_hyphenated(value: str) -> None:
    with pytest.raises(ValidationError):
        AccessDTO.model_validate(_access(uuid=value))


def test_dtos_are_immutable_and_reject_unknown_fields() -> None:
    access = AccessDTO.model_validate(_access())
    error = SafeErrorDTO(code="snapshot_too_large", message="Too large.")

    with pytest.raises(ValidationError):
        access.access_revision = 2
    with pytest.raises(ValidationError):
        AccessDTO.model_validate({**_access(), "email": "sensitive@example.test"})
    with pytest.raises(ValidationError):
        error.code = "changed"


@pytest.mark.parametrize(
    "accesses",
    [
        [_access(10), _access(2)],
        [_access(2), _access(2, uuid="2f1c5a63-7bd6-4ac1-86dc-16b7adf580df")],
    ],
)
def test_accesses_must_arrive_strictly_ascending_and_unique(accesses: list[dict[str, object]]) -> None:
    snapshot = SnapshotDTO.model_validate(_snapshot_payload(accesses))

    with pytest.raises(InvalidSnapshotError, match="strictly ascending"):
        validate_snapshot(snapshot)


def test_wrong_well_formed_hash_is_rejected() -> None:
    snapshot = SnapshotDTO.model_validate(
        _snapshot_payload(snapshot_hash_value="0" * 64)
    )

    with pytest.raises(InvalidSnapshotError, match="hash"):
        validate_snapshot(snapshot)


def test_non_lowercase_or_malformed_hash_is_rejected_at_dto_boundary() -> None:
    for value in ("A" * 64, "0" * 63, "g" * 64):
        with pytest.raises(ValidationError):
            SnapshotDTO.model_validate(_snapshot_payload(snapshot_hash_value=value))


def test_unknown_schema_major_has_compatibility_domain_error() -> None:
    snapshot = SnapshotDTO.model_validate(_snapshot_payload(schema_version="2.0"))

    with pytest.raises(IncompatibleSchemaVersionError):
        validate_snapshot(snapshot)


def test_more_than_5000_accesses_has_size_domain_error() -> None:
    assert MAX_SNAPSHOT_ACCESSES == 5_000
    accesses = [
        _access(
            access_id,
            uuid=f"00000000-0000-4000-8000-{access_id:012x}",
        )
        for access_id in range(1, MAX_SNAPSHOT_ACCESSES + 2)
    ]
    snapshot = SnapshotDTO.model_validate(_snapshot_payload(accesses))

    with pytest.raises(SnapshotTooLargeError, match="entries"):
        validate_snapshot(snapshot)


def test_more_than_1048576_canonical_bytes_has_size_domain_error() -> None:
    assert MAX_CANONICAL_BYTES == 1_048_576
    snapshot = SnapshotDTO.model_validate(
        _snapshot_payload(schema_version="2" * MAX_CANONICAL_BYTES)
    )

    with pytest.raises(SnapshotTooLargeError, match="bytes"):
        validate_snapshot(snapshot)
