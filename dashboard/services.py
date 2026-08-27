############### dashboard/services.py ################

from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext as _
from dataclasses import dataclass

from orders.models import (
    Order,
    OrderStatusHistory,
    OrderNote,
)
from blog.models import Comment
from reviews.models import Review, ReviewComment
from orders.services import restore_stock_for_order, cancel_unpaid_order as cancel_eligible_unpaid_order, mark_cod_payment_collected
from products.models import Product, ProductImage, ProductVariant, VariantImage
from products.services import set_variant_price
from products.catalog_compat import (
    lock_and_validate_tile_product,
    lock_and_validate_tile_variant,
    sync_tile_product_catalog,
    sync_tile_variant_catalog,
)
from notifications.models import Notification, NotificationEvent
from notifications.services import create_notification, create_notification_once, reverse_notification_target
from TileCompany.uploads import validate_raster_image

# ==========================================================
# Internal Helpers
# ==========================================================

def _notify_customer_about_order(order, *, category, event_type):
    """Persist a customer notification once an order transition has succeeded."""
    create_notification(
        user=order.user,
        category=category,
        event_type=event_type,
        payload={"order_number": order.user_order_number},
        target_url=reverse_notification_target("orders:order_detail", kwargs={"order_id": order.id}),
    )

def log_status_change(order, status, note=""):
    """
    Create an order status history record.
    """

    OrderStatusHistory.objects.create(
        order=order,
        status=status,
        note=note,
    )
def create_order_note(
    order,
    *,
    author=None,
    visibility=OrderNote.Visibility.INTERNAL,
    category=OrderNote.Category.STATUS,
    operation_code="",
    message="",
):
    """
    Create an operational note for an order.
    """

    OrderNote.objects.create(
        order=order,
        author=author,
        visibility=visibility,
        category=category,
        status=order.status,
        operation_code=operation_code,
        message=message,
    )
def update_order_status(
    order,
    status,
    note="",
    *,
    author=None,
    visibility=OrderNote.Visibility.INTERNAL,
    category=OrderNote.Category.STATUS,
    operation_code="STATUS_CHANGED",
    extra_update_fields=(),
):
    """
    Update an order status, record it in history,
    and create an operational note.
    """

    order.status = status

    order.save(update_fields=["status", *extra_update_fields])

    log_status_change(
        order,
        status,
        note,
    )

    create_order_note(
        order,
        author=author,
        visibility=visibility,
        category=category,
        operation_code=operation_code,
        message=note,
    )
# ==========================================================
# Refund Workflow
# ==========================================================

@dataclass(frozen=True, slots=True)
class RefundActionResult:
    success: bool
    inventory_restored: bool = False
    inventory_already_restored: bool = False
    restored_quantity: int = 0
    restored_unit_quantities: tuple[tuple[str, int], ...] = ()
    fulfillment_cancelled: bool = False

    def __bool__(self):
        return self.success


def _is_pre_shipment_refund(order):
    return order.refund_requested_stage in {
        Order.Status.CONFIRMED,
        Order.Status.ON_HOLD,
    }


def _refund_support_category():
    """Return the explicit category used for system-created refund conversations."""
    from support.models import TicketCategory
    from support.taxonomy import REFUND_CATEGORY_SLUG

    category, _created = TicketCategory.objects.get_or_create(
        slug=REFUND_CATEGORY_SLUG,
        defaults={
            "name": "Returns & Refunds",
            "name_fa": "مرجوعی و بازپرداخت",
            "description": "Help with refund requests and the information needed to review them.",
            "description_fa": "راهنمای درخواست بازپرداخت و اطلاعات لازم برای بررسی آن.",
            "icon": "rotate-ccw",
        },
    )
    return category


@transaction.atomic
def request_refund_information(order, staff_user, refund_response=""):
    """Pause a refund review until the customer replies in its linked ticket."""
    from support.models import Ticket, TicketStatus
    from support.services import create_ticket, create_ticket_reply, reopen_ticket

    question = refund_response.strip()
    if not question:
        raise ValueError("A customer-safe clarification message is required.")

    order = (
        Order.objects.select_for_update(of=("self",))
        .select_related("user", "refund_support_ticket")
        .get(pk=order.pk)
    )
    if not order.is_refund_under_review:
        return RefundActionResult(False)

    ticket = order.refund_support_ticket
    if ticket is not None:
        ticket = Ticket.objects.select_for_update().get(pk=ticket.pk)
        if ticket.user_id != order.user_id or ticket.order_id != order.id:
            raise ValueError("The linked refund ticket does not belong to this order.")
        if ticket.status in {TicketStatus.RESOLVED, TicketStatus.CLOSED}:
            reopen_ticket(ticket=ticket, actor=staff_user)
    else:
        initial_message = "\n\n".join(
            [
                str(_("Refund request")),
                f"{_('Reason:')}\n{order.get_refund_reason_display()}",
                f"{_('Additional information:')}\n{order.refund_message}",
            ]
        )
        ticket = create_ticket(
            user=order.user,
            category=_refund_support_category(),
            subject=_("Refund request for order #%(number)s") % {"number": order.user_order_number},
            message=initial_message,
            order=order,
        )
        order.refund_support_ticket = ticket

    reply = create_ticket_reply(
        ticket=ticket,
        author=staff_user,
        message=question,
        is_staff_reply=True,
        notify_owner=False,
    )
    order.refund_response = question
    order.refund_status = Order.RefundStatus.NEEDS_INFORMATION
    order.refund_reviewed_by = staff_user
    order.refund_reviewed_at = timezone.now()
    order.save(
        update_fields=[
            "refund_support_ticket",
            "refund_response",
            "refund_status",
            "refund_reviewed_by",
            "refund_reviewed_at",
        ]
    )
    log_status_change(order, order.status, f"Refund clarification requested by {staff_user.username}.")
    create_order_note(
        order,
        author=staff_user,
        category=OrderNote.Category.REFUND,
        operation_code="REFUND_INFORMATION_REQUESTED",
        message="Refund clarification requested through linked support ticket.",
    )
    create_notification_once(
        user=order.user,
        category=Notification.Category.REFUND,
        event_type=NotificationEvent.REFUND_NEEDS_INFORMATION,
        payload={
            "order_number": order.user_order_number,
            "ticket_id": ticket.id,
            "ticket_reply_id": reply.id,
        },
        target_url=reverse_notification_target(
            "support:ticket_detail",
            kwargs={"ticket_id": ticket.id},
        ),
    )
    return RefundActionResult(True)

@transaction.atomic
def approve_refund(order, staff_user, refund_response=""):
    """
    Approve a refund request.

    Money has NOT been refunded yet.
    """

    order = Order.objects.select_for_update().get(pk=order.pk)
    if not order.is_refund_under_review:
        return RefundActionResult(False)

    order.refund_response = refund_response
    order.refund_status = Order.RefundStatus.APPROVED
    order.refund_reviewed_by = staff_user
    order.refund_reviewed_at = timezone.now()

    order.save(
        update_fields=[
            "refund_response",
            "refund_status",
            "refund_reviewed_by",
            "refund_reviewed_at",
        ]
    )

    restoration = None
    fulfillment_cancelled = False
    if _is_pre_shipment_refund(order):
        restoration = restore_stock_for_order(
            order,
            actor=staff_user,
            reason="pre-shipment refund approval",
        )
        update_order_status(
            order,
            Order.Status.CANCELLED,
            f"Refund approved by {staff_user.username}; fulfillment cancelled.",
            author=staff_user,
            category=OrderNote.Category.REFUND,
            operation_code="REFUND_APPROVED_CANCELLED",
        )
        fulfillment_cancelled = True
    else:
        log_status_change(
            order,
            order.status,
            f"Refund approved by {staff_user.username}. Awaiting processing.",
        )

    create_order_note(
        order,
        author=staff_user,
        category=OrderNote.Category.REFUND,
        operation_code="REFUND_APPROVED",
        message=(
            f"Refund approved by {staff_user.username}."
            f"\n\nResponse:\n{refund_response}"
        )
    )

    _notify_customer_about_order(
        order,
        category=Notification.Category.REFUND,
        event_type=NotificationEvent.REFUND_APPROVED,
    )

    return RefundActionResult(
        True,
        inventory_restored=restoration.restored if restoration else False,
        inventory_already_restored=restoration.already_restored if restoration else False,
        restored_quantity=restoration.total_quantity if restoration else 0,
        restored_unit_quantities=restoration.unit_quantities if restoration else (),
        fulfillment_cancelled=fulfillment_cancelled,
    )


@transaction.atomic
def reject_refund(order, staff_user, refund_response=""):
    """
    Reject a refund request.
    """

    order = Order.objects.select_for_update().get(pk=order.pk)
    if not order.is_refund_under_review:
        return RefundActionResult(False)

    order.refund_response = refund_response
    order.refund_status = Order.RefundStatus.REJECTED
    order.refund_reviewed_by = staff_user
    order.refund_reviewed_at = timezone.now()

    order.save(
        update_fields=[
            "refund_response",
            "refund_status",
            "refund_reviewed_by",
            "refund_reviewed_at",
        ]
    )

    log_status_change(
        order,
        order.status,
        f"Refund rejected by {staff_user.username}.",
    )
    create_order_note(
        order,
        author=staff_user,
        category=OrderNote.Category.REFUND,
        operation_code="REFUND_REJECTED",
        message=(
            f"Refund rejected by {staff_user.username}."
            f"\n\nResponse:\n{refund_response}"
        )
    )

    _notify_customer_about_order(
        order,
        category=Notification.Category.REFUND,
        event_type=NotificationEvent.REFUND_REJECTED,
    )

    return RefundActionResult(True)

@transaction.atomic
def process_refund(order, staff_user):
    """
    Mark an approved refund as processed.

    This is the point where the payment has actually
    been returned to the customer.
    """

    order = Order.objects.select_for_update().get(pk=order.pk)
    if not order.is_refund_approved:
        return RefundActionResult(False)

    restoration = None
    if order.refund_requested_stage == Order.Status.DELIVERED:
        restoration = restore_stock_for_order(
            order,
            actor=staff_user,
            reason="delivered refund processing",
        )

    order.refund_status = Order.RefundStatus.PROCESSED
    order.refund_processed_at = timezone.now()

    order.save(
        update_fields=[
            "refund_status",
            "refund_processed_at",
        ]
    )

    create_order_note(
        order,
        author=staff_user,
        category=OrderNote.Category.REFUND,
        operation_code="REFUND_PROCESSED",
        message=f"Refund processed by {staff_user.username}.",
    )

    _notify_customer_about_order(
        order,
        category=Notification.Category.REFUND,
        event_type=NotificationEvent.REFUND_PROCESSED,
    )

    return RefundActionResult(
        True,
        inventory_restored=restoration.restored if restoration else False,
        inventory_already_restored=(
            restoration.already_restored if restoration else bool(order.inventory_restored_at)
        ),
        restored_quantity=restoration.total_quantity if restoration else 0,
        restored_unit_quantities=restoration.unit_quantities if restoration else (),
    )
# ==========================================================
# Shipping Workflow
# ==========================================================

@transaction.atomic
def mark_as_shipped(order, staff_user):
    """
    Mark an order as shipped.
    """

    if not order.can_ship:
        return False

    update_order_status(
        order,
        Order.Status.SHIPPED,
        f"Marked as shipped by {staff_user.username}.",
        author=staff_user,
        category=OrderNote.Category.SHIPPING,
        operation_code="SHIP_ORDER",
    )

    _notify_customer_about_order(
        order,
        category=Notification.Category.ORDER,
        event_type=NotificationEvent.ORDER_SHIPPED,
    )

    return True
@transaction.atomic
def hold_order(order, staff_user):
    """
    Place a fulfillment-ready confirmed order on hold.
    """

    if not order.can_hold:
        return False

    update_order_status(
        order,
        Order.Status.ON_HOLD,
        f"Order placed on hold by {staff_user.username}.",
        author=staff_user,
        category=OrderNote.Category.STATUS,
        operation_code="ORDER_ON_HOLD",
    )

    _notify_customer_about_order(
        order,
        category=Notification.Category.ORDER,
        event_type=NotificationEvent.ORDER_ON_HOLD,
    )

    return True


@transaction.atomic
def resume_order(order, staff_user):
    """
    Resume an order that is currently on hold.
    """

    if order.status != Order.Status.ON_HOLD:
        return False

    update_order_status(
        order,
        Order.Status.CONFIRMED,
        f"Order resumed by {staff_user.username}.",
        author=staff_user,
        category=OrderNote.Category.STATUS,
        operation_code="ORDER_RESUMED",
    )

    _notify_customer_about_order(
        order,
        category=Notification.Category.ORDER,
        event_type=NotificationEvent.ORDER_RESUMED,
    )

    return True

@transaction.atomic
def mark_as_delivered(order, staff_user):
    """
    Mark an order as delivered.
    """

    if order.status != Order.Status.SHIPPED:
        return False

    # Delivery is the operational collection point for COD.  Keeping this in
    # the same transaction avoids a delivered order being left unintentionally
    # unpaid when staff uses the normal delivery action.
    if order.is_cash_on_delivery and not order.is_paid:
        mark_cod_payment_collected(order, actor=staff_user)

    order.delivered_at = timezone.now()
    update_order_status(
        order,
        Order.Status.DELIVERED,
        f"Marked as delivered by {staff_user.username}.",
        author=staff_user,
        category=OrderNote.Category.SHIPPING,
        operation_code="DELIVER_ORDER",
        extra_update_fields=("delivered_at",),
    )

    _notify_customer_about_order(
        order,
        category=Notification.Category.ORDER,
        event_type=NotificationEvent.ORDER_DELIVERED,
    )

    return True


# ==========================================================
# Cancellation
# ==========================================================

@transaction.atomic
def cancel_unpaid_order(order, staff_user=None):
    """
    Cancel an eligible unpaid order (pending online or confirmed COD).
    """

    order = Order.objects.select_for_update().get(pk=order.pk)
    was_cancelled = order.status == Order.Status.CANCELLED
    result = cancel_eligible_unpaid_order(
        order=order,
        actor=staff_user,
        reason="unpaid order cancellation",
        operation_code="CANCEL_ORDER",
    )
    if result and not was_cancelled:
        _notify_customer_about_order(
            order,
            category=Notification.Category.ORDER,
            event_type=NotificationEvent.ORDER_CANCELLED,
        )
    return result

def can_execute(order, action):
    """
    Determine whether an action is currently valid for an order.
    """

    rules = {
        "ship": order.can_ship,
        "hold": order.can_hold,

        "resume": (
                order.status == Order.Status.ON_HOLD
        ),

        "deliver": (
            order.status == Order.Status.SHIPPED
        ),

        "approve_refund": (
            order.is_refund_under_review
        ),

        "reject_refund": (
            order.is_refund_under_review
        ),
        "request_refund_information": order.is_refund_under_review,
        "process_refund": (
            order.refund_status == Order.RefundStatus.APPROVED
        ),

        "cancel": order.can_cancel,
    }

    return rules.get(action, False)

# ==========================================================
# Moderation
# ==========================================================
@transaction.atomic
def approve_blog_comment(comment,moderator):

    if comment.parent_id and not comment.parent.is_approved:
        return False

    was_approved = comment.is_approved
    comment.is_approved = True

    comment.save(
        update_fields=[
            "is_approved",
        ]
    )

    if not was_approved:
        create_notification(
            user=comment.user,
            category=Notification.Category.CONTENT,
            event_type=NotificationEvent.ARTICLE_COMMENT_APPROVED,
            payload={"article_title": comment.article.title},
            target_url=reverse_notification_target(
                "blog:article_detail",
                kwargs={"slug": comment.article.slug},
            ),
        )

    return True


def reject_blog_comment(comment, moderator):

    comment.delete()

    return True


@transaction.atomic
def approve_review(review, moderator):
    was_approved = review.is_approved

    review.is_approved = True

    review.save(
        update_fields=[
            "is_approved",
        ]
    )

    if not was_approved:
        create_notification(
            user=review.user,
            category=Notification.Category.REVIEW,
            event_type=NotificationEvent.REVIEW_APPROVED,
            payload={"product_name": review.product.name},
            target_url=reverse_notification_target(
                "products:product_detail",
                kwargs={"slug": review.product.slug},
            ),
        )

    return True


def reject_review(review, moderator):

    review.delete()

    return True


@transaction.atomic
def approve_review_comment(comment, moderator):

    if comment.parent_id and not comment.parent.is_approved:
        return False

    was_approved = comment.is_approved
    comment.is_approved = True

    comment.save(
        update_fields=[
            "is_approved",
        ]
    )

    if not was_approved:
        create_notification(
            user=comment.user,
            category=Notification.Category.REVIEW,
            event_type=NotificationEvent.REVIEW_COMMENT_APPROVED,
            target_url=reverse_notification_target(
                "products:product_detail",
                kwargs={"slug": comment.review.product.slug},
            ),
    )

    return True


def reject_review_comment(comment, moderator):

    comment.delete()

    return True

# ==========================================================
# Product Management
# ==========================================================

@transaction.atomic
def create_product(form, *, user=None):
    """
    Create a new product from the dashboard form.
    """

    product = form.save()
    return sync_tile_product_catalog(product)


@transaction.atomic
def update_product(product, form, *, user=None):
    """
    Persist dashboard product changes.
    """

    lock_and_validate_tile_product(product.pk)
    product = form.save()
    return sync_tile_product_catalog(product, legacy_write_authorized=True)


@transaction.atomic
def create_product_variant(*, product, form, user=None, write_prices=True):
    """Create a variant owned by the supplied product."""

    variant = form.save(commit=False)
    variant.product = product
    variant.save()
    variant = sync_tile_variant_catalog(variant)
    if write_prices:
        _save_variant_prices(variant=variant, form=form)
    return variant


@transaction.atomic
def update_product_variant(*, variant, form, user=None, write_prices=True):
    """Persist changes to an existing product variant."""

    lock_and_validate_tile_variant(variant.pk)
    from products.inventory_price_imports import record_product_operational_change, snapshot_variant_inventory_prices
    from products.models import ProductOperationalChange
    persisted_before = ProductVariant.objects.select_related("product").prefetch_related("prices").get(pk=variant.pk)
    before = snapshot_variant_inventory_prices(persisted_before)
    variant = form.save()
    variant = sync_tile_variant_catalog(variant, legacy_write_authorized=True)
    if write_prices:
        _save_variant_prices(variant=variant, form=form)
    if user is not None:
        variant = ProductVariant.objects.select_related("product").prefetch_related("prices").get(pk=variant.pk)
        record_product_operational_change(actor=user, variant=variant, source=ProductOperationalChange.Source.MANUAL, before=before, after=snapshot_variant_inventory_prices(variant))
    return variant


def _save_variant_prices(*, variant, form):
    for currency, amount, compare_at_amount in form.currency_price_data():
        if amount is None:
            variant.prices.filter(currency=currency).delete()
        else:
            set_variant_price(
                variant=variant,
                currency=currency,
                amount=amount,
                compare_at_amount=compare_at_amount,
            )


@transaction.atomic
def add_product_gallery_images(*, product, files):
    files = [validate_raster_image(image) for image in files]
    start = product.gallery.count()
    return [ProductImage.objects.create(product=product, image=image, sort_order=start + index) for index, image in enumerate(files)]


@transaction.atomic
def add_variant_gallery_images(*, variant, files):
    files = [validate_raster_image(image) for image in files]
    start = variant.gallery.count()
    return [VariantImage.objects.create(variant=variant, image=image, sort_order=start + index) for index, image in enumerate(files)]
