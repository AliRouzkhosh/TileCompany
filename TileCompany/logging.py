"""Small, conservative logging safeguards for deployment-captured output."""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping


REDACTED = "<redacted>"
_SENSITIVE_KEY_PARTS = (
    "password",
    "token",
    "secret",
    "authorization",
    "cookie",
    "session",
    "csrf",
    "otp",
    "merchant",
    "api_key",
    "apikey",
)
_KEY_VALUE_PATTERN = re.compile(
    r"(?i)\b(password|token|secret|authorization|cookie|session|csrf|otp|merchant(?:[_-]?id)?|api[_-]?key)"
    r"\s*([=:])\s*([^\s,;}\]\r\n]+)"
)


def _is_sensitive_key(key):
    normalized = str(key).casefold().replace("-", "_")
    return any(part in normalized for part in _SENSITIVE_KEY_PARTS)


def redact_value(value):
    """Redact sensitive structured values without changing ordinary diagnostics."""
    if isinstance(value, Mapping):
        return {
            key: REDACTED if _is_sensitive_key(key) else redact_value(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact_value(item) for item in value]
    if isinstance(value, tuple):
        return tuple(redact_value(item) for item in value)
    return value


def redact_text(value):
    """Fallback for exception/provider text containing conventional key=value fields."""
    return _KEY_VALUE_PATTERN.sub(lambda match: f"{match.group(1)}{match.group(2)}{REDACTED}", str(value))


class SensitiveDataRedactionFilter(logging.Filter):
    """Sanitize mapping arguments before a handler formats the record."""

    def filter(self, record):
        if isinstance(record.args, Mapping):
            record.args = redact_value(record.args)
        return True


class RedactingFormatter(logging.Formatter):
    """Redact conventional secret fields in exception text before console emission."""

    def formatException(self, exc_info):  # noqa: N802 - logging API spelling
        return redact_text(super().formatException(exc_info))
