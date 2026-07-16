from __future__ import annotations

from google.protobuf import descriptor_pb2, descriptor_pool, message_factory
from google.protobuf.message import Message


def _field(
    message: descriptor_pb2.DescriptorProto,
    *,
    name: str,
    number: int,
    field_type: int,
    type_name: str = "",
    repeated: bool = False,
) -> None:
    field = message.field.add()
    field.name = name
    field.number = number
    field.type = field_type
    field.label = (
        descriptor_pb2.FieldDescriptorProto.LABEL_REPEATED
        if repeated
        else descriptor_pb2.FieldDescriptorProto.LABEL_OPTIONAL
    )
    if type_name:
        field.type_name = type_name


def _message(
    file: descriptor_pb2.FileDescriptorProto,
    name: str,
) -> descriptor_pb2.DescriptorProto:
    message = file.message_type.add()
    message.name = name
    return message


def _build_pool() -> descriptor_pool.DescriptorPool:
    pool = descriptor_pool.DescriptorPool()

    typed_file = descriptor_pb2.FileDescriptorProto(
        name="common/serial/typed_message.proto",
        package="xray.common.serial",
        syntax="proto3",
    )
    typed = _message(typed_file, "TypedMessage")
    _field(typed, name="type", number=1, field_type=descriptor_pb2.FieldDescriptorProto.TYPE_STRING)
    _field(typed, name="value", number=2, field_type=descriptor_pb2.FieldDescriptorProto.TYPE_BYTES)
    pool.Add(typed_file)

    user_file = descriptor_pb2.FileDescriptorProto(
        name="common/protocol/user.proto",
        package="xray.common.protocol",
        syntax="proto3",
        dependency=["common/serial/typed_message.proto"],
    )
    user = _message(user_file, "User")
    _field(user, name="level", number=1, field_type=descriptor_pb2.FieldDescriptorProto.TYPE_UINT32)
    _field(user, name="email", number=2, field_type=descriptor_pb2.FieldDescriptorProto.TYPE_STRING)
    _field(
        user,
        name="account",
        number=3,
        field_type=descriptor_pb2.FieldDescriptorProto.TYPE_MESSAGE,
        type_name=".xray.common.serial.TypedMessage",
    )
    pool.Add(user_file)

    account_file = descriptor_pb2.FileDescriptorProto(
        name="proxy/vless/account.proto",
        package="xray.proxy.vless",
        syntax="proto3",
    )
    account = _message(account_file, "Account")
    _field(account, name="id", number=1, field_type=descriptor_pb2.FieldDescriptorProto.TYPE_STRING)
    _field(account, name="flow", number=2, field_type=descriptor_pb2.FieldDescriptorProto.TYPE_STRING)
    _field(account, name="encryption", number=3, field_type=descriptor_pb2.FieldDescriptorProto.TYPE_STRING)
    pool.Add(account_file)

    command_file = descriptor_pb2.FileDescriptorProto(
        name="app/proxyman/command/command.proto",
        package="xray.app.proxyman.command",
        syntax="proto3",
        dependency=[
            "common/protocol/user.proto",
            "common/serial/typed_message.proto",
        ],
    )
    add_user = _message(command_file, "AddUserOperation")
    _field(
        add_user,
        name="user",
        number=1,
        field_type=descriptor_pb2.FieldDescriptorProto.TYPE_MESSAGE,
        type_name=".xray.common.protocol.User",
    )
    remove_user = _message(command_file, "RemoveUserOperation")
    _field(remove_user, name="email", number=1, field_type=descriptor_pb2.FieldDescriptorProto.TYPE_STRING)
    alter_request = _message(command_file, "AlterInboundRequest")
    _field(alter_request, name="tag", number=1, field_type=descriptor_pb2.FieldDescriptorProto.TYPE_STRING)
    _field(
        alter_request,
        name="operation",
        number=2,
        field_type=descriptor_pb2.FieldDescriptorProto.TYPE_MESSAGE,
        type_name=".xray.common.serial.TypedMessage",
    )
    _message(command_file, "AlterInboundResponse")
    get_request = _message(command_file, "GetInboundUserRequest")
    _field(get_request, name="tag", number=1, field_type=descriptor_pb2.FieldDescriptorProto.TYPE_STRING)
    _field(get_request, name="email", number=2, field_type=descriptor_pb2.FieldDescriptorProto.TYPE_STRING)
    get_response = _message(command_file, "GetInboundUserResponse")
    _field(
        get_response,
        name="users",
        number=1,
        field_type=descriptor_pb2.FieldDescriptorProto.TYPE_MESSAGE,
        type_name=".xray.common.protocol.User",
        repeated=True,
    )
    service = command_file.service.add()
    service.name = "HandlerService"
    get_method = service.method.add()
    get_method.name = "GetInboundUsers"
    get_method.input_type = ".xray.app.proxyman.command.GetInboundUserRequest"
    get_method.output_type = ".xray.app.proxyman.command.GetInboundUserResponse"
    alter_method = service.method.add()
    alter_method.name = "AlterInbound"
    alter_method.input_type = ".xray.app.proxyman.command.AlterInboundRequest"
    alter_method.output_type = ".xray.app.proxyman.command.AlterInboundResponse"
    pool.Add(command_file)
    return pool


_POOL = _build_pool()


def _class(name: str) -> type[Message]:
    return message_factory.GetMessageClass(_POOL.FindMessageTypeByName(name))


TypedMessage = _class("xray.common.serial.TypedMessage")
User = _class("xray.common.protocol.User")
VlessAccount = _class("xray.proxy.vless.Account")
AddUserOperation = _class("xray.app.proxyman.command.AddUserOperation")
RemoveUserOperation = _class("xray.app.proxyman.command.RemoveUserOperation")
AlterInboundRequest = _class("xray.app.proxyman.command.AlterInboundRequest")
AlterInboundResponse = _class("xray.app.proxyman.command.AlterInboundResponse")
GetInboundUserRequest = _class("xray.app.proxyman.command.GetInboundUserRequest")
GetInboundUserResponse = _class("xray.app.proxyman.command.GetInboundUserResponse")


__all__ = (
    "AddUserOperation",
    "AlterInboundRequest",
    "AlterInboundResponse",
    "GetInboundUserRequest",
    "GetInboundUserResponse",
    "RemoveUserOperation",
    "TypedMessage",
    "User",
    "VlessAccount",
)
