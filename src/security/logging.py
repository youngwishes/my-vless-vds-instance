from __future__ import annotations

import logging
import re
from collections.abc import Mapping
from functools import wraps
from typing import Any


REDACTED = "[REDACTED]"

_QUOTED_AUTHORIZATION = re.compile(
    r"(?i)([\"']?authorization[\"']?\s*[:=]\s*)([\"'])(.*?)(\2)"
)
_UNQUOTED_AUTHORIZATION = re.compile(
    r"(?i)(\bauthorization\b\s*[:=]\s*)[^\r\n,;}]+"
)
_BEARER_CREDENTIAL = re.compile(r"(?i)\bbearer\s+[^\s,;}\]\[\"']+")
_STANDARD_LOG_RECORD_ATTRIBUTES = frozenset(
    logging.LogRecord("", 0, "", 0, "", (), None).__dict__
)


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


def _normalized_key(key: object | None) -> str:
    try:
        if isinstance(key, bytes):
            return key.decode("latin-1").lower().replace("-", "_")
        return str(key).lower().replace("-", "_") if key is not None else ""
    except Exception:
        return ""


def _redacted_like(value: object) -> object:
    if isinstance(value, bytes):
        return REDACTED.encode("ascii")
    if isinstance(value, bytearray):
        return bytearray(REDACTED, "ascii")
    return REDACTED


def _redact_log_value(
    value: Any,
    *,
    key: object | None = None,
    seen: set[int] | None = None,
) -> Any:
    if seen is None:
        seen = set()
    normalized_key = _normalized_key(key)
    if normalized_key in {"authorization", "authorization_header"}:
        return _redacted_like(value)
    if value is None or isinstance(value, (bool, int, float, complex)):
        return value
    if isinstance(value, str):
        return redact_log_text(value)
    if isinstance(value, bytes):
        return redact_log_text(value.decode("latin-1")).encode("latin-1")
    if isinstance(value, bytearray):
        redacted = redact_log_text(value.decode("latin-1"))
        return bytearray(redacted, "latin-1")
    if isinstance(value, Mapping):
        identity = id(value)
        if identity in seen:
            return REDACTED
        seen.add(identity)
        try:
            return {
                item_key: _redact_log_value(
                    item_value,
                    key=item_key,
                    seen=seen,
                )
                for item_key, item_value in value.items()
            }
        except Exception:
            return REDACTED
        finally:
            seen.discard(identity)
    if isinstance(value, tuple):
        if len(value) == 2 and _normalized_key(value[0]) in {
            "authorization",
            "authorization_header",
        }:
            return (value[0], _redacted_like(value[1]))
        identity = id(value)
        if identity in seen:
            return REDACTED
        seen.add(identity)
        try:
            return tuple(_redact_log_value(item, seen=seen) for item in value)
        except Exception:
            return REDACTED
        finally:
            seen.discard(identity)
    if isinstance(value, list):
        identity = id(value)
        if identity in seen:
            return REDACTED
        seen.add(identity)
        try:
            return [_redact_log_value(item, seen=seen) for item in value]
        except Exception:
            return REDACTED
        finally:
            seen.discard(identity)
    try:
        return redact_log_text(str(value))
    except Exception:
        return REDACTED


class BearerCredentialRedactionFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            self._redact_record(record)
        except Exception:
            self._apply_safe_fallback(record)
        return True

    def _redact_record(self, record: logging.LogRecord) -> None:
        try:
            rendered_message = record.getMessage()
        except Exception:
            record.msg = _redact_log_value(record.msg)
            record.args = _redact_log_value(record.args)
        else:
            record.msg = redact_log_text(rendered_message)
            record.args = ()

        if record.exc_info is not None:
            try:
                exception_text = logging.Formatter().formatException(record.exc_info)
                record.exc_text = redact_log_text(exception_text)
            except Exception:
                record.exc_text = REDACTED
            record.exc_info = None
        elif record.exc_text is not None:
            record.exc_text = redact_log_text(record.exc_text)
        if record.stack_info is not None:
            record.stack_info = redact_log_text(record.stack_info)

        custom_keys = tuple(
            key
            for key in record.__dict__
            if key not in _STANDARD_LOG_RECORD_ATTRIBUTES
        )
        for key in custom_keys:
            record.__dict__[key] = _redact_log_value(
                record.__dict__[key],
                key=key,
            )

    def _apply_safe_fallback(self, record: logging.LogRecord) -> None:
        record.msg = REDACTED
        record.args = ()
        record.exc_info = None
        record.exc_text = REDACTED
        record.stack_info = None
        for key in tuple(record.__dict__):
            if key not in _STANDARD_LOG_RECORD_ATTRIBUTES:
                record.__dict__[key] = REDACTED


def install_logging_redaction() -> None:
    current_make_record = logging.Logger.makeRecord
    if getattr(current_make_record, "_vless_bearer_redaction", False):
        return

    redaction_filter = BearerCredentialRedactionFilter()

    @wraps(current_make_record)
    def redacting_make_record(
        logger: logging.Logger,
        *args: Any,
        **kwargs: Any,
    ) -> logging.LogRecord:
        record = current_make_record(logger, *args, **kwargs)
        redaction_filter.filter(record)
        return record

    redacting_make_record._vless_bearer_redaction = True  # type: ignore[attr-defined]
    logging.Logger.makeRecord = redacting_make_record  # type: ignore[method-assign]


__all__ = (
    "BearerCredentialRedactionFilter",
    "install_logging_redaction",
    "redact_log_text",
)
