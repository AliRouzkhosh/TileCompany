from decimal import Decimal, ROUND_HALF_UP

from django import template
from django.utils.translation import gettext as _
from django.utils.translation import get_language

from products.currency import Currency


register = template.Library()

_PERSIAN_DIGITS = str.maketrans("0123456789,.", "۰۱۲۳۴۵۶۷۸۹٬٫")


def format_localized_number(value, *, decimals=None):
    """Format a numeric presentation value without changing domain data."""
    if value is None or value == "":
        return ""
    amount = Decimal(value)
    if decimals is None:
        whole, _, fractional = f"{amount:,f}".partition(".")
        rendered = whole if not fractional.rstrip("0") else f"{whole}.{fractional.rstrip('0')}"
    else:
        rendered = f"{amount:,.{decimals}f}"
    language = (get_language() or "").lower().replace("_", "-")
    return rendered.translate(_PERSIAN_DIGITS) if language.startswith("fa") else rendered


@register.filter
def localized_number(value):
    try:
        return format_localized_number(value)
    except (ArithmeticError, ValueError, TypeError):
        return value


@register.filter
def localized_digits(value):
    """Localize digits embedded in human-readable text, never machine values."""
    if value is None:
        return ""
    rendered = str(value)
    language = (get_language() or "").lower().replace("_", "-")
    return rendered.translate(_PERSIAN_DIGITS) if language.startswith("fa") else rendered


@register.filter
def localized_integer(value):
    """Render percentage/count presentation values as rounded whole numbers."""
    try:
        rounded = Decimal(value).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
        return format_localized_number(rounded, decimals=0)
    except (ArithmeticError, ValueError, TypeError):
        return value


def format_money(value, currency):
    """Format a resolved money value without querying the database."""
    if value is None or value == "":
        return _("Price unavailable")

    amount = Decimal(value)
    if currency == Currency.TOMAN:
        return f"{format_localized_number(amount, decimals=0)} {_('Toman')}"
    if currency == Currency.USD:
        return f"${format_localized_number(amount, decimals=2)}"
    return f"{format_localized_number(amount, decimals=2)} {currency}"


@register.filter
def money(value, currency):
    return format_money(value, currency)
