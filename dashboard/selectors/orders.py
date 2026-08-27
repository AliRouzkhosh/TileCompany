############ dashboard/selectors/orders.py ################

from django.db.models import Prefetch, Sum

from orders.models import Order, OrderItem
from orders.purchase_presentation import purchase_history_item_queryset
from products.models import Product, ProductView
from django.db.models import Q
from blog.models import (
    Comment,
    Article,
    ArticleStatus,
)

from reviews.models import (
    Review,
    ReviewComment,
)
from support.models import TicketStatus

# ==========================================================
# Filtering
# ==========================================================

def filter_orders(
    queryset,
    search="",
    status="",
    payment="",
):
    """
    Generic dashboard filtering.
    """

    if search:

        queryset = queryset.filter(

            Q(user__username__icontains=search)
            | Q(full_name__icontains=search)
            | Q(phone__icontains=search)
            | Q(payment_reference__icontains=search)
            | Q(user_order_number__icontains=search)

        )

    if status:

        queryset = queryset.filter(
            status=status
        )

    if payment == "paid":

        queryset = queryset.filter(
            is_paid=True
        )

    elif payment == "unpaid":

        queryset = queryset.filter(
            is_paid=False
        )

    return queryset


# ==========================================================
# Base Query
# ==========================================================

def get_orders_queryset(*, include_refund_attachments=False):
    """
    Base queryset used throughout the dashboard.
    """

    related = [
        Prefetch("items", queryset=purchase_history_item_queryset()),
        "status_history",
        "notes",
    ]
    if include_refund_attachments:
        related.append("refund_attachments")

    return (
        Order.objects
        .select_related("user", "refund_support_ticket")
        .prefetch_related(*related)
        .order_by("-created_at")
    )


# ==========================================================
# Queues
# ==========================================================

def get_fulfillment_queue():
    """
    Paid orders waiting to be shipped.
    """

    return (
        get_orders_queryset()
        .filter(
            status=Order.Status.CONFIRMED,
        )
        .filter(
            Q(is_paid=True) | Q(payment_method=Order.PaymentMethod.COD),
        )
        .exclude(refund_status__in=[
            Order.RefundStatus.REQUESTED,
            Order.RefundStatus.NEEDS_INFORMATION,
            Order.RefundStatus.APPROVED,
        ])
    )


def get_in_transit_queue():
    """Orders that have shipped and are awaiting delivery."""

    return get_orders_queryset().filter(
        status=Order.Status.SHIPPED,
    )


def get_refund_review_queue():
    """
    Orders awaiting refund review.
    """

    return get_orders_queryset().filter(
        refund_status__in=[
            Order.RefundStatus.REQUESTED,
            Order.RefundStatus.NEEDS_INFORMATION,
        ]
    )


def get_refund_requested_queue():
    return get_orders_queryset().filter(refund_status=Order.RefundStatus.REQUESTED)


def get_refund_waiting_customer_queue():
    return get_orders_queryset().filter(
        refund_status=Order.RefundStatus.NEEDS_INFORMATION,
        refund_support_ticket__status=TicketStatus.WAITING_CUSTOMER,
    )


def get_refund_customer_replied_queue():
    return get_orders_queryset().filter(
        refund_status=Order.RefundStatus.NEEDS_INFORMATION,
        refund_support_ticket__status=TicketStatus.WAITING_SUPPORT,
    )


def get_refund_processing_queue():
    """Approved refunds that still require the financial action."""

    return get_orders_queryset().filter(
        refund_status=Order.RefundStatus.APPROVED,
    )


def get_refund_queue():
    """Backward-compatible name for the refund-review queue."""

    return get_refund_review_queue()


def get_attention_queue():
    """
    Orders requiring manual intervention.
    """

    return (
        get_orders_queryset()
        .filter(
            Q(status=Order.Status.ON_HOLD)
            |
            Q(
                status=Order.Status.CONFIRMED,
                is_paid=False,
            )
            & ~Q(payment_method=Order.PaymentMethod.COD)
        )
    )


def get_all_orders():
    """
    Complete order list.
    """

    return get_orders_queryset()


# ==========================================================
# Single Order
# ==========================================================

def get_order(order_id, *, include_refund_attachments=False):
    """
    Retrieve one order with all required relations.
    """

    return (
        get_orders_queryset(include_refund_attachments=include_refund_attachments)
        .get(pk=order_id)
    )
# ==========================================================
# Dashboard Statistics
# ==========================================================

def get_dashboard_statistics():
    """
    Summary cards displayed on dashboard home.
    """

    qs = get_orders_queryset()

    return {
        "total_orders": qs.count(),

        "pending_orders": qs.filter(
            status=Order.Status.PENDING
        ).count(),

        "fulfillment_queue": qs.filter(
            status=Order.Status.CONFIRMED,
        ).filter(
            Q(is_paid=True) | Q(payment_method=Order.PaymentMethod.COD),
        ).exclude(refund_status__in=[Order.RefundStatus.REQUESTED, Order.RefundStatus.NEEDS_INFORMATION, Order.RefundStatus.APPROVED]).count(),

        "in_transit_orders": qs.filter(
            status=Order.Status.SHIPPED,
        ).count(),

        "refund_review_queue": qs.filter(
            refund_status__in=[Order.RefundStatus.REQUESTED, Order.RefundStatus.NEEDS_INFORMATION],
        ).count(),
        "refund_waiting_customer_queue": qs.filter(
            refund_status=Order.RefundStatus.NEEDS_INFORMATION,
            refund_support_ticket__status=TicketStatus.WAITING_CUSTOMER,
        ).count(),
        "refund_customer_replied_queue": qs.filter(
            refund_status=Order.RefundStatus.NEEDS_INFORMATION,
            refund_support_ticket__status=TicketStatus.WAITING_SUPPORT,
        ).count(),
        "refund_processing_queue": qs.filter(
            refund_status=Order.RefundStatus.APPROVED,
        ).count(),
        "refund_queue": qs.filter(refund_status__in=[Order.RefundStatus.REQUESTED, Order.RefundStatus.NEEDS_INFORMATION]).count(),

        "cancelled_orders": qs.filter(
            status=Order.Status.CANCELLED
        ).count(),

        "delivered_orders": qs.filter(
            status=Order.Status.DELIVERED
        ).count(),

        "paid_orders": qs.filter(
            is_paid=True
        ).count(),

        "total_revenue_by_currency": list(
            qs.filter(is_paid=True)
            .values("currency")
            .annotate(total=Sum("total_amount"))
            .order_by("currency")
        ),
    }
