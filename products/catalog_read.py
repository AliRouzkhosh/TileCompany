"""Side-effect-free generic catalog read contracts and batched adapters.

This module is shadow infrastructure.  It reads only the generic catalog
schema/value tables and deliberately has no fallback to Tile legacy fields.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, replace
from decimal import Decimal
from types import MappingProxyType
from typing import Any, Iterable, Mapping

from django.utils.translation import gettext as _
from django.utils.translation import override

from .catalog_domain import serialize_combination_signature
from .models import (
    AttributeDefinition,
    AttributeOption,
    Product,
    ProductAttributeValue,
    ProductType,
    ProductTypeAttribute,
    ProductTypeAttributeOption,
    ProductVariant,
    VariantAttributeValue,
)
from .presentation import get_localized_value


CARD_ATTRIBUTE_LIMIT = 2


class CatalogReadIntegrityError(RuntimeError):
    """Generic schema/value state cannot be represented safely."""

    def __init__(self, issues: Iterable[str]):
        self.issues = tuple(issues)
        super().__init__("; ".join(self.issues))


@dataclass(frozen=True)
class AttributeValueDTO:
    definition_code: str
    scope: str
    is_variant_dimension: bool
    label: str
    data_type: str
    value_code: str | None
    raw_value: str | int | Decimal | bool
    display_value: str
    unit: str | None
    sort_order: int
    is_retired: bool
    is_internal: bool = False


@dataclass(frozen=True)
class AttributeGroupDTO:
    definition_code: str
    scope: str
    is_variant_dimension: bool
    label: str
    data_type: str
    required: bool
    cardinality: str
    sort_order: int
    values: tuple[AttributeValueDTO, ...]


@dataclass(frozen=True)
class ProductAttributeBundleDTO:
    product_id: int
    product_type_code: str
    attributes: tuple[AttributeGroupDTO, ...]
    all_storefront_attributes: tuple[AttributeGroupDTO, ...]
    specification_attributes: tuple[AttributeGroupDTO, ...]
    card_attributes: tuple[AttributeGroupDTO, ...]
    snapshot_eligible_product_attributes: tuple[AttributeGroupDTO, ...]


@dataclass(frozen=True)
class VariantOptionDTO:
    code: str
    label: str
    presentation_metadata: Mapping[str, Any]
    is_retired: bool
    available: bool
    stable_id: int


@dataclass(frozen=True)
class VariantGroupDTO:
    definition_code: str
    label: str
    sort_order: int
    required: bool
    options: tuple[VariantOptionDTO, ...]


@dataclass(frozen=True)
class GenericVariantDTO:
    id: int
    sku: str
    combination_signature: str | None
    attributes: Mapping[str, str]
    attribute_values: tuple[AttributeValueDTO, ...]
    specification_attributes: tuple[AttributeValueDTO, ...]
    stock: int
    is_active: bool


@dataclass(frozen=True)
class GenericProductDetailDTO:
    product_id: int
    product_type_code: str
    product_attributes: ProductAttributeBundleDTO
    variant_groups: tuple[VariantGroupDTO, ...]
    variants: tuple[GenericVariantDTO, ...]


@dataclass(frozen=True)
class SpecificationBundleDTO:
    product_attributes: tuple[AttributeGroupDTO, ...]
    variant_attributes: tuple[AttributeValueDTO, ...]


@dataclass
class _ReadContext:
    products: list[Product]
    product_types: dict[int, ProductType]
    bindings_by_type_scope: dict[tuple[int, str], list[ProductTypeAttribute]]
    product_values: dict[int, list[ProductAttributeValue]]
    variants_by_product: dict[int, list[ProductVariant]]
    variant_values: dict[int, list[VariantAttributeValue]]
    options_by_definition: dict[int, list[AttributeOption]]
    restrictions_by_binding: dict[int, list[ProductTypeAttributeOption]]


def _immutable_mapping(value: Mapping[Any, Any]) -> Mapping[Any, Any]:
    return MappingProxyType(dict(value))


def _as_product_list(products: Product | Iterable[Product]) -> list[Product]:
    if isinstance(products, Product):
        return [products]
    return list(products)


def _load_context(
    products: Product | Iterable[Product],
    *,
    include_variants: bool,
    include_options: bool,
) -> _ReadContext:
    product_list = _as_product_list(products)
    if not product_list:
        return _ReadContext([], {}, {}, {}, {}, {}, {}, {})

    issues = [
        f"Product ID {product.pk} has no ProductType."
        for product in product_list
        if not product.product_type_id
    ]
    if issues:
        raise CatalogReadIntegrityError(issues)

    product_ids = [product.pk for product in product_list]
    type_ids = {product.product_type_id for product in product_list}
    product_types = {
        item.pk: item for item in ProductType.objects.filter(pk__in=type_ids)
    }
    missing_types = sorted(type_ids - product_types.keys())
    if missing_types:
        raise CatalogReadIntegrityError(
            [f"Missing ProductType rows: {missing_types}."]
        )

    bindings = list(
        ProductTypeAttribute.objects.filter(
            product_type_id__in=type_ids,
            is_active=True,
        )
        .select_related("definition")
        .order_by("product_type_id", "scope", "sort_order", "id")
    )
    bindings_by_type_scope: dict[tuple[int, str], list[ProductTypeAttribute]] = defaultdict(list)
    for binding in bindings:
        bindings_by_type_scope[(binding.product_type_id, binding.scope)].append(binding)

    product_values: dict[int, list[ProductAttributeValue]] = defaultdict(list)
    for value in (
        ProductAttributeValue.objects.filter(product_id__in=product_ids)
        .select_related("binding__definition", "option_value")
        .order_by("product_id", "binding__sort_order", "sort_order", "id")
    ):
        product_values[value.product_id].append(value)

    variants_by_product: dict[int, list[ProductVariant]] = defaultdict(list)
    variant_values: dict[int, list[VariantAttributeValue]] = defaultdict(list)
    if include_variants:
        variants = list(
            ProductVariant.objects.filter(product_id__in=product_ids)
            .order_by("product_id", "sort_order", "id")
        )
        for variant in variants:
            variants_by_product[variant.product_id].append(variant)
        variant_ids = [variant.pk for variant in variants]
        if variant_ids:
            for value in (
                VariantAttributeValue.objects.filter(variant_id__in=variant_ids)
                .select_related("binding__definition", "option_value")
                .order_by("variant_id", "binding__sort_order", "id")
            ):
                variant_values[value.variant_id].append(value)

    options_by_definition: dict[int, list[AttributeOption]] = defaultdict(list)
    restrictions_by_binding: dict[int, list[ProductTypeAttributeOption]] = defaultdict(list)
    if include_options:
        dimension_bindings = [binding for binding in bindings if binding.is_variant_dimension]
        definition_ids = {binding.definition_id for binding in dimension_bindings}
        for option in (
            AttributeOption.objects.filter(definition_id__in=definition_ids)
            .order_by("definition_id", "sort_order", "name", "id")
        ):
            options_by_definition[option.definition_id].append(option)
        binding_ids = [binding.pk for binding in dimension_bindings]
        if binding_ids:
            for restriction in (
                ProductTypeAttributeOption.objects.filter(binding_id__in=binding_ids)
                .select_related("option")
                .order_by("binding_id", "sort_order", "id")
            ):
                restrictions_by_binding[restriction.binding_id].append(restriction)

    return _ReadContext(
        products=product_list,
        product_types=product_types,
        bindings_by_type_scope=dict(bindings_by_type_scope),
        product_values=dict(product_values),
        variants_by_product=dict(variants_by_product),
        variant_values=dict(variant_values),
        options_by_definition=dict(options_by_definition),
        restrictions_by_binding=dict(restrictions_by_binding),
    )


def _format_decimal(value: Decimal) -> str:
    rendered = format(value, "f")
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    return rendered


def _resolve_value(value, binding, *, language=None) -> AttributeValueDTO:
    definition = binding.definition
    label = get_localized_value(definition, "name", language=language)
    data_type = definition.data_type
    option = value.option_value
    value_code = None
    is_retired = False
    is_internal = False

    if data_type == AttributeDefinition.DataType.OPTION:
        if option is None:
            raise CatalogReadIntegrityError(
                [f"Value ID {value.pk} for {definition.code} has no option carrier."]
            )
        if option.definition_id != definition.pk:
            raise CatalogReadIntegrityError(
                [f"Value ID {value.pk} uses an option from another definition."]
            )
        value_code = option.code
        raw_value = option.code
        display_value = get_localized_value(option, "name", language=language)
        is_retired = not option.is_active
        is_internal = bool((option.presentation_metadata or {}).get("internal"))
    elif data_type == AttributeDefinition.DataType.TEXT:
        raw_value = value.text_value
        display_value = raw_value
    elif data_type == AttributeDefinition.DataType.INTEGER:
        raw_value = value.integer_value
        display_value = str(raw_value)
    elif data_type == AttributeDefinition.DataType.DECIMAL:
        raw_value = value.decimal_value
        display_value = _format_decimal(raw_value) if raw_value is not None else ""
    elif data_type == AttributeDefinition.DataType.BOOLEAN:
        raw_value = value.boolean_value
        if raw_value is None:
            raise CatalogReadIntegrityError(
                [f"Value ID {value.pk} for {definition.code} has no boolean carrier."]
            )
        if language:
            with override(language):
                display_value = str(_("Yes")) if raw_value else str(_("No"))
        else:
            display_value = str(_("Yes")) if raw_value else str(_("No"))
    else:
        raise CatalogReadIntegrityError(
            [f"Definition {definition.code} has unsupported type {data_type}."]
        )

    if raw_value is None:
        raise CatalogReadIntegrityError(
            [f"Value ID {value.pk} has no {data_type} carrier."]
        )
    unit = definition.unit or None
    if unit and data_type in {
        AttributeDefinition.DataType.INTEGER,
        AttributeDefinition.DataType.DECIMAL,
    }:
        display_value = f"{display_value} {unit}"

    return AttributeValueDTO(
        definition_code=definition.code,
        scope=binding.scope,
        is_variant_dimension=binding.is_variant_dimension,
        label=label,
        data_type=data_type,
        value_code=value_code,
        raw_value=raw_value,
        display_value=display_value,
        unit=unit,
        sort_order=binding.sort_order,
        is_retired=is_retired,
        is_internal=is_internal,
    )


def _rows_for_binding(rows, binding):
    matching = [value for value in rows if value.binding_id == binding.pk]
    if rows and isinstance(rows[0], ProductAttributeValue):
        matching.sort(
            key=lambda value: (
                value.sort_order,
                value.option_value.sort_order if value.option_value_id else 0,
                value.option_value.code if value.option_value_id else "",
                value.pk,
            )
        )
    return matching


def _validate_owner_rows(owner_label, owner_type_id, scope, bindings, rows):
    active_ids = {binding.pk for binding in bindings}
    issues = []
    for value in rows:
        binding = value.binding
        if binding.product_type_id != owner_type_id or binding.scope != scope:
            issues.append(
                f"{owner_label} has value ID {value.pk} under an inapplicable binding."
            )
        elif not binding.is_active or binding.pk not in active_ids:
            issues.append(
                f"{owner_label} has unexpected value ID {value.pk} under inactive binding {binding.definition.code}."
            )
    if issues:
        raise CatalogReadIntegrityError(issues)


def _build_groups(owner_label, bindings, rows, *, language=None, enforce_required=True):
    groups = []
    for binding in bindings:
        matching = _rows_for_binding(rows, binding)
        if binding.cardinality == ProductTypeAttribute.Cardinality.SINGLE and len(matching) > 1:
            raise CatalogReadIntegrityError(
                [f"{owner_label} has multiple values for single attribute {binding.definition.code}."]
            )
        if enforce_required and binding.is_required and not matching:
            raise CatalogReadIntegrityError(
                [f"{owner_label} is missing required attribute {binding.definition.code}."]
            )
        if not matching:
            continue
        groups.append(
            AttributeGroupDTO(
                definition_code=binding.definition.code,
                scope=binding.scope,
                is_variant_dimension=binding.is_variant_dimension,
                label=get_localized_value(binding.definition, "name", language=language),
                data_type=binding.definition.data_type,
                required=binding.is_required,
                cardinality=binding.cardinality,
                sort_order=binding.sort_order,
                values=tuple(
                    _resolve_value(value, binding, language=language)
                    for value in matching
                ),
            )
        )
    return tuple(groups)


def public_attribute_groups(groups):
    """Remove internal readiness values at the public presentation boundary."""
    return tuple(
        replace(group, values=values)
        for group in groups
        if (values := tuple(value for value in group.values if not value.is_internal))
    )


def public_attribute_values(values):
    return tuple(value for value in values if not value.is_internal)


def _build_product_bundle(product, context, *, language=None):
    bindings = context.bindings_by_type_scope.get(
        (product.product_type_id, ProductTypeAttribute.Scope.PRODUCT), []
    )
    rows = context.product_values.get(product.pk, [])
    _validate_owner_rows(
        f"Product ID {product.pk}", product.product_type_id,
        ProductTypeAttribute.Scope.PRODUCT, bindings, rows,
    )
    groups = _build_groups(f"Product ID {product.pk}", bindings, rows, language=language)
    by_code = {group.definition_code: group for group in groups}

    def selected(predicate):
        return tuple(
            by_code[binding.definition.code]
            for binding in bindings
            if predicate(binding) and binding.definition.code in by_code
        )

    return ProductAttributeBundleDTO(
        product_id=product.pk,
        product_type_code=context.product_types[product.product_type_id].code,
        attributes=groups,
        all_storefront_attributes=public_attribute_groups(selected(lambda binding: binding.show_on_storefront)),
        specification_attributes=public_attribute_groups(selected(
            lambda binding: (
                binding.show_on_storefront and binding.show_in_specifications
            )
        )),
        card_attributes=public_attribute_groups(selected(lambda binding: binding.show_on_product_card))[:CARD_ATTRIBUTE_LIMIT],
        snapshot_eligible_product_attributes=selected(
            lambda binding: binding.include_in_purchase_snapshot
        ),
    )


def read_product_attribute_bundles(products, *, language=None):
    """Return Product ID -> bundle with a constant number of generic queries."""
    context = _load_context(products, include_variants=False, include_options=False)
    return {
        product.pk: _build_product_bundle(product, context, language=language)
        for product in context.products
    }


def read_product_attributes(product, *, language=None):
    return read_product_attribute_bundles([product], language=language)[product.pk]


def read_product_card_attributes(products, *, language=None):
    return {
        product_id: bundle.card_attributes
        for product_id, bundle in read_product_attribute_bundles(
            products, language=language
        ).items()
    }


def _variant_dtos(product, context, *, language=None):
    bindings = context.bindings_by_type_scope.get(
        (product.product_type_id, ProductTypeAttribute.Scope.VARIANT), []
    )
    binding_by_code = {binding.definition.code: binding for binding in bindings}
    result = []
    for variant in context.variants_by_product.get(product.pk, []):
        rows = context.variant_values.get(variant.pk, [])
        _validate_owner_rows(
            f"Variant ID {variant.pk}", product.product_type_id,
            ProductTypeAttribute.Scope.VARIANT, bindings, rows,
        )
        groups = _build_groups(
            f"Variant ID {variant.pk}", bindings, rows, language=language,
            enforce_required=variant.is_active,
        )
        values = tuple(value for group in groups for value in group.values)
        dimension_map = {
            value.definition_code: value.value_code
            for value in values
            if value.is_variant_dimension and value.value_code is not None
        }
        expected_dimension_codes = {
            binding.definition.code
            for binding in bindings
            if binding.is_variant_dimension
        }
        if variant.is_active and set(dimension_map) != expected_dimension_codes:
            missing = sorted(expected_dimension_codes - set(dimension_map))
            raise CatalogReadIntegrityError(
                [f"Variant ID {variant.pk} is missing dimensions: {missing}."]
            )
        expected_signature = serialize_combination_signature(dimension_map.items())
        if (
            variant.combination_signature is not None or variant.is_active
        ) and variant.combination_signature != expected_signature:
            raise CatalogReadIntegrityError(
                [
                    f"Variant ID {variant.pk} signature {variant.combination_signature!r} "
                    f"does not match {expected_signature!r}."
                ]
            )
        specification_values = tuple(
            value
            for value in values
            if (
                binding_by_code[value.definition_code].show_on_storefront
                and binding_by_code[value.definition_code].show_in_specifications
                and not value.is_internal
            )
        )
        result.append(
            GenericVariantDTO(
                id=variant.pk,
                sku=variant.sku,
                combination_signature=variant.combination_signature,
                attributes=_immutable_mapping(dimension_map),
                attribute_values=values,
                specification_attributes=specification_values,
                stock=variant.stock,
                is_active=variant.is_active,
            )
        )
    return tuple(result)


def _variant_groups(product, context, variant_dtos, *, language=None):
    bindings = [
        binding
        for binding in context.bindings_by_type_scope.get(
            (product.product_type_id, ProductTypeAttribute.Scope.VARIANT), []
        )
        if binding.is_variant_dimension
    ]
    used_codes_by_binding = defaultdict(set)
    for variant in variant_dtos:
        for definition_code, option_code in variant.attributes.items():
            used_codes_by_binding[definition_code].add(option_code)

    groups = []
    for binding in bindings:
        options = context.options_by_definition.get(binding.definition_id, [])
        restrictions = context.restrictions_by_binding.get(binding.pk, [])
        restrictions_by_option = {item.option_id: item for item in restrictions}
        if restrictions:
            available_ids = {
                item.option_id
                for item in restrictions
                if item.is_active and item.option.is_active
            }
        else:
            available_ids = {option.pk for option in options if option.is_active}
        used_codes = used_codes_by_binding[binding.definition.code]
        included = [
            option
            for option in options
            if (option.pk in available_ids or option.code in used_codes)
            and not bool((option.presentation_metadata or {}).get("internal"))
        ]
        included.sort(
            key=lambda option: (
                restrictions_by_option[option.pk].sort_order
                if option.pk in restrictions_by_option
                else option.sort_order,
                option.sort_order,
                option.code,
                option.pk,
            )
        )
        groups.append(
            VariantGroupDTO(
                definition_code=binding.definition.code,
                label=get_localized_value(binding.definition, "name", language=language),
                sort_order=binding.sort_order,
                required=binding.is_required,
                options=tuple(
                    VariantOptionDTO(
                        code=option.code,
                        label=get_localized_value(option, "name", language=language),
                        presentation_metadata=_immutable_mapping(option.presentation_metadata),
                        is_retired=not option.is_active,
                        available=option.pk in available_ids,
                        stable_id=option.pk,
                    )
                    for option in included
                ),
            )
        )
    return tuple(groups)


def read_product_details(products, *, language=None):
    """Build shadow product-detail attribute contracts in batches."""
    context = _load_context(products, include_variants=True, include_options=True)
    result = {}
    for product in context.products:
        product_bundle = _build_product_bundle(product, context, language=language)
        variants = _variant_dtos(product, context, language=language)
        result[product.pk] = GenericProductDetailDTO(
            product_id=product.pk,
            product_type_code=product_bundle.product_type_code,
            product_attributes=product_bundle,
            variant_groups=_variant_groups(
                product, context, variants, language=language
            ),
            variants=variants,
        )
    return result


def read_product_detail(product, *, language=None):
    return read_product_details([product], language=language)[product.pk]


def read_variant_attributes(variant, *, language=None):
    detail = read_product_detail(variant.product, language=language)
    for item in detail.variants:
        if item.id == variant.pk:
            return item
    raise CatalogReadIntegrityError(
        [f"Variant ID {variant.pk} is not attached to Product ID {variant.product_id}."]
    )


def specification_bundle(detail, *, selected_variant=None):
    """Return variant specifications only when a variant is explicitly selected."""
    variant_values = ()
    if selected_variant is not None:
        if selected_variant.id not in {variant.id for variant in detail.variants}:
            raise CatalogReadIntegrityError(
                [f"Variant ID {selected_variant.id} does not belong to this detail contract."]
            )
        variant_values = selected_variant.specification_attributes
    return SpecificationBundleDTO(
        product_attributes=detail.product_attributes.specification_attributes,
        variant_attributes=variant_values,
    )


def serialize_variant_selection(detail):
    """Serialize the generic shadow selector payload without commerce facts."""
    return {
        "product_type": detail.product_type_code,
        "groups": [
            {
                "code": group.definition_code,
                "label": group.label,
                "sort_order": group.sort_order,
                "required": group.required,
                "options": [
                    {
                        "code": option.code,
                        "label": option.label,
                        "presentation_metadata": dict(option.presentation_metadata),
                        "is_retired": option.is_retired,
                        "available": option.available,
                    }
                    for option in group.options
                ],
            }
            for group in detail.variant_groups
        ],
        "variants": [
            {
                "id": variant.id,
                "sku": variant.sku,
                "combination_signature": variant.combination_signature,
                "attributes": dict(variant.attributes),
                "stock": variant.stock,
                "is_active": variant.is_active,
            }
            for variant in detail.variants
        ],
    }
