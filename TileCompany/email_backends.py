"""Delivery backends used only by explicitly selected local verification modes."""

from django.core.exceptions import ImproperlyConfigured
from django.core.mail.backends.base import BaseEmailBackend


class LocalProductionDisabledEmailBackend(BaseEmailBackend):
    """Fail clearly without opening an SMTP connection or claiming delivery."""

    def send_messages(self, email_messages):
        if not email_messages:
            return 0
        raise ImproperlyConfigured(
            "Email delivery is disabled in APP_ENV=local_production; "
            "verify it in real staging or production."
        )
