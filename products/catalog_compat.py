from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction

from .catalog_backfill import (
    PRODUCT_LEGACY_VALUES,
    SPEC_BY_CODE,
    TILE_PRODUCT_TYPE,
    VARIANT_LEGACY_VALUES,
)
from .catalog_domain import calculate_variant_combination_signature
from .models import (
    AttributeDefinition,
    AttributeOption,
    Brand,
    Color,
    Design,
    Finish,
    Material,
    Product,
    ProductAttributeValue,
    ProductType,
    ProductTypeAttribute,
    ProductTypeAttributeOption,
    ProductVariant,
    Size,
    Usage,
    VariantAttributeValue,
)


TAXONOMY_CODE_BY_MODEL = {
    Brand: "brand",
    Design: "design",
    Material: "material",
    Usage: "usage",
    Color: "color",
    Size: "size",
    Finish: "finish",
}


def _tile_type():
    try:
        tile = ProductType.objects.get(code=TILE_PRODUCT_TYPE["code"])
    except ProductType.DoesNotExist:
        raise ValidationError(
            "The Tile framework schema is missing. Run the approved F1B.2 reconciliation first."
        )
    expected = {key: value for key, value in TILE_PRODUCT_TYPE.items() if key != "code"}
    if any(getattr(tile, key) != value for key, value in expected.items()):
        raise ValidationError("The Tile ProductType conflicts with the approved framework schema.")
    return tile


def _tile_binding(code):
    tile = _tile_type()
    spec = SPEC_BY_CODE[code]
    try:
        definition = AttributeDefinition.objects.get(code=code)
        binding = ProductTypeAttribute.objects.select_related("definition", "product_type").get(
            product_type=tile, definition=definition
        )
    except (AttributeDefinition.DoesNotExist, ProductTypeAttribute.DoesNotExist):
        raise ValidationError(
            f"The Tile {code!r} schema binding is missing. Run reconciliation before catalog writes."
        )
    if definition.data_type != AttributeDefinition.DataType.OPTION or any(
        getattr(binding, field) != value for field, value in spec.binding_values.items()
    ):
        raise ValidationError(f"The Tile {code!r} schema binding conflicts with the approved contract.")
    return binding


def _tile_option_membership(binding, option, *, allow_create=False):
    membership = ProductTypeAttributeOption.objects.filter(
        binding=binding, option=option
    ).first()
    if membership is None:
        if not allow_create:
            raise ValidationError(
                f"Tile option membership {binding.definition.code}:{option.code} is missing."
            )
        membership = ProductTypeAttributeOption(
            binding=binding,
            option=option,
            sort_order=option.sort_order,
            is_active=True,
        )
        membership.full_clean()
        membership.save()
    elif not membership.is_active:
        raise ValidationError(
            f"Tile option membership {binding.definition.code}:{option.code} is inactive."
        )
    return membership


def _option_for_legacy(code, legacy_value):
    binding = _tile_binding(code)
    try:
        option = AttributeOption.objects.get(
            definition=binding.definition, code=legacy_value.slug
        )
    except AttributeOption.DoesNotExist:
        raise ValidationError(
            f"Generic option {code}:{legacy_value.slug} is missing. Synchronize the catalog term first."
        )
    if (
        option.name != legacy_value.name
        or option.name_fa != legacy_value.name_fa
        or not option.is_active
    ):
        raise ValidationError(
            f"Generic option {code}:{legacy_value.slug} conflicts with its authoritative legacy term."
        )
    _tile_option_membership(binding, option)
    return binding, option


def _expected_product_options(product, code):
    return {_option_for_legacy(code, value)[1].pk for value in PRODUCT_LEGACY_VALUES[code](product)}


def _expected_variant_option(variant, code):
    legacy_value = VARIANT_LEGACY_VALUES[code](variant)
    if legacy_value is None:
        return None
    return _option_for_legacy(code, legacy_value)[1].pk


def validate_tile_product_parity(product, *, allow_unmirrored_null_type=False):
    tile = _tile_type()
    if product.product_type_id not in (None, tile.pk):
        raise ValidationError(
            {"product_type": "A legacy Tile product cannot be changed to another ProductType."}
        )

    approved_binding_ids = []
    has_any_mirror = False
    for code in PRODUCT_LEGACY_VALUES:
        binding = _tile_binding(code)
        approved_binding_ids.append(binding.pk)
        expected = _expected_product_options(product, code)
        rows = list(product.attribute_values.filter(binding=binding))
        actual = {row.option_value_id for row in rows if row.option_value_id is not None}
        invalid = len(actual) != len(rows)
        has_any_mirror = has_any_mirror or bool(rows)
        if invalid or actual != expected:
            if allow_unmirrored_null_type and product.product_type_id is None and not rows:
                continue
            raise ValidationError(
                f"Product {product.pk}/{product.sku} has generic {code} drift; run reconciliation before editing."
            )

    if product.product_type_id is None and has_any_mirror:
        raise ValidationError("A Product with generic Tile values cannot have a null ProductType.")
    return True


def _legacy_variant_signature(variant):
    if any(getattr(variant, f"{code}_id") is None for code in VARIANT_LEGACY_VALUES):
        return None
    # The existing canonical helper is used after mirroring. This function only
    # identifies whether persisted pre-write state is complete.
    return True


def validate_tile_variant_parity(variant):
    tile = _tile_type()
    if variant.product.product_type_id != tile.pk:
        raise ValidationError("Variant owner must be an established Tile product.")
    for code in VARIANT_LEGACY_VALUES:
        binding = _tile_binding(code)
        expected = _expected_variant_option(variant, code)
        rows = list(variant.attribute_values.filter(binding=binding))
        exact = (
            (expected is None and not rows)
            or (
                expected is not None
                and len(rows) == 1
                and rows[0].option_value_id == expected
            )
        )
        if not exact:
            raise ValidationError(
                f"Variant {variant.pk}/{variant.sku} has generic {code} drift; run reconciliation before editing."
            )
    if _legacy_variant_signature(variant):
        expected_signature = calculate_variant_combination_signature(variant)
        if variant.combination_signature != expected_signature:
            raise ValidationError(
                f"Variant {variant.pk}/{variant.sku} has a noncanonical combination signature."
            )
    elif variant.combination_signature is not None:
        raise ValidationError(
            f"Incomplete variant {variant.pk}/{variant.sku} must have a null combination signature."
        )
    return True


def validate_tile_catalog_option_parity(instance):
    code = TAXONOMY_CODE_BY_MODEL.get(type(instance))
    if code is None:
        return True
    binding = _tile_binding(code)
    try:
        option = AttributeOption.objects.get(
            definition=binding.definition, code=instance.slug
        )
    except AttributeOption.DoesNotExist:
        raise ValidationError(
            f"Generic option {code}:{instance.slug} is missing; run reconciliation before editing."
        )
    if (
        option.name != instance.name
        or option.name_fa != instance.name_fa
        or not option.is_active
    ):
        raise ValidationError(
            f"Generic option {code}:{instance.slug} conflicts with its authoritative legacy term."
        )
    return True


def lock_and_validate_tile_product(product_id):
    product = (
        Product.objects.select_for_update(of=("self",))
        .select_related("product_type", "brand")
        .prefetch_related("designs", "materials", "usages")
        .get(pk=product_id)
    )
    validate_tile_product_parity(product, allow_unmirrored_null_type=True)
    return product


def lock_and_validate_tile_variant(variant_id):
    variant = (
        ProductVariant.objects.select_for_update(of=("self",))
        .select_related("product__product_type", "color", "size", "finish")
        .get(pk=variant_id)
    )
    # Product-scoped serialization protects combination changes; the database
    # conditional uniqueness remains the final cross-process race guard.
    Product.objects.select_for_update().get(pk=variant.product_id)
    list(
        ProductVariant.objects.select_for_update()
        .filter(product_id=variant.product_id)
        .values_list("pk", flat=True)
    )
    validate_tile_variant_parity(variant)
    return variant


@transaction.atomic
def sync_tile_product_catalog(product, *, legacy_write_authorized=False):
    product = (
        Product.objects.select_for_update(of=("self",))
        .select_related("product_type", "brand")
        .prefetch_related("designs", "materials", "usages")
        .get(pk=product.pk)
    )
    tile = _tile_type()
    if product.product_type_id == tile.pk and not legacy_write_authorized:
        validate_tile_product_parity(product)
    if product.product_type_id is None:
        Product.objects.filter(pk=product.pk).update(product_type=tile)
        product.product_type = tile
    elif product.product_type_id != tile.pk:
        raise ValidationError(
            {"product_type": "A legacy Tile product cannot be changed to another ProductType."}
        )

    for code in PRODUCT_LEGACY_VALUES:
        binding = _tile_binding(code)
        expected = _expected_product_options(product, code)
        current = product.attribute_values.filter(binding=binding)
        current.exclude(option_value_id__in=expected).delete()
        existing = set(current.values_list("option_value_id", flat=True))
        for option_id in expected - existing:
            value = ProductAttributeValue(
                product=product, binding=binding, option_value_id=option_id
            )
            value.full_clean()
            value.save()

    validate_tile_product_parity(product)
    return product


@transaction.atomic
def sync_tile_variant_catalog(variant, *, legacy_write_authorized=False):
    variant = (
        ProductVariant.objects.select_for_update(of=("self",))
        .select_related("product__product_type", "color", "size", "finish")
        .get(pk=variant.pk)
    )
    Product.objects.select_for_update().get(pk=variant.product_id)
    list(
        ProductVariant.objects.select_for_update()
        .filter(product_id=variant.product_id)
        .values_list("pk", flat=True)
    )
    if variant.product.product_type_id != _tile_type().pk:
        raise ValidationError("Variant owner must be an established Tile product.")
    if not legacy_write_authorized and (
        variant.attribute_values.filter(
            binding__definition__code__in=VARIANT_LEGACY_VALUES
        ).exists()
        or variant.combination_signature is not None
    ):
        validate_tile_variant_parity(variant)

    missing = [code for code in VARIANT_LEGACY_VALUES if getattr(variant, f"{code}_id") is None]
    if variant.is_active and missing:
        raise ValidationError(
            {code: "Active Tile variants require Color, Size, and Finish." for code in missing}
        )

    for code in VARIANT_LEGACY_VALUES:
        binding = _tile_binding(code)
        option_id = _expected_variant_option(variant, code)
        current = variant.attribute_values.filter(binding=binding)
        if option_id is None:
            current.delete()
            continue
        row = current.first()
        if row is None:
            row = VariantAttributeValue(
                variant=variant, binding=binding, option_value_id=option_id
            )
        elif row.option_value_id != option_id:
            row.option_value_id = option_id
        row.full_clean()
        row.save()

    signature = None if missing else calculate_variant_combination_signature(variant)
    if signature is not None and ProductVariant.objects.filter(
        product_id=variant.product_id, combination_signature=signature
    ).exclude(pk=variant.pk).exists():
        raise ValidationError(
            "Another variant of this product already has the same Color, Size, and Finish combination."
        )
    variant.combination_signature = signature
    try:
        with transaction.atomic():
            variant.full_clean()
            variant.save(update_fields=["combination_signature"])
    except IntegrityError as exc:
        raise ValidationError(
            "Another variant of this product already has the same Color, Size, and Finish combination."
        ) from exc

    validate_tile_variant_parity(variant)
    return variant


def _resequence_options(model, code, definition, binding, *, require_all=True):
    for sort_order, legacy in enumerate(model.objects.all(), start=1):
        option = AttributeOption.objects.filter(
            definition=definition, code=legacy.slug
        ).first()
        if require_all and option is None:
            raise ValidationError(
                f"Cannot order {code}:{legacy.slug}; its generic option is missing or ambiguous."
            )
        if option is None:
            continue
        if option.sort_order != sort_order:
            option.sort_order = sort_order
            option.save(update_fields=["sort_order", "updated_at"])
        membership = _tile_option_membership(binding, option)
        if membership.sort_order != sort_order:
            membership.sort_order = sort_order
            membership.full_clean()
            membership.save(update_fields=["sort_order"])


@transaction.atomic
def save_tile_catalog_term(form):
    model = form._meta.model
    code = TAXONOMY_CODE_BY_MODEL.get(model)
    if code is None:
        return form.save()
    binding = _tile_binding(code)
    creating = not form.instance.pk

    if creating:
        slug = form.cleaned_data["slug"]
        if AttributeOption.objects.filter(definition=binding.definition, code=slug).exists():
            raise ValidationError(
                {"slug": "This slug conflicts with an existing stable generic catalog option."}
            )
        instance = form.save()
        option = AttributeOption(
            definition=binding.definition,
            code=instance.slug,
            name=instance.name,
            name_fa=instance.name_fa,
            sort_order=binding.definition.options.count() + 1,
            is_active=True,
            presentation_metadata={},
        )
        option.full_clean()
        option.save()
        _tile_option_membership(binding, option, allow_create=True)
    else:
        persisted = model.objects.select_for_update().get(pk=form.instance.pk)
        validate_tile_catalog_option_parity(persisted)
        if form.cleaned_data["slug"] != persisted.slug:
            raise ValidationError(
                {"slug": "Slug is a stable catalog code after framework migration and cannot be changed."}
            )
        instance = form.save()
        option = AttributeOption.objects.select_for_update().get(
            definition=binding.definition, code=persisted.slug
        )
        option.name = instance.name
        option.name_fa = instance.name_fa
        option.full_clean()
        option.save(update_fields=["name", "name_fa", "updated_at"])
        _tile_option_membership(binding, option)

    _resequence_options(model, code, binding.definition, binding)
    validate_tile_catalog_option_parity(instance)
    return instance


@transaction.atomic
def sync_legacy_catalog_option(instance, *, allow_create=False, defer_complete_resequence=False):
    """Synchronize a term already saved by a controlled development workflow."""
    code = TAXONOMY_CODE_BY_MODEL.get(type(instance))
    if code is None:
        return None
    binding = _tile_binding(code)
    option = AttributeOption.objects.filter(
        definition=binding.definition, code=instance.slug
    ).first()
    if option is None:
        if not allow_create:
            raise ValidationError(
                f"Generic option {code}:{instance.slug} is missing and cannot be recreated implicitly."
            )
        option = AttributeOption(
            definition=binding.definition,
            code=instance.slug,
            name=instance.name,
            name_fa=instance.name_fa,
            sort_order=binding.definition.options.count() + 1,
            is_active=True,
            presentation_metadata={},
        )
        option.full_clean()
        option.save()
    elif option.name != instance.name or option.name_fa != instance.name_fa:
        raise ValidationError(
            f"Generic option {code}:{instance.slug} conflicts with the legacy term."
        )
    _tile_option_membership(binding, option, allow_create=allow_create)
    _resequence_options(
        type(instance), code, binding.definition, binding,
        require_all=not defer_complete_resequence,
    )
    return option


def delete_tile_catalog_term(instance):
    code = TAXONOMY_CODE_BY_MODEL.get(type(instance))
    if code is None:
        instance.delete()
        return
    _tile_binding(code)
    raise ValidationError(
        "Mapped catalog terms cannot be deleted after framework migration. "
        "Their stable option identity must be retired through a future schema workflow."
    )
