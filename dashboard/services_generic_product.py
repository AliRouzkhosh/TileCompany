"""Atomic, generic-authoritative Product writes for non-Tile ProductTypes."""

from django.core.exceptions import ValidationError
from django.db import transaction

from dashboard.catalog_schema_policy import (
    ensure_generic_product_type,
    validate_generic_product_write,
)
from products.catalog_domain import VALUE_FIELDS, validate_primary_category_membership
from products.models import (
    Category,
    Product,
    ProductAttributeValue,
    ProductTypeAttribute,
)


CORE_PRODUCT_FIELDS = (
    "name", "name_fa", "slug", "sku", "description", "description_fa",
    "brand", "image", "is_featured",
)


def _set_core_fields(product, form):
    for field in CORE_PRODUCT_FIELDS:
        setattr(product, field, form.cleaned_data[field])


def _set_categories_and_primary(product, form):
    category_ids = {int(value) for value in form.cleaned_data.get("category_ids", [])}
    categories = list(Category.objects.filter(pk__in=category_ids))
    if {category.pk for category in categories} != category_ids:
        raise ValidationError({"category_ids": "One or more selected Categories no longer exist."})
    product.categories.set(categories)
    primary_id = form.cleaned_data.get("primary_category_id")
    product.primary_category = next(
        (category for category in categories if category.pk == primary_id), None
    )
    validate_primary_category_membership(product, product.primary_category)
    product.save(update_fields=["primary_category", "updated_at"])


def _set_value_carrier(row, binding, desired):
    for field in VALUE_FIELDS:
        setattr(row, field, None)
    field = {
        "text": "text_value",
        "integer": "integer_value",
        "decimal": "decimal_value",
        "boolean": "boolean_value",
    }[binding.definition.data_type]
    setattr(row, field, desired)


def _write_option_values(product, binding, desired, existing):
    desired_ids = []
    if binding.cardinality == ProductTypeAttribute.Cardinality.MULTIPLE:
        desired_ids = [int(value) for value in (desired or [])]
    elif desired is not None:
        desired_ids = [int(desired)]

    existing_by_option = {
        row.option_value_id: row for row in existing if row.option_value_id is not None
    }
    for row in existing:
        if row.option_value_id not in desired_ids:
            row.delete()
    for sort_order, option_id in enumerate(desired_ids):
        row = existing_by_option.get(option_id)
        if row is None:
            row = ProductAttributeValue(
                product=product,
                binding=binding,
                option_value_id=option_id,
                sort_order=sort_order,
            )
        else:
            row.sort_order = sort_order
        row.full_clean()
        row.save()


def _write_scalar_value(product, binding, desired, existing):
    if desired is None or (binding.definition.data_type == "text" and desired == ""):
        for row in existing:
            row.delete()
        return
    row = existing[0] if existing else ProductAttributeValue(product=product, binding=binding)
    for extra in existing[1:]:
        extra.delete()
    _set_value_carrier(row, binding, desired)
    row.sort_order = 0
    row.full_clean()
    row.save()


def _write_active_product_attributes(product, form):
    bindings = list(
        ProductTypeAttribute.objects.select_for_update().filter(
            product_type=product.product_type,
            scope=ProductTypeAttribute.Scope.PRODUCT,
            is_active=True,
        ).select_related("definition").order_by("sort_order", "id")
    )
    submitted_binding_ids = {binding.pk for binding in form.schema_context.bindings}
    if {binding.pk for binding in bindings} != submitted_binding_ids:
        raise ValidationError(
            "The ProductType schema changed while this form was open. Reload and review the Product again."
        )
    desired_by_binding = form.desired_attribute_values()
    current_rows = list(
        ProductAttributeValue.objects.select_for_update(of=("self",)).filter(
            product=product, binding_id__in=submitted_binding_ids
        ).select_related("binding__definition", "option_value")
    )
    rows_by_binding = {}
    for row in current_rows:
        rows_by_binding.setdefault(row.binding_id, []).append(row)

    for binding in bindings:
        desired = desired_by_binding[binding.pk]
        existing = rows_by_binding.get(binding.pk, [])
        if binding.definition.data_type == "option":
            _write_option_values(product, binding, desired, existing)
        else:
            _write_scalar_value(product, binding, desired, existing)

    # Local post-write integrity for the governed active schema. Inactive
    # historical rows are intentionally outside replacement semantics.
    for binding in bindings:
        rows = list(product.attribute_values.filter(binding=binding).select_related("option_value"))
        if binding.is_required and not rows:
            raise ValidationError(
                {form.dynamic_name(binding): f"{binding.definition.name} is required."}
            )
        if binding.cardinality == ProductTypeAttribute.Cardinality.SINGLE and len(rows) > 1:
            raise ValidationError(f"{binding.definition.code} is unexpectedly multi-valued.")
        for row in rows:
            row.full_clean()


@transaction.atomic
def create_generic_product(*, product_type, form, user=None, failure_hook=None):
    ensure_generic_product_type(product_type, for_creation=True)
    product = form.save(commit=False)
    product.product_type = product_type
    product.is_active = False
    validate_generic_product_write(product)
    product.full_clean()
    product.save()
    _set_categories_and_primary(product, form)
    _write_active_product_attributes(product, form)
    if failure_hook:
        failure_hook()
    return product


@transaction.atomic
def update_generic_product(*, product, form, user=None, failure_hook=None):
    from products.publication_services import lock_product_publication_scope
    persisted = lock_product_publication_scope(product.pk)
    ensure_generic_product_type(persisted.product_type)
    if (
        product.product_type_id != persisted.product_type_id
        or form.product_type.pk != persisted.product_type_id
    ):
        raise ValidationError(
            "ProductType cannot change through ordinary Product editing; this requires a catalog migration."
        )
    original = Product.objects.get(pk=persisted.pk)
    _set_core_fields(persisted, form)
    persisted.is_active = original.is_active
    validate_generic_product_write(persisted, persisted=original)
    persisted.full_clean()
    persisted.save()
    _set_categories_and_primary(persisted, form)
    _write_active_product_attributes(persisted, form)
    if persisted.is_active:
        from products.publication_services import require_product_ready
        require_product_ready(persisted)
    if failure_hook:
        failure_hook()
    return persisted
