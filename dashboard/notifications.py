"""Derived, permission-filtered operational Dashboard notifications.

These are deliberately not ``notifications.Notification`` rows.  That model is
the customer-facing notification centre.  Dashboard notices are current
operational conditions, generated from the relevant internal queues and shown
only to a Dashboard user who may act on that queue.
"""

from django.db.models import F
from django.urls import reverse
from django.utils.translation import gettext as _, ngettext

from blog.models import Comment
from orders.models import Order
from products.commerce_presentation import quantity_phrase
from products.models import ProductVariant
from reviews.models import Review, ReviewComment
from support.models import Ticket, TicketPriority, TicketStatus
from dashboard.request_cache import get_request_value


def _notice(level, title, message, url, icon):
    return {"level": level, "title": title, "message": message, "url": url, "icon": icon}


def _can_see(user, permission):
    return user is None or user.has_perm(permission)


def get_inventory_alerts(user=None, *, per_state_limit=3, request=None):
    """Return the single inventory-attention projection for Dashboard UI.

    Dashboard Home and the operational feed consume this selector, so their
    active-Variant, low-stock, and out-of-stock rules cannot diverge.
    """
    cache_key = ("inventory_alerts", getattr(user, "pk", None), per_state_limit)
    if request is not None:
        return get_request_value(
            request,
            cache_key,
            lambda: get_inventory_alerts(user, per_state_limit=per_state_limit),
        )
    empty = {"low_stock_count": 0, "out_of_stock_count": 0, "items": []}
    if not _can_see(user, "products.change_inventory"):
        return empty

    base = ProductVariant.objects.filter(
        is_active=True, product__is_active=True,
    ).select_related("product")
    low = base.filter(stock__gt=0, stock__lte=F("low_stock_threshold"))
    out_of_stock = base.filter(stock__lte=0)
    items = []
    for variant in low.order_by("stock", "id")[:per_state_limit]:
        items.append(_notice(
            "warning", _("Low stock"),
            _("%(product)s — %(sku)s has only %(quantity)s remaining.") % {
                "product": variant.product.name, "sku": variant.sku,
                "quantity": quantity_phrase(variant.price_unit, variant.stock),
            },
            reverse("dashboard:products:variants:edit", args=[variant.product_id, variant.id]),
            "chart-no-axes-column-decreasing",
        ))
    for variant in out_of_stock.order_by("id")[:per_state_limit]:
        items.append(_notice(
            "danger", _("Out of stock"),
            _("%(product)s — %(sku)s is out of stock.") % {
                "product": variant.product.name, "sku": variant.sku,
            },
            reverse("dashboard:products:variants:edit", args=[variant.product_id, variant.id]),
            "package-x",
        ))
    return {
        "low_stock_count": low.count(),
        "out_of_stock_count": out_of_stock.count(),
        "items": items,
    }


def get_dashboard_notifications(user=None, *, request=None):
    """Return current Dashboard operational notices visible to ``user``.

    Passing no actor returns the internal condition set without a visibility
    filter.  This is useful to inventory/system callers and keeps generation
    independent of a particular manager account; request rendering always
    supplies the authenticated Dashboard user.
    """
    notices = []
    actor_id = getattr(user, "pk", None)
    if _can_see(user, "support.view_ticket"):
        tickets = Ticket.objects.filter(status__in=(TicketStatus.OPEN, TicketStatus.WAITING_SUPPORT))
        count = get_request_value(request, ("support", "needs_response", actor_id), tickets.count)
        if count:
            notices.append(_notice("warning", _("Tickets needing response"), ngettext("%(count)d support ticket requires a response.", "%(count)d support tickets require a response.", count) % {"count": count}, reverse("dashboard:support:inbox") + "?queue=needs_response", "ticket"))
        urgent = get_request_value(request, ("support", "urgent", actor_id), lambda: tickets.filter(priority=TicketPriority.URGENT).count())
        if urgent:
            notices.append(_notice("danger", _("Urgent support tickets"), ngettext("%(count)d urgent support ticket needs attention.", "%(count)d urgent support tickets need attention.", urgent) % {"count": urgent}, reverse("dashboard:support:inbox") + "?queue=urgent", "ticket"))
    if _can_see(user, "orders.view_order"):
        held = Order.objects.filter(status=Order.Status.ON_HOLD).count()
        ready = Order.objects.filter(status=Order.Status.CONFIRMED, is_paid=True).count()
        if held:
            notices.append(_notice("danger", _("Orders on hold"), ngettext("%(count)d order requires manual attention.", "%(count)d orders require manual attention.", held) % {"count": held}, reverse("dashboard:attention_queue"), "triangle-alert"))
        if ready:
            notices.append(_notice("warning", _("Orders ready to ship"), ngettext("%(count)d paid order is waiting for shipment.", "%(count)d paid orders are waiting for shipment.", ready) % {"count": ready}, reverse("dashboard:fulfillment_queue"), "package-check"))
    if user is None or user.is_superuser:
        refunds = get_request_value(request, ("orders", "refund_review", actor_id), lambda: Order.objects.filter(refund_status__in=[Order.RefundStatus.REQUESTED, Order.RefundStatus.NEEDS_INFORMATION]).count())
        processing = get_request_value(request, ("orders", "refund_processing", actor_id), lambda: Order.objects.filter(refund_status=Order.RefundStatus.APPROVED).count())
        if refunds:
            notices.append(_notice("danger", _("Refund requests waiting"), ngettext("%(count)d refund request requires review.", "%(count)d refund requests require review.", refunds) % {"count": refunds}, reverse("dashboard:refund_queue"), "rotate-ccw"))
        if processing:
            notices.append(_notice("danger", _("Approved refunds awaiting processing"), ngettext("%(count)d approved refund still requires financial processing.", "%(count)d approved refunds still require financial processing.", processing) % {"count": processing}, reverse("dashboard:refund_processing_queue"), "credit-card"))
    blog = _can_see(user, "blog.moderate_article_comments")
    reviews = _can_see(user, "reviews.moderate_reviews")
    if blog or reviews:
        count = (
            get_request_value(request, ("moderation", "blog", actor_id), lambda: Comment.objects.filter(is_approved=False).count()) if blog else 0
        ) + (
            get_request_value(request, ("moderation", "reviews", actor_id), lambda: Review.objects.filter(is_approved=False).count()) if reviews else 0
        ) + (
            get_request_value(request, ("moderation", "review_comments", actor_id), lambda: ReviewComment.objects.filter(is_approved=False).count()) if reviews else 0
        )
        if count:
            notices.append(_notice("warning", _("Content moderation"), ngettext("%(count)d comment or review requires approval.", "%(count)d comments or reviews require approval.", count) % {"count": count}, reverse("dashboard:moderation"), "message-square-text"))
    notices.extend(get_inventory_alerts(user, request=request)["items"])
    return notices
