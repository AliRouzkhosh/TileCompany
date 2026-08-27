"""Production composition for generic catalog presentation contracts.

F1B.5 keeps core Product/commerce objects intact while supplying generic
attribute DTOs to approved templates.  Legacy construction is a whole-bundle
availability fallback only; it never repairs or mixes generic state.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, replace
from typing import Iterable

from django.utils.translation import gettext as _
from django.utils.translation import override

from .catalog_read import (
    AttributeGroupDTO,
    AttributeValueDTO,
    CARD_ATTRIBUTE_LIMIT,
    CatalogReadIntegrityError,
    GenericProductDetailDTO,
    ProductAttributeBundleDTO,
    read_product_attribute_bundles,
    read_product_attributes,
    read_product_detail,
    specification_bundle,
)
from .models import Product, ProductVariant, VariantPrice
from .presentation import get_localized_value


logger = logging.getLogger(__name__)

GENERIC_SOURCE = "generic"
LEGACY_FALLBACK_SOURCE = "legacy_fallback"
# Card/list scarcity is intentionally a presentation concern.  Product Detail
# may later surface exact resolved-Variant stock independently.
LOW_STOCK_THRESHOLD = 5


@dataclass(frozen=True)
class ProductPresentationDTO:
    product_id: int
    product_type_code: str | None
    source: str
    card_attributes: tuple[AttributeGroupDTO, ...]
    specification_attributes: tuple[AttributeGroupDTO, ...]
    selected_variant_specifications: tuple[AttributeValueDTO, ...]
    category_labels: tuple[str, ...]
    brand_label: str = ""

    @property
    def used_fallback(self):
        return self.source == LEGACY_FALLBACK_SOURCE


@dataclass(frozen=True, slots=True)
class ProductCardCommerceDTO:
    """Localized card-only commerce facts selected in one bounded batch."""

    selling_unit_label: str
    compare_at_amount: object | None
    low_stock_count: int | None = None
    low_stock_unit: str = ""


def _attach_product_card_commerce(products, *, currency=None, language=None):
    """Attach selling-unit and compare-at facts without per-card ORM reads."""

    if not products or currency is None:
        for product in products:
            product.card_commerce = ProductCardCommerceDTO("", None)
        return

    from .commerce_presentation import selling_unit_presentation
    from .currency import Currency

    currency = Currency(currency).value
    prices_by_product = {product.pk: [] for product in products}
    missing_product_ids = []

    for product in products:
        variants = getattr(product, "_prefetched_objects_cache", {}).get("variants")
        if variants is None:
            missing_product_ids.append(product.pk)
            continue
        for variant in variants:
            price = next(
                (
                    row
                    for row in getattr(variant, "currency_prices", ())
                    if row.currency == currency
                ),
                None,
            )
            if price is not None:
                prices_by_product[product.pk].append(price)

    if missing_product_ids:
        rows = (
            VariantPrice.objects.filter(
                variant__product_id__in=missing_product_ids,
                variant__product__is_active=True,
                variant__is_active=True,
                currency=currency,
            )
            .select_related("variant")
            .order_by("variant__product_id", "amount", "variant__sort_order", "variant_id")
        )
        for price in rows:
            prices_by_product[price.variant.product_id].append(price)

    for product in products:
        prices = sorted(
            prices_by_product[product.pk],
            key=lambda row: (row.amount, row.variant.sort_order, row.variant_id),
        )
        if not prices:
            product.card_commerce = ProductCardCommerceDTO("", None)
            continue

        unit_codes = {price.variant.price_unit for price in prices}
        unit_label = ""
        low_stock_unit = ""
        if len(unit_codes) == 1:
            selling_unit = selling_unit_presentation(next(iter(unit_codes)), language=language)
            unit_label = selling_unit.price_label
        else:
            selling_unit = None

        # A Product card is never allowed to infer scarcity from its lowest
        # price/representative Variant.  Only active Variants with a current
        # price in this storefront currency are sellable in this context.
        total_sellable_stock = sum(price.variant.stock for price in prices)
        low_stock_count = (
            total_sellable_stock
            if 0 < total_sellable_stock < LOW_STOCK_THRESHOLD and product.is_active
            else None
        )
        if low_stock_count and selling_unit is not None:
            low_stock_unit = selling_unit.noun_for(low_stock_count)

        minimum = prices[0]
        compare_at = (
            minimum.compare_at_amount
            if minimum.compare_at_amount is not None
            and minimum.compare_at_amount > minimum.amount
            else None
        )
        product.card_commerce = ProductCardCommerceDTO(
            unit_label, compare_at, low_stock_count, low_stock_unit
        )


def _translated_label(label, *, language=None):
    if language:
        with override(language):
            return str(_(label))
    return str(_(label))


def _legacy_option_group(
    code,
    label,
    values,
    *,
    scope,
    sort_order,
    cardinality="single",
    is_variant_dimension=False,
    language=None,
):
    resolved = []
    for value in values:
        if value is None:
            continue
        resolved.append(
            AttributeValueDTO(
                definition_code=code,
                scope=scope,
                is_variant_dimension=is_variant_dimension,
                label=_translated_label(label, language=language),
                data_type="option",
                value_code=value.slug,
                raw_value=value.slug,
                display_value=get_localized_value(value, "name", language=language),
                unit=None,
                sort_order=sort_order,
                is_retired=False,
            )
        )
    if not resolved:
        return None
    return AttributeGroupDTO(
        definition_code=code,
        scope=scope,
        is_variant_dimension=is_variant_dimension,
        label=_translated_label(label, language=language),
        data_type="option",
        required=False,
        cardinality=cardinality,
        sort_order=sort_order,
        values=tuple(resolved),
    )


def _legacy_product_groups(product, *, language=None):
    """Build the complete Tile-era Product bundle used only on read failure."""
    candidates = (
        _legacy_option_group(
            "brand", "Brand", [product.brand] if product.brand_id else [],
            scope="product", sort_order=1, language=language,
        ),
        _legacy_option_group(
            "material", "Material", list(product.materials.all()),
            scope="product", sort_order=2, cardinality="multiple", language=language,
        ),
        _legacy_option_group(
            "design", "Design", list(product.designs.all()),
            scope="product", sort_order=3, cardinality="multiple", language=language,
        ),
        _legacy_option_group(
            "usage", "Usage", list(product.usages.all()),
            scope="product", sort_order=4, cardinality="multiple", language=language,
        ),
    )
    return tuple(group for group in candidates if group is not None)


def _legacy_variant_values(selected_variant, *, language=None):
    if selected_variant is None:
        return ()
    groups = (
        _legacy_option_group(
            "color", "Color", [selected_variant.color] if selected_variant.color_id else [],
            scope="variant", sort_order=1, is_variant_dimension=True, language=language,
        ),
        _legacy_option_group(
            "size", "Size", [selected_variant.size] if selected_variant.size_id else [],
            scope="variant", sort_order=2, is_variant_dimension=True, language=language,
        ),
        _legacy_option_group(
            "finish", "Finish", [selected_variant.finish] if selected_variant.finish_id else [],
            scope="variant", sort_order=3, is_variant_dimension=True, language=language,
        ),
    )
    return tuple(
        value
        for group in groups
        if group is not None
        for value in group.values
    )


def _category_labels(product, *, language=None):
    return tuple(
        get_localized_value(category, "name", language=language)
        for category in product.categories.all()
    )


def _generic_presentation(
    product,
    bundle,
    *,
    detail: GenericProductDetailDTO | None = None,
    selected_variant=None,
    language=None,
):
    specification_attributes = tuple(
        replace(
            group,
            values=tuple(value for value in group.values if not value.is_retired),
        )
        for group in bundle.specification_attributes
        if any(not value.is_retired for value in group.values)
    )
    variant_specifications = ()
    if detail is not None and selected_variant is not None:
        generic_variant = next(
            (variant for variant in detail.variants if variant.id == selected_variant.pk),
            None,
        )
        if generic_variant is None:
            raise CatalogReadIntegrityError(
                [
                    f"Selected Variant ID {selected_variant.pk} is absent from the "
                    f"generic detail contract for Product ID {product.pk}."
                ]
            )
        configured_codes = {
            value.definition_code
            for value in specification_bundle(
                detail, selected_variant=generic_variant
            ).variant_attributes
        }
        variant_specifications = tuple(
            value
            for value in generic_variant.attribute_values
            if (
                value.is_variant_dimension
                or value.definition_code in configured_codes
            )
            and (value.is_variant_dimension or not value.is_retired)
        )
    return ProductPresentationDTO(
        product_id=product.pk,
        product_type_code=bundle.product_type_code,
        source=GENERIC_SOURCE,
        card_attributes=bundle.card_attributes,
        specification_attributes=specification_attributes,
        selected_variant_specifications=variant_specifications,
        category_labels=_category_labels(product, language=language) if detail is not None else (),
        brand_label=(
            get_localized_value(product.brand, "name", language=language)
            if product.brand_id else ""
        ),
    )


def _legacy_fallback_presentation(
    product,
    *,
    selected_variant=None,
    include_detail=False,
    language=None,
):
    groups = _legacy_product_groups(product, language=language)
    by_code = {group.definition_code: group for group in groups}
    card_attributes = tuple(
        by_code[code] for code in ("brand", "material") if code in by_code
    )[:CARD_ATTRIBUTE_LIMIT]
    return ProductPresentationDTO(
        product_id=product.pk,
        product_type_code=(product.product_type.code if product.product_type_id else None),
        source=LEGACY_FALLBACK_SOURCE,
        card_attributes=card_attributes,
        specification_attributes=groups,
        selected_variant_specifications=(
            _legacy_variant_values(selected_variant, language=language)
            if include_detail else ()
        ),
        category_labels=(
            _category_labels(product, language=language) if include_detail else ()
        ),
        brand_label=(
            get_localized_value(product.brand, "name", language=language)
            if product.brand_id else ""
        ),
    )


def _log_fallback(product, error):
    logger.error(
        "generic_product_presentation_fallback product_id=%s sku=%s cause=%s",
        product.pk,
        product.sku,
        error,
        extra={
            "event": "generic_product_presentation_fallback",
            "product_id": product.pk,
            "product_sku": product.sku,
            "catalog_read_cause": str(error),
        },
    )


def prepare_product_card_presentations(
    products: Iterable[Product], *, language=None, currency=None
):
    """Attach card DTOs using one generic batch in the normal path."""
    product_list = list(products)
    if not product_list:
        _attach_product_card_commerce(
            product_list, currency=currency, language=language
        )
        return product_list
    try:
        bundles = read_product_attribute_bundles(product_list, language=language)
    except CatalogReadIntegrityError:
        # Degraded transition path: isolate the bad record without mixing bundles.
        for product in product_list:
            try:
                bundle = read_product_attributes(product, language=language)
                presentation = _generic_presentation(
                    product, bundle, language=language
                )
            except CatalogReadIntegrityError as error:
                _log_fallback(product, error)
                presentation = _legacy_fallback_presentation(
                    product, language=language
                )
            product.catalog_presentation = presentation
        _attach_product_card_commerce(
            product_list, currency=currency, language=language
        )
        return product_list

    for product in product_list:
        product.catalog_presentation = _generic_presentation(
            product, bundles[product.pk], language=language
        )
    _attach_product_card_commerce(
        product_list, currency=currency, language=language
    )
    return product_list


def compose_product_detail_presentation(
    product: Product,
    *,
    selected_variant: ProductVariant | None,
    detail: GenericProductDetailDTO | None = None,
    language=None,
):
    """Compose Product specifications once, using only an explicit variant."""
    try:
        detail = detail or read_product_detail(product, language=language)
        return _generic_presentation(
            product,
            detail.product_attributes,
            detail=detail,
            selected_variant=selected_variant,
            language=language,
        )
    except CatalogReadIntegrityError as error:
        _log_fallback(product, error)
        return _legacy_fallback_presentation(
            product,
            selected_variant=selected_variant,
            include_detail=True,
            language=language,
        )
