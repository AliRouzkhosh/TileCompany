"""
accounts/selectors.py

Read-only queries for the Accounts application.

Selectors should ONLY retrieve data.
No updates, business rules, or permission checks belong here.
"""

from django.contrib.auth.models import User
from django.db.models import Count

from .models import Profile, Address
from orders.models import Order
from products.models import Wishlist
from support.models import Ticket


# ---------------------------------------------------------------------
# Profile
# ---------------------------------------------------------------------

def get_profile(user: User) -> Profile:
    """
    Return or create profile safely.
    """
    profile, _ = Profile.objects.get_or_create(user=user)
    return profile

# ---------------------------------------------------------------------
# Addresses
# ---------------------------------------------------------------------

def get_addresses(user: User):
    """
    Return all addresses belonging to the user.
    """
    return (
        Address.objects
        .filter(user=user)
        .order_by(
            "-is_default_shipping",
            "-is_default_billing",
            "-created_at",
        )
    )


def get_address(*, user: User, address_id: int):
    """
    Return one address owned by the user.
    """
    return Address.objects.get(
        id=address_id,
        user=user,
    )


def get_default_shipping_address(user: User):
    return (
        Address.objects
        .filter(
            user=user,
            is_default_shipping=True,
        )
        .first()
    )


def get_default_billing_address(user: User):
    return (
        Address.objects
        .filter(
            user=user,
            is_default_billing=True,
        )
        .first()
    )

# ---------------------------------------------------------------------
# Account Overview
# ---------------------------------------------------------------------

def get_account_overview(user: User) -> dict:
    """
    Central read-only aggregator for account dashboard.
    """

    profile = get_profile(user)

    status_rows = list(
        Order.objects.filter(user=user)
        .values("status")
        .annotate(count=Count("id"))
    )
    counts_by_status = {row["status"]: row["count"] for row in status_rows}
    default_address = get_default_shipping_address(user)

    return {
        "profile": profile,
        "order_status_counts": {
            "current": sum(
                counts_by_status.get(status, 0)
                for status in (
                    Order.Status.CONFIRMED,
                    Order.Status.ON_HOLD,
                    Order.Status.SHIPPED,
                )
            ),
            "delivered": counts_by_status.get(Order.Status.DELIVERED, 0),
            "cancelled": counts_by_status.get(Order.Status.CANCELLED, 0),
        },
        "default_address": default_address,
    }
