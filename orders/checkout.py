################ orders/checkout.py #################
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
from django.urls import reverse
from django.shortcuts import get_object_or_404
from django.utils.translation import gettext as _
from django.db import IntegrityError, transaction
from django.utils.translation import get_language
from uuid import UUID, uuid4

from .forms import (
    ShippingForm,
    PaymentMethodForm,
)
from .selectors import (
    get_checkout_context,
)
from .services import (
    ExistingCheckoutAttemptOrder,
    RetryableOnlineOrderConflict,
    confirm_cod_order,
    create_order,
    detach_created_order_cart,
    get_payment_choices,
)
from .models import Order
from accounts.models import Address


CHECKOUT_ATTEMPT_TOKEN_SESSION_KEY = "checkout_attempt_token"


def _start_checkout_attempt(request):
    token = uuid4()
    request.session[CHECKOUT_ATTEMPT_TOKEN_SESSION_KEY] = str(token)
    request.session.modified = True
    return token


def _get_checkout_attempt_token(request):
    raw_token = request.session.get(CHECKOUT_ATTEMPT_TOKEN_SESSION_KEY)
    try:
        return UUID(str(raw_token))
    except (TypeError, ValueError):
        return None


def _redirect_for_created_order(order):
    if order.requires_online_payment and not order.is_paid:
        return redirect("orders:payment_start", order_id=order.id)
    return redirect("orders:order_result", order_id=order.id)


# ==========================================================
# Shipping
# ==========================================================

@login_required
def shipping(request):

    checkout = get_checkout_context(request)

    if checkout.unavailable_variant_ids:
        messages.error(request, _("One or more cart items are no longer available in the current currency."))
        return redirect("orders:cart_detail")

    if not checkout.items:
        messages.error(request, _("Your cart is empty."))
        return redirect("orders:cart_detail")

    shipping_choices = [
        (method.code, method.name)
        for method in checkout.shipping_methods
    ]

    if request.method == "POST":

        form = ShippingForm(
            request.POST,
            shipping_choices=shipping_choices,
        )

        if form.is_valid():

            request.session["checkout_shipping"] = form.cleaned_data
            _start_checkout_attempt(request)

            return redirect("orders:checkout_payment")

    else:
        initial = dict(request.session.get("checkout_shipping", {}))

        if "address_id" not in initial and checkout.default_address:
            initial["address_id"] = checkout.default_address.id

        if "shipping_method" not in initial and checkout.shipping_methods:
            initial["shipping_method"] = (
                checkout.shipping_methods[0].code
            )

        form = ShippingForm(
            initial=initial,
            shipping_choices=shipping_choices,
        )

    return render(
        request,
        "orders/shipping.html",
        {
            "form": form,
            "checkout": checkout,
            "checkout_step": "shipping",
            "breadcrumbs": [
                {
                    "label": _("Cart"),
                    "url": reverse("orders:cart_detail"),
                },
                {
                    "label": _("Shipping"),
                    "url": None,
                },
            ],
        },
    )


# ==========================================================
# Payment
# ==========================================================

@login_required
def payment(request):

    checkout = get_checkout_context(request)

    if checkout.unavailable_variant_ids:
        messages.error(request, _("One or more cart items are no longer available in the current currency."))
        return redirect("orders:cart_detail")

    if not checkout.items:
        return redirect("orders:cart_detail")

    if "checkout_shipping" not in request.session:
        return redirect("orders:checkout_shipping")

    if request.method == "POST":

        form = PaymentMethodForm(
            request.POST,
            payment_choices=get_payment_choices(checkout.currency),
            customer=request.user,
        )

        if form.is_valid():

            request.session["checkout_payment"] = form.cleaned_data

            return redirect("orders:checkout_review")

    else:
        form = PaymentMethodForm(
            initial=request.session.get("checkout_payment", {}),
            payment_choices=get_payment_choices(checkout.currency),
            customer=request.user,
        )

    return render(
        request,
        "orders/payment.html",
        {
            "form": form,
            "checkout": checkout,
            "checkout_step": "payment",
        },
    )


# ==========================================================
# Review
# ==========================================================

@login_required
def review(request):

    if request.method == "POST":
        session_token = _get_checkout_attempt_token(request)
        submitted_token = request.POST.get("checkout_attempt_token")
        if session_token is None or submitted_token != str(session_token):
            messages.error(request, _("This checkout attempt is no longer valid. Please review your order again."))
            return redirect("orders:checkout_shipping")

        existing = Order.objects.filter(
            user=request.user,
            checkout_attempt_token=session_token,
        ).first()
        if existing:
            return _redirect_for_created_order(existing)

    checkout = get_checkout_context(request)

    if checkout.unavailable_variant_ids:
        messages.error(request, _("One or more cart items are no longer available in the current currency."))
        return redirect("orders:cart_detail")

    if not checkout.items:
        return redirect("orders:cart_detail")

    if (
        "checkout_shipping" not in request.session
        or "checkout_payment" not in request.session
    ):
        return redirect("orders:checkout_shipping")

    shipping = request.session["checkout_shipping"]
    payment = request.session["checkout_payment"]

    selected_shipping_method = next(
        (
            method
            for method in checkout.shipping_methods
            if method.code == shipping.get("shipping_method")
        ),
        None,
    )
    payment_method_label = dict(get_payment_choices(checkout.currency)).get(
        payment.get("payment_method"),
        payment.get("payment_method", ""),
    )

    selected_address = get_object_or_404(
        Address,
        id=shipping["address_id"],
        user=request.user,
    )

    if request.method == "POST":

        checkout_attempt_token = _get_checkout_attempt_token(request)

        cleaned_data = {
            **shipping,
            **payment,
        }

        created = True
        try:
            with transaction.atomic():
                order = create_order(
                    user=request.user,
                    address=selected_address,
                    cleaned_data=cleaned_data,
                    checkout=checkout,
                    checkout_attempt_token=checkout_attempt_token,
                    enforce_single_retryable_online_order=True,
                    communication_language=get_language() or "en",
                )
        except ExistingCheckoutAttemptOrder as replay:
            created = False
            order = replay.order
        except RetryableOnlineOrderConflict as conflict:
            return render(
                request,
                "orders/review.html",
                {
                    "checkout": checkout,
                    "selected_address": selected_address,
                    "shipping": shipping,
                    "payment": payment,
                    "selected_shipping_method": selected_shipping_method,
                    "payment_method_label": payment_method_label,
                    "checkout_step": "review",
                    "checkout_attempt_token": checkout_attempt_token or _start_checkout_attempt(request),
                    "payment_conflict": conflict.order,
                },
                status=409,
            )
        except IntegrityError:
            created = False
            order = Order.objects.filter(
                user=request.user,
                checkout_attempt_token=checkout_attempt_token,
            ).first()
            if order is None:
                raise
        except ValueError as error:
            messages.error(request, str(error))
            return redirect("orders:checkout_shipping")

        if created:
            detach_created_order_cart(
                request,
                checkout_attempt_token=checkout_attempt_token,
            )

        if order.requires_online_payment:
            return redirect("orders:payment_start", order_id=order.id)

        confirm_cod_order(order, actor=request.user)
        return redirect("orders:order_result", order_id=order.id)

    return render(
        request,
        "orders/review.html",
        {
            "checkout": checkout,
            "selected_address": selected_address,
            "shipping": shipping,
            "payment": payment,
            "selected_shipping_method": selected_shipping_method,
            "payment_method_label": payment_method_label,
            "checkout_step": "review",
            "checkout_attempt_token": _get_checkout_attempt_token(request) or _start_checkout_attempt(request),
        },
    )
