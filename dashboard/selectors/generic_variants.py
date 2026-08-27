from dataclasses import dataclass

from django.shortcuts import get_object_or_404

from products.models import (
    AttributeOption,
    Product,
    ProductTypeAttribute,
    ProductTypeAttributeOption,
    ProductVariant,
    VariantAttributeValue,
)


@dataclass(frozen=True)
class GenericVariantFormContext:
    product: Product
    bindings: tuple[ProductTypeAttribute, ...]
    dimension_bindings: tuple[ProductTypeAttribute, ...]
    specification_bindings: tuple[ProductTypeAttribute, ...]
    options_by_binding: dict[int, tuple[AttributeOption, ...]]
    unavailable_current_option_ids: frozenset[int]
    values_by_binding: dict[int, tuple[VariantAttributeValue, ...]]


def get_generic_variant_product(pk):
    return get_object_or_404(
        Product.objects.select_related("product_type").prefetch_related("variants__prices"),
        pk=pk,
    )


def get_generic_variant(product, pk):
    return get_object_or_404(
        ProductVariant.objects.select_related("product", "product__product_type")
        .prefetch_related("prices", "gallery"),
        pk=pk, product=product,
    )


def load_generic_variant_form_context(product, *, variant=None):
    bindings = tuple(
        ProductTypeAttribute.objects.filter(
            product_type=product.product_type,
            scope=ProductTypeAttribute.Scope.VARIANT,
            is_active=True,
        ).select_related("definition").order_by("sort_order", "id")
    )
    option_bindings = [binding for binding in bindings if binding.definition.data_type == "option"]
    options = list(
        AttributeOption.objects.filter(
            definition_id__in={binding.definition_id for binding in option_bindings}
        ).order_by("definition_id", "sort_order", "name", "id")
    )
    by_definition = {}
    for option in options:
        by_definition.setdefault(option.definition_id, []).append(option)
    restrictions = list(
        ProductTypeAttributeOption.objects.filter(
            binding_id__in=[binding.pk for binding in option_bindings]
        ).select_related("option").order_by("binding_id", "sort_order", "id")
    )
    by_binding_restrictions = {}
    for row in restrictions:
        by_binding_restrictions.setdefault(row.binding_id, []).append(row)
    values = []
    if variant is not None:
        values = list(
            VariantAttributeValue.objects.filter(variant=variant)
            .select_related("binding__definition", "option_value")
            .order_by("binding__sort_order", "id")
        )
    values_by_binding = {}
    for value in values:
        values_by_binding.setdefault(value.binding_id, []).append(value)

    options_by_binding = {}
    unavailable = set()
    for binding in option_bindings:
        definition_options = by_definition.get(binding.definition_id, [])
        rows = by_binding_restrictions.get(binding.pk, [])
        if rows:
            allowed = {row.option_id for row in rows if row.is_active and row.option.is_active}
            order = {row.option_id: row.sort_order for row in rows}
            available = [option for option in definition_options if option.pk in allowed]
            available.sort(key=lambda option: (order[option.pk], option.sort_order, option.pk))
        else:
            available = [option for option in definition_options if option.is_active]
        available_ids = {option.pk for option in available}
        for value in values_by_binding.get(binding.pk, []):
            option = value.option_value
            if option and option.pk not in available_ids:
                available.append(option)
                available_ids.add(option.pk)
                unavailable.add(option.pk)
        options_by_binding[binding.pk] = tuple(available)

    return GenericVariantFormContext(
        product=product,
        bindings=bindings,
        dimension_bindings=tuple(binding for binding in bindings if binding.is_variant_dimension),
        specification_bindings=tuple(binding for binding in bindings if not binding.is_variant_dimension),
        options_by_binding=options_by_binding,
        unavailable_current_option_ids=frozenset(unavailable),
        values_by_binding={key: tuple(rows) for key, rows in values_by_binding.items()},
    )
