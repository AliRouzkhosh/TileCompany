"""Autocomplete groups with a complete legacy taxonomy fallback."""

from collections import defaultdict
import logging
from urllib.parse import urlencode

from django.db.models import Q
from django.urls import reverse
from django.utils.translation import gettext as _

from .suggestions import (
    brands, colors, designs, finishes, materials, sizes, usages,
)
from ..models import (
    AttributeOption, Category, Product, ProductAttributeValue,
    VariantAttributeValue,
)
from ..presentation import get_localized_value
from .tile_compat import TILE_PRODUCT_TYPE_CODE


logger = logging.getLogger(__name__)


LEGACY_SEARCH_GROUPS = {
    "Brands": {"provider": brands, "filter": "brand"},
    "Materials": {"provider": materials, "filter": "material"},
    "Finishes": {"provider": finishes, "filter": "finish"},
    "Colors": {"provider": colors, "filter": "color"},
    "Sizes": {"provider": sizes, "filter": "size"},
    "Designs": {"provider": designs, "filter": "design"},
    "Usage": {"provider": usages, "filter": "usage"},
}


def legacy_grouped_suggestions(query, *, tile_only=False):
    query = (query or "").strip().lower()
    results = []
    for title, config in LEGACY_SEARCH_GROUPS.items():
        matches = []
        for item in config["provider"](tile_only=tile_only):
            label = get_localized_value(item, "name") if hasattr(item, "name") else item
            value = getattr(item, "slug", item)
            if not query or query in label.lower():
                matches.append({
                    "label": label, "value": value, "filter": config["filter"],
                    "url": f"{reverse('products:product_list')}?{urlencode({config['filter']: value})}",
                })
        if matches:
            results.append({
                "title": title, "count": len(matches),
                "items": sorted(matches, key=lambda item: item["label"]),
            })
    return results


def _generic_option_groups(query, limit):
    lookup = (
        Q(option_value__code__icontains=query)
        | Q(option_value__name__icontains=query)
        | Q(option_value__name_fa__icontains=query)
    ) if query else Q()
    product_ids = ProductAttributeValue.objects.filter(
        product__is_active=True, binding__is_active=True,
        binding__is_searchable=True, option_value__isnull=False,
    ).filter(lookup).values_list("option_value_id", flat=True)
    variant_ids = VariantAttributeValue.objects.filter(
        variant__is_active=True, variant__product__is_active=True,
        binding__is_active=True, binding__is_searchable=True,
        option_value__isnull=False,
    ).filter(lookup).values_list("option_value_id", flat=True)
    option_ids = set(product_ids[:limit]) | set(variant_ids[:limit])
    options = AttributeOption.objects.filter(pk__in=option_ids).select_related("definition").order_by(
        "definition__name", "sort_order", "name", "id",
    )[:limit]
    grouped = defaultdict(list)
    for option in options:
        code = option.definition.code
        grouped[(code, get_localized_value(option.definition, "name"))].append({
            "type": "attribute_option",
            "label": get_localized_value(option, "name"),
            "secondary_label": get_localized_value(option.definition, "name"),
            "value": option.code,
            "filter": f"attr_{code}",
            "url": f"{reverse('products:product_list')}?{urlencode({f'attr_{code}': option.code})}",
        })
    return [{"title": label, "count": len(items), "items": items} for (_, label), items in grouped.items()]


def _category_group(query, limit=5):
    categories = Category.objects.filter(is_active=True)
    if query:
        categories = categories.filter(Q(name__icontains=query) | Q(name_fa__icontains=query))
    items = [{
        "type": "category", "label": get_localized_value(category, "name"),
        "secondary_label": str(_("Category")), "value": category.slug,
        "filter": "category",
        "url": reverse("products:category_list", kwargs={"slug": category.slug}),
    } for category in categories.order_by("sort_order", "name", "id")[:limit]]
    return {"title": str(_("Categories")), "count": len(items), "items": items} if items else None


def generic_grouped_suggestions(query, limit=12):
    """Strict schema-driven groups; callers own compatibility fallback."""
    query = " ".join((query or "").split())
    groups = _generic_option_groups(query, limit)
    category = _category_group(query)
    if category:
        groups.append(category)
    return groups


def grouped_suggestions(query, limit=12):
    query = " ".join((query or "").split())
    states = set(
        Product.objects.filter(is_active=True)
        .values_list("product_type_id", flat=True).distinct()
    )
    if states and states == {None}:
        return legacy_grouped_suggestions(query)
    if None in states:
        logger.error("generic_autocomplete_failed_closed cause=mixed_missing_product_type")
        return []
    try:
        return generic_grouped_suggestions(query, limit)
    except Exception as error:
        has_tile = Product.objects.filter(
            is_active=True, product_type__code=TILE_PRODUCT_TYPE_CODE,
        ).exists()
        if has_tile:
            logger.exception(
                "generic_autocomplete_tile_fallback cause=%s", error,
                extra={"event": "generic_autocomplete_tile_fallback", "cause": str(error)},
            )
            return legacy_grouped_suggestions(query, tile_only=True)
        logger.exception(
            "generic_autocomplete_failed_closed cause=%s", error,
            extra={"event": "generic_autocomplete_failed_closed", "cause": str(error)},
        )
        return []
