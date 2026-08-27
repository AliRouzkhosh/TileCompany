"""Atomic generic-authoritative Variant writes for non-Tile Products."""

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction

from dashboard.catalog_schema_policy import (
    ensure_generic_variant_product,
    validate_generic_variant_write,
)
from products.catalog_domain import VALUE_FIELDS, calculate_variant_combination_signature
from products.models import Product, ProductTypeAttribute, ProductVariant, VariantAttributeValue
from products.services import set_variant_price


CORE_FIELDS = ("sku", "price_unit", "stock", "low_stock_threshold", "sort_order")


def _set_carrier(row, binding, desired):
    for field in VALUE_FIELDS:
        setattr(row, field, None)
    if binding.definition.data_type == "option":
        row.option_value_id = desired
    else:
        field = {
            "text": "text_value", "integer": "integer_value",
            "decimal": "decimal_value", "boolean": "boolean_value",
        }[binding.definition.data_type]
        setattr(row, field, desired)


def _write_values(variant, form):
    bindings = list(
        ProductTypeAttribute.objects.select_for_update().filter(
            product_type=variant.product.product_type,
            scope=ProductTypeAttribute.Scope.VARIANT,
            is_active=True,
        ).select_related("definition").order_by("sort_order", "id")
    )
    if {binding.pk for binding in bindings} != {binding.pk for binding in form.schema_context.bindings}:
        raise ValidationError("The ProductType Variant schema changed while this form was open. Reload and review it.")
    desired = form.desired_attribute_values()
    current = list(
        VariantAttributeValue.objects.select_for_update(of=("self",)).filter(
            variant=variant, binding_id__in=[binding.pk for binding in bindings]
        ).select_related("binding__definition", "option_value")
    )
    existing = {row.binding_id: row for row in current}
    for binding in bindings:
        value = desired[binding.pk]
        if value is None or (binding.definition.data_type == "text" and value == ""):
            if binding.pk in existing:
                existing[binding.pk].delete()
            continue
        row = existing.get(binding.pk) or VariantAttributeValue(variant=variant, binding=binding)
        _set_carrier(row, binding, value)
        row.full_clean()
        row.save()
    return bindings


def _set_signature(variant, bindings, form):
    dimensions = [binding for binding in bindings if binding.is_variant_dimension]
    present = set(
        variant.attribute_values.filter(binding_id__in=[binding.pk for binding in dimensions])
        .values_list("binding_id", flat=True)
    )
    complete = len(present) == len(dimensions)
    variant.combination_signature = calculate_variant_combination_signature(variant) if complete else None
    if variant.combination_signature is not None and ProductVariant.objects.filter(
        product=variant.product, combination_signature=variant.combination_signature
    ).exclude(pk=variant.pk).exists():
        raise ValidationError("Another Variant already uses this complete dimension combination.")
    variant.full_clean()
    try:
        with transaction.atomic():
            variant.save(update_fields=["combination_signature"])
    except IntegrityError as error:
        raise ValidationError("Another Variant already uses this complete dimension combination.") from error


def _write_prices(variant, form):
    for currency, amount, compare_at in form.currency_price_data():
        if amount is None:
            variant.prices.filter(currency=currency).delete()
        else:
            set_variant_price(
                variant=variant, currency=currency, amount=amount,
                compare_at_amount=compare_at,
                enforce_active_readiness=False,
            )


@transaction.atomic
def create_generic_variant(*, product, form, user=None, failure_hook=None, write_prices=True):
    product = Product.objects.select_for_update(of=("self",)).select_related("product_type").get(pk=product.pk)
    ensure_generic_variant_product(product, for_creation=True)
    list(ProductVariant.objects.select_for_update().filter(product=product).values_list("pk", flat=True))
    variant = form.save(commit=False)
    variant.product = product
    variant.is_active = False
    validate_generic_variant_write(variant, product=product)
    variant.full_clean(exclude=("combination_signature",))
    variant.save()
    bindings = _write_values(variant, form)
    _set_signature(variant, bindings, form)
    if write_prices:
        _write_prices(variant, form)
    if failure_hook:
        failure_hook()
    return variant


@transaction.atomic
def update_generic_variant(*, variant, form, user=None, failure_hook=None, write_prices=True):
    from products.publication_services import lock_product_publication_scope
    product = lock_product_publication_scope(variant.product_id)
    persisted = ProductVariant.objects.select_related(
        "product", "product__product_type"
    ).prefetch_related("prices").get(pk=variant.pk)
    from products.inventory_price_imports import record_product_operational_change, snapshot_variant_inventory_prices
    from products.models import ProductOperationalChange
    before = snapshot_variant_inventory_prices(persisted)
    original = ProductVariant.objects.get(pk=persisted.pk)
    ensure_generic_variant_product(product)
    list(ProductVariant.objects.select_for_update().filter(product=product).values_list("pk", flat=True))
    if form.product.pk != product.pk or variant.product_id != product.pk:
        raise ValidationError("A Variant cannot move between Products.")
    for field in CORE_FIELDS:
        setattr(persisted, field, form.cleaned_data[field])
    persisted.is_active = original.is_active
    validate_generic_variant_write(persisted, product=product, persisted=original)
    persisted.full_clean(exclude=("combination_signature",))
    persisted.save(update_fields=[*CORE_FIELDS])
    bindings = _write_values(persisted, form)
    _set_signature(persisted, bindings, form)
    if write_prices:
        _write_prices(persisted, form)
    if persisted.is_active:
        from products.publication_services import require_variant_ready
        require_variant_ready(persisted)
    if failure_hook:
        failure_hook()
    if user is not None:
        persisted = ProductVariant.objects.select_related("product").prefetch_related("prices").get(pk=persisted.pk)
        record_product_operational_change(actor=user, variant=persisted, source=ProductOperationalChange.Source.MANUAL, before=before, after=snapshot_variant_inventory_prices(persisted))
    return persisted
