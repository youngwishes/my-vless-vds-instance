"""Pure VLESS agent domain logic."""

from src.domain.snapshot import (
    MAX_CANONICAL_BYTES,
    MAX_SNAPSHOT_ACCESSES,
    SCHEMA_VERSION,
    canonical_snapshot_bytes,
    snapshot_hash,
    validate_snapshot,
)
from src.exceptions import (
    IncompatibleSchemaVersionError,
    InvalidSnapshotError,
    SnapshotError,
    SnapshotTooLargeError,
)

__all__ = (
    "IncompatibleSchemaVersionError",
    "InvalidSnapshotError",
    "MAX_CANONICAL_BYTES",
    "MAX_SNAPSHOT_ACCESSES",
    "SCHEMA_VERSION",
    "SnapshotError",
    "SnapshotTooLargeError",
    "canonical_snapshot_bytes",
    "snapshot_hash",
    "validate_snapshot",
)
