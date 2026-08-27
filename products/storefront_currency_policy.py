"""Transitional source for currencies required on publishable Variants.

There is no enabled/disabled store-currency configuration yet.  Keeping this
boundary injectable lets a future StoreConfiguration replace the source without
changing publication rules.
"""

from .currency import Currency


def required_storefront_currencies(configured=None) -> tuple[str, ...]:
    values = configured if configured is not None else (currency.value for currency in Currency)
    return tuple(dict.fromkeys(str(value) for value in values))
