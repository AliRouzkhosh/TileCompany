"""Narrow, durable delivery of transactional Order email."""

import logging
from datetime import timedelta

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.db import transaction
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone, translation
from django.utils.translation import gettext as _

from .models import OrderEmailDelivery


logger = logging.getLogger(__name__)
STALE_SENDING_AFTER = timedelta(minutes=10)


def send_transactional_email(*, subject, recipient, text_template, html_template, context):
    """Send an HTML email with a required text alternative through Django's backend."""
    text_body = render_to_string(text_template, context)
    html_body = render_to_string(html_template, context)
    message = EmailMultiAlternatives(subject, text_body, settings.DEFAULT_FROM_EMAIL, [recipient])
    message.attach_alternative(html_body, "text/html")
    sent = message.send(fail_silently=False)
    if sent != 1:
        raise RuntimeError("email_backend_did_not_accept_message")


def _absolute_url(view_name, *, kwargs=None):
    return f"{settings.SITE_URL}{reverse(view_name, kwargs=kwargs or {})}"


def _subject(kind):
    return {
        OrderEmailDelivery.Kind.ONLINE_PAID_RECEIPT: _("Your paid TileStore order receipt"),
        OrderEmailDelivery.Kind.COD_CONFIRMATION: _("Your Cash on Delivery order confirmation"),
        OrderEmailDelivery.Kind.COD_PAID_RECEIPT: _("Your Cash on Delivery payment receipt"),
    }[kind]


def _email_context(delivery):
    order = delivery.order
    return {
        "delivery": delivery,
        "order": order,
        "items": order.items.all(),
        "order_url": _absolute_url("orders:order_detail", kwargs={"order_id": order.pk}),
        "support_url": _absolute_url("support:support_home"),
    }


def dispatch_order_email_delivery(*, delivery_id):
    """Claim then send a committed intent; failures are recorded without commerce rollback."""
    now = timezone.now()
    with transaction.atomic():
        delivery = (
            OrderEmailDelivery.objects.select_for_update()
            .select_related("order")
            .prefetch_related("order__items")
            .get(pk=delivery_id)
        )
        if delivery.status == OrderEmailDelivery.Status.SENT:
            return False
        if (
            delivery.status == OrderEmailDelivery.Status.SENDING
            and delivery.last_attempt_at
            and delivery.last_attempt_at > now - STALE_SENDING_AFTER
        ):
            return False
        delivery.status = OrderEmailDelivery.Status.SENDING
        delivery.attempt_count += 1
        delivery.last_attempt_at = now
        delivery.failure_code = ""
        delivery.save(update_fields=["status", "attempt_count", "last_attempt_at", "failure_code"])

    try:
        with translation.override(delivery.order.communication_language):
            send_transactional_email(
                subject=_subject(delivery.kind),
                recipient=delivery.recipient,
                text_template="orders/emails/order_email.txt",
                html_template="orders/emails/order_email.html",
                context=_email_context(delivery),
            )
    except Exception as error:
        logger.exception(
            "Order transactional email failed kind=%s order_id=%s error_class=%s",
            delivery.kind,
            delivery.order_id,
            error.__class__.__name__,
        )
        OrderEmailDelivery.objects.filter(pk=delivery_id).update(
            status=OrderEmailDelivery.Status.FAILED,
            failure_code=error.__class__.__name__[:100],
        )
        return False

    OrderEmailDelivery.objects.filter(pk=delivery_id).update(
        status=OrderEmailDelivery.Status.SENT,
        sent_at=timezone.now(),
        failure_code="",
    )
    logger.info("Order transactional email sent kind=%s order_id=%s", delivery.kind, delivery.order_id)
    return True


def ensure_order_email_delivery(*, order, kind):
    """Create one intent under the commerce transaction and dispatch only after commit."""
    if not order.email_receipt_requested or not order.receipt_email:
        return None
    # Django's get_or_create contains its own savepoint-backed integrity retry,
    # which keeps the surrounding commerce transaction usable under a race.
    delivery, _created = OrderEmailDelivery.objects.get_or_create(
        order=order,
        kind=kind,
        defaults={"recipient": order.receipt_email},
    )
    transaction.on_commit(lambda: dispatch_order_email_delivery(delivery_id=delivery.pk))
    return delivery
