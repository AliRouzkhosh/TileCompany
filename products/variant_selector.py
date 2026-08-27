"""Schema-driven browser Variant selector composition.

Commerce facts remain owned by ProductVariant/VariantPrice; this module only
joins them to the generic dimension read contract.
"""

from decimal import Decimal
from types import MappingProxyType

from django.utils.translation import get_language

from .catalog_read import read_product_detail
from .commerce_presentation import (
    prepare_variant_operational_facts,
    serialize_selling_unit,
)


TILE_LEGACY_QUERY_ALIASES = MappingProxyType({
    "color": "color", "size": "size", "finish": "finish",
})


def matching_variant_rows(rows, selection):
    return [
        row for row in rows
        if all(row.get("attributes", {}).get(code) == value for code, value in selection.items() if value)
    ]


def available_option_codes(rows, selection, group_codes, group_code):
    """Return values represented by any active selector Variant.

    ``selection`` and visual ``group_codes`` remain accepted for compatibility,
    but availability is deliberately independent of both.
    """
    return {
        row["attributes"][group_code]
        for row in rows
        if row.get("is_active", True)
        and group_code in row.get("attributes", {})
    }


def resolve_clicked_variant(rows, selection, definition_code, option_code):
    """Resolve the best active Variant while making the clicked value mandatory.

    Ranking is deterministic: preserve the most other current selections, then
    prefer a priced row, then ProductVariant sort order, then stable Variant ID.
    """
    candidates = [
        row for row in rows
        if row.get("is_active", True)
        and row.get("attributes", {}).get(definition_code) == option_code
    ]
    if not candidates:
        return None

    def rank(row):
        preserved = sum(
            1
            for code, value in selection.items()
            if code != definition_code
            and value
            and row.get("attributes", {}).get(code) == value
        )
        return (
            -preserved,
            -int(bool(row.get("is_priced"))),
            row.get("sort_order", 0),
            row.get("id", 0),
        )

    return min(candidates, key=rank)


def option_context_compatible(rows, selection, definition_code, option_code):
    """Whether an option can coexist with every other current selection."""
    other = {
        code: value
        for code, value in selection.items()
        if code != definition_code and value
    }
    return any(
        row.get("is_active", True)
        and row.get("attributes", {}).get(definition_code) == option_code
        and all(
            row.get("attributes", {}).get(code) == value
            for code, value in other.items()
        )
        for row in rows
    )


def option_visual_state(rows, selection, definition_code, option_code):
    """Mirror the browser's selected/contextual/global option-state contract."""
    globally_available = option_code in available_option_codes(
        rows, selection, (), definition_code
    )
    if not globally_available:
        return "globally_unavailable"
    if selection.get(definition_code) == option_code:
        return "selected"
    if option_context_compatible(
        rows, selection, definition_code, option_code
    ):
        return "compatible"
    return "contextually_incompatible"


def _discount(price):
    if not price or not price.compare_at_amount:
        return 0
    return round((1 - (price.amount / price.compare_at_amount)) * 100)


def _images(variant, product_images, product_image):
    variant_images = [image.image.url for image in variant.gallery.all()]
    images = variant_images or product_images or ([product_image.url] if product_image else [])
    return images, images[0] if images else ""


def _serialize_detail_value(value):
    """Serialize one already-interpreted generic value for browser rendering."""

    return {
        "definition_code": value.definition_code,
        "label": value.label,
        "display_value": value.display_value,
        "is_variant_dimension": value.is_variant_dimension,
        "sort_order": value.sort_order,
    }


def _variant_detail_specifications(dto):
    specification_codes = {
        value.definition_code for value in dto.specification_attributes
    }
    return [
        _serialize_detail_value(value)
        for value in dto.attribute_values
        if (
            value.is_variant_dimension
            or value.definition_code in specification_codes
        )
        and not value.is_internal
        and (value.is_variant_dimension or not value.is_retired)
    ]


def _serialize_operational_facts(
    product,
    variant,
    *,
    product_type_code,
    source="generic",
):
    return [
        {"label": fact.label, "value": fact.value}
        for fact in prepare_variant_operational_facts(
            product,
            variant,
            product_type_code=product_type_code,
            source=source,
        )
    ]


def _normalized_request(request, groups, *, product_type_code):
    codes = {group["code"] for group in groups}
    generic = {
        code: request.GET.get(f"attr_{code}", "").strip()
        for code in codes
        if request.GET.get(f"attr_{code}", "").strip()
    }
    if generic:
        return generic, "generic"
    if product_type_code == "tile":
        legacy = {
            code: request.GET.get(alias, "").strip()
            for alias, code in TILE_LEGACY_QUERY_ALIASES.items()
            if code in codes and request.GET.get(alias, "").strip()
        }
        if legacy:
            return legacy, "legacy_alias"
    return {}, "default"


def compose_generic_variant_selector(*, product, request, currency, variants, price_getter):
    detail = read_product_detail(product)
    active_ids = {variant.pk for variant in variants}
    dto_by_id = {dto.id: dto for dto in detail.variants if dto.id in active_ids}
    used = {group.definition_code: set() for group in detail.variant_groups}
    for dto in dto_by_id.values():
        for code, value in dto.attributes.items():
            used.setdefault(code, set()).add(value)
    groups = []
    for group in detail.variant_groups:
        represented = used.get(group.definition_code, set())
        options = [
            {
                "code": option.code,
                "label": option.label,
                "presentation_metadata": dict(option.presentation_metadata),
                "is_retired": option.is_retired,
                "available": True,
            }
            for option in group.options
            if option.code in represented
        ]
        if not options:
            continue
        groups.append({
            "code": group.definition_code,
            "label": group.label,
            "required": group.required,
            "sort_order": group.sort_order,
            "options": options,
        })
    product_images = (
        ([product.image.url] if product.image else [])
        + [image.image.url for image in product.gallery.all()]
    )
    rows = []
    variant_by_id = {variant.pk: variant for variant in variants}
    for variant in variants:
        dto = dto_by_id.get(variant.pk)
        if dto is None:
            continue
        price = price_getter(variant, currency)
        images, image = _images(variant, product_images, product.image)
        rows.append({
            "id": variant.pk, "sku": variant.sku,
            "sort_order": variant.sort_order,
            "attributes": dict(dto.attributes),
            "combination_signature": dto.combination_signature,
            "amount": str(price.amount) if price else None,
            "compare_at_amount": str(price.compare_at_amount) if price and price.compare_at_amount else None,
            "currency": currency, "price_unit": variant.price_unit,
            "price_unit_display": serialize_selling_unit(variant.price_unit, language=get_language())["price_label"],
            "selling_unit": serialize_selling_unit(variant.price_unit, language=get_language()),
            "is_priced": price is not None, "discount_percent": _discount(price),
            "stock": variant.stock, "is_active": variant.is_active,
            "images": images, "image": image,
            "detail_specifications": _variant_detail_specifications(dto),
            "operational_facts": _serialize_operational_facts(
                product,
                variant,
                product_type_code=detail.product_type_code,
            ),
        })
    row_by_id = {row["id"]: row for row in rows}
    explicit_variant = request.GET.get("variant")
    normalized, parameter_source = _normalized_request(
        request, groups, product_type_code=detail.product_type_code
    )
    selected = None
    if explicit_variant is not None:
        try:
            selected = row_by_id.get(int(explicit_variant))
        except (TypeError, ValueError):
            selected = None
        selection = dict(selected["attributes"]) if selected else {}
        parameter_source = "variant" if selected else "invalid_variant"
    else:
        matches = [
            row for row in rows
            if all(row["attributes"].get(code) == value for code, value in normalized.items())
        ]
        if normalized and not matches:
            selection = normalized
        else:
            selected = next((row for row in matches if row["is_priced"]), matches[0] if matches else None)
            selection = dict(selected["attributes"]) if selected else normalized
    initial_variant = variant_by_id.get(selected["id"]) if selected else None
    prices = [Decimal(row["amount"]) for row in rows if row["amount"] is not None]
    compares = [Decimal(row["compare_at_amount"]) for row in rows if row["compare_at_amount"]]
    return {
        "variants": variants, "variants_data": rows, "variant_groups": groups,
        "selector_source": "generic", "parameter_source": parameter_source,
        "preselected": selection,
        "preselected_variant_id": selected["id"] if selected else None,
        "initial_price": price_getter(initial_variant, currency) if initial_variant else None,
        "initial_discount_percent": selected["discount_percent"] if selected else 0,
        "initial_price_unit": selected["price_unit_display"] if selected else "",
        "initial_selling_unit": selected["selling_unit"] if selected else {},
        "best_discount": max((row["discount_percent"] for row in rows), default=0),
        "min_variant_price": min(prices, default=None),
        "max_variant_original": max(compares, default=None),
        "storefront_currency": currency,
        "detail_contract": detail,
    }


def adapt_complete_legacy_selector(context, *, product=None):
    """Isolated whole-contract Tile fallback; never mixes generic rows/groups."""
    group_specs = (
        ("size", "Size", context["variant_options"]["sizes"]),
        ("color", "Color", context["variant_options"]["colors"]),
        ("finish", "Finish", context["variant_options"]["finishes"]),
    )
    context["variant_groups"] = [
        {
            "code": code, "label": label, "required": False, "sort_order": index,
            "options": [{"code": item.slug, "label": str(item), "presentation_metadata": {}, "is_retired": False, "available": True} for item in items],
        }
        for index, (code, label, items) in enumerate(group_specs)
        if items
    ]
    for row in context["variants_data"]:
        row["attributes"] = {
            code: row[code] for code in ("size", "color", "finish") if row.get(code)
        }
        row["combination_signature"] = None
        row["is_active"] = True
        row["selling_unit"] = serialize_selling_unit(row["price_unit"])
        row["detail_specifications"] = [
            {
                "definition_code": group["code"],
                "label": group["label"],
                "display_value": next(
                    (
                        option["label"]
                        for option in group["options"]
                        if option["code"] == row["attributes"].get(group["code"])
                    ),
                    "",
                ),
                "is_variant_dimension": True,
                "sort_order": group["sort_order"],
            }
            for group in context["variant_groups"]
            if row["attributes"].get(group["code"])
        ]
        variant = next(
            (item for item in context["variants"] if item.pk == row["id"]),
            None,
        )
        row["operational_facts"] = (
            _serialize_operational_facts(
                product,
                variant,
                product_type_code="tile",
                source="legacy_fallback",
            )
            if product is not None and variant is not None
            else []
        )
    selected = next(
        (
            row for row in context["variants_data"]
            if row["id"] == context.get("preselected_variant_id")
        ),
        None,
    )
    context["initial_selling_unit"] = selected["selling_unit"] if selected else {}
    context["selector_source"] = "legacy"
    context["parameter_source"] = "legacy_fallback"
    return context


def add_tile_legacy_payload_aliases(context):
    """Temporary response-key compatibility, isolated from generic matching."""
    if context.get("selector_source") != "generic":
        return context
    labels = {
        group["code"]: {option["code"]: option["label"] for option in group["options"]}
        for group in context["variant_groups"]
    }
    for row in context["variants_data"]:
        for code in TILE_LEGACY_QUERY_ALIASES.values():
            value = row["attributes"].get(code, "")
            row[code] = value
            row[f"{code}_label"] = labels.get(code, {}).get(value, "")
    return context
