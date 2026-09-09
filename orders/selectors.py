# orders/selectors.py

from decimal import Decimal
from dataclasses import dataclass
from typing import Optional

from django.contrib.auth.models import User
from django.core.paginator import Paginator
from django.db.models import Count, Prefetch, Q
from django.utils.translation import get_language, gettext as _

from accounts.models import Address

from products.models import (
    Product,
    ProductVariant,
)
from products.commerce_presentation import (
    CommerceLinePresentationDTO,
    prepare_commerce_line_presentations,
)
from products.selectors import get_variant_price, prefetch_variant_prices_for_currency

from .cart import _get_cart
from .currency import get_cart_currency
from .models import Order
from .pricing import calculate_pricing, load_policy, shipping_methods
from .purchase_presentation import purchase_history_item_queryset


ORDER_HISTORY_REFUND_STATUSES = (
    Order.RefundStatus.REQUESTED,
    Order.RefundStatus.NEEDS_INFORMATION,
    Order.RefundStatus.APPROVED,
    Order.RefundStatus.PROCESSED,
)
ORDER_HISTORY_ACTIVE_STATUSES = (
    Order.Status.PENDING,
    Order.Status.CONFIRMED,
    Order.Status.ON_HOLD,
    Order.Status.SHIPPED,
)
ORDER_HISTORY_REFUND_QUERY = Q(refund_status__in=ORDER_HISTORY_REFUND_STATUSES)
ORDER_HISTORY_FILTERS = {
    # These predicates deliberately partition the customer order history. An
    # active or completed refund takes precedence over the lifecycle bucket.
    "current": Q(status__in=ORDER_HISTORY_ACTIVE_STATUSES) & ~ORDER_HISTORY_REFUND_QUERY,
    "delivered": Q(status=Order.Status.DELIVERED) & ~ORDER_HISTORY_REFUND_QUERY,
    "refund": ORDER_HISTORY_REFUND_QUERY,
    "cancelled": Q(status=Order.Status.CANCELLED) & ~ORDER_HISTORY_REFUND_QUERY,
}
ORDER_HISTORY_PREVIEW_LIMIT = 2
ORDER_HISTORY_PAGE_SIZE = 10


def normalize_order_history_filter(value):
    """Return the supported customer-history filter without trusting a URL."""
    return value if value in ORDER_HISTORY_FILTERS else "current"


def get_customer_order_history(*, user, status_filter=None, page_number=None):
    """Build the bounded, snapshot-backed customer Order-list read model."""
    selected_filter = normalize_order_history_filter(status_filter)
    base_orders = Order.objects.filter(user=user)
    filter_counts = base_orders.aggregate(
        **{key: Count("pk", filter=condition) for key, condition in ORDER_HISTORY_FILTERS.items()}
    )
    order_queryset = (
        base_orders.filter(ORDER_HISTORY_FILTERS[selected_filter])
        .annotate(item_count=Count("items"))
        .prefetch_related(Prefetch("items", queryset=purchase_history_item_queryset().order_by("pk")[:ORDER_HISTORY_PREVIEW_LIMIT], to_attr="order_preview_items"))
        .order_by("-created_at", "-pk")
    )
    page_obj = Paginator(order_queryset, ORDER_HISTORY_PAGE_SIZE).get_page(page_number)
    for order in page_obj.object_list:
        order.preview_extra_count = max(order.item_count - len(order.order_preview_items), 0)
    return {"page_obj": page_obj, "selected_filter": selected_filter, "filter_counts": filter_counts}


def get_customer_order_detail(*, user, order_id):
    """Return the complete, bounded customer-facing order read model.

    Item text is prepared from immutable purchase snapshots; the selected
    Product relation is only available for the optional current-image
    recognition aid.  Nothing in this query follows a product gallery or a
    current VariantPrice.
    """
    from django.shortcuts import get_object_or_404

    return get_object_or_404(
        Order.objects.select_related("refund_support_ticket").prefetch_related(
            "status_history",
            "refund_attachments",
            Prefetch("items", queryset=purchase_history_item_queryset().order_by("pk")),
        ),
        id=order_id,
        user=user,
    )


def get_retryable_online_orders_for_user(user):
    """Return the current database equivalent of ``can_start_online_payment``.

    This is deliberately scoped to online providers: a confirmed COD order is
    unpaid by design and must never participate in payment recovery or the
    storefront creation guard.
    """
    return Order.objects.filter(
        user=user,
        payment_method__in=(Order.PaymentMethod.MOCK, Order.PaymentMethod.ZARINPAL),
        status=Order.Status.PENDING,
        is_paid=False,
    ).order_by("created_at", "pk")


# ==========================================================
# Data Objects
# ==========================================================
@dataclass(slots=True)
class CheckoutItem:
    """
    Represents one checkout line.
    """

    key: str
    product: Product
    variant: ProductVariant

    quantity: int

    unit_price: Decimal
    subtotal: Decimal
    unit_reference_price: Decimal | None = None
    presentation: Optional[CommerceLinePresentationDTO] = None



@dataclass(slots=True)
class CheckoutSummary:
    currency: str
    subtotal: Decimal
    shipping: Decimal
    discount: Decimal
    tax: Decimal
    total: Decimal
    reference_subtotal: Decimal = Decimal("0.00")
    product_savings: Decimal = Decimal("0.00")
    delivery_is_free: bool = False
    delivery_free_by_threshold: bool = False
    delivery_pay_on_delivery: bool = False
    effective_delivery_mode: str | None = None
    tax_rate: Decimal = Decimal("0.00")

@dataclass(slots=True)
class ShippingMethod:
    code: str
    name: str
    amount: Decimal
    estimated_days: str
    description: str

    @property
    def price(self):
        """Template compatibility alias; amounts remain currency-bound."""
        return self.amount

@dataclass(slots=True)
class CheckoutContext:
    """
    Everything required by the checkout page.
    """

    items: list[CheckoutItem]

    summary: CheckoutSummary

    cart_quantity: int

    default_address: Optional[Address]

    addresses: list[Address]

    shipping_methods: list[ShippingMethod]
    currency: str
    unavailable_variant_ids: list[int]
    policy_version: int = 1




# ==========================================================
# Cart
# ==========================================================

def get_checkout_context(request, *, include_addresses=True):
    """
    Build a normalized checkout cart.

    Returns:
        {
            items,
            summary,
            cart_quantity,
        }
    """

    cart = _get_cart(request)
    currency = get_cart_currency(request)

    variant_ids = []

    for key in cart.keys():

        key = str(key)

        if key.startswith("v_"):

            raw = key[2:]

            if raw.isdigit():
                variant_ids.append(int(raw))


    variants = (
        ProductVariant.objects
        .filter(
            id__in=variant_ids,
            is_active=True,
            product__is_active=True,
        )
        .select_related("product", "product__product_type", "color", "size", "finish")
        .prefetch_related("product__materials")
    )
    variants = prefetch_variant_prices_for_currency(variants, currency)

    variant_map = {
        variant.id: variant
        for variant in variants
    }

    items = []
    unavailable_variant_ids = []

    subtotal = Decimal("0.00")
    reference_subtotal = Decimal("0.00")

    quantity = 0

    for key, qty in cart.items():

        try:
            qty = int(qty)
        except (TypeError, ValueError):
            continue

        if qty < 1:
            continue

        key = str(key)

        # -------------------------------------
        # Variant
        # -------------------------------------

        if key.startswith("v_"):

            raw = key[2:]

            if not raw.isdigit():
                continue

            variant = variant_map.get(int(raw))

            if not variant:
                continue

            # A checkout summary represents the customer's requested purchase
            # quantity.  Do not silently clamp it to a later stock value here:
            # create_order() locks the row and either reserves this exact
            # quantity or rolls the whole transaction back.

            price = get_variant_price(variant, currency)
            if price is None:
                unavailable_variant_ids.append(variant.pk)
                continue
            line_total = price.amount * qty
            reference_unit_price = price.compare_at_amount if price.compare_at_amount and price.compare_at_amount > price.amount else None

            subtotal += line_total
            reference_subtotal += (reference_unit_price or price.amount) * qty

            quantity += qty

            items.append(
                CheckoutItem(
                    key=key,
                    product=variant.product,
                    variant=variant,
                    quantity=qty,
                    unit_price=price.amount,
                    subtotal=line_total,
                    unit_reference_price=reference_unit_price,
                    
                )
            )
    presentations = prepare_commerce_line_presentations(
        items, currency=currency, language=get_language()
    )
    for item in items:
        item.presentation = presentations[item.variant.pk]
    selected_code = (
        request.session
        .get("checkout_shipping", {})
        .get("shipping_method")
    )

    policy_bundle = load_policy()
    methods = shipping_methods(subtotal=subtotal, currency=currency, policy_bundle=policy_bundle)
    pricing = calculate_pricing(
        subtotal=subtotal,
        currency=currency,
        delivery_method=selected_code,
        policy_bundle=policy_bundle,
    )

    summary = CheckoutSummary(
        currency=currency,
        subtotal=subtotal,
        shipping=pricing.delivery_amount,
        discount=Decimal("0.00"),
        tax=pricing.tax_amount,
        total=pricing.total_payable_now,
        reference_subtotal=reference_subtotal,
        product_savings=reference_subtotal - subtotal,
        delivery_is_free=pricing.delivery_is_free,
        delivery_free_by_threshold=pricing.delivery_free_by_threshold,
        delivery_pay_on_delivery=pricing.delivery_pay_on_delivery,
        effective_delivery_mode=pricing.effective_delivery_mode,
        tax_rate=pricing.tax_rate,
    )

    addresses = list(get_customer_addresses(request.user)) if include_addresses else []
    default_address = next((address for address in addresses if address.is_default_shipping), None)

    return CheckoutContext(
        items=items,
        summary=summary,
        cart_quantity=quantity,
        default_address=default_address,
        addresses=addresses,
        shipping_methods=methods,
        currency=currency,
        unavailable_variant_ids=unavailable_variant_ids,
        policy_version=pricing.policy_version,
    )


# ==========================================================
# Customer
# ==========================================================
def get_shipping_methods(currency, subtotal=Decimal("0.00")):
    """
    Available shipping methods.

    Later this can come from the database.
    """

    return shipping_methods(subtotal=subtotal, currency=currency)


def get_default_shipping_address(user: User):
    """
    Return the customer's default shipping address.
    """

    return (
        Address.objects
        .filter(
            user=user,
            is_default_shipping=True,
        )
        .first()
    )


def get_customer_addresses(user: User):
    """
    Return all customer addresses.
    """

    return (
        Address.objects
        .filter(user=user)
        .order_by(
            "-is_default_shipping",
            "-updated_at",
        )
    )



