################### orders/cart.py ###################
from django.shortcuts import render, redirect

from django.contrib import messages
from django.urls import reverse
from decimal import Decimal
from django.http import JsonResponse
from django.utils.translation import get_language, gettext as _



from products.models import ProductVariant
from products.commerce_presentation import (
    prepare_commerce_line_presentations,
    quantity_phrase,
)
from products.presentation import get_localized_value
from products.selectors import get_variant_price, prefetch_variant_prices_for_currency
from products.templatetags.pricing import format_localized_number, format_money
from .currency import get_cart_currency


def _get_cart(request):
    get_cart_currency(request)
    cart = request.session.setdefault("cart", {})  # {"v_<variant_id>": quantity}
    legacy_keys = [key for key in cart if not str(key).startswith("v_")]
    for key in legacy_keys:
        cart.pop(key, None)
    if legacy_keys:
        request.session.modified = True
    return cart
def _build_cart(request):
    """
    Returns a normalized cart structure that can be reused by
    cart_detail, checkout and AJAX responses.
    """

    cart = _get_cart(request)
    currency = get_cart_currency(request)

    variant_ids = []

    for key in cart.keys():
        if str(key).startswith("v_"):
            try:
                variant_ids.append(int(str(key).split("_", 1)[1]))
            except ValueError:
                continue

    variants = ProductVariant.objects.filter(
        id__in=variant_ids,
        is_active=True,
        product__is_active=True,
    ).select_related(
        "product",
        "product__product_type",
        "color",
        "size",
        "finish",
    ).prefetch_related("product__materials")
    variants = prefetch_variant_prices_for_currency(variants, currency)

    variant_map = {
        variant.id: variant
        for variant in variants
    }

    items = []
    total = Decimal("0.00")
    reference_subtotal = Decimal("0.00")
    product_savings = Decimal("0.00")
    cart_quantity = 0

    for key, quantity in cart.items():

        if not str(key).startswith("v_"):
            continue

        try:
            variant_id = int(str(key).split("_", 1)[1])
        except ValueError:
            continue

        variant = variant_map.get(variant_id)

        if not variant:
            continue

        quantity = max(1, int(quantity))

        # Rendering a cart must not rewrite a customer's requested quantity
        # when inventory changes.  The UI exposes current availability and
        # final order creation makes the authoritative locked decision.

        price = get_variant_price(variant, currency)
        if price is None:
            continue
        subtotal = price.amount * quantity
        reference_unit_price = price.compare_at_amount if price.compare_at_amount and price.compare_at_amount > price.amount else None
        line_reference_total = (reference_unit_price or price.amount) * quantity

        total += subtotal
        reference_subtotal += line_reference_total
        product_savings += line_reference_total - subtotal
        cart_quantity += quantity

        items.append({
            "key": key,
            "product": variant.product,
            "variant": variant,
            "quantity": quantity,
            "unit_price": price.amount,
            "unit_reference_price": reference_unit_price,
            "subtotal": subtotal,
        })

    presentations = prepare_commerce_line_presentations(
        items, currency=currency, language=get_language()
    )
    for item in items:
        item["presentation"] = presentations[item["variant"].pk]

    return {
        "items": items,
        "total": total,
        "reference_subtotal": reference_subtotal,
        "product_savings": product_savings,
        "currency": currency,
        "cart_quantity": cart_quantity,
        "breadcrumbs": [
            {
                "label": _("Shopping Cart"),
                "url": None,
            }
        ],
    }

def cart_add_variant(request):
    cart = _get_cart(request)
    currency = get_cart_currency(request)

    try:
        variant_id = int(request.POST.get("variant_id"))
    except (TypeError, ValueError):
        messages.error(request, _("Please select a valid variant."))
        return redirect(request.POST.get("next") or "products:product_list")

    variant = (
        ProductVariant.objects
        .filter(id=variant_id, is_active=True, product__is_active=True)
        .select_related("product", "color", "size", "finish")
        .prefetch_related("prices")
        .first()
    )
    if not variant:
        messages.error(request, _("Selected variant is not available."))
        return redirect(request.POST.get("next") or "products:product_list")

    try:
        quantity = int(request.POST.get("quantity", 1))
    except (TypeError, ValueError):
        quantity = 1
    quantity = max(1, quantity)

    key = f"v_{variant.id}"
    if variant.stock <= 0:
        messages.error(request, _("This variant is out of stock."))
        return redirect(request.POST.get("next") or "products:product_list")
    if get_variant_price(variant, currency) is None:
        messages.error(request, _("This variant is not available in the current currency."))
        return redirect(request.POST.get("next") or "products:product_list")

    new_total = cart.get(key, 0) + quantity
    allowed_total = min(new_total, variant.stock)
    added_quantity = allowed_total - cart.get(key, 0)
    if added_quantity <= 0:
        messages.warning(request, _("The maximum available quantity is already in your cart."))
        return redirect(request.POST.get("next") or "orders:cart_detail")
    cart[key] = allowed_total

    request.session.modified = True
    if added_quantity < quantity:
        messages.warning(request, _("Only %(quantity)s of %(product)s (%(sku)s) was added; that is all currently available.") % {"quantity": quantity_phrase(variant.price_unit, added_quantity), "product": get_localized_value(variant.product, "name"), "sku": variant.sku})
    else:
        messages.success(request, _("Added %(quantity)s of %(product)s (%(sku)s) to your cart.") % {"quantity": quantity_phrase(variant.price_unit, added_quantity), "product": get_localized_value(variant.product, "name"), "sku": variant.sku})
    if request.POST.get("buy_now"):
        return redirect("orders:checkout_shipping")

    next_url = request.POST.get("next") or request.META.get("HTTP_REFERER")
    return redirect(next_url or "orders:cart_detail")

def cart_update(request, cart_key):
    """Update quantity for a cart line identified by cart key, e.g. v_54."""
    cart = _get_cart(request)

    if cart_key not in cart:
        return redirect("orders:cart_detail")

    try:
        quantity = int(request.POST.get("quantity", 1))
    except (TypeError, ValueError):
        quantity = 1

    if quantity < 1:
        cart.pop(cart_key, None)
        request.session.modified = True
        return redirect("orders:cart_detail")

    # Variant-based key: v_<id>
    if cart_key.startswith("v_"):
        try:
            variant_id = int(cart_key.split("_", 1)[1])
        except (ValueError, IndexError):
            cart.pop(cart_key, None)
            request.session.modified = True
            return redirect("orders:cart_detail")

        variant = ProductVariant.objects.filter(
            id=variant_id, is_active=True, product__is_active=True
        ).first()

        if not variant:
            cart.pop(cart_key, None)
        else:
            cart[cart_key] = min(quantity, variant.stock)
    else:
        cart.pop(cart_key, None)

    request.session.modified = True
    cart_data = _build_cart(request)

    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        item = next(
            (i for i in cart_data["items"] if i["key"] == cart_key),
            None
        )

        return JsonResponse({
            "success": True,
            "quantity": item["quantity"] if item else 0,
            "quantity_display": format_localized_number(item["quantity"] if item else 0),
            "quantity_phrase": (
                _("%(quantity)s %(unit)s") % {
                    "quantity": format_localized_number(item["quantity"]),
                    "unit": item["presentation"].quantity_noun,
                }
                if item else ""
            ),
            "subtotal": str(item["subtotal"]) if item else "0.00",
            "subtotal_display": format_money(
                item["subtotal"] if item else Decimal("0.00"),
                cart_data["currency"],
            ),
            "line_savings": str(item["presentation"].line_savings) if item else "0.00",
            "line_savings_display": format_money(item["presentation"].line_savings if item else Decimal("0.00"), cart_data["currency"]),
            "line_savings_message": _("You save %(amount)s") % {"amount": format_money(item["presentation"].line_savings if item else Decimal("0.00"), cart_data["currency"])},
            "reference_subtotal": str(cart_data["reference_subtotal"]),
            "reference_subtotal_display": format_money(cart_data["reference_subtotal"], cart_data["currency"]),
            "product_savings": str(cart_data["product_savings"]),
            "product_savings_display": format_money(cart_data["product_savings"], cart_data["currency"]),
            "product_savings_message": _("You save %(amount)s") % {"amount": format_money(cart_data["product_savings"], cart_data["currency"])},
            "cart_total": str(cart_data["total"]),
            "cart_total_display": format_money(
                cart_data["total"], cart_data["currency"]
            ),
            "cart_quantity": cart_data["cart_quantity"],
            "cart_quantity_display": format_localized_number(
                cart_data["cart_quantity"]
            ),
            "quantity_noun": item["presentation"].quantity_noun if item else "",
            "currency": cart_data["currency"],
        })

    return redirect("orders:cart_detail")


def cart_remove(request, cart_key):
    cart = _get_cart(request)
    if cart_key in cart:
        del cart[cart_key]
        request.session.modified = True
    cart_data = _build_cart(request)

    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return JsonResponse({
            "success": True,
            "cart_total": str(cart_data["total"]),
            "cart_quantity": cart_data["cart_quantity"],
            "currency": cart_data["currency"],
        })

    return redirect("orders:cart_detail")


def cart_detail(request):

    cart = _build_cart(request)

    return render(
        request,
        "orders/cart_detail.html",
        cart,

    )
