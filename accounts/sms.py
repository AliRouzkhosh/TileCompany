"""Provider-neutral SMS boundary. Vendor adapters belong in future work."""
from dataclasses import dataclass
from django.conf import settings


@dataclass(frozen=True)
class SmsSendResult:
    accepted: bool
    status: str
    provider_message_id: str = ""
    error_class: str = ""


class DisabledSmsBackend:
    def send_sms(self, *, to, message, purpose, context=None):
        return SmsSendResult(False, "disabled", error_class="SmsDisabled")


outbox = []


class MemorySmsBackend:
    def send_sms(self, *, to, message, purpose, context=None):
        outbox.append({"to": to, "body": message, "purpose": purpose, "metadata": context or {}})
        return SmsSendResult(True, "accepted")


class ConsoleSmsBackend:
    def send_sms(self, *, to, message, purpose, context=None):
        # Only selectable with DEBUG=True; protected by a system check.
        print(f"[DEV SMS] To: {to}\nCode/message: {message}")
        return SmsSendResult(True, "accepted")


class FailingSmsBackend:
    """Test-only deterministic failure transport; never a production adapter."""
    def send_sms(self, *, to, message, purpose, context=None):
        return SmsSendResult(False, "failed", error_class="SimulatedSmsFailure")


def get_backend():
    from django.utils.module_loading import import_string
    backend = import_string(settings.SMS_BACKEND)
    return backend()


def send_sms(*, to, message, purpose, context=None):
    if not settings.SMS_ENABLED:
        return SmsSendResult(False, "disabled", error_class="SmsDisabled")
    return get_backend().send_sms(to=to, message=message, purpose=purpose, context=context)
