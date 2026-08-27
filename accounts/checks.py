from django.conf import settings
from django.core.checks import Error, register

_FAKE = {"accounts.sms.MemorySmsBackend", "accounts.sms.ConsoleSmsBackend"}


def _shared_cache():
    backend = settings.CACHES["default"]["BACKEND"].lower()
    return not any(value in backend for value in ("locmem", "dummy", "filebased", "database"))


@register()
def sms_configuration_check(app_configs, **kwargs):
    errors = []
    if settings.SMS_OTP_LOGIN_ENABLED and not settings.SMS_ENABLED:
        errors.append(Error("SMS_OTP_LOGIN_ENABLED requires SMS_ENABLED.", id="accounts.E001"))
    if not settings.DEBUG and settings.SMS_ENABLED and settings.SMS_BACKEND in _FAKE:
        errors.append(Error("Production SMS cannot use a fake SMS backend.", id="accounts.E002"))
    if not settings.DEBUG and settings.SMS_ENABLED and not _shared_cache():
        errors.append(Error("Production SMS requires a shared cache for abuse protection.", id="accounts.E003"))
    return errors
