from __future__ import annotations

import logging
import re
from collections.abc import Callable, Mapping
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


def _redact_log_value(value: Any, *, key: object | None = None) -> Any:
    normalized_key = str(key).lower().replace("-", "_") if key is not None else ""
    if normalized_key in {"authorization", "authorization_header"}:
        return REDACTED
    if isinstance(value, str):
        return redact_log_text(value)
    if isinstance(value, Mapping):
        return {
            item_key: _redact_log_value(item_value, key=item_key)
            for item_key, item_value in value.items()
        }
    if isinstance(value, tuple):
        return tuple(_redact_log_value(item) for item in value)
    if isinstance(value, list):
        return [_redact_log_value(item) for item in value]
    return value


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
            setattr(record, key, _redact_log_value(value, key=key))
        return True


LogRecordFactory = Callable[..., logging.LogRecord]


def install_logging_redaction() -> None:
    for logger in _configured_loggers():
        for handler in logger.handlers:
            if not any(
                isinstance(item, BearerCredentialRedactionFilter)
                for item in handler.filters
            ):
                handler.addFilter(BearerCredentialRedactionFilter())

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


def _configured_loggers() -> tuple[logging.Logger, ...]:
    named_loggers = tuple(
        logger
        for logger in logging.Logger.manager.loggerDict.values()
        if isinstance(logger, logging.Logger)
    )
    return (logging.getLogger(), *named_loggers)


__all__ = (
    "BearerCredentialRedactionFilter",
    "install_logging_redaction",
    "redact_log_text",
)
