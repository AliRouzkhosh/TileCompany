############### orders/orders.py ##########################
from django.shortcuts import render, redirect, get_object_or_404
from django.http import FileResponse, Http404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.urls import reverse
from TileCompany.uploads import safe_upload_filename
from django.utils.translation import gettext as _, pgettext
from TileCompany.htmx import is_htmx_target
from decimal import Decimal

from .models import (
    Order,
    RefundAttachment,
)

from .forms import RefundRequestForm
from .selectors import get_customer_order_history, get_customer_order_detail

from .services import (
    cancel_unpaid_order,
    create_refund_request,
)

# ==========================================================
# Helpers
# ==========================================================

def _redirect_to_order(order):
    return redirect(
        "orders:order_detail",
        order_id=order.id,
    )
# ==========================================================
# Order List
# ==========================================================

@login_required
def order_list(request):
    history = get_customer_order_history(
        user=request.user,
        status_filter=request.GET.get("status"),
        page_number=request.GET.get("page"),
    )

    context = {
            **history,
            "breadcrumbs": [
                {
                    "label": pgettext("account breadcrumb", "My Account"),
                    "url": reverse("accounts:overview"),
                },
                {
                    "label": pgettext("order breadcrumb", "Orders"),
                    "url": None,
                }
            ],
    }
    if is_htmx_target(request, "account-workspace"):
        context["account_content_template"] = "orders/components/orders_workspace.html"
        return render(request, "accounts/components/account_workspace.html", context)
    if is_htmx_target(request, "orders-workspace"):
        return render(request, "orders/components/orders_workspace.html", context)
    return render(request, "orders/order_list.html", context)


def _refund_action_breadcrumbs(order, final_label):
    return [
        {"label": pgettext("account breadcrumb", "My Account"), "url": reverse("accounts:overview")},
        {"label": pgettext("order breadcrumb", "Orders"), "url": reverse("orders:order_list")},
        {
            "label": _("Order #%(number)s") % {"number": order.user_order_number},
            "url": reverse("orders:order_detail", kwargs={"order_id": order.id}),
        },
        {"label": final_label, "url": None},
    ]


# ==========================================================
# Order Detail
# ==========================================================

def _customer_timeline(order):
    """Read persisted customer-visible lifecycle events without mutation."""
    # Refund transitions are represented from the authoritative refund fields
    # below.  Status-history notes may contain staff names and fulfillment
    # details, neither of which belongs in the customer timeline.
    entries = [
        {"label": item.display_note or item.get_status_display(), "timestamp": item.changed_at}
        for item in order.status_history.all()
        if not item.note.startswith("Refund ")
    ]
    if order.paid_at:
        # Reuse the established, translated customer-facing payment label.
        entries.append({"label": _("Payment successful"), "timestamp": order.paid_at})
    labels = {"requested": _("Refund requested"), "needs_information": _("More information required"), "approved": _("Refund approved"), "rejected": _("Refund rejected"), "processed": _("Refund processed")}
    if order.refund_requested_at:
        entries.append({"label": labels["requested"], "timestamp": order.refund_requested_at})
    if order.refund_reviewed_at and order.refund_status in labels:
        entries.append({"label": labels[order.refund_status], "timestamp": order.refund_reviewed_at})
    if order.refund_processed_at:
        entries.append({"label": labels["processed"], "timestamp": order.refund_processed_at})
    return sorted(entries, key=lambda entry: entry["timestamp"])


def _historical_order_totals(order):
    """Present reference totals only when every line has frozen reference truth."""
    items = list(order.items.all())
    subtotal = sum((item.subtotal for item in items), Decimal("0.00"))
    reference_complete = bool(items) and all(
        item.unit_reference_price is not None for item in items
    )
    reference_subtotal = (
        sum((item.unit_reference_price * item.quantity for item in items), Decimal("0.00"))
        if reference_complete else None
    )
    return {
        "subtotal": subtotal,
        "reference_subtotal": reference_subtotal,
        "product_discounts": (
            reference_subtotal - subtotal if reference_subtotal is not None else None
        ),
        "tax": order.tax_amount,
    }

@login_required
def order_detail(request, order_id):
    """
        Display a customer's order details, including its
        current status, history, and available actions.
        """
    order = get_customer_order_detail(user=request.user, order_id=order_id)

    return render(
        request,
        "orders/order_detail.html",
        {
            "order": order,
            "timeline": _customer_timeline(order),
            "historical_totals": _historical_order_totals(order),
            "can_cancel_order": order.can_cancel,
            "can_request_refund": order.can_request_refund,
            "breadcrumbs": [
                {
                    "label": pgettext("account breadcrumb", "My Account"),
                    "url": reverse("accounts:overview"),
                },
                {
                    "label": pgettext("order breadcrumb", "Orders"),
                    "url": reverse("orders:order_list"),
                },
                {
                    "label": _("Order #%(number)s") % {"number": order.user_order_number},
                    "url": None,
                },
            ],
        },
    )


@login_required
def refund_attachment_download(request, attachment_id):
    attachment = get_object_or_404(RefundAttachment.objects.select_related("order"), pk=attachment_id)
    if attachment.order.user_id != request.user.id and not request.user.is_superuser:
        raise Http404
    response = FileResponse(attachment.file.open("rb"), as_attachment=True, filename=safe_upload_filename(attachment.original_filename or attachment.file.name.rsplit("/", 1)[-1]))
    response["Cache-Control"] = "private, no-store"
    return response


# ==========================================================
# Cancel Order
# ==========================================================

@login_required
def order_cancel(request, order_id):
    """
        Handle customer-initiated order cancellation or
        refund requests based on the current order state.
        """
    order = get_object_or_404(
        Order,
        id=order_id,
        user=request.user,
    )

    # ---------------------------------------------------------
    # BLOCK: shipped / delivered
    # ---------------------------------------------------------
    if order.is_shipped:
        messages.error(
            request,
            _("Refund requests cannot be submitted while the order is in transit.")
        )
        return _redirect_to_order(order)

    # ---------------------------------------------------------
    # CASE 1: PRE-PAYMENT CANCEL (pending)
    # ---------------------------------------------------------
    if order.can_cancel:

        if request.method == "POST":
            cancel_unpaid_order(
                order=order,
                actor=request.user,
            )

            messages.success(
                request,
                _("Your order has been cancelled and stock released."),
            )

            return _redirect_to_order(order)

        return render(
            request,
            "orders/order_cancel_confirm.html",
            {
                "order": order,
                "breadcrumbs": _refund_action_breadcrumbs(order, _("Cancel order")),
            },
        )

    # ---------------------------------------------------------
    # CASE 2: POST-PAYMENT REFUND REQUEST (confirmed)
    # ---------------------------------------------------------
    if order.can_request_refund:

        if request.method == "POST":

            form = RefundRequestForm(request.POST, request.FILES)

            if form.is_valid():
                try:
                    create_refund_request(
                        order=order,
                        actor=request.user,
                        cleaned_data=form.cleaned_data,
                        attachments=form.cleaned_data["attachments"],
                    )
                except ValueError:
                    messages.error(request, _("This order is not eligible for a refund request."))
                    return _redirect_to_order(order)

                messages.success(
                    request,
                    _(
                        "Your refund request has been submitted successfully. "
                        "Our team will review it and contact you if additional "
                        "information is required."
                    ),
                )

                return _redirect_to_order(order)

        else:

            form = RefundRequestForm()

        return render(
            request,
            "orders/order_refund_confirm.html",
            {
                "order": order,
                "form": form,
                "breadcrumbs": _refund_action_breadcrumbs(order, _("Refund request")),
            },
        )

    # ---------------------------------------------------------
    # DEFAULT: NOT ALLOWED
    # ---------------------------------------------------------
    if order.refund_status != Order.RefundStatus.NONE:
        return _redirect_to_order(order)

    messages.error(
        request,
        _("This order cannot be cancelled.")
    )

    return redirect("orders:order_detail", order_id=order.id)
