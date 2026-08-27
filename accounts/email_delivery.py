"""Post-commit, informational account email delivery."""

import logging

from django.template.loader import render_to_string
from django.utils import translation
from django.utils.translation import gettext as _

from orders.email_delivery import send_transactional_email


logger = logging.getLogger(__name__)


def _send_welcome_email(*, user, language, account_url, support_url):
    """Delivery failures are contained: account creation is already committed."""
    try:
        with translation.override(language):
            subject = _("Welcome to TileStore")
            context = {
                "user": user,
                "account_url": account_url,
                "support_url": support_url,
            }
            send_transactional_email(
                subject=subject,
                recipient=user.email,
                text_template="accounts/emails/welcome_email.txt",
                html_template="accounts/emails/welcome_email.html",
                context=context,
            )
    except Exception:
        logger.exception("Welcome email delivery failed for user_id=%s", user.pk)


def schedule_welcome_email(*, user, language, account_url, support_url):
    """Schedule exactly once for this successful registration transaction."""
    from django.db import transaction

    transaction.on_commit(
        lambda: _send_welcome_email(
            user=user,
            language=language,
            account_url=account_url,
            support_url=support_url,
        )
    )
