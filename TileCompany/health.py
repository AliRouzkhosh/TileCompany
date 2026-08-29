"""Minimal operational readiness probes."""

import logging
from uuid import uuid4

from django.core.cache import cache
from django.http import HttpResponse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET
from redis.exceptions import RedisError


logger = logging.getLogger(__name__)


class CacheProbeFailure(Exception):
    """The configured cache accepted no trustworthy readiness round-trip."""


@require_GET
@never_cache
def readiness(request):
    """Return readiness only when the configured Django cache round-trips."""
    key = f"tilecompany:readiness:{uuid4().hex}"
    value = uuid4().hex

    try:
        cache.set(key, value, timeout=5)
        if cache.get(key) != value:
            raise CacheProbeFailure
        cache.delete(key)
    except (CacheProbeFailure, OSError, TimeoutError, RedisError):
        # Deliberately do not include the exception: connection details belong
        # neither in the response nor in ordinary operational logs.
        logger.warning("Readiness cache probe unavailable")
        return HttpResponse("unavailable", status=503, content_type="text/plain")

    return HttpResponse("ready", content_type="text/plain")
