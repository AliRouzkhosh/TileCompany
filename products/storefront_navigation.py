"""Canonical, bounded payload for the public Shop navigation."""

from django.conf import settings
from django.utils.translation import get_language, gettext as _
from types import SimpleNamespace

from .represented_catalog import (
    represented_application_areas, represented_brands, represented_category_hierarchy,
    represented_navigation_categories, shop_filter_url,
)


CATEGORY_ROW_LIMIT = 60
APPLICATION_AREA_ROW_LIMIT = 40
BRAND_ROW_LIMIT = 10
REQUEST_CACHE_ATTRIBUTE = "_storefront_navigation_payload"
DISCOVERY_CACHE_ATTRIBUTE = "_storefront_discovery_data"


def prepare_storefront_discovery(request):
    """Load the public discovery rows once for all consumers in this request."""
    cached = getattr(request, DISCOVERY_CACHE_ATTRIBUTE, None)
    if cached is not None:
        return cached
    config = settings.STOREFRONT_MEGA_MENU
    application_config = next(item for item in config if item["kind"] == "application_areas")
    categories, category_counts = represented_navigation_categories(limit=CATEGORY_ROW_LIMIT)
    cached = SimpleNamespace(
        categories=categories,
        category_counts=category_counts,
        application_areas=represented_application_areas(
            application_config,
            row_limit=APPLICATION_AREA_ROW_LIMIT,
        ),
        brands=represented_brands(limit=BRAND_ROW_LIMIT),
    )
    setattr(request, DISCOVERY_CACHE_ATTRIBUTE, cached)
    return cached


def _localized_label(item, language_code):
    if language_code.startswith("fa") and item.name_fa:
        return item.name_fa
    return item.name


def _category_section(rows, direct_counts, language_code, config):
    """Build visible roots and children, retaining parents represented by descendants."""
    represented, descendant_counts, by_parent = represented_category_hierarchy(rows, direct_counts)

    def item(category):
        return {
            "label": _localized_label(category, language_code),
            "url": shop_filter_url(categories=category.slug),
            "children": [
                item(child) for child in by_parent.get(category.id, ())
                if child.id in represented
            ],
        }

    items, remaining = [], config.get("total_limit", 16)
    for root in by_parent[None][:config.get("root_limit", 8)]:
        if root.id not in represented or remaining <= 0:
            continue
        root_item = item(root)
        child_limit = min(config.get("child_limit", 4), max(remaining - 1, 0))
        root_item["children"] = root_item["children"][:child_limit]
        remaining -= 1 + len(root_item["children"])
        items.append(root_item)
    return {"kind": "categories", "code": "categories", "label": _("Categories"), "items": items}


def _application_area_section(rows, language_code):
    return {
        "kind": "application_areas", "code": "shop-by-space", "label": _("Shop by Space"),
        "items": [
            {
                "label": row["name_fa"] if language_code.startswith("fa") and row["name_fa"] else row["name"],
                "url": shop_filter_url(
                    product_type=row["product_type_code"],
                    **{f"attr_{row['definition_code']}": row["code"]},
                ),
            }
            for row in rows
        ],
    }


def _brand_section(rows, language_code):
    return {
        "kind": "brands", "code": "brands", "label": _("Brands"),
        "items": [
            {"label": _localized_label(row, language_code), "url": shop_filter_url(brand=row.slug)}
            for row in rows
        ],
    }


def build_storefront_navigation(request):
    """Return a request-cached public Shop payload with four bounded queries."""
    cached = getattr(request, REQUEST_CACHE_ATTRIBUTE, None)
    if cached is not None:
        return cached

    config = settings.STOREFRONT_MEGA_MENU
    category_config = next(item for item in config if item["kind"] == "categories")
    language_code = get_language() or settings.LANGUAGE_CODE
    discovery = prepare_storefront_discovery(request)

    sections = {
        "categories": _category_section(discovery.categories, discovery.category_counts, language_code, category_config),
        "application_areas": _application_area_section(discovery.application_areas, language_code),
        "brands": _brand_section(discovery.brands, language_code),
    }
    payload = {
        "sections": [sections[item["kind"]] for item in config],
        "view_all_url": shop_filter_url(),
    }
    setattr(request, REQUEST_CACHE_ATTRIBUTE, payload)
    return payload
