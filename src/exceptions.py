from __future__ import annotations


class SnapshotError(ValueError):
    """The exact snapshot cannot be accepted."""


class InvalidSnapshotError(SnapshotError):
    """The exact snapshot violates contract validation rules."""


class SnapshotTooLargeError(SnapshotError):
    """The exact snapshot exceeds contract entry or canonical-byte limits."""


class IncompatibleSchemaVersionError(SnapshotError):
    """The snapshot schema version is not supported by this agent."""


__all__ = (
    "IncompatibleSchemaVersionError",
    "InvalidSnapshotError",
    "SnapshotError",
    "SnapshotTooLargeError",
)
