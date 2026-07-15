from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Protocol, final

import grpc
from google.protobuf.message import DecodeError, Message

from src.xray.dtos import XrayUser
from src.xray.exceptions import XrayProtocolError, XrayTimeoutError, XrayUnavailableError
from src.xray.protocol import (
    AddUserOperation,
    AlterInboundRequest,
    AlterInboundResponse,
    GetInboundUserRequest,
    GetInboundUserResponse,
    RemoveUserOperation,
    TypedMessage,
    User,
    VlessAccount,
)


_HANDLER_SERVICE = "/xray.app.proxyman.command.HandlerService"
_VLESS_ACCOUNT_TYPE = "xray.proxy.vless.Account"
_ADD_USER_TYPE = "xray.app.proxyman.command.AddUserOperation"
_REMOVE_USER_TYPE = "xray.app.proxyman.command.RemoveUserOperation"
_CANONICAL_UUID = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-"
    r"[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)


class XrayClient(Protocol):
    def get_inbound_users(self, *, tag: str) -> tuple[XrayUser, ...]: ...

    def add_user(self, *, tag: str, user: XrayUser) -> None: ...

    def remove_user(self, *, tag: str, email: str) -> None: ...


@final
@dataclass(kw_only=True, slots=True, frozen=True)
class GrpcXrayClient:
    channel: grpc.Channel
    timeout_seconds: float

    def get_inbound_users(self, *, tag: str) -> tuple[XrayUser, ...]:
        response = self._rpc(
            method="GetInboundUsers",
            request=GetInboundUserRequest(tag=tag),
            response_type=GetInboundUserResponse,
        )
        users: list[XrayUser] = []
        seen_emails: set[str] = set()
        try:
            for raw_user in response.users:
                if not raw_user.email or raw_user.email in seen_emails:
                    raise XrayProtocolError("Xray returned an invalid user set")
                if raw_user.account.type != _VLESS_ACCOUNT_TYPE:
                    raise XrayProtocolError("Xray returned an unsupported account type")
                account = VlessAccount.FromString(raw_user.account.value)
                if not _CANONICAL_UUID.fullmatch(account.id):
                    raise XrayProtocolError("Xray returned an invalid VLESS account")
                seen_emails.add(raw_user.email)
                users.append(
                    XrayUser(
                        email=raw_user.email,
                        uuid=account.id,
                        flow=account.flow,
                    )
                )
        except DecodeError as error:
            raise XrayProtocolError("Xray returned an invalid protocol message") from error
        return tuple(sorted(users, key=lambda user: user.email))

    def add_user(self, *, tag: str, user: XrayUser) -> None:
        account = VlessAccount(id=user.uuid, flow=user.flow)
        protocol_user = User(
            email=user.email,
            account=TypedMessage(
                type=_VLESS_ACCOUNT_TYPE,
                value=account.SerializeToString(),
            ),
        )
        operation = AddUserOperation(user=protocol_user)
        self._alter(
            tag=tag,
            operation_type=_ADD_USER_TYPE,
            operation=operation,
        )

    def remove_user(self, *, tag: str, email: str) -> None:
        self._alter(
            tag=tag,
            operation_type=_REMOVE_USER_TYPE,
            operation=RemoveUserOperation(email=email),
        )

    def _alter(self, *, tag: str, operation_type: str, operation: Message) -> None:
        request = AlterInboundRequest(
            tag=tag,
            operation=TypedMessage(
                type=operation_type,
                value=operation.SerializeToString(),
            ),
        )
        self._rpc(
            method="AlterInbound",
            request=request,
            response_type=AlterInboundResponse,
        )

    def _rpc(
        self,
        *,
        method: str,
        request: Message,
        response_type: type[Message],
    ) -> Message:
        rpc = self.channel.unary_unary(
            f"{_HANDLER_SERVICE}/{method}",
            request_serializer=lambda message: message.SerializeToString(),
            response_deserializer=response_type.FromString,
        )
        try:
            return rpc(request, timeout=self.timeout_seconds)
        except grpc.RpcError as error:
            if error.code() is grpc.StatusCode.DEADLINE_EXCEEDED:
                raise XrayTimeoutError("Xray request timed out") from error
            if error.code() is grpc.StatusCode.UNAVAILABLE:
                raise XrayUnavailableError("Xray is unavailable") from error
            raise XrayProtocolError("Xray rejected the runtime operation") from error
        except DecodeError as error:
            raise XrayProtocolError("Xray returned an invalid protocol message") from error


__all__ = ("GrpcXrayClient", "XrayClient")
