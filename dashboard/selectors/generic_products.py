from dataclasses import dataclass

from django.shortcuts import get_object_or_404

from products.models import (
    AttributeOption,
    Category,
    Product,
    ProductAttributeValue,
    ProductType,
    ProductTypeAttribute,
    ProductTypeAttributeOption,
)


@dataclass(frozen=True)
class GenericProductFormContext:
    product_type: ProductType
    bindings: tuple[ProductTypeAttribute, ...]
    options_by_binding: dict[int, tuple[AttributeOption, ...]]
    unavailable_current_option_ids: frozenset[int]
    values_by_binding: dict[int, tuple[ProductAttributeValue, ...]]
    category_choices: tuple[tuple[int, str], ...]


def get_active_generic_product_types():
    return ProductType.objects.filter(is_active=True).exclude(code="tile").order_by(
        "sort_order", "name", "id"
    )


def get_generic_product(pk):
    return get_object_or_404(
        Product.objects.select_related("brand", "product_type", "primary_category")
        .prefetch_related("categories"),
        pk=pk,
    )


def _category_choices():
    categories = list(Category.objects.order_by("sort_order", "name", "id"))
    children = {}
    for category in categories:
        children.setdefault(category.parent_id, []).append(category)

    def label(category, depth):
        bilingual = (
            f"{category.name} — {category.name_fa}"
            if category.name_fa else category.name
        )
        return f"{'    ' * depth}{bilingual}"

    ordered = []
    visited = set()

    def visit(parent_id, depth):
        for category in children.get(parent_id, []):
            if category.pk in visited:
                continue
            visited.add(category.pk)
            ordered.append((category.pk, label(category, depth)))
            visit(category.pk, depth + 1)

    visit(None, 0)
    # Defensive visibility for pre-existing inconsistent/orphaned rows.
    for category in categories:
        if category.pk not in visited:
            ordered.append((category.pk, label(category, 0)))
    return tuple(ordered)


def load_generic_product_form_context(product_type, *, product=None):
    bindings = tuple(
        ProductTypeAttribute.objects.filter(
            product_type=product_type,
            scope=ProductTypeAttribute.Scope.PRODUCT,
            is_active=True,
        ).select_related("definition").order_by("sort_order", "id")
    )
    option_bindings = [
        binding for binding in bindings
        if binding.definition.data_type == binding.definition.DataType.OPTION
    ]
    definition_ids = {binding.definition_id for binding in option_bindings}
    all_options = list(
        AttributeOption.objects.filter(definition_id__in=definition_ids)
        .order_by("definition_id", "sort_order", "name", "id")
    )
    options_by_definition = {}
    for option in all_options:
        options_by_definition.setdefault(option.definition_id, []).append(option)

    restrictions = list(
        ProductTypeAttributeOption.objects.filter(
            binding_id__in=[binding.pk for binding in option_bindings]
        ).select_related("option").order_by("binding_id", "sort_order", "id")
    )
    restrictions_by_binding = {}
    for restriction in restrictions:
        restrictions_by_binding.setdefault(restriction.binding_id, []).append(restriction)

    values = []
    if product is not None:
        values = list(
            ProductAttributeValue.objects.filter(product=product)
            .select_related("binding__definition", "option_value")
            .order_by("binding__sort_order", "sort_order", "id")
        )
    values_by_binding = {}
    for value in values:
        values_by_binding.setdefault(value.binding_id, []).append(value)

    options_by_binding = {}
    unavailable_current = set()
    for binding in option_bindings:
        definition_options = options_by_definition.get(binding.definition_id, [])
        binding_restrictions = restrictions_by_binding.get(binding.pk, [])
        if binding_restrictions:
            allowed_ids = {
                row.option_id for row in binding_restrictions if row.is_active and row.option.is_active
            }
            available = [option for option in definition_options if option.pk in allowed_ids]
            restriction_order = {row.option_id: row.sort_order for row in binding_restrictions}
            available.sort(key=lambda option: (restriction_order[option.pk], option.sort_order, option.pk))
        else:
            available = [option for option in definition_options if option.is_active]
        current_options = [
            value.option_value for value in values_by_binding.get(binding.pk, [])
            if value.option_value_id
        ]
        available_ids = {option.pk for option in available}
        for option in current_options:
            if option.pk not in available_ids:
                available.append(option)
                unavailable_current.add(option.pk)
                available_ids.add(option.pk)
        options_by_binding[binding.pk] = tuple(available)

    return GenericProductFormContext(
        product_type=product_type,
        bindings=bindings,
        options_by_binding=options_by_binding,
        unavailable_current_option_ids=frozenset(unavailable_current),
        values_by_binding={key: tuple(rows) for key, rows in values_by_binding.items()},
        category_choices=_category_choices(),
    )
