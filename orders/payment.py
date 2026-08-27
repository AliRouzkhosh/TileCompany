############## orders/payment.py ####################
from abc import ABC, abstractmethod
from dataclasses import dataclass
import logging
import secrets

from requests.exceptions import RequestException
from .zarinpal_client import (
    request_payment,
    get_payment_url,
    verify_payment,
)
from django.shortcuts import get_object_or_404, redirect, render
from django.contrib.auth.decorators import login_required
from django.urls import reverse
from django.conf import settings
from django.contrib import messages
from django.utils.translation import gettext as _

from .models import Order
from .services import mark_order_paid, record_payment_recovery_notification
from products.currency import Currency


logger = logging.getLogger(__name__)


# ==========================================================
# Payment Result
# ==========================================================

@dataclass
class PaymentResult:
    success: bool
    state: str = "failure"
    payment_reference: str = ""
    provider: str = ""
    authority: str = ""
    message: str = ""


# ==========================================================
# Base Gateway
# ==========================================================

class BasePaymentGateway(ABC):
    provider = ""
    @abstractmethod
    def request(
        self,
        amount,
        description,
        callback_url,
        mobile="",
        email="",
    ):
        pass

    @abstractmethod
    def payment_url(self, authority):
        pass

    @abstractmethod
    def verify(self, amount, authority):
        pass


# ==========================================================
# ZarinPal Gateway
# ==========================================================

class ZarinPalGateway(BasePaymentGateway):
    provider = "ZarinPal"
    def request(
        self,
        amount,
        description,
        callback_url,
        mobile="",
        email="",
    ):
        return request_payment(
            amount_toman=amount,
            description=description,
            callback_url=callback_url,
            mobile=mobile,
            email=email,
        )

    def payment_url(self, authority):
        return get_payment_url(authority)

    def verify(self, amount, authority):

        success, ref_id, _ = verify_payment(
            amount_toman=amount,
            authority=authority,
        )

        return PaymentResult(
            success=success,
            state="success" if success else "failure",
            payment_reference=str(ref_id or ""),
            provider=self.provider,
            authority=authority,
            message=_("Payment verified.") if success else _("Payment verification failed."),
        )


# ==========================================================
# Development Gateway
# ==========================================================

class MockGateway(BasePaymentGateway):
    RESULT_SUCCESS = "success"
    RESULT_CANCELLED = "cancelled"
    RESULT_FAILED = "failed"
    provider = _("Development gateway")

    def request(
        self,
        amount,
        description,
        callback_url,
        mobile="",
        email="",
    ):
        return "MOCK_AUTHORITY"

    def payment_url(self, authority):
        return None

    def verify(self, amount, authority, result="success"):

        if result == self.RESULT_SUCCESS:
            return PaymentResult(
                success=True,
                state="success",
                payment_reference="MOCK-REF-100001",
                provider=self.provider,
                authority=authority,
                message=_("Mock payment successful."),
            )

        if result == self.RESULT_CANCELLED:
            return PaymentResult(
                success=False,
                state="cancelled",
                provider=self.provider,
                authority=authority,
                message=_("Customer cancelled the payment."),
            )

        return PaymentResult(
            success=False,
            state="failure",
            provider=self.provider,
            authority=authority,
            message=_("Payment failed."),
        )


class LocalPaymentSimulatorGateway(BasePaymentGateway):
    """Explicit local-production-only provider facade for browser testing."""

    RESULT_SUCCESS = "success"
    RESULT_CANCELLED = "cancelled"
    RESULT_FAILED = "failed"
    provider = _("Local payment simulator")

    def request(self, amount, description, callback_url, mobile="", email=""):
        return f"LOCAL-{secrets.token_urlsafe(24)}"

    def payment_url(self, authority):
        return None

    def verify(self, amount, authority, result="failed"):
        if result == self.RESULT_SUCCESS:
            return PaymentResult(
                success=True,
                state="success",
                payment_reference=f"LOCAL-REF-{authority.removeprefix('LOCAL-')}",
                provider=self.provider,
                authority=authority,
                message=_("Simulated payment verified."),
            )
        if result == self.RESULT_CANCELLED:
            return PaymentResult(False, "cancelled", provider=self.provider, authority=authority, message=_("Customer cancelled the payment."))
        return PaymentResult(False, "failure", provider=self.provider, authority=authority, message=_("Payment failed."))


class DevelopmentZarinPalGateway(MockGateway):
    """Development-only ZarinPal stand-in that supplies controlled provider outcomes."""

    provider = _("ZarinPal development gateway")

    def request(self, amount, description, callback_url, mobile="", email=""):
        return "DEV_ZARINPAL_AUTHORITY"

    def verify(self, amount, authority, result="success"):
        if result == self.RESULT_SUCCESS:
            return PaymentResult(
                success=True,
                state="success",
                payment_reference="DEV-ZARINPAL-REF-100001",
                provider=self.provider,
                authority=authority,
                message=_("Development ZarinPal payment successful."),
            )

        if result == self.RESULT_CANCELLED:
            return PaymentResult(
                success=False,
                state="cancelled",
                provider=self.provider,
                authority=authority,
                message=_("Customer cancelled the payment."),
            )

        return PaymentResult(
            success=False,
            state="failure",
            provider=self.provider,
            authority=authority,
            message=_("Payment failed."),
        )


# ==========================================================
# Factory
# ==========================================================

def get_payment_gateway_for_order(order):
    if order.payment_method == Order.PaymentMethod.MOCK:
        if settings.LOCAL_PAYMENT_SIMULATOR_ENABLED:
            if order.currency != Currency.USD:
                raise ValueError(_("The local payment simulator is only available for USD orders."))
            return LocalPaymentSimulatorGateway()
        if not settings.MOCK_PAYMENT_SIMULATION_ENABLED:
            raise ValueError(_("Online payment is temporarily unavailable. Please try again later or contact support."))
        return MockGateway()
    if order.payment_method == Order.PaymentMethod.ZARINPAL:
        if order.currency != Currency.TOMAN:
            raise ValueError(_("ZarinPal is only available for Toman orders."))
        if settings.ZARINPAL_DEVELOPMENT_AUTO_SUCCESS and settings.MOCK_PAYMENT_SIMULATION_ENABLED:
            return DevelopmentZarinPalGateway()
        if not settings.ZARINPAL_MERCHANT_ID.strip():
            raise ValueError(_("Online payment is temporarily unavailable. Please try again later or contact support."))
        return ZarinPalGateway()
    raise ValueError(_("This order does not use an online payment gateway."))


def _payment_not_available_response(request, order):
    messages.error(request, _("This order is not eligible for online payment."))
    return redirect("orders:order_detail", order_id=order.id)


def _result_presentation(order, *, payment=None, explicit_state=""):
    """Build result copy and actions from persisted Order truth."""

    state = explicit_state or getattr(payment, "state", "")
    if order.is_cancelled:
        state = "order_cancelled"
    elif order.is_cash_on_delivery:
        state = "cod"
    elif order.is_paid:
        state = "success"
    elif state not in {"failure", "cancelled"}:
        state = "pending"

    definitions = {
        "success": {
            "tone": "success",
            "icon": "circle-check-big",
            "title": _("Payment successful"),
            "description": _("Your payment has been confirmed and your order is now being processed."),
        },
        "cod": {
            "tone": "info",
            "icon": "package-check",
            "title": _("Order confirmed"),
            "description": _("Cash on Delivery order confirmed; payment is due on delivery."),
        },
        "failure": {
            "tone": "error",
            "icon": "circle-x",
            "title": _("Payment failed"),
            "description": _("Order created, awaiting payment."),
        },
        "cancelled": {
            "tone": "warning",
            "icon": "ban",
            "title": _("Payment cancelled."),
            "description": _("Order created, awaiting payment."),
        },
        "pending": {
            "tone": "info",
            "icon": "clock-3",
            "title": _("Awaiting payment"),
            "description": _("Order created, awaiting payment."),
        },
        "order_cancelled": {
            "tone": "warning",
            "icon": "ban",
            "title": _("Order cancelled"),
            "description": _("Order cancelled."),
        },
    }
    result = definitions[state]
    can_retry = order.can_start_online_payment
    result.update(
        {
            "state": state,
            "order_reference": (
                f"#{order.user_order_number}"
                if order.user_order_number is not None
                else "—"
            ),
            "order_status_label": order.get_status_display(),
            "payment_method_label": order.get_payment_method_display(),
            "payment_status_label": (
                _("Unpaid")
                if state == "order_cancelled" and not order.is_paid
                else order.payment_status_display
            ),
            "payment_reference": order.payment_reference if order.is_paid else "",
            "can_retry": can_retry,
            "primary_url": reverse(
                "orders:payment_start" if can_retry else "orders:order_detail",
                args=[order.id],
            ),
            "primary_label": _("Retry payment") if can_retry else _("View order"),
            "secondary_url": (
                reverse("orders:order_detail", args=[order.id])
                if can_retry
                else reverse("products:product_list")
            ),
            "secondary_label": _("View order") if can_retry else _("Continue shopping"),
        }
    )
    return result


def _render_result(request, order, *, payment=None, explicit_state=""):
    return render(
        request,
        "orders/payment_result.html",
        {
            "order": order,
            "payment": payment,
            "result": _result_presentation(
                order,
                payment=payment,
                explicit_state=explicit_state,
            ),
        },
    )


@login_required
def order_result(request, order_id):
    """Render an ownership-protected result from persisted Order state."""

    order = get_object_or_404(Order, id=order_id, user=request.user)
    return _render_result(request, order)


@login_required
def payment_start(request, order_id):
    """
        Initiate payment for an unpaid order and redirect
        the customer to the selected payment gateway.
        """
    order = get_object_or_404(
        Order,
        id=order_id,
        user=request.user,
    )

    if not order.can_start_online_payment:
        return _payment_not_available_response(request, order)
    try:
        gateway = get_payment_gateway_for_order(order)
    except ValueError as error:
        messages.error(request, str(error))
        return redirect("orders:order_detail", order_id=order.id)

    callback_url = request.build_absolute_uri(
        reverse(
            "orders:payment_callback",
            args=[order.id],
        )
    )

    try:
        authority = gateway.request(
            amount=order.total_amount,
            description=f"Order #{order.user_order_number}",
            callback_url=callback_url,
            mobile=order.phone,
        )
    except (RequestException, ValueError) as error:
        # External-provider exception bodies can carry payload details; retain
        # a correlation identifier and class without emitting that payload.
        logger.warning(
            "payment_start_failed order_id=%s error_class=%s",
            order.pk,
            error.__class__.__name__,
        )
        messages.error(
            request,
            _("We couldn't start your payment. Please try again later or contact support."),
        )
        return redirect("orders:order_detail", order_id=order.id)

    order.payment_reference = authority
    order.save(update_fields=["payment_reference"])

    payment_url = gateway.payment_url(authority)

    if isinstance(gateway, LocalPaymentSimulatorGateway):
        return redirect("orders:local_payment_simulator", order_id=order.id)

    if isinstance(gateway, DevelopmentZarinPalGateway):
        if settings.MOCK_PAYMENT_SIMULATION_ENABLED:
            return redirect("orders:mock_payment", order_id=order.id)
        return _payment_not_available_response(request, order)

    if type(gateway) is MockGateway and settings.MOCK_PAYMENT_SIMULATION_ENABLED:
        return redirect("orders:mock_payment", order_id=order.id)

    if payment_url is None:
        return redirect(
            reverse(
                "orders:payment_callback",
                args=[order.id],
            )
            + "?result=success"
        )

    return redirect(payment_url)

@login_required
def payment_callback(request, order_id):
    """
      Handle the payment gateway callback and finalize
      the order if payment verification succeeds.
      """
    order = get_object_or_404(
        Order,
        id=order_id,
        user=request.user,
    )

    if order.is_paid:
        # A duplicate callback never creates a second intent; it may safely
        # retry a previously failed post-commit delivery.
        from .email_delivery import ensure_order_email_delivery
        from .models import OrderEmailDelivery
        ensure_order_email_delivery(
            order=order,
            kind=OrderEmailDelivery.Kind.ONLINE_PAID_RECEIPT,
        )
        return _render_result(request, order)

    if not order.can_start_online_payment:
        return _render_result(
            request,
            order,
            payment=PaymentResult(
                success=False,
                state="failure",
                message=_("This order is not eligible for online payment."),
            ),
        )

    try:
        gateway = get_payment_gateway_for_order(order)
    except ValueError as error:
        return _render_result(
            request,
            order,
            payment=PaymentResult(success=False, state="failure", message=str(error)),
        )

    if isinstance(gateway, LocalPaymentSimulatorGateway):
        result = request.GET.get("result", "failed")
        authority = request.GET.get("authority", "")
        if authority != order.payment_reference:
            payment = PaymentResult(
                success=False, state="failure", provider=gateway.provider,
                authority=authority, message=_("Payment verification failed."),
            )
        else:
            payment = gateway.verify(order.total_amount, authority, result=result)
    elif isinstance(gateway, MockGateway):

        result = request.GET.get("result", "success")

        payment = gateway.verify(
            order.total_amount,
            order.payment_reference,
            result=result,
        )

    else:

        status = request.GET.get("Status")

        authority = request.GET.get("Authority")

        if status != "OK":

            payment = PaymentResult(
                success=False,
                provider=gateway.provider,
                authority=authority or "",
                state="cancelled",
                message=_("Payment cancelled."),
            )

        elif not authority or authority != order.payment_reference:

            payment = PaymentResult(
                success=False,
                provider=gateway.provider,
                authority=authority or "",
                state="failure",
                message=_("Payment verification failed."),
            )

        else:

            payment = gateway.verify(
                order.total_amount,
                authority,
            )

    if payment.success:
        order = mark_order_paid(
            order=order,
            payment_reference=(
                    payment.payment_reference
                    or payment.authority
            ),
            provider=(
                    payment.provider
                    or gateway.provider
            ),
        )

        return _render_result(request, order, payment=payment)

    record_payment_recovery_notification(order=order, state=payment.state)
    return _render_result(request, order, payment=payment)


@login_required
def mock_payment(request, order_id):
    """Development-only deterministic UI for mock and development ZarinPal outcomes."""

    order = get_object_or_404(Order, id=order_id, user=request.user)
    if not settings.MOCK_PAYMENT_SIMULATION_ENABLED:
        return _payment_not_available_response(request, order)
    try:
        gateway = get_payment_gateway_for_order(order)
    except ValueError:
        return _payment_not_available_response(request, order)
    if not isinstance(gateway, MockGateway):
        return _payment_not_available_response(request, order)
    if not order.can_start_online_payment:
        return _payment_not_available_response(request, order)

    if request.method == "POST":
        outcome = request.POST.get("outcome")
        allowed_outcomes = {
            MockGateway.RESULT_SUCCESS,
            MockGateway.RESULT_FAILED,
            MockGateway.RESULT_CANCELLED,
        }
        if outcome not in allowed_outcomes:
            messages.error(request, _("Please select a valid payment outcome."))
            return redirect("orders:mock_payment", order_id=order.id)
        return redirect(
            reverse("orders:payment_callback", args=[order.id])
            + f"?result={outcome}"
        )

    return render(
        request,
        "orders/mock_payment.html",
        {
            "order": order,
            "simulator_title": (
                _("ZarinPal development simulator")
                if isinstance(gateway, DevelopmentZarinPalGateway)
                else _("Mock payment")
            ),
        },
    )


@login_required
def local_payment_simulator(request, order_id):
    """Controlled local provider page; outcomes only enter via POST then callback."""

    order = get_object_or_404(Order, id=order_id, user=request.user)
    if not settings.LOCAL_PAYMENT_SIMULATOR_ENABLED:
        return _payment_not_available_response(request, order)
    try:
        gateway = get_payment_gateway_for_order(order)
    except ValueError:
        return _payment_not_available_response(request, order)
    if not isinstance(gateway, LocalPaymentSimulatorGateway) or not order.can_start_online_payment:
        return _payment_not_available_response(request, order)

    if request.method == "POST":
        outcome = request.POST.get("outcome")
        if outcome not in {
            LocalPaymentSimulatorGateway.RESULT_SUCCESS,
            LocalPaymentSimulatorGateway.RESULT_FAILED,
            LocalPaymentSimulatorGateway.RESULT_CANCELLED,
        }:
            messages.error(request, _("Please select a valid payment outcome."))
            return redirect("orders:local_payment_simulator", order_id=order.id)
        callback = reverse("orders:payment_callback", args=[order.id])
        return redirect(f"{callback}?result={outcome}&authority={order.payment_reference}")

    return render(request, "orders/local_payment_simulator.html", {"order": order})
