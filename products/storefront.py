"""Temporary storefront presentation policy, kept separate from pricing domain logic."""

from .currency import Currency
from django.utils.translation import get_language


def get_storefront_currency():
    """Return the locale-selected storefront currency without conversion."""

    language = (get_language() or "").lower().replace("_", "-")
    return Currency.TOMAN if language.startswith("fa") else Currency.USD
