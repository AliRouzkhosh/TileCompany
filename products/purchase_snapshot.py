"""Versioned, immutable purchase snapshot contracts.

The writer freezes catalog truth into ``OrderItem.variant_snapshot``.  The
parser deliberately consumes JSON only, so historical presentation never
depends on mutable catalog rows.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal
from types import MappingProxyType
from typing import Any, Iterable, Mapping

from django.core.exceptions import ValidationError
from django.utils.translation import override

from .catalog_domain import (
    VALUE_FIELD_BY_DATA_TYPE,
    VALUE_FIELDS,
    serialize_combination_signature,
    validate_scalar_against_rules,
)
from .commerce_presentation import selling_unit_presentation
from .models import (
    AttributeDefinition,
    Product,
    ProductAttributeValue,
    ProductTypeAttribute,
    ProductVariant,
    VariantAttributeValue,
)


SNAPSHOT_VERSION = 2
TILE_PRODUCT_TYPE_CODE = "tile"


class PurchaseSnapshotIntegrityError(ValueError):
    """Current catalog data cannot produce complete historical truth."""

    def __init__(self, issues: str | Iterable[str]):
        self.issues = (issues,) if isinstance(issues, str) else tuple(issues)
        super().__init__("; ".join(self.issues))


class UnsupportedPurchaseSnapshotVersion(PurchaseSnapshotIntegrityError):
    """A stored snapshot uses a version this application cannot interpret."""


@dataclass(frozen=True, slots=True)
class PurchasedAttributeValueDTO:
    option_code: str | None
    raw_value: str | int | bool
    display_default: str
    display_fa: str
    is_internal: bool = False


@dataclass(frozen=True, slots=True)
class PurchasedAttributeDTO:
    definition_code: str
    label_default: str
    label_fa: str
    scope: str
    data_type: str
    unit: str | None
    is_dimension: bool
    sort_order: int
    values: tuple[PurchasedAttributeValueDTO, ...]


@dataclass(frozen=True, slots=True)
class PurchasedItemSnapshotDTO:
    version: int | None
    source: str
    product: Mapping[str, Any]
    variant: Mapping[str, Any]
    product_attributes: tuple[PurchasedAttributeDTO, ...]
    variant_attributes: tuple[PurchasedAttributeDTO, ...]
    operational: Mapping[str, Any]
    original_snapshot: Mapping[str, Any]


def _deep_freeze(value):
    if isinstance(value, Mapping):
        return MappingProxyType({key: _deep_freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_deep_freeze(item) for item in value)
    return value


def _frozen_mapping(value: Mapping[str, Any]) -> Mapping[str, Any]:
    return _deep_freeze(value)


def _labels(obj, field="name"):
    default = str(getattr(obj, field, "") or "")
    fa = str(getattr(obj, f"{field}_fa", "") or "").strip() or default
    return default, fa


def _canonical_decimal(value: Decimal) -> str:
    """Serialize Decimal without passing through a binary float."""

    return format(value, "f")


def _scalar_display(raw_value, definition):
    if definition.data_type == AttributeDefinition.DataType.BOOLEAN:
        default = "Yes" if raw_value else "No"
        with override("fa"):
            # This is application vocabulary, not a mutable catalog label.
            from django.utils.translation import gettext as _

            fa = str(_("Yes") if raw_value else _("No"))
    else:
        default = str(raw_value)
        fa = default
    if definition.unit and definition.data_type in {
        AttributeDefinition.DataType.INTEGER,
        AttributeDefinition.DataType.DECIMAL,
    }:
        default = f"{default} {definition.unit}"
        fa = f"{fa} {definition.unit}"
    return default, fa


def _serialize_value(value, binding):
    definition = binding.definition
    populated = [field for field in VALUE_FIELDS if getattr(value, field) is not None]
    expected = VALUE_FIELD_BY_DATA_TYPE[definition.data_type]
    if populated != [expected]:
        raise PurchaseSnapshotIntegrityError(
            f"Value ID {value.pk} for {definition.code!r} does not use its "
            f"{definition.data_type!r} typed carrier."
        )

    if definition.data_type == AttributeDefinition.DataType.OPTION:
        option = value.option_value
        if option.definition_id != definition.pk:
            raise PurchaseSnapshotIntegrityError(
                f"Value ID {value.pk} uses an option from another definition."
            )
        display_default, display_fa = _labels(option)
        return {
            "option_code": option.code,
            "is_internal": bool((option.presentation_metadata or {}).get("internal")),
            "raw_value": option.code,
            "display_default": display_default,
            "display_fa": display_fa,
        }

    raw_value = getattr(value, expected)
    try:
        validate_scalar_against_rules(definition, raw_value)
    except ValidationError as error:
        raise PurchaseSnapshotIntegrityError(
            f"Value ID {value.pk} violates validation for {definition.code!r}: {error}."
        ) from error
    if definition.data_type == AttributeDefinition.DataType.DECIMAL:
        raw_value = _canonical_decimal(raw_value)
    display_default, display_fa = _scalar_display(raw_value, definition)
    return {
        "option_code": None,
        "raw_value": raw_value,
        "display_default": display_default,
        "display_fa": display_fa,
    }


def _serialize_attribute(binding, rows):
    definition = binding.definition
    label_default, label_fa = _labels(definition)
    if binding.cardinality == ProductTypeAttribute.Cardinality.SINGLE and len(rows) != 1:
        raise PurchaseSnapshotIntegrityError(
            f"Snapshot binding {definition.code!r} requires exactly one stored value."
        )
    return {
        "definition_code": definition.code,
        "label_default": label_default,
        "label_fa": label_fa,
        "scope": binding.scope,
        "data_type": definition.data_type,
        "unit": definition.unit or None,
        "is_dimension": binding.is_variant_dimension,
        "sort_order": binding.sort_order,
        "values": [_serialize_value(value, binding) for value in rows],
    }


def _selling_unit_snapshot(code):
    try:
        default = selling_unit_presentation(code, language="en")
        fa = selling_unit_presentation(code, language="fa")
    except ValueError as error:
        raise PurchaseSnapshotIntegrityError(str(error)) from error
    return {
        "code": default.code,
        "price_label_default": default.price_label,
        "price_label_fa": fa.price_label,
        "singular_noun_default": default.singular_noun,
        "singular_noun_fa": fa.singular_noun,
        "plural_noun_default": default.plural_noun,
        "plural_noun_fa": fa.plural_noun,
    }


def _tile_operational_snapshot(variant):
    facts = {}
    for field in ("length_mm", "width_mm", "tiles_per_box"):
        value = getattr(variant, field)
        if value is not None:
            facts[field] = value
    for field in ("thickness_mm", "coverage_per_box_m2"):
        value = getattr(variant, field)
        if value is not None:
            facts[field] = _canonical_decimal(value)
    if not facts:
        return {}
    facts["display"] = {
        "thickness": variant.display_thickness,
        "coverage": variant.display_coverage,
        "tiles_per_box": variant.display_tiles_per_box,
    }
    return {"tile": facts}


def _ordered_rows(rows, *, product_scope):
    if product_scope:
        return sorted(
            rows,
            key=lambda row: (
                row.sort_order,
                row.option_value.sort_order if row.option_value_id else 0,
                row.option_value.code if row.option_value_id else "",
                row.pk,
            ),
        )
    return sorted(rows, key=lambda row: row.pk)


def _validate_value_ownership(value, product_type_id, scope):
    binding = value.binding
    if binding.product_type_id != product_type_id or binding.scope != scope:
        raise PurchaseSnapshotIntegrityError(
            f"Value ID {value.pk} belongs to an inapplicable {scope!r} schema binding."
        )
    if not binding.is_active:
        raise PurchaseSnapshotIntegrityError(
            f"Value ID {value.pk} belongs to inactive binding {binding.definition.code!r}."
        )


def _prepare_generic_v2_snapshots(variants, products_by_id):
    if not variants:
        return {}
    product_ids = {variant.product_id for variant in variants}
    products = [products_by_id[product_id] for product_id in product_ids]
    issues = [
        f"Product ID {product.pk} has no ProductType."
        for product in products
        if not product.product_type_id
    ]
    if issues:
        raise PurchaseSnapshotIntegrityError(issues)

    type_ids = {product.product_type_id for product in products}
    bindings = list(
        ProductTypeAttribute.objects.filter(product_type_id__in=type_ids)
        .select_related("definition")
        .order_by("product_type_id", "scope", "sort_order", "definition__code", "id")
    )
    active_bindings = [binding for binding in bindings if binding.is_active]
    bindings_by_type_scope = defaultdict(list)
    for binding in active_bindings:
        bindings_by_type_scope[(binding.product_type_id, binding.scope)].append(binding)

    product_values = defaultdict(list)
    for value in (
        ProductAttributeValue.objects.filter(product_id__in=product_ids)
        .select_related("binding__definition", "option_value")
        .order_by("product_id", "binding__sort_order", "sort_order", "id")
    ):
        product_values[value.product_id].append(value)

    variant_ids = [variant.pk for variant in variants]
    variant_values = defaultdict(list)
    for value in (
        VariantAttributeValue.objects.filter(variant_id__in=variant_ids)
        .select_related("binding__definition", "option_value")
        .order_by("variant_id", "binding__sort_order", "id")
    ):
        variant_values[value.variant_id].append(value)

    snapshots = {}
    for variant in variants:
        product = products_by_id[variant.product_id]
        product_type = product.product_type
        product_bindings = bindings_by_type_scope[
            (product.product_type_id, ProductTypeAttribute.Scope.PRODUCT)
        ]
        variant_bindings = bindings_by_type_scope[
            (product.product_type_id, ProductTypeAttribute.Scope.VARIANT)
        ]
        product_binding_ids = {binding.pk for binding in product_bindings}
        variant_binding_ids = {binding.pk for binding in variant_bindings}

        for value in product_values[product.pk]:
            _validate_value_ownership(
                value, product.product_type_id, ProductTypeAttribute.Scope.PRODUCT
            )
            if value.binding_id not in product_binding_ids:
                raise PurchaseSnapshotIntegrityError(
                    f"Product ID {product.pk} has a value outside its active schema."
                )
        for value in variant_values[variant.pk]:
            _validate_value_ownership(
                value, product.product_type_id, ProductTypeAttribute.Scope.VARIANT
            )
            if value.binding_id not in variant_binding_ids:
                raise PurchaseSnapshotIntegrityError(
                    f"Variant ID {variant.pk} has a value outside its active schema."
                )

        product_rows_by_binding = defaultdict(list)
        for value in product_values[product.pk]:
            product_rows_by_binding[value.binding_id].append(value)
        variant_rows_by_binding = defaultdict(list)
        for value in variant_values[variant.pk]:
            variant_rows_by_binding[value.binding_id].append(value)

        product_attributes = []
        for binding in product_bindings:
            rows = _ordered_rows(
                product_rows_by_binding[binding.pk], product_scope=True
            )
            if binding.is_required and not rows:
                raise PurchaseSnapshotIntegrityError(
                    f"Product ID {product.pk} is missing required attribute "
                    f"{binding.definition.code!r}."
                )
            if binding.include_in_purchase_snapshot and rows:
                product_attributes.append(_serialize_attribute(binding, rows))

        variant_attributes = []
        dimension_pairs = []
        for binding in variant_bindings:
            rows = _ordered_rows(
                variant_rows_by_binding[binding.pk], product_scope=False
            )
            included = binding.is_variant_dimension or binding.include_in_purchase_snapshot
            if binding.is_variant_dimension and len(rows) != 1:
                raise PurchaseSnapshotIntegrityError(
                    f"Variant ID {variant.pk} must have exactly one value for dimension "
                    f"{binding.definition.code!r}."
                )
            if binding.is_required and not rows:
                raise PurchaseSnapshotIntegrityError(
                    f"Variant ID {variant.pk} is missing required attribute "
                    f"{binding.definition.code!r}."
                )
            if not included or not rows:
                continue
            entry = _serialize_attribute(binding, rows)
            variant_attributes.append(entry)
            if binding.is_variant_dimension:
                option_code = entry["values"][0]["option_code"]
                if option_code is None:
                    raise PurchaseSnapshotIntegrityError(
                        f"Variant dimension {binding.definition.code!r} is not option-valued."
                    )
                dimension_pairs.append((binding.definition.code, option_code))

        expected_signature = serialize_combination_signature(dimension_pairs)
        if variant.combination_signature != expected_signature:
            raise PurchaseSnapshotIntegrityError(
                f"Variant ID {variant.pk} signature {variant.combination_signature!r} "
                f"does not match purchased dimensions {expected_signature!r}."
            )

        product_type_default, product_type_fa = _labels(product_type)
        product_default, product_fa = _labels(product)
        snapshots[variant.pk] = {
            "version": SNAPSHOT_VERSION,
            "product": {
                "id": product.pk,
                "product_type": {
                    "code": product_type.code,
                    "label_default": product_type_default,
                    "label_fa": product_type_fa,
                },
                "sku": product.sku,
                "name_default": product_default,
                "name_fa": product_fa,
            },
            "variant": {
                "id": variant.pk,
                "sku": variant.sku,
                "combination_signature": variant.combination_signature,
                "selling_unit": _selling_unit_snapshot(variant.price_unit),
            },
            "attributes": {
                "product": product_attributes,
                "variant": variant_attributes,
            },
            "operational": (
                _tile_operational_snapshot(variant)
                if product_type.code == TILE_PRODUCT_TYPE_CODE
                else {}
            ),
        }
    return snapshots


def prepare_generic_v2_purchase_snapshots(variants: Iterable[ProductVariant]):
    """Build v2 snapshots for many Variants with a bounded EAV query budget."""

    variants = list(variants)
    if not variants:
        return {}
    products_by_id = Product.objects.filter(
        pk__in={variant.product_id for variant in variants}
    ).select_related("product_type").in_bulk()
    missing = sorted({variant.product_id for variant in variants} - products_by_id.keys())
    if missing:
        raise PurchaseSnapshotIntegrityError(f"Missing Product rows: {missing}.")
    return _prepare_generic_v2_snapshots(variants, products_by_id)


def build_generic_v2_purchase_snapshot(variant: ProductVariant):
    return prepare_generic_v2_purchase_snapshots([variant])[variant.pk]


def build_legacy_tile_purchase_snapshot(variant: ProductVariant):
    """Retain the compatibility-protected, unversioned Tile contract exactly."""

    return variant.order_snapshot


def prepare_purchase_snapshots(variants: Iterable[ProductVariant]):
    """Dispatch Tile/untyped compatibility rows to legacy and non-Tile to v2."""

    variants = list(variants)
    if not variants:
        return {}
    products_by_id = Product.objects.filter(
        pk__in={variant.product_id for variant in variants}
    ).select_related("product_type").in_bulk()
    missing = sorted({variant.product_id for variant in variants} - products_by_id.keys())
    if missing:
        raise PurchaseSnapshotIntegrityError(f"Missing Product rows: {missing}.")

    generic = []
    result = {}
    for variant in variants:
        product = products_by_id[variant.product_id]
        if not product.product_type_id or product.product_type.code == TILE_PRODUCT_TYPE_CODE:
            result[variant.pk] = build_legacy_tile_purchase_snapshot(variant)
        else:
            generic.append(variant)
    result.update(_prepare_generic_v2_snapshots(generic, products_by_id))
    return result


def _parse_value(value):
    if not isinstance(value, dict):
        raise PurchaseSnapshotIntegrityError("Attribute values must be JSON objects.")
    required = {"option_code", "raw_value", "display_default", "display_fa"}
    if not required.issubset(value):
        raise PurchaseSnapshotIntegrityError("A v2 attribute value is incomplete.")
    return PurchasedAttributeValueDTO(
        option_code=value["option_code"],
        raw_value=value["raw_value"],
        display_default=str(value["display_default"]),
        display_fa=str(value["display_fa"]),
        is_internal=bool(value.get("is_internal", False)),
    )


def _parse_attribute(entry, expected_scope):
    if not isinstance(entry, dict):
        raise PurchaseSnapshotIntegrityError("Attribute entries must be JSON objects.")
    required = {
        "definition_code", "label_default", "label_fa", "scope", "data_type",
        "unit", "is_dimension", "sort_order", "values",
    }
    if not required.issubset(entry) or entry["scope"] != expected_scope:
        raise PurchaseSnapshotIntegrityError("A v2 attribute entry is incomplete or mis-scoped.")
    if not isinstance(entry["values"], list) or not entry["values"]:
        raise PurchaseSnapshotIntegrityError("A v2 attribute must contain values.")
    return PurchasedAttributeDTO(
        definition_code=str(entry["definition_code"]),
        label_default=str(entry["label_default"]),
        label_fa=str(entry["label_fa"]),
        scope=str(entry["scope"]),
        data_type=str(entry["data_type"]),
        unit=entry["unit"],
        is_dimension=bool(entry["is_dimension"]),
        sort_order=int(entry["sort_order"]),
        values=tuple(_parse_value(value) for value in entry["values"]),
    )


def _parse_v2(snapshot):
    for key in ("product", "variant", "attributes", "operational"):
        if not isinstance(snapshot.get(key), dict):
            raise PurchaseSnapshotIntegrityError(f"V2 snapshot block {key!r} is missing.")
    product = snapshot["product"]
    variant = snapshot["variant"]
    if not {"id", "product_type", "sku", "name_default", "name_fa"}.issubset(product):
        raise PurchaseSnapshotIntegrityError("V2 Product identity is incomplete.")
    if not isinstance(product["product_type"], dict) or not {
        "code", "label_default", "label_fa"
    }.issubset(product["product_type"]):
        raise PurchaseSnapshotIntegrityError("V2 ProductType identity is incomplete.")
    if not {"id", "sku", "combination_signature", "selling_unit"}.issubset(variant):
        raise PurchaseSnapshotIntegrityError("V2 Variant identity is incomplete.")
    unit = variant["selling_unit"]
    if not isinstance(unit, dict) or not {
        "code", "price_label_default", "price_label_fa",
        "singular_noun_default", "singular_noun_fa",
        "plural_noun_default", "plural_noun_fa",
    }.issubset(unit):
        raise PurchaseSnapshotIntegrityError("V2 selling-unit identity is incomplete.")
    attributes = snapshot["attributes"]
    if not isinstance(attributes.get("product"), list) or not isinstance(
        attributes.get("variant"), list
    ):
        raise PurchaseSnapshotIntegrityError("V2 attribute collections are incomplete.")
    return PurchasedItemSnapshotDTO(
        version=SNAPSHOT_VERSION,
        source="v2",
        product=_frozen_mapping(product),
        variant=_frozen_mapping(variant),
        product_attributes=tuple(
            _parse_attribute(entry, ProductTypeAttribute.Scope.PRODUCT)
            for entry in attributes["product"]
        ),
        variant_attributes=tuple(
            _parse_attribute(entry, ProductTypeAttribute.Scope.VARIANT)
            for entry in attributes["variant"]
        ),
        operational=_frozen_mapping(snapshot["operational"]),
        original_snapshot=_frozen_mapping(snapshot),
    )


def _legacy_attribute(code, label, value, *, operational=False, scope=None):
    if not value:
        return None
    return PurchasedAttributeDTO(
        definition_code=code,
        label_default=label,
        label_fa=label,
        scope=(
            "operational"
            if operational
            else scope or ProductTypeAttribute.Scope.VARIANT
        ),
        data_type=AttributeDefinition.DataType.TEXT,
        unit=None,
        is_dimension=code in {"size", "finish", "color"},
        sort_order=0,
        values=(
            PurchasedAttributeValueDTO(
                option_code=None,
                raw_value=str(value),
                display_default=str(value),
                display_fa=str(value),
            ),
        ),
    )


def _parse_legacy(snapshot):
    attributes = tuple(
        entry
        for entry in (
            _legacy_attribute("size", "Size", snapshot.get("size")),
            _legacy_attribute(
                "material", "Material", snapshot.get("material"),
                scope=ProductTypeAttribute.Scope.PRODUCT,
            ),
            _legacy_attribute("finish", "Finish", snapshot.get("finish")),
            _legacy_attribute("color", "Color", snapshot.get("color")),
            _legacy_attribute("thickness", "Thickness", snapshot.get("thickness"), operational=True),
        )
        if entry is not None
    )
    operational = {
        key: snapshot[key]
        for key in ("thickness", "coverage", "tiles_per_box")
        if snapshot.get(key)
    }
    return PurchasedItemSnapshotDTO(
        version=None,
        source="legacy",
        product=_frozen_mapping({}),
        variant=_frozen_mapping({"sku": snapshot.get("sku", "")}),
        product_attributes=tuple(
            entry for entry in attributes if entry.definition_code == "material"
        ),
        variant_attributes=tuple(
            entry
            for entry in attributes
            if entry.scope == ProductTypeAttribute.Scope.VARIANT
        ),
        operational=_frozen_mapping(operational),
        original_snapshot=_frozen_mapping(snapshot),
    )


def parse_purchase_snapshot(snapshot: Mapping[str, Any]):
    """Normalize legacy or v2 JSON without consulting any ORM model."""

    if not isinstance(snapshot, Mapping):
        raise PurchaseSnapshotIntegrityError("Purchase snapshot must be a JSON object.")
    snapshot = dict(snapshot)
    version = snapshot.get("version")
    if version is None:
        return _parse_legacy(snapshot)
    if version == SNAPSHOT_VERSION:
        return _parse_v2(snapshot)
    raise UnsupportedPurchaseSnapshotVersion(
        f"Unsupported purchase snapshot version: {version!r}."
    )
