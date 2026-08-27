"""Version-controlled Dashboard role bundles.

Groups are assignment bundles only. Views must authorize with ``has_perm`` and
must not inspect these Group names.
"""

from itertools import chain
from django.utils.translation import gettext_lazy as _


def _crud(app_label, *models):
    return frozenset(
        f"{app_label}.{action}_{model}"
        for model in models
        for action in ("add", "change", "delete", "view")
    )


def _view(app_label, *models):
    return frozenset(f"{app_label}.view_{model}" for model in models)


PRODUCT_MANAGER_PERMISSIONS = (
    _view("products", "product", "productvariant", "productimage", "variantimage", "variantprice")
    | frozenset({
        "products.add_product", "products.change_product",
        "products.add_productvariant", "products.change_productvariant",
        "products.add_productimage", "products.delete_productimage",
        "products.add_variantimage", "products.delete_variantimage",
        "products.add_variantprice", "products.change_variantprice", "products.delete_variantprice",
        # Generic attribute writes may replace old value rows, so deletion is required.
        "products.add_productattributevalue", "products.change_productattributevalue", "products.delete_productattributevalue",
        "products.add_variantattributevalue", "products.change_variantattributevalue", "products.delete_variantattributevalue",
        "products.change_inventory", "products.change_pricing", "products.publish_product",
        "products.run_inventory_price_import", "products.apply_inventory_price_import",
    })
    | _crud("products", "category", "brand", "design", "usage", "material", "color", "size", "finish")
)
ORDER_MANAGER_PERMISSIONS = (
    _view("orders", "order", "orderitem", "orderstatushistory", "ordernote")
    | frozenset({"orders.manage_fulfillment"})
)
SUPPORT_MANAGER_PERMISSIONS = (
    _view("support", "ticket", "ticketreply", "ticketnote", "ticketcategory", "ticketattachment", "helparticle", "ticketactivity")
    | frozenset({
        "support.change_ticket",
        "support.add_ticketreply", "support.add_ticketnote", "support.add_ticketattachment",
    })
    | _crud("support", "ticketcategory", "helparticle")
)
CONTENT_MANAGER_PERMISSIONS = (
    _crud("blog", "article", "articleattachment", "blogcategory", "blogtag")
    | frozenset({"blog.view_sitepage", "blog.change_sitepage"})
    | _view("products", "category", "product")
    | frozenset({"blog.publish_article", "products.manage_storefront_media"})
)
MODERATOR_PERMISSIONS = (
    _view("reviews", "review", "reviewcomment")
    | _view("blog", "comment", "article")
    | frozenset({"reviews.moderate_reviews", "blog.moderate_article_comments"})
)
ANALYST_PERMISSIONS = frozenset({"dashboard.view_sensitive_analytics"})

ROLE_DEFINITIONS = {
    "Product Manager": PRODUCT_MANAGER_PERMISSIONS,
    "Order Manager": ORDER_MANAGER_PERMISSIONS,
    "Support Manager": SUPPORT_MANAGER_PERMISSIONS,
    "Content Manager": CONTENT_MANAGER_PERMISSIONS,
    "Moderator": MODERATOR_PERMISSIONS,
    "Analyst": ANALYST_PERMISSIONS,
}

ROLE_DESCRIPTIONS = {
    "Product Manager": _("Products, variants, stock, prices and catalog operations"),
    "Order Manager": _("Order fulfillment and operational Order handling"),
    "Support Manager": _("Customer Support Tickets and Help Center"),
    "Content Manager": _("Articles, site pages and storefront editorial media"),
    "Moderator": _("Reviews and Article Comment moderation"),
    "Analyst": _("Read-only business analytics"),
}

# These are presentation labels only.  Group names above remain stable
# canonical identifiers used by seeding and authorization tests.
ROLE_LABELS = {
    "Product Manager": _("Product Manager"),
    "Order Manager": _("Order Manager"),
    "Support Manager": _("Support Manager"),
    "Content Manager": _("Content Manager"),
    "Moderator": _("Moderator"),
    "Analyst": _("Analyst"),
}


def role_display_label(role_name):
    return ROLE_LABELS.get(role_name, role_name)

# This is maintained explicitly, so unrelated database permissions never grant entry.
DASHBOARD_CAPABILITY_PERMISSIONS = frozenset(chain.from_iterable(ROLE_DEFINITIONS.values()))
