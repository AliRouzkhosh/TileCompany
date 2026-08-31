"""Minimal operational readiness probes."""

import logging
from uuid import uuid4

from django.core.cache import cache
from django.db import DatabaseError, InterfaceError, connection
from django.http import HttpResponse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET
from redis.exceptions import RedisError


logger = logging.getLogger(__name__)


class CacheProbeFailure(Exception):
    """The configured cache accepted no trustworthy readiness round-trip."""


class DatabaseProbeFailure(Exception):
    """The configured database returned no trustworthy readiness result."""


@require_GET
@never_cache
def readiness(request):
    """Return readiness only when database and cache dependencies are usable."""
    # Probe the database first so readiness proves the system of record before
    # testing the shared cache.  A failure remains deliberately generic.
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            if cursor.fetchone() != (1,):
                raise DatabaseProbeFailure
    except (DatabaseProbeFailure, DatabaseError, InterfaceError):
        # Deliberately do not include the exception: connection details belong
        # neither in the response nor in ordinary operational logs.
        logger.warning("Readiness database probe unavailable")
        return HttpResponse("unavailable", status=503, content_type="text/plain")

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
