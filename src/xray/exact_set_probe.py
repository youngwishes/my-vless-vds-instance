from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, final

from src.xray.dtos import VLESS_VISION_FLOW, XrayUser, access_email

if TYPE_CHECKING:
    from collections.abc import Sequence

    from src.api.schemas import AccessDTO
    from src.xray.client import XrayClient


@final
@dataclass(kw_only=True, slots=True, frozen=True)
class ExactSetMatchesService:
    client: XrayClient
    managed_inbound_tag: str

    def __call__(self, *, accesses: Sequence[AccessDTO]) -> bool:
        desired = {
            XrayUser(
                email=access_email(access.access_id),
                uuid=access.uuid,
                flow=VLESS_VISION_FLOW,
            )
            for access in accesses
        }
        current = set(
            self.client.get_inbound_users(tag=self.managed_inbound_tag)
        )
        return current == desired


__all__ = ("ExactSetMatchesService",)
