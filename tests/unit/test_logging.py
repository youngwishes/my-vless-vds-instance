from __future__ import annotations

import logging
from io import StringIO

from starlette.datastructures import Headers

from src.app import create_app
from src.config import EnvironmentMode, Settings
from src.security.logging import BearerCredentialRedactionFilter


TOKEN = "raw-secret-token-000000000000000001"


class _CollectingHandler(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


class _SecretStringObject:
    def __str__(self) -> str:
        return f"Authorization: Bearer {TOKEN}"


class _BrokenStringObject:
    def __str__(self) -> str:
        raise RuntimeError("cannot stringify")


def test_redaction_filter_removes_bearer_and_authorization_values() -> None:
    record = logging.LogRecord(
        name="agent.test",
        level=logging.WARNING,
        pathname=__file__,
        lineno=1,
        msg="request headers={'Authorization': 'Bearer %s'} fallback=Bearer %s",
        args=(TOKEN, TOKEN),
        exc_info=None,
    )

    assert BearerCredentialRedactionFilter().filter(record) is True

    rendered = record.getMessage()
    assert TOKEN not in rendered
    assert "Authorization" in rendered
    assert "[REDACTED]" in rendered


def test_create_app_installs_redaction_for_captured_logs(caplog) -> None:
    create_app(
        settings=Settings(
            vless_node_id="node-01",
            environment_mode=EnvironmentMode.TEST,
            agent_token_current="explicit-test-token",
        )
    )

    with caplog.at_level(logging.INFO, logger="agent.authentication"):
        logging.getLogger("agent.authentication").info(
            "Authorization: Bearer %s",
            TOKEN,
        )

    assert TOKEN not in caplog.text
    assert "Authorization: [REDACTED]" in caplog.text


def test_installed_filter_redacts_post_extra_authorization_and_nested_headers(
    caplog,
) -> None:
    create_app(
        settings=Settings(
            vless_node_id="node-01",
            environment_mode=EnvironmentMode.TEST,
            agent_token_current="explicit-test-token",
        )
    )
    previous_formatter = caplog.handler.formatter
    caplog.handler.setFormatter(
        logging.Formatter("%(message)s %(authorization)s %(headers)s")
    )
    try:
        with caplog.at_level(logging.INFO, logger="agent.authentication"):
            logging.getLogger("agent.authentication").info(
                "request rejected",
                extra={
                    "authorization": f"Bearer {TOKEN}",
                    "headers": {
                        "Authorization": f"Bearer {TOKEN}",
                        "X-Safe": "visible",
                    },
                },
            )

        assert TOKEN not in caplog.text
        assert caplog.text.count("[REDACTED]") == 2
        assert "X-Safe" in caplog.text
        assert "visible" in caplog.text
    finally:
        caplog.handler.setFormatter(previous_formatter)


def test_installed_redaction_protects_handler_attached_after_app_creation() -> None:
    create_app(
        settings=Settings(
            vless_node_id="node-01",
            environment_mode=EnvironmentMode.TEST,
            agent_token_current="explicit-test-token",
        )
    )
    output = StringIO()
    handler = logging.StreamHandler(output)
    handler.setFormatter(logging.Formatter("%(message)s %(authorization)s"))
    logger = logging.getLogger("agent.late-handler")
    previous_level = logger.level
    previous_propagate = logger.propagate
    logger.setLevel(logging.INFO)
    logger.propagate = False
    logger.addHandler(handler)
    try:
        logger.info(
            "request rejected",
            extra={"authorization": f"Bearer {TOKEN}"},
        )
    finally:
        logger.removeHandler(handler)
        logger.setLevel(previous_level)
        logger.propagate = previous_propagate

    assert TOKEN not in output.getvalue()
    assert "[REDACTED]" in output.getvalue()


def test_installed_redaction_handles_asgi_bytes_headers_after_extra_merge() -> None:
    create_app(
        settings=Settings(
            vless_node_id="node-01",
            environment_mode=EnvironmentMode.TEST,
            agent_token_current="explicit-test-token",
        )
    )
    output = StringIO()
    handler = logging.StreamHandler(output)
    handler.setFormatter(logging.Formatter("%(scope)s"))
    logger = logging.getLogger("agent.asgi")
    previous_level = logger.level
    previous_propagate = logger.propagate
    logger.setLevel(logging.INFO)
    logger.propagate = False
    logger.addHandler(handler)
    try:
        logger.info(
            "request rejected",
            extra={
                "scope": {
                    b"headers": [
                        (b"authorization", f"Bearer {TOKEN}".encode()),
                        (b"x-safe", b"visible"),
                    ],
                    b"authorization": f"Bearer {TOKEN}".encode(),
                }
            },
        )
    finally:
        logger.removeHandler(handler)
        logger.setLevel(previous_level)
        logger.propagate = previous_propagate

    rendered = output.getvalue()
    assert TOKEN not in rendered
    assert rendered.count("[REDACTED]") == 2
    assert "visible" in rendered


def test_logging_redaction_installation_is_idempotent() -> None:
    settings = Settings(
        vless_node_id="node-01",
        environment_mode=EnvironmentMode.TEST,
        agent_token_current="explicit-test-token",
    )
    create_app(settings=settings)
    installed_make_record = logging.Logger.makeRecord

    create_app(settings=settings)

    assert logging.Logger.makeRecord is installed_make_record


def test_installed_redaction_preserves_deferred_formatting_failure() -> None:
    create_app(
        settings=Settings(
            vless_node_id="node-01",
            environment_mode=EnvironmentMode.TEST,
            agent_token_current="explicit-test-token",
        )
    )
    logger = logging.getLogger("agent.deferred-formatting")
    previous_level = logger.level
    previous_propagate = logger.propagate
    logger.setLevel(logging.INFO)
    logger.propagate = False
    try:
        logger.info("%d", "not-an-int")
    finally:
        logger.setLevel(previous_level)
        logger.propagate = previous_propagate


def test_installed_redaction_handles_self_referential_extra() -> None:
    create_app(
        settings=Settings(
            vless_node_id="node-01",
            environment_mode=EnvironmentMode.TEST,
            agent_token_current="explicit-test-token",
        )
    )
    recursive: dict[str, object] = {}
    recursive["self"] = recursive
    handler = _CollectingHandler()
    logger = logging.getLogger("agent.recursive-extra")
    logger.addHandler(handler)
    try:
        logger.warning("request rejected", extra={"payload": recursive})
    finally:
        logger.removeHandler(handler)

    assert handler.records
    assert handler.records[0].payload == {"self": "[REDACTED]"}


def test_installed_redaction_normalizes_generic_mapping_without_raising() -> None:
    create_app(
        settings=Settings(
            vless_node_id="node-01",
            environment_mode=EnvironmentMode.TEST,
            agent_token_current="explicit-test-token",
        )
    )
    handler = _CollectingHandler()
    logger = logging.getLogger("agent.headers-extra")
    logger.addHandler(handler)
    try:
        logger.warning(
            "request rejected",
            extra={
                "headers": Headers(
                    {
                        "Authorization": f"Bearer {TOKEN}",
                        "X-Safe": "visible",
                    }
                )
            },
        )
    finally:
        logger.removeHandler(handler)

    assert handler.records[0].headers == {
        "authorization": "[REDACTED]",
        "x-safe": "visible",
    }


def test_installed_redaction_safely_stringifies_unknown_extra_objects() -> None:
    create_app(
        settings=Settings(
            vless_node_id="node-01",
            environment_mode=EnvironmentMode.TEST,
            agent_token_current="explicit-test-token",
        )
    )
    handler = _CollectingHandler()
    logger = logging.getLogger("agent.custom-extra")
    logger.addHandler(handler)
    try:
        logger.warning(
            "request rejected",
            extra={
                "secret_object": _SecretStringObject(),
                "broken_object": _BrokenStringObject(),
                "attempt": 3,
                "enabled": True,
            },
        )
    finally:
        logger.removeHandler(handler)

    record = handler.records[0]
    assert TOKEN not in record.secret_object
    assert record.secret_object == "Authorization: [REDACTED]"
    assert record.broken_object == "[REDACTED]"
    assert record.attempt == 3
    assert record.enabled is True


def test_redaction_filter_scrubs_structured_authorization_field() -> None:
    record = logging.LogRecord(
        name="agent.test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="request rejected",
        args=(),
        exc_info=None,
    )
    record.authorization = f"Bearer {TOKEN}"

    BearerCredentialRedactionFilter().filter(record)

    assert record.authorization == "[REDACTED]"


def test_redaction_filter_scrubs_credentials_from_exception_text() -> None:
    try:
        raise RuntimeError(f"upstream sent Authorization: Bearer {TOKEN}")
    except RuntimeError:
        exc_info = __import__("sys").exc_info()
    record = logging.LogRecord(
        name="agent.test",
        level=logging.ERROR,
        pathname=__file__,
        lineno=1,
        msg="request failed",
        args=(),
        exc_info=exc_info,
    )

    BearerCredentialRedactionFilter().filter(record)
    rendered = logging.Formatter().format(record)

    assert TOKEN not in rendered
    assert "Authorization: [REDACTED]" in rendered
