from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, final

from src.xray.dtos import XrayUser, access_email

if TYPE_CHECKING:
    from collections.abc import Sequence

    from src.api.schemas import AccessDTO
    from src.xray.client import XrayClient


@final
@dataclass(kw_only=True, slots=True, frozen=True)
class ApplyExactSetService:
    client: XrayClient
    managed_inbound_tag: str

    def __call__(self, *, accesses: Sequence[AccessDTO]) -> None:
        desired = {
            access_email(access.access_id): XrayUser(
                email=access_email(access.access_id),
                uuid=access.uuid,
            )
            for access in accesses
        }
        current = {
            user.email: user
            for user in self.client.get_inbound_users(tag=self.managed_inbound_tag)
        }

        removals = sorted(
            (
                email
                for email, user in current.items()
                if email not in desired or desired[email].uuid != user.uuid
            ),
            key=_email_sort_key,
        )
        additions = sorted(
            (
                user
                for email, user in desired.items()
                if email not in current or current[email].uuid != user.uuid
            ),
            key=lambda user: int(user.email.removeprefix("vless-access-").split("@", 1)[0]),
        )

        for email in removals:
            self.client.remove_user(tag=self.managed_inbound_tag, email=email)
        for user in additions:
            self.client.add_user(tag=self.managed_inbound_tag, user=user)


def _email_sort_key(email: str) -> tuple[int, int, str]:
    prefix = "vless-access-"
    suffix = "@agent.invalid"
    if email.startswith(prefix) and email.endswith(suffix):
        access_id = email[len(prefix) : -len(suffix)]
        if access_id.isdecimal():
            return (0, int(access_id), email)
    return (1, 0, email)


def create_apply_exact_set_service(
    *,
    target: str,
    managed_inbound_tag: str,
    timeout_seconds: float,
) -> ApplyExactSetService:
    import grpc

    from src.xray.client import GrpcXrayClient

    return ApplyExactSetService(
        client=GrpcXrayClient(
            channel=grpc.insecure_channel(target),
            timeout_seconds=timeout_seconds,
        ),
        managed_inbound_tag=managed_inbound_tag,
    )


__all__ = ("ApplyExactSetService", "create_apply_exact_set_service")
