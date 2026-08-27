"""Currency resolution at the request/session boundary for purchases."""

from products.currency import Currency
from products.storefront import get_storefront_currency


STOREFRONT_CURRENCY_SESSION_KEY = "storefront_currency"
CART_CURRENCY_SESSION_KEY = "cart_currency"


def get_request_currency(request):
    """Return the active storefront locale's canonical currency.

    A cart remains single-currency; ``get_cart_currency`` handles clearing a
    stale cart when the active locale changes.
    """
    currency = get_storefront_currency().value
    request.session[STOREFRONT_CURRENCY_SESSION_KEY] = currency
    return currency


def get_cart_currency(request):
    """Return the cart's single currency, resetting lines on a safe currency switch."""
    requested_currency = get_request_currency(request)
    cart_currency = request.session.get(CART_CURRENCY_SESSION_KEY)
    cart = request.session.get("cart", {})
    if cart_currency and cart_currency != requested_currency and cart:
        request.session["cart"] = {}
    if cart_currency != requested_currency:
        request.session[CART_CURRENCY_SESSION_KEY] = requested_currency
        request.session.modified = True
    return requested_currency
