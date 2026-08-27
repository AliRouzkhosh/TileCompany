"""Legacy-authoritative Tile search compatibility boundary.

Nothing in this module is part of the reusable generic search contract.  It
exists while Tile Products continue to be authored through legacy fields.
"""

from functools import reduce
from operator import or_

from django.db.models import CharField, Q, Value

from ..models import Product, ProductVariant
from .synonyms import SYNONYMS


TILE_PRODUCT_TYPE_CODE = "tile"
LEGACY_TILE_SEARCH_SOURCE = "legacy_tile"

LEGACY_PRODUCT_FIELDS = (
    "name", "name_fa", "categories__name", "categories__name_fa",
    "materials__name", "materials__name_fa", "usages__name", "usages__name_fa",
    "brand__name", "brand__name_fa", "series_name", "series_name_fa",
    "designs__name", "designs__name_fa", "description", "description_fa", "sku",
)
LEGACY_VARIANT_FIELDS = (
    "size__name", "size__name_fa", "color__name", "color__name_fa",
    "finish__name", "finish__name_fa", "sku",
)


def _fields_q(fields, value, *, prefix=""):
    return reduce(
        or_,
        (Q(**{f"{prefix}{field}__icontains": value}) for field in fields),
    )


def normalize_terms(query):
    return [word for word in " ".join((query or "").split()).split(" ") if word]


def tile_compatibility_terms(term):
    """Tile-only domain expansions retained from the pre-framework search."""
    return tuple(dict.fromkeys(SYNONYMS.get(term.lower(), [term])))


def tile_core_q(term, *, prefix=""):
    """Tile-only legacy core facts that supplement generic Product facts."""
    query = Q()
    for synonym in tile_compatibility_terms(term):
        query |= _fields_q(
            (
                "name", "name_fa", "description", "description_fa",
                "categories__name", "categories__name_fa",
                "series_name", "series_name_fa",
            ),
            synonym,
            prefix=prefix,
        )
    return Q(**{f"{prefix}product_type__code": TILE_PRODUCT_TYPE_CODE}) & query


def tile_value_q(term):
    """Apply retained Tile synonyms only to values owned by the Tile schema."""
    query = Q()
    for synonym in tile_compatibility_terms(term):
        query |= (
            Q(option_value__code__icontains=synonym)
            | Q(option_value__name__icontains=synonym)
            | Q(option_value__name_fa__icontains=synonym)
            | Q(text_value__icontains=synonym)
        )
    return Q(binding__product_type__code=TILE_PRODUCT_TYPE_CODE) & query


def legacy_search_products(query, *, universe=None):
    """Run the complete pre-F3C Tile contract inside a caller-owned universe."""
    universe = universe if universe is not None else Product.objects.filter(is_active=True)
    universe = universe.filter(is_active=True)
    query = (query or "").strip()
    if not query:
        return universe

    products = universe.filter(sku__icontains=query)
    if products.exists():
        return products.distinct()

    variants = ProductVariant.objects.filter(
        product_id__in=universe.values("pk"), is_active=True, sku__icontains=query,
    )
    if variants.exists():
        return universe.filter(id__in=variants.values("product_id")).distinct()

    product_matches = universe
    variant_matches = ProductVariant.objects.filter(
        product_id__in=universe.values("pk"), is_active=True,
        product__is_active=True,
    )
    for term in normalize_terms(query):
        product_q = Q()
        variant_q = Q()
        for synonym in tile_compatibility_terms(term):
            product_q |= _fields_q(LEGACY_PRODUCT_FIELDS, synonym)
            variant_q |= _fields_q(LEGACY_VARIANT_FIELDS, synonym)
        product_matches = product_matches.filter(product_q)
        variant_matches = variant_matches.filter(variant_q)

    ids = set(product_matches.values_list("id", flat=True))
    ids.update(variant_matches.values_list("product_id", flat=True))
    return universe.filter(id__in=ids).distinct()


def legacy_search_with_source(query, *, universe):
    return legacy_search_products(query, universe=universe).annotate(
        search_source=Value(LEGACY_TILE_SEARCH_SOURCE, output_field=CharField()),
    )


def compatibility_fallback_search(products, query):
    """Return a complete Tile-only fallback, or ``None`` if unsupported."""
    universe = products.filter(product_type__code=TILE_PRODUCT_TYPE_CODE)
    if not universe.exists():
        return None
    return legacy_search_with_source(query, universe=universe)
