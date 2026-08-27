from django.urls import reverse
from django.utils.functional import SimpleLazyObject
from django.utils.translation import gettext_lazy as _

from .models import SitePageKey
from .selectors import get_published_site_page_keys


FOOTER_SITE_PAGE_ROUTES = {
    SitePageKey.ABOUT: ("sitepages:about", _("About")),
    SitePageKey.PRIVACY: ("sitepages:privacy", _("Privacy")),
    SitePageKey.TERMS: ("sitepages:terms", _("Terms & Conditions")),
    SitePageKey.SHIPPING_DELIVERY: (
        "sitepages:shipping_delivery",
        _("Shipping & Delivery"),
    ),
    SitePageKey.RETURNS_REFUNDS: (
        "sitepages:returns_refunds",
        _("Returns & Refunds"),
    ),
    SitePageKey.PAYMENT_INFORMATION: (
        "sitepages:payment_information",
        _("Payment Information"),
    ),
}


def _footer_site_page_links():
    published_keys = set(get_published_site_page_keys())
    return [
        {"key": key, "url": reverse(route_name), "label": label}
        for key, (route_name, label) in FOOTER_SITE_PAGE_ROUTES.items()
        if key in published_keys
    ]


def site_page_footer(request):
    # Remains unevaluated for fragment templates that do not render the footer.
    return {"footer_site_page_links": SimpleLazyObject(_footer_site_page_links)}
