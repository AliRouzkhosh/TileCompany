from django.db.models import Count, Min
from django.urls import reverse

from .selectors import get_retryable_online_orders_for_user


def cart_item_count(request):
    """Makes {{ cart_count }} available in every template (used for the navbar badge)."""
    cart = request.session.get("cart", {})
    count = sum(cart.values())
    return {"cart_count": count}


def payment_attention(request):
    """Provide one bounded, authoritative payment-recovery summary for the storefront shell."""
    if not request.user.is_authenticated:
        return {"payment_attention": None}

    summary = get_retryable_online_orders_for_user(request.user).aggregate(
        count=Count("pk"),
        order_id=Min("pk"),
        order_number=Min("user_order_number"),
    )
    if not summary["count"]:
        return {"payment_attention": None}

    attention = {
        "count": summary["count"],
        "order_number": summary["order_number"],
    }
    if summary["count"] == 1:
        attention["action_url"] = reverse(
            "orders:order_detail",
            kwargs={"order_id": summary["order_id"]},
        )
    else:
        attention["action_url"] = reverse("orders:order_list")
    return {"payment_attention": attention}
