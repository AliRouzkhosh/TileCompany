"""
accounts/services.py

Business logic for the Accounts application.

Services are responsible for:

- Updating user/account data
- Executing business rules
- Coordinating model updates
- Database transactions

Services should NOT render templates or return HTTP responses.
"""
from django.contrib.auth.models import User
from django.db import transaction

from .models import Profile
from .selectors import get_account_overview
from django.shortcuts import get_object_or_404

from .models import Address


# ---------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------

def build_account_overview(*, user: User) -> dict:
    return get_account_overview(user)


# ---------------------------------------------------------------------
# Profile
# ---------------------------------------------------------------------

@transaction.atomic
def update_account(*, user_form, profile_form):

    user = user_form.save()
    profile = profile_form.save()

    return user, profile


# ---------------------------------------------------------------------
# Password
# ---------------------------------------------------------------------

@transaction.atomic
def change_user_password(*, user: User, new_password: str):
    """
    Secure password update.
    """

    user.set_password(new_password)
    user.save(update_fields=["password"])
    return user

# ---------------------------------------------------------------------
# Addresses
# ---------------------------------------------------------------------

@transaction.atomic
def create_address(*, user, form):
    """
    Create a new customer address.
    """

    address = form.save(commit=False)
    address.user = user

    if address.is_default_shipping:
        (
            Address.objects
            .filter(user=user)
            .update(is_default_shipping=False)
        )

    if address.is_default_billing:
        (
            Address.objects
            .filter(user=user)
            .update(is_default_billing=False)
        )

    address.save()

    return address

@transaction.atomic
def update_address(*, user, address, form):
    """
    Update an existing address.
    """

    address = form.save(commit=False)

    if address.is_default_shipping:
        (
            Address.objects
            .filter(user=user)
            .exclude(pk=address.pk)
            .update(is_default_shipping=False)
        )

    if address.is_default_billing:
        (
            Address.objects
            .filter(user=user)
            .exclude(pk=address.pk)
            .update(is_default_billing=False)
        )

    address.save()

    return address

@transaction.atomic
def delete_address(address):
    address.delete()