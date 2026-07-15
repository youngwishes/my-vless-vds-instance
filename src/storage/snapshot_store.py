from __future__ import annotations

import json
import os
import secrets
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import final

from pydantic import ValidationError

from src.api.schemas import SnapshotDTO
from src.domain import MAX_CANONICAL_BYTES, validate_snapshot
from src.exceptions import SnapshotError


# Persistence adds the 64-byte hash and its JSON key/delimiters (83 bytes).
# The fixed 128-byte allowance leaves bounded room for that complete envelope.
MAX_PERSISTED_SNAPSHOT_BYTES = MAX_CANONICAL_BYTES + 128


class SnapshotStoreError(OSError):
    """The durable snapshot could not be stored safely."""


class SnapshotRecoveryError(SnapshotStoreError):
    """The durable snapshot cannot be recovered safely."""


@final
@dataclass(kw_only=True, slots=True, frozen=True)
class SnapshotStore:
    path: Path

    def save(self, *, snapshot: SnapshotDTO) -> None:
        validated = validate_snapshot(snapshot)
        payload = json.dumps(
            validated.model_dump(mode="json"),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        if len(payload) > MAX_PERSISTED_SNAPSHOT_BYTES:
            raise SnapshotStoreError("snapshot could not be stored safely")

        directory_descriptor: int | None = None
        temporary_name: str | None = None

        try:
            directory_descriptor = _open_parent_directory(
                self.path.parent,
                create=True,
            )
            descriptor, temporary_name = _create_temporary_file(
                directory_descriptor=directory_descriptor,
                target_name=self.path.name,
            )
            try:
                os.fchmod(descriptor, 0o600)
                temporary_file = os.fdopen(descriptor, "wb")
                descriptor = -1
                with temporary_file:
                    temporary_file.write(payload)
                    temporary_file.flush()
                    os.fsync(temporary_file.fileno())
            finally:
                if descriptor != -1:
                    os.close(descriptor)

            os.replace(
                temporary_name,
                self.path.name,
                src_dir_fd=directory_descriptor,
                dst_dir_fd=directory_descriptor,
            )
            temporary_name = None
            os.fsync(directory_descriptor)
        except OSError as error:
            raise SnapshotStoreError("snapshot could not be stored safely") from error
        finally:
            if temporary_name is not None and directory_descriptor is not None:
                try:
                    os.unlink(temporary_name, dir_fd=directory_descriptor)
                except FileNotFoundError:
                    pass
                except OSError:
                    pass
            if directory_descriptor is not None:
                os.close(directory_descriptor)

    def load(self) -> SnapshotDTO | None:
        directory_descriptor: int | None = None
        try:
            directory_descriptor = _open_parent_directory(
                self.path.parent,
                create=False,
            )
            metadata = os.stat(
                self.path.name,
                dir_fd=directory_descriptor,
                follow_symlinks=False,
            )
        except FileNotFoundError:
            if directory_descriptor is not None:
                os.close(directory_descriptor)
            return None
        except OSError as error:
            if directory_descriptor is not None:
                os.close(directory_descriptor)
            raise SnapshotRecoveryError("durable snapshot cannot be recovered safely") from error

        descriptor: int | None = None
        try:
            _validate_snapshot_metadata(metadata)
            flags = (
                os.O_RDONLY
                | getattr(os, "O_CLOEXEC", 0)
                | getattr(os, "O_NOFOLLOW", 0)
            )
            descriptor = os.open(
                self.path.name,
                flags,
                dir_fd=directory_descriptor,
            )
            opened_metadata = os.fstat(descriptor)
            _validate_snapshot_metadata(opened_metadata)
            if (metadata.st_dev, metadata.st_ino) != (
                opened_metadata.st_dev,
                opened_metadata.st_ino,
            ):
                raise SnapshotRecoveryError("durable snapshot changed during recovery")
            raw_payload = os.read(descriptor, MAX_PERSISTED_SNAPSHOT_BYTES + 1)
            final_metadata = os.fstat(descriptor)
            _validate_snapshot_metadata(final_metadata)
            if (
                len(raw_payload) > MAX_PERSISTED_SNAPSHOT_BYTES
                or opened_metadata.st_mode != final_metadata.st_mode
                or opened_metadata.st_size != len(raw_payload)
                or opened_metadata.st_size != final_metadata.st_size
                or (opened_metadata.st_dev, opened_metadata.st_ino)
                != (final_metadata.st_dev, final_metadata.st_ino)
            ):
                raise SnapshotRecoveryError(
                    "durable snapshot cannot be recovered safely"
                )
        except SnapshotRecoveryError:
            raise
        except OSError as error:
            raise SnapshotRecoveryError("durable snapshot cannot be recovered safely") from error
        finally:
            if descriptor is not None:
                os.close(descriptor)
            if directory_descriptor is not None:
                os.close(directory_descriptor)

        try:
            snapshot = SnapshotDTO.model_validate_json(raw_payload)
            return validate_snapshot(snapshot)
        except (SnapshotError, ValidationError, ValueError, UnicodeError) as error:
            raise SnapshotRecoveryError("durable snapshot cannot be recovered safely") from error


def _open_parent_directory(parent: Path, *, create: bool) -> int:
    absolute_parent = Path(os.path.abspath(parent))
    flags = (
        os.O_RDONLY
        | os.O_DIRECTORY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    descriptor = os.open(absolute_parent.anchor, flags)
    try:
        for component in absolute_parent.parts[1:]:
            created = False
            try:
                child_descriptor = os.open(component, flags, dir_fd=descriptor)
            except FileNotFoundError:
                if not create:
                    raise
                try:
                    os.mkdir(component, mode=0o700, dir_fd=descriptor)
                    created = True
                except FileExistsError:
                    pass
                if created:
                    os.chmod(
                        component,
                        0o700,
                        dir_fd=descriptor,
                        follow_symlinks=False,
                    )
                child_descriptor = os.open(component, flags, dir_fd=descriptor)

            if created:
                os.fchmod(child_descriptor, 0o700)
                os.fsync(child_descriptor)
                os.fsync(descriptor)
            os.close(descriptor)
            descriptor = child_descriptor
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _create_temporary_file(
    *,
    directory_descriptor: int,
    target_name: str,
) -> tuple[int, str]:
    flags = (
        os.O_RDWR
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    for _ in range(100):
        temporary_name = f".{target_name}.{secrets.token_hex(16)}.tmp"
        try:
            descriptor = os.open(
                temporary_name,
                flags,
                0o600,
                dir_fd=directory_descriptor,
            )
        except FileExistsError:
            continue
        return descriptor, temporary_name
    raise SnapshotStoreError("snapshot could not be stored safely")


def _validate_snapshot_metadata(metadata: os.stat_result) -> None:
    if not stat.S_ISREG(metadata.st_mode):
        raise SnapshotRecoveryError("durable snapshot must be a regular file")
    if stat.S_IMODE(metadata.st_mode) != 0o600:
        raise SnapshotRecoveryError("durable snapshot has unsafe permissions")
    if metadata.st_size > MAX_PERSISTED_SNAPSHOT_BYTES:
        raise SnapshotRecoveryError("durable snapshot cannot be recovered safely")


__all__ = (
    "MAX_PERSISTED_SNAPSHOT_BYTES",
    "SnapshotRecoveryError",
    "SnapshotStore",
    "SnapshotStoreError",
)
