"""Generic Product suggestions composed with complete legacy fallbacks."""

import logging

from ..models import Brand, Color, Design, Finish, Material, Product, Size, Usage
from ..presentation import get_localized_value
from .engine import (
    GENERIC_SEARCH_SOURCE, search_products,
)
from .tile_compat import TILE_PRODUCT_TYPE_CODE, legacy_search_products
from ..selectors import annotate_product_price_range
from ..catalog_presentation import (
    GENERIC_SOURCE, prepare_product_card_presentations,
)


logger = logging.getLogger(__name__)


class AutocompleteIntegrityError(RuntimeError):
    """A complete generic autocomplete response cannot be represented."""


# Legacy providers are retained only for complete legacy autocomplete fallback.
def brands(*, tile_only=False):
    items = Brand.objects.filter(products__is_active=True)
    if tile_only:
        items = items.filter(products__product_type__code=TILE_PRODUCT_TYPE_CODE)
    return list(items.distinct().order_by("name"))


def materials(*, tile_only=False):
    items = Material.objects.filter(products__is_active=True)
    if tile_only:
        items = items.filter(products__product_type__code=TILE_PRODUCT_TYPE_CODE)
    return list(items.distinct().order_by("name"))


def finishes(*, tile_only=False):
    items = Finish.objects.filter(variants__is_active=True, variants__product__is_active=True)
    if tile_only:
        items = items.filter(variants__product__product_type__code=TILE_PRODUCT_TYPE_CODE)
    return list(items.distinct().order_by("name"))


def colors(*, tile_only=False):
    items = Color.objects.filter(variants__is_active=True, variants__product__is_active=True)
    if tile_only:
        items = items.filter(variants__product__product_type__code=TILE_PRODUCT_TYPE_CODE)
    return list(items.distinct().order_by("name"))


def designs(*, tile_only=False):
    items = Design.objects.filter(products__is_active=True)
    if tile_only:
        items = items.filter(products__product_type__code=TILE_PRODUCT_TYPE_CODE)
    return list(items.distinct().order_by("name"))


def usages(*, tile_only=False):
    items = Usage.objects.filter(products__is_active=True)
    if tile_only:
        items = items.filter(products__product_type__code=TILE_PRODUCT_TYPE_CODE)
    return list(items.distinct().order_by("name"))


def sizes(*, tile_only=False):
    items = Size.objects.filter(variants__is_active=True, variants__product__is_active=True)
    if tile_only:
        items = items.filter(variants__product__product_type__code=TILE_PRODUCT_TYPE_CODE)
    return list(items.distinct().order_by("length_mm", "width_mm", "name"))


def _render_product_suggestions(queryset, currency, *, generic, limit):
    queryset = annotate_product_price_range(
        queryset.select_related("brand", "product_type").prefetch_related(
            "categories", "materials"
        ),
        currency,
    )[:limit]
    products = list(queryset)
    if generic:
        if any(
            getattr(product, "search_source", None) != GENERIC_SEARCH_SOURCE
            for product in products
        ):
            raise AutocompleteIntegrityError(
                "search result source is not wholly generic"
            )
        prepare_product_card_presentations(products)
        if any(
            product.catalog_presentation.source != GENERIC_SOURCE
            for product in products
        ):
            raise AutocompleteIntegrityError(
                "generic Product-card presentation used compatibility fallback"
            )

    results = []
    for product in products:
        category_names = ", ".join(
            get_localized_value(category, "name") for category in product.categories.all()
        )
        if generic:
            attribute_values = [
                ", ".join(value.display_value for value in attribute.values)
                for attribute in product.catalog_presentation.card_attributes
            ]
            subtitle = " • ".join(filter(None, [*attribute_values, category_names]))
        else:
            material_names = ", ".join(
                get_localized_value(material, "name") for material in product.materials.all()
            )
            subtitle = " • ".join(filter(None, [
                get_localized_value(product.brand, "name") if product.brand else "",
                material_names, category_names,
            ]))

        url = product.get_absolute_url()
        target = getattr(product, "search_target_variant_id", None)
        if target:
            url = f"{url}?variant={target}"
        results.append({
            "type": "product",
            "name": get_localized_value(product, "name"),
            "subtitle": subtitle,
            "url": url,
            "price": (
                f"{product.requested_currency_min_price:.2f}"
                if product.requested_currency_min_price is not None else None
            ),
            "currency": str(currency),
            "image": product.image.url if product.image else None,
        })
    return results


def generic_product_suggestions(query, currency, limit=8):
    """Strict generic suggestion builder used by the all-or-nothing bundle."""
    queryset = search_products(
        query, currency=currency, compatibility_fallback=False,
    )
    return _render_product_suggestions(
        queryset, currency, generic=True, limit=limit,
    )


def legacy_product_suggestions(query, currency, limit=8, *, tile_only=False):
    universe = Product.objects.filter(is_active=True)
    if tile_only:
        universe = universe.filter(product_type__code=TILE_PRODUCT_TYPE_CODE)
    queryset = legacy_search_products(query, universe=universe)
    return _render_product_suggestions(
        queryset, currency, generic=False, limit=limit,
    )


def product_suggestions(query, currency, limit=8):
    states = set(
        Product.objects.filter(is_active=True)
        .values_list("product_type_id", flat=True).distinct()
    )
    if states and states == {None}:
        return legacy_product_suggestions(query, currency, limit)
    if None in states:
        logger.error("generic_autocomplete_failed_closed cause=mixed_missing_product_type")
        return []
    try:
        return generic_product_suggestions(query, currency, limit)
    except Exception as error:
        has_tile = Product.objects.filter(
            is_active=True, product_type__code=TILE_PRODUCT_TYPE_CODE,
        ).exists()
        if has_tile:
            logger.exception(
                "generic_autocomplete_tile_fallback cause=%s", error,
                extra={"event": "generic_autocomplete_tile_fallback", "cause": str(error)},
            )
            return legacy_product_suggestions(
                query, currency, limit, tile_only=True,
            )
        logger.exception(
            "generic_autocomplete_failed_closed cause=%s", error,
            extra={"event": "generic_autocomplete_failed_closed", "cause": str(error)},
        )
        return []
