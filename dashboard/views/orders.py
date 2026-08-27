#################### dashboard/views/orders.py #################
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST
from django.utils.translation import gettext as _

from products.commerce_presentation import quantity_breakdown

from dashboard.actions import (
    execute_bulk_action,
    execute_single_action,
)

from dashboard.forms import RefundReviewForm

from dashboard.permissions import dashboard_permission_required, owner_required
from dashboard.request_cache import get_request_value, set_request_value

from dashboard.selectors import (
    filter_orders,
    get_all_orders,
    get_attention_queue,
    get_fulfillment_queue,
    get_in_transit_queue,
    get_order,
    get_refund_processing_queue,
    get_refund_queue,
    get_refund_requested_queue,
    get_refund_waiting_customer_queue,
    get_refund_customer_replied_queue,
    paginate_queryset,
)


def _restored_quantity_text(result):
    return quantity_breakdown(
        getattr(result, "restored_unit_quantities", ())
        or getattr(result, "unit_quantities", ()),
        fallback_quantity=(
            getattr(result, "restored_quantity", 0)
            or getattr(result, "total_quantity", 0)
        ),
    )

@dashboard_permission_required(
    "orders.view_order",
    "orders.view_orderitem",
    "orders.view_orderstatushistory",
    "orders.view_ordernote",
)
def order_detail(request, order_id):

    is_owner = request.user.is_superuser
    order = get_order(order_id, include_refund_attachments=is_owner)

    return render(
        request,
        "dashboard/orders/order_detail.html",
        {
            "order": order,
            "can_manage_fulfillment": request.user.has_perm("orders.manage_fulfillment"),
            "is_owner": is_owner,
            "can_view_sensitive_payment": is_owner,
        },
    )

@dashboard_permission_required("orders.view_order", "orders.manage_fulfillment")
@require_POST
def ship_order(request, order_id):

    order = get_order(order_id)

    if execute_single_action(
        "ship",
        order,
        request.user,
    ):
        messages.success(request, _("Order marked as shipped."))
    else:
        messages.error(request, _("Unable to ship this order."))

    return redirect(
        "dashboard:order_detail",
        order_id=order.id,
    )

@dashboard_permission_required("orders.view_order", "orders.manage_fulfillment")
@require_POST
def deliver_order(request, order_id):

    order = get_order(order_id)

    if execute_single_action(
        "deliver",
        order,
        request.user,
    ):
        messages.success(request, _("Order delivered."))
    else:
        messages.error(request, _("Unable to deliver this order."))

    return redirect(
        "dashboard:order_detail",
        order_id=order.id,
    )

@owner_required
def approve_refund(request, order_id):

    order = get_order(order_id)

    if request.method == "POST":

        form = RefundReviewForm(
            request.POST,
            instance=order,
        )

        if form.is_valid():

            result = execute_single_action(
                "approve_refund",
                order,
                request.user,
                refund_response=form.cleaned_data["refund_response"],
            )
            if result:
                if result.inventory_restored:
                    messages.success(request, _("Refund approved. Order cancelled and %(quantities)s returned to inventory. Refund is awaiting financial processing.") % {"quantities": _restored_quantity_text(result)})
                else:
                    messages.success(request, _("Refund approved. Inventory remains unchanged until the refund is processed."))

                return redirect(
                    "dashboard:order_detail",
                    order_id=order.id,
                )

            messages.error(
                request,
                _("Unable to approve refund."),
            )

    else:

        form = RefundReviewForm(
            instance=order,
        )

    return render(
        request,
        "dashboard/refunds/refund_review.html",
        {
            "order": order,
            "form": form,
            "action": "approve",
        },
    )

@owner_required
def reject_refund(request, order_id):

    order = get_order(order_id)

    if request.method == "POST":

        form = RefundReviewForm(
            request.POST,
            instance=order,
        )

        if form.is_valid():

            if execute_single_action(
                "reject_refund",
                order,
                request.user,
                refund_response=form.cleaned_data["refund_response"],
            ):
                messages.success(
                    request,
                    _("Refund rejected."),
                )

                return redirect(
                    "dashboard:order_detail",
                    order_id=order.id,
                )

            messages.error(
                request,
                _("Unable to reject refund."),
            )

    else:

        form = RefundReviewForm(
            instance=order,
        )

    return render(
        request,
        "dashboard/refunds/refund_review.html",
        {
            "order": order,
            "form": form,
            "action": "reject",
        },
    )

@owner_required
@require_POST
def process_refund(request, order_id):

    order = get_order(order_id)

    result = execute_single_action(
        "process_refund",
        order,
        request.user,
    )
    if result:
        if result.inventory_restored:
            messages.success(request, _("Refund processed. %(quantities)s returned to inventory.") % {"quantities": _restored_quantity_text(result)})
        elif result.inventory_already_restored:
            messages.success(request, _("Refund processed. Inventory had already been restored earlier; no stock adjustment was made."))
        else:
            messages.success(request, _("Refund processed."))
    else:
        messages.error(
            request,
            _("Unable to process refund."),
        )

    return redirect(
        "dashboard:order_detail",
        order_id=order.id,
    )

@dashboard_permission_required("orders.view_order", "orders.manage_fulfillment")
@require_POST
def cancel_order(request, order_id):

    order = get_order(order_id)

    result = execute_single_action(
        "cancel",
        order,
        request.user,
    )
    if result:
        if result.restored:
            messages.success(request, _("Order cancelled. %(quantities)s returned to inventory.") % {"quantities": _restored_quantity_text(result)})
        else:
            messages.success(request, _("Order cancelled."))
    else:
        messages.error(request, _("Unable to cancel order."))

    return redirect(
        "dashboard:order_detail",
        order_id=order.id,
    )

@login_required
@require_POST
def bulk_action(request):

    action = request.POST.get("action")

    order_ids = request.POST.getlist("orders")

    fulfillment_actions = {"ship", "deliver", "hold", "resume", "cancel"}
    refund_actions = {
        "approve_refund", "reject_refund", "request_refund_information", "process_refund",
    }
    if action in fulfillment_actions:
        if not request.user.has_perms(("orders.view_order", "orders.manage_fulfillment")):
            raise PermissionDenied
    elif action in refund_actions:
        if not request.user.is_superuser:
            raise PermissionDenied
    else:
        raise PermissionDenied

    queryset = get_all_orders().filter(
        id__in=order_ids,
    )

    success_count, failed_count = execute_bulk_action(
        action,
        queryset,
        request.user,
    )

    if success_count:
        messages.success(
            request,
            f"{success_count} order(s) updated successfully.",
        )

    if failed_count:
        messages.warning(
            request,
            f"{failed_count} order(s) could not be updated.",
        )

    return redirect(
        request.META.get("HTTP_REFERER", "dashboard:home")
    )

def build_queue_context(request, queryset, *, count_cache_key=None):
    """
    Apply search, filters and pagination.
    """

    search = request.GET.get("search", "").strip()
    status = request.GET.get("status", "")
    payment = request.GET.get("payment", "")

    queryset = filter_orders(
        queryset,
        search=search,
        status=status,
        payment=payment,
    )

    page = paginate_queryset(
        request,
        queryset,
    )
    # A filtered page count is not the same value as the global queue badge.
    # Page-number-only requests remain safe because Paginator.count still
    # describes the complete, unfiltered queue.
    if count_cache_key is not None and not any((search, status, payment)):
        set_request_value(request, count_cache_key, page.paginator.count)

    return {
        "orders": page,
        "page_obj": page,
        "search": search,
        "status": status,
        "payment": payment,
    }

@dashboard_permission_required("orders.view_order")
def fulfillment_queue(request):
    context = build_queue_context(
        request,
        get_fulfillment_queue(),
        count_cache_key=("orders", "fulfillment", request.user.pk),
    )

    return render(
        request,
        "dashboard/orders/queues/fulfillment.html",
        context,
    )

@dashboard_permission_required("orders.view_order")
def in_transit_queue(request):
    context = build_queue_context(
        request,
        get_in_transit_queue(),
    )

    return render(
        request,
        "dashboard/orders/queues/in_transit.html",
        context,
    )


@owner_required
def request_refund_information(request, order_id):
    order = get_order(order_id)

    if request.method == "POST":
        form = RefundReviewForm(request.POST, instance=order)
        if form.is_valid():
            result = execute_single_action(
                "request_refund_information",
                order,
                request.user,
                refund_response=form.cleaned_data["refund_response"],
            )
            if result:
                messages.success(request, _("The customer has been asked for more information."))
                return redirect("dashboard:order_detail", order_id=order.id)
            messages.error(request, _("Unable to request more information for this refund."))
    else:
        form = RefundReviewForm(instance=order)

    return render(
        request,
        "dashboard/refunds/refund_review.html",
        {
            "order": order,
            "form": form,
            "action": "information",
        },
    )

@owner_required
def refund_queue(request):
    refund_state = request.GET.get("refund_state", "")
    state_queues = {
        "new": get_refund_requested_queue,
        "waiting_customer": get_refund_waiting_customer_queue,
        "customer_replied": get_refund_customer_replied_queue,
    }
    selected_queue = state_queues.get(refund_state, get_refund_queue)
    context = build_queue_context(
        request,
        selected_queue(),
        count_cache_key=("orders", {
            "new": "refund_requested",
            "waiting_customer": "refund_waiting_customer",
            "customer_replied": "refund_customer_replied",
        }.get(refund_state, "refund_review"), request.user.pk),
    )
    context.update({
        "queue_mode": "review",
        "refund_state": refund_state if refund_state in state_queues else "",
        "refund_counts": {
            "all": get_request_value(request, ("orders", "refund_review", request.user.pk), lambda: get_refund_queue().count()),
            "new": get_request_value(request, ("orders", "refund_requested", request.user.pk), lambda: get_refund_requested_queue().count()),
            "waiting_customer": get_request_value(request, ("orders", "refund_waiting_customer", request.user.pk), lambda: get_refund_waiting_customer_queue().count()),
            "customer_replied": get_request_value(request, ("orders", "refund_customer_replied", request.user.pk), lambda: get_refund_customer_replied_queue().count()),
        },
    })

    return render(
        request,
        "dashboard/orders/queues/refunds.html",
        context,
    )

@dashboard_permission_required("orders.view_order")
def attention_queue(request):
    context = build_queue_context(
        request,
        get_attention_queue(),
        count_cache_key=("orders", "attention", request.user.pk),
    )

    return render(
        request,
        "dashboard/orders/queues/attention.html",
        context,
    )
@dashboard_permission_required("orders.view_order")
def all_orders(request):
    context = build_queue_context(
        request,
        get_all_orders(),
    )

    return render(
        request,
        "dashboard/orders/queues/all_orders.html",
        context,
    )


@dashboard_permission_required("orders.view_order", "orders.manage_fulfillment")
@require_POST
def hold_order(request, order_id):

    order = get_order(order_id)

    if execute_single_action(
        "hold",
        order,
        request.user,
    ):
        messages.success(request, _("Order placed on hold."))
    else:
        messages.error(request, _("Unable to place order on hold."))

    return redirect(
        "dashboard:order_detail",
        order_id=order.id,
    )

@dashboard_permission_required("orders.view_order", "orders.manage_fulfillment")
@require_POST
def resume_order(request, order_id):

    order = get_order(order_id)

    if execute_single_action(
        "resume",
        order,
        request.user,
    ):
        messages.success(request, _("Order resumed."))
    else:
        messages.error(request, _("Unable to resume order."))

    return redirect(
        "dashboard:order_detail",
        order_id=order.id,
    )


@owner_required
def refund_processing_queue(request):
    context = build_queue_context(request, get_refund_processing_queue())
    context["queue_mode"] = "processing"
    return render(request, "dashboard/orders/queues/refunds.html", context)
