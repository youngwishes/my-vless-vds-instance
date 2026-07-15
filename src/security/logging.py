from __future__ import annotations

import logging
import re
from collections.abc import Callable
from typing import Any


REDACTED = "[REDACTED]"

_QUOTED_AUTHORIZATION = re.compile(
    r"(?i)([\"']?authorization[\"']?\s*[:=]\s*)([\"'])(.*?)(\2)"
)
_UNQUOTED_AUTHORIZATION = re.compile(
    r"(?i)(\bauthorization\b\s*[:=]\s*)[^\r\n,;}]+"
)
_BEARER_CREDENTIAL = re.compile(r"(?i)\bbearer\s+[^\s,;}\]\[\"']+")


def redact_log_text(value: str) -> str:
    value = _QUOTED_AUTHORIZATION.sub(
        lambda match: f"{match.group(1)}{match.group(2)}{REDACTED}{match.group(2)}",
        value,
    )
    value = _UNQUOTED_AUTHORIZATION.sub(
        lambda match: f"{match.group(1)}{REDACTED}",
        value,
    )
    return _BEARER_CREDENTIAL.sub(f"Bearer {REDACTED}", value)


class BearerCredentialRedactionFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = redact_log_text(record.getMessage())
        record.args = ()
        if record.exc_info is not None:
            record.exc_text = redact_log_text(
                logging.Formatter().formatException(record.exc_info)
            )
            record.exc_info = None
        elif record.exc_text is not None:
            record.exc_text = redact_log_text(record.exc_text)
        if record.stack_info is not None:
            record.stack_info = redact_log_text(record.stack_info)
        for key, value in tuple(record.__dict__.items()):
            if key.lower() in {"authorization", "authorization_header"}:
                setattr(record, key, REDACTED)
            elif isinstance(value, str):
                setattr(record, key, redact_log_text(value))
        return True


LogRecordFactory = Callable[..., logging.LogRecord]


def install_logging_redaction() -> None:
    current_factory: LogRecordFactory = logging.getLogRecordFactory()
    if getattr(current_factory, "_vless_bearer_redaction", False):
        return

    redaction_filter = BearerCredentialRedactionFilter()

    def redacting_factory(*args: Any, **kwargs: Any) -> logging.LogRecord:
        record = current_factory(*args, **kwargs)
        redaction_filter.filter(record)
        return record

    redacting_factory._vless_bearer_redaction = True  # type: ignore[attr-defined]
    logging.setLogRecordFactory(redacting_factory)


__all__ = (
    "BearerCredentialRedactionFilter",
    "install_logging_redaction",
    "redact_log_text",
)
