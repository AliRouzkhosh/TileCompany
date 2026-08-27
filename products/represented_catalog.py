"""Shared bounded selectors for public catalog discovery surfaces."""

from collections import defaultdict
from urllib.parse import urlencode

from django.db.models import Count, Q
from django.urls import reverse

from .models import AttributeOption, Brand, Category, Product


def public_catalog_products():
    return Product.objects.filter(is_active=True, product_type__is_active=True)


def shop_filter_url(**params):
    base_url = reverse("products:product_list")
    return f"{base_url}?{urlencode(params)}" if params else base_url


def represented_navigation_categories(*, limit=60):
    """Return navigable categories and direct public membership counts in two queries."""
    rows = list(
        Category.objects.filter(is_active=True, show_in_navigation=True)
        .only("id", "parent_id", "name", "name_fa", "slug", "sort_order", "storefront_image")
        .order_by("sort_order", "name", "id")[:limit]
    )
    counts = dict(
        public_catalog_products().filter(categories__isnull=False).order_by()
        .values("categories__id").annotate(total=Count("pk", distinct=True))
        .values_list("categories__id", "total")
    )
    return rows, counts


def represented_category_hierarchy(rows, direct_counts):
    """Return represented IDs, descendant-aware counts, and children by parent."""
    by_parent = defaultdict(list)
    by_id = {row.id: row for row in rows}
    for row in rows:
        by_parent[row.parent_id].append(row)
    counts = {category_id: direct_counts.get(category_id, 0) for category_id in by_id}
    represented = {category_id for category_id, count in counts.items() if count}
    for category_id in tuple(represented):
        parent = by_id.get(category_id)
        while parent and parent.parent_id is not None:
            parent = by_id.get(parent.parent_id)
            if parent is None:
                break
            represented.add(parent.id)
            counts[parent.id] = counts.get(parent.id, 0) + direct_counts.get(category_id, 0)
    return represented, counts, by_parent


def represented_application_areas(config, *, row_limit=40):
    rows = list(
        AttributeOption.objects.filter(
            definition__code=config["definition_code"], is_active=True,
            product_values__binding__product_type__code=config["product_type_code"],
            product_values__binding__is_active=True,
            product_values__product__in=public_catalog_products(),
        ).values("id", "definition__code", "code", "name", "name_fa", "sort_order", "presentation_metadata")
        .annotate(public_product_count=Count("product_values__product_id", distinct=True))
        .order_by("sort_order", "name", "code")[:row_limit]
    )
    return [
        {**row, "definition_code": row["definition__code"], "product_type_code": config["product_type_code"]}
        for row in rows if not (row["presentation_metadata"] or {}).get("internal")
    ][:config.get("limit", 8)]


def represented_brands(*, limit=10):
    return list(
        Brand.objects.filter(products__in=public_catalog_products()).annotate(
            public_product_count=Count(
                "products", filter=Q(products__is_active=True, products__product_type__is_active=True), distinct=True,
            ),
        ).only("id", "slug", "name", "name_fa")
        .order_by("-public_product_count", "name", "slug")[:limit]
    )
