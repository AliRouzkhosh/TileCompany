"""Reusable autocomplete contract used by non-view callers."""

import logging

from ..currency import Currency
from ..models import Product
from .categories import generic_grouped_suggestions, legacy_grouped_suggestions
from .suggestions import generic_product_suggestions, legacy_product_suggestions
from .tile_compat import TILE_PRODUCT_TYPE_CODE


logger = logging.getLogger(__name__)


def autocomplete(query, currency=Currency.USD):
    query = " ".join((query or "").split())
    states = set(
        Product.objects.filter(is_active=True)
        .values_list("product_type_id", flat=True).distinct()
    )
    if states and states == {None}:
        return {
            "groups": legacy_grouped_suggestions(query),
            "products": (
                legacy_product_suggestions(query, currency)
                if len(query) >= 2 else []
            ),
        }
    if None in states:
        logger.error("generic_autocomplete_failed_closed cause=mixed_missing_product_type")
        return {"groups": [], "products": []}

    try:
        return {
            "groups": generic_grouped_suggestions(query),
            "products": (
                generic_product_suggestions(query, currency)
                if len(query) >= 2 else []
            ),
        }
    except Exception as error:
        has_tile = Product.objects.filter(
            is_active=True, product_type__code=TILE_PRODUCT_TYPE_CODE,
        ).exists()
        if has_tile:
            logger.exception(
                "generic_autocomplete_tile_fallback cause=%s", error,
                extra={"event": "generic_autocomplete_tile_fallback", "cause": str(error)},
            )
            return {
                "groups": legacy_grouped_suggestions(query, tile_only=True),
                "products": (
                    legacy_product_suggestions(
                        query, currency, tile_only=True,
                    ) if len(query) >= 2 else []
                ),
            }
        logger.exception(
            "generic_autocomplete_failed_closed cause=%s", error,
            extra={"event": "generic_autocomplete_failed_closed", "cause": str(error)},
        )
        return {"groups": [], "products": []}
