"""Small, currency-local pricing facts shared by commerce presentation."""

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class DiscountPricing:
    selling_unit_price: Decimal
    reference_unit_price: Decimal | None
    unit_savings: Decimal
    line_savings: Decimal
    discount_percent: int


def discount_pricing(*, selling_unit_price, reference_unit_price, quantity=1):
    """Derive truthful savings from one currency's list/selling price pair."""

    selling = Decimal(selling_unit_price)
    reference = Decimal(reference_unit_price) if reference_unit_price is not None else None
    if reference is None or reference <= selling:
        return DiscountPricing(selling, None, Decimal("0"), Decimal("0"), 0)
    unit_savings = reference - selling
    return DiscountPricing(
        selling,
        reference,
        unit_savings,
        unit_savings * int(quantity),
        round((unit_savings / reference) * 100),
    )
