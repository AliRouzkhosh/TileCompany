"""ProductType-neutral catalog search composed with an isolated Tile adapter."""

import logging
from decimal import Decimal, InvalidOperation

from django.db.models import (
    Case, CharField, Exists, F, IntegerField, OuterRef, Q, Subquery, Value,
    When,
)

from ..models import (
    Product, ProductAttributeValue, ProductVariant, VariantAttributeValue,
    VariantPrice,
)
from .tile_compat import (
    compatibility_fallback_search,
    legacy_search_with_source,
    tile_core_q,
    tile_value_q,
)


logger = logging.getLogger(__name__)


GENERIC_CORE_FIELDS = (
    "name", "name_fa", "sku",
    "description", "description_fa", "brand__name", "brand__name_fa",
    "categories__name", "categories__name_fa",
)
GENERIC_SEARCH_SOURCE = "generic"


def normalize_terms(query):
    return [word for word in " ".join((query or "").split()).split(" ") if word]


def _core_q(term, *, prefix=""):
    query = Q()
    for field in GENERIC_CORE_FIELDS:
        query |= Q(**{f"{prefix}{field}__icontains": term})
    return query


def _numeric_q(term):
    query = Q()
    try:
        number = Decimal(term)
        query |= Q(decimal_value=number)
        if number == number.to_integral_value():
            query |= Q(integer_value=int(number))
    except (InvalidOperation, TypeError, ValueError):
        pass
    return query


def _value_text_q(term):
    query = _numeric_q(term)
    query |= (
        Q(option_value__code__icontains=term)
        | Q(option_value__name__icontains=term)
        | Q(option_value__name_fa__icontains=term)
        | Q(text_value__icontains=term)
    )
    # Boolean free-text search is intentionally omitted; F3B owns Boolean discovery.
    return query


def _product_values(term, *, owner_ref):
    return ProductAttributeValue.objects.filter(
        product_id=OuterRef(owner_ref), binding__is_active=True,
        binding__is_searchable=True, binding__definition__is_active=True,
    ).filter(_value_text_q(term) | tile_value_q(term))


def _variant_values(term):
    return VariantAttributeValue.objects.filter(
        variant_id=OuterRef("pk"), binding__is_active=True,
        binding__is_searchable=True, binding__definition__is_active=True,
    ).filter(_value_text_q(term) | tile_value_q(term))


def apply_search_terms_to_variants(variants, query):
    """Constrain Variant rows using the same mixed core/one-Variant token rule."""
    for term in normalize_terms(query):
        variants = variants.filter(
            _core_q(term, prefix="product__")
            | tile_core_q(term, prefix="product__")
            | Exists(_product_values(term, owner_ref="product_id"))
            | Q(sku__icontains=term)
            | Exists(_variant_values(term))
        )
    return variants


def _generic_search(products, query, *, currency=None):
    terms = normalize_terms(query)
    if not terms:
        return products

    # Preserve the former whole-query SKU short-circuit and make Variant identity explicit.
    product_skus = products.filter(sku__icontains=query)
    if product_skus.exists():
        return product_skus.annotate(
            search_rank=Case(
                When(sku__iexact=query, then=Value(1000)),
                default=Value(950), output_field=IntegerField(),
            ),
            search_target_variant_id=Value(None, output_field=IntegerField()),
            search_source=Value(GENERIC_SEARCH_SOURCE, output_field=CharField()),
        ).order_by("-search_rank", "pk")

    sku_variants = ProductVariant.objects.filter(
        product_id=OuterRef("pk"), product__is_active=True, is_active=True,
        sku__icontains=query,
    ).order_by(
        Case(When(sku__iexact=query, then=Value(0)), default=Value(1)),
        "sort_order", "id",
    )
    sku_products = products.filter(Exists(sku_variants))
    if sku_products.exists():
        return sku_products.annotate(
            search_rank=Value(900, output_field=IntegerField()),
            search_target_variant_id=Subquery(sku_variants.values("id")[:1]),
            search_source=Value(GENERIC_SEARCH_SOURCE, output_field=CharField()),
        ).order_by("-search_rank", "pk")

    core_only = products
    candidates = ProductVariant.objects.filter(
        product_id=OuterRef("pk"), product__is_active=True, is_active=True,
    )
    any_variant_specific = Q()
    for term in terms:
        core_only = core_only.filter(
            _core_q(term) | tile_core_q(term)
            | Exists(_product_values(term, owner_ref="pk"))
        )
        variant_specific = Q(sku__icontains=term) | Exists(_variant_values(term))
        any_variant_specific |= variant_specific

    candidates = apply_search_terms_to_variants(candidates, query)

    target = candidates.filter(any_variant_specific)
    if currency:
        price = VariantPrice.objects.filter(
            variant_id=OuterRef("pk"), currency=str(currency),
        )
        target = target.annotate(
            search_price=Subquery(price.values("amount")[:1])
        ).order_by(F("search_price").asc(nulls_last=True), "sort_order", "id")
    else:
        target = target.order_by("sort_order", "id")

    product_attr_full = _product_values(query, owner_ref="pk")
    variant_attr_full = VariantAttributeValue.objects.filter(
        variant__product_id=OuterRef("pk"), variant__is_active=True,
        binding__is_active=True, binding__is_searchable=True,
        binding__definition__is_active=True,
    ).filter(_value_text_q(query))

    return products.filter(
        Q(pk__in=core_only.values("pk")) | Exists(candidates)
    ).annotate(
        search_rank=Case(
            When(name__iexact=query, then=Value(850)),
            When(name__icontains=query, then=Value(800)),
            When(Exists(product_attr_full), then=Value(700)),
            When(Exists(variant_attr_full), then=Value(600)),
            When(Q(description__icontains=query) | Q(description_fa__icontains=query), then=Value(400)),
            default=Value(300), output_field=IntegerField(),
        ),
        search_target_variant_id=Subquery(target.values("id")[:1]),
        search_source=Value(GENERIC_SEARCH_SOURCE, output_field=CharField()),
    ).distinct().order_by("-search_rank", "-created_at", "pk")


def search_products_in_queryset(
    products, query, *, currency=None, compatibility_fallback=True,
):
    """Search a caller-owned Product universe with complete-source fallback."""
    query = " ".join((query or "").split())
    if not query:
        return products
    type_ids = set(products.order_by().values_list("product_type_id", flat=True).distinct())
    if None in type_ids:
        if any(value is not None for value in type_ids):
            logger.error("generic_search_unusable cause=mixed_missing_product_type")
            return products.none()
        logger.error("generic_search_fallback cause=legacy_products_without_product_type")
        return legacy_search_with_source(query, universe=products)
    try:
        return _generic_search(products, query, currency=currency)
    except Exception as error:
        if not compatibility_fallback:
            raise
        fallback = compatibility_fallback_search(products, query)
        if fallback is not None:
            logger.exception(
                "generic_search_tile_fallback cause=%s", error,
                extra={"event": "generic_search_tile_fallback", "cause": str(error)},
            )
            return fallback
        logger.exception(
            "generic_search_failed_closed cause=%s", error,
            extra={"event": "generic_search_failed_closed", "cause": str(error)},
        )
        return products.none()


def search_products(query, *, currency=None, compatibility_fallback=True):
    return search_products_in_queryset(
        Product.objects.filter(is_active=True), query, currency=currency,
        compatibility_fallback=compatibility_fallback,
    )
