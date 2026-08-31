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
_REQUEST_QUERY_PATTERN = re.compile(r"(?P<path>/[^\s?]+)\?[^\s]*")
_PASSWORD_RESET_PATH_PATTERN = re.compile(
    r"(?P<prefix>/(?:en|fa)/accounts/reset/)[^/\s?]+/[^/\s?]+/"
)
_SMS_CHALLENGE_PATH_PATTERN = re.compile(
    r"(?P<prefix>/(?:en|fa)/accounts/(?:login/sms|register/sms|profile/phone)/)"
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/"
    r"(?P<resend>resend/)?",
    flags=re.IGNORECASE,
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
    if isinstance(value, str):
        return redact_request_path_text(value)
    return value


def redact_text(value):
    """Fallback for exception/provider text containing conventional key=value fields."""
    return _KEY_VALUE_PATTERN.sub(lambda match: f"{match.group(1)}{match.group(2)}{REDACTED}", str(value))


def redact_request_path_text(value):
    """Remove query strings and redact only established sensitive account paths."""
    text = _REQUEST_QUERY_PATTERN.sub(lambda match: match.group("path"), str(value))
    text = _PASSWORD_RESET_PATH_PATTERN.sub(
        lambda match: f"{match.group('prefix')}[redacted]/[redacted]/", text
    )
    return _SMS_CHALLENGE_PATH_PATTERN.sub(
        lambda match: f"{match.group('prefix')}[redacted]/"
        + ("resend/" if match.group("resend") else ""),
        text,
    )


class SensitiveDataRedactionFilter(logging.Filter):
    """Sanitize structured data and known sensitive request paths before output."""

    def filter(self, record):
        if isinstance(record.msg, str):
            record.msg = redact_request_path_text(record.msg)
        if isinstance(record.args, Mapping):
            record.args = redact_value(record.args)
        elif isinstance(record.args, tuple):
            record.args = tuple(redact_request_path_text(arg) if isinstance(arg, str) else arg for arg in record.args)
        elif isinstance(record.args, str):
            record.args = redact_request_path_text(record.args)
        return True


class RedactingFormatter(logging.Formatter):
    """Redact conventional secret fields in exception text before console emission."""

    def formatException(self, exc_info):  # noqa: N802 - logging API spelling
        return redact_text(super().formatException(exc_info))
