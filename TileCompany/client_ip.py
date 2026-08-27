"""Authoritative client-IP extraction for abuse-control request boundaries."""
import ipaddress

from django.conf import settings


def _validated_ip(value):
    try:
        return str(ipaddress.ip_address((value or "").strip()))
    except ValueError:
        return None


def get_client_ip(request):
    """Return a normalized IPv4/IPv6 address, or ``unknown`` when unavailable.

    The proxy-provided header is considered only after an explicit production
    configuration opt-in. A malformed trusted header falls back to REMOTE_ADDR;
    neither attacker-controlled value is logged here.
    """
    if settings.TRUST_PROXY_CLIENT_IP:
        trusted = _validated_ip(request.META.get(settings.TRUSTED_CLIENT_IP_HEADER))
        if trusted:
            return trusted
    return _validated_ip(request.META.get("REMOTE_ADDR")) or "unknown"
