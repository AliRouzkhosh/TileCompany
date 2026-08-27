"""Privacy-minimized security events for deployment-captured application logs."""

from __future__ import annotations

import hashlib
import hmac
import logging

from django.conf import settings


logger = logging.getLogger("TileCompany.security")


def _subject_digest(subject):
    return hmac.new(
        settings.SECRET_KEY.encode(),
        str(subject).encode(),
        hashlib.sha256,
    ).hexdigest()[:16]


def security_event(event, *, subject=None):
    """Log a stable event name and optional pseudonymous correlation subject only."""
    if subject is None:
        logger.warning("security_event=%s", event)
        return
    logger.warning("security_event=%s subject=%s", event, _subject_digest(subject))
