# orders/services.py

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.conf import settings
from django.utils import timezone
from django.utils.translation import gettext as _
from TileCompany.uploads import safe_upload_filename
from decimal import Decimal
from dataclasses import dataclass
import uuid

from .models import (
    Order,
    OrderItem,
    OrderStatusHistory,
    OrderNote,
)
from accounts.models import Address
from products.models import ProductVariant
from products.models import VariantPrice
from products.commerce_presentation import quantity_breakdown
from products.purchase_snapshot import prepare_purchase_snapshots
from products.currency import Currency
from .selectors import get_retryable_online_orders_for_user
from .pricing import calculate_pricing
from notifications.models import Notification, NotificationEvent
from notifications.services import create_notification, reverse_notification_target


# ==========================================================
# Inventory
# ==========================================================

@dataclass(frozen=True, slots=True)
class InventoryRestoreResult:
    restored: bool
    already_restored: bool
    total_quantity: int
    unit_quantities: tuple[tuple[str, int], ...] = ()

@transaction.atomic
def reserve_variant_stock(*, variant_id, quantity):
    """Lock and reserve stock from the current variant row."""

    if quantity <= 0:
        raise ValueError("Order quantity must be greater than zero.")

    variant = (
        ProductVariant.objects.select_for_update()
        .select_related("product")
        .filter(pk=variant_id, is_active=True, product__is_active=True)
        .first()
    )
    if variant is None:
        raise ValueError("Selected variant is no longer available.")
    if quantity > variant.stock:
        raise ValueError("Insufficient stock.")

    variant.stock -= quantity
    variant.save(update_fields=["stock"])
    return variant



@transaction.atomic
def restore_stock_for_order(order, *, actor=None, reason="order cancellation"):
    """Return reserved variant inventory exactly once for an order."""

    order = Order.objects.select_for_update().get(pk=order.pk)
    if order.inventory_restored_at:
        return InventoryRestoreResult(False, True, 0)

    total_quantity = 0
    quantities_by_unit = {}
    for item in order.items.exclude(variant__isnull=True):
        variant = ProductVariant.objects.select_for_update().get(pk=item.variant_id)
        variant.stock += item.quantity
        variant.save(update_fields=["stock"])
        total_quantity += item.quantity
        quantities_by_unit[variant.price_unit] = (
            quantities_by_unit.get(variant.price_unit, 0) + item.quantity
        )

    order.inventory_restored_at = timezone.now()
    order.save(update_fields=["inventory_restored_at"])
    OrderNote.objects.create(
        order=order,
        author=actor,
        visibility=OrderNote.Visibility.INTERNAL,
        category=OrderNote.Category.STATUS,
        status=order.status,
        operation_code="INVENTORY_RESTORED",
        message=_("%(quantities)s returned to inventory following %(reason)s.") % {
            "quantities": quantity_breakdown(
                tuple(quantities_by_unit.items()), fallback_quantity=total_quantity
            ),
            "reason": reason,
        },
    )
    return InventoryRestoreResult(
        True, False, total_quantity, tuple(quantities_by_unit.items())
    )


# ==========================================================
# Order Creation
# ==========================================================


class ExistingCheckoutAttemptOrder(Exception):
    """The submitted checkout token already owns its previously created order."""

    def __init__(self, order):
        self.order = order


class RetryableOnlineOrderConflict(Exception):
    """A separate storefront attempt is blocked by an actionable online order."""

    def __init__(self, order):
        self.order = order


class CommercePolicyChanged(Exception):
    """The customer reviewed an older authoritative policy version."""

@transaction.atomic
def create_order(
    *,
    user,
    address,
    cleaned_data,
    checkout,
    checkout_attempt_token=None,
    enforce_single_retryable_online_order=False,
    communication_language="en",
    reviewed_policy_version=None,
):
    """
    Create a pending order and reserve inventory.
    """

    if enforce_single_retryable_online_order:
        # PostgreSQL serializes separate checkout submissions for this customer
        # here. The token replay check must precede the active-order guard so a
        # replay remains a replay rather than a conflict.
        customer = get_user_model().objects.select_for_update().get(pk=user.pk)
        if checkout_attempt_token:
            existing_attempt_order = Order.objects.filter(
                user=customer,
                checkout_attempt_token=checkout_attempt_token,
            ).first()
            if existing_attempt_order is not None:
                raise ExistingCheckoutAttemptOrder(existing_attempt_order)

        if cleaned_data["payment_method"] in (
            Order.PaymentMethod.MOCK,
            Order.PaymentMethod.ZARINPAL,
        ):
            existing_retryable_order = get_retryable_online_orders_for_user(customer).first()
            if existing_retryable_order is not None:
                raise RetryableOnlineOrderConflict(existing_retryable_order)

        user = customer

    receipt_requested = bool(cleaned_data.get("email_receipt_requested", False))
    receipt_email = str(cleaned_data.get("receipt_email", "")).strip()
    if receipt_requested:
        try:
            validate_email(receipt_email)
        except ValidationError as error:
            raise ValueError("A valid receipt email is required when an email receipt is requested.") from error

    next_order_number = (
        Order.objects
        .filter(user=user)
        .count()
        + 1
    )
    validate_checkout(
        address=address,
        checkout=checkout,
    )

    snapshot = copy_address_snapshot(address)

    currency = checkout.currency
    validate_payment_method(cleaned_data["payment_method"], currency)

    requested_variant_ids = {line.variant.pk for line in checkout.items}
    variants = {
        variant.pk: variant
        for variant in (
            ProductVariant.objects.select_for_update(of=("self",))
            .select_related("product", "product__product_type")
            .filter(pk__in=requested_variant_ids, is_active=True, product__is_active=True)
            .order_by("pk")
        )
    }
    prices = {
        price.variant_id: price
        for price in (
            VariantPrice.objects.select_for_update()
            .filter(variant_id__in=requested_variant_ids, currency=currency)
            .order_by("variant_id", "pk")
        )
    }

    authoritative_lines = []
    subtotal = Decimal("0.00")
    for line in checkout.items:
        variant = variants.get(line.variant.pk)
        if variant is None or line.quantity <= 0 or line.quantity > variant.stock:
            available = variant.stock if variant is not None else 0
            sku = variant.sku if variant is not None else line.variant.sku
            raise ValueError(
                _(
                    "Availability changed for %(sku)s. You requested %(requested)s; "
                    "only %(available)s are currently available. Please review your cart."
                ) % {
                    "sku": sku,
                    "requested": line.quantity,
                    "available": available,
                }
            )
        price = prices.get(variant.pk)
        if price is None:
            raise ValueError("A cart item is no longer available in the current currency.")
        line_total = price.amount * line.quantity
        subtotal += line_total
        reference_unit_price = (
            price.compare_at_amount
            if price.compare_at_amount and price.compare_at_amount > price.amount
            else None
        )
        authoritative_lines.append((variant, price.amount, reference_unit_price, line.quantity))

    # Freeze and validate every purchase configuration before creating the
    # Order or decrementing stock. Generic lines are prepared in one bounded
    # schema/value read pass; Tile remains on its legacy snapshot contract.
    purchase_snapshots = prepare_purchase_snapshots(
        variant for variant, _unit_price, _reference_unit_price, _quantity in authoritative_lines
    )

    pricing = calculate_pricing(
        subtotal=subtotal, currency=currency,
        delivery_method=cleaned_data["shipping_method"], for_update=True,
    )
    if reviewed_policy_version is not None and int(reviewed_policy_version) != pricing.policy_version:
        raise CommercePolicyChanged
    shipping_cost = pricing.delivery_amount
    order_total = pricing.total_payable_now
    order = Order.objects.create(

        user=user,

        user_order_number=next_order_number,

        shipping_address=address,

        shipping_method=cleaned_data.get(
            "shipping_method",
            "standard",
        ),
        shipping_cost=shipping_cost,
        shipping_charge_mode=pricing.effective_delivery_mode,
        shipping_free_by_threshold=pricing.delivery_free_by_threshold,

        delivery_slot=cleaned_data.get(
            "delivery_slot",
            "",
        ),

        payment_method=cleaned_data["payment_method"],

        email_receipt_requested=receipt_requested,
        receipt_email=receipt_email,
        communication_language=communication_language.split("-", 1)[0],

        full_name=snapshot["full_name"],

        address=snapshot["address"],

        city=snapshot["city"],

        phone=snapshot["phone"],

        total_amount=order_total,
        tax_rate_snapshot=pricing.tax_rate,
        tax_amount_snapshot=pricing.tax_amount,
        commerce_policy_version=pricing.policy_version,
        currency=currency,
        checkout_attempt_token=checkout_attempt_token or uuid.uuid4(),
    )

    for variant, unit_price, unit_reference_price, quantity in authoritative_lines:
        variant.stock -= quantity
        variant.save(update_fields=["stock"])
        product = variant.product

        OrderItem.objects.create(
            order=order,
            product=product,
            variant=variant,
            product_name=(
                f"{product.name} ({variant.sku})"
            ),
            unit_price=unit_price,
            unit_reference_price=unit_reference_price,
            quantity=quantity,
            variant_snapshot=purchase_snapshots[variant.pk],
        )

    OrderStatusHistory.objects.create(
        order=order,
        status=Order.Status.PENDING,
        note="Order created, awaiting payment.",
    )

    return order

# ==========================================================
# Checkout Helpers
# ==========================================================

def copy_address_snapshot(address):
    return {
        "full_name": (
            f"{address.first_name} {address.last_name}"
        ).strip(),
        "phone": address.phone,
        "address": address.street_address,
        "city": address.city,
        "country": address.country,
        "province": address.province,
        "postal_code": address.postal_code,
        "company_name": address.company_name,
    }


def calculate_shipping(method: str, currency: str, subtotal=Decimal("0.00")) -> Decimal:
    """
    Calculate the shipping cost.

    This can later be replaced with carrier integrations,
    distance calculations, or shipping rules.
    """

    return calculate_pricing(subtotal=subtotal, currency=currency, delivery_method=method).delivery_amount


def get_payment_choices(currency):
    if currency == Currency.TOMAN:
        return [(Order.PaymentMethod.ZARINPAL, "ZarinPal"), (Order.PaymentMethod.COD, _("Cash on delivery"))]
    choices = [(Order.PaymentMethod.COD, _("Cash on delivery"))]
    if settings.LOCAL_PAYMENT_SIMULATOR_ENABLED:
        choices.insert(0, (Order.PaymentMethod.MOCK, _("Simulated online payment")))
    elif settings.MOCK_PAYMENT_SIMULATION_ENABLED:
        choices.insert(0, (Order.PaymentMethod.MOCK, _("Development payment")))
    return choices


def validate_payment_method(payment_method, currency):
    if payment_method not in dict(get_payment_choices(currency)):
        raise ValueError("The selected payment method is not available for this currency.")


def validate_checkout(
    *,
    address,
    checkout,
):
    """
    Basic checkout validation.

    Future versions can validate coupons,
    warehouse inventory,
    delivery restrictions,
    payment availability, etc.
    """

    if address is None:
        raise ValueError("Shipping address is required.")

    if not checkout.items:
        raise ValueError("The cart is empty.")

    for line in checkout.items:
        if not line.variant or line.quantity <= 0:
            raise ValueError("Every checkout item must have a valid variant and quantity.")
# ==========================================================
# Payment
# ==========================================================

@transaction.atomic
def record_payment_recovery_notification(*, order, state):
    """Persist one actionable recovery notification for the current unpaid order state."""
    event_type = {
        "failure": NotificationEvent.PAYMENT_FAILED,
        "cancelled": NotificationEvent.PAYMENT_CANCELLED,
    }.get(state)
    if event_type is None:
        return None

    order = Order.objects.select_for_update().get(pk=order.pk)
    if not order.can_start_online_payment:
        return None

    payload = {
        "order_id": order.id,
        "order_number": order.user_order_number,
        "payment_state": state,
    }
    notification, _created = Notification.objects.get_or_create(
        user=order.user,
        category=Notification.Category.ORDER,
        event_type=event_type,
        payload=payload,
        defaults={
            "target_url": reverse_notification_target(
                "orders:order_detail",
                kwargs={"order_id": order.id},
            ),
        },
    )
    return notification

def detach_created_order_cart(request, *, checkout_attempt_token):
    """Detach the cart after it has been frozen into an order.

    The completed token stays session-bound solely to make a replay of the
    same Place Order POST return that exact order. A new shipping submission
    rotates it before a new checkout starts.
    """
    request.session["cart"] = {}
    request.session.pop("checkout_shipping", None)
    request.session.pop("checkout_payment", None)
    request.session["checkout_attempt_token"] = str(checkout_attempt_token)
    request.session.modified = True


def _record_payment(order, *, payment_reference, provider, operation_code):
    """Record payment facts only; this deliberately never changes fulfillment status."""
    if order.is_paid:
        return False
    order.is_paid = True
    order.payment_reference = payment_reference or ""
    order.payment_provider = provider
    order.paid_at = timezone.now()
    order.save(update_fields=["is_paid", "payment_reference", "payment_provider", "paid_at"])
    OrderNote.objects.create(
        order=order,
        visibility=OrderNote.Visibility.INTERNAL,
        category=OrderNote.Category.PAYMENT,
        status=order.status,
        operation_code=operation_code,
        message=f"Payment recorded via {provider}. Reference: {payment_reference or '-'}",
    )
    return True


@transaction.atomic
def mark_order_paid(order, payment_reference, provider):
    """Record a successful gateway payment and confirm a still-pending online order."""
    order = Order.objects.select_for_update().get(pk=order.pk)
    if order.is_paid:
        from .email_delivery import ensure_order_email_delivery
        from .models import OrderEmailDelivery
        ensure_order_email_delivery(
            order=order,
            kind=OrderEmailDelivery.Kind.ONLINE_PAID_RECEIPT,
        )
        return order
    if not order.can_start_online_payment:
        raise ValueError("Only pending unpaid online-payment orders can be confirmed by a gateway callback.")
    recorded = _record_payment(order, payment_reference=payment_reference, provider=provider, operation_code="ONLINE_PAYMENT_RECORDED")
    if order.status == Order.Status.PENDING:
        order.status = Order.Status.CONFIRMED
        order.save(update_fields=["status"])
        OrderStatusHistory.objects.create(order=order, status=Order.Status.CONFIRMED, note="Online payment confirmed.")
        # The payment note above and this status history are intentionally one each.
    from .email_delivery import ensure_order_email_delivery
    from .models import OrderEmailDelivery
    ensure_order_email_delivery(
        order=order,
        kind=OrderEmailDelivery.Kind.ONLINE_PAID_RECEIPT,
    )
    return order


@transaction.atomic
def confirm_cod_order(order, *, actor=None):
    """Explicitly confirm a reserved COD order while leaving it unpaid."""
    order = Order.objects.select_for_update().get(pk=order.pk)
    if not order.is_cash_on_delivery:
        raise ValueError("Only Cash on Delivery orders can be confirmed this way.")
    if order.status == Order.Status.CONFIRMED:
        from .email_delivery import ensure_order_email_delivery
        from .models import OrderEmailDelivery
        ensure_order_email_delivery(
            order=order,
            kind=OrderEmailDelivery.Kind.COD_CONFIRMATION,
        )
        return order
    if order.status != Order.Status.PENDING:
        raise ValueError("Only pending COD orders can be confirmed.")
    order.status = Order.Status.CONFIRMED
    order.save(update_fields=["status"])
    OrderStatusHistory.objects.create(order=order, status=Order.Status.CONFIRMED, note="COD order confirmed; payment is due on delivery.")
    OrderNote.objects.create(order=order, author=actor, visibility=OrderNote.Visibility.INTERNAL, category=OrderNote.Category.STATUS, status=order.status, operation_code="COD_CONFIRMED", message="Cash on Delivery order confirmed with payment due on delivery.")
    from .email_delivery import ensure_order_email_delivery
    from .models import OrderEmailDelivery
    ensure_order_email_delivery(
        order=order,
        kind=OrderEmailDelivery.Kind.COD_CONFIRMATION,
    )
    return order


@transaction.atomic
def mark_cod_payment_collected(order, *, payment_reference="", actor=None):
    """Record COD collection without regressing shipped or delivered status."""
    order = Order.objects.select_for_update().get(pk=order.pk)
    if not order.is_cash_on_delivery:
        raise ValueError("Only Cash on Delivery orders can record COD collection.")
    if order.is_paid:
        from .email_delivery import ensure_order_email_delivery
        from .models import OrderEmailDelivery
        ensure_order_email_delivery(
            order=order,
            kind=OrderEmailDelivery.Kind.COD_PAID_RECEIPT,
        )
        return order
    if order.status != Order.Status.SHIPPED:
        raise ValueError("Cash on Delivery payment can only be collected for a shipped order.")
    _record_payment(order, payment_reference=payment_reference, provider="Cash on Delivery", operation_code="COD_PAYMENT_COLLECTED")
    from .email_delivery import ensure_order_email_delivery
    from .models import OrderEmailDelivery
    ensure_order_email_delivery(
        order=order,
        kind=OrderEmailDelivery.Kind.COD_PAID_RECEIPT,
    )
    return order


@transaction.atomic
def cancel_unpaid_order(*, order, actor=None, reason="unpaid cancellation", operation_code="UNPAID_ORDER_CANCELLED"):
    """Cancel an eligible unpaid online/COD order and restore inventory once."""
    order = Order.objects.select_for_update().get(pk=order.pk)
    if order.status == Order.Status.CANCELLED:
        return restore_stock_for_order(order, actor=actor, reason=reason)
    if not order.can_cancel:
        return None
    restoration = restore_stock_for_order(order, actor=actor, reason=reason)
    order.status = Order.Status.CANCELLED
    order.save(update_fields=["status"])
    OrderStatusHistory.objects.create(order=order, status=Order.Status.CANCELLED, note=f"Cancelled: {reason}.")
    OrderNote.objects.create(order=order, author=actor, visibility=OrderNote.Visibility.INTERNAL, category=OrderNote.Category.STATUS, status=order.status, operation_code=operation_code, message=f"Unpaid order cancelled: {reason}.")
    if operation_code == "STALE_ONLINE_PAYMENT_CANCELLED":
        create_notification(
            user=order.user,
            category=Notification.Category.ORDER,
            event_type=NotificationEvent.ORDER_CANCELLED,
            payload={"order_number": order.user_order_number},
            target_url=reverse_notification_target("orders:order_detail", kwargs={"order_id": order.id}),
        )
    return restoration


# Backward-compatible import name for callers outside this app.
cancel_pending_order = cancel_unpaid_order
@transaction.atomic
def create_refund_request(
    *,
    order,
    actor,
    cleaned_data,
    attachments=(),
):
    order = Order.objects.select_for_update().get(pk=order.pk)
    if actor.id != order.user_id:
        raise PermissionDenied("This order does not belong to you.")
    if not order.can_request_refund:
        raise ValueError("This order is not eligible for a refund request.")

    message = str(cleaned_data.get("message", "")).strip()
    if not message:
        raise ValueError(_("Additional information is required."))

    order.refund_status = Order.RefundStatus.REQUESTED
    order.refund_reason = cleaned_data["reason"]
    order.refund_message = message

    order.refund_requested_at = timezone.now()
    order.refund_requested_stage = order.status

    order.refund_response = ""
    order.refund_reviewed_by = None
    order.refund_reviewed_at = None

    order.save(
        update_fields=[
            "refund_status",
            "refund_reason",
            "refund_message",
            "refund_requested_at",
            "refund_requested_stage",
            "refund_response",
            "refund_reviewed_by",
            "refund_reviewed_at",
        ]
    )

    from .models import RefundAttachment
    RefundAttachment.objects.bulk_create([
        RefundAttachment(
            order=order,
            file=uploaded_file,
            original_filename=safe_upload_filename(uploaded_file.name),
        )
        for uploaded_file in attachments
    ])

    OrderStatusHistory.objects.create(
        order=order,
        status=Order.Status.CONFIRMED,
        note=f"Refund requested ({order.get_refund_reason_display()}).",
    )

    OrderNote.objects.create(
        order=order,
        author=actor,
        visibility=OrderNote.Visibility.INTERNAL,
        category=OrderNote.Category.REFUND,
        status=order.status,
        operation_code="REFUND_REQUESTED",
        message=(
            f"Customer requested a refund.\n\n"
            f"Reason: {order.get_refund_reason_display()}\n"
            f"Message: {order.refund_message or 'No additional message.'}"
        ),
    )

    return order
