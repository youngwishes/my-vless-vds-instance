from __future__ import annotations

import json
import os
import stat
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import final

from pydantic import ValidationError

from src.api.schemas import SnapshotDTO
from src.exceptions import SnapshotError
from src.domain import validate_snapshot


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
        temporary_path: Path | None = None

        try:
            _ensure_private_parent(self.path.parent)
            descriptor, temporary_name = tempfile.mkstemp(
                dir=self.path.parent,
                prefix=f".{self.path.name}.",
                suffix=".tmp",
            )
            temporary_path = Path(temporary_name)
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

            os.replace(temporary_path, self.path)
            temporary_path = None
            directory_descriptor = os.open(self.path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory_descriptor)
            finally:
                os.close(directory_descriptor)
        except OSError as error:
            raise SnapshotStoreError("snapshot could not be stored safely") from error
        finally:
            if temporary_path is not None:
                try:
                    temporary_path.unlink(missing_ok=True)
                except OSError:
                    pass

    def load(self) -> SnapshotDTO | None:
        try:
            metadata = self.path.lstat()
        except FileNotFoundError:
            return None
        except OSError as error:
            raise SnapshotRecoveryError("durable snapshot cannot be recovered safely") from error

        if not stat.S_ISREG(metadata.st_mode):
            raise SnapshotRecoveryError("durable snapshot must be a regular file")
        if stat.S_IMODE(metadata.st_mode) != 0o600:
            raise SnapshotRecoveryError("durable snapshot has unsafe permissions")

        descriptor: int | None = None
        try:
            flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
            descriptor = os.open(self.path, flags)
            opened_metadata = os.fstat(descriptor)
            if not stat.S_ISREG(opened_metadata.st_mode):
                raise SnapshotRecoveryError("durable snapshot must be a regular file")
            if stat.S_IMODE(opened_metadata.st_mode) != 0o600:
                raise SnapshotRecoveryError("durable snapshot has unsafe permissions")
            if (metadata.st_dev, metadata.st_ino) != (
                opened_metadata.st_dev,
                opened_metadata.st_ino,
            ):
                raise SnapshotRecoveryError("durable snapshot changed during recovery")
            with os.fdopen(descriptor, "rb") as snapshot_file:
                descriptor = None
                raw_payload = snapshot_file.read()
        except SnapshotRecoveryError:
            raise
        except OSError as error:
            raise SnapshotRecoveryError("durable snapshot cannot be recovered safely") from error
        finally:
            if descriptor is not None:
                os.close(descriptor)

        try:
            snapshot = SnapshotDTO.model_validate_json(raw_payload)
            return validate_snapshot(snapshot)
        except (SnapshotError, ValidationError, ValueError, UnicodeError) as error:
            raise SnapshotRecoveryError("durable snapshot cannot be recovered safely") from error


def _ensure_private_parent(parent: Path) -> None:
    try:
        parent.mkdir(mode=0o700, parents=True, exist_ok=False)
    except FileExistsError:
        return
    parent.chmod(0o700)


__all__ = (
    "SnapshotRecoveryError",
    "SnapshotStore",
    "SnapshotStoreError",
)
