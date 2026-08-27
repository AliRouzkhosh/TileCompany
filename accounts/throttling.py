"""Small cache-backed, privacy-preserving throttle primitives."""
import hashlib
import hmac

from django.conf import settings
from django.core.cache import cache


class ThrottleUnavailable(Exception):
    """The backing cache cannot safely enforce an abuse-control limit."""


# Fixed-window public authentication controls. They count every validly submitted
# request before identity lookup, avoiding account-existence branches and durable
# victim-account locks. OTP retains its own independent F3J namespaces.
PASSWORD_LOGIN_IP_LIMIT, PASSWORD_LOGIN_IP_WINDOW = 30, 15 * 60
PASSWORD_LOGIN_IDENTIFIER_IP_LIMIT, PASSWORD_LOGIN_IDENTIFIER_IP_WINDOW = 8, 15 * 60
PASSWORD_RESET_IP_LIMIT, PASSWORD_RESET_IP_WINDOW = 10, 60 * 60
PASSWORD_RESET_IDENTIFIER_IP_LIMIT, PASSWORD_RESET_IDENTIFIER_IP_WINDOW = 3, 60 * 60
SIGNUP_IP_LIMIT, SIGNUP_IP_WINDOW = 10, 60 * 60


def subject_digest(subject):
    return hmac.new(
        settings.SECRET_KEY.encode(),
        str(subject).encode(),
        hashlib.sha256,
    ).hexdigest()


def cache_key(namespace, subject):
    return f"throttle:{namespace}:{subject_digest(subject)}"


def normalize_identifier(value):
    return str(value or "").strip().casefold()


def allow(*, namespace, subject, limit, window_seconds):
    """Atomically consume a fixed-window quota and return whether it is allowed.

    ``add`` establishes the counter and its expiry atomically; ``incr`` is atomic
    on Django's Redis backend and preserves that expiry. A retry covers expiry
    between those operations without falling open when the backend is unavailable.
    """
    key = cache_key(namespace, subject)
    for _ in range(2):
        try:
            if cache.add(key, 1, timeout=window_seconds):
                return True
            return cache.incr(key) <= limit
        except ValueError:
            # The counter expired between add/incr. Retry creation once.
            continue
        except Exception as exc:
            raise ThrottleUnavailable from exc
    raise ThrottleUnavailable


def password_login_allowed(*, client_ip, identifier):
    return (
        allow(
            namespace="auth:password-login-ip",
            subject=client_ip,
            limit=PASSWORD_LOGIN_IP_LIMIT,
            window_seconds=PASSWORD_LOGIN_IP_WINDOW,
        )
        and allow(
            namespace="auth:password-login-identifier-ip",
            subject=f"{normalize_identifier(identifier)}\x00{client_ip}",
            limit=PASSWORD_LOGIN_IDENTIFIER_IP_LIMIT,
            window_seconds=PASSWORD_LOGIN_IDENTIFIER_IP_WINDOW,
        )
    )


def password_reset_allowed(*, client_ip, identifier):
    return (
        allow(
            namespace="auth:password-reset-ip",
            subject=client_ip,
            limit=PASSWORD_RESET_IP_LIMIT,
            window_seconds=PASSWORD_RESET_IP_WINDOW,
        )
        and allow(
            namespace="auth:password-reset-identifier-ip",
            subject=f"{normalize_identifier(identifier)}\x00{client_ip}",
            limit=PASSWORD_RESET_IDENTIFIER_IP_LIMIT,
            window_seconds=PASSWORD_RESET_IDENTIFIER_IP_WINDOW,
        )
    )


def signup_allowed(*, client_ip):
    return allow(
        namespace="auth:signup-ip",
        subject=client_ip,
        limit=SIGNUP_IP_LIMIT,
        window_seconds=SIGNUP_IP_WINDOW,
    )
