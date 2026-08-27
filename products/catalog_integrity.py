"""Read-only catalog preflight helpers shared by future catalog operations."""

from django.db.models import Q

from .models import Product


def active_products_with_invalid_type():
    """Return publicly active Products lacking an active ProductType.

    This is intentionally diagnostic-only: callers must decide whether to
    correct, unpublish, or otherwise preserve malformed historical rows.
    """

    return Product.objects.filter(is_active=True).filter(
        Q(product_type__isnull=True) | Q(product_type__is_active=False)
    ).select_related("product_type").order_by("pk")


def active_untyped_products():
    """Compatibility-focused subset used by generic catalog fail-closed checks."""

    return active_products_with_invalid_type().filter(product_type__isnull=True)
