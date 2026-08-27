from django import template
from django.urls import reverse
from django.utils.translation import gettext_lazy as _


register = template.Library()


@register.simple_tag
def account_navigation_items(request):
    """One route/active-state definition for every Account navigation surface."""

    namespace = getattr(request.resolver_match, "namespace", "")
    url_name = getattr(request.resolver_match, "url_name", "")
    definitions = (
        ("accounts:overview", _("Overview"), "layout-dashboard", namespace == "accounts" and url_name == "overview", "overview"),
        ("orders:order_list", _("Orders"), "package", namespace == "orders", "orders"),
        ("products:wishlist", _("Wishlist"), "heart", namespace == "products" and url_name == "wishlist", "wishlist"),
        ("accounts:address_list", _("Addresses"), "map-pin", namespace == "accounts" and url_name.startswith("address"), "addresses"),
        ("accounts:profile", _("Profile"), "user-round", namespace == "accounts" and url_name == "profile", "profile"),
        ("notifications:center", _("Notifications"), "bell", namespace == "notifications", "notifications"),
        ("support:ticket_list", _("Support"), "headset", namespace == "support", "support"),
    )
    return tuple(
        {"route": route, "url": reverse(route), "label": label, "icon": icon, "active": active, "key": key}
        for route, label, icon, active, key in definitions
    )


@register.simple_tag
def account_navigation_current(items):
    return next((item for item in items if item["active"]), items[0])
