from __future__ import annotations

import hashlib
import json

from src.api.schemas import SnapshotDTO
from src.exceptions import (
    IncompatibleSchemaVersionError,
    InvalidSnapshotError,
    SnapshotTooLargeError,
)


SCHEMA_VERSION = "1.0"
MAX_SNAPSHOT_ACCESSES = 5_000
MAX_CANONICAL_BYTES = 1_048_576


def canonical_snapshot_bytes(snapshot: SnapshotDTO) -> bytes:
    payload = snapshot.model_dump(
        mode="json",
        include={"schema_version", "snapshot_revision", "accesses"},
    )
    return json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def snapshot_hash(snapshot: SnapshotDTO) -> str:
    return hashlib.sha256(canonical_snapshot_bytes(snapshot)).hexdigest()


def validate_snapshot(snapshot: SnapshotDTO) -> SnapshotDTO:
    canonical = canonical_snapshot_bytes(snapshot)
    if len(snapshot.accesses) > MAX_SNAPSHOT_ACCESSES:
        raise SnapshotTooLargeError(
            f"snapshot exceeds the maximum of {MAX_SNAPSHOT_ACCESSES} entries"
        )
    if len(canonical) > MAX_CANONICAL_BYTES:
        raise SnapshotTooLargeError(
            f"snapshot exceeds the maximum of {MAX_CANONICAL_BYTES} canonical bytes"
        )
    if snapshot.schema_version != SCHEMA_VERSION:
        raise IncompatibleSchemaVersionError(
            f"snapshot schema {snapshot.schema_version!r} is incompatible"
        )

    previous_access_id = 0
    for access in snapshot.accesses:
        if access.access_id <= previous_access_id:
            raise InvalidSnapshotError(
                "accesses must be strictly ascending and unique by numeric access_id"
            )
        previous_access_id = access.access_id

    expected_hash = hashlib.sha256(canonical).hexdigest()
    if snapshot.snapshot_hash != expected_hash:
        raise InvalidSnapshotError("snapshot hash does not match canonical content")
    return snapshot


__all__ = (
    "MAX_CANONICAL_BYTES",
    "MAX_SNAPSHOT_ACCESSES",
    "SCHEMA_VERSION",
    "canonical_snapshot_bytes",
    "snapshot_hash",
    "validate_snapshot",
)
