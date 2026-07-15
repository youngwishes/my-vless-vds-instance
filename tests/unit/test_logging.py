from __future__ import annotations

import logging

from src.app import create_app
from src.config import EnvironmentMode, Settings
from src.security.logging import BearerCredentialRedactionFilter


TOKEN = "raw-secret-token-000000000000000001"


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
