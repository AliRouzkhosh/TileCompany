from dataclasses import dataclass

from django.utils.html import conditional_escape
from django.utils.safestring import mark_safe
from django.utils.translation import gettext as _

from .models import NotificationEvent


@dataclass(frozen=True)
class NotificationPresentation:
    title: str
    message: str
    message_html: str = ""
    action_label: str = ""
    icon: str = "bell"
    tone: str = "neutral"


@dataclass(frozen=True)
class NotificationDisplay:
    notification: object
    presentation: NotificationPresentation


def _value(payload, key):
    value = payload.get(key, "—")
    return str(value) if value not in (None, "") else "—"


def _support_status_label(payload):
    labels = {
        "open": _("Open"),
        "waiting_customer": _("Waiting for Customer"),
        "waiting_support": _("Waiting for Support"),
        "resolved": _("Resolved"),
        "closed": _("Closed"),
    }
    return labels.get(payload.get("status"), _("Updated"))


def _message_with_isolated_values(message, values):
    """Escape notification copy and isolate only its mixed-direction values."""
    rendered = conditional_escape(message)
    for value in values:
        escaped = conditional_escape(str(value))
        if escaped:
            rendered = rendered.replace(escaped, mark_safe(f'<bdi dir="auto">{escaped}</bdi>'))
    return mark_safe(rendered)


def get_notification_presentation(notification, *, action_label=None):
    payload = notification.payload if isinstance(notification.payload, dict) else {}
    order_number = _value(payload, "order_number")

    presentations = {
        NotificationEvent.PAYMENT_FAILED: (
            _("Payment failed"),
            _("Payment for order #%(order_number)s was not completed. Your order is waiting for payment.") % {"order_number": order_number},
        ),
        NotificationEvent.PAYMENT_CANCELLED: (
            _("Payment cancelled"),
            _("Payment for order #%(order_number)s was cancelled. Your order is still waiting for payment.") % {"order_number": order_number},
        ),
        NotificationEvent.ORDER_CONFIRMED: (_("Order confirmed"), _("Order #%(order_number)s has been confirmed.") % {"order_number": order_number}),
        NotificationEvent.ORDER_ON_HOLD: (_("Order on hold"), _("Order #%(order_number)s has been placed on hold.") % {"order_number": order_number}),
        NotificationEvent.ORDER_RESUMED: (_("Order resumed"), _("Order #%(order_number)s has been resumed.") % {"order_number": order_number}),
        NotificationEvent.ORDER_SHIPPED: (_("Order shipped"), _("Order #%(order_number)s has been shipped.") % {"order_number": order_number}),
        NotificationEvent.ORDER_DELIVERED: (_("Order delivered"), _("Order #%(order_number)s has been delivered.") % {"order_number": order_number}),
        NotificationEvent.ORDER_CANCELLED: (_("Order cancelled"), _("Order #%(order_number)s has been cancelled.") % {"order_number": order_number}),
        NotificationEvent.REFUND_APPROVED: (_("Refund approved"), _("Your refund request for order #%(order_number)s has been approved.") % {"order_number": order_number}),
        NotificationEvent.REFUND_NEEDS_INFORMATION: (_("More information required for your refund request"), _("Support needs additional information to continue reviewing order #%(order_number)s.") % {"order_number": order_number}),
        NotificationEvent.REFUND_REJECTED: (_("Refund rejected"), _("Your refund request for order #%(order_number)s has been rejected.") % {"order_number": order_number}),
        NotificationEvent.REFUND_PROCESSED: (_("Refund processed"), _("Your refund for order #%(order_number)s has been processed.") % {"order_number": order_number}),
        NotificationEvent.REVIEW_APPROVED: (_("Review approved"), _("Your review for %(product_name)s was approved.") % {"product_name": _value(payload, "product_name")}),
        NotificationEvent.REVIEW_COMMENT_APPROVED: (_("Review comment approved"), _("Your review comment has been approved.")),
        NotificationEvent.ARTICLE_COMMENT_APPROVED: (_("Article comment approved"), _("Your comment on %(article_title)s has been approved.") % {"article_title": _value(payload, "article_title")}),
        NotificationEvent.SUPPORT_REPLY: (_("New support reply"), _("Support replied to %(ticket_subject)s.") % {"ticket_subject": _value(payload, "ticket_subject")}),
        NotificationEvent.SUPPORT_STATUS_CHANGED: (_("Support ticket updated"), _("Your ticket %(ticket_subject)s is now %(status)s.") % {"ticket_subject": _value(payload, "ticket_subject"), "status": _support_status_label(payload)}),
    }
    title, message = presentations.get(notification.event_type, (_("Notification"), _("There is an update to your account.")))
    technical_values = []
    if "order_number" in payload:
        technical_values.append(order_number)
    if notification.event_type in {NotificationEvent.REVIEW_APPROVED, NotificationEvent.ARTICLE_COMMENT_APPROVED}:
        technical_values.append(_value(payload, "product_name") if notification.event_type == NotificationEvent.REVIEW_APPROVED else _value(payload, "article_title"))
    if notification.event_type in {NotificationEvent.SUPPORT_REPLY, NotificationEvent.SUPPORT_STATUS_CHANGED}:
        technical_values.append(_value(payload, "ticket_subject"))
    event_icons = {
        NotificationEvent.PAYMENT_FAILED: ("credit-card", "danger"),
        NotificationEvent.PAYMENT_CANCELLED: ("credit-card", "warning"),
        NotificationEvent.ORDER_CONFIRMED: ("package-check", "success"),
        NotificationEvent.ORDER_ON_HOLD: ("pause-circle", "warning"),
        NotificationEvent.ORDER_RESUMED: ("play-circle", "info"),
        NotificationEvent.ORDER_SHIPPED: ("truck", "info"),
        NotificationEvent.ORDER_DELIVERED: ("package-check", "success"),
        NotificationEvent.ORDER_CANCELLED: ("package-x", "danger"),
        NotificationEvent.REFUND_APPROVED: ("rotate-ccw", "success"),
        NotificationEvent.REFUND_NEEDS_INFORMATION: ("circle-help", "warning"),
        NotificationEvent.REFUND_REJECTED: ("rotate-ccw", "danger"),
        NotificationEvent.REFUND_PROCESSED: ("badge-check", "success"),
        NotificationEvent.REVIEW_APPROVED: ("star", "success"),
        NotificationEvent.REVIEW_COMMENT_APPROVED: ("message-square-check", "success"),
        NotificationEvent.ARTICLE_COMMENT_APPROVED: ("message-square-check", "success"),
        NotificationEvent.SUPPORT_REPLY: ("message-circle", "info"),
        NotificationEvent.SUPPORT_STATUS_CHANGED: ("headset", "info"),
    }
    icon, tone = event_icons.get(notification.event_type, ("bell", "neutral"))
    if action_label is None:
        action_label = (
            _("Review and pay")
            if notification.event_type in {NotificationEvent.PAYMENT_FAILED, NotificationEvent.PAYMENT_CANCELLED}
            else ""
        )
        if notification.event_type == NotificationEvent.REFUND_NEEDS_INFORMATION:
            action_label = _("Provide details")
    return NotificationPresentation(
        title=title,
        message=message,
        message_html=_message_with_isolated_values(message, technical_values),
        action_label=action_label,
        icon=icon,
        tone=tone,
    )


def _current_action_labels(notifications):
    """Resolve actionable labels in bulk without changing stored destinations."""
    from orders.models import Order
    from support.models import Ticket, TicketStatus

    payment_events = {NotificationEvent.PAYMENT_FAILED, NotificationEvent.PAYMENT_CANCELLED}
    order_ids = {
        item.payload.get("order_id")
        for item in notifications
        if item.event_type in payment_events and isinstance(item.payload, dict)
    }
    order_ids.discard(None)
    orders = Order.objects.in_bulk(order_ids)

    ticket_ids = {
        item.payload.get("ticket_id")
        for item in notifications
        if item.event_type == NotificationEvent.REFUND_NEEDS_INFORMATION and isinstance(item.payload, dict)
    }
    ticket_ids.discard(None)
    tickets = {
        ticket.pk: ticket
        for ticket in Ticket.objects.filter(pk__in=ticket_ids).select_related("refund_order")
    }

    labels = {}
    for item in notifications:
        from .services import get_safe_internal_target

        if not get_safe_internal_target(item.target_url):
            labels[item.pk] = ""
            continue
        if item.event_type in payment_events:
            order_id = item.payload.get("order_id") if isinstance(item.payload, dict) else None
            order = orders.get(order_id)
            if not order or order.user_id != item.user_id:
                labels[item.pk] = ""
            else:
                labels[item.pk] = _("Review and pay") if order.can_start_online_payment else _("View order")
        elif item.event_type == NotificationEvent.REFUND_NEEDS_INFORMATION:
            ticket_id = item.payload.get("ticket_id") if isinstance(item.payload, dict) else None
            ticket = tickets.get(ticket_id)
            refund = getattr(ticket, "refund_order", None) if ticket and ticket.user_id == item.user_id else None
            actionable = (
                refund is not None
                and refund.refund_status == Order.RefundStatus.NEEDS_INFORMATION
                and ticket.status == TicketStatus.WAITING_CUSTOMER
            )
            labels[item.pk] = _("Provide details") if actionable else (_("View ticket") if ticket and ticket.user_id == item.user_id else "")
        elif item.category in {"order", "refund"}:
            labels[item.pk] = _("View order")
        elif item.category == "support":
            labels[item.pk] = _("View ticket")
        elif item.category == "review":
            labels[item.pk] = _("View details")
        else:
            labels[item.pk] = _("View details")
    return labels


def get_notification_displays(notifications, *, resolve_current_actions=False):
    notifications = list(notifications)
    action_labels = _current_action_labels(notifications) if resolve_current_actions else {}
    return [
        NotificationDisplay(
            notification=notification,
            presentation=get_notification_presentation(
                notification,
                action_label=action_labels.get(notification.pk) if resolve_current_actions else None,
            ),
        )
        for notification in notifications
    ]
