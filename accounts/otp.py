"""OTP domain services. They have no request/session/UI dependency."""
import secrets
from dataclasses import dataclass
from datetime import timedelta

from django.conf import settings
from django.contrib.auth.hashers import check_password, make_password
from django.core.cache import cache
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from .models import OtpChallenge, PhoneIdentity
from .phone_identity import normalize_phone_identity
from .sms import send_sms
from .throttling import ThrottleUnavailable, allow, subject_digest

OTP_TTL = timedelta(minutes=2)
COOLDOWN = 2 * 60
PHONE_SEND_LIMIT, PHONE_SEND_WINDOW = 3, 15 * 60
IP_SEND_LIMIT, IP_SEND_WINDOW = 10, 60 * 60
IP_VERIFY_LIMIT, IP_VERIFY_WINDOW = 20, 15 * 60
MAX_ATTEMPTS = 5


@dataclass(frozen=True)
class OtpRequestResult:
    challenge_id: str
    retry_after: int = 0
    delivery_failed: bool = False
    already_verified: bool = False


@dataclass(frozen=True)
class OtpVerificationResult:
    user: object = None
    success: bool = False
    account_exists: bool = False


def _fingerprint(value):
    # SECRET_KEY rotation intentionally resets short-lived throttle buckets.
    return subject_digest(value)


def _context_ip(context):
    return (context or {}).get("ip", "unknown")


def _limit(namespace, identity, limit, timeout):
    try:
        return allow(
            namespace=f"otp:{namespace}",
            subject=identity,
            limit=limit,
            window_seconds=timeout,
        )
    except ThrottleUnavailable as exc:
        # OTP/SMS is externally billed and abuse-sensitive: never bypass a
        # broken shared limiter in production (or accidentally in development).
        raise PermissionDenied("OTP rate limiting is temporarily unavailable.") from exc


def _cooldown(phone, purpose):
    key = f"otp:cooldown:{_fingerprint(phone + ':' + purpose)}"
    try:
        if cache.add(key, True, timeout=COOLDOWN):
            return 0
        return COOLDOWN  # UI may show a conservative upper bound; cache is authority.
    except Exception as exc:
        raise PermissionDenied("OTP rate limiting is temporarily unavailable.") from exc


def _ensure_sms_available(*, login=False):
    if not settings.SMS_ENABLED or (login and not settings.SMS_OTP_LOGIN_ENABLED):
        raise PermissionDenied("SMS OTP is not available.")


def _message(purpose, code, language):
    fa = language == "fa"
    if purpose == OtpChallenge.Purpose.LOGIN:
        return (f"کد ورود شما: {code}\nاین کد تا ۲ دقیقه معتبر است." if fa else f"Your login code is {code}. It expires in 2 minutes.")
    label = "ثبت‌نام" if purpose == OtpChallenge.Purpose.SIGNUP else "تأیید"
    return (f"کد {label} شما: {code}\nاین کد تا ۲ دقیقه معتبر است." if fa else f"Your verification code is {code}. It expires in 2 minutes.")


def request_signup_otp(*, raw_phone, language="fa", request_context=None):
    prepared = _request(raw_phone=raw_phone, purpose=OtpChallenge.Purpose.SIGNUP, language=language, request_context=request_context)
    if isinstance(prepared, OtpRequestResult):
        return prepared
    phone, ip_fp = prepared
    # The public request remains opaque; possession proof determines the safe UI branch.
    challenge, failed = _create_challenge(phone=phone, purpose=OtpChallenge.Purpose.SIGNUP, user=None, language=language, ip_fingerprint=ip_fp, send=True)
    return OtpRequestResult(challenge_id=str(challenge.id), delivery_failed=failed)


def verify_signup_otp(*, challenge_id, code, request_context=None):
    challenge = _verify(challenge_id=challenge_id, code=code, purpose=OtpChallenge.Purpose.SIGNUP, request_context=request_context)
    if not challenge:
        return OtpVerificationResult()
    with transaction.atomic():
        challenge = OtpChallenge.objects.select_for_update().filter(id=challenge.id, is_active=True, consumed_at__isnull=True).first()
        if not challenge or challenge.expires_at <= timezone.now():
            return OtpVerificationResult()
        if PhoneIdentity.objects.filter(phone_e164=challenge.phone_e164).exists():
            challenge.consumed_at, challenge.is_active = timezone.now(), False
            challenge.save(update_fields=["consumed_at", "is_active"])
            return OtpVerificationResult(account_exists=True)
        challenge.consumed_at, challenge.is_active = timezone.now(), False
        challenge.save(update_fields=["consumed_at", "is_active"])
        return OtpVerificationResult(user=challenge.phone_e164, success=True)


def _create_challenge(*, phone, purpose, user, language, ip_fingerprint, send):
    now = timezone.now()
    code = f"{secrets.randbelow(1_000_000):06d}"
    # The partial unique constraint is the final authority across processes.
    # A concurrent sender can race on databases where the initial row lock sees
    # no active row; retry once after invalidating the newly visible contender.
    for _ in range(2):
        try:
            with transaction.atomic():
                OtpChallenge.objects.select_for_update().filter(phone_e164=phone, purpose=purpose, is_active=True).update(is_active=False, invalidated_at=now)
                challenge = OtpChallenge.objects.create(
                    purpose=purpose, phone_e164=phone, user=user, code_hash=make_password(code),
                    communication_language=language if language in {"fa", "en"} else "fa", expires_at=now + OTP_TTL,
                    request_ip_fingerprint=ip_fingerprint, send_status=OtpChallenge.SendStatus.PENDING if send else OtpChallenge.SendStatus.NOT_SENT,
                )
            break
        except IntegrityError:
            continue
    else:
        raise PermissionDenied("OTP request could not be safely created.")
    if not send:
        return challenge, False
    result = send_sms(to=phone, message=_message(purpose, code, challenge.communication_language), purpose=purpose)
    if result.accepted:
        challenge.send_status, challenge.sent_at, challenge.provider_message_id = OtpChallenge.SendStatus.SENT, timezone.now(), result.provider_message_id
    else:
        # A failed delivery is terminal: a user must explicitly retry after cooldown.
        challenge.send_status, challenge.send_error_class, challenge.is_active, challenge.invalidated_at = OtpChallenge.SendStatus.FAILED, result.error_class, False, timezone.now()
    challenge.save(update_fields=["send_status", "sent_at", "provider_message_id", "send_error_class", "is_active", "invalidated_at"])
    return challenge, not result.accepted


def _request(*, raw_phone, purpose, user=None, language="fa", request_context=None, login=False):
    _ensure_sms_available(login=login)
    phone = normalize_phone_identity(raw_phone)
    ip = _context_ip(request_context)
    if not _limit("send-phone", phone, PHONE_SEND_LIMIT, PHONE_SEND_WINDOW) or not _limit("send-ip", ip, IP_SEND_LIMIT, IP_SEND_WINDOW):
        raise PermissionDenied("Too many OTP requests.")
    retry = _cooldown(phone, purpose)
    if retry:
        return OtpRequestResult(challenge_id="", retry_after=retry)
    return phone, _fingerprint(ip)


def request_login_otp(*, raw_phone, language="fa", request_context=None):
    prepared = _request(raw_phone=raw_phone, purpose=OtpChallenge.Purpose.LOGIN, language=language, request_context=request_context, login=True)
    if isinstance(prepared, OtpRequestResult):
        return prepared
    phone, ip_fp = prepared
    identity = PhoneIdentity.objects.select_related("user").filter(phone_e164=phone, user__is_active=True).first()
    # Always create a public UUID challenge: unknown/inactive flows are opaque and cannot authenticate.
    challenge, failed = _create_challenge(phone=phone, purpose=OtpChallenge.Purpose.LOGIN, user=identity.user if identity else None, language=language, ip_fingerprint=ip_fp, send=bool(identity))
    return OtpRequestResult(challenge_id=str(challenge.id), delivery_failed=failed)


def request_phone_verification(*, user, raw_phone, language="fa", request_context=None):
    _ensure_sms_available()
    if not user or not user.is_authenticated or not user.is_active:
        raise PermissionDenied("An active authenticated user is required.")
    phone = normalize_phone_identity(raw_phone)
    existing = PhoneIdentity.objects.filter(phone_e164=phone).first()
    if existing and existing.user_id == user.id:
        return OtpRequestResult(challenge_id="", already_verified=True)
    if existing:
        raise ValidationError("This phone number is already verified.")
    prepared = _request(raw_phone=raw_phone, purpose=OtpChallenge.Purpose.VERIFY_PHONE, user=user, language=language, request_context=request_context)
    if isinstance(prepared, OtpRequestResult):
        return prepared
    _, ip_fp = prepared
    challenge, failed = _create_challenge(phone=phone, purpose=OtpChallenge.Purpose.VERIFY_PHONE, user=user, language=language, ip_fingerprint=ip_fp, send=True)
    return OtpRequestResult(challenge_id=str(challenge.id), delivery_failed=failed)


def _verify(*, challenge_id, code, purpose, request_context):
    ip = _context_ip(request_context)
    try:
        allowed = _limit("verify-ip", ip, IP_VERIFY_LIMIT, IP_VERIFY_WINDOW)
    except PermissionDenied:
        # A verifier must fail closed without turning cache availability into an
        # account-enumeration or request-error signal.
        return None
    if not allowed:
        return None
    now = timezone.now()
    with transaction.atomic():
        challenge = OtpChallenge.objects.select_for_update().filter(id=challenge_id, purpose=purpose).first()
        if not challenge or not challenge.is_active or challenge.expires_at <= now or challenge.consumed_at or challenge.invalidated_at:
            return None
        if not check_password(code or "", challenge.code_hash):
            challenge.attempt_count += 1
            fields = ["attempt_count"]
            if challenge.attempt_count >= MAX_ATTEMPTS:
                challenge.is_active, challenge.invalidated_at = False, now
                fields += ["is_active", "invalidated_at"]
            challenge.save(update_fields=fields)
            return None
        return challenge


def verify_login_otp(*, challenge_id, code, request_context=None):
    challenge = _verify(challenge_id=challenge_id, code=code, purpose=OtpChallenge.Purpose.LOGIN, request_context=request_context)
    if not challenge or not challenge.user_id:
        return OtpVerificationResult()
    # Re-lock before consuming. `_verify` intentionally releases its lock after
    # counting a failed attempt, so this second transaction closes the success
    # race: only one contender can observe the still-active correct challenge.
    with transaction.atomic():
        challenge = OtpChallenge.objects.select_for_update(of=("self",)).select_related("user").filter(id=challenge.id).first()
        if not challenge or not challenge.is_active or challenge.consumed_at or challenge.invalidated_at or challenge.expires_at <= timezone.now():
            return OtpVerificationResult()
        # Authoritative ownership is rechecked before one-time consumption.
        if not challenge.user.is_active or not PhoneIdentity.objects.filter(user=challenge.user, phone_e164=challenge.phone_e164).exists():
            return OtpVerificationResult()
        challenge.consumed_at, challenge.is_active = timezone.now(), False
        challenge.save(update_fields=["consumed_at", "is_active"])
        return OtpVerificationResult(user=challenge.user, success=True)


def verify_phone_otp(*, user, challenge_id, code, request_context=None):
    if not user or not user.is_authenticated or not user.is_active:
        return OtpVerificationResult()
    challenge = _verify(challenge_id=challenge_id, code=code, purpose=OtpChallenge.Purpose.VERIFY_PHONE, request_context=request_context)
    if not challenge or challenge.user_id != user.id:
        return OtpVerificationResult()
    try:
        with transaction.atomic():
            challenge = OtpChallenge.objects.select_for_update().filter(id=challenge.id, user=user).first()
            if not challenge or not challenge.is_active or challenge.consumed_at or challenge.invalidated_at or challenge.expires_at <= timezone.now():
                return OtpVerificationResult()
            PhoneIdentity.objects.select_for_update().filter(user=user).first()
            if PhoneIdentity.objects.filter(phone_e164=challenge.phone_e164).exclude(user=user).exists():
                return OtpVerificationResult()
            PhoneIdentity.objects.update_or_create(user=user, defaults={"phone_e164": challenge.phone_e164, "verified_at": timezone.now()})
            challenge.consumed_at, challenge.is_active = timezone.now(), False
            challenge.save(update_fields=["consumed_at", "is_active"])
    except IntegrityError:
        return OtpVerificationResult()
    return OtpVerificationResult(user=user, success=True)
